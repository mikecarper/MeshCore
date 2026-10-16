#!/usr/bin/env python3
"""Exercise the actual legacy UI startup without allocating a version copy."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
static const char* firmware_version;
static const char* firmware_date;
#define FIRMWARE_VERSION firmware_version
#define FIRMWARE_BUILD_DATE firmware_date
static bool fail_allocation;
static unsigned allocations;
[[maybe_unused]] static char* copyVersion(const char* value) {
  ++allocations;
  if (fail_allocation) return nullptr;
  return ::strdup(value);
}
#define strdup copyVersion
static unsigned long millis() { return 1234; }
struct SensorManager {};
struct CompanionNodePrefs { unsigned display_rotation_degrees = 270; };
struct Board {
  bool isExternalPowered() const { return false; }
  bool isUsbHostConnected() const { return true; }
};
struct DisplayDriver {
  unsigned rotation = 0, power_calls = 0;
  void setRotationDegrees(unsigned value) { rotation = value; }
  void servicePower(bool powered, bool connected) {
    assert(powered && !connected); ++power_calls;
  }
};
struct UITask {
  char (&_version_info)[@CAPACITY@];
  Board board;
  Board* _board = &board;
  DisplayDriver* _display = nullptr;
  SensorManager* _sensors = nullptr;
  CompanionNodePrefs* _node_prefs = nullptr;
  unsigned clears = 0, resets = 0;
  unsigned long ui_started_at = 0;
  explicit UITask(char (&label)[@CAPACITY@]) : _version_info(label) {}
  void clearMsgPreview() { ++clears; }
  void resetRadioProfileDisplayPage() { ++resets; }
  bool hasConnection() const { return false; }
  void begin(DisplayDriver*, SensorManager*, CompanionNodePrefs*);
};
@BEGIN@
static void exercise(const std::string& version, const std::string& date,
                     bool use_display, bool fail) {
  firmware_version = version.c_str(); firmware_date = date.c_str();
  allocations = 0; fail_allocation = fail;
  char exact[@CAPACITY@]; memset(exact, 0x55, sizeof(exact));
  UITask ui(exact); DisplayDriver display; SensorManager sensors;
  CompanionNodePrefs prefs;
  ui.begin(use_display ? &display : nullptr, &sensors, &prefs);
  std::string expected = version.substr(0, version.find('-')) + " (" + date + ")";
  expected.resize(expected.size() < sizeof(exact) ? expected.size() : sizeof(exact)-1);
  assert(memchr(exact, 0, sizeof(exact)) && expected == exact);
  assert(allocations == 0);
  assert(ui._sensors == &sensors && ui._node_prefs == &prefs);
  assert(ui.clears == 1 && ui.resets == 1 && ui.ui_started_at == 1234);
  assert(display.power_calls == unsigned(use_display));
  if (use_display) assert(display.rotation == 270);
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "overflow")) {
    exercise(std::string(2048, 'v'), "15-Oct-2026", true, false);
  } else if (!strcmp(argv[1], "allocation_failure")) {
    exercise("v1.18.0", "15-Oct-2026", true, true);
  } else {
    for (const std::string& version : {std::string(), std::string("v1.18.0"),
         std::string("v1.18.0-abcdef01"), std::string("-abcdef01"),
         std::string(17, 'v'), std::string(18, 'v'), std::string(2048, 'v')})
      for (const std::string& date : {std::string("15-Oct-2026"), std::string(), std::string(4096, 'd')})
        for (bool display : {false, true}) for (bool fail : {false, true})
          exercise(version, date, display, fail);
  }
}
'''

LEGACY_FORMAT = r'''
  char* version = strdup(FIRMWARE_VERSION);
  char* dash = strchr(version, '-');
  if (dash) *dash = 0;
  sprintf(_version_info, "%s (%s)", version, FIRMWARE_BUILD_DATE);
'''


class LegacyUIVersionBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("legacy UI version tests require a sanitizer compiler")
        source = (ROOT / "examples/companion_radio/ui-orig/UITask.cpp").read_text()
        header = (ROOT / "examples/companion_radio/ui-orig/UITask.h").read_text()
        begin = body(source, "void UITask::begin(")
        capacity = re.search(r"char _version_info\[(\d+)\]", header).group(1)
        start = begin.index("  // strip off dash")
        end = begin.index("\n#ifdef PIN_BUZZER", start)
        legacy = begin[:start] + LEGACY_FORMAT + begin[end:]
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-ui-version-")
        cls.binaries = {}
        for name, method in (("production", begin), ("legacy", legacy)):
            directory = Path(cls.work.name)
            path, binary = directory / (name + ".cpp"), directory / name
            path.write_text(HARNESS.replace("@CAPACITY@", capacity).replace("@BEGIN@", method), encoding="ascii")
            result = subprocess.run([
                compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                str(path), "-o", str(binary)], capture_output=True, text=True, timeout=30)
            if result.returncode:
                cls.work.cleanup()
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[name] = binary

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def run_case(self, variant, scenario):
        return subprocess.run([str(self.binaries[variant]), scenario], capture_output=True,
                              text=True, timeout=10,
                              env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1"))

    def test_production_long_versions_are_bounded_and_need_no_allocation(self):
        for scenario in ("overflow", "allocation_failure", "all"):
            with self.subTest(scenario=scenario):
                result = self.run_case("production", scenario)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_previous_startup_reproduces_overflow_and_failed_allocation(self):
        for scenario in ("overflow", "allocation_failure"):
            with self.subTest(scenario=scenario):
                result = self.run_case("legacy", scenario)
                self.assertNotEqual(result.returncode, 0)
                if scenario == "overflow":
                    self.assertIn("AddressSanitizer", result.stderr)
                    self.assertIn("buffer-overflow", result.stderr)
                else:
                    self.assertIn("runtime error: null pointer", result.stderr)


if __name__ == "__main__":
    unittest.main()
