#!/usr/bin/env python3
"""Execute production ESP32 compatibility settings with NVS and SDK faults."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/wifi_compatibility"
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
ORDINARY = ("defaults", "parse", "protocol_reboot", "mode_reboot", "corrupt",
            "invalid", "unloaded", "open_failure", "write_failure", "verify_failure",
            "independent", "bg_ap", "bg_ap_race", "bg_lr_start", "owner_conflict",
            "driver_failure")


class WiFiCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")
        cls.directory = tempfile.TemporaryDirectory(prefix="meshcore-wifi-compat-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.binaries = {}
        for kind, flags in (
            ("ordinary", []),
            ("bridge", ["-DWITH_ESPNOW_BRIDGE=1"]),
            ("primary", ["-DMESH_PRIMARY_ESPNOW=1"]),
            ("primary_full", ["-DMESH_PRIMARY_ESPNOW=1", "-DMESH_ESPNOW_RADIO=1"]),
        ):
            binary = Path(cls.directory.name) / (kind + ".exe")
            compiled = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", *SANITIZERS,
                 "-DESP32=1", *flags, f"-I{FIXTURE}", f"-I{ROOT / 'src'}",
                 str(FIXTURE / "test.cpp"), "-o", str(binary)],
                capture_output=True, text=True)
            if compiled.returncode:
                raise AssertionError(compiled.stdout + compiled.stderr)
            cls.binaries[kind] = binary

    def run_case(self, name, kind="ordinary"):
        result = subprocess.run([str(self.binaries[kind]), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ordinary_settings(self):
        for scenario in ORDINARY:
            with self.subTest(scenario=scenario):
                self.run_case(scenario)

    def test_bridge_build_preserves_normal_and_can_use_bg_when_not_running(self):
        for scenario in ORDINARY:
            with self.subTest(scenario=scenario):
                self.run_case(scenario, "bridge")

    def test_primary_espnow_preserves_lr_and_rejects_bg_without_mutation(self):
        for kind in ("primary", "primary_full"):
            for scenario in ("defaults", "parse", "mode_reboot", "corrupt", "invalid",
                             "unloaded", "open_failure", "write_failure", "verify_failure",
                             "primary_conflict"):
                with self.subTest(kind=kind, scenario=scenario):
                    self.run_case(scenario, kind)


if __name__ == "__main__":
    unittest.main()
