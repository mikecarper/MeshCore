#if defined(USE_LR2021)

#include "LR2021CalibrationSettings.h"
#include "LR2021CalibrationPolicy.h"
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
#include "LR2021AutoTrim.h"
#include "LR2021BootAutoTrim.h"
#endif
#include "CustomLR2021Wrapper.h"
#include <helpers/RadioDirectionPolicy.h>
#include <helpers/CheckedSettingsFile.h>
#include <stdio.h>
#include <string.h>

namespace mesh { namespace lr2021 {
namespace {
FILESYSTEM* settings_fs = nullptr;
XoscTrimSelection saved_selection;
XoscTrimSelection previous_selection;
bool pending = false;
bool failed = false;
bool rollback_failed = false;
uint32_t pending_started = 0;
constexpr uint32_t ApplyTimeoutMillis = 30000;
constexpr uint32_t ApplyHandoffMillis = 2000;
constexpr char path[] = "/radio_xtrim";
constexpr char backup[] = "/radio_xtrim.bak";
constexpr char temporary[] = "/radio_xtrim.tmp";
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
constexpr char combined_path[] = "/radio_calibration";
constexpr char combined_backup[] = "/radio_calibration.bak";
constexpr char combined_temporary[] = "/radio_calibration.tmp";
constexpr char journal_path[] = "/radio_calibration.journal";
constexpr char journal_temporary[] = "/radio_calibration.journal.tmp";
AutoTrim auto_trim;
uint32_t auto_frequency = 0, auto_started = 0, auto_generation = 0, auto_restore_failed_at = 0;
uint32_t auto_restore_started = 0;
bool auto_saved = false, auto_restore_error = false, auto_storage_error = false, auto_deferred_restore = false;
bool auto_restore_timing = false;
bool auto_physical_uncertain = false;
bool auto_correction_calculated = false, auto_correction_needs_restore = false;
int32_t auto_previous_correction = 0, auto_target_correction = 0;
bool pending_correction_ready = false, pending_correction_attempted = false;
bool manual_correction_restore = false;
uint32_t manual_restore_started = 0;
uint8_t auto_restore_attempts = 0;
BootAutoTrim boot_auto;
bool boot_autostart_enabled = true, boot_stable = false;
uint32_t boot_stable_since = 0, boot_stable_generation = 0, boot_stable_frequency = 0;
constexpr char auto_policy_path[] = "/radio_xtrim_auto";
constexpr char auto_policy_backup[] = "/radio_xtrim_auto.bak";
constexpr char auto_policy_temporary[] = "/radio_xtrim_auto.tmp";

bool readAutoPolicy(const char* name, bool& enabled) {
  uint8_t bytes[6];
  if (!storage::readFixedSettingsFile(settings_fs,name,bytes,sizeof(bytes))
      || memcmp(bytes,"XAP1",4) || bytes[4]>1) return false;
  uint8_t sum=0; for (unsigned i=0;i<5;++i) sum^=bytes[i];
  if (sum!=bytes[5]) return false;
  enabled=bytes[4]; return true;
}

bool loadAutoPolicy() {
  boot_autostart_enabled=true;
  if (!settings_fs) return false;
  if (settings_fs->exists(auto_policy_path)) return readAutoPolicy(auto_policy_path,boot_autostart_enabled);
  if (settings_fs->exists(auto_policy_backup)) return readAutoPolicy(auto_policy_backup,boot_autostart_enabled);
  return true;
}

bool saveAutoPolicy(bool enabled) {
  if (!settings_fs) return false;
  if (!settings_fs->exists(auto_policy_path) && settings_fs->exists(auto_policy_backup)) {
    bool recovered=false;
    if (!readAutoPolicy(auto_policy_backup,recovered)
        || !settings_fs->rename(auto_policy_backup,auto_policy_path)
        || !readAutoPolicy(auto_policy_path,recovered)) return false;
  }
  if (settings_fs->exists(auto_policy_temporary) && !settings_fs->remove(auto_policy_temporary)) return false;
  uint8_t bytes[6]={'X','A','P','1',uint8_t(enabled),0};
  for (unsigned i=0;i<5;++i) bytes[5]^=bytes[i];
  File file=settings_fs->open(auto_policy_temporary,"w",true);
  if (!file) return false;
  const bool written=file.write(bytes,sizeof(bytes))==sizeof(bytes); file.close();
  bool checked=false;
  if (!written || !readAutoPolicy(auto_policy_temporary,checked) || checked!=enabled) return false;
  if (settings_fs->exists(auto_policy_backup) && !settings_fs->remove(auto_policy_backup)) return false;
  if (settings_fs->exists(auto_policy_path) && !settings_fs->rename(auto_policy_path,auto_policy_backup)) return false;
  if (!settings_fs->rename(auto_policy_temporary,auto_policy_path)
      || !readAutoPolicy(auto_policy_path,checked) || checked!=enabled) {
    if (settings_fs->exists(auto_policy_path)) settings_fs->remove(auto_policy_path);
    if (settings_fs->exists(auto_policy_backup)) settings_fs->rename(auto_policy_backup,auto_policy_path);
    return false;
  }
  if (settings_fs->exists(auto_policy_backup)) settings_fs->remove(auto_policy_backup);
  return true;
}

bool hasCalibration() { return saved_selection.enabled || saved_selection.correction_ppb!=0; }

bool beginAutomaticTrial(CustomLR2021Wrapper& radio, uint32_t frequency, uint32_t now,
                         bool fresh_boot = false) {
  XoscTrimSelection current;
  current.enabled=radio.getXoscTrim(current.a,current.b);
  current.correction_ppb=radio.frequencyCorrectionPpb();
  if (!auto_trim.begin(current,frequency,now,900000,120000,1500,2000,fresh_boot)) return false;
  auto_started=now; auto_frequency=frequency; auto_generation=radio.profiles()->generation[0];
  auto_saved=auto_restore_error=auto_storage_error=false;
  auto_restore_attempts=0; auto_restore_timing=auto_physical_uncertain=false;
  auto_previous_correction=current.correction_ppb; auto_target_correction=current.correction_ppb;
  auto_correction_calculated=auto_correction_needs_restore=false;
  return true;
}
bool autoStorageReady() {
  if (!settings_fs) return false;
  constexpr char probe[] = "/radio_xtrim.probe";
  if (settings_fs->exists(probe) && !settings_fs->remove(probe)) return false;
  File file = settings_fs->open(probe, "w", true);
  if (!file) return false;
  const uint8_t expected[] = {'X', 'T', 'P', 1};
  const bool written = file.write(expected, sizeof(expected)) == sizeof(expected);
  file.close();
  uint8_t checked[sizeof(expected)] = {};
  const bool verified = written && storage::readFixedSettingsFile(settings_fs, probe, checked, sizeof(checked))
      && !memcmp(checked, expected, sizeof(expected));
  const bool removed = settings_fs->remove(probe) && !settings_fs->exists(probe);
  return verified && removed;
}
const char* autoState() {
  switch (auto_trim.action()) {
    case AutoTrim::Action::Idle: return "idle";
    case AutoTrim::Action::Collect: return "listening";
    case AutoTrim::Action::Apply: return "applying trial";
    case AutoTrim::Action::Restore: return auto_restore_error ? "restore retry" : "restoring";
    case AutoTrim::Action::Commit: return "saving";
    case AutoTrim::Action::Complete: return auto_deferred_restore
        ? (auto_storage_error ? "restore deferred; storage uncertain; reboot required" : "restore deferred; reboot required")
        : auto_storage_error ? "restored; storage uncertain" : auto_saved
        ? (auto_trim.baselineKnown() ? "saved" : "saved; baseline unknown")
        : "finished; original retained";
  }
  return "unknown";
}
#endif

bool read(const char* name, XoscTrimSelection& selection) {
  uint8_t bytes[XoscTrimRecordSize];
  if (storage::readFixedSettingsFile(settings_fs, name, bytes, sizeof(bytes))
      && decodeXoscTrim(bytes, sizeof(bytes), selection)) return true;
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  if (!strcmp(name, combined_path) || !strcmp(name, combined_backup) || !strcmp(name, combined_temporary)) return false;
  return storage::readFixedSettingsFile(settings_fs, name, bytes, XoscTrimLegacyRecordSize)
      && decodeXoscTrim(bytes, XoscTrimLegacyRecordSize, selection);
#else
  return false;
#endif
}

bool recover(const char* main_path, const char* backup_path) {
  XoscTrimSelection checked;
  if (read(main_path, checked) || !read(backup_path, checked)) return true;
  if (settings_fs->exists(main_path) && !settings_fs->remove(main_path)) return false;
  return settings_fs->rename(backup_path, main_path) && read(main_path, checked);
}

bool saveRecord(const XoscTrimSelection& selection, const char* main_path,
                const char* backup_path, const char* temporary_path, bool legacy) {
  if (!settings_fs || !validFrequencyCorrection(selection.correction_ppb)
      || !recover(main_path, backup_path)) return false;
  if (settings_fs->exists(temporary_path) && !settings_fs->remove(temporary_path)) return false;
  if (settings_fs->exists(temporary_path)) return false;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  // LittleFS opens through a File bound to the filesystem. The temporary
  // path was removed above, so FILE_O_WRITE cannot append stale data.
  File file(*settings_fs);
  if (!file.open(temporary_path, FILE_O_WRITE)) return false;
#elif defined(RP2040_PLATFORM)
  File file = settings_fs->open(temporary_path, "w");
  if (!file) return false;
#else
  File file = settings_fs->open(temporary_path, "w", true);
  if (!file) return false;
#endif
  uint8_t bytes[XoscTrimRecordSize];
  size_t length = sizeof(bytes);
  if (legacy) {
    uint8_t old[XoscTrimLegacyRecordSize]; encodeLegacyXoscTrim(selection, old);
    memcpy(bytes, old, sizeof(old)); length = sizeof(old);
  } else encodeXoscTrim(selection, bytes);
  const bool written = file.write(bytes, length) == length;
  file.close();
  XoscTrimSelection checked;
  if (!written || !read(temporary_path, checked) || !sameCalibration(selection, checked)) return false;
  if (settings_fs->exists(backup_path) && !settings_fs->remove(backup_path)) return false;
  if (settings_fs->exists(main_path) && !settings_fs->rename(main_path, backup_path)) return false;
  const bool published = settings_fs->rename(temporary_path, main_path);
  if (!published || !read(main_path, checked) || !sameCalibration(selection, checked)) {
    // A failed rename/readback may have changed the public record anyway.
    // Remove it before restoring the last complete setting, without touching
    // radio hardware until the new public record has been verified.
    if (settings_fs->exists(main_path)) settings_fs->remove(main_path);
    if (settings_fs->exists(backup_path)) settings_fs->rename(backup_path, main_path);
    return false;
  }
  if (settings_fs->exists(backup_path)) settings_fs->remove(backup_path);
  return true;
}

#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
// Roll back both records after an interrupted paired update. The journal is
// published before either target changes and removed only after both verify.
constexpr size_t JournalSize = 26;
bool readJournal(uint8_t (&bytes)[JournalSize]) {
  if (!storage::readFixedSettingsFile(settings_fs, journal_path, bytes, sizeof(bytes))
      || memcmp(bytes, "XCJ1", 4) || (bytes[4] & ~3U)) return false;
  uint8_t checksum = 0;
  for (unsigned i = 0; i < JournalSize-1; ++i) checksum ^= bytes[i];
  if (checksum != bytes[JournalSize-1]) return false;
  XoscTrimSelection checked;
  return (!(bytes[4] & 1) || decodeXoscTrim(bytes+5, 8, checked))
      && (!(bytes[4] & 2) || decodeXoscTrim(bytes+13, 12, checked));
}

bool restoreJournal() {
  if (!settings_fs || !settings_fs->exists(journal_path)) return settings_fs != nullptr;
  uint8_t bytes[JournalSize];
  if (!readJournal(bytes)) return false;
  XoscTrimSelection selection;
  if (bytes[4] & 1) {
    if (!decodeXoscTrim(bytes+5, 8, selection)
        || !saveRecord(selection, path, backup, temporary, true)) return false;
  } else {
    if (settings_fs->exists(path) && !settings_fs->remove(path)) return false;
    if (settings_fs->exists(backup) && !settings_fs->remove(backup)) return false;
  }
  if (bytes[4] & 2) {
    if (!decodeXoscTrim(bytes+13, 12, selection)
        || !saveRecord(selection, combined_path, combined_backup, combined_temporary, false)) return false;
  } else {
    if (settings_fs->exists(combined_path) && !settings_fs->remove(combined_path)) return false;
    if (settings_fs->exists(combined_backup) && !settings_fs->remove(combined_backup)) return false;
  }
  return settings_fs->remove(journal_path) && !settings_fs->exists(journal_path);
}

bool beginJournal() {
  if (!restoreJournal() || !recover(path, backup) || !recover(combined_path, combined_backup)) return false;
  uint8_t bytes[JournalSize] = {'X','C','J','1'};
  XoscTrimSelection old;
  if (settings_fs->exists(path)) {
    if (!read(path, old)) return false;
    bytes[4] |= 1; uint8_t legacy[8]; encodeLegacyXoscTrim(old, legacy); memcpy(bytes+5, legacy, 8);
  }
  if (settings_fs->exists(combined_path)) {
    if (!read(combined_path, old)) return false;
    bytes[4] |= 2; uint8_t combined[XoscTrimRecordSize]; encodeXoscTrim(old, combined); memcpy(bytes+13, combined, 12);
  }
  for (unsigned i = 0; i < JournalSize-1; ++i) bytes[JournalSize-1] ^= bytes[i];
  if (settings_fs->exists(journal_temporary) && !settings_fs->remove(journal_temporary)) return false;
  File file = settings_fs->open(journal_temporary, "w", true);
  if (!file) return false;
  const bool written = file.write(bytes, sizeof(bytes)) == sizeof(bytes); file.close();
  uint8_t checked[JournalSize];
  if (!written || !storage::readFixedSettingsFile(settings_fs, journal_temporary, checked, sizeof(checked))
      || memcmp(bytes, checked, sizeof(bytes))) return false;
  return settings_fs->rename(journal_temporary, journal_path) && readJournal(checked);
}
#endif

bool save(const XoscTrimSelection& selection) {
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  if (!beginJournal()) return false;
  XoscTrimSelection old_format = selection; old_format.correction_ppb = 0;
  if (!saveRecord(old_format, path, backup, temporary, true)
      || !saveRecord(selection, combined_path, combined_backup, combined_temporary, false)) {
    restoreJournal(); return false;
  }
  if (!settings_fs->remove(journal_path) || settings_fs->exists(journal_path)) {
    restoreJournal(); return false;
  }
  return true;
#else
  return saveRecord(selection, path, backup, temporary, false);
#endif
}

void failPending(CustomLR2021Wrapper& radio) {
  radio.cancelXoscTrimApply();
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  radio.cancelFrequencyCorrectionApply();
  manual_correction_restore = pending_correction_attempted;
  manual_restore_started = millis();
  pending_correction_ready = pending_correction_attempted = false;
#endif
  pending = false;
  failed = true;
  rollback_failed = !save(previous_selection);
  if (!rollback_failed) saved_selection = previous_selection;
}

void formatSelection(char* reply, size_t capacity) {
  const char* suffix =
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
      (auto_trim.action() != AutoTrim::Action::Idle && auto_trim.action() != AutoTrim::Action::Complete)
        ? " (saved; auto trial running)" :
      manual_correction_restore ? " (failed; correction restoring)" :
#endif
      pending ? " (pending)" :
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
      auto_deferred_restore ? (auto_storage_error
          ? " (active trial and storage uncertain; reboot required)"
          : " (active trial may differ; reboot required)") :
      auto_storage_error ? " (storage uncertain; verify after reboot)" :
#endif
      failed
      ? rollback_failed ? " (failed; saved rollback failed)" : " (failed; previous saved setting restored)" : "";
  if (saved_selection.enabled) {
    snprintf(reply, capacity, "> %u,%u%s", saved_selection.a, saved_selection.b, suffix);
  } else {
    snprintf(reply, capacity, "> default%s", suffix);
  }
}
}  // namespace

bool loadLR2021CalibrationSettings(FILESYSTEM* fs, CustomLR2021Wrapper& radio) {
  settings_fs = fs;
  saved_selection = XoscTrimSelection{};
  previous_selection = XoscTrimSelection{};
  pending = failed = rollback_failed = false;
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  auto_trim=AutoTrim{}; boot_stable=false;
  auto_saved=auto_restore_error=auto_storage_error=auto_deferred_restore=false;
  auto_correction_needs_restore=auto_physical_uncertain=manual_correction_restore=false;
  boot_auto.reset(millis(),false,false,false);
#endif
  radio.cancelXoscTrimApply();
  if (!radio.supportsXoscTrim()) { failed = true; return false; }
  bool loaded = false;
  if (fs) {
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
    if (!restoreJournal()) {
      uint8_t journal[JournalSize];
      if (readJournal(journal)) {
        if (journal[4] & 2) decodeXoscTrim(journal+13, 12, saved_selection);
        else if (journal[4] & 1) decodeXoscTrim(journal+5, 8, saved_selection);
      }
      radio.configureFrequencyCorrection(saved_selection.correction_ppb);
      radio.configureBootXoscTrim(saved_selection.enabled, saved_selection.a, saved_selection.b);
      failed = rollback_failed = auto_storage_error = true;
      return false;
    }
    recover(combined_path, combined_backup);
    loaded = read(combined_path, saved_selection) || read(combined_backup, saved_selection);
    if (loaded) {
      XoscTrimSelection legacy;
      if ((read(path, legacy) || read(backup, legacy)) && !sameXoscTrim(legacy, saved_selection)) {
        // An older image may have changed the legacy record after downgrade.
        // Its edit invalidates the old digital correction and takes priority.
        saved_selection = legacy;
      }
    }
#endif
    if (!loaded) { recover(path, backup); loaded = read(path, saved_selection) || read(backup, saved_selection); }
  }
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  if (!radio.configureFrequencyCorrection(saved_selection.correction_ppb)) {
    failed = true;
    return false;
  }
#endif
  if (!radio.configureBootXoscTrim(saved_selection.enabled, saved_selection.a, saved_selection.b)) {
    failed = true;
    return false;
  }
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  const bool records_present=fs && (fs->exists(path) || fs->exists(backup)
      || fs->exists(combined_path) || fs->exists(combined_backup));
  const bool policy_ok=loadAutoPolicy();
  boot_auto.reset(millis(),boot_autostart_enabled,hasCalibration(),policy_ok && (!records_present || loaded));
#endif
  return loaded;
}

bool LR2021CalibrationSettingsReady() {
  return !failed && !rollback_failed;
}

void loopLR2021CalibrationSettings(CustomLR2021Wrapper& radio) {
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  const uint32_t boot_now=millis();
  if (boot_auto.inProgress() && auto_trim.action()==AutoTrim::Action::Complete) {
    boot_auto.finished(boot_now,auto_saved && hasCalibration(),
        !auto_deferred_restore && !auto_storage_error && !auto_physical_uncertain);
  }
  if (boot_auto.inProgress() && boot_auto.expired(boot_now)) auto_trim.cancel();
  const auto boot_state=boot_auto.state();
  const bool boot_waiting=boot_state==BootAutoTrim::State::WaitingRadio
      || boot_state==BootAutoTrim::State::Ready || boot_state==BootAutoTrim::State::Backoff;
  if (boot_waiting && !boot_auto.nextDelay(boot_now)
      && !pending && !manual_correction_restore && !auto_deferred_restore && !auto_storage_error
      && (auto_trim.action()==AutoTrim::Action::Idle || auto_trim.action()==AutoTrim::Action::Complete)) {
    uint32_t frequency=0, active_frequency=0;
    const bool profile_ready=mesh::radioDirection::radioReceiveAllowed()
        && LR2021CalibrationSettingsReady() && radio.backgroundAutoTrimProfile(frequency);
    const uint32_t generation=radio.profiles() ? radio.profiles()->generation[0] : 0;
    if (!profile_ready) boot_stable=false;
    else if (!boot_stable || frequency!=boot_stable_frequency || generation!=boot_stable_generation) {
      boot_stable=true; boot_stable_since=boot_now;
      boot_stable_frequency=frequency; boot_stable_generation=generation;
    }
    const bool eligible=profile_ready && radio.backgroundAutoTrimEligible(active_frequency)
        && active_frequency==frequency;
    if (boot_auto.ready(boot_now,eligible && boot_stable && uint32_t(boot_now-boot_stable_since)>=5000)) {
      boot_auto.started(boot_now);
      boot_stable=false; // Any retry observes a fresh configuration-stability window.
      if (auto_trim.action()==AutoTrim::Action::Complete) auto_trim.reset();
      if (!autoStorageReady() || !beginAutomaticTrial(radio,frequency,boot_now,true))
        boot_auto.finished(boot_now,false,false);
    }
  }
  if (manual_correction_restore) {
    if (!mesh::radioDirection::radioReceiveAllowed()
        || uint32_t(millis() - manual_restore_started) >= ApplyTimeoutMillis) {
      radio.cancelFrequencyCorrectionApply(); manual_correction_restore = false;
      auto_deferred_restore = true; return;
    }
    const auto restored = radio.trySetFrequencyCorrection(previous_selection.correction_ppb);
    if (restored == RadioParamApplyResult::APPLIED) manual_correction_restore = false;
    else if (restored == RadioParamApplyResult::FAILED) {
      manual_correction_restore = false; auto_deferred_restore = true;
    }
    return;
  }
  if (auto_trim.action() != AutoTrim::Action::Idle && auto_trim.action() != AutoTrim::Action::Complete) {
    const uint32_t now = millis();
    if (!mesh::radioDirection::radioReceiveAllowed()) {
      radio.cancelXoscTrimApply();
      radio.cancelFrequencyCorrectionApply();
      auto_deferred_restore = auto_physical_uncertain || auto_correction_needs_restore
          || !sameXoscTrim(auto_trim.current(), saved_selection);
      auto_trim.abandon();
      return;
    }
    uint32_t frequency = 0;
    if (!radio.profiles() || radio.profiles()->enabled()
        || radio.profiles()->generation[0] != auto_generation
        || radio.receiveProfileGeneration() != auto_generation
        || !radio.getFrequencyHzForAutoTrim(frequency) || frequency != auto_frequency) auto_trim.cancel();
    auto_trim.tick(now);
    // Leave the original profile up long enough for the CLI reply to go out.
    if (uint32_t(now - auto_started) < ApplyHandoffMillis) return;
    const auto action = auto_trim.action();
    if (action == AutoTrim::Action::Apply || action == AutoTrim::Action::Restore) {
      if (action == AutoTrim::Action::Restore) {
        if (!auto_restore_timing) { auto_restore_timing = true; auto_restore_started = now; }
        if (uint32_t(now - auto_restore_started) >= 30000) {
          radio.cancelXoscTrimApply(); radio.cancelFrequencyCorrectionApply();
          auto_deferred_restore = true; auto_trim.abandon(); return;
        }
      }
      if (action == AutoTrim::Action::Restore && auto_restore_error
          && uint32_t(now - auto_restore_failed_at) < 1000) return;
      const auto& target = auto_trim.target();
      const bool restore_correction = action == AutoTrim::Action::Restore && auto_correction_needs_restore;
      const auto result = restore_correction
          ? radio.trySetFrequencyCorrection(auto_previous_correction)
          : radio.trySetXoscTrim(target.enabled, target.a, target.b);
      if (result == RadioParamApplyResult::APPLIED) {
        auto_restore_error = false;
        auto_physical_uncertain = false;
        if (restore_correction) { auto_correction_needs_restore = false; return; }
        auto_trim.applied(true, now);
      } else if (result == RadioParamApplyResult::FAILED) {
        auto_physical_uncertain = true;
        auto_restore_error = action == AutoTrim::Action::Restore;
        if (auto_restore_error) {
          auto_restore_failed_at = now;
          if (++auto_restore_attempts >= 3) {
            radio.cancelXoscTrimApply();
            radio.cancelFrequencyCorrectionApply();
            auto_deferred_restore = true;
            auto_trim.abandon();
          }
        }
        auto_trim.applied(false, now);
      }
    } else if (action == AutoTrim::Action::Commit) {
      if (!auto_correction_calculated) {
        const int32_t residual = auto_trim.verifiedOffsetHz();
        if (residual > -400 && residual < 400) {
          auto_target_correction = auto_previous_correction;
          auto_correction_calculated = true;
          auto_trim.skipDigitalCorrection();
        } else {
        // Add 75% of the verified residual to the existing correction. The
        // peer-derived offset is relative, not an absolute frequency standard.
        const int64_t numerator = int64_t(residual) * 750000000LL;
        const int64_t delta = numerator >= 0
            ? (numerator + auto_frequency / 2) / auto_frequency
            : (numerator - auto_frequency / 2) / auto_frequency;
        const int64_t proposed = int64_t(auto_previous_correction) + delta;
        if (proposed < -MaxFrequencyCorrectionPpb || proposed > MaxFrequencyCorrectionPpb) {
          auto_trim.committed(false); return;
        }
        auto_target_correction = int32_t(proposed); auto_correction_calculated = true;
        }
      }
      if (!auto_trim.digitalVerified()) {
        auto_correction_needs_restore = true;
        const auto result = radio.trySetFrequencyCorrection(auto_target_correction);
        if (result == RadioParamApplyResult::BUSY) return;
        if (result == RadioParamApplyResult::FAILED) {
          auto_physical_uncertain = true; auto_trim.committed(false); return;
        }
        auto_trim.beginDigitalVerification(now);
        return;
      }
      XoscTrimSelection target = auto_trim.target(); target.correction_ppb = auto_target_correction;
      const bool saved = save(target);
      if (saved) { saved_selection = target; auto_correction_needs_restore = false; }
      auto_saved = saved; auto_storage_error = !saved; auto_trim.committed(saved);
    }
    return;
  }
#endif
  if (!pending) return;
  const uint32_t elapsed = uint32_t(millis() - pending_started);
  // Let the acknowledgement leave with the previous calibration first.
  if (elapsed < ApplyHandoffMillis) return;
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  if (!pending_correction_ready) {
    pending_correction_attempted = true;
    const auto correction = radio.trySetFrequencyCorrection(saved_selection.correction_ppb);
    if (correction == RadioParamApplyResult::APPLIED) pending_correction_ready = true;
    else if (correction == RadioParamApplyResult::FAILED
        || elapsed - ApplyHandoffMillis >= ApplyTimeoutMillis) failPending(radio);
    return;
  }
#endif
  const auto result = radio.trySetXoscTrim(saved_selection.enabled,
      saved_selection.a, saved_selection.b);
  if (result == RadioParamApplyResult::APPLIED) {
    pending = false;
    failed = rollback_failed = false;
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
    auto_deferred_restore = auto_storage_error = false;
    pending_correction_ready = pending_correction_attempted = false;
#endif
  } else if (result == RadioParamApplyResult::FAILED
      || (result == RadioParamApplyResult::BUSY
          && elapsed - ApplyHandoffMillis >= ApplyTimeoutMillis)) {
    // Give the wrapper its bounded RX-only admission attempt at the deadline.
    // Protected work that remains busy must still terminate this request.
    failPending(radio);
  }
}

bool handleLR2021CalibrationCommand(const char* command, char* reply,
                                   size_t capacity, CustomLR2021Wrapper& radio) {
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  if (!strcmp(command,"get radio.xtrim.autostart")) {
    if (!radio.supportsXoscTrim()) snprintf(reply,capacity,"Error: auto trim needs a crystal LR2021");
    else {
      char retry[16]="none";
      if (boot_auto.state()==BootAutoTrim::State::Backoff)
        snprintf(retry,sizeof(retry),"%lu",(unsigned long)boot_auto.nextDelay(millis()));
      const char* blocked=radio.profiles() && radio.profiles()->enabled()?"radio2":"stable-primary-or-owner";
      snprintf(reply,capacity,"> %s; state=%s attempts=%u/2 next_retry_ms=%s blocked=%s",
          boot_autostart_enabled?"on":"off",boot_auto.stateName(),unsigned(boot_auto.attempts()),retry,
          boot_auto.state()==BootAutoTrim::State::WaitingRadio?blocked:"none");
    }
    return true;
  }
  constexpr char policy_command[]="set radio.xtrim.autostart ";
  if (!strncmp(command,policy_command,sizeof(policy_command)-1)) {
    const char* value=command+sizeof(policy_command)-1;
    if (!radio.supportsXoscTrim()) snprintf(reply,capacity,"Error: auto trim needs a crystal LR2021");
    else if (strcmp(value,"on") && strcmp(value,"off")) snprintf(reply,capacity,"Error: use set radio.xtrim.autostart on|off");
    else if (!saveAutoPolicy(!strcmp(value,"on"))) snprintf(reply,capacity,"Error: auto trim policy save failed; unchanged");
    else {
      boot_autostart_enabled=!strcmp(value,"on");
      const bool was_background=boot_auto.inProgress();
      boot_auto.setEnabled(millis(),boot_autostart_enabled);
      if (!boot_autostart_enabled && was_background) auto_trim.cancel();
      snprintf(reply,capacity,"OK - auto trim startup %s",value);
    }
    return true;
  }
  if (!strcmp(command, "get radio.xtrim.correction")) {
    snprintf(reply, capacity, "> %ld ppb (active); %ld ppb (saved)",
        long(radio.frequencyCorrectionPpb()), long(saved_selection.correction_ppb));
    return true;
  }
  constexpr char peer_command[]="get radio.xtrim.auto.peer";
  if (!strncmp(command,peer_command,sizeof(peer_command)-1)
      && (!command[sizeof(peer_command)-1] || command[sizeof(peer_command)-1]==' ')) {
    const char* value=command+sizeof(peer_command)-1;
    if (value[0]!=' ' || value[1]<'0' || value[1]>'2' || value[2]) {
      snprintf(reply,capacity,"Error: use get radio.xtrim.auto.peer 0|1|2");
    } else {
      const uint8_t index=uint8_t(value[1]-'0');
      const uint8_t* key=nullptr; uint32_t signed_time=0; uint8_t samples=0;
      if (!auto_trim.peerStatus(index,key,signed_time,samples)) snprintf(reply,capacity,"> none");
      else {
        constexpr char hex[]="0123456789abcdef";
        char encoded[65];
        for (unsigned i=0;i<32;++i) { encoded[2*i]=hex[key[i]>>4]; encoded[2*i+1]=hex[key[i]&15]; }
        encoded[64]=0;
        snprintf(reply,capacity,"> index=%u,key=%s,signed_time=%lu,samples=%u,locked=%u",
            unsigned(index),encoded,(unsigned long)signed_time,unsigned(samples),auto_trim.cohortLocked()?1U:0U);
      }
    }
    return true;
  }
  if (!strcmp(command, "get radio.xtrim.auto")) {
    const auto& current = auto_trim.current();
    char trial[20];
    if (current.enabled) snprintf(trial, sizeof(trial), "%u,%u", current.a, current.b);
    else snprintf(trial, sizeof(trial), "default");
    snprintf(reply, capacity, "> %s; trial=%s phase=%u samples=%u/%u baseline=%s; startup=%s peers=%u/3 confidence=%s",
        autoState(), trial, unsigned(auto_trim.round()), unsigned(auto_trim.sampleCount()),
        unsigned(auto_trim.requiredSampleCount()), auto_trim.baselineKnown() ? "known" : "unknown",boot_auto.stateName(),
        unsigned(auto_trim.peerCount()),auto_trim.confidenceName());
    return true;
  }
  if (!strcmp(command, "set radio.xtrim auto cancel")) {
    boot_auto.cancel();
    if (auto_trim.action() == AutoTrim::Action::Idle || auto_trim.action() == AutoTrim::Action::Complete) {
      snprintf(reply, capacity, "OK - auto trim not active");
      return true;
    }
    auto_trim.cancel();
    snprintf(reply, capacity, "OK - auto trim stopping; restoring original");
    return true;
  }
  if (!strcmp(command, "set radio.xtrim auto")) {
    uint32_t frequency = 0;
    if (auto_trim.action() == AutoTrim::Action::Complete) {
      if (boot_auto.inProgress()) boot_auto.finished(millis(),auto_saved && hasCalibration(),
          !auto_deferred_restore && !auto_storage_error && !auto_physical_uncertain);
      auto_trim.reset();
    }
    if (!radio.supportsXoscTrim()) {
      snprintf(reply, capacity, "Error: auto trim needs a crystal LR2021");
    } else if (!mesh::radioDirection::radioReceiveAllowed() || auto_deferred_restore || auto_storage_error) {
      snprintf(reply, capacity, "Error: RX and verified saved trim required; reboot or set trim");
    } else if (pending || manual_correction_restore || auto_trim.action() != AutoTrim::Action::Idle
        || !radio.profiles() || radio.profiles()->enabled()
        || radio.receiveProfileGeneration() != radio.profiles()->generation[0]
        || !radio.getFrequencyHzForAutoTrim(frequency)) {
      snprintf(reply, capacity, "Error: radio busy or auto trim already active");
    } else if (!autoStorageReady()) {
      snprintf(reply, capacity, "Error: auto trim storage unavailable; radio unchanged");
    } else {
      const uint32_t now = millis();
      if (beginAutomaticTrial(radio,frequency,now)) {
        boot_auto.cancel(); // Explicit one-shot owns this boot; no automatic restart.
        snprintf(reply, capacity, "OK - auto trim listening; prefers 3 nearby radios");
      } else snprintf(reply, capacity, "Error: auto trim could not start");
    }
    return true;
  }
#endif
  if (!strcmp(command, "get radio.freqerror")) {
    radio.formatFrequencyError(reply, capacity);
    return true;
  }
  const bool get = !strncmp(command, "get ", 4);
  if (!get && strncmp(command, "set ", 4)) return false;
  constexpr char key[] = "radio.xtrim";
  constexpr size_t length = sizeof(key) - 1;
  if (strncmp(command + 4, key, length)
      || (command[4 + length] && command[4 + length] != ' ')) return false;
  if (!radio.supportsXoscTrim()) {
    snprintf(reply, capacity, "Error: radio.xtrim requires an LR2021 with a crystal, not a TCXO");
    return true;
  }
  const char* value = command + 4 + length;
  if (get) {
    if (*value) snprintf(reply, capacity, "Error: use get radio.xtrim");
    else formatSelection(reply, capacity);
    return true;
  }
  while (*value == ' ') ++value;
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  if (manual_correction_restore) {
    snprintf(reply, capacity, "Error: prior correction restoring");
    return true;
  }
  if (auto_trim.action() != AutoTrim::Action::Idle && auto_trim.action() != AutoTrim::Action::Complete) {
    snprintf(reply, capacity, "Error: auto trim active; cancel and wait for restore");
    return true;
  }
#endif
  XoscTrimSelection candidate;
  if (!parseXoscTrim(value, candidate)) {
    snprintf(reply, capacity, "Error: use set radio.xtrim <0-47>[,<0-47>] or default");
    return true;
  }
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  boot_auto.cancel(); // A valid manual request reserves this boot even if unchanged.
#endif
  if (sameCalibration(candidate, saved_selection) && !failed
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
      && !auto_deferred_restore && !auto_storage_error
#endif
      ) {
    snprintf(reply, capacity, pending ? "OK - oscillator calibration pending" : "OK - oscillator calibration unchanged");
    return true;
  }
  if (!save(candidate)) {
    snprintf(reply, capacity, "Error: oscillator calibration save failed; radio unchanged");
    return true;
  }
  if (!pending) {
    previous_selection = XoscTrimSelection{};
    previous_selection.enabled = radio.getXoscTrim(previous_selection.a, previous_selection.b);
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
    previous_selection.correction_ppb = radio.frequencyCorrectionPpb();
#endif
  }
  saved_selection = candidate;
  pending = true;
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  pending_correction_ready = false;
#endif
  failed = rollback_failed = false;
  pending_started = millis();
  snprintf(reply, capacity, "OK - oscillator calibration applying in 2s");
  return true;
}

#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
void observeLR2021AutoTrimAdvert(const uint8_t* sender_key, uint32_t signed_time,
    uint32_t utc_now, const PacketFrequencyObservation& frequency, bool eligible) {
  AutoTrimSample sample;
  sample.sender_key = sender_key; sample.signed_time = signed_time; sample.utc_now = utc_now;
  sample.frequency_hz = frequency.frequency_hz; sample.offset_hz = frequency.offset_hz;
  sample.captured_at_ms = frequency.captured_at_ms; sample.now_ms = millis();
  sample.verified = true; sample.packet_bound = frequency.valid; sample.direct_rf = eligible;
  auto_trim.observe(sample);
}
#endif

}}  // namespace mesh::lr2021

#endif  // USE_LR2021
