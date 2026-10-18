#!/usr/bin/env python3
"""Exercise the optional RAK4631 radio probe on the host, without PIO or RF."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/rak3401"


def numeric_define(source, name):
    match = re.search(rf"^\s*#define\s+{name}\s+\(?\s*(\d+)\s*\)?", source, re.MULTILINE)
    if match is None:
        raise AssertionError(f"missing numeric definition for {name}")
    return int(match.group(1))


def compile_source(testcase, source, *, expect_success=True):
    compiler = shutil.which("c++") or shutil.which("g++")
    testcase.assertIsNotNone(compiler, "this host-only suite requires a C++ compiler")
    with tempfile.TemporaryDirectory(prefix="rak3401-onboard-radio-") as folder:
        work = Path(folder)
        generated = work / "case.cpp"
        generated.write_text(source, encoding="ascii")
        binary = work / ("case.exe" if os.name == "nt" else "case")
        command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                   "-I", str(ROOT), str(generated), "-o", str(binary)]
        if sys.platform.startswith("linux"):
            command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        built = subprocess.run(command, capture_output=True, text=True, timeout=60)
        testcase.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        if expect_success:
            testcase.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        else:
            testcase.assertNotEqual(ran.returncode, 0, "negative control did not fail")


def onboard_defines():
    variant = (VARIANT / "variant.h").read_text()
    return "\n".join(f"#define ONBOARD_LORA_{name} {numeric_define(variant, 'ONBOARD_LORA_' + name)}"
                     for name in ("NSS", "SCLK", "MOSI", "MISO", "BUSY", "RESET", "ANT_SW"))


ADAPTER_MOCKS = r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include "variants/rak3401/OnboardRadioSleep.h"
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define INPUT_PULLDOWN 3
#define MSBFIRST 1
#define SPI_MODE0 0
#define RADIOLIB_SX126X_CMD_READ_REGISTER 0x1d
#define RADIOLIB_SX126X_REG_VERSION_STRING 0x0320
#define RADIOLIB_SX126X_CMD_SET_SLEEP 0x84
#define RADIOLIB_SX126X_SLEEP_START_COLD 0
#define RADIOLIB_SX126X_SLEEP_RTC_OFF 0
#define RADIOLIB_SX1262_CHIP_TYPE "SX1261"
template <typename... Args> void debug(const char*, Args...) {}
#define MESH_DEBUG_PRINTLN(...) debug(__VA_ARGS__)

enum class Scenario { Present, MissingBusy, WrongVersion, ZeroVersion,
                      FloatingVersion, BusyAfterRead, SleepRejected };
struct Event { std::string name; unsigned pin; int value; };
struct Hardware {
  Scenario scenario = Scenario::Present;
  uint32_t clock = 0;
  uint32_t elapsed = 0;
  std::array<int, 48> modes;
  std::array<int, 48> levels;
  std::array<unsigned, 48> defaults{};
  std::vector<Event> events;
  std::vector<std::vector<uint8_t>> frames;
  std::vector<uint8_t> frame;
  bool version_read = false;
  bool sleep_issued = false;
  bool reset_low_seen = false;
  bool transaction = false;
  Hardware() { modes.fill(-1); levels.fill(-1); }
} hw;
std::array<uint32_t, 48> pin_map() {
  std::array<uint32_t, 48> pins{};
  for (unsigned i = 0; i < pins.size(); ++i) pins[i] = i;
  return pins;
}
const std::array<uint32_t, 48> g_ADigitalPinMap = pin_map();
void event(const char* name, unsigned pin = 0, int value = 0) {
  hw.events.push_back({name, pin, value});
}
void pinMode(unsigned pin, int mode) {
  hw.modes.at(pin) = mode;
  event("mode", pin, mode);
}
void digitalWrite(unsigned pin, int value) {
  if (pin == ONBOARD_LORA_RESET && value == LOW) {
    assert(hw.modes.at(pin) == OUTPUT && hw.levels.at(pin) == HIGH);
    hw.reset_low_seen = true;
  }
  if (pin == ONBOARD_LORA_NSS && value == LOW) {
    assert(hw.transaction && !hw.sleep_issued);
  }
  if (pin == ONBOARD_LORA_NSS && value == HIGH && hw.transaction &&
      !hw.frame.empty() && hw.frame.front() == RADIOLIB_SX126X_CMD_SET_SLEEP) {
    assert((hw.frame == std::vector<uint8_t>{RADIOLIB_SX126X_CMD_SET_SLEEP, 0}));
    assert(hw.modes.at(ONBOARD_LORA_BUSY) == INPUT);
    assert(hw.modes.at(ONBOARD_LORA_MISO) == INPUT);
    hw.sleep_issued = true;
  }
  hw.levels.at(pin) = value;
  event("write", pin, value);
}
int digitalRead(unsigned pin) {
  assert(pin == ONBOARD_LORA_BUSY);
  assert(hw.modes.at(pin) == INPUT || hw.modes.at(pin) == INPUT_PULLUP);
  event("read", pin);
  if (hw.scenario == Scenario::MissingBusy) return HIGH;
  if (hw.sleep_issued) return hw.scenario == Scenario::SleepRejected ? LOW : HIGH;
  if (hw.version_read && hw.scenario == Scenario::BusyAfterRead) return HIGH;
  return LOW;
}
uint32_t millis() { return hw.clock; }
void delay(uint32_t interval) {
  hw.clock += interval;
  hw.elapsed += interval;
  assert(hw.elapsed <= 100); // Infinite or wrap-unsafe waits fail promptly.
  event("delay", 0, static_cast<int>(interval));
}
void nrf_gpio_cfg_default(uint32_t pin) {
  assert(pin == ONBOARD_LORA_SCLK || pin == ONBOARD_LORA_MOSI ||
         pin == ONBOARD_LORA_MISO || pin == ONBOARD_LORA_BUSY);
  hw.modes.at(pin) = 99;
  ++hw.defaults.at(pin);
  event("default", pin);
}
struct SPISettings {
  SPISettings(unsigned frequency, unsigned bit_order, unsigned mode) {
    assert(frequency == 2000000 && bit_order == MSBFIRST && mode == SPI_MODE0);
  }
};
struct SPIClass {
  std::string name;
  bool begun = false;
  bool pins_set = false;
  explicit SPIClass(const char* value) : name(value) {}
  void check_owner() const { assert(name == "SPI"); }
  void setPins(unsigned miso, unsigned sclk, unsigned mosi) {
    check_owner();
    assert(!begun && miso == ONBOARD_LORA_MISO &&
           sclk == ONBOARD_LORA_SCLK && mosi == ONBOARD_LORA_MOSI);
    pins_set = true;
    event("spi-pins");
  }
  void begin() {
    check_owner();
    assert(pins_set && !begun);
    assert(hw.levels.at(ONBOARD_LORA_NSS) == HIGH);
    assert(hw.modes.at(ONBOARD_LORA_NSS) == OUTPUT);
    assert(hw.levels.at(ONBOARD_LORA_RESET) == HIGH);
    assert(hw.modes.at(ONBOARD_LORA_RESET) == OUTPUT);
    assert(hw.levels.at(ONBOARD_LORA_ANT_SW) == LOW);
    assert(hw.modes.at(ONBOARD_LORA_ANT_SW) == OUTPUT);
    assert(hw.modes.at(ONBOARD_LORA_BUSY) == INPUT_PULLUP);
    begun = true;
    event("spi-begin");
  }
  void beginTransaction(SPISettings) {
    check_owner();
    assert(begun && !hw.transaction && !hw.sleep_issued);
    assert(hw.levels.at(ONBOARD_LORA_NSS) == HIGH);
    hw.transaction = true;
    hw.frame.clear();
    event("transaction");
  }
  uint8_t transfer(uint8_t value) {
    check_owner();
    assert(begun && hw.transaction && !hw.sleep_issued);
    assert(hw.levels.at(ONBOARD_LORA_NSS) == LOW && hw.reset_low_seen);
    hw.frame.push_back(value);
    event("transfer", 0, value);
    if (hw.frame.front() != RADIOLIB_SX126X_CMD_READ_REGISTER || hw.frame.size() < 5)
      return 0;
    assert(hw.frame.size() <= 10);
    assert(hw.modes.at(ONBOARD_LORA_MISO) == INPUT_PULLDOWN);
    const unsigned index = static_cast<unsigned>(hw.frame.size() - 5);
    if (hw.scenario == Scenario::ZeroVersion) return 0;
    if (hw.scenario == Scenario::FloatingVersion) return 0xff;
    if (hw.scenario == Scenario::WrongVersion) return static_cast<uint8_t>("SX126X"[index]);
    return static_cast<uint8_t>("SX1261"[index]);
  }
  void endTransaction() {
    check_owner();
    assert(begun && hw.transaction && hw.levels.at(ONBOARD_LORA_NSS) == HIGH);
    if (hw.frame.front() == RADIOLIB_SX126X_CMD_READ_REGISTER) {
      assert((hw.frame == std::vector<uint8_t>{0x1d, 3, 0x20, 0, 0, 0, 0, 0, 0, 0}));
      hw.version_read = true;
    }
    hw.frames.push_back(hw.frame);
    hw.transaction = false;
    event("transaction-end");
  }
  void end() {
    check_owner();
    assert(begun && !hw.transaction);
    begun = false;
    event("spi-end");
  }
} SPI("SPI"), SPI1("SPI1");
'''


ADAPTER_MAIN = r'''
size_t first(const char* name) {
  for (size_t i = 0; i < hw.events.size(); ++i)
    if (hw.events[i].name == name) return i;
  return hw.events.size();
}
int main() {
  for (uint32_t start : {0u, UINT32_MAX - 10u}) {
    for (Scenario scenario : {Scenario::Present, Scenario::MissingBusy, Scenario::WrongVersion,
         Scenario::ZeroVersion, Scenario::FloatingVersion, Scenario::BusyAfterRead,
         Scenario::SleepRejected}) {
      hw = Hardware(); hw.clock = start; hw.scenario = scenario;
      SPI.begun = SPI.pins_set = SPI1.begun = SPI1.pins_set = false;
      const auto result = rak3401::sleepOnboardRadio();
      using State = rak3401::OnboardRadioState;
      const auto expected = scenario == Scenario::Present ? State::Asleep :
          scenario == Scenario::MissingBusy || scenario == Scenario::BusyAfterRead ? State::BusyTimeout :
          scenario == Scenario::SleepRejected ? State::SleepFailed : State::Absent;
      assert(result == expected);
      assert(!SPI.begun && !SPI1.begun && !SPI1.pins_set && !hw.transaction);
      assert(first("spi-pins") < first("spi-begin"));
      assert(first("spi-begin") < first("spi-end"));
      for (unsigned pin : {ONBOARD_LORA_SCLK, ONBOARD_LORA_MOSI,
                           ONBOARD_LORA_MISO, ONBOARD_LORA_BUSY}) {
        assert(hw.modes.at(pin) == 99 && hw.defaults.at(pin) == 1);
        for (size_t i = 0; i < hw.events.size(); ++i)
          if (hw.events[i].name == "default" && hw.events[i].pin == pin)
            assert(first("spi-end") < i);
      }
      for (unsigned pin : {ONBOARD_LORA_NSS, ONBOARD_LORA_RESET}) {
        assert(hw.modes.at(pin) == OUTPUT && hw.levels.at(pin) == HIGH);
        assert(hw.defaults.at(pin) == 0);
      }
      assert(hw.modes.at(ONBOARD_LORA_ANT_SW) == OUTPUT);
      assert(hw.levels.at(ONBOARD_LORA_ANT_SW) == LOW);
      // The optional radio cannot touch shared slot power, the primary radio,
      // GPS, storage CS, or the internal radio's unused DIO1 interrupt pin.
      for (unsigned pin : {3u, 4u, 9u, 10u, 15u, 16u, 17u, 21u, 26u, 29u, 30u, 31u, 34u, 47u})
        assert(hw.modes.at(pin) == -1 && hw.levels.at(pin) == -1 && hw.defaults.at(pin) == 0);
      const bool should_sleep = scenario == Scenario::Present || scenario == Scenario::SleepRejected;
      assert(hw.sleep_issued == should_sleep);
      assert(hw.frames.size() == (scenario == Scenario::MissingBusy ? 0u : should_sleep ? 2u : 1u));
      if (scenario == Scenario::MissingBusy || scenario == Scenario::BusyAfterRead ||
          scenario == Scenario::SleepRejected) assert(hw.elapsed >= 20);
      assert(hw.elapsed <= 70);
      assert(static_cast<uint32_t>(hw.clock - start) == hw.elapsed);
    }
  }
}
'''


def adapter_source(mutation=None):
    target = (VARIANT / "target.cpp").read_text()
    adapter = extract_braced(target, "class OnboardRadioIO") + ";"
    if mutation == "probe-pulls-retained":
        adapter = adapter.replace("pinMode(ONBOARD_LORA_BUSY, INPUT);", "// negative control")
    elif mutation == "bus-left-active":
        adapter = adapter.replace("SPI.end();", "// negative control")
    elif mutation == "nss-wakes-radio":
        before, after = adapter.rsplit("digitalWrite(ONBOARD_LORA_NSS, HIGH);", 1)
        adapter = before + "digitalWrite(ONBOARD_LORA_NSS, LOW);" + after
    function = extract_braced(target, "rak3401::OnboardRadioState rak3401::sleepOnboardRadio()")
    return onboard_defines() + ADAPTER_MOCKS + adapter + "\n" + function + ADAPTER_MAIN


STARTUP_MOCKS = r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <string>
#include <vector>
#include "variants/rak3401/OnboardRadioSleep.h"
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define NRF52_POWER_MANAGEMENT 1
#define PIN_VBAT_READ 5
#define PIN_USER_BTN 33
#define PIN_USER_BTN_ANA 31
#define PIN_BOARD_SDA 13
#define PIN_BOARD_SCL 14
#define PIN_3V3_EN 34
#define SX126X_POWER_EN 21
#define P_LORA_NSS 26
#define P_LORA_BUSY 9
#define P_LORA_RESET 4
#define SHUTDOWN_REASON_LOW_VOLTAGE 0x4c
#define SHUTDOWN_REASON_BOOT_PROTECT 0x42
struct Event { std::string name; unsigned pin; int value; };
std::vector<Event> events;
std::array<int, 48> levels;
std::array<int, 48> modes;
bool permit_boot = true;
bool primary_busy = false;
uint32_t clock_ms = 0;
rak3401::OnboardRadioState radio_state;
void event(const char* name, unsigned pin = 0, int value = 0) {
  events.push_back({name, pin, value});
}
void pinMode(unsigned pin, int mode) { modes.at(pin) = mode; event("mode", pin, mode); }
void digitalWrite(unsigned pin, int value) { levels.at(pin) = value; event("write", pin, value); }
int digitalRead(unsigned pin) { assert(pin == P_LORA_BUSY); return primary_busy ? HIGH : LOW; }
uint32_t millis() { return clock_ms; }
void delay(uint32_t value) { clock_ms += value; assert(clock_ms <= 200); }
struct PowerMgtConfig { uint8_t lpcomp_ain_channel = 3, lpcomp_refsel = 4; } power_config;
struct SystemOff { uint8_t reason; };
class NRF52BoardDCDC {
public:
  void begin() { event("dcdc"); }
  virtual void initiateShutdown(uint8_t) = 0;
  void checkBootVoltage(const PowerMgtConfig*) {
    event("voltage");
    if (!permit_boot) initiateShutdown(SHUTDOWN_REASON_BOOT_PROTECT);
  }
  void configureVoltageWake(uint8_t ain, uint8_t ref) {
    assert(ain == 3 && ref == 4); event("voltage-wake");
  }
  void enterSystemOff(uint8_t reason) { event("systemoff"); throw SystemOff{reason}; }
  void serviceWatchdog() { event("watchdog"); }
};
class RAK3401Board : public NRF52BoardDCDC {
public:
  void begin();
  bool recoverRadio();
  void enableRadioFrontend();
  void initiateShutdown(uint8_t reason) override;
};
struct WireMock {
  void setPins(unsigned sda, unsigned scl) { assert(sda == 13 && scl == 14); event("wire-pins"); }
  void begin() { event("wire-begin"); }
} Wire;
rak3401::OnboardRadioState rak3401::sleepOnboardRadio() {
  event("onboard");
  return radio_state;
}
'''


STARTUP_MAIN = r'''
size_t first(const char* name) {
  for (size_t i = 0; i < events.size(); ++i)
    if (events[i].name == name) return i;
  return events.size();
}
void prepare() {
  events.clear(); modes.fill(-1); levels.fill(-1); clock_ms = 0; primary_busy = false;
  // initVariant() already holds the shared rail up and external FEM down.
  levels.at(PIN_3V3_EN) = HIGH; levels.at(SX126X_POWER_EN) = LOW;
}
int main() {
  using State = rak3401::OnboardRadioState;
  for (State state : {State::Asleep, State::Absent, State::BusyTimeout, State::SleepFailed}) {
    for (bool boot : {true, false}) {
      prepare(); radio_state = state; permit_boot = boot; RAK3401Board board;
      bool stopped = false;
      try { board.begin(); } catch (const SystemOff& off) {
        assert(off.reason == SHUTDOWN_REASON_BOOT_PROTECT); stopped = true;
      }
      assert(stopped == !boot);
      assert(first("dcdc") == 0 && first("onboard") == 1);
      assert(first("onboard") < first("voltage"));
      assert(levels.at(SX126X_POWER_EN) == LOW);
      if (boot) {
        assert(levels.at(PIN_3V3_EN) == HIGH);
        assert(first("systemoff") == events.size());
        assert(board.recoverRadio());
        assert(levels.at(SX126X_POWER_EN) == LOW);
        assert(levels.at(PIN_3V3_EN) == HIGH);
        board.enableRadioFrontend();
        assert(levels.at(SX126X_POWER_EN) == HIGH);
        // A later failed external-radio reset must keep shared peripherals up
        // and the external frontend quiescent, without reproving the optional radio.
        primary_busy = true;
        assert(!board.recoverRadio());
        assert(levels.at(PIN_3V3_EN) == HIGH && levels.at(SX126X_POWER_EN) == LOW);
        unsigned probes = 0;
        for (const auto& e : events) if (e.name == "onboard") ++probes;
        assert(probes == 1);
      } else {
        assert(first("voltage") < first("voltage-wake"));
        assert(first("voltage-wake") < first("systemoff"));
        assert(levels.at(PIN_3V3_EN) == LOW);
      }
    }
  }
}
'''


def startup_source(mutation=None):
    board = (VARIANT / "RAK3401Board.cpp").read_text()
    methods = "\n".join(extract_braced(board, signature) for signature in (
        "void RAK3401Board::begin()", "void RAK3401Board::initiateShutdown(uint8_t reason)",
        "bool RAK3401Board::recoverRadio()", "void RAK3401Board::enableRadioFrontend()"))
    if mutation == "probe-after-voltage":
        methods = methods.replace("rak3401::sleepOnboardRadio();", "// negative control")
        methods = methods.replace("checkBootVoltage(&power_config);",
                                  "checkBootVoltage(&power_config); rak3401::sleepOnboardRadio();")
    return STARTUP_MOCKS + methods + STARTUP_MAIN


class Rak3401OnboardRadioTests(unittest.TestCase):
    def test_actual_adapter_and_template_all_outcomes_and_timer_wrap(self):
        for debug_enabled in (0, 1):
            with self.subTest(debug=debug_enabled):
                compile_source(self, f"#define MESH_DEBUG {debug_enabled}\n" + adapter_source())

    def test_gpio_cleanup_and_sleep_readback_negative_controls(self):
        for mutation in ("probe-pulls-retained", "bus-left-active", "nss-wakes-radio"):
            with self.subTest(mutation=mutation):
                compile_source(self, adapter_source(mutation), expect_success=False)

    def test_real_startup_protects_low_voltage_and_preserves_external_recovery(self):
        compile_source(self, startup_source())

    def test_boot_voltage_order_negative_control(self):
        compile_source(self, startup_source("probe-after-voltage"), expect_success=False)

    def test_optional_pin_map_matches_the_rak4631_internal_radio(self):
        external_variant = (VARIANT / "variant.h").read_text()
        internal_variant = (ROOT / "variants/rak4631/variant.h").read_text()
        for optional, active in (("NSS", "P_LORA_NSS"), ("SCLK", "P_LORA_SCLK"),
                                 ("MOSI", "P_LORA_MOSI"), ("MISO", "P_LORA_MISO"),
                                 ("BUSY", "P_LORA_BUSY"), ("RESET", "P_LORA_RESET"),
                                 ("DIO_1", "P_LORA_DIO_1"),
                                 ("ANT_SW", "SX126X_POWER_EN")):
            with self.subTest(pin=optional):
                self.assertEqual(numeric_define(external_variant, "ONBOARD_LORA_" + optional),
                                 numeric_define(internal_variant, active))

    def test_standard_rak4631_keeps_its_active_radio(self):
        board = (ROOT / "variants/rak4631/RAK4631Board.cpp").read_text()
        target = (ROOT / "variants/rak4631/target.cpp").read_text()
        self.assertNotIn("sleepOnboardRadio", board + target)
        self.assertNotIn("ONBOARD_LORA_", board + target)
        begin = extract_braced(board, "void RAK4631Board::begin()")
        self.assertIn("digitalWrite(SX126X_POWER_EN, HIGH);", begin)
        init = extract_braced(target, "bool radio_init()")
        self.assertIn("radio.std_init(&SPI)", init)
        self.assertIn("P_LORA_RESET, P_LORA_BUSY, SPI)", target)
        # External-radio construction and retry must retain their SPI1 owner.
        external_target = (VARIANT / "target.cpp").read_text()
        self.assertIn("P_LORA_RESET, P_LORA_BUSY, SPI1)", external_target)
        external_init = extract_braced(external_target, "bool radio_init()")
        self.assertIn("radio.std_init(&SPI1)", external_init)
        self.assertNotIn("sleepOnboardRadio", external_init)


if __name__ == "__main__":
    unittest.main()
