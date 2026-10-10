#!/usr/bin/env python3
"""Run pinned stock JavaScript room handlers against real firmware output.

This witnesses the pinned public web app, not the installed phone UI. The
production room queue and Companion framing/login code are extracted without
edits. Dart runtime, SQLite, packet encryption and transports are boundaries.
No PlatformIO, hardware, app fork or persistent settings are involved.
"""
from pathlib import Path
import json
import re
import shutil
import tempfile
import unittest

from test_companion_delayed_reply_delivery import production_delayed_reply_inputs
from test_official_app_compatibility import checked, official_app_bundle, SANITIZERS
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test/fixtures/official_room_app_compatibility"


def room_outputs(work, compiler):
    source = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
    acl = (ROOT / "src/helpers/ClientACL.h").read_text()
    header = (ROOT / "examples/simple_room_server/MyMesh.h").read_text()
    state = "\n".join(line for line in acl.splitlines()
                      if line.startswith(("#define PERM_ACL_", "#define OUT_PATH_")))
    state += "\n" + extract_braced(acl, "struct ClientInfo") + ";\n"
    state += extract_braced(header, "struct PostInfo") + ";\n"
    clock = (ROOT / "src/MeshCore.h").read_text()
    state += "namespace mesh {\n" + extract_braced(clock, "class RTCClock") + ";\n}\n"
    (work / "state.inc").write_text(state, encoding="ascii")
    definitions = "\n".join(extract_braced(source, signature) for signature in (
        "bool MyMesh::pushPostToClient(", "bool MyMesh::pushRoomTextToClient(",
        "bool MyMesh::processAck(", "void MyMesh::activateRoomTopic(",
        "bool MyMesh::handleRoomTopicCommand(", "void MyMesh::serviceRoomPush("))
    (work / "production.inc").write_text(definitions, encoding="ascii")
    fixture = (ROOT / "test/fixtures/room_topic_delivery/test.cpp").read_text()
    fixture = fixture[:fixture.index("int main()")]
    signed_type = next(line for line in (ROOT / "src/helpers/TxtDataHelpers.h").read_text().splitlines()
                       if line.startswith("#define TXT_TYPE_SIGNED_PLAIN"))
    maximum = next(line for line in header.splitlines() if line.startswith("#define MAX_POST_TEXT_LEN"))
    fixture = re.sub(r"^#define TXT_TYPE_SIGNED_PLAIN .*", signed_type, fixture, flags=re.M)
    fixture = re.sub(r"^#define MAX_POST_TEXT_LEN .*", maximum, fixture, flags=re.M)
    (work / "room_delivery_fixture.inc").write_text(fixture, encoding="ascii")

    companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
    codes = "\n".join(line for line in companion.splitlines()
                      if line.startswith("#define RESP_CODE_CONTACT_MSG_RECV"))
    (work / "frame_codes.inc").write_text(codes, encoding="ascii")
    presentation = extract_braced(companion, "void MyMesh::queueMessage(")
    # The entire original framing prefix runs unchanged; the offline queue/UI
    # boundary is replaced by capture of those exact bytes, not a serializer.
    presentation = presentation[:presentation.index("  const char* sensitive_reply = text;")]
    presentation += "  (void)path_len;\n  frame.assign(out_frame, out_frame + i);\n}\n"
    presentation = presentation.replace("MyMesh::queueMessage", "WirePresentation::queueMessage")
    presentation = presentation.replace("const ContactInfo &from", "const RoomContact &from")
    presentation = presentation.replace("mesh::Packet *pkt", "CompanionPacket *pkt")
    (work / "presentation.inc").write_text(presentation, encoding="ascii")
    binary = work / "room-messages"
    checked([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
             "-Wno-unused-parameter", "-Wno-missing-field-initializers", *SANITIZERS,
             f"-I{work}", f"-I{ROOT / 'src'}", str(FIXTURES / "room_messages.cpp"), "-o", str(binary)])
    records = []
    for line in checked([str(binary)], timeout=20).splitlines():
        kind, group, index, version, flood, timestamp, author, text, frame = line.split(":")
        if kind != "POST":
            raise AssertionError(line)
        records.append({"group": group, "index": int(index), "version": int(version),
                        "flood": bool(int(flood)), "timestamp": int(timestamp),
                        "author": author, "text": text, "frame": frame})
    if len(records) != 13:
        raise AssertionError(records)
    return records


def login_binary(work, compiler):
    production_delayed_reply_inputs(work)
    fixture = (ROOT / "test/fixtures/companion_delayed_reply_delivery/test.cpp").read_text()
    (work / "companion_login_fixture.inc").write_text(
        fixture[:fixture.index("int main()")], encoding="ascii")
    binary = work / "room-login"
    checked([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-sign-compare", "-Wno-class-memaccess",
        *SANITIZERS, "-DCOMPANION_FEATURE_TEXT_TERMINAL=0", "-DMESH_ENABLE_ONE_KEY_DM=0",
        f"-I{work}", f"-I{ROOT / 'test/mocks'}",
        f"-I{ROOT / 'test/fixtures/serial_wifi_sessions/mocks'}", f"-I{ROOT / 'src'}",
        str(FIXTURES / "room_login.cpp"), *[str(ROOT / "src/helpers" / name) for name in (
            "CompanionDelayedReplies.cpp", "ArduinoSerialInterface.cpp",
            "wifi/SerialWifiInterface.cpp", "TxtDataHelpers.cpp")], "-o", str(binary)])
    return binary


class OfficialRoomAppCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not cls.node or not compiler:
            raise AssertionError("Node.js and a host C++ compiler are required for stock app room compatibility")
        cls.bundle = official_app_bundle()
        temporary = tempfile.TemporaryDirectory(prefix="official-room-app-")
        cls.addClassCleanup(temporary.cleanup)
        cls.work = Path(temporary.name)
        cls.posts = room_outputs(cls.work, compiler)
        cls.login = login_binary(cls.work, compiler)

    def app(self, mode, **values):
        config = self.work / (mode + ".json")
        config.write_text(json.dumps({"bundle": str(self.bundle), "mode": mode, **values}), encoding="ascii")
        return json.loads(checked([self.node, str(FIXTURES / "room_contract.js"), str(config)], timeout=20))

    def test_actual_app_room_decode_storage_and_maximum_topic(self):
        result = self.app("posts", posts=self.posts)
        self.assertEqual(result["post_cases"], 13)
        self.assertEqual(result["ordered_timestamps"], [100, 200, 1000, 1100])
        self.assertEqual(result["negative_controls"], 3)

    def test_actual_app_guest_admin_and_legacy_login(self):
        requests = self.app("build_logins")
        self.assertEqual([request["password"] for request in requests], ["", "admin", "legacy"])
        replies = []
        for format, request in enumerate(requests):
            output = checked([str(self.login), request["command"], str(format)], timeout=20)
            kind, sent, frame = output.strip().split(":")
            self.assertEqual(kind, "LOGIN")
            replies.append({**request, "sent": sent, "frame": frame,
                "admin": 1 if format == 1 else 0,
                "timestamp": None if format == 2 else 0x76543210,
                "permissions": None if format == 2 else (3 if format else 1),
                "firmware_level": None if format == 2 else 13})
        result = self.app("logins", logins=replies)
        self.assertEqual(result["login_cases"], 3)
        self.assertTrue(result["guest_and_admin_distinct"])
        self.assertTrue(result["wrong_server_ignored"])


if __name__ == "__main__":
    unittest.main()
