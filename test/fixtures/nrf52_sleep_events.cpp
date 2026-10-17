// Host-only causal model, not an emulator of the opaque Nordic SoftDevice.
//
// ARM Cortex-M4: SEV sets the event register; WFE consumes an already-set
// event without sleeping. With SEVONPEND an IRQ can leave the event latched
// after its handler has run. The installed FreeRTOS portYIELD also sends SEV.
// Nordic sd_app_evt_wait's documented contract preserves an application IRQ
// that happened before its single wait so the main loop can service it.
//
// This model proves ordering properties and known counterexamples only.
// It cannot prove actual firmware call placement, SDK filtering, ISR details,
// elapsed sleep time, or current consumption; those need integration/hardware.

#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>

enum class Backend { Raw, SoftDevice };
enum class Point {
  None,
  BeforePrepare,
  BeforeForcedYield,
  AfterForcedYield,
  AfterEntrySdkWait,
  BetweenEarlySevAndWfe,
  AfterPrepare,
  BeforeService,
  AfterService,
  BeforePredicate,
  AfterPredicate,
  BeforeWait,
  DuringWait,
  AfterFirstWait,
  BetweenCleanupSevAndWfe
};

struct Cpu {
  bool event = true; // Prior loop-task yield already sent SEV.
  bool work_pending = false;
  bool slept_with_work = false;
  unsigned services = 0;
  unsigned sleeps = 0;
  unsigned immediate_returns = 0;
  unsigned waits = 0;
  unsigned sdk_calls = 0;
  unsigned yields = 0;
  unsigned polls = 0;
  unsigned predicate_vetoes = 0;
  Point injection = Point::None;
  bool injected = false;
  uint8_t app_irq_current = 1, app_irq_acknowledged = 0;
  bool cc_pending = false, cc_enabled = false, other_irq_pending = false;
  unsigned cc_clears = 0;

  void sev() { event = true; }

  void recordApplicationInterrupt() {
    const uint8_t next = static_cast<uint8_t>(app_irq_current + 1);
    // Model the ROM's wrap-safe private latch: never let unacknowledged
    // application interrupts wrap back to the previous wait's count.
    if (next != app_irq_acknowledged) app_irq_current = next;
    event = true;
  }

  void schedulerYield() {
    ++yields;
    recordApplicationInterrupt(); // Real application PendSV, not just SEV.
  }

  void pendCryptoCell() {
    if (!cc_pending) event = true; // New pending transition with SEVONPEND.
    cc_pending = true; // A disabled IRQ does not run an ISR/update SDK counter.
  }

  void applicationIrq() {
    work_pending = true; // ISR-published work survives event consumption.
    recordApplicationInterrupt();
  }

  void inject(Point point) {
    if (!injected && injection == point) {
      applicationIrq();
      injected = true;
    }
  }

  // Exactly one WFE decision. A simulated next periodic IRQ ends real sleep,
  // without generating application work unless DuringWait was requested.
  void waitOnce() {
    ++waits;
    if (event) {
      event = false;
      ++immediate_returns;
      return;
    }
    ++sleeps;
    if (work_pending) slept_with_work = true;
    inject(Point::DuringWait);
    // WFE has now been woken by an IRQ. Cleanup below consumes its event.
    recordApplicationInterrupt();
  }

  // A guaranteed nonblocking consume, performed only before all work polls.
  void prepare(Backend backend, bool acknowledge_sd = true, bool clean_cc = true) {
    inject(Point::BeforePrepare);
    if (clean_cc && !cc_enabled) {
      cc_pending = false;
      ++cc_clears;
    }
    if (backend == Backend::SoftDevice && acknowledge_sd) {
      inject(Point::BeforeForcedYield);
      schedulerYield();
      inject(Point::AfterForcedYield);
      const unsigned before = sleeps;
      singleWait(backend, true);
      assert(sleeps == before); // The real PendSV guarantees a pending app IRQ.
      inject(Point::AfterEntrySdkWait);
    }
    sev();
    inject(Point::BetweenEarlySevAndWfe);
    assert(event);
    event = false; // Early WFE; deliberately no second WFE here.
    inject(Point::AfterPrepare);
  }

