#include "BeaconManager.h"

#if MESH_ENABLE_TX_BEACONS

#include <Mesh.h>
#include <helpers/FilePresence.h>
#include <helpers/MeshtextChat.h>
#include <Arduino.h>
#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <new>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace mesh {
namespace {
constexpr size_t HeaderSize = 8, RecordSize = 98;
constexpr size_t ConfigSize = HeaderSize + BeaconManager::SlotCount * RecordSize + 4;
constexpr size_t IdSize = 16;
const char* const ConfigPath = "/beacons";
const char* const ConfigBackup = "/beacons.bak";
const char* const ConfigTemp = "/beacons.tmp";
const char* const IdPath = "/beacon_ids";
const char* const IdBackup = "/beacon_ids.bak";
const char* const IdTemp = "/beacon_ids.tmp";

uint32_t crcUpdate(uint32_t crc, const uint8_t* bytes, size_t size) {
  while (size--) {
    crc ^= *bytes++;
    for (unsigned i = 0; i < 8; ++i)
      crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
  }
  return crc;
}
void put32(uint8_t* bytes, uint32_t value) {
  for (unsigned i = 0; i < 4; ++i) bytes[i] = uint8_t(value >> (8 * i));
}
uint32_t get32(const uint8_t* bytes) {
  return uint32_t(bytes[0]) | (uint32_t(bytes[1]) << 8)
      | (uint32_t(bytes[2]) << 16) | (uint32_t(bytes[3]) << 24);
}
bool validInterval(uint32_t hours) {
  return hours == 12 || hours == 24 || hours == 36 || hours == 48;
}
void encodeSlot(const BeaconSlot& slot, uint8_t bytes[RecordSize]) {
  memset(bytes, 0, RecordSize);
  if (!slot.enabled) return;
  static_assert(sizeof(float) == 4, "beacon images require 32-bit floats");
  uint32_t bits;
  memcpy(&bits, &slot.radio.freq, 4); put32(bytes, bits);
  memcpy(&bits, &slot.radio.bw, 4); put32(bytes + 4, bits);
  bytes[8] = uint8_t(slot.radio.preamble);
  bytes[9] = uint8_t(slot.radio.preamble >> 8);
  bytes[10] = slot.radio.sf; bytes[11] = slot.radio.cr;
  bytes[12] = uint8_t(slot.radio.protocol); bytes[13] = uint8_t(slot.power);
  bytes[14] = slot.preset; bytes[15] = slot.hops; bytes[16] = 1;
  memcpy(bytes + 17, slot.text, BeaconSlot::TextCapacity);
}
bool decodeSlot(const uint8_t bytes[RecordSize], BeaconSlot& slot) {
  slot = {};
  if (bytes[16] == 0) {
    for (size_t i = 0; i < RecordSize; ++i) if (bytes[i]) return false;
    return true;
  }
  if (bytes[16] != 1 || bytes[12] > uint8_t(RadioProfileProtocol::Meshtext)
      || bytes[15] > 7 || !memchr(bytes + 17, 0, BeaconSlot::TextCapacity)
      || !bytes[17]) return false;
  uint32_t bits = get32(bytes); memcpy(&slot.radio.freq, &bits, 4);
  bits = get32(bytes + 4); memcpy(&slot.radio.bw, &bits, 4);
  slot.radio.preamble = uint16_t(bytes[8]) | (uint16_t(bytes[9]) << 8);
  slot.radio.sf = bytes[10]; slot.radio.cr = bytes[11];
  slot.radio.protocol = RadioProfileProtocol(bytes[12]);
  slot.power = int8_t(bytes[13]); slot.preset = bytes[14]; slot.hops = bytes[15];
  slot.enabled = true;
  memcpy(slot.text, bytes + 17, BeaconSlot::TextCapacity);
  // Reject noncanonical trailing data instead of carrying hidden text bytes.
  for (size_t i = strlen(slot.text) + 1; i < BeaconSlot::TextCapacity; ++i)
    if (slot.text[i]) return false;
  for (const unsigned char* c = reinterpret_cast<const unsigned char*>(slot.text); *c; ++c)
    if (*c < 32 || *c == 127) return false;
  return isfinite(slot.radio.freq) && isfinite(slot.radio.bw)
      && (slot.radio.protocol != RadioProfileProtocol::Meshtext
          || (slot.preset <= uint8_t(mt::Preset::LongSlow) && slot.radio.preamble == 16));
}
const char* skipSpace(const char* text) {
  while (*text == ' ' || *text == '\t') ++text;
  return text;
}
bool token(const char*& cursor, char* output, size_t size) {
  cursor = skipSpace(cursor);
  const char* begin = cursor;
  while (*cursor && *cursor != ' ' && *cursor != '\t') ++cursor;
  const size_t count = size_t(cursor - begin);
  if (!count || count >= size) return false;
  memcpy(output, begin, count); output[count] = 0;
  cursor = skipSpace(cursor);
  return true;
}
bool unsignedValue(const char* text, uint32_t& value) {
  if (!*text) return false;
  value = 0;
  for (; *text; ++text) {
    if (*text < '0' || *text > '9' || value > (UINT32_MAX - unsigned(*text - '0')) / 10)
      return false;
    value = value * 10 + unsigned(*text - '0');
  }
  return true;
}
bool powerValue(const char* text, int8_t& value) {
  bool negative = *text == '-';
  if (negative) ++text;
  uint32_t parsed;
  if (!unsignedValue(text, parsed) || parsed > (negative ? 128U : 127U)) return false;
  value = int8_t(negative ? -int(parsed) : int(parsed));
  return true;
}
bool floatValue(const char* text, float& value) {
  if (!*text || *text == ' ' || *text == '\t') return false;
  errno = 0;
  char* end = nullptr;
  value = strtof(text, &end);
  return end && !*end && errno != ERANGE && isfinite(value);
}
bool meshcoreTuple(char* text, BeaconSlot& slot) {
  char* fields[6];
  unsigned count = 0;
  do {
    if (count == 6 || !*text) return false;
    fields[count++] = text;
    char* comma = strchr(text, ',');
    if (!comma) break;
    *comma = 0; text = comma + 1;
  } while (true);
  uint32_t sf, cr, preamble = 32;
  if ((count != 5 && count != 6) || !floatValue(fields[0], slot.radio.freq)
      || !floatValue(fields[1], slot.radio.bw) || !unsignedValue(fields[2], sf)
      || !unsignedValue(fields[3], cr) || sf > 255 || cr > 255
      || !powerValue(fields[4], slot.power)
      || (count == 6 && !unsignedValue(fields[5], preamble))
      || preamble < 8 || preamble > RadioProfiles::MaxPreamble) return false;
  slot.radio.sf = uint8_t(sf); slot.radio.cr = uint8_t(cr);
  slot.radio.preamble = uint16_t(preamble);
  return true;
}
bool textValue(const char* text, char* output) {
  const size_t size = strlen(text);
  if (!size || size >= BeaconSlot::TextCapacity) return false;
  for (size_t i = 0; i < size; ++i) {
    const unsigned char c = static_cast<unsigned char>(text[i]);
    if (c < 32 || c == 127) return false;
  }
  memcpy(output, text, size + 1);
  return true;
}
File makeFile(FILESYSTEM* fs) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  return File(*fs);
#else
  (void)fs;
  return File();
#endif
}
bool openRead(FILESYSTEM* fs, const char* path, File& file) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  (void)fs;
  return file.open(path, FILE_O_READ);
