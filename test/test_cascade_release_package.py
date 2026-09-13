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
    def test_portable_exclusions_require_a_complete_summary(self):
        self.assertEqual(package.portable_profile_exclusions({}), [])
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "matrix.log"
            status = {"log": str(log)}
            header = "2 standard ESP32 target(s) exceeded the portable OTA slot and were deferred to the expanded FULL pass:\n"
            for body in ("DEFERRED: one (standard)\n", header + "  one\n", header + "  one\n  one\n"):
                log.write_text(body)
                with self.assertRaisesRegex(ValueError, "exclusion summary"):
                    package.portable_profile_exclusions(status)
            log.write_text(header + "  one\n  two\nLogging matrix completed successfully.\n")
            self.assertEqual([r["target"] for r in package.portable_profile_exclusions(status)], ["one", "two"])

    def test_partial_matrix_requires_completion_and_an_explicit_opt_in(self):
        self.assertEqual(package.completed_matrix_failures({"state": "completed", "exit_code": "0"}), [])
        for state, code in (("running", "0"), ("starting", "0"), ("failed", "143")):
            with self.subTest(state=state, code=code), self.assertRaisesRegex(ValueError, "finished matrix"):
                package.completed_matrix_failures({"state": state, "exit_code": code}, True)
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "matrix.log"
            status = {"state": "failed", "exit_code": "1", "log": str(log)}
            with self.assertRaisesRegex(ValueError, "not completed successfully"):
                package.completed_matrix_failures(status)
            for body in ("", "Building a target\n", "Logging matrix completed with 2 failed build(s):\n  one (standard) -> /tmp/one.log\n"):
                log.write_text(body)
                with self.assertRaisesRegex(ValueError, "summary"):
                    package.completed_matrix_failures(status, True)
            log.write_text("Logging matrix completed with 2 failed build(s):\n"
                           "  one (standard) -> /tmp/one.log\n"
                           "  two (full-usb-wifi-ota) -> /tmp/two.log\n")
            failures = package.completed_matrix_failures(status, True)
            self.assertEqual([f["target"] for f in failures], ["one", "two"])
            self.assertEqual(failures[1]["profile"], "full-usb-wifi-ota")
            self.assertEqual(failures[1]["log_file"], "two.log")

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
            proof = dict(schema_version=1, passed=True, available_internal_bytes=80000,
                         required_heap_bytes=50000, largest_internal_region_bytes=80000,
                         required_contiguous_bytes=5120, elf_sha256="a" * 64,
                         files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.iterdir()})
            (inputs / (stem + ".memory.json")).write_text(json.dumps(proof))
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

            # A finished matrix can publish good files while explicitly
            # retaining the failed attempts and the real nonzero exit code.
            log = directory / "matrix.log"
            log.write_text("1 standard ESP32 target(s) exceeded the portable OTA slot and were deferred to the expanded FULL pass:\n"
                           "  large_repeater\n"
                           "Logging matrix completed with 1 failed build(s):\n"
                           "  missing_repeater (standard) -> /tmp/missing.log\n")
            status.write_text("state=failed\n" + settings.replace("exit_code=0", "exit_code=1")
                              + f"log={log}\n")
            partial_command = command.copy()
            partial_command[partial_command.index("--output") + 1] = str(directory / "partial")
            result = subprocess.run(partial_command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((directory / "partial").exists())
            result = subprocess.run(partial_command + ["--allow-partial"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((directory / "partial/release-plan.json").read_text())
            self.assertEqual(plan["matrix"]["exit_code"], 1)
            self.assertEqual(plan["matrix"]["failures"][0]["target"], "missing_repeater")
            assets = directory / "partial/companion"
            self.assertIn("Partial matrix", (assets / "BUILD-NOTES.txt").read_text())
            self.assertIn("missing_repeater", (assets / "BUILD-FAILURES.md").read_text())
            self.assertEqual(plan["matrix"]["portable_profile_exclusions"][0]["target"], "large_repeater")
            self.assertIn("large_repeater", (assets / "PORTABLE-PROFILE-EXCLUSIONS.md").read_text())
            self.assertIn("Portable image limits", (assets / "BUILD-NOTES.txt").read_text())
            self.assertEqual(len(list(assets.iterdir())), plan["groups"][0]["asset_count"])
            checksums = (assets / "SHA256SUMS.txt").read_text()
            self.assertIn("BUILD-FAILURES.json", checksums)
            self.assertIn("PORTABLE-PROFILE-EXCLUSIONS.json", checksums)
            for line in checksums.splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((assets / name).read_bytes()).hexdigest(), digest)

            # Opting in to holes never authorizes publishing bad firmware.
            (inputs / (stem + ".bin")).write_bytes(b"corrupted")
            partial_command[partial_command.index("--output") + 1] = str(directory / "bad")
            result = subprocess.run(partial_command + ["--allow-partial"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((directory / "bad").exists())


if __name__ == "__main__":
    unittest.main()
