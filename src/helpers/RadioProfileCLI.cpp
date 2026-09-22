#include "RadioProfileCLI.h"
#include "CarrierWaveCLI.h"
#include "RadioProfileCommandUtils.h"
#include "radiolib/RXPowerSaving.h"
#include <Arduino.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace {
constexpr size_t ImageSize = 24;
const char* const ImagePath = "/radio_profiles";
const char* const BackupPath = "/radio_profiles.bak";
const char* const TempPath = "/radio_profiles.tmp";
// Never considered a boot-time committed image. Losing power before the
// acknowledgement leaves the previous saved radio untouched.
const char* const ReplyPath = "/radio_profiles.reply";
uint32_t checksum(const uint8_t* p, size_t n) {
  uint32_t crc = 0xffffffffU;
  while (n--) {
    crc ^= *p++;
    for (int i = 0; i < 8; ++i) crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
  }
  return crc;
}
const char* modeName(RadioProfileMode m) {
  return m == RadioProfileMode::RxTx ? "rxtx" : m == RadioProfileMode::Rx ? "rx" : "off";
}
bool parseMode(const char* text, RadioProfileMode& mode) {
  if (!strcmp(text, "rx")) mode = RadioProfileMode::Rx;
  else if (!strcmp(text, "rxtx") || !strcmp(text, "rx&tx")) mode = RadioProfileMode::RxTx;
  else return false;
  return true;
}
unsigned split(char* s, char* parts[], unsigned maximum) {
  unsigned count = 0;
  do {
    if (count == maximum || !*s) return 0;
    parts[count++] = s;
    char* comma = strchr(s, ',');
    if (!comma) break;
    *comma = 0;
    s = comma + 1;
  } while (true);
  return count;
}
bool parsePreamble(const char* s, uint16_t& result) {
  return cli::parseRadioPreamble(s, result);
}
bool validReplySetting(uint8_t value) {
  return (value >= 0xb0 && value <= 0xb4) || value == 0xba || value == 0xbb;
}
uint32_t remainingMillis(uint32_t end, uint32_t now) {
  const int32_t remaining = (int32_t)(end - now);
  return remaining > 0 ? (uint32_t)remaining : 0;
}
}

RadioProfileCLI::ImageReadResult RadioProfileCLI::readImage(
    const char* path, uint8_t* bytes, size_t size) {
  if (!fs_) return ImageReadResult::Unreadable;
#if defined(NRF52_PLATFORM)
  File file(*fs_);
  if (!file.open(path, FILE_O_READ)) {
    return fs_->exists(path) ? ImageReadResult::Unreadable
                             : ImageReadResult::Missing;
  }
#elif defined(STM32_PLATFORM)
  File file = fs_->open(path, FILE_O_READ);
#else
  File file = fs_->open(path, "r");
#endif
  if (!file) {
    return fs_->exists(path) ? ImageReadResult::Unreadable
                             : ImageReadResult::Missing;
  }
  const bool right_size = file.size() == size;
  const bool read_complete = right_size && file.read(bytes, size) == (int)size;
  file.close();
  if (!right_size) return ImageReadResult::Invalid;
  if (!read_complete) return ImageReadResult::Unreadable;
  uint32_t stored;
  memcpy(&stored, bytes + size - 4, 4);
  return bytes[0] == 'R' && bytes[1] == '2' && bytes[2] == 1
      && stored == checksum(bytes, size - 4)
      ? ImageReadResult::Valid : ImageReadResult::Invalid;
}

bool RadioProfileCLI::writeImage(const char* path, const uint8_t* bytes, size_t size) {
  if (fs_->exists(path) && !fs_->remove(path)) return false;
#if defined(NRF52_PLATFORM)
  File file(*fs_);
  if (!file.open(path, FILE_O_WRITE)) return false;
#elif defined(STM32_PLATFORM)
  File file = fs_->open(path, FILE_O_WRITE);
#else
  File file = fs_->open(path, "w");
#endif
  if (!file) return false;
  bool ok = file.write(bytes, size) == size;
  file.flush();
  file.close();
  uint8_t verify[ImageSize];
  return ok && readImage(path, verify, size) == ImageReadResult::Valid
      && memcmp(bytes, verify, size) == 0;
}

bool RadioProfileCLI::save(const RadioProfileConfig& config, uint16_t preamble, RadioCrossMode cross) {
  if (!discardCorruptSavedImagesForWrite()) return false;
  return prepareSavedImage(TempPath, config, preamble, cross) && commitSavedImage(TempPath);
}

bool RadioProfileCLI::discardCorruptSavedImagesForWrite() {
  if (!hold_) return true;
  if (!fs_ || !recoverable_corrupt_store_) return false;

  // This is deliberately a user-triggered recovery, never a boot-time cleanup.
  // The flag is set only after every present candidate was read completely and
  // proved invalid, so no valid radio2 configuration is discarded. This store
  // contains only radio-profile settings; node identity, contacts, and normal
  // Companion preferences live elsewhere.
  const char* const paths[] = {ImagePath, BackupPath};
  for (const char* path : paths) {
    if (fs_->exists(path) && !fs_->remove(path)) return false;
  }
  hold_ = false;
  recoverable_corrupt_store_ = false;
  return true;
}