#else
  file = fs->open(path, "r");
#endif
  return bool(file);
}
bool openWrite(FILESYSTEM* fs, const char* path, File& file) {
  bool present = false;
  if (!filePresence(fs, path, present) || (present && !fs->remove(path))) return false;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  return file.open(path, FILE_O_WRITE);
#else
  file = fs->open(path, "w");
#endif
  return bool(file);
}
} // namespace

BeaconManager::~BeaconManager() { delete[] slots_; }
bool BeaconManager::allocate() {
  if (!slots_) slots_ = new (std::nothrow) BeaconSlot[SlotCount];
  return slots_ != nullptr;
}
bool BeaconManager::anyEnabled() const {
  if (!slots_) return false;
  for (unsigned i = 0; i < SlotCount; ++i) if (slots_[i].enabled) return true;
  return false;
}

BeaconManager::ReadResult BeaconManager::readConfig(
    const char* path, BeaconSlot* output, uint8_t& hours, bool& initialized) {
  bool present = false;
  if (!fs_ || !filePresence(fs_, path, present)) return ReadResult::Unreadable;
  if (!present) return ReadResult::Missing;
  File file = makeFile(fs_);
  if (!openRead(fs_, path, file)) return ReadResult::Unreadable;
  uint8_t header[HeaderSize];
  if (file.size() < HeaderSize || file.read(header, HeaderSize) != int(HeaderSize)) {
    file.close(); return ReadResult::Unreadable;
  }
  if (memcmp(header, "BCN!", 4) != 0) { file.close(); return ReadResult::Invalid; }
  if (header[4] != 1) { file.close(); return ReadResult::Future; }
  if (file.size() != ConfigSize || header[5] != SlotCount || header[6] > 1
      || !validInterval(header[7])) { file.close(); return ReadResult::Invalid; }
  uint32_t crc = crcUpdate(0xffffffffU, header, HeaderSize);
  bool valid = true;
  for (unsigned i = 0; i < SlotCount; ++i) {
    uint8_t bytes[RecordSize];
    if (file.read(bytes, sizeof(bytes)) != int(sizeof(bytes))) {
      file.close(); return ReadResult::Unreadable;
    }
    crc = crcUpdate(crc, bytes, sizeof(bytes));
    BeaconSlot slot;
    if (!decodeSlot(bytes, slot) || (slot.enabled && (!mesh_ || !mesh_->validateTextBeacon(slot)))
        || (slot.enabled && slot.radio.protocol == RadioProfileProtocol::Meshtext && !header[6]))
      valid = false;
    if (output) output[i] = slot;
  }
  uint8_t trailer[4];
  const bool read = file.read(trailer, 4) == 4;
  file.close();
  if (!read) return ReadResult::Unreadable;
  if (!valid || get32(trailer) != crc) return ReadResult::Invalid;
  hours = header[7]; initialized = header[6] != 0;
  return ReadResult::Valid;
}

