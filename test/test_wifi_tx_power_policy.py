"""Hold the W12 WiFi transmit ceiling to one table across the profile flag and the driver call."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
RADIO_POLICY = (ROOT / "src/helpers/esp32/WiFiRadioPolicy.h").read_text()
CAP_POLICY = extract_braced(RADIO_POLICY, "inline esp_err_t applyStationTxPowerCap()")

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
  // One acceptance rule, shared by the join path and the modem-sleep path:
  // a reported ceiling no higher than the request is the cap in effect.
  assert(W::txCeilingSatisfied(52, 52));
  assert(W::txCeilingSatisfied(44, 52));
  assert(!W::txCeilingSatisfied(56, 52));
  return 0;
}
"""

DRIVER = r"""
#include <cassert>
#include <cstdint>
#include <helpers/esp32/WiFiTxPowerPolicy.h>

enum esp_err_t {
  ESP_OK = 0,
  ESP_ERR_INVALID_ARG = 1,
  ESP_ERR_INVALID_STATE = 2,
  ESP_ERR_WIFI_NOT_INIT = 3,
  ESP_ERR_WIFI_NOT_STARTED = 4,
};

int calls = 0;
int8_t ceiling = 84;
int8_t table_step = 4;      // the driver's own power-table granularity
bool started = true;
bool initialized = true;
bool driver_ignores_write = false;

esp_err_t esp_wifi_set_max_tx_power(int8_t power) {
  ++calls;
  if (!initialized) return ESP_ERR_WIFI_NOT_INIT;
  if (!started) return ESP_ERR_WIFI_NOT_STARTED;
  if (power < 8 || power > 84) return ESP_ERR_INVALID_ARG;
  if (driver_ignores_write) return ESP_OK;  // keeps reporting its old ceiling
  ceiling = power - (power % table_step);    // floored to the driver's table
  return ESP_OK;
}
esp_err_t esp_wifi_get_max_tx_power(int8_t* out) {
  // The SDK refuses both reads and writes before the driver is initialized or
  // started, so the mock has to report that or the readiness branches go
  // untested.
  if (!initialized) return ESP_ERR_WIFI_NOT_INIT;
  if (!started) return ESP_ERR_WIFI_NOT_STARTED;
  *out = ceiling;
  return ESP_OK;
}

namespace mesh { namespace wifi {
@CAP_POLICY@
}}

int main() {
  namespace W = mesh::wifi;
#if defined(WIFI_TX_POWER_DBM)
  calls = 0; started = true; initialized = true; ceiling = 84; table_step = 4; driver_ignores_write = false;
  assert(W::applyStationTxPowerCap() == ESP_OK);
  assert(calls == 1);
  assert(ceiling == W::configuredTxPowerUnits());

  // A driver that is not initialized or not started yet is not a policy failure:
  // the ceiling is deferred, not dropped, and no write is attempted.
  calls = 0; started = true; initialized = false; ceiling = 84;
  assert(W::applyStationTxPowerCap() == ESP_OK);
  assert(calls == 0);

  calls = 0; started = false; initialized = true; ceiling = 84;
  assert(W::applyStationTxPowerCap() == ESP_OK);
  assert(calls == 0);

  // Once the driver is up, the deferred ceiling is written on the next join.
  calls = 0; started = true; initialized = true; ceiling = 84;
  assert(W::applyStationTxPowerCap() == ESP_OK);
  assert(calls == 1);
  assert(ceiling == W::configuredTxPowerUnits());

  // The driver floors a request to its own power table, so an applied ceiling
  // below the request is still a successful cap: 13 dBm becomes 12 dBm when the
  // table steps by 2 dBm.
  calls = 0; started = true; initialized = true; ceiling = 84; table_step = 8; driver_ignores_write = false;
  assert(W::applyStationTxPowerCap() == ESP_OK);
  assert(calls == 1);
  const int8_t floored = static_cast<int8_t>(W::configuredTxPowerUnits()
                                           - (W::configuredTxPowerUnits() % 8));
  assert(ceiling == floored);
  assert(floored < W::configuredTxPowerUnits());

  // A reported ceiling above the request means the write never took effect.
  calls = 0; started = true; initialized = true; ceiling = 84; table_step = 4; driver_ignores_write = true;
  assert(W::applyStationTxPowerCap() == ESP_ERR_INVALID_STATE);
  assert(calls == 1);

  // A ceiling already at or below the cap (a power-save write, or the join that
  // applied it) is preserved without a redundant write.
  calls = 0; started = true; initialized = true; ceiling = 44; table_step = 4; driver_ignores_write = false;
  assert(W::applyStationTxPowerCap() == ESP_OK);
  assert(calls == 0);
  assert(ceiling == 44);

  calls = 0; started = true; initialized = true; ceiling = W::configuredTxPowerUnits();
  table_step = 4; driver_ignores_write = false;
  assert(W::applyStationTxPowerCap() == ESP_OK);
  assert(calls == 0);
#else
  calls = 0; started = true; initialized = true; ceiling = 84;
  assert(W::applyStationTxPowerCap() == ESP_OK);
  assert(calls == 0);  // boards without a profile ceiling never touch the driver
  assert(ceiling == 84);
#endif
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

    def test_driver_call_only_happens_for_boards_with_a_ceiling(self):
        fixture = DRIVER.replace("@CAP_POLICY@", CAP_POLICY)
        self.compile_and_run(fixture, ())
        self.compile_and_run(fixture, ("-DWIFI_TX_POWER_DBM=13",))


if __name__ == "__main__":
    unittest.main()
