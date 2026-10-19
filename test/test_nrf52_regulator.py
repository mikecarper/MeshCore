#!/usr/bin/env python3
"""Execute the production nRF52 CPU regulator and reset classification paths."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "src/helpers/NRF52Board.cpp"


def run_native(source):
    with tempfile.TemporaryDirectory(prefix="meshcore-nrf52-regulator-") as directory:
        cpp, binary = Path(directory) / "test.cpp", Path(directory) / "test"
        cpp.write_text(source, encoding="ascii")
        compiled = subprocess.run(
            [os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra",
             "-Werror", "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
            capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)


class Nrf52RegulatorTests(unittest.TestCase):
    def test_saved_cpu_mode_uses_checked_sdk_and_rejects_unsupported_boards(self):
        source = BOARD.read_text()
        methods = "\n".join(body(source, signature) for signature in (
            "uint8_t NRF52Board::mcuRegulatorModes() const",
            "mesh::power::RegulatorMode NRF52Board::defaultMcuRegulatorMode() const",
            "mesh::power::RegulatorMode NRF52Board::activeMcuRegulatorMode() const",
            "bool NRF52Board::applyMcuRegulator(mesh::power::RegulatorMode mode)",
        ))
        run_native(r'''
#include <cassert>
#include <cstdint>
#include "helpers/RegulatorMode.h"
using mesh::power::RegulatorMode;
constexpr uint32_t NRF_SUCCESS = 0;
constexpr uint8_t NRF_POWER_DCDC_DISABLE = 0, NRF_POWER_DCDC_ENABLE = 1;
static uint32_t query_result, setter_result;
static uint8_t softdevice, requested;
static unsigned setter_calls;
struct Power { unsigned DCDCEN; } power;
#define NRF_POWER (&power)
namespace mesh_nrf52 {
uint32_t softdeviceIsEnabled(uint8_t& enabled) {
  enabled = softdevice;
  return query_result;
}
}
uint32_t sd_power_dcdc_mode_set(uint8_t mode) {
  assert(softdevice);
  requested = mode;
  ++setter_calls;
  return setter_result;
}
struct NRF52Board {
  bool cpu_dcdc_supported = false;
  RegulatorMode cpu_regulator_mode = RegulatorMode::Ldo;
  uint8_t mcuRegulatorModes() const;
  RegulatorMode defaultMcuRegulatorMode() const;
  RegulatorMode activeMcuRegulatorMode() const;
  bool applyMcuRegulator(RegulatorMode);
};
@METHODS@
int main() {
  NRF52Board board;
  power.DCDCEN = 77;
  assert(board.mcuRegulatorModes() == 0);
  assert(board.defaultMcuRegulatorMode() == RegulatorMode::Default);
  assert(!board.applyMcuRegulator(RegulatorMode::Dcdc));
  assert(!board.applyMcuRegulator(RegulatorMode::Ldo));
  assert(!board.applyMcuRegulator(RegulatorMode::Default));
  assert(power.DCDCEN == 77 && setter_calls == 0);
  board.cpu_dcdc_supported = true;
  assert(board.mcuRegulatorModes() == 3);
  assert(board.defaultMcuRegulatorMode() == RegulatorMode::Dcdc);
  assert(!board.applyMcuRegulator(static_cast<RegulatorMode>(255)));
  for (softdevice = 0; softdevice < 2; ++softdevice) {
    power.DCDCEN = 77;
    query_result = 5;
    assert(!board.applyMcuRegulator(RegulatorMode::Dcdc));
    assert(power.DCDCEN == 77 && setter_calls == 0);
    query_result = 0;
    setter_result = 0;
    assert(board.applyMcuRegulator(RegulatorMode::Default));
    assert(board.activeMcuRegulatorMode() == RegulatorMode::Dcdc);
    assert(softdevice ? requested == 1 : power.DCDCEN == 1);
    assert(board.applyMcuRegulator(RegulatorMode::Ldo));
    assert(board.activeMcuRegulatorMode() == RegulatorMode::Ldo);
    assert(softdevice ? requested == 0 : power.DCDCEN == 0);
    if (softdevice) {
      setter_result = 4;
      assert(!board.applyMcuRegulator(RegulatorMode::Dcdc));
      assert(board.activeMcuRegulatorMode() == RegulatorMode::Ldo);
      assert(power.DCDCEN == 77);
    }
    setter_calls = 0;
  }
}
'''.replace("@METHODS@", methods))

    def test_reset_classifier_excludes_wakes_and_intentional_resets(self):
        method = body(BOARD.read_text(), "ResetKind platformResetKind()")
        run_native(r'''
#include <cassert>
#include <cstdint>
#include "helpers/RegulatorRecovery.h"
using mesh::power::ResetKind;
constexpr uint32_t NRF_SUCCESS = 0;
#define POWER_RESETREAS_RESETPIN_Msk 1U
#define POWER_RESETREAS_DOG_Msk 2U
#define POWER_RESETREAS_SREQ_Msk 4U
#define POWER_RESETREAS_LOCKUP_Msk 8U
#define POWER_RESETREAS_OFF_Msk 16U
#define POWER_RESETREAS_LPCOMP_Msk 32U
#define POWER_RESETREAS_VBUS_Msk 64U
#define POWER_RESETREAS_DIF_Msk 128U
static uint32_t regulator_reset_reason;
static uint8_t regulator_shutdown_reason;
static uint32_t query_result = 3, clear_result = 7;
static unsigned query_calls, clear_calls;
struct Power { uint32_t RESETREAS = 0; } power;
#define NRF_POWER (&power)
namespace mesh_nrf52 {
uint32_t softdeviceIsEnabled(uint8_t& enabled) {
  ++query_calls;
  enabled = 1;
  return query_result;
}
}
uint32_t sd_power_reset_reason_clr(uint32_t bits) {
  assert(bits == 0xFFFFFFFFUL);
  ++clear_calls;
  return clear_result;
}
@METHOD@
int main() {
  assert(platformResetKind() == ResetKind::Unknown);
  assert(query_calls == 1 && clear_calls == 0 && power.RESETREAS == 0);
  query_result = 0;
  assert(platformResetKind() == ResetKind::Unknown);
  assert(clear_calls == 1 && power.RESETREAS == 0);
  clear_result = 0;
  regulator_reset_reason = 0;
  assert(platformResetKind() == ResetKind::Cold);
  assert(clear_calls == 2);
  for (uint32_t reason : {2U, 4U, 8U, 2U | 4U}) {
    regulator_reset_reason = reason;
    assert(platformResetKind() == ResetKind::Crash);
  }
  for (uint32_t reason : {16U, 32U, 64U, 16U | 2U}) {
    regulator_reset_reason = reason;
    assert(platformResetKind() == ResetKind::Wake);
  }
  for (uint32_t reason : {1U, 128U, 1U | 2U}) {
    regulator_reset_reason = reason;
    assert(platformResetKind() == ResetKind::Intentional);
  }
  regulator_reset_reason = 8U;
  for (uint8_t shutdown : {0x4C, 0x55, 0x42}) {
    regulator_shutdown_reason = shutdown;
    assert(platformResetKind() == ResetKind::Intentional);
  }
  regulator_shutdown_reason = 0;
  regulator_reset_reason = 0x80000000UL;
  assert(platformResetKind() == ResetKind::Unknown);
  assert(clear_calls == 2);
}
'''.replace("@METHOD@", method).replace("#include <cstdint>",
                                      "#include <cstdint>\n#include <initializer_list>"))

    def test_reset_acknowledgement_without_softdevice_uses_direct_register(self):
        method = body(BOARD.read_text(), "ResetKind platformResetKind()")
        run_native(r'''
#include <cassert>
#include <cstdint>
#include "helpers/RegulatorRecovery.h"
using mesh::power::ResetKind;
constexpr uint32_t NRF_SUCCESS = 0;
#define POWER_RESETREAS_RESETPIN_Msk 1U
#define POWER_RESETREAS_DOG_Msk 2U
#define POWER_RESETREAS_SREQ_Msk 4U
#define POWER_RESETREAS_LOCKUP_Msk 8U
static uint32_t regulator_reset_reason;
static uint8_t regulator_shutdown_reason;
static unsigned query_calls, clear_calls;
struct Power { uint32_t RESETREAS = 0; } power;
#define NRF_POWER (&power)
namespace mesh_nrf52 {
uint32_t softdeviceIsEnabled(uint8_t& enabled) {
  ++query_calls;
  enabled = 0;
  return 0;
}
}
uint32_t sd_power_reset_reason_clr(uint32_t) {
  ++clear_calls;
  return 9;
}
@METHOD@
int main() {
  assert(platformResetKind() == ResetKind::Cold);
  assert(query_calls == 1 && clear_calls == 0);
  assert(power.RESETREAS == 0xFFFFFFFFUL);
  regulator_reset_reason = POWER_RESETREAS_DOG_Msk;
  assert(platformResetKind() == ResetKind::Crash);
  assert(query_calls == 1 && clear_calls == 0);
}
'''.replace("@METHOD@", method))

    def test_clean_reboot_marker_is_optional_and_precedes_reset(self):
        method = body(BOARD.read_text(), "void NRF52Board::reboot()")
        run_native(r'''
#include <cassert>
static unsigned resets, clean_marks;
static void (*meshRegulatorCleanReset)();
void markClean() { assert(resets == 1); ++clean_marks; }
void NVIC_SystemReset() {
  assert(resets == 0 || clean_marks == 1);
  ++resets;
}
struct NRF52Board { void reboot(); };
@METHOD@
int main() {
  NRF52Board board;
  board.reboot();
  assert(resets == 1 && clean_marks == 0);
  meshRegulatorCleanReset = markClean;
  board.reboot();
  assert(resets == 2 && clean_marks == 1);
}
'''.replace("@METHOD@", method))


if __name__ == "__main__":
    unittest.main()
