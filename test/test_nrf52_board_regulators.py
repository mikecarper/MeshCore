#!/usr/bin/env python3
"""Run real board startup methods against counted nRF52 regulator SDK calls."""
import configparser
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#define NRF52_POWER_MANAGEMENT 1
#define PIN_BOARD_SDA 1
#define PIN_BOARD_SCL 2
#define PIN_USER_BTN 9
#define PIN_USER_BTN_ANA 31
constexpr int INPUT = 0, OUTPUT = 1, INPUT_PULLUP = 2, LOW = 0, HIGH = 1;
constexpr int PIN_VBAT_READ = 5, WB_IO2 = 34, SX126X_POWER_EN = 37;
constexpr int NRF_POWER_DCDC_ENABLE = 1, NRF_POWER_DCDC_DISABLE = 0;
static unsigned base_calls, sdk_calls, voltage_checks;
static uint8_t softdevice;
struct Power { unsigned DCDCEN; } power;
#define NRF_POWER (&power)
namespace mesh_nrf52 {
void softdeviceIsEnabled(uint8_t& enabled) { enabled = softdevice; }
}
void sd_power_dcdc_mode_set(int mode) {
  assert(base_calls == 1 && softdevice && mode == NRF_POWER_DCDC_ENABLE);
  ++sdk_calls;
}
void pinMode(int, int) {}
void digitalWrite(int, int) {}
void delay(unsigned) {}
struct Bus { void setPins(int, int) {} void begin() {} } Wire;
struct Config {} power_config;
struct NRF52Board { void begin() { ++base_calls; } };
struct NRF52BoardDCDC : NRF52Board { void begin(); };
struct @BOARD@ : NRF52BoardDCDC {
  void begin();
  void checkBootVoltage(const Config*) {
    ++voltage_checks;
    assert(base_calls == 1);
    assert(softdevice ? sdk_calls == 1 : power.DCDCEN == 1);
  }
};
@METHODS@
int main() {
  for (softdevice = 0; softdevice < 2; ++softdevice) {
    power.DCDCEN = base_calls = sdk_calls = voltage_checks = 0;
    @BOARD@ board;
    board.begin();
    assert(base_calls == 1 && voltage_checks == 1);
    assert(sdk_calls == softdevice);
    assert(power.DCDCEN == (softdevice ? 0U : 1U));
  }
}
'''


class Nrf52BoardRegulatorTests(unittest.TestCase):
    def run_startup(self, filename, board, flags=()):
        methods = body((ROOT / 'src/helpers/NRF52Board.cpp').read_text(),
                       'void NRF52BoardDCDC::begin()')
        methods += body((ROOT / filename).read_text(), f'void {board}::begin()')
        source = HARNESS.replace('@BOARD@', board).replace('@METHODS@', methods)
        with tempfile.TemporaryDirectory(prefix='meshcore-board-regulator-') as tmp:
            cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
            cpp.write_text(source, encoding='ascii')
            built = subprocess.run([
                os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra',
                '-Werror', *flags, str(cpp), '-o', str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rak4631_startup_uses_dcdc_before_voltage_check(self):
        # Cover boot before BLE starts and an already-active SoftDevice. Also
        # prove an obsolete downstream flag cannot silently force LDO again.
        for flags in ((), ('-DRAK4631_COMPANION_FORCE_LDO=1',)):
            with self.subTest(flags=flags):
                self.run_startup('variants/rak4631/RAK4631Board.cpp', 'RAK4631Board', flags)

    def test_rak_companion_profiles_do_not_force_ldo(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / 'variants/rak4631/platformio.ini')
        for role in ('usb', 'ble', 'ethernet', 'full'):
            with self.subTest(role=role):
                section = config[f'env:RAK_4631_companion_radio_{role}']
                self.assertNotIn('RAK4631_COMPANION_FORCE_LDO', section['build_flags'])
        self.assertEqual(config['env:RAK_4631_companion_radio_full']['extends'],
                         'env:RAK_4631_companion_radio_usb')


if __name__ == '__main__':
    unittest.main()
