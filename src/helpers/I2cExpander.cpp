#include "I2cExpander.h"

#if MESH_I2C_EXPANDER
#include <Arduino.h>
#include <Wire.h>
#include <target.h>
#include <helpers/IdentityStore.h>
#include <helpers/CheckedSettingsFile.h>
#include <helpers/ContactFileTransaction.h>
#include <helpers/FilePresence.h>
#include <atomic>
#include <string.h>

namespace mesh { namespace expander {
namespace {
// Codes are persisted in the config and consumer identity records. Append new
// kinds without renumbering these existing values.
enum class Kind : uint8_t { Off = 0, Mcp23017 = 1, Mcp23008 = 2, Pcf8575 = 3, Pcf8574 = 4, Pcf8574a = 5 };
struct Config { Kind kind = Kind::Off; uint8_t address = 0; };
constexpr char Path[] = "/expander_v1";
constexpr char Backup[] = "/expander_v1.bak";
constexpr size_t RecordSize = 12;
Config selected;
FILESYSTEM* settings_fs = nullptr;
bool ready = false, writable = false, rejected_record = false, address_reserved = false;
uint16_t outputs = 0, pullups = 0, latch = 0;
uint32_t last_probe = 0;
bool (*conflict_check)(uint8_t) = nullptr;
std::atomic<uint16_t> owner_masks[4]{};
std::atomic_flag claims_busy = ATOMIC_FLAG_INIT;
std::atomic_flag io_busy = ATOMIC_FLAG_INIT;

bool pcf(Kind kind) { return kind == Kind::Pcf8575 || kind == Kind::Pcf8574 || kind == Kind::Pcf8574a; }
unsigned count(Kind kind) { return kind == Kind::Off ? 0u : kind == Kind::Mcp23017 || kind == Kind::Pcf8575 ? 16u : 8u; }
uint16_t deviceMask(Kind kind) { return count(kind) == 16 ? 0xffffu : count(kind) ? 0xffu : 0u; }
const char* name(Kind kind) {
  switch (kind) {
    case Kind::Mcp23017: return "mcp23017";
    case Kind::Mcp23008: return "mcp23008";
    case Kind::Pcf8575: return "pcf8575";
    case Kind::Pcf8574: return "pcf8574";
    case Kind::Pcf8574a: return "pcf8574a";
    default: return "off";
  }
}
bool valid(const Config& value) {
  if (value.kind == Kind::Off) return value.address == 0;
  if (unsigned(value.kind) > unsigned(Kind::Pcf8574a)) return false;
  return value.kind == Kind::Pcf8574a ? value.address >= 0x38 && value.address <= 0x3f
      : value.address >= 0x20 && value.address <= 0x27;
}
uint32_t crc(const uint8_t* bytes, size_t length) {
  uint32_t result = 0xffffffffu;
  while (length--) {
    result ^= *bytes++;
    for (unsigned bit = 0; bit < 8; ++bit)
      result = (result >> 1) ^ (0xedb88320u & (0u - (result & 1u)));
  }
  return ~result;
}
void encode(const Config& value, uint8_t (&bytes)[RecordSize]) {
  memset(bytes, 0, sizeof(bytes));
  bytes[0] = 'I'; bytes[1] = 'O'; bytes[2] = 'X'; bytes[3] = 1;
  bytes[4] = uint8_t(value.kind); bytes[5] = value.address;
  const uint32_t checksum = crc(bytes, 8);
  for (unsigned i = 0; i < 4; ++i) bytes[8 + i] = uint8_t(checksum >> (i * 8));
}
bool load(const char* path, Config& result) {
  uint8_t bytes[RecordSize], expected[RecordSize];
  if (!storage::readFixedSettingsFile(settings_fs, path, bytes, sizeof(bytes))) return false;
  Config value; value.kind = Kind(bytes[4]); value.address = bytes[5];
  if (!valid(value)) return false;
  encode(value, expected);
  if (memcmp(bytes, expected, sizeof(bytes))) return false;
  result = value; return true;
}
bool save(const Config& value) {
  if (!settings_fs || !writable) return false;
  uint8_t bytes[RecordSize], verified[RecordSize]; encode(value, bytes);
  ContactFileTransaction file(settings_fs, Path, filePresence<FILESYSTEM>);
  using Outcome = decltype(file.outcome());
  if (!file || file.write(bytes, sizeof(bytes)) != sizeof(bytes) || !file.commit()) {
    if (file.outcome() == Outcome::Indeterminate) writable = false;
    return false;
  }
  if (file.outcome() != Outcome::Committed ||
      !storage::readFixedSettingsFile(settings_fs, Path, verified, sizeof(verified)) ||
      memcmp(bytes, verified, sizeof(bytes))) { writable = false; return false; }
  return true;
}
struct IoLock {
  bool locked;
  IoLock() : locked(!io_busy.test_and_set(std::memory_order_acquire)) {}
  ~IoLock() { if (locked) io_busy.clear(std::memory_order_release); }
};
struct ClaimsLock {
  bool locked;
  ClaimsLock() : locked(!claims_busy.test_and_set(std::memory_order_acquire)) {}
  ~ClaimsLock() { if (locked) claims_busy.clear(std::memory_order_release); }
};
// Every shared-bus adjustment is restored before returning to other sensors.
// PCF8574/74A permit only 100 kHz; other supported devices permit 400 kHz.
struct WireSettings {
  uint32_t clock = Wire.getClock();
  uint16_t timeout = Wire.getTimeOut();
  bool clock_changed = false, timeout_changed = false, ok = true;
  explicit WireSettings(Kind kind) {
    const uint32_t maximum = kind == Kind::Pcf8574 || kind == Kind::Pcf8574a ? 100000u : 400000u;
    if (clock > maximum) { ok = Wire.setClock(maximum); clock_changed = ok; }
    if (timeout > 20u) { Wire.setTimeOut(20u); timeout_changed = true; }
  }
  ~WireSettings() {
    if (clock_changed) Wire.setClock(clock);
    if (timeout_changed) Wire.setTimeOut(timeout);
  }
};
bool failed() { ready = false; return false; }
bool writeBytes(const Config& config, const uint8_t* data, size_t length) {
  WireSettings limits(config.kind); if (!limits.ok) return failed();
  Wire.beginTransmission(config.address);
  const bool complete = Wire.write(data, length) == length;
  const bool acknowledged = Wire.endTransmission() == 0;
  return complete && acknowledged ? true : failed();
}
bool readBytes(const Config& config, uint8_t* data, unsigned length, int reg = -1) {
  WireSettings limits(config.kind); if (!limits.ok) return failed();
  if (reg >= 0) {
    Wire.beginTransmission(config.address);
    const bool complete = Wire.write(uint8_t(reg)) == 1;
    const bool acknowledged = Wire.endTransmission(false) == 0;
    if (!complete || !acknowledged) return failed();
  }
  const size_t received = Wire.requestFrom(config.address, uint8_t(length));
  if (received != length) { while (Wire.available()) Wire.read(); return failed(); }
  for (unsigned i = 0; i < length; ++i) {
    if (!Wire.available()) return failed();
    const int value = Wire.read(); if (value < 0) return failed();
    data[i] = uint8_t(value);
  }
  return true;
}
bool reg8(const Config& config, uint8_t reg, uint8_t value) {
  const uint8_t bytes[] = {reg, value}; return writeBytes(config, bytes, sizeof(bytes));
}
bool reg16(const Config& config, uint8_t reg, uint16_t value) {
  const uint8_t bytes[] = {reg, uint8_t(value), uint8_t(value >> 8)};
  return writeBytes(config, bytes, config.kind == Kind::Mcp23017 ? 3u : 2u);
}
bool snapshot(uint16_t& value) {
  uint8_t bytes[2] = {};
  const int reg = pcf(selected.kind) ? -1 : selected.kind == Kind::Mcp23017 ? 0x12 : 0x09;
  if (!readBytes(selected, bytes, count(selected.kind) == 16 ? 2 : 1, reg)) return false;
  value = uint16_t(bytes[0]) | uint16_t(bytes[1]) << 8;
  last_probe = millis(); return true;
}
bool writePcf(uint16_t output_mask, uint16_t output_latch) {
  // Quasi-bidirectional inputs must stay released HIGH. Never derive the
  // output latch from a read containing externally grounded input contacts.
  const uint16_t physical = uint16_t(output_latch | ~output_mask) & deviceMask(selected.kind);
  const uint8_t bytes[] = {uint8_t(physical), uint8_t(physical >> 8)};
  return writeBytes(selected, bytes, count(selected.kind) == 16 ? 2u : 1u);
}
bool ensureBus() {
#if defined(PIN_BOARD_SDA) && defined(PIN_BOARD_SCL)
  return PIN_BOARD_SDA >= 0 && PIN_BOARD_SDA < 64 && PIN_BOARD_SCL >= 0 && PIN_BOARD_SCL < 64 &&
      PIN_BOARD_SDA != PIN_BOARD_SCL && Wire.begin(PIN_BOARD_SDA, PIN_BOARD_SCL);
#else
  return false;
#endif
}
bool probe(const Config& value) {
  if (!ensureBus()) return false;
  uint8_t bytes[2] = {};
  if (pcf(value.kind)) return readBytes(value, bytes, count(value.kind) == 16 ? 2u : 1u);
  // These chips have no immutable ID. This is only a readable-register check,
  // not automatic chip identification; model and address are explicit.
  return readBytes(value, bytes, 1, value.kind == Kind::Mcp23017 ? 0x0a : 0x05);
}
bool initialize() {
  ready = false; outputs = pullups = 0; latch = pcf(selected.kind) ? deviceMask(selected.kind) : 0;
  if (!configured() || address_reserved || !probe(selected)) return false;
  if (pcf(selected.kind)) {
    if (!writePcf(0, latch)) return false;
  } else if (selected.kind == Kind::Mcp23017) {
    // In BANK=1, register 05 is IOCON: clear BANK. In BANK=0 the same
    // address is GPINTENB, so clearing it is also safe during explicit setup.
    if (!reg8(selected, 0x05, 0)) return false;
    // Disable bank/sequence/polarity/interrupt modes; all unassigned pins are
    // released as inputs. LED polarity is implemented by the caller.
    if (!reg8(selected, 0x0a, 0) || !reg16(selected, 0x00, 0xffffu) ||
        !reg16(selected, 0x02, 0) || !reg16(selected, 0x04, 0) ||
        !reg16(selected, 0x0c, 0) || !reg16(selected, 0x14, 0)) return false;
  } else {
    if (!reg8(selected, 0x05, 0) || !reg8(selected, 0x00, 0xff) ||
        !reg8(selected, 0x01, 0) || !reg8(selected, 0x02, 0) ||
        !reg8(selected, 0x06, 0) || !reg8(selected, 0x0a, 0)) return false;
  }
  uint16_t initial;
  if (!snapshot(initial)) return false;
  ready = true; return true;
}
bool anyClaims() {
  for (unsigned i = 0; i < 4; ++i) if (owner_masks[i].load(std::memory_order_acquire)) return true;
  return false;
}
bool parseConfig(const char* text, Config& result) {
  if (!strcmp(text, "off")) { result = Config(); return true; }
  const char* const aliases[] = {"pcf8574at", "pcf8574t"};
  for (const auto& alias : aliases) {
    const size_t length = strlen(alias);
    if (!strncmp(text, alias, length) && text[length] == ',') {
      char canonical[40];
      if (snprintf(canonical, sizeof(canonical), "%s%s", alias[7] == 'a' ? "pcf8574a" : "pcf8574", text + length) >= int(sizeof(canonical))) return false;
      return parseConfig(canonical, result);
    }
  }
  for (unsigned i = 1; i <= unsigned(Kind::Pcf8574a); ++i) {
    const Kind kind = Kind(i); const size_t length = strlen(name(kind));
    if (strncmp(text, name(kind), length) || text[length] != ',') continue;
    text += length + 1;
    unsigned base = 10;
    if (text[0] == '0' && (text[1] == 'x' || text[1] == 'X')) { base = 16; text += 2; }
    if (!*text) return false;
    unsigned address = 0;
    for (; *text; ++text) {
      unsigned digit = *text >= '0' && *text <= '9' ? unsigned(*text - '0')
          : *text >= 'a' && *text <= 'f' ? unsigned(*text - 'a') + 10u
          : *text >= 'A' && *text <= 'F' ? unsigned(*text - 'A') + 10u : 255u;
      if (digit >= base || address > 255u / base) return false;
      address = address * base + digit; if (address > 255u) return false;
    }
    result.kind = kind; result.address = uint8_t(address); return valid(result);
  }
  return false;
}
} // namespace

void setAddressConflictCheck(bool (*reserved)(uint8_t)) { conflict_check = reserved; }
bool configured() { return selected.kind != Kind::Off; }
uint16_t configIdentity() { return configured() ? uint16_t(unsigned(selected.kind) << 8) | selected.address : 0; }
bool online() { return configured() && ready; }
bool ownsAddress(uint8_t address) { return configured() && selected.address == address; }
unsigned pinCount() { return count(selected.kind); }
bool supportsPullup(bool enabled) { return configured() && (!pcf(selected.kind) || enabled); }
bool recommendedActiveLow() { return configured() && pcf(selected.kind); }
bool available(int pin) {
  // Microchip restricts GPA7/GPB7 to outputs due to an I2C silicon issue.
  // Excluding both avoids pretending these endpoints can be released to an
  // ordinary input when an LED is disabled or the firmware shuts down.
  return online() && isPin(pin) && unsigned(pin - 64) < pinCount() &&
      !(selected.kind == Kind::Mcp23017 && (pin == 71 || pin == 79));
}
bool inputAvailable(int pin, bool pullup) {
  return available(pin) && supportsPullup(pullup) &&
      !(selected.kind == Kind::Mcp23017 && (pin == 71 || pin == 79));
}
uint16_t held(pins::Owner owner) {
  const unsigned index = unsigned(owner);
  return index < 4 ? owner_masks[index].load(std::memory_order_acquire) : 0;
}
bool claimedByOther(int pin, pins::Owner except) {
  const uint16_t bit = pinMask(pin); if (!bit) return true;
  for (unsigned i = 0; i < 4; ++i) if (i != unsigned(except) && (mesh::expander::held(pins::Owner(i)) & bit)) return true;
  return false;
}
bool tryClaim(pins::Owner owner, uint16_t additional) {
  if (!additional) return true;
  const unsigned index = unsigned(owner);
  const uint16_t assignable = selected.kind == Kind::Mcp23017 ? uint16_t(0x7f7fu) : deviceMask(selected.kind);
  if (index >= 4 || !online() || (additional & ~assignable) ||
      claims_busy.test_and_set(std::memory_order_acquire)) return false;
  bool free = true;
  for (unsigned i = 0; i < 4; ++i) if (i != index && (mesh::expander::held(pins::Owner(i)) & additional)) free = false;
  if (free) owner_masks[index].fetch_or(additional, std::memory_order_release);
  claims_busy.clear(std::memory_order_release); return free;
}
void release(pins::Owner owner, uint16_t retired) {
  if (unsigned(owner) < 4 && retired) owner_masks[unsigned(owner)].fetch_and(uint16_t(~retired), std::memory_order_release);
}
bool configurePin(int pin, bool output, bool pullup) {
  IoLock lock;
  if (!lock.locked || !available(pin) || (!output && !inputAvailable(pin, pullup))) return false;
  const uint16_t bit = pinMask(pin);
  const uint16_t next_outputs = output ? uint16_t(outputs | bit) : uint16_t(outputs & ~bit);
  const uint16_t next_pullups = !output && pullup ? uint16_t(pullups | bit) : uint16_t(pullups & ~bit);
  if (next_outputs == outputs && next_pullups == pullups) return true;
  if (pcf(selected.kind)) {
    if (!writePcf(next_outputs, latch)) return false;
  } else {
    const uint8_t latch_reg = selected.kind == Kind::Mcp23017 ? 0x14 : 0x0a;
    const uint8_t pullup_reg = selected.kind == Kind::Mcp23017 ? 0x0c : 0x06;
    if (output && !reg16(selected, latch_reg, latch)) return false;
    if (next_pullups != pullups && !reg16(selected, pullup_reg, next_pullups)) return false;
    if (!reg16(selected, 0x00, uint16_t(~next_outputs))) return false;
  }
  outputs = next_outputs; pullups = next_pullups; return true;
}
bool write(int pin, bool high) {
  IoLock lock; if (!lock.locked || !available(pin)) return false;
  const uint16_t bit = pinMask(pin);
  const uint16_t next = high ? uint16_t(latch | bit) : uint16_t(latch & ~bit);
  if (next == latch) return true;
  // Stage the level before direction is changed, avoiding a transient active
  // pulse when an active-low LED is first assigned to a released PCF port.
  if (outputs & bit) {
    if (pcf(selected.kind)) { if (!writePcf(outputs, next)) return false; }
    else if (!reg16(selected, selected.kind == Kind::Mcp23017 ? 0x14 : 0x0a, next)) return false;
  }
  latch = next; return true;
}
bool read(int pin, bool& high) {
  IoLock lock; if (!lock.locked || !available(pin)) return false;
  uint16_t value; if (!snapshot(value)) return false;
  high = (value & pinMask(pin)) != 0; return true;
}
bool readPair(int first, int second, uint8_t& state) {
  IoLock lock; if (!lock.locked || !available(first) || !available(second)) return false;
  uint16_t value; if (!snapshot(value)) return false;
  state = ((value & pinMask(first)) ? 2u : 0u) | ((value & pinMask(second)) ? 1u : 0u); return true;
}
void loop() {
  if (!online() || uint32_t(millis() - last_probe) < 1000u) return;
  IoLock lock; if (!lock.locked) return;
  uint16_t ignored; snapshot(ignored);
}
void shutdown() {
  IoLock lock; if (!lock.locked) { ready = false; return; }
  if (online()) {
    if (pcf(selected.kind)) writePcf(0, deviceMask(selected.kind));
    else reg16(selected, 0x00, 0xffffu);
  }
  ready = false; outputs = pullups = 0;
  for (auto& mask : owner_masks) mask.store(0, std::memory_order_release);
}
void begin(MainBoard&, FILESYSTEM* filesystem) {
  shutdown(); selected = Config(); settings_fs = filesystem;
  writable = filesystem != nullptr; rejected_record = address_reserved = false;
  Config saved;
  bool loaded = load(Path, saved);
  if (!loaded && load(Backup, saved)) {
    loaded = true;
    if ((!filesystem->exists(Path) || filesystem->remove(Path)) && filesystem->rename(Backup, Path)) {
      Config verified;
      if (!load(Path, verified) || verified.kind != saved.kind || verified.address != saved.address) writable = false;
    } else writable = false;
  }
  if (!loaded) { rejected_record = filesystem && (filesystem->exists(Path) || filesystem->exists(Backup)); return; }
  selected = saved;
  address_reserved = configured() && conflict_check && conflict_check(selected.address);
  if (!address_reserved) initialize();
}
bool handleCommand(const char* command, char* reply, size_t capacity) {
  if (!command || !reply || !capacity) return false;
  if (!strcmp(command, "get expander")) {
    if (!configured()) snprintf(reply, capacity, "> off%s", rejected_record ? "; saved config rejected" : "");
    else snprintf(reply, capacity, "> %s,0x%02X %s pins=x0..x%u%s", name(selected.kind), selected.address,
        online() ? "online" : "offline", pinCount() - 1u, address_reserved ? "; address reserved" : "");
    return true;
  }
  static constexpr char Prefix[] = "set expander";
  if (strncmp(command, Prefix, sizeof(Prefix) - 1) ||
      (command[sizeof(Prefix) - 1] && command[sizeof(Prefix) - 1] != ' ' && command[sizeof(Prefix) - 1] != '\t')) return false;
  const char* value = command + sizeof(Prefix) - 1;
  while (*value == ' ' || *value == '\t') ++value;
  Config next;
  if (!parseConfig(value, next)) {
    snprintf(reply, capacity, "Error: set expander mcp23017|mcp23008|pcf8575|pcf8574|pcf8574a,0xADDR or off"); return true;
  }
  ClaimsLock claim_lock;
  if (!claim_lock.locked) { snprintf(reply, capacity, "Error: expander claims busy"); return true; }
  if (anyClaims()) { snprintf(reply, capacity, "Error: disable expander rotary and LED pins first"); return true; }
  if (next.kind != Kind::Off && conflict_check && conflict_check(next.address)) {
    snprintf(reply, capacity, "Error: I2C address is reserved by board hardware or sensors"); return true;
  }
  if (!writable) { snprintf(reply, capacity, "Error: expander settings unavailable or save unconfirmed"); return true; }
  IoLock lock;
  if (!lock.locked) { snprintf(reply, capacity, "Error: expander busy"); return true; }
  if (next.kind != Kind::Off && !probe(next)) { snprintf(reply, capacity, "Error: expander not responding on the board I2C bus"); return true; }
  if (!save(next)) { snprintf(reply, capacity, "Error: expander save unconfirmed; active settings unchanged; reboot may differ"); return true; }
  selected = next; address_reserved = rejected_record = false;
  if (!configured()) { ready = false; outputs = pullups = 0; snprintf(reply, capacity, "OK - expander off"); }
  else if (initialize()) snprintf(reply, capacity, "OK - %s,0x%02X online", name(selected.kind), selected.address);
  else snprintf(reply, capacity, "Error: expander saved but initialization failed; offline");
  return true;
}

}} // namespace mesh::expander
#endif
