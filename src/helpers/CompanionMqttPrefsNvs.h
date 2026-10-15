#pragma once

#include <stddef.h>
#include <stdint.h>

#include "MQTTPrefsStorage.h"

#ifdef WITH_MQTT_BRIDGE

// The ESP32 Companion setup portal stores a raw MQTTPrefs blob in NVS rather
// than the observer's versioned /mqtt_prefs file. Display preferences are not
// used by Companion firmware. With advert verification off, keep writing the
// last pre-display boundary so rollback preserves a normally configured node.
// Enabling verification opts into a separately marked blob: an old recovery
// record's former padding must never silently enable a new packet filter.
namespace CompanionMqttPrefsNvs {

static constexpr size_t kWriteSize = MQTT_PREFS_V1_PRE_DISPLAY_PAYLOAD_SIZE;
// A short-lived development image wrote the compiler-padded runtime struct.
// Freeze that exact deployed shape: using sizeof(MQTTPrefs) here would stop
// recognizing its records the next time an append-only field grows the struct.
static constexpr size_t kRecoverySize = 2880;
static_assert(sizeof(MQTTPrefs) >= kRecoverySize,
              "MQTTPrefs no longer contains the recoverable NVS prefix");
static constexpr size_t kVerifiedSize = 2881;
static constexpr uint8_t kVerifiedMarker = 0xa7;
struct Record {
  MQTTPrefs prefs;
  uint8_t marker;
};
static_assert(offsetof(Record, marker) == kRecoverySize,
              "the marked NVS blob must append exactly one format byte");
static_assert(sizeof(Record) >= kVerifiedSize,
              "the marked NVS record must contain its whole serialized blob");

inline size_t writeSizeFor(const MQTTPrefs& prefs) {
  return prefs.mqtt_advert_verify != 0 ? kVerifiedSize : kWriteSize;
}

inline bool accepts(uint16_t version, size_t length) {
  return version == MQTT_PREFS_VERSION
      && (length == kWriteSize || length == kRecoverySize || length == kVerifiedSize);
}

}  // namespace CompanionMqttPrefsNvs

#endif
