#pragma once

#include <RadioLib.h>
#include <string.h>
#include "SX1262ProfileFrequencyImage.h"

namespace sx1262profile {

inline bool hasVersion(Module& mod, const char (&expected)[16]) {
  uint8_t version[16] = {};
  mod.SPIreadRegisterBurst(RADIOLIB_SX126X_REG_VERSION_STRING, sizeof(version), version);
  return memcmp(version, expected, sizeof(version)) == 0;
}

// The RadioLib patch uploader activates the image before its caller can check
// the bytes. Verify this fixed image while the radio is in standby, then activate.
inline bool install(Module& mod) {
  mod.SPIwriteRegister(RADIOLIB_SX126X_REG_PATCH_UPDATE_ENABLE, RADIOLIB_SX126X_PATCH_UPDATE_ENABLED);
  bool matched = true;
  for (size_t i = 0; i < PROFILE_FREQUENCY_PRAM_WORDS && matched; ++i) {
    const uint32_t w = profile_frequency_pram[i];
    const uint8_t expected[] = {uint8_t(w >> 24), uint8_t(w >> 16), uint8_t(w >> 8), uint8_t(w)};
    const uint16_t addr = RADIOLIB_SX126X_REG_PATCH_MEMORY_BASE + 4 * i;
    uint8_t actual[4] = {};
    mod.SPIwriteRegisterBurst(addr, expected, sizeof(expected));
    mod.SPIreadRegisterBurst(addr, sizeof(actual), actual);
    matched = memcmp(actual, expected, sizeof(actual)) == 0;
  }
  mod.SPIwriteRegister(RADIOLIB_SX126X_REG_PATCH_UPDATE_ENABLE, RADIOLIB_SX126X_PATCH_UPDATE_DISABLED);
  return matched && mod.SPIwriteStream(RADIOLIB_SX126X_CMD_PRAM_UPDATE, nullptr, 0) == RADIOLIB_ERR_NONE
      && hasVersion(mod, "SX1261 V2D 2D0K");
}

// 0x9B writes only the four frequency registers. Check the radio's register
// word before the caller updates RadioLib's cache or admits RX on the new profile.
inline bool tune(Module& mod, float mhz) {
  const uint32_t word = uint32_t(mhz * (uint32_t(1) << RADIOLIB_SX126X_DIV_EXPONENT)
                                / RADIOLIB_SX126X_CRYSTAL_FREQ);
  const uint8_t expected[] = {uint8_t(word >> 24), uint8_t(word >> 16), uint8_t(word >> 8), uint8_t(word)};
  if (mod.SPIwriteStream(0x9B, expected, sizeof(expected)) != RADIOLIB_ERR_NONE) return false;
  uint8_t actual[4] = {};
  mod.SPIreadRegisterBurst(0x088B, sizeof(actual), actual);
  return memcmp(actual, expected, sizeof(actual)) == 0;
}

} // namespace sx1262profile
