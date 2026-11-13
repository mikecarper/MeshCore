#pragma once

#include <RadioLib.h>

// SX1261/2 retention memory holds at most four register addresses. Keep the
// existing entries: replacing this list drops unrelated radio workarounds.
template<class Radio>
int16_t sx126xRetainAgcRegisters(Radio* radio, bool retain_gain) {
  constexpr uint16_t list_address = 0x029F;
  constexpr uint8_t capacity = 4;
  uint8_t list[1 + 2 * capacity] = {};
  int16_t status = radio->readRegister(list_address, list, sizeof(list));
  if (status != RADIOLIB_ERR_NONE) return status;
  if (list[0] > capacity) return RADIOLIB_ERR_UNKNOWN;

  const uint16_t required[] = {0x08B5, 0x08AC};
  bool changed = false;
  for (uint8_t i = 0; i < (retain_gain ? 2 : 1); ++i) {
    bool found = false;
    for (uint8_t j = 0; j < list[0]; ++j) {
      if (list[1 + 2 * j] == uint8_t(required[i] >> 8)
          && list[2 + 2 * j] == uint8_t(required[i])) found = true;
    }
    if (found) continue;
    if (list[0] == capacity) return RADIOLIB_ERR_UNKNOWN;
    list[1 + 2 * list[0]] = uint8_t(required[i] >> 8);
    list[2 + 2 * list[0]] = uint8_t(required[i]);
    ++list[0];
    changed = true;
  }
  if (!changed) return RADIOLIB_ERR_NONE;

  const uint8_t length = 1 + 2 * list[0];
  status = radio->writeRegister(list_address, list, length);
  if (status != RADIOLIB_ERR_NONE) return status;
  uint8_t actual[sizeof(list)] = {};
  status = radio->readRegister(list_address, actual, length);
  if (status != RADIOLIB_ERR_NONE) return status;
  for (uint8_t i = 0; i < length; ++i) {
    if (actual[i] != list[i]) return RADIOLIB_ERR_UNKNOWN;
  }
  return RADIOLIB_ERR_NONE;
}

template<class Radio>
int16_t sx126xWriteRetainedAgcPatch(Radio* radio, uint8_t value, bool retain_gain = true) {
  int16_t status = sx126xRetainAgcRegisters(radio, retain_gain);
  if (status != RADIOLIB_ERR_NONE) return status;
  value |= 0x01;
  status = radio->writeRegister(0x08B5, &value, 1);
  if (status != RADIOLIB_ERR_NONE) return status;
  uint8_t actual = 0;
  status = radio->readRegister(0x08B5, &actual, 1);
  if (status != RADIOLIB_ERR_NONE) return status;
  return (actual & 0x01) ? RADIOLIB_ERR_NONE : RADIOLIB_ERR_UNKNOWN;
}

template<class Radio>
int16_t sx126xApplyRetainedAgcPatch(Radio* radio, bool retain_gain = true) {
  uint8_t value = 0;
  const int16_t status = radio->readRegister(0x08B5, &value, 1);
  if (status != RADIOLIB_ERR_NONE) return status;
  return sx126xWriteRetainedAgcPatch(radio, value, retain_gain);
}