bool RadioProfileCLI::prepareSavedImage(const char* path, const RadioProfileConfig& config,
                                      uint16_t preamble, RadioCrossMode cross) {
  if (!fs_ || hold_) return false;
  uint8_t image[ImageSize] = {'R', '2', 1, (uint8_t)config.mode};
  memcpy(image + 4, &config.params.freq, 4);
  memcpy(image + 8, &config.params.bw, 4);
  image[12] = config.params.sf; image[13] = config.params.cr;
  memcpy(image + 14, &config.params.preamble, 2);
  memcpy(image + 16, &preamble, 2);
  image[18] = (uint8_t)cross;
  image[19] = reply_setting_;
  const uint32_t crc = checksum(image, ImageSize - 4);
  memcpy(image + ImageSize - 4, &crc, 4);
  return writeImage(path, image, sizeof(image));
}

bool RadioProfileCLI::commitSavedImage(const char* path) {
  if (!fs_ || hold_) return false;
  if (fs_->exists(ImagePath)) {
    if (fs_->exists(BackupPath) && !fs_->remove(BackupPath)) return false;
    if (!fs_->rename(ImagePath, BackupPath)) return false;
  }
  if (!fs_->rename(path, ImagePath)) {
    if (fs_->exists(BackupPath) && !fs_->rename(BackupPath, ImagePath)) hold_ = true;
    return false;
  }
  if (fs_->exists(BackupPath)) fs_->remove(BackupPath);
  return true;
}

void RadioProfileCLI::begin(FILESYSTEM* fs, Radio* radio, RTCClock* rtc, bool infrastructure_replies) {
  fs_ = fs; radio_ = radio; rtc_ = rtc; last_ms_ = millis();
  hold_ = false;
  recoverable_corrupt_store_ = false;
  infrastructure_replies_ = infrastructure_replies;
  if (radio_ && radio_->profiles()) {
    radio_->profiles()->reply_tx = infrastructure_replies ? RADIO_TX_BOTH : RADIO_TX_AUTO;
    radio_->profiles()->reply_force = false;
  }
  if (!fs_ || !radio_ || !radio_->profiles()) return;
  uint8_t bytes[ImageSize];
  const ImageReadResult primary_image = readImage(ImagePath, bytes, sizeof(bytes));
  bool loaded = primary_image == ImageReadResult::Valid;
  ImageReadResult backup_image = ImageReadResult::Missing;
  if (!loaded) backup_image = readImage(BackupPath, bytes, sizeof(bytes));
  if (!loaded && backup_image == ImageReadResult::Valid) {
    // Use the verified backup even when repairing the interrupted save is
    // impossible. Keep writes held so the sole committed image stays intact.
    loaded = true;
    hold_ = (fs_->exists(ImagePath) && !fs_->remove(ImagePath))
        || !fs_->rename(BackupPath, ImagePath);
  }
  if (!loaded) {
    hold_ = primary_image != ImageReadResult::Missing
        || backup_image != ImageReadResult::Missing;
    // An unreadable file may be a transient filesystem fault. Do not remove
    // it. A later explicit setter can recreate only files proven corrupt.
    recoverable_corrupt_store_ = hold_
        && primary_image != ImageReadResult::Unreadable
        && backup_image != ImageReadResult::Unreadable
        && (primary_image == ImageReadResult::Invalid
            || backup_image == ImageReadResult::Invalid);
    return;
  }
  saved_.mode = (RadioProfileMode)bytes[3];
  memcpy(&saved_.params.freq, bytes + 4, 4);
  memcpy(&saved_.params.bw, bytes + 8, 4);
  saved_.params.sf = bytes[12]; saved_.params.cr = bytes[13];
  memcpy(&saved_.params.preamble, bytes + 14, 2);
  memcpy(&primary_preamble_, bytes + 16, 2);
  cross_ = (RadioCrossMode)bytes[18];
  reply_setting_ = validReplySetting(bytes[19]) ? bytes[19] : 0;
  if ((uint8_t)saved_.mode > 2 || (uint8_t)cross_ > 2
      || (saved_.mode != RadioProfileMode::Off && !radio_->validateProfile(saved_.params))
      || (primary_preamble_ && (primary_preamble_ < 8 || primary_preamble_ > RadioProfiles::MaxPreamble))) {
    // A CRC-valid image can still be semantically stale (for example, after
    // an older build wrote a now-unsupported value). Do not recreate it if a
    // structurally valid backup exists; that backup is the last known-good
    // radio-profile image and must remain available for manual recovery.
    const ImageReadResult backup_image = readImage(BackupPath, bytes, sizeof(bytes));
    hold_ = true; saved_ = {}; primary_preamble_ = 0; cross_ = RadioCrossMode::Auto;
    recoverable_corrupt_store_ = backup_image != ImageReadResult::Valid
        && backup_image != ImageReadResult::Unreadable;
    reply_setting_ = 0;  // reject the entire image, including its reply override
    return;
  }
  stagePrimary(primary_preamble_, false);
  publish();
}

