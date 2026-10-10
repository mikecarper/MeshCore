#!/usr/bin/env python3
"""Execute production room push/ACK/CLI code against loss and catch-up cases."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/room_topic_delivery/test.cpp"


class RoomTopicDeliveryTests(unittest.TestCase):
    def test_production_delivery_and_commands(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        source = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        acl = (ROOT / "src/helpers/ClientACL.h").read_text()
        header = (ROOT / "examples/simple_room_server/MyMesh.h").read_text()
        generated = "\n".join(line for line in acl.splitlines()
                              if line.startswith(("#define PERM_ACL_", "#define OUT_PATH_")))
        generated += "\n" + extract_braced(acl, "struct ClientInfo") + ";\n"
        generated += extract_braced(header, "struct PostInfo") + ";\n"
        clock = (ROOT / "src/MeshCore.h").read_text()
        generated += "namespace mesh {\n" + extract_braced(clock, "class RTCClock") + ";\n}\n"
        definitions = "\n".join(extract_braced(source, signature) for signature in (
            "bool MyMesh::pushPostToClient(", "bool MyMesh::pushRoomTextToClient(",
            "bool MyMesh::processAck(", "void MyMesh::activateRoomTopic(",
            "bool MyMesh::handleRoomTopicCommand(", "void MyMesh::serviceRoomPush("))
        with tempfile.TemporaryDirectory(prefix="room-topic-delivery-") as directory:
            work = Path(directory)
            (work / "state.inc").write_text(generated)
            (work / "production.inc").write_text(definitions)
            binary = work / "delivery"
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                       "-Wno-unused-parameter", "-I" + str(work),
                       str(FIXTURE), "-o", str(binary)]
            if sys.platform.startswith("linux"):
                command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                "-fno-pie", "-no-pie"]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
            self.assertIn("room topic delivery regressions passed", tested.stdout)


if __name__ == "__main__":
    unittest.main()