BeaconManager::ReadResult BeaconManager::readIds(const char* path, uint32_t& last) {
  bool present = false;
  if (!fs_ || !filePresence(fs_, path, present)) return ReadResult::Unreadable;
  if (!present) return ReadResult::Missing;
  File file = makeFile(fs_);
  if (!openRead(fs_, path, file)) return ReadResult::Unreadable;
  uint8_t bytes[IdSize];
  if (file.size() != IdSize) { file.close(); return ReadResult::Invalid; }
  const bool read = file.read(bytes, sizeof(bytes)) == int(sizeof(bytes));
  file.close();
  if (!read) return ReadResult::Unreadable;
  if (memcmp(bytes, "BCI!", 4)) return ReadResult::Invalid;
  if (bytes[4] != 1) return ReadResult::Future;
  if (bytes[5] || bytes[6] || bytes[7]
      || get32(bytes + 12) != crcUpdate(0xffffffffU, bytes, 12)) return ReadResult::Invalid;
  last = get32(bytes + 8);
  return ReadResult::Valid;
}

bool BeaconManager::commit(const char* path, const char* backup,
                           const char* temporary, bool& hold) {
  bool active_present = false, backup_present = false;
  if (!filePresence(fs_, path, active_present) || !filePresence(fs_, backup, backup_present)) return false;
  if (active_present) {
    if (backup_present && !fs_->remove(backup)) return false;
    if (!fs_->rename(path, backup)) return false;
    backup_present = true;
  }
  if (!fs_->rename(temporary, path)) {
    if (backup_present && !fs_->rename(backup, path)) hold = true;
    return false;
  }
  // A leftover backup after a successful commit is harmless. Never choose it
  // over a valid active image at boot.
  if (backup_present) fs_->remove(backup);
  return true;
}

