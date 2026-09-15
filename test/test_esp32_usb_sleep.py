#!/usr/bin/env python3
"""Run ESP32Board's real sleep/USB methods against host peripheral stubs."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cstdint>
#include <stdexcept>
#include <iostream>
#include <array>

static void require(bool ok, const char* message) {
  if (!ok) throw std::runtime_error(message);
}
struct SerialPort {
  bool terminal_open = false;
  bool host_attached = false;
  explicit operator bool() const { return terminal_open; }
  bool isPlugged() const { return host_attached; }
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
    // A UART build has no native host detection. Keep its existing behavior.
    attachHost(true);
    Serial.terminal_open = true;
    require(!board.isUsbHostConnected(), "UART build invented a USB host");
#endif

    // USB power without an enumerated host must still permit battery saving.
    attachHost(false);
    Serial.terminal_open = false;
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


class Esp32UsbSleepTest(unittest.TestCase):
    def test_native_host_survives_terminal_close_and_reconnect(self):
        compiler = os.environ.get("CXX", "c++")
        self.assertIsNotNone(shutil.which(compiler), "a C++17 compiler is required")
        methods = "\n".join(board_method(signature) for signature in (
            "void sleep(uint32_t secs) override",
            "bool isUsbDataConnected() override",
            "bool isUsbHostConnected() override",
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
                        f"-DMOMENTARY_BUTTON_WAKE_FROM_SLEEP={button}",
                        f"-DMESH_USB_LOGGING_AVAILABLE={logging}", "-DPIN_USER_BTN=38",
                        "-DCONFIG_TINYUSB_ENABLED=1", str(cpp), "-o", str(binary),
                    ], capture_output=True, text=True)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    result = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_g3_enables_gesture_polling_after_gpio_wake(self):
        profile = (ROOT / "variants/station_g3_esp32/platformio.ini").read_text()
        base = profile.split("[Station_G3_ESP32]", 1)[1].split("\n[", 1)[0]
        self.assertIn("-D MOMENTARY_BUTTON_WAKE_FROM_SLEEP=1", base)
        self.assertIn("-D MOMENTARY_BUTTON_WAKE_HOLD_MS=120000UL", base)
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
