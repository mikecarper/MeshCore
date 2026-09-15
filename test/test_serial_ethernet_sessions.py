#!/usr/bin/env python3
"""Exercise production Ethernet framing, backpressure and session ownership."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_serial_wifi_sessions import SANITIZER_FLAGS
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/serial_ethernet_sessions"
MOCKS = ROOT / "test/fixtures/serial_wifi_sessions/mocks"

SCENARIOS = (
    "short header writes retain suffix",
    "short payload writes retain suffix",
    "zero-byte write is backpressure",
    "higher-priority response cannot interrupt partial head",
    "full queue cannot evict partly sent head",
    "maximum frame final byte survives priority insertion",
    "same-IP live replacement replays whole frame and keeps owner",
    "different-IP replacement cancels owner and clears bounded queue",
    "actual disconnect cancels owner but keeps complete same-IP backlog",
    "replacement clears old stream route before exposing new command",
    "disable closes socket cancels once and clears queue",
    "oversized input disconnects without truncated execution",
    "incomplete command deadline disconnects",
    "fragmented maximum frame and next command parse intact",
    "incomplete command deadline survives millis rollover",
    "null output never dispatches or dereferences a command",
)


class SerialEthernetSessionTests(unittest.TestCase):
    def test_production_transport_sessions(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-ethernet-sessions-") as directory:
            binary = Path(directory) / "ethernet-sessions.exe"
            for raw in (0, 1):
                compiled = subprocess.run(
                    [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                     *SANITIZER_FLAGS, f"-DETHERNET_RAW_LINE={raw}",
                     f"-I{MOCKS}", f"-I{ROOT / 'src'}",
                     str(FIXTURE / "test.cpp"),
                     str(ROOT / "src/helpers/ethernet/SerialEthernetInterface.cpp"),
                     "-o", str(binary)], capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                for scenario, description in enumerate(SCENARIOS):
                    with self.subTest(raw=raw, scenario=description):
                        checked = subprocess.run(
                            [str(binary), str(scenario)], capture_output=True,
                            text=True, timeout=10,
                        )
                        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                        self.assertIn(f"PASS: Ethernet session scenario {scenario}", checked.stdout)

    def test_hardware_drivers_cancel_before_exposing_new_client(self):
        for driver in ("ch390/CH390EthernetInterface", "RAK13800/RAK13800EthernetInterface"):
            source = (ROOT / f"src/helpers/ethernet/{driver}.cpp").read_text(encoding="utf-8")
            name = driver.split("/")[-1]
            loop = extract_braced(source, f"void {name}::loop()")
            with self.subTest(driver=name):
                self.assertLess(loop.index("disconnectClient();"), loop.index("onClientConnected("))
                self.assertLess(loop.index("onClientConnected((uint32_t)remoteIp);"),
                                loop.index("client = newClient;"))
                close = extract_braced(source, f"void {name}::disconnectClient()")
                self.assertIn("_isConnected = false;", close)
                self.assertIn("client.stop();", close)


if __name__ == "__main__":
    unittest.main()
