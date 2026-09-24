"""No-hardware contract tests for the serial ESP32 migration build recipe."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_esp32_partition_migration import build_steps, verify_archive  # noqa: E402


class Esp32MigrationRecipeTest(unittest.TestCase):
    def test_full_images_precede_every_bridge_and_builds_are_serial(self):
        steps = build_steps(["heltec-v4", "xiao-s3-wio"], "v1.17.1.7-test",
                            "usa-cascadia", "cascade", 4)
        self.assertEqual(6, len(steps))
        self.assertEqual([True, True, False, False, False, False],
                         [full for _, full in steps])
        self.assertEqual("heltec_v4_repeater", steps[0][0][3])
        self.assertEqual("Xiao_S3_WIO_repeater", steps[1][0][3])
        self.assertTrue(all("--full-exact" in command for command, _ in steps[:2]))
        self.assertEqual([
            "heltec_v4_partition_migrator",
            "heltec_v4_partition_migrator_lora_repeater",
            "xiao_s3_partition_migrator",
            "xiao_s3_partition_migrator_lora_repeater",
        ], [command[3] for command, _ in steps[2:]])

    def test_dry_run_has_no_build_side_effects(self):
        with tempfile.TemporaryDirectory(prefix="esp32-migration-plan-") as temporary:
            output_root = Path(temporary) / "release"
            result = subprocess.run([
                sys.executable, "-B",
                str(ROOT / "scripts/build_esp32_partition_migration.py"),
                "--version", "v1.17.1.7-test", "--radio-preset", "usa-cascadia",
                "--board", "heltec-v4", "--output-root", str(output_root),
                "--dry-run",
            ], cwd=ROOT, text=True, capture_output=True, check=True)
            self.assertIn("build-firmware heltec_v4_repeater", result.stdout)
            self.assertIn("pio run -e heltec_v4_partition_migrator", result.stdout)
            self.assertFalse(output_root.exists())

    def test_archive_verification_rejects_changed_payload(self):
        with tempfile.TemporaryDirectory(prefix="esp32-migration-zip-") as temporary:
            archive_path = Path(temporary) / "test.zip"
            payload = b"correct image"
            manifest = {
                "board": "heltec-v4", "firmware_version": "v1.17.1.7-test",
                "source_commit": "01234567", "files": {
                    "full-application.bin": {
                        "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                },
            }
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                archive.writestr("full-application.bin", payload)
            verify_archive(archive_path, "heltec-v4", "v1.17.1.7-test", "01234567")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                archive.writestr("full-application.bin", b"damaged image")
            with self.assertRaisesRegex(ValueError, "invalid full-application.bin"):
                verify_archive(archive_path, "heltec-v4", "v1.17.1.7-test", "01234567")


if __name__ == "__main__":
    unittest.main()
