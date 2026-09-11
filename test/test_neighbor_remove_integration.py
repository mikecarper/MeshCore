"""Compile real neighbor CLI/removal code, including the remote whitespace path."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class NeighborRemoveIntegrationTest(unittest.TestCase):
    def test_neighbor_removal_and_mobile_clear_all(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="utf-8")
        repeater = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="utf-8")
        room = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text(encoding="utf-8")
        utils = (ROOT / "src/Utils.cpp").read_text(encoding="utf-8")
        arm_start = common.rfind("else if", 0, common.index('"neighbor.remove'))
        arm = extract_braced(common[arm_start:], "else if")
        trim = extract_braced(repeater, "while (command_end > command")
        prefix = extract_braced(repeater, "if (strlen(command) > 4 && command[2] == '|')")
        definitions = "\n".join(extract_braced(utils, signature) for signature in (
            "static uint8_t hexVal(", "bool Utils::isHexChar(", "bool Utils::fromHex("))
        code = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <initializer_list>
#include <helpers/CLICommandUtils.h>
#define PUB_KEY_SIZE 32
#define MAX_NEIGHBOURS 4
#define WITH_MQTT_NEIGHBORS 1
namespace mesh {
struct Utils {
  static bool isHexChar(char);
  static bool fromHex(uint8_t*, int, const char*);
};
''' + definitions + r'''
}
struct NeighbourInfo {
  struct { uint8_t pub_key[PUB_KEY_SIZE] = {}; } id;
  uint32_t heard_timestamp = 0;
  int8_t snr = 0, rssi = 0;
};
struct Callbacks { virtual void removeNeighbor(const uint8_t*, int) = 0; };
struct CommonCLI {
  Callbacks* _callbacks;
  void handleCommand(char* command, char* reply) {
    mesh::cli::normalizeCommandVerb(command);
    if (false) {}
''' + arm + r'''
    else strcpy(reply, "Unknown command");
  }
};
'''
        for namespace, source in (("repeater", repeater), ("room", room)):
            code += "namespace " + namespace + r''' {
struct MyMesh : Callbacks {
  NeighbourInfo neighbours[MAX_NEIGHBOURS];
  void removeNeighbor(const uint8_t*, int) override;
};
''' + extract_braced(source, "void MyMesh::removeNeighbor(") + "\n}\n"
        code += r'''
void send(CommonCLI& cli, const char* text, char* reply, bool remote) {
  char storage[256] = {};
  strcpy(storage, text);
  char* command = storage;
  if (remote) {
    char* command_end = command + strlen(command);
''' + trim + "\n" + prefix + r'''
  }
  cli.handleCommand(command, reply);
}
template<class Mesh> void seed(Mesh& mesh) {
  mesh.neighbours[0] = NeighbourInfo();
  mesh.neighbours[1] = NeighbourInfo();
  mesh.neighbours[2] = NeighbourInfo();
  mesh.neighbours[3] = NeighbourInfo();
  mesh.neighbours[0].id.pub_key[0] = 0xAB;
  mesh.neighbours[1].id.pub_key[0] = 0xAB;
  mesh.neighbours[1].id.pub_key[1] = 0xCD;
  mesh.neighbours[2].id.pub_key[0] = 0x12;
  for (int i = 0; i < 3; ++i) {
    mesh.neighbours[i].heard_timestamp = 100 + i;
    mesh.neighbours[i].snr = 5;
    mesh.neighbours[i].rssi = -90;
  }
}
template<class Mesh> void check() {
  Mesh mesh;
  CommonCLI cli{&mesh};
  char reply[160] = {};
  for (bool remote : {true, false}) {
    for (const char* clear : {"neighbor.remove ", "neighbor.remove",
                             "neighbor.remove  ", "neighbor.remove \t\r\n",
                             "Neighbor.remove "}) {
      seed(mesh);
      send(cli, clear, reply, remote);
      assert(!strcmp(reply, "OK"));
      for (const auto& item : mesh.neighbours) {
        assert(item.heard_timestamp == 0 && item.snr == 0 && item.rssi == 0);
        for (uint8_t value : item.id.pub_key) assert(value == 0);
      }
      send(cli, clear, reply, remote);  // Clearing an empty table is idempotent.
      assert(!strcmp(reply, "OK"));
    }
    seed(mesh);
    send(cli, "neighbor.remove aB", reply, remote);
    assert(!strcmp(reply, "OK"));
    assert(mesh.neighbours[0].heard_timestamp == 0);
    assert(mesh.neighbours[1].heard_timestamp == 0);
    assert(mesh.neighbours[2].heard_timestamp == 102);

    seed(mesh);
    const std::string exact = "neighbor.remove abcd" + std::string(60, '0');
    send(cli, exact.c_str(), reply, remote);
    assert(!strcmp(reply, "OK"));
    assert(mesh.neighbours[0].heard_timestamp == 100);
    assert(mesh.neighbours[1].heard_timestamp == 0);
    assert(mesh.neighbours[2].heard_timestamp == 102);

    for (const auto& invalid : {std::string("neighbor.remove A"),
         std::string("neighbor.remove AG"), std::string("neighbor.remove *"),
         std::string("neighbor.remove AB CD"), std::string("neighbor.remove all"),
         std::string("neighbor.remove ") + std::string(66, 'A')}) {
      seed(mesh);
      send(cli, invalid.c_str(), reply, remote);
      assert(!strcmp(reply, "ERR: bad pubkey"));
      for (int i = 0; i < 3; ++i) assert(mesh.neighbours[i].heard_timestamp == 100U+i);
    }
    seed(mesh);
    send(cli, "neighbor.removeall", reply, remote);
    assert(!strcmp(reply, "Unknown command"));
    assert(mesh.neighbours[0].heard_timestamp == 100);
    send(cli, "neighbor.remove FF", reply, remote);
    assert(!strcmp(reply, "OK"));
    assert(mesh.neighbours[0].heard_timestamp == 100);
  }
  seed(mesh);
  send(cli, "ab|neighbor.remove \r\n", reply, true);
  assert(!strcmp(reply, "ab|OK"));
  assert(mesh.neighbours[0].heard_timestamp == 0);

  seed(mesh);
  uint8_t key[PUB_KEY_SIZE] = {};
  mesh.removeNeighbor(nullptr, 1);
  mesh.removeNeighbor(key, -1);
  mesh.removeNeighbor(key, PUB_KEY_SIZE + 1);
  assert(mesh.neighbours[0].heard_timestamp == 100);
  mesh.removeNeighbor(nullptr, 0);
  assert(mesh.neighbours[0].heard_timestamp == 0);
}
int main() { check<repeater::MyMesh>(); check<room::MyMesh>(); puts("neighbor removal checks passed"); }
'''
        with tempfile.TemporaryDirectory(prefix="meshcore-neighbors-") as directory:
            work = Path(directory)
            source = work / "test.cpp"
            source.write_text(code, encoding="utf-8")
            binary = work / "neighbors.exe"
            result = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-I" + str(ROOT / "src"), str(source), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
