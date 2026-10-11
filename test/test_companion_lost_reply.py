#!/usr/bin/env python3
"""Exercise actual Companion private receive, reply send and local CLI paths."""

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
FIXTURE = ROOT / "test/fixtures/companion_lost_reply.cpp"
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])


class CompanionLostReplyTests(unittest.TestCase):
    def test_actual_private_callbacks_reply_transport_and_local_config(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++ compiler is required")
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        base = (ROOT / "src/helpers/BaseChatMesh.cpp").read_text()
        methods = "#if MESH_ENABLE_LOST_REPLY\n" + extract_braced(
            source, "void MyMesh::maybeReplyToLostQuestion(") + "\n#endif\n"
        methods += "\n".join(extract_braced(source, signature) for signature in (
            "void MyMesh::onMessageRecv(",
            "void MyMesh::onCLICommandRecv(", "bool MyMesh::onSignedMessageRecv(",
            "void MyMesh::onChannelMessageRecv(", "void MyMesh::clearExpectedAck(",
            "void MyMesh::expireExpectedAcks(", "bool MyMesh::processAck(",
        ))
        methods += "\n" + "\n".join(extract_braced(base, signature).replace(
            "BaseChatMesh::", "MyMesh::") for signature in (
                "mesh::Packet* BaseChatMesh::composeMsgPacket(",
                "int BaseChatMesh::sendMessage(",
                "int BaseChatMesh::sendMessageDetached(",
                "void BaseChatMesh::onAckRecv(",
                "bool BaseChatMesh::onContactPathRecv(",
        ))
        direct = extract_braced(source, "bool MyMesh::handleDirectCommand(")
        direct_prefix = direct[:direct.index("#ifdef MESH_BUTTON_AUDIO_HIL")]
        methods += "\n" + direct_prefix + "  return false;\n}\n"
        guard = re.search(r"if \(sender_timestamp == 0 && handleDirectCommand\([^\n]+", source)
        self.assertIsNotNone(guard)
        methods += "\nbool MyMesh::handleCommand(const char* command, uint32_t sender_timestamp, char* reply) {\n"
        methods += "  const size_t reply_capacity = 160;\n  " + guard.group()
        methods += '\n  return false;\n}\n'
        snapshot = extract_braced(source, "void MyMesh::getNodeSnapshot(")
        saved = re.search(r"#if MESH_ENABLE_LOST_REPLY\n  s\.lost_reply[\s\S]*?#endif", snapshot)
        capability = re.search(r"#if MESH_ENABLE_LOST_REPLY\n  s\.capabilities \|=[\s\S]*?#endif", snapshot)
        self.assertIsNotNone(saved)
        self.assertIsNotNone(capability)
        methods += "\nvoid MyMesh::snapshot(LostSnapshot& s) {\n" + saved.group() + "\n" + capability.group() + "\n}\n"
        include = re.search(r"#if MESH_ENABLE_LOST_REPLY\n#include <helpers/CompanionLostReply.h>\n#endif", header)
        state = re.search(r"#if MESH_ENABLE_LOST_REPLY\n  mesh::companion::LostReplyLimiter[\s\S]*?#endif", header)
        self.assertIsNotNone(include)
        self.assertIsNotNone(state)
        constants = "\n".join(re.findall(
            r"^#define (?:EXPECTED_ACK_[A-Z_]+|PUSH_CODE_SEND_CONFIRMED|RESP_CODE_CHANNEL_MSG_RECV(?:_V3)?|PUSH_CODE_MSG_WAITING|EMERGENCY_CLIENT_REPEAT_[A-Z_]+)\s+.+$",
            source, re.MULTILINE))
        with tempfile.TemporaryDirectory(prefix="meshcore-lost-reply-") as directory:
            work = Path(directory)
            (work / "production_constants.inc").write_text(constants, encoding="ascii")
            (work / "production_entry.inc").write_text(
                extract_braced(header, "struct AckTableEntry") + ";\n", encoding="ascii")
            (work / "production_methods.inc").write_text(methods, encoding="ascii")
            (work / "production_lost_include.inc").write_text(include.group(), encoding="ascii")
            (work / "production_lost_state.inc").write_text(state.group(), encoding="ascii")
            variants = ((0, 0, 0, []), (0, 0, 1, []), (1, 0, 0, []), (1, 1, 0, []),
                        (0, 0, 0, ["-DSTM32_PLATFORM=1", "-DMESH_ENABLE_LOST_REPLY=1"]),
                        (0, 0, 0, ["-DSTM32_PLATFORM=1"]),
                        (0, 0, 0, ["-DMESH_ENABLE_LOST_REPLY=0"]))
            for number, (one_key, shared, terminal, feature_flags) in enumerate(variants):
                with self.subTest(one_key=one_key, shared=shared, terminal=terminal, flags=feature_flags):
                    binary = work / f"lost-{number}.exe"
                    compiled = subprocess.run([
                        compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", "-Wno-unused-variable", *SANITIZERS,
                        f"-DMESH_ENABLE_ONE_KEY_DM={one_key}",
                        f"-DONE_KEY_DM_SHARED_OFFLINE_QUEUE={shared}",
                        *(["-DENABLE_USB_INTERFACE=1"] if terminal else []),
                        *feature_flags,
                        "-DCOMPANION_FEATURE_NOTIFICATIONS=0",
                        f"-I{work}", f"-I{ROOT / 'src'}",
                        f"-I{ROOT / 'examples/companion_radio'}", str(FIXTURE), "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("PASS: Companion lost reply production paths", checked.stdout)


if __name__ == "__main__":
    unittest.main()
