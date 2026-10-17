#!/usr/bin/env python3

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "src" / "helpers" / "ui" / "ST7735Display.cpp"


class ST7735DisplayWakeProfileTest(unittest.TestCase):
    def test_sleep_command_precedes_pin_release_with_shared_or_ungated_power(self):
        source = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
constexpr int INPUT = 0, PIN_TFT_CS = 1, PIN_TFT_DC = 2, PIN_TFT_SDA = 3;
constexpr int PIN_TFT_SCL = 4, PIN_TFT_RST = 5, PIN_TFT_LEDA_CTL = 6;
constexpr int PIN_TFT_LEDA_CTL_ACTIVE = 1;
constexpr int ST77XX_DISPOFF = 0x28, ST77XX_SLPIN = 0x10;
static unsigned commands, pins, waits, waited;
static bool sleeping;
void sendCommand(int command) {
  assert(command == (commands == 0 ? ST77XX_DISPOFF : ST77XX_SLPIN));
  assert(commands < 2 && pins == 0);
  if (command == ST77XX_SLPIN) sleeping = true;
  ++commands;
}
void digitalWrite(int pin, int value) {
  assert(pin == PIN_TFT_LEDA_CTL && value == !PIN_TFT_LEDA_CTL_ACTIVE);
}
void delay(unsigned ms) { assert(sleeping); ++waits; waited += ms; }
void pinMode(int, int mode) {
  assert(mode == INPUT && sleeping && waited >= 5);
  ++pins;
}
struct Rail {
  unsigned claims;
  void release() { assert(sleeping && waited >= 5 && pins == 6); --claims; }
};
struct ST7735Display {
  bool _isOn = true;
  Rail* _peripher_power;
  void turnOff();
};
@METHOD@
int main() {
  // Standalone display, GPS sharing the rail, and boards with no rail control.
  for (unsigned users : {1U, 2U, 0U}) {
    commands = pins = waits = waited = 0; sleeping = false;
    Rail rail{users};
    ST7735Display display{true, users ? &rail : nullptr};
    display.turnOff();
    assert(sleeping && commands == 2 && waits == 1 && pins == 6 && !display._isOn);
    assert(rail.claims == (users ? users - 1 : 0));
    display.turnOff();
    assert(commands == 2 && waits == 1 && pins == 6);
  }
}
'''
        source = source.replace('@METHOD@', body(DRIVER.read_text(),
                                                'void ST7735Display::turnOff()'))
        with tempfile.TemporaryDirectory(prefix='meshcore-tft-sleep-') as directory:
            cpp, binary = Path(directory) / 'test.cpp', Path(directory) / 'test'
            cpp.write_text(source, encoding='ascii')
            subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(binary)],
                           check=True, capture_output=True, text=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_power_off_releases_bus_without_uninitializing_spi(self):
        source = DRIVER.read_text(encoding="utf-8")
        turn_off_start = source.index("void ST7735Display::turnOff()")
        turn_off_end = source.index("\n}\n", turn_off_start)
        turn_off = source[turn_off_start:turn_off_end]

        self.assertNotIn("_spi->end();", turn_off)
        self.assertIn("pinMode(PIN_TFT_SDA, INPUT);", turn_off)
        self.assertIn("pinMode(PIN_TFT_SCL, INPUT);", turn_off)

    def test_begin_restores_bus_pins_before_spi_begin(self):
        source = DRIVER.read_text(encoding="utf-8")
        begin_start = source.index("bool ST7735Display::begin()")
        begin_end = source.index("\n}\n", begin_start)
        begin = source[begin_start:begin_end]

        sda_restore = begin.index("pinMode(PIN_TFT_SDA, OUTPUT);")
        scl_restore = begin.index("pinMode(PIN_TFT_SCL, OUTPUT);")
        spi_begin = begin.index("_spi->begin(")
        self.assertLess(sda_restore, spi_begin)
        self.assertLess(scl_restore, spi_begin)

        backlight_inactive = begin.index(
            "digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);"
        )
        backlight_output = begin.index("pinMode(PIN_TFT_LEDA_CTL, OUTPUT);")
        self.assertLess(backlight_inactive, backlight_output)

    def test_nrf52_wake_restores_spi_high_drive(self):
        source = DRIVER.read_text(encoding="utf-8")
        begin_start = source.index("bool ST7735Display::begin()")
        begin_end = source.index("\n}\n", begin_start)
        begin = source[begin_start:begin_end]

        self.assertIn("nrf_gpio_cfg(PIN_TFT_SCL,", begin)
        self.assertIn("NRF_GPIO_PIN_INPUT_CONNECT", begin)
        self.assertIn("nrf_gpio_cfg(PIN_TFT_SDA,", begin)
        self.assertIn("NRF_GPIO_PIN_INPUT_DISCONNECT", begin)
        self.assertEqual(begin.count("NRF_GPIO_PIN_H0H1"), 2)

    def test_wake_reenters_full_display_begin_path(self):
        source = DRIVER.read_text(encoding="utf-8")
        turn_on_start = source.index("void ST7735Display::turnOn()")
        turn_on_end = source.index("\n}\n", turn_on_start)
        turn_on = source[turn_on_start:turn_on_end]

        self.assertIn("begin();", turn_on)

    def test_reset_release_waits_before_first_init_command(self):
        source = DRIVER.read_text(encoding="utf-8")
        reset_start = source.index("void ST7735Display::_resetAndInit()")
        reset_end = source.index("\n}\n", reset_start)
        reset = source[reset_start:reset_end]

        reset_release = reset.index("digitalWrite(PIN_TFT_RST, HIGH);", 100)
        recovery_wait = reset.index("delay(120);", reset_release)
        display_init = reset.index("displayInit(Rcmd1);", reset_release)
        self.assertLess(reset_release, recovery_wait)
        self.assertLess(recovery_wait, display_init)

    def test_begin_settles_normal_mode_before_marking_display_on(self):
        source = DRIVER.read_text(encoding="utf-8")
        begin_start = source.index("bool ST7735Display::begin()")
        begin_end = source.index("\n}\n", begin_start)
        begin = source[begin_start:begin_end]

        display_init = begin.index("_resetAndInit();")
        normal_on = begin.index("sendCommand(ST77XX_NORON);", display_init)
        normal_delay = begin.index("delay(10);", normal_on)
        display_on = begin.index("sendCommand(ST77XX_DISPON);", normal_delay)
        display_delay = begin.index("delay(100);", display_on)
        prime_frame = begin.index("endFrame();", display_delay)
        backlight = begin.index(
            "digitalWrite(PIN_TFT_LEDA_CTL, PIN_TFT_LEDA_CTL_ACTIVE);",
            prime_frame,
        )
        marked_on = begin.index("_isOn = true;", backlight)

        self.assertLess(display_init, normal_on)
        self.assertLess(normal_on, normal_delay)
        self.assertLess(normal_delay, display_on)
        self.assertLess(display_on, display_delay)
        self.assertLess(display_delay, prime_frame)
        self.assertLess(prime_frame, backlight)
        self.assertLess(backlight, marked_on)
        self.assertNotIn("fillScreen(ST77XX_BLACK)", begin[display_delay:prime_frame])

        reset_start = source.index("void ST7735Display::_resetAndInit()")
        reset_end = source.index("\n}\n", reset_start)
        reset = source[reset_start:reset_end]
        self.assertNotIn("endFrame();", reset)


if __name__ == "__main__":
    unittest.main()
