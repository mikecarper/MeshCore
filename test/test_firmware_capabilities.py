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
sys.path.insert(0, str(ROOT / "tools" / "mota"))
import motalib as mota


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

    def test_application_gate_rejects_elf_only_logging_evidence(self):
        # Debug/symbol strings in the ELF must not qualify an image whose
        # uploadable binary has had the implementation compiled or linked out.
        result, manifest = self.run_checker(
            b"USB logger", "--expect-application", "logging.usb.packets=USB logger",
            artifacts={"firmware-bin": b"no logging implementation"})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(manifest["verified"])
        self.assertIn("packaged application", result.stderr)

    def test_application_gate_accepts_packaged_code_without_elf_marker(self):
        result, manifest = self.run_checker(
            b"stripped symbols", "--expect-application", "logging.usb.packets=USB logger",
            artifacts={"firmware-bin": b"USB logger"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(manifest["verified"])
        self.assertIn("logging.usb.packets", manifest["capabilities"])
        self.assertEqual(manifest["verification"][0]["source"], "packaged application")

    def test_full_logging_requires_packet_code_as_well_as_the_setting(self):
        expectations = ["--expect-application", "logging.usb.control=usb.logging",
                        "--expect-application", "logging.usb.packets=packet logger"]
        for body, accepted in [(b"usb.logging debug logger", False),
                               (b"packet logger", False),
                               (b"usb.logging packet logger", True)]:
            with self.subTest(body=body):
                result, manifest = self.run_checker(b"", *expectations,
                                                     artifacts={"firmware-bin": body})
                self.assertEqual(result.returncode, 0 if accepted else 1, result.stderr)
                self.assertEqual(manifest["verified"], accepted)

    def test_application_checks_require_the_installable_artifact(self):
        result, manifest = self.run_checker(
            b"USB logger", "--expect-application", "logging.usb.packets=USB logger")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(manifest["verified"])
        self.assertIn("require --firmware-bin or --dfu-package", result.stderr)

    def test_nrf52_logging_is_checked_inside_the_dfu_application(self):
        for body, accepted in [(b"packet logger", True), (b"no logger", False)]:
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("manifest.json", json.dumps({"manifest": {
                    "application": {"bin_file": "app.bin"}}}))
                package.writestr("app.bin", body)
                # A marker elsewhere in the ZIP is not proof of running code.
                package.writestr("notes.txt", "packet logger")
            result, manifest = self.run_checker(
                b"packet logger", "--platform", "NRF52_PLATFORM",
                "--expect-application", "logging.usb.packets=packet logger",
                artifacts={"dfu-package": archive.getvalue()})
            self.assertEqual(result.returncode, 0 if accepted else 1, result.stderr)
            self.assertEqual(manifest["verified"], accepted)

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

    def nrf_dfu(self, flags=16, *, receiver=True, corrupt=False):
        body = b"OTA: status" + (b"invalid in-place patch geometry" if receiver else b"source only")
        layout = mota.Nrf52Layout(0x26000, 0xED000, 0xED000, flags)
        body = mota.ensure_nrf52_layout(body, layout)
        image = body + mota.build_endf(body)
        if corrupt:
            image = b"X" + image[1:]
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("manifest.json", json.dumps({"manifest": {
                "application": {"bin_file": "app.bin", "dat_file": "app.dat"}}}))
            package.writestr("app.bin", image)
            package.writestr("app.dat", b"init packet")
        return {"dfu-package": archive.getvalue()}

    def test_nrf52_lora_requires_packaged_receiver_and_valid_layout(self):
        for artifacts in [self.nrf_dfu(receiver=False), self.nrf_dfu(corrupt=True)]:
            result, manifest = self.run_checker(
                b"dfu invalid in-place patch geometry", "--platform", "NRF52_PLATFORM",
                "--require-ota", "--expect", "ota.update.bluetooth=dfu",
                "--expect", "ota.update.lora=invalid in-place patch geometry", artifacts=artifacts)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(manifest["ota_update_verified"])
            self.assertNotIn("ota.update.lora", manifest["capabilities"])

    def test_nrf52_lora_reports_storage_specific_requirements(self):
        for flags, storage, types in [(16, "internal_flash_and_retained_ram", ["in_place_delta"]),
                                      (0, "internal_flash", ["in_place_delta"]),
                                      (4, "external_qspi", ["full", "in_place_delta"]),
                                      (1, "external_sd", ["full", "in_place_delta"])]:
            result, manifest = self.run_checker(
                b"dfu invalid in-place patch geometry", "--platform", "NRF52_PLATFORM",
                "--require-ota", "--expect", "ota.update.bluetooth=dfu",
                "--expect", "ota.update.lora=invalid in-place patch geometry", artifacts=self.nrf_dfu(flags))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(manifest["ota_update_methods"], ["bluetooth", "lora"])
            details = manifest["ota_update_requirements"]["lora"]
            self.assertEqual(details["storage"], storage)
            self.assertEqual(details["package_types"], types)
            self.assertTrue(details["bootloader_release"].endswith("0.11.0-OTAFIX2.4.6"))

    def test_nrf52_full_companion_cannot_claim_lora_self_update(self):
        result, manifest = self.run_checker(
            b"invalid in-place patch geometry", "--platform", "NRF52_PLATFORM",
            "--target", "RAK_4631_companion_radio_full", "--require-ota",
            "--expect", "ota.update.lora=invalid in-place patch geometry", artifacts=self.nrf_dfu())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(manifest["ota_update_methods"], [])


if __name__ == "__main__":
    unittest.main()
