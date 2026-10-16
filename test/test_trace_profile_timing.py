#!/usr/bin/env python3
"""Execute trace producers, profile admission and complete wire-length timing."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/trace_profile_timing.cpp"


class TraceProfileTimingTests(unittest.TestCase):
    def run_fixture(self, mutation=None):
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        dispatcher = (ROOT / "src/Dispatcher.cpp").read_text()
        mesh = (ROOT / "src/Mesh.cpp").read_text()
        driver = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        methods = "namespace mesh {\n"
        for signature in (
            "Packet* Dispatcher::obtainNewPacket(",
            "bool Dispatcher::queueOutboundPacket(",
            "uint8_t Dispatcher::getTransmitProfileMask(",
            "uint32_t Dispatcher::getTransmitAirtime(const Packet* packet) const",
            "uint32_t Dispatcher::getTransmitAirtime(const Packet* packet, int wire_length) const",
            "bool Dispatcher::sendPacket(",
        ):
            methods += extract_braced(dispatcher, signature) + "\n"
        for signature in ("Packet* Mesh::createTrace(", "bool Mesh::sendDirect("):
            methods += extract_braced(mesh, signature) + "\n"
        methods += "}\n" + extract_braced(driver, "uint32_t RadioLibWrapper::getProfileAirtime(") + "\n"
        terminal = extract_braced(companion, "void MyMesh::sendTerminalTraceRoute(")
        binary = extract_braced(companion, "} else if (cmd_frame[0] == CMD_SEND_TRACE_PATH")
        if mutation:
            terminal, binary = mutation(terminal, binary)
        methods += extract_braced(companion, "uint32_t MyMesh::calcDirectTimeoutMillisFor(") + "\n"
        methods += terminal + "\nvoid MyMesh::handleTraceFrame(size_t len) {\n"
        methods += binary.replace("} else if", "if", 1) + "\n}\n"
        constants = "\n".join(re.findall(
            r"^#define (?:CMD_SEND_TRACE_PATH|ERR_CODE_BAD_STATE|ERR_CODE_ILLEGAL_ARG|ERR_CODE_TABLE_FULL|SEND_TIMEOUT_BASE_MILLIS|DIRECT_SEND_PERHOP_FACTOR|DIRECT_SEND_PERHOP_EXTRA_MILLIS)\s+.+$",
            companion, re.MULTILINE))
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        cache = Path.home() / ".cache"
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="trace-profile-timing-", dir=cache) as directory:
            work = Path(directory)
            (work / "production.inc").write_text(methods, encoding="ascii")
            (work / "constants.inc").write_text(constants, encoding="ascii")
            binary_path = work / "trace"
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-sign-compare", *sanitizers, "-I" + str(work),
                "-I" + str(ROOT / "src"), "-I" + str(ROOT / "test/mocks"),
                str(FIXTURE), str(ROOT / "src/Packet.cpp"),
                str(ROOT / "src/helpers/CompanionDelayedReplies.cpp"), "-o", str(binary_path)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            return subprocess.run([str(binary_path)], capture_output=True, text=True, timeout=20)

    def test_actual_trace_producers_mask_admission_and_complete_wire_length(self):
        result = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: trace timing", result.stdout)

    def test_legacy_current_radio_estimates_fail_each_producer(self):
        for terminal in (False, True):
            def legacy(text, framed):
                selected = text if terminal else framed
                selected, count = re.subn(
                    r"getTransmitAirtime\((?:packet|pkt),\s*2 \+ (?:packet|pkt)->payload_len \+ (route_byte_len|path_len)\)",
                    lambda match: "_radio->getEstAirtimeFor(11 + " + match[1] + ")", selected)
                self.assertEqual(count, 1)
                return (selected, framed) if terminal else (text, selected)
            with self.subTest(terminal=terminal):
                result = self.run_fixture(legacy)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("trace timeout must follow selected profiles", result.stderr)

    def test_unset_route_length_and_omitted_appended_bytes_fail_each_producer(self):
        for terminal in (False, True):
            for route_bytes in (True, False):
                def wrong_length(text, framed):
                    selected = text if terminal else framed
                    selected, count = re.subn(
                        r"2 \+ ((?:packet|pkt))->payload_len \+ (route_byte_len|path_len)",
                        lambda match: match[1] + "->getRawLength()"
                            + (" + " + match[2] if route_bytes else ""), selected)
                    self.assertEqual(count, 1)
                    return (selected, framed) if terminal else (text, selected)
                with self.subTest(terminal=terminal, route_bytes=route_bytes):
                    result = self.run_fixture(wrong_length)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("trace timing must use final serialized wire length", result.stderr)


if __name__ == "__main__":
    unittest.main()
