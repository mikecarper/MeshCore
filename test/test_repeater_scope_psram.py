"""Exercise actual scope table owners, persistence and rollback in both RAM backends."""
from pathlib import Path
import os
import resource
import signal
import subprocess
import tempfile
import unittest

from cpp_source import body
from test_flood_advert_psram import HEAP


ROOT = Path(__file__).resolve().parents[1]
SDK = ("#define CONFIG_IDF_TARGET_ESP32S3 1\n"
       "#define CONFIG_SPIRAM_USE_MALLOC 1\n")
DEFINES = ["ESP32_PLATFORM=1", "BOARD_HAS_PSRAM=1", "STATION_G3_ESP32=1",
           "MESH_CLIENT_REPEATER_ONLY=1"]

PRELUDE = r'''
#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <esp_heap_caps.h>
#include <helpers/RepeaterPsramStorage.h>
#include <helpers/FloodFilterPolicy.h>
#define CHECK(value) do { if (!(value)) std::abort(); } while (0)
#define FLOOD_CHANNEL_SCOPE_SLOTS 255
#define FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS 32
#define FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS 255
#define FLOOD_PACKET_FILTER_SCOPE_NAME_LEN 32
#define FLOOD_CHANNEL_SCOPE_TXT_ANY 0
#define FLOOD_CHANNEL_SCOPE_OTHER_ANY 2
#define MESH_ENABLE_FLOOD_RULE_ENGINE 0
#define FLOOD_CHANNEL_SCOPE_FILE "/scopes"
#define FLOOD_CHANNEL_SCOPE_TEMP_FILE "/scopes.tmp"
#define FLOOD_CHANNEL_SCOPE_REQUIRE_FILE "/require"
#define FLOOD_CHANNEL_SCOPE_REQUIRE_TEMP_FILE "/require.tmp"
struct RegionMap {
  static bool is_name_char(uint8_t c) { return std::isalnum(c) || c == '_' || c == '-'; }
};
namespace mesh { struct Utils {
  static void sha256(void* out, size_t size, const uint8_t*, size_t length) {
    CHECK(size == 1 && (length == 16 || length == 32));
    std::memset(out, 0, size);
  }
}; }
static unsigned external_io = 0;
static void inspectSpan(const void* data, size_t size) {
  const uintptr_t address = reinterpret_cast<uintptr_t>(data);
  for (const auto& block : heap_test::heap().blocks) {
    const uintptr_t start = reinterpret_cast<uintptr_t>(block.first);
    if (address >= start && address < start + block.second.bytes) {
      CHECK(size <= start + block.second.bytes - address);
      if (block.second.caps & MALLOC_CAP_SPIRAM) ++external_io;
    }
  }
}
struct MemoryFS;
struct File {
  MemoryFS* fs = nullptr;
  std::string path;
  size_t cursor = 0;
  bool valid = false;
  explicit operator bool() const { return valid; }
  size_t read(uint8_t*, size_t);
  size_t write(const uint8_t*, size_t);
  void close() { valid = false; }
};
struct MemoryFS {
  std::map<std::string, std::vector<uint8_t>> files;
  bool fail_rename = false, short_write = false;
  bool exists(const char* path) { return files.count(path); }
  bool remove(const char* path) { return files.erase(path); }
  bool rename(const char* from, const char* to) {
    if (fail_rename || !files.count(from)) return false;
    files[to] = files[from]; files.erase(from); return true;
  }
};
size_t File::read(uint8_t* out, size_t size) {
  if (!valid) return 0;
  const auto& bytes = fs->files[path];
  size = std::min(size, bytes.size() - cursor);
  inspectSpan(out, size);
  if (size) std::memcpy(out, bytes.data() + cursor, size);
  cursor += size; return size;
}
size_t File::write(const uint8_t* data, size_t size) {
  if (!valid) return 0;
  if (fs->short_write && size) --size;
  inspectSpan(data, size);
  auto& bytes = fs->files[path];
  bytes.insert(bytes.end(), data, data + size); return size;
}
static File openFloodSettingsRead(MemoryFS* fs, const char* path) {
  return File{fs, path, 0, fs->exists(path)};
}
static File openFloodSettingsWrite(MemoryFS* fs, const char* path) {
  fs->files[path].clear(); return File{fs, path, 0, true};
}
'''

