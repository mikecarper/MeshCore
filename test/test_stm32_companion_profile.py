#!/usr/bin/env python3
"""Guard headless STM32 Companion size recipes and deployed storage contracts.

The stm32-companion-smoke job also links the actual image. These checks prevent
fixing its size limit by silently shrinking storage or Companion capacities.
"""

import configparser
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import textwrap
import unittest

from cpp_source import body
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]


class STM32CompanionProfileTest(unittest.TestCase):
    variant = "rak3x72"
    target = "RAK_3x72_companion_radio_usb"
    base = "rak3x72"
    extra_timer_targets = ()

    def setUp(self):
        self.config = configparser.ConfigParser(
            interpolation=None, inline_comment_prefixes=(";",))
        self.config.read(ROOT / "variants" / self.variant / "platformio.ini")
        self.companion = self.config["env:" + self.target]

    def test_unused_timer_wrappers_are_removed_only_from_approved_headless_roles(self):
        for flag in ("RADIOLIB_TONE_UNSUPPORTED", "HAL_TIM_MODULE_ONLY"):
            self.assertRegex(self.companion["build_flags"], r"-D\s+" + flag + r"\b")
            for section in self.config.sections():
                if section != self.companion.name:
                    flags = self.config[section].get("build_flags", "")
                    if section in self.extra_timer_targets:
                        self.assertRegex(flags, r"-D\s+" + flag + r"\b")
                    else:
                        self.assertNotIn(flag, flags)

    def test_flash_and_filesystem_boundaries_do_not_move(self):
        self.assertNotIn("board_upload.maximum_size", self.companion)
        self.assertEqual(self.config[self.base].getint("board_upload.maximum_size"),
                         229376)
        self.assertNotIn("LFS_FLASH_TOTAL_SIZE", self.companion["build_flags"])
        self.assertNotIn("LFS_FLASH_TOTAL_SIZE", self.config[self.base]["build_flags"])
        header = (ROOT / "src/helpers/stm32/InternalFileSystem.h").read_text()
        self.assertRegex(header, r"#define\s+LFS_FLASH_TOTAL_SIZE\s+\(16\s*\*\s*2048\)")
        self.assertEqual(229376 + 16 * 2048, 256 * 1024)

    def test_companion_capacities_and_interfaces_are_retained(self):
        flags = self.companion["build_flags"]
        for definition in ("MAX_CONTACTS=100", "MAX_GROUP_CHANNELS=8",
                           "ENABLE_USB_INTERFACE"):
            self.assertRegex(flags, r"-D\s+" + re.escape(definition) + r"\b")
        for flags in (flags, self.config[self.base]["build_flags"]):
            self.assertNotRegex(flags, r"COMPANION_FEATURE_\w+=0")
            self.assertNotRegex(flags, r"MESH_ENABLE_\w+=0")
        self.assertIn("+<../examples/companion_radio/*.cpp>",
                      self.companion["build_src_filter"])
        self.assertIn("-Oz", self.companion["build_flags"])
        self.assertEqual(self.companion["build_unflags"].split(), ["-Os"])

    def test_size_profile_uses_equivalent_fixed_loop_field_products(self):
        for definition in ("SHA512", "SCALAR", "FIELD"):
            self.assertRegex(self.companion["build_flags"],
                             r"-D\s+ED25519_COMPACT_" + definition + r"=1\b")


class TinyRelaySTM32CompanionProfileTest(STM32CompanionProfileTest):
    variant = "tiny_relay"
    target = "Tiny_Relay_companion_radio_usb"
    base = "Tiny_Relay"

    def test_relay_gpio_and_adc_monitor_are_retained(self):
        header = (ROOT / "variants" / self.variant / "target.h").read_text()
        self.assertIn("digitalWrite(PA0, values & 1)", header)
        self.assertIn("digitalWrite(PA1, (values & 2) >> 1)", header)
        self.assertIn("analogRead(PIN_VBAT_READ)", header)


class WioDevSTM32CompanionProfileTest(STM32CompanionProfileTest):
    variant = "wio-e5-dev"
    target = "wio-e5_companion_radio_usb"
    base = "lora_e5"

    def test_uart_pins_and_adc_monitor_are_retained(self):
        flags = self.config[self.base]["build_flags"]
        self.assertRegex(flags, r"-D\s+PIN_SERIAL_RX=PB7\b")
        self.assertRegex(flags, r"-D\s+PIN_SERIAL_TX=PB6\b")
        header = (ROOT / "variants" / self.variant / "target.h").read_text()
        self.assertIn("analogRead(PIN_A3)", header)


