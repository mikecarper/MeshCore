#!/usr/bin/env python3
"""Execute production room-browser callbacks with real JSON, stores and gates."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_room_history_store import STAT_MOCK

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test/fixtures/room_web_service"


class RoomWebServiceTest(unittest.TestCase):
    def test_actual_callbacks_with_real_arduinojson_and_stores(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        json_header = next((ROOT / ".pio/libdeps").glob("*/ArduinoJson/src/ArduinoJson.h"), None)
        self.assertIsNotNone(json_header, "actual ArduinoJson headers from the checkout are required")
        version = (json_header.parent / "ArduinoJson/version.hpp").read_text()
        self.assertRegex(version, r'ARDUINOJSON_VERSION "7\.4\.\d+"')
        source = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        header = (ROOT / "examples/simple_room_server/MyMesh.h").read_text()
        production = "\n".join(extract_braced(source, signature) for signature in (
            "bool MyMesh::handleRoomWebCommand(", "static const char* roomWebString(",
            "void MyMesh::processRoomRequest(", "void MyMesh::serviceRoomQuotas(",
            "bool MyMesh::storePost(", "bool MyMesh::snapshotRoomHistory(",
            "bool MyMesh::executeClientPathCommand(", "bool MyMesh::setRoomClientPath(",
            "bool MyMesh::applyRoomCatchUpCommand(", "bool MyMesh::saveFilter(",
            "void MyMesh::writeRoomClientJson(",
            "bool MyMesh::roomClientChatEnabled(", "uint8_t MyMesh::getUnsyncedCount(", "bool MyMesh::handleRoomHistoryCommand(",
            "bool MyMesh::handleRoomTopicCommand(", "void MyMesh::activateRoomTopic("))
        packet = (ROOT / "src/Packet.cpp").read_text()
        production = "namespace mesh {\n" + extract_braced(
            packet, "bool Packet::isValidPathLen(") + "\n}\n" + production
        utils = (ROOT / "src/Utils.cpp").read_text()
        production = "namespace mesh {\n" + "\n".join(extract_braced(utils, signature)
            for signature in ("static uint8_t hexVal(", "bool Utils::isHexChar(",
                              "bool Utils::fromHex(")) + "\n}\n" + production
        production = next(line for line in source.splitlines()
                          if line.startswith("#define LAZY_CONTACTS_WRITE_DELAY")) + "\n" + production
        acl = (ROOT / "src/helpers/ClientACL.h").read_text()
        acl_source = (ROOT / "src/helpers/ClientACL.cpp").read_text()
        production += "\n" + "\n".join(extract_braced(acl_source, signature).replace(
            "ClientACL::", "ACL::") for signature in (
                "ClientInfo* ClientACL::putClient(", "bool ClientACL::applyPermissions("))
        roles = "\n".join(line for line in acl.splitlines()
                          if line.startswith(("#define PERM_ACL_", "#define OUT_PATH_")))
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        # 64-bit host slots are twice ESP32's size. This reproduces the real
        # ESP32 default 1024-byte pool, while testing its unchanged 8192-byte
        # allocator budget and the actual ArduinoJson parser/temporary strings.
        with tempfile.TemporaryDirectory(prefix="meshcore-room-web-service-") as temporary:
            work = Path(temporary)
            (work / "sys").mkdir()
            (work / "sys/stat.h").write_text(STAT_MOCK)
            (work / "filesystem.h").write_text((ROOT / "test/fixtures/room_history_store/filesystem.h").read_text())
            (work / "production.inc").write_text(production)
            (work / "roles.inc").write_text(roles)
            (work / "client.inc").write_text(extract_braced(acl, "struct ClientInfo") + ";")
            (work / "post.inc").write_text(extract_braced(header, "struct PostInfo {") + ";")
            web_user = extract_braced(header, "struct RoomWebUser {")
            (work / "web_user.inc").write_text(web_user + " room_web_users[8] = {};")
            binary = work / "service"
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    "-Wno-misleading-indentation", *sanitizers, "-DESP32_PLATFORM=1",
                                    "-DARDUINOJSON_POOL_CAPACITY=64",
                                    "-I" + str(work), "-I" + str(ROOT / "src"), "-I" + str(json_header.parent),
                                    "-I" + str(ROOT / "test/mocks"),
                                    str(FIXTURES / "service.cpp"), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("actual room web service", checked.stdout)
            for group in ("ADMIN_AUTH", "ADMIN_ROUTES", "ADMIN_CATCHUP", "ADMIN_USERS",
                          "ADMIN_SETTINGS", "ADMIN_INVALID", "MAIL_AUTH", "MAIL_DELIVERY",
                          "MAIL_ADMIN", "MAIL_PAGING", "MAIL_STORAGE", "MAIL_GLOBAL"):
                self.assertRegex(checked.stdout, r"(?m)^" + group + ":")
            for kind in ("STATUS", "INDEX", "READ"):
                match = re.search(r"^BOUNDARY_" + kind + r" (.+)$", checked.stdout, re.M)
                self.assertIsNotNone(match)
                actual = json.loads(match.group(1))
                expected = json.loads((FIXTURES / "boundary.json").read_text())[kind.lower()]
                self.assertEqual(actual, expected)
            print(checked.stdout.splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
