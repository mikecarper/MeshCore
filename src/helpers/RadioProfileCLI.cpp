#include "RadioProfileCLI.h"
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
}

bool RadioProfileCLI::readImage(const char* path, uint8_t* bytes, size_t size) {
#if defined(NRF52_PLATFORM)
  File file(*fs_);
  if (!file.open(path, FILE_O_READ)) return false;
#elif defined(STM32_PLATFORM)
  File file = fs_->open(path, FILE_O_READ);
#else
  File file = fs_->open(path, "r");
#endif
  if (!file) return false;
  bool ok = file.size() == size && file.read(bytes, size) == (int)size;
  file.close();
  if (!ok) return false;
  uint32_t stored;
  memcpy(&stored, bytes + size - 4, 4);
  return bytes[0] == 'R' && bytes[1] == '2' && bytes[2] == 1
      && stored == checksum(bytes, size - 4);
}

bool RadioProfileCLI::writeImage(const char* path, const uint8_t* bytes, size_t size) {
  if (fs_->exists(path)) fs_->remove(path);
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
  return ok && readImage(path, verify, size) && memcmp(bytes, verify, size) == 0;
}

bool RadioProfileCLI::save(const RadioProfileConfig& config, uint16_t preamble, RadioCrossMode cross) {
  if (!fs_ || hold_) return false;
  uint8_t image[ImageSize] = {'R', '2', 1, (uint8_t)config.mode};
  memcpy(image + 4, &config.params.freq, 4);
  memcpy(image + 8, &config.params.bw, 4);
  image[12] = config.params.sf; image[13] = config.params.cr;
  memcpy(image + 14, &config.params.preamble, 2);
  memcpy(image + 16, &preamble, 2);
  image[18] = (uint8_t)cross;
  const uint32_t crc = checksum(image, ImageSize - 4);
  memcpy(image + ImageSize - 4, &crc, 4);
  if (!writeImage(TempPath, image, sizeof(image))) return false;
  if (fs_->exists(ImagePath)) {
    if (fs_->exists(BackupPath)) fs_->remove(BackupPath);
    if (!fs_->rename(ImagePath, BackupPath)) return false;
  }
  if (!fs_->rename(TempPath, ImagePath)) {
    if (fs_->exists(BackupPath)) fs_->rename(BackupPath, ImagePath);
    return false;
  }
  if (fs_->exists(BackupPath)) fs_->remove(BackupPath);
  return true;
}

void RadioProfileCLI::begin(FILESYSTEM* fs, Radio* radio, RTCClock* rtc) {
  fs_ = fs; radio_ = radio; rtc_ = rtc; last_ms_ = millis();
  if (!fs_ || !radio_ || !radio_->profiles()) return;
  uint8_t bytes[ImageSize];
  bool loaded = readImage(ImagePath, bytes, sizeof(bytes));
  if (!loaded && readImage(BackupPath, bytes, sizeof(bytes))) {
    if (fs_->exists(ImagePath)) fs_->remove(ImagePath);
    loaded = fs_->rename(BackupPath, ImagePath);
  }
  if (!loaded) {
    hold_ = fs_->exists(ImagePath) || fs_->exists(BackupPath);
    return;
  }
  saved_.mode = (RadioProfileMode)bytes[3];
  memcpy(&saved_.params.freq, bytes + 4, 4);
  memcpy(&saved_.params.bw, bytes + 8, 4);
  saved_.params.sf = bytes[12]; saved_.params.cr = bytes[13];
  memcpy(&saved_.params.preamble, bytes + 14, 2);
  memcpy(&primary_preamble_, bytes + 16, 2);
  cross_ = (RadioCrossMode)bytes[18];
  if ((uint8_t)saved_.mode > 2 || (uint8_t)cross_ > 2
      || (saved_.mode != RadioProfileMode::Off && !radio_->validateProfile(saved_.params))
      || (primary_preamble_ && (primary_preamble_ < 8 || primary_preamble_ > RadioProfiles::MaxPreamble))) {
    hold_ = true; saved_ = {}; primary_preamble_ = 0; cross_ = RadioCrossMode::Auto;
    return;
  }
  stagePrimary(primary_preamble_, false);
  publish();
}

