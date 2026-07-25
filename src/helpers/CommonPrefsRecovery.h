#pragma once

#include <stdint.h>

// Recovery policy for a verified temp/backup preference transaction.
// The writer moves the old primary to backup only after temp is complete.
namespace CommonPrefsRecovery {

enum class Action : uint8_t {
  None,
  KeepPrimary,
  PromoteTemp,
  PromoteBackup,
  DiscardTemp,
};

inline Action select(bool primary_exists, bool temp_exists, bool backup_exists) {
  if (primary_exists) return Action::KeepPrimary;
  if (backup_exists && temp_exists) return Action::PromoteTemp;
  if (backup_exists) return Action::PromoteBackup;
  if (temp_exists) return Action::DiscardTemp;
  return Action::None;
}

}  // namespace CommonPrefsRecovery
