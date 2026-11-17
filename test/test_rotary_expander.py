#!/usr/bin/env python3
"""Run rotary settings and quadrature decoding against a fault-injected expander."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_rotary_settings import ARDUINO, BOARD, PRESENCE, FILESYSTEM

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <cassert>
#include <algorithm>
#include <cstring>
#include <string>
#include <Arduino.h>
#include <MeshCore.h>
#include <helpers/IdentityStore.h>
#include <helpers/RotarySettings.h>
#include <helpers/ui/RotaryInput.h>
#include <helpers/ui/GpioRotaryInput.h>
#include <helpers/I2cExpander.h>
using namespace mesh::ui;
using mesh::pins::Owner;
using Event = RotaryInputEvent;
static uint32_t now;
static int levels[64], modes[64];
static unsigned writes;
void pinMode(int pin, int mode) { assert(pin >= 0 && pin < 64); ++writes; modes[pin] = mode; }
int digitalRead(int pin) { assert(pin >= 0 && pin < 64); return levels[pin]; }
uint32_t millis() { return now; }
void attachInterrupt(uint32_t, void (*)(), int) { assert(false); }
void detachInterrupt(uint32_t) { assert(false); }
static std::string cmd(const char* text) {
  char reply[200] = {}; assert(handleRotaryCommand(text, reply, sizeof(reply))); return reply;
}
static void reset(mesh::MainBoard& board, MockFS& fs) {
  beginRotarySettings(board, nullptr, false);
  for (Owner owner : {Owner::Rotary, Owner::Outputs, Owner::UserGpio, Owner::Notifications})
    mesh::pins::release(owner, UINT64_MAX);
  mesh::expander::testState() = mesh::expander::TestState();
  mesh::expander::testState().online = true;
  std::fill(levels, levels + 64, HIGH); std::fill(modes, modes + 64, -1);
  writes = 0; now = 0;
  assert(!beginRotarySettings(board, &fs, true));
}
static Event state(unsigned ab) {
  auto& io = mesh::expander::testState();
  io.levels = (io.levels & ~3u) | ((ab & 2) ? 1u : 0u) | ((ab & 1) ? 2u : 0u);
  return customRotaryInput()->poll();
}
static void settings_and_rotation() {
  mesh::MainBoard board; MockFS fs; reset(board, fs);
  assert(cmd("set rotary.pins x0,x1,0") == "OK");
  assert(writes == 0 && fs.files["/rotary_v1"][2] == 2);
  assert(cmd("get rotary.pins") == "rotary.pins x0,x1,0");
  assert(cmd("set rotary on") == "OK");
  assert(customRotaryInput() && rotaryOwnsButtonPin(0));
  assert(!rotaryOwnsButtonPin(64));
  assert(mesh::expander::held(Owner::Rotary) == 3);
  assert(mesh::pins::held(Owner::Rotary) == 1);
  assert(modes[0] == INPUT_PULLUP);
  const auto initial_pairs = mesh::expander::testState().pair_reads;
  for (unsigned cycle = 0; cycle < 8; ++cycle) {
    for (unsigned ab : {1,0,2}) assert(state(ab) == Event::None);
    assert(state(3) == Event::Next);
  }
  assert(mesh::expander::testState().pair_reads == initial_pairs + 32);
  assert(mesh::expander::testState().single_reads == 0); // One coherent port snapshot per AB sample.
  levels[0] = LOW; now = 100; assert(customRotaryInput()->poll() == Event::None);
  now = 125; assert(customRotaryInput()->poll() == Event::None);
  assert(customRotaryInput()->isButtonPressed());
  levels[0] = HIGH; now = 200; assert(customRotaryInput()->poll() == Event::None);
  now = 225; assert(customRotaryInput()->poll() == Event::Select);
  assert(beginRotarySettings(board, &fs, true));
  assert(cmd("get rotary.pins") == "rotary.pins x0,x1,0");
  assert(cmd("set rotary off") == "OK");
  assert(mesh::expander::held(Owner::Rotary) == 0 && mesh::pins::held(Owner::Rotary) == 0);
  assert(cmd("set rotary.pins 6,43,0") == "OK");
  assert(fs.files["/rotary_v1"][2] == 1); // Native-only persistence stays compatible.
}
static void parsing_claims_and_failed_save() {
  mesh::MainBoard board; MockFS fs; reset(board, fs);
  assert(cmd("set rotary.pins x0,x1,0") == "OK" && cmd("set rotary on") == "OK");
  const auto original = fs.files["/rotary_v1"];
  const unsigned original_writes = writes;
  for (const char* invalid : {"set rotary.pins 64,x1,0", "set rotary.pins x0,65,0",
      "set rotary.pins x16,x1,0", "set rotary.pins x0,x0,0", "set rotary.pins x0,x1,x1",
      "set rotary.pins x-1,x1,0", "set rotary.pins x0junk,x1,0"}) {
    assert(cmd(invalid).find("Error:") == 0);
    assert(fs.files["/rotary_v1"] == original && writes == original_writes);
  }
  assert(mesh::expander::tryClaim(Owner::Outputs, 4));
  assert(cmd("set rotary.pins x2,x3,0").find("Error:") == 0);
  assert(mesh::expander::held(Owner::Rotary) == 3);
  mesh::expander::release(Owner::Outputs, 4);
  fs.fail_write = true;
  assert(cmd("set rotary.pins x2,x3,0").find("save unconfirmed") != std::string::npos);
  fs.fail_write = false;
  assert(fs.files["/rotary_v1"] == original && writes == original_writes);
  assert(mesh::expander::held(Owner::Rotary) == 3);
  assert(mesh::expander::tryClaim(Owner::Outputs, 12)); // Added claims rolled back after failed save.
  mesh::expander::release(Owner::Outputs, 12);
  assert(cmd("set rotary.pins x2,x3,0") == "OK");
  assert(mesh::expander::held(Owner::Rotary) == 12);
}
static void bus_loss_and_offline_boot() {
  mesh::MainBoard board; MockFS fs; reset(board, fs);
  assert(cmd("set rotary.pins x0,x1,0") == "OK" && cmd("set rotary on") == "OK");
  assert(state(1) == Event::None); // Incomplete detent before loss.
  auto* input = customRotaryInput();
  mesh::expander::testState().fail_read = true;
  assert(input->poll() == Event::None && !input->isReady());
  assert(!customRotaryInput());
  assert(modes[0] == INPUT_PULLUP && mesh::pins::held(Owner::Rotary) == 1);
  // The board's ordinary button reader can safely take over, while the
  // stopped rotary input emits no stale gesture even if the switch changes.
  levels[0] = LOW; now += 1500;
  assert(input->poll() == Event::None && !input->isButtonPressed() && !input->needsPolling());
  levels[0] = HIGH;
  mesh::expander::testState().fail_read = false;
  mesh::expander::testState().online = true;
  for (unsigned n = 0; n < 4; ++n) assert(input->poll() == Event::None); // No automatic reconnect replay.
  assert(cmd("set rotary off") == "OK");
  assert(mesh::expander::held(Owner::Rotary) == 0 && mesh::pins::held(Owner::Rotary) == 0);
  assert(modes[0] == INPUT);
  assert(cmd("set rotary on") == "OK");
  assert(state(0) == Event::None && state(2) == Event::None && state(3) == Event::None);
  assert(state(1) == Event::Next); // Fresh four-transition detent only.
  mesh::expander::testState().online = false;
  assert(!beginRotarySettings(board, &fs, true));
  assert(cmd("get rotary.pins") == "rotary.pins x0,x1,0");
  assert(!customRotaryInput() && mesh::expander::held(Owner::Rotary) == 0);
  assert(cmd("set rotary off") == "OK"); // Offline hardware cannot prevent releasing an owned configuration.
}
static void type_limits_and_pulls() {
  mesh::MainBoard board; MockFS fs; reset(board, fs);
  auto& io = mesh::expander::testState(); io.count = 8;
  assert(cmd("set rotary.pins x0,x8,0").find("Error:") == 0);
  io.require_pullups = true;
  assert(cmd("set rotary.pins x0,x1,0") == "OK" && cmd("set rotary on") == "OK");
  assert(cmd("set rotary.pullup off").find("Error:") == 0);
  assert(cmd("get rotary.pullup") == "rotary.pullup on");
  assert(cmd("set rotary off") == "OK");
  io.count = 16; io.require_pullups = false; io.input_allowed &= ~(1u << 7);
  assert(cmd("set rotary.pins x0,x7,0").find("Error:") == 0);
}
static void virtual_button_and_init_failure() {
  mesh::MainBoard board; MockFS fs; reset(board, fs);
  auto& io = mesh::expander::testState(); io.levels &= ~4u;
  assert(cmd("set rotary.pins x0,x1,x2") == "OK" && cmd("set rotary on") == "OK");
  assert(!customRotaryInput()->isButtonPressed());
  now = 100; io.levels |= 4; assert(customRotaryInput()->poll() == Event::None);
  now = 125; assert(customRotaryInput()->poll() == Event::None);
  now = 200; io.levels &= ~4u; assert(customRotaryInput()->poll() == Event::None);
  now = 225; assert(customRotaryInput()->poll() == Event::None);
  now = 300; io.levels |= 4; assert(customRotaryInput()->poll() == Event::None);
  now = 325; assert(customRotaryInput()->poll() == Event::Select);
  assert(cmd("set rotary off") == "OK");
  io.fail_configuration = true;
  assert(cmd("set rotary on").find("initialization failed") != std::string::npos);
  assert(!customRotaryInput());
  assert(cmd("set rotary off") == "OK" && mesh::expander::held(Owner::Rotary) == 0);
}
static void record_versions_and_display_handoff() {
  mesh::MainBoard board; MockFS fs; reset(board, fs);
  assert(cmd("set rotary.pins x0,x1,0") == "OK" && cmd("set rotary on") == "OK");
  const auto saved = fs.files["/rotary_v1"];
  assert(saved[2] == 2);
  assert(!setRotaryUiSupported(false));
  assert(!customRotaryInput() && mesh::expander::held(Owner::Rotary) == 0);
  assert(setRotaryUiSupported(true) && customRotaryInput());
  assert(fs.files["/rotary_v1"] == saved);
  // A virtual endpoint in an old record must not be accepted merely because
  // the record has a valid checksum. Old/native records cannot acquire x pins.
  auto forged = saved; forged[2] = 1;
  uint16_t crc = 0xffff;
  for (unsigned i = 0; i < 14; ++i) {
    crc ^= uint16_t(forged[i]) << 8;
    for (unsigned bit = 0; bit < 8; ++bit)
      crc = (crc & 0x8000) ? uint16_t((crc << 1) ^ 0x1021) : uint16_t(crc << 1);
  }
  forged[14] = crc; forged[15] = crc >> 8; fs.files["/rotary_v1"] = forged;
  beginRotarySettings(board, nullptr, false); writes = 0;
  assert(!beginRotarySettings(board, &fs, true));
  assert(!customRotaryInput() && writes == 0 && mesh::expander::held(Owner::Rotary) == 0);
}
static void changed_expander_cannot_inherit_saved_input() {
  for (const uint16_t replacement : {uint16_t(0x0126), uint16_t(0x0227)}) {
    mesh::MainBoard board; MockFS fs; reset(board, fs);
    assert(cmd("set rotary.pins x0,x1,0") == "OK" && cmd("set rotary on") == "OK");
    const auto saved = fs.files["/rotary_v1"];
    assert(saved[10] == 0x27 && saved[11] == 1);
    mesh::expander::testState().online = false;
    assert(!beginRotarySettings(board, &fs, true));
    writes = 0;
    mesh::expander::testState().identity = replacement;
    mesh::expander::testState().online = true;
    assert(cmd("set rotary on").find("Error:") == 0);
    assert(!setRotaryUiSupported(true));
    assert(!customRotaryInput() && writes == 0 && fs.files["/rotary_v1"] == saved);
    assert(mesh::expander::held(Owner::Rotary) == 0 && mesh::pins::held(Owner::Rotary) == 0);
    assert(cmd("set rotary off") == "OK");
    assert(fs.files["/rotary_v1"][10] == 0x27 && fs.files["/rotary_v1"][11] == 1);
    // An explicit new pin selection is the deliberate rebind operation.
    assert(cmd("set rotary.pins x0,x1,0") == "OK");
    assert(fs.files["/rotary_v1"][10] == uint8_t(replacement));
    assert(fs.files["/rotary_v1"][11] == uint8_t(replacement >> 8));
    assert(cmd("set rotary on") == "OK" && customRotaryInput());
  }
}
int main() {
  settings_and_rotation(); parsing_claims_and_failed_save(); bus_loss_and_offline_boot();
  type_limits_and_pulls(); virtual_button_and_init_failure(); record_versions_and_display_handoff();
  changed_expander_cannot_inherit_saved_input();
}
'''


class RotaryExpanderTests(unittest.TestCase):
    def test_actual_rotary_with_expander_failures(self):
        compiler = os.environ.get("CXX") or shutil.which("g++")
        if not compiler:
            self.skipTest("native C++ compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="meshcore-rotary-expander-") as directory:
            folder = Path(directory)
            (folder / "helpers").mkdir()
            filesystem = FILESYSTEM.replace(
                "  void flush() {}", "  void flush() {}\n  bool setBufferSize(size_t) { return true; }"
            ).replace("  unsigned writes=0,renames=0;", "  unsigned writes=0,renames=0; bool fail_metadata=false;")
            for name, content in {
                "helpers/IdentityStore.h": filesystem,
                "helpers/FilePresence.h": PRESENCE,
                "helpers/I2cExpander.h": (ROOT / "test/fixtures/expander_rotary/I2cExpander.h").read_text(),
                "helpers/ContactFileTransaction.h": (ROOT / "src/helpers/ContactFileTransaction.h").read_text(),
                "helpers/PersistentStoreFormat.h": (ROOT / "src/helpers/PersistentStoreFormat.h").read_text(),
                "MeshCore.h": BOARD, "Arduino.h": ARDUINO, "target.h": "#pragma once\n",
            }.items():
                (folder / name).write_text(content, encoding="ascii")
            source = folder / "test.cpp"
            source.write_text(HARNESS, encoding="ascii")
            binary = folder / "rotary-expander.exe"
            compiled = subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-g", "-O1",
                *([] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-pie", "-no-pie"]),
                "-DMESH_CUSTOM_PERIPHERALS=1", "-DESP32_PLATFORM=1",
                "-I", str(folder), "-I", str(ROOT / "src"), str(source),
                str(ROOT / "src/helpers/RotarySettings.cpp"), str(ROOT / "src/helpers/ui/GpioRotaryInput.cpp"),
                "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
