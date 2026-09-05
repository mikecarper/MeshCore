#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
