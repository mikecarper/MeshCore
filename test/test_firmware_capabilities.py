#!/usr/bin/env python3

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import struct
import io
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_firmware_capabilities.py"


class FirmwareCapabilityCheckerTest(unittest.TestCase):
    def run_checker(self, image_bytes, *extra_args, artifacts=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        directory = Path(temp_dir.name)
        image = directory / "firmware.elf"
        manifest = directory / "firmware.capabilities.json"
        image.write_bytes(image_bytes)
        artifact_args = []
        for option, data in (artifacts or {}).items():
            path = directory / option
            path.write_bytes(data)
            artifact_args.extend(["--" + option, str(path)])
        result = subprocess.run(
            [
                sys.executable,
                str(CHECKER),
                "--image",
                str(image),
                "--output",
                str(manifest),
                "--target",
                "test_target",
                "--platform",
                "ESP32_PLATFORM",
                "--build-profile",
                "auto",
                *extra_args,
                *artifact_args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return result, json.loads(manifest.read_text())

    def test_writes_verified_manifest(self):
        result, manifest = self.run_checker(
            b"prefix retry.preset suffix",
            "--capability",
            "cli.retry_preset",
            "--expect",
            "cli.retry_preset=retry.preset",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(manifest["verified"])
        self.assertEqual(manifest["build_profile"], "auto")

    def test_fails_when_promised_marker_is_missing(self):
        result, manifest = self.run_checker(
            b"unrelated image",
            "--expect",
            "web.webconfig=start webconfig",
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(manifest["verified"])
        self.assertIn("promised", result.stderr)

    def ota_artifacts(self, *, second_slot=True, image_size=48):
        def entry(kind, subtype, address, size):
            return struct.pack("<HBBII16sI", 0x50AA, kind, subtype,
                               address, size, b"test", 0)
        table = entry(1, 0, 0xe000, 0x2000) + entry(0, 0x10, 0x10000, 64)
        if second_slot:
            table += entry(0, 0x11, 0x20000, 64)
        return {"firmware-bin": b"x" * image_size, "partitions": table}

    def test_seeder_does_not_count_as_self_update(self):
        result, manifest = self.run_checker(
            b"ota folder on", "--require-ota", "--expect",
            "companion.usb_mota_source=ota folder on")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(manifest["verified"])
        self.assertEqual(manifest["ota_update_methods"], [])

    def test_wifi_update_requires_two_slots(self):
        result, manifest = self.run_checker(
            b"uploader", "--require-ota", "--expect", "ota.update.wifi=uploader",
            artifacts=self.ota_artifacts(second_slot=False))
        self.assertEqual(result.returncode, 1)
        self.assertIn("ota_1", manifest["ota_update_evidence"])

    def test_wifi_update_must_fit_both_slots(self):
        result, manifest = self.run_checker(
            b"uploader", "--require-ota", "--expect", "ota.update.wifi=uploader",
            artifacts=self.ota_artifacts(image_size=65))
        self.assertEqual(result.returncode, 1)
        self.assertFalse(manifest["ota_update_verified"])

    def test_verified_wifi_update(self):
        result, manifest = self.run_checker(
            b"uploader", "--require-ota", "--expect", "ota.update.wifi=uploader",
            artifacts=self.ota_artifacts())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(manifest["ota_update_verified"])
        self.assertEqual(manifest["ota_update_methods"], ["wifi"])

    def test_bluetooth_requires_a_real_dfu_package(self):
        result, manifest = self.run_checker(
            b"dfu", "--platform", "NRF52_PLATFORM", "--require-ota", "--expect",
            "ota.update.bluetooth=dfu")
        self.assertEqual(result.returncode, 1)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("manifest.json", json.dumps({"manifest": {
                "application": {"bin_file": "app.bin", "dat_file": "app.dat"}}}))
            package.writestr("app.bin", b"firmware")
            package.writestr("app.dat", b"init packet")
        result, manifest = self.run_checker(
            b"dfu", "--platform", "NRF52_PLATFORM", "--require-ota", "--expect",
            "ota.update.bluetooth=dfu", artifacts={"dfu-package": archive.getvalue()})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(manifest["ota_update_methods"], ["bluetooth"])

    def test_usb_companion_can_report_no_self_update(self):
        result, manifest = self.run_checker(
            b"ota folder on", "--expect", "companion.usb_mota_source=ota folder on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(manifest["verified"])
        self.assertFalse(manifest["ota_update_verified"])


if __name__ == "__main__":
    unittest.main()
