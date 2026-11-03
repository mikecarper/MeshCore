#include <cassert>
#include "HomeBeaconController.h"

using mesh::home_beacon::Controller;
using Action = Controller::Action;

int main() {
  Controller c;
  constexpr uint64_t t = 1700000010ULL; // exactly a slot boundary
  assert(c.evaluate(0, t, true).action == Action::None);
  assert(!c.synchronized(0, 0));
  assert(c.synchronized(100, t));
  assert(c.evaluate(100, t, false).action == Action::None);
  assert(c.evaluate(100, t, true).action == Action::None);
  assert(c.synchronized(100, t));
  auto d = c.evaluate(100, t, true);
  assert(d.action == Action::Update && d.slot == t / 30);
  // A failed BLE start doesn't claim to advertise; retry is requested.
  assert(c.evaluate(101, t, true).action == Action::Update);
  c.started(d.slot);
  assert(c.evaluate(102, t + 29, true).action == Action::None);
  assert(c.evaluate(103, t + 30, true).action == Action::Update);
  c.stopped();
  d = c.evaluate(104, t + 30, true);
  c.started(d.slot);
  assert(c.evaluate(105, t + 30, false).action == Action::Stop);
  c.stopped();
  assert(c.evaluate(106, t + 30, false).action == Action::None);
  // Reconnection requires a fresh successful SNTP response.
  assert(c.evaluate(107, t + 30, true).action == Action::None);
  assert(c.synchronized(100, t + 30));
  assert(c.evaluate(107, t + 30, true).action == Action::Update);
  c.started(uint32_t((t + 30) / 30));
  assert(c.evaluate(Controller::kSyncMaxAgeMs + 99, t + 40, true).action == Action::None);
  assert(c.evaluate(Controller::kSyncMaxAgeMs + 100, t + 40, true).action == Action::Stop);
  c.stopped();
  assert(c.evaluate(Controller::kSyncMaxAgeMs + 101, t + 40, true).action == Action::None);
  // Backward time stops the beacon until explicit trusted resynchronization.
  assert(c.synchronized(500, t + 100));
  d = c.evaluate(500, t + 100, true);
  c.started(d.slot);
  assert(c.evaluate(501, t + 99, true).action == Action::Stop);
  c.stopped();
  assert(c.evaluate(502, t + 100, true).action == Action::None);
  assert(!c.synchronized(503, t + 100) && c.clockRollback());
  c.resetClockTrust(); // only after trusted recovery, not Wi-Fi reconnect
  assert(c.synchronized(503, t + 100));
  assert(c.evaluate(503, t + 100, true).action == Action::Update);
  c.invalidateClock();
  assert(c.evaluate(504, t + 100, true).action == Action::None);
  assert(!c.synchronized(505, t + 99) && c.clockRollback());
  assert(!c.synchronized(506, t + 101));
  c.resetClockTrust();
  assert(c.synchronized(507, t + 101));
  // A backward NTP callback while advertising must stop, not relabel time.
  d = c.evaluate(507, t + 101, true);
  c.started(d.slot);
  assert(!c.synchronized(508, t + 100));
  assert(c.evaluate(508, t + 101, true).action == Action::Stop);
  c.stopped();
  // millis() wrapping does not extend the two-hour freshness limit.
  constexpr uint32_t wrap = UINT32_MAX - 100;
  c.resetClockTrust();
  assert(c.synchronized(wrap, t));
  d = c.evaluate(wrap + 1, t, true);
  c.started(d.slot);
  assert(c.evaluate(wrap + Controller::kSyncMaxAgeMs - 1, t, true).action == Action::None);
  assert(c.evaluate(wrap + Controller::kSyncMaxAgeMs, t, true).action == Action::Stop);
}
