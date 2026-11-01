#!/usr/bin/env python3
"""Run the allocation-free OTA receive cadence helper with real C++ arithmetic."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <helpers/ota/OtaReceiveCadence.h>
using mesh::ota::OtaReceiveCadence;

static void baseline_and_slow_source() {
  OtaReceiveCadence c;
  assert(c.serviceIntervalMs(500) == 500);
  assert(c.serviceIntervalMs(0) == 0);
  assert(!c.observe(8, 0, 0));
  assert(c.observe(8, 1, 2000));
  assert(c.serviceIntervalMs(500) == 2000);
  assert(c.serviceIntervalMs(2500) == 2500);
  assert(c.observe(8, 2, 6000));
  assert(c.serviceIntervalMs(500) == 4000); // react before the next retry
  c.reset();
  assert(c.serviceIntervalMs(500) == 500);
  assert(!c.observe(8, 3, 9000)); // reset does not preserve a timestamp anchor
}

static void clean_recovery_and_bounded_pause() {
  OtaReceiveCadence c;
  assert(!c.observe(0, 0, 0));
  assert(c.observe(0, 1, 30000));
  uint32_t now = 30000;
  uint32_t previous = 30000;
  for (unsigned flight = 0; flight < 3; ++flight) {
    c.beginFlight();
    assert(!c.observe(flight + 1, 0, now));
    for (uint8_t fragment = 1; fragment < 16; ++fragment) {
      now += 1000;
      assert(c.observe(flight + 1, fragment, now));
      uint32_t estimate = c.serviceIntervalMs(0);
      assert(estimate >= 1000 && estimate <= previous);
      previous = estimate;
    }
  }
  assert(c.serviceIntervalMs(0) == 1000); // a rare pause cannot pin the estimate
  assert(c.serviceIntervalMs(1400) == 1400);
}

static void duplicates_backward_and_skipped_fragments() {
  OtaReceiveCadence c;
  assert(!c.observe(3, 0, 0));
  assert(c.observe(3, 1, 100));
  assert(!c.observe(3, 1, 199));
  assert(c.observe(3, 2, 200));
  assert(c.serviceIntervalMs(0) == 100);
  assert(!c.observe(3, 0, 299));
  assert(c.observe(3, 3, 300));
  assert(c.serviceIntervalMs(0) == 100);
  assert(!c.observe(3, 5, 1000)); // missing index is not a clean interval
  assert(c.serviceIntervalMs(0) == 100);
  assert(c.observe(3, 6, 1100));
  assert(!c.observe(3, 16, 1199));
  assert(c.observe(3, 7, 1200));
  assert(c.serviceIntervalMs(0) == 100);
}

static void flight_boundaries_and_interleaving() {
  OtaReceiveCadence c;
  assert(!c.observe(1, 0, 0));
  assert(!c.observe(2, 0, 100));
  assert(c.observe(1, 1, 1000));
  assert(c.observe(2, 1, 1100));
  assert(c.serviceIntervalMs(0) == 1000);
  c.beginFlight(); // proof, CLI and idle time is outside DATA cadence
  assert(!c.observe(3, 0, 1000000000));
  assert(c.serviceIntervalMs(0) == 1000);
  assert(c.observe(3, 1, 1000001000));
  assert(!c.observe(4, 0, 1000050000)); // another block has its own first timestamp
  assert(c.serviceIntervalMs(0) == 1000);
  assert(c.observe(4, 1, 1000051000));
  assert(c.serviceIntervalMs(0) == 1000);
  assert(!c.observe(5, 0, 1000060000)); // bounded anchor eviction is harmless
  assert(!c.observe(3, 2, 1000061000));
  assert(c.serviceIntervalMs(0) == 1000);
}

static void wrap_zero_and_outliers() {
  OtaReceiveCadence c;
  assert(!c.observe(2, 0, UINT32_MAX - 49));
  assert(c.observe(2, 1, 50));
  assert(c.serviceIntervalMs(0) == 100);
  c.reset();
  assert(!c.observe(2, 0, UINT32_MAX - 99));
  assert(c.observe(2, 1, 0));
  assert(c.serviceIntervalMs(0) == 100);
  assert(!c.observe(2, 2, 0));
  assert(c.observe(2, 3, 100));
  assert(c.serviceIntervalMs(0) == 100);
  c.reset();
  assert(!c.observe(2, 0, 100));
  assert(!c.observe(2, 1, 100 + OtaReceiveCadence::MaxSampleMs + 1));
  assert(c.serviceIntervalMs(500) == 500);
  assert(c.observe(2, 2, 200 + OtaReceiveCadence::MaxSampleMs + 1));
  assert(c.serviceIntervalMs(0) == 100);
  c.reset();
  assert(!c.observe(2, 0, 1000));
  assert(!c.observe(2, 1, 900)); // not millis wrap: enormous unsigned elapsed
  assert(c.observe(2, 2, 1000));
  assert(c.serviceIntervalMs(0) == 100);
  assert(c.serviceIntervalMs(UINT32_MAX) == UINT32_MAX);
  c.reset();
  assert(!c.observe(UINT32_MAX, 0, 0));
  assert(!c.observe(3, 0, 0));
  assert(c.observe(3, 1, OtaReceiveCadence::MaxSampleMs));
  assert(c.serviceIntervalMs(0) == OtaReceiveCadence::MaxSampleMs);
}

int main() {
  static_assert(sizeof(OtaReceiveCadence) <= 40, "no per-fragment allocation");
  baseline_and_slow_source();
  clean_recovery_and_bounded_pause();
  duplicates_backward_and_skipped_fragments();
  flight_boundaries_and_interleaving();
  wrap_zero_and_outliers();
  std::puts("PASS: physical floor, pacing, recovery, flight isolation and rollover");
}
'''


class OtaReceiveCadenceTest(unittest.TestCase):
    def test_production_helper(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler, 'a host C++ compiler is required')
        with tempfile.TemporaryDirectory(prefix='ota-receive-cadence-') as directory:
            work = Path(directory)
            cpp, binary = work/'cadence.cpp', work/'cadence'
            cpp.write_text(HARNESS, encoding='ascii')
            sanitizers = (['-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                           '-fno-pie', '-no-pie'] if sys.platform.startswith('linux') else [])
            built = subprocess.run([compiler, '-std=c++11', '-Wall', '-Wextra', '-Werror',
                *sanitizers, '-I'+str(ROOT/'src'), str(cpp), '-o', str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stdout+ran.stderr)
            self.assertIn('PASS:', ran.stdout)


if __name__ == '__main__': unittest.main()
