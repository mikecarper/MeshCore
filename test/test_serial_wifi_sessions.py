#!/usr/bin/env python3
"""Exercise production Wi-Fi transport reconnect ownership and partial writes."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/serial_wifi_sessions"
# MinGW does not ship these runtimes. Non-PIE avoids ASan address-space
# collisions in Linux hosts, matching the other native regression harnesses.
SANITIZER_FLAGS = (
    ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
    if sys.platform.startswith("linux") else []
)

SCENARIOS = (
    "same IP live replacement keeps replies",
    "same IP reconnect after disconnect keeps replies",
    "different IP receives only its own replies",
    "old IP returns before grace deadline and swaps queues",
    "old IP expires at exactly 30 seconds",
    "same IP reconnect does not extend another IP grace deadline",
    "old IP returns before deadline across millis rollover",
    "old IP expires across millis rollover",
    "third IP evicts older parked backlog",
    "partial writes retain every byte",
    "higher priority reply cannot interrupt partial frame",
    "same IP reconnect replays whole partial frame",
    "different IP return replays whole partial frame",
    "disable clears both queues and active connection",
    "end clears both queues and active connection",
    "different IP callback precedes new input and resets partial RX",
    "same IP preserves pending operations but resets partial RX",
    "zero-byte write retains pending frame",
    "both bounded queues retain four complete responses",
    "empty different IP takeover preserves existing held backlog",
    "empty takeover does not extend expiry and parked data does not busy-loop",
    "full queue admission cannot evict a partly sent frame",
    "disable cancels owner once and resets partial RX state",
    "maximum-sized partly sent frame stays intact before higher priority response",
    "maximum-sized partial frame and parked queue survive owner swap",
    "oversized header disconnects immediately without reading its body",
    "invalid type disconnects without discard loop or tail dispatch",
    "zero-length header disconnects without tail dispatch",
    "incomplete payload disconnects after bounded deadline",
    "incomplete header disconnects after bounded deadline",
    "short payload read never dispatches a truncated command",
    "short header read disconnects and resets framing",
    "fragmented legal frame completes before deadline",
    "maximum-sized and consecutive legal frames parse intact",
    "incomplete payload deadline survives millis rollover",
    "physical disconnect cancels once even with incomplete input and queued reply",
)


class SerialWifiSessionTests(unittest.TestCase):
    def test_production_transport_sessions(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-wifi-sessions-") as directory:
            binary = Path(directory) / "wifi-sessions.exe"
            compiled = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 *SANITIZER_FLAGS,
                 f"-I{FIXTURE / 'mocks'}", f"-I{ROOT / 'src'}",
                 str(FIXTURE / "test.cpp"),
                 str(ROOT / "src/helpers/esp32/SerialWifiInterface.cpp"),
                 "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            for scenario, description in enumerate(SCENARIOS):
                with self.subTest(scenario=description):
                    checked = subprocess.run(
                        [str(binary), str(scenario)], capture_output=True,
                        text=True, timeout=10,
                    )
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn(f"PASS: Wi-Fi session scenario {scenario}", checked.stdout)


if __name__ == "__main__":
    unittest.main()
