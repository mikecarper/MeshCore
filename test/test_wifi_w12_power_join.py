"""Exercise W12 station joins with the real CLI power preference and policy."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_station_tx_power as station
import test_mqtt_ap_ownership as ownership

ROOT = Path(__file__).resolve().parents[1]
join = extract_braced((ROOT / 'src/helpers/esp32/WiFiStationPolicy.h').read_text(),
                      'inline wl_status_t beginStation(')

HARNESS = r'''
#include <cassert>
#include <cstdint>
int mode=0, power=-1, set_ps_result=0, set_power_result=0, get_ps_result=0;
bool nvs_available=true, nvs_present=false, nvs_write=true, nvs_bad_type=false;
bool namespace_present=false, nvs_commit_ok=true;
bool driver_ignores_write=false, readback_fails=false;
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
    def test_production_profile_preserves_authentication_window_before_retry(self):
        profile = (ROOT / 'variants/meshnology_w12/platformio.ini').read_text()
        base = profile.split('[meshnology_w12]', 1)[1].split('\n[', 1)[0]
        definitions = dict(re.findall(r'-D\s+(MESH_WIFI_RECOVERY|WEBCONFIG_WIFI_DEFAULT_CONNECT_TIMEOUT_MS)=(\d+)\b', base))
        self.assertEqual(definitions.get('MESH_WIFI_RECOVERY'), '1')
        self.assertEqual(definitions.get('WEBCONFIG_WIFI_DEFAULT_CONNECT_TIMEOUT_MS'), '60000')
        flags = [f'-D{name}={value}' for name, value in definitions.items()]

        # Execute the actual observer startup/reconnect methods with the flags
        # inherited by production W12 targets, including setup/OTA ownership.
        fixture = ownership.MQTTAccessPointOwnershipTest()
        source = fixture.fixture().replace(
            '#include "helpers/MQTTConnectionPolicy.h"',
            '#include "helpers/MQTTConnectionPolicy.h"\n'
            '#include "helpers/WebConfigBatch.h"\n'
            'static_assert(WebConfigBatch::kInitialWiFiConnectTimeoutMs == 60000U, "initial window");\n'
            'static_assert(WebConfigBatch::kSetupWiFiConnectTimeoutMs == 60000U, "setup window");')
        fixture.run_fixture('recovery_window', source, *flags)

        # The former W12 profile ran this ordinary MQTT retry policy. Keep the
        # same live-association assertions to show its first retry cuts in early.
        ordinary = source.replace(
            '#if defined(MESH_WIFI_RECOVERY) && MESH_WIFI_RECOVERY\n  const uint32_t start=1000000U;',
            '#if 1\n  const uint32_t start=1000000U;')
        self.assertNotEqual(ordinary, source)
        with self.assertRaises(ownership.FixtureFailure):
            fixture.run_fixture('recovery_window', ordinary,
                                '-DWEBCONFIG_WIFI_DEFAULT_CONNECT_TIMEOUT_MS=60000')

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
