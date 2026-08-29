#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh {
namespace companion {

static constexpr uint8_t kRunCliCommand = 0x42;

// Original aliases predate upstream's command-66 allocation. The later block
// was shipped briefly before this fork standardized new clients on framed CLI.
static constexpr uint8_t kOriginalGetFemRxGain = 0x42;
static constexpr uint8_t kOriginalSetFemRxGain = 0x43;
static constexpr uint8_t kOriginalGetRadioRxGain = 0x44;
static constexpr uint8_t kOriginalSetRadioRxGain = 0x45;
static constexpr uint8_t kOriginalGetWiFiPowerSave = 0x46;
static constexpr uint8_t kOriginalSetWiFiPowerSave = 0x47;
static constexpr uint8_t kOriginalGetBluetoothName = 0x48;
static constexpr uint8_t kOriginalSetBluetoothName = 0x49;

static constexpr uint8_t kDeprecatedGetFemRxGain = 0x78;
static constexpr uint8_t kDeprecatedSetFemRxGain = 0x79;
static constexpr uint8_t kDeprecatedGetRadioRxGain = 0x7A;
static constexpr uint8_t kDeprecatedSetRadioRxGain = 0x7B;
static constexpr uint8_t kDeprecatedGetWiFiPowerSave = 0x7C;
static constexpr uint8_t kDeprecatedSetWiFiPowerSave = 0x7D;
static constexpr uint8_t kDeprecatedGetBluetoothName = 0x7E;
static constexpr uint8_t kDeprecatedSetBluetoothName = 0x7F;

inline bool isRunCliFrame(uint8_t command, size_t frame_len) {
  return command == kRunCliCommand && frame_len >= 2;
}

inline bool isFemRxGainGet(uint8_t command) {
  return command == kOriginalGetFemRxGain || command == kDeprecatedGetFemRxGain;
}
inline bool isFemRxGainSet(uint8_t command) {
  return command == kOriginalSetFemRxGain || command == kDeprecatedSetFemRxGain;
}
inline bool isRadioRxGainGet(uint8_t command) {
  return command == kOriginalGetRadioRxGain || command == kDeprecatedGetRadioRxGain;
}
inline bool isRadioRxGainSet(uint8_t command) {
  return command == kOriginalSetRadioRxGain || command == kDeprecatedSetRadioRxGain;
}
inline bool isWiFiPowerSaveGet(uint8_t command) {
  return command == kOriginalGetWiFiPowerSave || command == kDeprecatedGetWiFiPowerSave;
}
inline bool isWiFiPowerSaveSet(uint8_t command) {
  return command == kOriginalSetWiFiPowerSave || command == kDeprecatedSetWiFiPowerSave;
}
inline bool isBluetoothNameGet(uint8_t command) {
  return command == kOriginalGetBluetoothName || command == kDeprecatedGetBluetoothName;
}
inline bool isBluetoothNameSet(uint8_t command) {
  return command == kOriginalSetBluetoothName || command == kDeprecatedSetBluetoothName;
}

}  // namespace companion
}  // namespace mesh