bool BeaconManager::saveConfig(unsigned changed_slot, const BeaconSlot* changed,
                               uint8_t hours, bool initialized, bool clear_all) {
  if (!fs_ || hold_ || !validInterval(hours)) return false;
  File file = makeFile(fs_);
  if (!openWrite(fs_, ConfigTemp, file)) return false;
  uint8_t header[HeaderSize] = {'B', 'C', 'N', '!', 1, SlotCount,
                                uint8_t(initialized), hours};
  uint32_t crc = crcUpdate(0xffffffffU, header, sizeof(header));
  bool ok = file.write(header, sizeof(header)) == sizeof(header);
  for (unsigned i = 0; i < SlotCount && ok; ++i) {
    BeaconSlot empty;
    const BeaconSlot& slot = clear_all ? empty
        : (changed && changed_slot == i ? *changed : (slots_ ? slots_[i] : empty));
    uint8_t bytes[RecordSize]; encodeSlot(slot, bytes);
    crc = crcUpdate(crc, bytes, sizeof(bytes));
    ok = file.write(bytes, sizeof(bytes)) == sizeof(bytes);
  }
  uint8_t trailer[4]; put32(trailer, crc);
  if (ok) ok = file.write(trailer, sizeof(trailer)) == sizeof(trailer);
  file.flush(); file.close();
  uint8_t verified_hours = 0; bool verified_initialized = false;
  if (!ok || readConfig(ConfigTemp, nullptr, verified_hours, verified_initialized) != ReadResult::Valid
      || verified_hours != hours || verified_initialized != initialized) return false;
  return commit(ConfigPath, ConfigBackup, ConfigTemp, hold_);
}

bool BeaconManager::saveIds(uint32_t last) {
  if (!fs_ || ids_hold_) return false;
  File file = makeFile(fs_);
  if (!openWrite(fs_, IdTemp, file)) { ids_hold_ = true; return false; }
  uint8_t bytes[IdSize] = {'B', 'C', 'I', '!', 1, 0, 0, 0};
  put32(bytes + 8, last); put32(bytes + 12, crcUpdate(0xffffffffU, bytes, 12));
  bool ok = file.write(bytes, sizeof(bytes)) == sizeof(bytes);
  file.flush(); file.close();
  uint32_t verified = 0;
  ok = ok && readIds(IdTemp, verified) == ReadResult::Valid && verified == last;
  if (ok) ok = commit(IdPath, IdBackup, IdTemp, ids_hold_);
  if (!ok) { ids_hold_ = true; return false; }
  last_id_ = last; ids_known_ = true;
  return true;
}

bool BeaconManager::initializeIds() {
  if (ids_hold_) return false;
  if (ids_known_) return last_id_ != UINT32_MAX;
  if (ids_initialized_) { ids_hold_ = true; return false; }
  if (!mesh_ || !mesh_->getRNG()) return false;
  uint32_t seed = 0;
  mesh_->getRNG()->random(reinterpret_cast<uint8_t*>(&seed), sizeof(seed));
  // A deliberate filesystem wipe can preserve the node identity. Start at
  // a random point rather than deterministically reusing its old CTR nonce.
  // Ordinary reboots always continue the committed high-water mark.
  seed = (seed & 0x7fffffffU) | 1U;
  return saveIds(seed);
}
bool BeaconManager::nextId(uint32_t& id) {
  if (!ids_initialized_ || !initializeIds() || last_id_ == UINT32_MAX) return false;
  const uint32_t next = last_id_ + 1;
  if (!saveIds(next)) return false;
  id = next;
  return true;
}

