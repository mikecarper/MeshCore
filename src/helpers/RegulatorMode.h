#pragma once
#include <stdint.h>

namespace mesh { namespace power {

enum class RegulatorMode : uint8_t { Default = 0, Dcdc = 1, Ldo = 2 };
constexpr uint8_t REGULATOR_DCDC = 1;
constexpr uint8_t REGULATOR_LDO = 2;

inline bool regulatorModeSupported(uint8_t modes, RegulatorMode mode) {
  return modes != 0 && (mode == RegulatorMode::Default
      || (mode == RegulatorMode::Dcdc && (modes & REGULATOR_DCDC))
      || (mode == RegulatorMode::Ldo && (modes & REGULATOR_LDO)));
}

}} // namespace mesh::power
