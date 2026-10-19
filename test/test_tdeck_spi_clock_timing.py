"""Check the HIL-only ESP32-S3 radio clock phase experiment under C++11."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TDeckSpiClockTimingTests(unittest.TestCase):
    def test_all_dividers_preserve_frequency_and_correct_only_phase_fields(self):
        fixture = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include "TDeckSpiClockTiming.h"
int main() {
  for (uint32_t pre = 0; pre < 16; ++pre) {
    for (uint32_t n = 0; n < 64; ++n) {
      for (uint32_t h = 0; h < 64; ++h) {
        // Vary L independently too. Include reserved bits so a phase repair
        // cannot silently modify frequency, source selection or other fields.
        const uint32_t l = (h * 19 + 7) & 63;
        const uint32_t original = 0x12400000U | (pre << 18) | (n << 12) | (h << 6) | l;
        const uint32_t corrected = TDeckSpiClockTiming::corrected(original);
        assert((corrected & ~0x0fffU) == (original & ~0x0fffU));
        if (n == 0) assert(corrected == original);
        else {
          assert((corrected & 63) == n);
          assert(((corrected >> 6) & 63) == (n + 1) / 2 - 1);
          assert(TDeckSpiClockTiming::corrected(corrected) == corrected);
        }
        // The undivided source-clock mode must remain completely untouched.
        const uint32_t sourceClock = original | (1U << 31);
        assert(TDeckSpiClockTiming::corrected(sourceClock) == sourceClock);
      }
    }
  }
  // 8 MHz and 10 MHz can use period=2 plus a pre-divider. Their original
  // H=0,L=1 is already correct, so this correction is not a general speed fix.
  for (uint32_t pre : {4U, 3U, 2U}) {
    const uint32_t raw = (pre << 18) | (1U << 12) | 1U;
    assert(TDeckSpiClockTiming::corrected(raw) == raw);
  }
  // At 80 MHz source, /5 is 16 MHz but has 2 high and 3 low source ticks:
  // 25 ns/37.5 ns. /6 with no pre-divider has symmetric 37.5 ns phases.
  const uint32_t divide5 = TDeckSpiClockTiming::corrected((4U << 12) | 2U);
  const uint32_t divide6 = TDeckSpiClockTiming::corrected((5U << 12) | 3U);
  assert(((divide5 >> 6) & 63) + 1 == 2 && (divide5 & 63) == 4);
  assert(((divide6 >> 6) & 63) + 1 == 3 && (divide6 & 63) == 5);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "clock.cpp"
            binary = Path(directory) / ("clock.exe" if os.name == "nt" else "clock")
            cpp.write_text(fixture, encoding="ascii")
            built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++11",
                "-Wall", "-Wextra", "-I", str(ROOT / "tools/hil"), str(cpp), "-o", str(binary)],
                capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
