#!/usr/bin/env python3
"""Compile the real manual-sleep driver guard for WiFi/BT SDK configurations."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

WIFI = r'''
#pragma once
using esp_err_t = int;
constexpr int ESP_OK = 0, ESP_ERR_WIFI_NOT_INIT = 1, ESP_ERR_WIFI_NOT_STARTED = 2;
enum wifi_mode_t { WIFI_MODE_NULL, WIFI_MODE_STA, WIFI_MODE_AP, WIFI_MODE_APSTA };
static wifi_mode_t mode = WIFI_MODE_NULL;
static int wifi_error = ESP_OK;
inline int esp_wifi_get_mode(wifi_mode_t* out) { *out = mode; return wifi_error; }
'''
CONTROLLER = r'''
#pragma once
enum esp_bt_controller_status_t { ESP_BT_CONTROLLER_STATUS_IDLE,
  ESP_BT_CONTROLLER_STATUS_INITED, ESP_BT_CONTROLLER_STATUS_ENABLED };
static auto controller = ESP_BT_CONTROLLER_STATUS_IDLE;
inline esp_bt_controller_status_t esp_bt_controller_get_status() { return controller; }
'''
HOST = r'''
#pragma once
enum esp_bluedroid_status_t { ESP_BLUEDROID_STATUS_UNINITIALIZED,
  ESP_BLUEDROID_STATUS_INITIALIZED, ESP_BLUEDROID_STATUS_ENABLED };
static auto host = ESP_BLUEDROID_STATUS_UNINITIALIZED;
inline esp_bluedroid_status_t esp_bluedroid_get_status() { return host; }
'''
HARNESS = r'''
#include <helpers/esp32/LightSleepPolicy.h>
#include <cassert>
using mesh::powerdiag::Reason;
using mesh::esp32::manualLightSleepBlocker;
using mesh::esp32::manualLightSleepReady;
int main() {
  assert(manualLightSleepReady());
  for (auto value : {WIFI_MODE_STA, WIFI_MODE_AP, WIFI_MODE_APSTA}) {
    mode = value;
    assert(manualLightSleepBlocker() == Reason::Network);
  }
  mode = static_cast<wifi_mode_t>(99);
  assert(manualLightSleepBlocker() == Reason::Network);
  mode = WIFI_MODE_NULL;
  for (int error : {ESP_ERR_WIFI_NOT_INIT, ESP_ERR_WIFI_NOT_STARTED}) {
    wifi_error = error;
    assert(manualLightSleepReady());
  }
  wifi_error = -1;
  assert(manualLightSleepBlocker() == Reason::Network);
  wifi_error = ESP_OK;
#if defined(CONFIG_BT_ENABLED) && CONFIG_BT_ENABLED
  controller = ESP_BT_CONTROLLER_STATUS_INITED;
  assert(manualLightSleepReady());
  controller = ESP_BT_CONTROLLER_STATUS_ENABLED;
  assert(manualLightSleepBlocker() == Reason::Wireless);
  controller = static_cast<esp_bt_controller_status_t>(99);
  assert(manualLightSleepBlocker() == Reason::Wireless);
  controller = ESP_BT_CONTROLLER_STATUS_IDLE;
#if defined(CONFIG_BLUEDROID_ENABLED) && CONFIG_BLUEDROID_ENABLED \
    && !defined(MESH_USE_NIMBLE_ARDUINO)
  host = ESP_BLUEDROID_STATUS_INITIALIZED;
  assert(manualLightSleepReady());
  host = ESP_BLUEDROID_STATUS_ENABLED;
  assert(manualLightSleepBlocker() == Reason::Wireless);
  host = static_cast<esp_bluedroid_status_t>(99);
  assert(manualLightSleepBlocker() == Reason::Wireless);
  host = ESP_BLUEDROID_STATUS_UNINITIALIZED;
#endif
#endif
  assert(manualLightSleepReady());
}
'''


SDK_MODES = (
            (), ("CONFIG_BT_ENABLED=0", "CONFIG_IDF_TARGET_ESP32S2=1"),
            ("CONFIG_BT_ENABLED=1", "CONFIG_BLUEDROID_ENABLED=1", "CONFIG_IDF_TARGET_ESP32=1"),
            ("CONFIG_BT_ENABLED=1", "CONFIG_BLUEDROID_ENABLED=1", "CONFIG_IDF_TARGET_ESP32S3=1"),
            ("CONFIG_BT_ENABLED=1", "CONFIG_BLUEDROID_ENABLED=0", "CONFIG_IDF_TARGET_ESP32C3=1"),
            ("CONFIG_BT_ENABLED=1", "CONFIG_BLUEDROID_ENABLED=1", "MESH_USE_NIMBLE_ARDUINO=1", "CONFIG_IDF_TARGET_ESP32C6=1"),
        )


class LightSleepPolicyTest(unittest.TestCase):
    def test_usb_pm_lock_only_releases_during_bounded_unplugged_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "Arduino.h").write_text("#pragma once\n#include <cstdint>\n")
            (work / "esp_pm.h").write_text(r"""