bool RadioProfileCLI::savePrimaryPreamble(uint16_t symbols) {
  if (symbols && (symbols < 8 || symbols > RadioProfiles::MaxPreamble)) return false;
  if (!radio_ || !radio_->profiles()) return symbols == 0;
  if (symbols == primary_preamble_) return true;
  if (hasReplyMutation()) return false;
  if (!save(saved_, symbols, cross_)) return false;
  primary_preamble_ = symbols;
  return true;
}

bool RadioProfileCLI::acceptsPrimary(float freq, float bw, uint8_t sf, uint8_t cr, uint16_t preamble) const {
  if (!radio_ || !radio_->profiles()) return preamble == 0;
  auto preview = *radio_->profiles();
  auto& p = preview.primary;
  p.freq = freq; p.bw = bw; p.sf = sf; p.cr = cr; p.preamble = preamble;
  return radio_->validateProfile(p) && preview.automaticPreambleFits();
}

void RadioProfileCLI::publish() {
  if (!radio_ || !radio_->profiles()) return;
  radio_->profiles()->cross = cross_;
  radio_->profiles()->reply_tx = infrastructure_replies_
      ? (validReplySetting(reply_setting_) ? reply_setting_ & 7 : RADIO_TX_BOTH) : RADIO_TX_AUTO;
  radio_->profiles()->reply_force = infrastructure_replies_
      && validReplySetting(reply_setting_) && (reply_setting_ & 8);
  if (publish_pending_ && (int32_t)(millis() - publish_after_ms_) < 0) return;
  publish_pending_ = false;
  radio_->profiles()->setSecondary(temp_active_ ? temporary_ : saved_, temp_active_);
}

bool RadioProfileCLI::stageRemoteMutation(RemoteMutation kind, const RadioProfileConfig& config,
                                         uint32_t duration_ms, uint8_t delete_mask) {
  if (hasReplyMutation()) return false;
  remote_mutation_ = kind;
  ++remote_generation_;
  remote_config_ = config;
  remote_duration_ms_ = duration_ms;
  remote_start_ms_ = millis() + 2000;
  remote_delete_mask_ = delete_mask;
  remote_delivered_ = false;
  remote_commit_retry_ms_ = 0;
  return true;
}

bool RadioProfileCLI::finishReplyMutation(bool delivered) {
  if (delivered) { remote_delivered_ = hasReplyMutation(); return true; }
  const auto kind = remote_mutation_;
  remote_mutation_ = RemoteMutation::None;
  remote_delivered_ = false;
  // This file was never committed, so cancellation needs no risky rollback of
  // the only saved image. Failure to remove scratch cannot activate it at boot.
  return (kind != RemoteMutation::Saved && kind != RemoteMutation::Off)
      || !fs_ || !fs_->exists(ReplyPath) || fs_->remove(ReplyPath);
}

bool RadioProfileCLI::applyReplyMutation() {
  const auto kind = remote_mutation_;
  if ((kind == RemoteMutation::Saved || kind == RemoteMutation::Off)
      && !commitSavedImage(ReplyPath)) return false;
  remote_mutation_ = RemoteMutation::None;
  remote_delivered_ = false;
  if (kind == RemoteMutation::None) return true;
  if (kind == RemoteMutation::Temporary) {
    pending_temporary_ = remote_config_;
    pending_duration_ms_ = remote_duration_ms_;
    temp_start_ms_ = remote_start_ms_;  // waiting never extends the hard lease
    temp_pending_ = true;
  } else if (kind == RemoteMutation::Saved) {
    saved_ = remote_config_;
    publish_pending_ = true; publish_after_ms_ = millis();
  } else if (kind == RemoteMutation::DeleteTemp) {
    for (unsigned i = 0; i < 4; ++i) if (remote_delete_mask_ & (1U << i)) {
      if (schedules_[4+i].started) { temp_active_ = false; temp_remaining_ms_ = 0; }
      schedules_[4+i] = {};
    }
  } else {
    if (kind == RemoteMutation::Off) { saved_ = {}; schedule_retry_ms_ = 0; }
    temp_pending_ = temp_active_ = false; temp_remaining_ms_ = 0;
    for (auto& s : schedules_) if (kind == RemoteMutation::Off || s.temporary) s = {};
  }
  // loop() owns publishing, after all completion/failure callbacks have
  // returned and Dispatcher no longer owns either response packet.
  return true;
}

