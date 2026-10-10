#!/usr/bin/env python3
"""Execute actual room history admission, commands and boot loading with the real store."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_room_history_store import STAT_MOCK

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/room_history_integration/test.cpp"


class RoomHistoryIntegrationTests(unittest.TestCase):
    def test_actual_post_admission_persistence_commands_and_boot(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        source = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        header = (ROOT / "examples/simple_room_server/MyMesh.h").read_text()
        acl = (ROOT / "src/helpers/ClientACL.h").read_text()
        text = (ROOT / "src/helpers/TxtDataHelpers.cpp").read_text()
        state = "\n".join(line for line in acl.splitlines() if line.startswith("#define PERM_ACL_"))
        state += "\n" + extract_braced(header, "struct PostInfo") + ";\n"
        definitions = "\n".join(extract_braced(source, signature) for signature in (
            "bool MyMesh::addPost(", "bool MyMesh::addSystemPost(", "bool MyMesh::storePost(",
            "bool MyMesh::snapshotRoomHistory(", "void MyMesh::loadRoomHistory(",
            "bool MyMesh::handleRoomHistoryCommand("))
        definitions += "\n" + extract_braced(text, "void StrHelper::strncpy(")
        receive = extract_braced(source, "void MyMesh::onPeerDataRecv(")
        gate = extract_braced(receive, "if (flags == TXT_TYPE_PLAIN)")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="room-history-integration-") as directory:
            work = Path(directory)
            (work / "sys").mkdir()
            (work / "sys/stat.h").write_text(STAT_MOCK)
            (work / "state.inc").write_text(state)
            (work / "production.inc").write_text(definitions)
            (work / "post_gate.inc").write_text(gate)
            for platform in (None, "NRF52_PLATFORM", "STM32_PLATFORM", "RP2040_PLATFORM", "ESP32_PLATFORM"):
                with self.subTest(platform=platform):
                    binary = work / (platform or "generic")
                    defines = ["-D" + platform + "=1"] if platform else []
                    built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", *sanitizers, *defines, f"-I{work}", f"-I{ROOT / 'src'}",
                        f"-I{ROOT / 'test/mocks'}", str(FIXTURE), "-o", str(binary)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
                    self.assertIn("production room history integration scenarios passed", tested.stdout)
                    print((platform or "generic") + ": " + tested.stdout.strip())


if __name__ == "__main__":
    unittest.main()
