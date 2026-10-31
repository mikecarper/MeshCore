"""Resolve SX1262/SX1268 board recipes without starting PlatformIO."""
import configparser
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
GROUPS = (
    ("Generic_E22", "generic-e22", "generic_e22_universal", ("repeater", "repeater_bridge_espnow")),
    ("Meshadventurer", "meshadventurer", "meshadventurer_universal",
     ("repeater", "repeater_bridge_espnow", "companion_radio_usb", "companion_radio_ble",
      "terminal_chat", "room_server", "kiss_modem")),
    ("Tenstar_C3", "tenstar_c3", "tenstar_c3_universal",
     ("repeater", "repeater_bridge_espnow", "kiss_modem")),
)


class UniversalSX126xBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configparser.ConfigParser(interpolation=None, strict=False,
                                              inline_comment_prefixes=(";",))
        cls.config.read([ROOT / "platformio.ini", *sorted((ROOT / "variants").glob("*/platformio.ini"))])

    def option(self, section, key):
        values = self.config[section]
        if key not in values:
            for parent in values.get("extends", "").split(","):
                if parent.strip():
                    value = self.option(parent.strip(), key)
                    if value is not None:
                        return value
            return None
        return re.sub(r"\$\{([^}.]+)\.([^}]+)\}",
                      lambda m: self.option(m[1], m[2]) or "", values[key])

    def defines(self, target):
        return dict((m[1], m[2] or "1") for m in re.finditer(
            r"-D\s*([A-Za-z_][A-Za-z0-9_]*)(?:=([^\s]+))?",
            self.option("env:" + target, "build_flags")))

    def targets(self):
        for prefix, old_folder, folder, roles in GROUPS:
            for role in roles:
                yield prefix + "_" + role, prefix + "_sx1262_" + role, old_folder, folder
        yield "Generic_E22_kiss_modem", None, "generic-e22", "generic_e22_universal"

    def test_all_roles_select_real_sx1262_and_sx1268_backends(self):
        for target, _, old_folder, folder in self.targets():
            with self.subTest(target=target):
                flags = self.defines(target)
                for key in ("MESH_RADIO_AUTO_FAMILY", "MESH_RADIO_AUTO_SX1268", "USE_SX1262", "USE_SX1268"):
                    self.assertEqual(flags[key], "1")
                self.assertEqual(flags["MESH_RADIO_AUTO_SX127X"], "0")
                self.assertEqual(flags["RADIO_CLASS"], "UniversalLoRaRadio")
                self.assertEqual(flags["WRAPPER_CLASS"], "UniversalLoRaRadio")
                self.assertNotIn("USE_SX1276", flags)
                self.assertNotIn("P_LORA_DIO_0", flags)
                filters = self.option("env:" + target, "build_src_filter")
                self.assertIn("+<../variants/" + folder + ">", filters)
                self.assertNotIn("+<../variants/" + old_folder + ">", filters)

    def test_existing_capacities_and_peripherals_are_preserved(self):
        replaced = {"RADIO_CLASS", "WRAPPER_CLASS", "USE_SX1262", "USE_SX1268"}
        for target, legacy, _, _ in self.targets():
            if legacy is None:
                continue
            with self.subTest(target=target):
                current, old = self.defines(target), self.defines(legacy)
                for key, value in old.items():
                    if key not in replaced:
                        self.assertEqual(current.get(key), value, key)
                self.assertEqual(self.option("env:" + target, "board"),
                                 self.option("env:" + legacy, "board"))
                self.assertEqual(self.option("env:" + target, "lib_deps"),
                                 self.option("env:" + legacy, "lib_deps"))

    def test_chip_specific_legacy_recipes_keep_their_driver(self):
        for prefix, old_folder, _, roles in GROUPS:
            for role in roles:
                for chip in ("sx1262", "sx1268"):
                    target = prefix + "_" + chip + "_" + role
                    if "env:" + target not in self.config:
                        continue
                    with self.subTest(target=target):
                        flags = self.defines(target)
                        self.assertEqual(flags["RADIO_CLASS"], "CustomSX" + chip[2:])
                        self.assertNotIn("MESH_RADIO_AUTO_FAMILY", flags)
                        self.assertIn("+<../variants/" + old_folder + ">",
                                      self.option("env:" + target, "build_src_filter"))

    def test_reset_and_rf_control_follow_actual_board_wiring(self):
        for prefix in ("Generic_E22", "Meshadventurer"):
            flags = self.defines(prefix + "_repeater")
            for key, value in {"P_LORA_NSS": "18", "P_LORA_DIO_1": "33",
                               "P_LORA_RESET": "23", "P_LORA_BUSY": "32",
                               "P_LORA_SCLK": "5", "P_LORA_MOSI": "27", "P_LORA_MISO": "19",
                               "SX126X_TXEN": "13", "SX126X_RXEN": "14"}.items():
                self.assertEqual(flags[key], value)
        tenstar = self.defines("Tenstar_C3_repeater")
        for key, value in {"P_LORA_NSS": "6", "P_LORA_DIO_1": "2", "P_LORA_BUSY": "3",
                           "P_LORA_RESET": "RADIOLIB_NC", "P_LORA_SCLK": "8",
                           "P_LORA_MOSI": "7", "P_LORA_MISO": "9", "LORA_TX_BOOST_PIN": "4"}.items():
            self.assertEqual(tenstar[key], value)

    def test_board_power_precedes_selection_and_selection_is_stable(self):
        parents = ("ESP32Board", "MeshadventurerBoard", "XiaoC3Board")
        for (_, _, folder, _), parent in zip(GROUPS, parents):
            header = (ROOT / "variants" / folder / "target.h").read_text()
            source = (ROOT / "variants" / folder / "target.cpp").read_text()
            self.assertLess(header.index(parent + "::begin();"),
                            header.index("    prepareUniversalLoRaRadio();"))
            self.assertIn("if (radio_driver.family() != mesh::LoRaRadioFamily::None) return true;", source)
            self.assertIn("radio_driver(radio_module, radio_module, board)", source)
            self.assertNotIn("TunableRadioHal", source)


if __name__ == "__main__":
    unittest.main()