bool RadioProfileCLI::savePrimaryPreamble(uint16_t symbols) {
  if (symbols && (symbols < 8 || symbols > RadioProfiles::MaxPreamble)) return false;
  if (!radio_ || !radio_->profiles()) return symbols == 0;
  if (symbols == primary_preamble_) return true;
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
  if (publish_pending_ && (int32_t)(millis() - publish_after_ms_) < 0) return;
  publish_pending_ = false;
  radio_->profiles()->setSecondary(temp_active_ ? temporary_ : saved_, temp_active_);
}

void RadioProfileCLI::loop() {
  if (!radio_ || !radio_->profiles()) return;
  const uint32_t now = millis(), elapsed = now - last_ms_;
  last_ms_ = now;
  if (temp_pending_ && (int32_t)(now - temp_start_ms_) >= 0) {
    temporary_ = pending_temporary_;
    const uint32_t late = now - temp_start_ms_;
    temp_remaining_ms_ = late < pending_duration_ms_ ? pending_duration_ms_ - late : 0;
    temp_pending_ = false; temp_active_ = temp_remaining_ms_ != 0;
  } else if (temp_active_) {
    if (elapsed >= temp_remaining_ms_) { temp_remaining_ms_ = 0; temp_active_ = false; }
    else temp_remaining_ms_ -= elapsed;
  }
  const uint32_t epoch = rtc_ ? rtc_->getCurrentTime() : 0;
  for (auto& s : schedules_) {
    if (!s.active) continue;
    if (s.temporary) {
      if (elapsed >= s.remaining_ms || epoch >= s.end) {
        if (s.started) { temp_active_ = false; temp_remaining_ms_ = 0; }
        s = {}; continue;
      }
      s.remaining_ms -= elapsed;
    }
    if (s.started || epoch < s.start) continue;
    if (s.temporary) {
      if (temp_active_ || temp_pending_) continue;
      temporary_ = s.config;
      temp_remaining_ms_ = s.remaining_ms;
      const uint32_t epoch_left = (s.end - epoch) * 1000UL;
      if (temp_remaining_ms_ > epoch_left) temp_remaining_ms_ = epoch_left;
      temp_active_ = true; s.started = true;
    } else if (save(s.config, primary_preamble_, cross_)) {
      saved_ = s.config; s = {};
    } else {
      // Avoid a flash write on every main-loop iteration after a storage fault.
      s.start = epoch <= UINT32_MAX - 60 ? epoch + 60 : UINT32_MAX;
    }
  }
  publish();
}

bool RadioProfileCLI::parseSuffix(const char* input, unsigned fields, char* legacy,
                                 size_t capacity, uint16_t& preamble) {
  return cli::parseRadioPreambleSuffix(input, fields, legacy, capacity, preamble);
}

void RadioProfileCLI::appendPreamble(char* reply, size_t capacity, uint8_t profile) const {
  if (!radio_ || !radio_->profiles()) return;
  const size_t used = strlen(reply);
  if (used < capacity) snprintf(reply + used, capacity - used, ",preamble=%u",
                               (unsigned)radio_->profilePreamble(profile));
}

void RadioProfileCLI::appendSavedPreamble(char* reply, size_t capacity, uint8_t sf, float bw) const {
  if (!radio_ || !radio_->profiles()) return;
  auto preview = *radio_->profiles();
  preview.primary.sf = sf; preview.primary.bw = bw;
  preview.primary.preamble = primary_preamble_;
  const size_t used = strlen(reply);
  if (used < capacity) snprintf(reply + used, capacity - used, ",preamble=%u%s",
      preview.preamble(0, rxPowerSavingPreambleForParams(sf, bw)), primary_preamble_ ? "" : " (auto)");
}

