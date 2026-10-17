// Execute the real tracker wake helper against a one-shot timer daemon.
// A successful command wakes the higher-priority daemon and emits a scheduler
// event; merely reading the active deadline does not. No event-clear primitive
// is exposed to the extracted helper.
#include <cassert>
#include <cstdint>
#include <limits>

using TickType_t = uint32_t;
struct Timer;
using TimerHandle_t = Timer*;
struct Timer {
  bool active = false;
  TickType_t expiry = 0, period = 0;
  void (*callback)(TimerHandle_t) = nullptr;
} timer;

constexpr int pdPASS = 1, pdFALSE = 0;
static bool allocate_ok = true, queue_ok = true, event_latched = false;
static bool expire_after_active_read = false, deferred_expiry = false;
static bool app_irq_after_unlock = false, application_work = false;
static TickType_t now_ticks = 0;
static unsigned begins = 0, commands = 0, callbacks = 0, active_reads = 0;
static unsigned expiry_reads = 0, tick_reads = 0, sleeps = 0, short_returns = 0;
static unsigned critical_depth = 0, max_critical_depth = 0;

static void expireTimer() {
  assert(timer.active && critical_depth == 0);
  timer.active = false; // One-shot becomes dormant before its no-op callback.
  ++callbacks;
  timer.callback(&timer);
  event_latched = true;
}
static void taskENTER_CRITICAL() {
  ++critical_depth;
  if (critical_depth > max_critical_depth) max_critical_depth = critical_depth;
}
static void taskEXIT_CRITICAL() {
  assert(critical_depth);
  if (--critical_depth == 0) {
    if (deferred_expiry) {
      deferred_expiry = false;
      now_ticks = timer.expiry; // Pending RTC tick is delivered after unmask.
      expireTimer();
    }
    if (app_irq_after_unlock) {
      app_irq_after_unlock = false;
      application_work = event_latched = true;
    }
  }
}
static int xTimerIsTimerActive(TimerHandle_t handle) {
  assert(handle == &timer && critical_depth == 1);
  // The real API has its own nested critical section.
  taskENTER_CRITICAL();
  ++active_reads;
  const bool active = timer.active;
  if (expire_after_active_read) {
    assert(active);
    expire_after_active_read = false;
    deferred_expiry = true; // Daemon cannot invalidate the compound snapshot.
  }
  taskEXIT_CRITICAL();
  return active;
}
static TickType_t xTimerGetExpiryTime(TimerHandle_t handle) {
  assert(handle == &timer && critical_depth == 1 && timer.active);
  ++expiry_reads;
  return timer.expiry;
}
static TickType_t xTaskGetTickCount() {
  assert(critical_depth == 1);
  ++tick_reads;
  return now_ticks;
}
static TickType_t pdMS_TO_TICKS(uint32_t milliseconds) {
  return static_cast<TickType_t>((uint64_t(milliseconds) * 1024) / 1000);
}
static int xTimerChangePeriod(TimerHandle_t handle, TickType_t ticks, int wait) {
  assert(handle == &timer && critical_depth == 0 && ticks && wait == 0);
  ++commands;
  if (!queue_ok) return 0;
  timer.period = ticks;
  timer.expiry = static_cast<TickType_t>(now_ticks + ticks);
  timer.active = true;
  event_latched = true; // Queue unblocks timer-priority2 above loop-priority1.
  return pdPASS;
}
class SoftwareTimer {
  TimerHandle_t handle_ = nullptr;
 public:
  TimerHandle_t getHandle() { return handle_; }
  void begin(uint32_t period, void (*callback)(TimerHandle_t), void* id, bool repeat) {
    assert(period == 1000 && callback && !id && !repeat);
    ++begins;
    if (allocate_ok) {
      handle_ = &timer;
      timer.callback = callback;
    }
  }
};
static SoftwareTimer tracker_wake_timer;
static void trackerWakeCallback(TimerHandle_t) {}

@ARM@

