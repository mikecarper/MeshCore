"""Compile the real Companion shutdown path with every optional hook enabled."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <string>
#include <vector>

static std::vector<std::string> events;
static uint32_t now = 5000;
uint32_t millis() { return now++; }

struct Display {
  bool on;
  explicit Display(bool active) : on(active) {}
  virtual ~Display() = default;
  virtual bool isOn() { return on; }
  virtual void turnOff() = 0;
};
struct Panel final : Display {
  const char* kind;
  Panel(const char* type, bool active) : Display(active), kind(type) {}
  void turnOff() override {
    assert(on);
    events.emplace_back(kind);
    on = false;
  }
};
struct Board {
  void reboot() { events.emplace_back("reboot"); }
  void powerOff() { events.emplace_back("power-off"); }
};
struct ReaderScreen {
  bool flush() { events.emplace_back("reader-flush"); return true; }
};
struct Buzzer {
  int playing = 0;
  void shutdown() { events.emplace_back("buzzer-shutdown"); playing = 2; }
  bool isPlaying() const { return playing != 0; }
  void loop() { events.emplace_back("buzzer-loop"); --playing; }
};
struct UITask {
  Display* _display;
  Board* _board;
  ReaderScreen* reader;
  Buzzer buzzer;
  bool prepared = true;
  bool prepareForShutdown() {
    events.emplace_back("prepare");
    return prepared;
  }
  void shutdown(bool restart);
};

@SHUTDOWN@

static std::vector<std::string> expected(const char* panel, const char* exit) {
  std::vector<std::string> result{"prepare"};
#if COMPANION_FEATURE_READER
  result.emplace_back("reader-flush");
#endif
#ifdef PIN_BUZZER
  result.emplace_back("buzzer-shutdown");
  result.emplace_back("buzzer-loop");
  result.emplace_back("buzzer-loop");
#endif
  if (panel) result.emplace_back(panel);
  result.emplace_back(exit);
  return result;
}

int main() {
  Board board;
  ReaderScreen reader;
  // Polymorphic off goes through the same driver hook for every panel family.
  for (const char* kind : {"oled-off", "tft-off", "eink-off"}) {
    Panel panel(kind, true);
    UITask task{&panel, &board, &reader};
    events.clear();
    task.shutdown(false);
    assert(!panel.on);
    assert(events == expected(kind, "power-off"));

    // Already-off or absent hardware must not receive another shutdown write.
    events.clear();
    task.shutdown(false);
    assert(events == expected(nullptr, "power-off"));

    // A restart keeps the former display behavior and takes the reboot path.
    panel.on = true;
    events.clear();
    task.shutdown(true);
    assert(panel.on && events == expected(nullptr, "reboot"));

    task.prepared = false;
    events.clear();
    task.shutdown(false);
    assert(panel.on && events == std::vector<std::string>{"prepare"});
  }
  UITask headless{nullptr, &board, &reader};
  events.clear();
  headless.shutdown(false);
  assert(events == expected(nullptr, "power-off"));
  // No reader is a valid startup/partial-initialization state.
  headless.reader = nullptr;
  events.clear();
  headless.shutdown(false);
  auto without_reader = expected(nullptr, "power-off");
#if COMPANION_FEATURE_READER
  without_reader.erase(without_reader.begin() + 1);
#endif
  assert(events == without_reader);
  return 0;
}
'''


class CompanionShutdownDisplayTests(unittest.TestCase):
    def test_actual_shutdown_driver_dispatch_and_ordering(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++17 compiler is required")
        source = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text(encoding="utf-8")
        handler = extract_braced(source, "void UITask::shutdown(bool restart)")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        for reader in (False, True):
            for buzzer in (False, True):
                with self.subTest(reader=reader, buzzer=buzzer):
                    with tempfile.TemporaryDirectory(prefix="meshcore-ui-shutdown-") as work:
                        root = Path(work)
                        code = root / "shutdown.cpp"
                        code.write_text(HARNESS.replace("@SHUTDOWN@", handler), encoding="utf-8")
                        binary = root / ("shutdown.exe" if sys.platform == "win32" else "shutdown")
                        flags = ["-DCOMPANION_FEATURE_READER=" + str(int(reader))]
                        if buzzer:
                            flags.append("-DPIN_BUZZER=1")
                        result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                                 "-Wno-missing-field-initializers", *flags, *sanitizers,
                                                 str(code), "-o", str(binary)],
                                                capture_output=True, text=True, timeout=60)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
