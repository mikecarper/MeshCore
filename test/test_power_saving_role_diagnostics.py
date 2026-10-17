#!/usr/bin/env python3
"""Execute real sleep guards and nRF52 wait calls with diagnostics on and off."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class PowerSavingRoleDiagnosticsTests(unittest.TestCase):
    def execute(self, code, defines=()):
        with tempfile.TemporaryDirectory(prefix="meshcore-power-role-") as directory:
            source = Path(directory) / "test.cpp"
            binary = Path(directory) / "test"
            source.write_text(code, encoding="ascii")
            result = subprocess.run([
                os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                "-fno-pie", "-no-pie", "-I", str(ROOT / "src"),
                *["-D" + value for value in defines], str(source), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_radio_default_and_public_dispatcher_deadline_forwarder(self):
        header = (ROOT / 'src/Dispatcher.h').read_text(encoding='ascii')
        source = (ROOT / 'src/Dispatcher.cpp').read_text(encoding='ascii')
        conservative = extract_braced(header, 'virtual bool getNoiseFloorWakeDelay(')
        forwarder = extract_braced(source, 'bool Dispatcher::getNextNoiseFloorWakeDelay(')
        self.execute(r'''
#include <cassert>
#include <cstdint>
struct Radio {
  bool calibrating = false;
  bool isCalibratingNoiseFloor() const { return calibrating; }
@DEFAULT@
};
struct SpacedRadio : Radio {
  mutable unsigned reads = 0;
  uint32_t delay = 50;
  bool getNoiseFloorWakeDelay(uint32_t now, uint32_t& out) const override {
    assert(now == 123); ++reads; out = delay; return true;
  }
};
struct Clock {
  unsigned reads = 0;
  uint32_t getMillis() { ++reads; return 123; }
};
struct Dispatcher {
  bool radio_available = true;
  Radio* _radio;
  Clock* _ms;
  bool getNextNoiseFloorWakeDelay(uint32_t&) const;
};
@FORWARDER@
int main() {
  Radio legacy;
  Clock clock;
  Dispatcher node{true, &legacy, &clock};
  uint32_t delay = 71;
  assert(!node.getNextNoiseFloorWakeDelay(delay) && delay == 71);
  legacy.calibrating = true;
  assert(node.getNextNoiseFloorWakeDelay(delay) && delay == 0);
  SpacedRadio spaced;
  node._radio = &spaced;
  assert(node.getNextNoiseFloorWakeDelay(delay) && delay == 50 && spaced.reads == 1);
  node.radio_available = false;
  const auto before = clock.reads;
  assert(!node.getNextNoiseFloorWakeDelay(delay));
  assert(clock.reads == before && spaced.reads == 1);
}
'''.replace('@DEFAULT@', conservative).replace('@FORWARDER@', forwarder))

    def test_actual_role_guards_preserve_short_circuit_and_ignore_charger_only(self):
        for role in ("simple_repeater", "simple_room_server", "simple_sensor"):
            with self.subTest(role=role):
                source = (ROOT / f"examples/{role}/main.cpp").read_text(encoding="ascii")
                start = source.index("  const auto sleep_guard =")
                end = source.index("  if (can_power_save)", start)
                guard = source[start:end]
                sensor = role == "simple_sensor"
                code = r'''
#include <cassert>
#include <helpers/PowerSavingDiagnostics.h>
static uint32_t millis() { return 100; }
static bool morse_timing = false, logging_armed = false;
struct Prefs { bool powersaving_enabled = true; };
struct Mesh {
  Prefs prefs; bool tracker = false;
  Prefs* getNodePrefs() { return &prefs; }
  bool isTrackerModeEnabled() const { return tracker; }
} the_mesh;
struct Board {
  bool usb = false, charger = true;
  unsigned usb_reads = 0, charger_reads = 0;
  bool isUsbDataConnected() { ++usb_reads; return usb; }
  bool isExternalPowered() { ++charger_reads; return charger; }
} board;
namespace mesh {
bool isUsbLoggingWatchdogArmed() { return logging_armed; }
namespace wireless {
struct Control { bool busy = false; unsigned reads = 0;
  bool pending() { ++reads; return busy; }
};
Control& control() { static Control value; return value; }
}}
static bool probe() {
@GUARD@
  return can_power_save;
}
int main() {
  using namespace mesh::powerdiag;
  for (unsigned flags = 0; flags != 64; ++flags) {
    the_mesh.prefs.powersaving_enabled = !(flags & 1);
    the_mesh.tracker = flags & 2;
    morse_timing = flags & 4;
    board.usb = flags & 8;
    logging_armed = flags & 16;
    mesh::wireless::control().busy = flags & 32;
    const bool policy = the_mesh.prefs.powersaving_enabled || (@SENSOR@ && the_mesh.tracker);
    const bool expected = policy && !morse_timing && !board.usb && !logging_armed
        && (@SENSOR@ || !mesh::wireless::control().busy);
    unsigned off_usb = 0, off_wireless = 0;
    for (bool diagnostics : {false, true}) {
      setEnabled(diagnostics, millis()); reset(millis());
      board.usb_reads = board.charger_reads = mesh::wireless::control().reads = 0;
      assert(probe() == expected);
      assert(board.charger_reads == 0); // USB power alone is not a data session.
      if (!diagnostics) {
        off_usb = board.usb_reads;
        off_wireless = mesh::wireless::control().reads;
      } else {
        assert(board.usb_reads == off_usb);
        assert(mesh::wireless::control().reads == off_wireless);
        if (!expected) {
          const Reason reason = !policy ? Reason::PolicyOff
              : morse_timing ? Reason::Morse : board.usb ? Reason::UsbSession
              : logging_armed ? Reason::UsbRecovery : Reason::Wireless;
          assert(snapshot(millis()).current == mask(reason));
        }
      }
    }
  }
}
'''.replace("@GUARD@", guard).replace("@SENSOR@", "true" if sensor else "false")
                code = code.replace("#include <cassert>", "#include <cassert>\n#include <initializer_list>")
                self.execute(code)

    def test_repeater_actual_pending_reasons_keep_suspended_rs232_sleepable(self):
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="ascii")
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "bool MyMesh::hasPendingWork() const",
            "mesh::powerdiag::Mask MyMesh::pendingWorkReason() const",
            "uint32_t MyMesh::getPowerSaveSleepMilliseconds(uint32_t max_ms) const",
            "uint32_t MyMesh::getPowerSaveSleepSeconds(uint32_t max_secs) const",
        ))
        code = r'''
#include <cassert>
#include <initializer_list>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/PowerManagementUtils.h>
static uint32_t millis() { return 100; }
#define WITH_BRIDGE 1
#define WITH_RS232_BRIDGE 1
#define ETHERNET_ENABLED 1
struct AbstractBridge {
  bool running = false; uint32_t wake_ms = 60000;
  bool isRunning() const { return running; }
  uint32_t powerWakeDelayMs(uint32_t) const { return wake_ms; }
};
struct MyMesh {
  bool dual = false, profile_idle = false, ota = false, queue = false, retry = false, scheduled = false;
  bool bridge_enabled = true;
  bool queue_delay_set = false, retry_delay_set = false;
  uint32_t queue_delay = 0, retry_delay = 0, calibration_delay = 0;
  struct { bool busy_value = false; bool busy() const { return busy_value; } } _local_cli_output;
  struct { bool pending = false; } deferred_cli_command;
  struct { bool gpio = false; bool hasActiveUserGpioTimer() const { return gpio; } } _cli;
  struct { bool watchdog = false, calibration = false;
    bool isWatchdogObserving() const { return watchdog; }
    bool isCalibratingNoiseFloor() const { return calibration; }
  } radio_driver;
  bool pending_self_advert = false;
  struct { bool battery_alert_enabled = false; } _prefs;
  unsigned long next_flood_advert = 0, next_local_advert = 0, dirty_contacts_expiry = 0;
  unsigned long next_recent_repeater_sweep = 0, next_battery_alert_check = 0;
  AbstractBridge uart; AbstractBridge* bridge = &uart;
  uint8_t active_rs232_bridge_uart = 0;
  const AbstractBridge* activeBridge() const { return bridge; }
  bool rs232BridgeEnabled() const { return bridge_enabled; }
  bool isDualRadioActive() const { return dual; }
  bool canIdleDualRadio() const { return !dual || profile_idle; }
  bool hasPendingOtaApply() const { return ota; }
  bool hasQueuedWorkDue() const { return queue; }
  bool hasRetryWorkDue() const { return retry; }
  bool hasScheduledRadioWorkDue() const { return scheduled; }
  bool isMillisTimerDue(unsigned long value) const { return value && value <= millis(); }
  bool getNextQueueWakeDelay(uint32_t& value) const { value = queue_delay; return queue_delay_set; }
  bool getNextRetryWakeDelay(uint32_t& value) const { value = retry_delay; return retry_delay_set; }
  bool getNextNoiseFloorWakeDelay(uint32_t& value) const {
    value = calibration_delay; return radio_driver.calibration;
  }
  uint32_t limitSleepToMillisTimer(unsigned long value, uint32_t seconds) const {
    return value && value <= millis() ? 0 : seconds;
  }
  uint32_t limitSleepToScheduledRadioWork(uint32_t value) const { return value; }
  bool hasPendingWork() const;
  mesh::powerdiag::Mask pendingWorkReason() const;
  uint32_t getPowerSaveSleepMilliseconds(uint32_t max_ms) const;
  uint32_t getPowerSaveSleepSeconds(uint32_t max_secs) const;
};
@METHODS@
int main() {
  using namespace mesh::powerdiag;
  for (bool diagnostics : {false, true}) {
    setEnabled(diagnostics, millis()); reset(millis());
    MyMesh node;
    assert(node.bridge_enabled && !node.uart.running);
    assert(!node.hasPendingWork()); // Configured but suspended is not active UART ownership.
    assert(node.getPowerSaveSleepSeconds(30) == 30);
    auto check = [&](bool& flag, Reason why) {
      flag = true;
      assert(node.hasPendingWork());
      assert(node.pendingWorkReason() == mask(why));
      assert(node.getPowerSaveSleepSeconds(30) == 0);
      if (diagnostics) assert(snapshot(millis()).current == mask(why));
      flag = false;
    };
    check(node.dual, Reason::DualRadio); check(node.ota, Reason::Ota);
    node.dual = true; node.profile_idle = true;
    assert(!node.hasPendingWork());
    check(node.ota, Reason::Ota); // An idle scanner does not bypass other role guards.
    check(node.queue, Reason::Queue); check(node.retry, Reason::Retry);
    node.dual = node.profile_idle = false;
    check(node._local_cli_output.busy_value, Reason::Serial);
    check(node.deferred_cli_command.pending, Reason::Scheduled);
    check(node.uart.running, Reason::Bridge);
    node.active_rs232_bridge_uart = 1;
    assert(node.pendingWorkReason() == mask(Reason::BridgeCleanup));
    node.active_rs232_bridge_uart = 0;
    node.uart.wake_ms = 0;
    assert(node.pendingWorkReason() == mask(Reason::BridgeWake));
    node.uart.wake_ms = 500;
    assert(!node.hasPendingWork());
    reset(millis());
    assert(node.getPowerSaveSleepMilliseconds(30000) == 500);
    if (diagnostics) assert(snapshot(millis()).current == 0);
    node.uart.wake_ms = 60000;
    check(node.radio_driver.watchdog, Reason::RadioWatchdog);
    check(node.radio_driver.calibration, Reason::Calibration);
    node.radio_driver.calibration = true; node.calibration_delay = 50;
    assert(!node.hasPendingWork() && node.getPowerSaveSleepSeconds(30) == 30);
    node.calibration_delay = 0;
    assert(node.pendingWorkReason() == mask(Reason::Calibration));
    node.radio_driver.calibration = false;
    check(node.queue, Reason::Queue); check(node.retry, Reason::Retry);
    check(node.scheduled, Reason::Scheduled);
    node.queue_delay_set = true; node.queue_delay = 500;
    reset(millis());
    assert(!node.hasPendingWork() && node.getPowerSaveSleepMilliseconds(30000) == 500);
    if (diagnostics) assert(snapshot(millis()).current == 0);
    node.queue_delay = 0;
    assert(node.getPowerSaveSleepMilliseconds(30000) == 0);
    if (diagnostics) assert(snapshot(millis()).current == mask(Reason::Queue));
    node.queue_delay_set = false; node.retry_delay_set = true; node.retry_delay = 500;
    reset(millis());
    assert(node.getPowerSaveSleepMilliseconds(30000) == 500);
    if (diagnostics) assert(snapshot(millis()).current == 0);
    node.retry_delay = 0;
    assert(node.getPowerSaveSleepMilliseconds(30000) == 0);
    if (diagnostics) assert(snapshot(millis()).current == mask(Reason::Retry));
    node.retry_delay_set = false; node.dirty_contacts_expiry = millis();
    assert(node.pendingWorkReason() == mask(Reason::Scheduled));
  }
}
'''.replace("@METHODS@", methods)
        self.execute(code)

    def test_nrf52_real_wait_sequence_veto_and_sdk_errors_are_observational(self):
        source = (ROOT / "src/helpers/NRF52Board.cpp").read_text(encoding="ascii")
        method = extract_braced(source, "void NRF52Board::sleep(uint32_t secs)")
        code = r'''
#include <cassert>
#include <cstring>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/nrf52/SleepCycleState.h>
static uint32_t now_us = 1000, wait_duration = 547, sdk_error = 0;
static bool softdevice = true;
static char sequence[32] = {}; static unsigned steps = 0, yields = 0;
static uint32_t micros() { return now_us; }
static uint32_t millis() { return now_us / 1000; }
static void delay(unsigned value) { yields += value; }
constexpr uint32_t NRF_SUCCESS = 0;
namespace mesh_nrf52 {
void softdeviceIsEnabled(uint8_t& value) { value = softdevice; }
}
static uint32_t sd_app_evt_wait() { sequence[steps++] = 'A'; now_us += wait_duration; return sdk_error; }
static void __WFE() { sequence[steps++] = 'W'; now_us += 20; }
static void __SEV() { sequence[steps++] = 'S'; }
struct NRF52Board {
  bool test = false;
  bool isRadioTestActive() const { return test; }
  void sleep(uint32_t secs);
};
@METHOD@
static void prepare() {
  auto& state = mesh_nrf52::sleepCycleState();
  const uint32_t token = state.begin();
  assert(state.publish(token, softdevice, 0));
}
int main() {
  using namespace mesh::powerdiag;
  NRF52Board board;
  prepare();
  board.sleep(0);
  assert(steps == 1 && sequence[0] == 'A');
  assert(snapshot(millis()).calls == 0);
  setEnabled(true, millis()); reset(millis());
  prepare();
  board.sleep(0);
  auto value = snapshot(millis());
  assert(value.calls == 1 && value.returns == 1 && value.last_call_us == 547);
  assert(value.errors == 0 && value.board_vetoes == 0);
  board.test = true;
  prepare();
  board.sleep(0);
  value = snapshot(millis());
  assert(steps == 2 && yields == 1);
  assert(value.calls == 1 && value.board_vetoes == 1 && value.detail == 2);
  assert(value.current & mask(Reason::Board));
  board.test = false; sdk_error = 17;
  prepare();
  board.sleep(0);
  value = snapshot(millis());
  assert(value.errors == 1 && value.detail == 17 && value.calls == 2 && value.returns == 2);
  softdevice = false; steps = 0; memset(sequence, 0, sizeof(sequence));
  prepare();
  board.sleep(0);
  assert(!strcmp(sequence, "WSW")); // Do not erase a radio GPIO edge with SEV/WFE/WFE.
  value = snapshot(millis());
  assert(value.calls == 3 && value.returns == 3 && value.last_call_us == 40);
  assert(value.short_returns == 1);
}
'''.replace("@METHOD@", method)
        self.execute(code)

    def test_esp32_real_veto_and_sdk_failure_timing_does_not_invent_blocked_residency(self):
        source = (ROOT / "src/helpers/ESP32Board.h").read_text(encoding="ascii")
        method = "\n".join(extract_braced(source, signature) for signature in (
            "void sleep(uint32_t secs) override",
            "void sleepMilliseconds(uint32_t millisecs) override",
            "void sleepMicroseconds(uint64_t wake_us, bool require_timer = false)",
        ))
        code = r'''
#include <cassert>
#include <helpers/PowerSavingDiagnostics.h>
#define ARDUINO_USB_CDC_ON_BOOT 1
#define MESH_USB_LOGGING_AVAILABLE 1
using esp_err_t = int;
using gpio_num_t = int;
constexpr int ESP_OK = 0, HIGH = 1, LOW = 0;
constexpr int USER_BTN_PRESSED = LOW;
constexpr int GPIO_INTR_HIGH_LEVEL = 1, GPIO_INTR_LOW_LEVEL = 2;
constexpr int GPIO_INTR_POSEDGE = 3, GPIO_INTR_DISABLE = 0;
static uint32_t now_us = 1000, sleep_duration = 7000;
static int timer_error = 0, gpio_error = 0, pin_error = 0, sleep_error = 0;
static unsigned sdk_calls = 0, yields = 0, critical_depth = 0, irq_reads = 0;
static bool radio_pending = false;
static uint32_t millis() { return now_us / 1000; }
static uint32_t micros() { return now_us; }
static void delay(unsigned ms) { yields += ms; }
static uint64_t timer_us = 0;
static esp_err_t esp_sleep_enable_timer_wakeup(uint64_t value) { timer_us = value; return timer_error; }
static esp_err_t esp_sleep_enable_gpio_wakeup() { return gpio_error; }
static esp_err_t gpio_wakeup_enable(gpio_num_t, int) { return pin_error; }
static void gpio_wakeup_disable(gpio_num_t) {}
static void gpio_set_intr_type(gpio_num_t, int) {}
static int gpio_get_level(gpio_num_t) { ++irq_reads; return radio_pending ? HIGH : LOW; }
static esp_err_t esp_light_sleep_start() { ++sdk_calls; now_us += sleep_duration; return sleep_error; }
#define portENTER_CRITICAL(mux) (++critical_depth)
#define portEXIT_CRITICAL(mux) (--critical_depth)
namespace mesh {
static bool logging = false;
static bool isUsbLoggingEnabled() { return logging; }
}
struct MainBoard {
  virtual void sleep(uint32_t) = 0;
  virtual void sleepMilliseconds(uint32_t) = 0;
};
struct ESP32Board : MainBoard {
  bool inhibit_sleep = false, usb = false, test = false;
  uint32_t irq = 14;
  bool isUsbSleepHeld() { return usb; }
  bool isRadioTestActive() const { return test; }
  uint32_t getIRQGpio() const { return irq; }
@METHOD@
};
int main() {
  using namespace mesh::powerdiag;
  ESP32Board board;
  board.sleep(30);
  assert(sdk_calls == 1 && snapshot(millis()).calls == 0);
  assert(timer_us == 30000000ULL);
  board.sleepMilliseconds(50);
  assert(sdk_calls == 2 && timer_us == 50000ULL);
  board.sleepMilliseconds(0);
  assert(sdk_calls == 2 && timer_us == 50000ULL);
  setEnabled(true, millis()); reset(millis()); decision(0, millis());
  timer_error = 29; gpio_error = 31; pin_error = 33;
  board.sleep(30); // Diagnostics may observe failures, never repair/change policy.
  auto value = snapshot(millis());
  assert(sdk_calls == 3 && value.calls == 1 && value.returns == 1 && value.errors == 1);
  assert(value.detail == 29); // First setup failure, without changing later SDK calls.
  assert(value.last_call_us == 7000 && value.eligible_ms == 7);
  assert(value.blocked_ms[static_cast<size_t>(Reason::Board)] == 0);
  assert(critical_depth == 0);
  reset(millis()); decision(0, millis());
  const auto before_failed_slice = sdk_calls;
  board.sleepMilliseconds(50);
  value = snapshot(millis());
  assert(sdk_calls == before_failed_slice && value.calls == 0);
  assert(value.errors == 1 && value.detail == 29 && critical_depth == 0);
  reset(millis()); decision(0, millis());
  timer_error = gpio_error = pin_error = 0; sleep_error = 41;
  board.sleep(30);
  value = snapshot(millis());
  assert(value.calls == 1 && value.returns == 1 && value.errors == 1 && value.detail == 41);
  assert(value.eligible_ms == 7 && value.union_blocked_ms == 0);
  sleep_error = 0;
  auto veto = [&](Reason why, int detail) {
    reset(millis()); decision(0, millis());
    const unsigned before = sdk_calls;
    board.sleep(30);
    const auto got = snapshot(millis());
    assert(sdk_calls == before && got.calls == 0 && got.board_vetoes == 1);
    assert(got.current == mask(why) && got.detail == detail && critical_depth == 0);
  };
  board.inhibit_sleep = true; veto(Reason::Board, 1); board.inhibit_sleep = false;
  board.test = true; veto(Reason::Board, 2); board.test = false;
  board.usb = true; veto(Reason::UsbSession, 1); board.usb = false;
  mesh::logging = true; veto(Reason::UsbLogging, 0); mesh::logging = false;
  radio_pending = true; veto(Reason::Mesh, 14); radio_pending = false;
  reset(millis()); board.irq = static_cast<uint32_t>(-1);
  const unsigned before = sdk_calls;
  board.sleep(0);
  value = snapshot(millis());
  assert(sdk_calls == before && value.board_vetoes == 1 && value.current == mask(Reason::NoWake));
  assert(yields == 5 && irq_reads > 0);
}
'''.replace("@METHOD@", method)
        self.execute(code)


if __name__ == "__main__":
    unittest.main()
