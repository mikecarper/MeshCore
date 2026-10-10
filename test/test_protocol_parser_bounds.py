#!/usr/bin/env python3
"""Run the production advert parser and anonymous repeater handlers under ASan/UBSan.

Radio/allocator boundaries are doubles. Path decoding, all three anonymous
handlers, their receive dispatcher, and the advert implementation are real.
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/protocol_parser_bounds/test.cpp"
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])


class ProtocolParserBoundsTests(unittest.TestCase):
    def test_production_parsers_with_exact_buffers_and_all_bridge_modes(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        repeater = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        packet = (ROOT / "src/Packet.cpp").read_text()
        identity = (ROOT / "src/Identity.cpp").read_text()
        handlers = "\n".join(extract_braced(repeater, signature) for signature in (
            "static bool hasCompleteAnonReplyPath(",
            "uint8_t MyMesh::handleAnonRegionsReq(",
            "uint8_t MyMesh::handleAnonOwnerReq(",
            "uint8_t MyMesh::handleAnonClockReq(",
            "void MyMesh::onAnonDataRecv(",
        ))
        constructors = "namespace mesh {\n" + "\n".join(
            extract_braced(packet, signature) for signature in (
                "Packet::Packet()", "bool Packet::isValidPathLen(",
                "size_t Packet::writePath("))
        constructors += "\n" + extract_braced(identity, "Identity::Identity()") + "\n}\n"
        constants = "\n".join(re.findall(
            r"^#define ANON_REQ_TYPE_(?:REGIONS|OWNER|BASIC)\s+.+$",
            repeater, re.MULTILINE))
        constants += "\n" + re.search(
            r"static constexpr size_t MAX_ANON_REPLY_LEN =[^;]+;", repeater).group()
        constants += "\n" + re.search(
            r"static constexpr uint32_t LOGIN_PATH_OBSERVATION_TIMEOUT_MS =[^;]+;",
            repeater).group()
        constants += "\n" + re.search(
            r"^\s*#define SERVER_RESPONSE_DELAY\s+.+$", repeater, re.MULTILINE).group()
        constants += "\n" + re.search(
            r"^#define OUT_PATH_UNKNOWN\s+.+$",
            (ROOT / "src/helpers/ContactInfo.h").read_text(), re.MULTILINE).group()
        constants += "\n" + re.search(
            r"^#define REGION_DENY_FLOOD\s+.+$",
            (ROOT / "src/helpers/RegionMap.h").read_text(), re.MULTILINE).group()

        with tempfile.TemporaryDirectory(prefix="meshcore-parser-bounds-") as directory:
            work = Path(directory)
            (work / "production_handlers.inc").write_text(handlers)
            (work / "production_packet.inc").write_text(constructors)
            (work / "production_constants.inc").write_text(constants)
            variants = (("ordinary", []), ("rs232", ["-DWITH_RS232_BRIDGE=1"]),
                        ("espnow", ["-DWITH_ESPNOW_BRIDGE=1"]),
                        ("both", ["-DWITH_RS232_BRIDGE=1", "-DWITH_ESPNOW_BRIDGE=1"]))
            for name, defines in variants:
                with self.subTest(bridge=name):
                    binary = work / f"bounds-{name}"
                    built = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", "-Wno-reorder", "-Wno-sign-compare",
                        *SANITIZERS, *defines, f"-I{work}", f"-I{ROOT / 'src'}",
                        f"-I{ROOT / 'test/mocks'}", f"-I{ROOT}",
                        str(FIXTURE), str(ROOT / "src/helpers/AdvertDataHelpers.cpp"),
                        "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("PASS: production protocol parser bounds", checked.stdout)


if __name__ == "__main__":
    unittest.main()
