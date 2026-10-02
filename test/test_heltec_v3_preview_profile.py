#!/usr/bin/env python3
"""Keep the opt-in V3 preview Full, capacity-preserving, and reproducible."""

import configparser
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HeltecV3PreviewProfileTest(unittest.TestCase):
    def setUp(self):
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read(ROOT / "platformio.heltec-v3-preview3.ini")
        self.env = self.config["env:Heltec_v3_companion_radio_full_preview3"]

    def test_full_features_and_existing_capacities(self):
        flags = self.env["build_flags"]
        for name in (
                "COMPANION_RADIO_FULL", "ENABLE_USB_INTERFACE", "ENABLE_OTA",
                "OTA_SEEDER_ONLY", "WIFI_OTA_SEEDER",
                "COMPANION_FEATURE_TEMP_RADIO", "COMPANION_FEATURE_OTA_CLI",
                "COMPANION_FEATURE_USB_MOTA_SOURCE",
                "COMPANION_FEATURE_NETWORK_TERMINAL",
                "COMPANION_FEATURE_MEMORY_DIAGNOSTICS"):
            self.assertIn("-D " + name + "=1", flags)
        self.assertIn("-D BLE_PIN_CODE=123456", flags)
        self.assertIn("-D MAX_CONTACTS=100", flags)
        self.assertNotIn("-D MAX_GROUP_CHANNELS", flags)
        self.assertNotIn("-D OFFLINE_QUEUE_SIZE", flags)
        self.assertNotIn("-D OTA_SHARED_COMPANION_QUEUE", flags)
        self.assertNotIn("-D ONE_KEY_DM_SHARED_OFFLINE_QUEUE", flags)
        self.assertNotIn("-D MESH_MIN_RUNTIME_HEAP", flags)
        self.assertEqual(self.env["extends"], "env:Heltec_v3_companion_radio_full")
        self.assertIn("${env:Heltec_v3_companion_radio_full.build_flags}", flags)

    def test_nimble_is_pinned_and_not_in_the_ordinary_release_matrix(self):
        self.assertIn("-D MESH_USE_NIMBLE_ARDUINO=1", self.env["build_flags"])
        self.assertIn("h2zero/NimBLE-Arduino @ 2.5.1", self.env["lib_deps"])
        self.assertIn("${env:Heltec_v3_companion_radio_full.lib_deps}",
                      self.env["lib_deps"])
        self.assertEqual(self.env["lib_ignore"], "ESP32 BLE Arduino")
        base = configparser.ConfigParser(interpolation=None, strict=False)
        base.read(ROOT / "platformio.ini")
        self.assertNotIn("platformio.heltec-v3-preview3.ini",
                         base["platformio"]["extra_configs"])

    def test_version_and_partition_contract(self):
        self.assertEqual(self.env["board_build.flash_mode"], "dio")
        self.assertEqual(self.env["board_build.partitions"], "default_8MB.csv")
        self.assertIn("v1.17.1.8-preview3", self.env["build_flags"])
        self.assertNotIn("DISABLE_LORA_OTA=1", self.env["build_flags"])
        # Never set a flag that removes the WiFi/BLE/sensor/display features.
        self.assertNotRegex(self.env["build_flags"], re.compile(
            r"-D\s*(WEBCONFIG_DISABLED|DISABLE_WIFI_OTA|UI_NO_\w+|"
            r"ENV_EXCLUDE_\w+)(?:=|\s|$)"))


if __name__ == "__main__":
    unittest.main()
