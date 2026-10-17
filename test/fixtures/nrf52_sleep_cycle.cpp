// Exercise the real portable cache and extracted board methods. Hardware
// primitives implement the documented event contract, not Nordic SDK internals.
#include <cassert>
#include <cstdint>
#include <cstring>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/nrf52/SleepCycleState.h>

void invalidateSleepCycleFromOtherTranslationUnit();

static bool event_latched = true, application_work = false;
static bool softdevice_enabled = false, interrupt_during_query = false;
static bool invalidate_during_query = false;
static uint32_t state_error = 0, usb_error = 0, wait_error = 0, clock_us = 10000;
static unsigned state_queries = 0, usb_queries = 0, sdk_waits = 0;
static unsigned raw_wfes = 0, real_sleeps = 0, short_returns = 0;
static uint8_t app_irq_current = 1, app_irq_acknowledged = 0;
static bool cc_enabled = false, cc_pending = false, other_irq_pending = false;
static bool sdk_critical_after_yield = false;
static unsigned cc_clears = 0;
constexpr uint32_t NRF_SUCCESS = 0;
constexpr uint32_t NRF_ERROR_INVALID_STATE = 8;
constexpr uint32_t POWER_USBREGSTATUS_VBUSDETECT_Msk = 1;
constexpr uint32_t SCB_ICSR_PENDSVSET_Msk = 1u << 28;
constexpr int taskSCHEDULER_RUNNING = 1;
#define NRF_CRYPTOCELL 1
constexpr int CRYPTOCELL_IRQn = 42;
struct SdkNvicState { volatile uint32_t __cr_flag = 0; } nrf_nvic_state;
static uint32_t NVIC_GetEnableIRQ(int irq) {
  assert(irq == CRYPTOCELL_IRQn);
  return cc_enabled;
}
static void NVIC_ClearPendingIRQ(int irq) {
  assert(irq == CRYPTOCELL_IRQn);
  ++cc_clears;
  cc_pending = false;
}
struct SystemRegisters { uint32_t ICSR = 0; } system_registers;
static SystemRegisters* const SCB = &system_registers;
static uint32_t __get_IPSR() { return 0; }
static uint32_t __get_PRIMASK() { return 0; }
static uint32_t __get_BASEPRI() { return 0; }
static uint32_t __get_FAULTMASK() { return 0; }
static int xTaskGetSchedulerState() { return taskSCHEDULER_RUNNING; }
struct PowerRegisters { uint32_t USBREGSTATUS = 0; } power_registers;
static PowerRegisters* const NRF_POWER = &power_registers;

static uint32_t micros() { return clock_us; }
static uint32_t millis() { return clock_us / 1000; }
static void delay(unsigned amount) { clock_us += amount * 1000; }
static void recordApplicationInterrupt() {
  const uint8_t next = static_cast<uint8_t>(app_irq_current + 1);
  if (next != app_irq_acknowledged) app_irq_current = next;
  event_latched = true;
}
void taskYIELD() {
  recordApplicationInterrupt();
  if (sdk_critical_after_yield) nrf_nvic_state.__cr_flag = 1;
}
static void applicationIrq() { application_work = true; recordApplicationInterrupt(); }
namespace mesh_nrf52 {
static uint32_t softdeviceIsEnabled(uint8_t& enabled) {
  ++state_queries;
  event_latched = true; // The state-query SVC is itself an event source.
  if (interrupt_during_query) applicationIrq();
  if (invalidate_during_query) invalidateSleepCycleFromOtherTranslationUnit();
  enabled = softdevice_enabled;
  return state_error;
}
}
static uint32_t sd_power_usbregstatus_get(uint32_t* status) {
  ++usb_queries;
  event_latched = true;
  *status = power_registers.USBREGSTATUS;
  return usb_error;
}
static void waitOnce() {
  if (event_latched) {
    ++short_returns;
    event_latched = false;
  } else {
    assert(!application_work); // A second late wait would lose this work.
    ++real_sleeps;
    clock_us += 547;
    recordApplicationInterrupt(); // The next periodic IRQ wakes the CPU.
  }
}
static void __SEV() { event_latched = true; }
static void __WFE() { ++raw_wfes; waitOnce(); }
static uint32_t sd_app_evt_wait() {
  assert(softdevice_enabled);
  ++sdk_waits;
  const bool app_irq_pending = app_irq_current != app_irq_acknowledged;
  // Conservative API abstraction, not the exact ROM counter-copy timing:
  // retaining a waking IRQ in private history avoids hiding application work.
  // The ROM copies after its first WFE triplet/comparison, not at this point.
  app_irq_acknowledged = app_irq_current;
  // Public SVC entry consumes its own ARM event, then the SDK's separate
  // application-IRQ latch decides immediate return versus a real sleep.
  event_latched = false;
  if (!app_irq_pending && !cc_pending && !other_irq_pending) {
    assert(!application_work);
    ++real_sleeps;
    clock_us += 547;
    recordApplicationInterrupt();
  } else {
    ++short_returns;
  }
  __SEV();
  event_latched = false;
  return wait_error;
}