static bool attempt(uint32_t seconds) {
  const bool ready = armTrackerSleepWake(seconds);
  assert(critical_depth == 0);
  if (ready) {
    if (event_latched) { event_latched = false; ++short_returns; }
    else { assert(timer.active && !application_work); ++sleeps; }
  }
  return ready;
}
static void reset() {
  tracker_wake_timer = SoftwareTimer();
  timer = Timer();
  allocate_ok = queue_ok = true;
  event_latched = expire_after_active_read = deferred_expiry = false;
  app_irq_after_unlock = application_work = false;
  now_ticks = 100;
  begins = commands = callbacks = active_reads = expiry_reads = tick_reads = 0;
  sleeps = short_returns = critical_depth = max_critical_depth = 0;
}

static void idleReuse() {
  reset();
  assert(attempt(30) && commands == 1 && short_returns == 1);
  const TickType_t deadline = timer.expiry;
  for (unsigned i = 0; i < 1000; ++i) {
    ++now_ticks;
    assert(attempt(30));
    assert(commands == 1 && "idle timer poll must not requeue and relatch scheduler event");
    assert(timer.expiry == deadline && !event_latched);
  }
  assert(sleeps == 1000 && begins == 1 && max_critical_depth == 2);
}
static void changedDeadlines() {
  reset();
  assert(attempt(30));
  ++now_ticks;
  assert(attempt(7) && commands == 2 && timer.period == pdMS_TO_TICKS(7000));
  const TickType_t earlier = timer.expiry;
  assert(attempt(30) && commands == 2 && timer.expiry == earlier);
  assert(attempt(7) && commands == 2);
  assert(attempt(3) && commands == 3 && timer.period == pdMS_TO_TICKS(3000));
  now_ticks = timer.expiry - 1;
  assert(attempt(1) && commands == 3); // Existing earlier wake is sufficient.
}
static void expiryAndRearm() {
  reset();
  assert(attempt(1));
  now_ticks = timer.expiry;
  expireTimer();
  event_latched = false; // Next loop entry acknowledges the completed callback.
  assert(attempt(1) && commands == 2 && timer.active && callbacks == 1);
  now_ticks = timer.expiry; // Due but daemon has not removed it yet.
  assert(attempt(1) && commands == 3);
  now_ticks = timer.expiry + 1; // An overdue active deadline is not reusable.
  assert(attempt(1) && commands == 4);
}
static void failureAndRecovery() {
  reset();
  assert(!attempt(0) && !attempt(31) && begins == 0 && commands == 0);
  allocate_ok = false;
  assert(!attempt(30) && commands == 0 && critical_depth == 0);
  allocate_ok = true; queue_ok = false;
  assert(!attempt(30) && commands == 1 && !timer.active);
  queue_ok = true;
  assert(attempt(30) && commands == 2);
  const TickType_t old_deadline = timer.expiry;
  queue_ok = false;
  assert(!attempt(3) && commands == 3 && timer.expiry == old_deadline);
  queue_ok = true;
  assert(attempt(3) && commands == 4);
  queue_ok = false;
  assert(attempt(30) && commands == 4); // Reuse needs no free command-queue slot.
}
static void tickWrap() {
  reset();
  now_ticks = std::numeric_limits<TickType_t>::max() - 500;
  assert(attempt(1) && timer.expiry == 523);
  now_ticks = std::numeric_limits<TickType_t>::max() - 100;
  assert(attempt(1) && commands == 1);
  now_ticks = 500;
  assert(attempt(1) && commands == 1);
  now_ticks = 523;
  assert(attempt(1) && commands == 2);
  now_ticks = timer.expiry + 1;
  assert(attempt(1) && commands == 3);
}
static void expiryAfterSnapshot() {
  reset();
  assert(attempt(30));
  now_ticks = timer.expiry - 1;
  expire_after_active_read = true;
  assert(attempt(30));
  assert(callbacks == 1 && !timer.active && short_returns == 2 && commands == 1);
  assert(attempt(30) && timer.active && commands == 2); // Same period still rearms.
  assert(attempt(30) && sleeps == 1 && commands == 2);
}
static void applicationIrqAfterSnapshot() {
  reset();
  assert(attempt(30));
  app_irq_after_unlock = true;
  assert(attempt(30) && application_work && short_returns == 2 && commands == 1);
  application_work = false; // Following service pass handles the late IRQ.
  assert(attempt(30) && sleeps == 1 && commands == 1);
}

int main() {
  idleReuse();
  changedDeadlines();
  expiryAndRearm();
  failureAndRecovery();
  tickWrap();
  expiryAfterSnapshot();
  applicationIrqAfterSnapshot();
}