#pragma once
#include <cassert>
using esp_pm_lock_handle_t = int*;
constexpr int ESP_OK = 0, ESP_PM_NO_LIGHT_SLEEP = 2;
static int holds = 0, creates = 0, deletes = 0, fail = 1, during_delay = -1;
#define pdMS_TO_TICKS(ms) (ms)
#define ESP_ERROR_CHECK(error) assert((error) == ESP_OK)
int esp_pm_lock_create(int kind, int, const char*, int** handle) {
  assert(kind == ESP_PM_NO_LIGHT_SLEEP); ++creates;
  if (fail == 1) return -1;
  *handle = &holds; return ESP_OK;
}
int esp_pm_lock_delete(int* handle) { assert(handle == &holds); ++deletes; return ESP_OK; }
int esp_pm_lock_acquire(int* handle) {
  assert(handle == &holds && holds == 0);
  if (fail == 2) return -1;
  ++holds; return ESP_OK;
}
int esp_pm_lock_release(int* handle) {
  assert(handle == &holds && holds == 1);
  if (fail == 3) return -1;
  --holds; return ESP_OK;
}
void vTaskDelay(unsigned ms) { assert(ms == 10); during_delay = holds; }
""")
            source = work / "usb.cpp"
            source.write_text(r"""
#include <helpers/esp32/UsbAutoLightSleep.h>
int main() {
  mesh::esp32::UsbAutoLightSleep usb;
  assert(!usb.prepare() && !usb.ready());
  fail = 2;
  assert(!usb.prepare() && !usb.ready() && deletes == 1);
  fail = 0;
  assert(usb.prepare() && usb.prepare() && creates == 3 && holds == 1);
  usb.idle(200, true); assert(during_delay == 1 && holds == 1);
  usb.idle(10, false); assert(during_delay == 0 && holds == 1);
  fail = 3;
  usb.idle(10, false); assert(during_delay == 1 && holds == 1);
}
""")
            binary = work / "usb"
            subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-I", str(work),
                            "-I", str(ROOT / "src"), str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_real_guard_across_sdk_and_host_modes(self):
        for flags in SDK_MODES:
            with self.subTest(flags=flags):
                self.run_guard((*flags, 'WIFI_SSID=""'))

    def test_every_wifi_owner_keeps_physical_driver_fail_closed(self):
        for owner in ("WIFI_SSID", "ENABLE_WIFI_INTERFACE", "WITH_MQTT_BRIDGE",
                      "WITH_ESPNOW_BRIDGE", "MESH_PRIMARY_ESPNOW", "WIFI_OTA_SEEDER",
                      "ADMIN_PASSWORD", "COMPANION_RADIO_FULL"):
            with self.subTest(owner=owner):
                self.run_guard((owner, "CONFIG_BT_ENABLED=1",
                                "CONFIG_BLUEDROID_ENABLED=1"))

    def test_ble_only_links_without_wifi_sdk_or_headers(self):
        # No WiFi SDK header or function definition exists. An unconditional
        # check fails compilation/linking instead of silently passing a fake.
        # ENABLE_OTA is deliberately present: this role still has LoRa OTA.
        source = "#include <initializer_list>\n" + HARNESS
        start = source.index("  for (auto value : {WIFI_MODE_STA")
        end = source.index("#if defined(CONFIG_BT_ENABLED)", start)
        source = source[:start] + source[end:]
        for flags in SDK_MODES:
            with self.subTest(flags=flags):
                self.run_guard((*flags, "BLE_PIN_CODE=123456", "ENABLE_OTA=1",
                                "MESH_PRIMARY_ESPNOW=0"), source, wifi=False)

    def run_guard(self, flags, source=None, wifi=True):
        with tempfile.TemporaryDirectory(prefix="mesh-esp32-sleep-driver-") as directory:
            root = Path(directory)
            (root / "sdkconfig.h").write_text("#pragma once\n")
            (root / "esp_wifi.h").write_text(WIFI if wifi else '#error "WiFi SDK is absent"\n')
            bt = "CONFIG_BT_ENABLED=1" in flags
            bluedroid = bt and "CONFIG_BLUEDROID_ENABLED=1" in flags and "MESH_USE_NIMBLE_ARDUINO=1" not in flags
            (root / "esp_bt.h").write_text(CONTROLLER if bt else '#error "Bluetooth absent on this target"\n')
            (root / "esp_bt_main.h").write_text(HOST if bluedroid else '#error "Bluedroid absent from this host build"\n')
            cpp, exe = root / "guard.cpp", root / "guard"
            cpp.write_text(source or "#include <initializer_list>\n" + HARNESS)
            result = subprocess.run([
                os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                *("-D" + flag for flag in flags), "-I", str(root), "-I", str(ROOT / "src"),
                str(cpp), "-o", str(exe)], text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            if not wifi:
                symbols = subprocess.run(["nm", "-C", str(exe)], text=True,
                                         capture_output=True, timeout=10)
                self.assertEqual(symbols.returncode, 0, symbols.stderr)
                self.assertNotIn("esp_wifi_", symbols.stdout)


if __name__ == "__main__":
    unittest.main()
