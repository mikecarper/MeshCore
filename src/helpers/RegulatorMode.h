#pragma once
#include <stdint.h>

// The current integrated STM32WL driver has no selectable supply domain:
// MCU and radio share a regulator and need coordinated support first. Keep
// its unsupported defaults out of every board/radio vtable as well as the
// settings implementation. Other drivers/platforms retain virtual dispatch.
#define MESH_REGULATOR_FIXED_RADIO_CustomSTM32WLx 1
#define MESH_REGULATOR_FIXED_RADIO_I(type) MESH_REGULATOR_FIXED_RADIO_##type
#define MESH_REGULATOR_FIXED_RADIO(type) MESH_REGULATOR_FIXED_RADIO_I(type)
#if defined(STM32_PLATFORM) && MESH_REGULATOR_FIXED_RADIO(RADIO_CLASS)
#define MESH_REGULATOR_FIXED_SUPPLY 1
#define MESH_REGULATOR_VIRTUAL
#else
#define MESH_REGULATOR_FIXED_SUPPLY 0
#define MESH_REGULATOR_VIRTUAL virtual
#endif
#undef MESH_REGULATOR_FIXED_RADIO
#undef MESH_REGULATOR_FIXED_RADIO_I
#undef MESH_REGULATOR_FIXED_RADIO_CustomSTM32WLx

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
