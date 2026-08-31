#!/usr/bin/env python3
"""Offline tests for the LoRa OTA orchestration helper."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
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
    nrf_qspi: bool = False,
    boot_codecs: int | None = None,
    boot_version: str | None = None,
    current_version: str | None = None,
) -> ota.TargetInfo:
    return ota.TargetInfo(
        name="remote",
        target_id=TARGET,
        base_hash=base_hash,
        platform=platform,
        nrf_sd=nrf_sd,
        hw_id="TestBoard",
        bootloader_version=boot_version,
        bootloader_abi=2 if platform == "nrf52" else None,
        bootloader_codecs=boot_codecs,
        status="status",
        self_status="self",
        current_version=current_version,
        nrf_qspi=nrf_qspi,
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
        package_build_timeout=ota.DEFAULT_PACKAGE_BUILD_TIMEOUT_SECONDS,
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

    def test_bootloader_packages_are_explicitly_refused(self) -> None:
        blob = bytearray(mota_blob(firmware(b"B" * 5000, VERSION_NEW)))
        blob[8] = ota.MOTA_BOOT_FORMAT_VERSION
        blob[9] |= ota.MOTA_FLAG_SIGNED | ota.MOTA_FLAG_BOOTLOADER
        with self.assertRaisesRegex(ota.OtaError, "bootloader mOTA packages"):
            ota.parse_mota(bytes(blob))

        blob[8] = ota.MOTA_FORMAT_VERSION
        with self.assertRaisesRegex(ota.OtaError, "invalid flags in v2"):
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

    def test_adaptive_preamble_tuples_require_current_participants(self) -> None:
        for temp_values, expected_profile in (
            ((909.95, 250.0, 5, 5, 120), ota.RxpsTempProfile(8, 64)),
            ((909.95, 500.0, 6, 5, 120), ota.RxpsTempProfile(8, 64)),
            ((909.95, 500.0, 5, 5, 120), ota.RxpsTempProfile(8, 128)),
        ):
            with self.subTest(temp_values=temp_values):
                self.assertIsNone(
                    ota.select_rxps_temp_profile(
                        "v1.17.1.4",
                        temp_values,
                        all_participants_support_adaptive_preamble=True,
                    )
                )
                self.assertIsNone(
                    ota.select_rxps_temp_profile(
                        "v1.17.1.5",
                        temp_values,
                        all_participants_support_adaptive_preamble=False,
                    )
                )
                self.assertEqual(
                    ota.select_rxps_temp_profile(
                        "v1.17.1.5",
                        temp_values,
                        all_participants_support_adaptive_preamble=True,
                    ),
                    expected_profile,
                )

    def test_rxps_temp_profiles_cover_only_qualified_fast_tuples(self) -> None:
        qualified = (
            ((909.95, 500.0, 5, 5, 120), 8, 128),
            ((909.95, 500.0, 6, 5, 120), 8, 64),
            ((909.95, 500.0, 7, 5, 120), 7, 32),
            ((909.95, 250.0, 6, 5, 120), 7, 32),
            ((909.95, 125.0, 5, 5, 120), 7, 32),
            ((909.95, 62.5, 5, 5, 120), 10, 16),
        )
        for values, level, preamble in qualified:
            with self.subTest(values=values):
                profile = ota.select_rxps_temp_profile(
                    "v1.17.1.5",
                    values,
                    all_participants_support_adaptive_preamble=True,
                )
                self.assertIsNotNone(profile)
                self.assertEqual(profile.boundary_level, level)
                self.assertEqual(profile.boundary_preamble, preamble)

        self.assertIsNone(
            ota.select_rxps_temp_profile(
                "v1.17.1.5",
                (909.95, 500.0, 8, 5, 120),
                all_participants_support_adaptive_preamble=True,
            )
        )

    def test_unknown_participant_version_fails_closed(self) -> None:
        current = ota.RXPS_ADAPTIVE_PREAMBLE_MIN_VERSION
        self.assertTrue(
            ota.participants_support_adaptive_preamble(
                {"destination": current, "source": current}
            )
        )
        self.assertFalse(
            ota.participants_support_adaptive_preamble(
                {"destination": current, "source": None}
            )
        )

    def test_optional_participant_version_probes_are_bounded(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.relay_calls = 0

            @staticmethod
            def get_firmware_version() -> tuple[str, int]:
                return "v1.17.1.5", ota.parse_version("v1.17.1.5")

            def remote_command(
                self,
                _target: str,
                command: str,
                *,
                retry: bool = True,
                **_kwargs: object,
            ) -> str:
                if command != "ver":
                    raise AssertionError(command)
                if retry:
                    raise AssertionError("optional relay probe was unbounded")
                self.relay_calls += 1
                raise ota.TransmissionError("synthetic relay version loss")

        args = argparse.Namespace(
            source_shares_controller=False,
            source_already_temp=False,
            source_cli_serial="/dev/source",
            source_cli_tcp=None,
            relay_values=[("relay", "secret")],
        )
        controller = Controller()
        with (
            mock.patch.object(
                ota,
                "source_cli_command",
                side_effect=ota.TransmissionError(
                    "synthetic source version loss"
                ),
            ) as source_command,
            mock.patch.object(ota.time, "sleep"),
        ):
            versions = ota.read_lora_ota_participant_versions(
                controller,
                args,
                target(current_version="v1.17.1.5"),
            )
        source_command.assert_called_once_with(args, "ver", bounded=True)
        self.assertEqual(
            controller.relay_calls, ota.TRANSMISSION_RETRY_LIMIT + 1
        )
        self.assertIsNone(versions["source"])
        self.assertIsNone(versions["relay:relay"])

    def test_rxps_config_parser_keeps_legacy_compatibility(self) -> None:
        self.assertEqual(
            ota.parse_rxps_settings("> on,65625,60000", "remote"),
            ota.RxpsSettings(True, 65625, 60000),
        )
        self.assertEqual(
            ota.parse_rxps_settings(
                "> on,level=8,preamble=16,rx=65625,sleep=60000",
                "remote",
            ),
            ota.RxpsSettings(True, 65625, 60000, 8, 16),
        )
        with self.assertRaisesRegex(ota.OtaError, "RXPS state"):
            ota.parse_rxps_settings("> on,31,60000", "remote")
        self.assertEqual(
            ota.parse_rxps_settings(
                "> on,level=8,preamble=0,rx=626,sleep=6398",
                "remote",
            ),
            ota.RxpsSettings(True, 626, 6398, 8, 0),
        )
        self.assertEqual(
            ota.parse_rxps_settings(
                "radio.rxps.config on,level=8,preamble=16,"
                "rx=65625,sleep=60000",
                "local",
            ),
            ota.RxpsSettings(True, 65625, 60000, 8, 16),
        )

    def test_rxps_reader_falls_back_to_legacy_query(self) -> None:
        class LegacyController:
            def __init__(self) -> None:
                self.commands: list[str] = []

            def remote_command(
                self, _target: str, command: str, **_kwargs: object
            ) -> str:
                self.commands.append(command)
                if command == "get radio.rxps.config":
                    return "Unknown command"
                return "> on,65625,60000"

        controller = LegacyController()
        self.assertEqual(
            ota.read_remote_rxps(controller, "remote"),
            ota.RxpsSettings(True, 65625, 60000),
        )
        self.assertEqual(
            controller.commands,
            ["get radio.rxps.config", "get radio.rxps"],
        )

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
        self.assertFalse(chain.legacy_full_airtime)

    def test_package_build_timeout_is_configurable_and_positive(self) -> None:
        parser = ota.build_parser()
        default = parser.parse_args(["release.mota", "remote"])
        self.assertEqual(
            default.package_build_timeout,
            ota.DEFAULT_PACKAGE_BUILD_TIMEOUT_SECONDS,
        )

        custom = parser.parse_args([
            "release.zip", "offline", "--prepare-only", "--platform", "esp32",
            "--target-id", f"{TARGET:08X}", "--package-build-timeout", "7200",
        ])
        ota.validate_args(custom, parser)
        self.assertEqual(custom.package_build_timeout, 7200)

        invalid = parser.parse_args([
            "release.zip", "offline", "--prepare-only", "--platform", "esp32",
            "--target-id", f"{TARGET:08X}", "--package-build-timeout", "0",
        ])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ota.validate_args(invalid, parser)

    def test_airtime_estimator_uses_forward_preamble_contract(self) -> None:
        expected_preambles = (
            (500.0, 7, 32),
            (250.0, 6, 32),
            (125.0, 5, 32),
            (62.5, 5, 32),
            (250.0, 5, 64),
            (500.0, 6, 64),
            (500.0, 5, 128),
            (125.0, 9, 16),
        )
        for bandwidth, sf, preamble in expected_preambles:
            with self.subTest(bandwidth=bandwidth, sf=sf):
                self.assertEqual(
                    ota.wire_preamble_symbols(bandwidth, sf),
                    preamble,
                )
                self.assertEqual(
                    ota.lora_airtime_seconds(184, bandwidth, sf, 5),
                    ota.lora_airtime_seconds(
                        184,
                        bandwidth,
                        sf,
                        5,
                        preamble_symbols=preamble,
                    ),
                )

    def test_rak_chain_full_airtime_is_explicit(self) -> None:
        args = rak_chain.build_parser().parse_args(["--legacy-full-airtime"])
        self.assertTrue(args.legacy_full_airtime)

    def test_offline_sd_nrf52_does_not_require_a_base_hash(self) -> None:
        parser = ota.build_parser()
        args = parser.parse_args([
            "release.zip", "offline", "--prepare-only", "--platform", "nrf52",
            "--target-id", f"{TARGET:08X}", "--nrf-sd",
        ])
        ota.validate_args(args, parser)

    def test_offline_qspi_nrf52_does_not_require_a_base_hash(self) -> None:
        parser = ota.build_parser()
        args = parser.parse_args([
            "release.zip", "offline", "--prepare-only", "--platform", "nrf52",
            "--target-id", f"{TARGET:08X}", "--nrf-qspi",
        ])
        ota.validate_args(args, parser)

    def test_nrf_external_stores_are_mutually_exclusive(self) -> None:
        parser = ota.build_parser()
        args = parser.parse_args([
            "release.zip", "offline", "--prepare-only", "--platform", "nrf52",
            "--target-id", f"{TARGET:08X}", "--nrf-sd", "--nrf-qspi",
        ])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
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

    def test_serial_aliases_cannot_identify_the_same_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "ttyACM0"
            device.touch()
            alias = root / "by-id-radio"
            alias.symlink_to(device)
            parser = ota.build_parser()
            for source_option in ("--source-serial", "--source-cli-serial"):
                arguments = [
                    "release.mota", "remote",
                    "--controller-serial", str(device),
                ]
                if source_option == "--source-cli-serial":
                    arguments.extend((
                        "--source-tcp", "192.0.2.10:5001",
                        source_option, str(alias),
                    ))
                else:
                    arguments.extend((source_option, str(alias)))
                args = parser.parse_args(arguments)
                with self.subTest(source_option=source_option), \
                        contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit):
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


class DebugTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_debug = ota.DEBUG
        ota.DEBUG = False

    def tearDown(self) -> None:
        ota.DEBUG = self.previous_debug

    def test_debug_flag_is_available_in_both_runners(self) -> None:
        generic = ota.build_parser().parse_args(
            ["release.mota", "remote", "--debug"]
        )
        chain = rak_chain.build_parser().parse_args(["--debug"])
        self.assertTrue(generic.debug)
        self.assertTrue(chain.debug)
        self.assertFalse(
            ota.build_parser().parse_args(["release.mota", "remote"]).debug
        )

    def test_run_checked_is_safe_for_direct_module_callers(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["tool"], returncode=0, stdout="ok", stderr=""
        )
        with mock.patch.object(ota.subprocess, "run", return_value=completed):
            result = ota.run_checked(["tool"], label="direct call")
        self.assertEqual(result.stdout, "ok")

    def test_debug_reports_success_and_timeout_output(self) -> None:
        ota.DEBUG = True
        completed = subprocess.CompletedProcess(
            args=["tool"], returncode=0, stdout="hello\n", stderr="warning\n"
        )
        output = io.StringIO()
        with (
            mock.patch.object(ota.subprocess, "run", return_value=completed),
            contextlib.redirect_stdout(output),
        ):
            ota.run_checked(
                ["tool", "argument with spaces"], label="debug success", timeout=5
            )
        rendered = output.getvalue()
        self.assertIn("command: tool 'argument with spaces'", rendered)
        self.assertIn("timeout: 5", rendered)
        self.assertIn("exit code: 0", rendered)
        self.assertIn("hello", rendered)
        self.assertIn("warning", rendered)

        timeout = subprocess.TimeoutExpired(
            ["tool"], 3, output="partial out", stderr="partial err"
        )
        output = io.StringIO()
        with (
            mock.patch.object(ota.subprocess, "run", side_effect=timeout),
            contextlib.redirect_stdout(output),
            self.assertRaisesRegex(ota.OtaError, "timed out"),
        ):
            ota.run_checked(["tool"], label="debug timeout", timeout=3)
        rendered = output.getvalue()
        self.assertIn("partial out", rendered)
        self.assertIn("partial err", rendered)

    def test_run_checked_redacts_secrets_from_debug_command_and_output(self) -> None:
        ota.DEBUG = True
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="reply top-secret", stderr=""
        )
        output = io.StringIO()
        with (
            mock.patch.object(ota.subprocess, "run", return_value=completed),
            contextlib.redirect_stdout(output),
        ):
            ota.run_checked(
                ["tool", "--password", "top-secret"],
                label="redaction test",
                sensitive_values=("top-secret",),
            )
        rendered = output.getvalue()
        self.assertNotIn("top-secret", rendered)
        self.assertIn("<REDACTED>", rendered)

    def test_debug_stream_accepts_and_redacts_bytearray_output(self) -> None:
        ota.DEBUG = True
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            ota.debug_stream(
                "pending output",
                bytearray(b"reply top-secret"),
                ("top-secret",),
            )
        rendered = output.getvalue()
        self.assertNotIn("top-secret", rendered)
        self.assertIn("reply <REDACTED>", rendered)

    def test_redact_text_handles_overlapping_sensitive_values_once(self) -> None:
        rendered = ota.redact_text(
            "short secret and longer secret-suffix",
            ("secret", "secret-suffix", "RED"),
        )
        self.assertEqual(
            rendered,
            "short <REDACTED> and longer <REDACTED>",
        )

    def test_persistent_meshcli_exit_redacts_exception_and_debug_tail(self) -> None:
        ota.DEBUG = True
        secret = "admin-secret"
        session = ota.PersistentMeshcliSession(["meshcli"])
        session.pending = bytearray(
            f"script output: login remote {secret}\nfatal {secret}".encode()
        )
        session.output_queue = mock.Mock()
        session.output_queue.get.side_effect = ota.queue.Empty
        session.process = mock.Mock()
        session.process.poll.return_value = 17
        output = io.StringIO()
        with (
            mock.patch.object(session, "close"),
            contextlib.redirect_stdout(output),
            self.assertRaisesRegex(
                ota.OtaError, "persistent meshcli session exited"
            ) as raised,
        ):
            session._read_frame(
                "FRAME_START", "FRAME_END", 5, (secret,)
            )
        rendered = f"{raised.exception}\n{output.getvalue()}"
        self.assertNotIn(secret, rendered)
        self.assertIn("login remote <REDACTED>", rendered)

    def test_persistent_meshcli_close_redacts_exception_and_debug_tail(self) -> None:
        ota.DEBUG = True
        secret = "admin-secret"
        session = ota.PersistentMeshcliSession(["meshcli"])
        session.pending = bytearray(f"closed after {secret}".encode())
        session.output_queue = mock.Mock()
        session.output_queue.get.return_value = None
        output = io.StringIO()
        with (
            mock.patch.object(session, "close"),
            contextlib.redirect_stdout(output),
            self.assertRaisesRegex(
                ota.OtaError, "persistent meshcli session closed"
            ) as raised,
        ):
            session._read_frame(
                "FRAME_START", "FRAME_END", 5, (secret,)
            )
        rendered = f"{raised.exception}\n{output.getvalue()}"
        self.assertNotIn(secret, rendered)
        self.assertIn("closed after <REDACTED>", rendered)

    def test_persistent_meshcli_timeout_redacts_before_tail_truncation(self) -> None:
        ota.DEBUG = True
        secret = "boundary-secret"
        session = ota.PersistentMeshcliSession(["meshcli"])
        # Put the secret across the old 4096-byte raw-tail boundary. Redacting
        # only after slicing would expose its suffix in debug output.
        session.pending = bytearray(
            ("x" * 10 + secret + "y" * 4090).encode()
        )
        output = io.StringIO()
        with (
            mock.patch.object(ota.time, "monotonic", side_effect=(1.0, 3.0)),
            mock.patch.object(session, "close"),
            contextlib.redirect_stdout(output),
            self.assertRaisesRegex(
                ota.OtaError, "persistent meshcli command timed out"
            ),
        ):
            session._read_frame(
                "FRAME_START", "FRAME_END", 1, (secret,)
            )
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn(secret[-6:], output.getvalue())

    def test_persistent_meshcli_output_limit_closes_without_leaking_detail(
        self,
    ) -> None:
        secret = "admin-secret"
        session = ota.PersistentMeshcliSession(["meshcli"])
        session.pending = bytearray(b"FRAME_START\n")
        session.pending.extend(b"x" * (8 * 1024 * 1024))
        session.pending.extend(secret.encode())
        with (
            mock.patch.object(session, "close") as close,
            self.assertRaisesRegex(
                ota.OtaError, "persistent meshcli output exceeded 8 MiB"
            ) as raised,
        ):
            session._read_frame(
                "FRAME_START", "FRAME_END", 5, (secret,)
            )
        close.assert_called_once_with()
        self.assertNotIn(secret, str(raised.exception))

    def test_persistent_meshcli_success_redacts_frame_and_debug_output(self) -> None:
        ota.DEBUG = True
        secret = "admin-secret"
        session = ota.PersistentMeshcliSession(["meshcli"])
        session.pending = bytearray(
            f"FRAME_START\nlogin remote {secret}\nFRAME_END\n".encode()
        )
        session.process = mock.Mock()
        session.process.poll.return_value = None
        session.process.stdin = mock.Mock()
        output = io.StringIO()
        with (
            tempfile.NamedTemporaryFile() as script,
            contextlib.redirect_stdout(output),
        ):
            result = session.run_script(
                Path(script.name),
                "FRAME_START",
                "FRAME_END",
                5,
                (secret,),
            )
        rendered = f"{result}\n{output.getvalue()}"
        self.assertNotIn(secret, rendered)
        self.assertIn("login remote <REDACTED>", rendered)

    def test_persistent_meshcli_input_error_redacts_exception(self) -> None:
        secret = "admin-secret"
        session = ota.PersistentMeshcliSession(["meshcli"])
        session.process = mock.Mock()
        session.process.poll.return_value = None
        session.process.stdin.write.side_effect = BrokenPipeError(
            f"echoed login remote {secret}"
        )
        with (
            tempfile.NamedTemporaryFile() as script,
            mock.patch.object(session, "close"),
            self.assertRaisesRegex(
                ota.OtaError,
                "persistent meshcli input failed: echoed login remote <REDACTED>",
            ) as raised,
        ):
            session.run_script(
                Path(script.name), "FRAME_START", "FRAME_END", 5, (secret,)
            )
        self.assertNotIn(secret, str(raised.exception))

    def test_meshcli_debug_redacts_admin_password(self) -> None:
        ota.DEBUG = True
        args = argparse.Namespace(
            meshcli="meshcli",
            reply_timeout=20,
            controller_serial="/dev/controller",
            controller_tcp=None,
            controller_ble=None,
            controller_baud=115200,
        )
        controller = ota.Controller(args, "top-secret", persistent=False)
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="echo top-secret", stderr=""
        )
        output = io.StringIO()
        with (
            mock.patch.object(ota, "run_checked", return_value=completed),
            contextlib.redirect_stdout(output),
        ):
            result = controller._execute(
                [
                    "contact_info", "login", "login", "login", "top-secret",
                    "sync_msgs",
                ],
                "redaction test",
            )
        rendered = output.getvalue()
        self.assertNotIn("top-secret", rendered)
        self.assertIn("<REDACTED>", rendered)
        self.assertNotIn("top-secret", result.stdout)

    def test_meshcli_minimum_version_is_enforced(self) -> None:
        old = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="meshcore-cli version v1.5.9", stderr="",
        )
        with (
            mock.patch.object(ota, "require_command"),
            mock.patch.object(ota, "run_checked", return_value=old),
            self.assertRaisesRegex(ota.OtaError, "too old"),
        ):
            ota.require_meshcli_version("meshcli")

        current = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="meshcore-cli version v1.6.0", stderr="",
        )
        with (
            mock.patch.object(ota, "require_command"),
            mock.patch.object(ota, "run_checked", return_value=current),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                ota.require_meshcli_version("meshcli"), (1, 6, 0)
            )


class SourceCliTests(unittest.TestCase):
    @staticmethod
    def source_args() -> argparse.Namespace:
        return argparse.Namespace(
            source_cli_serial=None,
            source_serial="/dev/source",
            source_cli_tcp=None,
        )

    def test_source_rxps_enabled_profile_is_saved_and_disabled(self) -> None:
        args = self.source_args()
        with mock.patch.object(
            ota,
            "source_cli_command",
            side_effect=(
                "radio.rxps.config on,level=8,preamble=16,"
                "rx=18205,sleep=20423",
                "OK - off,18205,20423",
                "radio.rxps.config off,level=8,preamble=16,"
                "rx=18205,sleep=20423",
            ),
        ) as source_command:
            saved = ota.read_source_rxps(args)
            changed = ota.disable_source_rxps(args, saved)

        self.assertEqual(
            saved, ota.RxpsSettings(True, 18205, 20423, 8, 16)
        )
        self.assertTrue(changed)
        self.assertEqual(
            [call.args[1] for call in source_command.call_args_list],
            [
                "get radio.rxps.config",
                "set radio.rxps off",
                "get radio.rxps.config",
            ],
        )

    def test_source_rxps_recovery_record_preserves_exact_setting(self) -> None:
        args = self.source_args()
        args.source_baud = 115200
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        with tempfile.TemporaryDirectory() as directory:
            path = ota.write_source_rxps_recovery(
                Path(directory), args, saved
            )
            payload = json.loads(path.read_text(encoding="ascii"))
            mode = path.stat().st_mode & 0o777
            recovered = ota.read_source_rxps_recovery(path, args)

        self.assertEqual(mode, 0o600)
        self.assertEqual(recovered, saved)
        self.assertEqual(
            payload,
            {
                "connection": {
                    "baud": 115200,
                    "endpoint": "/dev/source",
                    "kind": "serial",
                },
                "restore_command": "set radio.rxps level 8 preamble 16",
                "rxps_enabled": True,
                "rxps_level": 8,
                "rxps_preamble": 16,
                "rxps_rx_us": 18205,
                "rxps_sleep_us": 20423,
            },
        )

    def test_source_rxps_recovery_flushes_file_and_directory(self) -> None:
        args = self.source_args()
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            ota.os, "fsync"
        ) as fsync:
            ota.write_source_rxps_recovery(Path(directory), args, saved)
        expected_calls = 1 if os.name == "nt" else 2
        self.assertEqual(fsync.call_count, expected_calls)

    def test_retired_source_rxps_record_is_no_longer_active(self) -> None:
        args = self.source_args()
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = ota.write_source_rxps_recovery(root, args, saved)
            ota.retire_source_rxps_recovery(path)
            self.assertFalse(path.exists())
            self.assertEqual(list(root.glob(".*.restored-*")), [])

    def test_source_rxps_recovery_record_restores_saved_off_state(self) -> None:
        args = self.source_args()
        with tempfile.TemporaryDirectory() as directory:
            path = ota.write_source_rxps_recovery(
                Path(directory),
                args,
                ota.RxpsSettings(False, 18205, 20423, 8, 16),
            )
            payload = json.loads(path.read_text(encoding="ascii"))
        self.assertEqual(payload["restore_command"], "set radio.rxps off")

    def test_source_rxps_recovery_rejects_a_different_endpoint(self) -> None:
        args = self.source_args()
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        with tempfile.TemporaryDirectory() as directory:
            path = ota.write_source_rxps_recovery(Path(directory), args, saved)
            other = self.source_args()
            other.source_serial = "/dev/other"
            with self.assertRaisesRegex(ota.OtaError, "different CLI endpoint"):
                ota.read_source_rxps_recovery(path, other)
    def test_source_rxps_legacy_query_fallback(self) -> None:
        args = self.source_args()
        with mock.patch.object(
            ota,
            "source_cli_command",
            side_effect=("Unknown command", "radio.rxps on,65625,60000"),
        ) as source_command:
            saved = ota.read_source_rxps(args)

        self.assertEqual(saved, ota.RxpsSettings(True, 65625, 60000))
        self.assertEqual(
            [call.args[1] for call in source_command.call_args_list],
            ["get radio.rxps.config", "get radio.rxps"],
        )

    def test_source_rxps_already_off_is_not_changed(self) -> None:
        args = self.source_args()
        with mock.patch.object(
            ota,
            "source_cli_command",
            return_value=(
                "radio.rxps.config off,level=8,preamble=16,"
                "rx=18205,sleep=20423"
            ),
        ) as source_command:
            saved = ota.read_source_rxps(args)
            changed = ota.disable_source_rxps(args, saved)

        self.assertFalse(changed)
        source_command.assert_called_once_with(args, "get radio.rxps.config")

    def test_source_rxps_disable_requires_off_readback(self) -> None:
        args = self.source_args()
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        with (
            mock.patch.object(
                ota,
                "source_cli_command",
                side_effect=(
                    "OK - off,18205,20423",
                    "radio.rxps.config on,level=8,preamble=16,"
                    "rx=18205,sleep=20423",
                ),
            ),
            self.assertRaisesRegex(ota.OtaError, "did not read back as off"),
        ):
            ota.disable_source_rxps(args, saved)

    def test_source_rxps_disable_retries_explicit_radio_busy(self) -> None:
        args = self.source_args()
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        with (
            mock.patch.object(
                ota,
                "source_cli_command",
                side_effect=(
                    ota.OtaError(
                        "source rejected 'set radio.rxps off': "
                        "Error: radio busy; retry"
                    ),
                    "OK - off,18205,20423",
                    "radio.rxps.config off,level=8,preamble=16,"
                    "rx=18205,sleep=20423",
                ),
            ) as source_command,
            mock.patch.object(ota.time, "sleep") as sleep,
        ):
            self.assertTrue(ota.disable_source_rxps(args, saved))

        self.assertEqual(
            [call.args[1] for call in source_command.call_args_list],
            [
                "set radio.rxps off",
                "set radio.rxps off",
                "get radio.rxps.config",
            ],
        )
        sleep.assert_called_once_with(
            ota.source_rxps_busy_retry_delay(1)
        )

    def test_source_rxps_busy_retry_is_bounded(self) -> None:
        args = self.source_args()
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        busy = ota.OtaError("Error: radio busy; retry")
        with (
            mock.patch.object(
                ota,
                "source_cli_command",
                side_effect=[busy] * (ota.SOURCE_RXPS_BUSY_RETRY_LIMIT + 1),
            ) as source_command,
            mock.patch.object(ota.time, "sleep") as sleep,
            self.assertRaisesRegex(
                ota.OtaError,
                f"remained radio busy after {ota.SOURCE_RXPS_BUSY_RETRY_LIMIT} bounded",
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            ota.disable_source_rxps(args, saved)

        self.assertEqual(
            source_command.call_count,
            ota.SOURCE_RXPS_BUSY_RETRY_LIMIT + 1,
        )
        expected_delays = [
            ota.source_rxps_busy_retry_delay(index)
            for index in range(1, ota.SOURCE_RXPS_BUSY_RETRY_LIMIT + 1)
        ]
        self.assertEqual(len(set(expected_delays)), len(expected_delays))
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list], expected_delays
        )
        self.assertGreater(sum(expected_delays), 9.0)
        self.assertLess(sum(expected_delays), 10.0)

    def test_source_rxps_disable_does_not_retry_other_rejections(self) -> None:
        args = self.source_args()
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        with (
            mock.patch.object(
                ota,
                "source_cli_command",
                side_effect=ota.OtaError("Error: unsupported"),
            ) as source_command,
            mock.patch.object(ota.time, "sleep") as sleep,
            self.assertRaisesRegex(ota.OtaError, "unsupported"),
        ):
            ota.disable_source_rxps(args, saved)

        source_command.assert_called_once_with(args, "set radio.rxps off")
        sleep.assert_not_called()

    def test_source_rxps_restore_uses_saved_level_and_preamble(self) -> None:
        args = self.source_args()
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        disabled = ota.RxpsSettings(False, 18205, 20423, 8, 16)
        with (
            mock.patch.object(
                ota, "read_source_rxps", side_effect=(disabled, saved)
            ) as read_source,
            mock.patch.object(
                ota,
                "source_cli_command",
                side_effect=(
                    ota.OtaError("Error: radio busy; retry"),
                    "OK - level 8,on,18205,20423,preamble=16",
                ),
            ) as source_command,
            mock.patch.object(ota.time, "sleep") as sleep,
        ):
            ota.restore_source_rxps(args, saved)

        self.assertEqual(read_source.call_count, 2)
        self.assertEqual(
            source_command.call_args_list,
            [
                mock.call(args, "set radio.rxps level 8 preamble 16"),
                mock.call(args, "set radio.rxps level 8 preamble 16"),
            ],
        )
        sleep.assert_called_once_with(
            ota.source_rxps_busy_retry_delay(1)
        )

    def test_read_only_target_failure_does_not_mutate_source_rxps(self) -> None:
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        events: list[str] = []

        def source_clock_gate(_args: argparse.Namespace) -> tuple[int, int]:
            events.append("source-clock-gate")
            return (1_800_000_000, 1_800_000_059)

        def controller_clock_gate(_controller: object) -> int:
            events.append("controller-clock-gate")
            return 1_800_000_000

        def fail_target_query(*_args: object) -> ota.TargetInfo:
            events.append("target-query")
            raise ota.OtaError("synthetic failure")

        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory) / "work"
            argv = [
                "release.mota",
                "remote",
                "--controller-serial", "/dev/controller",
                "--source-serial", "/dev/source",
                "--password", "secret",
                "--work-dir", str(work_dir),
                "--yes",
            ]
            with (
                mock.patch.object(ota, "preflight_inputs"),
                mock.patch.object(ota, "preflight_source_cli"),
                mock.patch.object(
                    ota,
                    "ensure_source_clock_gate_safe",
                    side_effect=source_clock_gate,
                ),
                mock.patch.object(
                    ota,
                    "ensure_controller_clock_safe",
                    side_effect=controller_clock_gate,
                ),
                mock.patch.object(
                    ota,
                    "read_source_rxps",
                    side_effect=(saved, saved, saved),
                ) as read_source,
                mock.patch.object(ota, "source_cli_command") as source_command,
                mock.patch.object(ota, "disable_source_rxps") as disable_source,
                mock.patch.object(
                    ota, "query_target", side_effect=fail_target_query
                ),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = ota.main(argv, controller_override=mock.Mock())
            recovery_exists = (
                work_dir / ota.SOURCE_RXPS_RECOVERY_FILE
            ).exists()

        self.assertEqual(result, 2)
        self.assertFalse(recovery_exists)
        self.assertEqual(read_source.call_count, 1)
        disable_source.assert_not_called()
        source_command.assert_not_called()
        self.assertEqual(
            events,
            ["source-clock-gate", "controller-clock-gate", "target-query"],
        )

    def test_rerun_uses_persisted_source_rxps_instead_of_temporary_off(self) -> None:
        original = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        currently_off = ota.RxpsSettings(False, 18205, 20423, 8, 16)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery_path = ota.write_source_rxps_recovery(
                root,
                argparse.Namespace(
                    source_cli_serial=None,
                    source_serial="/dev/source",
                    source_cli_tcp=None,
                    source_baud=115200,
                ),
                original,
            )
            argv = [
                "release.mota",
                "remote",
                "--controller-serial", "/dev/controller",
                "--source-serial", "/dev/source",
                "--password", "secret",
                "--source-rxps-recovery-file", str(recovery_path),
                "--work-dir", str(root / "attempt"),
                "--yes",
            ]
            with (
                mock.patch.object(ota, "preflight_inputs"),
                mock.patch.object(ota, "preflight_source_cli"),
                mock.patch.object(
                    ota,
                    "ensure_source_clock_gate_safe",
                    return_value=(1_800_000_000, 1_800_000_059),
                ),
                mock.patch.object(ota, "ensure_controller_clock_safe"),
                mock.patch.object(
                    ota, "read_source_rxps", return_value=currently_off
                ),
                mock.patch.object(
                    ota, "disable_source_rxps", return_value=False
                ) as disable,
                mock.patch.object(ota, "restore_source_rxps") as restore,
                mock.patch.object(
                    ota, "query_target", side_effect=ota.OtaError("synthetic failure")
                ),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = ota.main(argv, controller_override=mock.Mock())

        self.assertEqual(result, 2)
        disable.assert_not_called()
        restore.assert_called_once_with(mock.ANY, original)

    def test_package_and_confirmation_precede_fresh_source_rxps_disable(self) -> None:
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        image = firmware(b"phase ordering" * 500, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))
        normal = ota.RadioSettings(910.525, 62.5, 7, 5, False)
        controller = mock.Mock()
        controller.get_radio.return_value = normal
        events: list[str] = []
        read_count = 0

        def read_source(_args: argparse.Namespace) -> ota.RxpsSettings:
            nonlocal read_count
            read_count += 1
            events.append(f"source-read-{read_count}")
            return saved

        def prepare(
            _args: argparse.Namespace,
            _target: ota.TargetInfo,
            _work_dir: Path,
        ) -> tuple[Path, ota.MotaInfo, bytes]:
            events.append("package")
            return Path("release.mota"), package, ota.parse_endf(image).body_hash

        def confirm(
            _args: argparse.Namespace,
            _target: ota.TargetInfo,
            _package: ota.MotaInfo,
        ) -> None:
            events.append("confirm")

        def rehearse(*_args: object) -> None:
            events.append("three-minute-preflight")

        def disable(
            _args: argparse.Namespace,
            current: ota.RxpsSettings,
        ) -> bool:
            self.assertEqual(current, saved)
            events.append("disable")
            raise ota.OtaError("stop after ordering assertion")

        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "release.zip", "remote",
                "--controller-serial", "/dev/controller",
                "--source-serial", "/dev/source",
                "--password", "secret",
                "--work-dir", str(Path(directory) / "work"),
                "--yes",
            ]
            with (
                mock.patch.object(ota, "preflight_inputs"),
                mock.patch.object(ota, "preflight_source_cli"),
                mock.patch.object(ota, "ensure_controller_clock_safe"),
                mock.patch.object(
                    ota,
                    "ensure_source_clock_gate_safe",
                    return_value=(1_800_000_000, 1_800_000_059),
                ),
                mock.patch.object(ota, "read_source_rxps", side_effect=read_source),
                mock.patch.object(ota, "query_target", return_value=target()),
                mock.patch.object(ota, "prepare_package", side_effect=prepare),
                mock.patch.object(
                    ota, "read_lora_ota_participant_versions", return_value={}
                ),
                mock.patch.object(
                    ota,
                    "read_remote_rxps",
                    return_value=ota.RxpsSettings(False, 18205, 20423, 8, 16),
                ),
                mock.patch.object(ota, "confirm_update", side_effect=confirm),
                mock.patch.object(
                    ota, "run_temp_radio_preflight", side_effect=rehearse
                ),
                mock.patch.object(ota, "disable_source_rxps", side_effect=disable),
                mock.patch.object(ota, "restore_source_rxps") as restore,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = ota.main(argv, controller_override=controller)

        self.assertEqual(result, 2)
        self.assertEqual(
            events,
            [
                "source-read-1",
                "package",
                "confirm",
                "three-minute-preflight",
                "source-read-2",
                "disable",
            ],
        )
        restore.assert_called_once_with(mock.ANY, saved)

    def test_package_timeout_never_disables_source_rxps(self) -> None:
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        normal = ota.RadioSettings(910.525, 62.5, 7, 5, False)
        controller = mock.Mock()
        controller.get_radio.return_value = normal
        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "release.zip", "remote",
                "--controller-serial", "/dev/controller",
                "--source-serial", "/dev/source",
                "--password", "secret",
                "--work-dir", str(Path(directory) / "work"),
                "--yes",
            ]
            with (
                mock.patch.object(ota, "preflight_inputs"),
                mock.patch.object(ota, "preflight_source_cli"),
                mock.patch.object(ota, "ensure_controller_clock_safe"),
                mock.patch.object(ota, "read_source_rxps", return_value=saved),
                mock.patch.object(ota, "query_target", return_value=target()),
                mock.patch.object(
                    ota,
                    "prepare_package",
                    side_effect=ota.OtaError("timed out while running build mOTA"),
                ),
                mock.patch.object(ota, "disable_source_rxps") as disable,
                mock.patch.object(ota, "restore_source_rxps") as restore,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = ota.main(argv, controller_override=controller)

        self.assertEqual(result, 2)
        disable.assert_not_called()
        restore.assert_not_called()

    def test_confirmation_cancellation_never_disables_source_rxps(self) -> None:
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        image = firmware(b"confirmation cancellation" * 300, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))
        normal = ota.RadioSettings(910.525, 62.5, 7, 5, False)
        controller = mock.Mock()
        controller.get_radio.return_value = normal
        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "release.mota", "remote",
                "--controller-serial", "/dev/controller",
                "--source-serial", "/dev/source",
                "--password", "secret",
                "--work-dir", str(Path(directory) / "work"),
            ]
            with (
                mock.patch.object(ota, "preflight_inputs"),
                mock.patch.object(ota, "preflight_source_cli"),
                mock.patch.object(ota, "ensure_controller_clock_safe"),
                mock.patch.object(ota, "verify_shared_source_identity"),
                mock.patch.object(ota, "read_source_rxps", return_value=saved),
                mock.patch.object(ota, "query_target", return_value=target()),
                mock.patch.object(
                    ota,
                    "prepare_package",
                    return_value=(Path("release.mota"), package, None),
                ),
                mock.patch.object(
                    ota, "read_lora_ota_participant_versions", return_value={}
                ),
                mock.patch.object(
                    ota,
                    "read_remote_rxps",
                    return_value=ota.RxpsSettings(False, 18205, 20423, 8, 16),
                ),
                mock.patch.object(
                    ota,
                    "confirm_update",
                    side_effect=ota.OtaError("operator cancelled"),
                ),
                mock.patch.object(ota, "disable_source_rxps") as disable,
                mock.patch.object(ota, "restore_source_rxps") as restore,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = ota.main(argv, controller_override=controller)

        self.assertEqual(result, 2)
        disable.assert_not_called()
        restore.assert_not_called()

    def test_external_source_rxps_change_before_disable_is_preserved(self) -> None:
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        changed = ota.RxpsSettings(True, 12520, 16400, 9, 32)
        image = firmware(b"external RXPS change" * 300, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))
        controller = mock.Mock()
        controller.get_radio.return_value = ota.RadioSettings(
            910.525, 62.5, 7, 5, False
        )
        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "release.mota", "remote",
                "--controller-serial", "/dev/controller",
                "--source-serial", "/dev/source",
                "--password", "secret",
                "--work-dir", str(Path(directory) / "work"),
                "--yes",
            ]
            with (
                mock.patch.object(ota, "preflight_inputs"),
                mock.patch.object(ota, "preflight_source_cli"),
                mock.patch.object(ota, "ensure_controller_clock_safe"),
                mock.patch.object(ota, "verify_shared_source_identity"),
                mock.patch.object(
                    ota, "read_source_rxps", side_effect=(saved, changed)
                ),
                mock.patch.object(ota, "query_target", return_value=target()),
                mock.patch.object(
                    ota,
                    "prepare_package",
                    return_value=(Path("release.mota"), package, None),
                ),
                mock.patch.object(
                    ota, "read_lora_ota_participant_versions", return_value={}
                ),
                mock.patch.object(
                    ota,
                    "read_remote_rxps",
                    return_value=ota.RxpsSettings(False, 18205, 20423, 8, 16),
                ),
                mock.patch.object(ota, "confirm_update"),
                mock.patch.object(ota, "disable_source_rxps") as disable,
                mock.patch.object(ota, "restore_source_rxps") as restore,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = ota.main(argv, controller_override=controller)

        self.assertEqual(result, 2)
        disable.assert_not_called()
        restore.assert_not_called()

    def test_fresh_source_rxps_read_failure_does_not_restore(self) -> None:
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        image = firmware(b"fresh RXPS read failure" * 300, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))
        controller = mock.Mock()
        controller.get_radio.return_value = ota.RadioSettings(
            910.525, 62.5, 7, 5, False
        )
        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "release.mota", "remote",
                "--controller-serial", "/dev/controller",
                "--source-serial", "/dev/source",
                "--password", "secret",
                "--work-dir", str(Path(directory) / "work"),
                "--yes",
            ]
            with (
                mock.patch.object(ota, "preflight_inputs"),
                mock.patch.object(ota, "preflight_source_cli"),
                mock.patch.object(ota, "ensure_controller_clock_safe"),
                mock.patch.object(ota, "verify_shared_source_identity"),
                mock.patch.object(
                    ota,
                    "read_source_rxps",
                    side_effect=(saved, ota.OtaError("fresh read failed")),
                ),
                mock.patch.object(ota, "query_target", return_value=target()),
                mock.patch.object(
                    ota,
                    "prepare_package",
                    return_value=(Path("release.mota"), package, None),
                ),
                mock.patch.object(
                    ota, "read_lora_ota_participant_versions", return_value={}
                ),
                mock.patch.object(
                    ota,
                    "read_remote_rxps",
                    return_value=ota.RxpsSettings(False, 18205, 20423, 8, 16),
                ),
                mock.patch.object(ota, "confirm_update"),
                mock.patch.object(ota, "disable_source_rxps") as disable,
                mock.patch.object(ota, "restore_source_rxps") as restore,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = ota.main(argv, controller_override=controller)

        self.assertEqual(result, 2)
        disable.assert_not_called()
        restore.assert_not_called()

    def test_source_recovery_file_inside_attempt_is_rejected_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory) / "attempt"
            argv = [
                "release.mota", "remote",
                "--controller-serial", "/dev/controller",
                "--source-serial", "/dev/source",
                "--password", "secret",
                "--work-dir", str(work_dir),
                "--source-rxps-recovery-file",
                str(work_dir / "controller-radio.txt"),
                "--yes",
            ]
            with (
                mock.patch.object(ota, "preflight_inputs"),
                mock.patch.object(ota, "preflight_source_cli") as source_preflight,
                mock.patch.object(ota, "ensure_controller_clock_safe"),
                mock.patch.object(ota, "read_source_rxps") as read_source,
                mock.patch.object(ota, "disable_source_rxps") as disable_source,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = ota.main(argv, controller_override=mock.Mock())

        self.assertEqual(result, 2)
        source_preflight.assert_not_called()
        read_source.assert_not_called()
        disable_source.assert_not_called()

    def test_success_path_source_rxps_restore_failure_is_retried_in_finally(self) -> None:
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        image = firmware(b"source-restore-retry" * 500, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))
        expected_body_hash = ota.parse_endf(image).body_hash
        normal = ota.RadioSettings(910.525, 62.5, 7, 5, False)
        controller = mock.Mock()
        controller.get_radio.return_value = normal
        controller.remote_command.return_value = "OK"
        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "release.mota",
                "remote",
                "--controller-serial", "/dev/controller",
                "--source-serial", "/dev/source",
                "--password", "secret",
                "--work-dir", str(Path(directory) / "work"),
                "--yes",
            ]
            seeder = mock.Mock()
            with contextlib.ExitStack() as stack:
                for name in (
                    "preflight_inputs",
                    "preflight_source_cli",
                    "ensure_source_clock_gate_safe",
                    "ensure_controller_clock_safe",
                    "verify_shared_source_identity",
                    "confirm_update",
                    "run_temp_radio_preflight",
                    "arm_target_temp_radio",
                    "arm_source_temp_radio_once",
                    "switch_controller_to_temp_radio",
                    "find_and_start_pull",
                    "monitor_download",
                    "verify_installed",
                ):
                    stack.enter_context(mock.patch.object(ota, name))
                stack.enter_context(
                    mock.patch.object(ota, "read_source_rxps", return_value=saved)
                )
                stack.enter_context(
                    mock.patch.object(ota, "disable_source_rxps", return_value=True)
                )
                restore_source = stack.enter_context(
                    mock.patch.object(
                        ota,
                        "restore_source_rxps",
                        side_effect=(
                            ota.OtaError("transient restore failure"),
                            None,
                        ),
                    )
                )
                stack.enter_context(
                    mock.patch.object(ota, "query_target", return_value=target())
                )
                stack.enter_context(
                    mock.patch.object(
                        ota,
                        "prepare_package",
                        return_value=(
                            Path("release.mota"),
                            package,
                            expected_body_hash,
                        ),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        ota,
                        "read_lora_ota_participant_versions",
                        return_value={"destination": VERSION_NEW},
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        ota,
                        "read_remote_rxps",
                        return_value=ota.RxpsSettings(False, 0, 0),
                    )
                )
                stack.enter_context(
                    mock.patch.object(ota, "request_install", return_value=True)
                )
                stack.enter_context(
                    mock.patch.object(
                        ota, "shorten_source_temp_window", return_value=True
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        ota,
                        "wait_for_post_install_identity",
                        return_value=(
                            "self body=1 image=2 base_hash="
                            f"{expected_body_hash.hex().upper()}"
                        ),
                    )
                )
                stack.enter_context(
                    mock.patch.object(ota, "SeederProcess", return_value=seeder)
                )
                stack.enter_context(mock.patch.object(ota.time, "sleep"))
                stack.enter_context(
                    mock.patch.object(ota, "source_cli_command", return_value="OK")
                )
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                result = ota.main(argv, controller_override=controller)

        self.assertEqual(result, 2)
        self.assertEqual(restore_source.call_count, 2)

    def test_shared_failure_cleanup_never_persists_temp_radio_tuple(self) -> None:
        normal = ota.RadioSettings(910.525, 62.5, 7, 5, False)
        controller = mock.Mock()
        controller.get_radio.return_value = normal
        current_epoch = int(ota.time.time())
        controller.get_clock.return_value = current_epoch
        package = mock.Mock(
            version="1.17.1.5",
            kind="full",
            target_id=0x1234ABCD,
            manifest_id="01234567",
        )
        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "release.mota", "remote",
                "--controller-tcp", "127.0.0.1:5000",
                "--source-tcp", "127.0.0.1:5001",
                "--source-cli-tcp", "127.0.0.1:5002",
                "--source-shares-controller",
                "--password", "secret",
                "--work-dir", str(Path(directory) / "work"),
                "--yes",
            ]
            with (
                mock.patch.object(ota, "preflight_inputs"),
                mock.patch.object(ota, "preflight_source_cli"),
                mock.patch.object(
                    ota,
                    "ensure_source_clock_gate_safe",
                    return_value=(current_epoch, current_epoch),
                ),
                mock.patch.object(ota, "verify_shared_source_identity"),
                mock.patch.object(
                    ota,
                    "read_source_rxps",
                    return_value=ota.RxpsSettings(False, 0, 0),
                ),
                mock.patch.object(ota, "disable_source_rxps", return_value=False),
                mock.patch.object(ota, "query_target", return_value=target()),
                mock.patch.object(
                    ota,
                    "prepare_package",
                    return_value=(Path("release.mota"), package, None),
                ),
                mock.patch.object(
                    ota, "read_lora_ota_participant_versions", return_value={}
                ),
                mock.patch.object(ota, "read_remote_rxps", return_value=None),
                mock.patch.object(ota, "confirm_update"),
                mock.patch.object(ota, "run_temp_radio_preflight"),
                mock.patch.object(
                    ota,
                    "arm_target_temp_radio",
                    side_effect=ota.OtaError("synthetic handoff failure"),
                ),
                mock.patch.object(ota, "shorten_target_temp_window"),
                mock.patch.object(
                    ota, "shorten_source_temp_window", return_value=False
                ),
                mock.patch.object(ota, "source_cli_command", return_value="OK") as source_cli,
                mock.patch.object(ota.time, "sleep"),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = ota.main(argv, controller_override=controller)

        self.assertEqual(result, 2)
        self.assertIn(
            mock.call(
                mock.ANY,
                "tempradio 909.950,250,5,5,120",
                retry=False,
            ),
            source_cli.call_args_list,
        )
        controller.set_radio.assert_not_called()

    def test_full_companion_tcp_console_command(self) -> None:
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.side_effect = [
            b"===== MeshCore Full Companion Terminal =====\r\n> ",
            b"  OTA seeder | install:disabled | serving:1\r\n> ",
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

    def test_shared_full_terminal_banner_binds_supported_ver_to_binary_key(
        self,
    ) -> None:
        key = "A5" * 32
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.side_effect = [
            (
                "===== MeshCore Full Companion Terminal =====\r\n\r\n"
                f"WELCOME  V4\r\n{key}\r\nCompanion v1.17.1.5\r\n> "
            ).encode("ascii"),
            b"Companion v1.17.1.5 (protocol 1, build test)\r\n> ",
        ]
        args = argparse.Namespace(
            source_cli_serial=None,
            source_serial=None,
            source_cli_tcp="192.0.2.10:5002",
            meshcli="meshcli",
            source_baud=115200,
            shared_source_public_key=key.lower(),
        )
        with mock.patch.object(
            ota.socket, "create_connection", return_value=connection
        ):
            output = ota.source_cli_command(args, "ver")
        self.assertEqual(
            output, "Companion v1.17.1.5 (protocol 1, build test)"
        )
        connection.sendall.assert_called_once_with(b"ver\r\n")

    def test_shared_full_terminal_rejects_banner_for_a_different_key(self) -> None:
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.side_effect = [
            (
                "===== MeshCore Full Companion Terminal =====\r\n\r\n"
                f"WELCOME  V4\r\n{'B6' * 32}\r\n"
                "Companion v1.17.1.5\r\n> "
            ).encode("ascii"),
            b"Companion v1.17.1.5\r\n> ",
        ]
        args = argparse.Namespace(
            source_cli_serial=None,
            source_serial=None,
            source_cli_tcp="192.0.2.10:5002",
            meshcli="meshcli",
            source_baud=115200,
            shared_source_public_key="A5" * 32,
        )
        with (
            mock.patch.object(
                ota.socket, "create_connection", return_value=connection
            ),
            self.assertRaisesRegex(
                ota.OtaError, "terminal identity mismatch"
            ),
        ):
            ota.source_cli_command(args, "ver")

    def test_legacy_tcp_ota_console_reply_is_still_accepted(self) -> None:
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
        ):
            output = ota.source_cli_command(args, "ota status")

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

    def test_already_temp_source_rejects_managed_cli_or_non_tcp_source(self) -> None:
        parser = ota.build_parser()
        cases = (
            [
                "--source-tcp", "192.0.2.10:5001",
                "--source-cli-tcp", "192.0.2.10:5002",
                "--source-already-temp",
            ],
            ["--source-serial", "/dev/source", "--source-already-temp"],
        )
        for source_arguments in cases:
            args = parser.parse_args([
                "release.mota", "remote", "--controller-serial",
                "/dev/controller", *source_arguments,
            ])
            with self.subTest(source_arguments=source_arguments), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
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

    def test_serial_preflight_accepts_ascii_first_full_companion(self) -> None:
        args = argparse.Namespace(
            source_serial="/dev/source",
            source_cli_serial=None,
            source_cli_tcp=None,
        )
        with mock.patch.object(
            ota,
            "source_cli_command",
            return_value="OTA seeder | install:disabled | target:00000000",
        ) as source_command:
            ota.preflight_source_cli(args)

        self.assertFalse(args.source_companion_terminal)
        source_command.assert_called_once_with(
            args, "ota status", check=False
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
                args, "tempradio 909.95,250,5,5,120", retry=False
            )

        wire_command = run.call_args.args[0][-1]
        self.assertEqual(
            wire_command,
            "+++MESHCORE-TERM-STOP\r"
            "+++MESHCORE-TERM-START\r"
            "tempradio 909.95,250,5,5,120\r"
            "+++MESHCORE-TERM-STOP\r",
        )
        self.assertIn("OK - temp params", output)

    def test_source_relative_tempradio_requires_one_shot_handling(self) -> None:
        with self.assertRaisesRegex(ota.OtaError, "state-aware"):
            ota.source_cli_command(
                argparse.Namespace(),
                "tempradio 909.95,250,5,5,120",
            )

    def test_serial_companion_seeder_uses_direct_mota_preamble(self) -> None:
        args = argparse.Namespace(
            motatool="motatool",
            source_serial="/dev/source",
            source_tcp=None,
            source_baud=115200,
            source_companion_terminal=True,
        )
        seeder = ota.SeederProcess(args, Path("/served"), Path("/work"))
        self.assertEqual(
            seeder.command,
            [
                "motatool", "serve", "--dir", "/served", "-v",
                "--serial", "/dev/source", "--baud", "115200",
            ],
        )

    def test_seeder_readiness_requires_device_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            args = argparse.Namespace(
                motatool="motatool",
                source_serial="/dev/source",
                source_tcp=None,
                source_baud=115200,
                seeder_start_wait=1.0,
            )
            seeder = ota.SeederProcess(args, Path("/served"), work_dir)
            seeder.process = mock.MagicMock()
            seeder.process.poll.return_value = None
            seeder.log_path.write_text(
                "[host] serving /served\n[dev] COUNT -> 3\n",
                encoding="utf-8",
            )

            seeder._wait_until_attached()

    def test_seeder_readiness_surfaces_device_attach_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            args = argparse.Namespace(
                motatool="motatool",
                source_serial="/dev/source",
                source_tcp=None,
                source_baud=115200,
                seeder_start_wait=1.0,
            )
            seeder = ota.SeederProcess(args, Path("/served"), work_dir)
            seeder.process = mock.MagicMock()
            seeder.process.poll.return_value = None
            seeder.log_path.write_text(
                "[dev] ERR folder source unavailable\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ota.OtaError, "rejected by the device"):
                seeder._wait_until_attached()

    def test_seeder_readiness_times_out_without_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            args = argparse.Namespace(
                motatool="motatool",
                source_serial="/dev/source",
                source_tcp=None,
                source_baud=115200,
                seeder_start_wait=1.0,
            )
            seeder = ota.SeederProcess(args, Path("/served"), work_dir)
            seeder.process = mock.MagicMock()
            seeder.process.poll.return_value = None
            seeder.log_path.write_text(
                "[host] serving /served\n", encoding="utf-8"
            )

            with (
                mock.patch.object(ota.time, "monotonic", side_effect=(10.0, 11.0)),
                self.assertRaisesRegex(ota.OtaError, "device COUNT"),
            ):
                seeder._wait_until_attached()

    def test_ascii_first_full_companion_seeder_uses_direct_preamble(self) -> None:
        args = argparse.Namespace(
            motatool="motatool",
            source_serial="/dev/source",
            source_tcp=None,
            source_baud=115200,
            source_companion_terminal=False,
        )
        seeder = ota.SeederProcess(args, Path("/served"), Path("/work"))
        self.assertEqual(
            seeder.command,
            [
                "motatool", "serve", "--dir", "/served", "-v",
                "--serial", "/dev/source", "--baud", "115200",
            ],
        )


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
        self.assertIn("exact-board OTAFIX", reason)

    def test_sd_nrf52_accepts_full_when_bootloader_does(self) -> None:
        full = ota.parse_mota(mota_blob(self.new_image))
        nrf = target(platform="nrf52", nrf_sd=True, boot_codecs=1)
        self.assertTrue(ota.compatible_mota(full, nrf)[0])

    def test_qspi_nrf52_accepts_full_when_bootloader_does(self) -> None:
        full = ota.parse_mota(mota_blob(self.new_image))
        nrf = target(platform="nrf52", nrf_qspi=True, boot_codecs=1)
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

    def test_raw_package_build_uses_configured_timeout(self) -> None:
        image = firmware(b"configured build timeout" * 300, VERSION_NEW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("firmware.bin", image)
            args = prepare_args(archive_path, "motatool")
            args.package_build_timeout = 4321
            work = root / "work"
            work.mkdir()

            def run_tool(
                command: list[str], *, label: str, timeout: float | None = None,
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                if label == "build mOTA":
                    output = Path(command[command.index("--out") + 1])
                    output.write_bytes(mota_blob(image))
                return subprocess.CompletedProcess(
                    args=command, returncode=0, stdout="OK", stderr=""
                )

            with mock.patch.object(
                ota, "run_checked", side_effect=run_tool
            ) as run:
                _path, package, _expected = ota.prepare_package(
                    args, target(), work
                )

        self.assertTrue(package.is_full)
        build_call = next(
            call for call in run.call_args_list
            if call.kwargs["label"] == "build mOTA"
        )
        self.assertEqual(build_call.kwargs["timeout"], 4321)
        verify_call = next(
            call for call in run.call_args_list
            if call.kwargs["label"].startswith("verify ")
        )
        self.assertEqual(verify_call.kwargs["timeout"], 120)

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

    def test_completed_previous_session_waits_for_staged_verification(self) -> None:
        args = self.args()
        args.clear_completed_manifest = "DEADBEEF"
        args.clear_completed_on_body_hash = "0011223344556677"
        controller = self.Controller([
            "OTA | download: verifying staged blocks 9/9 id=DEADBEEF 1s",
            "OTA | download: verifying staged blocks 9/9 id=DEADBEEF 2s",
            "OTA | download: ready to install 9/9 id=DEADBEEF 3s",
            "self body=1 image=2 base_hash=0011223344556677",
            "OK dropped session",
            "OTA | no download",
            "Updates 1/1",
            f"OK pulling mid={self.package.manifest_id} -> flash (primary traffic)",
        ])
        seeder = mock.Mock()
        with mock.patch.object(ota.time, "sleep"):
            ota.find_and_start_pull(controller, args, self.package, seeder)
        self.assertEqual(
            controller.commands,
            [
                "ota status", "ota status", "ota status", "ota self",
                "ota cancel", "ota status", "ota ls",
                f"ota pull {self.package.manifest_id} flash",
            ],
        )
        seeder.ensure_running.assert_any_call(
            "while waiting for a completed previous manifest"
        )

    def test_completed_previous_session_does_not_cancel_idle_manager(self) -> None:
        args = self.args()
        args.clear_completed_manifest = "DEADBEEF"
        args.clear_completed_on_body_hash = "0011223344556677"
        controller = self.Controller([
            "OTA | download: verifying staged blocks 9/9 id=DEADBEEF 1s",
            "OTA | no download",
            "self body=1 image=2 base_hash=0011223344556677",
            "Updates 1/1",
            f"OK pulling mid={self.package.manifest_id} -> flash (primary traffic)",
        ])
        with mock.patch.object(ota.time, "sleep"):
            ota.find_and_start_pull(controller, args, self.package)
        self.assertEqual(
            controller.commands,
            [
                "ota status", "ota status", "ota self", "ota ls",
                f"ota pull {self.package.manifest_id} flash",
            ],
        )

    def test_completed_previous_verification_refuses_changed_manifest(self) -> None:
        args = self.args()
        args.clear_completed_manifest = "DEADBEEF"
        args.clear_completed_on_body_hash = "0011223344556677"
        controller = self.Controller([
            "OTA | download: verifying staged blocks 9/9 id=DEADBEEF 1s",
            "OTA | download: ready to install 9/9 id=CAFEBABE 2s",
        ])
        with (
            mock.patch.object(ota.time, "sleep"),
            self.assertRaisesRegex(ota.OtaError, "changed during"),
        ):
            ota.find_and_start_pull(controller, args, self.package)

    def test_completed_previous_verification_refuses_failed_store(self) -> None:
        args = self.args()
        args.clear_completed_manifest = "DEADBEEF"
        args.clear_completed_on_body_hash = "0011223344556677"
        controller = self.Controller([
            "OTA | download: verifying staged blocks 9/9 id=DEADBEEF 1s",
            "OTA | download: failed (hash) 9/9 id=DEADBEEF 2s",
        ])
        with (
            mock.patch.object(ota.time, "sleep"),
            self.assertRaisesRegex(ota.OtaError, "failed staged-block"),
        ):
            ota.find_and_start_pull(controller, args, self.package)

    def test_completed_previous_verification_refuses_incomplete_store(self) -> None:
        args = self.args()
        args.clear_completed_manifest = "DEADBEEF"
        args.clear_completed_on_body_hash = "0011223344556677"
        controller = self.Controller([
            "OTA | download: verifying staged blocks 9/9 id=DEADBEEF 1s",
            "OTA | download: downloading 8/9 id=DEADBEEF 2s",
        ])
        with (
            mock.patch.object(ota.time, "sleep"),
            self.assertRaisesRegex(ota.OtaError, "became incomplete"),
        ):
            ota.find_and_start_pull(controller, args, self.package)

    def test_completed_previous_verification_has_bounded_timeout(self) -> None:
        args = self.args()
        args.clear_completed_manifest = "DEADBEEF"
        args.clear_completed_on_body_hash = "0011223344556677"
        controller = self.Controller([
            "OTA | download: verifying staged blocks 9/9 id=DEADBEEF 1s",
            "OTA | download: verifying staged blocks 9/9 id=DEADBEEF 2s",
        ])
        with (
            mock.patch.object(ota.time, "monotonic", side_effect=[0.0, 0.0, 1.0]),
            mock.patch.object(ota.time, "sleep"),
            self.assertRaisesRegex(ota.OtaError, "timed out waiting"),
        ):
            ota.find_and_start_pull(controller, args, self.package)

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

    def test_pull_accepts_store_resume_confirmation(self) -> None:
        controller = self.Controller([
            "OTA | no download",
            "Updates 1/1",
            f"OK resuming mid={self.package.manifest_id} -> flash (primary traffic)",
        ])
        ota.find_and_start_pull(controller, self.args(), self.package)
        self.assertEqual(
            controller.commands,
            ["ota status", "ota ls", f"ota pull {self.package.manifest_id} flash"],
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

    def test_lost_discovery_reply_still_attempts_exact_pull_and_reconciles(self) -> None:
        controller = mock.Mock()
        controller.remote_command.side_effect = (
            "OTA | no download",
            ota.TransmissionError("discovery reply lost"),
            ota.TransmissionError("pull reply lost"),
            f"OTA | download: downloading 1/9 id={self.package.manifest_id} 2s",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            ota.find_and_start_pull(controller, self.args(), self.package)
        self.assertEqual(
            controller.remote_command.call_args_list,
            [
                mock.call("remote", "ota status"),
                mock.call("remote", "ota ls", retry=False),
                mock.call(
                    "remote",
                    f"ota pull {self.package.manifest_id} flash",
                    retry=False,
                ),
                mock.call("remote", "ota status"),
            ],
        )
        self.assertIn("`ota ls` reply was lost", output.getvalue())
        self.assertIn("pull reply was lost, but session", output.getvalue())

    def test_no_such_update_waits_then_repeats_one_discovery_and_pull(self) -> None:
        controller = mock.Mock()
        controller.remote_command.side_effect = (
            "OTA | no download",
            "No updates seen yet",
            "ERR no such update",
            "Updates 1/1",
            f"OK pulling mid={self.package.manifest_id} -> flash (primary traffic)",
        )
        args = self.args()
        args.discovery_timeout = 60
        with mock.patch.object(ota.time, "sleep") as sleep:
            ota.find_and_start_pull(controller, args, self.package)
        self.assertEqual(
            controller.remote_command.call_args_list,
            [
                mock.call("remote", "ota status"),
                mock.call("remote", "ota ls", retry=False),
                mock.call(
                    "remote",
                    f"ota pull {self.package.manifest_id} flash",
                    retry=False,
                ),
                mock.call("remote", "ota ls", retry=False),
                mock.call(
                    "remote",
                    f"ota pull {self.package.manifest_id} flash",
                    retry=False,
                ),
            ],
        )
        sleep.assert_called_once_with(args.discovery_interval)


class TempRadioClockSafetyTests(unittest.TestCase):
    HOST_EPOCH = 1_800_000_000

    class Controller:
        def __init__(self, epochs: list[int]) -> None:
            self.epochs = iter(epochs)
            self.sync_calls = 0
            self.read_calls = 0

        def get_clock(self, *, timeout: float | None = None) -> int:
            if timeout is None or timeout <= 0:
                raise AssertionError("clock read was not bounded")
            self.read_calls += 1
            return next(self.epochs)

        def sync_clock_forward(self, *, timeout: float | None = None) -> None:
            if timeout is None or timeout <= 0:
                raise AssertionError("clock sync was not bounded")
            self.sync_calls += 1

    def test_rebooted_controller_months_behind_is_advanced_before_use(
        self,
    ) -> None:
        controller = self.Controller([
            1_700_000_000,
            self.HOST_EPOCH,
        ])
        with mock.patch.object(
            ota.time, "time", return_value=float(self.HOST_EPOCH)
        ):
            result = ota.ensure_controller_clock_safe(controller)

        self.assertEqual(result, self.HOST_EPOCH)
        self.assertEqual(controller.sync_calls, 1)
        self.assertEqual(controller.read_calls, 2)

    def test_controller_at_ten_minute_lead_is_preserved(self) -> None:
        controller = self.Controller([
            self.HOST_EPOCH + ota.TEMP_RADIO_CLOCK_MAX_AHEAD_SECONDS
        ])
        with mock.patch.object(
            ota.time, "time", return_value=float(self.HOST_EPOCH)
        ):
            result = ota.ensure_controller_clock_safe(controller)

        self.assertEqual(
            result,
            self.HOST_EPOCH + ota.TEMP_RADIO_CLOCK_MAX_AHEAD_SECONDS,
        )
        self.assertEqual(controller.sync_calls, 0)

    def test_controller_beyond_ten_minute_lead_fails_closed(self) -> None:
        controller = self.Controller([
            self.HOST_EPOCH
            + ota.TEMP_RADIO_CLOCK_MAX_AHEAD_SECONDS
            + 1
        ])
        with (
            mock.patch.object(
                ota.time, "time", return_value=float(self.HOST_EPOCH)
            ),
            self.assertRaisesRegex(ota.OtaError, "ahead"),
        ):
            ota.ensure_controller_clock_safe(controller)
        self.assertEqual(controller.sync_calls, 0)

    def test_controller_forward_sync_rejects_backward_readback(self) -> None:
        controller = self.Controller([
            self.HOST_EPOCH - 3_600,
            self.HOST_EPOCH - 3_601,
        ])
        with (
            mock.patch.object(
                ota.time, "time", return_value=float(self.HOST_EPOCH)
            ),
            self.assertRaisesRegex(ota.OtaError, "moved backward"),
        ):
            ota.ensure_controller_clock_safe(controller)

    def test_source_lost_set_reply_is_resolved_by_forward_readback(self) -> None:
        args = argparse.Namespace()
        host_epoch = self.HOST_EPOCH + 50
        replies: list[object] = [
            "07:30 - 15/1/2027 UTC",
            ota.TransmissionError("serial reply lost"),
            "08:03 - 15/1/2027 UTC",
        ]
        with (
            mock.patch.object(
                ota, "source_cli_command", side_effect=replies
            ) as source_command,
            mock.patch.object(
                ota.time, "time", return_value=float(host_epoch)
            ),
        ):
            window = ota.ensure_source_clock_safe(args)

        guarded_minimum = (
            host_epoch + ota.TEMP_RADIO_SOURCE_CLOCK_PIN_LEAD_SECONDS
        )
        requested = ((guarded_minimum + 59) // 60) * 60
        self.assertEqual(requested % 60, 0)
        self.assertEqual(window, (requested, requested + 59))
        self.assertEqual(
            [call.args[1] for call in source_command.call_args_list],
            ["clock", f"time {requested}", "clock"],
        )
        self.assertFalse(source_command.call_args_list[1].kwargs["retry"])

    def test_source_host_inside_displayed_minute_never_writes_backward(
        self,
    ) -> None:
        # Physical regression: terminal showed 09:38, while host time was
        # 09:38:50. The old code wrote that exact host epoch even though the
        # hidden source seconds could already be 51-59.
        minute_start = 1_788_082_680
        expected_window = (minute_start, minute_start + 59)
        args = argparse.Namespace()
        for hidden_second in (0, 50, 59):
            with self.subTest(hidden_second=hidden_second):
                host_epoch = minute_start + hidden_second
                with (
                    mock.patch.object(
                        ota,
                        "source_cli_command",
                        return_value="09:38 - 30/8/2026 UTC",
                    ) as source_command,
                    mock.patch.object(
                        ota.time, "time", return_value=float(host_epoch)
                    ),
                ):
                    window = ota.ensure_source_clock_safe(args)

                self.assertEqual(window, expected_window)
                source_command.assert_called_once_with(
                    args, "clock", bounded=True
                )

    def test_source_known_future_minute_is_preserved_without_write(self) -> None:
        args = argparse.Namespace()
        with (
            mock.patch.object(
                ota,
                "source_cli_command",
                return_value="08:01 - 15/1/2027 UTC",
            ) as source_command,
            mock.patch.object(
                ota.time, "time", return_value=float(self.HOST_EPOCH)
            ),
        ):
            window = ota.ensure_source_clock_safe(args)

        self.assertEqual(
            window, (self.HOST_EPOCH + 60, self.HOST_EPOCH + 119)
        )
        source_command.assert_called_once_with(args, "clock", bounded=True)

    def test_source_backward_rejection_is_read_back_once_not_replayed(
        self,
    ) -> None:
        args = argparse.Namespace()
        requested = (
            self.HOST_EPOCH
            + ota.TEMP_RADIO_SOURCE_CLOCK_PIN_LEAD_SECONDS
        )
        for rejection in (
            "(ERR: clock cannot go backwards)",
            ota.OtaError(
                "source rejected fixed time: ERR: clock cannot go backwards"
            ),
        ):
            with self.subTest(rejection=type(rejection).__name__):
                replies: list[object] = [
                    "07:30 - 15/1/2027 UTC",
                    rejection,
                    "08:03 - 15/1/2027 UTC",
                ]
                with (
                    mock.patch.object(
                        ota, "source_cli_command", side_effect=replies
                    ) as source_command,
                    mock.patch.object(
                        ota.time, "time", return_value=float(self.HOST_EPOCH)
                    ),
                ):
                    window = ota.ensure_source_clock_safe(args)

                self.assertEqual(
                    [call.args[1] for call in source_command.call_args_list],
                    ["clock", f"time {requested}", "clock"],
                )
                self.assertEqual(
                    window, (self.HOST_EPOCH + 180, self.HOST_EPOCH + 239)
                )

    def test_source_forward_write_must_reach_guarded_epoch(self) -> None:
        args = argparse.Namespace()
        replies = [
            "07:30 - 15/1/2027 UTC",
            "OK - clock set: 08:02 - 15/1/2027 UTC",
            "08:01 - 15/1/2027 UTC",
        ]
        with (
            mock.patch.object(ota, "source_cli_command", side_effect=replies),
            mock.patch.object(
                ota.time, "time", return_value=float(self.HOST_EPOCH)
            ),
            self.assertRaisesRegex(ota.OtaError, "requested guarded epoch"),
        ):
            ota.ensure_source_clock_safe(args)

    def test_source_host_backward_step_cannot_reduce_guarded_base(self) -> None:
        args = argparse.Namespace()
        replies = ["07:30 - 15/1/2027 UTC"]
        with (
            mock.patch.object(
                ota, "source_cli_command", side_effect=replies
            ) as source_command,
            mock.patch.object(
                ota.time,
                "time",
                side_effect=(
                    float(self.HOST_EPOCH),
                    float(self.HOST_EPOCH - 600),
                ),
            ),
            self.assertRaisesRegex(ota.OtaError, "ten-minute drift policy"),
        ):
            ota.ensure_source_clock_safe(args)

        # The host stepped backward too far to prove a policy-safe guarded
        # epoch, so no state-changing command was attempted.
        source_command.assert_called_once_with(args, "clock", bounded=True)

    def test_source_clock_gate_inspects_temp_work_before_clock(self) -> None:
        args = argparse.Namespace(
            source_serial="/dev/source",
            source_cli_serial=None,
            source_cli_tcp=None,
        )
        events: list[str] = []

        def status(*_args: object, **_kwargs: object) -> tuple[str, None]:
            events.append("immediate-status")
            return "inactive", None

        def schedule(*_args: object, **_kwargs: object) -> str:
            events.append("fixed-schedule")
            return "  -> > -none-\r\n> "

        def clock(*_args: object, **_kwargs: object) -> tuple[int, int]:
            events.append("clock")
            return self.HOST_EPOCH, self.HOST_EPOCH + 59

        with (
            mock.patch.object(
                ota, "read_source_temp_radio_status", side_effect=status
            ),
            mock.patch.object(
                ota, "optional_source_cli_command", side_effect=schedule
            ),
            mock.patch.object(
                ota, "ensure_source_clock_safe", side_effect=clock
            ),
        ):
            result = ota.ensure_source_clock_gate_safe(args)

        self.assertEqual(result, (self.HOST_EPOCH, self.HOST_EPOCH + 59))
        self.assertEqual(events, ["immediate-status", "fixed-schedule", "clock"])

    def test_source_clock_gate_refuses_active_or_pending_work(self) -> None:
        args = argparse.Namespace(
            source_serial="/dev/source",
            source_cli_serial=None,
            source_cli_tcp=None,
        )
        for state in ("active", "pending"):
            with self.subTest(state=state):
                with (
                    mock.patch.object(
                        ota,
                        "read_source_temp_radio_status",
                        return_value=(state, mock.Mock()),
                    ),
                    mock.patch.object(
                        ota, "optional_source_cli_command"
                    ) as schedule,
                    mock.patch.object(ota, "ensure_source_clock_safe") as clock,
                    self.assertRaisesRegex(
                        ota.OtaError, "active or pending TempRadio work"
                    ),
                ):
                    ota.ensure_source_clock_gate_safe(args)
                schedule.assert_not_called()
                clock.assert_not_called()

    def test_source_clock_gate_refuses_existing_fixed_schedule(self) -> None:
        args = argparse.Namespace(
            source_serial="/dev/source",
            source_cli_serial=None,
            source_cli_tcp=None,
        )
        with (
            mock.patch.object(
                ota,
                "read_source_temp_radio_status",
                return_value=("inactive", None),
            ),
            mock.patch.object(
                ota,
                "optional_source_cli_command",
                return_value="  -> > 1:909.95,250,5,5@100-200\r\n> ",
            ),
            mock.patch.object(ota, "ensure_source_clock_safe") as clock,
            self.assertRaisesRegex(ota.OtaError, "scheduled TempRadio work"),
        ):
            ota.ensure_source_clock_gate_safe(args)
        clock.assert_not_called()

    def test_source_ten_minute_display_is_too_ambiguous_to_accept(self) -> None:
        args = argparse.Namespace()
        with (
            mock.patch.object(
                ota,
                "source_cli_command",
                return_value="08:10 - 15/1/2027 UTC",
            ) as source_command,
            mock.patch.object(
                ota.time, "time", return_value=float(self.HOST_EPOCH)
            ),
            self.assertRaisesRegex(ota.OtaError, "minute resolution"),
        ):
            ota.ensure_source_clock_safe(args)
        source_command.assert_called_once_with(args, "clock", bounded=True)

    def test_invalid_or_ambiguous_text_clock_is_rejected(self) -> None:
        with self.assertRaisesRegex(ota.OtaError, "2 unambiguous"):
            ota.parse_source_clock_window(
                "08:00 - 15/1/2027 UTC\n08:01 - 15/1/2027 UTC",
                "source",
            )

    def test_remote_clock_pin_loss_is_read_back_without_replay(self) -> None:
        class Controller:
            reply_timeout = 20

            def __init__(self) -> None:
                self.epochs = iter((1_000, 1_120))
                self.commands: list[tuple[str, bool | None]] = []

            def get_contact_clock(
                self,
                _target: str,
                _key: str,
                *,
                timeout: float | None = None,
            ) -> int:
                if timeout is None or timeout <= 0:
                    raise AssertionError("unbounded clock read")
                return next(self.epochs)

            def remote_command(
                self,
                _target: str,
                command: str,
                **kwargs: object,
            ) -> str:
                self.commands.append((command, kwargs.get("retry")))
                raise ota.TransmissionError("lost one-shot fixed reply")

        controller = Controller()
        with (
            mock.patch.object(ota.time, "time", return_value=1_000.0),
            mock.patch.object(ota.time, "monotonic", return_value=0.0),
            mock.patch.object(ota.time, "sleep") as sleep,
        ):
            sample = ota.pin_remote_clock_forward(
                controller, "remote", "AA" * 32
            )

        self.assertEqual(sample.epoch, 1_120)
        self.assertEqual(
            controller.commands,
            [("time 1120", False)],
        )
        sleep.assert_not_called()

    def test_remote_clock_pin_does_not_add_lead_to_existing_lead(self) -> None:
        controller = mock.Mock()
        controller.reply_timeout = 20
        controller.get_contact_clock.side_effect = (1_300, 1_301)
        controller.remote_command.return_value = (
            "OK - clock set: 08:00 - 15/1/2027 UTC"
        )
        with (
            mock.patch.object(ota.time, "time", return_value=1_000.0),
            mock.patch.object(ota.time, "monotonic", return_value=0.0),
        ):
            sample = ota.pin_remote_clock_forward(
                controller, "remote", "AA" * 32
            )

        self.assertEqual(sample.epoch, 1_301)
        self.assertEqual(
            controller.remote_command.call_args.args[1], "time 1301"
        )

    def test_unreconciled_remote_clock_write_is_never_replayed(self) -> None:
        controller = mock.Mock()
        controller.reply_timeout = 20
        controller.get_contact_clock.side_effect = (1_000, 1_001)
        controller.remote_command.side_effect = ota.TransmissionError(
            "lost without delivery"
        )
        with (
            mock.patch.object(ota.time, "time", return_value=1_000.0),
            mock.patch.object(ota.time, "monotonic", return_value=0.0),
            self.assertRaisesRegex(ota.OtaError, "was not replayed"),
        ):
            ota.pin_remote_clock_forward(
                controller, "remote", "AA" * 32
            )
        controller.remote_command.assert_called_once()

    def test_remote_clock_over_ten_minutes_ahead_is_not_mutated(self) -> None:
        controller = mock.Mock()
        controller.get_contact_clock.return_value = 1_601
        with (
            mock.patch.object(ota.time, "time", return_value=1_000.0),
            mock.patch.object(ota.time, "monotonic", return_value=0.0),
            self.assertRaisesRegex(ota.OtaError, "maximum allowed"),
        ):
            ota.pin_remote_clock_forward(
                controller, "remote", "AA" * 32
            )
        controller.remote_command.assert_not_called()


class TempRadioNormalBaselineBudgetTests(unittest.TestCase):
    class Clock:
        def __init__(self) -> None:
            self.now = 0.0
            self.sleeps: list[float] = []

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds

    def test_read_only_baseline_survives_four_consecutive_reply_losses(
        self,
    ) -> None:
        clock = self.Clock()
        expected = target(base_hash=b"\x01\x23\x45\x67\x89\xAB\xCD\xEF")

        class Controller:
            reply_timeout = 20

            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []
                self.status_attempts = 0

            def remote_command(
                self,
                _target: str,
                command: str,
                **kwargs: object,
            ) -> str:
                self.calls.append((command, kwargs))
                if command == "ota status":
                    self.status_attempts += 1
                    if self.status_attempts <= 4:
                        # Model the physical 20-second reply wait before each
                        # missed read-only response.
                        clock.now += 20
                        raise ota.TransmissionError("missed status reply")
                    return (
                        "OTA | no download | "
                        f"target:{expected.target_id:08X} hw={expected.hw_id}"
                    )
                if command == "ota self":
                    return (
                        "self body=1 image=2 "
                        f"base_hash={expected.base_hash.hex().upper()}"
                    )
                raise AssertionError(command)

        controller = Controller()
        with (
            mock.patch.object(
                ota.time, "monotonic", side_effect=clock.monotonic
            ),
            mock.patch.object(ota.time, "sleep", side_effect=clock.sleep),
        ):
            ota.require_destination_preflight_identity(
                controller,
                argparse.Namespace(target="remote"),
                expected,
                "normal-channel baseline",
                deadline=(
                    clock.monotonic()
                    + ota.TEMP_RADIO_NORMAL_BASELINE_TIMEOUT_SECONDS
                ),
                retry_limit=ota.TEMP_RADIO_NORMAL_BASELINE_RETRY_LIMIT,
                deadline_label="normal-channel baseline proof budget",
            )

        self.assertEqual(controller.status_attempts, 5)
        self.assertEqual(
            [command for command, _kwargs in controller.calls],
            ["ota status"] * 5 + ["ota self"],
        )
        self.assertTrue(
            all(
                kwargs.get("retry") is False
                for _command, kwargs in controller.calls
            )
        )
        self.assertTrue(
            all(
                float(kwargs["operation_timeout"])
                <= ota.TEMP_RADIO_PREFLIGHT_OPERATION_TIMEOUT_SECONDS
                for _command, kwargs in controller.calls
            )
        )
        self.assertLess(
            clock.now, ota.TEMP_RADIO_NORMAL_BASELINE_TIMEOUT_SECONDS
        )

    def test_baseline_deadline_fails_without_a_state_changing_command(
        self,
    ) -> None:
        clock = self.Clock()

        class Controller:
            reply_timeout = 20

            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def remote_command(
                self,
                _target: str,
                command: str,
                **kwargs: object,
            ) -> str:
                self.calls.append((command, kwargs))
                clock.now += 30
                raise ota.TransmissionError("still unreachable")

        controller = Controller()
        with (
            mock.patch.object(
                ota.time, "monotonic", side_effect=clock.monotonic
            ),
            mock.patch.object(ota.time, "sleep", side_effect=clock.sleep),
            self.assertRaisesRegex(
                ota.TransmissionError,
                "normal-channel baseline proof budget",
            ),
        ):
            ota.require_destination_preflight_identity(
                controller,
                argparse.Namespace(target="remote"),
                target(),
                "normal-channel baseline",
                deadline=65.0,
                retry_limit=ota.TEMP_RADIO_NORMAL_BASELINE_RETRY_LIMIT,
                deadline_label="normal-channel baseline proof budget",
            )

        self.assertEqual(
            [command for command, _kwargs in controller.calls],
            ["ota status", "ota status"],
        )
        self.assertTrue(
            all(
                kwargs.get("retry") is False
                for _command, kwargs in controller.calls
            )
        )

    def test_reply_completing_after_deadline_is_not_accepted(self) -> None:
        clock = self.Clock()

        class Controller:
            reply_timeout = 20

            def remote_command(
                self,
                _target: str,
                _command: str,
                **_kwargs: object,
            ) -> str:
                clock.now = 10.0
                return "late success"

        with (
            mock.patch.object(
                ota.time, "monotonic", side_effect=clock.monotonic
            ),
            self.assertRaisesRegex(
                ota.TransmissionError,
                "completed at or after.*expired",
            ),
        ):
            ota.bounded_remote_command(
                Controller(),
                "remote",
                "ota status",
                deadline=10.0,
                deadline_label="normal-channel baseline proof budget",
            )

    def test_status_and_self_share_one_deadline(self) -> None:
        clock = self.Clock()
        expected = target()

        class Controller:
            reply_timeout = 20

            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def remote_command(
                self,
                _target: str,
                command: str,
                **kwargs: object,
            ) -> str:
                self.calls.append((command, kwargs))
                if command == "ota status":
                    clock.now = 225.0
                    return (
                        "OTA | no download | "
                        f"target:{expected.target_id:08X} hw={expected.hw_id}"
                    )
                if command == "ota self":
                    return "self body=1 image=2 base_hash=0000000000000000"
                raise AssertionError(command)

        controller = Controller()
        with mock.patch.object(
            ota.time, "monotonic", side_effect=clock.monotonic
        ):
            ota.require_destination_preflight_identity(
                controller,
                argparse.Namespace(target="remote"),
                expected,
                "normal-channel baseline",
                deadline=240.0,
                retry_limit=ota.TEMP_RADIO_NORMAL_BASELINE_RETRY_LIMIT,
                deadline_label="normal-channel baseline proof budget",
            )

        self.assertEqual(
            controller.calls[1][1]["operation_timeout"], 15.0
        )

    def test_live_lease_keeps_the_smaller_default_retry_limit(self) -> None:
        clock = self.Clock()

        class Controller:
            reply_timeout = 20

            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def remote_command(
                self,
                _target: str,
                _command: str,
                **kwargs: object,
            ) -> str:
                self.calls.append(kwargs)
                raise ota.TransmissionError("missed live-lease reply")

        controller = Controller()
        with (
            mock.patch.object(
                ota.time, "monotonic", side_effect=clock.monotonic
            ),
            mock.patch.object(ota.time, "sleep", side_effect=clock.sleep),
            self.assertRaisesRegex(
                ota.TransmissionError,
                rf"failed after {ota.TRANSMISSION_RETRY_LIMIT} retries",
            ),
        ):
            ota.bounded_remote_command(
                controller,
                "remote",
                "ota self",
                deadline=1_000.0,
            )

        self.assertEqual(
            len(controller.calls), ota.TRANSMISSION_RETRY_LIMIT + 1
        )
        self.assertTrue(
            all(call.get("retry") is False for call in controller.calls)
        )


class TempRadioPreflightTests(unittest.TestCase):
    NORMAL = ota.RadioSettings(910.525, 62.5, 7, 5, False)
    TEMP = ota.RadioSettings(909.95, 250.0, 5, 5, False)
    CONTROLLER_KEY = "AA" * 32
    SOURCE_KEY = "BB" * 32
    NODE_KEYS = {
        "remote": "11" * 32,
        "far": "22" * 32,
        "near": "33" * 32,
        "source": SOURCE_KEY,
    }

    class Clock:
        def __init__(self, *, interrupt_count: int = 0) -> None:
            self.now = 0.0
            self.sleeps: list[float] = []
            self.interrupts_remaining = interrupt_count
            self.expiry_wait_started = False

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            # The fixed-window design also has a deliberate pre-activation
            # wait. Interrupt only the later owned-lease expiry wait.
            if seconds > 150:
                self.expiry_wait_started = True
            if (
                self.interrupts_remaining > 0
                and self.expiry_wait_started
                and seconds > 0
            ):
                self.interrupts_remaining -= 1
                # First leave five seconds, then half of any smaller tail.
                # Cleanup must keep using the same absolute deadline through
                # every interrupt rather than restarting or returning early.
                tail = 5.0 if seconds > 30 else max(0.5, seconds / 2.0)
                self.now += max(0.0, seconds - tail)
                raise KeyboardInterrupt("synthetic interrupted expiry wait")
            self.now += seconds

    class Controller:
        def __init__(
            self,
            clock: "TempRadioPreflightTests.Clock",
            failure: str | None = None,
            *,
            shared: bool = False,
        ) -> None:
            self.clock = clock
            self.failure = failure
            self.shared = shared
            self.password = "secret"
            self.reply_timeout = 20
            self.radio = TempRadioPreflightTests.NORMAL
            self.remote_calls: list[tuple[str, str, dict[str, object]]] = []
            self.source_challenges: list[tuple[str, str, float | None]] = []
            self.radio_calls: list[tuple[ota.RadioSettings, float | None]] = []
            self.temp_until = {
                name: 0.0 for name in TempRadioPreflightTests.NODE_KEYS
            }
            self.scheduled_temp: dict[str, tuple[float, float]] = {}
            self.delayed_temp_delivery: dict[
                str, tuple[float, float, float]
            ] = {}
            self.clock_adjustment = {
                name: 0 for name in TempRadioPreflightTests.NODE_KEYS
            }
            self.source_tuple = TempRadioPreflightTests.TEMP
            self.lost_ack_sent = False
            self.lost_source_ack_sent = False
            self.controller_restore_interrupt_sent = False
            self.slow_recovery_restore_completed_at: float | None = None

        def node_on_temp(self, name: str) -> bool:
            delayed = self.delayed_temp_delivery.get(name)
            if delayed is not None and self.clock.now >= delayed[0]:
                _delivery_at, start_at, end_at = delayed
                self.scheduled_temp[name] = (start_at, end_at)
                del self.delayed_temp_delivery[name]
            scheduled = self.scheduled_temp.get(name)
            scheduled_active = bool(
                scheduled is not None
                and scheduled[0] <= self.clock.now < scheduled[1]
            )
            return self.clock.now < self.temp_until[name] or scheduled_active

        def controller_on_temp(self) -> bool:
            if self.shared:
                return self.node_on_temp("source")
            return self.radio.matches(TempRadioPreflightTests.TEMP)

        def all_nodes_normal(self) -> bool:
            return not any(self.node_on_temp(name) for name in self.temp_until)

        def get_radio(self, timeout: float | None = None) -> ota.RadioSettings:
            if timeout is not None:
                self.assert_bounded_timeout(timeout)
            return self.radio

        @staticmethod
        def assert_bounded_timeout(timeout: float) -> None:
            if not 0 < timeout <= ota.TEMP_RADIO_PREFLIGHT_OPERATION_TIMEOUT_SECONDS:
                raise AssertionError(f"unbounded rehearsal timeout: {timeout}")

        def get_public_key(self, timeout: float | None = None) -> str:
            if timeout is not None:
                self.assert_bounded_timeout(timeout)
            if self.failure == "temp_controller_identity" and self.controller_on_temp():
                return "CC" * 32
            return TempRadioPreflightTests.CONTROLLER_KEY.lower()

        def get_clock(self, timeout: float | None = None) -> int:
            if timeout is not None:
                self.assert_bounded_timeout(timeout)
            return 1_800_000_000 + int(self.clock.now)

        def get_contact_clock(
            self,
            name: str,
            expected_public_key: str,
            *,
            timeout: float | None = None,
        ) -> int:
            if timeout is not None:
                self.assert_bounded_timeout(timeout)
            if expected_public_key.lower() != (
                TempRadioPreflightTests.NODE_KEYS[name].lower()
            ):
                raise ota.OtaError("synthetic clock identity mismatch")
            if self.failure == "clock_read_rtt":
                # Exercise distinct complete request/response intervals. The
                # returned epoch is sampled at completion, while production
                # scheduling must conservatively own any point in the RTT.
                self.clock.now += {
                    "remote": 17.0,
                    "far": 5.0,
                    "near": 23.0,
                }.get(name, 0.0)
            # Deliberately model minutes of inter-node offset within the
            # ten-minute safety policy. Projection into each local RTC must
            # still yield one common monotonic window.
            offsets = {"remote": 300, "far": -300, "near": 180}
            return (
                1_800_000_000
                + offsets.get(name, 0)
                + self.clock_adjustment[name]
                + int(self.clock.now)
            )

        def prove_contact_ack(
            self,
            name: str,
            expected_public_key: str,
            label: str,
            *,
            timeout: float | None = None,
        ) -> None:
            if timeout is not None:
                self.assert_bounded_timeout(timeout)
            self.source_challenges.append((name, label, timeout))
            if name != "source":
                raise ota.OtaError(f"unexpected challenge contact {name}")
            if self.node_on_temp(name) != self.controller_on_temp():
                raise ota.TransmissionError(
                    "synthetic source is not on the controller tuple"
                )
            on_temp = self.controller_on_temp()
            if (
                self.failure == "baseline_source_air"
                and not on_temp
                and self.clock.now == 0
            ):
                raise ota.OtaError(
                    f"{label} contact key mismatch for source"
                )
            if self.failure == "temp_source_air" and on_temp:
                raise ota.TransmissionError(
                    f"{label} received no matching ACK from source"
                )
            if (
                self.failure == "normal_source_air"
                and not on_temp
                and bool(self.scheduled_temp)
            ):
                raise ota.TransmissionError(
                    f"{label} received no matching ACK from source"
                )
            if expected_public_key.lower() != (
                TempRadioPreflightTests.SOURCE_KEY.lower()
            ):
                raise ota.OtaError(
                    f"{label} contact key mismatch for source"
                )

        def set_radio(
            self,
            settings: ota.RadioSettings,
            _label: str,
            timeout: float | None = None,
        ) -> None:
            if timeout is not None:
                self.assert_bounded_timeout(timeout)
            self.radio_calls.append((settings, timeout))
            if (
                self.failure == "controller_restore_interrupt"
                and settings.matches(TempRadioPreflightTests.NORMAL)
                and not self.controller_restore_interrupt_sent
            ):
                self.controller_restore_interrupt_sent = True
                raise KeyboardInterrupt(
                    "synthetic second interrupt during controller restore"
                )
            self.radio = settings
            if (
                self.failure == "controller_handoff"
                and settings.matches(TempRadioPreflightTests.TEMP)
            ):
                raise ota.OtaError("synthetic controller handoff failure")
            if (
                self.failure == "controller_restore_interrupt"
                and settings.matches(TempRadioPreflightTests.TEMP)
            ):
                raise KeyboardInterrupt(
                    "synthetic first interrupt during controller handoff"
                )

        def remote_command(
            self,
            name: str,
            command: str,
            **kwargs: object,
        ) -> str:
            self.remote_calls.append((name, command, dict(kwargs)))
            operation_timeout = kwargs.get("operation_timeout")
            if operation_timeout is not None:
                self.assert_bounded_timeout(float(operation_timeout))
            on_temp = self.controller_on_temp()
            if self.node_on_temp(name) != on_temp:
                raise ota.TransmissionError(
                    f"synthetic {name} is not on the controller tuple"
                )
            if command.startswith("time "):
                requested = int(command.split(" ", 1)[1])
                offsets = {"remote": 300, "far": -300, "near": 180}
                current = (
                    1_800_000_000
                    + offsets.get(name, 0)
                    + self.clock_adjustment[name]
                    + int(self.clock.now)
                )
                if requested <= current:
                    return "(ERR: clock cannot go backwards)"
                self.clock_adjustment[name] += requested - current
                return "OK - clock set: 08:00 - 15/1/2027 UTC"
            if command.startswith("set tempradioat "):
                values = command.split(" ", 2)[2].split(",")
                start_epoch = int(values[-2])
                end_epoch = int(values[-1])
                offsets = {"remote": 300, "far": -300, "near": 180}
                node_epoch = (
                    1_800_000_000 + offsets.get(name, 0)
                    + self.clock_adjustment[name]
                    + int(self.clock.now)
                )
                start_at = self.clock.now + (start_epoch - node_epoch)
                end_at = self.clock.now + (end_epoch - node_epoch)
                if self.failure == "lease_deadline":
                    self.clock.now += 70.0
                    node_epoch = (
                        1_800_000_000 + offsets.get(name, 0)
                        + self.clock_adjustment[name]
                        + int(self.clock.now)
                    )
                    if node_epoch >= start_epoch:
                        return "Error: start is in the past"
                if self.failure == "wrong_duration" and name == "remote":
                    end_at = start_at + 30 * 60
                if self.failure == "target_arm" and name == "remote":
                    return "Error, invalid params"
                if self.failure == "relay_arm" and name == "far":
                    return "Error, invalid params"
                if (
                    self.failure == "lost_target_ack_without_delivery"
                    and name == "remote"
                    and not self.lost_ack_sent
                ):
                    self.lost_ack_sent = True
                    raise ota.TransmissionError("synthetic lost acknowledgement")
                if (
                    self.failure == "lost_target_ack_slow"
                    and name == "remote"
                    and not self.lost_ack_sent
                ):
                    self.lost_ack_sent = True
                    self.clock.now += 30.0
                    self.scheduled_temp[name] = (start_at, end_at)
                    raise ota.TransmissionError(
                        "synthetic slow lost acknowledgement"
                    )
                if (
                    self.failure == "lost_target_ack_delayed"
                    and name == "remote"
                    and not self.lost_ack_sent
                ):
                    self.lost_ack_sent = True
                    self.delayed_temp_delivery[name] = (
                        self.clock.now + 10.0, start_at, end_at
                    )
                    raise ota.TransmissionError("synthetic lost acknowledgement")
                self.scheduled_temp[name] = (
                    start_at,
                    float("inf")
                    if self.failure in (
                        "stuck_remote_return",
                        "stuck_remote_slow_restore",
                        "late_recovery_source",
                        "wrong_recovery_duration",
                        "wrong_duration",
                    ) and name == "remote"
                    else end_at,
                )
                if (
                    self.failure == "lost_target_ack"
                    and name == "remote"
                    and not self.lost_ack_sent
                ):
                    self.lost_ack_sent = True
                    raise ota.TransmissionError("synthetic lost acknowledgement")
                return "OK - tempradioat 1 in 2m"
            if command == "normalradio":
                if (
                    self.failure == "stuck_remote_slow_restore"
                    and name == "remote"
                ):
                    self.clock.now += 30.0
                    self.slow_recovery_restore_completed_at = self.clock.now
                    self.scheduled_temp.pop(name, None)
                    self.temp_until[name] = self.clock.now + 1.5
                    raise ota.TransmissionError(
                        "synthetic slow lost recovery acknowledgement"
                    )
                self.scheduled_temp.pop(name, None)
                self.temp_until[name] = self.clock.now + 1.5
                return "OK - normal radio restore scheduled"
            if command == "get public.key":
                key = TempRadioPreflightTests.NODE_KEYS[name]
                if self.failure == "temp_remote_identity" and on_temp and name == "far":
                    return "> " + "44" * 32
                if self.failure == "normal_remote_identity" and not on_temp and self.scheduled_temp and name == "remote":
                    return "> " + "44" * 32
                return f"> {key}"
            if command == "get tempradioat":
                if self.failure == "baseline_schedule" and name == "far":
                    return "> 1:909.95,250,5,5@100-200"
                schedule_checks = sum(
                    1
                    for call_name, call_command, _options in self.remote_calls
                    if call_name == name and call_command == "get tempradioat"
                )
                if (
                    self.failure == "remote_schedule_appears_during_baseline"
                    and name == "remote"
                    and schedule_checks >= 2
                ):
                    return "> 1:909.95,250,5,5@100-200"
                if (
                    self.failure == "baseline_scheduleless"
                    and name == "remote"
                ):
                    return "Unknown command"
                return "> -none-"
            if command == "ota status":
                if self.failure == "baseline_destination" and not on_temp and self.clock.now == 0:
                    return "OTA | no download | target:DEADBEEF hw=TestBoard"
                return "OTA | no download | target:1234ABCD hw=TestBoard"
            if command == "ota self":
                if self.failure == "temp_destination" and on_temp:
                    return "self body=1 image=2 base_hash=DEADBEEFDEADBEEF"
                return "self body=1 image=2 base_hash=0000000000000000"
            raise AssertionError(f"unexpected remote command {name}: {command}")

    @staticmethod
    def args(*, shared: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            target="remote",
            relay_values=[("far", "far-secret"), ("near", "near-secret")],
            temp_values=(909.95, 250.0, 5, 5, 120),
            source_already_temp=False,
            source_shares_controller=shared,
            source_serial=None if shared else "/dev/source",
            source_cli_serial=None,
            source_cli_tcp="192.0.2.1:5002" if shared else None,
            source_contact_value=None,
            shared_source_public_key=(
                TempRadioPreflightTests.CONTROLLER_KEY if shared else None
            ),
        )

    def run_rehearsal(
        self,
        *,
        failure: str | None = None,
        shared: bool = False,
        capture_error: bool = False,
    ) -> tuple[
        "TempRadioPreflightTests.Controller",
        list[tuple[str, dict[str, object]]],
        "TempRadioPreflightTests.Clock",
        dict[str, object],
        BaseException | None,
    ]:
        clock = self.Clock(
            interrupt_count=(
                2 if failure == "double_interrupt_wait"
                else 1 if failure == "interrupt_wait"
                else 0
            )
        )
        controller = self.Controller(clock, failure, shared=shared)
        source_calls: list[tuple[str, dict[str, object]]] = []
        source_state: dict[str, object] = {"tuple": self.TEMP}
        args = self.args(shared=shared)
        if failure == "late_recovery_source":
            args.relay_values = []

        def source_active() -> bool:
            return controller.node_on_temp("source")

        def source_command(
            _args: argparse.Namespace,
            command: str,
            check: bool = True,
            **kwargs: object,
        ) -> str:
            source_calls.append((command, {"check": check, **kwargs}))
            if command == "get public.key":
                if shared:
                    raise ota.OtaError(
                        "source rejected 'get public.key': "
                        "ERROR: unknown command: get public.key"
                    )
                if failure == "baseline_source_identity" and len(
                    [call for call, _options in source_calls if call == command]
                ) == 1:
                    return "not a key"
                if failure == "temp_source_identity" and source_active():
                    return "> " + "DD" * 32
                source_key = self.CONTROLLER_KEY if shared else self.SOURCE_KEY
                return f"  -> > {source_key}\r\n> "
            if command == "get name":
                return "  -> > source\r\n> "
            if command == "ver":
                return "Companion v1.17.1.5"
            if command == "get tempradioat":
                if failure == "source_schedule_transport":
                    raise ota.TransmissionError("synthetic source schedule link loss")
                schedule_checks = sum(
                    1
                    for call, _options in source_calls
                    if call == "get tempradioat"
                )
                if (
                    failure == "source_schedule_appears_during_baseline"
                    and schedule_checks >= 2
                ):
                    return "  -> > 1:909.95,250,5,5@100-200\r\n> "
                return "  -> > -none-\r\n> "
            if command.startswith("tempradio "):
                minutes = int(command.rsplit(",", 1)[1])
                if failure == "source_arm":
                    raise ota.OtaError("synthetic source arm failure")
                if (
                    failure == "lost_source_ack_without_delivery"
                    and not controller.lost_source_ack_sent
                ):
                    controller.lost_source_ack_sent = True
                    raise ota.TransmissionError(
                        "synthetic lost source acknowledgement"
                    )
                if failure == "late_recovery_source" and minutes == 1:
                    clock.now += 30.0
                    controller.temp_until["source"] = clock.now + 60.0
                    raise ota.TransmissionError(
                        "synthetic late accepted recovery re-arm"
                    )
                if failure == "wrong_recovery_duration" and minutes == 1:
                    source_state["recovery_accepted_at"] = clock.now
                    controller.temp_until["source"] = clock.now + 30 * 60
                    return "  -> OK - temp params for 30 mins\r\n> "
                controller.temp_until["source"] = (
                    float("inf")
                    if failure in (
                        "normal_source_return",
                        "stuck_source_return",
                        "legacy_stuck_source",
                    )
                    else clock.now + minutes * 60
                )
                if (
                    failure == "lost_source_ack"
                    and not controller.lost_source_ack_sent
                ):
                    controller.lost_source_ack_sent = True
                    raise ota.TransmissionError(
                        "synthetic lost source acknowledgement"
                    )
                return (
                    f"  -> OK - temp params for {minutes} mins\r\n> "
                )
            if command == "tempradio":
                if failure == "source_status_transport":
                    raise ota.TransmissionError("synthetic source status link loss")
                if failure == "source_status_empty":
                    return ""
                if failure in ("legacy_source", "legacy_stuck_source"):
                    raise ota.OtaError("source rejected 'tempradio': Unknown command")
                if failure == "preexisting_source_active" and not source_active():
                    return "TempRadio active: 908.000,125.00,7,5 80s left\r\n> "
                if failure == "preexisting_source_pending" and not source_active():
                    return "TempRadio pending: 908.000,125.00,7,5 80s left\r\n> "
                if source_active():
                    if failure == "temp_source_tuple":
                        return "TempRadio active: 908.000,250.00,5,5 170s left"
                    return "  -> TempRadio active: 909.950,250.00,5,5 170s left\r\n> "
                return "TempRadio inactive\r\n> "
            if command == "normalradio":
                if failure in (
                    "late_recovery_source",
                    "wrong_recovery_duration",
                ) and clock.now >= 180:
                    raise ota.TransmissionError(
                        "synthetic lost source recovery restore"
                    )
                controller.temp_until["source"] = clock.now
                return "  -> OK - normal radio restore scheduled\r\n> "
            raise AssertionError(f"unexpected source command: {command}")

        error: BaseException | None = None
        with (
            mock.patch.object(ota, "source_cli_command", side_effect=source_command),
            mock.patch.object(
                ota,
                "ensure_controller_clock_safe",
                return_value=1_800_000_000,
            ),
            mock.patch.object(
                ota,
                "ensure_source_clock_safe",
                return_value=(1_800_000_000, 1_800_000_059),
            ),
            mock.patch.object(ota.time, "monotonic", side_effect=clock.monotonic),
            mock.patch.object(
                ota.time,
                "time",
                side_effect=lambda: 1_800_000_000 + clock.now,
            ),
            mock.patch.object(ota.time, "sleep", side_effect=clock.sleep),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            try:
                ota.run_temp_radio_preflight(
                    controller,
                    args,
                    target(base_hash=b"\0" * 8),
                    self.NORMAL,
                    self.TEMP,
                )
            except BaseException as exc:
                if not capture_error:
                    raise
                error = exc
        source_state["active"] = source_active()
        return controller, source_calls, clock, source_state, error

    def test_raw_serial_source_reply_parsers(self) -> None:
        key = "A5" * 32
        self.assertEqual(
            ota.parse_cli_public_key(f"  -> > {key}\r\n> ", "source"),
            key.lower(),
        )
        self.assertEqual(
            ota.parse_cli_value("  -> > source node\r\n> ", "name"),
            "source node",
        )
        ota.require_no_existing_temp_schedule(
            "  -> > -none-\r\n> ", "source"
        )
        self.assertEqual(
            ota.require_temp_radio_reply(
                "source", "  -> OK - temp params for 3 mins\r\n> ", 3
            ),
            3,
        )
        self.assertEqual(
            ota.parse_source_temp_radio_status("TempRadio inactive\r\n> "),
            ("inactive", None),
        )
        state, radio = ota.parse_source_temp_radio_status(
            "  -> TempRadio active: 909.950,250.00,5,5 170s left\r\n> "
        ) or ("", None)
        self.assertEqual(state, "active")
        self.assertIsNotNone(radio)
        self.assertTrue(self.TEMP.matches(radio))
        with self.assertRaisesRegex(ota.OtaError, "exact public key"):
            ota.parse_cli_public_key(
                f"  -> > {key}\r\n  -> > {'B6' * 32}\r\n> ",
                "source",
            )
        with self.assertRaisesRegex(ota.OtaError, "already has scheduled"):
            ota.require_no_existing_temp_schedule(
                "  -> > -none-\r\n  -> > 1:909.95,250,5,5@1-2\r\n> ",
                "source",
            )
        with self.assertRaisesRegex(ota.OtaError, "invalid TempRadio status"):
            ota.parse_source_temp_radio_status(
                "TempRadio inactive\r\n"
                "TempRadio active: 909.950,250.00,5,5 170s left\r\n> "
            )

    def test_optional_source_command_never_hides_transport_or_silence(self) -> None:
        args = self.args()
        with mock.patch.object(
            ota,
            "source_cli_command",
            side_effect=ota.TransmissionError("link failed"),
        ):
            with self.assertRaisesRegex(ota.TransmissionError, "link failed"):
                ota.optional_source_cli_command(args, "tempradio")
        with mock.patch.object(
            ota,
            "source_cli_command",
            side_effect=ota.OtaError("source rejected: Unknown command"),
        ):
            self.assertIsNone(
                ota.optional_source_cli_command(args, "tempradio")
            )
        with mock.patch.object(ota, "source_cli_command", return_value=""):
            with self.assertRaisesRegex(ota.OtaError, "empty reply"):
                ota.optional_source_cli_command(args, "tempradio")

    def test_generic_source_challenge_requires_exact_contact_key_and_ack(self) -> None:
        controller = ota.Controller.__new__(ota.Controller)
        valid = [
            {"adv_name": "source", "public_key": self.SOURCE_KEY},
            {"expected_ack": "12AB34CD"},
            {"code": "12ab34cd"},
        ]
        with (
            mock.patch.object(
                controller,
                "_run_marked",
                return_value=([], valid),
            ) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            controller.prove_contact_ack(
                "source", self.SOURCE_KEY, "source challenge", timeout=25
            )
        commands = run.call_args.args[0]
        self.assertEqual(commands[0], "echo")
        self.assertEqual(commands[2:5], ["contact_info", "source", "msg"])
        self.assertRegex(commands[6], r"^mOTA-preflight-[0-9a-f]{16}$")
        self.assertEqual(
            commands.count("wait_ack"),
            ota.TEMP_RADIO_PREFLIGHT_ACK_WAITS,
        )
        self.assertEqual(run.call_args.args[-1], 25)

        cases = {
            "contact key mismatch": [
                {"adv_name": "source", "public_key": "CC" * 32},
                {"expected_ack": "12AB34CD"},
                {"code": "12AB34CD"},
            ],
            "no matching ACK": [
                {"adv_name": "source", "public_key": self.SOURCE_KEY},
                {"expected_ack": "12AB34CD"},
                {"code": "DEADBEEF"},
            ],
        }
        for pattern, objects in cases.items():
            with self.subTest(pattern=pattern), mock.patch.object(
                controller, "_run_marked", return_value=([], objects)
            ), self.assertRaisesRegex(ota.OtaError, pattern):
                controller.prove_contact_ack(
                    "source", self.SOURCE_KEY, "source challenge"
                )

    def test_contact_clock_binds_exact_identity_and_one_raw_epoch(self) -> None:
        controller = ota.Controller.__new__(ota.Controller)

        def output_for(
            commands: list[str], _label: str, _timeout: float | None = None
        ) -> subprocess.CompletedProcess[str]:
            marker = commands[1]
            return subprocess.CompletedProcess(
                [], 0,
                stdout=(
                    f"{marker}\n"
                    + json.dumps({
                        "adv_name": "remote",
                        "public_key": self.NODE_KEYS["remote"],
                    })
                    + "\n1800000123\n"
                ),
                stderr="",
            )

        controller._execute = output_for
        self.assertEqual(
            controller.get_contact_clock(
                "remote", self.NODE_KEYS["remote"], timeout=30
            ),
            1_800_000_123,
        )

        def ambiguous(
            commands: list[str], _label: str, _timeout: float | None = None
        ) -> subprocess.CompletedProcess[str]:
            result = output_for(commands, _label, _timeout)
            result.stdout += "1800000124\n"
            return result

        controller._execute = ambiguous
        with self.assertRaisesRegex(ota.TransmissionError, "2 unambiguous"):
            controller.get_contact_clock(
                "remote", self.NODE_KEYS["remote"], timeout=30
            )

        controller._execute = output_for
        with self.assertRaisesRegex(ota.OtaError, "exact contact identity"):
            controller.get_contact_clock("remote", "FF" * 32, timeout=30)

    def test_rehearsal_uses_exact_three_minutes_and_farthest_first(self) -> None:
        controller, source_calls, clock, source_state, error = self.run_rehearsal()
        self.assertIsNone(error)

        armed = [
            (name, command)
            for name, command, _kwargs in controller.remote_calls
            if command.startswith("set tempradioat ")
        ]
        self.assertEqual([name for name, _command in armed], [
            "remote", "far", "near"
        ])
        projected_starts = []
        offsets = {"remote": 300, "far": -300, "near": 180}
        for name, scheduled_command in armed:
            fields = scheduled_command.split(" ", 2)[2].split(",")
            start_epoch, end_epoch = map(int, fields[-2:])
            self.assertEqual(end_epoch - start_epoch, 3 * 60)
            projected_starts.append(
                start_epoch
                - (
                    1_800_000_000
                    + offsets[name]
                    + controller.clock_adjustment[name]
                )
            )
        self.assertEqual(projected_starts, [120, 120, 120])
        clock_pins = [
            (name, command, options)
            for name, command, options in controller.remote_calls
            if command.startswith("time ")
        ]
        self.assertEqual([name for name, _command, _options in clock_pins], [
            "remote", "far", "near"
        ])
        self.assertTrue(all(options["retry"] is False for _, _, options in clock_pins))
        source_arm_options = next(
            options for command, options in source_calls
            if command == "tempradio 909.95,250,5,5,3"
        )
        self.assertTrue(source_arm_options["bounded"])
        self.assertEqual(source_arm_options["deadline"], 297.0)
        self.assertFalse(source_arm_options["retry"])
        self.assertNotIn("120", " ".join(command for command, _ in source_calls))
        self.assertEqual(
            clock.sleeps,
            [
                115.0,
                8.0,
                ota.TEMP_RADIO_SWITCH_DELAY_SECONDS,
                192.0,
            ],
        )
        self.assertEqual(
            [radio for radio, _timeout in controller.radio_calls],
            [self.TEMP, self.NORMAL],
        )
        self.assertTrue(
            all(
                timeout is not None
                and timeout <= ota.TEMP_RADIO_PREFLIGHT_OPERATION_TIMEOUT_SECONDS
                for _radio, timeout in controller.radio_calls
            )
        )
        self.assertEqual(len(controller.source_challenges), 3)
        self.assertTrue(controller.radio.matches(self.NORMAL))
        self.assertTrue(controller.all_nodes_normal())
        self.assertFalse(bool(source_state["active"]))

    def test_clock_read_rtt_expands_activation_and_cleanup_bounds(self) -> None:
        controller, _source_calls, clock, source_state, error = (
            self.run_rehearsal(
                failure="clock_read_rtt",
                capture_error=True,
            )
        )
        self.assertIsNone(error)
        # The slowest 23-second read moves the earliest possible remote start
        # 23 seconds before the latest one. Source arming therefore waits 92s,
        # not the zero-RTT 115s, and cleanup owns the corresponding late end.
        self.assertEqual(clock.sleeps[0], 92.0)
        self.assertGreaterEqual(clock.now, 408.0)
        self.assertTrue(controller.all_nodes_normal())
        self.assertFalse(bool(source_state["active"]))

    def test_shared_source_uses_bounded_local_tuple_not_binary_set(self) -> None:
        controller, source_calls, _clock, source_state, error = (
            self.run_rehearsal(shared=True)
        )
        self.assertIsNone(error)

        self.assertEqual(controller.radio_calls, [])
        source_arm_options = next(
            options for command, options in source_calls
            if command == "tempradio 909.95,250,5,5,3"
        )
        self.assertTrue(source_arm_options["bounded"])
        self.assertEqual(source_arm_options["deadline"], 297.0)
        self.assertFalse(source_arm_options["retry"])
        self.assertNotIn(
            "get public.key", [command for command, _options in source_calls]
        )
        self.assertGreaterEqual(
            [command for command, _options in source_calls].count("ver"), 3
        )
        self.assertTrue(controller.radio.matches(self.NORMAL))
        self.assertFalse(bool(source_state["active"]))

    def test_lost_fixed_schedule_ack_is_bounded_without_replay(self) -> None:
        for failure, succeeds in (
            ("lost_target_ack", True),
            ("lost_target_ack_without_delivery", False),
            ("lost_target_ack_delayed", True),
            ("lost_target_ack_slow", True),
        ):
            with self.subTest(failure=failure):
                controller, _calls, clock, _state, error = self.run_rehearsal(
                    failure=failure,
                    capture_error=True,
                )
                self.assertEqual(error is None, succeeds)
                target_arms = [
                    call for call in controller.remote_calls
                    if call[0] == "remote"
                    and call[1].startswith("set tempradioat ")
                ]
                self.assertEqual(len(target_arms), 1)
                # Even a lost reply returns after the one fixed rehearsal
                # interval; it never pays the multi-hour controller retry
                # horizon and never submits a second mutation.
                self.assertLess(clock.now, 6 * 60)
                self.assertTrue(controller.all_nodes_normal())

    def test_lost_source_ack_is_resolved_without_replay(self) -> None:
        for failure, succeeds in (
            ("lost_source_ack", True),
            ("lost_source_ack_without_delivery", False),
        ):
            with self.subTest(failure=failure):
                controller, source_calls, _clock, _state, error = self.run_rehearsal(
                    failure=failure,
                    capture_error=True,
                )
                self.assertEqual(error is None, succeeds)
                source_arms = [
                    call for call in source_calls
                    if call[0].startswith("tempradio ")
                ]
                self.assertEqual(len(source_arms), 1)
                self.assertTrue(controller.all_nodes_normal())

    def test_source_must_be_proven_over_lora(self) -> None:
        for failure, pattern in (
            ("baseline_source_air", "contact key mismatch"),
            ("temp_source_air", "matching ACK"),
            ("normal_source_air", "matching ACK"),
        ):
            with self.subTest(failure=failure):
                controller, _calls, _clock, _state, error = self.run_rehearsal(
                    failure=failure,
                    capture_error=True,
                )
                self.assertIsNotNone(error)
                self.assertRegex(str(error), pattern)
                self.assertTrue(controller.all_nodes_normal())

    def test_legacy_source_return_is_proven_over_lora(self) -> None:
        controller, _calls, _clock, _state, error = self.run_rehearsal(
            failure="legacy_source",
        )
        self.assertIsNone(error)
        self.assertEqual(len(controller.source_challenges), 3)
        self.assertTrue(controller.all_nodes_normal())

    def test_stuck_source_and_remote_are_actively_normalized(self) -> None:
        for failure in (
            "stuck_source_return",
            "legacy_stuck_source",
            "stuck_remote_return",
        ):
            with self.subTest(failure=failure):
                controller, source_calls, clock, state, error = (
                    self.run_rehearsal(
                        failure=failure,
                        capture_error=True,
                    )
                )
                self.assertIsNotNone(error)
                self.assertTrue(controller.all_nodes_normal())
                self.assertFalse(bool(state["active"]))
                self.assertTrue(controller.radio.matches(self.NORMAL))
                self.assertGreaterEqual(
                    clock.now,
                    ota.remote_cli_mutation_drain_seconds(self.TEMP)
                    + ota.TEMP_RADIO_PREFLIGHT_MARGIN_SECONDS,
                )
                if failure in ("stuck_source_return", "legacy_stuck_source"):
                    self.assertIn(
                        "normalradio",
                        [command for command, _options in source_calls],
                    )
                else:
                    self.assertIn(
                        ("remote", "normalradio"),
                        [
                            (name, command)
                            for name, command, _options in controller.remote_calls
                        ],
                    )

    def test_late_shared_recovery_rearm_is_owned_from_completion(self) -> None:
        controller, source_calls, clock, state, error = self.run_rehearsal(
            failure="late_recovery_source",
            shared=True,
            capture_error=True,
        )
        self.assertIsNotNone(error)
        self.assertFalse(bool(state["active"]))
        self.assertTrue(controller.all_nodes_normal())
        recovery_arms = [
            command
            for command, _options in source_calls
            if command.endswith(",1")
        ]
        self.assertEqual(
            recovery_arms,
            ["tempradio 909.95,250,5,5,1"],
        )
        self.assertGreaterEqual(
            clock.now,
            30.0
            + ota.TEMP_RADIO_RETURN_MINUTES * 60
            + ota.TEMP_RADIO_PREFLIGHT_MARGIN_SECONDS,
        )

    def test_shared_recovery_owns_an_explicit_unexpected_duration(self) -> None:
        controller, _source_calls, clock, state, error = self.run_rehearsal(
            failure="wrong_recovery_duration",
            shared=True,
            capture_error=True,
        )
        self.assertIsNotNone(error)
        accepted_at = float(state["recovery_accepted_at"])
        self.assertGreaterEqual(
            clock.now,
            accepted_at + 30 * 60 + ota.TEMP_RADIO_PREFLIGHT_MARGIN_SECONDS,
        )
        self.assertFalse(bool(state["active"]))
        self.assertTrue(controller.all_nodes_normal())

    def test_slow_ambiguous_remote_restore_drains_from_completion(self) -> None:
        controller, _source_calls, clock, _state, error = self.run_rehearsal(
            failure="stuck_remote_slow_restore",
            capture_error=True,
        )
        self.assertIsNotNone(error)
        completed_at = controller.slow_recovery_restore_completed_at
        self.assertIsNotNone(completed_at)
        assert completed_at is not None
        self.assertGreaterEqual(
            clock.now,
            completed_at
            + ota.remote_cli_mutation_drain_seconds(self.TEMP)
            + ota.TEMP_RADIO_PREFLIGHT_MARGIN_SECONDS,
        )
        self.assertTrue(controller.all_nodes_normal())

    def test_second_interrupt_during_controller_restore_cannot_escape_wait(
        self,
    ) -> None:
        controller, _calls, clock, _state, error = self.run_rehearsal(
            failure="controller_restore_interrupt",
            capture_error=True,
        )
        self.assertIsInstance(error, KeyboardInterrupt)
        self.assertTrue(controller.controller_restore_interrupt_sent)
        self.assertGreaterEqual(
            clock.now,
            ota.TEMP_RADIO_PREFLIGHT_MINUTES * 60
            + ota.TEMP_RADIO_PREFLIGHT_MARGIN_SECONDS,
        )
        self.assertTrue(controller.radio.matches(self.NORMAL))
        self.assertTrue(controller.all_nodes_normal())

    def test_preexisting_source_work_and_uncertain_status_block_mutation(self) -> None:
        cases = {
            "preexisting_source_active": "already has active",
            "preexisting_source_pending": "already has active or pending",
            "source_status_transport": "status link loss",
            "source_status_empty": "empty reply",
            "source_schedule_transport": "schedule link loss",
        }
        for failure, pattern in cases.items():
            with self.subTest(failure=failure):
                controller, source_calls, _clock, _state, error = self.run_rehearsal(
                    failure=failure,
                    capture_error=True,
                )
                self.assertIsNotNone(error)
                self.assertRegex(str(error), pattern)
                self.assertFalse(
                    any(command.startswith("tempradio ") for command, _ in source_calls)
                )
                self.assertFalse(
                    any(command.startswith("tempradio ") for _, command, _ in controller.remote_calls)
                )

    def test_unmanaged_source_is_rejected_before_any_mutation(self) -> None:
        controller = self.Controller(self.Clock())
        args = self.args()
        args.source_already_temp = True
        args.source_serial = None
        with self.assertRaisesRegex(ota.OtaError, "unmanaged"):
            ota.run_temp_radio_preflight(
                controller,
                args,
                target(base_hash=b"\0" * 8),
                self.NORMAL,
                self.TEMP,
            )
        self.assertEqual(controller.remote_calls, [])
        self.assertEqual(controller.radio_calls, [])

    def test_identical_temp_modulation_is_rejected_before_any_mutation(self) -> None:
        controller = self.Controller(self.Clock())
        args = self.args()
        same_modulation = ota.RadioSettings(
            self.NORMAL.frequency,
            self.NORMAL.bandwidth,
            self.NORMAL.spreading_factor,
            self.NORMAL.coding_rate,
            not self.NORMAL.repeat,
        )
        with self.assertRaisesRegex(ota.OtaError, "requires --temp-radio"):
            ota.run_temp_radio_preflight(
                controller,
                args,
                target(base_hash=b"\0" * 8),
                self.NORMAL,
                same_modulation,
            )
        self.assertEqual(controller.remote_calls, [])
        self.assertEqual(controller.radio_calls, [])

    def test_duplicate_hop_names_are_rejected_before_any_mutation(self) -> None:
        controller = self.Controller(self.Clock())
        args = self.args()
        args.relay_values = [("REMOTE", "other-password")]
        with self.assertRaisesRegex(ota.OtaError, "must be unique"):
            ota.run_temp_radio_preflight(
                controller,
                args,
                target(base_hash=b"\0" * 8),
                self.NORMAL,
                self.TEMP,
            )
        self.assertEqual(controller.remote_calls, [])
        self.assertEqual(controller.radio_calls, [])

    def test_failed_rehearsal_blocks_every_long_transfer_mutation(self) -> None:
        image = firmware(b"preflight integration" * 300, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))
        controller = mock.Mock()
        controller.get_radio.return_value = self.NORMAL
        controller.get_clock.return_value = int(ota.time.time()) + 1
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "release.mota", "remote",
                "--controller-serial", "/dev/controller",
                "--source-serial", "/dev/source",
                "--password", "secret",
                "--work-dir", str(Path(directory) / "work"),
                "--yes",
            ]
            with (
                mock.patch.object(ota, "preflight_inputs"),
                mock.patch.object(ota, "preflight_source_cli"),
                mock.patch.object(
                    ota,
                    "ensure_source_clock_gate_safe",
                    return_value=(1_800_000_000, 1_800_000_059),
                ),
                mock.patch.object(ota, "read_source_rxps", return_value=saved),
                mock.patch.object(ota, "query_target", return_value=target()),
                mock.patch.object(
                    ota,
                    "prepare_package",
                    return_value=(Path("release.mota"), package, None),
                ),
                mock.patch.object(
                    ota,
                    "read_lora_ota_participant_versions",
                    return_value={"destination": VERSION_NEW},
                ),
                mock.patch.object(
                    ota,
                    "read_remote_rxps",
                    return_value=ota.RxpsSettings(False, 18205, 20423, 8, 16),
                ),
                mock.patch.object(ota, "confirm_update"),
                mock.patch.object(
                    ota,
                    "run_temp_radio_preflight",
                    side_effect=ota.OtaError("synthetic rehearsal failure"),
                ) as rehearsal,
                mock.patch.object(ota, "disable_source_rxps") as disable_source,
                mock.patch.object(ota, "apply_remote_rxps_policy") as mutate_target,
                mock.patch.object(ota, "arm_target_temp_radio") as arm_long_target,
                mock.patch.object(ota, "switch_controller_to_temp_radio") as switch_long,
                mock.patch.object(ota, "SeederProcess") as seeder,
                mock.patch.object(ota, "find_and_start_pull") as pull,
                mock.patch.object(ota, "request_install") as install,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = ota.main(argv, controller_override=controller)

        self.assertEqual(result, 2)
        rehearsal.assert_called_once()
        disable_source.assert_not_called()
        mutate_target.assert_not_called()
        arm_long_target.assert_not_called()
        switch_long.assert_not_called()
        seeder.assert_not_called()
        pull.assert_not_called()
        install.assert_not_called()

    def test_schedule_appearing_during_baseline_blocks_every_mutation(
        self,
    ) -> None:
        for failure in (
            "source_schedule_appears_during_baseline",
            "remote_schedule_appears_during_baseline",
        ):
            with self.subTest(failure=failure):
                controller, source_calls, _clock, _state, error = (
                    self.run_rehearsal(
                        failure=failure,
                        capture_error=True,
                    )
                )

                self.assertIsNotNone(error)
                self.assertRegex(str(error), "scheduled TempRadio work")
                remote_commands = [
                    command
                    for _name, command, _options in controller.remote_calls
                ]
                self.assertFalse(
                    any(
                        command.startswith("time ")
                        or command.startswith("set tempradioat ")
                        or command == "normalradio"
                        for command in remote_commands
                    )
                )
                self.assertFalse(
                    any(
                        command.startswith("tempradio ")
                        or command.startswith("time ")
                        or command == "normalradio"
                        for command, _options in source_calls
                    )
                )
                self.assertEqual(controller.radio_calls, [])

    def test_failures_at_each_rehearsal_stage_leave_normal_state(self) -> None:
        failure_patterns = {
            "baseline_source_identity": "exact public key",
            "baseline_destination": "normal-channel baseline identity",
            "baseline_schedule": "already has scheduled TempRadio work",
            "baseline_scheduleless": "does not support fixed `tempradioat`",
            "target_arm": "did not accept one exact fixed TempRadio schedule",
            "relay_arm": "did not accept one exact fixed TempRadio schedule",
            "source_arm": "source arm failure",
            "controller_handoff": "controller handoff failure",
            "temp_source_tuple": "tuple mismatch",
            "temp_controller_identity": "controller on TempRadio public key changed",
            "temp_source_identity": "OTA source on TempRadio public key changed",
            "temp_remote_identity": "far on TempRadio public key changed",
            "temp_destination": "destination on TempRadio identity",
            "normal_source_return": "did not return automatically",
            "normal_remote_identity": "remote after natural return public key changed",
        }
        for failure, pattern in failure_patterns.items():
            with self.subTest(failure=failure):
                controller, source_calls, clock, source_state, error = (
                    self.run_rehearsal(
                        failure=failure,
                        capture_error=True,
                    )
                )
                self.assertIsNotNone(error)
                self.assertRegex(str(error), pattern)
                self.assertTrue(controller.radio.matches(self.NORMAL))
                self.assertTrue(controller.all_nodes_normal())
                self.assertFalse(bool(source_state["active"]))
                all_commands = [
                    command
                    for _name, command, _kwargs in controller.remote_calls
                ] + [command for command, _kwargs in source_calls]
                self.assertFalse(
                    any(
                        command.rstrip().endswith(",120")
                        for command in all_commands
                    )
                )
                if failure.startswith("baseline_"):
                    self.assertNotIn(
                        ota.TEMP_RADIO_PREFLIGHT_MINUTES * 60
                        + ota.TEMP_RADIO_PREFLIGHT_MARGIN_SECONDS,
                        clock.sleeps,
                    )
                else:
                    self.assertGreaterEqual(
                        clock.now,
                        ota.TEMP_RADIO_PREFLIGHT_MINUTES * 60
                        + ota.TEMP_RADIO_PREFLIGHT_MARGIN_SECONDS,
                    )

    def test_sequential_arming_deadline_fails_without_leaking_leases(self) -> None:
        controller, _calls, clock, _state, error = self.run_rehearsal(
            failure="lease_deadline",
            capture_error=True,
        )
        self.assertIsNotNone(error)
        self.assertRegex(str(error), "fixed TempRadio schedule")
        self.assertGreaterEqual(clock.now, 300)
        self.assertTrue(controller.radio.matches(self.NORMAL))
        self.assertTrue(controller.all_nodes_normal())

    def test_interrupted_expiry_wait_resumes_absolute_deadline(self) -> None:
        controller, _calls, clock, _state, error = self.run_rehearsal(
            failure="interrupt_wait",
            capture_error=True,
        )
        self.assertIsInstance(error, KeyboardInterrupt)
        long_sleeps = [value for value in clock.sleeps if value > 30]
        self.assertEqual(len(long_sleeps), 2)
        self.assertIn(5.0, clock.sleeps)
        self.assertLess(sum(clock.sleeps), 330)
        self.assertTrue(controller.all_nodes_normal())

    def test_repeated_interrupts_cannot_escape_owned_lease_cleanup(self) -> None:
        controller, _calls, clock, _state, error = self.run_rehearsal(
            failure="double_interrupt_wait",
            capture_error=True,
        )
        self.assertIsInstance(error, KeyboardInterrupt)
        self.assertEqual(clock.interrupts_remaining, 0)
        self.assertGreaterEqual(
            clock.now,
            ota.TEMP_RADIO_PREFLIGHT_MINUTES * 60
            + ota.TEMP_RADIO_PREFLIGHT_MARGIN_SECONDS,
        )
        self.assertLess(sum(clock.sleeps), 335)
        self.assertTrue(controller.radio.matches(self.NORMAL))
        self.assertTrue(controller.all_nodes_normal())

    def test_fixed_schedule_formatter_cannot_extend_three_minutes(self) -> None:
        command = ota.scheduled_temp_radio_command(
            self.args(), 1_800_000_120, 1_800_000_300
        )
        self.assertEqual(
            command,
            "set tempradioat 909.95,250,5,5,1800000120,1800000300",
        )
        with self.assertRaisesRegex(ota.OtaError, "interval"):
            ota.scheduled_temp_radio_command(
                self.args(), 1_800_000_120, 1_800_000_120
            )


class ReliabilityTests(unittest.TestCase):
    def test_controller_rejects_meshcli_zero_exit_no_response(self) -> None:
        """Do not trust meshcore-cli's exit status without protocol output."""
        diagnostic = (
            "ERROR:meshcore:No response from meshcore node, disconnecting\n"
            "ERROR:meshcore:Are you sure your node is a serial companion ?\n"
            "ERROR:meshcore:To connect to a repeater, use -r option.\n"
        )
        controller = object.__new__(ota.Controller)
        controller._execute = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="", stderr=diagnostic
            )
        )

        with self.assertRaisesRegex(
            ota.OtaError, "returned no JSON"
        ) as unmarked:
            controller._run(["infos", "ver", "clock"], "XIAO probe")
        self.assertIn("No response from meshcore node", str(unmarked.exception))

        with self.assertRaisesRegex(
            ota.TransmissionError, "did not reach the command marker"
        ) as marked:
            controller._run_marked(
                ["infos", "ver", "clock"], "XIAO probe", "NEVER_REACHED"
            )
        self.assertIn("No response from meshcore node", str(marked.exception))

    def test_target_version_falls_back_to_ver(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.replies = iter([
                    "OTA | no download | target:1234ABCD hw=TestBoard",
                    "Error: unsupported",
                    "self body=1 image=2 base_hash=0011223344556677",
                    "Unknown command",
                    "v1.16.9 (Build: test)",
                ])

            def remote_command(self, *_args: object, **_kwargs: object) -> str:
                return next(self.replies)

        result = ota.query_target(
            Controller(), argparse.Namespace(target="remote")
        )
        self.assertEqual(result.platform, "esp32")
        self.assertIsNone(result.bootloader_version)
        self.assertEqual(result.current_version, "v1.16.9")
        self.assertEqual(result.current_version_source, "ver")

    def test_bootloader_reply_match_rejects_unrelated_messages(self) -> None:
        self.assertTrue(
            ota.reply_matches_command(
                "get bootloader.ver", "> 0.9.2-OTAFIX2.4"
            )
        )
        self.assertTrue(
            ota.reply_matches_command(
                "get bootloader.ver", "Error: unsupported"
            )
        )
        self.assertFalse(
            ota.reply_matches_command(
                "get bootloader.ver", "OTA | no download | target:1234ABCD"
            )
        )
        self.assertTrue(
            ota.reply_matches_command(
                "ota pull 1234ABCD flash",
                "OK resuming mid=1234ABCD -> flash (primary traffic)",
            )
        )
        self.assertTrue(
            ota.reply_matches_command(
                "ota cancel",
                "ERR dropped RAM session (was I mid=-), but persistent OTA "
                "slot invalidation failed",
            )
        )

    def test_target_uses_bootloader_version_to_identify_nrf52(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.replies = iter([
                    "OTA | no download | target:1234ABCD hw=RAK_3401",
                    "> 0.9.2-OTAFIX2.4",
                    "self body=1 image=2 base_hash=0011223344556677 | "
                    "bootloader: apply OK (abi=2 codecs=0x4)",
                    "OTA | fw v1.17.0 id=00112233",
                ])

            def remote_command(
                self, _target: str, command: str, **_kwargs: object
            ) -> str:
                self.commands.append(command)
                return next(self.replies)

        controller = Controller()
        result = ota.query_target(
            controller, argparse.Namespace(target="remote")
        )
        self.assertEqual(result.platform, "nrf52")
        self.assertEqual(result.bootloader_version, "0.9.2-OTAFIX2.4")
        self.assertEqual(result.bootloader_abi, 2)
        self.assertEqual(result.bootloader_codecs, 0x4)
        self.assertEqual(
            controller.commands,
            ["ota status", "get bootloader.ver", "ota self", "ota stats"],
        )

    def test_optional_ota_stats_loss_is_bounded_then_falls_back_to_ver(
        self,
    ) -> None:
        class Controller:
            def __init__(self) -> None:
                self.commands: list[tuple[str, bool]] = []
                self.stats_calls = 0

            def remote_command(
                self,
                _target: str,
                command: str,
                *,
                retry: bool = True,
                **_kwargs: object,
            ) -> str:
                self.commands.append((command, retry))
                if command == "ota status":
                    return "OTA | no download | target:1234ABCD hw=RAK_3401"
                if command == "get bootloader.ver":
                    return "> 0.9.2-OTAFIX2.4"
                if command == "ota self":
                    return (
                        "self body=1 image=2 base_hash=0011223344556677 | "
                        "bootloader: apply OK (abi=2 codecs=0x4)"
                    )
                if command == "ota stats":
                    self.stats_calls += 1
                    self.assert_optional_probe(retry)
                    raise ota.TransmissionError("synthetic unsupported/lost stats")
                if command == "ver":
                    return "v1.16.7"
                raise AssertionError(command)

            @staticmethod
            def assert_optional_probe(retry: bool) -> None:
                if retry:
                    raise AssertionError("optional stats used unbounded retry")

        controller = Controller()
        with mock.patch.object(ota.time, "sleep"):
            result = ota.query_target(
                controller, argparse.Namespace(target="remote")
            )
        self.assertEqual(
            controller.stats_calls, ota.TRANSMISSION_RETRY_LIMIT + 1
        )
        self.assertEqual(result.current_version, "v1.16.7")
        self.assertEqual(result.current_version_source, "ver")
        self.assertEqual(controller.commands[-1], ("ver", True))

    def test_target_detects_qspi_staging(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.replies = iter([
                    "OTA | no download | target:1234ABCD hw=Xiao_nrf52 | bl:QSPI blrc:B0",
                    "> 0.9.2-OTAFIX2.4",
                    "self body=1 image=2 base_hash=0011223344556677 | "
                    "QSPI store:2048K | bootloader: QSPI apply OK "
                    "(abi=2 codecs=0x5)",
                    "OTA | fw v1.17.0 id=00112233",
                ])

            def remote_command(self, *_args: object, **_kwargs: object) -> str:
                return next(self.replies)

        result = ota.query_target(
            Controller(), argparse.Namespace(target="remote")
        )
        self.assertTrue(result.nrf_qspi)
        self.assertTrue(result.nrf_external)
        self.assertFalse(result.nrf_sd)

    def test_target_rejects_unavailable_qspi_store(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.replies = iter([
                    "OTA | no download | target:1234ABCD hw=Xiao_nrf52 | bl:QSPI blrc:B0",
                    "> 0.9.2-OTAFIX2.4",
                    "self body=1 image=2 base_hash=0011223344556677 | "
                    "QSPI store:ERR 0K | bootloader: QSPI apply OK "
                    "(abi=2 codecs=0x5)",
                ])

            def remote_command(self, *_args: object, **_kwargs: object) -> str:
                return next(self.replies)

        with self.assertRaisesRegex(
            ota.OtaError, "bootloader supports QSPI apply.*QSPI store:ERR 0K"
        ):
            ota.query_target(
                Controller(), argparse.Namespace(target="remote")
            )

    def test_stock_nrf52_bootloader_reports_required_action(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.replies = iter([
                    "OTA | no download | target:1234ABCD hw=RAK_3401",
                    "> 0.9.2",
                    "self body=1 image=2 base_hash=0011223344556677 | "
                    "bootloader: NO mota-apply support (delta install will refuse)",
                ])

            def remote_command(self, *_args: object, **_kwargs: object) -> str:
                return next(self.replies)

        with self.assertRaisesRegex(
            ota.OtaError, "bootloader 0.9.2.*exact-board OTAFIX"
        ):
            ota.query_target(Controller(), argparse.Namespace(target="remote"))

    def test_legacy_firmware_falls_back_to_ota_self_platform_marker(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.replies = iter([
                    "OTA | no download | target:1234ABCD hw=RAK_3401",
                    "Unknown command",
                    "self body=1 image=2 base_hash=0011223344556677 | "
                    "bootloader: apply OK (abi=2 codecs=0x4)",
                    "OTA | fw v1.17.0 id=00112233",
                ])

            def remote_command(self, *_args: object, **_kwargs: object) -> str:
                return next(self.replies)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = ota.query_target(
                Controller(), argparse.Namespace(target="remote")
            )
        self.assertEqual(result.platform, "nrf52")
        self.assertIsNone(result.bootloader_version)
        self.assertIn("legacy `ota self` platform markers", output.getvalue())

    def test_unattended_prompt_stops_without_another_retry_cycle(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", io.StringIO()),
            mock.patch.object(ota.time, "sleep") as sleep,
            contextlib.redirect_stdout(output),
        ):
            self.assertFalse(ota.prompt_after_transmission_failure(
                "test", ota.TransmissionError("lost")
            ))
        sleep.assert_not_called()
        self.assertIn("stopping (non-interactive)", output.getvalue())

    def test_interactive_prompt_timeout_still_continues(self) -> None:
        interactive_stdin = mock.Mock()
        interactive_stdin.isatty.return_value = True
        output = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", interactive_stdin),
            mock.patch.object(ota.os, "name", "posix"),
            mock.patch("select.select", return_value=([], [], [])),
            contextlib.redirect_stdout(output),
        ):
            self.assertTrue(
                ota.prompt_after_transmission_failure(
                    "test", ota.TransmissionError("lost")
                )
            )
        self.assertIn("continuing in 10s", output.getvalue())

    def test_unattended_retry_is_finite(self) -> None:
        calls = 0

        def action() -> str:
            nonlocal calls
            calls += 1
            raise ota.TransmissionError("lost")

        with (
            mock.patch.object(sys, "stdin", io.StringIO()),
            mock.patch.object(ota.time, "sleep"),
            self.assertRaisesRegex(
                ota.TransmissionStopped, "stopped after transmission"
            ),
        ):
            ota.retry_transmission(action, "unattended probe")
        self.assertEqual(calls, ota.TRANSMISSION_RETRY_LIMIT + 1)

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

    def test_matching_admin_reply_recovers_lost_login_acknowledgement(self) -> None:
        controller = object.__new__(ota.Controller)
        controller.reply_timeout = 20
        controller._authenticated_targets = set()
        key = "A1" * 32
        controller._run_marked = lambda _commands, _label, _marker: (
            [{"adv_name": "remote", "public_key": key}],
            [{
                "txt_type": 1,
                "text": "OTA | no download | target:1234ABCD",
                "pubkey_prefix": key[:12],
            }],
        )

        reply = controller._remote_command_once(
            "remote", "ota status", "secret"
        )

        self.assertTrue(reply.startswith("OTA |"))
        self.assertEqual(controller._authenticated_targets, {"remote"})

    def test_explicit_admin_login_failure_overrides_matching_reply(self) -> None:
        controller = object.__new__(ota.Controller)
        controller.reply_timeout = 20
        controller._authenticated_targets = set()
        key = "A1" * 32
        controller._run_marked = lambda _commands, _label, _marker: (
            [
                {"adv_name": "remote", "public_key": key},
                {"login_success": False},
            ],
            [{
                "txt_type": 1,
                "text": "OTA | no download | target:1234ABCD",
                "pubkey_prefix": key[:12],
            }],
        )

        with self.assertRaisesRegex(ota.OtaError, "admin login failed"):
            controller._remote_command_once("remote", "ota status", "wrong")

        self.assertEqual(controller._authenticated_targets, set())

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
        for command in (
            "ota install",
            "ota pull 1",
            "tempradio 909.95,250,5,5,120",
        ):
            with self.subTest(command=command), self.assertRaisesRegex(
                ota.OtaError, "state-aware"
            ):
                controller.remote_command("remote", command)

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
                self.retries: list[object] = []

            def remote_command(
                self, _target: str, command: str, **_kwargs: object
            ) -> str:
                self.commands.append(command)
                self.retries.append(_kwargs.get("retry"))
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
        self.assertEqual(controller.retries, [False, False])
        self.assertEqual(controller.radios, [temporary, normal])

    @mock.patch.object(ota.time, "sleep")
    def test_lost_target_temp_reply_uses_later_bounded_probe(
        self, sleep: mock.Mock
    ) -> None:
        normal = ota.RadioSettings(910.525, 62.5, 7, 5, False)
        temporary = ota.RadioSettings(909.95, 250.0, 5, 5, False)
        controller = mock.Mock()
        controller.remote_command.side_effect = (
            ota.TransmissionError("lost command reply"),
            ota.TransmissionError("missed first temp proof"),
            "self body=1 image=2 base_hash=0011223344556677",
        )

        ota.arm_target_temp_radio(
            controller,
            argparse.Namespace(target="remote"),
            "tempradio 909.95,250,5,5,120",
            temporary,
            normal,
        )

        self.assertEqual(controller.remote_command.call_count, 3)
        self.assertEqual(
            [
                call.kwargs.get("retry")
                for call in controller.remote_command.call_args_list
            ],
            [False, False, False],
        )
        sleep.assert_called_once_with(ota.transmission_retry_delay(1))
        controller.set_radio.assert_has_calls([
            mock.call(temporary, "switch controller to TempRadio"),
            mock.call(normal, "restore controller after TempRadio probe"),
        ])

    def test_shared_lost_target_temp_probe_preserves_saved_controller_tuple(self) -> None:
        normal = ota.RadioSettings(910.525, 62.5, 7, 5, False)
        temporary = ota.RadioSettings(909.95, 500.0, 5, 5, False)

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

        args = argparse.Namespace(
            target="remote",
            source_shares_controller=True,
            source_already_temp=False,
        )
        controller = Controller()
        command = "tempradio 909.95,500,5,5,120"
        with (
            mock.patch.object(
                ota,
                "source_cli_command",
                side_effect=(
                    "OK - temp params for 120 mins",
                    "OK - normal radio restore scheduled",
                    "TempRadio inactive",
                ),
            ) as source_command,
            mock.patch.object(ota.time, "sleep") as sleep,
        ):
            ota.arm_target_temp_radio(
                controller, args, command, temporary, normal
            )
        self.assertEqual(controller.commands, [command, "ota self"])
        self.assertEqual(controller.radios, [normal])
        self.assertEqual(
            source_command.call_args_list,
            [
                mock.call(args, command, retry=False),
                mock.call(args, "normalradio", check=True),
                mock.call(args, "tempradio", check=True),
            ],
        )
        sleep.assert_called_once_with(ota.TEMP_RADIO_SWITCH_DELAY_SECONDS)

    def test_shared_probe_reasserts_binary_tuple_when_local_cleanup_fails(self) -> None:
        normal = ota.RadioSettings(910.525, 62.5, 7, 5, False)
        temporary = ota.RadioSettings(909.95, 500.0, 5, 5, False)
        controller = mock.Mock()
        controller.remote_command.side_effect = (
            ota.TransmissionError("lost reply"),
            "self body=1 image=2 base_hash=0011223344556677",
        )
        args = argparse.Namespace(
            target="remote",
            source_shares_controller=True,
            source_already_temp=False,
        )
        with (
            mock.patch.object(ota, "switch_controller_to_temp_radio"),
            mock.patch.object(
                ota,
                "shorten_source_temp_window",
                side_effect=ota.OtaError("local cleanup failed"),
            ),
            mock.patch.object(ota.time, "sleep"),
            self.assertRaisesRegex(ota.OtaError, "local cleanup failed"),
        ):
            ota.arm_target_temp_radio(
                controller,
                args,
                "tempradio 909.95,500,5,5,120",
                temporary,
                normal,
            )
        controller.set_radio.assert_called_once_with(
            normal, "restore controller after TempRadio probe"
        )

    @mock.patch.object(ota.time, "sleep")
    def test_ambiguous_target_temp_probe_is_not_replayed(
        self, sleep: mock.Mock
    ) -> None:
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
            ["tempradio 909.95,250,5,5,120"] + ["ota self"] * 8,
        )
        self.assertEqual(controller.radios, [temporary, normal])
        self.assertEqual(sleep.call_count, ota.TRANSMISSION_RETRY_LIMIT * 2)

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
                self.replies = iter(
                    [ota.TransmissionError("lost command reply")]
                    + [
                        ota.TransmissionError("not on temporary channel")
                        for _ in range(4)
                    ]
                    + [
                        "self body=1 image=2 base_hash=0011223344556677",
                        "OK - temp params for 120 mins",
                    ]
                )

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
                "ota self",
                "ota self",
                "ota self",
                "tempradio 909.95,500,5,5,120",
            ],
        )
        self.assertEqual(controller.radios, [temporary, normal])
        self.assertEqual(
            sleep.call_args_list,
            [
                mock.call(ota.transmission_retry_delay(1)),
                mock.call(ota.transmission_retry_delay(2)),
                mock.call(ota.transmission_retry_delay(3)),
                mock.call(ota.transmission_retry_delay(1)),
            ],
        )

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

    def test_lost_install_window_reply_is_reconciled_without_replay(self) -> None:
        image = firmware(b"install-window" * 700, VERSION_NEW)
        package = ota.parse_mota(mota_blob(image))

        class Controller:
            def __init__(self) -> None:
                self.calls: list[tuple[str, bool | None]] = []

            def remote_command(
                self, _target: str, command: str, **kwargs: object
            ) -> str:
                self.calls.append((command, kwargs.get("retry")))
                if command.startswith("tempradio "):
                    raise ota.TransmissionError("lost window reply")
                if command == "ota status":
                    return (
                        "OTA | download: ready to install 9/9 "
                        f"id={package.manifest_id} 2s"
                    )
                raise AssertionError(command)

        controller = Controller()
        args = argparse.Namespace(
            target="remote",
            temp_values=(909.95, 250.0, 5, 5, 120),
        )
        ota.arm_target_install_window(controller, args, package)
        self.assertEqual(
            controller.calls,
            [
                ("tempradio 909.95,250,5,5,3", False),
                ("ota status", False),
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

    def test_reboot_ready_probe_defaults_to_five_minutes(self) -> None:
        parser = ota.build_parser()
        args = parser.parse_args([
            "release.mota", "remote",
            "--controller-serial", "/dev/controller",
            "--source-serial", "/dev/source",
        ])
        self.assertEqual(args.reboot_wait, 300)
        self.assertEqual(rak_chain.build_parser().parse_args([]).reboot_wait, 300)

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
                self.commands: list[
                    tuple[str, str, str | None, bool | None]
                ] = []

            def remote_command(
                self, target_name: str, command: str, **kwargs: object
            ) -> str:
                password = kwargs.get("password")
                self.commands.append(
                    (target_name, command, password, kwargs.get("retry"))
                )
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
                ("remote", "tempradio 909.95,250,5,5,1", None, False),
                (
                    "relay",
                    "tempradio 909.95,250,5,5,1",
                    "relay-secret",
                    False,
                ),
            ],
        )

    def test_relay_arm_owns_lost_ack_before_one_shot_send(self) -> None:
        ownership: list[tuple[str, str]] = []

        class Controller:
            owned_during_send = False
            calls = 0

            def remote_command(
                self, target: str, command: str, **kwargs: object
            ) -> str:
                self.calls += 1
                self.owned_during_send = (target, "secret") in ownership
                self.retry = kwargs.get("retry")
                raise ota.TransmissionError("lost acknowledgement")

        controller = Controller()
        confirmed = ota.arm_relay_temp_radio_once(
            controller,
            "relay",
            "secret",
            "tempradio 909.95,250,5,5,120",
            ownership,
            120,
        )
        self.assertFalse(confirmed)
        self.assertTrue(controller.owned_during_send)
        self.assertEqual(controller.calls, 1)
        self.assertIs(controller.retry, False)
        self.assertEqual(ownership, [("relay", "secret")])

    def test_relay_cleanup_attempts_every_node_after_failure(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.calls: list[tuple[str, bool | None]] = []

            def remote_command(
                self, target: str, command: str, **kwargs: object
            ) -> str:
                if command.startswith("tempradio "):
                    self.calls.append((target, kwargs.get("retry")))
                    if target == "offline":
                        raise ota.OtaError("unreachable")
                    return "OK - temp params for 1 mins"
                raise AssertionError(command)

        controller = Controller()
        args = argparse.Namespace(
            relay_values=[
                ("offline", "offline-secret"),
                ("online", "online-secret"),
            ],
            temp_values=(909.95, 250.0, 5, 5, 120),
        )
        with self.assertRaisesRegex(
            ota.OtaError, "offline: unreachable"
        ):
            ota.shorten_relay_temp_windows(controller, args)
        self.assertEqual(
            controller.calls,
            [("offline", False), ("online", False)],
        )

    def test_lost_relay_cleanup_reconciles_identity_without_replay(self) -> None:
        key = "AB" * 32

        class Controller:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, bool | None]] = []

            def remote_command(
                self, target: str, command: str, **kwargs: object
            ) -> str:
                self.calls.append((target, command, kwargs.get("retry")))
                if command.startswith("tempradio "):
                    raise ota.TransmissionError("lost cleanup reply")
                if command == "get public.key":
                    return f"> {key}"
                raise AssertionError(command)

        controller = Controller()
        args = argparse.Namespace(
            relay_values=[("relay", "secret")],
            temp_values=(909.95, 250.0, 5, 5, 120),
        )
        ota.shorten_relay_temp_windows(
            controller,
            args,
            expected_public_keys={"relay": key.lower()},
        )
        relative_writes = [
            call for call in controller.calls if call[1].startswith("tempradio ")
        ]
        self.assertEqual(
            relative_writes,
            [("relay", "tempradio 909.95,250,5,5,1", False)],
        )
        self.assertIn(("relay", "get public.key", False), controller.calls)

    def test_managed_relay_timing_is_saved_guarded_and_restored(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.rxdelay = 2.0
                self.txdelay = 0.5

            def remote_command(
                self, _target: str, command: str, **_kwargs: object
            ) -> str:
                if command == "get rxdelay":
                    return f"> {self.rxdelay}"
                if command == "get txdelay":
                    return f"> {self.txdelay}"
                if command.startswith("set rxdelay "):
                    self.rxdelay = float(command.split()[-1])
                    return "OK"
                if command.startswith("set txdelay "):
                    self.txdelay = float(command.split()[-1])
                    return "OK"
                raise AssertionError(command)

        controller = Controller()
        saved = ota.read_relay_timing(controller, "relay", "secret")
        ota.enforce_relay_timing(controller, saved, 0.3)
        self.assertEqual(controller.rxdelay, 0.0)
        self.assertEqual(controller.txdelay, 0.3)

        with tempfile.TemporaryDirectory() as directory:
            path = ota.write_relay_timing_recovery(Path(directory), [saved])
            payload = json.loads(path.read_text(encoding="ascii"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            payload,
            [{"name": "relay", "rxdelay": 2.0, "txdelay": 0.5}],
        )

        ota.restore_relay_timings(controller, [saved])
        self.assertEqual(controller.rxdelay, 2.0)
        self.assertEqual(controller.txdelay, 0.5)

    def test_unreachable_relay_does_not_block_other_relay_restoration(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.rxdelay = 0.0
                self.txdelay = 0.3

            def remote_command(
                self, target: str, command: str, **_kwargs: object
            ) -> str:
                if target == "offline":
                    raise ota.OtaError("unreachable")
                if command == "get rxdelay":
                    return f"> {self.rxdelay}"
                if command == "get txdelay":
                    return f"> {self.txdelay}"
                if command.startswith("set rxdelay "):
                    self.rxdelay = float(command.split()[-1])
                    return "OK"
                if command.startswith("set txdelay "):
                    self.txdelay = float(command.split()[-1])
                    return "OK"
                raise AssertionError(command)

        controller = Controller()
        settings = [
            ota.RelayTimingSettings("online", "secret", 2.0, 0.5),
            ota.RelayTimingSettings("offline", "secret", 1.0, 0.4),
        ]
        with self.assertRaisesRegex(ota.OtaError, "offline: unreachable"):
            ota.restore_relay_timings(controller, settings)
        self.assertEqual(controller.rxdelay, 2.0)
        self.assertEqual(controller.txdelay, 0.5)

    def test_source_cleanup_only_changes_a_script_owned_window(self) -> None:
        args = argparse.Namespace(
            source_already_temp=False,
            source_shares_controller=False,
            temp_values=(909.95, 250.0, 5, 5, 120),
        )
        with mock.patch.object(
            ota,
            "source_cli_command",
            side_effect=(
                "OK - normal radio restore scheduled",
                "TempRadio inactive",
            ),
        ) as source_command:
            self.assertTrue(ota.shorten_source_temp_window(args))
        self.assertEqual(
            source_command.call_args_list,
            [
                mock.call(args, "normalradio", check=False),
                mock.call(args, "tempradio", check=False),
            ],
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
                side_effect=(
                    "OK - normal radio restore scheduled",
                    "TempRadio active: 909.950,250.00,5,5 5s left",
                    "TempRadio inactive",
                ),
            ) as source_command,
            mock.patch.object(ota.time, "sleep") as sleep,
        ):
            self.assertTrue(ota.shorten_source_temp_window(args))
        self.assertEqual(
            source_command.call_args_list,
            [
                mock.call(args, "normalradio", check=True),
                mock.call(args, "tempradio", check=True),
                mock.call(args, "tempradio", check=True),
            ],
        )
        sleep.assert_called_once()

    def test_shared_controller_temp_switch_does_not_persist_binary_tuple(self) -> None:
        args = argparse.Namespace(source_shares_controller=True)
        controller = mock.Mock()
        temporary = ota.RadioSettings(909.95, 500.0, 5, 5, False)
        command = "tempradio 909.95,500,5,5,120"
        with mock.patch.object(
            ota,
            "source_cli_command",
            return_value="OK - temp params for 120 mins",
        ) as source_command:
            ota.switch_controller_to_temp_radio(
                controller, args, command, temporary
            )
        source_command.assert_called_once_with(args, command, retry=False)
        controller.set_radio.assert_not_called()

    def test_separate_controller_temp_switch_uses_binary_tuple(self) -> None:
        args = argparse.Namespace(source_shares_controller=False)
        controller = mock.Mock()
        temporary = ota.RadioSettings(909.95, 500.0, 5, 5, False)
        with mock.patch.object(ota, "source_cli_command") as source_command:
            ota.switch_controller_to_temp_radio(
                controller,
                args,
                "tempradio 909.95,500,5,5,120",
                temporary,
            )
        source_command.assert_not_called()
        controller.set_radio.assert_called_once_with(
            temporary, "switch controller to TempRadio"
        )


class Rak3401TransferGuardrailTests(unittest.TestCase):
    class Controller:
        def __init__(self) -> None:
            self.rxps_enabled = True
            self.rxps_rx_us = 65625
            self.rxps_sleep_us = 60000
            self.rxps_level = 8
            self.rxps_preamble = 16
            self.powersaving_enabled = True
            self.rxdelay = "2.0"
            self.airtime_factor = "1.0"
            self.ota_hops = 3
            self.commands: list[str] = []

        def remote_command(
            self, _target: str, command: str, **_kwargs: object
        ) -> str:
            self.commands.append(command)
            if command == "get radio.rxps.config":
                state = "on" if self.rxps_enabled else "off"
                return (
                    f"> {state},level={self.rxps_level},"
                    f"preamble={self.rxps_preamble},rx={self.rxps_rx_us},"
                    f"sleep={self.rxps_sleep_us}"
                )
            if command == "set radio.rxps off":
                self.rxps_enabled = False
                return f"OK - off,{self.rxps_rx_us},{self.rxps_sleep_us}"
            if command.startswith("set radio.rxps level "):
                parts = command.split()
                level = int(parts[3])
                preamble = int(parts[5]) if len(parts) == 6 else 0
                self.rxps_enabled = True
                self.rxps_level = level
                self.rxps_preamble = preamble
                if (level, preamble) == (8, 32):
                    self.rxps_rx_us = 1252
                    self.rxps_sleep_us = 6424
                return (
                    f"OK - level {level},on,{self.rxps_rx_us},"
                    f"{self.rxps_sleep_us},preamble={preamble}"
                )
            if command.startswith("set radio.rxps "):
                _set, _key, rx_us, sleep_us = command.split()
                self.rxps_enabled = True
                self.rxps_rx_us = int(rx_us)
                self.rxps_sleep_us = int(sleep_us)
                self.rxps_level = 0
                self.rxps_preamble = 0
                return f"OK - on,{rx_us},{sleep_us}"
            if command == "powersaving":
                return "on" if self.powersaving_enabled else "off"
            if command == "powersaving off":
                self.powersaving_enabled = False
                return "off"
            if command == "powersaving on":
                self.powersaving_enabled = True
                return "on - Immediate effect"
            if command == "get rxdelay":
                return f"> {self.rxdelay}"
            if command.startswith("set rxdelay "):
                self.rxdelay = command.split(maxsplit=2)[2]
                return "OK"
            if command == "get af":
                return f"> {self.airtime_factor}"
            if command.startswith("set af "):
                self.airtime_factor = command.split(maxsplit=2)[2]
                return "OK"
            if command == "ota config":
                return f"ota config: checkpoint=4 advert=1440min hops={self.ota_hops}"
            if command.startswith("ota config hops "):
                self.ota_hops = int(command.rsplit(maxsplit=1)[1])
                return f"OK OTA reach = {self.ota_hops} hops (saved)"
            raise AssertionError(command)

    def test_guardrails_disable_and_restore_all_original_settings(self) -> None:
        controller = self.Controller()
        saved = rak_chain.read_target_transfer_settings(controller, "remote")

        rak_chain.enforce_transfer_guardrails(
            controller, "remote", legacy_full_airtime=True
        )
        rak_chain.enforce_ota_hops(controller, "remote", 0)
        self.assertFalse(controller.rxps_enabled)
        self.assertFalse(controller.powersaving_enabled)
        self.assertEqual(controller.rxdelay, "0")
        self.assertEqual(controller.airtime_factor, "0")
        self.assertEqual(controller.ota_hops, 0)

        rak_chain.restore_transfer_settings(controller, "remote", saved)
        self.assertTrue(controller.rxps_enabled)
        self.assertEqual(controller.rxps_rx_us, 65625)
        self.assertEqual(controller.rxps_sleep_us, 60000)
        self.assertEqual(controller.rxps_level, 8)
        self.assertEqual(controller.rxps_preamble, 16)
        self.assertTrue(controller.powersaving_enabled)
        self.assertEqual(controller.rxdelay, "2.0")
        self.assertEqual(controller.airtime_factor, "1.0")
        self.assertEqual(controller.ota_hops, 3)
        self.assertIn("powersaving on", controller.commands)
        self.assertEqual(controller.commands[-1], "ota config")

    def test_restore_converges_when_saved_power_saving_was_off(self) -> None:
        controller = self.Controller()
        controller.powersaving_enabled = False
        saved = rak_chain.read_target_transfer_settings(controller, "remote")
        controller.powersaving_enabled = True

        rak_chain.restore_transfer_settings(controller, "remote", saved)

        self.assertFalse(controller.powersaving_enabled)
        self.assertIn("powersaving off", controller.commands)

    def test_guardrails_preserve_airtime_without_opt_in(self) -> None:
        controller = self.Controller()

        rak_chain.enforce_transfer_guardrails(controller, "remote")

        self.assertEqual(controller.airtime_factor, "1.0")
        self.assertNotIn("set af 0", controller.commands)

    def test_guardrails_keep_rxps_for_current_qualified_participants(self) -> None:
        controller = self.Controller()
        saved = rak_chain.read_target_transfer_settings(controller, "remote")

        rak_chain.enforce_transfer_guardrails(
            controller,
            "remote",
            saved=saved,
            current_version="v1.17.1.5",
            temp_values=(909.95, 250.0, 5, 5, 120),
            all_participants_support_adaptive_preamble=True,
        )

        self.assertTrue(controller.rxps_enabled)
        self.assertEqual(controller.rxps_rx_us, saved.rxps_rx_us)
        self.assertEqual(controller.rxps_sleep_us, saved.rxps_sleep_us)
        self.assertNotIn(
            "set radio.rxps level 8 preamble 32", controller.commands
        )
        self.assertFalse(controller.powersaving_enabled)
        self.assertEqual(controller.rxdelay, "0")

    def test_guardrails_restore_saved_rxps_preference_on_resume(self) -> None:
        controller = self.Controller()
        saved = rak_chain.read_target_transfer_settings(controller, "remote")
        controller.rxps_enabled = False

        rak_chain.enforce_transfer_guardrails(
            controller,
            "remote",
            saved=saved,
            current_version="v1.17.1.5",
            temp_values=(909.95, 250.0, 5, 5, 120),
            all_participants_support_adaptive_preamble=True,
        )

        self.assertTrue(controller.rxps_enabled)
        self.assertEqual(controller.rxps_level, saved.rxps_level)
        self.assertEqual(controller.rxps_preamble, saved.rxps_preamble)
        self.assertIn(
            "set radio.rxps level 8 preamble 16", controller.commands
        )

    def test_original_settings_are_persisted_for_resume(self) -> None:
        controller = self.Controller()
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            saved = rak_chain.load_or_capture_transfer_settings(
                controller, "remote", "AA" * 32, work_dir
            )
            command_count = len(controller.commands)
            controller.rxdelay = "0"
            controller.airtime_factor = "0"
            controller.ota_hops = 0
            loaded = rak_chain.load_or_capture_transfer_settings(
                controller, "remote", "AA" * 32, work_dir
            )
            mode = (work_dir / rak_chain.TRANSFER_SETTINGS_FILE).stat().st_mode

        self.assertEqual(loaded, saved)
        self.assertEqual(loaded.airtime_factor, "1.0")
        self.assertEqual(loaded.ota_hops, 3)
        self.assertEqual(len(controller.commands), command_count)
        self.assertEqual(mode & 0o777, 0o600)

    def test_successful_restore_retires_settings_and_next_run_recaptures(self) -> None:
        controller = self.Controller()
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            saved = rak_chain.load_or_capture_transfer_settings(
                controller, "remote", "AA" * 32, work_dir
            )
            rak_chain.enforce_transfer_guardrails(
                controller, "remote", legacy_full_airtime=True
            )
            rak_chain.enforce_ota_hops(controller, "remote", 0)
            rak_chain.restore_and_retire_transfer_settings(
                controller, "remote", saved, work_dir
            )
            self.assertFalse(
                (work_dir / rak_chain.TRANSFER_SETTINGS_FILE).exists()
            )

            controller.rxdelay = "4.0"
            controller.airtime_factor = "0.5"
            controller.ota_hops = 2
            recaptured = rak_chain.load_or_capture_transfer_settings(
                controller, "remote", "AA" * 32, work_dir
            )

        self.assertEqual(recaptured.rxdelay, "4.0")
        self.assertEqual(recaptured.airtime_factor, "0.5")
        self.assertEqual(recaptured.ota_hops, 2)

    def test_failed_restore_keeps_settings_record_armed(self) -> None:
        controller = self.Controller()
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            saved = rak_chain.load_or_capture_transfer_settings(
                controller, "remote", "AA" * 32, work_dir
            )
            path = work_dir / rak_chain.TRANSFER_SETTINGS_FILE
            with (
                mock.patch.object(
                    rak_chain,
                    "restore_transfer_settings",
                    side_effect=ota.OtaError("restore failed"),
                ),
                self.assertRaisesRegex(ota.OtaError, "restore failed"),
            ):
                rak_chain.restore_and_retire_transfer_settings(
                    controller, "remote", saved, work_dir
                )
            self.assertTrue(path.is_file())

    def test_saved_settings_reject_string_booleans(self) -> None:
        controller = self.Controller()
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            path = work_dir / rak_chain.TRANSFER_SETTINGS_FILE
            path.write_text(
                json.dumps(
                    {
                        "target_key": "aa" * 32,
                        "rxps_enabled": "false",
                        "rxps_rx_us": 65625,
                        "rxps_sleep_us": 60000,
                        "powersaving_enabled": False,
                        "rxdelay": "2.0",
                        "airtime_factor": "1.0",
                        "ota_hops": 3,
                    }
                ),
                encoding="ascii",
            )
            path.chmod(0o600)
            with self.assertRaisesRegex(ota.OtaError, "invalid saved transfer settings"):
                rak_chain.load_or_capture_transfer_settings(
                    controller, "remote", "AA" * 32, work_dir
                )

    def test_saved_settings_without_restore_fields_are_rejected(self) -> None:
        controller = self.Controller()
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            path = work_dir / rak_chain.TRANSFER_SETTINGS_FILE
            path.write_text(
                json.dumps(
                    {
                        "target_key": "aa" * 32,
                        "rxps_enabled": False,
                        "rxps_rx_us": 65625,
                        "rxps_sleep_us": 60000,
                        "powersaving_enabled": False,
                        "rxdelay": "2.0",
                    }
                ),
                encoding="ascii",
            )
            path.chmod(0o600)
            with self.assertRaisesRegex(
                ota.OtaError, "invalid saved transfer settings"
            ):
                rak_chain.load_or_capture_transfer_settings(
                    controller, "remote", "AA" * 32, work_dir
                )

    def test_saved_settings_reject_nonfinite_delay_and_non_string_key(self) -> None:
        controller = self.Controller()
        mutations = (
            ("target_key", 1234),
            ("rxdelay", "nan"),
            ("rxdelay", "inf"),
            ("rxdelay", "-1"),
            ("airtime_factor", 1.0),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value), \
                    tempfile.TemporaryDirectory() as directory:
                work_dir = Path(directory)
                rak_chain.load_or_capture_transfer_settings(
                    controller, "remote", "AA" * 32, work_dir
                )
                path = work_dir / rak_chain.TRANSFER_SETTINGS_FILE
                document = json.loads(path.read_text(encoding="ascii"))
                document[field] = value
                path.write_text(json.dumps(document), encoding="ascii")
                with self.assertRaisesRegex(
                    ota.OtaError, "invalid saved transfer settings"
                ):
                    rak_chain.load_or_capture_transfer_settings(
                        controller, "remote", "AA" * 32, work_dir
                    )


class Rak3401ExtractionCacheTests(unittest.TestCase):
    @staticmethod
    def make_archive(path: Path, root: str, marker: str) -> tuple[str, str]:
        checksum_text = "placeholder checksum list\n"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"{root}/CHAIN.csv", "step,from_version\n")
            archive.writestr(f"{root}/SHA256SUMS.txt", checksum_text)
            archive.writestr(f"{root}/{marker}.txt", marker)
        return (
            rak_chain.sha256_file(path),
            hashlib.sha256(checksum_text.encode("ascii")).hexdigest(),
        )

    def test_extraction_cache_is_bound_to_exact_archive_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = "RAK3401-test-bundle"
            first = Path(directory) / "first.zip"
            second = Path(directory) / "second.zip"
            destination = Path(directory) / "bundle"
            first_sha, checksum_sha = self.make_archive(first, root, "first")
            second_sha, second_checksum_sha = self.make_archive(
                second, root, "second"
            )
            self.assertEqual(checksum_sha, second_checksum_sha)
            with mock.patch.dict(
                rak_chain.PINNED_ARCHIVE_CHECKSUMS,
                {
                    first_sha: checksum_sha,
                    second_sha: checksum_sha,
                },
                clear=True,
            ):
                extracted = rak_chain.extract_bundle(
                    first, destination, first_sha
                )
                self.assertEqual(extracted, destination / root)
                with self.assertRaisesRegex(
                    ota.OtaError, "bound to a different archive"
                ):
                    rak_chain.extract_bundle(second, destination, second_sha)

    def test_matching_legacy_cache_is_adopted_only_after_checksum_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = "RAK3401-test-bundle"
            archive = Path(directory) / "bundle.zip"
            destination = Path(directory) / "bundle"
            archive_sha, checksum_sha = self.make_archive(
                archive, root, "candidate"
            )
            with mock.patch.dict(
                rak_chain.PINNED_ARCHIVE_CHECKSUMS,
                {archive_sha: checksum_sha},
                clear=True,
            ):
                rak_chain.extract_bundle(archive, destination, archive_sha)
                binding = destination / rak_chain.EXTRACTION_BINDING_FILE
                binding.unlink()
                extracted = rak_chain.extract_bundle(
                    archive, destination, archive_sha
                )
                self.assertEqual(extracted, destination / root)
                self.assertEqual(
                    json.loads(binding.read_text(encoding="ascii"))[
                        "archive_sha256"
                    ],
                    archive_sha,
                )

    def test_extraction_never_removes_a_preexisting_part_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = "RAK3401-test-bundle"
            parent = Path(directory)
            archive = parent / "bundle.zip"
            destination = parent / "bundle"
            old_fixed_scratch = parent / f"bundle.part-{os.getpid()}"
            old_fixed_scratch.mkdir()
            sentinel = old_fixed_scratch / "keep.txt"
            sentinel.write_text("owned by caller", encoding="ascii")
            archive_sha, checksum_sha = self.make_archive(
                archive, root, "candidate"
            )
            with mock.patch.dict(
                rak_chain.PINNED_ARCHIVE_CHECKSUMS,
                {archive_sha: checksum_sha},
                clear=True,
            ):
                rak_chain.extract_bundle(archive, destination, archive_sha)
            self.assertEqual(sentinel.read_text(encoding="ascii"), "owned by caller")

    def test_extraction_uses_frozen_archive_after_caller_path_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = "RAK3401-test-bundle"
            parent = Path(directory)
            archive = parent / "bundle.zip"
            replacement = parent / "replacement.zip"
            destination = parent / "extracted"
            archive_sha, checksum_sha = self.make_archive(
                archive, root, "first"
            )
            self.make_archive(replacement, root, "replacement")
            original_freeze = rak_chain.freeze_archive

            def freeze_then_replace(source: Path, frozen: Path) -> str:
                digest = original_freeze(source, frozen)
                os.replace(replacement, source)
                return digest

            with (
                mock.patch.dict(
                    rak_chain.PINNED_ARCHIVE_CHECKSUMS,
                    {archive_sha: checksum_sha},
                    clear=True,
                ),
                mock.patch.object(
                    rak_chain,
                    "freeze_archive",
                    side_effect=freeze_then_replace,
                ),
            ):
                extracted = rak_chain.extract_bundle(
                    archive, destination, archive_sha
                )

            self.assertTrue((extracted / "first.txt").is_file())
            self.assertFalse((extracted / "replacement.txt").exists())

    def test_archive_member_limit_is_enforced_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = "RAK3401-test-bundle"
            parent = Path(directory)
            archive = parent / "bundle.zip"
            destination = parent / "extracted"
            archive_sha, checksum_sha = self.make_archive(
                archive, root, "candidate"
            )
            with (
                mock.patch.dict(
                    rak_chain.PINNED_ARCHIVE_CHECKSUMS,
                    {archive_sha: checksum_sha},
                    clear=True,
                ),
                mock.patch.object(rak_chain, "MAX_BUNDLE_MEMBER_BYTES", 8),
                self.assertRaisesRegex(ota.OtaError, "exceeds"),
            ):
                rak_chain.extract_bundle(archive, destination, archive_sha)
            self.assertFalse(destination.exists())

    @staticmethod
    def make_extracted_tree(root: Path) -> tuple[Path, str]:
        bundle = root / "RAK3401-test-bundle"
        bundle.mkdir()
        payload = bundle / "motas" / "step.mota"
        payload.parent.mkdir()
        payload.write_bytes(b"pinned package bytes")
        checksum = hashlib.sha256(payload.read_bytes()).hexdigest()
        checksum_text = f"{checksum}  motas/step.mota\n"
        checksum_path = bundle / "SHA256SUMS.txt"
        checksum_path.write_text(checksum_text, encoding="ascii")
        return bundle, hashlib.sha256(checksum_text.encode("ascii")).hexdigest()

    def test_bundle_snapshot_is_immune_to_mutation_after_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, checksum_digest = self.make_extracted_tree(root)
            snapshot_parent = root / "snapshot"
            snapshot_parent.mkdir()
            with mock.patch.object(
                rak_chain, "CHECKSUM_LIST_SHA256", checksum_digest
            ):
                snapshot = rak_chain.snapshot_verified_bundle(
                    bundle, snapshot_parent
                )
            (bundle / "motas" / "step.mota").write_bytes(b"changed by caller")

            self.assertEqual(
                (snapshot / "motas" / "step.mota").read_bytes(),
                b"pinned package bytes",
            )

    def test_bundle_snapshot_detects_mutation_during_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, checksum_digest = self.make_extracted_tree(root)
            snapshot_parent = root / "snapshot"
            snapshot_parent.mkdir()
            original_copy = shutil.copyfileobj

            def copy_then_mutate(source: object, destination: object, length: int) -> None:
                original_copy(source, destination, length)
                if Path(source.name).name == "SHA256SUMS.txt":
                    (bundle / "motas" / "step.mota").write_bytes(b"changed mid-copy")

            with (
                mock.patch.object(
                    rak_chain, "CHECKSUM_LIST_SHA256", checksum_digest
                ),
                mock.patch.object(
                    rak_chain.shutil, "copyfileobj", side_effect=copy_then_mutate
                ),
                self.assertRaisesRegex(
                    ota.OtaError, "changed size|checksum mismatch"
                ),
            ):
                rak_chain.snapshot_verified_bundle(bundle, snapshot_parent)

    def test_bundle_snapshot_rejects_large_unlisted_file_before_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, checksum_digest = self.make_extracted_tree(root)
            (bundle / "unlisted.bin").write_bytes(b"x" * 32)
            snapshot_parent = root / "snapshot"
            snapshot_parent.mkdir()
            with (
                mock.patch.object(
                    rak_chain, "CHECKSUM_LIST_SHA256", checksum_digest
                ),
                mock.patch.object(rak_chain, "MAX_BUNDLE_MEMBER_BYTES", 24),
                mock.patch.object(rak_chain.shutil, "copyfileobj") as copy_file,
                self.assertRaisesRegex(ota.OtaError, "limit"),
            ):
                rak_chain.snapshot_verified_bundle(bundle, snapshot_parent)
            copy_file.assert_not_called()

    def test_bundle_snapshot_rejects_listed_file_growth_before_its_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, checksum_digest = self.make_extracted_tree(root)
            snapshot_parent = root / "snapshot"
            snapshot_parent.mkdir()
            payload = bundle / "motas" / "step.mota"
            original_copy = rak_chain.copy_regular_file_limited

            def grow_then_copy(
                source: Path,
                destination: Path,
                maximum: int,
                label: str,
                *,
                expected_size: int | None = None,
            ) -> int:
                if source == payload:
                    with source.open("ab") as output:
                        output.write(b"growth after inventory")
                return original_copy(
                    source,
                    destination,
                    maximum,
                    label,
                    expected_size=expected_size,
                )

            with (
                mock.patch.object(
                    rak_chain, "CHECKSUM_LIST_SHA256", checksum_digest
                ),
                mock.patch.object(
                    rak_chain,
                    "copy_regular_file_limited",
                    side_effect=grow_then_copy,
                ),
                self.assertRaisesRegex(ota.OtaError, "changed size"),
            ):
                rak_chain.snapshot_verified_bundle(bundle, snapshot_parent)
            self.assertFalse((snapshot_parent / bundle.name).exists())

    def test_chain_state_rejects_redirected_steps_and_progress_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            external = root / "external"
            external.mkdir()
            (work / "steps").symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(ota.OtaError, "real directory"):
                rak_chain.validate_chain_state_paths(work)

        if hasattr(os, "mkfifo"):
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                os.mkfifo(work / "progress.jsonl")
                with self.assertRaisesRegex(ota.OtaError, "regular file"):
                    rak_chain.validate_chain_state_paths(work)

    def test_progress_append_refuses_symlink_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            victim = work / "victim.txt"
            victim.write_text("unchanged\n", encoding="ascii")
            (work / "progress.jsonl").symlink_to(victim)
            step = mock.Mock(
                number=1, from_version="1.0", to_version="1.1"
            )
            with self.assertRaisesRegex(ota.OtaError, "symbolic link"):
                rak_chain.append_progress(work, step, b"12345678")
            self.assertEqual(victim.read_text(encoding="ascii"), "unchanged\n")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO test requires POSIX")
    def test_progress_append_refuses_fifo_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            os.mkfifo(work / "progress.jsonl")
            step = mock.Mock(
                number=1, from_version="1.0", to_version="1.1"
            )
            with self.assertRaisesRegex(ota.OtaError, "safely open"):
                rak_chain.append_progress(work, step, b"12345678")

    def test_attempt_work_directory_is_reserved_under_real_steps_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            attempt = rak_chain.next_attempt_dir(work, 3)
            self.assertEqual(attempt.name, "work")
            self.assertTrue(attempt.parent.is_dir())
            self.assertFalse(attempt.exists())

    def test_work_directory_cannot_be_inside_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            work = bundle / "work"
            work.mkdir(parents=True)
            with self.assertRaisesRegex(ota.OtaError, "outside the supplied bundle"):
                rak_chain.require_bundle_work_separation(bundle, work)

    def test_chain_rejects_controller_serial_aliases_before_live_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "ttyACM0"
            device.touch()
            alias = root / "by-id-radio"
            alias.symlink_to(device)
            parser = rak_chain.build_parser()
            for source_arguments in (
                ["--source-serial", str(alias)],
                [
                    "--source-tcp", "192.0.2.10:5001",
                    "--source-cli-serial", str(alias),
                ],
            ):
                args = parser.parse_args([
                    "--controller-serial", str(device), *source_arguments
                ])
                with self.subTest(source_arguments=source_arguments), \
                        contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit):
                    rak_chain.validate_args(args, parser)

    def test_chain_source_cli_transports_are_mutually_exclusive(self) -> None:
        parser = rak_chain.build_parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([
                "--source-tcp", "192.0.2.10:5001",
                "--source-cli-serial", "/dev/source",
                "--source-cli-tcp", "192.0.2.10:5002",
            ])

    def test_chain_already_temp_source_rejects_managed_cli(self) -> None:
        parser = rak_chain.build_parser()
        args = parser.parse_args([
            "--source-tcp", "192.0.2.10:5001",
            "--source-cli-tcp", "192.0.2.10:5002",
            "--source-already-temp",
        ])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            rak_chain.validate_args(args, parser)

    def test_chain_endpoint_recovers_persisted_source_rxps(self) -> None:
        saved = ota.RxpsSettings(True, 18205, 20423, 8, 16)
        current = ota.RxpsSettings(False, 18205, 20423, 8, 16)
        source_args = argparse.Namespace(
            source_cli_serial=None,
            source_serial="/dev/source",
            source_cli_tcp=None,
            source_baud=115200,
        )
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            ota.write_source_rxps_recovery(work_dir, source_args, saved)
            with (
                mock.patch.object(ota, "read_source_rxps", return_value=current),
                mock.patch.object(
                    ota, "shorten_source_temp_window", return_value=True
                ) as shorten,
                mock.patch.object(ota, "restore_source_rxps") as restore,
            ):
                rak_chain.restore_persisted_source_rxps(
                    work_dir, source_args
                )
                self.assertFalse(
                    (work_dir / ota.SOURCE_RXPS_RECOVERY_FILE).exists()
                )
        shorten.assert_called_once_with(source_args)
        restore.assert_called_once_with(source_args, saved)

    def test_chain_endpoint_proves_normalradio_even_when_rxps_already_matches(self) -> None:
        saved = ota.RxpsSettings(False, 18205, 20423, 8, 16)
        source_args = argparse.Namespace(
            source_cli_serial=None,
            source_serial="/dev/source",
            source_cli_tcp=None,
            source_baud=115200,
        )
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            ota.write_source_rxps_recovery(work_dir, source_args, saved)
            with (
                mock.patch.object(ota, "read_source_rxps", return_value=saved),
                mock.patch.object(
                    ota, "shorten_source_temp_window", return_value=True
                ) as shorten,
                mock.patch.object(ota, "restore_source_rxps") as restore,
            ):
                rak_chain.restore_persisted_source_rxps(work_dir, source_args)
            self.assertFalse(
                (work_dir / ota.SOURCE_RXPS_RECOVERY_FILE).exists()
            )
        shorten.assert_called_once_with(source_args)
        restore.assert_not_called()


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

    def test_completed_chain_step_does_not_claim_idle_store_is_cleared(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.replies = iter([
                    "OTA | no download | target:2FA509C1",
                ])

            def remote_command(self, _target: str, command: str) -> str:
                self.commands.append(command)
                return next(self.replies)

        controller = Controller()
        step = mock.Mock(number=12, package=mock.Mock(manifest_id="1234ABCD"))
        rak_chain.clear_completed_download(controller, "remote", step)
        self.assertEqual(controller.commands, ["ota status"])

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

    def test_chain_requires_rescue_for_intermediate_start_or_resume_only(self) -> None:
        controller = mock.Mock()
        with mock.patch.object(
            rak_chain, "require_rescue_capability"
        ) as require_rescue:
            rak_chain.require_rescue_capability_before_next_transition(
                controller, "remote", 0, 2
            )
            require_rescue.assert_not_called()

            rak_chain.require_rescue_capability_before_next_transition(
                controller, "remote", 1, 2
            )
            require_rescue.assert_called_once_with(controller, "remote")

            require_rescue.reset_mock()
            rak_chain.require_rescue_capability_before_next_transition(
                controller, "remote", 2, 2
            )
            require_rescue.assert_not_called()

    def test_chain_step_propagates_debug_to_nested_runner(self) -> None:
        args = argparse.Namespace(
            controller_serial="/dev/controller",
            controller_tcp=None,
            controller_ble=None,
            source_serial="/dev/source",
            source_tcp=None,
            source_cli_serial=None,
            source_cli_tcp=None,
            source_already_temp=False,
            source_shares_controller=False,
            controller_baud=115200,
            source_baud=115200,
            relay_txdelay=0.3,
            temp_radio="909.950,500,5,5,120",
            meshcli="meshcli",
            motatool="motatool",
            reply_timeout=45,
            discovery_timeout=180,
            discovery_interval=8,
            poll_seconds=60,
            transfer_timeout_minutes=90,
            seeder_start_wait=5,
            reboot_wait=90,
            relay=[],
            debug=False,
        )
        step = mock.Mock(
            number=1,
            path=Path("step-01.mota"),
            package=mock.Mock(manifest_id="1234ABCD"),
        )
        controller = mock.Mock()

        for enabled in (False, True):
            with self.subTest(debug=enabled), tempfile.TemporaryDirectory() as directory:
                args.debug = enabled
                with mock.patch.object(rak_chain.ota, "main", return_value=0) as nested:
                    rak_chain.run_step(
                        args,
                        "remote",
                        step,
                        None,
                        bytes.fromhex("0011223344556677"),
                        Path(directory),
                        controller,
                    )
                command = nested.call_args.args[0]
                self.assertEqual(command.count("--debug"), int(enabled))

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

    def test_physically_passed_compact_9_step_release_needs_no_lab_ack(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(9)]
        for number, image_sha256 in rak_chain.COMPACT_RELEASE_ANCHORS:
            steps[number - 1].target_sha256 = image_sha256
        rak_chain.require_live_release_safe(
            argparse.Namespace(accept_test_candidate=False), steps
        )

    def test_exact_current_ten_step_candidate_requires_explicit_lab_ack(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(10)]
        for number, image_sha256 in rak_chain.CURRENT_10_CANDIDATE_ANCHORS:
            steps[number - 1].target_sha256 = image_sha256
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "new b40d2e6c step 10.*bootloader simulators only",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=False), steps
            )
        rak_chain.require_live_release_safe(
            argparse.Namespace(accept_test_candidate=True), steps
        )

    def test_exact_fd98_ten_step_candidate_remains_gated_legacy(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(10)]
        for number, image_sha256 in rak_chain.PHYSICALLY_PASSED_FD98_ANCHORS:
            steps[number - 1].target_sha256 = image_sha256
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "legacy fd98bc90.*all ten pinned package transitions completed",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=False), steps
            )
        rak_chain.require_live_release_safe(
            argparse.Namespace(accept_test_candidate=True), steps
        )

    def test_current_and_legacy_ten_step_reports_do_not_mix_evidence(self) -> None:
        current = [mock.Mock(target_sha256="") for _ in range(10)]
        legacy = [mock.Mock(target_sha256="") for _ in range(10)]
        for number, image_sha256 in rak_chain.CURRENT_10_CANDIDATE_ANCHORS:
            current[number - 1].target_sha256 = image_sha256
        for number, image_sha256 in rak_chain.PHYSICALLY_PASSED_FD98_ANCHORS:
            legacy[number - 1].target_sha256 = image_sha256

        current_message = rak_chain.ten_step_verification_message(current)
        legacy_message = rak_chain.ten_step_verification_message(legacy)
        self.assertIn("new step 10", current_message)
        self.assertIn("not had a clean physical run", current_message)
        self.assertNotIn("endpoint passed independent SWD readback", current_message)
        self.assertIn("exact ten package transitions", legacy_message)
        self.assertIn("endpoint passed independent SWD readback", legacy_message)

    def test_ten_step_candidates_share_only_the_pinned_physical_prefix(self) -> None:
        self.assertEqual(
            rak_chain.CURRENT_10_CANDIDATE_ANCHORS[:9],
            rak_chain.PHYSICALLY_PASSED_FD98_ANCHORS[:9],
        )
        self.assertNotEqual(
            rak_chain.CURRENT_10_CANDIDATE_ANCHORS[-1],
            rak_chain.PHYSICALLY_PASSED_FD98_ANCHORS[-1],
        )
        steps = [mock.Mock(target_sha256="") for _ in range(10)]
        for number, image_sha256 in rak_chain.CURRENT_10_CANDIDATE_ANCHORS:
            steps[number - 1].target_sha256 = image_sha256
        steps[8].target_sha256 = "00" * 32
        self.assertIsNone(rak_chain.ten_step_candidate_kind(steps))

    def test_changed_current_ten_step_anchor_is_not_recognized(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(10)]
        for number, image_sha256 in rak_chain.CURRENT_10_CANDIDATE_ANCHORS:
            steps[number - 1].target_sha256 = image_sha256
        steps[-1].target_sha256 = "00" * 32
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "unrecognized variant of the pinned ten-step candidate",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=True), steps
            )

    def test_published_candidate_has_no_implicit_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / rak_chain.ASSET_NAME
            with self.assertRaisesRegex(
                ota.OtaError, "automatic release download is disabled.*--bundle"
            ):
                rak_chain.download_release_asset(destination)

    def test_fd98_archive_and_inner_checksum_remain_pinned(self) -> None:
        self.assertEqual(
            rak_chain.PINNED_ARCHIVE_CHECKSUMS[
                rak_chain.PHYSICALLY_PASSED_FD98_ASSET_SHA256
            ],
            rak_chain.PHYSICALLY_PASSED_FD98_CHECKSUM_LIST_SHA256,
        )

    def test_locate_bundle_accepts_exact_fd98_legacy_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / rak_chain.PHYSICALLY_PASSED_FD98_ASSET_NAME
            archive.write_bytes(b"fixture is identified by the mocked digest")
            extracted = root / rak_chain.PHYSICALLY_PASSED_FD98_ROOT_NAME
            args = argparse.Namespace(bundle=archive)
            with (
                mock.patch.object(
                    rak_chain,
                    "sha256_file_limited",
                    return_value=rak_chain.PHYSICALLY_PASSED_FD98_ASSET_SHA256,
                ),
                mock.patch.object(
                    rak_chain, "extract_bundle", return_value=extracted
                ) as extract_bundle,
            ):
                self.assertEqual(rak_chain.locate_bundle(args, root / "work"), extracted)
            extract_bundle.assert_called_once_with(
                archive.resolve(),
                root / "work" / "bundle",
                rak_chain.PHYSICALLY_PASSED_FD98_ASSET_SHA256,
            )

    def test_compact_9_step_release_rejects_changed_anchor(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(9)]
        for number, image_sha256 in rak_chain.COMPACT_RELEASE_ANCHORS:
            steps[number - 1].target_sha256 = image_sha256
        steps[5].target_sha256 = "00" * 32
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "unrecognized step-6 image",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=True), steps
            )

    def test_superseded_30_step_release_is_blocked(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(30)]
        for number, image_sha256 in rak_chain.PINNED_RELEASE_ANCHORS:
            steps[number - 1].target_sha256 = image_sha256
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "superseded 30-step release",
        ):
            rak_chain.require_live_release_safe(
                argparse.Namespace(accept_test_candidate=True), steps
            )

    def test_superseded_physically_passed_29_step_release_is_blocked(self) -> None:
        steps = [mock.Mock(target_sha256="") for _ in range(29)]
        for number, image_sha256 in rak_chain.SUPERSEDED_29_ANCHORS:
            steps[number - 1].target_sha256 = image_sha256
        with self.assertRaisesRegex(
            rak_chain.KnownUnsafeReleaseError,
            "superseded 29-step release",
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
        candidates = [
            os.environ.get("MOTATOOL_TEST_BIN"),
            shutil.which("motatool"),
            str(
                Path(__file__).resolve().parents[3]
                / "motatool"
                / "target"
                / "release"
                / "motatool"
            ),
        ]
        cls.motatool = next(
            (candidate for candidate in candidates
             if candidate and Path(candidate).is_file()),
            None,
        )
        if not cls.motatool or not Path(cls.motatool).is_file():
            raise unittest.SkipTest(
                "install/build motatool or set MOTATOOL_TEST_BIN to run "
                "motatool integration tests"
            )
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

    def test_raw_qspi_nrf52_zip_becomes_full_without_base(self) -> None:
        image = firmware(b"nrf-qspi-new" * 800, VERSION_NEW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("firmware.bin", image)
            work = root / "work"
            work.mkdir()
            _path, package, _expected = ota.prepare_package(
                prepare_args(archive_path, self.motatool),
                target(platform="nrf52", nrf_qspi=True, boot_codecs=1),
                work,
            )
            self.assertTrue(package.is_full)


if __name__ == "__main__":
    unittest.main(verbosity=2)
