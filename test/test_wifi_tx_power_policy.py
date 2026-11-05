"""Hold the W12 WiFi transmit ceiling to one table across the profile flag and the driver call."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TABLE = r"""
#include <cassert>
#include <cstdint>
#include <helpers/esp32/WiFiTxPowerPolicy.h>

int main() {
  namespace W = mesh::wifi;
#if defined(WIFI_TX_POWER_DBM)
  assert(W::hasConfiguredTxPowerCap());
  assert(W::configuredTxPowerUnits() == WIFI_TX_POWER_DBM * W::kTxPowerStepUnits);
  assert(W::txPowerDbmFromUnits(W::configuredTxPowerUnits()) == WIFI_TX_POWER_DBM);
#else
  assert(!W::hasConfiguredTxPowerCap());
  assert(W::configuredTxPowerUnits() == 0);
#endif
  // Every whole dBm in the SDK range must survive the 0.25 dBm conversion.
  for (int dbm = 2; dbm <= 20; ++dbm) {
    const int units = dbm * W::kTxPowerStepUnits;
    assert(units >= W::kTxPowerMinUnits && units <= W::kTxPowerMaxUnits);
    assert(W::txPowerDbmFromUnits(static_cast<int8_t>(units)) == dbm);
  }
  return 0;
}
"""



class WiFiTxPowerPolicyTest(unittest.TestCase):
    def compile_and_run(self, source, flags):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "A host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="wifi-tx-power-") as directory:
            path = Path(directory)
            (path / "test.cpp").write_text(source, encoding="ascii")
            sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
                         if sys.platform.startswith("linux") else [])
            result = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", *sanitizer, *flags,
                 "-I", str(ROOT / "src"), str(path / "test.cpp"), "-o", str(path / "test")],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(path / "test")], capture_output=True,
                                    text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_w12_profile_flag_is_the_only_source_of_the_ceiling(self):
        for flag in ("-DWIFI_TX_POWER_DBM=13", "-DWIFI_TX_POWER_DBM=2", "-DWIFI_TX_POWER_DBM=20"):
            with self.subTest(flag=flag):
                self.compile_and_run(TABLE, (flag,))
        self.compile_and_run(TABLE, ())

    def test_out_of_range_profile_flags_are_rejected_at_compile_time(self):
        for flag in ("-DWIFI_TX_POWER_DBM=1", "-DWIFI_TX_POWER_DBM=21", "-DWIFI_TX_POWER_DBM=-4"):
            with self.subTest(flag=flag):
                compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
                with tempfile.TemporaryDirectory(prefix="wifi-tx-power-") as directory:
                    path = Path(directory)
                    (path / "test.cpp").write_text(TABLE, encoding="ascii")
                    result = subprocess.run(
                        [compiler, "-std=c++17", flag, "-I", str(ROOT / "src"),
                         str(path / "test.cpp"), "-o", str(path / "test")],
                        capture_output=True, text=True, timeout=60)
                    self.assertNotEqual(result.returncode, 0,
                        f"{flag} must fail the static_assert")
                    self.assertIn("WIFI_TX_POWER_DBM must be 2..20 dBm",
                                  result.stdout + result.stderr)

    def test_w12_join_uses_saved_manual_or_automatic_preference(self):
        policy = (ROOT / "src/helpers/esp32/WiFiStationPolicy.h").read_text()
        self.assertIn("#if defined(WIFI_TX_POWER_DBM)", policy)
        self.assertIn("const esp_err_t tx_result = applyStationTxPower();", policy)
        self.assertIn("tx_result != ESP_ERR_WIFI_NOT_STARTED", policy)
        self.assertIn("tx_result != ESP_ERR_WIFI_NOT_INIT", policy)



if __name__ == "__main__":
    unittest.main()
