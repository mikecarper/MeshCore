#!/usr/bin/env python3
"""Run ESP32Board's real sleep/USB methods against host peripheral stubs."""

from pathlib import Path
import configparser
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_morse_loop_policy import RADIO as MORSE_RADIO

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cstdint>
#include <stdexcept>
#include <iostream>
#include <array>
#include <helpers/UsbHostSleepPolicy.h>

#ifndef MESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS
#define MESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS 120000UL
#endif
static uint32_t mock_millis = 1000;
static uint32_t millis() { return mock_millis; }

static void require(bool ok, const char* message) {
  if (!ok) throw std::runtime_error(message);
}
struct SerialPort {
  bool terminal_open = false;
  bool host_attached = false;
  bool driver_initialized = true;
  explicit operator bool() const {
#if ARDUINO_USB_CDC_ON_BOOT
    return terminal_open;
#else
    return driver_initialized;
#endif
  }
  bool isPlugged() const { return host_attached; }
  void end() { driver_initialized = false; }
} Serial;
struct UsbDevice {
  bool mounted = false;
  explicit operator bool() const { return mounted; }
} USB;
static void attachHost(bool attached) {
  Serial.host_attached = USB.mounted = attached;
}
namespace mesh {
static bool logging_enabled = false;
bool isUsbLoggingEnabled() { return logging_enabled; }
}

using gpio_num_t = int;
constexpr int HIGH = 1;
constexpr int LOW = 0;
constexpr int USER_BTN_PRESSED = LOW;
constexpr int GPIO_INTR_HIGH_LEVEL = 5;
constexpr int GPIO_INTR_LOW_LEVEL = 4;
constexpr int GPIO_INTR_POSEDGE = 1;
constexpr int GPIO_INTR_DISABLE = 0;
static unsigned sleep_calls = 0;
static uint64_t timer_us = 0;
static bool radio_irq_high = false;
static int critical_depth = 0;
static int wake_pin = -1;
static int restored_pin = -1;
static std::array<int, 64> wake_levels{};
static std::array<int, 64> restored_interrupts{};
static bool button_pressed = false;
static bool press_during_sleep = false;
static unsigned yield_ms = 0;
static void delay(unsigned ms) { yield_ms += ms; }
static void esp_sleep_enable_timer_wakeup(uint64_t us) { timer_us = us; }
static void esp_light_sleep_start() {
  ++sleep_calls;
  if (press_during_sleep) {
    require(wake_levels[38] == GPIO_INTR_LOW_LEVEL,
            "G3 button cannot wake the CPU from sleep");
    require(wake_levels[48] == GPIO_INTR_HIGH_LEVEL,
            "button wake replaced the LoRa wake source");
    button_pressed = true;
    press_during_sleep = false;
  }
}
static void esp_sleep_enable_gpio_wakeup() {}
static int gpio_get_level(gpio_num_t pin) {
  return pin == 38 ? (button_pressed ? LOW : HIGH) : (radio_irq_high ? HIGH : LOW);
}
static void gpio_wakeup_enable(gpio_num_t pin, int level) {
  wake_pin = pin;
  wake_levels.at(pin) = level;
}
static void gpio_wakeup_disable(gpio_num_t pin) {
  restored_pin = pin;
  wake_levels.at(pin) = 0;
}
static void gpio_set_intr_type(gpio_num_t pin, int type) {
  restored_interrupts.at(pin) = type;
}
#define portENTER_CRITICAL(mux) (++critical_depth)
#define portEXIT_CRITICAL(mux) (--critical_depth)

struct MainBoard {
  bool radio_test_active = false;
  bool isRadioTestActive() const { return radio_test_active; }
  virtual void sleep(uint32_t) = 0;
  virtual bool isUsbDataConnected() = 0;
  virtual bool isUsbHostConnected() = 0;
};
struct ESP32Board : MainBoard {
  bool inhibit_sleep = false;
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
  mesh::UsbHostSleepPolicy usb_host_sleep_policy;
#endif
  uint32_t irq = 48; // Station G3 LoRa DIO1
  uint32_t getIRQGpio() { return irq; }
@METHODS@
};

