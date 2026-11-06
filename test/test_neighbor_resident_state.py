"""Exercise the real infrastructure neighbor rows after unused state removal."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class NeighborResidentStateTest(unittest.TestCase):
    def test_real_rows_keep_full_capacity_updates_eviction_and_removal(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        source = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <helpers/Radio2DiscoverTrailer.h>
#define PUB_KEY_SIZE 32
#define MAX_NEIGHBOURS 50
#define WITH_MQTT_NEIGHBORS 1
namespace mesh {
struct Identity {
  uint8_t pub_key[PUB_KEY_SIZE] = {};
  bool matches(const Identity& other) const {
    return !memcmp(pub_key, other.pub_key, sizeof(pub_key));
  }
};
}
struct Clock { uint32_t now = 100; uint32_t getCurrentTime() const { return now; } };
'''
        for role in ("simple_repeater", "simple_room_server"):
            header = (ROOT / "examples" / role / "MyMesh.h").read_text()
            production = (ROOT / "examples" / role / "MyMesh.cpp").read_text()
            row = body(header, "struct NeighbourInfo {") + ";"
            methods = "\n".join(body(production, signature) for signature in (
                "void MyMesh::putNeighbour(", "void MyMesh::removeNeighbor("))
            source += "namespace " + role + " {\n" + row + r'''
struct MyMesh {
  Clock clock;
  NeighbourInfo neighbours[MAX_NEIGHBOURS] = {};
  Clock* getRTCClock() { return &clock; }
  void putNeighbour(const mesh::Identity&, uint32_t, float, int16_t);
  void removeNeighbor(const uint8_t*, int);
};
''' + methods + "\n}\n"
        source += r'''
// The public identity, heard time, signed SNR and optional RSSI remain complete.
// Natural alignment is retained; no packed records or bytewise timestamp loads.
static_assert(sizeof(simple_repeater::NeighbourInfo) == 40, "repeater RAM row");
static_assert(sizeof(simple_room_server::NeighbourInfo) == 48, "room RAM row");
static_assert(alignof(simple_repeater::NeighbourInfo) >= alignof(uint32_t), "aligned RTC");
static_assert(alignof(simple_room_server::NeighbourInfo) >= alignof(uint32_t), "aligned RTC");
template<class Mesh> void check() {
  Mesh mesh;
  mesh::Identity id;
  for (unsigned i = 0; i < MAX_NEIGHBOURS; ++i) {
    id.pub_key[0] = uint8_t(i + 1);
    id.pub_key[31] = uint8_t(255 - i); // preserve the complete 32-byte key
    mesh.clock.now = 100 + i;
    mesh.putNeighbour(id, UINT32_MAX - i, -4.25f, -123);
    assert(mesh.neighbours[i].id.matches(id));
    assert(mesh.neighbours[i].heard_timestamp == 100 + i);
    assert(mesh.neighbours[i].snr == -17 && mesh.neighbours[i].rssi == -123);
  }
  id = mesh.neighbours[0].id;
  mesh.clock.now = 200;
  mesh.putNeighbour(id, 0, 3.5f, -90);
  assert(mesh.neighbours[0].heard_timestamp == 200);
  assert(mesh.neighbours[0].snr == 14 && mesh.neighbours[0].rssi == -90);
  // A full table evicts the oldest locally heard row, independently of the
  // remote advertised timestamp, including its zero and UINT32_MAX extremes.
  id.pub_key[0] = 51;
  id.pub_key[31] = 3;
  mesh.clock.now = 201;
  mesh.putNeighbour(id, UINT32_MAX, -1.25f, -110);
  assert(mesh.neighbours[1].id.matches(id));
  assert(mesh.neighbours[0].heard_timestamp == 200);
  assert(mesh.neighbours[1].heard_timestamp == 201 && mesh.neighbours[1].snr == -5);
  mesh.removeNeighbor(nullptr, -1);
  mesh.removeNeighbor(nullptr, 1);
  assert(mesh.neighbours[1].id.matches(id));
  mesh.removeNeighbor(id.pub_key, PUB_KEY_SIZE);
  assert(mesh.neighbours[1].heard_timestamp == 0);
  assert(mesh.neighbours[1].snr == 0 && mesh.neighbours[1].rssi == 0);
  mesh.removeNeighbor(nullptr, 0);
  for (const auto& entry : mesh.neighbours) {
    assert(entry.heard_timestamp == 0 && entry.snr == 0 && entry.rssi == 0);
    for (uint8_t byte : entry.id.pub_key) assert(byte == 0);
  }
}
int main() { check<simple_repeater::MyMesh>(); check<simple_room_server::MyMesh>(); }
'''
        with tempfile.TemporaryDirectory(prefix="neighbor-resident-") as directory:
            work = Path(directory)
            (work / "test.cpp").write_text(source)
            binary = work / "test"
            subprocess.run([compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(ROOT / "src"), str(work / "test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
