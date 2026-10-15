"""PowerSaving-v17 target names and FEM defaults must survive integration."""
import configparser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class IotthinksMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configparser.ConfigParser(interpolation=None,
                                               inline_comment_prefixes=(";",))
        cls.config.read([ROOT / "platformio.ini", *sorted((ROOT / "variants").glob("*/platformio.ini"))],
                        encoding="utf-8")

    def flags(self, section):
        value = self.config[section].get("build_flags", "")
        import re
        return re.sub(r"\$\{([^}]+)\}", lambda match: self.flags(match[1].rsplit(".", 1)[0]), value)

    def test_old_v43_femoff_target_remains_alias_for_new_name(self):
        self.assertEqual(self.config["env:heltec_v4_3_companion_radio_ble_femoff"]["extends"],
                         "env:heltec_v4_companion_radio_ble_femoff")
        self.assertEqual(self.config["env:heltec_v4_3_companion_radio_ble_ps_femoff"]["extends"],
                         "env:heltec_v4_3_companion_radio_ble_femoff")
        self.assertIn("-D RADIO_FEM_RXGAIN=0", self.flags("env:heltec_v4_companion_radio_ble_femoff"))

    def test_explicit_femon_defaults_override_fork_default(self):
        for section in ("env:Heltec_t096_companion_radio_ble_femon",
                        "env:heltec_v4_companion_radio_ble_femon",
                        "env:heltec_tracker_v2_companion_radio_ble_femon"):
            with self.subTest(section=section):
                self.assertIn("-D RADIO_FEM_RXGAIN=1", self.flags(section))
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        self.assertIn("_prefs.radio_fem_rxgain = DEFAULT_FEM_RX_GAIN;", source)

    def test_new_serial_bridge_targets_keep_distinct_uarts(self):
        rak = self.flags("env:RAK_3401_repeater_bridge_rs232_serial1")
        tracker = self.flags("env:heltec_tracker_v2_repeater_bridge_rs232")
        self.assertIn("-D WITH_RS232_BRIDGE=Serial1", rak)
        self.assertIn("-D WITH_RS232_BRIDGE=Serial2", tracker)
        self.assertIn("-D WITH_RS232_BRIDGE_RX=44", tracker)
        self.assertIn("-D WITH_RS232_BRIDGE_TX=43", tracker)


if __name__ == "__main__":
    unittest.main()
