import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class WiFiTxCliTest(unittest.TestCase):
    def test_parse_persistence_and_live_application(self):
        compiler = shutil.which("g++")
        if compiler is None:
            self.skipTest("g++ unavailable")
        with tempfile.TemporaryDirectory() as folder:
            temp = pathlib.Path(folder)
            (temp / "WiFi.h").write_text(r'''
#pragma once
#include <stdint.h>
using wifi_power_t = int8_t;
const wifi_power_t WIFI_POWER_11dBm=44, WIFI_POWER_19_5dBm=78;
''')
            (temp / "nvs.h").write_text(r'''
#pragma once
#include <stdint.h>
#include "esp_wifi.h"
extern bool nvs_ok, exists, fail_write;
extern uint8_t saved;
using nvs_handle_t = int;
constexpr int NVS_READONLY=0, NVS_READWRITE=1, ESP_ERR_NVS_NOT_FOUND=3;
inline int nvs_open(const char*, int mode, nvs_handle_t* handle) {
  if (!nvs_ok) return ESP_FAIL;
  if (!exists && mode == NVS_READONLY) return ESP_ERR_NVS_NOT_FOUND;
  *handle=1; return ESP_OK;
}
inline int nvs_get_u8(nvs_handle_t, const char*, uint8_t* value) {
  if (!exists) return ESP_ERR_NVS_NOT_FOUND;
  *value=saved; return ESP_OK;
}
inline int nvs_set_u8(nvs_handle_t, const char*, uint8_t value) {
  if (fail_write) return ESP_FAIL;
  exists=true; saved=value; return ESP_OK;
}
inline int nvs_commit(nvs_handle_t) { return ESP_OK; }
inline void nvs_close(nvs_handle_t) {}
''')
            (temp / "esp_wifi.h").write_text(r'''
#pragma once
#include <stdint.h>
using esp_err_t=int;
constexpr int ESP_OK=0, ESP_FAIL=-1, ESP_ERR_WIFI_NOT_INIT=1, ESP_ERR_WIFI_NOT_STARTED=2, ESP_ERR_INVALID_STATE=4;
enum wifi_ps_type_t { WIFI_PS_NONE, WIFI_PS_MIN_MODEM, WIFI_PS_MAX_MODEM };
enum wifi_mode_t { WIFI_MODE_NULL, WIFI_MODE_STA };
extern bool started, fail_apply;
extern wifi_ps_type_t ps;
extern int8_t driver_power;
inline int esp_wifi_get_mode(wifi_mode_t* mode) { *mode=started ? WIFI_MODE_STA : WIFI_MODE_NULL; return ESP_OK; }
inline int esp_wifi_get_ps(wifi_ps_type_t* value) { if (!started) return ESP_ERR_WIFI_NOT_STARTED; *value=ps; return ESP_OK; }
inline int esp_wifi_set_ps(wifi_ps_type_t value) { if (!started) return ESP_ERR_WIFI_NOT_STARTED; ps=value; return ESP_OK; }
inline int esp_wifi_get_max_tx_power(int8_t* value) { if (!started) return ESP_ERR_WIFI_NOT_STARTED; *value=driver_power; return ESP_OK; }
inline int esp_wifi_set_max_tx_power(int8_t value) { if (!started) return ESP_ERR_WIFI_NOT_STARTED; if (fail_apply) return ESP_FAIL; driver_power=value; return ESP_OK; }
''')
            (temp / "check.cpp").write_text(r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
bool nvs_ok=true, exists=false, fail_write=false, started=false, fail_apply=false;
#include <stdint.h>
uint8_t saved=0;
#include "esp_wifi.h"
wifi_ps_type_t ps=WIFI_PS_NONE;
int8_t driver_power=78;
#include "src/helpers/esp32/WiFiTxPowerCLI.h"
int main() {
  using namespace mesh::wifi;
  uint8_t value=255;
  assert(parseTxPowerValue("auto",value) && value==0);
  assert(parseTxPowerValue(" 2.25 \t",value) && value==9);
  assert(parseTxPowerValue("8.5",value) && value==34);
  assert(parseTxPowerValue("21.00",value) && value==84);
  for (const char* bad : {"", "-2", "1.75", "21.25", "8.2", "8.500", "8foo", "autofoo"})
    assert(!parseTxPowerValue(bad,value));
  char reply[160];
  formatTxPowerStatus(reply,sizeof(reply)); assert(strstr(reply,"auto"));
  handleTxPowerSet("11.25",reply,sizeof(reply));
  assert(strstr(reply,"applies when WiFi starts") && saved==45);
  assert(readStationTxPower(value) && value==45);
  started=true;
  assert(applyStationPowerSave(WIFI_PS_MIN_MODEM)==ESP_OK && driver_power==45);
  handleTxPowerSet("18.5",reply,sizeof(reply));
  assert(saved==74 && driver_power==74 && strstr(reply,"driver limit 18.50"));
  assert(applyStationPowerSave(WIFI_PS_NONE)==ESP_OK && driver_power==74);
  handleTxPowerSet("auto",reply,sizeof(reply));
  assert(saved==0 && driver_power==78 && strstr(reply,"auto"));
  assert(applyStationPowerSave(WIFI_PS_MIN_MODEM)==ESP_OK && driver_power==44);
  setPrimaryEspNowAutomaticTxPower(60);
  handleTxPowerSet("16",reply,sizeof(reply));
  assert(saved==64 && driver_power==64);
  handleTxPowerSet("auto",reply,sizeof(reply));
  assert(saved==0 && driver_power==60);
  setPrimaryEspNowAutomaticTxPower(-1);
  assert(applyStationPowerSave(WIFI_PS_MIN_MODEM)==ESP_OK && driver_power==44);
  fail_write=true;
  handleTxPowerSet("14",reply,sizeof(reply));
  assert(strstr(reply,"save failed") && saved==0 && driver_power==44);
  fail_write=false; fail_apply=true;
  handleTxPowerSet("14",reply,sizeof(reply));
  assert(saved==56 && strstr(reply,"apply failed"));
  nvs_ok=false;
  formatTxPowerStatus(reply,sizeof(reply)); assert(strstr(reply,"cannot read"));
}
''')
            executable = temp / "check"
            subprocess.run([compiler, "-std=c++11", "-I", str(temp), "-I", str(ROOT),
                            str(temp / "check.cpp"), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
