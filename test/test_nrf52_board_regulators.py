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

# Each target has manufacturer/board-designer circuit evidence recorded in
# docs/power_saving_diagnostics.md. Do not infer eligibility from inheritance.
VERIFIED_BOARDS = (
    ('heltec_t1/T1Board.cpp', 'T1Board', True),
    ('heltec_mesh_solar/MeshSolarBoard.cpp', 'MeshSolarBoard', False),
    ('heltec_tower_v2/HeltecTowerV2Board.cpp', 'HeltecTowerV2Board', True),
    ('mesh_pocket/MeshPocket.cpp', 'HeltecMeshPocket', False),
    ('lilygo_techo/TechoBoard.cpp', 'TechoBoard', False),
    ('ikoka_handheld_nrf/IkokaNrf52Board.cpp', 'IkokaNrf52Board', False),
    ('ikoka_nano_nrf/IkokaNanoNRFBoard.cpp', 'IkokaNanoNRFBoard', False),
    ('ikoka_stick_nrf/IkokaStickNRFBoard.cpp', 'IkokaStickNRFBoard', False),
    ('minewsemi_me25ls01/MinewsemiME25LS01Board.cpp', 'MinewsemiME25LS01Board', False),
)

HARNESS = r'''
#include <cassert>
#include <cstdint>
#define PIN_BOARD_SDA 1
#define PIN_BOARD_SCL 2
#define PIN_USER_BTN 9
#define PIN_USER_BTN_ANA 31
#define PIN_WIRE_SDA 1
#define PIN_WIRE_SCL 2
#define P_LORA_TX_LED 28
#define BUTTON_PIN 9
#define DISPLAY_CLASS TestDisplay
#define DISPLAY_ROTATION 0
constexpr int INPUT = 0, OUTPUT = 1, INPUT_PULLUP = 2, LOW = 0, HIGH = 1;
constexpr int PIN_VBAT_READ = 5, WB_IO2 = 34, SX126X_POWER_EN = 37;
constexpr int PIN_VBAT = 5, VBAT_ENABLE = 14, PIN_BAT_CTL = 15, LED_PIN = 28;
constexpr int PIN_GPS_EN = 10, PIN_GPS_RESET = 11, PIN_GPS_STANDBY = 12;
constexpr int PIN_SENSOR_EN = 13, PIN_BUZZER = 16;
constexpr int PIN_BUZZER_VOLTAGE_MULTIPLIER_1 = 17, PIN_BUZZER_VOLTAGE_MULTIPLIER_2 = 18;
constexpr int LED_STATE_ON = 1, PIN_GPS_EN_ACTIVE = 1, PIN_SENSOR_EN_ACTIVE = 1;
constexpr int GPS_RESET_MODE = 0;
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
void requireDcdc() {
  assert(base_calls == 1);
  assert(softdevice ? sdk_calls == 1 : power.DCDCEN == 1);
}
void pinMode(int, int) { requireDcdc(); }
void digitalWrite(int, int) { requireDcdc(); }
void delay(unsigned) {}
void meshSolarStart() { requireDcdc(); }
struct Bus {
  void setPins(int, int) { requireDcdc(); }
  void begin() { requireDcdc(); }
} Wire;
struct Uart { void begin(unsigned) { requireDcdc(); } } Serial;
struct Peripheral {
  void begin() { requireDcdc(); }
  void init() { requireDcdc(); }
};
struct Config {} power_config;
struct NRF52Board { void begin() { ++base_calls; } };
struct NRF52BoardDCDC : NRF52Board { void begin(); };
struct @BOARD@ : NRF52BoardDCDC {
  Peripheral periph_power, loRaFEMControl;
  unsigned btn_prev_state = 0;
  void begin();
  void checkBootVoltage(const Config*) {
    ++voltage_checks;
    requireDcdc();
  }
};
@METHODS@
int main() {
  for (softdevice = 0; softdevice < 2; ++softdevice) {
    power.DCDCEN = base_calls = sdk_calls = voltage_checks = 0;
    @BOARD@ board;
    board.begin();
    assert(base_calls == 1 && voltage_checks == @VOLTAGE_CHECKS@);
    assert(sdk_calls == softdevice);
    assert(power.DCDCEN == (softdevice ? 0U : 1U));
  }
}
'''


class Nrf52BoardRegulatorTests(unittest.TestCase):
    def run_startup(self, filename, board, flags=(), power_management=True,
                    has_voltage_check=True):
        methods = body((ROOT / 'src/helpers/NRF52Board.cpp').read_text(),
                       'void NRF52BoardDCDC::begin()')
        methods += body((ROOT / filename).read_text(), f'void {board}::begin()')
        source = HARNESS.replace('@BOARD@', board).replace('@METHODS@', methods)
        source = source.replace('@VOLTAGE_CHECKS@', str(int(power_management and has_voltage_check)))
        if power_management:
            flags = (*flags, '-DNRF52_POWER_MANAGEMENT=1')
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

    def test_verified_boards_initialize_dcdc_before_peripherals(self):
        for filename, board, has_voltage_check in VERIFIED_BOARDS:
            for power_management in (False, True):
                with self.subTest(board=board, power_management=power_management):
                    self.run_startup('variants/' + filename, board,
                                     power_management=power_management,
                                     has_voltage_check=has_voltage_check)

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
