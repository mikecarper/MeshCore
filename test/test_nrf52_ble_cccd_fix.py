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

    def test_full_companion_initializes_cccd_from_ram_before_empty_fallback(self):
        patched = module.patched_connection_source(
            module.OLD_CONNECTION_INCLUDE + module.OLD_LOAD
        )
        self.assertIn("mesh_nrf52_restore_ram_cccd", patched)
        self.assertLess(patched.index("!mesh_nrf52_restore_ram_cccd"),
                        patched.index("!loadCccd()"))
        self.assertLess(patched.index("!loadCccd()"),
                        patched.index("sd_ble_gatts_sys_attr_set(_conn_hdl, NULL"))
        self.assertEqual(module.patched_connection_source(patched), patched)

    def test_full_companion_restores_same_peers_cccd_without_flash(self):
        app = (ROOT / "src/helpers/nrf52/SerialBLEInterface.cpp").read_text(encoding="utf-8")
        self.assertIn("captureCccdInRam", app)
        self.assertIn("std::atomic<bool> valid", app)
        self.assertIn("cccdPeersMatch", app)
        self.assertIn("BLE_UUID_DESCRIPTOR_CLIENT_CHAR_CONFIG", app)
        self.assertIn("sd_ble_gatts_sys_attr_get", app)
        self.assertIn("sd_ble_gatts_sys_attr_set", app)
        self.assertIn('__asm__ __volatile__("" ::: "memory")', app)
        self.assertIn("cccd_written_this_connection || !peer", app)
        self.assertIn("bool mesh_nrf52_restore_ram_cccd", app)
        self.assertIn("captureCccdDeferred", app)
        self.assertIn("ada_callback(&instance->_peer_address", app)
        self.assertNotIn("mesh_nrf52_service_cccd_save", app)

    def test_bluefruit_begin_uses_private_unoptimized_copy(self):
        patched = module.patched_bluefruit_source(
            "before\n" + module.OLD_BEGIN + "\nafter\n"
        )
        self.assertEqual(patched.count(module.FIXED_BEGIN), 1)
        self.assertEqual(module.patched_bluefruit_source(patched), patched)

    def test_unknown_bluefruit_source_fails_closed(self):
        with self.assertRaises(RuntimeError):
            module.patched_bluefruit_source("changed SDK layout")


if __name__ == "__main__":
    unittest.main()
