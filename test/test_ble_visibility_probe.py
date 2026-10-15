import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("ble_visibility_probe", Path(__file__).resolve().parents[1] / "tools/hil/ble_visibility_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
TARGET = "D2:90:05:46:31:01"


class VisibilityTests(unittest.TestCase):
    def test_info_not_available_is_not_a_cached_device(self):
        for code in (0, 1):
            self.assertFalse(probe.is_cached_device(f"Device {TARGET} not available\n", TARGET, code))
        self.assertFalse(probe.is_cached_device(f"Device {TARGET}\n", TARGET, 1))
        self.assertFalse(probe.is_cached_device(f"Name: Device {TARGET}\n", TARGET, 0))

    def test_successful_info_requires_exact_header(self):
        for header in (f"Device {TARGET}", f"Device {TARGET} (random)", f"Device {TARGET} (public)"):
            self.assertTrue(probe.is_cached_device(f"\x1b[0;92m{header}\x1b[0m\n\tName: test\n", TARGET, 0))

    def test_cache_query_errors_are_not_verified_absence(self):
        self.assertIsNone(probe.device_cache_state("org.bluez failed", TARGET, 1))
        self.assertIsNone(probe.device_cache_state(f"Device {TARGET}\n", TARGET, 1))
        self.assertIsNone(probe.device_cache_state(f"Device {TARGET}\nDevice {TARGET} not available\n", TARGET, 0))
        self.assertIs(probe.device_cache_state(f"Device {TARGET} not available\n", TARGET, 1), False)

    def test_new_cache_record_is_evidence_without_fabricated_events_or_rssi(self):
        scan = probe.parse_discovery(f"[NEW] Device {TARGET} name\nDiscovery started\n", TARGET)
        result = probe.with_cache_evidence(scan, False, True)
        self.assertTrue(result["advertisement_evidence"])
        self.assertTrue(result["new_target_record_during_scan"])
        self.assertFalse(result["post_start_event_evidence"])
        self.assertEqual(result["target_events"], 0)
        self.assertEqual(result["rssi_dbm"], [])
        self.assertEqual(result["evidence_sources"], ["absent_before_present_after_successful_discovery"])

    def test_existing_or_unknown_cache_and_failed_discovery_not_new_evidence(self):
        scan = probe.parse_discovery("Discovery started\n", TARGET)
        for before, after in ((True, True), (None, True), (False, None), (False, False)):
            result = probe.with_cache_evidence(scan, before, after)
            self.assertFalse(result["advertisement_evidence"])
        failed = probe.parse_discovery("Failed to start discovery\n", TARGET)
        self.assertFalse(probe.with_cache_evidence(failed, False, True)["advertisement_evidence"])

    def test_fresh_rssi_remains_separate_from_cache_transition(self):
        scan = probe.parse_discovery(f"Discovery started\n[CHG] Device {TARGET} RSSI: -40\n", TARGET)
        result = probe.with_cache_evidence(scan, True, True)
        self.assertTrue(result["post_start_event_evidence"])
        self.assertFalse(result["new_target_record_during_scan"])
        self.assertEqual(result["evidence_sources"], ["post_start_rssi_update"])

    def test_cached_enumeration_is_not_actual_discovery(self):
        result = probe.parse_discovery(f"[NEW] Device {TARGET} cached\nDiscovery started\n", TARGET)
        self.assertFalse(result["advertisement_evidence"])
        self.assertEqual(result["target_events"], 0)

    def test_filters_other_devices_and_parses_target_rssi(self):
        result = probe.parse_discovery(f"Discovery started\n[CHG] Device AA:00:00:00:00:01 RSSI: -12\n\x1b[0;92m[CHG]\x1b[0m Device {TARGET} RSSI: -54\n[CHG] Device {TARGET} RSSI: 0xffffffcb (-53)\n", TARGET)
        self.assertTrue(result["advertisement_evidence"])
        self.assertEqual(result["rssi_dbm"], [-54, -53])
        self.assertEqual(result["target_events"], 2)

    def test_scan_start_failure_does_not_use_stale_events(self):
        self.assertFalse(probe.parse_discovery(f"Failed to start discovery\n[NEW] Device {TARGET} cached\n", TARGET)["advertisement_evidence"])


if __name__ == "__main__":
    unittest.main()