void RadioProfileCLI::loop() {
  if (!radio_ || !radio_->profiles()) return;
  const uint32_t now = millis(), elapsed = now - last_ms_;
  last_ms_ = now;
  remote_commit_retry_ms_ = elapsed >= remote_commit_retry_ms_ ? 0 : remote_commit_retry_ms_ - elapsed;
  if (remote_delivered_ && !remote_commit_retry_ms_ && !applyReplyMutation()) {
    // Keep the accepted operation pending and the old channel live if the
    // post-ACK atomic rename fails. Never report/apparently apply an unsaved
    // tuple, and avoid a hot storage retry loop.
    remote_commit_retry_ms_ = 1000;
  }
  if (!hasReplyMutation() && temp_pending_ && (int32_t)(now - temp_start_ms_) >= 0) {
    temporary_ = pending_temporary_;
    const uint32_t late = now - temp_start_ms_;
    temp_remaining_ms_ = late < pending_duration_ms_ ? pending_duration_ms_ - late : 0;
    temp_pending_ = false; temp_active_ = temp_remaining_ms_ != 0;
  } else if (temp_active_) {
    if (elapsed >= temp_remaining_ms_) { temp_remaining_ms_ = 0; temp_active_ = false; }
    else temp_remaining_ms_ -= elapsed;
  }
  const uint32_t epoch = rtc_ ? rtc_->getCurrentTime() : 0;
  // Expire every old lease before starting its successor. Slot insertion order
  // must not let an expired entry clear a session started earlier in this loop.
  for (auto& s : schedules_) {
    if (s.active && s.temporary && (!remainingMillis(s.end_ms, now) || epoch >= s.end)) {
      if (s.started) { temp_active_ = false; temp_remaining_ms_ = 0; }
      s = {};
    }
  }
  schedule_retry_ms_ = elapsed >= schedule_retry_ms_ ? 0 : schedule_retry_ms_ - elapsed;
  while (!schedule_retry_ms_ && !hasReplyMutation()) {
    Schedule* next = nullptr;
    for (auto& s : schedules_) {
      if (s.active && !s.temporary && epoch >= s.start && (!next || s.start < next->start)) next = &s;
    }
    if (!next) break;
    if (!save(next->config, primary_preamble_, cross_)) {
      // Keep the requested time and order intact. A failed earlier entry must
      // not be retried after a later one and overwrite the newer settings.
      schedule_retry_ms_ = 60000;
      break;
    }
    saved_ = next->config; *next = {};
  }
  for (auto& s : schedules_) {
    if (!s.active || !s.temporary || s.started || epoch < s.start || temp_active_ || temp_pending_ || hasReplyMutation()) continue;
    temporary_ = s.config;
    temp_remaining_ms_ = remainingMillis(s.end_ms, now);
    const uint32_t epoch_left = (s.end - epoch) * 1000UL;
    if (temp_remaining_ms_ > epoch_left) temp_remaining_ms_ = epoch_left;
    temp_active_ = true; s.started = true;
  }
  publish();
}

bool RadioProfileCLI::parseSuffix(const char* input, unsigned fields, char* legacy,
                                 size_t capacity, uint16_t& preamble) {
  return cli::parseRadioPreambleSuffix(input, fields, legacy, capacity, preamble);
}

void RadioProfileCLI::appendPreamble(char* reply, size_t capacity, uint8_t profile) const {
  if (!capacity || !radio_ || !radio_->profiles()) return;
  const size_t used = strlen(reply);
  if (used < capacity) snprintf(reply + used, capacity - used, ",preamble=%u",
                               (unsigned)radio_->profilePreamble(profile));
  appendChirpWarning(reply, capacity, *radio_->profiles());
}

void RadioProfileCLI::appendSavedPreamble(char* reply, size_t capacity, uint8_t sf, float bw) const {
  if (!capacity || !radio_ || !radio_->profiles()) return;
  auto preview = *radio_->profiles();
  preview.primary.sf = sf; preview.primary.bw = bw;
  preview.primary.preamble = primary_preamble_;
  if (preview.primary != radio_->profiles()->primary) preview.resetSwitchTest();
  const size_t used = strlen(reply);
  if (used < capacity) snprintf(reply + used, capacity - used, ",preamble=%u%s",
      preview.preamble(0, rxPowerSavingPreambleForParams(sf, bw)), primary_preamble_ ? "" : " (auto)");
  appendChirpWarning(reply, capacity, preview);
}

void RadioProfileCLI::appendPrimaryChirpWarning(char* reply, size_t capacity, uint8_t sf, float bw,
                                               uint16_t preamble) const {
  if (!radio_ || !radio_->profiles()) return;
  auto preview = *radio_->profiles();
  preview.primary.sf = sf; preview.primary.bw = bw; preview.primary.preamble = preamble;
  if (preview.primary != radio_->profiles()->primary) preview.resetSwitchTest();
  appendChirpWarning(reply, capacity, preview);
}

