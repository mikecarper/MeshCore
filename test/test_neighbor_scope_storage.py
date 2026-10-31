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
    role = "simple_repeater"

    def bridge_constants(self):
        source = (ROOT / "src/helpers/bridges/MQTTBridge.h").read_text()
        start = source.index("  #if defined(BOARD_HAS_PSRAM)", source.index("// Single source of truth for the neighbors JSON size"))
        return source[start:source.index("  // Called by the mesh", start)]

    def test_response_bounds_and_publish_prefix(self):
        header = (ROOT / "examples" / self.role / "MyMesh.h").read_text()
        source = (ROOT / "examples" / self.role / "MyMesh.cpp").read_text()
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
#include <helpers/Radio2DiscoverTrailer.h>
struct MQTTRadio2Info { const char* radio; const char* mode; bool temporary; };
inline const MQTTRadio2Info* radio2InfoFor(const Radio2DiscoverTrailer&, MQTTRadio2Info&,
                                           char*, size_t) { return nullptr; }
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
    const char* status; int16_t rssi; const MQTTRadio2Info* radio2;
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
    auto& entry = m.neighbor_discover_metadata[i];
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
                            "-I" + str(ROOT / "src"),
                            str(path / "test.cpp"), "-o", str(path / "test")], check=True)
            subprocess.run([str(path / "test")], check=True)

    def real_serializer_fixture(self, psram=True):
        header = (ROOT / "examples" / self.role / "MyMesh.h").read_text()
        source = (ROOT / "examples" / self.role / "MyMesh.cpp").read_text()
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
            "    uint32_t heard_timestamp;\n"
            "    uint32_t tag;\n"
            "    int16_t rssi;\n"
            "    int8_t snr;\n",
            "    uint32_t heard_timestamp;\n"
            "    int8_t snr;\n"
            "    int16_t rssi;\n"
            "    uint32_t tag;\n")
        legacy_layout = legacy_layout.replace(
            'sizeof(NeighborDiscoverMetadata) == 12',
            'sizeof(NeighborDiscoverMetadata) == 16')
        self.assertNotEqual(layout, legacy_layout)
        fixture = (ROOT / "test/fixtures/neighbor_scope_storage.cpp").read_text()
        if not psram:
            fixture = fixture.replace("#define BOARD_HAS_PSRAM 1\n", "")
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

    def lifecycle_fixture(self):
        header = (ROOT / "examples" / self.role / "MyMesh.h").read_text()
        source = (ROOT / "examples" / self.role / "MyMesh.cpp").read_text()
        layout = header[header.index("  struct NeighborDiscoverEntry {"):
                        header.index("  uint8_t neighbor_discover_count;")]
        legacy = layout.replace(
            "  static constexpr size_t NEIGHBOR_SCOPE_RESULTS =\n"
            "      MAX_NEIGHBOURS < MQTTBridge::NEIGHBORS_SCOPE_RESULT_CAPACITY\n"
            "          ? MAX_NEIGHBOURS : MQTTBridge::NEIGHBORS_SCOPE_RESULT_CAPACITY;",
            "  static constexpr size_t NEIGHBOR_SCOPE_RESULTS = MAX_NEIGHBOURS;")
        start = header.index("  uint8_t neighbor_discover_count;")
        end = header.index("  char neighbor_discover_origin[32];", start)
        fields = header[start:end] + "  char neighbor_discover_origin[32];\n"
        hooks = extract_braced(extract_braced(source, "void MyMesh::logTx("),
                               "if (neighbor_discover_active")
        fail_hook = extract_braced(extract_braced(source, "void MyMesh::logTxFail("),
                                   "if (neighbor_discover_active")
        state = fields + r'''
  NeighbourInfo neighbours[MAX_NEIGHBOURS] = {};
  mesh::LocalIdentity self_id = {};
  Cli _cli; Clock clock; MQTTBridge mock_bridge; MQTTBridge* bridge = &mock_bridge;
  mesh::Packet packet;
  bool admit=true;
  int requested=0, touched=0;
  ACL acl; GuestLogin guest_login; int matching_peer_indexes[MAX_CLIENTS] = {};
  static const int NEIGHBOR_DISCOVER_PEER_BASE=1000;
  Clock* getRTCClock() { return &clock; }
  uint32_t neighborDiscoverQueryTimeoutMs() const { return 100; }
  bool cancelNeighborDiscoverRequest() { return false; }
  bool isCurrentOutbound(mesh::Packet*) { return false; }
  bool neighborDiscoverReady(char*) { return true; }
  mesh::Packet* sendAnonRegionsReq(const mesh::Identity&,uint32_t& tag) {
    ++requested; tag=uint32_t(requested); return admit ? &packet : nullptr;
  }
  void touchNeighbourHeard(const mesh::Identity&,uint32_t,float,int16_t) { ++touched; }
  // The fixture keeps each neighbour's radio2 beside it; the repeater's
  // real lookup (a small cache) is tested in test_radio2_neighbour_cache.py.
  Radio2DiscoverTrailer neighbourRadio2(const mesh::Identity& id) const {
    for (const auto& n : neighbours) if (!memcmp(n.id.pub_key,id.pub_key,32)) return n.radio2;
    return Radio2DiscoverTrailer::unknown();
  }
  const MQTTRadio2Info* selfRadio2(MQTTRadio2Info& out,char* radio,size_t size) {
    snprintf(radio,size,"910.525000,250.0,7,5");
    out.radio=radio; out.mode="rxtx"; out.temporary=false; return &out;
  }
  void resetNeighborDiscoverJsonBudget() {
    strcpy(self_scopes_buf,"sea;pdx"); strcpy(self_default_scope_buf,"sea");
    strcpy(neighbor_discover_origin,"Seattle");
    char key[65]; mesh::Utils::toHex(key,self_id.pub_key,32);
    neighbor_discover_publish_count=neighbor_discover_queried_count=0;
    neighbor_discover_truncated=false;
    neighbor_discover_json_size=MQTTMessageBuilder::measureNeighborsMessageBase(
        neighbor_discover_origin,key,"2026-10-10T01:23:45Z",self_scopes_buf,
        self_default_scope_buf,neighbor_discover_count);
  }
  void markTransmitted() { mesh::Packet* pkt=&packet;
''' + hooks + r'''
  }
  void markFailed() { mesh::Packet* pkt=&packet;
''' + fail_hook + r'''
  }
  int searchPeersByHash(const uint8_t*);
  void getPeerSharedSecret(uint8_t*,int);
  bool startNeighborDiscover(char*);
  void loopNeighborDiscover();
  void finishNeighborDiscover();
  bool completeNeighborDiscoverEntry();
  bool handleNeighborDiscoverResponse(int,const uint8_t*,size_t,float,int16_t);
'''
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "bool MyMesh::startNeighborDiscover(", "void MyMesh::loopNeighborDiscover()",
            "void MyMesh::finishNeighborDiscover()", "bool MyMesh::completeNeighborDiscoverEntry()",
            "bool MyMesh::handleNeighborDiscoverResponse(", "int MyMesh::searchPeersByHash(",
            "void MyMesh::getPeerSharedSecret("))
        methods = methods.replace("mqtt_bridge", "bridge")
        helpers = "\n".join(extract_braced(source, signature) for signature in (
            "static bool neighborHeardAgeUsable(", "static bool neighborPublishEntryComesBefore("))
        fixture = (ROOT / "test/fixtures/neighbor_scope_lifecycle.cpp").read_text()
        identity = (ROOT / "src/Identity.h").read_text()
        fixture = fixture.replace("@HASH_MATCH@", extract_braced(identity,
                                  "bool isHashMatch(const uint8_t* hash) const"))
        if self.role == "simple_repeater":
            fixture = "#define TEST_REPEATER_ROLE 1\n" + fixture
        code = (fixture.replace("@BRIDGE_CONSTANTS@", self.bridge_constants())
                .replace("@HELPERS@", helpers).replace("@LAYOUT@", layout)
                .replace("@LEGACY_LAYOUT@", legacy).replace("@STATE@", state)
                .replace("@METHODS@", methods)
                .replace("@LEGACY_METHODS@", methods.replace("MyMesh::", "LegacyMesh::")))
        return code

    def test_actual_discovery_lifecycle_and_repeated_passes(self):
        code = self.lifecycle_fixture()
        for psram in (False, True):
            with self.subTest(role=self.role, psram=psram):
                self.compile_real_serializer(("#define TEST_PSRAM 1\n" if psram else "") + code)

    def test_dropping_unqueried_crypto_keys_fails_overlay_regression(self):
        self.compile_real_serializer("#define TEST_LOST_KEY_TAIL 1\n" + self.lifecycle_fixture(),
                                     expect_rejection=True)

    def test_full_254_neighbors_keep_every_publishable_entry_and_json(self):
        for psram in (False, True):
            with self.subTest(role=self.role, psram=psram):
                self.compile_real_serializer(self.real_serializer_fixture(psram))

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


class RoomNeighborScopeStorageTest(NeighborScopeStorageTest):
    role = "simple_room_server"


if __name__ == "__main__":
    unittest.main()
