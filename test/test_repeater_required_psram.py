"""Exercise required external tables and the real early USB-only failure path."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body
from test_flood_advert_psram import HEAP

ROOT = Path(__file__).resolve().parents[1]
SDK = "#define CONFIG_IDF_TARGET_ESP32S3 1\n#define CONFIG_SPIRAM_USE_MALLOC 1\n"
FLAGS = ["ESP32_PLATFORM=1", "ESP_PLATFORM=1", "BOARD_HAS_PSRAM=1",
         "MESH_CLIENT_REPEATER_ONLY=1", "MESH_USB_CONSOLE_COOPERATIVE=1"]
HEAP = HEAP.replace("bool psram = true, internal = true;",
                    "bool psram = true, internal = true, wrong_external = false; unsigned fail_external_at = 0;")
HEAP = HEAP.replace("if (external ? !state.psram : !state.internal) return nullptr;",
                    "if (external ? (!state.psram || state.external_calls == state.fail_external_at) : !state.internal) return nullptr;")
HEAP = HEAP.replace("state.blocks[memory] = {size, caps};",
                    "state.blocks[memory] = {size, state.wrong_external ? unsigned(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) : caps};")
MEMORY = r'''
#pragma once
#include <esp_heap_caps.h>
inline bool esp_ptr_external_ram(const void* memory) {
  const auto found = heap_test::heap().blocks.find(const_cast<void*>(memory));
  return found != heap_test::heap().blocks.end() && (found->second.caps & MALLOC_CAP_SPIRAM);
}
'''
PRELUDE = r'''
#include <cassert>
#include <string>
#include <vector>
#include <helpers/FloodAdvertLimiter.h>
#define CHECK(value) do { if (!(value)) std::abort(); } while (0)
#define FLOOD_CHANNEL_SCOPE_SLOTS 255
#define FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS 32
#define FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS 255
#define FLOOD_PACKET_FILTER_SCOPE_NAME_LEN 32
static uint32_t clock_ms = 100;
static unsigned logging_services = 0, terminal_services = 0, radio_starts = 0, normal_loops = 0;
static unsigned board_starts = 0;
struct Console {
  std::string input;
  std::vector<std::string> lines;
  void begin(unsigned) {}
  int available() const { return input.size(); }
  int read() { const int value = input.front(); input.erase(0, 1); return value; }
  void println(const char* value) { lines.emplace_back(value); }
} serial;
#define Serial serial
static uint32_t millis() { return clock_ms; }
static void delay(unsigned value) { clock_ms += value; }
namespace mesh {
static void serviceUsbLoggingPort() { ++logging_services; }
static void serviceUsbTerminalPort() { ++terminal_services; }
static void prepareUsbLoggingPort() {}
static void beginUsbLoggingPort() {}
static Console& usbConsolePort() { return serial; }
static void hilStartupTrace(const char*) {}
namespace wireless {
struct Control { void begin(int) {} };
static Control& control() { static Control value; return value; }
}}
static int infrastructure_wireless = 0;
struct Board { void begin() { ++board_starts; } } board;
'''
SCENARIOS = r'''
int main(int argc, char** argv) {
  CHECK(argc == 2);
  const std::string mode = argv[1];
  auto& heap = heap_test::heap();
  if (mode == "missing") heap.psram = false;
  if (mode == "second") heap.fail_external_at = 2;
  if (mode == "wrong") heap.wrong_external = true;
  {
    MyMesh value;
    owner = &value;
    CHECK(heap.external_calls == 0 && heap.internal_calls == 0);
    setup();
    CHECK(board_starts == 1);
    if (mode != "healthy") {
      CHECK(required_psram_failed && radio_starts == 0);
      CHECK(heap.internal_calls == 0);
      CHECK(heap.external_calls == (mode == "second" ? 2u : 1u));
      CHECK(serial.lines.size() == 1);
      CHECK(serial.lines.front().find("required PSRAM") != std::string::npos);
      CHECK(serial.lines.front().find("LoRa startup stopped") != std::string::npos);
      const unsigned allocated = heap.external_calls;
      serial.input = std::string(1000, 'x');
      loop();
      CHECK(serial.input.size() == 744); // bounded input cannot starve the idle task
      serial.input = "set radio.mode both\n";
      loop();
      CHECK(serial.lines.size() == 2 && radio_starts == 0 && normal_loops == 0);
      for (unsigned i = 0; i < 1000; ++i) loop();
      CHECK(logging_services >= 1003 && terminal_services >= 1003);
      CHECK(serial.lines.size() >= 3 && heap.external_calls == allocated);
      CHECK(radio_starts == 0 && normal_loops == 0);
    } else {
      CHECK(!required_psram_failed && radio_starts == 1);
      CHECK(heap.external_calls == 2 && heap.internal_calls == 0 && heap.blocks.size() == 2);
      CHECK(serial.lines.empty());
      CHECK(sizeof(value.floodChannelScopes()) == 255 * 36);
      CHECK(sizeof(value.floodChannelDirectScopes()) == 32 * 32);
      CHECK(sizeof(value.floodChannelScopeRequirements()) == 255 * 34);
      auto* scopes = &value.floodChannelScopes();
      for (unsigned i = 0; i < 255; ++i) {
        value.floodChannelScopes()[i].target_id = i + 1;
        value.floodChannelScopeRequirements()[i].secret[31] = i;
      }
      const unsigned allocated = heap.external_calls;
      CHECK(value.prepareStartupStorage());
      CHECK(scopes == &value.floodChannelScopes() && heap.external_calls == allocated);
      for (unsigned i = 0; i < 255; ++i) {
        CHECK(value.floodChannelScopes()[i].target_id == i + 1);
        CHECK(value.floodChannelScopeRequirements()[i].secret[31] == uint8_t(i));
      }
      for (unsigned i = 0; i < 128; ++i) {
        uint8_t key[PUB_KEY_SIZE] = {}, hash[MAX_HASH_SIZE] = {};
        key[0] = i; hash[0] = i;
        value.flood_advert_limiter.observe(key, hash, 0, 1);
        CHECK(value.flood_advert_limiter.check(key, hash, 1) == mesh::FloodAdvertLimiter::Decision::Allow);
        value.flood_advert_limiter.commit(key, hash, 1);
      }
      for (unsigned i = 0; i < 128; ++i) {
        uint8_t key[PUB_KEY_SIZE] = {}, hash[MAX_HASH_SIZE] = {};
        key[0] = i; hash[0] = i;
        CHECK(value.flood_advert_limiter.check(key, hash, 1) == mesh::FloodAdvertLimiter::Decision::Duplicate);
      }
      CHECK(value.prepareStartupStorage());
      loop();
      CHECK(normal_loops == 1);
    }
  }
  CHECK(heap.blocks.empty());
}
'''


def program():
    header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
    source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
    main = (ROOT / "examples/simple_repeater/main.cpp").read_text()
    location = header.index("  struct FloodChannelStorage {")
    start = header.rfind("#if MESH_REPEATER_PSRAM_TABLES", 0, location)
    declarations = header[start:header.index("#if MESH_ENABLE_FLOOD_GROUP_MODERATION", location)]
    owner = "class MyMesh { public:\n"
    for name in ("FloodChannelScopeEntry", "FloodChannelScopeRequireEntry"):
        owner += body(header, "struct " + name + " {") + ";\n"
    owner += declarations
    owner += "mesh::StaticFloodAdvertLimiter<> flood_advert_limiter;\nbool prepareStartupStorage();\n};\n"
    owner += body(source, "bool MyMesh::prepareStartupStorage(")
    owner += "\nstatic MyMesh* owner = nullptr;\n#define the_mesh (*owner)\n"
    recovery = main[main.index("#if MESH_REPEATER_REQUIRE_PSRAM\nstatic bool"):main.index("void setup() {")]
    setup = body(main, "void setup() {")
    setup = setup[:setup.index("#ifdef HAS_EXTERNAL_WATCHDOG")] + "++radio_starts;\n}\n"
    loop = body(main, "void loop() {")
    loop = loop[:loop.index("#if defined(NRF52_PLATFORM)")] + "++normal_loops;\n}\n"
    return PRELUDE + owner + recovery + setup + loop + SCENARIOS


class RequiredRepeaterPsramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="required-repeater-psram-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        for name, content in {"esp_heap_caps.h": HEAP, "esp_memory_utils.h": MEMORY,
                              "sdkconfig.h": SDK, "required.cpp": program()}.items():
            (cls.work / name).write_text(content)
        cls.compiler = [os.environ.get("CXX", "g++")]
        cls.args = ["-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                    "-I", str(cls.work), "-isystem", str(ROOT / "src"), "-isystem", str(ROOT / "test/mocks")]
        cls.binary = cls.work / "required"
        result = subprocess.run(cls.compiler + cls.args + ["-D" + flag for flag in FLAGS] +
                                ["-DMESH_REPEATER_REQUIRE_PSRAM=1", str(cls.work / "required.cpp"),
                                 str(ROOT / "src/helpers/FloodAdvertLimiter.cpp"), "-o", str(cls.binary)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_scenario(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_missing_psram_keeps_usb_alive_without_starting_radio_or_retrying_allocations(self):
        result = self.run_scenario("missing")
        self.assertIn("channel scope tables (18874 bytes) in required PSRAM", result.stderr)

    def test_second_table_failure_never_starts_partial_radio_or_internal_fallback(self):
        result = self.run_scenario("second")
        self.assertIn("advert source history (17920 bytes) in required PSRAM", result.stderr)

    def test_wrong_memory_capability_is_rejected_before_startup(self):
        self.run_scenario("wrong")

    def test_full_scope_and_advert_capacities_start_once_and_preserve_all_entries(self):
        self.run_scenario("healthy")

    def test_required_psram_header_supports_arduino2_and_3_sdk_layouts(self):
        # Exercise the real production header with the actual public-header
        # layouts of IDF 4 and 5. Give both layouts distinct markers so a
        # successful compile also proves the selected header is correct.
        program = r"""