  void service() {
    inject(Point::BeforeService);
    if (work_pending) {
      ++services;
      work_pending = false;
    }
    inject(Point::AfterService);
  }

  void pollRx(bool yielding_empty_poll) {
    ++polls;
    if (yielding_empty_poll) {
      schedulerYield(); // Adafruit_USBD_CDC::available -> yield -> portYIELD.
    }
  }

  void singleWait(Backend backend, bool entry_ack = false) {
    if (backend == Backend::SoftDevice) {
      ++sdk_calls;
      ++waits;
      const bool app_irq_pending = app_irq_current != app_irq_acknowledged;
      // Conservative public-contract abstraction, not exact ROM timing: ack
      // the pre-call history before simulating the wait. This intentionally
      // retains a new IRQ from during the wait for the next call. The ROM
      // copies its counter after an initial WFE triplet/comparison instead.
      app_irq_acknowledged = app_irq_current;
      // Public SDK entry itself uses SVC and consumes that ARM event first.
      // Its private application-IRQ counter then decides whether to return
      // or loop into a real wait. Raw ER consumption does not acknowledge it.
      sev();
      event = false;
      if (!app_irq_pending && !cc_pending && !other_irq_pending) {
        ++sleeps;
        if (work_pending) slept_with_work = true;
        inject(Point::DuringWait);
        recordApplicationInterrupt(); // Periodic application IRQ wakes SDK.
      } else {
        ++immediate_returns;
      }
    } else {
      waitOnce();
    }
    if (!entry_ack) inject(Point::AfterFirstWait);
    sev();
    if (!entry_ack) inject(Point::BetweenCleanupSevAndWfe);
    assert(event);
    event = false; // Cleanup WFE cannot sleep because its own SEV is set.
  }

  void iteration(Backend backend, bool entry_clear, bool yielding_empty_poll,
                 bool predicate_sees_work = true, bool unsafe_late_clear = false,
                 bool acknowledge_sd = true, bool clean_cc = true) {
    if (entry_clear) prepare(backend, acknowledge_sd, clean_cc);
    service();
    pollRx(yielding_empty_poll);
    inject(Point::BeforePredicate);
    const bool eligible = !predicate_sees_work || !work_pending;
    inject(Point::AfterPredicate);
    if (eligible) {
      inject(Point::BeforeWait);
      if (unsafe_late_clear) {
        // Raw SEV/WFE/WFE or two consecutive SD waits: the first consumes
        // the late application's only event, the second can then sleep.
        if (backend == Backend::SoftDevice) {
          singleWait(backend);
        } else {
          sev();
          event = false;
        }
      }
      singleWait(backend);
    } else {
      ++predicate_vetoes;
    }
    // Actual pinned Arduino main.cpp yields after every sketch loop().
    schedulerYield();
  }
};

static void scheduler() {
  for (Backend backend : {Backend::Raw, Backend::SoftDevice}) {
    Cpu cpu;
    for (unsigned n = 0; n < 100; ++n) cpu.iteration(backend, false, false);
    assert(cpu.sleeps == 0 && cpu.immediate_returns == 100);
    assert(cpu.waits == 100 && cpu.yields == 100);
    assert(!cpu.slept_with_work);
    assert(cpu.sdk_calls == (backend == Backend::SoftDevice ? 100u : 0u));
  }
}

static void entryIdle() {
  for (Backend backend : {Backend::Raw, Backend::SoftDevice}) {
    Cpu cpu;
    for (unsigned n = 0; n < 100; ++n) cpu.iteration(backend, true, false);
    assert(cpu.sleeps == 100);
    assert(cpu.immediate_returns == (backend == Backend::SoftDevice ? 100u : 0u));
    assert(cpu.polls == 100 && cpu.yields == (backend == Backend::SoftDevice ? 200u : 100u));
    assert(!cpu.slept_with_work && cpu.services == 0);
  }
}

static void pollYield() {
  for (Backend backend : {Backend::Raw, Backend::SoftDevice}) {
    Cpu cpu;
    for (unsigned n = 0; n < 100; ++n) cpu.iteration(backend, true, true);
    assert(cpu.sleeps == 0);
    assert(cpu.immediate_returns == (backend == Backend::SoftDevice ? 200u : 100u));
    assert(cpu.polls == 100 && cpu.yields == (backend == Backend::SoftDevice ? 300u : 200u));
    assert(!cpu.slept_with_work);
  }
}

