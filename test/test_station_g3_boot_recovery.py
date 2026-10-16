"""Host-test the actual bounded Station G3 pre-radio USB recovery implementation."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "variants/station_g3_esp32"

USB_HEADER = r'''#pragma once
#include <cstddef>
#include <cstdint>
class Stream {
 public:
  virtual int available() = 0;
  virtual int read() = 0;
  virtual size_t write(const uint8_t*, size_t) = 0;
  virtual ~Stream() = default;
};
namespace mesh {
void serviceUsbLoggingPort();
void serviceUsbTerminalPort();
bool takeUsbTerminalSessionReset();
bool tryCompleteUsbTerminalSessionReset();
Stream& usbConsolePort();
}
'''

BOARD_HEADER = r'''#pragma once
#include <cstdint>
class StationG3Board {
 public:
  bool handleCommand(const char*, uint32_t, char*);
  void reboot();
};
'''

HARNESS = r'''
#include "StationG3BootRecovery.h"
#include "StationG3Board.h"
#include <helpers/UsbLogging.h>
#include <algorithm>
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

struct Port : Stream {
  std::string input, output;
  size_t at = 0, readCount = 0, writeCount = 0;
  size_t txLimit = std::numeric_limits<size_t>::max();
  bool transientReadFailure = false;
  int available() override { return static_cast<int>(input.size() - at); }
  int read() override {
    ++readCount;
    if (transientReadFailure) { transientReadFailure = false; return -1; }
    return at < input.size() ? static_cast<unsigned char>(input[at++]) : -1;
  }
  size_t write(const uint8_t* bytes, size_t size) override {
    ++writeCount;
    const size_t count = std::min(size, txLimit);
    output.append(reinterpret_cast<const char*>(bytes), count);
    return count;
  }
  void feed(const std::string& bytes) { input += bytes; }
} port;
static bool reset = false, resetReady = true, saveFails = false, handled = true;
static unsigned logs = 0, terminals = 0, reboots = 0;
static std::vector<std::string> commands;
static std::string saved = "auto";

namespace mesh {
void serviceUsbLoggingPort() { ++logs; }
void serviceUsbTerminalPort() { ++terminals; }
bool takeUsbTerminalSessionReset() { const bool result = reset; reset = false; return result; }
bool tryCompleteUsbTerminalSessionReset() { return resetReady; }
Stream& usbConsolePort() { return port; }
}
bool StationG3Board::handleCommand(const char* command, uint32_t stamp, char* reply) {
  assert(stamp == 0);
  commands.emplace_back(command);
  if (!handled) return false;
  if (std::strncmp(command, "set radio.slot ", 15) == 0) {
    if (saveFails) { std::strcpy(reply, "Error: slot could not be saved"); return true; }
    saved = command + 15;
    std::strcpy(reply, "OK - saved; reboot required");
  } else std::strcpy(reply, "saved=auto active=none");
  return true;
}
void StationG3Board::reboot() { ++reboots; }
static StationG3Board board;
void tick(bool first = false) { station_g3::serviceBootRecovery(board, first); }
void drain(unsigned ticks = 50) { while (ticks--) tick(); }
void fresh() { tick(true); port.output.clear(); port.writeCount = port.readCount = 0; }

int main(int argc, char** argv) {
  assert(argc == 2);
  const int scenario = std::atoi(argv[1]);
  fresh();
  if (scenario == 0) {
    port.feed("!g3 get radio.slot\r\n!g3 set radio.slot 2\n");
    tick();
    assert(commands.size() == 1 && commands[0] == "get radio.slot");
    assert(port.output == "saved=auto active=none\r\n");
    tick();
    assert(commands.size() == 2 && saved == "2");
    assert(reboots == 0);  // Changing slot must not reboot behind the operator.
    assert(port.output.find("OK - saved; reboot required\r\n") != std::string::npos);
    for (const auto& choice : {"1", "auto"}) {
      port.feed(std::string("!g3 set radio.slot ") + choice + "\n"); tick();
      assert(saved == choice);
    }
    port.feed("!g3 reboot\n"); tick(); assert(reboots == 1);
  } else if (scenario == 1) {
    for (const auto& text : {"get radio.slot", "set radio.slot 2", "reboot", "help",
                             " !g3 reboot", "!g3reboot", "!G3 reboot"}) {
      port.feed(std::string(text) + "\n"); tick();
    }
    assert(commands.empty() && reboots == 0 && port.output.empty());
    for (const auto& text : {"!g3 set radio.slot 0", "!g3 set radio.slot 3",
                             "!g3 set radio.slot 2 extra", "!g3 set radio.slot 01",
                             "!g3 set tx 22", "!g3 erase", "!g3 reboot now"}) {
      port.feed(std::string(text) + "\n"); tick();
    }
    assert(commands.empty() && reboots == 0 && saved == "auto");
    assert(port.output.find("Error: recovery accepts only") != std::string::npos);
  } else if (scenario == 2) {
    port.feed("!g3 set radio."); tick(); assert(commands.empty());
    port.feed("slot 2"); tick(); assert(commands.empty());
    port.feed("\n"); tick(); assert(commands.size() == 1 && saved == "2");
    port.feed(std::string(1000, 'x') + "!g3 reboot\n!g3 get radio.slot\n");
    const size_t before = port.readCount; tick();
    assert(port.readCount - before == 64 && commands.size() == 1);
    drain();
    assert(reboots == 0 && commands.size() == 2 && commands.back() == "get radio.slot");
  } else if (scenario == 3) {
    for (int byte : {0, 1, 8, 9, 27, 127, 128, 255}) {
      port.feed(std::string("!g3 ") + static_cast<char>(byte) + "reboot\n"); tick();
    }
    assert(commands.empty() && reboots == 0);
    port.feed(std::string(1, char(0xc0)) + char(0) + "\n!g3 reboot\n!g3 set radio.slot 2\n"
              + char(0xc0) + char(0) + "\n!g3 reboot\n" + char(0xc0));
    drain();
    assert(commands.empty() && reboots == 0 && saved == "auto");
    // An explicit Enter following the closing FEND leaves quarantine.
    port.feed("\r\n!g3 get radio.slot\n"); drain();
    assert(commands.size() == 1 && commands[0] == "get radio.slot");
  } else if (scenario == 4) {
    port.feed("!g3 set radio.slot "); tick();
    reset = true; resetReady = false;
    port.feed("2\n"); tick();
    assert(commands.empty());
    const size_t before = port.readCount;
    tick(); assert(port.readCount == before);
    resetReady = true; drain();
    assert(commands.empty() && saved == "auto");
    port.feed("!g3 set radio.slot 1\n"); tick(); assert(saved == "1");
    assert(port.output.find("USB recovery is active") != std::string::npos);
  } else if (scenario == 5) {
    port.txLimit = 0;
    tick(true); port.feed("!g3 set radio.slot 2\n");
    for (unsigned i = 0; i < 10; ++i) tick();
    assert(port.readCount == 0 && commands.empty());
    port.txLimit = 3;
    drain(150);
    assert(commands.size() == 1 && saved == "2");
    assert(port.output.find("OK - saved; reboot required\r\n") != std::string::npos);
    assert(port.output.find("OK - saved;") == port.output.rfind("OK - saved;"));
  } else if (scenario == 6) {
    saveFails = true;
    port.feed("!g3 set radio.slot 2\n"); tick();
    assert(saved == "auto" && reboots == 0);
    assert(port.output == "Error: slot could not be saved\r\n");
    handled = false; port.output.clear();
    port.feed("!g3 get radio.slot\n"); tick();
    assert(port.output == "Error: radio.slot recovery unavailable.\r\n");
  } else if (scenario == 7) {
    port.feed("!g3 help\n"); tick();
    assert(commands.empty() && reboots == 0);
    for (const auto& text : {"!g3 get radio.slot", "!g3 set radio.slot auto|1|2", "!g3 reboot"})
      assert(port.output.find(text) != std::string::npos);
    port.feed("!g3 reboot\n"); port.transientReadFailure = true;
    const size_t before = port.readCount; tick();
    assert(port.readCount == before + 1 && reboots == 0);
    tick(); assert(reboots == 1);
  } else if (scenario == 8) {
    port.feed("!g3 set radio.slot "); tick();
    tick(true); port.feed("2\n"); tick();
    assert(commands.empty() && saved == "auto");
    port.feed("!g3 get radio.slot\n"); tick(); assert(commands.size() == 1);
  } else assert(false);
  assert(logs != 0 && terminals != 0);
  std::cout << "PASS: Station G3 recovery " << scenario << '\n';
}
'''


class StationG3BootRecoveryTests(unittest.TestCase):
    def test_all_role_usb_startup_precedes_board_and_radio(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++ preprocessor is required")
        role_paths = ("simple_repeater", "simple_room_server", "simple_sensor",
                      "companion_radio", "kiss_modem")
        for role in role_paths:
            with self.subTest(role=role):
                source = (ROOT / "examples" / role / "main.cpp").read_text(encoding="utf-8")
                setup = source.split("void setup() {", 1)[1].split("void loop() {", 1)[0]
                self.assertLess(setup.index("board.begin()"), setup.index("radio_init()"))
                before_radio = setup[:setup.index("radio_init()")]
                self.assertNotIn("Serial.end(", before_radio)
                # Preprocess actual startup: textual index checks alone can
                # mistake an excluded #if branch for a functioning USB path.
                before_board = setup[:setup.index("board.begin()")]
                flags = ["ESP32=1", "STATION_G3_ESP32=1", "ARDUINO_USB_MODE=1",
                         "ARDUINO_USB_CDC_ON_BOOT=1", "MESH_USB_CONSOLE_COOPERATIVE=1",
                         "MESH_ESP32_HWCDC_SESSION_GUARD=1"]
                for uart in ((False, True) if role == "kiss_modem" else (False,)):
                    active_flags = flags + (["KISS_UART_RX=16", "KISS_UART_TX=17",
                                             "KISS_HWCDC_TRANSPORT=0"] if uart else [])
                    if not uart:
                        active_flags.append("KISS_HWCDC_TRANSPORT=1")
                    checked = subprocess.run(
                        [compiler, "-E", "-P", "-x", "c++",
                         *["-D" + flag for flag in active_flags], "-"],
                        input=before_board, capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(checked.returncode, 0, checked.stderr)
                    compiled = checked.stdout
                    self.assertEqual(compiled.count("Serial.begin(115200)"), 1)
                    self.assertEqual(compiled.count("mesh::prepareUsbLoggingPort()"), 1)
                    self.assertEqual(compiled.count("mesh::beginUsbLoggingPort()"), 1)
                    self.assertLess(compiled.index("mesh::prepareUsbLoggingPort()"),
                                    compiled.index("Serial.begin(115200)"))
                    self.assertLess(compiled.index("Serial.begin(115200)"),
                                    compiled.index("mesh::beginUsbLoggingPort()"))
                self.assertNotIn("serviceBootRecovery", setup)
                self.assertNotIn("serviceBootRecovery", source.split("void loop() {", 1)[1])

    def test_actual_parser_and_usb_state_machine(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="g3-boot-recovery-") as folder:
            work = Path(folder)
            (work / "helpers").mkdir()
            for name in ("StationG3BootRecovery.h", "StationG3BootRecovery.cpp"):
                # Compile the whole production translation unit, not a copied parser.
                shutil.copyfile(SOURCE / name, work / name)
            (work / "StationG3Board.h").write_text(BOARD_HEADER, encoding="utf-8")
            (work / "helpers/UsbLogging.h").write_text(USB_HEADER, encoding="utf-8")
            (work / "test.cpp").write_text(HARNESS, encoding="utf-8")
            binary = work / "test.exe"
            built = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 "-I" + str(work), str(work / "StationG3BootRecovery.cpp"),
                 str(work / "test.cpp"), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            for scenario in range(9):
                with self.subTest(scenario=scenario):
                    checked = subprocess.run([str(binary), str(scenario)],
                                             capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("PASS: Station G3 recovery", checked.stdout)

    def test_recovery_has_no_raw_or_blocking_transport(self):
        source = (SOURCE / "StationG3BootRecovery.cpp").read_text(encoding="utf-8")
        self.assertNotIn("Serial.", source)
        self.assertNotIn("delay(", source)
        self.assertNotIn("readString", source)
        self.assertNotIn("readBytes", source)
        self.assertNotIn(".flush(", source)
        self.assertNotIn("new ", source)
        self.assertIn("mesh::usbConsolePort()", source)
        self.assertIn("mesh::tryCompleteUsbTerminalSessionReset()", source)
        self.assertIn("kRxBudget = 64", source)


if __name__ == "__main__":
    unittest.main()
