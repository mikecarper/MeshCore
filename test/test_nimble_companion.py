#!/usr/bin/env python3
"""Exercise the real ESP32 Companion adapter against the NimBLE 2.5 API."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NimbleCompanionTests(unittest.TestCase):
    def test_identity_security_and_data_path_under_sanitizers(self):
        self.run_adapter()

    def test_lab15_interval_real_adapter_under_sanitizers(self):
        self.run_adapter("-DMESH_HIL_BLE_BENCH=1", "-DMESH_HIL_BLE_INTERVAL_MS=15")

    def test_fast_interval_is_lab_only_and_bounded(self):
        invalid = (
            ("-DMESH_HIL_BLE_INTERVAL_MS=15",),
            ("-DMESH_HIL_BLE_BENCH=0", "-DMESH_HIL_BLE_INTERVAL_MS=15"),
            ("-DMESH_HIL_BLE_BENCH=2", "-DMESH_HIL_BLE_INTERVAL_MS=15"),
            ("-DMESH_HIL_BLE_BENCH=1", "-DMESH_HIL_BLE_INTERVAL_MS=6"),
            ("-DMESH_HIL_BLE_BENCH=1", "-DMESH_HIL_BLE_INTERVAL_MS=61"),
        )
        for flags in invalid:
            with self.subTest(flags=flags):
                result = subprocess.run([
                    "c++", "-std=c++17", "-fsyntax-only", "-DMESH_USE_NIMBLE_ARDUINO=1", *flags,
                    "-I", str(ROOT / "test/fixtures/nimble_companion/mocks"),
                    "-I", str(ROOT / "src"),
                    str(ROOT / "src/helpers/esp32/SerialBLEInterface.cpp"),
                ], text=True, capture_output=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("MESH_HIL_BLE_INTERVAL_MS", result.stderr)

    def run_adapter(self, *flags):
        with tempfile.TemporaryDirectory(prefix="meshcore-nimble-") as temp:
            binary = Path(temp) / "nimble-companion"
            result = subprocess.run([
                "c++", "-std=c++17", "-g", "-fsanitize=address,undefined",
                "-DMESH_USE_NIMBLE_ARDUINO=1",
                *flags,
                "-I", str(ROOT / "test/fixtures/nimble_companion/mocks"),
                "-I", str(ROOT / "src"),
                str(ROOT / "src/helpers/esp32/SerialBLEInterface.cpp"),
                str(ROOT / "test/fixtures/nimble_companion/test_nimble_companion.cpp"),
                "-o", str(binary),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
