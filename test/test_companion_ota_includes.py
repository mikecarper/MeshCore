#!/usr/bin/env python3
"""Compile the production Companion include block without transitive OTA headers."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "examples/companion_radio/main.cpp"


class CompanionOtaIncludeTest(unittest.TestCase):
    def compile_include_block(self, flags, usb, ble):
        # Preserve the actual production includes and their feature guards.
        # Unlike handler-extraction tests, this harness does not inject either
        # OTA header. MyMesh intentionally lacks its optional queue-dependent
        # OtaContext include, just like the Heltec V4 Full configuration.
        prefix = MAIN.read_text().split(
            "// Believe it or not, this std C function is busted on some platforms!",
            1,
        )[0]
        self.assertIn('#include "MyMesh.h"', prefix)
        with tempfile.TemporaryDirectory(prefix="meshcore-companion-includes-") as temp:
            directory = Path(temp)
            for header in (
                "helpers/RegulatorSettings.h",
                "helpers/ui/StartupScreen.h", "helpers/ui/DisplayPowerSettings.h",
                "helpers/UsbLoggingWatchdog.h", "helpers/UsbLoggingClientActivity.h",
                "Mesh.h", "helpers/BluetoothMac.h", "CompanionBluetooth.h",
                "CompanionWireless.h", "CompanionWiFi.h", "esp_bt.h", "esp_pm.h",
                "esp_sleep.h", "esp_system.h", "esp_heap_caps.h",
            ):
                path = directory / header
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("#pragma once\n")
            (directory / "MyMesh.h").write_text(
                '#pragma once\n#include "CompanionFeatures.h"\n'
            )
            if not (usb or ble):
                # Prove that small/non-OTA images do not gain these dependencies.
                for header in ("MotaSourceSerial.h", "OtaContext.h"):
                    path = directory / "helpers/ota" / header
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('#error "OTA headers must remain feature guarded"\n')
            checks = (
                "static_assert(COMPANION_FEATURE_USB_MOTA_SOURCE == %d, \"USB profile\");\n"
                "static_assert(COMPANION_FEATURE_BLE_MOTA_SOURCE == %d, \"BLE profile\");\n"
            ) % (usb, ble)
            if usb or ble:
                checks += (
                    "static_assert(sizeof(mesh::ota::OtaContext) != 0, \"context declared\");\n"
                    "static_assert(mesh::ota::SerialMotaSource::RESPONSE_BYTE == -2, "
                    "\"source declared\");\n"
                )
            if usb:
                checks += (
                    "mesh::ota::SerialMotaSource& source_under_test() {\n"
                    "  return mesh::ota::OtaContext::serialFolderSource();\n}\n"
                )
            source = directory / "includes.cpp"
            source.write_text(
                "class Stream;\nStream& include_test_serial_port();\n"
                "#define OTA_FOLDER_SERIAL_STREAM include_test_serial_port()\n"
                + prefix + checks
            )
            result = subprocess.run([
                "c++", "-std=c++17", "-fsyntax-only", *flags,
                "-I", temp, "-I", str(ROOT / "src"),
                "-I", str(ROOT / "test/mocks"),
                "-I", str(ROOT / "examples/companion_radio"), str(source),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_usb_only_full_esp32_declares_actual_ota_types(self):
        self.compile_include_block([
            "-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1", "-DBLE_PIN_CODE=123456",
            "-DWIFI_SSID=\"\"", "-DENABLE_USB_INTERFACE=1", "-DENABLE_OTA=1",
            "-DOTA_FOLDER_SERIAL=1", "-DOTA_SEEDER_ONLY=1",
        ], usb=1, ble=0)

    def test_ble_only_full_nrf52_declares_actual_ota_types(self):
        self.compile_include_block([
            "-DNRF52_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1", "-DBLE_PIN_CODE=123456",
            "-DENABLE_OTA=1", "-DOTA_SEEDER_ONLY=1",
            "-DCOMPANION_FEATURE_BLE_MOTA_SOURCE=1",
        ], usb=0, ble=1)

    def test_non_ota_image_keeps_ota_headers_out(self):
        self.compile_include_block(["-DSTM32_PLATFORM=1"], usb=0, ble=0)


if __name__ == "__main__":
    unittest.main()
