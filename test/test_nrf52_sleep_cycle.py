#!/usr/bin/env python3
"""Execute the one-cycle SDK snapshot and its actual nRF52 board consumers."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class Nrf52SleepCycleTests(unittest.TestCase):
    def execute(self, code):
        with tempfile.TemporaryDirectory(prefix="meshcore-sleep-cycle-") as directory:
            source = Path(directory) / "test.cpp"
            binary = Path(directory) / "test"
            source.write_text(code, encoding="ascii")
            result = subprocess.run([
                os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra",
                "-pthread", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                "-fno-pie", "-no-pie", "-I", str(ROOT / "src"),
                str(source), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cache_one_shot_error_invalidation_and_concurrent_publication(self):
        self.execute(r'''
#include <cassert>
#include <thread>
#include <helpers/nrf52/SleepCycleState.h>
int main() {
  using namespace mesh_nrf52;
  SleepCycleState state;
  SleepCycleSnapshot out;
  assert(!state.snapshot(out) && !state.take(out) && state.lastError() == 0);
  auto token = state.begin();
  assert(!state.snapshot(out));
  assert(state.publish(token, false, 3));
  assert(state.snapshot(out) && !out.softdevice_enabled && out.usb_status == 3);
  assert(state.take(out) && !state.take(out) && !state.snapshot(out));
  token = state.begin();
  state.invalidate(); // Startup began during an authoritative SDK query.
  assert(!state.publish(token, false, 1) && !state.snapshot(out));
  token = state.begin();
  assert(state.publish(token, true, 1, 17));
  assert(!state.take(out) && state.lastError() == 17);
  state.invalidate();
  assert(state.lastError() == 0); // Do not attribute the old error to a new transition.
  token = state.begin();
  assert(state.publish(token, true, 0) && state.snapshot(out));
  const auto previous = out;
  token = state.begin();
  assert(!state.current(previous.generation) && !state.snapshot(out));
  assert(state.publish(token, false, 1) && state.snapshot(out));
  assert(!out.softdevice_enabled && out.usb_status == 1);

  std::atomic<bool> done{false};
  std::thread writer([&]() {
    for (unsigned i = 0; i < 100000; ++i) {
      const auto next = state.begin();
      state.publish(next, (next & 1) != 0, next ^ 0x75201346U);
    }
    done.store(true, std::memory_order_release);
  });
  // Seed a consistent record before accepting the publication stress cases.
  state.invalidate();
  while (!done.load(std::memory_order_acquire)) {
    if (state.snapshot(out)) {
      assert(out.usb_status == (out.generation ^ 0x75201346U));
      assert(out.softdevice_enabled == ((out.generation & 1) != 0));
    }
  }
  writer.join();
}
''')

    def test_actual_prepare_power_cache_sleep_and_partial_startup_fail_closed(self):
        source = (ROOT / "src/helpers/NRF52Board.cpp").read_text(encoding="ascii")
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "void NRF52Board::prepareSleepCycle()",
            "bool NRF52Board::isExternalPowered()",
            "void NRF52Board::sleep(uint32_t secs)",
        ))
        code = r'''
#include <cassert>
#include <initializer_list>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/nrf52/SleepCycleState.h>
constexpr uint32_t NRF_SUCCESS = 0, NRF_ERROR_INVALID_STATE = 8;
constexpr uint32_t POWER_USBREGSTATUS_VBUSDETECT_Msk = 1;
constexpr uint32_t SCB_ICSR_PENDSVSET_Msk = 1UL << 28;
constexpr int taskSCHEDULER_RUNNING = 2;
#define NRF_CRYPTOCELL 1
constexpr int CRYPTOCELL_IRQn = 42;
struct NvicState { volatile uint32_t __cr_flag = 0; } nrf_nvic_state;
static bool cc_enabled = false, cc_pending = false;
static unsigned cc_clears = 0;
static uint32_t NVIC_GetEnableIRQ(int irq) {
  assert(irq == CRYPTOCELL_IRQn); return cc_enabled;
}
static void NVIC_ClearPendingIRQ(int irq) {
  assert(irq == CRYPTOCELL_IRQn); cc_pending = false; ++cc_clears;
}
struct Scb { uint32_t ICSR = 0; } scb;
static Scb* SCB = &scb;
struct Power { uint32_t USBREGSTATUS = 0; } power;
static Power* NRF_POWER = &power;
static bool actual_sd = false, invalidate_during_query = false, event = false;
static uint32_t actual_usb = 0, sd_error = 0, usb_error = 0;
static unsigned state_queries = 0, usb_queries = 0, sd_waits = 0, bare_waits = 0, delays = 0;
static unsigned acknowledgements = 0, forced_yields = 0;
static uint32_t ipsr = 0, primask = 0, basepri = 0, faultmask = 0;
static int scheduler_state = taskSCHEDULER_RUNNING;
static bool yielded_app_irq = false, defer_pendsv = false, invalidate_during_yield = false;
static bool critical_during_yield = false;
static uint32_t acknowledgement_error = 0;
static uint32_t __get_IPSR() { return ipsr; }
static uint32_t __get_PRIMASK() { return primask; }
static uint32_t __get_BASEPRI() { return basepri; }
static uint32_t __get_FAULTMASK() { return faultmask; }
static int xTaskGetSchedulerState() { return scheduler_state; }
static void taskYIELD() {
  ++forced_yields; event = true;
  if (defer_pendsv) scb.ICSR |= SCB_ICSR_PENDSVSET_Msk;
  else yielded_app_irq = true;
  if (invalidate_during_yield) mesh_nrf52::sleepCycleState().invalidate();
  if (critical_during_yield) nrf_nvic_state.__cr_flag = 1;
}
static uint32_t now_us = 1000;
static uint32_t millis() { return now_us / 1000; }
static uint32_t micros() { return now_us; }
static void delay(unsigned ms) { delays += ms; }
namespace mesh_nrf52 {
uint32_t softdeviceIsEnabled(uint8_t& out) {
  ++state_queries; out = actual_sd; event = true; // Model the SVC-caused event.
  if (invalidate_during_query) sleepCycleState().invalidate();
  return sd_error;
}}
static uint32_t sd_power_usbregstatus_get(uint32_t* out) {
  ++usb_queries; *out = actual_usb; event = true; return usb_error;
}
static uint32_t sd_app_evt_wait() {
  if (yielded_app_irq) {
    yielded_app_irq = false; ++acknowledgements;
    return acknowledgement_error;
  }
  ++sd_waits; now_us += 300; return 0;
}
static void __SEV() { event = true; }
static void __WFE() {
  if (event) event = false;
  else { ++bare_waits; now_us += 500; }
}
struct NRF52Board {
  bool radio_test = false;
  bool isRadioTestActive() const { return radio_test; }
  void prepareSleepCycle();
  bool isExternalPowered();
  void sleep(uint32_t secs);
};
@METHODS@
int main() {
  using namespace mesh::powerdiag;
  NRF52Board board;
  board.sleep(0); assert(state_queries == 0 && sd_waits == 0 && bare_waits == 0);
  power.USBREGSTATUS = 1;
  board.prepareSleepCycle();
  assert(state_queries == 1 && usb_queries == 0 && !event);
  assert(forced_yields == 0 && acknowledgements == 0);
  for (int i = 0; i < 20; ++i) assert(board.isExternalPowered());
  assert(state_queries == 1 && !event); // UI power polling must not create late SVC events.
  power.USBREGSTATUS = 0;
  assert(board.isExternalPowered()); // Stable only within this prepared cycle.
  board.prepareSleepCycle();
  assert(!board.isExternalPowered() && state_queries == 2);
  board.sleep(0);
  assert(bare_waits == 1 && sd_waits == 0 && state_queries == 2);
  board.sleep(0); assert(bare_waits == 1 && state_queries == 2); // One-shot, not a global SD cache.

  // Startup can partially enable SD before failing. Invalidation is BEFORE
  // the attempt, not conditional on a successful Bluefruit return value.
  board.prepareSleepCycle();
  mesh_nrf52::sleepCycleState().invalidate(); actual_sd = true; actual_usb = 1;
  assert(board.isExternalPowered()); // Stale SD-off cycle cannot bypass the SDK.
  assert(usb_queries == 1);
  const auto before = state_queries;
  board.sleep(0); assert(state_queries == before && sd_waits == 0);
  power.USBREGSTATUS = 0;
  board.prepareSleepCycle();
  assert(!event && board.isExternalPowered() && usb_queries == 2);
  assert(forced_yields == 1 && acknowledgements == 1);
  board.sleep(0); assert(sd_waits == 1 && bare_waits == 1);

  setEnabled(true, millis()); reset(millis());
  sd_error = 17;
  board.prepareSleepCycle(); board.sleep(0);
  auto value = snapshot(millis());
  assert(value.errors == 1 && value.detail == 17 && value.calls == 0);
  assert(sd_waits == 1 && bare_waits == 1);
  sd_error = 0; usb_error = 23; reset(millis());
  board.prepareSleepCycle(); board.sleep(0);
  value = snapshot(millis());
  assert(value.errors == 1 && value.detail == 23 && value.calls == 0);
  usb_error = 0; invalidate_during_query = true; reset(millis());
  board.prepareSleepCycle(); board.sleep(0);
  value = snapshot(millis());
  assert(value.board_vetoes == 1 && value.detail == 3 && value.calls == 0);
  invalidate_during_query = false;
  for (uint32_t* mask : {&ipsr, &primask, &basepri, &faultmask}) {
    *mask = 1; reset(millis());
    const auto before_query = state_queries, before_ack = acknowledgements;
    board.prepareSleepCycle(); board.sleep(0);
    value = snapshot(millis());
    assert(value.errors == 1 && value.detail == NRF_ERROR_INVALID_STATE);
    assert(state_queries == before_query && acknowledgements == before_ack);
    *mask = 0;
  }
  scheduler_state = 0; reset(millis());
  board.prepareSleepCycle(); board.sleep(0);
  assert(snapshot(millis()).detail == NRF_ERROR_INVALID_STATE);
  scheduler_state = taskSCHEDULER_RUNNING;
  defer_pendsv = true; reset(millis());
  const auto before_ack = acknowledgements;
  board.prepareSleepCycle(); board.sleep(0);
  assert(acknowledgements == before_ack && snapshot(millis()).detail == NRF_ERROR_INVALID_STATE);
  defer_pendsv = false; scb.ICSR = 0;
  acknowledgement_error = 31; reset(millis());
  board.prepareSleepCycle(); board.sleep(0);
  assert(snapshot(millis()).errors == 1 && snapshot(millis()).detail == 31);
  acknowledgement_error = 0;
  invalidate_during_yield = true; reset(millis());
  const auto before_invalid_ack = acknowledgements;
  board.prepareSleepCycle(); board.sleep(0);
  assert(acknowledgements == before_invalid_ack && snapshot(millis()).detail == 3);
  invalidate_during_yield = false; yielded_app_irq = false;
  board.prepareSleepCycle(); board.radio_test = true; board.sleep(0);
  board.radio_test = false; board.sleep(0);
  assert(sd_waits == 1); // A veto consumed the prior preparation too.

  // Only the disabled polling CryptoCell IRQ may be cleaned, and only at
  // entry. An enabled owner and unrelated NVIC sources are never touched.
  cc_pending = true;
  const auto before_cc_clear = cc_clears;
  board.prepareSleepCycle();
  assert(!cc_pending && cc_clears == before_cc_clear + 1);
  cc_enabled = true; cc_pending = true;
  board.prepareSleepCycle();
  assert(cc_pending && cc_clears == before_cc_clear + 1);
  cc_enabled = false;
  // A public SDK critical region can virtually mask an enabled owner even
  // with all CPU mask registers clear. Refuse preparation before cleanup.
  nrf_nvic_state.__cr_flag = 1;
  const auto before_critical_query = state_queries;
  board.prepareSleepCycle();
  assert(state_queries == before_critical_query && cc_pending);
  assert(cc_clears == before_cc_clear + 1);
  assert(mesh_nrf52::sleepCycleState().lastError() == NRF_ERROR_INVALID_STATE);
  nrf_nvic_state.__cr_flag = 0;
  // Recheck the volatile SDK critical flag after taskYIELD, not just at entry.
  critical_during_yield = true;
  const auto before_critical_ack = acknowledgements;
  board.prepareSleepCycle();
  assert(acknowledgements == before_critical_ack);
  assert(mesh_nrf52::sleepCycleState().lastError() == NRF_ERROR_INVALID_STATE);
}
'''.replace("@METHODS@", methods)
        self.execute(code)

    def test_every_application_bluefruit_attempt_invalidates_before_call(self):
        for filename, call in (
            ("src/helpers/NRF52Board.cpp", "if (!Bluefruit.begin(1, 0))"),
            ("src/helpers/nrf52/SerialBLEInterface.cpp", "if (!Bluefruit.begin())"),
        ):
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text(encoding="ascii")
                at = source.index(call)
                self.assertTrue(source[:at].rstrip().endswith(
                    "mesh_nrf52::sleepCycleState().invalidate();"))
        board = (ROOT / "src/helpers/NRF52Board.cpp").read_text(encoding="ascii")
        late = extract_braced(board, "void NRF52Board::sleep(uint32_t secs)")
        self.assertNotIn("softdeviceIsEnabled", late)
        self.assertNotIn("sd_power_usbregstatus_get", late)
        self.assertNotIn("NVIC->", late)


if __name__ == "__main__":
    unittest.main()
