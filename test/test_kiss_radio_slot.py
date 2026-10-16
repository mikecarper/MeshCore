"""Actual KISS framing/dispatch for persistent G3 physical-slot selection."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include "KissModem.h"
#include "StationG3RadioSlot.h"
#include <algorithm>
#include <cassert>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

struct Buffer : Stream {
  std::vector<uint8_t> input, output;
  size_t at = 0, chunk = 4096;
  int availableForWrite() override { return static_cast<int>(chunk); }
  int available() override { return static_cast<int>(input.size() - at); }
  int read() override { return at < input.size() ? input[at++] : -1; }
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* data, size_t size) override {
    const size_t count = std::min(size, chunk);
    output.insert(output.end(), data, data + count);
    return count;
  }
};
struct Radio : mesh::Radio {
  unsigned sends = 0, tunes = 0;
  bool startSendRaw(const uint8_t*, uint16_t) override { ++sends; return false; }
  mesh::RadioParamApplyResult trySetPrimaryParams(const mesh::RadioProfileParams&,
      bool, const uint32_t* = nullptr) override { ++tunes; return mesh::RadioParamApplyResult::FAILED; }
};
struct Rng : mesh::RNG {
  void random(uint8_t* dest, size_t count) override { std::memset(dest, 0, count); }
};
struct Board : mesh::MainBoard {
  bool fail = false, recognized = true, lie = false;
  unsigned calls = 0, reboots = 0;
  std::string last;
  void reboot() override { ++reboots; }
  bool handleCommand(const char* command, uint32_t timestamp, char* reply) override {
    ++calls; last = command; assert(timestamp == 0);
    if (!recognized) return false;
    if (fail) { std::strcpy(reply, "Error: radio.slot could not be saved; running slot unchanged"); return true; }
    assert(std::strncmp(command, "set radio.slot ", 15) == 0);
    const char* mode = command + 15;
    if (!lie) station_g3::slotState().savedMode =
        std::strcmp(mode, "auto") == 0 ? 0 : static_cast<uint8_t>(mode[0] - '0');
    std::strcpy(reply, "OK - radio.slot saved; reboot to apply");
    return true;
  }
};
struct Sensors : SensorManager {};
static std::vector<uint8_t> expected(uint8_t response, std::vector<uint8_t> bytes = {}) {
  std::vector<uint8_t> result{KISS_FEND, KISS_CMD_SETHARDWARE, response};
  result.insert(result.end(), bytes.begin(), bytes.end());
  result.push_back(KISS_FEND);
  return result;
}
int main() {
  Buffer serial; Radio radio; Rng rng; Board board; Sensors sensors;
  mesh::LocalIdentity identity;
  KissModem modem(serial, identity, rng, radio, board, sensors); modem.begin();
  auto& state = station_g3::slotState();
  state.bootMode = 0; state.savedMode = 0; state.active = 2;
  state.detected = 3; state.candidate = 2; state.last = 2;
  // Any unintended pin operation is observable, including unchanged levels.
  for (unsigned pin = 0; pin < 64; ++pin) {
    g_mock_pin_modes[pin] = 77; g_mock_pin_levels[pin] = 88;
  }
  auto request = [&](uint8_t command, std::vector<uint8_t> bytes = {}) {
    serial.output.clear();
    serial.input.insert(serial.input.end(), {KISS_FEND, KISS_CMD_SETHARDWARE, command});
    for (uint8_t byte : bytes) {
      if (byte == KISS_FEND) serial.input.insert(serial.input.end(), {KISS_FESC, KISS_TFEND});
      else if (byte == KISS_FESC) serial.input.insert(serial.input.end(), {KISS_FESC, KISS_TFESC});
      else serial.input.push_back(byte);
    }
    serial.input.push_back(KISS_FEND);
    for (unsigned i = 0; i < 20; ++i) modem.loop();
  };
#if defined(STATION_G3_ESP32)
  request(HW_CMD_GET_RADIO_SLOT);
  assert(serial.output == expected(0xa2, {0, 2, 3, 0}));
  request(HW_CMD_GET_RADIO_SLOT, {0});
  assert(serial.output == expected(HW_RESP_ERROR, {HW_ERR_INVALID_LENGTH}));
  for (auto bytes : {std::vector<uint8_t>{}, std::vector<uint8_t>{1, 2}}) {
    request(HW_CMD_SET_RADIO_SLOT, bytes);
    assert(serial.output == expected(HW_RESP_ERROR, {HW_ERR_INVALID_LENGTH}));
  }
  for (uint8_t mode : {3, 255, int(KISS_FEND), int(KISS_FESC)}) {
    request(HW_CMD_SET_RADIO_SLOT, {mode});
    assert(serial.output == expected(HW_RESP_ERROR, {HW_ERR_INVALID_PARAM}));
  }
  assert(board.calls == 0 && state.savedMode == 0);
  for (uint8_t mode : {1, 2, 0}) {
    request(HW_CMD_SET_RADIO_SLOT, {mode});
    assert(serial.output == expected(HW_RESP_OK));
    assert(state.savedMode == mode);
    assert(board.last == (mode == 0 ? "set radio.slot auto" : mode == 1 ? "set radio.slot 1" : "set radio.slot 2"));
    request(HW_CMD_GET_RADIO_SLOT);
    assert(serial.output == expected(0xa2, {mode, 2, 3, static_cast<uint8_t>(mode != 0)}));
  }
  board.fail = true;
  request(HW_CMD_SET_RADIO_SLOT, {1});
  assert(serial.output == expected(HW_RESP_ERROR, {HW_ERR_SAVE_FAILED}));
  assert(state.savedMode == 0);
  board.fail = false; board.recognized = false;
  request(HW_CMD_SET_RADIO_SLOT, {1});
  assert(serial.output == expected(HW_RESP_ERROR, {HW_ERR_NO_CALLBACK}));
  board.recognized = true; board.lie = true;
  request(HW_CMD_SET_RADIO_SLOT, {1});
  assert(serial.output == expected(HW_RESP_ERROR, {HW_ERR_SAVE_FAILED}));
  // Status stays fixed-length even when no radio is present.
  state.active = 0; state.detected = 0;
  serial.chunk = 1;
  request(HW_CMD_GET_RADIO_SLOT);
  assert(serial.output == expected(0xa2, {0, 0, 0, 0}));
#else
  for (uint8_t command : {HW_CMD_GET_RADIO_SLOT, HW_CMD_SET_RADIO_SLOT}) {
    for (auto bytes : {std::vector<uint8_t>{}, std::vector<uint8_t>{2}, std::vector<uint8_t>{1, 2}}) {
      request(command, bytes);
      assert(serial.output == expected(HW_RESP_ERROR, {HW_ERR_UNKNOWN_CMD}));
    }
  }
  assert(board.calls == 0 && state.savedMode == 0);
#endif
  assert(radio.sends == 0 && radio.tunes == 0 && board.reboots == 0);
  assert(state.bootMode == 0 && state.candidate == 2 && state.last == 2);
  for (unsigned pin = 0; pin < 64; ++pin) {
    assert(g_mock_pin_modes[pin] == 77 && g_mock_pin_levels[pin] == 88);
  }
  std::cout << "PASS: KISS physical radio slots\n";
}
'''


class KissRadioSlotTests(unittest.TestCase):
    def test_actual_kiss_dispatch_framing_and_board_gating(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="kiss-radio-slot-") as folder:
            work = Path(folder)
            mesh = (ROOT / "test/mocks/Mesh.h").read_text(encoding="utf-8")
            # Extend this test's isolated mock with the existing production board API.
            mesh = mesh.replace("virtual void reboot() {}", "virtual void reboot() {}\n"
                                "  virtual bool handleCommand(const char*, uint32_t, char*) { return false; }")
            (work / "Mesh.h").write_text(mesh, encoding="utf-8")
            (work / "test.cpp").write_text(HARNESS, encoding="utf-8")
            for g3 in (False, True):
                with self.subTest(station_g3=g3):
                    binary = work / "test.exe"
                    built = subprocess.run(
                        [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                         "-Wno-unused-parameter", "-Wno-sign-compare",
                         *(["-DSTATION_G3_ESP32=1"] if g3 else []),
                         "-I" + str(work), "-I" + str(ROOT / "test/mocks"),
                         "-I" + str(ROOT / "src"),
                         "-I" + str(ROOT / "examples/kiss_modem"),
                         "-I" + str(ROOT / "variants/station_g3_esp32"),
                         str(ROOT / "examples/kiss_modem/KissModem.cpp"),
                         str(work / "test.cpp"), "-o", str(binary)],
                        capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("PASS: KISS physical radio slots", checked.stdout)


if __name__ == "__main__":
    unittest.main()
