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
import tempfile
import unittest
import zipfile

import lora_ota as ota


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
) -> bytes:
    if payload is None:
        payload = image if full else b"synthetic delta payload" * 100
    codec = ota.MOTA_CODEC_FULL if codec is None and full else (
        ota.MOTA_CODEC_SEQUENTIAL if codec is None else codec
    )
    block_size = 1024
    leaves = [
        hashlib.sha256(payload[offset:offset + block_size]).digest()[:4]
        for offset in range(0, len(payload), block_size)
    ]
    manifest = bytearray((ota.MOTA_FORMAT_VERSION, ota.MOTA_FLAG_FULL if full else 0, 0x12))
    manifest += struct.pack(
        "<IIII", target, version, len(image), len(payload)
    )
    manifest.append(10)
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
            ota.parse_temp_radio("909.950,250,7,5,120"),
            (909.95, 250.0, 7, 5, 120),
        )
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "bandwidth must be"):
            ota.parse_temp_radio("909.950,200,7,5,120")

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
        def __init__(self, replies: list[str]):
            self.replies = iter(replies)
            self.commands: list[str] = []

        def remote_command(self, _target: str, command: str) -> str:
            self.commands.append(command)
            return next(self.replies)

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

    def test_replace_active_session_requires_explicit_flag(self) -> None:
        controller = self.Controller([
            "OTA | download: downloading 3/9 id=DEADBEEF 2s",
            "OK dropped session",
            "Updates 1/1",
            "OK pulling mid=12345678 -> flash (low priority)",
        ])
        ota.find_and_start_pull(controller, self.args(replace=True), self.package)
        self.assertEqual(
            controller.commands,
            ["ota status", "ota cancel", "ota ls", f"ota pull {self.package.manifest_id} flash"],
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