static const Point irq_points[] = {
  Point::BeforePrepare, Point::BeforeForcedYield, Point::AfterForcedYield,
  Point::AfterEntrySdkWait, Point::BetweenEarlySevAndWfe, Point::AfterPrepare,
  Point::BeforeService, Point::AfterService, Point::BeforePredicate,
  Point::AfterPredicate, Point::BeforeWait, Point::DuringWait,
  Point::AfterFirstWait, Point::BetweenCleanupSevAndWfe
};

static void injections(bool predicate_sees_work) {
  for (Backend backend : {Backend::Raw, Backend::SoftDevice}) {
    for (Point point : irq_points) {
      if (backend == Backend::Raw && (point == Point::BeforeForcedYield
          || point == Point::AfterForcedYield || point == Point::AfterEntrySdkWait)) continue;
      Cpu cpu;
      cpu.injection = point;
      cpu.iteration(backend, true, false, predicate_sees_work);
      assert(cpu.injected);
      assert(!cpu.slept_with_work);
      if (point == Point::AfterPredicate || point == Point::BeforeWait) {
        assert(cpu.sleeps == 0);
        assert(cpu.immediate_returns == (backend == Backend::SoftDevice ? 2u : 1u));
        assert(cpu.work_pending && cpu.services == 0);
      }
      if (!predicate_sees_work &&
          (point == Point::AfterService || point == Point::BeforePredicate)) {
        assert(cpu.sleeps == 0);
        assert(cpu.immediate_returns == (backend == Backend::SoftDevice ? 2u : 1u));
      }
      // Work arriving during cleanup must not be followed by another wait.
      // Entry clear next time is safe because service follows it.
      cpu.iteration(backend, true, false, predicate_sees_work);
      assert(cpu.services == 1 && !cpu.work_pending);
      assert(!cpu.slept_with_work);
      assert(cpu.sdk_calls <= 4);
    }
  }
}

static void lateDouble() {
  for (Backend backend : {Backend::Raw, Backend::SoftDevice}) {
    for (Point point : {Point::AfterPredicate, Point::BeforeWait}) {
      Cpu cpu;
      cpu.injection = point;
      cpu.iteration(backend, true, false, true, true);
      assert(cpu.injected && cpu.services == 0 && cpu.work_pending);
      assert(cpu.sleeps == 1 && cpu.slept_with_work);
      if (backend == Backend::SoftDevice) {
        assert(cpu.sdk_calls == 3 && cpu.waits == 3);
        assert(cpu.immediate_returns == 2);
      }
    }
  }
}

static void cleanup() {
  for (Point point : {Point::AfterFirstWait, Point::BetweenCleanupSevAndWfe}) {
    Cpu cpu;
    cpu.injection = point;
    cpu.iteration(Backend::Raw, true, false);
    assert(cpu.waits == 1 && cpu.sleeps == 1);
    assert(cpu.injected && cpu.work_pending && !cpu.slept_with_work);
    cpu.iteration(Backend::Raw, true, false);
    assert(cpu.services == 1 && !cpu.work_pending && !cpu.slept_with_work);
  }
}

static void privateCounter() {
  Cpu cpu;
  for (unsigned n = 0; n < 100; ++n)
    cpu.iteration(Backend::SoftDevice, true, false, true, false, false);
  assert(cpu.sleeps == 0 && cpu.sdk_calls == 100);
  assert(cpu.immediate_returns == 100); // Raw early clear cannot ack SDK state.
}

static void sevOnlyEntry() {
  Cpu cpu;
  cpu.app_irq_acknowledged = cpu.app_irq_current;
  cpu.work_pending = true; // Preexisting service work, not a new IRQ.
  cpu.sev();
  cpu.singleWait(Backend::SoftDevice);
  assert(cpu.sleeps == 1 && cpu.slept_with_work);
}

static void privateCounterWrap() {
  Cpu cpu;
  cpu.app_irq_current = cpu.app_irq_acknowledged = 19;
  for (unsigned n = 0; n < 2048; ++n) {
    cpu.recordApplicationInterrupt();
    assert(cpu.app_irq_current != cpu.app_irq_acknowledged);
  }
  const unsigned before = cpu.sleeps;
  cpu.singleWait(Backend::SoftDevice);
  assert(cpu.sleeps == before && cpu.app_irq_current == cpu.app_irq_acknowledged);
}

