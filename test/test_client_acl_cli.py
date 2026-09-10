#!/usr/bin/env python3
"""Execute the real ACL page handler, ACL storage, and role dispatch branches."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/client_acl_cli"
ACL_MOCKS = ROOT / "test/fixtures/client_acl_spiffs/mocks"
ROLES = {
    "repeater": ROOT / "examples/simple_repeater/MyMesh.cpp",
    "room": ROOT / "examples/simple_room_server/MyMesh.cpp",
    "sensor": ROOT / "examples/simple_sensor/SensorMesh.cpp",
}


def role_handler(role, source):
    # Compile the actual timestamp normalization, prefix handling, manager
    # guard, and both ACL branches. Unrelated radio/board handlers are omitted.
    owner = "SensorMesh" if role == "sensor" else "MyMesh"
    body = extract_braced(source, f"void {owner}::handleCommand(uint32_t sender_timestamp,")
    zero_guard = re.search(r"if \([^\n]+sender_timestamp == 0\) sender_timestamp = 1;", body).group()
    prefix = extract_braced(body, "if (strlen(command) > 4 && command[2] == '|')")
    paged = extract_braced(body, "if (mesh::cli::handleACLGet(")
    local = extract_braced(body, 'if (sender_timestamp == 0 && strcmp(command, "get acl") == 0)')
    # The room source opens its next conditional branch's preprocessor guard
    # immediately before the closing brace of the local ACL branch.
    local = re.sub(r"\n#if defined\(WITH_MQTT_NEIGHBORS\)\n\s*}$", "\n}", local)
    guard = ""
    if role == "repeater":
        guard_start = body.rindex("if (sender && !sender->isAdmin())", 0,
                                 body.index("mesh::cli::handleACLGet("))
        guard = extract_braced(body[guard_start:], "if (sender && !sender->isAdmin())")
    return f"""
static void {role}Command(ClientACL& acl, ClientInfo* sender,
                          uint32_t sender_timestamp, char* command, char* reply) {{
  const int gpio_client_index = sender == nullptr ? -1 : 0;
  (void)gpio_client_index;
  {zero_guard}
  while (*command == ' ') ++command;
  {prefix}
  mesh::cli::normalizeCommandVerb(command);
  {guard}
  {paged} else {local} else strcpy(reply, "unhandled");
}}
"""


class ClientAclCliTest(unittest.TestCase):
    def test_executable_pages_and_role_dispatch(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        sources = {role: path.read_text() for role, path in ROLES.items()}
        utils = (ROOT / "src/Utils.cpp").read_text()
        hex_chars = re.search(r"static const char hex_chars\[\].*;", utils).group()
        generated = "namespace mesh {\n" + hex_chars + "\n"
        generated += extract_braced(utils, "void Utils::toHex(") + "\n"
        generated += extract_braced(utils, "void Utils::printHex(") + "\n}\n"
        for signature in (
            "static bool commandFamilyMatches(", "static bool isCommonManagerReadOnlyAllowed(",
            "static bool isRegionMgrAllowed(", "static bool isFilterMgrAllowed(",
        ):
            generated += extract_braced(sources["repeater"], signature) + "\n"
        for role, source in sources.items():
            generated += role_handler(role, source)
        with tempfile.TemporaryDirectory(prefix=".tmp-acl-cli-", dir=ROOT) as directory:
            work = Path(directory)
            # Only declarations are mocked; both hex conversion definitions
            # above come from production Utils.cpp, with the real hex alphabet.
            (work / "Utils.h").write_text("""#pragma once
#include <stddef.h>
#include <stdint.h>
class Stream;
namespace mesh { class Utils { public:
  static void toHex(char*, const uint8_t*, size_t);
  static void printHex(Stream&, const uint8_t*, size_t);
}; }
""")
            (work / "production.h").write_text(generated)
            for clients in (32, 256):
                with self.subTest(max_clients=clients):
                    binary = work / f"acl-cli-{clients}"
                    result = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-DESP32=1",
                        "-DESP32_PLATFORM=1", "-DMESH_ENABLE_FLOOD_RULE_ENGINE=1",
                        f"-DMAX_CLIENTS={clients}", f"-I{work}", f"-I{ACL_MOCKS}",
                        f"-I{ROOT / 'src'}", str(FIXTURE / "test_client_acl_cli.cpp"),
                        "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("9 ACL CLI checks passed", checked.stdout)

    def test_remote_dispatch_stays_behind_existing_admin_guards(self):
        for role, path in ROLES.items():
            with self.subTest(role=role):
                source = path.read_text()
                handler = extract_braced(source, f"void {'SensorMesh' if role == 'sensor' else 'MyMesh'}::handleCommand(uint32_t sender_timestamp,")
                self.assertLess(handler.index("mesh::cli::handleACLGet("),
                                handler.index('if (sender_timestamp == 0 && strcmp(command, "get acl") == 0)'))
                self.assertIn("reply, 160 - 3,", handler)
                if role == "repeater":
                    self.assertLess(handler.index("if (sender && !sender->isAdmin())"),
                                    handler.index("mesh::cli::handleACLGet("))
                elif role == "room":
                    receiver = extract_braced(source, "void MyMesh::onPeerDataRecv(")
                    admin = extract_braced(receiver, "if (client->isAdmin())")
                    self.assertIn("handleCommand(sender_timestamp,", admin)
                else:
                    admin = extract_braced(source, "if (type == PAYLOAD_TYPE_TXT_MSG && len > 5 && from->isAdmin())")
                    self.assertIn("handleCommand(sender_timestamp,", admin)


if __name__ == "__main__":
    unittest.main()
