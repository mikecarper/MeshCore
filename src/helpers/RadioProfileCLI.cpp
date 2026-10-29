#include "CLIReplyText.h"
#include "RadioProfileCLI.h"
#include "CarrierTools.h"
#if MESH_ENABLE_CARRIER_TOOLS
#include "CarrierWaveCLI.h"
#endif
#include "RadioProfileCommandUtils.h"
#include "RadioProfilePresets.h"
#include "FilePresence.h"
#include "PersistentStoreFormat.h"
#include "radiolib/RXPowerSaving.h"
#include "radiolib/HilTxFrequencyGuard.h"
#if defined(MESH_HIL_ESP_SPI_AB) && MESH_HIL_ESP_SPI_AB
#include "radiolib/ESP32SpiABDiagnostics.h"
#endif
#include <Arduino.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

namespace mesh {
namespace {
// Keep bounded response appends out of each formatter's call site. This
// shares their length/bounds/varargs setup on flash-constrained boards.
void appendReply(char* reply, size_t capacity, const char* format, ...)
    __attribute__((format(printf, 3, 4)));
void appendReply(char* reply, size_t capacity, const char* format, ...) {
  if (!capacity) return;
  const size_t used = strlen(reply);
  if (used >= capacity) return;
  va_list args;
  va_start(args, format);
  vsnprintf(reply + used, capacity - used, format, args);
  va_end(args);
}
constexpr size_t ImageSize = 24;
const char* const ImagePath = "/radio_profiles";
const char* const BackupPath = "/radio_profiles.bak";
const char* const TempPath = "/radio_profiles.tmp";
// Never considered a boot-time committed image. Losing power before the
// acknowledgement leaves the previous saved radio untouched.
const char* const ReplyPath = "/radio_profiles.reply";
#if MESH_RADIO_SWITCH_TUNING
const char* const FastPath = "/radio_fast";
const char* const FastBackup = "/radio_fast.bak";
const char* const FastTemp = "/radio_fast.tmp";
#endif
uint32_t checksum(const uint8_t* p, size_t n) {
  return storage::updateCRC32(0xffffffffU, p, n);
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
uint32_t frequencyHz(float mhz) {
  return uint32_t(double(mhz) * 1000000.0 + 0.5);
}
uint32_t secondaryClearanceHz(const RadioProfiles& profiles) {
  // Frequencies are MHz, bandwidths are kHz. Expand radio1's occupied
  // bandwidth by 6 kHz on each side; SF and coding rate do not affect it.
  return uint32_t((double(profiles.primary.bw) + double(profiles.secondary.params.bw))
                 * 500.0 + 6000.5);
}
bool secondaryOverlaps(uint32_t primary_hz, float secondary_mhz, uint32_t clearance_hz) {
  const uint32_t secondary_hz = frequencyHz(secondary_mhz);
  return (primary_hz > secondary_hz ? primary_hz - secondary_hz : secondary_hz - primary_hz)
         <= clearance_hz;
}
#if !defined(COMPANION_RADIO_FULL) && !MESH_ROLE_COMPANION
bool isMediaMeshProfile(const RadioProfileConfig& config) {
  return config.mode != RadioProfileMode::Off
      && config.params.freq >= 911.1495f && config.params.freq <= 911.1505f
      && (config.params.bw == 250.0f || config.params.bw == 125.0f)
      && config.params.sf == 7 && config.params.cr == 5;
}
#endif
}

#if !defined(COMPANION_RADIO_FULL) && !MESH_ROLE_COMPANION
void RadioProfileCLI::setMediaMeshScopeHandler(MediaMeshScopeHandler handler, void* context) {
  media_scope_handler_ = handler;
  media_scope_context_ = context;
  media_scope_pending_ = false;
  updateMediaMeshScope();
}

bool RadioProfileCLI::mediaMeshScopeReady(const RadioProfileConfig& config) const {
  return !media_scope_handler_ || !isMediaMeshProfile(config)
      || media_scope_handler_(media_scope_context_, true, false);
}

void RadioProfileCLI::updateMediaMeshScope() {
  // A held/unreadable radio store is unknown intent, not a saved Off choice.
  if (!media_scope_handler_ || hold_) return;
  if (isMediaMeshProfile(saved_)) media_scope_enabled_ = true;
  else if (saved_.mode == RadioProfileMode::Off) media_scope_enabled_ = false;
  else return;  // Custom MediaMesh tuning retains the saved region permission.
  media_scope_pending_ = true;
  media_scope_retry_ms_ = 0;
  retryMediaMeshScope();
}

void RadioProfileCLI::retryMediaMeshScope() {
  if (!media_scope_pending_ || !media_scope_handler_ || hold_
      || (media_scope_retry_ms_ && (int32_t)(millis() - media_scope_retry_ms_) < 0)) return;
  media_scope_pending_ = !media_scope_handler_(media_scope_context_, media_scope_enabled_, true);
  media_scope_retry_ms_ = media_scope_pending_ ? millis() + 1000 : 0;
}
#endif

RadioProfileCLI::ImageReadResult RadioProfileCLI::readImage(
    const char* path, uint8_t* bytes, size_t size) {
  if (!fs_) return ImageReadResult::Unreadable;
#if defined(ESP32_PLATFORM)
  // SPIFFS may return a truthy directory when a missing path is opened for
  // reading. Probe metadata first so a fresh store stays Missing, rather than
  // being marked corrupt from that directory's zero-byte size. Metadata/open
  // failures still protect an existing image from automatic replacement.
  bool present = false;
  if (!filePresence(fs_, path, present)) return ImageReadResult::Unreadable;
  if (!present) return ImageReadResult::Missing;
#endif
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
#if defined(ESP32_PLATFORM)
    return ImageReadResult::Unreadable;
#else
    return fs_->exists(path) ? ImageReadResult::Unreadable
                             : ImageReadResult::Missing;
#endif
  }
  const bool right_size = file.size() == size;
  const bool read_complete = right_size && file.read(bytes, size) == (int)size;
  file.close();
  if (!right_size) return ImageReadResult::Invalid;
  if (!read_complete) return ImageReadResult::Unreadable;
  uint32_t stored;
  memcpy(&stored, bytes + size - 4, 4);
  return bytes[0] == 'R' && bytes[1] == '2' && (bytes[2] == 1 || bytes[2] == 2)
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

bool RadioProfileCLI::save(const RadioProfileConfig& config, uint16_t preamble, RadioCrossMode cross,
                           int8_t manual_secondary) {
  if (!discardCorruptSavedImagesForWrite()) return false;
  return prepareSavedImage(TempPath, config, preamble, cross, manual_secondary) && commitSavedImage(TempPath);
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
                                      uint16_t preamble, RadioCrossMode cross, int8_t manual_secondary) {
  if (!fs_ || hold_) return false;
  uint8_t image[ImageSize] = {'R', '2', 1, (uint8_t)config.mode};
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  // Version 2 keeps the 24-byte layout and CRC; bit 7 records explicit radio2
  // intent. Other settings can save an unused Off slot without claiming it.
  image[2] = 2;
  const bool manual = manual_secondary < 0 ? manual_secondary_ : manual_secondary != 0;
  if (manual || config.mode != RadioProfileMode::Off) image[3] |= 0x80;
#else
  (void)manual_secondary;
#endif
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
#if MESH_RADIO_SWITCH_TUNING
  return commitImage(path, ImagePath, BackupPath, hold_);
#else
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
#endif
}

#if MESH_RADIO_SWITCH_TUNING
bool RadioProfileCLI::commitImage(const char* path, const char* active,
                                  const char* backup, bool& hold, bool preserve_backup) {
  if (!fs_ || hold) return false;
  if (fs_->exists(active)) {
    if (preserve_backup) {
      if (!fs_->remove(active)) return false;
    } else {
      if (fs_->exists(backup) && !fs_->remove(backup)) return false;
      if (!fs_->rename(active, backup)) return false;
    }
  }
  if (!fs_->rename(path, active)) {
    if (fs_->exists(backup) && !fs_->rename(backup, active)) hold = true;
    return false;
  }
  if (fs_->exists(backup)) fs_->remove(backup);
  return true;
}

bool RadioProfileCLI::validFastImage(const uint8_t* bytes, RadioSwitchTuning& value) const {
  if (bytes[2] != 1) return false;
  for (size_t n = 5; n < ImageSize - 4; ++n) if (bytes[n]) return false;
  value.flags = bytes[3]; value.spi_mhz = bytes[4];
  return radio_ && validRadioSwitchTuning(value, radio_->switchTuningCapabilities());
}

void RadioProfileCLI::configureBootTuning(FILESYSTEM* fs, Radio& radio) {
  RadioProfileCLI loader;
  loader.fs_ = fs; loader.radio_ = &radio;
  loader.loadFastSettings(true);
}

__attribute__((noinline)) void RadioProfileCLI::loadFastSettings(bool boot) {
  fast_pending_ = fast_hold_ = false;
  if (!fs_ || !radio_) return;
  uint8_t bytes[ImageSize];
  const auto primary = readImage(FastPath, bytes, sizeof(bytes));
  bool loaded = primary == ImageReadResult::Valid && validFastImage(bytes, fast_saved_);
  if (!loaded) {
    const auto backup = readImage(FastBackup, bytes, sizeof(bytes));
    loaded = backup == ImageReadResult::Valid && validFastImage(bytes, fast_saved_);
    fast_hold_ = primary == ImageReadResult::Unreadable || backup == ImageReadResult::Unreadable;
    if (loaded && !fast_hold_) {
      // Publish the known-good backup before accepting further writes. Never
      // move a corrupt/unsupported primary over this only valid record.
      fast_hold_ = (fs_->exists(FastPath) && !fs_->remove(FastPath))
          || !fs_->rename(FastBackup, FastPath);
    }
  }
  if (loaded) {
    fast_pending_ = boot ? !radio_->configureBootSwitchTuning(fast_saved_)
        : radio_->trySetSwitchTuning(fast_saved_) != RadioParamApplyResult::APPLIED;
    fast_retry_ms_ = millis() + 1000;
  }
}

bool RadioProfileCLI::saveFastSettings(const RadioSwitchTuning& value) {
  if (!fs_ || fast_hold_) return false;
  // A read error must not be interpreted as a missing/corrupt saved policy.
  uint8_t previous[ImageSize];
  RadioSwitchTuning decoded;
  const auto read = readImage(FastPath, previous, sizeof(previous));
  if (read == ImageReadResult::Unreadable) return false;
  const bool preserve = read != ImageReadResult::Valid || !validFastImage(previous, decoded);
  uint8_t image[ImageSize] = {'R', '2', 1, value.flags, value.spi_mhz};
  const uint32_t crc = checksum(image, ImageSize - 4);
  memcpy(image + ImageSize - 4, &crc, 4);
  if (!writeImage(FastTemp, image, sizeof(image))
      || !commitImage(FastTemp, FastPath, FastBackup, fast_hold_, preserve)) return false;
  fast_saved_ = value;
  fast_pending_ = false;
  return true;
}

bool RadioProfileCLI::commitFastSettings(void* context, const RadioSwitchTuning& value) {
  return static_cast<RadioProfileCLI*>(context)->saveFastSettings(value);
}

__attribute__((noinline)) bool RadioProfileCLI::handleFastSettings(const char* key, const char* args, int verb,
                                        bool remote, char* reply, size_t capacity) {
  if (strcmp(key, "radio.fast") && strncmp(key, "radio.fast.", 11)) return false;
  if (!radio_) { cli::formatError(reply, capacity, "radio tuning unsupported"); return true; }
  const auto caps = radio_->switchTuningCapabilities();
  const auto current = radio_->switchTuning();
  const char* suffix = key + (key[10] ? 11 : 10);
#if defined(MESH_HIL_ESP_SPI_AB) && MESH_HIL_ESP_SPI_AB
  if (esp_spiab::handle(suffix, args, verb, reply, capacity)) return true;
#endif
  static const char names[] = "warm\0fs\0packet\0iq\0modem\0batch\0buffer";
  const char* name = names;
  unsigned feature = 0;
  while (feature < 7 && strcmp(suffix, name)) { name += strlen(name) + 1; ++feature; }
  const bool master = !*suffix, spi = !strcmp(suffix, "spi");
  const bool capabilities = !strcmp(suffix, "caps");
  if (!master && !spi && !capabilities && feature == 7) {
    cli::formatError(reply, capacity, "unknown radio.fast control"); return true;
  }
  const uint8_t bit = feature < 7 ? 1U << feature : 0;
  if ((spi && !caps.spi_clocks) || (bit && !(caps.flags & bit))
      || (master && !caps.flags && !caps.spi_clocks)) {
    cli::formatError(reply, capacity, "radio tuning unsupported"); return true;
  }
  if (verb == 0 && !*args) {
    if (capabilities) {
      snprintf(reply, capacity, "> features:");
      name = names;
      for (unsigned n = 0; n < 7; ++n) {
        if (caps.flags & (1U << n)) appendReply(reply, capacity, " %s", name);
        name += strlen(name) + 1;
      }
      appendReply(reply, capacity, "; spi=%s", caps.spi_clocks ? "0" : "na");
      for (unsigned n = 1; n <= 4; ++n) {
        if (caps.spi_clocks & (1U << n)) appendReply(reply, capacity, ",%u", 1U << n);
        if (n == 3 && (caps.spi_clocks & RadioSwitchCapabilities::Spi9))
          appendReply(reply, capacity, ",9");
      }
    } else if (master) {
      if (caps.spi_clocks) {
        snprintf(reply, capacity, "> spi=%u (%luHz)", current.spi_mhz,
            (unsigned long)radio_->switchSpiHz());
      } else snprintf(reply, capacity, "> spi=na");
      if (fast_pending_) appendReply(reply, capacity, " pending saved policy");
      name = names;
      for (unsigned n = 0; n < 7; ++n) {
        appendReply(reply, capacity, " %s=%s", name,
            !(caps.flags & (1U << n)) ? "na" : current.flags & (1U << n) ? "on" : "off");
        name += strlen(name) + 1;
      }
    } else if (spi) snprintf(reply, capacity, "> %u (%luHz; 0=board default)",
        current.spi_mhz, (unsigned long)radio_->switchSpiHz());
    else snprintf(reply, capacity, "> %s", current.flags & bit ? "on" : "off");
    return true;
  }
  if (capabilities) {
    cli::formatError(reply, capacity, "radio.fast.caps is read-only"); return true;
  }
  if (verb != 1 || !*args) {
    cli::formatError(reply, capacity, "use get/set radio.fast[.control] value"); return true;
  }
  if (remote || remote_command_) {
    cli::formatError(reply, capacity, "radio.fast changes require local USB or BLE"); return true;
  }
  if (hasReplyMutation() || fast_hold_) {
    cli::formatError(reply, capacity, "radio settings held; retry after recovery"); return true;
  }
  RadioSwitchTuning next = fast_pending_ ? fast_saved_ : current;
  if (spi) {
    // Single-digit selections remain strict; 9 labels the 8888888 Hz clock.
    if (!args[1] && strchr("02489", args[0])) next.spi_mhz = args[0] - '0';
    else if (!strcmp(args, "16")) next.spi_mhz = 16;
    else { cli::formatError(reply, capacity, "SPI MHz: 0,2,4,8,9,16 where supported"); return true; }
  } else {
    const bool on = !strcmp(args, "on");
    if (!on && strcmp(args, "off")) { cli::formatError(reply, capacity, "use on or off"); return true; }
    if (master) {
      next.flags = on ? caps.flags : 0;
      next.spi_mhz = !on && (caps.spi_clocks & 2) ? 2 : 0;
    } else if (on) next.flags |= bit;
    else next.flags &= ~bit;
  }
  if (!validRadioSwitchTuning(next, caps)) {
    cli::formatError(reply, capacity, "radio tuning unsupported"); return true;
  }
  const auto result = radio_->trySetSwitchTuning(next, commitFastSettings, this);
  if (result == RadioParamApplyResult::APPLIED) snprintf(reply, capacity, "OK - %s=%s (saved)", key, args);
  else if (result == RadioParamApplyResult::BUSY && saveFastSettings(next)) {
    fast_pending_ = true;
    fast_retry_ms_ = 0;
    snprintf(reply, capacity, "OK - saved; pending");
  } else cli::formatError(reply, capacity, "radio tuning could not be applied/saved");
  return true;
}
#endif

void RadioProfileCLI::begin(FILESYSTEM* fs, Radio* radio, RTCClock* rtc, bool infrastructure_replies) {
  fs_ = fs; radio_ = radio; rtc_ = rtc; last_ms_ = millis();
#if MESH_RADIO_SWITCH_TUNING
  loadFastSettings();
#endif
  hold_ = false;
  cross_ = RadioCrossMode::Off;
  recoverable_corrupt_store_ = false;
  manual_secondary_ = false;
  secondary_intent_generation_ = 0;
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  auto_secondary_requested_ = secondary_protocol_overlay_ = false;
  owns_secondary_runtime_ = external_secondary_override_ = false;
#endif
  infrastructure_replies_ = infrastructure_replies;
  auto* profiles = radio_ ? radio_->profiles() : nullptr;
  if (profiles) {
    profiles->cross = cross_;
    profiles->reply_tx = infrastructure_replies ? RADIO_TX_BOTH : RADIO_TX_AUTO;
    profiles->reply_force = false;
  }
  if (!fs_ || !profiles) return;
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
  // Legacy Off is ambiguous: preserve it as deliberate manual intent rather
  // than allowing a newly installed automatic feature to turn the radio on.
  manual_secondary_ = bytes[2] == 1 || (bytes[3] & 0x80) != 0;
  saved_.mode = (RadioProfileMode)(bytes[2] == 2 ? bytes[3] & 0x7F : bytes[3]);
  memcpy(&saved_.params.freq, bytes + 4, 4);
  memcpy(&saved_.params.bw, bytes + 8, 4);
  saved_.params.sf = bytes[12]; saved_.params.cr = bytes[13];
  memcpy(&saved_.params.preamble, bytes + 14, 2);
  memcpy(&primary_preamble_, bytes + 16, 2);
  cross_ = (RadioCrossMode)bytes[18];
  reply_setting_ = validReplySetting(bytes[19]) ? bytes[19] : 0;
  if ((uint8_t)saved_.mode > 2 || (uint8_t)cross_ > 2
      || (bytes[2] == 2 && saved_.mode != RadioProfileMode::Off && !manual_secondary_)
      || (saved_.mode != RadioProfileMode::Off && !radio_->validateProfile(saved_.params))
      || (primary_preamble_ && (primary_preamble_ < 8 || primary_preamble_ > RadioProfiles::MaxPreamble))) {
    // A CRC-valid image can still be semantically stale (for example, after
    // an older build wrote a now-unsupported value). Do not recreate it if a
    // structurally valid backup exists; that backup is the last known-good
    // radio-profile image and must remain available for manual recovery.
    const ImageReadResult backup_image = readImage(BackupPath, bytes, sizeof(bytes));
    hold_ = true; saved_ = {}; primary_preamble_ = 0; cross_ = RadioCrossMode::Off;
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
  if (symbols == primary_preamble_ && !primary_preamble_dirty_) return true;
  if (hasReplyMutation()) return false;
  if (!save(saved_, symbols, cross_)) return false;
  primary_preamble_ = symbols;
  primary_preamble_dirty_ = false;
  primary_preamble_retry_ms_ = 0;
  return true;
}

bool RadioProfileCLI::restorePrimaryPreamble(uint16_t symbols) {
  if (symbols && (symbols < 8 || symbols > RadioProfiles::MaxPreamble)) return false;
  if (savePrimaryPreamble(symbols)) return true;
  primary_preamble_ = symbols;
  primary_preamble_dirty_ = true;
  primary_preamble_retry_ms_ = 0;
  return false;
}

bool RadioProfileCLI::acceptsPrimary(float freq, float bw, uint8_t sf, uint8_t cr, uint16_t preamble) const {
  const auto* profiles = radio_ ? radio_->profiles() : nullptr;
  if (!profiles) return preamble == 0;
  auto preview = *profiles;
  auto& p = preview.primary;
  p.freq = freq; p.bw = bw; p.sf = sf; p.cr = cr; p.preamble = preamble;
  return radio_->validateProfile(p) && preview.automaticPreambleFits();
}

void RadioProfileCLI::publish() {
  auto* profiles = radio_ ? radio_->profiles() : nullptr;
  if (!profiles) return;
  profiles->cross = cross_;
  profiles->reply_tx = infrastructure_replies_
      ? (validReplySetting(reply_setting_) ? reply_setting_ & 7 : RADIO_TX_BOTH) : RADIO_TX_AUTO;
  profiles->reply_force = infrastructure_replies_
      && validReplySetting(reply_setting_) && (reply_setting_ & 8);
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  protectExternalSecondary();
  if (external_secondary_override_) return;
#endif
  if (publish_pending_ && (int32_t)(millis() - publish_after_ms_) < 0) return;
  // Reply packets own the old RX generation until they finish. A post-ACK
  // storage retry must not extend an expired temporary lease.
  if (hasReplyMutation() && !remote_delivered_) return;
  publish_pending_ = false;
  RadioProfileConfig config = temp_active_ ? temporary_ : saved_;
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  if (auto_secondary_requested_ && canAutoSecondary()) config = auto_secondary_;
  else if (secondary_protocol_overlay_ && manualSecondaryConfigured() && !secondaryRuntimeBusy()) {
    config.params.protocol = secondary_protocol_;
    if (secondary_protocol_ == RadioProfileProtocol::Meshtext) config.params.preamble = 16;
  }
#endif
  profiles->setSecondary(config, temp_active_);
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  owns_secondary_runtime_ = auto_secondary_requested_ || secondary_protocol_overlay_;
  if (owns_secondary_runtime_) {
    owned_secondary_snapshot_ = config;
    owned_secondary_generation_ = profiles->generation[1];
  }
#endif
}

#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
bool RadioProfileCLI::runtimeSecondaryUnchanged() const {
  if (!owns_secondary_runtime_ || !radio_ || !radio_->profiles()) return true;
  const auto* profiles = radio_->profiles();
  return profiles->generation[1] == owned_secondary_generation_
      && profiles->secondary.mode == owned_secondary_snapshot_.mode
      && profiles->secondary.params == owned_secondary_snapshot_.params;
}

void RadioProfileCLI::protectExternalSecondary() {
  if (runtimeSecondaryUnchanged()) return;
  // Another owner changed the running tuple without a CLI mutation. Relinquish
  // our RAM layer without restoring Off over that owner's settings, now or on
  // later loop publications. An explicit CLI change can claim ownership back.
  external_secondary_override_ = true;
  owns_secondary_runtime_ = auto_secondary_requested_ = secondary_protocol_overlay_ = false;
}
#endif

bool RadioProfileCLI::hasSecondarySchedule() const {
  for (const auto& schedule : schedules_) if (schedule.active) return true;
  return false;
}

bool RadioProfileCLI::secondaryRuntimeBusy() const {
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  if (external_secondary_override_ || !runtimeSecondaryUnchanged()) return true;
#endif
  if (!fs_ || !radio_) return true;
  const auto* profiles = radio_->profiles();
  return !profiles || hold_ || temp_pending_ || temp_active_
      || publish_pending_ || hasReplyMutation() || hasSecondarySchedule()
      || profiles->primary_temporary || profiles->secondary_temporary;
}

void RadioProfileCLI::noteSecondaryManualChange() {
  ++secondary_intent_generation_;
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  auto_secondary_requested_ = secondary_protocol_overlay_ = false;
  owns_secondary_runtime_ = external_secondary_override_ = false;
#endif
}

bool RadioProfileCLI::canAutoSecondary() const {
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  if (manual_secondary_ || saved_.mode != RadioProfileMode::Off || secondaryRuntimeBusy()) return false;
  const auto& current = radio_->profiles()->secondary;
  return current.mode == RadioProfileMode::Off || (auto_secondary_requested_
      && current.mode == auto_secondary_.mode && current.params == auto_secondary_.params);
#else
  return false;
#endif
}

bool RadioProfileCLI::requestAutoSecondary(const RadioProfileConfig& config) {
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  if (!canAutoSecondary() || config.mode == RadioProfileMode::Off
      || (uint8_t)config.mode > (uint8_t)RadioProfileMode::RxTx
      || !radio_->validateProfile(config.params)) return false;
  if (auto_secondary_requested_ && (config.mode != auto_secondary_.mode
      || config.params != auto_secondary_.params)) return false;
  auto preview = *radio_->profiles();
  preview.setSecondary(config, false);
  if (!preview.automaticPreambleFits()) return false;
  auto_secondary_ = config;
  auto_secondary_requested_ = true;
  publish();
  return autoSecondaryActive();
#else
  (void)config;
  return false;
#endif
}

void RadioProfileCLI::clearAutoSecondary() {
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  if (!auto_secondary_requested_) return;
  protectExternalSecondary();
  auto_secondary_requested_ = false;
  owns_secondary_runtime_ = secondary_protocol_overlay_;
  publish();
#endif
}

bool RadioProfileCLI::autoSecondaryActive() const {
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  if (!auto_secondary_requested_ || !canAutoSecondary()) return false;
  const auto& current = radio_->profiles()->secondary;
  return !radio_->profiles()->secondary_temporary && current.mode == auto_secondary_.mode
      && current.params == auto_secondary_.params;
#else
  return false;
#endif
}

bool RadioProfileCLI::useSecondaryProtocol(RadioProfileProtocol protocol) {
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  if (protocol == RadioProfileProtocol::MeshCore) {
    clearSecondaryProtocol();
    return true;
  }
  const bool supported_protocol =
#if MESH_ENABLE_MESHTEXT_CHAT
      protocol == RadioProfileProtocol::Meshtext ||
#endif
#if MESH_ENABLE_ETHERNET_TUNNEL
      protocol == RadioProfileProtocol::Ethernet ||
#endif
#if MESH_ENABLE_HOST_KISS
      protocol == RadioProfileProtocol::Kiss ||
#endif
      false;
  if (!supported_protocol || !manualSecondaryConfigured()
      || secondaryRuntimeBusy()) return false;
  // Never silently replace another foreign owner's overlay.
  if (secondary_protocol_overlay_ && secondary_protocol_ != protocol) return false;
  auto preview = *radio_->profiles();
  auto config = saved_;
  config.params.protocol = protocol;
  if (protocol == RadioProfileProtocol::Meshtext) config.params.preamble = 16;
  preview.setSecondary(config, false);
  if (!radio_->validateProfile(config.params) || !preview.automaticPreambleFits()) return false;
  secondary_protocol_ = protocol;
  secondary_protocol_overlay_ = true;
  publish();
  return true;
#else
  (void)protocol;
  return false;
#endif
}

void RadioProfileCLI::clearSecondaryProtocol() {
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  if (!secondary_protocol_overlay_) return;
  protectExternalSecondary();
  secondary_protocol_overlay_ = false;
  owns_secondary_runtime_ = auto_secondary_requested_;
  publish();
#endif
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

#if !MESH_CLIENT_REPEATER_ONLY
// Companion channel joins use this transaction; repeaters use the CLI below.
bool RadioProfileCLI::stageChannelPreset(const char* name) {
  if (!name || !cli::radio2PresetTuple(name) || remote_command_
      || !secondaryRuntimeAvailable()) return false;
  const auto& secondary = radio_->profiles()->secondary;
  if (secondary.mode != RadioProfileMode::Off
      && secondary.params.protocol != RadioProfileProtocol::MeshCore) return false;
  char command[48], reply[160];
  snprintf(command, sizeof(command), "set radio2 %s", name);
  beginReplyCommand();
  const bool handled = handle(command, reply, sizeof(reply));
  endReplyCommand();
  return handled && !strncmp(reply, "OK", 2)
      && remote_mutation_ == RemoteMutation::Saved;
}

bool RadioProfileCLI::finishChannelPreset(bool commit) {
  if (remote_command_ || remote_mutation_ != RemoteMutation::Saved) return false;
  if (!commit) return finishReplyMutation(false);
  if (!applyReplyMutation()) return false;
  publish_after_ms_ = millis() + 2000;
  return true;
}
#endif

#if MESH_ENABLE_FLEET_CONTROL
bool RadioProfileCLI::stageRemoteSchedule(unsigned slot, const Schedule& schedule) {
  if (slot >= 4 || !stageRemoteMutation(schedule.temporary
          ? RemoteMutation::AddTemporarySchedule : RemoteMutation::AddSavedSchedule,
          schedule.config, schedule.end, uint8_t(1U << slot))) return false;
  // Reuse the pending mutation's two time fields for the accepted UTC start
  // and end. Keep the separate monotonic end anchored at command admission;
  // neither a late reply nor a wall-clock correction may extend the lease.
  remote_start_ms_ = schedule.start;
  remote_schedule_end_ms_ = schedule.end_ms;
  return true;
}
#endif

bool RadioProfileCLI::applyReplyMutation() {
  const auto kind = remote_mutation_;
  if (kind == RemoteMutation::Saved && !mediaMeshScopeReady(remote_config_)) return false;
  if ((kind == RemoteMutation::Saved || kind == RemoteMutation::Off)
      && !commitSavedImage(ReplyPath)) return false;
  remote_mutation_ = RemoteMutation::None;
  remote_delivered_ = false;
  if (kind == RemoteMutation::None) return true;
  noteSecondaryManualChange();
  if (kind == RemoteMutation::Temporary) {
    pending_temporary_ = remote_config_;
    pending_duration_ms_ = remote_duration_ms_;
    temp_start_ms_ = remote_start_ms_;  // waiting never extends the hard lease
    temp_pending_ = true;
  } else if (kind == RemoteMutation::Saved) {
    saved_ = remote_config_;
    manual_secondary_ = true;
    publish_pending_ = true; publish_after_ms_ = millis();
#if MESH_ENABLE_FLEET_CONTROL
  } else if (kind == RemoteMutation::AddSavedSchedule
             || kind == RemoteMutation::AddTemporarySchedule) {
    const bool temporary = kind == RemoteMutation::AddTemporarySchedule;
    const uint32_t now = millis(), epoch = rtc_ ? rtc_->getCurrentTime() : 0;
    if (temporary && (!remainingMillis(remote_schedule_end_ms_, now)
                      || epoch >= remote_duration_ms_)) return true;
    for (unsigned i = 0; i < 4; ++i) if (remote_delete_mask_ & (1U << i)) {
      Schedule& scheduled = schedules_[(temporary ? 4 : 0) + i];
      scheduled = {};
      scheduled.config = remote_config_;
      scheduled.start = remote_start_ms_;
      scheduled.end = remote_duration_ms_;
      scheduled.end_ms = remote_schedule_end_ms_;
      scheduled.temporary = temporary;
      scheduled.active = true;
      break;
    }
#endif
  } else if (kind == RemoteMutation::DeleteTemp) {
    for (unsigned i = 0; i < 4; ++i) if (remote_delete_mask_ & (1U << i)) {
      if (schedules_[4+i].started) { temp_active_ = false; temp_remaining_ms_ = 0; }
      schedules_[4+i] = {};
    }
#if MESH_ENABLE_FLEET_CONTROL
  } else if (kind == RemoteMutation::DeleteSavedSchedule) {
    for (unsigned i = 0; i < 4; ++i) if (remote_delete_mask_ & (1U << i)) {
      schedules_[i] = {};
    }
    schedule_retry_ms_ = 0;
#endif
  } else {
    if (kind == RemoteMutation::Off) { saved_ = {}; manual_secondary_ = true; schedule_retry_ms_ = 0; }
    temp_pending_ = temp_active_ = false; temp_remaining_ms_ = 0;
    for (auto& s : schedules_) if (kind == RemoteMutation::Off || s.temporary) s = {};
  }
  // loop() owns publishing, after all completion/failure callbacks have
  // returned and Dispatcher no longer owns either response packet.
  if (kind == RemoteMutation::Saved || kind == RemoteMutation::Off) updateMediaMeshScope();
  return true;
}

void RadioProfileCLI::servicePendingTuning() {
  if (primary_preamble_dirty_ && (!primary_preamble_retry_ms_
      || (int32_t)(millis() - primary_preamble_retry_ms_) >= 0)
      && !savePrimaryPreamble(primary_preamble_)) {
    primary_preamble_retry_ms_ = millis() + 1000;
    if (!primary_preamble_retry_ms_) primary_preamble_retry_ms_ = 1;
  }
#if MESH_RADIO_SWITCH_TUNING
  const uint32_t now = millis();
  if (fast_pending_ && radio_ && (!fast_retry_ms_ || (int32_t)(now - fast_retry_ms_) >= 0)) {
    // A saved setting may fail RX verification as well as be temporarily busy.
    // Avoid repeatedly applying and rolling it back at the main-loop rate.
    const auto result = radio_->trySetSwitchTuning(fast_saved_);
    fast_pending_ = result != RadioParamApplyResult::APPLIED;
    fast_retry_ms_ = result == RadioParamApplyResult::FAILED ? now + 1000 : 0;
    if (result == RadioParamApplyResult::FAILED && !fast_retry_ms_) fast_retry_ms_ = 1;
  }
#endif
}

void RadioProfileCLI::loop() {
  const uint32_t now = millis();
  servicePendingTuning();
  retryMediaMeshScope();
  if (!radio_ || !radio_->profiles()) return;
  const uint32_t elapsed = now - last_ms_;
  last_ms_ = now;
  remote_commit_retry_ms_ = elapsed >= remote_commit_retry_ms_ ? 0 : remote_commit_retry_ms_ - elapsed;
  if (remote_delivered_ && !remote_commit_retry_ms_ && !applyReplyMutation()) {
    // Keep the accepted operation pending if the post-ACK atomic rename fails.
    // Saved intent stays authoritative while expired leases can restore it;
    // never apply an unsaved tuple or retry storage in a hot loop.
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
    if (!mediaMeshScopeReady(next->config)
        || !save(next->config, primary_preamble_, cross_, true)) {
      // Keep the requested time and order intact. A failed earlier entry must
      // not be retried after a later one and overwrite the newer settings.
      schedule_retry_ms_ = 60000;
      break;
    }
    saved_ = next->config; manual_secondary_ = true; noteSecondaryManualChange(); *next = {};
    updateMediaMeshScope();
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
  appendReply(reply, capacity, ",preamble=%u",
                               (unsigned)radio_->profilePreamble(profile));
  appendChirpWarning(reply, capacity, *radio_->profiles());
}

void RadioProfileCLI::appendSavedPreamble(char* reply, size_t capacity, uint8_t sf, float bw) const {
  if (!capacity || !radio_) return;
  const auto* profiles = radio_->profiles();
  if (!profiles) return;
  auto preview = *profiles;
  preview.primary.sf = sf; preview.primary.bw = bw;
  preview.primary.preamble = primary_preamble_;
  if (preview.primary != profiles->primary) preview.resetSwitchTest();
  appendReply(reply, capacity, ",preamble=%u%s",
      preview.preamble(0, rxPowerSavingPreambleForParams(sf, bw)), primary_preamble_ ? "" : " (auto)");
  appendChirpWarning(reply, capacity, preview);
}

void RadioProfileCLI::appendPrimaryChirpWarning(char* reply, size_t capacity, uint8_t sf, float bw,
                                               uint16_t preamble) const {
  const auto* profiles = radio_ ? radio_->profiles() : nullptr;
  if (!profiles) return;
  auto preview = *profiles;
  preview.primary.sf = sf; preview.primary.bw = bw; preview.primary.preamble = preamble;
  if (preview.primary != profiles->primary) preview.resetSwitchTest();
  appendChirpWarning(reply, capacity, preview);
}

void RadioProfileCLI::appendSecondaryWarnings(char* reply, size_t capacity,
                                              const RadioProfiles& preview) const {
  if (!capacity) return;
  if (mediaMeshScopePending()) appendReply(reply, capacity, "; MediaMesh scope save pending");
  if (preview.enabled() && RadioProfiles::valid(preview.primary)
      && RadioProfiles::valid(preview.secondary.params)) {
    const uint32_t primary_hz = frequencyHz(preview.primary.freq);
    const uint32_t clearance_hz = secondaryClearanceHz(preview);
    if (secondaryOverlaps(primary_hz, preview.secondary.params.freq, clearance_hz)) {
      appendReply(reply, capacity,
          "; WARN radio2 overlaps radio1 (6kHz guard)");
      const bool prefer_upper = preview.secondary.params.freq >= preview.primary.freq;
      // Suggest the nearest whole-kHz center outside the guarded interval on
      // the requested side. Verify the float round-trip and the radio's own
      // frequency limits; fall back to the other side near a band limit.
      for (unsigned side = 0; side < 2; ++side) {
        const bool upper = prefer_upper != bool(side);
        uint32_t khz = upper ? (primary_hz + clearance_hz) / 1000 + 1
                             : (primary_hz - clearance_hz - 1) / 1000;
        auto suggested = preview.secondary.params;
        char frequency_text[12];
        bool clear = false;
        for (unsigned step = 0; step < 2; ++step) {
          snprintf(frequency_text, sizeof(frequency_text), "%lu.%03lu",
              (unsigned long)(khz / 1000), (unsigned long)(khz % 1000));
          // Validate the exact displayed token through the command parser:
          // its float rounding can differ from double-to-float conversion.
          if (!cli::parseDecimalStrict(frequency_text, suggested.freq)) break;
          clear = !secondaryOverlaps(primary_hz, suggested.freq, clearance_hz);
          if (clear) break;
          if (upper) ++khz; else --khz;
        }
        if (clear && radio_->validateProfile(suggested)) {
          appendReply(reply, capacity,
              "; try %sMHz", frequency_text);
          break;
        }
      }
    }
  }
  // The overlap warning must be visible even while retune timing is pending.
  appendChirpWarning(reply, capacity, preview);
}

void RadioProfileCLI::appendChirpWarning(char* reply, size_t capacity, const RadioProfiles& preview) {
  if (!capacity || !preview.enabled()) return;
  if (!preview.recommendationReady()) {
    appendReply(reply, capacity,
        "; timing self-test pending; wait=%lus",
        (unsigned long)((preview.recommendationRemainingMs(millis()) + 999) / 1000));
    return;
  }
  const auto timing = preview.chirpTiming();
  if (!timing.valid) return;
  const bool over_a = timing.preamble[0] > RadioProfiles::MaxAutomaticPreamble;
  const bool over_b = timing.preamble[1] > RadioProfiles::MaxAutomaticPreamble;
  if (over_a || over_b)
    appendReply(reply, capacity, "; WARN need >%u: %s",
             unsigned(RadioProfiles::MaxAutomaticPreamble),
             over_a && over_b ? "radio,radio2" : over_a ? "radio" : "radio2");
  const bool short_a = preview.primary.preamble && preview.primary.preamble < timing.preamble[0];
  const bool short_b = preview.secondary.params.preamble && preview.secondary.params.preamble < timing.preamble[1];
  const bool a = timing.preamble[0] > 32 || short_a, b = timing.preamble[1] > 32 || short_b;
  if (a || b) {
    appendReply(reply, capacity, "; WARN recommended preamble: %s=%.0f",
                a ? "radio" : "radio2", timing.preamble[a ? 0 : 1]);
    if (a && b) appendReply(reply, capacity, ",radio2=%.0f", timing.preamble[1]);
  }
  if (short_a || short_b)
    appendReply(reply, capacity, "; short override: %s",
             short_a && short_b ? "radio,radio2" : short_a ? "radio" : "radio2");
  // Preamble-bearing acknowledgements and getters share this formatter. Only
  // report the measured allowance for the same calibrated profile pair; a new
  // pair still has to activate and complete its self-test after the first ACK.
  // Dedicated timing/status getters already include this value under their
  // existing switch/budget labels.
  if (!strstr(reply, "switch=") && !strstr(reply, "budget="))
    appendReply(reply, capacity, "; switch=%luus",
             (unsigned long)preview.switchBudgetUs());
}

void RadioProfileCLI::formatConfig(char* reply, size_t capacity, const RadioProfileConfig& config,
                                   bool temporary, uint32_t remaining, bool automatic,
                                   const Schedule* schedule) const {
  if (config.mode == RadioProfileMode::Off) { snprintf(reply, capacity, "> off"); return; }
  RadioProfiles preview = *radio_->profiles();
  preview.setSecondary(config, temporary);
  uint16_t preamble = preview.preamble(1, 32);
  snprintf(reply, capacity, "> %.3f,%.3f,%u,%u,%s,%u%s%s", config.params.freq, config.params.bw,
           config.params.sf, config.params.cr, modeName(config.mode), preamble,
           config.params.preamble ? "" : " (auto)", automatic ? "; auto-owned" : "");
  if (schedule) {
    appendReply(reply, capacity, "; @%lu-%lu",
        (unsigned long)schedule->start, (unsigned long)schedule->end);
  } else if (temporary) {
    uint32_t minutes = (remaining + 59999UL) / 60000UL;
    appendReply(reply, capacity, "; %lud%luh%lum (%lu min) left",
        (unsigned long)(minutes / 1440), (unsigned long)(minutes / 60 % 24),
        (unsigned long)(minutes % 60), (unsigned long)minutes);
  }
  appendSecondaryWarnings(reply, capacity, preview);
}

bool RadioProfileCLI::handle(const char* command, char* reply, size_t capacity, bool remote_origin) {
#if defined(MESH_HIL_TX_FREQ_MHZ)
  if (handleHilTxFrequencyCommand(command, reply, capacity)) return true;
#endif
#if MESH_ENABLE_CARRIER_TOOLS
  if (handleCarrierWaveCommand(radio_, command, reply, capacity)) return true;
#endif
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
#if MESH_RADIO_SWITCH_TUNING
  if (handleFastSettings(key, args, verb, remote_origin, reply, capacity)) return true;
#endif
  enum { Base, Temporary, Scheduled, ScheduledTemporary, Crossing, Replies,
         Status, Scan, Timing, TimingAlias, KeyCount };
  static const char keys[] = "radio2\0tempradio2\0radioat2\0tempradioat2\0radio2.cross\0"
      "tx.reply\0radio2.status\0radio2.scan\0radio2.timing\0radio.timing";
  const char* candidate = keys;
  unsigned kind = 0;
  while (kind < KeyCount && strcmp(key, candidate)) {
    candidate += strlen(candidate) + 1;
    ++kind;
  }
  if (kind == KeyCount || (kind == Replies && !infrastructure_replies_)) return false;
  const bool base = kind == Base;
  const bool temporary = kind == Temporary;
  const bool scheduled = kind == Scheduled || kind == ScheduledTemporary;
  const bool scheduled_temp = kind == ScheduledTemporary;
  const bool crossing = kind == Crossing;
  const bool replies = kind == Replies;
  const bool status = kind == Status;
  const bool scan = kind == Scan;
  const bool timing = kind == Timing || kind == TimingAlias;
  if (!radio_ || !radio_->profiles()) { cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("radio profiles unsupported", "profiles unsupported")); return true; }
  if (command == text && *args && verb == Get && (temporary || base)) verb = Set;
  if (remote_origin && !remote_command_
      && (((base || temporary) && verb == Set) || (scheduled_temp && verb == Delete))) {
    cli::formatError(reply, capacity, "this role requires local USB for radio2 changes"); return true;
  }
  if (hasReplyMutation() && verb != Get) {
    cli::formatError(reply, capacity, "radio acknowledgement pending"); return true;
  }
  if ((timing || scan || status) && (verb != Get || *args)) {
    snprintf(reply, capacity, "Error: %s is read-only", timing ? "radio timing" : key);
    return true;
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
      cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("use get/set tx.reply auto|radio|radio2|both|off [force for radio2/both]", "get/set tx.reply auto|radio|radio2|both|off [force for radio2/both]"));
      return true;
    }
    const uint8_t previous = reply_setting_;
    reply_setting_ = 0xb0 | policy | (force ? 8 : 0);
    if (!save(saved_, primary_preamble_, cross_)) {
      reply_setting_ = previous;
      cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("settings could not be saved", "settings not saved"));
    } else {
      publish();
      snprintf(reply, capacity, "OK - tx.reply=%s%s", radioTxPolicyName(policy), force ? " force" : "");
    }
    return true;
  }
  if (timing) {
    const auto& p = *radio_->profiles();
    const auto t = p.chirpTiming();
    if (!p.enabled()) snprintf(reply, capacity, "> off");
    else if (!p.recommendationReady()) snprintf(reply, capacity,
        "> timing self-test pending; test=%u/%u,%u/%u; wait=%lus; provisional switch=%luus preamble=%u,%u",
        p.switch_test_samples[0], RadioProfiles::SwitchTestSamplesPerDirection,
        p.switch_test_samples[1], RadioProfiles::SwitchTestSamplesPerDirection,
        (unsigned long)((p.recommendationRemainingMs(millis()) + 999) / 1000),
        (unsigned long)p.switchBudgetUs(),
        radio_->profilePreamble(0), radio_->profilePreamble(1));
    else if (!t.valid) cli::formatError(reply, capacity, "timing unavailable");
    else {
      snprintf(reply, capacity, "> chirps=%.2f,%.2f; need=%.0f,%.0f; switch=%luus; loop=%luus%s",
          t.listen_us[0] / t.symbol_us[0], t.listen_us[1] / t.symbol_us[1],
          t.preamble[0], t.preamble[1], (unsigned long)t.switch_us,
          (unsigned long)t.loop_us, t.loop_us ? " (estimate)" : "");
      appendReply(reply, capacity, "; test=%u/%u,%u/%u%s",
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
      else cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("settings could not be saved", "settings not saved"));
    } else cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("use get/set radio2.cross [auto|on|off]", "get/set radio2.cross [auto|on|off]"));
    return true;
  }
  if (scan) {
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
      appendReply(reply, capacity, "; test=%u/%u,%u/%u%s budget=%luus",
          p.switch_test_samples[0], RadioProfiles::SwitchTestSamplesPerDirection,
          p.switch_test_samples[1], RadioProfiles::SwitchTestSamplesPerDirection,
          p.recommendationReady() ? " ready" : " pending", (unsigned long)p.switchBudgetUs());
      appendChirpWarning(reply, capacity, p);
    }
    if (mediaMeshScopePending()) appendReply(reply, capacity, "; MediaMesh scope save pending");
    return true;
  }
  if ((base || temporary) && verb == Get && !*args) {
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
    if (base && autoSecondaryActive()) {
      formatConfig(reply, capacity, auto_secondary_, false, 0, true);
      return true;
    }
    if (base && saved_.mode == RadioProfileMode::Off) {
      snprintf(reply, capacity, "> off (%s)", manual_secondary_ ? "manual" : "automatic allowed");
      if (mediaMeshScopePending()) appendReply(reply, capacity, "; MediaMesh scope save pending");
      return true;
    }
#endif
    if (temporary && !temp_active_ && !temp_pending_) snprintf(reply, capacity, "> off");
    else formatConfig(reply, capacity, temporary ? (temp_pending_ ? pending_temporary_ : temporary_) : saved_,
                      temporary, temp_pending_ ? pending_duration_ms_ : temp_remaining_ms_);
    return true;
  }
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  if (base && verb == Set && !strcmp(args, "auto")) {
    // This clears only saved permission, never an active/pending lease or a
    // schedule. Remote acknowledgement staging must not weaken that contract.
    if (remote_command_ || temp_pending_ || temp_active_ || hasSecondarySchedule()
        || hasReplyMutation() || publish_pending_ || radio_->profiles()->primary_temporary
        || radio_->profiles()->secondary_temporary) {
      cli::formatError(reply, capacity, "automatic radio2 requires no pending change, lease or schedule");
      return true;
    }
    if (!save({}, primary_preamble_, cross_, 0)) {
      cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("settings could not be saved", "settings not saved")); return true;
    }
    noteSecondaryManualChange();
    saved_ = {}; manual_secondary_ = false;
    updateMediaMeshScope();
    publish_pending_ = true; publish_after_ms_ = millis() + 2000;
    publish();
    snprintf(reply, capacity, "OK - automatic radio2 permitted in 2s");
    if (mediaMeshScopePending()) appendReply(reply, capacity, "; MediaMesh scope save pending");
    return true;
  }
