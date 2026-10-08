#!/usr/bin/env python3
"""Keep experimental RAK configs matched and separate from release recipes."""

import configparser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/generate_rak4631_combined_test.py"
SPEC = importlib.util.spec_from_file_location("rak_combined_config", SCRIPT)
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def ini(path):
    result = configparser.ConfigParser(interpolation=None)
    result.read(path)
    return result


class RakCombinedBuildConfigTest(unittest.TestCase):
    def test_matching_profiles_preserve_identity_and_never_run_platformio(self):
        original = (ROOT / "platformio.ini").read_bytes()
        variant = (ROOT / "variants/rak4631/platformio.ini").read_bytes()
        with tempfile.TemporaryDirectory(prefix="meshcore-rak-config-") as temp:
            directory = Path(temp)
            marker = directory / "unexpected-platformio"
            fake_pio = directory / "pio"
            fake_pio.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\nexit 91\n")
            fake_pio.chmod(0o755)
            output = directory / "generated configs"
            result = subprocess.run(["python3", str(SCRIPT), "--output", str(output)],
                                    cwd=ROOT, text=True, capture_output=True,
                                    env={**os.environ, "PATH": str(directory) + os.pathsep + os.environ["PATH"]})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists(), "config generation invoked PlatformIO")
            receipt = json.loads((output / "profile-flags.json").read_text())
            self.assertEqual(receipt["loop_stack_bytes"], 8192)
            self.assertEqual(receipt["full_rule_capacity"], 63)
            self.assertEqual(receipt["mota_target_id"], "0x05f5ffae")
            self.assertEqual(len(result.stdout.splitlines()), 4)
            for item in receipt["configs"]:
                name = item["profile"]
                config_path = Path(item["config"])
                config = ini(config_path)
                overlay_path = output / ("overlay-" + name + ".ini")
                overlay = ini(overlay_path)["env:" + receipt["target"]]
                flags = overlay["build_flags"]
                self.assertIn("-DMOTA_TARGET_ID=0x05f5ffae", flags)
                self.assertIn("-D MAX_NEIGHBOURS=50", flags)
                self.assertNotIn("FLOOD_PACKET_FILTER_SLOTS", flags)
                self.assertIn("-Ofast", overlay["build_unflags"])
                self.assertIn("-Os", flags)
                self.assertIn("-DLORA_FREQ=910.525", flags)
                self.assertIn(str(overlay_path), config["platformio"]["extra_configs"])
                self.assertEqual(hashlib.sha256(config_path.read_bytes()).hexdigest(), item["config_sha256"])
                combined = name.startswith("combined")
                self.assertEqual("-DRAK4631_COMBINED_ETHERNET=1" in flags, combined)
                self.assertEqual("-DETHERNET_ENABLED=1" in flags, combined)
                self.assertEqual("lib_deps" in overlay, combined)
                if combined:
                    self.assertIn(generator.ETHERNET_LIBRARY, overlay["lib_deps"])
                reduced = name.endswith("reduced")
                self.assertEqual("-UENV_INCLUDE_BME680_BSEC" in flags, reduced)
                if reduced:
                    self.assertIn("-DENV_INCLUDE_INA219=1", flags)
                    self.assertIn("${rak4631.build_flags}", flags)
                    self.assertNotIn("-UENV_INCLUDE_GPS", flags)
                    self.assertNotIn("ENV_INCLUDE_GPS", overlay["build_unflags"])
                    # GPS comes from the inherited full sensor base; the
                    # reduction never removes or overrides that driver.
                    self.assertIn("-D ENV_INCLUDE_GPS=1", ini(ROOT / "platformio.ini")["sensor_base"]["build_flags"])
            self.assertEqual((ROOT / "platformio.ini").read_bytes(), original)
            self.assertEqual((ROOT / "variants/rak4631/platformio.ini").read_bytes(), variant)

    def test_rejects_release_output_and_invalid_version_before_writing(self):
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            generator.generate(ROOT / "out" / "unexpected-trial", "v1.17.1.9-test")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "configs"
            for version in ("", "v1\n-DOTHER=1", 'v1"bad', "v1;bad", "v1$(bad)"):
                with self.subTest(version=version), self.assertRaisesRegex(ValueError, "firmware version"):
                    generator.generate(output, version)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
