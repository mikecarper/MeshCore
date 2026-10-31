"""Check resolved universal board contracts without starting PlatformIO."""
import configparser
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import consolidated_profiles


class UniversalLoRaBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configparser.ConfigParser(interpolation=None, strict=False,
                                              inline_comment_prefixes=(";",))
        cls.config.read([ROOT / "platformio.ini", *sorted((ROOT / "variants").glob("*/platformio.ini"))])

    def option(self, section, key):
        values = self.config[section]
        if key in values:
            text = values[key]
        else:
            for parent in values.get("extends", "").split(","):
                if parent.strip():
                    value = self.option(parent.strip(), key)
                    if value is not None:
                        return value
            return None
        return re.sub(r"\$\{([^}.]+)\.([^}]+)\}",
                      lambda m: self.option(m[1], m[2]) or "", text)

    def defines(self, target):
        flags = self.option("env:" + target, "build_flags")
        return dict((m[1], m[2] or "1") for m in re.finditer(
            r"-D\s*([A-Za-z_][A-Za-z0-9_]*)(?:=([^\s]+))?", flags))

    def test_all_existing_roles_have_universal_counterparts(self):
        for prefix, folder in (("Tbeam", "lilygo_tbeam_universal"),
                               ("LilyGo_T3S3", "lilygo_t3s3_universal")):
            old = prefix + ("_SX1262_" if prefix == "Tbeam" else "_sx1262_")
            roles = [section.removeprefix("env:" + old)
                     for section in self.config.sections() if section.startswith("env:" + old)]
            self.assertTrue(roles)
            for role in roles:
                target = prefix + "_" + role
                self.assertIn("env:" + target, self.config, target)
                flags = self.defines(target)
                self.assertEqual(flags.get("MESH_RADIO_AUTO_FAMILY"), "1", target)
                self.assertEqual(flags["RADIO_CLASS"], "UniversalLoRaRadio", target)
                self.assertEqual(flags["WRAPPER_CLASS"], "UniversalLoRaRadio", target)
                self.assertIn("USE_SX1262", flags)
                self.assertIn("USE_SX1276", flags)
                self.assertEqual(flags["LORA_TX_POWER"], "20")
                filters = self.option("env:" + target, "build_src_filter")
                self.assertIn("../variants/" + folder, filters)
                self.assertNotIn("../variants/lilygo_tbeam_SX1262>", filters)
                self.assertNotIn("../variants/lilygo_t3s3>", filters)

    def test_selected_chip_irq_and_board_wiring(self):
        expected = {
            "Tbeam_repeater": ("ttgo-t-beam", "26", "33", "32", "18", "23"),
            "LilyGo_T3S3_repeater": ("t3_s3_v1_x", "9", "33", "34", "7", "8"),
        }
        for target, (board, dio0, dio1, busy, nss, reset) in expected.items():
            flags = self.defines(target)
            self.assertEqual(self.option("env:" + target, "board"), board)
            for name, value in (("P_LORA_DIO_0", dio0), ("P_LORA_DIO_1", dio1),
                                ("P_LORA_BUSY", busy), ("P_LORA_NSS", nss), ("P_LORA_RESET", reset)):
                self.assertEqual(flags[name], value)
            self.assertEqual(flags["SX126X_DIO3_TCXO_VOLTAGE"], "1.8")
            self.assertEqual(flags["SX126X_CURRENT_LIMIT"], "140")
            self.assertEqual(flags["SX127X_CURRENT_LIMIT"], "120")

    def test_t3_does_not_drive_sx127_dio_as_rf_switch_or_oled_reset(self):
        flags = self.defines("LilyGo_T3S3_repeater")
        self.assertNotIn("SX127X_RXEN", flags)
        self.assertNotIn("SX127X_TXEN", flags)
        self.assertEqual(flags["PIN_OLED_RESET"], "-1")

    def test_legacy_names_keep_their_chip_and_paths(self):
        for target, chip, folder in (
            ("Tbeam_SX1262_repeater", "CustomSX1262", "lilygo_tbeam_SX1262"),
            ("Tbeam_SX1276_repeater", "CustomSX1276", "lilygo_tbeam_SX1276"),
            ("LilyGo_T3S3_sx1262_repeater", "CustomSX1262", "lilygo_t3s3"),
            ("LilyGo_T3S3_sx1276_repeater", "CustomSX1276", "lilygo_t3s3_sx1276"),
        ):
            flags = self.defines(target)
            self.assertEqual(flags["RADIO_CLASS"], chip)
            self.assertNotIn("MESH_RADIO_AUTO_FAMILY", flags)
            self.assertIn("../variants/" + folder, self.option("env:" + target, "build_src_filter"))

    def test_runtime_proof_matches_only_universal_targets(self):
        for target in ("Tbeam_repeater", "LilyGo_T3S3_repeater"):
            env = {"board": self.option("env:" + target, "board"),
                   "build_flags": self.option("env:" + target, "build_flags").splitlines()}
            specs = consolidated_profiles.specs_for_config(target, env)
            self.assertEqual(len(specs), 1)
            self.assertEqual(specs[0]["capability"], "radio.autodetect.sx126x_sx127x")
            old = target.replace("_repeater", "_SX1262_repeater" if target.startswith("Tbeam") else "_sx1262_repeater")
            self.assertFalse(consolidated_profiles.specs_for_config(old, env))


if __name__ == "__main__":
    unittest.main()
