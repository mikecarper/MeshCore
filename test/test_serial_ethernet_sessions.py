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
    "worker ownership blocks socket IO but preserves logical session and queue",
    "worker pause retains only remaining framed input deadline",
    "worker pause cannot revive an already expired frame",
    "worker pause and remaining deadline survive millis rollover",
    "disconnect resets worker pause while replaying whole same-IP backlog",
    "disable resets worker pause and clears backlog",
    "same-IP replacement resets partial input and pause while replaying backlog",
    "different-IP replacement resets partial input pause and backlog",
    "worker pause beginning at zero preserves remaining deadline",
    "worker ownership without partial input cannot extend next frame deadline",
    "write command ownership pauses input deadline immediately",
    "read command ownership pauses before polling the socket again",
    "availability command ownership prevents a following socket read",
    "main-loop ownership service preserves timeout behind another locked route",
    "continuously replenished idle input yields after entry budget 1",
    "continuously replenished idle input yields after entry budget 7",
    "continuously replenished idle input yields after entry budget 257",
    "new command waits behind entry budget 1 until next loop",
    "new command waits behind entry budget 7 until next loop",
    "new command waits behind entry budget 257 until next loop",
    "last budgeted read records worker pause after parser state changes",
    "last budgeted read worker pause preserves deadline across rollover",
    "zero-available snapshot records worker pause immediately",
    "zero-available worker pause preserves deadline across rollover",
)


class SerialEthernetSessionTests(unittest.TestCase):
    def compile_transport(self, compiler, raw, binary, source):
        compiled = subprocess.run(
            [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
             *SANITIZER_FLAGS, f"-DETHERNET_RAW_LINE={raw}",
             f"-I{MOCKS}", f"-I{ROOT / 'src'}",
             f"-I{ROOT / 'src/helpers/ethernet'}", str(FIXTURE / "test.cpp"),
             str(source), "-o", str(binary)], capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)

    def test_production_transport_sessions(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-ethernet-sessions-") as directory:
            binary = Path(directory) / "ethernet-sessions.exe"
            for raw in (0, 1):
                self.compile_transport(compiler, raw, binary,
                                       ROOT / "src/helpers/ethernet/SerialEthernetInterface.cpp")
                for scenario, description in enumerate(SCENARIOS):
                    with self.subTest(raw=raw, scenario=description):
                        checked = subprocess.run(
                            [str(binary), str(scenario)], capture_output=True,
                            text=True, timeout=10,
                        )
                        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                        self.assertIn(f"PASS: Ethernet session scenario {scenario}", checked.stdout)

    def test_live_available_and_missing_final_pause_negative_controls(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        source = (ROOT / "src/helpers/ethernet/SerialEthernetInterface.cpp").read_text()
        snapshot = '''      int pending = available();
      while (pending-- > 0) {
        // A read may likewise hand its receive-ACK command to the worker;
        // leave the rest of the frame untouched until hardware is available.
        if (updateIOPause((uint32_t)millis())) break;'''
        live = '''      while (true) {
        if (updateIOPause((uint32_t)millis()) || !available()) break;
        if (updateIOPause((uint32_t)millis())) break;'''
        pause = '''      // Snapshot availability or the final budgeted read can hand IO to the
      // worker. Observe that pause after the byte has changed parser state.
      (void)updateIOPause((uint32_t)millis());'''
        self.assertEqual(source.count(snapshot), 1)
        self.assertEqual(source.count(pause), 1)
        with tempfile.TemporaryDirectory(prefix="meshcore-ethernet-rx-negative-") as directory:
            work = Path(directory)
            for name, modified, modes, scenarios in (
                    ("live", source.replace(snapshot, live), (0, 1), range(30, 36)),
                    ("naive", source.replace(pause, ""), (0,), range(36, 40))):
                variant = work / (name + ".cpp")
                variant.write_text(modified)
                for raw in modes:
                    binary = work / (name + str(raw) + ".exe")
                    self.compile_transport(compiler, raw, binary, variant)
                    for scenario in scenarios:
                        with self.subTest(variant=name, raw=raw, scenario=SCENARIOS[scenario]):
                            checked = subprocess.run([str(binary), str(scenario)], capture_output=True,
                                                     text=True, timeout=10)
                            self.assertNotEqual(checked.returncode, 0, "unsafe RX loop escaped regression")
                            expected = ("bounded read guard" if scenario <= 32 else
                                        "new arrivals must wait" if scenario <= 35 else
                                        "worker handoff must preserve remaining frame deadline")
                            self.assertIn(expected, checked.stderr)

    def test_hardware_drivers_cancel_before_exposing_new_client(self):
        for driver in ("ch390/CH390EthernetInterface", "RAK13800/RAK13800EthernetInterface"):
            source = (ROOT / f"src/helpers/ethernet/{driver}.cpp").read_text(encoding="utf-8")
            name = driver.split("/")[-1]
            loop = extract_braced(source, f"void {name}::loop()")
            with self.subTest(driver=name):
                close = extract_braced(source, f"void {name}::disconnectClient()")
                self.assertIn("_isConnected = false;", close)
                if name == "RAK13800EthernetInterface":
                    # The worker owns the physical socket until main commits
                    # the session transition and acknowledges its event. The
                    # logical route must remain unavailable during callbacks.
                    self.assertLess(loop.index("_isConnected = false;"),
                                    loop.index("onClientConnected("))
                    self.assertLess(loop.index("onClientConnected("),
                                    loop.index("_isConnected = true;"))
                    self.assertLess(loop.index("_isConnected = true;"),
                                    loop.index("_eventPending.store(false);"))
                    self.assertIn("_closeRequested.store(true);", close)
                    self.assertNotIn("client.stop();", close)
                else:
                    self.assertLess(loop.index("disconnectClient();"), loop.index("onClientConnected("))
                    self.assertLess(loop.index("onClientConnected((uint32_t)remoteIp);"),
                                    loop.index("client = newClient;"))
                    self.assertIn("client.stop();", close)


if __name__ == "__main__":
    unittest.main()
