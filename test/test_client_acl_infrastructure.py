#!/usr/bin/env python3
"""Execute Room/Sensor ACL handlers and encrypted packet assembly with bounds."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_client_acl_response import parse_acl

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/client_acl_infrastructure/test_client_acl_infrastructure.cpp"


def production_methods(role, transform=None):
    sensor = role == "sensor"
    path = ROOT / ("examples/simple_sensor/SensorMesh.cpp" if sensor
                   else "examples/simple_room_server/MyMesh.cpp")
    source = path.read_text()
    if transform:
        source = transform(source)
    name = "SensorMesh" if sensor else "MyMesh"
    handler = extract_braced(source, ("uint8_t " if sensor else "int ") + name + "::handleRequest(")
    begin = handler.index("  if (req_type == REQ_TYPE_GET_TELEMETRY_DATA" if sensor
                          else "  if (payload[0] == REQ_TYPE_GET_STATUS)")
    prefix = handler[:begin]
    # The new board protocol has its own admission/storage regression suite.
    # Keep this legacy ACL/telemetry fixture on its original request families.
    if not sensor and "if (payload[0] == mesh::ROOM_BOARD_REQUEST_SUBTYPE)" in prefix:
        board = extract_braced(prefix, "if (payload[0] == mesh::ROOM_BOARD_REQUEST_SUBTYPE)")
        prefix = prefix.replace(board, "", 1)
    acl = extract_braced(handler, "if (req_type == REQ_TYPE_GET_ACCESS_LIST" if sensor
                         else "if (payload[0] == REQ_TYPE_GET_ACCESS_LIST")
    generated = prefix + ("  (void)perms;\n" if sensor else "") + acl + "\nreturn 0;\n}\n"
    signature = "if (type == PAYLOAD_TYPE_REQ) {  // request" if sensor else "if (type == PAYLOAD_TYPE_REQ && len >= 5)"
    receive = extract_braced(source, signature)
    generated += "void " + name + "::receive(mesh::Packet* packet, uint8_t* data, size_t len) {\n"
    generated += "ClientInfo* " + ("from" if sensor else "client") + " = &sender;\n"
    generated += "const uint8_t type = PAYLOAD_TYPE_REQ; uint8_t secret[PUB_KEY_SIZE] = {};\n" + receive + "\n}\n"
    if not sensor:
        generated += extract_braced(source, "bool MyMesh::saveFilter(") + "\n"
        telemetry = extract_braced(handler, "if (payload[0] == REQ_TYPE_GET_TELEMETRY_DATA)")
        telemetry_prefix = telemetry[:telemetry.index("    telemetry.reset();")]
        generated += "int MyMesh::telemetryPrefix(uint8_t* payload, size_t payload_len) {\n"
        generated += "ClientInfo* sender = &this->sender; uint32_t sender_timestamp = 51;\n"
        generated += "size_t reply_capacity = mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY;\n"
        generated += prefix[prefix.index("{") + 1:] + telemetry_prefix + "return perm_mask;\n}\nreturn 0;\n}\n"
    if sensor:
        generated += extract_braced(source, "bool SensorMesh::sendFloodScoped(") + "\n"
        generated += extract_braced(source, "bool SensorMesh::sendFloodReply(") + "\n"
    mesh_source = (ROOT / "src/Mesh.cpp").read_text()
    generated += "namespace mesh {\n#define MAX_COMBINED_PATH (MAX_PACKET_PAYLOAD - 2 - CIPHER_BLOCK_SIZE)\n"
    for signature in ("Packet* Mesh::createPathReturn(const Identity& dest,",
                      "Packet* Mesh::createPathReturn(const uint8_t* dest_hash,",
                      "Packet* Mesh::createDatagram(", "bool Mesh::sendFlood(Packet* packet, uint32_t",
                      "bool Mesh::sendFlood(Packet* packet, uint16_t*", "bool Mesh::sendDirect("):
        generated += extract_braced(mesh_source, signature) + "\n"
    packet_source = (ROOT / "src/Packet.cpp").read_text()
    for signature in ("Packet::Packet()", "bool Packet::isValidPathLen(", "uint8_t Packet::copyPath(", "size_t Packet::writePath("):
        generated += extract_braced(packet_source, signature) + "\n"
    return generated + "}\n"


class ClientAclInfrastructureTest(unittest.TestCase):
    def execute(self, role, transform=None):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++17 compiler required")
        with tempfile.TemporaryDirectory(prefix="infrastructure-acl-") as directory:
            work = Path(directory)
            (work / "production.inc").write_text(production_methods(role, transform), encoding="ascii")
            binary = work / "acl.exe"
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                       "-I" + str(work), "-I" + str(ROOT / "test/fixtures/room_history_store"), "-I" + str(ROOT / "src"), str(FIXTURE), "-o", str(binary)]
            if role == "sensor": command.insert(1, "-DTEST_SENSOR=1")
            if sys.platform.startswith("linux"):
                command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            return run

    def check_role(self, role):
        run = self.execute(role)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Room/Sensor ACL route, input, and permission checks passed", run.stdout)
        decoded = 0
        for line in run.stdout.splitlines():
            if not line.startswith("LEGACY:"): continue
            _, expected, body = line.split(":")
            entries = parse_acl(bytes.fromhex(body))
            self.assertEqual(len(entries), int(expected))
            self.assertTrue(all(entry["perm"] == 3 for entry in entries))
            decoded += 1
        self.assertGreaterEqual(decoded, 10)

    def test_room_actual_route_capacity_input_and_admin_filter(self):
        self.check_role("room")

    def test_sensor_actual_route_capacity_input_and_true_admin_permission(self):
        self.check_role("sensor")

    def test_old_acl_loop_is_rejected_for_both_roles(self):
        def old(source):
            self.assertIn("ofs + 7 <= reply_capacity", source)
            return source.replace("ofs + 7 <= reply_capacity", "ofs + 7 <= sizeof(reply_data) - 4", 1)
        for role in ("room", "sensor"):
            with self.subTest(role=role): self.assertNotEqual(self.execute(role, old).returncode, 0)

    def test_old_room_reserved_byte_overread_is_rejected(self):
        def old(source):
            self.assertIn("    if (payload_len < 3) return 0;", source)
            return source.replace("    if (payload_len < 3) return 0;", "", 1)
        self.assertNotEqual(self.execute("room", old).returncode, 0)

    def test_old_sensor_truncated_packet_overread_is_rejected(self):
        def old(source):
            self.assertIn("    if (len < 5) return;", source)
            return source.replace("    if (len < 5) return;", "", 1)
        self.assertNotEqual(self.execute("sensor", old).returncode, 0)

    def test_old_sensor_effective_permissions_break_genuine_admin(self):
        def old(source):
            self.assertIn("      && from->isAdmin())", source)
            return source.replace("      && from->isAdmin())", "      && (perms & PERM_ACL_ROLE_MASK) == PERM_ACL_ADMIN)", 1)
        self.assertNotEqual(self.execute("sensor", old).returncode, 0)

    def test_old_force_flood_route_selection_is_rejected(self):
        for role, client in (("room", "client"), ("sensor", "from")):
            def old(source):
                guard = "mesh::Packet::isValidPathLen(" + client + "->out_path_len)"
                signature = ("if (type == PAYLOAD_TYPE_REQ && len >= 5)" if role == "room"
                             else "if (type == PAYLOAD_TYPE_REQ) {  // request")
                receive = extract_braced(source, signature)
                self.assertIn(guard, receive)
                altered = receive.replace(guard, client + "->out_path_len != OUT_PATH_UNKNOWN")
                return source.replace(receive, altered, 1)
            with self.subTest(role=role):
                self.assertNotEqual(self.execute(role, old).returncode, 0)

    def test_sensor_early_replay_commit_prevents_retry_after_rejection(self):
        def old(source):
            commit = """      if (reply_queued) {
        from->last_timestamp = timestamp;
        from->last_activity = getRTCClock()->getCurrentTime();
      }"""
            self.assertIn(commit, source)
            return source.replace(commit, "      (void)reply_queued;", 1).replace(
                "      bool reply_queued = false;",
                "      from->last_timestamp = timestamp;\n"
                "      from->last_activity = getRTCClock()->getCurrentTime();\n"
                "      bool reply_queued = false;", 1)
        self.assertNotEqual(self.execute("sensor", old).returncode, 0)

    def test_sensor_scope_wrappers_must_propagate_failed_queue_admission(self):
        def old(source):
            changed, count = re.subn(r"return (sendFlood(?:Scoped)?\([^;]+\));",
                                     r"\1; return true;", source)
            self.assertGreaterEqual(count, 3)
            return changed
        self.assertNotEqual(self.execute("sensor", old).returncode, 0)

    def test_default_capacity_declarations_and_ci_are_wired(self):
        for path in ("examples/simple_room_server/MyMesh.h", "examples/simple_sensor/SensorMesh.h"):
            self.assertIn("size_t reply_capacity = MAX_PACKET_PAYLOAD - CIPHER_MAC_SIZE - (CIPHER_BLOCK_SIZE - 1)",
                          (ROOT / path).read_text())
        self.assertIn("          python3 -B test/test_client_acl_infrastructure.py -v\n",
                      (ROOT / ".github/workflows/run-unit-tests.yml").read_text())


if __name__ == "__main__":
    unittest.main()
