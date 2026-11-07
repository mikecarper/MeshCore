#!/usr/bin/env python3
"""Run saved rotary commands, real GPIO driver and filesystem transactions."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_regulator_settings import FILESYSTEM

ROOT = Path(__file__).resolve().parents[1]

ARDUINO = r'''
#pragma once
#include <stdint.h>
#define LOW 0
#define HIGH 1
#define INPUT 0
#define INPUT_PULLUP 2
#define CHANGE 3
void pinMode(int pin, int mode);
int digitalRead(int pin);
uint32_t millis();
void attachInterrupt(uint32_t, void (*)(), int);
void detachInterrupt(uint32_t);
'''

BOARD = r'''
#pragma once
#include <stdint.h>
namespace mesh {
struct MainBoard {
  uint64_t denied = (UINT64_C(1) << 20) | (UINT64_C(1) << 37);
  bool isPeripheralPinAvailable(uint8_t pin, bool pullup = false) const {
    return pin < 64 && !(denied & (UINT64_C(1) << pin)) && !(pullup && pin == 35);
  }
  bool isPeripheralButtonAvailable(uint8_t pin, bool pullup = true) const {
    return pin == 37 || isPeripheralPinAvailable(pin, pullup);
  }
  uint16_t peripheralPinIdentity(uint8_t pin) const {
    return pin == 13 ? 10 : pin == 62 ? UINT16_MAX : pin;
  }
};
}
'''

PRESENCE = r'''
#pragma once
namespace mesh {
template <typename FS> bool filePresence(FS* fs, const char* path, bool& present) {
  if (!fs || fs->fail_metadata) return false;
  present = fs->exists(path); return true;
}
}
'''

HARNESS = r'''
#include <cassert>
#include <cstring>
#include <string>
#include <algorithm>
#include <Arduino.h>
#include <MeshCore.h>
#include <helpers/IdentityStore.h>
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#undef ATOMIC_FILE_WRITER_IMPLEMENTATION
#endif
#include <helpers/RotarySettings.h>
#include <helpers/ui/RotaryInput.h>
#include <helpers/RuntimePinClaims.h>

using namespace mesh::ui;
using mesh::pins::Owner;
static unsigned gpio_writes = 0;
static uint32_t now = 0;
static int levels[64], modes[64];
void pinMode(int pin, int mode) {
  assert(pin >= 0 && pin < 64); ++gpio_writes; modes[pin] = mode;
}
int digitalRead(int pin) { assert(pin >= 0 && pin < 64); return levels[pin]; }
uint32_t millis() { return now; }
void attachInterrupt(uint32_t pin, void (*)(), int mode) {
  assert(pin < 64 && mode == CHANGE);
}
void detachInterrupt(uint32_t pin) { assert(pin < 64); }
static const char* const path = "/rotary_v1";
static const char* const backup = "/rotary_v1.bak";
static std::string command(const char* text) {
  char reply[200] = {};
  assert(handleRotaryCommand(text, reply, sizeof(reply)));
  return reply;
}
static uint64_t mask(int a, int b, int button = -1) {
  return mesh::pins::pinMask(a) | mesh::pins::pinMask(b) |
      (button < 0 ? 0 : mesh::pins::pinMask(button));
}
static void reset(mesh::MainBoard& board, MockFS& fs, bool supported = true) {
  beginRotarySettings(board, nullptr, false);
  mesh::pins::release(Owner::UserGpio, UINT64_MAX);
  mesh::pins::release(Owner::Outputs, UINT64_MAX);
  mesh::pins::release(Owner::Notifications, UINT64_MAX);
  std::fill(levels, levels + 64, HIGH);
  gpio_writes = 0;
  assert(!beginRotarySettings(board, &fs, supported));
  assert(gpio_writes == 0 && customRotaryInput() == nullptr);
}
static void setup(mesh::MainBoard& board, MockFS& fs) {
  reset(board, fs);
  assert(command("set rotary.pins 10,11,12") == "OK");
  assert(!customRotaryInput() && gpio_writes == 0);
  assert(command("set rotary on") == "OK");
  assert(customRotaryInput() && rotarySettingsEnabled());
  assert(mesh::pins::held(Owner::Rotary) == mask(10, 11, 12));
}
static void defaults_and_persistence() {
  mesh::MainBoard board; MockFS fs;
  reset(board, fs);
  assert(command("get rotary") == "rotary off pins -1,-1,-1 steps 4 reverse off pullup on");
  assert(fs.writes == 0);
  assert(command("set rotary on").find("Error:") == 0);
  assert(fs.writes == 0 && gpio_writes == 0);
  assert(command("set rotary.pins 10, 11, 37") == "OK");
  assert(fs.files[path].size() == 16 && gpio_writes == 0);
  assert(command("set rotary.steps 255") == "OK");
  assert(command("set rotary.reverse on") == "OK");
  assert(command("set rotary on") == "OK");
  assert(customRotaryInput() && rotaryOwnsButtonPin(37) && !rotaryOwnsButtonPin(12));
  assert(modes[37] == INPUT_PULLUP);
  assert(command("get rotary.pins") == "rotary.pins 10,11,37");
  assert(command("get rotary.steps") == "rotary.steps 255");
  assert(command("get rotary.reverse") == "rotary.reverse on");
  assert(command("get rotary.pullup") == "rotary.pullup on");
  assert(beginRotarySettings(board, &fs, true));
  assert(customRotaryInput() && rotaryOwnsButtonPin(37));
  assert(command("set rotary off") == "OK");
  assert(!customRotaryInput() && !rotaryOwnsButtonPin(37));
  assert(mesh::pins::held(Owner::Rotary) == 0 && modes[37] == INPUT);
  assert(!beginRotarySettings(board, &fs, true));
  assert(!customRotaryInput());
  assert(command("set rotary.pins 30,31") == "OK");
  assert(command("get rotary.pins") == "rotary.pins 30,31,-1");
  assert(command("set rotary.pullup off") == "OK");
  assert(command("set rotary on") == "OK");
  assert(modes[30] == INPUT && modes[31] == INPUT);
  assert(!rotaryOwnsButtonPin(255));
  assert(command("set rotary.pins 30,31,10") == "OK");
  assert(rotaryOwnsButtonPin(10) && rotaryOwnsButtonPin(13)); // Arduino aliases share ownership.
}
static void invalid_and_claimed() {
  mesh::MainBoard board; MockFS fs; setup(board, fs);
  const auto original = fs.files[path];
  const unsigned original_gpio = gpio_writes, original_writes = fs.writes;
  const char* invalid[] = {"set rotary.pins 10", "set rotary.pins 10,10",
    "set rotary.pins 10,13", "set rotary.pins 10,11,13", "set rotary.pins 10,11,10",
    "set rotary.pins -1,11", "set rotary.pins 10,11,-2", "set rotary.pins 10,11,64",
    "set rotary.pins 10,11,12junk", "set rotary.pins 10,11,12,14",
    "set rotary.pins 10,11,", "set rotary.pins 10,20", "set rotary.pins 10,62",
    "set rotary.pins 10,35", "set rotary.steps 0", "set rotary.steps 256",
    "set rotary.steps 99999999999999", "set rotary.steps -1", "set rotary.steps 3x",
    "set rotary.reverse true", "set rotary.pullup 0", "set rotary ON", "set rotary on extra"};
  for (const char* text : invalid) {
    assert(command(text).find("Error:") == 0);
    assert(customRotaryInput() && fs.files[path] == original);
    assert(gpio_writes == original_gpio && fs.writes == original_writes);
    assert(mesh::pins::held(Owner::Rotary) == mask(10,11,12));
  }
  for (Owner owner : {Owner::UserGpio, Owner::Outputs, Owner::Notifications}) {
    assert(mesh::pins::tryClaim(owner, mesh::pins::pinMask(30)));
    assert(command("set rotary.pins 30,31").find("Error:") == 0);
    assert(gpio_writes == original_gpio && fs.writes == original_writes);
    mesh::pins::release(owner, mesh::pins::pinMask(30));
  }
  assert(command("set rotary.pullup off") == "OK");
  assert(command("set rotary.pins 10,35") == "OK");
  const unsigned before_pullup = gpio_writes;
  assert(command("set rotary.pullup on").find("Error:") == 0);
  assert(gpio_writes == before_pullup && command("get rotary.pullup") == "rotary.pullup off");
  char reply[4] = {'a','b','c','d'};
  assert(handleRotaryCommand("get rotary", reply, 1) && reply[0] == 0 && reply[1] == 'b');
  assert(!handleRotaryCommand(nullptr, reply, sizeof(reply)));
  assert(!handleRotaryCommand("get rotary", nullptr, 4));
  assert(!handleRotaryCommand("get rotary", reply, 0));
  for (const char* text : {"", "g", "get", "get ", "get rotar", "get rotaryX", "set rotar on"})
    assert(!handleRotaryCommand(text, reply, sizeof(reply)));
  assert(command("get rotary.pins extra").find("Error:") == 0);
  assert(command("get rotary.unknown").find("Error:") == 0);
  const auto available = command("get rotary.pins.available");
  assert(available.find("rotary.pins.available ") == 0);
  assert(available.find("...") != std::string::npos && available.size() < 160);
  assert(command("set rotary.pins.available on").find("read-only") != std::string::npos);
}
static uint64_t expected_union;
static unsigned observed_union;
static void observe_claims() {
  ++observed_union;
  assert(mesh::pins::held(Owner::Rotary) == expected_union);
  assert(!mesh::pins::tryClaim(Owner::Outputs, mesh::pins::pinMask(30)));
}
static void save_failures_and_union() {
  for (unsigned failure = 0; failure < 5; ++failure) {
    mesh::MainBoard board; MockFS fs; setup(board, fs);
    const auto original = fs.files[path];
    const unsigned before = gpio_writes;
    if (failure == 0) fs.fail_open = true;
    if (failure == 1) fs.fail_write = true;
    if (failure == 2) fs.corrupt_write = true;
    if (failure == 3) fs.fail_rename_from = "/rotary_v1.tmp";
    if (failure == 4) fs.fail_read_path = path;
    assert(command("set rotary.pins 30,31").find("save unconfirmed") != std::string::npos);
    assert(customRotaryInput() && command("get rotary.pins") == "rotary.pins 10,11,12");
    assert(gpio_writes == before && mesh::pins::held(Owner::Rotary) == mask(10,11,12));
    if (failure != 4) assert(fs.files[path] == original);
    fs.fail_open = fs.fail_write = fs.corrupt_write = false;
    fs.fail_rename_from.clear(); fs.fail_read_path.clear();
    // Public readback uncertainty blocks later writes until startup reload.
    if (failure == 4) {
      const unsigned writes = fs.writes;
      assert(command("set rotary off").find("save unconfirmed") != std::string::npos);
      assert(fs.writes == writes && gpio_writes == before);
    }
  }
  mesh::MainBoard board; MockFS fs; setup(board, fs);
  expected_union = mask(10,11,12) | mask(30,31); observed_union = 0;
  fs.on_write = observe_claims;
  assert(command("set rotary.pins 30,31") == "OK");
  assert(observed_union > 0 && mesh::pins::held(Owner::Rotary) == mask(30,31));
  assert(modes[10] == INPUT && modes[12] == INPUT && modes[30] == INPUT_PULLUP);
}
static void recovery_and_corruption() {
  mesh::MainBoard board; MockFS fs; setup(board, fs);
  const auto saved = fs.files[path];
  fs.files[backup] = saved; fs.files.erase(path);
  assert(beginRotarySettings(board, &fs, true));
  assert(fs.files[path] == saved && !fs.exists(backup));
  for (unsigned i = 0; i < saved.size(); ++i) {
    fs.files[path] = saved; fs.files[path][i] ^= 1;
    beginRotarySettings(board, nullptr, false); gpio_writes = 0;
    assert(!beginRotarySettings(board, &fs, true));
    assert(!customRotaryInput() && gpio_writes == 0 && mesh::pins::held(Owner::Rotary) == 0);
  }
  for (unsigned length = 0; length <= saved.size() + 1; ++length) {
    if (length == saved.size()) continue;
    fs.files[path] = saved; fs.files[path].resize(length);
    gpio_writes = 0;
    assert(!beginRotarySettings(board, &fs, true));
    assert(gpio_writes == 0 && !customRotaryInput());
  }
  fs.files[path] = saved; fs.fail_metadata = true; gpio_writes = 0;
  assert(!beginRotarySettings(board, &fs, true) && gpio_writes == 0);
  fs.fail_metadata = false; fs.fail_read_path = path;
  assert(!beginRotarySettings(board, &fs, true) && gpio_writes == 0);
  fs.fail_read_path.clear();
  assert(mesh::pins::tryClaim(Owner::Outputs, mesh::pins::pinMask(10)));
  assert(!beginRotarySettings(board, &fs, true) && gpio_writes == 0);
  mesh::pins::release(Owner::Outputs, mesh::pins::pinMask(10));
  assert(!beginRotarySettings(board, &fs, false) && gpio_writes == 0);
  assert(command("set rotary on").find("unsupported") != std::string::npos);
  fs.files[path] = saved; board.denied |= mesh::pins::pinMask(10);
  assert(!beginRotarySettings(board, &fs, true) && gpio_writes == 0);
}
static void display_handoff() {
  mesh::MainBoard board; MockFS fs; setup(board, fs);
  const auto saved = fs.files[path];
  const unsigned writes = fs.writes;
  assert(!setRotaryUiSupported(false));
  assert(!customRotaryInput() && !rotaryOwnsButtonPin(12));
  assert(mesh::pins::held(Owner::Rotary) == 0);
  assert(command("get rotary").find("rotary on ") == 0);
  assert(command("get rotary").find("inactive:no display") != std::string::npos);
  assert(fs.writes == writes && fs.files[path] == saved);
  assert(mesh::pins::tryClaim(Owner::Outputs, mesh::pins::pinMask(10)));
  const unsigned before = gpio_writes;
  assert(!setRotaryUiSupported(true));
  assert(gpio_writes == before && !customRotaryInput());
  assert(command("get rotary").find("inactive:pin conflict") != std::string::npos);
  mesh::pins::release(Owner::Outputs, mesh::pins::pinMask(10));
  levels[12] = LOW;
  assert(setRotaryUiSupported(true));
  assert(customRotaryInput() && !customRotaryInput()->isButtonPressed());
  assert(fs.writes == writes && fs.files[path] == saved);
  // A saved enabled profile remains selected through a screenless startup.
  assert(!beginRotarySettings(board, &fs, false));
  assert(!customRotaryInput());
  assert(command("get rotary").find("inactive:no display") != std::string::npos);
  assert(setRotaryUiSupported(true) && customRotaryInput());
}
static void build_defaults() {
#if defined(UI_ROTARY_INPUT_GPIO) && UI_ROTARY_INPUT_GPIO
  mesh::MainBoard board; MockFS fs;
  std::fill(levels, levels + 64, HIGH);
  assert(beginRotarySettings(board, &fs, true));
  assert(command("get rotary.pins") == "rotary.pins 30,31,37");
  assert(command("get rotary.steps") == "rotary.steps 2");
  assert(command("get rotary.reverse") == "rotary.reverse on");
  assert(fs.writes == 0);
  assert(command("set rotary off") == "OK");
  assert(!beginRotarySettings(board, &fs, true)); // Saved off overrides build defaults.
  fs.files[path] = {'x'}; gpio_writes = 0;
  assert(!beginRotarySettings(board, &fs, true) && gpio_writes == 0);
  fs.files.clear(); board.denied |= mesh::pins::pinMask(30);
  assert(!beginRotarySettings(board, &fs, true) && gpio_writes == 0);
#endif
}
int main() {
#if defined(UI_ROTARY_INPUT_GPIO) && UI_ROTARY_INPUT_GPIO
  build_defaults();
#else
  defaults_and_persistence(); invalid_and_claimed(); save_failures_and_union(); recovery_and_corruption(); display_handoff();
#endif
}
'''


class RotarySettingsTests(unittest.TestCase):
    def test_settings_across_filesystems(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("native C++ compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="meshcore-rotary-settings-") as directory:
            folder = Path(directory)
            (folder / "helpers").mkdir()
            filesystem = FILESYSTEM.replace(
                "  void flush() {}", "  void flush() {}\n  bool setBufferSize(size_t) { return true; }"
            ).replace(
                "#if !defined(NRF52_PLATFORM)",
                "#if !defined(NRF52_PLATFORM) && !defined(STM32_PLATFORM)",
            ).replace(
                "  unsigned writes=0,renames=0;",
                "  unsigned writes=0,renames=0; bool fail_metadata=false; void (*on_write)()=nullptr;",
            ).replace("  ++fs->writes;", "  ++fs->writes; if(fs->on_write)fs->on_write();")
            for name, content in {
                "helpers/IdentityStore.h": filesystem,
                "helpers/FilePresence.h": PRESENCE,
                "MeshCore.h": BOARD, "Arduino.h": ARDUINO, "target.h": "#pragma once\n",
                "helpers/ContactFileTransaction.h": (ROOT / "src/helpers/ContactFileTransaction.h").read_text(),
                "helpers/PersistentStoreFormat.h": (ROOT / "src/helpers/PersistentStoreFormat.h").read_text(),
            }.items():
                (folder / name).write_text(content, encoding="ascii")
            source = folder / "test.cpp"
            source.write_text(HARNESS, encoding="ascii")
            for platform, flags in {
                "esp32": ["ESP32_PLATFORM=1"],
                "nrf52": ["NRF52_PLATFORM=1"],
                "rp2040": ["RP2040_PLATFORM=1"],
                "defaults": ["RP2040_PLATFORM=1", "UI_ROTARY_INPUT_GPIO=1", "PIN_ROTARY_CLK=30",
                             "PIN_ROTARY_DT=31", "PIN_ROTARY_BUTTON=37",
                             "ROTARY_TRANSITIONS_PER_DETENT=2", "ROTARY_REVERSE=1"],
            }.items():
                with self.subTest(platform=platform):
                    binary = folder / platform
                    result = subprocess.run([
                        compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-function", "-g", "-O1", "-fno-omit-frame-pointer",
                        "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                        "-DMESH_CUSTOM_PERIPHERALS=1", *["-D" + flag for flag in flags],
                        "-I", str(folder), "-I", str(ROOT / "src"), str(source),
                        str(ROOT / "src/helpers/RotarySettings.cpp"),
                        str(ROOT / "src/helpers/ui/GpioRotaryInput.cpp"), "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            # Opted-out images use header-only stubs and do not link a GPIO
            # driver, transaction writer, or custom-pin registry.
            source.write_text(r'''
#include <cassert>
#include <MeshCore.h>
#include <helpers/RotarySettings.h>
int main() {
  mesh::MainBoard board;
  assert(!mesh::ui::beginRotarySettings(board, nullptr, true));
  assert(!mesh::ui::customRotaryInput());
  assert(!mesh::ui::rotarySettingsEnabled());
  assert(!mesh::ui::setRotaryUiSupported(true));
  assert(!mesh::ui::rotaryOwnsButtonPin(1));
  char reply[4] = {};
  assert(!mesh::ui::handleRotaryCommand("get rotary", reply, sizeof(reply)));
}
''', encoding="ascii")
            binary = folder / "disabled"
            result = subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-DMESH_CUSTOM_PERIPHERALS=0", "-I", str(folder), "-I", str(ROOT / "src"),
                str(source), str(ROOT / "src/helpers/RotarySettings.cpp"), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
