"""Exercise the shared ESP32 station power policy at its SDK boundary."""
from pathlib import Path
import unittest

import test_wifi_ota_start as wifi_start

ROOT = Path(__file__).resolve().parents[1]


def power_policy():
    source = (ROOT / "src/helpers/esp32/WiFiStationPower.h").read_text()
    return source[source.index("namespace mesh {"):]


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
constexpr int ESP_OK=0, ESP_FAIL=-1;
int ps_result=ESP_OK, applied_ps=-1, applied_tx=-1, tx_writes=0;
bool tx_success=true;
int esp_wifi_set_ps(wifi_ps_type_t mode) { applied_ps=mode; return ps_result; }
int esp_wifi_set_max_tx_power(int8_t power) {
  ++tx_writes;
  if (!tx_success) return -8;
  applied_tx=power; return ESP_OK;
}
''' + power_policy() + r'''
int main() {
  using mesh::wifi::applyStationPowerSave;
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


if __name__ == "__main__":
    unittest.main()
