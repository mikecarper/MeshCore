#!/usr/bin/env python3
"""Run the actual ESP32 BLE adapter through reversible stack lifecycles."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Esp32BlePowerTests(unittest.TestCase):
    def test_bluedroid_bond_session_cleanup(self):
        self.run_adapter("test_bluedroid_peer_cleanup.cpp", [
            "-DCONFIG_BLUEDROID_ENABLED=1", "-DCONFIG_BT_ACL_CONNECTIONS=4",
            "-DMESH_BLUEDROID_BOND_PROTECTION=1"],
            ROOT / "test/fixtures/esp32_ble_power/mocks")

    def test_nimble_reversible_power_faults_identity_bonds_and_callbacks(self):
        self.run_adapter("test_nimble_power.cpp", ["-DMESH_USE_NIMBLE_ARDUINO=1"],
                         ROOT / "test/fixtures/nimble_companion/mocks")

    def test_bluedroid_legacy_static_ownership_and_partial_stop_failures(self):
        self.run_adapter("test_bluedroid_power.cpp", ["-DCONFIG_BLUEDROID_ENABLED=1", "-DCONFIG_BT_GATTS_SEND_SERVICE_CHANGE_AUTO=1"],
                         ROOT / "test/fixtures/esp32_ble_power/mocks")

    def test_bluedroid_new_library_ownership_and_partial_stop_failures(self):
        self.run_adapter("test_bluedroid_power.cpp", ["-DCONFIG_BLUEDROID_ENABLED=1",
                         "-DTEST_BLE_OWNED_SERVER=1", "-DCONFIG_BT_GATTS_SEND_SERVICE_CHANGE_AUTO=1"],
                         ROOT / "test/fixtures/esp32_ble_power/mocks")

    def test_bluedroid_manual_mode_keeps_marker_dependency_disabled(self):
        self.run_adapter("test_bluedroid_power.cpp", ["-DCONFIG_BLUEDROID_ENABLED=1"],
                         ROOT / "test/fixtures/esp32_ble_power/mocks")

    def run_adapter(self, fixture, flags, mocks):
        with tempfile.TemporaryDirectory(prefix="meshcore-ble-power-") as temp:
            binary = Path(temp) / "ble-power"
            result = subprocess.run([
                "c++", "-std=c++17", "-g", "-fsanitize=address,undefined", *flags,
                "-I", str(mocks),
                "-I", str(ROOT / "test/fixtures/nimble_companion/mocks"),
                "-I", str(ROOT / "src"),
                str(ROOT / "src/helpers/esp32/SerialBLEInterface.cpp"),
                str(ROOT / "test/fixtures/esp32_ble_power" / fixture),
                "-o", str(binary),
            ], text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            if fixture != "test_bluedroid_peer_cleanup.cpp":
                self.assertIn("lifecycle suites passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