void RadioProfileCLI::appendChirpWarning(char* reply, size_t capacity, const RadioProfiles& preview) {
  if (!capacity || !preview.enabled()) return;
  if (!preview.switchTestReady()) {
    const size_t used = strlen(reply);
    if (used < capacity) snprintf(reply + used, capacity - used, "; timing self-test pending");
    return;
  }
  const auto timing = preview.chirpTiming();
  if (!timing.valid) return;
  size_t used = strlen(reply);
  const bool over_a = timing.preamble[0] > RadioProfiles::MaxAutomaticPreamble;
  const bool over_b = timing.preamble[1] > RadioProfiles::MaxAutomaticPreamble;
  if (used < capacity && (over_a || over_b))
    snprintf(reply + used, capacity - used, "; WARN need >128: %s",
             over_a && over_b ? "radio,radio2" : over_a ? "radio" : "radio2");
  used = strlen(reply);
  const bool short_a = preview.primary.preamble && preview.primary.preamble < timing.preamble[0];
  const bool short_b = preview.secondary.params.preamble && preview.secondary.params.preamble < timing.preamble[1];
  const bool a = timing.preamble[0] > 32 || short_a, b = timing.preamble[1] > 32 || short_b;
  if (used < capacity) {
    if (a && b) snprintf(reply + used, capacity - used, "; WARN recommended preamble: radio=%.0f,radio2=%.0f",
                         timing.preamble[0], timing.preamble[1]);
    else if (a || b) snprintf(reply + used, capacity - used, "; WARN recommended preamble: %s=%.0f",
                              a ? "radio" : "radio2", timing.preamble[a ? 0 : 1]);
  }
  used = strlen(reply);
  if (used < capacity && (short_a || short_b))
    snprintf(reply + used, capacity - used, "; short override: %s",
             short_a && short_b ? "radio,radio2" : short_a ? "radio" : "radio2");
  // Preamble-bearing acknowledgements and getters share this formatter. Only
  // report the measured allowance for the same calibrated profile pair; a new
  // pair still has to activate and complete its self-test after the first ACK.
  // Dedicated timing/status getters already include this value under their
  // existing switch/budget labels.
  used = strlen(reply);
  if (used < capacity && !strstr(reply, "switch=") && !strstr(reply, "budget="))
    snprintf(reply + used, capacity - used, "; switch=%luus",
             (unsigned long)preview.switchBudgetUs());
}

void RadioProfileCLI::formatConfig(char* reply, size_t capacity, const RadioProfileConfig& config,
                                   bool temporary, uint32_t remaining) const {
  if (config.mode == RadioProfileMode::Off) { snprintf(reply, capacity, "> off"); return; }
  RadioProfiles preview = *radio_->profiles();
  preview.setSecondary(config, temporary);
  uint16_t preamble = preview.preamble(1, 32);
  snprintf(reply, capacity, "> %.3f,%.3f,%u,%u,%s,%u%s", config.params.freq, config.params.bw,
           config.params.sf, config.params.cr, modeName(config.mode), preamble,
           config.params.preamble ? "" : " (auto)");
  if (temporary) {
    uint32_t minutes = (remaining + 59999UL) / 60000UL;
    const size_t used = strlen(reply);
    if (used < capacity) snprintf(reply + used, capacity - used, "; %lud%luh%lum (%lu min) left",
        (unsigned long)(minutes / 1440), (unsigned long)(minutes / 60 % 24),
        (unsigned long)(minutes % 60), (unsigned long)minutes);
  }
  appendChirpWarning(reply, capacity, preview);
}

