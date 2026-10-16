#!/usr/bin/env python3
"""Keep upstream dynamic-WiFi flags on the Companion's existing boot policy."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "examples/companion_radio"


class DynamicWiFiBootContractTest(unittest.TestCase):
    def test_real_include_order_normalizes_before_feature_selection(self):
        mesh = (COMPANION / "MyMesh.h").read_text()
        abstract = (COMPANION / "AbstractUITask.h").read_text()
        self.assertLess(mesh.index('#include "AbstractUITask.h"'),
                        mesh.index('#include "CompanionFeatures.h"'))
        self.assertIn('#include "NodePrefs.h"', abstract)
        # Execute the actual normalization directives, omitting only the SDK
        # types after the first include, then include the real feature header.
        prefs = (COMPANION / "NodePrefs.h").read_text()
        normalization = prefs[:prefs.index("#include <Arduino.h>")].replace("#pragma once", "")
        implementation = (COMPANION / "MyMesh.cpp").read_text()
        end = implementation.index("  _prefs.wifi_enabled = 1;")
        start = implementation.rfind("#if", 0, end)
        end = implementation.index("#endif", end) + len("#endif")
        wifi_defaults = implementation[start:end]
        bluetooth_default = "  _prefs.bluetooth_enabled = 1;"
        self.assertIn(bluetooth_default, implementation)
        cases = (
            # defines, terminal feature, WiFi macro, default WiFi, default SSID
            (("ESP32_PLATFORM", "ENABLE_WIFI_INTERFACE", "BLE_PIN_CODE=123456"), 1, 1, 0, ""),
            (("ESP32_PLATFORM", "ENABLE_WIFI_INTERFACE", "BLE_PIN_CODE=123456", "ENABLE_USB_INTERFACE"), 1, 1, 0, ""),
            (("ESP32_PLATFORM", "ENABLE_WIFI_INTERFACE"), 1, 1, 1, ""),
            (("ESP32_PLATFORM", "BLE_PIN_CODE=123456"), 0, 0, 0, ""),
            (("ESP32_PLATFORM", "ENABLE_WIFI_INTERFACE", "BLE_PIN_CODE=123456", 'WIFI_SSID="saved-build-ssid"'), 1, 1, 0, "saved-build-ssid"),
            (("ESP32_PLATFORM", "ENABLE_WIFI_INTERFACE", "BLE_PIN_CODE=123456", "WEBCONFIG_DISABLED"), 0, 1, 0, ""),
            (("NRF52_PLATFORM", "BLE_PIN_CODE=123456"), 0, 0, 0, ""),
        )
        with tempfile.TemporaryDirectory(prefix="dynamic-wifi-boot-") as directory:
            for index, (defines, terminal, has_wifi, wifi_on, ssid) in enumerate(cases):
                with self.subTest(defines=defines):
                    program = normalization + r'''
#include "examples/companion_radio/CompanionFeatures.h"
#include <cassert>
#include <cstring>
static_assert(COMPANION_FEATURE_TEXT_TERMINAL == @TERMINAL@,
              "dynamic WiFi must expose WebConfig's text terminal");
#if @HAS_WIFI@
#ifndef WIFI_SSID
#error Dynamic WiFi must enter the existing WIFI_SSID boot routing
#endif
static_assert(sizeof(WIFI_SSID) == sizeof("@SSID@"), "keep credentials unchanged");
#else
#ifdef WIFI_SSID
#error Do not invent WiFi support on an ordinary BLE-only target
#endif
#endif
#ifdef WIFI_PWD
#error Enabling the interface must not inject WiFi credentials
#endif
int main() {
  struct { int wifi_enabled = -1, bluetooth_enabled = -1; } _prefs;
@WIFI_DEFAULTS@
@BLUETOOTH_DEFAULT@
  assert(_prefs.wifi_enabled == @WIFI_ON@);
  assert(_prefs.bluetooth_enabled == 1);
#if @HAS_WIFI@
  assert(strcmp(WIFI_SSID, "@SSID@") == 0);
#endif
}
'''
                    for key, value in {
                        "TERMINAL": terminal, "HAS_WIFI": has_wifi,
                        "SSID": ssid, "WIFI_ON": wifi_on,
                        "WIFI_DEFAULTS": wifi_defaults,
                        "BLUETOOTH_DEFAULT": bluetooth_default,
                    }.items():
                        program = program.replace("@" + key + "@", str(value))
                    binary = Path(directory) / str(index)
                    built = subprocess.run([
                        "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-I", str(ROOT), "-I", str(ROOT / "src"),
                        *["-D" + define for define in defines],
                        "-x", "c++", "-", "-o", str(binary),
                    ], input=program, text=True, capture_output=True, timeout=30)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True,
                                         text=True, timeout=10)
                    self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
