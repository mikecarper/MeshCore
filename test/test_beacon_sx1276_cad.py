"""Run SX1276 CAD completion and beacon virtual dispatch against counted pins.

Production driver, wrapper override, and beacon CAD caller are used as written.
The modem reproduces pinned RadioLib's pre-CAD_DONE FREE result, so bypassing
the SX1276 override is observably unsafe. No PlatformIO or hardware is used.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
RADIO = ROOT / "src/helpers/radiolib"


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -16
#define RADIOLIB_CHANNEL_FREE -15
#define RADIOLIB_PREAMBLE_DETECTED -14
#define RADIOLIB_ERR_RX_TIMEOUT -6
#define STATE_IDLE 0
#define STATE_TX_WAIT 3
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define RADIOLIB_ASSERT(result) do { if ((result) != 0) return (result); } while (0)
static uint32_t now_ms = 0;
static unsigned yields = 0;
uint32_t millis() { return now_ms; }
void yield() { ++now_ms; ++yields; }
namespace mesh {
struct MainBoard {
  unsigned watchdogs = 0;
  void serviceWatchdog() { ++watchdogs; }
};
}
struct Hal {
  uint32_t started = 0, done_after = 0, detected_after = UINT32_MAX;
  bool done() const { return done_after != UINT32_MAX && uint32_t(now_ms - started) >= done_after; }
  bool digitalRead(unsigned pin) {
    return pin == 0 ? done()
        : detected_after != UINT32_MAX && uint32_t(now_ms - started) >= detected_after;
  }
  void yield() { ::yield(); }
};
struct Module {
  Hal hardware;
  Hal* hal = &hardware;
  unsigned getIrq() const { return 0; }
  unsigned getGpio() const { return 1; }
};
struct CustomSX1276 {
  Module module;
  Module* mod = &module;
  int start_result = 0, result = RADIOLIB_CHANNEL_FREE, stop_result = 0, clear_result = 0;
  unsigned starts = 0, reads = 0, stops = 0, clears = 0;
  int16_t startChannelScan() { ++starts; mod->hardware.started = now_ms; return start_result; }
  int16_t getChannelScanResult() {
    ++reads;
    // Pinned SX127x checks CAD_DETECTED, not whether CAD_DONE occurred yet.
    return mod->hardware.done() ? result : RADIOLIB_CHANNEL_FREE;
  }
  int standby() { ++stops; return stop_result; }
  int clearIrqFlags(uint32_t) { ++clears; return clear_result; }
  @DRIVER@
};
struct RadioLibWrapper {
  CustomSX1276* _radio;
  mesh::MainBoard* _board;
  bool _beacon_owned = true, _beacon_restoring = false, busy = false;
  uint8_t state = STATE_IDLE;
  uint32_t _beacon_cad_timeout = 100;
  unsigned claimed = 0;
  RadioLibWrapper(CustomSX1276& chip, mesh::MainBoard& board) : _radio(&chip), _board(&board) {}
  bool isChipBusy() const { return busy; }
  bool claimRadioInterrupt() { ++claimed; return false; }
  @SCAN_DECLARATION@
  bool isBeaconChannelBusy();
};
struct CustomSX1276Wrapper : RadioLibWrapper {
  CustomSX1276Wrapper(CustomSX1276& chip, mesh::MainBoard& board) : RadioLibWrapper(chip, board) {}
  @OVERRIDE@
};
@COMMON_SCAN@
@BEACON@
struct Fixture {
  CustomSX1276 chip;
  mesh::MainBoard board;
  CustomSX1276Wrapper wrapper{chip, board};
  Fixture() { now_ms = 100; yields = 0; }
};
static void immediate() {
  { Fixture f; assert(f.chip.tryScanChannel(100, f.board) == RADIOLIB_CHANNEL_FREE);
    assert(!yields && f.chip.reads == 1); }
  { Fixture f; f.chip.result = RADIOLIB_PREAMBLE_DETECTED;
    assert(f.chip.tryScanChannel(100, f.board) == RADIOLIB_PREAMBLE_DETECTED);
    assert(!yields && f.chip.reads == 1); }
}
static void delayed() {
  for (int result : {RADIOLIB_CHANNEL_FREE, RADIOLIB_PREAMBLE_DETECTED}) {
    Fixture f; f.chip.result = result; f.chip.mod->hardware.done_after = 3;
    assert(f.chip.tryScanChannel(100, f.board) == result);
    assert(yields == 3 && f.chip.reads == 1 && now_ms == 103);
  }
  { Fixture f; f.chip.mod->hardware.done_after = UINT32_MAX;
    f.chip.mod->hardware.detected_after = 2;
    assert(f.chip.tryScanChannel(100, f.board) == RADIOLIB_PREAMBLE_DETECTED);
    assert(yields == 2 && f.chip.reads == 0); }
  // Completion at the deadline is authoritative, even with no DIO1 level.
  { Fixture f; f.chip.mod->hardware.done_after = 100;
    f.chip.result = RADIOLIB_PREAMBLE_DETECTED;
    assert(f.chip.tryScanChannel(100, f.board) == RADIOLIB_PREAMBLE_DETECTED);
    assert(yields == 100 && f.chip.reads == 1); }
}
static void deadlines() {
  { Fixture f; f.chip.mod->hardware.done_after = UINT32_MAX;
    assert(f.chip.tryScanChannel(2500, f.board) == RADIOLIB_ERR_RX_TIMEOUT);
    assert(yields == 2500 && f.board.watchdogs == 2 && f.chip.reads == 0); }
  { Fixture f; now_ms = UINT32_MAX - 10; f.chip.mod->hardware.done_after = UINT32_MAX;
    const uint32_t start = now_ms;
    assert(f.chip.tryScanChannel(100, f.board) == RADIOLIB_ERR_RX_TIMEOUT);
    assert(yields == 100 && uint32_t(now_ms - start) == 100); }
  { Fixture f; f.chip.mod->hardware.done_after = UINT32_MAX;
    assert(f.chip.tryScanChannel(0, f.board) == RADIOLIB_ERR_RX_TIMEOUT);
    assert(!yields && f.chip.reads == 0); }
}
static void failures() {
  { Fixture f; f.chip.start_result = -77;
    assert(f.chip.tryScanChannel(100, f.board) == -77);
    assert(f.chip.starts == 1 && !f.chip.reads && !yields && !f.board.watchdogs); }
  { Fixture f; f.chip.mod->hardware.done_after = 4; f.chip.result = -78;
    assert(f.chip.tryScanChannel(100, f.board) == -78);
    assert(f.chip.reads == 1 && yields == 4); }
}
static void beacon_dispatch() {
  { Fixture f; f.chip.mod->hardware.done_after = 3; f.chip.result = RADIOLIB_PREAMBLE_DETECTED;
    RadioLibWrapper& base = f.wrapper;
    assert(base.isBeaconChannelBusy());
    assert(yields == 3 && f.chip.reads == 1 && f.chip.stops == 1 && f.chip.clears == 1);
    assert(f.wrapper.claimed == 1 && f.wrapper.state == STATE_IDLE); }
  { Fixture f; f.chip.mod->hardware.done_after = 5;
    assert(!f.wrapper.isBeaconChannelBusy());
    assert(yields == 5 && f.chip.reads == 1); }
  { Fixture f; f.chip.mod->hardware.done_after = UINT32_MAX; f.wrapper._beacon_cad_timeout = 13;
    assert(f.wrapper.isBeaconChannelBusy()); assert(yields == 13 && !f.chip.reads); }
  { Fixture f; f.chip.start_result = -77; assert(f.wrapper.isBeaconChannelBusy());
    assert(!yields && f.chip.stops == 1 && f.chip.clears == 1); }
  { Fixture f; f.chip.stop_result = -1; assert(f.wrapper.isBeaconChannelBusy()); }
  { Fixture f; f.chip.clear_result = -1; assert(f.wrapper.isBeaconChannelBusy()); }
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "immediate")) immediate();
  else if (!strcmp(argv[1], "delayed")) delayed();
  else if (!strcmp(argv[1], "deadlines")) deadlines();
  else if (!strcmp(argv[1], "failures")) failures();
  else if (!strcmp(argv[1], "beacon")) beacon_dispatch();
  else assert(false);
  puts("PASS");
}
'''


class BeaconSX1276CADTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise unittest.SkipTest("a host C++ compiler is required")
        driver = (RADIO / "CustomSX1276.h").read_text(encoding="utf-8")
        wrapper = (RADIO / "CustomSX1276Wrapper.h").read_text(encoding="utf-8")
        common = (RADIO / "RadioLibWrappers.cpp").read_text(encoding="utf-8")
        header = (RADIO / "RadioLibWrappers.h").read_text(encoding="utf-8")
        declaration = re.search(r"virtual int16_t performChannelScanWithTimeout\(unsigned long timeout_ms\);", header)
        if declaration is None:
            raise AssertionError("beacon CAD timeout helper must use virtual driver dispatch")
        cls.source = (HARNESS.replace("@DRIVER@", body(driver, "int tryScanChannel("))
                      .replace("@OVERRIDE@", body(wrapper, "int16_t performChannelScanWithTimeout("))
                      .replace("@SCAN_DECLARATION@", declaration.group(0))
                      .replace("@COMMON_SCAN@", body(common, "int16_t RadioLibWrapper::performChannelScanWithTimeout("))
                      .replace("@BEACON@", body(common, "bool RadioLibWrapper::isBeaconChannelBusy(")))
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-sx1276-cad-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.binary = cls.compile_source(cls.source, "checked")

    @classmethod
    def compile_source(cls, source, name):
        cpp = cls.work / (name + ".cpp")
        cpp.write_text("#include <initializer_list>\n" + source, encoding="ascii")
        binary = cls.work / (name + (".exe" if sys.platform == "win32" else ""))
        flags = ["-std=c++17", "-Wall", "-Wextra", "-Werror"]
        if sys.platform.startswith("linux"):
            flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
        result = subprocess.run([cls.compiler, *flags, str(cpp), "-o", str(binary)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return binary

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True,
                                cwd=self.work, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_immediate_cad_done_checks_detected_flag_instead_of_assuming_free(self):
        self.run_case("immediate")

    def test_delayed_free_detected_early_preamble_and_done_at_deadline(self):
        self.run_case("delayed")

    def test_timeout_millisecond_wrap_and_watchdog_service_are_bounded(self):
        self.run_case("deadlines")

    def test_start_and_result_errors_propagate_without_false_free(self):
        self.run_case("failures")

    def test_actual_beacon_cad_uses_sx1276_virtual_override_and_cleans_up(self):
        self.run_case("beacon")

    def test_old_free_on_done_and_nonvirtual_dispatch_fail_negative_controls(self):
        controls = (
            (self.source.replace("return getChannelScanResult();", "return(RADIOLIB_CHANNEL_FREE);", 1), "immediate"),
            (self.source.replace("virtual int16_t performChannelScanWithTimeout", "int16_t performChannelScanWithTimeout", 1)
             .replace("unsigned long timeout_ms) override", "unsigned long timeout_ms)", 1), "beacon"),
        )
        for index, (source, case) in enumerate(controls):
            with self.subTest(case=case):
                self.assertNotEqual(source, self.source)
                binary = self.compile_source(source, "broken-" + str(index))
                result = subprocess.run([str(binary), case], capture_output=True, text=True,
                                        cwd=self.work, timeout=30)
                self.assertNotEqual(result.returncode, 0, "unsafe CAD implementation unexpectedly passed")
                self.assertIn("Assertion", result.stderr)


if __name__ == "__main__":
    unittest.main()
