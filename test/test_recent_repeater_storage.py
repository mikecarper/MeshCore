#!/usr/bin/env python3
"""Exercise real recent-table clock behavior and alignment-safe ARM access."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

ARDUINO_CASES = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <new>
#include <type_traits>
#include "helpers/SimpleMeshTables.h"

using Info = SimpleMeshTables::RecentRepeaterInfo;
static_assert(sizeof(Info) == 9 && alignof(Info) == 1, "compact ARM-compatible rows");
static_assert(std::is_trivially_copyable<Info>::value, "clear/compaction/cursor API");

int main() {
  // A full table must evict the oldest learned time, including a millis wrap.
  Info rows[3];
  SimpleMeshTables table(rows, 3);
  const uint8_t a[] = {0x10, 0x20, 0x30}, b[] = {0x11, 0x21, 0x31};
  const uint8_t c[] = {0x12, 0x22, 0x32}, d[] = {0x13, 0x23, 0x33};
  g_mock_millis = 0xffffff00;
  assert(table.setRecentRepeater(a, 3, -128));
  g_mock_millis = 0xffffff80;
  assert(table.setRecentRepeater(b, 3, 127));
  g_mock_millis = 0x10;
  assert(table.setRecentRepeater(c, 3, 4));
  g_mock_millis = 0x20;
  assert(table.setRecentRepeater(d, 3, 8));
  assert(!table.findRecentRepeaterByHash(a, 3));
  assert(table.findRecentRepeaterByHash(b, 3));
  assert(table.findRecentRepeaterByHash(c, 3));
  assert(table.findRecentRepeaterByHash(d, 3));
  assert(table.getRecentRepeaterCount() == 3);
  assert(uint32_t(table.findRecentRepeaterByHash(b, 3)->last_heard_millis) == 0xffffff80);
  assert(uint32_t(table.findRecentRepeaterByHash(d, 3)->last_heard_millis) == 0x20);

  // An update must refresh the full timestamp and keep the SNR blending rule.
  g_mock_millis = 0xfedcba98;
  assert(table.setRecentRepeater(b, 3, -128));
  Info saved;
  SimpleMeshTables::copyRecentRepeaterInfo(saved, *table.findRecentRepeaterByHash(b, 3));
  assert(uint32_t(saved.last_heard_millis) == 0xfedcba98);
  assert(saved.snr_x4 == 64);  // ceil((127*3 - 128)/4)
  assert(table.expireRecentRepeaters(0xfedcbaa0, 8) == 2);
  assert(table.getRecentRepeaterCount() == 1);
  assert(uint32_t(rows[0].last_heard_millis) == 0xfedcba98);
  assert(uint32_t(rows[1].last_heard_millis) == 0);
  assert(uint32_t(rows[2].last_heard_millis) == 0);
  assert(uint32_t(saved.last_heard_millis) == 0xfedcba98);
  table.clearRecentRepeaters();
  assert(table.getRecentRepeaterCount() == 0);
  assert(uint32_t(rows[0].last_heard_millis) == 0);

  // Every board capacity retains every unique row with no alignment padding.
  for (const size_t capacity : {size_t(64), size_t(256), size_t(512), size_t(2048)}) {
    Info* storage = new Info[capacity];
    SimpleMeshTables full(storage, capacity);
    for (size_t i = 0; i < capacity; ++i) {
      const uint8_t prefix[] = {0x80, uint8_t(i >> 8), uint8_t(i)};
      g_mock_millis = 0xff000001u + uint32_t(i);
      assert(full.setRecentRepeater(prefix, 3, int8_t(i % 128)));
    }
    assert(full.getRecentRepeaterCount() == int(capacity));
    for (size_t i = 0; i < capacity; ++i) {
      const uint8_t prefix[] = {0x80, uint8_t(i >> 8), uint8_t(i)};
      const Info* found = full.findRecentRepeaterByHash(prefix, 3);
      assert(found && uint32_t(found->last_heard_millis) == 0xff000001u + uint32_t(i));
      assert(found->snr_x4 == int8_t(i % 128));
    }
    delete[] storage;
  }

  // Roles which do not supply a history keep the zero-capacity behavior.
  SimpleMeshTables no_storage;
  assert(!no_storage.setRecentRepeater(a, 3, 12));
  assert(!no_storage.findRecentRepeaterByHash(a, 3));
  assert(no_storage.expireRecentRepeaters(0xffffffff, 1) == 0);
  assert(no_storage.getRecentRepeaterCount() == 0);
}
'''


def run_checked(case, args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60, **kwargs)
    case.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    return result


class RecentRepeaterStorageTests(unittest.TestCase):
    def test_real_table_arduino_clock_and_all_board_capacities(self):
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "recent.cpp"
            cpp.write_text(ARDUINO_CASES)
            for sanitizer in (False, True):
                with self.subTest(sanitizer=sanitizer):
                    binary = Path(directory) / ("recent-san" if sanitizer else "recent")
                    flags = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                              "-fno-pie", "-no-pie"] if sanitizer else [])
                    run_checked(self, [
                        "g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", "-Wno-sign-compare", "-Wno-reorder",
                        "-DARDUINO=1", *flags, "-I", str(ROOT / "src"),
                        "-I", str(ROOT / "test/mocks"), str(cpp),
                        str(ROOT / "src/Packet.cpp"), "-o", str(binary)])
                    run_checked(self, [str(binary)])

    def test_actual_row_arm_copy_clear_prefix_and_timestamp_accesses_are_bytes(self):
        compiler = shutil.which("arm-none-eabi-g++")
        package_dir = Path(os.environ.get("PLATFORMIO_CORE_DIR",
                                          str(Path.home() / ".platformio"))) / "packages"
        compilers = set(package_dir.glob("toolchain-gccarmnoneeabi*/bin/arm-none-eabi-g++"))
        if compiler:
            compilers.add(Path(compiler))
        if not compilers:
            if os.environ.get("MESHCORE_REQUIRE_ARM_COMPILER") == "1":
                self.fail("ARM compiler required for compact recent-row alignment checks")
            self.skipTest("ARM code generation additionally checked by firmware qualification")
        header = (ROOT / "src/helpers/SimpleMeshTables.h").read_text()
        start = header.index("  struct RecentRepeaterMillis {")
        end = header.index("\nprivate:", start)
        # Extract the actual definition: do not duplicate the production wrapper.
        source = "#include <stdint.h>\n#include <stddef.h>\n#include <type_traits>\n#define MAX_ROUTE_HASH_BYTES 3\n"
        prefix_start = header.index("inline int routeHashPrefixBytesCompare(")
        prefix_end = header.index("\ninline bool routeHashPrefixesOverlap(", prefix_start)
        source += header[prefix_start:prefix_end] + "\n"
        source += "struct RecentRows {\n" + header[start:end] + "\n};\n"
        source += r'''
extern "C" __attribute__((noinline)) uint32_t read_recent(
    const RecentRows::RecentRepeaterInfo* rows, unsigned i) {
  return rows[i].last_heard_millis;
}
extern "C" __attribute__((noinline)) void write_recent(
    RecentRows::RecentRepeaterInfo* rows, unsigned i, uint32_t value) {
  rows[i].last_heard_millis = value;
}
extern "C" __attribute__((noinline, flatten)) void copy_recent(
    RecentRows::RecentRepeaterInfo* out, const RecentRows::RecentRepeaterInfo* in) {
  RecentRows::copyRecentRepeaterInfo(*out, *in);
}
extern "C" __attribute__((noinline, flatten)) void clear_recent(
    RecentRows::RecentRepeaterInfo* out) {
  RecentRows::clearRecentRepeaterInfo(*out);
}
extern "C" __attribute__((noinline, flatten)) void clear_history(
    RecentRows::RecentRepeaterInfo* out) {
  for (unsigned i = 0; i < 512; ++i) RecentRows::clearRecentRepeaterInfo(out[i]);
}
extern "C" __attribute__((noinline, flatten)) void copy_prefix(
    uint8_t* out, const RecentRows::RecentRepeaterInfo* in) {
  RecentRows::copyRecentRepeaterPrefix(out, *in);
}
extern "C" __attribute__((noinline, flatten)) int compare_prefix(
    const RecentRows::RecentRepeaterInfo* a, const RecentRows::RecentRepeaterInfo* b) {
  return routeHashPrefixBytesCompare(a->prefix, b->prefix, 3);
}
extern "C" __attribute__((noinline, flatten)) uint8_t read_prefix_length(
    const RecentRows::RecentRepeaterInfo* rows, unsigned i) {
  return rows[i].prefix_len;
}
extern "C" __attribute__((noinline, flatten)) int8_t read_snr(
    const RecentRows::RecentRepeaterInfo* rows, unsigned i) {
  return rows[i].snr_x4;
}
RecentRows::RecentRepeaterInfo history[512];
static_assert(sizeof(history) == 4608, "all 512 rows stay available");
'''
        # Prefix writes must also use the actual production implementation.
        write_start = header.index("    volatile uint8_t* out_prefix = slot.prefix;")
        write_end = header.index("    slot.prefix_len = prefix_len;", write_start)
        source += 'extern "C" __attribute__((noinline, flatten)) void write_prefix(\n'
        source += "    RecentRows::RecentRepeaterInfo& slot, const uint8_t* prefix, uint8_t prefix_len) {\n"
        source += header[write_start:write_end] + "}\n"
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "arm-recent.cpp"
            cpp.write_text(source)
            for compiler_path in sorted(compilers):
                for cpu in ("cortex-m0", "cortex-m4"):
                    for optimization in ("-Os", "-O2"):
                        with self.subTest(compiler=str(compiler_path), cpu=cpu, optimization=optimization):
                            asm = Path(directory) / "arm-recent.s"
                            run_checked(self, [str(compiler_path), "-std=c++11", "-mcpu=" + cpu,
                                               "-mthumb", optimization, "-Wall", "-Wextra", "-Werror",
                                               "-S", str(cpp), "-o", str(asm)])
                            generated = asm.read_text()
                            for function, operation in (("read_recent", "ldrb"), ("write_recent", "strb")):
                                body = generated.split(function + ":", 1)[1].split(".size", 1)[0]
                                self.assertEqual(len(re.findall(r"\b" + operation + r"(?:\.w)?\s", body)), 4,
                                                 body)
                                self.assertNotRegex(body, r"\b(?:ldr|str|ldrh|strh|ldrd|strd|ldm\w*|stm\w*)(?:\.w)?\s")
                            for function, operation in (("copy_recent", "ldrb"), ("clear_recent", "strb"),
                                                        ("clear_history", "strb"), ("copy_prefix", "ldrb"),
                                                        ("compare_prefix", "ldrb"), ("write_prefix", "strb"),
                                                        ("read_prefix_length", "ldrb"), ("read_snr", "ldr(?:s)?b")):
                                body = generated.split(function + ":", 1)[1].split(".size", 1)[0]
                                # Literal-pool LDR and stack PUSH/POP are naturally
                                # aligned; loads/stores through row pointers must be bytes.
                                self.assertRegex(body, r"\b" + operation + r"(?:\.w)?\s", body)
                                self.assertNotRegex(body, r"\b(?:ldr|str|ldrh|strh|ldrd|strd)(?:\.w)?\s[^\n]*\[")
                                self.assertNotRegex(body, r"\b(?:ldm\w*|stm\w*)\s")
                                self.assertNotRegex(body, r"\b(?:memcpy|memset|memcmp)\b")

    def test_actual_compaction_and_console_callers_use_byte_helpers(self):
        header = (ROOT / "src/helpers/SimpleMeshTables.h").read_text()
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        self.assertIn("copyRecentRepeaterInfo(info, _recent_repeaters[_recent_repeater_count])", header)
        self.assertIn("clearRecentRepeaterInfo(_recent_repeaters[_recent_repeater_count])", header)
        self.assertNotIn("memset(_recent_repeaters", header)
        self.assertNotIn("memcpy(slot.prefix", header)
        self.assertNotIn("memcmp(info->prefix", header)
        self.assertIn("copyRecentRepeaterInfo(serial_recent_cursor, *info)", source)
        self.assertNotIn("serial_recent_cursor = *info", source)
        self.assertNotIn("toHex(prefix, info->prefix", source)
        self.assertEqual(source.count("copyRecentRepeaterPrefix(recorded_prefix, *info)"), 4)
        self.assertEqual(source.count("alignas(uint32_t) uint8_t recorded_prefix[MAX_ROUTE_HASH_BYTES]"), 4)


if __name__ == "__main__":
    unittest.main()