SCENARIOS = r'''
static std::vector<uint8_t> snapshot(const MyMesh& mesh) {
  std::vector<uint8_t> bytes;
  const auto add = [&](const void* data, size_t size) {
    const auto first = static_cast<const uint8_t*>(data);
    bytes.insert(bytes.end(), first, first + size);
  };
  add(mesh.floodChannelScopes(), sizeof(mesh.floodChannelScopes()));
  add(mesh.floodChannelDirectScopes(), sizeof(mesh.floodChannelDirectScopes()));
  add(mesh.floodChannelScopeRequirements(), sizeof(mesh.floodChannelScopeRequirements()));
  return bytes;
}
static void populate(MyMesh& mesh) {
  for (unsigned i = 0; i < 32; ++i)
    std::snprintf(mesh.floodChannelDirectScopes()[i], 32, "#Scope%u", i);
  for (unsigned i = 0; i < 255; ++i) {
    auto& row = mesh.floodChannelScopes()[i];
    row.target_id = i % 32 + 1;
    row.selector = FloodFilterPolicy::encodeChannelScopeTargetSelector(0, true);
    auto& requirement = mesh.floodChannelScopeRequirements()[i];
    requirement.key_len = 32;
    std::memset(requirement.secret, uint8_t(i + 1), sizeof(requirement.secret));
  }
}
static void exercise(const char* mode) {
  MemoryFS disk;
  {
    const unsigned constructor_calls = heap_test::heap().external_calls + heap_test::heap().internal_calls;
    MyMesh writer(disk);
    CHECK(heap_test::heap().external_calls + heap_test::heap().internal_calls == constructor_calls);
    writer.begin();
    static_assert(sizeof(writer.floodChannelScopes()) == 255 * 36, "all scope slots");
    static_assert(sizeof(writer.floodChannelDirectScopes()) == 32 * 32, "all direct names");
    static_assert(sizeof(writer.floodChannelScopeRequirements()) == 255 * 34, "all requirements");
#if MESH_REPEATER_PSRAM_TABLES
    static_assert(sizeof(MyMesh) < 256, "no inline fallback arrays");
    auto& heap = heap_test::heap();
    CHECK(heap.blocks.size() == 1);
    CHECK(heap.last_bytes == 18874);
    const auto caps = heap.blocks.begin()->second.caps;
    CHECK(caps == (!std::strcmp(mode, "fallback")
        ? (MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
        : (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)));
    const unsigned calls = heap.external_calls + heap.internal_calls;
    CHECK(writer.flood_channel_storage.begin());
    CHECK(heap.external_calls + heap.internal_calls == calls);
#else
    CHECK(heap_test::heap().blocks.empty());
    (void)mode;
#endif
    CHECK(snapshot(writer) == std::vector<uint8_t>(18874, 0));
    const void* scopes = writer.floodChannelScopes();
    const void* direct = writer.floodChannelDirectScopes();
    const void* require = writer.floodChannelScopeRequirements();
    populate(writer);
    const auto initial = snapshot(writer);
    writer.begin();
    CHECK(snapshot(writer) == initial);
    CHECK(writer.saveFloodChannelScopes());
    CHECK(writer.saveFloodChannelScopeRequirements());
    CHECK(disk.files["/scopes"].size() == 10210);
    CHECK(disk.files["/require"].size() == 8675);
    std::vector<uint8_t> expected = {'F','C','S','5',255};
    const auto* start = reinterpret_cast<const uint8_t*>(writer.floodChannelScopes());
    expected.insert(expected.end(), start, start + 9180);
    expected.push_back(32);
    start = reinterpret_cast<const uint8_t*>(writer.floodChannelDirectScopes());
    expected.insert(expected.end(), start, start + 1024);
    CHECK(disk.files["/scopes"] == expected);
    expected = {'F','C','R','1',255};
    start = reinterpret_cast<const uint8_t*>(writer.floodChannelScopeRequirements());
    expected.insert(expected.end(), start, start + 8670);
    CHECK(disk.files["/require"] == expected);
    const auto saved = disk.files;
    char reply[160];
    for (bool short_write : {false, true}) {
      disk.fail_rename = !short_write; disk.short_write = short_write;
      writer.deleteFloodChannelScope(".255", reply);
      CHECK(!std::strcmp(reply, "Err - save failed"));
      CHECK(snapshot(writer) == initial && disk.files == saved);
      writer.deleteFloodChannelScopeRequirement(".255", reply);
      CHECK(!std::strcmp(reply, "Err - save failed"));
      CHECK(snapshot(writer) == initial && disk.files == saved);
      writer.deleteFloodChannelScope("all", reply);
      CHECK(!std::strcmp(reply, "Err - save failed"));
      CHECK(snapshot(writer) == initial && disk.files == saved);
      writer.deleteFloodChannelScopeRequirement("all", reply);
      CHECK(!std::strcmp(reply, "Err - save failed"));
      CHECK(snapshot(writer) == initial && disk.files == saved);
    }
    disk.fail_rename = disk.short_write = false;
    std::memset(writer.floodChannelScopes(), 0xa5, sizeof(writer.floodChannelScopes()));
    std::memset(writer.floodChannelDirectScopes(), 0xa5, sizeof(writer.floodChannelDirectScopes()));
    std::memset(writer.floodChannelScopeRequirements(), 0xa5, sizeof(writer.floodChannelScopeRequirements()));
    CHECK(writer.loadFloodChannelScopes());
    writer.loadFloodChannelScopeRequirements();
    CHECK(snapshot(writer) == initial);
    disk.files["/scopes"].pop_back();
    disk.files["/require"].pop_back();
    CHECK(!writer.loadFloodChannelScopes());
    writer.loadFloodChannelScopeRequirements();
    CHECK(snapshot(writer) == std::vector<uint8_t>(18874, 0));
    disk.files = saved;
    CHECK(writer.loadFloodChannelScopes());
    writer.loadFloodChannelScopeRequirements();
    CHECK(snapshot(writer) == initial);
    CHECK(scopes == writer.floodChannelScopes() && direct == writer.floodChannelDirectScopes()
          && require == writer.floodChannelScopeRequirements());
    // Compare the complete production file payloads between inline and PSRAM runs.
    for (const char* path : {"/scopes", "/require"}) {
      const auto& bytes = disk.files[path];
      CHECK(std::fwrite(bytes.data(), 1, bytes.size(), stdout) == bytes.size());
    }
    writer.deleteFloodChannelScope(".255", reply);
    CHECK(!std::strcmp(reply, "OK") && writer.floodChannelScopes()[254].target_id == 0);
    writer.deleteFloodChannelScopeRequirement(".255", reply);
    CHECK(!std::strcmp(reply, "OK") && writer.floodChannelScopeRequirements()[254].key_len == 0);
    writer.deleteFloodChannelScope("all", reply);
    CHECK(!std::strcmp(reply, "OK"));
    writer.deleteFloodChannelScopeRequirement("all", reply);
    CHECK(!std::strcmp(reply, "OK"));
    CHECK(snapshot(writer) == std::vector<uint8_t>(18874, 0));
  }
  CHECK(heap_test::heap().blocks.empty());
  MyMesh recreated(disk);
  CHECK(heap_test::heap().blocks.empty());
  recreated.begin();
  CHECK(snapshot(recreated) == std::vector<uint8_t>(18874, 0));
}
int main(int argc, char** argv) {
  const char* mode = argc > 1 ? argv[1] : "trace";
  auto& heap = heap_test::heap();
  if (std::strcmp(mode, "trace")) heap.psram = false;
  if (!std::strcmp(mode, "no-memory")) heap.internal = false;
  if (!std::strcmp(mode, "reserve")) heap.dma_free = 16384 + 18874 - 1;
  if (!std::strcmp(mode, "fragmented")) heap.dma_largest = 18874 - 1;
  if (!std::strcmp(mode, "boundary")) {
    heap.dma_free = 16384 + 18874;
    heap.dma_largest = 18874;
    mode = "fallback";
  }
  const size_t free_before = heap.dma_free;
  exercise(mode);
  CHECK(heap.blocks.empty() && heap.dma_free == free_before);
#if MESH_REPEATER_PSRAM_TABLES
  CHECK(heap.external_calls == 2 && heap.frees == 2);
  CHECK(heap.internal_calls == (!std::strcmp(mode, "fallback") ? 2u : 0u));
  CHECK(external_io > 0 || !std::strcmp(mode, "fallback"));
#else
  CHECK(heap.external_calls == 0 && heap.internal_calls == 0 && heap.frees == 0);
#endif
}
'''


