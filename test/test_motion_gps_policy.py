"""Motion GPS throttle and verified-quiet behavior, including millis wrap."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MotionGpsPolicyTests(unittest.TestCase):
    def test_policy(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ unavailable")
        code = r"""
#include <cassert>
#include <cstdint>
#include "helpers/tracker/MotionGpsPolicy.h"
using namespace mesh::tracker::motion;
int main() {
  GpsPolicy gps;
  gps.noteMotion(); assert(gps.due(0xffff0000));
  gps.started(); assert(!gps.pending());
  gps.completed(0xffff0000, true);
  gps.noteMotion(); assert(!gps.due(0xffff0000));
  assert(!gps.due(uint32_t(0xffff0000u + GpsPolicy::kNormalDelayMs - 1)));
  assert(gps.due(uint32_t(0xffff0000u + GpsPolicy::kNormalDelayMs)));
  uint32_t t = 10000;
  gps.started(); gps.completed(t, false); gps.noteMotion();
  assert(!gps.due(t + 3600000 - 1)); assert(gps.due(t + 3600000));
  t += 3600000; gps.started(); gps.completed(t, false); gps.noteMotion();
  assert(!gps.due(t + 7200000 - 1)); assert(gps.due(t + 7200000));
  t += 7200000; gps.started(); gps.completed(t, false); gps.noteMotion();
  assert(!gps.due(t + 14400000 - 1)); assert(gps.due(t + 14400000));
  t += 14400000; gps.started(); gps.completed(t, false); gps.noteMotion();
  assert(!gps.due(t + 21600000 - 1)); assert(gps.due(t + 21600000));
  // Any successful manual/scheduled owner fix clears failure backoff.
  gps.started(); gps.completed(t, true); gps.noteMotion();
  assert(gps.due(t + GpsPolicy::kNormalDelayMs));

  // Profile changes update future successful-fix pacing immediately.
  GpsPolicy profiles;
  profiles.started(); profiles.completed(1000, true); profiles.noteMotion();
  profiles.setHiking(true, 1000 + 60000);
  assert(!profiles.due(1000 + GpsPolicy::kHikingDelayMs - 1));
  assert(profiles.due(1000 + GpsPolicy::kHikingDelayMs));
  // A due acquisition cannot be postponed by switching back to backyard.
  profiles.setHiking(false, 1000 + GpsPolicy::kHikingDelayMs);
  assert(profiles.due(1000 + GpsPolicy::kHikingDelayMs));
  profiles.started(); profiles.completed(2000, true); profiles.noteMotion();
  profiles.setHiking(true, 2001);
  profiles.setHiking(false, 3000);
  assert(!profiles.due(2000 + GpsPolicy::kNormalDelayMs - 1));
  assert(profiles.due(2000 + GpsPolicy::kNormalDelayMs));
  // A profile change must never bypass exponential no-fix backoff.
  profiles.started(); profiles.completed(4000, false); profiles.noteMotion();
  profiles.setHiking(true, 4001);
  assert(!profiles.due(4000 + GpsPolicy::kInitialFailureDelayMs - 1));
  assert(profiles.due(4000 + GpsPolicy::kInitialFailureDelayMs));
  profiles.started(); profiles.completed(0xffff0000u, true); profiles.noteMotion();
  profiles.setHiking(false, 0xffff0001u);
  assert(!profiles.due(uint32_t(0xffff0000u + GpsPolicy::kNormalDelayMs - 1)));
  assert(profiles.due(uint32_t(0xffff0000u + GpsPolicy::kNormalDelayMs)));

  QuietPolicy quiet;
  assert(!quiet.observe(Observation::Unknown, 0));
  assert(!quiet.confirmed());
  assert(!quiet.observe(Observation::NoMotionObserved, 0xffff0000));
  assert(!quiet.observe(Observation::NoMotionObserved,
                        uint32_t(0xffff0000u + QuietPolicy::kQuietMs)));
  assert(quiet.confirmed());
  assert(quiet.observe(Observation::Motion,
                       uint32_t(0xffff0000u + QuietPolicy::kQuietMs + 1)));
  assert(!quiet.confirmed());
  // Short quiet spells cannot repeatedly trigger early owner traffic.
  uint32_t q = uint32_t(0xffff0000u + QuietPolicy::kQuietMs + 2);
  quiet.observe(Observation::NoMotionObserved, q);
  quiet.observe(Observation::NoMotionObserved, q + QuietPolicy::kQuietMs);
  assert(!quiet.observe(Observation::Motion, q + QuietPolicy::kQuietMs + 1));
  quiet.observe(Observation::NoMotionObserved, q + 2 * QuietPolicy::kQuietMs);
  quiet.observe(Observation::Unknown, q + 3 * QuietPolicy::kQuietMs);
  assert(!quiet.confirmed());
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "test.cpp"
            binary = Path(tmp) / "test"
            source.write_text(code)
            subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "src"), str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
