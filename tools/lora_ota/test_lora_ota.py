#!/usr/bin/env python3
"""Offline tests for the LoRa OTA orchestration helper."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import lora_ota as ota
import rak3401_mota_chain as rak_chain


TARGET = 0x1234ABCD
VERSION_OLD = 0x01100000
VERSION_NEW = 0x01110000


def firmware(body: bytes, version: int, target: int = TARGET, hw: str = "TestBoard") -> bytes:
    hw_bytes = hw.encode("ascii")[:32].ljust(32, b"\0")
    return (
        body
        + ota.ENDF_MAGIC
        + struct.pack("<I", len(body))
        + hashlib.sha256(body).digest()[:8]
        + struct.pack("<II", version, target)
        + hw_bytes
    )


def mota_blob(
    image: bytes,
    *,
    full: bool = True,
    version: int = VERSION_NEW,
    target: int = TARGET,
    hw: str = "TestBoard",
    base_hash: bytes = b"\0" * 8,
    payload: bytes | None = None,
    codec: int | None = None,
    block_size_log2: int = 10,
) -> bytes:
    if payload is None:
        payload = image if full else b"synthetic delta payload" * 100
    codec = ota.MOTA_CODEC_FULL if codec is None and full else (
        ota.MOTA_CODEC_SEQUENTIAL if codec is None else codec
    )
    block_size = 1 << block_size_log2
    leaves = [
        hashlib.sha256(payload[offset:offset + block_size]).digest()[:4]
        for offset in range(0, len(payload), block_size)
    ]
    manifest = bytearray((ota.MOTA_FORMAT_VERSION, ota.MOTA_FLAG_FULL if full else 0, 0x12))
    manifest += struct.pack(
        "<IIII", target, version, len(image), len(payload)
    )
    manifest.append(block_size_log2)
    manifest += ota.merkle_root(leaves)
    manifest += hashlib.sha256(image).digest()
    manifest.append(codec)
    manifest += hw.encode("ascii")[:32].ljust(32, b"\0")
    manifest += (b"\0" * 8 if full else base_hash)
    manifest += b"\0" * 32
    manifest += b"\0" * 64
    manifest += b"\xFF" * 4
    assert len(manifest) == ota.MOTA_FIXED_MANIFEST_SIZE
    manifest += b"".join(leaves)
    total = 8 + len(manifest) + len(payload) + len(ota.MOTA_TRAILER)
    return ota.MOTA_MAGIC + struct.pack("<I", total) + manifest + payload + ota.MOTA_TRAILER


def target(
    *,
    platform: str = "esp32",
    base_hash: bytes = b"\0" * 8,
    nrf_sd: bool = False,
    boot_codecs: int | None = None,
    current_version: str | None = None,
) -> ota.TargetInfo:
    return ota.TargetInfo(
        "remote", TARGET, base_hash, platform, nrf_sd, "TestBoard",
        2 if platform == "nrf52" else None, boot_codecs,
        "status", "self", current_version,
    )


def prepare_args(package: Path, motatool: str, base: Path | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        package=package,
        zip_member=None,
        motatool=motatool,
        base=base,
        inplace_memory=None,
        sign_key=None,
        public_key=None,
    )


class FormatTests(unittest.TestCase):
    def test_endf_and_full_mota_round_trip(self) -> None:
        image = firmware(bytes(range(251)) * 20, VERSION_NEW)
        identity = ota.parse_endf(image)
        self.assertEqual(identity.target_id, TARGET)
        self.assertEqual(identity.fw_version, VERSION_NEW)

        parsed = ota.parse_mota(mota_blob(image))
        self.assertTrue(parsed.is_full)
        self.assertEqual(parsed.payload, image)
        self.assertEqual(parsed.version, "v1.17.0")

    def test_corrupt_payload_is_rejected(self) -> None:
        blob = bytearray(mota_blob(firmware(b"A" * 5000, VERSION_NEW)))
        blob[-10] ^= 0x01
        with self.assertRaisesRegex(ota.OtaError, "block hashes"):
            ota.parse_mota(bytes(blob))

    def test_block_larger_than_firmware_buffer_is_rejected(self) -> None:
        image = firmware(b"C" * 5000, VERSION_NEW)
        with self.assertRaisesRegex(ota.OtaError, "at most 1024 bytes"):
            ota.parse_mota(mota_blob(image, block_size_log2=11))

    def test_manifest_endf_identity_mismatch_is_rejected(self) -> None:
        wrong_image = firmware(b"B" * 5000, VERSION_NEW, target=TARGET + 1)
        with self.assertRaisesRegex(ota.OtaError, "target IDs differ"):
            ota.parse_mota(mota_blob(wrong_image))

    def test_version_conversion_is_numeric(self) -> None:
        self.assertEqual(ota.parse_version("v1.10.2"), 0x010A0200)
        self.assertGreater(ota.parse_version("v1.10.0"), ota.parse_version("v1.9.9"))
        self.assertIsNone(ota.parse_version("release-one"))

    def test_temp_radio_accepts_only_cli_bandwidths(self) -> None:
        self.assertEqual(
            ota.parse_temp_radio("909.950,250,5,5,120"),
            (909.95, 250.0, 5, 5, 120),
        )
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "bandwidth must be"):
            ota.parse_temp_radio("909.950,200,5,5,120")

    def test_ota_runners_default_to_sf5_and_250_khz(self) -> None:
        generic = ota.build_parser().parse_args(["release.mota", "remote"])
        chain = rak_chain.build_parser().parse_args([])
        self.assertEqual(generic.temp_radio, "909.950,250,5,5,120")
        self.assertEqual(chain.temp_radio, "909.950,250,5,5,120")

    def test_offline_sd_nrf52_does_not_require_a_base_hash(self) -> None:
        parser = ota.build_parser()
        args = parser.parse_args([
            "release.zip", "offline", "--prepare-only", "--platform", "nrf52",
            "--target-id", f"{TARGET:08X}", "--nrf-sd",
        ])
        ota.validate_args(args, parser)

    def test_nrf_sd_is_rejected_for_esp32(self) -> None:
        parser = ota.build_parser()
        args = parser.parse_args([
            "release.zip", "offline", "--prepare-only", "--platform", "esp32",
            "--target-id", f"{TARGET:08X}", "--nrf-sd",
        ])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ota.validate_args(args, parser)

    def test_expected_installed_body_hash_is_normalized(self) -> None:
        parser = ota.build_parser()
        args = parser.parse_args([
            "release.mota", "remote",
            "--controller-serial", "/dev/controller",
            "--source-serial", "/dev/source",
            "--expected-installed-body-hash", "aabbccddeeff0011",
        ])
        ota.validate_args(args, parser)
        self.assertEqual(
            args.expected_installed_body_hash, "AABBCCDDEEFF0011"
        )

    def test_expected_installed_body_hash_rejects_stage_only(self) -> None:
        parser = ota.build_parser()
        args = parser.parse_args([
            "release.mota", "remote",
            "--controller-serial", "/dev/controller",
            "--source-serial", "/dev/source",
            "--expected-installed-body-hash", "AABBCCDDEEFF0011",
            "--no-install",
        ])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ota.validate_args(args, parser)

    def test_intel_hex_rejects_an_excessive_address_span(self) -> None:
        def record(address: int, record_type: int, data: bytes) -> str:
            raw = bytes((len(data), address >> 8, address & 0xFF, record_type)) + data
            checksum = (-sum(raw)) & 0xFF
            return ":" + (raw + bytes((checksum,))).hex().upper()

        raw = "\n".join((
            record(0, 4, b"\x00\x00"),
            record(0, 0, b"A"),
            record(0, 4, b"\x10\x00"),
            record(0, 0, b"B"),
            record(0, 1, b""),
        )).encode("ascii")
        with self.assertRaisesRegex(ota.OtaError, "address span"):
            ota.parse_intel_hex(raw)

    def test_oversized_direct_file_is_rejected_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized.mota"
            path.write_bytes(b"1234")
            with (
                mock.patch.object(
                    Path, "read_bytes",
                    side_effect=AssertionError("oversized file was read"),
                ),
                self.assertRaisesRegex(ota.OtaError, "unexpectedly large"),
            ):
                ota.read_bounded_file(path, 3, "mOTA file")


class SourceCliTests(unittest.TestCase):
    def test_full_companion_tcp_console_command(self) -> None:
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.side_effect = [
            b"OTA console - type `ota ...`\r\n> ",
            b"  -> OTA seeder | install:disabled | serving:1\r\n> ",
        ]
        args = argparse.Namespace(
            source_cli_serial=None,
            source_serial=None,
            source_cli_tcp="192.0.2.10",
            meshcli="meshcli",
            source_baud=115200,
        )

        with mock.patch.object(
            ota.socket, "create_connection", return_value=connection
        ) as create_connection:
            output = ota.source_cli_command(args, "ota status")

        create_connection.assert_called_once_with(("192.0.2.10", 5002), timeout=10)
        connection.sendall.assert_called_once_with(b"ota status\r\n")
        self.assertEqual(output, "OTA seeder | install:disabled | serving:1")

    def test_full_companion_tcp_source_arguments_validate(self) -> None:
        parser = ota.build_parser()
        args = parser.parse_args([
            "release.mota", "remote",
            "--controller-serial", "/dev/controller",
            "--source-tcp", "192.0.2.10:5001",
            "--source-cli-tcp", "192.0.2.10:5002",
        ])
        ota.validate_args(args, parser)

    def test_source_preflight_accepts_seeder_only_status(self) -> None:
        args = argparse.Namespace(
            source_serial=None,
            source_cli_serial=None,
            source_cli_tcp="192.0.2.10:5002",
        )
        with mock.patch.object(
            ota,
            "source_cli_command",
            return_value="OTA seeder | install:disabled | target:00000000",
        ) as source_command:
            ota.preflight_source_cli(args)
        source_command.assert_called_once_with(args, "ota status")

    def test_serial_preflight_falls_back_to_companion_terminal(self) -> None:
        args = argparse.Namespace(
            source_serial="/dev/source",
            source_cli_serial=None,
            source_cli_tcp=None,
        )
        with mock.patch.object(
            ota,
            "source_cli_command",
            side_effect=(
                "",
                "OTA seeder | install:disabled | target:00000000",
            ),
        ) as source_command:
            ota.preflight_source_cli(args)

        self.assertTrue(args.source_companion_terminal)
        self.assertEqual(
            source_command.call_args_list,
            [
                mock.call(args, "ota status", check=False),
                mock.call(args, "ota status"),
            ],
        )

    def test_serial_companion_command_is_wrapped_in_terminal_tokens(self) -> None:
        args = argparse.Namespace(
            source_cli_serial=None,
            source_serial="/dev/source",
            source_cli_tcp=None,
            source_companion_terminal=True,
            meshcli="meshcli",
            source_baud=115200,
        )
        completed = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="OK - temp params for 120 mins",
            stderr="",
        )
        with mock.patch.object(ota, "run_checked", return_value=completed) as run:
            output = ota.source_cli_command(
                args, "tempradio 909.95,250,5,5,120"
            )

        wire_command = run.call_args.args[0][-1]
        self.assertEqual(
            wire_command,
            "+++MESHCORE-TERM-START\r"
            "tempradio 909.95,250,5,5,120\r"
            "+++MESHCORE-TERM-STOP",
        )
        self.assertIn("OK - temp params", output)


class CompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base_image = firmware(b"old" * 2000, VERSION_OLD)
        self.new_image = firmware(b"new" * 2100, VERSION_NEW)
        self.base_hash = ota.parse_endf(self.base_image).body_hash

    def test_internal_nrf52_requires_matching_in_place_delta(self) -> None:
        full = ota.parse_mota(mota_blob(self.new_image))
        nrf = target(platform="nrf52", base_hash=self.base_hash, boot_codecs=1 << 2)
        self.assertFalse(ota.compatible_mota(full, nrf)[0])

        delta = ota.parse_mota(mota_blob(
            self.new_image, full=False, base_hash=self.base_hash,
            codec=ota.MOTA_CODEC_IN_PLACE,
        ))
        self.assertTrue(ota.compatible_mota(delta, nrf)[0])

        wrong_base = ota.parse_mota(mota_blob(
            self.new_image, full=False, base_hash=b"X" * 8,
            codec=ota.MOTA_CODEC_IN_PLACE,
        ))
        self.assertFalse(ota.compatible_mota(wrong_base, nrf)[0])

    def test_nrf52_bootloader_codec_mask_is_checked(self) -> None:
        delta = ota.parse_mota(mota_blob(
            self.new_image, full=False, base_hash=self.base_hash,
            codec=ota.MOTA_CODEC_IN_PLACE,
        ))
        nrf = target(platform="nrf52", base_hash=self.base_hash, boot_codecs=1)
        good, reason = ota.compatible_mota(delta, nrf)
        self.assertFalse(good)
        self.assertIn("codec mask", reason)

    def test_sd_nrf52_accepts_full_when_bootloader_does(self) -> None:
        full = ota.parse_mota(mota_blob(self.new_image))
        nrf = target(platform="nrf52", nrf_sd=True, boot_codecs=1)
        self.assertTrue(ota.compatible_mota(full, nrf)[0])

    def test_zip_prefers_equal_version_delta(self) -> None:
        full = mota_blob(self.new_image)
        delta = mota_blob(
            self.new_image, full=False, base_hash=self.base_hash,
            codec=ota.MOTA_CODEC_SEQUENTIAL,
        )
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("full.mota", full)
                archive.writestr("delta.mota", delta)
            with zipfile.ZipFile(archive_path) as archive:
                selected = ota.select_mota_from_zip(
                    archive, target(base_hash=self.base_hash), None
                )
        self.assertIsNotNone(selected)
        self.assertFalse(selected[0].is_full)

    def test_base_zip_selects_running_hash_not_newest_file(self) -> None:
        other = firmware(b"other" * 1300, VERSION_NEW)
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "bases.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("newer.bin", other)
                archive.writestr("running.bin", self.base_image)
            selected = ota.load_base_image(
                archive_path,
                target(
                    base_hash=self.base_hash,
                    current_version="v1.16.0",
                ),
            )
        self.assertEqual(selected.image, self.base_image)


class DownloadSessionTests(unittest.TestCase):
    class Controller:
        def __init__(self, replies: list[str | Exception]):
            self.replies = iter(replies)
            self.commands: list[str] = []

        def remote_command(self, _target: str, command: str, **_kwargs: object) -> str:
            self.commands.append(command)
            reply = next(self.replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

    def setUp(self) -> None:
        image = firmware(b"download" * 900, VERSION_NEW)
        self.package = ota.parse_mota(mota_blob(image))

    def args(self, replace: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            target="remote", replace_active_download=replace,
            discovery_timeout=1, discovery_interval=1,
        )

    def test_matching_active_session_is_resumed(self) -> None:
        controller = self.Controller([
            f"OTA | download: downloading 3/9 id={self.package.manifest_id} 2s"
        ])
        ota.find_and_start_pull(controller, self.args(), self.package)
        self.assertEqual(controller.commands, ["ota status"])

    def test_different_active_session_is_preserved_by_default(self) -> None:
        controller = self.Controller(["OTA | download: ready to install 9/9 id=DEADBEEF 2s"])
        with self.assertRaisesRegex(ota.OtaError, "already has mOTA"):
            ota.find_and_start_pull(controller, self.args(), self.package)
        self.assertEqual(controller.commands, ["ota status"])

    def test_completed_previous_session_requires_exact_running_hash(self) -> None:
        args = self.args()
        args.clear_completed_manifest = "DEADBEEF"
        args.clear_completed_on_body_hash = "0011223344556677"
        controller = self.Controller([
            "OTA | download: ready to install 9/9 id=DEADBEEF 2s",
            "self body=1 image=2 base_hash=8899AABBCCDDEEFF",
        ])
        with self.assertRaisesRegex(ota.OtaError, "running body hash"):
            ota.find_and_start_pull(controller, args, self.package)
        self.assertEqual(controller.commands, ["ota status", "ota self"])

    def test_completed_previous_session_is_cleared_after_exact_proof(self) -> None:
        args = self.args()
        args.clear_completed_manifest = "DEADBEEF"
        args.clear_completed_on_body_hash = "0011223344556677"
        controller = self.Controller([
            "OTA | download: ready to install 9/9 id=DEADBEEF 2s",
            "self body=1 image=2 base_hash=0011223344556677",
            "OK dropped session",
            "OTA | no download",
            "Updates 1/1",
            f"OK pulling mid={self.package.manifest_id} -> flash (primary traffic)",
        ])
        ota.find_and_start_pull(controller, args, self.package)
        self.assertEqual(
            controller.commands,
            [
                "ota status", "ota self", "ota cancel", "ota status", "ota ls",
                f"ota pull {self.package.manifest_id} flash",
            ],
        )

    def test_replace_active_session_requires_explicit_flag(self) -> None:
        controller = self.Controller([
            "OTA | download: downloading 3/9 id=DEADBEEF 2s",
            "OK dropped session",
            "Updates 1/1",
            f"OK pulling mid={self.package.manifest_id} -> flash (primary traffic)",
        ])
        ota.find_and_start_pull(controller, self.args(replace=True), self.package)
        self.assertEqual(
            controller.commands,
            ["ota status", "ota cancel", "ota ls", f"ota pull {self.package.manifest_id} flash"],
        )

    def test_monitor_rejects_ready_session_for_another_package(self) -> None:
        controller = self.Controller([
            "OTA | download: ready to install 9/9 id=DEADBEEF 2s"
        ])
        args = argparse.Namespace(
            target="remote", transfer_timeout_minutes=1, poll_seconds=1,
        )
        with (
            mock.patch.object(ota.time, "sleep"),
            self.assertRaisesRegex(ota.OtaError, "switched to mOTA"),
        ):
            ota.monitor_download(controller, args, self.package)

    def test_monitor_accepts_only_the_expected_ready_session(self) -> None:
        controller = self.Controller([
            f"OTA | download: ready to install 9/9 id={self.package.manifest_id} 2s"
        ])
        args = argparse.Namespace(
            target="remote", transfer_timeout_minutes=1, poll_seconds=1,
        )
        with mock.patch.object(ota.time, "sleep") as sleep:
            status = ota.monitor_download(controller, args, self.package)
        self.assertIn(self.package.manifest_id, status)
        sleep.assert_called_once_with(1.0)

    def test_monitor_stops_immediately_when_seeder_exits(self) -> None:
        controller = self.Controller([])
        args = argparse.Namespace(
            target="remote", transfer_timeout_minutes=1, poll_seconds=1,
        )

        class Seeder:
            def ensure_running(self, _context: str) -> None:
                raise ota.OtaError("seeder exited")

        with self.assertRaisesRegex(ota.OtaError, "seeder exited"):
            ota.monitor_download(controller, args, self.package, Seeder())
        self.assertEqual(controller.commands, [])

    def test_monitor_uses_advancing_source_log_without_airtime_queries(self) -> None:
        controller = self.Controller([
            f"OTA | download: ready to install 9/9 id={self.package.manifest_id} 2s"
        ])
        args = argparse.Namespace(
            target="remote", transfer_timeout_minutes=1, poll_seconds=1,
        )
        total = (
            self.package.payload_size + self.package.block_size - 1
        ) // self.package.block_size

        class Seeder:
            calls = 0

            def ensure_running(self, _context: str) -> None:
                return None

            def payload_read_progress(
                self, _package: ota.MotaInfo
            ) -> tuple[int, int, int]:
                self.calls += 1
                progress = min(self.calls, total)
                return progress, total, progress

        seeder = Seeder()
        with (
            mock.patch.object(ota.time, "sleep"),
            mock.patch.object(ota, "transfer_tail_guard_seconds", return_value=0),
        ):
            status = ota.monitor_download(controller, args, self.package, seeder)
        self.assertIn(self.package.manifest_id, status)
        self.assertEqual(controller.commands, ["ota status"])
        self.assertEqual(seeder.calls, total)

    def test_lost_pull_reply_is_resolved_without_blind_replay(self) -> None:
        controller = self.Controller([
            "OTA | no download",
            "Updates 1/1",
            ota.TransmissionError("reply lost"),
            f"OTA | download: downloading 1/9 id={self.package.manifest_id} 2s",
        ])
        ota.find_and_start_pull(controller, self.args(), self.package)
        self.assertEqual(
            controller.commands,
            [
                "ota status", "ota ls",
                f"ota pull {self.package.manifest_id} flash", "ota status",
            ],
        )


class ReliabilityTests(unittest.TestCase):
    def test_target_version_falls_back_to_ver(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.replies = iter([
                    "OTA | no download | target:1234ABCD hw=TestBoard",
                    "self body=1 image=2 base_hash=0011223344556677",
                    "Unknown command",
                    "v1.16.9 (Build: test)",
                ])

            def remote_command(self, *_args: object, **_kwargs: object) -> str:
                return next(self.replies)

        result = ota.query_target(
            Controller(), argparse.Namespace(target="remote")
        )
        self.assertEqual(result.current_version, "v1.16.9")
        self.assertEqual(result.current_version_source, "ver")

    def test_unattended_prompt_waits_ten_seconds_and_continues(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", io.StringIO()),
            mock.patch.object(ota.time, "sleep") as sleep,
            contextlib.redirect_stdout(output),
        ):
            self.assertTrue(ota.prompt_after_transmission_failure(
                "test", ota.TransmissionError("lost")
            ))
        sleep.assert_called_once_with(10)
        self.assertIn("continuing in 10s", output.getvalue())

    def test_retry_prompt_continues_by_default_choice(self) -> None:
        calls = 0

        def action() -> str:
            nonlocal calls
            calls += 1
            if calls <= 4:
                raise ota.TransmissionError("lost")
            return "done"

        with (
            mock.patch.object(ota.time, "sleep"),
            mock.patch.object(
                ota, "prompt_after_transmission_failure", return_value=True
            ) as prompt,
        ):
            self.assertEqual(ota.retry_transmission(action, "test command"), "done")
        self.assertEqual(calls, 5)
        prompt.assert_called_once()

    def test_ninety_second_limit_can_prompt_before_three_retries(self) -> None:
        calls = 0

        def action() -> str:
            nonlocal calls
            calls += 1
            raise ota.TransmissionError("lost")

        with (
            mock.patch.object(ota.time, "monotonic", side_effect=[0, 91]),
            mock.patch.object(
                ota, "prompt_after_transmission_failure", return_value=False
            ) as prompt,
            self.assertRaises(ota.TransmissionStopped),
        ):
            ota.retry_transmission(action, "test command")
        self.assertEqual(calls, 1)
        prompt.assert_called_once()

    def test_retry_prompt_can_stop(self) -> None:
        calls = 0

        def action() -> str:
            nonlocal calls
            calls += 1
            raise ota.TransmissionError("lost")

        with (
            mock.patch.object(ota.time, "sleep"),
            mock.patch.object(
                ota, "prompt_after_transmission_failure", return_value=False
            ),
            self.assertRaisesRegex(ota.OtaError, "stopped after transmission"),
        ):
            ota.retry_transmission(action, "test command")
        self.assertEqual(calls, 4)

    def test_retry_delay_is_bounded_exponential(self) -> None:
        self.assertEqual(
            [ota.transmission_retry_delay(number) for number in range(1, 6)],
            [2, 4, 8, 8, 8],
        )

    def test_status_poll_interval_adapts_to_contention(self) -> None:
        backed_off = ota.adaptive_poll_interval(60, 60, 30, 45)
        self.assertEqual(backed_off, 90)
        self.assertEqual(
            ota.adaptive_poll_interval(backed_off, 60, 2, 45),
            67.5,
        )
        self.assertEqual(ota.adaptive_poll_ceiling(60), 180)

    def test_first_status_wait_scales_with_radio_and_relay_airtime(self) -> None:
        package = ota.parse_mota(mota_blob(firmware(b"A" * 50_000, VERSION_NEW)))
        direct = argparse.Namespace(
            poll_seconds=30,
            temp_values=(909.95, 500.0, 5, 5, 120),
            relay=[],
        )
        relayed = argparse.Namespace(
            poll_seconds=30,
            temp_values=(909.95, 62.5, 7, 5, 120),
            relay=["relay"],
        )
        self.assertGreaterEqual(ota.initial_status_wait_seconds(direct, package), 30)
        self.assertGreater(
            ota.initial_status_wait_seconds(relayed, package),
            ota.initial_status_wait_seconds(direct, package),
        )
        self.assertLessEqual(
            ota.initial_status_wait_seconds(relayed, package),
            ota.adaptive_poll_ceiling(30),
        )
        self.assertGreater(
            ota.passive_progress_stall_seconds(relayed, package),
            ota.passive_progress_stall_seconds(direct, package),
        )

    def test_seeder_progress_counts_only_payload_block_boundaries(self) -> None:
        package = ota.parse_mota(mota_blob(firmware(b"B" * 2500, VERSION_NEW)))
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "serve.log"
            expected = [
                package.payload_offset + index * package.block_size
                for index in range(3)
            ]
            log.write_text(
                "[dev] READ 0 @8 OK\n"
                f"[dev] READ 0 @{expected[0]} OK\n"
                f"[dev] READ 0 @{expected[2]} OK\n",
                encoding="utf-8",
            )
            seeder = object.__new__(ota.SeederProcess)
            seeder.log_path = log
            seeder.log_file = None
            self.assertEqual(seeder.payload_read_progress(package), (2, 3, 2))

    def test_marked_output_excludes_queued_messages(self) -> None:
        controller = object.__new__(ota.Controller)
        controller._execute = lambda _commands, _label: subprocess.CompletedProcess(
            [], 0,
            stdout=(
                '{"text":"stale","txt_type":1}\n'
                'UNIQUE_MARKER\n'
                '{"text":"fresh","txt_type":1}\n'
            ),
            stderr="",
        )
        all_objects, post_objects = controller._run_marked(
            ["unused"], "test", "UNIQUE_MARKER"
        )
        self.assertEqual(len(all_objects), 2)
        self.assertEqual([item["text"] for item in post_objects], ["fresh"])

    def test_remote_command_ignores_unrelated_post_marker_reply(self) -> None:
        controller = object.__new__(ota.Controller)
        controller.reply_timeout = 20
        key = "A1" * 32
        controller._run_marked = lambda _commands, _label, _marker: (
            [
                {"adv_name": "remote", "public_key": key},
                {"login_success": True},
            ],
            [
                {
                    "txt_type": 1, "text": "OK dropped session",
                    "pubkey_prefix": key[:12],
                },
                {
                    "txt_type": 1,
                    "text": "OTA | no download | target:1234ABCD",
                    "pubkey_prefix": key[:12],
                },
            ],
        )
        reply = controller._remote_command_once("remote", "ota status", "secret")
        self.assertTrue(reply.startswith("OTA |"))

    def test_remote_admin_login_is_reused_until_explicit_refresh(self) -> None:
        controller = object.__new__(ota.Controller)
        controller.reply_timeout = 20
        controller._authenticated_targets = set()
        key = "A1" * 32
        commands_seen: list[list[str]] = []

        def run_marked(commands: list[str], _label: str, _marker: str):
            commands_seen.append(commands)
            objects = [{"adv_name": "remote", "public_key": key}]
            if "login" in commands:
                objects.append({"login_success": True})
            return objects, [{
                "txt_type": 1,
                "text": "OTA | no download | target:1234ABCD",
                "pubkey_prefix": key[:12],
            }]

        controller._run_marked = run_marked
        controller._remote_command_once("remote", "ota status", "secret")
        controller._remote_command_once("remote", "ota status", "secret")
        self.assertIn("login", commands_seen[0])
        self.assertNotIn("login", commands_seen[1])

        controller.forget_remote_auth("remote")
        controller._remote_command_once("remote", "ota status", "secret")
        self.assertIn("login", commands_seen[2])

    def test_remote_command_can_bound_one_reply_wait(self) -> None:
        controller = object.__new__(ota.Controller)
        controller.reply_timeout = 45
        controller._authenticated_targets = {"remote"}
        key = "A1" * 32
        commands_seen: list[str] = []

        def run_marked(commands: list[str], _label: str, _marker: str):
            commands_seen.extend(commands)
            return (
                [{"adv_name": "remote", "public_key": key}],
                [{
                    "txt_type": 1,
                    "text": "OTA | no download | target:1234ABCD",
                    "pubkey_prefix": key[:12],
                }],
            )

        controller._run_marked = run_marked
        controller._remote_command_once(
            "remote", "ota status", "secret", reply_timeout=10
        )
        wait_index = commands_seen.index("trywait_msg")
        self.assertEqual(commands_seen[wait_index + 1], "10")

    def test_silent_commands_retain_cached_admin_session(self) -> None:
        controller = object.__new__(ota.Controller)
        controller.reply_timeout = 20
        controller._authenticated_targets = {"remote"}
        key = "A1" * 32
        commands_seen: list[list[str]] = []

        def run_marked(commands: list[str], _label: str, _marker: str):
            commands_seen.append(commands)
            objects = [{"adv_name": "remote", "public_key": key}]
            if "login" in commands:
                objects.append({"login_success": True})
            replies = []
            if len(commands_seen) == 3:
                replies = [{
                    "txt_type": 1,
                    "text": "OTA | no download | target:1234ABCD",
                    "pubkey_prefix": key[:12],
                }]
            return objects, replies

        controller._run_marked = run_marked
        with self.assertRaises(ota.TransmissionError):
            controller._remote_command_once("remote", "ota status", "secret")
        with self.assertRaises(ota.TransmissionError):
            controller._remote_command_once("remote", "ota status", "secret")
        controller._remote_command_once("remote", "ota status", "secret")
        self.assertNotIn("login", commands_seen[0])
        self.assertNotIn("login", commands_seen[1])
        self.assertNotIn("login", commands_seen[2])

    def test_generic_retry_rejects_state_changing_ota_commands(self) -> None:
        controller = object.__new__(ota.Controller)
        controller.password = "secret"
        with self.assertRaisesRegex(ota.OtaError, "state-aware"):
            controller.remote_command("remote", "ota install")

    def test_unknown_contact_is_not_retried_as_packet_loss(self) -> None:
        controller = object.__new__(ota.Controller)
        controller.reply_timeout = 20
        controller._run_marked = lambda _commands, _label, _marker: (
            [{"error": "contact unknown", "name": "missing"}], []
        )
        with self.assertRaisesRegex(ota.OtaError, "no contact"):
            controller._remote_command_once("missing", "ota status", "secret")

    def test_empty_json_radio_result_is_an_error(self) -> None:
        controller = object.__new__(ota.Controller)
        controller._execute = lambda _commands, _label: subprocess.CompletedProcess(
            [], 0, stdout="Error: radio rejected\n", stderr=""
        )
        with self.assertRaisesRegex(ota.OtaError, "radio rejected"):
            controller._run(["set", "radio", "bad"], "set radio")

    def test_set_radio_requires_matching_readback(self) -> None:
        controller = object.__new__(ota.Controller)
        controller._run = lambda *_args: [{}]
        controller.get_radio = lambda: ota.RadioSettings(915, 250, 7, 5, False)
        requested = ota.RadioSettings(909.95, 250, 5, 5, False)
        with self.assertRaisesRegex(ota.OtaError, "read back"):
            controller.set_radio(requested, "set radio")

    def test_lost_target_temp_reply_is_resolved_on_temporary_channel(self) -> None:
        normal = ota.RadioSettings(910.525, 62.5, 7, 5, False)
        temporary = ota.RadioSettings(909.95, 250.0, 5, 5, False)

        class Controller:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.radios: list[ota.RadioSettings] = []

            def remote_command(
                self, _target: str, command: str, **_kwargs: object
            ) -> str:
                self.commands.append(command)
                if command.startswith("tempradio "):
                    raise ota.TransmissionError("lost reply")
                return "self body=1 image=2 base_hash=0011223344556677"

            def set_radio(self, radio: ota.RadioSettings, _label: str) -> None:
                self.radios.append(radio)

        controller = Controller()
        ota.arm_target_temp_radio(
            controller,
            argparse.Namespace(target="remote"),
            "tempradio 909.95,250,5,5,120",
            temporary,
            normal,
        )
        self.assertEqual(
            controller.commands,
            ["tempradio 909.95,250,5,5,120", "ota self"],
        )
        self.assertEqual(controller.radios, [temporary, normal])

    def test_ambiguous_target_temp_probe_is_not_replayed(self) -> None:
        normal = ota.RadioSettings(910.525, 62.5, 7, 5, False)
        temporary = ota.RadioSettings(909.95, 250.0, 5, 5, False)

        class Controller:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.radios: list[ota.RadioSettings] = []

            def remote_command(
                self, _target: str, command: str, **_kwargs: object
            ) -> str:
                self.commands.append(command)
                raise ota.TransmissionError("lost reply")

            def set_radio(self, radio: ota.RadioSettings, _label: str) -> None:
                self.radios.append(radio)

        controller = Controller()
        with self.assertRaisesRegex(ota.OtaError, "outcome is ambiguous"):
            ota.arm_target_temp_radio(
                controller,
                argparse.Namespace(target="remote"),
                "tempradio 909.95,250,5,5,120",
                temporary,
                normal,
            )
        self.assertEqual(
            controller.commands,
            ["tempradio 909.95,250,5,5,120", "ota self", "ota self"],
        )
        self.assertEqual(controller.radios, [temporary, normal])

    @mock.patch.object(ota.time, "sleep")
    def test_target_temp_retries_only_after_exact_normal_identity(
        self, sleep: mock.Mock
    ) -> None:
        normal = ota.RadioSettings(910.525, 62.5, 7, 5, False)
        temporary = ota.RadioSettings(909.95, 500.0, 5, 5, False)

        class Controller:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.radios: list[ota.RadioSettings] = []
                self.replies = iter([
                    ota.TransmissionError("lost command reply"),
                    ota.TransmissionError("not on temporary channel"),
                    "self body=1 image=2 base_hash=0011223344556677",
                    "OK - temp params for 120 mins",
                ])

            def remote_command(
                self, _target: str, command: str, **_kwargs: object
            ) -> str:
                self.commands.append(command)
                reply = next(self.replies)
                if isinstance(reply, Exception):
                    raise reply
                return reply

            def set_radio(self, radio: ota.RadioSettings, _label: str) -> None:
                self.radios.append(radio)

        controller = Controller()
        ota.arm_target_temp_radio(
            controller,
            argparse.Namespace(target="remote"),
            "tempradio 909.95,500,5,5,120",
            temporary,
            normal,
        )
        self.assertEqual(
            controller.commands,
            [
                "tempradio 909.95,500,5,5,120",
                "ota self",
                "ota self",
                "tempradio 909.95,500,5,5,120",
            ],
        )
        self.assertEqual(controller.radios, [temporary, normal])
        sleep.assert_called_once_with(ota.transmission_retry_delay(1))

    def test_install_retries_only_after_still_ready_is_confirmed(self) -> None:
        image = firmware(b"install" * 900, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))

        class Controller:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.replies: list[str | Exception] = [
                    f"OTA | download: ready to install 9/9 id={package.manifest_id} 2s",
                    "OK - temp params for 3 mins",
                    ota.TransmissionError("install reply lost"),
                    f"OTA | download: ready to install 9/9 id={package.manifest_id} 8s",
                    "OK - temp params for 3 mins",
                    "OK | verified; applying",
                ]

            def remote_command(
                self, _target: str, command: str, **_kwargs: object
            ) -> str:
                self.commands.append(command)
                reply = self.replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply

        controller = Controller()
        args = argparse.Namespace(
            target="remote", temp_values=(909.95, 250.0, 5, 5, 120)
        )
        with mock.patch.object(ota.time, "sleep"):
            self.assertTrue(ota.request_install(controller, args, package))
        self.assertEqual(
            controller.commands,
            [
                "ota status", "tempradio 909.95,250,5,5,3", "ota install",
                "ota status", "tempradio 909.95,250,5,5,3", "ota install",
            ],
        )

    def test_install_watchdog_gate_runs_immediately_before_install(self) -> None:
        image = firmware(b"watchdog" * 900, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))

        class Controller:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.replies = iter([
                    f"OTA | download: ready to install 9/9 id={package.manifest_id} 2s",
                    "OK - temp params for 3 mins",
                    "> off",
                    "OK | verified; applying",
                ])

            def remote_command(
                self, _target: str, command: str, **_kwargs: object
            ) -> str:
                self.commands.append(command)
                return next(self.replies)

        controller = Controller()
        args = argparse.Namespace(
            target="remote",
            temp_values=(909.95, 250.0, 5, 5, 120),
            require_system_watchdog_off=True,
        )
        self.assertTrue(ota.request_install(controller, args, package))
        self.assertEqual(
            controller.commands,
            [
                "ota status",
                "tempradio 909.95,250,5,5,3",
                "get system.watchdog",
                "ota install",
            ],
        )

    def test_install_watchdog_gate_rejects_enabled_watchdog(self) -> None:
        image = firmware(b"watchdog-on" * 900, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))

        class Controller:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.replies = iter([
                    f"OTA | download: ready to install 9/9 id={package.manifest_id} 2s",
                    "OK - temp params for 3 mins",
                    "> on",
                ])

            def remote_command(
                self, _target: str, command: str, **_kwargs: object
            ) -> str:
                self.commands.append(command)
                return next(self.replies)

        controller = Controller()
        args = argparse.Namespace(
            target="remote",
            temp_values=(909.95, 250.0, 5, 5, 120),
            require_system_watchdog_off=True,
        )
        with self.assertRaisesRegex(ota.OtaError, "must report `> off`"):
            ota.request_install(controller, args, package)
        self.assertNotIn("ota install", controller.commands)

    def test_post_install_verification_requires_exact_version(self) -> None:
        image = firmware(b"verify" * 900, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))
        expected_hash = ota.parse_endf(image).body_hash

        class Controller:
            def __init__(self, version: str) -> None:
                self.replies = iter([
                    f"self body=1 image=2 base_hash={expected_hash.hex()}",
                    f"OTA stats | fw {version}",
                ])

            def remote_command(self, *_args: object, **_kwargs: object) -> str:
                return next(self.replies)

        args = argparse.Namespace(target="remote")
        ota.verify_installed(
            Controller("v1.17.0 (Build: test)"), args, package, expected_hash
        )
        with self.assertRaisesRegex(ota.OtaError, "expected v1.17.0"):
            ota.verify_installed(
                Controller("v1.16.9 (Build: old)"), args, package, expected_hash
            )

    def test_post_install_ready_probe_runs_at_ten_and_twenty_seconds(self) -> None:
        expected_hash = bytes.fromhex("0011223344556677")
        old_hash = bytes.fromhex("8899AABBCCDDEEFF")

        class Controller:
            reply_timeout = 45

            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def remote_command(
                self, _target: str, command: str, **kwargs: object
            ) -> str:
                self.calls.append((command, kwargs))
                if len(self.calls) == 1:
                    raise ota.TransmissionError("still rebooting")
                return (
                    "self body=1 image=2 "
                    f"base_hash={expected_hash.hex().upper()}"
                )

        controller = Controller()
        with (
            mock.patch.object(
                ota.time, "monotonic", side_effect=[0.0, 0.0, 10.0]
            ),
            mock.patch.object(ota.time, "sleep") as sleep,
        ):
            reply = ota.wait_for_post_install_identity(
                controller,
                argparse.Namespace(target="remote"),
                expected_hash,
                old_hash,
                20,
            )

        self.assertIn(expected_hash.hex().upper(), reply)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [10, 10])
        self.assertEqual([item[0] for item in controller.calls], ["ota self"] * 2)
        self.assertTrue(all(
            item[1] == {"retry": False, "reply_timeout": 10}
            for item in controller.calls
        ))

    def test_post_install_ready_probe_rejects_no_endf_reply(self) -> None:
        class Controller:
            reply_timeout = 45

            def remote_command(self, *_args: object, **_kwargs: object) -> str:
                return "ERR no EndF (firmware lacks the trailer?)"

        with (
            mock.patch.object(ota.time, "monotonic", side_effect=[0.0, 0.0]),
            mock.patch.object(ota.time, "sleep"),
            self.assertRaisesRegex(ota.OtaError, "without a running body hash"),
        ):
            ota.wait_for_post_install_identity(
                Controller(),
                argparse.Namespace(target="remote"),
                bytes.fromhex("0011223344556677"),
                bytes.fromhex("8899AABBCCDDEEFF"),
                20,
            )

    def test_reboot_ready_probe_defaults_to_twenty_seconds(self) -> None:
        parser = ota.build_parser()
        args = parser.parse_args([
            "release.mota", "remote",
            "--controller-serial", "/dev/controller",
            "--source-serial", "/dev/source",
        ])
        self.assertEqual(args.reboot_wait, 20)
        self.assertEqual(rak_chain.build_parser().parse_args([]).reboot_wait, 20)

    def test_post_install_version_falls_back_to_runtime_ver(self) -> None:
        image = firmware(b"verify-fallback" * 700, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))
        expected_hash = ota.parse_endf(image).body_hash

        class Controller:
            def __init__(self) -> None:
                self.replies = iter([
                    f"self body=1 image=2 base_hash={expected_hash.hex()}",
                    "ERR command not found",
                    "v1.17.0 (Build: test)",
                ])

            def remote_command(self, *_args: object, **_kwargs: object) -> str:
                return next(self.replies)

        ota.verify_installed(
            Controller(), argparse.Namespace(target="remote"), package, expected_hash
        )

    def test_exact_hash_allows_historical_runtime_label_when_stats_unknown(self) -> None:
        image = firmware(b"verify-historical" * 700, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))
        expected_hash = ota.parse_endf(image).body_hash

        class Controller:
            def __init__(self) -> None:
                self.replies = iter([
                    f"self body=1 image=2 base_hash={expected_hash.hex()}",
                    "OTA | fw v? id=?",
                    "v1.16.9 (Build: historical)",
                ])

            def remote_command(self, *_args: object, **_kwargs: object) -> str:
                return next(self.replies)

        ota.verify_installed(
            Controller(), argparse.Namespace(target="remote"), package, expected_hash
        )

    def test_unknown_new_hash_must_differ_from_pre_install_hash(self) -> None:
        image = firmware(b"delta-result" * 700, VERSION_NEW)
        package = ota.parse_mota(mota_blob(
            image,
            full=False,
            base_hash=b"B" * 8,
            codec=ota.MOTA_CODEC_SEQUENTIAL,
        ))
        old_hash = bytes.fromhex("0011223344556677")

        class Controller:
            def __init__(self) -> None:
                self.replies = iter([
                    f"self body=1 image=2 base_hash={old_hash.hex()}",
                ])

            def remote_command(self, *_args: object, **_kwargs: object) -> str:
                return next(self.replies)

        with self.assertRaisesRegex(ota.OtaError, "pre-install firmware hash"):
            ota.verify_installed(
                Controller(), argparse.Namespace(target="remote"),
                package, None, old_hash,
            )

    def test_temp_window_includes_setup_and_discovery_budget(self) -> None:
        parser = ota.build_parser()
        default_args = parser.parse_args([
            "release.mota", "remote",
            "--controller-serial", "/dev/controller",
            "--source-serial", "/dev/source",
        ])
        ota.validate_args(default_args, parser)

        args = parser.parse_args([
            "release.mota", "remote",
            "--controller-serial", "/dev/controller",
            "--source-serial", "/dev/source",
            "--temp-radio", "909.950,250,5,5,114",
        ])
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            ota.validate_args(args, parser)

    def test_stage_cleanup_shortens_target_and_relays(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.commands: list[tuple[str, str, str | None]] = []

            def remote_command(
                self, target_name: str, command: str, **kwargs: object
            ) -> str:
                password = kwargs.get("password")
                self.commands.append((target_name, command, password))
                return "OK - temp params for 1 mins"

        controller = Controller()
        args = argparse.Namespace(
            target="remote",
            relay_values=[("relay", "relay-secret")],
            temp_values=(909.95, 250.0, 5, 5, 120),
        )
        ota.shorten_target_temp_window(controller, args)
        ota.shorten_relay_temp_windows(controller, args)
        self.assertEqual(
            controller.commands,
            [
                ("remote", "tempradio 909.95,250,5,5,1", None),
                ("relay", "tempradio 909.95,250,5,5,1", "relay-secret"),
            ],
        )

    def test_source_cleanup_only_changes_a_script_owned_window(self) -> None:
        args = argparse.Namespace(
            source_already_temp=False,
            source_shares_controller=False,
            temp_values=(909.95, 250.0, 5, 5, 120),
        )
        with mock.patch.object(
            ota, "source_cli_command", return_value="OK - temp params for 1 mins"
        ) as source_command:
            self.assertTrue(ota.shorten_source_temp_window(args))
        source_command.assert_called_once_with(
            args, "tempradio 909.95,250,5,5,1", check=True
        )

        args.source_already_temp = True
        with mock.patch.object(ota, "source_cli_command") as source_command:
            self.assertTrue(ota.shorten_source_temp_window(args))
        source_command.assert_not_called()

    def test_shared_source_cleanup_ends_temp_before_binary_restore(self) -> None:
        args = argparse.Namespace(
            source_already_temp=False,
            source_shares_controller=True,
            temp_values=(909.95, 250.0, 5, 5, 120),
        )
        with (
            mock.patch.object(
                ota,
                "source_cli_command",
                return_value="OK - normal radio restore scheduled",
            ) as source_command,
            mock.patch.object(ota.time, "sleep") as sleep,
        ):
            self.assertTrue(ota.shorten_source_temp_window(args))
        source_command.assert_called_once_with(args, "normalradio", check=True)
        sleep.assert_called_once_with(ota.TEMP_RADIO_SWITCH_DELAY_SECONDS)


class Rak3401KnownUnsafeReleaseTests(unittest.TestCase):
    def test_chain_resume_uses_exact_body_hash_not_runtime_label(self) -> None:
        steps = [
            mock.Mock(base_hash=bytes.fromhex("0011223344556677")),
            mock.Mock(base_hash=bytes.fromhex("8899AABBCCDDEEFF")),
        ]
        live = target(
            platform="nrf52",
            base_hash=steps[1].base_hash,
            boot_codecs=1 << ota.MOTA_CODEC_IN_PLACE,
            current_version="v9.9.9",
        )
        self.assertEqual(
            rak_chain.find_resume_index(live, steps, b"FINAL123"), 1
        )

    def test_completed_chain_step_clears_only_its_retained_manifest(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.replies = iter([
                    "OTA | download: ready to install 9/9 id=1234ABCD 2s",
                    "OK dropped session",
                    "OTA | no download | target:2FA509C1",
                ])

            def remote_command(self, _target: str, command: str) -> str:
                self.commands.append(command)
                return next(self.replies)

        controller = Controller()
        step = mock.Mock(number=12, package=mock.Mock(manifest_id="1234ABCD"))
        rak_chain.clear_completed_download(controller, "remote", step)
        self.assertEqual(
            controller.commands, ["ota status", "ota cancel", "ota status"]
        )

    def test_completed_chain_step_refuses_another_retained_manifest(self) -> None:
        class Controller:
            def remote_command(self, _target: str, _command: str) -> str:
                return "OTA | download: ready to install 9/9 id=DEADBEEF 2s"

        step = mock.Mock(number=12, package=mock.Mock(manifest_id="1234ABCD"))
        with self.assertRaisesRegex(ota.OtaError, "retained mOTA DEADBEEF"):
            rak_chain.clear_completed_download(Controller(), "remote", step)

    def test_chain_requires_rescue_command_before_another_transition(self) -> None:
        controller = mock.Mock()
        controller.remote_command.return_value = (
            "OTA: status | install | rescue install <hash16> | cancel"
        )
        rak_chain.require_rescue_capability(controller, "remote")
        controller.remote_command.assert_called_once_with("remote", "ota help")

        controller.remote_command.return_value = "OTA: status | install | cancel"
        with self.assertRaisesRegex(ota.OtaError, "refusing to expose"):
            rak_chain.require_rescue_capability(controller, "remote")

    def test_live_chain_is_blocked_after_failed_physical_step_6(self) -> None:
        self.assertEqual(rak_chain.KNOWN_UNSAFE_STEP, 6)
        self.assertEqual(rak_chain.KNOWN_UNSAFE_VERSION, "1.16.8.7")
        steps = [mock.Mock(target_sha256="") for _ in range(6)]
        steps[5].target_sha256 = rak_chain.KNOWN_UNSAFE_IMAGE_SHA256
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "physical RAK3401 test reached step 6",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=False), steps
            )

    def test_second_failed_chain_is_always_blocked(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(16)]
        steps[5].target_sha256 = (
            rak_chain.KNOWN_FAILED_V11701_SAFE_STEP6_IMAGE_SHA256
        )
        steps[14].target_sha256 = (
            rak_chain.KNOWN_FAILED_V11701_SAFE_STEP15_IMAGE_SHA256
        )
        steps[15].target_sha256 = (
            rak_chain.KNOWN_FAILED_V11701_STEP16_IMAGE_SHA256
        )
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "physical RAK3401 test passed steps 1-15",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=False), steps
            )
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "physical RAK3401 test passed steps 1-15",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=True), steps
            )

    def test_checked_cc310_step16_is_always_blocked(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(16)]
        steps[5].target_sha256 = (
            rak_chain.KNOWN_FAILED_V11701_SAFE_STEP6_IMAGE_SHA256
        )
        steps[14].target_sha256 = (
            rak_chain.KNOWN_FAILED_V11701_SAFE_STEP15_IMAGE_SHA256
        )
        steps[15].target_sha256 = (
            rak_chain.KNOWN_FAILED_V11701_STEP16_IMAGE_SHA256
        )
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "return-code fallback is not sufficient",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=True), steps
            )

    def test_first_v11701_candidate_is_blocked_after_failed_step15(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(15)]
        steps[5].target_sha256 = (
            rak_chain.KNOWN_FAILED_V11701_SAFE_STEP6_IMAGE_SHA256
        )
        steps[14].target_sha256 = (
            rak_chain.KNOWN_FAILED_V11701_STEP15_IMAGE_SHA256
        )
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "passed steps 1-14",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=True), steps
            )

    def test_unrecognized_step15_is_blocked(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(15)]
        steps[5].target_sha256 = (
            rak_chain.KNOWN_FAILED_V11701_SAFE_STEP6_IMAGE_SHA256
        )
        steps[14].target_sha256 = "00" * 32
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "step 15 is not a recognized",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=True), steps
            )

    def test_physically_passed_29_step_release_needs_no_lab_gate(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(29)]
        for number, image_sha256 in rak_chain.PHYSICALLY_PASSED_ANCHORS:
            steps[number - 1].target_sha256 = image_sha256
        rak_chain.require_live_release_safe(
            argparse.Namespace(accept_test_candidate=False), steps
        )
        rak_chain.require_live_release_safe(
            argparse.Namespace(accept_test_candidate=True), steps
        )

    def test_physically_passed_29_step_release_rejects_changed_anchor(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(29)]
        for number, image_sha256 in rak_chain.PHYSICALLY_PASSED_ANCHORS:
            steps[number - 1].target_sha256 = image_sha256
        steps[10].target_sha256 = "00" * 32
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "unrecognized step-11 image",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=True), steps
            )

    def test_superseded_27_step_candidate_is_always_blocked(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(27)]
        steps[5].target_sha256 = rak_chain.SUPERSEDED_27_STEP6_IMAGE_SHA256
        steps[14].target_sha256 = rak_chain.SUPERSEDED_27_STEP15_IMAGE_SHA256
        steps[15].target_sha256 = rak_chain.SUPERSEDED_27_STEP16_IMAGE_SHA256
        steps[-1].target_sha256 = rak_chain.SUPERSEDED_27_FINAL_IMAGE_SHA256
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "superseded 27-step candidate",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=True), steps
            )

    def test_unrecognized_step6_is_blocked(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(6)]
        steps[5].target_sha256 = "00" * 32
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "not a recognized, audited RAK3401 bridge image",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=True), steps
            )


class MotatoolIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.motatool = os.environ.get("MOTATOOL_TEST_BIN")
        if not cls.motatool or not Path(cls.motatool).is_file():
            raise unittest.SkipTest("set MOTATOOL_TEST_BIN to run motatool integration tests")
        subprocess.run([cls.motatool, "--version"], check=True, capture_output=True)

    def test_raw_esp32_zip_becomes_full_mota(self) -> None:
        image = firmware(b"ESP32-new" * 900, VERSION_NEW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("firmware.bin", image)
            work = root / "work"
            work.mkdir()
            _path, package, expected = ota.prepare_package(
                prepare_args(archive_path, self.motatool), target(), work
            )
            self.assertTrue(package.is_full)
            self.assertEqual(expected, ota.parse_endf(image).body_hash)

    def test_bash_wrapper_prepare_only_from_zip(self) -> None:
        image = firmware(b"wrapper-new" * 700, VERSION_NEW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("firmware.bin", image)
            result = subprocess.run(
                [
                    str(Path(__file__).with_name("lora_ota.sh")),
                    str(archive_path), "offline",
                    "--prepare-only", "--platform", "esp32",
                    "--target-id", f"{TARGET:08X}",
                    "--target-hw", "TestBoard",
                    "--motatool", self.motatool,
                    "--work-dir", str(root / "work"),
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Prepared and verified:", result.stdout)

    def test_raw_internal_nrf52_zip_becomes_in_place_delta(self) -> None:
        base_image = firmware(b"nrf-old" * 1000, VERSION_OLD)
        new_image = firmware(b"nrf-new" * 1010, VERSION_NEW)
        base_hash = ota.parse_endf(base_image).body_hash
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_path = root / "running.bin"
            base_path.write_bytes(base_image)
            archive_path = root / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("firmware.bin", new_image)
            work = root / "work"
            work.mkdir()
            _path, package, expected = ota.prepare_package(
                prepare_args(archive_path, self.motatool, base_path),
                target(
                    platform="nrf52", base_hash=base_hash,
                    boot_codecs=1 << ota.MOTA_CODEC_IN_PLACE,
                    current_version="v1.16.0",
                ),
                work,
            )
            self.assertFalse(package.is_full)
            self.assertEqual(package.codec_id, ota.MOTA_CODEC_IN_PLACE)
            self.assertEqual(package.base_hash, base_hash)
            self.assertEqual(expected, ota.parse_endf(new_image).body_hash)

    def test_raw_sd_nrf52_zip_becomes_full_without_base(self) -> None:
        image = firmware(b"nrf-sd-new" * 800, VERSION_NEW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("firmware.bin", image)
            work = root / "work"
            work.mkdir()
            _path, package, _expected = ota.prepare_package(
                prepare_args(archive_path, self.motatool),
                target(platform="nrf52", nrf_sd=True, boot_codecs=1),
                work,
            )
            self.assertTrue(package.is_full)


if __name__ == "__main__":
    unittest.main(verbosity=2)
