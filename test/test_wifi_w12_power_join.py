"""Exercise W12 station joins with the real CLI power preference and policy."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_station_tx_power as station

ROOT = Path(__file__).resolve().parents[1]
join = extract_braced((ROOT / 'src/helpers/esp32/WiFiStationPolicy.h').read_text(),
                      'inline wl_status_t beginStation(')

HARNESS = r'''
#include <cassert>
#include <cstdint>
int mode=0, power=-1, set_ps_result=0, set_power_result=0, get_ps_result=0;
bool nvs_available=true, nvs_present=false, nvs_write=true, nvs_bad_type=false;
bool namespace_present=false, nvs_commit_ok=true;
uint8_t stored=0;
#include "helpers/esp32/WiFiStationPower.h"
constexpr int ESP_ERR_WIFI_NOT_STARTED=-30, ESP_ERR_WIFI_NOT_INIT=-31;
enum wl_status_t { WL_IDLE_STATUS=0, WL_CONNECT_FAILED=1 };
namespace mesh { namespace wireless {
enum { WiFi=1 }; struct Control { bool blocked(int) const { return false; } };
Control& control() { static Control c; return c; }
}}
namespace mesh { namespace wifi {
enum class ConnectionPhase { Blocked, NoSSID, PolicyFailed };
int phase_failures=0, joins=0;
void beginStationDiagnostics(const char*) {}
void noteStationDiagnosticPhase(ConnectionPhase) { ++phase_failures; }
bool espNowChannelConstrained() { return false; }
wl_status_t beginStationWithSecurity(const char*, const char*) { ++joins; return WL_IDLE_STATUS; }
@JOIN@
}}
int main() {
  using namespace mesh::wifi;
  assert(beginStation("ssid", "pass") == WL_IDLE_STATUS && joins == 1 && power == 52);
  mode = WIFI_PS_MIN_MODEM;
  assert(beginStation("ssid", "pass") == WL_IDLE_STATUS && power == 44);
  assert(saveStationTxPower(84));
  assert(beginStation("ssid", "pass") == WL_IDLE_STATUS && power == 84);
  mode = WIFI_PS_NONE;
  assert(beginStation("ssid", "pass") == WL_IDLE_STATUS && power == 84);
  assert(saveStationTxPower(kWifiTxPowerAuto));
  assert(beginStation("ssid", "pass") == WL_IDLE_STATUS && power == 52);
  get_ps_result = ESP_ERR_WIFI_NOT_STARTED;
  assert(beginStation("ssid", "pass") == WL_IDLE_STATUS && phase_failures == 0);
  get_ps_result = 0; nvs_available = false;
  assert(beginStation("ssid", "pass") == WL_IDLE_STATUS && phase_failures == 1);
}
'''.replace('@JOIN@', join)


class W12PowerJoinTest(unittest.TestCase):
    def test_real_join_with_auto_and_manual_power(self):
        compiler = shutil.which('g++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name, content in [('WiFi.h', station.WIFI), ('esp_wifi.h', station.ESP_WIFI),
                                  ('nvs.h', station.NVS), ('test.cpp', HARNESS)]:
                (path / name).write_text(content)
            binary = path / 'test'
            subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-DWIFI_TX_POWER_DBM=13', '-I', str(path), '-I', str(ROOT / 'src'),
                            str(path / 'test.cpp'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
