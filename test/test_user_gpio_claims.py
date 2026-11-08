"""Exercise actual GPIO commands and timer ownership with physical aliases."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_rak_nor_gpio_reservations import ARDUINO

ROOT = Path(__file__).resolve().parents[1]

SOURCE = r'''
#include <Arduino.h>
#include <helpers/UserGpio.h>
#include <helpers/RuntimePinClaims.h>
#include <cstring>
using namespace mesh::pins;
class Board : public mesh::MainBoard {
public:
  uint16_t getBattMilliVolts() override { return 0; }
  const char* getManufacturerName() const override { return "test"; }
  void reboot() override {}
  uint8_t getStartupReason() const override { return BD_STARTUP_NORMAL; }
  uint16_t peripheralPinIdentity(uint8_t pin) const override { return pin == 6 ? 4 : pin; }
  bool isUserGpioAvailable(uint8_t pin) const override {
    return (pin == 4 || pin == 6 || pin == 16)
        && !claimedByOther(peripheralPinIdentity(pin), Owner::UserGpio);
  }
};
int main() {
  Board board;
  UserGpio gpio(board);
  char reply[160];
  assert(tryClaim(Owner::Outputs, pinMask(4)));
  assert(gpio.handleSet("4 on", reply, sizeof(reply)).outcome == UserGpio::SetResult::ERROR);
  assert(gpio_writes == 0 && gpio_modes == 0);
  release(Owner::Outputs, pinMask(4));
  assert(gpio.handleSet("4 on", reply, sizeof(reply)).outcome == UserGpio::SetResult::APPLIED);
  assert(held(Owner::UserGpio) == pinMask(4));
  assert(!tryClaim(Owner::Rotary, pinMask(4)));
  const unsigned writes = gpio_writes, modes = gpio_modes;
  assert(gpio.handleSet("6 on 1s reset", reply, sizeof(reply)).outcome == UserGpio::SetResult::ERROR);
  assert(gpio.handleSet("6 reset", reply, sizeof(reply)).outcome == UserGpio::SetResult::ERROR);
  assert(gpio_writes == writes && gpio_modes == modes);
  assert(held(Owner::UserGpio) == pinMask(4));
  assert(gpio.handleSet("4 off", reply, sizeof(reply)).outcome == UserGpio::SetResult::APPLIED);
  assert(held(Owner::UserGpio) == pinMask(4));
  assert(gpio.handleSet("16 off 100ms reset", reply, sizeof(reply)).outcome == UserGpio::SetResult::TIMER_STARTED);
  assert(held(Owner::UserGpio) == (pinMask(4) | pinMask(16)));
  assert(!tryClaim(Owner::Notifications, pinMask(16)));
  g_mock_millis = 99;
  gpio.loop();
  assert(held(Owner::UserGpio) & pinMask(16));
  g_mock_millis = 100;
  gpio.loop();
  assert(!(held(Owner::UserGpio) & pinMask(16)));
  assert(g_mock_pin_modes[16] == INPUT);
  assert(tryClaim(Owner::Notifications, pinMask(16)));
  release(Owner::Notifications, pinMask(16));
  assert(gpio.handleSet("4 reset", reply, sizeof(reply)).outcome == UserGpio::SetResult::APPLIED);
  assert(held(Owner::UserGpio) == 0);
  assert(tryClaim(Owner::Rotary, pinMask(4)));
  assert(gpio.handleSet("4 off", reply, sizeof(reply)).outcome == UserGpio::SetResult::ERROR);
  release(Owner::Rotary, pinMask(4));
  assert(gpio.handleSet("16 on 1ms off", reply, sizeof(reply)).outcome == UserGpio::SetResult::TIMER_STARTED);
  g_mock_millis = 101;
  gpio.loop();
  assert(held(Owner::UserGpio) == pinMask(16));
  assert(g_mock_pin_modes[16] == OUTPUT && g_mock_pin_levels[16] == LOW);
  gpio.handleSet("16 reset", reply, sizeof(reply));
  assert(held(Owner::UserGpio) == 0);
  assert(gpio.handleSet("4 on 0ms reset", reply, sizeof(reply)).outcome == UserGpio::SetResult::ERROR);
  assert(held(Owner::UserGpio) == 0);
}
'''


class UserGpioClaimsTest(unittest.TestCase):
    def test_claims_follow_outputs_and_timers_until_reset(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="user-gpio-claims-") as directory:
            work = Path(directory)
            (work / "Arduino.h").write_text(ARDUINO)
            source = work / "test.cpp"
            source.write_text(SOURCE)
            binary = work / "test"
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-DMESH_CUSTOM_PERIPHERALS=1", "-I" + str(work),
                "-I" + str(ROOT / "src"), str(source), str(ROOT / "src/helpers/UserGpio.cpp"),
                "-o", str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
