#!/usr/bin/env python3
"""Exercise production USB framing and immediate replies under backpressure."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/arduino_serial_backpressure/test.cpp"
TRANSPORT = ROOT / "src/helpers/ArduinoSerialInterface.cpp"
SANITIZERS = (
    ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
    if sys.platform.startswith("linux") else []
)
SCENARIOS = (
    "five-command burst retains fifth command until required reply fits",
    "partial headers and payloads preserve timeout budget across rollover",
    "every RX split resumes intact without consuming the following command",
    "only unsent best-effort frames provide replacement reply capacity",
    "terminal and mOTA control tokens preserve their following input",
    "session, passthrough and flow-control resets clear backpressure state",
    "full TX queue cannot revive an already expired inbound frame",
    "blocked USB input does not stop commands from another transport",
)


class ArduinoSerialBackpressureTests(unittest.TestCase):
    def setUp(self):
        self.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if self.compiler is None:
            self.skipTest("a host C++17 compiler is required")

    def build(self, directory, transport):
        directory = Path(directory)
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        methods = "\n".join(extract_braced(mesh, signature) for signature in (
            "void MyMesh::writeOKFrame(", "void MyMesh::writeErrFrame(",
        ))
        (directory / "immediate_reply_methods.inc").write_text(methods)
        candidate = directory / "ArduinoSerialInterface.cpp"
        candidate.write_text(transport)
        binary = directory / "serial-backpressure"
        built = subprocess.run([
            self.compiler, "-std=c++17", *SANITIZERS,
            "-I" + str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
            "-I" + str(ROOT / "src/helpers"), "-I" + str(directory),
            str(FIXTURE), str(candidate), "-o", str(binary),
        ], capture_output=True, text=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        return binary

    def run_scenario(self, binary, scenario):
        return subprocess.run([str(binary), str(scenario)], capture_output=True,
                              text=True, timeout=15)

    def test_production_parser_queue_and_immediate_reply_methods(self):
        with tempfile.TemporaryDirectory(prefix="meshcore-serial-backpressure-") as directory:
            binary = self.build(directory, TRANSPORT.read_text())
            for scenario, description in enumerate(SCENARIOS):
                with self.subTest(scenario=description):
                    checked = self.run_scenario(binary, scenario)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn(f"PASS: Arduino serial backpressure scenario {scenario}",
                                  checked.stdout)

    def test_negative_controls_reproduce_lost_reply_and_partial_frame(self):
        source = TRANSPORT.read_text()
        controls = (
            ("  if (!responseCapacityAvailable()) return 0;\n", "", 0),
            ("      && !_receive_capacity_paused\n", "", 1),
        )
        for old, new, scenario in controls:
            with self.subTest(removed=old.strip()):
                self.assertEqual(source.count(old), 1)
                with tempfile.TemporaryDirectory(prefix="meshcore-serial-negative-") as directory:
                    binary = self.build(directory, source.replace(old, new))
                    checked = self.run_scenario(binary, scenario)
                    self.assertNotEqual(checked.returncode, 0,
                                        "negative control unexpectedly passed")
                    self.assertIn("FAIL line", checked.stderr)


if __name__ == "__main__":
    unittest.main()