#endif
  if ((base || temporary) && verb == Set && !strcmp(args, "off")) {
    if (base && !(remote_command_ ? prepareSavedImage(ReplyPath, {}, primary_preamble_, cross_, true)
                                 : save({}, primary_preamble_, cross_, true))) {
      cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("settings could not be saved", "settings not saved")); return true;
    }
    if (remote_command_) {
      stageRemoteMutation(base ? RemoteMutation::Off : RemoteMutation::TempOff);
      snprintf(reply, capacity, "OK - %s after reply", !base && saved_.mode != RadioProfileMode::Off
          ? "saved radio2 restored" : "single radio");
      return true;
    }
    noteSecondaryManualChange();
    if (base) {
      saved_ = {}; manual_secondary_ = true; schedule_retry_ms_ = 0;
      updateMediaMeshScope();
    }
    temp_pending_ = temp_active_ = false; temp_remaining_ms_ = 0;
    for (auto& s : schedules_) if (base || s.temporary) s = {};
    publish_pending_ = true; publish_after_ms_ = millis() + 2000;
    publish(); snprintf(reply, capacity, "OK - %s in 2s", saved_.mode != RadioProfileMode::Off ? "saved radio2 restored" : "single radio");
    if (mediaMeshScopePending()) appendReply(reply, capacity, "; MediaMesh scope save pending");
    return true;
  }
  if (scheduled && (verb == Get || verb == Delete)) {
    uint32_t index = 0;
    if (*args && strcmp(args, "all") && (!cli::parseUnsignedIntegerStrict(args, index) || index < 1 || index > 4)) {
      cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("slot must be 1-4 or all", "slot: 1-4|all")); return true;
    }
    const unsigned first = scheduled_temp ? 4 : 0;
    if (verb == Delete) {
      if (remote_command_ && (scheduled_temp || MESH_ENABLE_FLEET_CONTROL)) {
#if MESH_ENABLE_FLEET_CONTROL
        stageRemoteMutation(scheduled_temp ? RemoteMutation::DeleteTemp
                                          : RemoteMutation::DeleteSavedSchedule,
                            {}, 0, index ? 1U << (index - 1) : 15);
#else
        stageRemoteMutation(RemoteMutation::DeleteTemp, {}, 0, index ? 1U << (index - 1) : 15);
#endif
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
      noteSecondaryManualChange();
      publish(); snprintf(reply, capacity, "OK"); return true;
    }
    if (!index) {
      snprintf(reply, capacity, "> slots: ");
      for (unsigned i = 0; i < 4; ++i) if (schedules_[first+i].active) {
        appendReply(reply, capacity, "%u@%lu ", i+1, (unsigned long)schedules_[first+i].start);
      }
      return true;
    }
    const auto& s = schedules_[first+index-1];
    if (!s.active) snprintf(reply, capacity, "> off");
    else {
      formatConfig(reply, capacity, s.config, s.temporary, remainingMillis(s.end_ms, millis()), false, &s);
    }
    return true;
  }
  if (verb != Set) { cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("invalid radio2 command", "bad radio2 command")); return true; }
  if (base) {
    const char* preset = cli::radio2PresetTuple(args);
    if (preset) args = preset;
  }
  char local[140];
  if (strlen(args) >= sizeof(local)) { cli::formatError(reply, capacity, "params too long"); return true; }
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
  const uint32_t epoch = scheduled && rtc_ ? rtc_->getCurrentTime() : 0;
  if (valid && count > expected) valid = parsePreamble(parts[expected], config.params.preamble);
  if (valid && temporary) valid = cli::parseUnsignedIntegerStrict(parts[5], minutes) && minutes >= 1 && minutes <= 10080;
  if (valid && scheduled) valid = cli::parseRadioScheduleTime(parts[5], epoch, start);
  if (valid && scheduled_temp) valid = cli::parseRadioScheduleTime(parts[6], epoch, end);
  if (valid) valid = radio_->validateProfile(config.params);
  RadioProfiles preview = *radio_->profiles(); preview.setSecondary(config, false);
  if (valid) valid = preview.automaticPreambleFits();
  if (!valid) { snprintf(reply, capacity, MESH_CLI_REPLY_TEXT("Error: use %s f,bw,sf,cr,rx|rxtx%s[,preamble|auto]", "Error: %s f,bw,sf,cr,rx|rxtx%s[,preamble|auto]"), key,
      scheduled_temp ? ",start,end (UTC or +minutes)" : scheduled ? ",start (UTC or +minutes)" : temporary ? ",minutes" : ""); return true; }
  if (base && !mediaMeshScopeReady(config)) {
    cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("MediaMesh region unavailable or full", "MediaMesh region unavailable/full")); return true;
  }
  if (scheduled) {
    if (!epoch || start <= epoch || (uint64_t)(start-epoch) * 1000 > 0x7fffffffULL
        || (scheduled_temp && (end <= start || (uint64_t)(end-epoch) * 1000 > 0x7fffffffULL))) {
      cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("future UTC start/end required, within 24 days", "future UTC start/end within 24 days")); return true;
    }
    if (scheduled_temp) {
      const uint64_t pending_left = temp_pending_
          ? pending_duration_ms_ + (uint64_t)((int32_t)(temp_start_ms_ - millis()) > 0 ? temp_start_ms_ - millis() : 0)
          : 0;
      const uint64_t session_left = pending_left > temp_remaining_ms_ ? pending_left : temp_remaining_ms_;
      if ((temp_active_ || temp_pending_) && (uint64_t)(start-epoch)*1000 < session_left) {
        cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("temporary schedule overlaps active session", "temp schedule overlaps active session")); return true;
      }
      for (const auto& s : schedules_) if (s.active && s.temporary && start < s.end && end > s.start) {
        cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("temporary schedules overlap", "temp schedules overlap")); return true;
      }
    }
    const unsigned first = scheduled_temp ? 4 : 0;
    for (unsigned i = 0; i < 4; ++i) if (!schedules_[first+i].active) {
#if MESH_ENABLE_FLEET_CONTROL
      Schedule s;
#else
      auto& s = schedules_[first+i]; s = {};
#endif
      s.config = config; s.start = start; s.end = end; s.temporary = scheduled_temp;
      s.end_ms = scheduled_temp ? millis() + (end-epoch)*1000UL : 0; s.active = true;
#if MESH_ENABLE_FLEET_CONTROL
      if (remote_command_) {
        if (!stageRemoteSchedule(i, s)) {
          cli::formatError(reply, capacity, "radio acknowledgement pending"); return true;
        }
      } else { schedules_[first+i] = s; noteSecondaryManualChange(); publish(); }
#else
      noteSecondaryManualChange(); publish();
#endif
      snprintf(reply, capacity, "OK - %s %u queued, preamble=%u", key, i+1, preview.preamble(1, 32));
      appendSecondaryWarnings(reply, capacity, preview); return true;
    }
    cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("all four schedule slots occupied", "all four slots full")); return true;
  }
  if (temporary) {
    for (const auto& s : schedules_) if (s.active && s.temporary) {
      cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("clear tempradioat2 schedules first", "clear tempradioat2 first")); return true;
    }
    if (remote_command_) stageRemoteMutation(RemoteMutation::Temporary, config, minutes * 60000UL);
    else {
      noteSecondaryManualChange();
      pending_temporary_ = config; pending_duration_ms_ = minutes * 60000UL;
      temp_start_ms_ = millis() + 2000; temp_pending_ = true;
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
      publish_pending_ = true; publish_after_ms_ = temp_start_ms_;
#endif
    }
    // An existing session remains active until the new request can take over.
    snprintf(reply, capacity, "OK - tempradio2 %lud%luh%lum (%lu min); preamble=%u",
        (unsigned long)(minutes/1440), (unsigned long)(minutes/60%24),
        (unsigned long)(minutes%60), (unsigned long)minutes, preview.preamble(1, 32));
  } else if (remote_command_ ? prepareSavedImage(ReplyPath, config, primary_preamble_, cross_, true)
                            : save(config, primary_preamble_, cross_, true)) {
    if (remote_command_) stageRemoteMutation(RemoteMutation::Saved, config);
    else {
      noteSecondaryManualChange();
      saved_ = config;
      manual_secondary_ = true;
      updateMediaMeshScope();
      publish_pending_ = true; publish_after_ms_ = millis() + 2000;
      publish();
    }
    snprintf(reply, capacity, "OK - radio2 %s%s; preamble=%u", modeName(config.mode),
        remote_command_ ? " pending after reply" : "", preview.preamble(1, 32));
  } else cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("settings could not be saved", "settings not saved"));
  if (!strncmp(reply, "OK", 2)) appendSecondaryWarnings(reply, capacity, preview);
  return true;
}

}  // namespace mesh
