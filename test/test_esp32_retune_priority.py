"""Exercise the actual optional retune scope with a deterministic RTOS stub."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ESP32RetunePriorityTests(unittest.TestCase):
    def test_disabled_rx_gate_nested_restore_and_failed_operation_cleanup(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a native C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="esp32-retune-priority-") as directory:
            work = Path(directory)
            (work / "freertos").mkdir()
            (work / "freertos/FreeRTOS.h").write_text(
                "#pragma once\nusing UBaseType_t = unsigned;\n"
                "struct FakeTask { UBaseType_t priority; };\n"
                "using TaskHandle_t = FakeTask*;\n"
                "#define configMAX_PRIORITIES 25\n", encoding="ascii")
            (work / "freertos/task.h").write_text(
                "#pragma once\n#include <freertos/FreeRTOS.h>\n"
                "UBaseType_t uxTaskPriorityGet(TaskHandle_t);\n"
                "TaskHandle_t xTaskGetCurrentTaskHandle();\n"
                "void vTaskPrioritySet(TaskHandle_t, UBaseType_t);\n", encoding="ascii")
            source = work / "main.cpp"
            source.write_text(r'''
#include <cassert>
#include <helpers/radiolib/ESP32RetunePriority.h>
FakeTask task{1};
unsigned queries = 0, writes = 0;
UBaseType_t uxTaskPriorityGet(TaskHandle_t owner) {
  ++queries;
  assert(owner == nullptr || owner == &task);
  return task.priority;
}
TaskHandle_t xTaskGetCurrentTaskHandle() { return &task; }
void vTaskPrioritySet(TaskHandle_t owner, UBaseType_t priority) {
  assert(owner == &task && priority < configMAX_PRIORITIES);
  ++writes;
  owner->priority = priority;
}
int main() {
  ESP32RetunePriority scope;
  assert(scope.requestedPriority() == 0 && !scope.isActive());
  scope.begin(true); scope.end(); scope.end();
  assert(task.priority == 1 && queries == 0 && writes == 0);

  assert(scope.setPriority(3));
  scope.begin(false); scope.end();
  assert(task.priority == 1 && queries == 0 && writes == 0);

  scope.begin(true);
  assert(task.priority == 3 && scope.isActive() && writes == 1);
  scope.begin(true); scope.begin(false);
  assert(task.priority == 3 && queries == 1 && writes == 1);
  scope.end(); scope.end();
  assert(task.priority == 3 && scope.isActive() && writes == 1);
  scope.end();
  assert(task.priority == 1 && !scope.isActive() && writes == 2);

  // Hook cleanup is unconditional: both a deferred guard and a failed radio
  // operation use the same end() and must return the original priority.
  for (bool operationSucceeded : {false, true}) {
    (void)operationSucceeded;
    scope.begin(true);
    assert(task.priority == 3);
    scope.end();
    assert(task.priority == 1 && !scope.isActive());
  }

  task.priority = 7;
  unsigned beforeWrites = writes;
  scope.begin(true); scope.end();
  assert(task.priority == 7 && writes == beforeWrites && !scope.isActive());

  task.priority = 1;
  scope.begin(true);
  assert(task.priority == 3);
  assert(scope.setPriority(0));
  assert(!scope.setPriority(configMAX_PRIORITIES));
  assert(scope.requestedPriority() == 0);
  scope.end();
  assert(task.priority == 1 && !scope.isActive());
  beforeWrites = writes;
  scope.begin(true); scope.end();
  assert(task.priority == 1 && writes == beforeWrites);
  scope.end();
  assert(task.priority == 1 && writes == beforeWrites);
}
'''.replace("#include <cassert>", "#include <cassert>\n#include <initializer_list>"), encoding="ascii")
            binary = work / "retune-priority.exe"
            result = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 "-I", str(work), "-I", str(ROOT / "src"), str(source), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
