#pragma once

#include <stdint.h>

// Pure recovery policy for the three MQTT preference transaction files.  The
// writer first moves the old primary to .bak, then moves the verified .tmp to
// the primary name.  On a reset, the loader uses this policy before decoding
// /mqtt_prefs.  "Preserve" is deliberately distinct from "Usable": it covers
// an unsupported newer layout, corruption, or an unreadable file and must
// never be replaced by an older image.
namespace MQTTPrefsRecovery {

enum class FileState : uint8_t {
  Missing,
  Usable,
  Preserve,
};

enum class Action : uint8_t {
  None,
  KeepPrimary,
  PromoteBackup,
  DiscardTemp,
};

inline Action select(FileState primary, FileState temp, FileState backup) {
  // A primary of any kind owns the name. In particular, do not roll a newer
  // or corrupt primary back to an older backup just because it cannot be read
  // by this firmware.
  if (primary != FileState::Missing) return Action::KeepPrimary;

  // Until temp has its final name, it is not committed. A failed publication
  // may have been reported to the caller, so never activate it during recovery.
  // The backup remains authoritative, including an opaque newer layout.
  if (backup != FileState::Missing) return Action::PromoteBackup;
  if (temp != FileState::Missing) return Action::DiscardTemp;
  return Action::None;
}

}  // namespace MQTTPrefsRecovery
