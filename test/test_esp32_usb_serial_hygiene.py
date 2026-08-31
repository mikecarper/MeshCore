#!/usr/bin/env python3

import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text()


class Esp32UsbSerialHygieneTest(unittest.TestCase):
    def test_operational_wifi_diagnostics_use_runtime_logging_port(self):
        for relative in (
            "src/helpers/ESP32Board.cpp",
            "src/helpers/CompanionMqttSetupPortal.cpp",
            "src/helpers/JWTHelper.cpp",
            "src/helpers/bridges/MQTTBridge.cpp",
            "src/helpers/esp32/WiFiOtaSeeder.cpp",
            "src/helpers/esp32/WebConfigServer.cpp",
            "src/helpers/WiFiSetupPortal.cpp",
        ):
            text = source(relative)
            self.assertIn("UsbLogging.h", text, relative)
            self.assertNotRegex(
                text,
                r"\bSerial\.(?:print|println|printf|write)\s*\(",
                relative,
            )
            self.assertIn("mesh::usbLoggingPort()", text, relative)

    def test_v4_companion_uses_usb_serial_jtag_mode(self):
        platformio = source("variants/heltec_v4/platformio.ini")
        base = platformio[
            platformio.index("[Heltec_lora32_v4]") :
            platformio.index("[heltec_v4_oled]")
        ]
        self.assertIn("-D ARDUINO_USB_MODE=1", base)
        self.assertNotIn("ARDUINO_USB_MODE=0", base)

        board = source("boards/heltec_v4.json")
        self.assertIn('"-DARDUINO_USB_CDC_ON_BOOT=1"', board)
        self.assertIn('"-DARDUINO_USB_MODE=1"', board)

    def test_mqtt_ntp_detail_never_bypasses_logging_mode(self):
        text = source("src/helpers/bridges/MQTTBridge.cpp")
        self.assertIn(
            "if (verbose && mesh::isUsbLoggingEnabled())", text
        )
        self.assertIn("Stream& output = mesh::usbLoggingPort();", text)

    def test_framework_diagnostics_follow_same_runtime_gate(self):
        text = source("src/helpers/UsbLogging.cpp")
        self.assertIn("Serial.setDebugOutput(enabled);", text)

        setter = text[
            text.index("void setUsbLoggingEnabled(") :
            text.index("bool saveUsbLoggingBootPreference(")
        ]
        self.assertIn("setPlatformDebugOutputEnabled(enabled);", setter)

        begin = text[
            text.index("void beginUsbLoggingPort(") :
            text.index("void serviceUsbLoggingPort(")
        ]
        self.assertIn(
            "setPlatformDebugOutputEnabled(isUsbLoggingEnabled());", begin
        )

    def test_expected_fresh_nvs_state_is_silent(self):
        wifi_setup = source("src/helpers/WiFiSetupPortal.cpp")
        webconfig = source("src/helpers/esp32/WebConfigServer.cpp")
        radio_policy = source("src/helpers/esp32/WiFiRadioPolicy.h")
        mqtt_setup = source("src/helpers/CompanionMqttSetupPortal.cpp")

        for text in (wifi_setup, webconfig, radio_policy, mqtt_setup):
            self.assertNotRegex(
                text,
                r'\.begin\("mesh-(?:wifi|webui|mqtt)",\s*true\)',
            )
        self.assertNotIn("nvs.begin(NVS_NAMESPACE, true)", mqtt_setup)

        for text in (wifi_setup, webconfig):
            self.assertIn('isKey("ssid")', text)
            self.assertIn('isKey("password")', text)
        self.assertIn('isKey("enabled")', webconfig)
        self.assertIn('isKey("cli")', webconfig)
        self.assertIn('isKey("powersave")', webconfig)
        self.assertIn('isKey("espnow_ch")', radio_policy)
        self.assertIn("nvs.isKey(NVS_VERSION_KEY)", mqtt_setup)
        self.assertIn("nvs.isKey(NVS_PREFS_KEY)", mqtt_setup)

    def test_indicator_reports_specific_hardware(self):
        header = source("variants/sensecap_indicator-espnow/target.h")
        implementation = source("variants/sensecap_indicator-espnow/target.cpp")
        self.assertIn(
            "class SenseCapIndicatorBoard : public ESP32Board", header
        )
        self.assertIn('return "Seeed SenseCAP Indicator";', header)
        self.assertIn("extern SenseCapIndicatorBoard board;", header)
        self.assertIn("SenseCapIndicatorBoard board;", implementation)


if __name__ == "__main__":
    unittest.main()
