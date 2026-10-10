#!/usr/bin/env python3
"""Exercise actual Room CLI/Sensor ACK routes through real Mesh admission."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_client_acl_infrastructure import production_methods

ROOT = Path(__file__).resolve().parents[1]


class StoredClientReplyRoutesTest(unittest.TestCase):
    def execute(self, role, old=False, old_keepalive=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++17 compiler required")
        sensor = role == "sensor"
        name = "SensorMesh" if sensor else "MyMesh"
        source = (ROOT / ("examples/simple_sensor/SensorMesh.cpp" if sensor
                          else "examples/simple_room_server/MyMesh.cpp")).read_text()
        if old:
            guard = "!mesh::Packet::isValidPathLen(" + ("dest." if sensor else "client->") + "out_path_len)"
            self.assertIn(guard, source)
            source = source.replace(guard, ("dest." if sensor else "client->") + "out_path_len == OUT_PATH_UNKNOWN")
        if sensor:
            route = extract_braced(source, "void SensorMesh::sendAckTo(")
        else:
            ack_start = source.index("    if (send_ack) {")
            branch = extract_braced(source[ack_start:], "if (" + (
                "client->out_path_len == OUT_PATH_UNKNOWN" if old else
                "!mesh::Packet::isValidPathLen(client->out_path_len)") + ")")
            # Extract_braced returns the first if block; retain its actual
            # matching else through the end of this send_ack branch too.
            full = extract_braced(source, "if (send_ack) {")
            start = full.index(branch)
            route_body = full[start:full.rfind("}")]
            route = """void MyMesh::sendCliAck(const ClientInfo& dest) {
const ClientInfo* client = &dest;
mesh::Packet incoming;
mesh::Packet* packet = &incoming;
uint32_t ack_hash = 7, delay_millis = 0;
""" + route_body + "\n(void)delay_millis;\n}\n"
        transform = None
        if old_keepalive:
            self.assertFalse(sensor)
            def transform(text):
                receive = extract_braced(text, "if (data[4] == REQ_TYPE_KEEP_ALIVE && packet->isRouteDirect())")
                guard = "mesh::Packet::isValidPathLen(client->out_path_len)"
                self.assertIn(guard, receive)
                altered = receive.replace(guard, "client->out_path_len != OUT_PATH_UNKNOWN")
                return text.replace(receive, altered, 1)
        generated = production_methods(role, transform) + route
        mesh_source = (ROOT / "src/Mesh.cpp").read_text()
        generated += "namespace mesh {\n"
        for signature in ("Packet* Mesh::createAck(const uint8_t*", "Packet* Mesh::createAck(uint32_t",
                          "Packet* Mesh::createMultiAck(const uint8_t*", "Packet* Mesh::createMultiAck(uint32_t"):
            generated += extract_braced(mesh_source, signature) + "\n"
        generated += "}\n"
        fixture = (ROOT / "test/fixtures/client_acl_infrastructure/test_client_acl_infrastructure.cpp").read_text()
        fixture = fixture.replace("#define SERVER_RESPONSE_DELAY 300", "#define SERVER_RESPONSE_DELAY 300\n#define TXT_ACK_DELAY 300\n#define REPLY_DELAY_MILLIS 1500")
        declaration = "  Packet* createDatagram(uint8_t, const Identity&, const uint8_t*, const uint8_t*, size_t);"
        self.assertIn(declaration, fixture)
        fixture = fixture.replace(declaration, declaration + """
  Packet* createAck(const uint8_t*, uint8_t);
  Packet* createAck(uint32_t);
  Packet* createMultiAck(const uint8_t*, uint8_t, uint8_t);
  Packet* createMultiAck(uint32_t, uint8_t);
""")
        fixture = fixture.replace("  mesh::Packet* createAck(uint32_t) { return nullptr; }", """
  uint8_t getExtraAckTransmitCount() const { return 0; }
#ifdef TEST_SENSOR
  void sendAckTo(const ClientInfo&, uint32_t, uint8_t);
#else
  void sendCliAck(const ClientInfo&);
#endif
""")
        fixture = fixture.replace("  unsigned released = 0;", "  unsigned released = 0, obtained = 0;")
        fixture = fixture.replace("    auto* packet = new Packet;", "    ++obtained;\n    auto* packet = new Packet;")
        fixture = fixture.replace("int main() {", "int original_main() {", 1)
        marker = '  std::cout << "Room/Sensor ACL route, input, and permission checks passed\\n";'
        fixture = fixture.replace(marker, marker + "\n  return 0;")
        fixture += r'''
int main() {
  original_main();
  for (uint8_t stored_path : {0, 1, 0x41, 0xFE, 0xFF, 0xC0, 0xBF}) {
    for (int refusal = 0; refusal < 3; ++refusal) {
      TARGET_CLASS target;
      target.sender.out_path_len = stored_path;
      target.refuse_allocation = refusal == 1;
      target.refuse_queue = refusal == 2;
#ifdef TEST_SENSOR
      target.sendAckTo(target.sender, 7, 1);
#else
      target.sendCliAck(target.sender);
#endif
      if (refusal) { assert(target.queued == 0); continue; }
      assert(target.queued == 1 && target.last_reply != nullptr);
      const bool direct = mesh::Packet::isValidPathLen(stored_path);
      assert(target.direct_sent == unsigned(direct));
      assert(target.flood_sent == unsigned(!direct));
      assert(target.last_reply->getPayloadType() == PAYLOAD_TYPE_ACK);
      assert(target.last_reply->payload_len == 4);
    }
#ifndef TEST_SENSOR
    for (int refusal = 0; refusal < 3; ++refusal) {
      TARGET_CLASS target;
      target.sender.out_path_len = stored_path;
      target.refuse_allocation = refusal == 1;
      target.refuse_queue = refusal == 2;
      mesh::Packet incoming;
      incoming.header = (PAYLOAD_TYPE_REQ << PH_TYPE_SHIFT) | ROUTE_TYPE_DIRECT;
      uint8_t request[9] = {};
      uint32_t timestamp = 51;
      memcpy(request, &timestamp, sizeof(timestamp));
      request[4] = REQ_TYPE_KEEP_ALIVE;
      target.receive(&incoming, request, sizeof(request));
      const bool direct = mesh::Packet::isValidPathLen(stored_path);
      assert(target.flood_sent == 0);
      assert(target.sender.last_timestamp == timestamp);
      assert(target.sender.last_activity == 100);
      if (!direct) {
        assert(target.queued == 0 && target.obtained == 0);
      } else if (refusal) {
        assert(target.queued == 0);
      } else {
        assert(target.queued == 1 && target.direct_sent == 1);
        assert(target.last_reply->payload_len == 5);
      }
    }
#endif
  }
}
'''
        with tempfile.TemporaryDirectory(prefix="stored-reply-route-") as directory:
            work = Path(directory)
            (work / "production.inc").write_text(generated, encoding="ascii")
            (work / "test.cpp").write_text(fixture, encoding="ascii")
            binary = work / "test"
            # Actual Mesh::createMultiAck has a pre-existing signed-size comparison.
            # Keep other fixture warnings fatal without changing production code.
            args = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-sign-compare",
                    "-I" + str(work), "-I" + str(ROOT / "test/fixtures/room_history_store"), "-I" + str(ROOT / "src"), str(work / "test.cpp"), "-o", str(binary)]
            if sensor: args.insert(1, "-DTEST_SENSOR=1")
            if sys.platform.startswith("linux"):
                args[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            built = subprocess.run(args, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            return subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)

    def test_actual_room_cli_and_sensor_ack_route_and_pool_admission(self):
        for role in ("room", "sensor"):
            with self.subTest(role=role):
                run = self.execute(role)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_legacy_unknown_only_guard_drops_force_flood_ack(self):
        for role in ("room", "sensor"):
            with self.subTest(role=role):
                self.assertNotEqual(self.execute(role, old=True).returncode, 0)

    def test_legacy_keepalive_guard_attempts_invalid_direct_allocation(self):
        self.assertNotEqual(self.execute("room", old_keepalive=True).returncode, 0)

    def test_suite_is_explicitly_wired_in_ci(self):
        self.assertIn("          python3 -B test/test_stored_client_reply_routes.py -v\n",
                      (ROOT / ".github/workflows/run-unit-tests.yml").read_text())


if __name__ == "__main__":
    unittest.main()
