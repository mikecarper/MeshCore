#!/usr/bin/env python3
"""Exercise the nRF52 live PIN setter with the actual implementation."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <string>

struct SecurityStub {
  bool accept = true;
  int calls = 0;
  std::string active = "123456";
  bool setPIN(const char *pin) {
    ++calls;
    if (!accept) return false;
    active = pin;
    return true;
  }
};
struct { SecurityStub Security; } Bluefruit;
struct SerialBLEInterface {
  bool _begin_ready = false;
  bool setPin(uint32_t pin_code);
};
@SET_PIN@
int main() {
  SerialBLEInterface ble;
  assert(!ble.setPin(555555));
  assert(Bluefruit.Security.calls == 0);
  ble._begin_ready = true;
  assert(ble.setPin(654321));
  assert(Bluefruit.Security.active == "654321");
  assert(ble.setPin(7));
  assert(Bluefruit.Security.active == "000007");
  assert(ble.setPin(0));
  assert(Bluefruit.Security.active == "000000");
  const int calls = Bluefruit.Security.calls;
  assert(!ble.setPin(1000000));
  assert(Bluefruit.Security.calls == calls);
  Bluefruit.Security.accept = false;
  assert(!ble.setPin(111111));
  assert(Bluefruit.Security.active == "000000");
}
'''


class Nrf52LivePinTest(unittest.TestCase):
    def test_updates_passkey_without_reinitializing_stack(self):
        source = (ROOT / "src/helpers/nrf52/SerialBLEInterface.cpp").read_text()
        setter = method(source, "bool SerialBLEInterface::setPin(")
        with tempfile.TemporaryDirectory(prefix="meshcore-nrf-live-pin-") as temp:
            temp = Path(temp)
            src, binary = temp / "test.cpp", temp / "test"
            src.write_text(HARNESS.replace("@SET_PIN@", setter))
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            str(src), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
