#!/usr/bin/env python3
"""Keep chemistry-aware nRF52 safety through upstream bootlock-stopgap merges.

This parses the actual recipes without PlatformIO and executes the production
voltage rules on the host. It does not claim physical ADC/low-voltage testing.
"""
import configparser
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOARDS = {
    "gat562_30s_mesh_kit": "GAT562_30S_Mesh_Kit",
    "gat562_mesh_evb_pro": "GAT562_Mesh_EVB_Pro",
    "gat562_mesh_tracker_pro": "GAT562_Mesh_Tracker_Pro",
    "gat562_mesh_watch13": "GAT562_Mesh_Watch13",
    "heltec_t096": "Heltec_t096",
    "heltec_t114": "Heltec_t114",
    "muziworks_r1_neo": "R1Neo",
    "rak3401": "rak3401",
    "rak4631": "rak4631",
    "sensecap_solar": "SenseCap_Solar",
    "xiao_nrf52": "Xiao_nrf52",
}


class Recipes:
    def __init__(self):
        self.config = configparser.ConfigParser(interpolation=None,
                                                inline_comment_prefixes=(";",))
        self.config.read([ROOT / "platformio.ini",
                          *sorted(ROOT.glob("variants/*/platformio.ini"))])

    def value(self, section, option, seen=()):
        pair = (section, option)
        if pair in seen:
            raise AssertionError(f"recipe interpolation cycle: {pair}")
        seen = (*seen, pair)
        if not self.config.has_option(section, option):
            for parent in self.config.get(section, "extends", fallback="").split(","):
                if parent.strip():
                    value = self.value(parent.strip(), option, seen)
                    if value:
                        return value
            return ""
        raw = self.config.get(section, option)
        return re.sub(r"\$\{([^}.]+)\.([^}]+)\}",
                      lambda match: self.value(match[1], match[2], seen), raw)


class UpstreamPowerPolicyMergeTests(unittest.TestCase):
    def test_all_eleven_board_bases_keep_chemistry_power_policy(self):
        recipes = Recipes()
        for directory, section in BOARDS.items():
            with self.subTest(board=directory):
                self.assertTrue(recipes.config.has_section(section))
                flags = recipes.value(section, "build_flags")
                directives = re.findall(
                    r"(?:^|\s)-([DU])\s*NRF52_POWER_MANAGEMENT(?=$|\s|=)", flags)
                self.assertTrue(directives, f"{directory}: voltage policy compiled out")
                self.assertEqual(directives[-1], "D", f"{directory}: policy later undefined")
                self.assertNotIn("NRF52_POWER_MANAGEMENT",
                                 recipes.value(section, "build_unflags"))

    def test_affected_board_roles_inherit_chemistry_policy(self):
        recipes = Recipes()
        roles = 0
        for directory in BOARDS:
            local = configparser.ConfigParser(interpolation=None)
            local.read(ROOT / "variants" / directory / "platformio.ini")
            for section in local.sections():
                if not section.startswith("env:"):
                    continue
                with self.subTest(role=section):
                    flags = recipes.value(section, "build_flags")
                    directives = re.findall(
                        r"(?:^|\s)-([DU])\s*NRF52_POWER_MANAGEMENT(?=$|\s|=)", flags)
                    self.assertTrue(directives)
                    self.assertEqual(directives[-1], "D")
                    self.assertNotIn("NRF52_POWER_MANAGEMENT",
                                     recipes.value(section, "build_unflags"))
                    roles += 1
        self.assertGreater(roles, 30)

    def test_voltage_policy_still_loads_saved_chemistry_and_uses_safe_adc(self):
        board = (ROOT / "src/helpers/NRF52Board.cpp").read_text()
        policy = (ROOT / "src/helpers/NRF52VoltagePolicy.cpp").read_text()
        header = (ROOT / "src/helpers/NRF52VoltagePolicy.h").read_text()
        self.assertIn("defined(NRF52_POWER_MANAGEMENT)", header)
        self.assertIn("handleVoltagePolicyCommand", header)
        self.assertIn("loadVoltagePolicyAtBoot(config->voltage_bootlock)", board)
        self.assertIn("medianVoltage(samples[0], samples[1], samples[2])", board)
        self.assertIn("if (threshold == 0 || external) return true;", board)
        self.assertIn("lifepo4", policy)
        self.assertIn("configuredBootlock()", policy)
        self.assertIn("configuredCutoff()", policy)
        self.assertIn("AtomicFileWriter", policy)

    def test_native_voltage_rules_preserve_lifepo4_cutoff_and_boot_validation(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++17 compiler is required")
        source = r'''
#include <helpers/NRF52VoltagePolicy.h>
#include <helpers/PowerManagementUtils.h>
#include <cassert>
#include <type_traits>
int main() {
  using namespace mesh::power;
  static_assert(std::is_same<decltype(&configuredBootlock), uint16_t(*)()>::value, "boot API");
  static_assert(std::is_same<decltype(&configuredCutoff), uint16_t(*)()>::value, "cutoff API");
  assert(validThresholdPair(3300, 0, false));
  assert(validThresholdPair(3000, 2700, false));
  assert(!validThresholdPair(0, 0, false));
  assert(validThresholdPair(0, 0, true));
  assert(!validThresholdPair(3000, 3100, false));
  assert(batteryPercent(3550, 2700, 3550) == 100);
  assert(batteryPercent(2700, 2700, 3550) == 0);
  assert(medianVoltage(999, 3500, 3510) == 3500);
  assert(!shouldBootLock(999, 3300, false));
  assert(!shouldBootLock(3000, 3300, true));
  assert(shouldBootLock(3000, 3300, false));
  assert(nextLowVoltageCount(2, 2600, 2700, false) == 3);
  assert(nextLowVoltageCount(2, 2600, 2700, true) == 0);
}
'''
        with tempfile.TemporaryDirectory(prefix="meshcore-power-merge-") as directory:
            cpp = Path(directory) / "test.cpp"
            binary = Path(directory) / "test.exe"
            cpp.write_text(source)
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            built = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", *sanitizers,
                 "-DNRF52_PLATFORM=1", "-DNRF52_POWER_MANAGEMENT=1",
                 f"-I{ROOT / 'src'}", str(cpp), "-o", str(binary)],
                capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
