"""Exercise upstream sensor-list bounds and duplicate-stat wire saturation."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]

SENSOR_HARNESS = r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

@ATOI@

struct Setting { const char* name; const char* value; };
struct Sensors {
  std::vector<Setting> settings;
  int reads = 0;
  int getNumSettings() const { return static_cast<int>(settings.size()); }
  const char* getSettingName(int i) {
    assert(i >= 0 && i < getNumSettings());
    ++reads;
    return settings[i].name;
  }
  const char* getSettingValue(int i) {
    assert(i >= 0 && i < getNumSettings());
    ++reads;
    return settings[i].value;
  }
};
struct Fixture {
  Sensors* _sensors;
  void handle(char* command, char* reply) {
    @SENSOR_LIST@
    else { assert(false && "fixture accepts only sensor list"); }
  }
};

static std::string page(Fixture& fixture, int start = -1) {
  // A long trailing guard also makes the old unbounded write fail our own
  // assertion, without depending on a particular platform's stack layout.
  std::array<unsigned char, 32 + 160 + 8192> guarded;
  guarded.fill(0xA5);
  char* reply = reinterpret_cast<char*>(guarded.data() + 32);
  std::memset(reply, 0, 160);
  std::string text = "sensor list";
  if (start >= 0) text += " " + std::to_string(start);
  std::vector<char> command(text.begin(), text.end());
  command.push_back(0);
  fixture.handle(command.data(), reply);
  for (size_t i = 0; i < 32; ++i) assert(guarded[i] == 0xA5);
  for (size_t i = 32 + 160; i < guarded.size(); ++i) assert(guarded[i] == 0xA5);
  assert(std::memchr(reply, 0, 160) != nullptr);
  return reply;
}

static int nextPage(const std::string& text) {
  const size_t marker = text.find("... next:");
  return marker == std::string::npos ? -1 : std::atoi(text.c_str() + marker + 9);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string scenario = argv[1];
  if (scenario == "nulls") {
    Sensors sensors{{{nullptr, nullptr}, {"enabled", nullptr}, {nullptr, "ok"}}};
    Fixture fixture{&sensors};
    assert(page(fixture) == "3 vars\n?=\nenabled=\n?=ok");
    sensors.settings = {{"", ""}, {"name", "1"}};
    assert(page(fixture) == "2 vars\n=\nname=1");
    sensors.settings.clear(); sensors.reads = 0;
    assert(page(fixture) == "no custom var" && sensors.reads == 0);
    sensors.settings = {{"one", "1"}};
    assert(page(fixture, 1) == "no custom var");
    assert(page(fixture, 999) == "no custom var" && sensors.reads == 0);
  } else if (scenario == "oversized") {
    const std::string name(1024, 'n'), value(3072, 'v');
    Sensors sensors{{{name.c_str(), value.c_str()}}};
    Fixture fixture{&sensors};
    const std::string output = page(fixture);
    assert(output.size() < 160 && output.find("1 vars\n") == 0);
    assert(nextPage(output) == -1);
  } else if (scenario == "pagination") {
    std::vector<std::string> names, values;
    names.reserve(30); values.reserve(30);
    Sensors sensors;
    for (int i = 0; i < 30; ++i) {
      names.push_back("sensor" + std::to_string(i));
      values.push_back(std::string(20, 'a' + i % 26));
      sensors.settings.push_back({names.back().c_str(), values.back().c_str()});
    }
    Fixture fixture{&sensors};
    int start = 0, pages = 0;
    std::string all;
    do {
      const std::string output = page(fixture, start);
      assert(output.find("30 vars\n") == 0);
      all += output + "\n";
      const int next = nextPage(output);
      if (next < 0) break;
      assert(next > start && next < 30 && ++pages < 30);
      start = next;
    } while (true);
    assert(pages > 1);
    for (size_t i = 0; i < names.size(); ++i) {
      const std::string row = names[i] + "=" + values[i] + "\n";
      const size_t found = all.find(row);
      assert(found != std::string::npos);
      assert(all.find(row, found + row.size()) == std::string::npos);
    }
    // An oversized entry encountered after a fitting entry is deferred, then
    // consumed once on the next page, so it cannot trap the paging cursor.
    const std::string huge(2048, 'z');
    sensors.settings = {{"first", "ok"}, {"large", huge.c_str()}, {"last", "ok"}};
    const std::string first = page(fixture, 0);
    assert(first.find("first=ok\n") != std::string::npos && nextPage(first) == 1);
    assert(nextPage(page(fixture, 1)) == 2);
    assert(page(fixture, 2) == "3 vars\nlast=ok");
  } else if (scenario == "boundaries") {
    // Move each entry across every remaining-space and truncation boundary.
    for (size_t length = 0; length <= 260; ++length) {
      const std::string value(length, 'x');
      Sensors sensors{{{"edge", value.c_str()}, {"tail", "done"}}};
      Fixture fixture{&sensors};
      const std::string first = page(fixture);
      const int next = nextPage(first);
      if (next >= 0) {
        assert(next == 1);
        assert(page(fixture, next) == "2 vars\ntail=done");
      } else {
        assert(first.find("tail=done") != std::string::npos);
      }
    }
  } else { assert(false && "unknown scenario"); }
  return 0;
}
'''

STATS_HARNESS = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstring>

@STRUCT@;
using WireStats = @STATS_TYPE@;
static_assert(sizeof(WireStats::n_direct_dups) == 2, "direct duplicate wire width");
static_assert(sizeof(WireStats::n_flood_dups) == 2, "flood duplicate wire width");
struct SimpleMeshTables {
  uint32_t direct, flood;
  uint32_t getNumDirectDups() const { return direct; }
  uint32_t getNumFloodDups() const { return flood; }
};
struct Fixture {
  SimpleMeshTables tables;
  void* getTables() { return &tables; }
  WireStats read() {
    WireStats stats{};
    @ASSIGNMENTS@
    return stats;
  }
};
int main() {
  const uint32_t values[] = {0, 1, 65530, 65534, 65535, 65536, 65537, 131065, UINT32_MAX};
  for (const uint32_t direct : values) for (const uint32_t flood : values) {
    Fixture fixture{{direct, flood}};
    const WireStats stats = fixture.read();
    assert(stats.n_direct_dups == (direct > 65535 ? 65535 : direct));
    assert(stats.n_flood_dups == (flood > 65535 ? 65535 : flood));
    assert(fixture.tables.direct == direct && fixture.tables.flood == flood);
    unsigned char wire[sizeof(stats)];
    std::memcpy(wire, &stats, sizeof(stats));
    WireStats decoded{};
    std::memcpy(&decoded, wire, sizeof(decoded));
    assert(decoded.n_direct_dups == stats.n_direct_dups);
    assert(decoded.n_flood_dups == stats.n_flood_dups);
  }
  return 0;
}
'''


class UpstreamCliBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise unittest.SkipTest("a host C++17 compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="meshcore-upstream-bounds-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.work = Path(cls.temporary.name)
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="utf-8")
        cls.sensor_code = SENSOR_HARNESS.replace(
            "@ATOI@", extract_braced(source, "static uint32_t _atoi("))
        cls.sensor_code = cls.sensor_code.replace(
            "@SENSOR_LIST@", extract_braced(source, 'if (memcmp(command, "sensor list", 11) == 0)'))
        cls.sensor = cls.compile(cls.sensor_code, "sensors")

    @classmethod
    def compile(cls, code, name):
        source = cls.work / (name + ".cpp")
        binary = cls.work / (name + (".exe" if sys.platform == "win32" else ""))
        source.write_text(code, encoding="utf-8")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        built = subprocess.run([cls.compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra",
                                "-Werror", *sanitizers, str(source), "-o", str(binary)],
                               capture_output=True, text=True, timeout=60)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)
        return binary

    def run_binary(self, binary, *arguments, expect_failure=False):
        result = subprocess.run([str(binary), *arguments], capture_output=True, text=True, timeout=30)
        if expect_failure:
            self.assertNotEqual(result.returncode, 0, "unsafe negative control unexpectedly passed")
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sensor_list_handles_null_empty_and_exhausted_entries(self):
        self.run_binary(self.sensor, "nulls")

    def test_sensor_list_truncates_one_oversized_entry_without_overflow(self):
        self.run_binary(self.sensor, "oversized")

    def test_sensor_list_paginates_without_losing_entries_or_stalling(self):
        self.run_binary(self.sensor, "pagination")

    def test_sensor_list_remaining_space_boundaries(self):
        self.run_binary(self.sensor, "boundaries")

    def test_previous_unbounded_sensor_write_is_detected(self):
        unsafe = self.sensor_code.replace('snprintf(dp, room, "%s=%s\\n", name, value);',
                                           'sprintf(dp, "%s=%s\\n", name, value);')
        self.assertNotEqual(unsafe, self.sensor_code)
        self.run_binary(self.compile(unsafe, "unsafe-sensors"), "oversized", expect_failure=True)

    def stats_code(self, role, unsafe=False):
        source = (ROOT / "examples" / role / "MyMesh.cpp").read_text(encoding="utf-8")
        if role == "simple_repeater":
            struct_source = (ROOT / "examples" / role / "MyMesh.h").read_text(encoding="utf-8")
            stats_type = "RepeaterStats"
        else:
            struct_source = source
            stats_type = "ServerStats"
        request = extract_braced(source, "int MyMesh::handleRequest(")
        status = extract_braced(request, "if (payload[0] == REQ_TYPE_GET_STATUS)")
        assignments = []
        for field in ("direct", "flood"):
            match = re.search(r"stats\.n_" + field + r"_dups\s*=[^;]+;", status)
            self.assertIsNotNone(match, role + " " + field)
            text = match.group()
            if unsafe:
                text = re.sub(r"std::min<uint32_t>\((.*), 0xFFFF\)", r"\1", text)
                self.assertNotEqual(text, match.group())
            assignments.append(text)
        return (STATS_HARNESS.replace("@STRUCT@", extract_braced(struct_source, "struct " + stats_type))
                .replace("@STATS_TYPE@", stats_type).replace("@ASSIGNMENTS@", "\n".join(assignments)))

    def test_duplicate_stats_saturate_both_roles_at_the_wire_boundary(self):
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role):
                self.run_binary(self.compile(self.stats_code(role), role + "-stats"))

    def test_previous_wrapping_duplicate_stats_are_detected(self):
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role):
                self.run_binary(self.compile(self.stats_code(role, unsafe=True), role + "-unsafe-stats"),
                                expect_failure=True)


if __name__ == "__main__":
    unittest.main()
