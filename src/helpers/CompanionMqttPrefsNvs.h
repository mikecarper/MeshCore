#pragma once

#include <stddef.h>
#include <stdint.h>

#include "MQTTPrefsStorage.h"

#ifdef WITH_MQTT_BRIDGE

// The ESP32 Companion setup portal stores a raw MQTTPrefs blob in NVS rather
// than the observer's versioned /mqtt_prefs file. Display preferences are not
// used by Companion firmware, so keep writing the last pre-display boundary:
// older firmware can then read a record saved by a newer image after rollback.
namespace CompanionMqttPrefsNvs {

static constexpr size_t kWriteSize = MQTT_PREFS_V1_PRE_DISPLAY_PAYLOAD_SIZE;
// A short-lived development image wrote the compiler-padded runtime struct.
// Freeze that exact deployed shape: using sizeof(MQTTPrefs) here would stop
// recognizing its records the next time an append-only field grows the struct.
static constexpr size_t kRecoverySize = 2880;
static_assert(sizeof(MQTTPrefs) >= kRecoverySize,
              "MQTTPrefs no longer contains the recoverable NVS prefix");

inline bool accepts(uint16_t version, size_t length) {
  return version == MQTT_PREFS_VERSION
      && (length == kWriteSize || length == kRecoverySize);
}

}  // namespace CompanionMqttPrefsNvs

#endif
