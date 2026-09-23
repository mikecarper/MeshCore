"""Exercise production scope storage and truncation with 254 neighbors."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class NeighborScopeStorageTest(unittest.TestCase):
    def test_response_bounds_and_publish_prefix(self):
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        layout = header[header.index("  struct NeighborDiscoverEntry {"):
                        header.index("  uint8_t neighbor_discover_count;")]
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "bool MyMesh::completeNeighborDiscoverEntry()",
            "bool MyMesh::handleNeighborDiscoverResponse("))
        code = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstddef>
#define MAX_NEIGHBOURS 254
#define PUB_KEY_SIZE 32
namespace mesh {
struct Identity { uint8_t pub_key[32]; };
struct Utils { static void toHex(char* out, const uint8_t*, size_t) { out[0] = 0; } };
}
struct MQTTBridge {
  static constexpr int NEIGHBORS_MAX_PUBLISH_ENTRIES = 20;
  static constexpr size_t NEIGHBORS_JSON_BUFFER_SIZE = 8192;
};
struct MQTTMessageBuilder {
  struct NeighborsMessageEntry {
    const char* key; float snr; uint32_t age; const char* scopes;
    const char* status; int16_t rssi;
  };
  static size_t measureNeighborsMessageEntry(const NeighborsMessageEntry&) { return 100; }
};
struct Clock { uint32_t getCurrentTime() { return 100; } };
struct MyMesh {
  enum { ND_UNSENT, ND_QUEUED, ND_PENDING, ND_RESPONDED, ND_TIMEOUT, ND_SEND_FAILED };
''' + layout + r'''
  unsigned neighbor_discover_next = 0, neighbor_discover_count = 254;
  unsigned neighbor_discover_publish_count = 0;
  size_t neighbor_discover_json_size = 0;
  bool neighbor_discover_truncated = false, finished = false;
  Clock clock;
  Clock* getRTCClock() { return &clock; }
  void touchNeighbourHeard(const mesh::Identity&, uint32_t, float, int16_t) {}
  void finishNeighborDiscover() { finished = true; }
  bool completeNeighborDiscoverEntry();
  bool handleNeighborDiscoverResponse(int, const uint8_t*, size_t, float, int16_t);
};
''' + methods + r'''
int main() {
  MyMesh m = {};
  static_assert(sizeof(m.neighbor_discover_scopes) == 21 * 96, "bounded scope storage");
  static_assert(sizeof(m.neighbor_discover) / sizeof(m.neighbor_discover[0]) == 254,
                "retain every neighbor snapshot");
  uint8_t response[200] = {};
  uint32_t tag = 123;
  memcpy(response, &tag, 4);
  memset(response + 8, 'x', sizeof(response) - 8);
  for (int i = 0; i <= 20; ++i) {
    auto& entry = m.neighbor_discover[i];
    entry.status = MyMesh::ND_PENDING;
    entry.tag = tag;
    assert(m.handleNeighborDiscoverResponse(i, response, sizeof(response), 5, -100));
    assert(strlen(m.neighbor_discover_scopes[i]) == 95);
    assert(m.completeNeighborDiscoverEntry() == (i < 20));
  }
  assert(m.finished && m.neighbor_discover_truncated);
  assert(m.neighbor_discover_publish_count == 20);
  assert(!m.handleNeighborDiscoverResponse(21, response, sizeof(response), 5, -100));
  assert(!m.handleNeighborDiscoverResponse(-1, response, sizeof(response), 5, -100));
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "test.cpp").write_text(code)
            subprocess.run(["g++", "-std=c++17", "-fsanitize=address,undefined",
                            str(path / "test.cpp"), "-o", str(path / "test")], check=True)
            subprocess.run([str(path / "test")], check=True)


if __name__ == "__main__":
    unittest.main()
