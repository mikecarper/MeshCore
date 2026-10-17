#!/usr/bin/env python3
"""Execute the real two-phase nRF52 profile-idle preparation and tick guard."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class Nrf52ProfileIdleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-profile-idle-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = (ROOT / "src/helpers/NRF52Board.cpp").read_text(encoding="ascii")
        methods = "\n".join(body(source, signature) for signature in (
            "void NRF52Board::prepareSleepCycle()",
            "bool NRF52Board::canIdleRadioProfile()",
            "void NRF52Board::prepareProfileIdleCycle()",
            "void NRF52Board::sleep(uint32_t secs)",
        ))
        fixture = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/nrf52/SleepCycleState.h>
#ifndef TEST_TICK_RATE
#define TEST_TICK_RATE 1024
#endif
#define configTICK_RATE_HZ TEST_TICK_RATE
#define __FPU_USED 1
#define NRF_CRYPTOCELL 1
constexpr uint32_t NRF_SUCCESS = 0, NRF_ERROR_INVALID_STATE = 8;
constexpr uint32_t SCB_ICSR_PENDSVSET_Msk = 1UL << 28;
constexpr uint32_t RTC_INTENSET_TICK_Msk = 1;
constexpr int RTC1_IRQn = 17, FPU_IRQn = 38, CRYPTOCELL_IRQn = 42;
constexpr int taskSCHEDULER_RUNNING = 1;
struct SdkNvicState { volatile uint32_t __cr_flag = 0; } nrf_nvic_state;
struct Scb { uint32_t ICSR = 0; } scb;
static Scb* const SCB = &scb;
struct Rtc { uint32_t PRESCALER = 31, INTENSET = RTC_INTENSET_TICK_Msk; } rtc;
static Rtc* const NRF_RTC1 = &rtc;
struct Power { uint32_t USBREGSTATUS = 0; } power;
static Power* const NRF_POWER = &power;
static bool irq_enabled[64] = {}, irq_pending[64] = {};
static unsigned clears[64] = {};
static uint32_t ipsr = 0, primask = 0, basepri = 0, faultmask = 0;
static uint32_t fpscr = 0, clock_us = 10000;
static int scheduler_state = taskSCHEDULER_RUNNING;
static bool event = false, softdevice = false, work_pending = false;
static bool inject_during_prepare = false;
static unsigned app_irq = 1, acknowledged_irq = 0;
static unsigned state_queries = 0, sdk_calls = 0, sleeps = 0, delays = 0;
static uint32_t __get_IPSR() { return ipsr; }
static uint32_t __get_PRIMASK() { return primask; }
static uint32_t __get_BASEPRI() { return basepri; }
static uint32_t __get_FAULTMASK() { return faultmask; }
static int xTaskGetSchedulerState() { return scheduler_state; }
static uint32_t NVIC_GetEnableIRQ(int irq) { return irq_enabled[irq]; }
static void NVIC_ClearPendingIRQ(int irq) { irq_pending[irq] = false; ++clears[irq]; }
static uint32_t __get_FPSCR() { return fpscr; }
static void __set_FPSCR(uint32_t value) { fpscr = value; }
static uint32_t millis() { return clock_us / 1000; }
static uint32_t micros() { return clock_us; }
static void delay(uint32_t ms) { delays += ms; }
static void applicationIrq() { ++app_irq; event = true; work_pending = true; }
static void taskYIELD() { ++app_irq; event = true; }
namespace mesh_nrf52 {
static uint32_t softdeviceIsEnabled(uint8_t& enabled) {
  ++state_queries; enabled = softdevice; event = true; return NRF_SUCCESS;
}
}
static uint32_t sd_power_usbregstatus_get(uint32_t* value) {
  *value = power.USBREGSTATUS; event = true; return NRF_SUCCESS;
}
static uint32_t sd_app_evt_wait() {
  ++sdk_calls;
  const bool history = app_irq != acknowledged_irq;
  acknowledged_irq = app_irq;
  event = false;
  if (!history && !irq_pending[FPU_IRQn] && !irq_pending[CRYPTOCELL_IRQn]) {
    assert(!work_pending); ++sleeps; clock_us += 500;
    ++app_irq;
  }
  return NRF_SUCCESS;
}
static void __SEV() { event = true; }
static void __WFE() {
  if (inject_during_prepare) { inject_during_prepare = false; applicationIrq(); }
  if (event) event = false;
  else { assert(!work_pending); ++sleeps; clock_us += 500; }
}
struct NRF52Board {
  bool radio_test = false;
  bool isRadioTestActive() const { return radio_test; }
  void prepareSleepCycle();
  static bool canIdleRadioProfile();
  void prepareProfileIdleCycle();
  void sleep(uint32_t secs);
};
@METHODS@
static void floatingWork() {
  fpscr = 0x60000010UL; irq_pending[FPU_IRQn] = true;
  event = true; // SEVONPEND retains the event even after NVIC pending clears.
}
static void ready() {
  irq_enabled[RTC1_IRQn] = true;
  assert(NRF52Board::canIdleRadioProfile() == (TEST_TICK_RATE == 1024));
}
static void normal(bool sdk) {
  ready(); softdevice = sdk;
  NRF52Board board;
  board.prepareSleepCycle();
  const auto initial_queries = state_queries;
  floatingWork();
  board.prepareProfileIdleCycle();
  assert(state_queries == initial_queries + 1);
  assert(!event && !irq_pending[FPU_IRQn] && (fpscr & 0x9f) == 0);
  assert((fpscr & ~0x9fUL) == 0x60000000UL);
  assert(clears[FPU_IRQn] == 1 && !work_pending);
  const auto waits_before = sdk_calls;
  assert(NRF52Board::canIdleRadioProfile()); // Final pure recheck creates no event/SVC.
  assert(!event && state_queries == initial_queries + 1 && sdk_calls == waits_before);
  board.sleep(0);
  assert(sleeps == 1 && delays == 0);
}
static void legacy() {
  ready(); NRF52Board board;
  board.prepareSleepCycle(); floatingWork();
  board.sleep(0);
  // The late FPU cleanup cannot remove an already-latched ARM event. This
  // negative control reproduces the measured tuple-dependent raw-WFE insomnia.
  assert(sleeps == 0 && !irq_pending[FPU_IRQn]);
}
static void vetoes() {
  ready();
  if (TEST_TICK_RATE != 1024) return;
  for (uint32_t* value : {&ipsr, &primask, &basepri, &faultmask}) {
    *value = 1; assert(!NRF52Board::canIdleRadioProfile()); *value = 0;
  }
  scheduler_state = 0; assert(!NRF52Board::canIdleRadioProfile());
  scheduler_state = taskSCHEDULER_RUNNING;
  nrf_nvic_state.__cr_flag = 1; assert(!NRF52Board::canIdleRadioProfile());
  nrf_nvic_state.__cr_flag = 0;
  scb.ICSR = SCB_ICSR_PENDSVSET_Msk; assert(!NRF52Board::canIdleRadioProfile());
  scb.ICSR = 0;
  rtc.PRESCALER = 0; assert(!NRF52Board::canIdleRadioProfile()); rtc.PRESCALER = 31;
  rtc.INTENSET = 0; assert(!NRF52Board::canIdleRadioProfile());
  rtc.INTENSET = RTC_INTENSET_TICK_Msk;
  irq_enabled[RTC1_IRQn] = false; assert(!NRF52Board::canIdleRadioProfile());
  irq_enabled[RTC1_IRQn] = true;
  assert(NRF52Board::canIdleRadioProfile() && state_queries == 0 && sdk_calls == 0);
}
static void owners() {
  ready(); NRF52Board board;
  floatingWork(); irq_enabled[FPU_IRQn] = true;
  irq_pending[7] = true;
  board.prepareProfileIdleCycle();
  assert(clears[FPU_IRQn] == 0 && irq_pending[FPU_IRQn]);
  assert(fpscr == 0x60000010UL && irq_pending[7] && clears[7] == 0);
  irq_enabled[FPU_IRQn] = false;
  nrf_nvic_state.__cr_flag = 1;
  const auto before = state_queries;
  board.prepareProfileIdleCycle();
  assert(state_queries == before && clears[FPU_IRQn] == 0 && irq_pending[FPU_IRQn]);
  assert(mesh_nrf52::sleepCycleState().lastError() == NRF_ERROR_INVALID_STATE);
}
static void arrivals(bool sdk, bool during_prepare) {
  ready(); softdevice = sdk; NRF52Board board;
  floatingWork(); inject_during_prepare = during_prepare;
  board.prepareProfileIdleCycle();
  if (during_prepare) {
    // Preparation may consume the event, but the ENTIRE following role
    // predicate still observes the ISR-owned work and must decline sleep.
    assert(work_pending && sleeps == 0);
    return;
  }
  assert(!work_pending && NRF52Board::canIdleRadioProfile());
  applicationIrq(); // Arrives after the role predicate's final decision.
  board.sleep(0);
  assert(work_pending && sleeps == 0 && delays == 0);
}
static void lateFloatingWork() {
  ready(); NRF52Board board;
  board.prepareProfileIdleCycle();
  floatingWork(); // A missed float call must degrade idle, never erase its event.
  board.sleep(0);
  assert(sleeps == 0 && delays == 0);
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!std::strcmp(argv[1], "raw")) normal(false);
  else if (!std::strcmp(argv[1], "sdk")) normal(true);
  else if (!std::strcmp(argv[1], "legacy")) legacy();
  else if (!std::strcmp(argv[1], "vetoes")) vetoes();
  else if (!std::strcmp(argv[1], "owners")) owners();
  else if (!std::strcmp(argv[1], "late-raw")) arrivals(false, false);
  else if (!std::strcmp(argv[1], "late-sdk")) arrivals(true, false);
  else if (!std::strcmp(argv[1], "prepare-raw")) arrivals(false, true);
  else if (!std::strcmp(argv[1], "prepare-sdk")) arrivals(true, true);
  else if (!std::strcmp(argv[1], "late-fpu")) lateFloatingWork();
  else assert(false);
}
'''.replace("@METHODS@", methods)
        cls.binaries = {}
        for tick_rate in (1024, 1000):
            cpp = Path(cls.temp.name) / ("idle-%d.cpp" % tick_rate)
            binary = cpp.with_suffix("")
            cpp.write_text(fixture, encoding="ascii")
            result = subprocess.run(
                shlex.split(os.environ.get("CXX", "g++"))
                + ["-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                   "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                   "-fno-pie", "-no-pie", "-DTEST_TICK_RATE=%d" % tick_rate,
                   "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            cls.binaries[tick_rate] = binary

    def run_case(self, case, tick_rate=1024):
        result = subprocess.run([str(self.binaries[tick_rate]), case],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fpu_event_refresh_restores_raw_and_softdevice_single_wait(self):
        self.run_case("raw")
        self.run_case("sdk")

    def test_late_existing_fpu_cleanup_reproduces_raw_insomnia(self):
        self.run_case("legacy")

    def test_tick_context_and_pending_scheduler_guards_are_pure(self):
        self.run_case("vetoes")
        self.run_case("vetoes", tick_rate=1000)

    def test_enabled_fpu_owner_unrelated_irq_and_sdk_critical_are_preserved(self):
        self.run_case("owners")

    def test_work_during_preparation_survives_for_complete_role_predicate(self):
        self.run_case("prepare-raw")
        self.run_case("prepare-sdk")

    def test_irq_after_predicate_prevents_sleep_without_late_event_clear(self):
        self.run_case("late-raw")
        self.run_case("late-sdk")

    def test_unexpected_float_after_preparation_remains_immediate_not_double_wait(self):
        self.run_case("late-fpu")


if __name__ == "__main__":
    unittest.main()
