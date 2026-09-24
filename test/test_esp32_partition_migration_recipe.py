"""No-hardware contract tests for the serial ESP32 migration build recipe."""

import hashlib
import json
import os
from pathlib import Path
import re
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
from package_esp32_partition_migration import BOARDS, mota_full, readme  # noqa: E402
from motalib import FwIdent  # noqa: E402


class Esp32MigrationRecipeTest(unittest.TestCase):
    def test_catalog_covers_all_three_roles_and_exact_flash_plans(self):
        self.assertEqual({"repeater", "room-server", "sensor"},
                         {spec["role"] for spec in BOARDS.values()})
        self.assertIn("heltec-v4-sensor", BOARDS)
        self.assertIn("xiao-s3-wio-sensor", BOARDS)
        self.assertIn("heltec-ct62-sensor", BOARDS)
        self.assertIn("nibble-zero-room-server", BOARDS)
        self.assertEqual("esp32_c3_4mb_partition_migrator",
                         BOARDS["heltec-ct62-sensor"]["wifi_bridge"])
        self.assertEqual("esp32_4mb_partition_migrator",
                         BOARDS["generic-e22-sx1262-repeater"]["wifi_bridge"])
        self.assertEqual("esp32_s3_8mb_partition_migrator",
                         BOARDS["heltec-v3-sensor"]["wifi_bridge"])
        self.assertEqual("esp32_8mb_partition_migrator",
                         BOARDS["heltec-v2-room-server"]["wifi_bridge"])
        expected_slots = {4 * 1024 * 1024: 0x1F0000,
                          8 * 1024 * 1024: 0x330000,
                          16 * 1024 * 1024: 0x640000}
        env_names = set()
        for config in (ROOT / "variants").glob("*/platformio.ini"):
            env_names.update(re.findall(r"^\[env:([^]]+)\]",
                                        config.read_text(), re.MULTILINE))
        for board, spec in BOARDS.items():
            with self.subTest(board=board):
                self.assertIn(spec["target"], env_names)
                self.assertIn(spec["wifi_bridge"], env_names)
                self.assertIn(spec["lora_bridge"], env_names)
                self.assertEqual(expected_slots[spec["flash_bytes"]],
                                 spec["slot_bytes"])

    @unittest.skipIf(os.name == "nt", "POSIX shell menu runs in WSL/Linux")
    def test_shell_menu_and_noninteractive_selection(self):
        script = str(ROOT / "scripts/build_esp32_partition_migration.sh")
        common = ["--version", "v1.17.1.7-test",
                  "--radio-preset", "usa-cascadia", "--dry-run"]
        one = subprocess.run(["sh", script, "--board", "heltec-v4", *common],
                             cwd=ROOT, text=True, capture_output=True, check=True)
        self.assertIn("build-firmware heltec_v4_repeater", one.stdout)
        self.assertNotIn("build-firmware Xiao_S3_WIO_repeater", one.stdout)
        self.assertNotIn("Bundle all configured board/role recipes", one.stdout)
        all_boards = subprocess.run(["sh", script, "--all", *common],
                                    cwd=ROOT, text=True, capture_output=True, check=True)
        self.assertIn("build-firmware Xiao_S3_WIO_repeater", all_boards.stdout)
        self.assertIn("build-firmware ThinkNode_M2_Repeater", all_boards.stdout)
        self.assertIn("Bundle all configured board/role recipes", all_boards.stdout)
        sensors = subprocess.run(["sh", script, "--role", "sensor", *common],
                                 cwd=ROOT, text=True, capture_output=True,
                                 check=True)
        self.assertIn("build-firmware Xiao_S3_WIO_sensor", sensors.stdout)
        self.assertNotIn("build-firmware Xiao_S3_WIO_repeater", sensors.stdout)
        self.assertNotIn("Bundle all configured board/role recipes", sensors.stdout)
        interactive = subprocess.run(["sh", script], input="q\n",
                                     cwd=ROOT, text=True, capture_output=True,
                                     check=True)
        self.assertIn("ESP32 in-place partition migration", interactive.stdout)
        invalid = subprocess.run(["sh", script, "--board", "unknown", *common],
                                 cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(0, invalid.returncode)
        self.assertIn("not configured", invalid.stderr)

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

    def test_4mb_roles_share_two_chip_family_bridges(self):
        steps = build_steps(["thinknode-m2-repeater", "thinknode-m2-room-server"],
                            "v1.17.1.7-test", "usa-cascadia", "cascade", 4)
        self.assertEqual([True, True, False, False], [full for _, full in steps])
        self.assertEqual("ThinkNode_M2_Repeater", steps[0][0][3])
        self.assertEqual("ThinkNode_M2_room_server", steps[1][0][3])
        self.assertEqual("esp32_s3_4mb_partition_migrator", steps[2][0][3])
        self.assertEqual("esp32_s3_4mb_partition_migrator_lora", steps[3][0][3])
        guide = readme("thinknode-m2-repeater", BOARDS["thinknode-m2-repeater"],
                       "v1.17.1.7-test", "01234567", full_mota_blocks=900)
        self.assertIn("old slot B", guide)
        self.assertIn("original private key from NVS", guide)
        self.assertIn("power loss during", guide)
        self.assertIn("exact\n   `Started:` URL", guide)
        self.assertIn("ota pull <id>", guide)
        large_guide = readme("heltec-v3-sensor", BOARDS["heltec-v3-sensor"],
                             "v1.17.1.7-test", "01234567", full_mota_blocks=1400)
        self.assertIn("5600 bytes of proof scratch", large_guide)

    def test_sensor_and_room_roles_share_the_8mb_bridge(self):
        steps = build_steps(["xiao-s3-wio-sensor", "xiao-s3-wio-room-server"],
                            "v1.17.1.7-test", "usa-cascadia", "cascade", 4)
        self.assertEqual([True, True, False, False], [full for _, full in steps])
        self.assertEqual("Xiao_S3_WIO_sensor", steps[0][0][3])
        self.assertEqual("Xiao_S3_WIO_room_server", steps[1][0][3])
        self.assertEqual("esp32_s3_8mb_partition_migrator", steps[2][0][3])
        self.assertEqual("esp32_s3_8mb_partition_migrator_lora", steps[3][0][3])

    def test_full_mota_can_exceed_old_seeder_scratch_limit(self):
        image = b"\xE9" + b"\0" * (1024 * 2048)
        package = mota_full(image, FwIdent(0x01020304, 0xAABBCCDD, "TEST"), 2048)
        self.assertGreater(len(package), len(image))

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
            sample_boards = ["heltec-v4", "xiao-s3-wio-room-server",
                             "heltec-ct62-sensor"]
            for board in sample_boards:
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
                                     sample_boards,
                                     version, source, "usa-cascadia", "cascade")
            with zipfile.ZipFile(release) as archive:
                self.assertIsNone(archive.testzip())
                release_manifest = json.loads(archive.read("release-manifest.json"))
                self.assertEqual(sample_boards,
                                 release_manifest["boards"])
                self.assertEqual(["repeater", "room-server", "sensor"],
                                 release_manifest["roles"])
                self.assertEqual("repeater",
                                 release_manifest["board_roles"]["heltec-v4"])
                self.assertEqual("room-server", release_manifest["board_roles"][
                    "xiao-s3-wio-room-server"])
                self.assertEqual("sensor", release_manifest["board_roles"][
                    "heltec-ct62-sensor"])
                for name, checks in release_manifest["packages"].items():
                    data = archive.read(name)
                    self.assertEqual(checks["bytes"], len(data))
                    self.assertEqual(checks["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(release, bundle_release(
                output_root, package_dir, sample_boards,
                version, source, "usa-cascadia", "cascade"))
            (package_dir / f"heltec-ct62-sensor-{version}-{source}-migration.zip").unlink()
            with self.assertRaisesRegex(ValueError, "missing heltec-ct62-sensor"):
                bundle_release(output_root, package_dir,
                               sample_boards,
                               version, source, "usa-cascadia", "cascade")


if __name__ == "__main__":
    unittest.main()
