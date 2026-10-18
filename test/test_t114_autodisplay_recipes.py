#!/usr/bin/env python3
"""Resolve T114 recipe contracts without PlatformIO or firmware builds."""

import shlex
import unittest

from test_upstream_observer_recipes import Recipes


ALIASES = {
    "Heltec_t114_without_display_repeater": "Heltec_t114_repeater",
    "Heltec_t114_without_display_repeater_bridge_rs232": "Heltec_t114_repeater_bridge_rs232",
    "Heltec_t114_without_display_room_server": "Heltec_t114_room_server",
    "Heltec_t114_without_display_companion_radio_ble": "Heltec_t114_companion_radio_ble",
    "Heltec_t114_without_display_companion_radio_usb": "Heltec_t114_companion_radio_usb",
}
ROLES = {
    "Heltec_t114_repeater": "simple_repeater",
    "Heltec_t114_repeater_bridge_rs232": "simple_repeater",
    "Heltec_t114_repeater_lora_ota_no_external_sensors": "simple_repeater",
    "Heltec_t114_room_server": "simple_room_server",
    "Heltec_t114_sensor": "simple_sensor",
    "Heltec_t114_companion_radio_ble": "companion_radio",
    "Heltec_t114_companion_radio_usb": "companion_radio",
}


def defines(flags):
    """Apply ordered compiler -D/-U options, including inherited overrides."""
    lexer = shlex.shlex(flags, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ";"
    words = iter(lexer)
    result = {}
    for word in words:
        if word in ("-D", "-U"):
            word += next(words)
        if word.startswith("-D"):
            name, _, value = word[2:].partition("=")
            result[name] = value or "1"
        elif word.startswith("-U"):
            result.pop(word[2:], None)
    return result


class T114AutoDisplayRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes = Recipes()

    def flags(self, target):
        return defines(self.recipes.value("env:" + target, "build_flags"))

    def test_every_ui_role_detects_the_same_optional_display(self):
        for target, role in ROLES.items():
            section = "env:" + target
            with self.subTest(target=target):
                flags = self.flags(target)
                for macro in ("HELTEC_T114", "HELTEC_T114_WITH_DISPLAY", "ST7789",
                              "T114_DISPLAY_AUTODETECT"):
                    self.assertEqual(flags[macro], "1")
                self.assertEqual(flags["DISPLAY_CLASS"], "ST7789Display")
                for source in ("ST7789Display", "MomentaryButton", "OLEDDisplay", "OLEDDisplayFonts"):
                    self.assertTrue(self.recipes.includes(section, "helpers/ui/" + source + ".cpp"))
                self.assertFalse(self.recipes.includes(section, "helpers/ui/NullDisplayDriver.cpp"))
                self.assertIn("Adafruit SSD1306", self.recipes.value(section, "lib_deps"))
                for other in set(ROLES.values()):
                    self.assertEqual(self.recipes.includes(section, "../examples/" + other + "/main.cpp"),
                                     other == role)

    def test_legacy_names_are_exact_canonical_aliases(self):
        # This also keeps the legacy and canonical USB bases identical for the
        # build script's derived Full Companion overlay.
        options = ("build_flags", "build_unflags", "build_src_filter", "lib_deps", "board",
                   "board_build.ldscript", "board_upload.maximum_size", "extra_scripts",
                   "debug_tool", "upload_protocol")
        for legacy, canonical in ALIASES.items():
            old, new = "env:" + legacy, "env:" + canonical
            with self.subTest(legacy=legacy):
                self.assertEqual(dict(self.recipes.config.items(old)), {"extends": new})
                for option in options:
                    self.assertEqual(self.recipes.value(old, option), self.recipes.value(new, option), option)

    def test_hardware_identity_pins_and_storage_are_preserved(self):
        wiring = {
            "P_LORA_DIO_1": "20", "P_LORA_NSS": "24", "P_LORA_RESET": "25",
            "P_LORA_BUSY": "17", "P_LORA_SCLK": "19", "P_LORA_MISO": "23",
            "P_LORA_MOSI": "22", "SX126X_POWER_EN": "37", "PIN_GPS_RX": "39",
            "PIN_GPS_TX": "37", "PIN_GPS_EN": "21", "PIN_GPS_RESET": "38",
        }
        for target in (*ROLES, *ALIASES, "Heltec_t114_kiss_modem"):
            section = "env:" + target
            with self.subTest(target=target):
                flags = self.flags(target)
                self.assertEqual(flags["MOTA_HW_ID"], '"Heltec_t114"')
                self.assertEqual(flags["NRF52_POWER_MANAGEMENT"], "1")
                self.assertEqual(self.recipes.value(section, "board"), "heltec_t114")
                for macro, value in wiring.items():
                    self.assertEqual(flags[macro], value, macro)
                companion = "companion_radio" in target
                linker = "boards/nrf52840_s140_v6" + ("_extrafs" if companion else "") + ".ld"
                self.assertEqual(self.recipes.value(section, "board_build.ldscript"), linker)
                if companion:
                    self.assertEqual(self.recipes.value(section, "board_upload.maximum_size"), "712704")
                    self.assertEqual(flags["MAX_CONTACTS"], "350")
                    self.assertEqual(flags["MAX_GROUP_CHANNELS"], "40")

    def test_gps_and_in_place_ota_policy_remain_explicit(self):
        for target in ROLES:
            with self.subTest(target=target):
                self.assertEqual(self.flags(target)["ENV_INCLUDE_GPS"], "1")
        for target in ("Heltec_t114_repeater", "Heltec_t114_repeater_bridge_rs232"):
            with self.subTest(target=target):
                flags = self.flags(target)
                self.assertNotIn("ENABLE_OTA", flags)
                self.assertEqual(flags["DISABLE_LORA_OTA"], "1")
        flags = self.flags("Heltec_t114_repeater_lora_ota_no_external_sensors")
        self.assertEqual(flags["ENABLE_OTA"], "1")
        self.assertEqual(flags["OTA_FLASH_STORE"], "1")
        self.assertNotIn("DISABLE_LORA_OTA", flags)
        for sensor in ("AHTX0", "BME280", "BMP280", "SHTC3", "SHT4X", "LPS22HB", "MLX90614",
                       "VL53L0X", "BME680", "BMP085", "RAK12035", "INA3221", "INA219", "INA226", "INA260"):
            self.assertNotIn("ENV_INCLUDE_" + sensor, flags)

    def test_transport_roles_and_headless_kiss_stay_distinct(self):
        ble = self.flags("Heltec_t114_companion_radio_ble")
        usb = self.flags("Heltec_t114_companion_radio_usb")
        self.assertEqual(ble["BLE_PIN_CODE"], "123456")
        self.assertEqual(ble["OFFLINE_QUEUE_SIZE"], "256")
        self.assertNotIn("ENABLE_USB_INTERFACE", ble)
        self.assertEqual(usb["ENABLE_USB_INTERFACE"], "1")
        self.assertNotIn("BLE_PIN_CODE", usb)
        for target in ("Heltec_t114_repeater", "Heltec_t114_repeater_lora_ota_no_external_sensors"):
            self.assertEqual(self.flags(target)["RS232_BRIDGE_MERGED"], "1")
        self.assertNotIn("RS232_BRIDGE_MERGED", self.flags("Heltec_t114_repeater_bridge_rs232"))
        flags = self.flags("Heltec_t114_kiss_modem")
        self.assertNotIn("DISPLAY_CLASS", flags)
        self.assertNotIn("T114_DISPLAY_AUTODETECT", flags)
        self.assertFalse(self.recipes.includes("env:Heltec_t114_kiss_modem", "helpers/ui/ST7789Display.cpp"))
        self.assertTrue(self.recipes.includes("env:Heltec_t114_kiss_modem", "../examples/kiss_modem/main.cpp"))


if __name__ == "__main__":
    unittest.main()
