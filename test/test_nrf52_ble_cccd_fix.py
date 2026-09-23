"""Guard the nRF52 Full Companion CCCD build-local workaround."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/nrf52_ble_cccd_fix.py"
spec = spec_from_file_location("nrf52_ble_cccd_fix", SCRIPT)
module = module_from_spec(spec)
spec.loader.exec_module(module)


class Nrf52BleCccdFixTest(unittest.TestCase):
    def test_only_full_companions_skip_persistence(self):
        patched = module.patched_source("before\n" + module.OLD_SAVE + "after\n")
        self.assertIn("!defined(COMPANION_RADIO_FULL) || !COMPANION_RADIO_FULL", patched)
        self.assertIn("conn->saveCccd();", patched)
        self.assertEqual(module.patched_source(patched), patched)

    def test_unrecognized_core_fails_closed(self):
        with self.assertRaises(RuntimeError):
            module.patched_source("changed SDK layout")

    def test_nrf52_base_loads_workaround(self):
        ini = (ROOT / "platformio.ini").read_text(encoding="utf-8")
        self.assertIn("pre:scripts/nrf52_ble_cccd_fix.py", ini)


if __name__ == "__main__":
    unittest.main()
