#!/usr/bin/env python3
"""Confirm lab observation leaves the existing SX126x command sequence intact."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

STUB = r'''
#pragma once
#include <stdint.h>
#include <string>
extern uint32_t clock_ms;
extern bool errors, stuck;
extern std::string calls;
inline uint32_t millis() { return clock_ms; }
#define RADIOLIB_SX126X_CALIBRATE_ALL 0x7f
#define RADIOLIB_SX126X_CMD_CALIBRATE 0x89
#define RADIOLIB_SX126X_STANDBY_RC 0
struct Hal {
  void delay(uint32_t n) { clock_ms += n; }
  int digitalRead(int) { return stuck; }
  void yield() { ++clock_ms; }
};
struct Module {
  Hal storage; Hal* hal = &storage;
  int getGpio() { return 13; }
  int16_t SPIwriteStream(int, uint8_t*, int, bool, bool) {
    calls += "calibrate,"; return errors ? -3 : 0;
  }
};
struct SX126x {
  Module storage; Module* mod = &storage; float freqMHz = 904.525f;
  int16_t sleep(bool) { calls += "sleep,"; ++clock_ms; return errors ? -1 : 0; }
  int16_t standby(uint8_t, bool) { calls += "standby,"; return errors ? -2 : 0; }
  int16_t calibrateImage(float) { calls += "image,"; return errors ? -4 : 0; }
  int16_t setDio2AsRfSwitch(bool) { calls += "dio2,"; return errors ? -5 : 0; }
  int16_t setRxBoostedGainMode(bool) { calls += "gain,"; return errors ? -6 : 0; }
  int16_t readRegister(int, uint8_t* data, int) {
    calls += "readpatch,"; *data = 0x24; return errors ? -7 : 0;
  }
  int16_t writeRegister(int, uint8_t* data, int) {
    calls += *data == 0x25 ? "writepatch," : "BADPATCH,";
    return errors ? -8 : 0;
  }
};
'''

MAIN = r'''
#include <assert.h>
#include <iostream>
#include <RadioLib.h>
uint32_t clock_ms = 0;
bool errors = false, stuck = false;
std::string calls;
#include "helpers/radiolib/SX126xReset.h"
int main(int argc, char** argv) {
  assert(argc == 2);
  errors = std::string(argv[1]) == "errors";
  stuck = std::string(argv[1]) == "timeout";
  SX126x radio;
#if MESH_AGC_TIMING_DIAGNOSTICS
  AGCTimingDiagnostics diagnostic;
  diagnostic.sequenceRequest(); diagnostic.admit(0);
  sx126xResetAGC(&radio, true, &diagnostic);
  diagnostic.helperFinished(clock_ms * 1000);
  assert(diagnostic.command_failures == (errors ? 8U : 0U));
  assert(diagnostic.failed_sequences == ((errors || stuck) ? 1U : 0U));
  assert(diagnostic.poll_timeouts == (stuck ? 1U : 0U));
  assert(diagnostic.last_error == (errors ? -1 : 0));
  if (stuck) assert(diagnostic.last_error_step == AGCTimingDiagnostics::Step::Poll);
#else
  sx126xResetAGC(&radio, true);
#endif
  std::cout << calls << clock_ms << "\n";
}
'''


class AGCTimingResetSequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("AGC sequence test requires a host C++ compiler")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-agc-sequence-")
        cls.addClassCleanup(cls.work.cleanup)
        folder = Path(cls.work.name)
        (folder / "RadioLib.h").write_text(STUB, encoding="ascii")
        (folder / "main.cpp").write_text(MAIN, encoding="ascii")
        cls.binaries = []
        for flag in (0, 1):
            binary = folder / ("sequence-" + str(flag))
            result = subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-DMESH_AGC_TIMING_DIAGNOSTICS=" + str(flag),
                "-DSX126X_DIO2_AS_RF_SWITCH=1", "-DSX126X_RX_BOOSTED_GAIN=1",
                "-DSX126X_REGISTER_PATCH=1", "-I" + str(folder),
                "-I" + str(ROOT / "src"), str(folder / "main.cpp"),
                "-o", str(binary)], capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries.append(binary)

    def compare(self, scenario, elapsed):
        outputs = []
        for binary in self.binaries:
            result = subprocess.run([str(binary), scenario], capture_output=True,
                                    text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0],
                         "sleep,standby,calibrate,image,dio2,gain,readpatch,writepatch,"
                         + str(elapsed) + "\n")

    def test_success_preserves_all_commands_and_delays(self):
        self.compare("success", 6)

    def test_errors_are_observed_without_short_circuiting_commands(self):
        self.compare("errors", 6)

    def test_extra_poll_timeout_preserves_existing_delay_and_commands(self):
        self.compare("timeout", 57)


if __name__ == "__main__":
    unittest.main()
