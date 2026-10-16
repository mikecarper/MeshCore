"""Exercise the actual tiny UI status-bar header across clock rollover."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CXX = shutil.which("g++")

DRIVER = r'''#pragma once
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
extern uint32_t now_ms;
inline uint32_t millis() { return now_ms; }
namespace UIColor { constexpr unsigned primary_txt = 1; }
struct DisplayDriver {
  int width = 200;
  unsigned prints = 0;
  int last_x = 0;
  void setTextSize(int) {}
  int getTextWidth(const char*) { return width; }
  void setColor(unsigned) {}
  void setCursor(int x, int) { last_x = x; }
  void print(const char*) { ++prints; }
};
'''

FIXTURE = r'''#include <ScrollingStatusBar.h>
#include <cstdlib>
uint32_t now_ms = 0;

int main(int argc, char** argv) {
  assert(argc == 2);
  const int mode = std::atoi(argv[1]);
  ScrollingStatusBar bar;
  DisplayDriver display;
  auto update = [&](const char* name) { bar.update(display, name, 4000, false, false, true); };
  if (mode == 1) {
    now_ms = 0xfffffff0U;
    bar.begin(72); update("wrapped-next");
    assert(bar.needsRedraw());
    bar.render(display);
    assert(!bar.needsRedraw());
    now_ms += 79; assert(!bar.needsRedraw());
    ++now_ms; assert(bar.needsRedraw());
    bar.render(display); assert(!bar.needsRedraw());
  } else if (mode == 2) {
    now_ms = 0xffffffffU - 100;
    bar.begin(72); update("overdue-high");
    assert(bar.needsRedraw()); bar.render(display);
    now_ms = 5; assert(bar.needsRedraw());
    bar.render(display); assert(!bar.needsRedraw());
  } else if (mode == 3) {
    now_ms = 0x80000000U;
    bar.begin(72); update("upper-half");
    assert(bar.needsRedraw()); bar.render(display);
    assert(!bar.needsRedraw());
    // Fresh content must redraw immediately, not inherit the old deadline.
    update("changed-with-future-scroll"); assert(bar.needsRedraw());
    bar.render(display); assert(!bar.needsRedraw());
  } else if (mode == 4) {
    now_ms = 0x80000000U;
    display.width = 50;
    bar.begin(72); update("static");
    assert(bar.needsRedraw()); bar.render(display);
    assert(!bar.needsRedraw());
    now_ms += 1000; assert(!bar.needsRedraw());
    update("static-changed"); assert(bar.needsRedraw());
    bar.render(display); assert(!bar.needsRedraw());
  } else if (mode == 5) {
    now_ms = 1000;
    display.width = 50;
    bar.begin(72); update("static");
    assert(bar.needsRedraw()); bar.render(display);
    assert(!bar.needsRedraw());
    bar.begin(72); assert(bar.needsRedraw());
  } else assert(false);
  assert(display.prints);
}
'''

@unittest.skipUnless(CXX, "Native C++ compiler is required")
class ScrollingStatusBarRolloverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="statusbar-rollover-")
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        stub = directory / "helpers/ui/DisplayDriver.h"
        stub.parent.mkdir(parents=True)
        stub.write_text(DRIVER, encoding="ascii")
        source = directory / "fixture.cpp"
        source.write_text(FIXTURE, encoding="ascii")
        cls.binary = directory / "probe"
        subprocess.run([
            CXX, "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
            "-I" + str(ROOT / "examples/companion_radio/ui-tiny"),
            "-I" + str(directory), str(source), "-o", str(cls.binary),
        ], check=True, capture_output=True, timeout=30)

    def run_mode(self, mode):
        result = subprocess.run([str(self.binary), str(mode)], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_future_wrapped_scroll_deadline_waits_exactly_eighty_ms(self):
        self.run_mode(1)

    def test_high_deadline_expired_after_wrap_is_immediately_due(self):
        self.run_mode(2)

    def test_begin_upper_half_and_changed_scrolling_content_are_immediate(self):
        self.run_mode(3)

    def test_static_content_updates_only_when_dirty(self):
        self.run_mode(4)

    def test_begin_rearms_static_content_for_redraw(self):
        self.run_mode(5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