struct NRF52Board {
  bool radio_test = false;
  bool isRadioTestActive() const { return radio_test; }
  void prepareSleepCycle();
  bool isExternalPowered();
  void sleep(uint32_t secs);
};

@BOARD_METHODS@

static void cacheContract() {
  using mesh_nrf52::SleepCycleSnapshot;
  mesh_nrf52::SleepCycleState state;
  SleepCycleSnapshot value = {};
  assert(!state.snapshot(value) && !state.take(value));
  const uint32_t first = state.begin();
  assert(state.publish(first, false, 1));
  assert(state.current(first) && state.snapshot(value));
  assert(value.generation == first && !value.softdevice_enabled && value.usb_status == 1);
  assert(state.take(value) && !state.take(value));

  const uint32_t stale = state.begin();
  state.invalidate();
  assert(!state.current(stale) && !state.publish(stale, true, 2));
  assert(!state.snapshot(value) && !state.take(value));
  const uint32_t failed = state.begin();
  assert(state.publish(failed, false, 0, 23));
  assert(state.lastError() == 23 && !state.take(value));
  const uint32_t recovery = state.begin();
  assert(state.publish(recovery, true, 2));
  assert(state.lastError() == 0 && state.snapshot(value));
  // A late completion from an obsolete cycle cannot overwrite new state.
  assert(!state.publish(stale, false, 99, 44));
  assert(state.snapshot(value) && value.generation == recovery);
  assert(value.softdevice_enabled && value.usb_status == 2);
}

static void boardFlow(bool enabled) {
  NRF52Board board;
  softdevice_enabled = enabled;
  // No historic app IRQ may be assumed when preparing the first SDK cycle.
  // A real forced PendSV, not SEV alone, must guarantee early SDK return.
  app_irq_acknowledged = app_irq_current;
  power_registers.USBREGSTATUS = 1;
  board.prepareSleepCycle();
  assert(state_queries == 1 && !event_latched && real_sleeps == 0);
  assert(board.isExternalPowered());
  const unsigned queries_before_sleep = state_queries + usb_queries;
  board.sleep(0);
  assert(state_queries + usb_queries == queries_before_sleep);
  assert(real_sleeps == 1 && sdk_waits == (enabled ? 2u : 0u));
  const unsigned waits = raw_wfes + sdk_waits;
  board.sleep(0);
  assert(raw_wfes + sdk_waits == waits); // Prepared attempt is single-use.
}

static void lateIrq(bool enabled) {
  NRF52Board board;
  softdevice_enabled = enabled;
  board.prepareSleepCycle();
  assert(!event_latched);
  // The main loop has already serviced inputs and accepted its idle predicate.
  applicationIrq();
  board.sleep(0);
  assert(application_work && real_sleeps == 0);
  board.prepareSleepCycle();
  assert(application_work); // Preparation consumes only the event, not work.
  application_work = false; // The next actual service pass processes it.
  board.sleep(0);
  assert(real_sleeps == 1);
}

static void transition() {
  NRF52Board board;
  board.prepareSleepCycle();
  invalidateSleepCycleFromOtherTranslationUnit();
  softdevice_enabled = true; // Also models a partial Bluefruit.begin failure.
  const unsigned waits = raw_wfes + sdk_waits;
  board.sleep(0);
  assert(raw_wfes + sdk_waits == waits && real_sleeps == 0);
  board.prepareSleepCycle();
  board.sleep(0);
  assert(sdk_waits == 2 && real_sleeps == 1);
}

