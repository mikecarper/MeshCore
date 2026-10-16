"""Offline contract for the headless Indicator LoRa+ESP-NOW HIL recipe.

Does not invoke PlatformIO, a compiler, a serial port or any hardware tool.
These checks bind the recipe to the existing physical variant and partition
contract; actual ELF/EndF/boot/RadioLib qualification remains a separate build.
"""

import configparser
import csv
import fnmatch
from pathlib import Path
import re
import shlex
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENV = "env:SenseCapIndicator-LoRa_repeater_bridge_espnow_hil"


class IndicatorRepeaterHilRecipeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configparser.ConfigParser(interpolation=None, strict=False)
        cls.config.read([ROOT / "platformio.ini",
            ROOT / "variants/sensecap_indicator-espnow/platformio.ini",
            ROOT / "tools/hil/indicator_repeater_espnow.ini"], encoding="utf-8")

    def option(self, section, option, depth=0):
        self.assertLess(depth, 30, "recursive recipe interpolation")
        if self.config.has_option(section, option):
            value = self.config.get(section, option)
            return re.sub(r"\$\{([^}]+)\}", lambda match:
                self.option(*match[1].split(".", 1), depth=depth + 1), value)
        for parent in self.config.get(section, "extends", fallback="").split(","):
            if parent.strip():
                try:
                    return self.option(parent.strip(), option, depth + 1)
                except KeyError:
                    pass
        raise KeyError((section, option))

    def defines(self):
        words = shlex.split(self.option(ENV, "build_flags"))
        values = {}
        index = 0
        while index < len(words):
            word = words[index]
            if word in ("-D", "-U"):
                index += 1
                word += words[index]
            if word.startswith("-U"):
                values.pop(word[2:], None)
            elif word.startswith("-D"):
                name, _, value = word[2:].partition("=")
                values[name] = value or "1"
            index += 1
        return values

    def selected(self, source):
        result = False
        for action, pattern in re.findall(r"([+-])<([^>]+)>", self.option(ENV, "build_src_filter")):
            # Top-level '*.cpp' does not mean recursive matching in PIO.
            if "/" not in pattern and "/" in source:
                continue
            match = fnmatch.fnmatchcase(source, pattern)
            if not any(character in pattern for character in "*?["):
                match = match or source.startswith(pattern.rstrip("/") + "/")
            if match:
                result = action == "+"
        return result

    def test_exact_physical_lora_variant_and_n8r8_memory_contract(self):
        self.assertEqual(self.option(ENV, "extends"), "SenseCapIndicator-LoRa")
        self.assertEqual(self.option(ENV, "board"), "esp32-s3-devkitc-1")
        self.assertEqual(self.option(ENV, "board_upload.flash_size"), "8MB")
        self.assertEqual(self.option(ENV, "board_build.arduino.memory_type"), "qio_opi")
        self.assertEqual(self.option(ENV, "board_build.psram_type"), "opi")
        definitions = self.defines()
        self.assertEqual(definitions["MOTA_HW_ID"], '"SenseCapIndicator-LoRa"')
        self.assertEqual(definitions["SENSECAP_INDICATOR_LORA"], "1")
        self.assertEqual(definitions["WRAPPER_CLASS"], "IndicatorSX1262Wrapper")
        self.assertEqual([definitions[name] for name in ("P_LORA_NSS", "P_LORA_RESET",
                         "P_LORA_BUSY", "P_LORA_DIO_1")], ["64", "65", "66", "67"])

    def test_only_lab_frequency_can_transmit_and_boot_is_quiet(self):
        definitions = self.defines()
        for name, expected in {"LORA_FREQ": "908.5", "MESH_HIL_TX_FREQ_MHZ": "908.5",
                "LORA_BW": "500", "LORA_SF": "5", "LORA_CR": "5", "LORA_TX_POWER": "2",
                "ENABLE_ADVERT_ON_BOOT": "0", "DEFAULT_ADVERT_INTERVAL_MINUTES": "0",
                "DEFAULT_FLOOD_ADVERT_INTERVAL_HOURS": "0"}.items():
            with self.subTest(name=name):
                self.assertEqual(definitions[name], expected)

    def test_headless_keeps_the_radio_expander_library_not_font_or_primary_espnow(self):
        definitions = self.defines()
        for name in ("DISPLAY_CLASS", "MESH_PRIMARY_ESPNOW", "DISABLE_WIFI_OTA",
                     "WEBCONFIG_DISABLED", "OTA_SEEDER_ONLY"):
            self.assertNotIn(name, definitions)
        self.assertIn("LovyanGFX", self.option(ENV, "lib_deps"))
        for source in ("../variants/sensecap_indicator-espnow/target.cpp",
                       "../variants/sensecap_indicator-espnow/IndicatorRadioHal.cpp"):
            self.assertTrue(self.selected(source), source)
        for source in ("helpers/esp32/ESPNOWRadio.cpp", "helpers/ui/LGFXDisplay.cpp",
                       "../variants/sensecap_indicator-espnow/IndicatorFontClient.cpp"):
            self.assertFalse(self.selected(source), source)

    def test_full_repeater_ota_and_bridge_sources_are_not_companion_sources(self):
        definitions = self.defines()
        for name in ("WITH_ESPNOW_BRIDGE", "ENABLE_OTA", "OTA_FLASH_STORE",
                     "WIFI_OTA_SEEDER", "MESHCORE_EXPANDED_PARTITION_PROFILE"):
            self.assertEqual(definitions[name], "1", name)
        for source in ("../examples/simple_repeater/main.cpp", "../examples/simple_repeater/MyMesh.cpp",
                       "helpers/bridges/ESPNowBridge.cpp", "helpers/esp32/WebConfigServer.cpp",
                       "helpers/esp32/WiFiOtaSeeder.cpp", "helpers/ota/OtaContext.cpp",
                       "helpers/ota/detools/detools.c"):
            self.assertTrue(self.selected(source), source)
        self.assertFalse(self.selected("../examples/companion_radio/main.cpp"))
        self.assertFalse(self.selected("../examples/kiss_modem/main.cpp"))
        self.assertIn("AsyncElegantOTA", self.option(ENV, "lib_deps"))
        scripts = self.option(ENV, "extra_scripts")
        for script in ("meshcore_image_identity.py", "pio_endf.py", "check_firmware_ram.py"):
            self.assertIn(script, scripts)

    def test_partitions_preserve_settings_and_fit_two_2p5m_slots_inside_8m(self):
        table = ROOT / self.option(ENV, "board_build.partitions")
        with table.open(encoding="utf-8") as stream:
            rows = [row for row in csv.reader(line for line in stream if not line.lstrip().startswith("#")) if row]
        partitions = {row[0].strip(): (int(row[3].strip(), 0), int(row[4].strip(), 0)) for row in rows}
        self.assertEqual(partitions["nvs"], (0x9000, 0x5000))
        self.assertEqual(partitions["spiffs"], (0x290000, 0x160000))
        self.assertEqual(partitions["app0"], (0x10000, 0x280000))
        self.assertEqual(partitions["app1"], (0x400000, 0x280000))
        self.assertEqual(int(self.option(ENV, "board_upload.maximum_size")), 0x280000)
        self.assertLessEqual(max(offset + size for offset, size in partitions.values()), 8 * 1024 * 1024)
        spans = sorted(partitions.values())
        self.assertTrue(all(offset + size <= next_offset
                            for (offset, size), (next_offset, _) in zip(spans, spans[1:])))

    def test_wrapped_bridge_and_enabled_defaults_are_existing_production_code(self):
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="utf-8")
        self.assertIn("_prefs.bridge_format = mesh::bridge::ESPNOW_FORMAT_WRAPPED;", source)
        self.assertIn("_prefs.bridge_channel = 1;", source)
        self.assertIn("_prefs.bridge_enabled = 1;", source)
        recipe = (ROOT / "tools/hil/indicator_repeater_espnow.ini").read_text(encoding="utf-8")
        self.assertIn("d8:3b:da:75:23:ac", recipe)
        self.assertIn("No production variant", recipe)
        self.assertIn("No flashing/erasing", recipe)


if __name__ == "__main__":
    unittest.main()
