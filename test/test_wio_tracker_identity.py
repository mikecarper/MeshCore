"""Verify USB-visible Wio Tracker identities without accessing a radio."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
STUB_ARDUINO = r"""
#pragma once
#include <cstdint>
constexpr int AR_INTERNAL = 0, PIN_VBAT_READ = 1;
constexpr float ADC_MULTIPLIER = 2.0f, AREF_VOLTAGE = 3.3f;
inline void analogReadResolution(int) {}
inline void analogReference(int) {}
inline void delay(int) {}
inline int analogRead(int) { return 0; }
"""
STUB_BOARD = r"""
#pragma once
#include <cstdint>
class KeyValueStore {};
class NRF52Board {
public:
  explicit NRF52Board(const char* = "") {}
  virtual ~NRF52Board() = default;
  virtual bool supportsBatteryCalibration() const { return false; }
  virtual uint16_t getBattMilliVolts() { return 0; }
  virtual const char* getManufacturerName() const = 0;
  virtual bool setLoRaFemPaGainEnabled(bool) { return false; }
  virtual bool isLoRaFemPaGainEnabled() const { return false; }
  virtual bool canControlLoRaFemPaGain() const { return false; }
  virtual void powerOff() {}
  uint16_t calibrateBatteryMillivolts(float value) { return value; }
};
class NRF52BoardDCDC : virtual public NRF52Board {};
"""


class WioTrackerIdentityTests(unittest.TestCase):
    def check_header(self, variant, expected, *, eink=False):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="wio-tracker-identity-") as folder:
            folder = Path(folder)
            (folder / "helpers").mkdir()
            (folder / "MeshCore.h").write_text("#pragma once\n", encoding="ascii")
            (folder / "Arduino.h").write_text(STUB_ARDUINO, encoding="ascii")
            (folder / "helpers/NRF52Board.h").write_text(STUB_BOARD, encoding="ascii")
            cpp = folder / "identity.cpp"
            executable = folder / ("identity.exe" if os.name == "nt" else "identity")
            source = f'''
#include <cassert>
#include <cstring>
#include "variants/{variant}/WioTrackerL1Board.h"
void WioTrackerL1Board::begin() {{}}
'''
            if variant.endswith("-1w"):
                source += "bool WioTrackerL1Board::setLoRaFemPaGainEnabled(bool) { return true; }\n"
            source += f'''
int main() {{
  const WioTrackerL1Board board;
  assert(std::strcmp(board.getManufacturerName(), "{expected}") == 0);
  assert(std::strcmp(board.getManufacturerName(), "Seeed Wio Tracker L1") != 0);
}}
'''
            cpp.write_text(source, encoding="ascii")
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                       "-I", str(folder), "-I", str(ROOT)]
            if eink:
                command.append("-DWIO_TRACKER_L1_EINK")
            result = subprocess.run(command + [str(cpp), "-o", str(executable)],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pro_reports_the_standard_power_profile(self):
        self.check_header("wio-tracker-l1", "Seeed Wio Tracker L1 Pro")

    def test_eink_reports_the_display_variant_from_its_existing_build_flag(self):
        configuration = (ROOT / "variants/wio-tracker-l1-eink/platformio.ini").read_text()
        self.assertIn("-I variants/wio-tracker-l1", configuration)
        self.assertIn("-D WIO_TRACKER_L1_EINK", configuration)
        self.check_header("wio-tracker-l1", "Seeed Wio Tracker L1 E-Ink", eink=True)

    def test_one_watt_reports_the_distinct_fem_power_profile(self):
        self.check_header("wio-tracker-l1-1w", "Seeed Wio Tracker L1 1W")


if __name__ == "__main__":
    unittest.main()
