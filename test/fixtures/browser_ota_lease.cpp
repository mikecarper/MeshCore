#include <cassert>
#include <cstdint>
#include <helpers/esp32/BrowserOtaLease.h>

using mesh::ota::BrowserOtaAuthority;
using mesh::ota::BrowserOtaLease;

int main() {
  uint8_t first[16] = {1};
  uint8_t second[16] = {2};
  BrowserOtaAuthority good;
  good.lan = good.same_origin = good.explicit_login = true;
  good.credential_generation = 7;
  good.session = first;
  good.session_size = sizeof(first);
  BrowserOtaLease lease;
  assert(!lease.claim(10, good));
  assert(!lease.arm(false, 10, 1000));
  assert(!lease.arm(true, 10, 0));
  assert(!lease.arm(true, 10, BrowserOtaLease::kMaxDurationMs + 1));
  assert(lease.arm(true, 10, 1000));

  BrowserOtaAuthority bad = good;
  bad.lan = false;
  assert(!lease.claim(20, bad));
  bad = good; bad.same_origin = false;
  assert(!lease.claim(20, bad));
  bad = good; bad.explicit_login = false;
  assert(!lease.claim(20, bad));
  bad = good; bad.session_size = 15;
  assert(!lease.claim(20, bad));
  bad = good; bad.session = nullptr;
  assert(!lease.claim(20, bad));
  assert(lease.claim(20, good));
  assert(!lease.claim(21, good));
  assert(lease.permits(1019, good));
  bad = good; bad.same_origin = false;
  assert(!lease.permits(102, bad));
  bad = good; bad.explicit_login = false;
  assert(!lease.permits(102, bad));
  bad = good; bad.lan = false;
  assert(!lease.permits(102, bad));
  bad = good; bad.session = second;
  assert(!lease.permits(102, bad));
  assert(lease.permits(103, good));
  assert(!lease.permits(1020, good));
  assert(!lease.permits(1021, good));

  assert(lease.arm(true, 100, 1000));
  assert(!lease.claim(100 + BrowserOtaLease::kClaimWindowMs, good));
  assert(!lease.claim(101, good));

  // Deadline arithmetic must remain valid across the uint32 millis rollover.
  const uint32_t start = UINT32_MAX - 99;
  assert(lease.arm(true, start, 1000));
  assert(!lease.claim(start + BrowserOtaLease::kClaimWindowMs, good));
  assert(lease.arm(true, start, 1000));
  assert(lease.claim(start + 1, good));
  assert(lease.permits(start + 1000, good));
  assert(!lease.permits(start + 1001, good));
  assert(lease.arm(true, start, 1000));
  assert(lease.claim(start + 1, good));
  bad = good; ++bad.credential_generation;
  assert(!lease.permits(start + 2, bad));
  assert(!lease.permits(start + 3, good));
  assert(lease.arm(true, 200, 1000));
  assert(lease.claim(201, good));
  lease.revoke();
  assert(!lease.permits(202, good));
}