static void failedQuery() {
  NRF52Board board;
  state_error = 17;
  board.prepareSleepCycle();
  const unsigned waits = raw_wfes + sdk_waits;
  board.sleep(0);
  assert(raw_wfes + sdk_waits == waits && real_sleeps == 0);
  state_error = 0;
  board.prepareSleepCycle();
  board.sleep(0);
  assert(real_sleeps == 1);
}

static void invalidationDuringQuery() {
  NRF52Board board;
  invalidate_during_query = true;
  board.prepareSleepCycle();
  const unsigned waits = raw_wfes + sdk_waits;
  board.sleep(0);
  assert(raw_wfes + sdk_waits == waits && real_sleeps == 0);
}

static void irqDuringPreparation() {
  NRF52Board board;
  interrupt_during_query = true;
  board.prepareSleepCycle();
  assert(application_work && real_sleeps == 0);
  application_work = false; // Work is serviced after preparation, never before.
  board.sleep(0);
  assert(real_sleeps == 1);
}

static void usbSnapshot() {
  NRF52Board board;
  power_registers.USBREGSTATUS = 1;
  board.prepareSleepCycle();
  assert(board.isExternalPowered());
  power_registers.USBREGSTATUS = 0;
  const unsigned queries = state_queries + usb_queries;
  assert(board.isExternalPowered()); // Per-cycle value, not another late SVC.
  assert(state_queries + usb_queries == queries);
  board.prepareSleepCycle();
  assert(!board.isExternalPowered());
}

static void ccRecovery(bool late) {
  NRF52Board board;
  softdevice_enabled = true;
  cc_pending = !late;
  board.prepareSleepCycle();
  assert(!cc_pending && cc_clears == 1);
  if (late) {
    cc_pending = true;
    event_latched = true;
    board.sleep(0);
    assert(cc_pending && real_sleeps == 0); // No unsafe late pending clear.
    board.prepareSleepCycle();
    assert(!cc_pending && cc_clears == 2);
  }
  board.sleep(0);
  assert(real_sleeps == 1);
}

static void preserveOwner(bool other_irq) {
  NRF52Board board;
  softdevice_enabled = true;
  cc_enabled = !other_irq;
  cc_pending = !other_irq;
  other_irq_pending = other_irq;
  board.prepareSleepCycle();
  assert(other_irq ? other_irq_pending : cc_pending);
  if (!other_irq) assert(cc_clears == 0);
  board.sleep(0);
  assert(real_sleeps == 0);
  assert(other_irq ? other_irq_pending : cc_pending);
}

static void sdkCriticalRegion(bool after_yield) {
  NRF52Board board;
  softdevice_enabled = true;
  cc_pending = true;
  sdk_critical_after_yield = after_yield;
  nrf_nvic_state.__cr_flag = after_yield ? 0 : 1;
  board.prepareSleepCycle();
  assert(sdk_waits == 0 && real_sleeps == 0);
  assert(mesh_nrf52::sleepCycleState().lastError() == NRF_ERROR_INVALID_STATE);
  if (!after_yield) assert(state_queries == 0 && cc_clears == 0 && cc_pending);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (!std::strcmp(argv[1], "cache")) cacheContract();
  else if (!std::strcmp(argv[1], "raw")) boardFlow(false);
  else if (!std::strcmp(argv[1], "sd")) boardFlow(true);
  else if (!std::strcmp(argv[1], "raw_irq")) lateIrq(false);
  else if (!std::strcmp(argv[1], "sd_irq")) lateIrq(true);
  else if (!std::strcmp(argv[1], "transition")) transition();
  else if (!std::strcmp(argv[1], "query_error")) failedQuery();
  else if (!std::strcmp(argv[1], "during_query")) invalidationDuringQuery();
  else if (!std::strcmp(argv[1], "prepare_irq")) irqDuringPreparation();
  else if (!std::strcmp(argv[1], "usb")) usbSnapshot();
  else if (!std::strcmp(argv[1], "cc_recover")) ccRecovery(false);
  else if (!std::strcmp(argv[1], "cc_late")) ccRecovery(true);
  else if (!std::strcmp(argv[1], "cc_owner")) preserveOwner(false);
  else if (!std::strcmp(argv[1], "other_owner")) preserveOwner(true);
  else if (!std::strcmp(argv[1], "sdk_critical")) sdkCriticalRegion(false);
  else if (!std::strcmp(argv[1], "sdk_critical_after_yield")) sdkCriticalRegion(true);
  else assert(false);
}
