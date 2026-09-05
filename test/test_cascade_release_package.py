#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
import hashlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_cascade_release", ROOT / "scripts/package_cascade_release.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class ReleaseQualificationTest(unittest.TestCase):
    def manifest(self, target="test_repeater", **changes):
        return {"target": target, "platform": "ESP32_PLATFORM",
                "schema_version": 2, "verified": True,
                "ota_update_verified": True, "verification": [], **changes}

    def test_source_capability_cannot_qualify_infrastructure(self):
        with self.assertRaisesRegex(ValueError, "wireless updater"):
            package.validate_manifest(self.manifest(ota_update_verified=False))

    def test_usb_companion_is_accepted(self):
        package.validate_manifest(self.manifest("test_companion_radio_usb", ota_update_verified=False))

    def test_full_companion_requires_linked_source_implementation(self):
        manifest = self.manifest("test_companion_radio_full", ota_update_verified=False)
        with self.assertRaisesRegex(ValueError, "MOTA sending"):
            package.validate_manifest(manifest)
        manifest["verification"] = [{"capability": name, "present": True} for name in (
            "companion.usb_mota_source", "companion.mota_sender", "companion.temp_radio",
            "companion.ota_cli", "companion.wifi_ota_seeder")]
        package.validate_manifest(manifest)
        manifest["verification"][1]["present"] = False
        with self.assertRaisesRegex(ValueError, "MOTA sending"):
            package.validate_manifest(manifest)

    def test_missing_qualification_and_mixed_versions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            stem = "test_companion_radio_usb-v1.0-deadbeef"
            (directory / (stem + ".bin")).write_bytes(b"firmware")
            with self.assertRaisesRegex(ValueError, "without qualification"):
                package.collect_artifacts(directory, "v1.0-deadbeef")
            (directory / (stem + ".capabilities.json")).write_text(json.dumps(self.manifest("test_companion_radio_usb")))
            with self.assertRaisesRegex(ValueError, "mixed-version"):
                package.collect_artifacts(directory, "v2.0-deadbeef")
            with self.assertRaisesRegex(ValueError, "pair incomplete"):
                package.collect_artifacts(directory, "v1.0-deadbeef")

    def test_stages_named_prerelease_and_checksums_only_after_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            inputs = directory / "input"
            inputs.mkdir()
            commit = "deadbeef" + "0" * 32
            label = "v1.17.1.5-halo-keymind-cascade-dev"
            stem = "test_companion_radio_usb-" + label + "-deadbeef"
            manifest = self.manifest("test_companion_radio_usb", ota_update_verified=False,
                                     artifact_target="test_companion_radio_usb", build_profile="standard")
            (inputs / (stem + ".capabilities.json")).write_text(json.dumps(manifest))
            (inputs / (stem + ".bin")).write_bytes(b"test application")
            (inputs / (stem + "-merged.bin")).write_bytes(b"test merged image")
            status = directory / "status"
            settings = (f"exit_code=0\nworking_directory={directory}\noutput_directory=input\n"
                        f"source_commit={commit}\nfirmware_version={label}\nfirmware_profile=cascade\n"
                        "radio_frequency=910.525\nradio_bandwidth=62.5\nradio_sf=7\nradio_cr=5\n")
            status.write_text("state=running\n" + settings)
            command = [sys.executable, str(ROOT / "scripts/package_cascade_release.py"),
                       "--input", str(inputs), "--output", str(directory / "stage"),
                       "--build-status", str(status), "--commit", commit]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((directory / "stage").exists())
            status.write_text("state=completed\n" + settings)
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((directory / "stage/release-plan.json").read_text())
            self.assertEqual(plan["groups"][0]["tag"], label + "-deadbeef")
            self.assertTrue(plan["groups"][0]["prerelease"])
            assets = directory / "stage/companion"
            picker = (directory / "stage/FIRMWARE-PICKER-1.17.1.5.html").read_text()
            self.assertIn(f'companion/{stem}.bin', picker)
            self.assertNotIn("/releases/download/", picker)
            self.assertIn("companion/FULL-COMPANION-FEATURES.md", picker)
            self.assertIn("/releases/download/", (assets / "FIRMWARE-PICKER-1.17.1.5.html").read_text())
            self.assertIn(stem + ".bin", (assets / "TARGET-MANIFEST.tsv").read_text())
            self.assertIn("910.525", (assets / "BUILD-NOTES.txt").read_text())
            for line in (assets / "SHA256SUMS.txt").read_text().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((assets / name).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
