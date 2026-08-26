#!/usr/bin/env python3

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_firmware_capabilities.py"


class FirmwareCapabilityCheckerTest(unittest.TestCase):
    def run_checker(self, image_bytes, *extra_args):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        directory = Path(temp_dir.name)
        image = directory / "firmware.elf"
        manifest = directory / "firmware.capabilities.json"
        image.write_bytes(image_bytes)
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


if __name__ == "__main__":
    unittest.main()