def production_program():
    header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text(encoding="ascii")
    source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="ascii")
    location = header.index("  struct FloodChannelStorage {")
    start = header.rfind("#if MESH_REPEATER_PSRAM_TABLES", 0, location)
    declarations = header[start:header.index("#if MESH_ENABLE_FLOOD_GROUP_MODERATION", location)]
    constructor = source.index("MyMesh::MyMesh(")
    start = source.index("#if !MESH_REPEATER_PSRAM_TABLES\n  memset(flood_channel_scopes", constructor)
    resets = source[start:source.index("#if MESH_ENABLE_FLOOD_GROUP_MODERATION", start)]
    begin_start = source.index("void MyMesh::begin(FILESYSTEM *fs, bool durable) {")
    begin_start = source.index("{", begin_start) + 1
    begin = source[begin_start:source.index("#if MESH_ENABLE_FLOOD_RULE_ENGINE", begin_start)]
    methods = [
        "bool MyMesh::prepareStartupStorage(",
        "bool MyMesh::loadFloodChannelScopes(", "bool MyMesh::saveFloodChannelScopes(",
        "void MyMesh::loadFloodChannelScopeRequirements(",
        "bool MyMesh::saveFloodChannelScopeRequirements(",
        "void MyMesh::deleteFloodChannelScope(",
        "void MyMesh::deleteFloodChannelScopeRequirement(",
    ]
    helpers = [
        "static const char* skipFloodFilterSpaces(", "static bool floodFilterAsciiEqual(",
        "static bool parseFloodFilterUnsigned(", "static bool parseFloodChannelScopeIndex(",
        "static bool isValidStoredFloodFilterScopeName(",
        "static bool isExactFloodChannelScopeSelector(",
    ]
    owner = ("class MyMesh {\npublic:\nMemoryFS* _fs;\n"
             "struct Limiter { bool begin() { return true; } } flood_advert_limiter;\n")
    for name in ("FloodChannelScopeEntry", "FloodChannelScopeRequireEntry"):
        owner += body(header, "struct " + name + " {") + ";\n"
    owner += declarations
    owner += "explicit MyMesh(MemoryFS& fs) : _fs(&fs) {\n" + resets + "}\n"
    owner += "void begin() {\n" + begin + "}\n"
    owner += ("bool prepareStartupStorage();\n"
              "bool loadFloodChannelScopes();\n"
              "bool saveFloodChannelScopes(bool empty_table=false);\n"
              "void loadFloodChannelScopeRequirements();\n"
              "bool saveFloodChannelScopeRequirements(bool empty_table=false);\n"
              "void deleteFloodChannelScope(const char*, char*);\n"
              "void deleteFloodChannelScopeRequirement(const char*, char*);\n};\n")
    return "\n".join([PRELUDE, owner] + [body(source, item) for item in helpers + methods] + [SCENARIOS])


class RepeaterScopePsramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="repeater-scope-psram-")
        cls.work = Path(cls.temporary.name)
        (cls.work / "esp_heap_caps.h").write_text(HEAP, encoding="ascii")
        (cls.work / "sdkconfig.h").write_text(SDK, encoding="ascii")
        (cls.work / "scope.cpp").write_text(production_program(), encoding="ascii")
        cls.compiler = [os.environ.get("CXX", "g++")]
        cls.binaries = {}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def build(self, enabled):
        if enabled not in self.binaries:
            binary = self.work / ("scope-%d" % enabled)
            command = self.compiler + ["-std=c++17", "-O1", "-g", "-DNDEBUG",
                "-Wall", "-Wextra", "-Werror", "-Wno-sign-compare",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                "-fno-pie", "-no-pie", "-I", str(self.work),
                "-isystem", str(ROOT / "src"), "-isystem", str(ROOT / "test/mocks")]
            command += ["-D" + define for define in DEFINES]
            command += ["-DMESH_REPEATER_PSRAM_TABLES=%d" % enabled,
                        str(self.work / "scope.cpp"), "-o", str(binary)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.binaries[enabled] = binary
        return self.binaries[enabled]

    def test_full_table_file_bytes_loads_resets_lifetime_and_rollback_match(self):
        baseline = subprocess.run([str(self.build(0))], capture_output=True, timeout=20)
        self.assertEqual(baseline.returncode, 0, baseline.stderr.decode())
        self.assertEqual(len(baseline.stdout), 18885)
        for mode in ("trace", "fallback", "boundary"):
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.build(1)), mode], capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(result.stdout, baseline.stdout)

    def test_no_capacity_reduction_or_null_references_on_allocation_failure(self):
        def no_core():
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        for mode in ("no-memory", "reserve", "fragmented"):
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.build(1)), mode], capture_output=True,
                    text=True, timeout=20, preexec_fn=no_core)
                self.assertEqual(result.returncode, -signal.SIGABRT, result.stderr)
                self.assertIn("Cannot allocate channel scope tables (18874 bytes)", result.stderr)
                self.assertNotIn("runtime error:", result.stderr)
                self.assertNotIn("AddressSanitizer", result.stderr)

    def test_default_selection_and_explicit_sdk_prerequisites(self):
        probe = self.work / "guard.cpp"
        probe.write_text('#include <helpers/RepeaterPsramStorage.h>\n'
                         'static_assert(MESH_REPEATER_PSRAM_TABLES == EXPECTED, "selection");\n',
                         encoding="ascii")
        cases = [([], 0, True, SDK), (["ESP32_PLATFORM=1", "STATION_G3_ESP32=1"], 0, True, SDK),
            (["ESP32_PLATFORM=1", "BOARD_HAS_PSRAM=1"], 0, True, SDK),
            (DEFINES[:-1], 0, True, SDK),
            ([define for define in DEFINES if not define.startswith("STATION_G3")], 1, True, SDK),
            (DEFINES, 1, True, SDK), (DEFINES + ["MESH_REPEATER_PSRAM_TABLES=0"], 0, True, ""),
            (["MESH_REPEATER_PSRAM_TABLES=1"], 1, False, SDK),
            (DEFINES[:-1] + ["MESH_REPEATER_PSRAM_TABLES=1"], 1, False, SDK),
            (DEFINES[:-1] + ["MESH_CLIENT_REPEATER_ONLY=0", "MESH_REPEATER_PSRAM_TABLES=1"],
             1, False, SDK),
            (DEFINES + ["MESH_REPEATER_PSRAM_TABLES=2"], 2, False, SDK),
            (DEFINES, 0, True, SDK.replace("CONFIG_IDF_TARGET_ESP32S3 1", "CONFIG_IDF_TARGET_ESP32 1")),
            (DEFINES + ["MESH_REPEATER_PSRAM_TABLES=1"], 1, False,
             SDK.replace("CONFIG_IDF_TARGET_ESP32S3 1", "CONFIG_IDF_TARGET_ESP32 1")),
            (DEFINES, 1, True, SDK + "#define CONFIG_SPIRAM_BOOT_INIT 1\n"),
            (DEFINES, 1, False, SDK.replace("CONFIG_SPIRAM_USE_MALLOC 1", "CONFIG_SPIRAM_USE_MALLOC 0"))]
        for index, (defines, expected, success, config) in enumerate(cases):
            with self.subTest(index=index, defines=defines):
                config_dir = self.work / ("guard-%d" % index)
                config_dir.mkdir()
                (config_dir / "sdkconfig.h").write_text(config, encoding="ascii")
                command = self.compiler + ["-std=c++17", "-fsyntax-only", "-I", str(config_dir),
                    "-I", str(self.work), "-I", str(ROOT / "src"), "-DEXPECTED=%d" % expected]
                command += ["-D" + define for define in defines] + [str(probe)]
                result = subprocess.run(command, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
