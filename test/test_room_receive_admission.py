#!/usr/bin/env python3
"""Execute room reception, inbox admission and notifications under sanitizers."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/room_receive_admission/test.cpp"
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])


class RoomReceiveAdmissionTests(unittest.TestCase):
    def test_actual_receive_queue_retry_and_notifications(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        base = (ROOT / "src/helpers/BaseChatMesh.cpp").read_text()
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        receive = extract_braced(base, "void BaseChatMesh::onPeerDataRecv(")
        queue = extract_braced(source, "bool MyMesh::queueMessage(")
        common = "\n".join(extract_braced(source, signature) for signature in (
            "bool MyMesh::Frame::isChannelMsg(", "int MyMesh::getOfflineQueueCapacity(",
            "MyMesh::Frame& MyMesh::offlineQueueFrameAt(", "bool MyMesh::addToOfflineQueue(",
            "void MyMesh::popOfflineQueue(", "bool MyMesh::onSignedMessageRecv(",
            "void MyMesh::onMessageRecv("))
        constants = "\n".join(re.findall(
            r"^#define (?:RESP_CODE_(?:CONTACT_MSG_RECV(?:_V3)?|CHANNEL_MSG_RECV(?:_V3)?|CHANNEL_DATA_RECV)|PUSH_CODE_MSG_WAITING)\s+.+$",
            source, re.M))
        constants += "\n" + "\n".join(line for line in (
            ROOT / "src/helpers/TxtDataHelpers.h").read_text().splitlines()
            if line.startswith("#define TXT_TYPE_"))
        rejected = extract_braced(receive, "if (!onSignedMessageRecv(")
        bypass = "(void)previous_sync_since; (void)previous_lastmod;\n" + \
            "onSignedMessageRecv(from, packet, sender_timestamp, &data[5], (const char *) &data[9]);"
        variants = (
            ("production", receive, queue, True),
            ("ack-without-admission", receive.replace(rejected, bypass), queue, False),
            ("notify-without-admission", receive, queue.replace(
                "if (txt_type == TXT_TYPE_SIGNED_PLAIN && !queued) return false;", ""), False),
        )
        with tempfile.TemporaryDirectory(prefix="room-receive-admission-") as directory:
            work = Path(directory)
            (work / "frame.inc").write_text(extract_braced(header, "struct Frame") + ";\n", encoding="ascii")
            (work / "constants.inc").write_text(constants, encoding="ascii")
            for name, actual_receive, actual_queue, passes in variants:
                with self.subTest(variant=name):
                    (work / "production.inc").write_text(
                        actual_receive + "\n" + actual_queue + "\n" + common, encoding="ascii")
                    binary = work / name
                    built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", *SANITIZERS, f"-I{work}", f"-I{ROOT / 'src'}",
                        str(FIXTURE), "-o", str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    if passes:
                        self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
                        self.assertIn("PASS: production room receive admission", tested.stdout)
                    else:
                        self.assertNotEqual(tested.returncode, 0, "negative control escaped the delivery checks")
                        self.assertIn("Assertion", tested.stderr)


if __name__ == "__main__":
    unittest.main()