void BeaconManager::begin(FILESYSTEM* fs, Mesh* mesh, RTCClock* rtc, const char* name) {
  (void)rtc; // A wall-clock correction must never compress beacon spacing.
  fs_ = fs; mesh_ = mesh; name_ = name;
  delete[] slots_; slots_ = nullptr;
  elapsed_ = anchor_ = handled_hour_ = last_send_ = 0;
  memset(slot_last_send_, 0, sizeof(slot_last_send_));
  memset(slot_not_before_, 0, sizeof(slot_not_before_));
  last_now_ = millis(); interval_hours_ = 24; last_id_ = 0;
  hold_ = ids_hold_ = ids_known_ = ids_initialized_ = false;
  if (!fs_ || !mesh_) { hold_ = true; return; }
  uint8_t hours = 24; bool initialized = false;
  ReadResult result = readConfig(ConfigPath, nullptr, hours, initialized);
  if (result == ReadResult::Future || result == ReadResult::Unreadable) hold_ = true;
  else if (result != ReadResult::Valid) {
    const ReadResult backup = readConfig(ConfigBackup, nullptr, hours, initialized);
    if (backup == ReadResult::Valid) {
      if ((fs_->exists(ConfigPath) && !fs_->remove(ConfigPath))
          || !fs_->rename(ConfigBackup, ConfigPath)) hold_ = true;
      result = ReadResult::Valid;
    } else if (result != ReadResult::Missing || backup != ReadResult::Missing) hold_ = true;
  }
  if (!hold_ && result == ReadResult::Valid) {
    if (!allocate() || readConfig(ConfigPath, slots_, hours, initialized) != ReadResult::Valid) {
      hold_ = true;
    } else { interval_hours_ = hours; ids_initialized_ = initialized; }
  }
  if (hold_) { delete[] slots_; slots_ = nullptr; }

  uint32_t last = 0;
  result = readIds(IdPath, last);
  if (result == ReadResult::Future || result == ReadResult::Unreadable) ids_hold_ = true;
  else if (result != ReadResult::Valid) {
    const ReadResult backup = readIds(IdBackup, last);
    if (backup == ReadResult::Valid) {
      // Missing active metadata does not prove an interrupted rename. A
      // stale backup can survive a cleanup failure after a newer ID was
      // transmitted. Never rewind to it, even before the first MT setting.
      ids_hold_ = true;
    } else if (result != ReadResult::Missing || backup != ReadResult::Missing) ids_hold_ = true;
  }
  if (!ids_hold_ && result == ReadResult::Valid) { last_id_ = last; ids_known_ = true; }
  if (ids_initialized_ && !ids_known_) ids_hold_ = true;
  loop(last_now_);
}

void BeaconManager::suspend() {
  hold_ = true;
  if (mesh_) {
    mesh_->setBeaconWakeDelay(millis(), 0, false);
    mesh_->cancelBeaconRadioFrame();
  }
}

void BeaconManager::loop(uint32_t now) {
  elapsed_ += uint32_t(now - last_now_); last_now_ = now;
  if (!mesh_) return;
  if (hold_ || !anyEnabled()) { mesh_->setBeaconWakeDelay(now, 0, false); return; }
  const uint64_t spacing = uint64_t(interval_hours_ / SlotCount) * HourMillis;
  const uint64_t period = uint64_t(interval_hours_) * HourMillis;
  const uint64_t offset = elapsed_ - anchor_;
  const uint64_t hour = offset / spacing;
  uint64_t delay = spacing - offset % spacing;
  if (hour > 0 && handled_hour_ != hour) {
    const unsigned index = unsigned((hour - 1) % SlotCount);
    BeaconSlot& slot = slots_[index];
    if (!slot.enabled) handled_hour_ = hour;
    else {
      uint64_t guard = last_send_ && elapsed_ - last_send_ < HourMillis
          ? HourMillis - (elapsed_ - last_send_) : 0;
      if (slot_not_before_[index] > elapsed_ && slot_not_before_[index] - elapsed_ > guard)
        guard = slot_not_before_[index] - elapsed_;
      if (slot_last_send_[index] && elapsed_ - slot_last_send_[index] < period) {
        const uint64_t slot_guard = period - (elapsed_ - slot_last_send_[index]);
        if (slot_guard > guard) guard = slot_guard;
      }
      if (!guard) {
        // One admission attempt per slot window; never retry a failed TX.
        handled_hour_ = hour;
        uint32_t id = 0;
        if ((slot.radio.protocol != RadioProfileProtocol::Meshtext || nextId(id))
            && mesh_->queueTextBeacon(slot, name_ ? name_ : "", mesh_->beaconNodeId(), id)) {
          last_send_ = elapsed_; slot_last_send_[index] = elapsed_;
        }
      } else if (guard < delay) delay = guard;
      else handled_hour_ = hour; // the old window expires before it is safe
    }
  }
  // Empty slots do not need hourly wakeups. Find the next configured window.
  if (handled_hour_ == hour || !hour) {
    for (unsigned next = 1; next <= SlotCount; ++next) {
      const uint64_t target = hour + next;
      if (slots_[unsigned((target - 1) % SlotCount)].enabled) {
        delay = target * spacing - offset; break;
      }
    }
  }
  mesh_->setBeaconWakeDelay(now, uint32_t(delay), true);
}

