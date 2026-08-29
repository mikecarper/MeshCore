#pragma once

#include <stddef.h>
#include <stdint.h>

#include "MQTTPrefsSerializer.h"

#ifdef WITH_MQTT_BRIDGE

// Conservative one-way import policy for observer-firmware-dev's historical
// `/mqtt.json` v1. Despite its name, that file uses ConfigSerializer notation
// (unquoted keys), so decoding must use the source firmware's grammar rather
// than a general-purpose RFC JSON parser.
namespace MQTTPrefsJsonImport {

static const size_t kMaximumFileSize = 16384;

enum class Decision : uint8_t {
  BinaryAuthoritative,
  NoJsonSource,
  ImportPrimary,
  HoldJsonArtifacts,
};

// No generation is shared by the two formats. Any binary primary or recovery
// artifact therefore owns the configuration name, even if it later proves
// corrupt, future, or default-valued. Likewise, unresolved JSON transaction
// artifacts are preserved rather than guessed at.
inline Decision select(bool binary_hold, bool binary_primary,
                       bool binary_temp, bool binary_backup,
                       bool json_primary, bool json_temp, bool json_backup) {
  if (binary_hold || binary_primary || binary_temp || binary_backup) {
    return Decision::BinaryAuthoritative;
  }
  if (json_temp || json_backup) return Decision::HoldJsonArtifacts;
  return json_primary ? Decision::ImportPrimary : Decision::NoJsonSource;
}

enum class Result : uint8_t {
  Loaded,
  LoadedWithRepairs,
  InvalidSyntax,
  InvalidSchema,
  UnsupportedVersion,
  TooLarge,
};

inline bool loaded(Result result) {
  return result == Result::Loaded || result == Result::LoadedWithRepairs;
}

// `candidate` must start with this build's defaults and must not alias the live
// runtime object. Strict parsing may touch it before a late error; the caller
// commits it only after this function reports Loaded/LoadedWithRepairs.
inline Result decode(Stream& source, const MQTTPrefs& repair_defaults,
                     MQTTPrefs* candidate,
                     MQTTPrefsPresetValidator preset_valid) {
  if (candidate == nullptr) return Result::InvalidSchema;
  MQTTPrefsSerializer serializer(candidate, &repair_defaults, preset_valid);
  const bool parsed = serializer.loadSerial(source);

  // Version is format-mandated as the first root property. If a future writer
  // uses new grammar later in the file, preserve it as opaque even though this
  // parser stops at that unknown construct.
  if (serializer.hasFutureVersion()) return Result::UnsupportedVersion;
  if (!parsed) return Result::InvalidSyntax;

  bool repaired = false;
  if (!serializer.apply(&repaired)) return Result::InvalidSchema;
  return repaired ? Result::LoadedWithRepairs : Result::Loaded;
}

}  // namespace MQTTPrefsJsonImport

#endif  // WITH_MQTT_BRIDGE