class WioSTM32CompanionProfileTest(STM32CompanionProfileTest):
    variant = "wio-e5-mini"
    target = "wio-e5-mini_companion_radio_usb"
    base = "lora_e5_mini"
    extra_timer_targets = ("env:wio-e5-mini_repeater",)

    def test_headless_button_controls_are_retained(self):
        self.assertRegex(self.companion["build_flags"],
                         r"-D\s+WIO_E5_MINI_HEADLESS_COMPANION_UI=1\b")
        self.assertRegex(self.config[self.base]["build_flags"],
                         r"-D\s+PIN_USER_BTN=USER_BTN\b")


class STM32CompanionCICoverageTest(unittest.TestCase):
    def test_every_small_companion_is_linked_serially_at_release_version_length(self):
        workflow = (ROOT / ".github/workflows/run-unit-tests.yml").read_text()
        job = re.search(r"^  stm32-companion-smoke:\n(.*?)(?=^  [\w-]+:|\Z)",
                        workflow, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(job)
        script = job[1].replace("\\\n", "")
        loop = re.search(r"for target in (.*?); do", script, re.DOTALL)
        self.assertIsNotNone(loop)
        self.assertEqual(set(shlex.split(loop[1])), {
            "RAK_3x72_companion_radio_usb", "wio-e5-mini_companion_radio_usb",
            "wio-e5_companion_radio_usb", "Tiny_Relay_companion_radio_usb"})
        self.assertIn("set -euo pipefail", script)
        self.assertIn('bash build.sh build-firmware "$target"', script)
        self.assertIn("--firmware-version v1.18.0.1-halo-keymind-cascade-dev", script)
        self.assertNotIn(" &", script)
        command = textwrap.dedent(job[1].rsplit("        run: |\n", 1)[1])
        subprocess.run(["bash", "-n"], input=command, text=True, check=True)


class RAK3x72BatteryScaleTest(unittest.TestCase):
    def test_all_eight_sample_adc_sums_keep_exact_previous_voltage_and_sampling(self):
        header = (ROOT / "variants/rak3x72/target.h").read_text()
        scale = re.search(r"^#define\s+ADC_MULTIPLIER\s+(.+)$", header, re.MULTILINE)[1]
        sample = body(header, "uint16_t getBattMilliVolts() override").replace(" override", "")
        source = r'''
#include <cassert>
#include <cstdint>
#include <limits>
#define PIN_VBAT_READ 7
#define ADC_MULTIPLIER @SCALE@
static unsigned reads, resolutions;
static uint32_t remaining;
void analogReadResolution(int bits) { assert(bits == 12 && reads == 0); ++resolutions; }
int analogRead(int pin) {
  assert(pin == PIN_VBAT_READ && resolutions == 1 && reads < 8);
  ++reads;
  const uint32_t value = remaining > 4095 ? 4095 : remaining;
  remaining -= value;
  return value;
}
class Board { public: @BODY@ };
static_assert(ADC_MULTIPLIER == 8650, "revisit the fixed-point scale if calibration changes");
static_assert(uint64_t(8) * 4095 * 8650 <= std::numeric_limits<uint32_t>::max(),
              "battery accumulation product must not overflow");
int main() {
  Board board;
  for (uint32_t sum = 0; sum <= 8 * 4095; ++sum) {
    reads = resolutions = 0;
    remaining = sum;
    const uint16_t previous = ((double)sum) * ADC_MULTIPLIER / 8 / 4096;
    assert(board.getBattMilliVolts() == previous);
    assert(reads == 8 && resolutions == 1 && remaining == 0);
  }
}
'''.replace("@SCALE@", scale).replace("@BODY@", sample)
        with tempfile.TemporaryDirectory(prefix="mesh-rak3x72-battery-") as directory:
            root = Path(directory)
            cpp = root / "battery.cpp"
            cpp.write_text(source)
            binary = root / "battery"
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            *SANITIZER_FLAGS, str(cpp), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