void BeaconManager::formatSlot(unsigned index, char* reply, size_t capacity) const {
  if (!slots_ || !slots_[index].enabled) {
    snprintf(reply, capacity, "> beacon %u off", index + 1); return;
  }
  const BeaconSlot& slot = slots_[index];
  const char* protocol = slot.radio.protocol == RadioProfileProtocol::Meshtext ? "meshtext" : "meshcore";
  snprintf(reply, capacity, "> %u %s %.4f,%g,%u,%u,%d,%u %s", index + 1, protocol,
      double(slot.radio.freq), double(slot.radio.bw), slot.radio.sf, slot.radio.cr,
      int(slot.power), slot.radio.preamble, slot.text);
}

bool BeaconManager::isCommand(const char* command) {
  if (!command) return false;
  const char* cursor = command;
  char verb[8], setting[24];
  return token(cursor, verb, sizeof(verb)) && token(cursor, setting, sizeof(setting))
      && (!strcmp(verb, "get") || !strcmp(verb, "set") || !strcmp(verb, "delete") || !strcmp(verb, "del"))
      && (!strcmp(setting, "beacon") || !strcmp(setting, "beacon.interval"));
}

bool BeaconManager::handle(const char* command, char* reply, size_t capacity) {
  if (!reply || !capacity || !isCommand(command)) return false;
  if (hold_) { snprintf(reply, capacity, "Err - beacon settings unavailable; storage held"); return true; }
  const bool interval_get = !strcmp(command, "get beacon.interval");
  const bool interval_set = !strncmp(command, "set beacon.interval ", 20);
  if (interval_get || interval_set) {
    if (interval_get) snprintf(reply, capacity, "> %u hours", interval_hours_);
    else {
      uint32_t hours;
      if (!unsignedValue(command + 20, hours) || !validInterval(hours))
        snprintf(reply, capacity, "Err - beacon interval must be 12, 24, 36, or 48 hours");
      else if (!saveConfig(SlotCount, nullptr, uint8_t(hours), ids_initialized_))
        snprintf(reply, capacity, "Err - beacon settings not saved");
      else {
        if (mesh_) mesh_->cancelBeaconRadioFrame();
        const uint32_t now = millis();
        elapsed_ += uint32_t(now - last_now_); last_now_ = now;
        interval_hours_ = uint8_t(hours); anchor_ = elapsed_; handled_hour_ = 0;
        snprintf(reply, capacity, "OK - beacon interval %lu hours", (unsigned long)hours);
        loop(millis());
      }
    }
    return true;
  }
  const bool get = !strcmp(command, "get beacon") || !strncmp(command, "get beacon ", 11);
  const bool set = !strncmp(command, "set beacon ", 11);
  const bool del = !strncmp(command, "delete beacon ", 14) || !strncmp(command, "del beacon ", 11);
  if (!get && !set && !del) {
    snprintf(reply, capacity, "Err - use get/set beacon or beacon.interval, delete beacon"); return true;
  }
  const char* cursor = command + (get && !strcmp(command, "get beacon") ? 10
      : !strncmp(command, "delete ", 7) ? 14 : 11);
  cursor = skipSpace(cursor);
  if (get && (!*cursor || !strcmp(cursor, "all"))) {
    size_t used = size_t(snprintf(reply, capacity, "> beacons %uh:", interval_hours_));
    for (unsigned i = 0; i < SlotCount && used < capacity; ++i) {
      if (slots_ && slots_[i].enabled) {
        const int count = snprintf(reply + used, capacity - used, " %u", i + 1);
        if (count > 0) used += size_t(count);
      }
    }
    return true;
  }
  if (del && !strcmp(cursor, "all")) {
    if (!saveConfig(SlotCount, nullptr, interval_hours_, ids_initialized_, true))
      snprintf(reply, capacity, "Err - beacon settings not saved");
    else {
      if (mesh_) mesh_->cancelBeaconRadioFrame();
      if (slots_) for (unsigned i = 0; i < SlotCount; ++i) slots_[i] = {};
      snprintf(reply, capacity, "OK - all beacons deleted"); loop(millis());
    }
    return true;
  }
  char number[16]; uint32_t index;
  if (!token(cursor, number, sizeof(number)) || !unsignedValue(number, index)
      || !index || index > SlotCount) {
    snprintf(reply, capacity, "Err - beacon slot must be 1..12"); return true;
  }
  --index;
  if (get || del) {
    if (*cursor) snprintf(reply, capacity, "Err - unexpected beacon arguments");
    else if (get) formatSlot(index, reply, capacity);
    else {
      BeaconSlot empty;
      if (!saveConfig(index, &empty, interval_hours_, ids_initialized_))
        snprintf(reply, capacity, "Err - beacon settings not saved");
      else {
        if (mesh_) mesh_->cancelBeaconRadioFrame();
        if (slots_) slots_[index] = {};
        snprintf(reply, capacity, "OK - beacon %lu deleted", (unsigned long)index + 1);
        loop(millis());
      }
    }
    return true;
  }
  BeaconSlot candidate;
  char protocol[16], argument[96];
  bool ok = token(cursor, protocol, sizeof(protocol));
  if (ok && !strcmp(protocol, "meshcore")) {
    ok = token(cursor, argument, sizeof(argument)) && meshcoreTuple(argument, candidate);
  } else if (ok && !strcmp(protocol, "meshtext")) {
#if MESH_ENABLE_MESHTEXT_WIRE
    char region_text[12], preset_text[24], power_text[16];
    mt::Region region = mt::Region::Unset; mt::Preset preset = mt::Preset::LongFast;
    ok = token(cursor, region_text, sizeof(region_text))
        && token(cursor, preset_text, sizeof(preset_text)) && token(cursor, power_text, sizeof(power_text));
    if (ok) {
      for (char* c = region_text; *c; ++c) *c = char(tolower(static_cast<unsigned char>(*c)));
      if (!strcmp(region_text, "us")) region = mt::Region::US;
      else if (!strcmp(region_text, "eu868")) region = mt::Region::EU868;
      else if (!strcmp(region_text, "eu433")) region = mt::Region::EU433;
      else if (!strcmp(region_text, "anz")) region = mt::Region::ANZ;
      ok = mt::parsePreset(preset_text, preset) && mt::modemConfig(region, preset, candidate.radio)
          && powerValue(power_text, candidate.power);
      candidate.preset = uint8_t(preset);
      candidate.radio.protocol = RadioProfileProtocol::Meshtext;
      candidate.radio.preamble = 16;
    }
#else
    snprintf(reply, capacity, "Err - Meshtext TX is unavailable on this modem"); return true;
#endif
  } else ok = false;
  candidate.enabled = true;
  if (!ok || !textValue(cursor, candidate.text) || !mesh_
      || !mesh_->validateTextBeacon(candidate)) {
    snprintf(reply, capacity, "Err - invalid beacon radio, power, preset, or text (1..80 bytes)"); return true;
  }
  bool initialized = ids_initialized_;
  if (candidate.radio.protocol == RadioProfileProtocol::Meshtext) {
    if (!initializeIds()) {
      snprintf(reply, capacity, "Err - Meshtext beacon ID storage unavailable"); return true;
    }
    initialized = true;
  }
  if (!allocate() || !saveConfig(index, &candidate, interval_hours_, initialized)) {
    snprintf(reply, capacity, "Err - beacon settings not saved"); return true;
  }
  if (mesh_) mesh_->cancelBeaconRadioFrame();
  const uint32_t now = millis();
  elapsed_ += uint32_t(now - last_now_); last_now_ = now;
  slots_[index] = candidate; ids_initialized_ = initialized;
  slot_not_before_[index] = elapsed_ + HourMillis;
  snprintf(reply, capacity, "OK - beacon %lu saved; %uh cycle", (unsigned long)index + 1, interval_hours_);
  loop(millis());
  return true;
}
} // namespace mesh

#endif