static void waitIrqHistory() {
  Cpu cpu;
  cpu.app_irq_acknowledged = cpu.app_irq_current;
  cpu.injection = Point::DuringWait;
  cpu.singleWait(Backend::SoftDevice);
  assert(cpu.sleeps == 1 && cpu.work_pending && !cpu.slept_with_work);
  assert(cpu.app_irq_current != cpu.app_irq_acknowledged);
  const unsigned sleeps = cpu.sleeps;
  // The next acknowledgement cannot sleep or erase the ISR-published work.
  cpu.singleWait(Backend::SoftDevice);
  assert(cpu.sleeps == sleeps && cpu.work_pending);
  assert(cpu.app_irq_current == cpu.app_irq_acknowledged);
  cpu.service();
  assert(!cpu.work_pending && cpu.services == 1);
}

static void persistentDisabledIrq() {
  Cpu cpu;
  cpu.pendCryptoCell();
  for (unsigned n = 0; n < 100; ++n)
    cpu.iteration(Backend::SoftDevice, true, false, true, false, true, false);
  assert(cpu.cc_pending && cpu.sleeps == 0);
  // ER consumption and private SDK acknowledgements did not clear NVIC state.
  cpu.iteration(Backend::SoftDevice, true, false);
  assert(!cpu.cc_pending && cpu.sleeps == 1 && cpu.cc_clears == 1);
}

static void lateDisabledIrq() {
  Cpu cpu;
  cpu.prepare(Backend::SoftDevice);
  cpu.service();
  cpu.pendCryptoCell(); // New pending IRQ after all service/predicate checks.
  cpu.singleWait(Backend::SoftDevice);
  assert(cpu.cc_pending && cpu.sleeps == 0);
  cpu.iteration(Backend::SoftDevice, true, false);
  assert(!cpu.cc_pending && cpu.sleeps == 1);
}

static void preserveIrqOwner() {
  Cpu cpu;
  cpu.cc_enabled = true;
  cpu.pendCryptoCell();
  cpu.prepare(Backend::SoftDevice);
  assert(cpu.cc_pending && cpu.cc_clears == 0);
  cpu.singleWait(Backend::SoftDevice);
  assert(cpu.sleeps == 0 && cpu.cc_pending);
  // Only the enabled owner's ISR consumes its pending bit and publishes work.
  cpu.cc_pending = false;
  cpu.applicationIrq();
  cpu.iteration(Backend::SoftDevice, true, false);
  assert(cpu.services == 1 && !cpu.work_pending && cpu.sleeps == 1);
  assert(cpu.cc_clears == 0);

  Cpu other;
  other.other_irq_pending = true;
  for (unsigned n = 0; n < 3; ++n)
    other.iteration(Backend::SoftDevice, true, false);
  assert(other.other_irq_pending && other.sleeps == 0);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (!std::strcmp(argv[1], "scheduler")) scheduler();
  else if (!std::strcmp(argv[1], "entry_idle")) entryIdle();
  else if (!std::strcmp(argv[1], "poll_yield")) pollYield();
  else if (!std::strcmp(argv[1], "injections")) injections(true);
  else if (!std::strcmp(argv[1], "unobserved")) injections(false);
  else if (!std::strcmp(argv[1], "late_double")) lateDouble();
  else if (!std::strcmp(argv[1], "cleanup")) cleanup();
  else if (!std::strcmp(argv[1], "private_counter")) privateCounter();
  else if (!std::strcmp(argv[1], "sev_only")) sevOnlyEntry();
  else if (!std::strcmp(argv[1], "counter_wrap")) privateCounterWrap();
  else if (!std::strcmp(argv[1], "wait_irq_history")) waitIrqHistory();
  else if (!std::strcmp(argv[1], "disabled_irq")) persistentDisabledIrq();
  else if (!std::strcmp(argv[1], "late_disabled_irq")) lateDisabledIrq();
  else if (!std::strcmp(argv[1], "irq_owner")) preserveIrqOwner();
  else assert(false);
}
