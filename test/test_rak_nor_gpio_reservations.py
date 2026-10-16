"""Run actual RAK board GPIO approval and commands with NOR pin ownership."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'test/fixtures/rak_nor_gpio_reservations.cpp'
POLICY = ROOT / 'src/helpers/UserGpioPinPolicy.h'
ARDUINO = r'''
#pragma once
#include <cassert>
#include <cstdint>
constexpr uint8_t LOW=0,HIGH=1,INPUT=0,OUTPUT=1,INPUT_PULLUP=2,INPUT_PULLDOWN=3;
inline uint8_t g_mock_pin_modes[64]={},g_mock_pin_levels[64]={};
inline unsigned gpio_writes=0,gpio_modes=0;
inline uint32_t g_mock_millis=0;
inline uint32_t millis(){return g_mock_millis;}
inline void pinMode(uint8_t p,uint8_t m){assert(p<64);++gpio_modes;g_mock_pin_modes[p]=m;}
inline void digitalWrite(uint8_t p,uint8_t v){assert(p<64);++gpio_writes;g_mock_pin_levels[p]=v;}
inline int digitalRead(uint8_t p){assert(p<64);return g_mock_pin_levels[p];}
'''


class RakNorGpioReservationsTest(unittest.TestCase):
    def test_real_board_and_gpio_preserve_nor_pin_ownership(self):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler, 'host C++17 compiler required')
        board = (ROOT / 'src/helpers/NRF52Board.cpp').read_text(encoding='ascii')
        fixture = FIXTURE.read_text(encoding='ascii')
        fixture = fixture.replace('@EXPOSED@', extract_braced(board, 'bool isExposedNrf52UserGpio('))
        fixture = fixture.replace('@APPROVAL@', extract_braced(board, 'bool NRF52Board::isUserGpioAvailable('))
        shared = ('RAK_4631=1', 'PIN_BOARD_SDA=13', 'PIN_BOARD_SCL=14',
                  'PIN_GPS_EN=-1', 'PIN_VBAT_READ=5', 'USER_GPIO_CONTROL=1')
        normal = ('OTA_RAK15002_SD_STORE=1', 'OTA_SD_STORE=1',
                  'OTA_QSPI_SCK_ARDUINO_PIN=3', 'OTA_QSPI_CS_ARDUINO_PIN=31',
                  'OTA_QSPI_IO0_ARDUINO_PIN=30', 'OTA_QSPI_IO1_ARDUINO_PIN=29',
                  'OTA_QSPI_IO2_NOT_CONNECTED=1', 'OTA_QSPI_IO3_NOT_CONNECTED=1')
        arbitrary = ('TEST_ARBITRARY=1', 'OTA_QSPI_SCK_ARDUINO_PIN=19',
                     'OTA_QSPI_CS_ARDUINO_PIN=2', 'OTA_QSPI_IO0_ARDUINO_PIN=20',
                     'OTA_QSPI_IO1_ARDUINO_PIN=21', 'OTA_QSPI_IO2_ARDUINO_PIN=24',
                     'OTA_QSPI_IO3_ARDUINO_PIN=25')
        variants = (
            ('normal_sensor_nor', normal + ('NRF52_PLATFORM=1', 'OTA_QSPI_STORE=1',
                                           'OTA_RAK_AUTO_STORE=1', 'TEST_EXPECT_NOR=1'), 48),
            ('normal_sensor_without_nor', normal + ('NRF52_PLATFORM=1', 'TEST_EXPECT_NOR=0'), 46),
            ('wrong_backend_does_not_claim', normal + ('OTA_QSPI_STORE=1', 'TEST_EXPECT_NOR=0'), 46),
            ('arbitrary_six_nor_pads', arbitrary + ('NRF52_PLATFORM=1', 'OTA_QSPI_STORE=1',
                                                   'TEST_EXPECT_NOR=1'), 51),
            ('arbitrary_without_backend', arbitrary + ('NRF52_PLATFORM=1', 'TEST_EXPECT_NOR=0'), 39),
            # Policy-only robustness control: ignored explicit pads must not
            # become user reservations. The storage backend independently
            # rejects contradictory connected/disconnected configuration.
            ('disconnected_explicit_io2_io3', arbitrary + ('NRF52_PLATFORM=1',
                'OTA_QSPI_STORE=1', 'TEST_EXPECT_NOR=1', 'TEST_DISCONNECTED_OVERRIDES=1',
                'OTA_QSPI_IO2_NOT_CONNECTED=1', 'OTA_QSPI_IO3_NOT_CONNECTED=1'), 47),
        )
        sanitizer = (['-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                      '-fno-pie', '-no-pie'] if sys.platform.startswith('linux') else [])
        with tempfile.TemporaryDirectory(prefix='rak-nor-gpio-reservations-') as folder:
            work = Path(folder)
            (work / 'WVariant.h').write_text('#pragma once\n#include <cstdint>\n', encoding='ascii')
            (work / 'Arduino.h').write_text(ARDUINO, encoding='ascii')
            # Preserve the header unchanged; its quoted sensor include resolves
            # against the real helper directory after this private directory.
            (work / 'UserGpioPinPolicy.h').write_bytes(POLICY.read_bytes())
            (work / 'test.cpp').write_text(fixture, encoding='ascii')
            for name, macros, scenarios in variants:
                with self.subTest(variant=name):
                    binary = work / (name + ('.exe' if os.name == 'nt' else ''))
                    command = [compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                               '-Wno-unused-parameter', *sanitizer,
                               *['-D' + x for x in shared + macros], '-I' + str(work),
                               '-I' + str(ROOT / 'src/helpers'), '-I' + str(ROOT / 'src'),
                               '-I' + str(ROOT / 'variants/rak4631'),
                               str(work / 'test.cpp'), str(ROOT / 'src/helpers/UserGpio.cpp'),
                               '-o', str(binary)]
                    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
                    self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
                    self.assertIn(f'{scenarios} RAK NOR GPIO ownership scenarios passed', tested.stdout)


if __name__ == '__main__':
    unittest.main()
