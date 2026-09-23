"""Exercise the production profile clock on and off both nRF52 and other boards."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
CLOCK = "static void syncProfileClock(bool dual_profile) {" + SOURCE.split(
    "static void syncProfileClock(bool dual_profile) {", 1
)[1].split("// this function is called", 1)[0]

HARNESS = r'''
#include <cassert>
#include <cstdint>
struct FakeRtc {
  uint32_t TASKS_STOP = 0, PRESCALER = 9, TASKS_CLEAR = 0, TASKS_START = 0;
  uint32_t COUNTER = 0;
} rtc;
#define NRF_RTC2 (&rtc)
uint32_t micros() { return 1234; }
@CLOCK@
int main() {
  assert(profileTimestamp(false) == 1234);
  assert(rtc.TASKS_START == 0 && rtc.TASKS_STOP == 0);
  assert(profileElapsedUs(12, 42, false) == 30);
#if defined(NRF52_PLATFORM)
  rtc.COUNTER = 100;
  assert(profileTimestamp(true) == 100);
  assert(rtc.TASKS_STOP == 1 && rtc.PRESCALER == 0);
  assert(rtc.TASKS_CLEAR == 1 && rtc.TASKS_START == 1);
  rtc.TASKS_CLEAR = rtc.TASKS_START = rtc.TASKS_STOP = 0;
  assert(profileTimestamp(true) == 100);
  assert(rtc.TASKS_CLEAR == 0 && rtc.TASKS_START == 0 && rtc.TASKS_STOP == 0);
  assert(profileElapsedUs(0, 32768, true) == 1000000);
  assert(profileElapsedUs(0x00fffff0, 0x10, true) == 977);
  syncProfileClock(false);
  assert(rtc.TASKS_STOP == 1 && profileTimestamp(false) == 1234);
  rtc.TASKS_CLEAR = rtc.TASKS_START = rtc.TASKS_STOP = 0;
  syncProfileClock(false);
  assert(rtc.TASKS_CLEAR == 0 && rtc.TASKS_START == 0 && rtc.TASKS_STOP == 0);
  rtc.COUNTER = 200;
  assert(profileTimestamp(true) == 200);
  assert(rtc.TASKS_CLEAR == 1 && rtc.TASKS_START == 1);
#else
  assert(profileTimestamp(true) == 1234);
  assert(profileElapsedUs(12, 42, true) == 30);
  assert(rtc.TASKS_START == 0 && rtc.TASKS_STOP == 0);
#endif
}
'''


class ProfileClockTest(unittest.TestCase):
    def test_clock_selection_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as folder:
            cpp = Path(folder) / "test.cpp"
            exe = Path(folder) / "test.exe"
            cpp.write_text(HARNESS.replace("@CLOCK@", CLOCK))
            for defines in ([], ["-DNRF52_PLATFORM=1"]):
                result = subprocess.run(
                    [os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra",
                     "-Werror", *defines, str(cpp), "-o", str(exe)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run([str(exe)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
