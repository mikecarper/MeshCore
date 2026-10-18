#include <cassert>
#include <cstdio>
#include <helpers/WebConfigLoginThrottle.h>

using namespace WebConfigLoginThrottle;

static const uint32_t A = 0x0204A8C0, B = 0x0304A8C0;

static void fail(Tracker& t, uint32_t client, uint32_t now, int times = 1) {
  for (int i = 0; i < times; i++) {
    assert(t.check(client, now) == ALLOW);
    t.noteFailure(client, now);
  }
}

int main() {
  // One address at its limit is locked on its own; others still get a try.
  {
    Tracker t; uint32_t now = 100000;
    fail(t, A, now, kClientFails - 1);
    assert(t.check(A, now) == ALLOW);
    t.noteFailure(A, now);
    assert(t.check(A, now) == CLIENT_LOCKED);
    assert(t.check(B, now) == ALLOW);
    t.noteSuccess(B);
    assert(t.check(A, now) == CLIENT_LOCKED);  // another client's success is not A's
    assert(t.check(A, now + kClientLockMs - 1) == CLIENT_LOCKED);
    assert(t.check(A, now + kClientLockMs) == ALLOW);
    fail(t, A, now + kClientLockMs, kClientFails - 1);  // the count restarted
    assert(t.check(A, now + kClientLockMs) == ALLOW);
  }
  // A success clears only that client's own count.
  {
    Tracker t; uint32_t now = 100000;
    fail(t, A, now, kClientFails - 1);
    t.noteSuccess(A);
    now += kClientLockMs;  // let the shared budget refill
    fail(t, A, now, kClientFails - 1);
    assert(t.check(A, now) == ALLOW);
  }
  // A client failing as fast as it is allowed never empties the shared budget,
  // so another address is never refused, while its own rate stays at the cap.
  {
    Tracker t; unsigned failures = 0;
    for (uint32_t now = 100000; now < 100000 + 3600000; now += 100) {
      if (t.check(A, now) == ALLOW) { t.noteFailure(A, now); failures++; }
      assert(t.check(B, now) == ALLOW);
    }
    printf("one address, 1 h: %u failures\n", failures);
    assert(failures <= kClientFails * (3600000 / kClientLockMs + 1));
  }
  // Changing address on every attempt is bounded by the shared budget: a
  // burst of kSharedBudget, then one failure per kSharedRefillMs.
  {
    Tracker t; uint32_t ip = 1; unsigned failures = 0;
    const uint32_t start = 100000;
    for (int i = 0; i < kSharedBudget; i++) fail(t, ip++, start);
    assert(t.check(ip, start) == SHARED_LOCKED);
    assert(t.check(ip, start + kSharedRefillMs - 1) == SHARED_LOCKED);
    fail(t, ip++, start + kSharedRefillMs);
    assert(t.check(ip, start + kSharedRefillMs) == SHARED_LOCKED);
    for (uint32_t now = start + kSharedRefillMs; now < start + 3600000; now += 10) {
      if (t.check(ip, now) == ALLOW) { t.noteFailure(ip++, now); failures++; }
    }
    failures += kSharedBudget + 1;
    printf("new address per attempt, 1 h: %u failures\n", failures);
    assert(failures <= kSharedBudget + 3600000 / kSharedRefillMs);
  }
  // The more specific answer wins when both limits apply.
  {
    Tracker t; uint32_t now = 100000;
    fail(t, A, now, kClientFails);
    fail(t, B, now, kSharedBudget - kClientFails);
    assert(t.check(A, now) == CLIENT_LOCKED);
    assert(t.check(B, now) == SHARED_LOCKED);
  }
  // Locks are measured as elapsed time, so an old lock does not come back
  // when millis() is half a wrap past it.
  {
    Tracker t; uint32_t now = 100000;
    fail(t, A, now, kClientFails);
    fail(t, B, now, kSharedBudget - kClientFails);
    now += 0x80000000u + 60000u;
    assert(t.check(A, now) == ALLOW);
    assert(t.check(B, now) == ALLOW);
  }
  // A lock started just before UINT32_MAX remains active across rollover and
  // expires at exactly thirty seconds, then gives the client a fresh count.
  {
    Tracker t; const uint32_t start = UINT32_MAX - 1000u;
    fail(t, A, start, kClientFails);
    assert(t.check(A, UINT32_MAX) == CLIENT_LOCKED);
    assert(t.check(A, 0) == CLIENT_LOCKED);
    assert(t.check(B, 0) == ALLOW);
    assert(t.check(A, start + kClientLockMs - 1) == CLIENT_LOCKED);
    fail(t, A, start + kClientLockMs, kClientFails);
    assert(t.check(A, start + kClientLockMs) == CLIENT_LOCKED);
  }
  // The shared allowance also refills across rollover, once per six seconds.
  {
    Tracker t; const uint32_t start = UINT32_MAX - 3000u;
    uint32_t ip = 1;
    for (int i = 0; i < kSharedBudget; i++) fail(t, ip++, start);
    assert(t.check(ip, UINT32_MAX) == SHARED_LOCKED);
    assert(t.check(ip, 0) == SHARED_LOCKED);
    assert(t.check(ip, start + kSharedRefillMs - 1) == SHARED_LOCKED);
    fail(t, ip++, start + kSharedRefillMs);
    assert(t.check(ip, start + 2 * kSharedRefillMs - 1) == SHARED_LOCKED);
    fail(t, ip++, start + 2 * kSharedRefillMs);
    assert(t.check(ip, start + 2 * kSharedRefillMs) == SHARED_LOCKED);
  }
  // Failures recorded without a check saturate instead of wrapping.
  {
    Tracker t; uint32_t now = 100000;
    for (int i = 0; i < 256; i++) t.noteFailure(A, now);
    assert(t.check(A, now) == CLIENT_LOCKED);
    assert(t.check(B, now + kSharedRefillMs - 1) == SHARED_LOCKED);
    assert(t.check(B, now + kSharedRefillMs) == ALLOW);
  }
  // A failure recorded later without a check first still counts in full.
  {
    Tracker t; uint32_t now = 100000;
    t.noteFailure(1, now);
    t.noteFailure(2, now += 60000);
    for (uint32_t ip = 3; ip < 3 + kSharedBudget - 1; ip++) fail(t, ip, now);
    assert(t.check(A, now) == SHARED_LOCKED);
  }
  // A free slot is reused before an in-use one; otherwise the slot with the
  // oldest failure is evicted.
  {
    Tracker t; uint32_t now = 100000;
    fail(t, 1, now);
    fail(t, 2, now += kSharedRefillMs);
    fail(t, 3, now += kSharedRefillMs);
    fail(t, 4, now += kSharedRefillMs);
    t.noteSuccess(4);
    fail(t, 5, now += kSharedRefillMs);  // takes 4's freed slot; 1 keeps its count
    for (int i = 0; i < kClientFails - 1; i++) fail(t, 1, now += kSharedRefillMs);
    assert(t.check(1, now) == CLIENT_LOCKED);
    fail(t, 6, now += kSharedRefillMs);  // all in use: evicts 2, the oldest
    for (int i = 0; i < kClientFails - 1; i++) fail(t, 2, now += kSharedRefillMs);
    assert(t.check(2, now) == ALLOW);
  }
  puts("WebConfig login throttle policy checks passed");
}
