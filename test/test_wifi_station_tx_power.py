"""Exercise the real station power helper with a fake ESP Wi-Fi driver and NVS."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

WIFI = r'''
#pragma once
#include <stdint.h>
using wifi_ps_type_t = int;
using wifi_power_t = int;
constexpr int WIFI_PS_NONE = 0, WIFI_PS_MIN_MODEM = 1, WIFI_POWER_11dBm = 44, WIFI_POWER_19_5dBm = 78;
'''
ESP_WIFI = r'''
#pragma once
#include <stdint.h>
using esp_err_t = int;
constexpr int ESP_OK = 0, ESP_FAIL = -1;
extern int mode, power, set_ps_result, set_power_result, get_ps_result;
inline int esp_wifi_set_ps(int value) { if (set_ps_result) return set_ps_result; mode = value; return ESP_OK; }
inline int esp_wifi_get_ps(int* value) { if (get_ps_result) return get_ps_result; *value = mode; return ESP_OK; }
inline int esp_wifi_set_max_tx_power(int8_t value) { if (set_power_result) return set_power_result; power = value; return ESP_OK; }
'''
NVS = r'''
#pragma once
#include <stdint.h>
using nvs_handle_t = int;
constexpr int NVS_READONLY = 0, NVS_READWRITE = 1, ESP_ERR_NVS_NOT_FOUND = -10;
extern bool nvs_available, nvs_present, nvs_write, nvs_bad_type, namespace_present, nvs_commit_ok;
extern uint8_t stored;
inline int nvs_open(const char*, int mode, nvs_handle_t* out) {
  if (!nvs_available) return -1;
  if (!namespace_present && mode == NVS_READONLY) return ESP_ERR_NVS_NOT_FOUND;
  namespace_present = true; *out = 1; return 0;
}
inline int nvs_get_u8(nvs_handle_t, const char*, uint8_t* out) {
  if (!nvs_present) return ESP_ERR_NVS_NOT_FOUND;
  if (nvs_bad_type) return -1;
  *out = stored; return 0;
}
inline int nvs_set_u8(nvs_handle_t, const char*, uint8_t value) {
  if (!nvs_write) return -1;
  nvs_present = true; stored = value; return 0;
}
inline int nvs_commit(nvs_handle_t) { return nvs_commit_ok ? 0 : -1; }
inline void nvs_close(nvs_handle_t) {}
'''
HARNESS = r'''
#include <cassert>
int mode = 0, power = -1, set_ps_result = 0, set_power_result = 0, get_ps_result = 0;
bool nvs_available = true, nvs_present = false, nvs_write = true, nvs_bad_type = false;
bool namespace_present = false, nvs_commit_ok = true;
#include <stdint.h>
uint8_t stored = 0;
#include "helpers/esp32/WiFiStationPower.h"
using namespace mesh::wifi;
int main() {
  uint8_t value = 99;
  assert(readStationTxPower(value) && value == kWifiTxPowerAuto);
  assert(!namespace_present); // a read on factory firmware does not create NVS
  assert(applyManualStationTxPower() == ESP_OK && power == -1); // AP auto is unchanged
  const int normal =
#ifdef WIFI_TX_POWER_DBM
      52;
#else
      78;
#endif
  assert(applyStationPowerSave(WIFI_PS_NONE) == ESP_OK && power == normal);
  assert(applyStationPowerSave(WIFI_PS_MIN_MODEM) == ESP_OK && power == 44);
  assert(saveStationTxPower(60));
  assert(applyManualStationTxPower() == ESP_OK && power == 60); // setup AP applies override
  assert(readStationTxPower(value) && value == 60);
  assert(applyStationTxPower() == ESP_OK && power == 60);
  assert(applyStationPowerSave(WIFI_PS_NONE) == ESP_OK && power == 60);
  assert(saveStationTxPower(84)); // Manual power above the W12 default is honored.
  assert(applyStationPowerSave(WIFI_PS_NONE) == ESP_OK && power == 84);
  assert(applyStationTxPower() == ESP_OK && power == 84); // join/reconnect
  assert(saveStationTxPower(60));
  assert(applyRequestedStationTxPower(40) == ESP_OK && power == 60); // ESP-NOW cannot overwrite override
  assert(saveStationTxPower(kWifiTxPowerAuto));
  assert(applyRequestedStationTxPower(40) == ESP_OK && power == 40); // legacy ESP-NOW auto behavior
  setPrimaryEspNowAutomaticTxPower(40);
  assert(applyStationTxPower() == ESP_OK && power == 40); // auto restores primary ESP-NOW preference
  setPrimaryEspNowAutomaticTxPower(-1);
  assert(applyStationPowerSave(WIFI_PS_MIN_MODEM) == ESP_OK && power == 44);
  assert(!saveStationTxPower(7) && !saveStationTxPower(85));
  assert(saveStationTxPower(8) && saveStationTxPower(84));
  nvs_write = false; assert(!saveStationTxPower(44)); nvs_write = true;
  nvs_commit_ok = false; assert(!saveStationTxPower(44)); nvs_commit_ok = true;
  nvs_available = false; assert(!readStationTxPower(value));
  assert(applyStationTxPower() == ESP_FAIL); nvs_available = true;
  nvs_bad_type = true; assert(!readStationTxPower(value)); nvs_bad_type = false;
  stored = 5; assert(!readStationTxPower(value)); stored = 44;
  get_ps_result = -4; assert(applyStationTxPower() == -4); get_ps_result = 0;
  set_power_result = -5; assert(applyStationTxPower() == -5); set_power_result = 0;
  set_ps_result = -6; assert(applyStationPowerSave(WIFI_PS_NONE) == -6);
}
'''


class WiFiStationTxPowerTest(unittest.TestCase):
    def test_persistence_reconnect_and_espnow_precedence(self):
        compiler = shutil.which('g++')
        if not compiler:
            self.skipTest('g++ not installed')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name, text in [('WiFi.h', WIFI), ('esp_wifi.h', ESP_WIFI),
                               ('nvs.h', NVS), ('test.cpp', HARNESS)]:
                (path / name).write_text(text)
            executable = path / 'test'
            for flags in ([], ['-DWIFI_TX_POWER_DBM=13']):
                subprocess.run([compiler, '-std=c++11', '-Wall', '-Wextra', '-Werror',
                                *flags, '-I', str(path), '-I', str(ROOT / 'src'),
                                str(path / 'test.cpp'), '-o', str(executable)], check=True)
                subprocess.run([str(executable)], check=True)


if __name__ == '__main__':
    unittest.main()
