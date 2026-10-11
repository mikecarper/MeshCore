#!/usr/bin/env python3
"""Execute production tracker routing decisions and its persisted byte format."""

from pathlib import Path
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <helpers/TrackerRoutePolicy.h>
using namespace mesh::tracker;
static unsigned checks = 0;
static void decision(const RouteState& state, uint64_t now, bool direct, RouteDecision expected) {
  RouteState next;
  assert(planRoute(state, now, direct, next) == expected);
  assert(isValidRouteState(next));
  ++checks;
}
static void repairCrc(uint8_t* image) {
  route_detail::put32(image + 28, route_detail::crc(image, 28));
}
int main() {
  const uint32_t start = 1000;
  RouteState empty, state, next;
  assert(isValidRouteState(empty));
  decision(empty, start, true, RouteDecision::Direct);
  assert(planRoute(empty, start, false, state) == RouteDecision::Wait);
  assert(state.outage_since == start && (state.flags & kOutageActive));
  for (uint32_t offset : {1u, 60u, 1800u, 21540u}) {
    assert(noteFailure(state, start + offset, next)); state = next;
    assert(state.outage_since == start);
  }
  decision(state, start + 21599, false, RouteDecision::Wait);
  decision(state, start + 21540, false, RouteDecision::Wait);
  decision(state, start + 21599, true, RouteDecision::Direct);
  decision(state, start + 21600, false, RouteDecision::Flood);
  decision(state, start + 21600, true, RouteDecision::Flood);
  assert(reserveFlood(state, start + 21600, next));
  assert(state.last_flood == 0 && next.last_flood == start + 21600);
  state = next;
  decision(state, start + 43199, false, RouteDecision::Wait);
  decision(state, start + 43200, false, RouteDecision::Flood);
  assert(!reserveFlood(state, start + 43199, next));
  assert(next.last_flood == state.last_flood);

  // A repeater delays the recovery gate, without restarting the target outage.
  RouteState repeater;
  assert(noteFailure(empty, start, repeater));
  assert(noteRepeater(repeater, start + 21599, next)); repeater = next;
  assert(repeater.outage_since == start);
  decision(repeater, start + 21600, false, RouteDecision::Wait);
  decision(repeater, start + 43198, false, RouteDecision::Wait);
  decision(repeater, start + 43199, false, RouteDecision::Flood);

  // The exact four-day boundary permanently suppresses automatic flooding.
  RouteState cutoff;
  assert(noteFailure(empty, start, cutoff));
  decision(cutoff, start + 342000, false, RouteDecision::Flood);
  decision(cutoff, start + 345599, false, RouteDecision::Flood);
  assert(planRoute(cutoff, start + 345600, false, next) == RouteDecision::Locked);
  cutoff = next;
  assert(cutoff.flags & kFloodLocked);
  decision(cutoff, start + 345600, true, RouteDecision::Direct);
  assert(!reserveFlood(cutoff, start + 345600, next));
  assert(noteRepeater(cutoff, start + 400000, next)); cutoff = next;
  assert(cutoff.outage_since == start && (cutoff.flags & kFloodLocked));
  assert(noteFailure(cutoff, start + 500000, next)); cutoff = next;
  decision(cutoff, start + 600000, false, RouteDecision::Locked);
  assert(noteAuthenticatedReply(cutoff, start + 600000, next)); cutoff = next;
  assert(!cutoff.flags && !cutoff.outage_since && cutoff.last_target_reply == start + 600000);
  decision(cutoff, start + 600001, true, RouteDecision::Direct);
  assert(noteFailure(cutoff, start + 600001, next)); cutoff = next;
  decision(cutoff, start + 621600, false, RouteDecision::Wait);
  decision(cutoff, start + 621601, false, RouteDecision::Flood);

  // A successful direct response cannot reset the global six-hour flood budget.
  assert(noteAuthenticatedReply(state, start + 21601, next)); state = next;
  assert(state.last_flood == start + 21600);
  assert(noteFailure(state, start + 21602, next)); state = next;
  decision(state, start + 43201, false, RouteDecision::Wait);
  decision(state, start + 43202, false, RouteDecision::Flood);

  // Invalid and backward clocks latch a conservative fault, including after
  // reboot. Hearing a repeater is never an unlock event.
  assert(!noteFailure(state, 0, next));
  assert(next.flags & kClockFault); RouteState clock = next;
  decision(clock, start + 80000, false, RouteDecision::InvalidClock);
  decision(clock, start + 80000, true, RouteDecision::Direct);
  assert(noteRepeater(clock, start + 80000, next)); clock = next;
  assert(clock.flags & kClockFault);
  assert(!noteAuthenticatedReply(clock, start + 79999, next));
  assert(next.flags & kClockFault);
  assert(noteAuthenticatedReply(clock, start + 80000, next));
  assert(!next.flags);
  decision(state, state.latest_observed - 1, true, RouteDecision::InvalidClock);
  decision(empty, uint64_t(UINT32_MAX) + 1, false, RouteDecision::InvalidClock);
  decision(empty, UINT64_MAX, true, RouteDecision::InvalidClock);
  RouteState upper;
  assert(noteFailure(empty, UINT32_MAX - 21600, upper));
  decision(upper, UINT32_MAX - 1, false, RouteDecision::Wait);
  decision(upper, UINT32_MAX, true, RouteDecision::Flood);
  assert(reserveFlood(upper, UINT32_MAX, next));
  decision(next, 1, false, RouteDecision::InvalidClock);

  // Explicit little-endian format, independently calculated by Python.
  RouteState golden;
  golden.flags = kOutageActive;
  golden.outage_since = 0x01020304;
  golden.last_target_reply = 0x01020300;
  golden.last_repeater = 0x01020305;
  golden.last_flood = 0x01020306;
  golden.latest_observed = 0x01020307;
  uint8_t image[kRouteStateImageSize], expected[] = {@GOLDEN@};
  assert(encodeRouteState(golden, image, sizeof(image)));
  assert(memcmp(image, expected, sizeof(image)) == 0);
  RouteState restored;
  assert(decodeRouteState(image, sizeof(image), restored));
  decision(restored, golden.latest_observed + 21600, false, RouteDecision::Flood);
  assert(!encodeRouteState(golden, nullptr, sizeof(image)));
  assert(!encodeRouteState(golden, image, sizeof(image) - 1));
  assert(!decodeRouteState(nullptr, sizeof(image), restored));
  assert(restored.flags & kClockFault);
  for (size_t length = 0; length < sizeof(image); ++length) {
    assert(!decodeRouteState(image, length, restored));
    assert(restored.flags & kClockFault); ++checks;
  }
  for (size_t position = 0; position < sizeof(image); ++position) {
    uint8_t damaged[kRouteStateImageSize]; memcpy(damaged, image, sizeof(image));
    damaged[position] ^= 1;
    assert(!decodeRouteState(damaged, sizeof(damaged), restored)); ++checks;
  }
  for (uint8_t position : {3, 4, 5, 6, 7}) {
    uint8_t damaged[kRouteStateImageSize]; memcpy(damaged, image, sizeof(image));
    damaged[position] = position == 3 ? 2 : (position == 4 ? 0x80 : 1); repairCrc(damaged);
    assert(!decodeRouteState(damaged, sizeof(damaged), restored)); ++checks;
  }
  RouteState invalid = golden;
  invalid.outage_since = 0; assert(!isValidRouteState(invalid));
  assert(!encodeRouteState(invalid, image, sizeof(image)));
  decision(invalid, start, false, RouteDecision::InvalidState);
  invalid = golden; invalid.last_target_reply = golden.latest_observed;
  assert(!isValidRouteState(invalid));
  invalid = golden; invalid.last_repeater = golden.latest_observed + 1;
  assert(!isValidRouteState(invalid));
  invalid = empty; invalid.flags = kFloodLocked; assert(!isValidRouteState(invalid));
  invalid = empty; invalid.last_flood = 1; assert(!isValidRouteState(invalid));
  printf("PASS: %u tracker route boundary and serialization checks\n", checks);
}
'''


class TrackerRoutePolicyTest(unittest.TestCase):
    def test_real_route_policy_and_explicit_state_image(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++ compiler is required")
        payload = b"TRP\x01\x01\0\0\0" + struct.pack(
            "<5I", 0x01020304, 0x01020300, 0x01020305, 0x01020306, 0x01020307)
        golden = payload + struct.pack("<I", zlib.crc32(payload))
        source_text = HARNESS.replace("@GOLDEN@", ",".join(map(str, golden)))
        sanitizers = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []
        with tempfile.TemporaryDirectory(prefix="meshcore-tracker-route-") as temporary:
            work = Path(temporary)
            source = work / "route.cpp"
            source.write_text(source_text)
            binary = work / "route"
            built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                    *sanitizers, "-I" + str(ROOT / "src"), str(source), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("tracker route boundary and serialization checks", checked.stdout)
            print(checked.stdout.strip())


if __name__ == "__main__":
    unittest.main()
