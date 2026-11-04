"""Exercise the shared ESP32 station power policy at its SDK boundary."""
from pathlib import Path
import unittest

import test_wifi_ota_start as wifi_start

ROOT = Path(__file__).resolve().parents[1]


def power_policy():
    source = (ROOT / "src/helpers/esp32/WiFiStationPower.h").read_text()
    return source[source.index("namespace mesh {"):]


def tx_power_policy():
    # The whole header: the WIFI_TX_POWER_DBM range static_assert is part of the
    # contract the modem-sleep clamp depends on.
    return (ROOT / "src/helpers/esp32/WiFiTxPowerPolicy.h").read_text()


class WiFiStationPowerTest(unittest.TestCase):
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run

    def fixture(self):
        return r'''
#include <cassert>
#include <cstdint>
using esp_err_t = int;
enum wifi_ps_type_t { WIFI_PS_NONE, WIFI_PS_MIN_MODEM, WIFI_PS_MAX_MODEM };
enum wifi_power_t { WIFI_POWER_8_5dBm=34, WIFI_POWER_11dBm=44,
                    WIFI_POWER_17dBm=68, WIFI_POWER_19_5dBm=78 };
constexpr int ESP_OK=0, ESP_FAIL=-1, ESP_ERR_INVALID_STATE=0x103;
int ps_result=ESP_OK, applied_ps=-1, applied_tx=-1, tx_writes=0, table_step=4;
bool tx_success=true, driver_ignores_write=false;
int esp_wifi_set_ps(wifi_ps_type_t mode) { applied_ps=mode; return ps_result; }
int esp_wifi_set_max_tx_power(int8_t power) {
  ++tx_writes;
  if (!tx_success) return -8;
  if (!driver_ignores_write) applied_tx=power - (power % table_step);
  return ESP_OK;
}
int esp_wifi_get_max_tx_power(int8_t* out) { *out = applied_tx; return ESP_OK; }
''' + tx_power_policy() + power_policy() + r'''
int main() {
  using mesh::wifi::applyStationPowerSave;
  using mesh::wifi::stationPowerSaveTxPower;
#ifdef TEST_TX_CEILING
  // esp_wifi_set_max_tx_power() replaces the ceiling, so a profile cap of
  // WIFI_TX_POWER_DBM must survive every modem-sleep write.
  {
    const int normal=78, saving=44;
    const int expected_normal=normal < TX_CEILING_UNITS ? normal : TX_CEILING_UNITS;
    const int expected_saving=saving < TX_CEILING_UNITS ? saving : TX_CEILING_UNITS;
    assert(applyStationPowerSave(WIFI_PS_NONE)==ESP_OK);
    assert(applied_tx==expected_normal);
    assert(applyStationPowerSave(WIFI_PS_MIN_MODEM)==ESP_OK);
    assert(applied_tx==expected_saving);
    assert(stationPowerSaveTxPower(WIFI_PS_NONE)==expected_normal);

    // A driver that floors the request to its own table still satisfies the cap.
    table_step=8;
    assert(applyStationPowerSave(WIFI_PS_NONE)==ESP_OK);
    assert(applied_tx==expected_normal-(expected_normal%8));
    table_step=4;

    // If the driver silently ignores the write, the read-back reports it.
    const int writes_before=tx_writes;
    applied_tx=84;
    driver_ignores_write=true;
    assert(applyStationPowerSave(WIFI_PS_NONE)==ESP_ERR_INVALID_STATE);
    assert(tx_writes==writes_before+1);  // the write was attempted, not skipped
    driver_ignores_write=false;
    return 0;
  }
#endif
#ifdef TEST_OVERRIDES
  const int normal=68, saving=34;
#else
  const int normal=78, saving=44;
#endif
  // Restoring NONE must undo a prior low-power cap, including repeated joins.
  for (auto mode : {WIFI_PS_NONE, WIFI_PS_MIN_MODEM, WIFI_PS_MAX_MODEM,
                    WIFI_PS_NONE, WIFI_PS_NONE}) {
    assert(applyStationPowerSave(mode)==ESP_OK);
    assert(applied_ps==mode);
    assert(applied_tx==(mode==WIFI_PS_NONE ? normal : saving));
  }
  ps_result=-7;
  const int previous_writes=tx_writes;
  assert(applyStationPowerSave(WIFI_PS_MIN_MODEM)==-7);
  assert(tx_writes==previous_writes);
  ps_result=ESP_OK; tx_success=false;
  assert(applyStationPowerSave(WIFI_PS_MIN_MODEM)==-8);
  tx_success=true;
  assert(applyStationPowerSave(WIFI_PS_NONE)==ESP_OK && applied_tx==normal);
}
'''

    def test_normal_power_restored_and_sdk_failures_propagated(self):
        self.compile_and_run("#include <initializer_list>\n" + self.fixture())

    def test_explicit_normal_and_power_save_overrides(self):
        self.compile_and_run("#include <initializer_list>\n" + self.fixture(),
                             "-DTEST_OVERRIDES=1",
                             "-DMQTT_WIFI_TX_POWER=WIFI_POWER_17dBm",
                             "-DMQTT_WIFI_POWER_SAVE_TX_POWER=WIFI_POWER_8_5dBm")

    def test_profile_tx_ceiling_is_not_reopened_by_power_save(self):
        # 13 dBm = 52 SDK units: below the 19.5 dBm modem-sleep write, so the
        # clamp must hold it. 20 dBm = 80 units: above it, so normal power is
        # left alone.
        self.compile_and_run("#include <initializer_list>\n" + self.fixture(),
                             "-DTEST_TX_CEILING=1", "-DWIFI_TX_POWER_DBM=13",
                             "-DTX_CEILING_UNITS=52")
        self.compile_and_run("#include <initializer_list>\n" + self.fixture(),
                             "-DTEST_TX_CEILING=1", "-DWIFI_TX_POWER_DBM=20",
                             "-DTX_CEILING_UNITS=80")


if __name__ == "__main__":
    unittest.main()