int main() {
  try {
    ESP32Board board;
#if ARDUINO_USB_CDC_ON_BOOT
    // The Pi has enumerated the G3, but no terminal asserts CDC DTR.
    attachHost(true);
    require(board.isUsbHostConnected(), "enumerated host was missed");
    require(!board.isUsbDataConnected(), "closed terminal reported as open");
    board.sleep(30);
    require(sleep_calls == 0, "closed terminal allowed sleep with USB attached");
    require(timer_us == 0 && wake_pin == -1 && critical_depth == 0,
            "USB-inhibited sleep touched wake peripherals");
    require(yield_ms > 0, "USB-inhibited sleep did not yield to other tasks");

    Serial.terminal_open = true;
    board.sleep(30);
    require(sleep_calls == 0, "open terminal allowed USB sleep");
    Serial.terminal_open = false;

    // The timer-only path must also preserve USB, with no LoRa wake pin.
    board.irq = static_cast<uint32_t>(-1);
    board.sleep(30);
    require(sleep_calls == 0, "timer-only sleep lost USB");
    board.irq = 48;
#else
    // A UART driver has no native host detection. Its initialized console
    // must preserve the first incoming command even before any host traffic.
    attachHost(true);
    Serial.terminal_open = true;
    require(!board.isUsbHostConnected(), "UART build invented a USB host");
    board.sleep(30);
    require(sleep_calls == 0, "initialized UART console allowed light sleep");
    Serial.end();
#endif

    // After the bounded host-reboot grace, disconnected USB power must still
    // permit battery saving. A fresh never-seen host is tested separately.
    attachHost(false);
    Serial.terminal_open = false;
    mock_millis += MESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS;
    board.sleep(30);
    require(sleep_calls == 1 && timer_us == 30000000ULL,
            "disconnected board did not retain its scheduled sleep");
    require(restored_interrupts[48] == GPIO_INTR_POSEDGE
            && wake_levels[48] == 0 && critical_depth == 0,
            "LoRa wake/interrupt restoration changed");

#if ARDUINO_USB_CDC_ON_BOOT
    // Reattaching the Pi must inhibit the next sleep without opening a TTY.
    attachHost(true);
    board.sleep(30);
    require(sleep_calls == 1, "reattached closed terminal failed to inhibit sleep");
    attachHost(false);
    mock_millis += MESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS;
#endif

    board.inhibit_sleep = true;
    board.sleep(30);
    require(sleep_calls == 1, "OTA sleep inhibition regressed");
    board.inhibit_sleep = false;
    board.radio_test_active = true;
    board.sleep(30);
    require(sleep_calls == 1, "CW deadline would be missed while USB is disconnected");
    board.radio_test_active = false;
    radio_irq_high = true;
    board.sleep(30);
    require(sleep_calls == 1 && critical_depth == 0,
            "pending LoRa packet was stranded in sleep");
    radio_irq_high = false;
#if MOMENTARY_BUTTON_WAKE_FROM_SLEEP
    // A fresh press during sleep wakes GPIO 38 alongside the radio GPIO.
    press_during_sleep = true;
    board.sleep(30);
    require(sleep_calls == 2 && button_pressed,
            "button did not wake a sleeping G3");
    require(wake_levels[38] == 0
            && restored_interrupts[38] == GPIO_INTR_DISABLE,
            "button wake interrupt was not cleaned up");
    board.sleep(30);
    require(sleep_calls == 2 && critical_depth == 0,
            "held button was stranded before the UI could poll it");
    button_pressed = false;
    board.sleep(30);
    require(sleep_calls == 3, "released button permanently disabled sleep");
#else
    board.irq = static_cast<uint32_t>(-1);
    board.sleep(0);
    require(sleep_calls == 1, "slept without a wake source");
    board.sleep(2);
    require(sleep_calls == 2 && timer_us == 2000000ULL,
            "disconnected timer-only sleep regressed");
#endif

    // Logging is an explicit sleep blocker even with the cable disconnected.
    const unsigned before_logging = sleep_calls;
    mesh::logging_enabled = true;
    board.sleep(30);
#if MESH_USB_LOGGING_AVAILABLE
    require(sleep_calls == before_logging, "enabled logging allowed USB sleep");
#else
    require(sleep_calls == before_logging + 1,
            "compiled-out logging disabled power saving");
#endif
    mesh::logging_enabled = false;
    const unsigned after_logging = sleep_calls;
    board.sleep(30);
    require(sleep_calls == after_logging + 1,
            "turning logging off did not restore sleep eligibility");
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
'''


def board_method(signature: str) -> str:
    source = (ROOT / "src/helpers/ESP32Board.h").read_text()
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def v4_profile_config():
    config = configparser.ConfigParser(
        interpolation=None, inline_comment_prefixes=("#", ";"))
    config.read([ROOT / "platformio.ini",
                 ROOT / "variants/heltec_v4/platformio.ini",
                 ROOT / "variants/heltec_v4_r8/platformio.ini"])
    return config


def profile_option(config, section, option, seen=()):
    key = (section, option)
    if key in seen:
        raise ValueError("cyclic profile interpolation")
    if config.has_option(section, option):
        value = config.get(section, option)
    else:
        parents = config.get(section, "extends", fallback="").replace(",", " ").split()
        value = next(profile_option(config, parent, option, seen + (key,))
                     for parent in parents if config.has_section(parent))
    return re.sub(r"\x24[{]([^}]+)[}]",
                  lambda match: profile_option(
                      config, *match[1].rsplit(".", 1), seen + (key,)),
                  value)


def button_defines(config, section):
    flags = profile_option(config, section, "build_flags")
    values = {}
    for match in re.finditer(
            r"(?:^|\s)-(D|U)\s*([A-Za-z_]\w*)(?:=([^\s]+))?", flags):
        operation, name, value = match.groups()
        if operation == "U":
            values.pop(name, None)
        else:
            values[name] = value if value is not None else "1"
    return values


def conditional_block(source, start):
    """Keep the production preprocessor branches, including nested guards."""
    depth = 0
    end = start
    for line in source[start:].splitlines(keepends=True):
        if re.match(r"\s*#if(?:def|ndef)?\b", line):
            depth += 1
        elif re.match(r"\s*#endif\b", line):
            depth -= 1
        end += len(line)
        if depth == 0:
            return source[start:end]
    raise ValueError("unterminated conditional block")


V4_BUTTON_HARNESS = r'''
#include <Arduino.h>
#include <helpers/UsbHostSleepPolicy.h>
#include <helpers/ui/MomentaryButton.h>
#include <helpers/ui/DisplayPowerPolicy.h>
#include <helpers/MorseLoopPolicy.h>
#include <array>
#include <stdexcept>
#include <iostream>
static void require(bool ok, const char* why) {
  if (!ok) throw std::runtime_error(why);
}
struct SerialPort {
  explicit operator bool() const { return false; }
  bool isPlugged() const { return false; }
} Serial;
namespace mesh {
bool isUsbLoggingEnabled() { return false; }
struct Console { void printf(const char*) {} };
static Console console;
Console& usbConsolePort() { return console; }
}
using gpio_num_t = int;
constexpr int USER_BTN_PRESSED = LOW;
constexpr int GPIO_INTR_HIGH_LEVEL = 5;
constexpr int GPIO_INTR_LOW_LEVEL = 4;
constexpr int GPIO_INTR_POSEDGE = 1;
constexpr int GPIO_INTR_DISABLE = 0;
static std::array<int, 64> wake_levels{}, restored_interrupts{};
static unsigned sleep_calls = 0;
static int critical_depth = 0;
static bool press_during_sleep = false, radio_irq_high = false;
static void esp_sleep_enable_timer_wakeup(uint64_t) {}
static void esp_sleep_enable_gpio_wakeup() {}
static int gpio_get_level(gpio_num_t pin) {
  return pin == PIN_USER_BTN ? digitalRead(pin) : (radio_irq_high ? HIGH : LOW);
}
static void gpio_wakeup_enable(gpio_num_t pin, int level) { wake_levels.at(pin) = level; }
static void gpio_wakeup_disable(gpio_num_t pin) { wake_levels.at(pin) = 0; }
static void gpio_set_intr_type(gpio_num_t pin, int type) { restored_interrupts.at(pin) = type; }
static void esp_light_sleep_start() {
  ++sleep_calls;
  if (press_during_sleep) {
    require(wake_levels[PIN_USER_BTN] == GPIO_INTR_LOW_LEVEL,
            "resolved V4 profile did not arm active-low button wake");
    require(wake_levels[P_LORA_DIO_1] == GPIO_INTR_HIGH_LEVEL,
            "button wake replaced the radio wake source");
    g_mock_pin_levels[PIN_USER_BTN] = LOW;
    press_during_sleep = false;
  }
}
#define portENTER_CRITICAL(mux) (++critical_depth)
#define portEXIT_CRITICAL(mux) (--critical_depth)
struct MainBoard {
  virtual void sleep(uint32_t) = 0;
  virtual bool isUsbDataConnected() = 0;
  virtual bool isUsbHostConnected() = 0;
  bool isRadioTestActive() const { return false; }
};
struct ESP32Board : MainBoard {
  bool inhibit_sleep = false;
  uint32_t getIRQGpio() { return P_LORA_DIO_1; }
  bool isExternalPowered() const { return false; }
@METHODS@
} board;
struct TestRadio : mesh::Radio {
  bool tx = false, rx = false;
  bool isMorseTxActive() const override { return tx; }
  bool isMorseRxActive() const override { return rx; }
} timing_radio;
struct TestMesh { mesh::Radio* getProfileRadio() { return &timing_radio; } } the_mesh;
TestMesh& activeSensorMesh() { return the_mesh; }
@BUTTON_CONSTRUCTOR@
struct TestDisplay {
  mesh::ui::DisplayPowerPrefs prefs;
  mesh::ui::DisplayPowerPolicy policy;
  bool servicePower(bool usb) {
    const bool before = policy.on();
    policy.update(prefs, usb, false, false, millis());
    return before != policy.on();
  }
  bool isOn() const { return policy.on(); }
  void wake(mesh::ui::DisplayWake reason) { policy.wake(reason, millis()); }
} display;
static constexpr uint32_t BOOT_SCREEN_MILLIS = 4000;
struct UITask {
  TestDisplay* _display = &display;
  bool _startup_complete = false;
  uint32_t _started_at = 0;
  uint32_t _next_refresh = 0, _powering_off_at = 0, _next_read = 0;
  uint32_t _last_refresh = 0, _last_read = 0; // Sensor uses elapsed-time anchors.
  int _prevBtnState = HIGH;
  void resetRadioProfileDisplayPage() {}
  void advanceRadioProfileDisplayPage() {}
  void loop() {
@UI_BUTTON@
  }
} ui;
static bool roleCanPowerSave() {
  bool can_power_save = true;
@ROLE_SLEEP_GUARD@
  return can_power_save;
}
int main() {
  try {
    static_assert(PIN_USER_BTN == 0 && P_LORA_DIO_1 == 14,
                  "fixture must use the actual V4 GPIOs");
    resetArduinoMock();
    g_mock_millis = 1000;
    g_mock_pin_levels[PIN_USER_BTN] = HIGH;
    user_btn.begin();
    require(!mesh::morseTimingActive(the_mesh.getProfileRadio()), "idle radio acquired Morse ownership");
    timing_radio.tx = true;
    require(mesh::morseTimingActive(the_mesh.getProfileRadio()), "TX ownership was not visible to UI");
    ui.loop();
    require(!display.isOn(), "button-mode screen did not start blank");
    require(roleCanPowerSave(), "idle button blocked initial power saving");
    // A press already pending must be serviced instead of entering sleep.
    g_mock_pin_levels[PIN_USER_BTN] = LOW;
    board.sleep(30);
    require(sleep_calls == 0 && critical_depth == 0,
            "V4 slept over an already pressed button");
    g_mock_pin_levels[PIN_USER_BTN] = HIGH;
    // Simulate the physical edge arriving while the CPU is sleeping.
    press_during_sleep = true;
    board.sleep(30);
    require(sleep_calls == 1 && digitalRead(PIN_USER_BTN) == LOW,
            "fresh button press failed to wake the V4");
    require(wake_levels[PIN_USER_BTN] == 0 && wake_levels[P_LORA_DIO_1] == 0
            && restored_interrupts[PIN_USER_BTN] == GPIO_INTR_DISABLE
            && restored_interrupts[P_LORA_DIO_1] == GPIO_INTR_POSEDGE
            && critical_depth == 0, "button/radio wake cleanup regressed");
    ui.loop();
    require(user_btn.needsPolling() && !roleCanPowerSave(),
            "role slept before button debounce completed");
    g_mock_millis += 25;
    ui.loop();
    require(user_btn.needsPolling() && !roleCanPowerSave(),
            "held button stopped gesture polling");
    g_mock_millis += 50;
    g_mock_pin_levels[PIN_USER_BTN] = HIGH;
    ui.loop();
    require(user_btn.needsPolling() && !roleCanPowerSave(),
            "role slept before release debounce completed");
    g_mock_millis += 25;
    ui.loop();
    require(user_btn.needsPolling() && !roleCanPowerSave(),
            "role slept over the pending multi-click deadline");
    g_mock_millis += MOMENTARY_BUTTON_MULTI_CLICK_MS;
    ui.loop();
    require(display.isOn(), "real UITask click did not wake the screen");
@SENSOR_REFRESH_CHECK@
    require(!user_btn.needsPolling() && roleCanPowerSave(),
            "completed gesture permanently disabled power saving");
    timing_radio.tx = false;
    timing_radio.rx = true;
    require(mesh::morseTimingActive(activeSensorMesh().getProfileRadio()), "RX ownership was not visible to UI");
    board.sleep(30);
    require(sleep_calls == 2, "released button failed to allow sleep again");
    g_mock_millis += 15000;
    ui.loop();
    require(!display.isOn(), "button screen failed to expire normally");
    timing_radio.rx = false;
    require(!mesh::morseTimingActive(the_mesh.getProfileRadio()), "Morse ownership failed to release");
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n'; return 1;
  }
}
'''


class Esp32UsbSleepTest(unittest.TestCase):
    def test_v4_profiles_inherit_button_wake_with_the_actual_gpio_pins(self):
        config = v4_profile_config()
        # These standalone partition-migration utilities deliberately bypass
        # the V4 board/display/radio bases and have no user-button support.
        utilities = {"env:heltec_v4_partition_migrator" + suffix for suffix in
                     ("", "_lora_repeater", "_test_hold", "_test_usb_diagnostic")}
        variants = configparser.ConfigParser(interpolation=None)
        variants.read([ROOT / "variants/heltec_v4/platformio.ini",
                       ROOT / "variants/heltec_v4_r8/platformio.ini"])
        profiles = [section for section in variants.sections()
                    if section.startswith("env:") and section not in utilities]
        self.assertGreater(len(profiles), 30)
        for section in profiles:
            with self.subTest(profile=section):
                defines = button_defines(config, section)
                self.assertEqual(defines["MOMENTARY_BUTTON_WAKE_FROM_SLEEP"], "1")
                self.assertEqual(defines["PIN_USER_BTN"], "0")
                self.assertEqual(defines["P_LORA_DIO_1"], "14")

    def test_v4_sleep_button_poller_and_real_ui_wake_the_screen(self):
        config = v4_profile_config()
        methods = "\n".join(board_method(signature) for signature in (
            "void sleep(uint32_t secs) override",
            "bool isUsbDataConnected() override",
            "bool isUsbHostConnected() override", "bool isUsbSleepHeld()"))
        compiler = os.environ.get("CXX", "c++")
        self.assertIsNotNone(shutil.which(compiler), "a C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-v4-button-wake-") as directory:
            (Path(directory) / "Dispatcher.h").write_text(MORSE_RADIO, encoding="ascii")
            for family in ("heltec_v4", "heltec_v4_r8"):
                target = (ROOT / f"variants/{family}/target.cpp").read_text()
                constructor = re.search(
                    r"MomentaryButton user_btn\([\s\S]*?\);", target)[0]
                for role, suffix in (("simple_repeater", "repeater"),
                                     ("simple_room_server", "room_server"),
                                     ("simple_sensor", "sensor")):
                    section = f"env:{family}_{suffix}"
                    defines = button_defines(config, section)
                    ui_source = (ROOT / f"examples/{role}/UITask.cpp").read_text()
                    loop = ui_source.index("void UITask::loop()")
                    opening = ui_source.index("{", loop) + 1
                    button_start = ui_source.index("#if defined(PIN_USER_BTN)", opening)
                    button_block = conditional_block(ui_source, button_start)
                    ui = ui_source[opening:button_start] + button_block
                    main = (ROOT / f"examples/{role}/main.cpp").read_text()
                    guard_start = main.index("#if defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP)")
                    guard = conditional_block(main, guard_start)
                    harness = V4_BUTTON_HARNESS.replace("@METHODS@", methods)
                    harness = harness.replace("@BUTTON_CONSTRUCTOR@", constructor)
                    harness = harness.replace("@UI_BUTTON@", ui)
                    harness = harness.replace("@ROLE_SLEEP_GUARD@", guard)
                    harness = harness.replace("@SENSOR_REFRESH_CHECK@",
                        '    require(ui._last_refresh == millis() - 1000u, '
                        '"sensor button wake did not rearm refresh");'
                        if role == "simple_sensor" else "")
                    cases = [("profile", harness, defines, None)]
                    if family == "heltec_v4" and role == "simple_repeater":
                        # Removing the actual inherited flag reproduces the
                        # missed GPIO wake using the same production methods.
                        missing = dict(defines, MOMENTARY_BUTTON_WAKE_FROM_SLEEP="0")
                        cases.append(("missing-flag", harness, missing,
                                      "V4 slept over an already pressed button"))
                        cases.append(("missing-poller", harness.replace(
                            "can_power_save = can_power_save && !user_btn.needsPolling();",
                            ""), defines, "role slept before button debounce completed"))
                        cases.append(("missing-display-wake", harness.replace(
                            "_display->wake(mesh::ui::DisplayWake::Button);",
                            "(void)0;"), defines,
                            "real UITask click did not wake the screen"))
                    if role == "simple_sensor":
                        cases.append(("missing-sensor-refresh", harness.replace(
                            "_last_refresh = millis() - 1000u;", "(void)0;"), defines,
                            "sensor button wake did not rearm refresh"))
                    for name, text, selected, expected_error in cases:
                        with self.subTest(profile=section, control=name):
                            stem = Path(directory) / f"{family}-{suffix}-{name}"
                            cpp = stem.with_suffix(".cpp")
                            cpp.write_text(text, encoding="ascii")
                            flags = [f"-D{key}={selected[key]}" for key in (
                                "PIN_USER_BTN", "P_LORA_DIO_1",
                                "MOMENTARY_BUTTON_WAKE_FROM_SLEEP", "DISPLAY_CLASS")]
                            result = subprocess.run([
                                compiler, "-std=c++17", "-Wall", "-Wextra",
                                "-DARDUINO_USB_CDC_ON_BOOT=1", "-DARDUINO_USB_MODE=1",
                                "-DMESH_ESP32_USB_CONSOLE_COOPERATIVE=0",
                                "-DMESH_USB_LOGGING_AVAILABLE=0",
                                "-DUSER_BTN_LONG_PRESS_MILLIS=" +
                                selected.get("USER_BTN_LONG_PRESS_MILLIS", "1000"),
                                "-DUSER_BTN_MULTICLICK=" +
                                selected.get("USER_BTN_MULTICLICK", "1"),
                                "-DPOWEROFF_DELAY=2000",
                                *flags, "-I", str(Path(directory)), "-I", str(ROOT / "test/mocks"),
                                "-I", str(ROOT / "src"), str(cpp),
                                str(ROOT / "src/helpers/ui/MomentaryButton.cpp"),
                                "-o", str(stem)], capture_output=True, text=True, timeout=60)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            result = subprocess.run(
                                [str(stem)], capture_output=True, text=True, timeout=10)
                            if expected_error is None:
                                self.assertEqual(result.returncode, 0, result.stderr)
                            else:
                                self.assertNotEqual(result.returncode, 0)
                                self.assertIn(expected_error, result.stderr)

    def test_raw_uart_driver_preserves_fresh_console_indefinitely_until_end(self):
        methods = "\n".join(board_method(signature) for signature in (
            "void sleep(uint32_t secs) override", "bool isUsbDataConnected() override",
            "bool isUsbHostConnected() override", "bool isUsbSleepHeld()"))
        harness = HARNESS.split("int main()", 1)[0] + r'''
int main() {
  try {
    ESP32Board board;
    attachHost(false);
    Serial.terminal_open = false;
    mesh::logging_enabled = false;
    for (uint32_t now : {180001U, 10800000U, UINT32_MAX, 0U, 180001U}) {
      mock_millis = now;
      require(!board.isUsbDataConnected() && !board.isUsbHostConnected(),
              "UART driver was falsely reported as a USB host");
      board.sleep(30);
      require(sleep_calls == 0, "fresh initialized UART console slept after idle");
      require(timer_us == 0 && critical_depth == 0,
              "UART-inhibited sleep configured timer or interrupted IRQs");
    }
    require(yield_ms > 0, "UART-inhibited sleep did not yield");
    Serial.end();
    board.sleep(30);
    require(sleep_calls == 1 && timer_us == 30000000ULL,
            "ending the raw UART driver did not restore sleep eligibility");
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n'; return 1;
  }
}
'''
        compiler = os.environ.get("CXX", "c++")
        self.assertIsNotNone(shutil.which(compiler), "a C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-raw-uart-sleep-") as directory:
            source = Path(directory) / "uart.cpp"
            binary = Path(directory) / "uart"
            source.write_text(harness.replace("@METHODS@", methods), encoding="ascii")
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                "-DARDUINO_USB_CDC_ON_BOOT=0", "-DMESH_ESP32_USB_CONSOLE_COOPERATIVE=0",
                "-DMESH_USB_LOGGING_AVAILABLE=0", "-I", str(ROOT / "src"),
                str(source), "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_host_survives_terminal_close_and_reconnect(self):
        compiler = os.environ.get("CXX", "c++")
        self.assertIsNotNone(shutil.which(compiler), "a C++17 compiler is required")
        methods = "\n".join(board_method(signature) for signature in (
            "void sleep(uint32_t secs) override",
            "bool isUsbDataConnected() override",
            "bool isUsbHostConnected() override",
            "bool isUsbSleepHeld()",
        ))
        with tempfile.TemporaryDirectory(prefix="meshcore-usb-sleep-") as temp:
            cpp = Path(temp) / "test.cpp"
            cpp.write_text(HARNESS.replace("@METHODS@", methods))
            for mode, cdc, button, logging in (
                (0, 1, 1, 1), (0, 1, 0, 1), (1, 1, 0, 1), (0, 0, 0, 1),
                (0, 1, 1, 0),
            ):
                with self.subTest(usb_mode=mode, cdc=cdc, button=button, logging=logging):
                    binary = Path(temp) / f"test-{mode}-{cdc}-{button}-{logging}"
                    compiled = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra",
                        f"-DARDUINO_USB_MODE={mode}",
                        f"-DARDUINO_USB_CDC_ON_BOOT={cdc}",
                        f"-DMESH_ESP32_USB_CONSOLE_COOPERATIVE={int(bool(cdc))}",
                        f"-DMOMENTARY_BUTTON_WAKE_FROM_SLEEP={button}",
                        f"-DMESH_USB_LOGGING_AVAILABLE={logging}", "-DPIN_USER_BTN=38",
                        "-DCONFIG_TINYUSB_ENABLED=1", "-I", str(ROOT / "src"),
                        str(cpp), "-o", str(binary),
                    ], capture_output=True, text=True)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    result = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_host_reboot_grace_without_logging(self):
        methods = "\n".join(board_method(signature) for signature in (
            "void sleep(uint32_t secs) override",
            "bool isUsbDataConnected() override",
            "bool isUsbHostConnected() override",
            "bool isUsbSleepHeld()",
        ))
        harness = HARNESS.split("int main()", 1)[0] + r'''
int main() {
  try {
#if !ARDUINO_USB_CDC_ON_BOOT
    Serial.end(); // This case exercises a UART board without a console driver.
#endif
    // Charger-only/battery startup must not create a host-reboot grace.
    ESP32Board cold;
    attachHost(false);
    Serial.terminal_open = false;
    mock_millis = 0;
    require(!cold.isUsbHostConnected(), "cold boot invented a host");
    cold.sleep(30);
    require(sleep_calls == 1, "never-seen host disabled power saving");
    mock_millis = 121000;
    cold.sleep(30);
    require(sleep_calls == 2, "charger-only boot later armed a grace");

    ESP32Board board;
    attachHost(true);
    Serial.terminal_open = true;
    mock_millis = UINT32_MAX - 60000U;
    // Only the data getter runs while the terminal is open: the role loop can
    // skip board.sleep(), but must still remember its actual USB host.
    require(board.isUsbDataConnected() == bool(ARDUINO_USB_CDC_ON_BOOT),
            "data getter changed its raw connection meaning");
    attachHost(false);
    Serial.terminal_open = false;
    require(!board.isUsbHostConnected(), "grace falsely reported a present host");
    const unsigned before = sleep_calls;
    mock_millis += 6U; // Beyond HWCDC's five-millisecond SOF loss detector.
    board.sleep(30);
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
    require(sleep_calls == before, "SOF loss allowed immediate native USB sleep");
    mock_millis += MESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS - 7U;
    require(!board.isUsbDataConnected(), "closed data getter changed under grace");
    board.sleep(30);
    require(sleep_calls == before, "native grace ended early across millis wrap");
    ++mock_millis;
    board.sleep(30);
    require(sleep_calls == before + 1, "expired native grace never restored sleep");
    // Disconnected polls must not rearm the window, even after a full millis
    // cycle appears to bring the old timestamp near again.
    mock_millis = UINT32_MAX - 59999U;
    board.sleep(30);
    require(sleep_calls == before + 2, "expired host revived after clock wrap");

    // A returning closed terminal is a real host and refreshes the grace.
    attachHost(true);
    mock_millis = 100;
    require(board.isUsbHostConnected(), "returning host was not observed");
    attachHost(false);
    mock_millis += MESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS - 1U;
    board.sleep(30);
    require(sleep_calls == before + 2, "reconnect did not renew native grace");
    ++mock_millis;
    board.sleep(30);
    require(sleep_calls == before + 3, "renewed native grace never expired");

    // Observation must also continue while inhibit_sleep short-circuits the
    // old expression, or a long OTA can miss its last connected host entirely.
    ESP32Board inhibited;
    inhibited.inhibit_sleep = true;
    attachHost(true);
    mock_millis = 200000;
    inhibited.sleep(30);
    inhibited.inhibit_sleep = false;
    attachHost(false);
    mock_millis += 6U;
    const unsigned after = sleep_calls;
    inhibited.sleep(30);
    require(sleep_calls == after, "OTA inhibitor hid the host observation");
#else
    require(sleep_calls == before + 1, "non-native board gained a USB grace");
#endif
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
'''
        self.assertIn("mesh::UsbHostSleepPolicy usb_host_sleep_policy;",
                      (ROOT / "src/helpers/ESP32Board.h").read_text())
        with tempfile.TemporaryDirectory(prefix="meshcore-usb-reboot-") as temp:
            cpp = Path(temp) / "reboot.cpp"
            cpp.write_text(harness.replace("@METHODS@", methods))
            for mode, cdc, grace in ((0, 1, 120000), (1, 1, 120000),
                                     (1, 1, 1000), (1, 0, 120000)):
                with self.subTest(usb_mode=mode, cdc=cdc, grace=grace):
                    binary = Path(temp) / f"reboot-{mode}-{cdc}-{grace}"
                    compiled = subprocess.run([
                        os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra",
                        f"-DARDUINO_USB_MODE={mode}", f"-DARDUINO_USB_CDC_ON_BOOT={cdc}",
                        f"-DMESH_ESP32_USB_CONSOLE_COOPERATIVE={int(bool(cdc))}",
                        f"-DMESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS={grace}U",
                        "-DMOMENTARY_BUTTON_WAKE_FROM_SLEEP=0", "-DMESH_USB_LOGGING_AVAILABLE=0",
                        "-DCONFIG_TINYUSB_ENABLED=1", "-I", str(ROOT / "src"),
                        str(cpp), "-o", str(binary),
                    ], capture_output=True, text=True)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    result = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_g3_enables_gesture_polling_after_gpio_wake(self):
        profile = (ROOT / "variants/station_g3_esp32/platformio.ini").read_text()
        def section(name):
            header = f"\n[{name}]\n"
            self.assertIn(header, profile)
            return profile.split(header, 1)[1].split("\n[", 1)[0]

        common = section("Station_G3_ESP32_common")
        self.assertIn("-D MOMENTARY_BUTTON_WAKE_FROM_SLEEP=1", common)
        self.assertIn("-D MOMENTARY_BUTTON_WAKE_HOLD_MS=120000UL", common)
        for slot in ("Station_G3_ESP32", "Station_G3_ESP32_r2"):
            selected = section(slot)
            self.assertIn("extends = Station_G3_ESP32_common", selected)
            self.assertIn("${Station_G3_ESP32_common.build_flags}", selected)
        for role in ("simple_repeater", "simple_room_server", "companion_radio"):
            main = (ROOT / f"examples/{role}/main.cpp").read_text()
            guard = main.split("#if defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP)", 1)[1]
            guard = guard.split("#endif", 1)[0]
            self.assertIn("!user_btn.needsPolling()", guard)

    def test_button_holds_awake_for_two_minutes_and_renews_across_rollover(self):
        harness = r'''
#include <Arduino.h>
#include <helpers/ui/MomentaryButton.h>
#include <stdexcept>
#include <iostream>
static void require(bool ok, const char* why) {
  if (!ok) throw std::runtime_error(why);
}
static uint32_t click(MomentaryButton& button, uint32_t at) {
  g_mock_millis = at;
  g_mock_pin_levels[38] = LOW;
  button.check();
  require(button.isWakeHoldActive(), "raw press did not start wake interval");
  g_mock_millis += 25;
  button.check();
  const uint32_t last_pressed = g_mock_millis;
  g_mock_millis += 50;
  g_mock_pin_levels[38] = HIGH;
  button.check();
  g_mock_millis += 25;
  button.check();
  g_mock_millis += 280;
  require(button.check() == BUTTON_EVENT_CLICK, "wake hold swallowed the click");
  require(button.needsPolling(), "main loop could sleep after button release");
  return last_pressed;
}
int main() {
  try {
    for (uint32_t start : {0U, 120001U, UINT32_MAX - 60000U}) {
      resetArduinoMock();
      g_mock_pin_levels[38] = HIGH;
      MomentaryButton button(38, 1000, true);
      button.begin();
      button.check();
      require(!button.isWakeHoldActive(), "boot without a press started a hold");
      uint32_t last = click(button, start);
      g_mock_millis = last + 119999U;
      button.check();
      require(button.isWakeHoldActive() && button.needsPolling(),
              "button wake ended before two minutes");
      last = click(button, g_mock_millis);
      g_mock_millis = last + 119999U;
      button.check();
      require(button.needsPolling(), "another press did not renew two minutes");
      ++g_mock_millis;
      button.check();
      require(!button.isWakeHoldActive() && !button.needsPolling(),
              "expired wake interval never allowed sleep again");
    }
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
'''
        with tempfile.TemporaryDirectory(prefix="meshcore-g3-button-") as temp:
            cpp = Path(temp) / "button.cpp"
            binary = Path(temp) / "button"
            cpp.write_text(harness)
            compiled = subprocess.run([
                os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra",
                "-DMOMENTARY_BUTTON_WAKE_HOLD_MS=120000UL",
                "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                str(cpp), str(ROOT / "src/helpers/ui/MomentaryButton.cpp"),
                "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
