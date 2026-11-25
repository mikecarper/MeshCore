"""Reproduce the LTO wrapper bypass and execute the patched core task policy."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "nrf52_loop_stack_fix", ROOT / "scripts/nrf52_loop_stack_fix.py")
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)

RTOS = """#pragma once
#include <cstdint>
using BaseType_t = int;
using UBaseType_t = unsigned;
using TaskHandle_t = void*;
using TaskFunction_t = void (*)(void*);
using configSTACK_DEPTH_TYPE = uint16_t;
constexpr BaseType_t pdPASS = 1;
extern "C" BaseType_t xTaskCreate(TaskFunction_t, const char*,
    configSTACK_DEPTH_TYPE, void*, UBaseType_t, TaskHandle_t*);
"""
CORE = """#include "Arduino.h"
#include <cassert>
#include <cstddef>
#define LOOP_STACK_SZ       (256*4)
static TaskHandle_t _loopHandle;
static void loop_task(void*) {}
static unsigned last_depth;
constexpr unsigned TASK_PRIO_LOW = 1;
// The real RTOS exports this symbol. The linker resolves __real_xTaskCreate
// after LTO, so a whole-program test must keep the definition available.
extern "C" __attribute__((used, externally_visible)) BaseType_t xTaskCreate(TaskFunction_t, const char*,
    configSTACK_DEPTH_TYPE depth, void*, UBaseType_t, TaskHandle_t*) {
  last_depth = depth;
  return pdPASS;
}
int main() {
  xTaskCreate(loop_task, "loop", LOOP_STACK_SZ, NULL, TASK_PRIO_LOW, &_loopHandle);
  assert(last_depth == EXPECTED_LOOP_WORDS);
  xTaskCreate(nullptr, "usbd", 200, nullptr, 3, nullptr);
  assert(last_depth == 200);
  xTaskCreate(nullptr, "callback", 768, nullptr, 1, nullptr);
  assert(last_depth == 768);
}
"""


class Nrf52LoopStackFixTest(unittest.TestCase):
    def test_patch_is_idempotent_and_rejects_changed_or_partial_core(self):
        patched = PATCH.patched_source(CORE.replace("\n", "\r\n"))
        self.assertEqual(PATCH.patched_source(patched), patched)
        for source in (
            CORE.replace("LOOP_STACK_SZ       (256*4)", "LOOP_STACK_SZ       4096"),
            CORE.replace(PATCH.OLD_LOOP_CREATE, PATCH.LOOP_CREATE),
            CORE.replace(PATCH.LOOP_STACK, PATCH.DECLARATION + PATCH.LOOP_STACK),
            CORE + "\n" + PATCH.OLD_LOOP_CREATE,
        ):
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                PATCH.patched_source(source)

    @unittest.skipUnless(shutil.which("c++"), "native C++ compiler unavailable")
    def test_lto_bypass_is_reproduced_then_fixed_without_resizing_other_tasks(self):
        with tempfile.TemporaryDirectory(prefix="meshcore-loop-stack-") as temp:
            temp = Path(temp)
            (temp / "Arduino.h").write_text(RTOS)
            for name in ("FreeRTOS.h", "task.h"):
                (temp / name).write_text('#include "Arduino.h"\n')
            core, binary = temp / "main.cpp", temp / "core"
            for patched, configured_words in ((False, 2048), (True, 2048), (True, 3072)):
                with self.subTest(patched=patched, configured_words=configured_words):
                    core.write_text(PATCH.patched_source(CORE) if patched else CORE)
                    expected = configured_words if patched else 1024
                    built = subprocess.run([
                        "c++", "-std=c++17", "-O2", "-flto", "-flto-partition=one",
                        "-fno-semantic-interposition", "-DNRF52_PLATFORM=1",
                        f"-DMESH_NRF52_LOOP_STACK_WORDS={configured_words}",
                        f"-DEXPECTED_LOOP_WORDS={expected}", "-I" + str(temp),
                        str(core), str(ROOT / "src/Nrf52LoopStack.cpp"),
                        "-Wl,--wrap=xTaskCreate", "-o", str(binary),
                    ], text=True, capture_output=True)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    result = subprocess.run([str(binary)], text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_nrf52_build_uses_private_core_patch(self):
        config = (ROOT / "platformio.ini").read_text()
        nrf = config.split("[nrf52_base]", 1)[1].split("\n[", 1)[0]
        self.assertIn("pre:scripts/nrf52_loop_stack_fix.py", nrf)
        self.assertEqual(config.count("pre:scripts/nrf52_loop_stack_fix.py"), 1)


if __name__ == "__main__":
    unittest.main()
