"""Compile the actual peripheral pin policy against MCU pin-map adapters."""
from pathlib import Path
import configparser
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PeripheralPinPolicyTest(unittest.TestCase):
    def run_policy(self, macros, prelude, body):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="peripheral-pin-policy-") as directory:
            source = Path(directory) / "policy.cpp"
            binary = Path(directory) / "policy"
            source.write_text("#include <stdint.h>\n#include <cassert>\n#include <initializer_list>\n" + prelude
                              + "\n#include <helpers/PeripheralPinPolicy.h>\n"
                              + "using namespace PeripheralPinPolicy;\nint main(){\n"
                              + body + "\n}\n")
            built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                *["-D" + macro for macro in macros], "-I" + str(ROOT / "src"),
                str(source), "-o", str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    ESP32 = """
constexpr uint8_t TX=1, RX=3, SDA=21, SCL=22;
#define digitalPinIsValid(pin) ((pin) < 40 && (pin) != 20 && (pin) != 24)
#define digitalPinCanOutput(pin) (digitalPinIsValid(pin) && (pin) < 34)
"""

    def test_esp32_input_only_pads_require_external_pullups(self):
        self.run_policy(["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32"], self.ESP32, """
assert(available(34, Use::Input));
assert(rejection(34, Use::InputPullup) == Rejection::NoPullup);
assert(rejection(34, Use::Output) == Rejection::NoOutput);
assert(available(4, Use::InputPullup));
assert(available(4, Use::Output));
assert(rejection(24, Use::Input) == Rejection::InvalidPin);
for (uint8_t pin : {0, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 1, 3, 21, 22})
  assert(!available(pin, Use::Input));
""")

    def test_esp32_s3_flash_usb_jtag_and_hardware_are_locked(self):
        self.run_policy(["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32S3", "CONFIG_SPIRAM_MODE_OCT",
                         "P_LORA_NSS=8", "PIN_VBAT_READ=1", "PIN_BOARD_SDA=17", "PIN_BOARD_SCL=18"], """
#include <initializer_list>
constexpr uint8_t TX=43, RX=44, SDA=17, SCL=18;
#define digitalPinIsValid(pin) ((pin) < 49)
#define digitalPinCanOutput(pin) ((pin) < 49 && (pin) != 46)
""", """
assert(available(4, Use::InputPullup));
for (uint8_t pin : {0, 3, 8, 1, 17, 18, 19, 20, 26, 27, 28, 29, 30, 31, 32,
                    33, 34, 35, 36, 37, 39, 40, 41, 42, 43, 44, 45, 46})
  assert(!available(pin, Use::InputPullup));
assert(rejection(49, Use::Input) == Rejection::InvalidPin);
""")

    def test_existing_boot_button_can_be_reused_but_never_radio_alias(self):
        self.run_policy(["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32", "PIN_USER_BTN=0",
                         "PIN_BUTTON1=0", "BUTTON_PIN=0"], self.ESP32, """
assert(!available(0, Use::InputPullup));
assert(availableButton(0));
assert(!availableButton(2));
""")
        self.run_policy(["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32", "PIN_USER_BTN=0",
                         "P_LORA_NSS=0"], self.ESP32, """
assert(!availableButton(0));
assert(buttonRejection(0) == Rejection::FirmwareReserved);
""")

    def test_active_high_native_button_is_not_reused_as_active_low_switch(self):
        adapter = self.ESP32 + "\nconstexpr uint8_t LOW=0, HIGH=1;\n"
        self.run_policy(["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32", "PIN_USER_BTN=4",
                         "USER_BTN_PRESSED=HIGH"], adapter, """
assert(!availableButton(4));
assert(!availableButton(4, false));
assert(buttonRejection(4) == Rejection::FirmwareReserved);
assert(availableButton(16));
""")
        self.run_policy(["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32", "PIN_USER_BTN=4",
                         "USER_BTN_PRESSED=LOW"], adapter, """
assert(availableButton(4));
assert(availableButton(16));
""")

    def test_native_usb_releases_only_unassigned_default_uart_pads(self):
        adapter = """
constexpr uint8_t TX=43, RX=44, SDA=17, SCL=18;
#define digitalPinIsValid(pin) ((pin) <= 21 || ((pin) >= 26 && (pin) <= 48))
#define digitalPinCanOutput(pin) (digitalPinIsValid(pin) && (pin) != 46)
"""
        self.run_policy(["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32S3", "ARDUINO_USB_CDC_ON_BOOT=1"],
            adapter, """
assert(available(43, Use::InputPullup));
assert(available(44, Use::Output));
""")
        self.run_policy(["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32S3", "ARDUINO_USB_CDC_ON_BOOT=0"],
            adapter, """
assert(!available(43, Use::InputPullup));
assert(!available(44, Use::Output));
""")
        self.run_policy(["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32S3", "ARDUINO_USB_CDC_ON_BOOT=1",
                         "PIN_SERIAL_TX=43", "WITH_RS232_BRIDGE_RX=44"], adapter, """
assert(!available(43, Use::InputPullup));
assert(!available(44, Use::Output));
""")

    def test_actual_unified_v4_display_recipe_leaves_only_encoder_pair(self):
        config = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
        config.read([ROOT / "platformio.ini", ROOT / "variants/heltec_v4/platformio.ini"])

        def expand(value):
            def reference(match):
                section, key = match[1].rsplit(".", 1)
                return expand(config[section][key])
            return re.sub(r"\$\{([^}]+)\}", reference, value)

        flags = expand(config["heltec_v4_display"]["build_flags"])
        macros = [name + "=" + value for name, value in re.findall(
            r"-D\s*((?:P_LORA_|PIN_|ENV_PIN_|HELTEC_V4_)[A-Z0-9_]+)=(-?\d+)\b", flags)]
        target = (ROOT / "variants/heltec_v4/target.h").read_text()
        reserved = re.search(r"#define USER_GPIO_RESERVED_PINS ([^\n]+)", target)[1]
        self.run_policy(["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32S3", "ARDUINO_USB_CDC_ON_BOOT=1",
                         "DISPLAY_CLASS=HeltecV4Display", "USER_GPIO_RESERVED_PINS=" + reserved,
                         *macros], """
constexpr uint8_t TX=43, RX=44, SDA=3, SCL=4;
#define digitalPinIsValid(pin) ((pin) <= 21 || ((pin) >= 26 && (pin) <= 48))
#define digitalPinCanOutput(pin) (digitalPinIsValid(pin) && (pin) != 46)
""", """
for (uint8_t pin=0; pin<64; ++pin)
  assert(available(pin, Use::InputPullup) == (pin == 6 || pin == 43));
assert(availableButton(0));
assert(!available(44, Use::Output));
assert(!available(47, Use::Output));
assert(!available(48, Use::Output));
""")

    def test_nrf52_maps_aliases_and_guards_flash_power_and_native_rotary(self):
        self.run_policy(["NRF52_PLATFORM", "PINS_COUNT=13", "USER_GPIO_ALLOWED_PINS=0,1,2,3,4,5,6,7,8,9,10,11,12",
                         "PIN_QSPI_CS=1", "PIN_BUTTON1_A=4", "PIN_BUTTON1_B=5",
                         "VBAT_ENABLE=6", "PIN_CHARGING_CURRENT=7", "PIN_VBAT=8"], """
const uint32_t g_ADigitalPinMap[] = {2,3,3,0,4,5,6,7,8,9,10,18,0xff};
""", """
assert(available(0, Use::InputPullup));
assert(physicalPin(0) == 2);
assert(samePhysicalPin(1,2));
for (unsigned pin=1; pin<13; ++pin) assert(!available(pin, Use::InputPullup));
assert(rejection(2, Use::Output) == Rejection::FirmwareReserved);
assert(rejection(12, Use::Input) == Rejection::InvalidPin);
assert(rejection(13, Use::Input) == Rejection::InvalidPin);
assert(!samePhysicalPin(12,12));
""")

    def test_integrated_esp32_boards_require_audited_exposure_and_keep_wired_buses_locked(self):
        adapter = """
constexpr uint8_t TX=43, RX=44, SDA=1, SCL=2;
#define digitalPinIsValid(pin) ((pin) <= 21 || ((pin) >= 26 && (pin) <= 48))
#define digitalPinCanOutput(pin) (digitalPinIsValid(pin) && (pin) != 46)
"""
        common = ["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32S3", "ARDUINO_USB_CDC_ON_BOOT=1"]
        cardputer = '#include "' + str(ROOT / "variants/m5stack_cardputer_adv/globals.h") + '"\n'
        self.run_policy([*common, "M5STACK_CARDPUTER_ADV", "PIN_SD_CS=12"], cardputer + adapter, """
for (uint8_t pin=0; pin<64; ++pin) assert(!available(pin, Use::Output));
assert(UserGpioPinPolicy::isFirmwareReserved(8));
assert(UserGpioPinPolicy::isFirmwareReserved(9));
assert(UserGpioPinPolicy::isFirmwareReserved(11));
assert(UserGpioPinPolicy::isFirmwareReserved(12));
assert(UserGpioPinPolicy::isFirmwareReserved(43));
assert(UserGpioPinPolicy::isFirmwareReserved(44));
""")
        self.run_policy([*common, "M5STACK_CARDPUTER_ADV", "USER_GPIO_ALLOWED_PINS=8,16,44"],
            cardputer + adapter, """
assert(available(16, Use::InputPullup));
assert(!available(8, Use::InputPullup));
assert(!available(44, Use::Output));
""")
        for board, wired in (("MESH_SENSECAP_INDICATOR", "0,6,16,21,45,48"),
                             ("SEEED_WIO_TRACKER_L2", "10,11,12,16,38,39,40,41,42,46"),
                             ("LILYGO_TDECK", "1,2,3,5,6,7,14,15,16,21,39,46,47,48")):
            with self.subTest(board=board):
                self.run_policy([*common, board], adapter,
                    "for(uint8_t pin=0;pin<64;++pin) assert(!available(pin,Use::Input));")
                self.run_policy([*common, board, "USER_GPIO_ALLOWED_PINS=" + wired], adapter,
                    "for(uint8_t pin : {" + wired + "}) assert(!available(pin,Use::Output));")

    def test_station_g2_socket_whitelist_applies_to_inputs_and_static_outputs(self):
        self.run_policy(["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32S3", "STATION_G2",
                         "ARDUINO_USB_CDC_ON_BOOT=1"], """
constexpr uint8_t TX=43, RX=44, SDA=1, SCL=2;
#define digitalPinIsValid(pin) ((pin) <= 21 || ((pin) >= 26 && (pin) <= 48))
#define digitalPinCanOutput(pin) (digitalPinIsValid(pin) && (pin) != 46)
""", """
for (uint8_t pin=0;pin<64;++pin) {
  const bool allowed = pin==8 || pin==9 || pin==16 || pin==17 || pin==18;
  assert(available(pin,Use::InputPullup)==allowed);
  assert(available(pin,Use::Output)==allowed);
}
""")

    def test_nrf52_unknown_board_requires_allowlist_and_xiao_only_header(self):
        prelude = "const uint32_t g_ADigitalPinMap[] = {2,3,28,29,4,5,43,44,45,46,47,26,6};"
        self.run_policy(["NRF52_PLATFORM", "PINS_COUNT=13"], prelude, """
assert(rejection(0, Use::Input) == Rejection::NotExposed);
""")
        self.run_policy(["NRF52_PLATFORM", "PINS_COUNT=13", "XIAO_NRF52"], prelude, """
assert(available(0, Use::InputPullup));
assert(available(10, Use::Output));
assert(rejection(11, Use::Input) == Rejection::NotExposed);
""")

    def test_rp2040_excludes_internal_pico_and_unmapped_flash_pads(self):
        self.run_policy(["RP2040_PLATFORM", "ARDUINO_RASPBERRY_PI_PICO_W",
                         "PIN_VBAT_READ=26", "P_LORA_NSS=3"], "", """
assert(available(6, Use::InputPullup));
assert(available(28, Use::Output));
assert(rejection(23, Use::Output) == Rejection::NotExposed);
assert(rejection(26, Use::Input) == Rejection::FirmwareReserved);
assert(rejection(3, Use::Input) == Rejection::FirmwareReserved);
assert(rejection(29, Use::Input) == Rejection::NotExposed);
assert(rejection(30, Use::Input) == Rejection::InvalidPin);
""")

    def test_stm32_map_rejects_debug_clock_radio_and_physical_aliases(self):
        self.run_policy(["STM32_PLATFORM", "STM32WLxx", "NUM_DIGITAL_PINS=8", "NC=255",
                         "PA13=1", "PC14=2", "PA4=3", "PIN_VBAT_READ=4",
                         "USER_GPIO_ALLOWED_PINS=0,1,2,3,4,5,6,7"], """
const uint8_t mapping[] = {2,13,46,4,3,3,7,255};
inline uint8_t digitalPinToPinName(uint8_t pin) { return mapping[pin]; }
""", """
assert(available(0, Use::InputPullup));
assert(rejection(1, Use::Input) == Rejection::SystemReserved);
assert(rejection(2, Use::Input) == Rejection::SystemReserved);
assert(rejection(3, Use::Input) == Rejection::SystemReserved);
assert(rejection(5, Use::Input) == Rejection::FirmwareReserved);
assert(rejection(7, Use::Input) == Rejection::InvalidPin);
assert(physicalPin(8) == UINT32_MAX);
""")

    def test_stm32_e5_mini_header_support_excludes_battery_and_dedicated_pads(self):
        self.run_policy(["STM32_PLATFORM", "STM32WLxx", "MESH_WIO_E5_MINI", "NUM_DIGITAL_PINS=27",
                         "NC=255", "PA13=16", "PA14=15", "PC14=24", "PC15=23",
                         "PA4=20", "PA5=21", "PA6=26", "PA7=25", "PB0=22",
                         "PIN_VBAT_READ=17", "PIN_USER_BTN=11", "PIN_LED=19"], """
inline uint8_t digitalPinToPinName(uint8_t pin) { return pin; }
""", """
assert(available(0, Use::InputPullup));
assert(available(18, Use::Output));
assert(!available(11, Use::InputPullup));
assert(availableButton(11));
assert(!available(17, Use::InputPullup));
for (uint8_t pin : {15,16,19,20,21,22,23,24,25,26})
  assert(!available(pin, Use::Output));
""")

    def test_board_hardware_reservations_include_byte_pins_and_ignore_nc(self):
        self.run_policy(["USER_GPIO_RESERVED_PINS=120", "P_LORA_NSS=0xffffffffu",
                         "PIN_BUTTON1_A=8", "PIN_BUTTON1_B=9", "PIN_VBAT_READ=-1"], "", """
assert(UserGpioPinPolicy::isFirmwareReserved(120));
assert(UserGpioPinPolicy::isFirmwareReserved(8));
assert(UserGpioPinPolicy::isFirmwareReserved(9));
assert(!UserGpioPinPolicy::isFirmwareReserved(0));
""")

    def test_nrf_raw_nor_override_does_not_claim_on_other_platform(self):
        self.run_policy(["OTA_QSPI_STORE=1", "OTA_QSPI_CS_ARDUINO_PIN=31"], "", """
assert(!UserGpioPinPolicy::isFirmwareReserved(31));
""")

    def test_optional_peripherals_default_only_to_full_supported_companions(self):
        cases = (
            (["COMPANION_RADIO_FULL=1", "ESP32_PLATFORM"], 1, self.ESP32),
            (["COMPANION_RADIO_FULL=1", "NRF52_PLATFORM"], 1, ""),
            (["COMPANION_RADIO_FULL=1", "RP2040_PLATFORM"], 1, ""),
            (["COMPANION_RADIO_FULL=1", "STM32_PLATFORM"], 0, ""),
            (["ESP32_PLATFORM"], 0, self.ESP32),
            (["UI_ROTARY_INPUT_GPIO=1"], 1, ""),
            (["MESH_CUSTOM_PERIPHERALS=1", "STM32_PLATFORM"], 1, ""),
            (["COMPANION_RADIO_FULL=1", "ESP32_PLATFORM", "MESH_CUSTOM_PERIPHERALS=0"], 0, self.ESP32),
        )
        for macros, enabled, prelude in cases:
            with self.subTest(macros=macros):
                self.run_policy(macros, prelude + "\n#include <helpers/CustomPeripheralConfig.h>\n",
                    "static_assert(MESH_CUSTOM_PERIPHERALS == %d, \"feature scope\");" % enabled)


if __name__ == "__main__":
    unittest.main()
