"""No-hardware contract tests for the serial ESP32 migration build recipe."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_esp32_partition_migration import (  # noqa: E402
    build_steps, bundle_release, verify_archive,
)


class Esp32MigrationRecipeTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX shell menu runs in WSL/Linux")
    def test_shell_menu_and_noninteractive_selection(self):
        script = str(ROOT / "scripts/build_esp32_partition_migration.sh")
        common = ["--version", "v1.17.1.7-test",
                  "--radio-preset", "usa-cascadia", "--dry-run"]
        one = subprocess.run(["sh", script, "--board", "heltec-v4", *common],
                             cwd=ROOT, text=True, capture_output=True, check=True)
        self.assertIn("build-firmware heltec_v4_repeater", one.stdout)
        self.assertNotIn("build-firmware Xiao_S3_WIO_repeater", one.stdout)
        self.assertNotIn("Bundle complete qualified set", one.stdout)
        all_boards = subprocess.run(["sh", script, "--all", *common],
                                    cwd=ROOT, text=True, capture_output=True, check=True)
        self.assertIn("build-firmware Xiao_S3_WIO_repeater", all_boards.stdout)
        self.assertIn("Bundle complete qualified set", all_boards.stdout)
        interactive = subprocess.run(["sh", script], input="q\n",
                                     cwd=ROOT, text=True, capture_output=True,
                                     check=True)
        self.assertIn("ESP32 in-place partition migration", interactive.stdout)
        invalid = subprocess.run(["sh", script, "--board", "unknown", *common],
                                 cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(0, invalid.returncode)
        self.assertIn("not qualified", invalid.stderr)

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

    def test_all_bundle_contains_only_verified_board_packages(self):
        with tempfile.TemporaryDirectory(prefix="esp32-migration-release-") as temporary:
            output_root = Path(temporary)
            package_dir = output_root / "packages"
            package_dir.mkdir()
            version = "v1.17.1.7-test"
            source = "01234567"
            for board in ("heltec-v4", "xiao-s3-wio"):
                payload = (board + " image").encode()
                manifest = {
                    "board": board, "firmware_version": version,
                    "source_commit": source, "files": {
                        "full-application.bin": {
                            "bytes": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    },
                }
                archive_path = package_dir / f"{board}-{version}-{source}-migration.zip"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr("manifest.json", json.dumps(manifest))
                    archive.writestr("full-application.bin", payload)
            release = bundle_release(output_root, package_dir,
                                     ["heltec-v4", "xiao-s3-wio"],
                                     version, source, "usa-cascadia", "cascade")
            with zipfile.ZipFile(release) as archive:
                self.assertIsNone(archive.testzip())
                release_manifest = json.loads(archive.read("release-manifest.json"))
                self.assertEqual(["heltec-v4", "xiao-s3-wio"],
                                 release_manifest["boards"])
                for name, checks in release_manifest["packages"].items():
                    data = archive.read(name)
                    self.assertEqual(checks["bytes"], len(data))
                    self.assertEqual(checks["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(release, bundle_release(
                output_root, package_dir, ["heltec-v4", "xiao-s3-wio"],
                version, source, "usa-cascadia", "cascade"))
            (package_dir / f"xiao-s3-wio-{version}-{source}-migration.zip").unlink()
            with self.assertRaisesRegex(ValueError, "missing xiao-s3-wio"):
                bundle_release(output_root, package_dir,
                               ["heltec-v4", "xiao-s3-wio"],
                               version, source, "usa-cascadia", "cascade")


if __name__ == "__main__":
    unittest.main()
