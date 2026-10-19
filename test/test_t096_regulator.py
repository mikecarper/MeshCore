#!/usr/bin/env python3
"""T096 board startup must defer DC/DC until saved regulator policy is loaded."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class T096RegulatorTests(unittest.TestCase):
    def test_startup_defers_dcdc_until_after_battery_hardware_is_ready(self):
        methods = body((ROOT / 'src/helpers/NRF52Board.cpp').read_text(),
                       'void NRF52BoardDCDC::begin()')
        methods += body((ROOT / 'variants/heltec_t096/T096Board.cpp').read_text(),
                        'void T096Board::begin()')
        source = r'''
#include <cassert>
#include <cstdint>
#define NRF52_POWER_MANAGEMENT 1
#define PIN_BOARD_SDA 1
#define PIN_BOARD_SCL 2
constexpr int OUTPUT = 1, LOW = 0, P_LORA_TX_LED = 28;
constexpr int NRF_POWER_DCDC_ENABLE = 1;
static unsigned base_calls, sdk_calls, voltage_checks;
static uint8_t softdevice;
struct Power { unsigned DCDCEN; } power;
#define NRF_POWER (&power)
namespace mesh_nrf52 {
void softdeviceIsEnabled(uint8_t& enabled) { enabled = softdevice; }
}
void sd_power_dcdc_mode_set(int mode) {
  assert(softdevice && mode == NRF_POWER_DCDC_ENABLE);
  ++sdk_calls;
}
void pinMode(int, int) {}
void digitalWrite(int, int) {}
void delay(unsigned) {}
struct Bus { void setPins(int, int) {} void begin() {} } Wire;
struct Peripheral { void begin() {} void init() {} };
struct Config {} power_config;
struct NRF52Board {
  bool cpu_dcdc_supported = false;
  void begin() { ++base_calls; }
};
struct NRF52BoardDCDC : NRF52Board { void begin(); };
struct T096Board : NRF52BoardDCDC {
  Peripheral periph_power, loRaFEMControl;
  void begin();
  void checkBootVoltage(const Config*) {
    ++voltage_checks;
    assert(sdk_calls == 0 && power.DCDCEN == 0);
  }
};
@METHODS@
int main() {
  for (softdevice = 0; softdevice < 2; ++softdevice) {
    power.DCDCEN = base_calls = sdk_calls = voltage_checks = 0;
    T096Board board;
    board.begin();
    assert(base_calls == 1 && voltage_checks == 1);
    assert(board.cpu_dcdc_supported);
    assert(sdk_calls == 0 && power.DCDCEN == 0);
  }
}
'''
        with tempfile.TemporaryDirectory(prefix='meshcore-t096-regulator-') as tmp:
            cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
            cpp.write_text(source.replace('@METHODS@', methods), encoding='ascii')
            subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(binary)],
                           check=True, capture_output=True, text=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
