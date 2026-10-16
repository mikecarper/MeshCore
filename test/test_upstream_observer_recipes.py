#!/usr/bin/env python3
"""Host-only contracts for the observer recipes imported from 33f1b1096.

Resolve inheritance and interpolation without starting PlatformIO or a build.
The existing board recipes remain authoritative for hardware and OTA policy.
"""
import configparser
import csv
import fnmatch
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOARDS = {
    "ebyte_eora_s3": ("Ebyte_EoRa-S3",),
    "heltec_e213": ("Heltec_E213",),
    "heltec_e290": ("Heltec_E290",),
    "heltec_rc32": ("heltec_rc32", "heltec_rc32_without_display"),
    "heltec_tracker": ("Heltec_Wireless_Tracker",),
    "heltec_wireless_paper": ("Heltec_Wireless_Paper",),
    "lilygo_t3s3_sx1276": ("LilyGo_T3S3_sx1276",),
    "lilygo_teth_elite": ("LilyGo_TETH_Elite_sx1262",),
    "meshnology_w12": ("meshnology_w12",),
    "xiao_s3": ("Xiao_S3",),
    "waveshare_esp32s3_lr1121": ("Waveshare_S3_LR1121",),
}
NAMES = tuple(
    f"{board}_{role}_observer_mqtt"
    for boards in BOARDS.values() for board in boards
    for role in ("repeater", "room_server")
)


class Recipes:
    def __init__(self):
        self.config = configparser.ConfigParser(interpolation=None, strict=True)
        self.config.read([ROOT / "platformio.ini", *sorted(ROOT.glob("variants/*/platformio.ini"))])

    def value(self, section, key, seen=()):
        pair = (section, key)
        if pair in seen:
            raise AssertionError(f"cyclic recipe reference: {pair}")
        seen = (*seen, pair)
        if not self.config.has_option(section, key):
            for parent in self.config.get(section, "extends", fallback="").split(","):
                parent = parent.strip()
                if parent:
                    value = self.value(parent, key, seen)
                    if value:
                        return value
            return ""
        value = self.config.get(section, key)
        return re.sub(r"\$\{([^}.]+)\.([^}]+)\}",
                      lambda m: self.value(m[1], m[2], seen), value)

    def includes(self, section, source):
        included = False
        for sign, pattern in re.findall(r"([+-])<([^>]+)>", self.value(section, "build_src_filter")):
            # PlatformIO's file glob does not let a root *.cpp select a file
            # outside src. Match path components, then handle directory filters.
            source_parts, pattern_parts = source.split("/"), pattern.split("/")
            file_match = len(source_parts) == len(pattern_parts) and all(
                fnmatch.fnmatchcase(part, glob)
                for part, glob in zip(source_parts, pattern_parts)
            )
            if file_match or source.startswith(pattern.rstrip("/") + "/"):
                included = sign == "+"
        return included


class UpstreamObserverRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes = Recipes()

    def test_all_24_named_additions_exist_once(self):
        self.assertEqual(len(NAMES), 24)
        for family, boards in BOARDS.items():
            text = (ROOT / "variants" / family / "platformio.ini").read_text()
            for board in boards:
                for role in ("repeater", "room_server"):
                    name = f"{board}_{role}_observer_mqtt"
                    with self.subTest(env=name):
                        self.assertEqual(text.count(f"[env:{name}]"), 1)
                        self.assertTrue(self.recipes.config.has_section(f"env:{name}"))

    def test_observers_keep_inherited_ota_and_complete_tls_hooks(self):
        expected_hooks = (
            "generate_webconfig_html.py", "meshcore_image_identity.py",
            "portable_esp32_link.py", "esp32_ota_heap_context.py", "merge-bin.py",
            "tools/mota/pio_endf.py", "check_esp32_dram.py", "check_firmware_ram.py",
            "generate_cert_bundle.py",
        )
        for name in NAMES:
            section = f"env:{name}"
            with self.subTest(env=name):
                flags = self.recipes.value(section, "build_flags")
                for flag in ("ESP32_PLATFORM", "ENABLE_OTA=1", "OTA_FLASH_STORE=1",
                             "OTA_FOLDER_SERIAL", "WITH_MQTT_BRIDGE=1", "MQTT_MAX_PACKET_SIZE=1024"):
                    self.assertIn(flag, flags)
                hooks = self.recipes.value(section, "extra_scripts")
                for hook in expected_hooks:
                    self.assertIn(hook, hooks)
                self.assertEqual(self.recipes.value(section, "board_ssl_cert_source"), "adafruit-full")
                self.assertEqual(self.recipes.value(section, "board_build.embed_files"),
                                 "src/certs/x509_crt_bundle.bin")
                self.assertTrue((ROOT / "scripts/generate_cert_bundle.py").is_file())

    def test_mqtt_sources_dependencies_and_role_entrypoints(self):
        for name in NAMES:
            section = f"env:{name}"
            role = "simple_room_server" if "_room_server_" in name else "simple_repeater"
            other = "simple_repeater" if role == "simple_room_server" else "simple_room_server"
            with self.subTest(env=name):
                for source in ("helpers/bridges/MQTTBridge.cpp", "helpers/MQTTMessageBuilder.cpp",
                               "helpers/JWTHelper.cpp", f"../examples/{role}/main.cpp",
                               "helpers/ota/OtaTinf.c"):
                    self.assertTrue(self.recipes.includes(section, source), source)
                self.assertFalse(self.recipes.includes(section, f"../examples/{other}/main.cpp"))
                deps = self.recipes.value(section, "lib_deps")
                for dependency in ("PsychicMqttClient", "ArduinoJson @ 7.4.3", "NTPClient",
                                   "JChristensen/Timezone", "paulstoffregen/Time@1.6.1"):
                    self.assertIn(dependency, deps)
                if "WITH_SNMP=1" in self.recipes.value(section, "build_flags"):
                    self.assertTrue(self.recipes.includes(section, "helpers/SNMPAgent.cpp"))
                    self.assertIn("0neblock/SNMP_Agent", deps)
                if "_room_server_" in name:
                    self.assertIn("ROOM_PASSWORD=", self.recipes.value(section, "build_flags"))

    def test_small_flash_and_non_psram_profiles_keep_upstream_limits(self):
        for board in ("Ebyte_EoRa-S3", "LilyGo_T3S3_sx1276"):
            for role in ("repeater", "room_server"):
                section = f"env:{board}_{role}_observer_mqtt"
                self.assertEqual(self.recipes.value(section, "board_build.partitions"), "min_spiffs.csv")
                self.assertNotIn("WITH_SNMP", self.recipes.value(section, "build_flags"))
        for board in ("Heltec_Wireless_Tracker", "Heltec_Wireless_Paper"):
            for role in ("repeater", "room_server"):
                section = f"env:{board}_{role}_observer_mqtt"
                self.assertIn("MQTT_NEIGHBORS_WITHOUT_PSRAM=1", self.recipes.value(section, "build_flags"))

    def test_waveshare_observers_have_two_larger_slots_and_64k_filesystem(self):
        table = "variants/dual_ota_full_4MB.csv"
        for role in ("repeater", "room_server"):
            self.assertEqual(self.recipes.value(
                f"env:Waveshare_S3_LR1121_{role}_observer_mqtt", "board_build.partitions"), table)
        for role in ("repeater", "room_server", "companion_radio_usb", "companion_radio_ble", "kiss_modem"):
            self.assertEqual(self.recipes.value(
                f"env:Waveshare_S3_LR1121_{role}", "board_build.partitions"), "min_spiffs.csv")
        rows = list(csv.reader(line for line in (ROOT / table).read_text().splitlines()
                               if line.strip() and not line.lstrip().startswith("#")))
        parts = {row[0].strip(): (int(row[3], 0), int(row[4], 0)) for row in rows}
        self.assertEqual(parts["app0"], (0x10000, 0x1F0000))
        self.assertEqual(parts["app1"], (0x200000, 0x1F0000))
        self.assertEqual(parts["spiffs"], (0x3F0000, 0x10000))
        end = 0
        for offset, size in sorted(parts.values()):
            self.assertGreaterEqual(offset, end)
            end = offset + size
        self.assertEqual(end, 4 * 1024 * 1024)

    def test_rc32_local_startup_platform_and_safety_remain_inherited(self):
        for board in ("heltec_rc32", "heltec_rc32_without_display"):
            for role in ("repeater", "room_server"):
                section = f"env:{board}_{role}_observer_mqtt"
                flags = self.recipes.value(section, "build_flags")
                self.assertIn("ESP32_POST_BOOT_CPU_FREQ=160", flags)
                self.assertIn("RC32_PERIPHERAL_WARMUP_MS=100", flags)
                self.assertIn("-UENV_INCLUDE_GPS", flags)
                self.assertNotIn("-D ESP32_CPU_FREQ=", flags)
                self.assertNotIn("PIN_GPS_EN=45", flags)
                self.assertIn("55.03.311", self.recipes.value(section, "platform"))
                self.assertIn("3.3.11", self.recipes.value(section, "platform_packages"))
                self.assertEqual(self.recipes.value(section, "board_build.partitions"), "default_16MB.csv")
                self.assertEqual("HELTEC_RC32_WITH_DISPLAY" in flags, board == "heltec_rc32")


if __name__ == "__main__":
    unittest.main()
