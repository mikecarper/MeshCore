#!/usr/bin/env python3
"""Exercise the production DAC PA wrapper with a recording radio transport."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ElrsPowerTest(unittest.TestCase):
    def test_linkflow_uses_approved_calibration_and_keeps_ota(self):
        target = (ROOT / "variants/geprc_linkflow_900/target.cpp").read_text()
        levels = re.findall(r"\{\s*(\d+),\s*(\d+)\s*\}", target)
        self.assertEqual(levels, [("17", "0"), ("20", "22"), ("24", "50"),
                                  ("27", "75"), ("30", "130"), ("33", "225")])
        config = (ROOT / "variants/geprc_linkflow_900/platformio.ini").read_text()
        self.assertIn("MIN_LORA_TX_POWER=17", config)
        self.assertIn("MAX_LORA_TX_POWER=30", config)
        self.assertNotIn("-<helpers/ota/>", config)
        unflags = config.split("build_unflags =", 1)[1].split("build_src_filter", 1)[0]
        self.assertNotIn("ENABLE_OTA", unflags)

    def test_startup_recovery_limits_and_radio_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "Arduino.h").write_text("#include <cstdint>\n#include <cstddef>\ninline void dacWrite(uint8_t, uint8_t) {}\n")
            (path / "CustomSX1276Wrapper.h").write_text(r'''
#pragma once
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_INVALID_OUTPUT_POWER -1
namespace mesh { struct MainBoard {}; }
struct CustomSX1276 {
  int power = 17, error = 0, calls = 0;
  bool rfo = false;
  int16_t setOutputPower(int8_t dbm, bool use_rfo) {
    ++calls;
    if (error) return error;
    power = dbm; rfo = use_rfo; return 0;
  }
};
class CustomSX1276Wrapper {
protected:
  CustomSX1276* _radio;
  int8_t cached = 0;
  virtual int16_t applyCachedTxPower(int8_t dbm) = 0;
public:
  CustomSX1276Wrapper(CustomSX1276& radio, mesh::MainBoard&) : _radio(&radio) {}
  bool setTxPower(int8_t dbm) {
    if (applyCachedTxPower(dbm)) return false;
    cached = dbm; return true;
  }
  bool recover() {
    _radio->power = 17; _radio->rfo = false;
    return applyCachedTxPower(cached) == 0;
  }
};
''')
            # Preserve the production wrapper; substitute only its base transport.
            (path / "DacPaSX1276Wrapper.h").write_text(
                (ROOT / "src/helpers/radiolib/DacPaSX1276Wrapper.h").read_text())
            (path / "test.cpp").write_text(r'''
#include "DacPaSX1276Wrapper.h"
#include <cassert>
struct Driver : DacPaSX1276Wrapper {
  using DacPaSX1276Wrapper::DacPaSX1276Wrapper;
  int gain = -1, writes = 0;
  void writeGainControl(uint8_t code) override { gain = code; ++writes; }
};
int main() {
  mesh::MainBoard board;
  CustomSX1276 radio;
  const DacPaLevel levels[] = {{10,30}, {17,50}, {24,80}, {30,130}, {33,225}};
  Driver fixed(radio, board, 26, levels, 5, 30);
  assert(fixed.beginPowerControl(17) && radio.power == 2 && fixed.gain == 50);
  assert(fixed.setTxPower(30) && fixed.gain == 130);
  assert(fixed.recover() && radio.power == 2 && fixed.gain == 130);
  for (int dbm = -128; dbm <= 127; ++dbm) {
    const int writes = fixed.writes, calls = radio.calls;
    const bool valid = dbm >= 10 && dbm <= 30;
    assert(fixed.setTxPower(dbm) == valid);
    if (!valid) assert(fixed.writes == writes && radio.calls == calls);
  }
  assert(fixed.setTxPower(23) && fixed.gain == 50);
  radio.error = -7;
  const int writes = fixed.writes;
  assert(!fixed.setTxPower(24) && fixed.writes == writes);
  assert(!fixed.beginPowerControl(17) && fixed.writes == writes);
  radio.error = 0;
  const int8_t steps[] = {2,6,9,10,12};
  Driver stepped(radio, board, 26, levels, 5, 30, steps, true);
  assert(stepped.beginPowerControl(24) && radio.power == 9 && radio.rfo);
  assert(stepped.recover() && radio.power == 9 && radio.rfo);
  Driver rfo(radio, board, 26, levels, 5, 30, nullptr, true, -4);
  assert(rfo.beginPowerControl(17) && rfo.recover() && radio.power == -4 && radio.rfo);
  Driver capped(radio, board, 26, levels, 5, 23);
  assert(capped.beginPowerControl(33) && capped.gain == 50);
  assert(!capped.setTxPower(24));
  Driver impossible(radio, board, 26, levels, 5, 9);
  assert(!impossible.beginPowerControl(17) && impossible.writes == 0);
  Driver empty(radio, board, 26, nullptr, 0);
  assert(!empty.beginPowerControl(17) && empty.writes == 0);
}
''')
            binary = path / "power.exe"
            flags = [] if os.name == "nt" else ["-fsanitize=address,undefined"]
            result = subprocess.run(["c++", "-std=c++17", *flags, "-I", temp,
                                     str(path / "test.cpp"), "-o", str(binary)],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