void RadioProfileCLI::formatConfig(char* reply, size_t capacity, const RadioProfileConfig& config,
                                   bool temporary, uint32_t remaining) const {
  if (config.mode == RadioProfileMode::Off) { snprintf(reply, capacity, "> off"); return; }
  RadioProfiles preview = *radio_->profiles();
  preview.secondary = config;
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
}

bool RadioProfileCLI::handle(const char* command, char* reply, size_t capacity) {
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
  const bool status = !strcmp(key, "radio2.status");
  const bool scan = !strcmp(key, "radio2.scan");
  if (!base && !temporary && !scheduled && !crossing && !status && !scan) return false;
  if (!radio_ || !radio_->profiles()) { snprintf(reply, capacity, "Error: radio profiles unsupported"); return true; }
  if (command == text && *args && verb == Get && (temporary || base)) verb = Set;
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
    }
    return true;
  }
  if (status) {
    if (verb != Get || *args) { snprintf(reply, capacity, "Error: radio2.status is read-only"); return true; }
    const auto& p = *radio_->profiles();
    snprintf(reply, capacity, "> %s; RX=%lu,%lu TX=%lu,%lu switches=%lu errors=%lu max=%luus preamble=%u,%u",
        modeName(p.secondary.mode), (unsigned long)p.rx_packets[0], (unsigned long)p.rx_packets[1],
        (unsigned long)p.tx_packets[0], (unsigned long)p.tx_packets[1],
        (unsigned long)p.switches, (unsigned long)p.switch_failures,
        (unsigned long)p.longest_switch_us, radio_->profilePreamble(0), radio_->profilePreamble(1));
    return true;
  }
  if ((base || temporary) && verb == Get && !*args) {
    if (temporary && !temp_active_ && !temp_pending_) snprintf(reply, capacity, "> off");
    else formatConfig(reply, capacity, temporary ? (temp_pending_ ? pending_temporary_ : temporary_) : saved_,
                      temporary, temp_pending_ ? pending_duration_ms_ : temp_remaining_ms_);
    return true;
  }
  if ((base || temporary) && verb == Set && !strcmp(args, "off")) {
    if (base && !save({}, primary_preamble_, cross_)) {
      snprintf(reply, capacity, "Error: settings could not be saved"); return true;
    }
    if (base) saved_ = {};
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
      formatConfig(reply, capacity, s.config, s.temporary, s.remaining_ms);
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
  RadioProfiles preview = *radio_->profiles(); preview.secondary = config;
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
      s.remaining_ms = scheduled_temp ? (end-epoch)*1000UL : 0; s.active = true;
      snprintf(reply, capacity, "OK - %s %u queued, preamble=%u", key, i+1, preview.preamble(1, 32)); return true;
    }
    snprintf(reply, capacity, "Error: all four schedule slots occupied"); return true;
  }
  if (temporary) {
    for (const auto& s : schedules_) if (s.active && s.temporary) {
      snprintf(reply, capacity, "Error: clear tempradioat2 schedules first"); return true;
    }
    pending_temporary_ = config; pending_duration_ms_ = minutes * 60000UL;
    temp_start_ms_ = millis() + 2000; temp_pending_ = true;
    // An existing session remains active until the new request can take over.
    snprintf(reply, capacity, "OK - tempradio2 %lud%luh%lum (%lu min); preamble=%u",
        (unsigned long)(minutes/1440), (unsigned long)(minutes/60%24),
        (unsigned long)(minutes%60), (unsigned long)minutes, preview.preamble(1, 32));
  } else if (save(config, primary_preamble_, cross_)) {
    saved_ = config;
    publish_pending_ = true; publish_after_ms_ = millis() + 2000;
    publish();
    snprintf(reply, capacity, "OK - radio2 %s; preamble=%u", modeName(config.mode), preview.preamble(1, 32));
  } else snprintf(reply, capacity, "Error: settings could not be saved");
  return true;
}

}  // namespace mesh