bool RadioProfileCLI::handle(const char* command, char* reply, size_t capacity, bool remote_origin) {
  if (handleCarrierWaveCommand(radio_, command, reply, capacity)) return true;
  const char* text = command;
  enum { Get, Set, Delete } verb = Get;
  if (!strncmp(text, "get ", 4)) text += 4;
  else if (!strncmp(text, "set ", 4)) { text += 4; verb = Set; }
  else if (!strncmp(text, "del ", 4)) { text += 4; verb = Delete; }
  char key[24];
  size_t n = strcspn(text, " \t");
  if (n >= sizeof(key)) return false;
  memcpy(key, text, n); key[n] = 0;
  const char* args = text + n;
  while (*args == ' ' || *args == '\t') ++args;
  const bool base = !strcmp(key, "radio2");
  const bool temporary = !strcmp(key, "tempradio2");
  const bool scheduled = !strcmp(key, "radioat2") || !strcmp(key, "tempradioat2");
  const bool scheduled_temp = !strcmp(key, "tempradioat2");
  const bool crossing = !strcmp(key, "radio2.cross");
  const bool replies = infrastructure_replies_ && !strcmp(key, "tx.reply");
  const bool status = !strcmp(key, "radio2.status");
  const bool scan = !strcmp(key, "radio2.scan");
  const bool timing = !strcmp(key, "radio2.timing") || !strcmp(key, "radio.timing");
  if (!base && !temporary && !scheduled && !crossing && !replies && !status && !scan && !timing) return false;
  if (!radio_ || !radio_->profiles()) { snprintf(reply, capacity, "Error: radio profiles unsupported"); return true; }
  if (command == text && *args && verb == Get && (temporary || base)) verb = Set;
  if (remote_origin && !remote_command_
      && (((base || temporary) && verb == Set) || (scheduled_temp && verb == Delete))) {
    snprintf(reply, capacity, "Error: this role requires local USB for radio2 changes"); return true;
  }
  if (hasReplyMutation() && verb != Get) {
    snprintf(reply, capacity, "Error: radio acknowledgement pending"); return true;
  }
  if (replies) {
    const auto* p = radio_->profiles();
    if (verb == Get && !*args) {
      snprintf(reply, capacity, "> %s%s%s", radioTxPolicyName(p->reply_tx),
          p->reply_force ? " force" : "",
          (p->reply_tx == RADIO_TX_BOTH || p->reply_tx == RADIO_TX_SECONDARY)
              && !p->canTransmit(1, p->reply_force) ? "; radio2 TX unavailable" : "");
      return true;
    }
    char mode[16];
    const size_t length = strcspn(args, " \t");
    uint8_t policy = RADIO_TX_AUTO;
    const char* suffix = args + length;
    while (*suffix == ' ' || *suffix == '\t') ++suffix;
    const bool force = !strcmp(suffix, "force");
    if (length < sizeof(mode)) { memcpy(mode, args, length); mode[length] = 0; }
    if (verb != Set || length >= sizeof(mode) || !parseRadioTxPolicy(mode, policy)
        || (*suffix && !force)
        || (force && policy != RADIO_TX_BOTH && policy != RADIO_TX_SECONDARY)) {
      snprintf(reply, capacity, "Error: use get/set tx.reply auto|radio|radio2|both|off [force for radio2/both]");
      return true;
    }
    const uint8_t previous = reply_setting_;
    reply_setting_ = 0xb0 | policy | (force ? 8 : 0);
    if (!save(saved_, primary_preamble_, cross_)) {
      reply_setting_ = previous;
      snprintf(reply, capacity, "Error: settings could not be saved");
    } else {
      publish();
      snprintf(reply, capacity, "OK - tx.reply=%s%s", radioTxPolicyName(policy), force ? " force" : "");
    }
    return true;
  }
  if (timing) {
    if (verb != Get || *args) { snprintf(reply, capacity, "Error: radio timing is read-only"); return true; }
    const auto& p = *radio_->profiles();
    const auto t = p.chirpTiming();
    if (!p.enabled()) snprintf(reply, capacity, "> off");
    else if (!p.switchTestReady()) snprintf(reply, capacity,
        "> timing self-test pending; test=%u/%u,%u/%u; provisional preamble=%u,%u",
        p.switch_test_samples[0], RadioProfiles::SwitchTestSamplesPerDirection,
        p.switch_test_samples[1], RadioProfiles::SwitchTestSamplesPerDirection,
        radio_->profilePreamble(0), radio_->profilePreamble(1));
    else if (!t.valid) snprintf(reply, capacity, "Error: timing unavailable");
    else {
      snprintf(reply, capacity, "> chirps=%.2f,%.2f; need=%.0f,%.0f; switch=%luus; loop=%luus (estimate)",
          t.listen_us[0] / t.symbol_us[0], t.listen_us[1] / t.symbol_us[1],
          t.preamble[0], t.preamble[1], (unsigned long)t.switch_us, (unsigned long)t.loop_us);
      size_t used = strlen(reply);
      if (used < capacity) snprintf(reply + used, capacity - used, "; test=%u/%u,%u/%u%s",
          p.switch_test_samples[0], RadioProfiles::SwitchTestSamplesPerDirection,
          p.switch_test_samples[1], RadioProfiles::SwitchTestSamplesPerDirection,
          p.switchTestReady() ? " ready" : " pending");
      appendChirpWarning(reply, capacity, p);
    }
    return true;
  }
  if (crossing) {
    if (verb == Get && !*args) {
      snprintf(reply, capacity, "> %s", cross_ == RadioCrossMode::Auto ? "auto" : cross_ == RadioCrossMode::On ? "on" : "off");
    } else if (verb == Set && (!strcmp(args, "auto") || !strcmp(args, "on") || !strcmp(args, "off"))) {
      auto mode = !strcmp(args, "auto") ? RadioCrossMode::Auto : !strcmp(args, "on") ? RadioCrossMode::On : RadioCrossMode::Off;
      if (save(saved_, primary_preamble_, mode)) { cross_ = mode; publish(); snprintf(reply, capacity, "OK"); }
      else snprintf(reply, capacity, "Error: settings could not be saved");
    } else snprintf(reply, capacity, "Error: use get/set radio2.cross [auto|on|off]");
    return true;
  }
  if (scan) {
    if (verb != Get || *args) { snprintf(reply, capacity, "Error: radio2.scan is read-only"); return true; }
    const auto& p = *radio_->profiles();
    if (!p.enabled()) snprintf(reply, capacity, "> off");
    else {
      const uint8_t slow = p.slowerProfile();
      const uint16_t preamble = radio_->profilePreamble(slow);
      snprintf(reply, capacity, "> slow=%s; listen_us=%lu,%lu; preamble=%u,%u",
          slow ? "radio2" : "radio", (unsigned long)p.listenUs(0, preamble),
          (unsigned long)p.listenUs(1, preamble), radio_->profilePreamble(0), radio_->profilePreamble(1));
      appendChirpWarning(reply, capacity, p);
    }
    return true;
  }
  if (status) {
    if (verb != Get || *args) { snprintf(reply, capacity, "Error: radio2.status is read-only"); return true; }
    if (hasReplyMutation()) {
      snprintf(reply, capacity, "> radio2 change pending %s; previous channel retained",
          remote_delivered_ ? "storage commit (retrying)" : "reply transmission");
      return true;
    }
    const auto& p = *radio_->profiles();
    snprintf(reply, capacity, "> %s; RX=%lu,%lu TX=%lu,%lu switches=%lu errors=%lu max=%luus preamble=%u,%u",
        modeName(p.secondary.mode), (unsigned long)p.rx_packets[0], (unsigned long)p.rx_packets[1],
        (unsigned long)p.tx_packets[0], (unsigned long)p.tx_packets[1],
        (unsigned long)p.switches, (unsigned long)p.switch_failures,
        (unsigned long)p.longest_switch_us, radio_->profilePreamble(0), radio_->profilePreamble(1));
    if (p.enabled()) {
      size_t used = strlen(reply);
      if (used < capacity) snprintf(reply + used, capacity - used, "; test=%u/%u,%u/%u%s budget=%luus",
          p.switch_test_samples[0], RadioProfiles::SwitchTestSamplesPerDirection,
          p.switch_test_samples[1], RadioProfiles::SwitchTestSamplesPerDirection,
          p.switchTestReady() ? " ready" : " pending", (unsigned long)p.switchBudgetUs());
      appendChirpWarning(reply, capacity, p);
    }
    return true;
  }
  if ((base || temporary) && verb == Get && !*args) {
    if (temporary && !temp_active_ && !temp_pending_) snprintf(reply, capacity, "> off");
    else formatConfig(reply, capacity, temporary ? (temp_pending_ ? pending_temporary_ : temporary_) : saved_,
                      temporary, temp_pending_ ? pending_duration_ms_ : temp_remaining_ms_);
    return true;
  }
  if ((base || temporary) && verb == Set && !strcmp(args, "off")) {
    if (base && !(remote_command_ ? prepareSavedImage(ReplyPath, {}, primary_preamble_, cross_)
                                 : save({}, primary_preamble_, cross_))) {
      snprintf(reply, capacity, "Error: settings could not be saved"); return true;
    }
    if (remote_command_) {
      stageRemoteMutation(base ? RemoteMutation::Off : RemoteMutation::TempOff);
      snprintf(reply, capacity, "OK - %s after reply", !base && saved_.mode != RadioProfileMode::Off
          ? "saved radio2 restored" : "single radio");
      return true;
    }
    if (base) { saved_ = {}; schedule_retry_ms_ = 0; }
    temp_pending_ = temp_active_ = false; temp_remaining_ms_ = 0;
    for (auto& s : schedules_) if (base || s.temporary) s = {};
    publish_pending_ = true; publish_after_ms_ = millis() + 2000;
    publish(); snprintf(reply, capacity, "OK - %s in 2s", saved_.mode != RadioProfileMode::Off ? "saved radio2 restored" : "single radio");
    return true;
  }
  if (scheduled && (verb == Get || verb == Delete)) {
    uint32_t index = 0;
    if (*args && strcmp(args, "all") && (!cli::parseUnsignedIntegerStrict(args, index) || index < 1 || index > 4)) {
      snprintf(reply, capacity, "Error: slot must be 1-4 or all"); return true;
    }
    const unsigned first = scheduled_temp ? 4 : 0;
    if (verb == Delete) {
      if (remote_command_ && scheduled_temp) {
        stageRemoteMutation(RemoteMutation::DeleteTemp, {}, 0, index ? 1U << (index - 1) : 15);
        snprintf(reply, capacity, "OK - schedule cleared after reply"); return true;
      }
      if (!scheduled_temp) schedule_retry_ms_ = 0;
      for (unsigned i = 0; i < 4; ++i) if (!index || index == i + 1) {
        if (schedules_[first+i].started) {
          temp_active_ = false; temp_remaining_ms_ = 0;
          publish_pending_ = true; publish_after_ms_ = millis() + 2000;
        }
        schedules_[first+i] = {};
      }
      publish(); snprintf(reply, capacity, "OK"); return true;
    }
    if (!index) {
      snprintf(reply, capacity, "> slots: ");
      for (unsigned i = 0; i < 4; ++i) if (schedules_[first+i].active) {
        const size_t used = strlen(reply);
        if (used < capacity) snprintf(reply + used, capacity-used, "%u@%lu ", i+1, (unsigned long)schedules_[first+i].start);
      }
      return true;
    }
    const auto& s = schedules_[first+index-1];
    if (!s.active) snprintf(reply, capacity, "> off");
    else {
      formatConfig(reply, capacity, s.config, s.temporary, remainingMillis(s.end_ms, millis()));
      const size_t used = strlen(reply);
      if (used < capacity) snprintf(reply+used, capacity-used, "; @%lu-%lu", (unsigned long)s.start, (unsigned long)s.end);
    }
    return true;
  }
  if (verb != Set) { snprintf(reply, capacity, "Error: invalid radio2 command"); return true; }
  char local[140];
  if (strlen(args) >= sizeof(local)) { snprintf(reply, capacity, "Error: params too long"); return true; }
  strcpy(local, args);
  char* parts[8];
  const unsigned count = split(local, parts, 8);
  const unsigned expected = scheduled_temp ? 7 : scheduled || temporary ? 6 : 5;
  RadioProfileConfig config;
  uint32_t sf = 0, cr = 0, start = 0, end = 0, minutes = 0;
  bool valid = (count == expected || count == expected + 1)
      && cli::parseDecimalStrict(parts[0], config.params.freq)
      && cli::parseDecimalStrict(parts[1], config.params.bw)
      && cli::parseUnsignedIntegerStrict(parts[2], sf) && sf <= 255
      && cli::parseUnsignedIntegerStrict(parts[3], cr) && cr <= 255
      && parseMode(parts[4], config.mode);
  config.params.sf = sf; config.params.cr = cr;
  if (valid && count > expected) valid = parsePreamble(parts[expected], config.params.preamble);
  if (valid && temporary) valid = cli::parseUnsignedIntegerStrict(parts[5], minutes) && minutes >= 1 && minutes <= 10080;
  if (valid && scheduled) valid = cli::parseUnsignedIntegerStrict(parts[5], start);
  if (valid && scheduled_temp) valid = cli::parseUnsignedIntegerStrict(parts[6], end);
  if (valid) valid = radio_->validateProfile(config.params);
  RadioProfiles preview = *radio_->profiles(); preview.setSecondary(config, false);
  if (valid) valid = preview.automaticPreambleFits();
  if (!valid) { snprintf(reply, capacity, "Error: use %s f,bw,sf,cr,rx|rxtx%s[,preamble|auto]", key,
      scheduled_temp ? ",start,end" : scheduled ? ",start" : temporary ? ",minutes" : ""); return true; }
  if (scheduled) {
    const uint32_t epoch = rtc_ ? rtc_->getCurrentTime() : 0;
    if (!epoch || start <= epoch || (uint64_t)(start-epoch) * 1000 > 0x7fffffffULL
        || (scheduled_temp && (end <= start || (uint64_t)(end-epoch) * 1000 > 0x7fffffffULL))) {
      snprintf(reply, capacity, "Error: future UTC start/end required, within 24 days"); return true;
    }
    if (scheduled_temp) {
      const uint64_t pending_left = temp_pending_
          ? pending_duration_ms_ + (uint64_t)((int32_t)(temp_start_ms_ - millis()) > 0 ? temp_start_ms_ - millis() : 0)
          : 0;
      const uint64_t session_left = pending_left > temp_remaining_ms_ ? pending_left : temp_remaining_ms_;
      if ((temp_active_ || temp_pending_) && (uint64_t)(start-epoch)*1000 < session_left) {
        snprintf(reply, capacity, "Error: temporary schedule overlaps active session"); return true;
      }
      for (const auto& s : schedules_) if (s.active && s.temporary && start < s.end && end > s.start) {
        snprintf(reply, capacity, "Error: temporary schedules overlap"); return true;
      }
    }
    const unsigned first = scheduled_temp ? 4 : 0;
    for (unsigned i = 0; i < 4; ++i) if (!schedules_[first+i].active) {
      auto& s = schedules_[first+i]; s = {};
      s.config = config; s.start = start; s.end = end; s.temporary = scheduled_temp;
      s.end_ms = scheduled_temp ? millis() + (end-epoch)*1000UL : 0; s.active = true;
      snprintf(reply, capacity, "OK - %s %u queued, preamble=%u", key, i+1, preview.preamble(1, 32));
      appendChirpWarning(reply, capacity, preview); return true;
    }
    snprintf(reply, capacity, "Error: all four schedule slots occupied"); return true;
  }
  if (temporary) {
    for (const auto& s : schedules_) if (s.active && s.temporary) {
      snprintf(reply, capacity, "Error: clear tempradioat2 schedules first"); return true;
    }
    if (remote_command_) stageRemoteMutation(RemoteMutation::Temporary, config, minutes * 60000UL);
    else {
      pending_temporary_ = config; pending_duration_ms_ = minutes * 60000UL;
      temp_start_ms_ = millis() + 2000; temp_pending_ = true;
    }
    // An existing session remains active until the new request can take over.
    snprintf(reply, capacity, "OK - tempradio2 %lud%luh%lum (%lu min); preamble=%u",
        (unsigned long)(minutes/1440), (unsigned long)(minutes/60%24),
        (unsigned long)(minutes%60), (unsigned long)minutes, preview.preamble(1, 32));
  } else if (remote_command_ ? prepareSavedImage(ReplyPath, config, primary_preamble_, cross_)
                            : save(config, primary_preamble_, cross_)) {
    if (remote_command_) stageRemoteMutation(RemoteMutation::Saved, config);
    else {
      saved_ = config;
      publish_pending_ = true; publish_after_ms_ = millis() + 2000;
      publish();
    }
    snprintf(reply, capacity, "OK - radio2 %s%s; preamble=%u", modeName(config.mode),
        remote_command_ ? " pending after reply" : "", preview.preamble(1, 32));
  } else snprintf(reply, capacity, "Error: settings could not be saved");
  if (!strncmp(reply, "OK", 2)) appendChirpWarning(reply, capacity, preview);
  return true;
}

}  // namespace mesh
