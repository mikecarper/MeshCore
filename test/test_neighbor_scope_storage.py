"""Exercise production scope storage and truncation with 254 neighbors."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class NeighborScopeStorageTest(unittest.TestCase):
    def bridge_constants(self):
        source = (ROOT / "src/helpers/bridges/MQTTBridge.h").read_text()
        start = source.index("  #if defined(BOARD_HAS_PSRAM)", source.index("// Single source of truth for the neighbors JSON size"))
        return source[start:source.index("  // Called by the mesh", start)]

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
''' + self.bridge_constants() + r'''
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
  uint8_t neighbor_discover_next = 0, neighbor_discover_count = 254;
  uint8_t neighbor_discover_publish_count = 0;
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

    def real_serializer_fixture(self):
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        layout = header[header.index("  struct NeighborDiscoverEntry {"):
                        header.index("  uint8_t neighbor_discover_count;")]
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "bool MyMesh::completeNeighborDiscoverEntry()",
            "bool MyMesh::handleNeighborDiscoverResponse("))
        legacy_layout = layout.replace(
            "  static constexpr size_t NEIGHBOR_SCOPE_RESULTS =\n"
            "      MAX_NEIGHBOURS < MQTTBridge::NEIGHBORS_SCOPE_RESULT_CAPACITY\n"
            "          ? MAX_NEIGHBOURS : MQTTBridge::NEIGHBORS_SCOPE_RESULT_CAPACITY;",
            "  static constexpr size_t NEIGHBOR_SCOPE_RESULTS = MAX_NEIGHBOURS;")
        legacy_layout = legacy_layout.replace(
            "    uint32_t tag;            // anon-regions request tag we're waiting on\n"
            "    int16_t rssi;            // dBm from the last packet heard from this neighbour\n"
            "    int8_t snr;              // multiplied by 4\n",
            "    int8_t snr;              // multiplied by 4\n"
            "    int16_t rssi;            // dBm from the last packet heard from this neighbour\n"
            "    uint32_t tag;            // anon-regions request tag we're waiting on\n")
        self.assertNotEqual(layout, legacy_layout)
        fixture = (ROOT / "test/fixtures/neighbor_scope_storage.cpp").read_text()
        return (fixture.replace("@BRIDGE_CONSTANTS@", self.bridge_constants())
                .replace("@LAYOUT@", layout).replace("@METHODS@", methods)
                .replace("@LEGACY_LAYOUT@", legacy_layout)
                .replace("@LEGACY_METHODS@", methods.replace("MyMesh::", "LegacyMesh::")))

    def compile_real_serializer(self, code, expect_rejection=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        json_header = next((ROOT / ".pio/libdeps").glob("*/ArduinoJson/src/ArduinoJson.h"), None)
        self.assertIsNotNone(json_header, "actual native ArduinoJson headers are required")
        with tempfile.TemporaryDirectory(prefix="meshcore-neighbor-scope-") as temporary:
            work = Path(temporary)
            generated = work / "scope.cpp"
            generated.write_text(code)
            binary = work / "scope"
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                       "-Wno-missing-field-initializers", *sanitizers,
                                       "-I" + str(json_header.parent), "-I" + str(ROOT / "src"),
                                       "-isystem", str(ROOT / "test/mocks"),
                                       str(generated), str(ROOT / "src/helpers/MQTTPayloadBuilder.cpp"),
                                       str(ROOT / "src/helpers/TxtDataHelpers.cpp"),
                                       "-o", str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            if expect_rejection:
                self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn("Assertion", checked.stderr)
            else:
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn("real neighbor serialization", checked.stdout)

    def test_full_254_neighbors_keep_every_publishable_entry_and_json(self):
        self.compile_real_serializer(self.real_serializer_fixture())

    def test_reintroduced_full_scope_array_fails_runtime_memory_regression(self):
        fixture = self.real_serializer_fixture()
        old = fixture
        start = old.index("struct MyMesh")
        end = old.index("struct LegacyMesh")
        candidate = old[start:end]
        candidate = candidate.replace(
            "  static constexpr size_t NEIGHBOR_SCOPE_RESULTS =\n"
            "      MAX_NEIGHBOURS < MQTTBridge::NEIGHBORS_SCOPE_RESULT_CAPACITY\n"
            "          ? MAX_NEIGHBOURS : MQTTBridge::NEIGHBORS_SCOPE_RESULT_CAPACITY;",
            "  static constexpr size_t NEIGHBOR_SCOPE_RESULTS = MAX_NEIGHBOURS;")
        old = old[:start] + candidate + old[end:]
        self.assertNotEqual(old, fixture)
        self.compile_real_serializer(old, expect_rejection=True)

    def test_over_restrictive_bound_fails_actual_publication_regression(self):
        fixture = self.real_serializer_fixture()
        old = fixture.replace("NEIGHBORS_MIN_DISCOVERY_ENTRY_JSON_BYTES = 144;",
                              "NEIGHBORS_MIN_DISCOVERY_ENTRY_JSON_BYTES = 512;", 1)
        self.assertNotEqual(old, fixture)
        # Run through publication comparison instead of stopping at the memory
        # or bound assertion: a needlessly small array loses actual JSON rows.
        old = "#define TEST_PUBLICATION_NEGATIVE_CONTROL 1\n" + old
        self.compile_real_serializer(old, expect_rejection=True)


if __name__ == "__main__":
    unittest.main()