#include <helpers/RepeaterPsramStorage.h>
#ifndef EXPECTED_MEMORY_HEADER
#error wrong memory header
#endif
struct Table { int value; };
int main() { mesh::RepeaterPsramStorage<Table> storage; return storage.begin() ? 0 : 1; }
"""
        for layout in ('arduino2', 'arduino3'):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as td:
                work = Path(td)
                (work / 'sdkconfig.h').write_text(SDK)
                (work / 'esp_heap_caps.h').write_text(HEAP)
                (work / 'soc').mkdir()
                (work / 'soc/soc_memory_types.h').write_text(
                    ('#define EXPECTED_MEMORY_HEADER 1\n' if layout == 'arduino2'
                     else '#error IDF5 must prefer esp_memory_utils.h\n') + MEMORY)
                if layout == 'arduino3':
                    (work / 'esp_memory_utils.h').write_text('#define EXPECTED_MEMORY_HEADER 1\n' + MEMORY)
                result = subprocess.run(
                    self.compiler + ['-std=c++17', '-fsyntax-only', '-I', str(work),
                                     '-isystem', str(ROOT / 'src'), '-isystem', str(ROOT / 'test/mocks')]
                    + ['-D' + flag for flag in FLAGS]
                    + ['-DMESH_REPEATER_REQUIRE_PSRAM=1', '-x', 'c++', '-'],
                    input=program, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_illegal_required_policy_is_rejected_by_production_header(self):
        probe = self.work / "guard.cpp"
        probe.write_text('#include <helpers/RepeaterPsramStorage.h>\n')
        cases = [(["MESH_REPEATER_REQUIRE_PSRAM=2"], "must be 0 or 1"),
                 (["MESH_REPEATER_REQUIRE_PSRAM=-1"], "must be 0 or 1"),
                 (["MESH_REPEATER_REQUIRE_PSRAM=1"], "needs an ESP32 PSRAM table build"),
                 (FLAGS + ["MESH_REPEATER_REQUIRE_PSRAM=1", "MESH_REPEATER_PSRAM_TABLES=0"],
                  "needs an ESP32 PSRAM table build"),
                 ([flag for flag in FLAGS if not flag.startswith("BOARD_HAS_PSRAM")]
                  + ["MESH_REPEATER_REQUIRE_PSRAM=1"], "needs an ESP32 PSRAM table build")]
        for flags, error in cases:
            with self.subTest(flags=flags):
                result = subprocess.run(self.compiler + self.args + ["-fsyntax-only"] +
                                        ["-D" + flag for flag in flags] + [str(probe)],
                                        capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)

    def test_preallocation_precedes_filesystem_radio_identity_and_normal_loop(self):
        source = (ROOT / "examples/simple_repeater/main.cpp").read_text()
        setup = body(source, "void setup() {")
        self.assertLess(setup.index("board.begin();"), setup.index("prepareStartupStorage()"))
        for operation in ("FILESYSTEM* fs;", "while (!radio_init())", "store.loadResult", "the_mesh.begin(fs)"):
            self.assertLess(setup.index("prepareStartupStorage()"), setup.index(operation))
        loop = body(source, "void loop() {")
        self.assertLess(loop.index("if (required_psram_failed)"), loop.index("serviceCommandInterfaces();"))


if __name__ == "__main__":
    unittest.main()
