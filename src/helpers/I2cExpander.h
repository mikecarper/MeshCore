#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include "CustomPeripheralConfig.h"
#include "RuntimePinClaims.h"

#if MESH_CUSTOM_PERIPHERALS && defined(ESP32_PLATFORM)
#define MESH_I2C_EXPANDER 1
namespace fs { class FS; }
#else
#define MESH_I2C_EXPANDER 0
#endif

namespace mesh {
class MainBoard;
namespace expander {

// Virtual endpoints are deliberately distinct from native MCU GPIO numbers.
// CLI users select x0..x15; raw numeric 64..79 are never accepted.
inline bool parsePinToken(const char*& text, int16_t& pin, bool allowOff = false) {
  if (!text) return false;
  const char* cursor = text;
  while (*cursor == ' ' || *cursor == '\t') ++cursor;
  const bool absent = allowOff && ((cursor[0] == 'o' && cursor[1] == 'f' && cursor[2] == 'f')
      || (cursor[0] == '-' && cursor[1] == '1'));
  if (absent) { cursor += *cursor == '-' ? 2 : 3; pin = -1; }
  else {
    bool expanded = *cursor == 'x';
    if (expanded) {
#if !MESH_I2C_EXPANDER
      return false;
#endif
      ++cursor;
    }
    if (*cursor < '0' || *cursor > '9') return false;
    unsigned value = 0;
    do {
      value = value * 10u + unsigned(*cursor++ - '0');
      if (value > (expanded ? 15u : 63u)) return false;
    } while (*cursor >= '0' && *cursor <= '9');
    pin = int16_t(value + (expanded ? 64u : 0u));
  }
  while (*cursor == ' ' || *cursor == '\t') ++cursor;
  if (*cursor && *cursor != ',') return false;
  text = cursor;
  return true;
}
inline void formatPin(int pin, char* text, size_t capacity) {
  if (!text || !capacity) return;
  if (pin < 0) snprintf(text, capacity, "off");
#if MESH_I2C_EXPANDER
  else if (pin >= 64 && pin < 80) snprintf(text, capacity, "x%u", unsigned(pin - 64));
#endif
  else if (pin < 64) snprintf(text, capacity, "%u", unsigned(pin));
  else snprintf(text, capacity, "invalid");
}

#if MESH_I2C_EXPANDER
constexpr bool isPin(int pin) { return pin >= 64 && pin < 80; }
constexpr uint16_t pinMask(int pin) { return isPin(pin) ? uint16_t(1u << (pin - 64)) : 0; }
#ifdef FILESYSTEM
void begin(MainBoard& board, FILESYSTEM* filesystem);
#else
void begin(MainBoard& board, fs::FS* filesystem);
#endif
void setAddressConflictCheck(bool (*reserved)(uint8_t));
bool ownsAddress(uint8_t address);
bool handleCommand(const char* command, char* reply, size_t capacity);
void loop();
void shutdown();
bool configured();
// Persistent consumer records bind to the exact model/address configuration.
uint16_t configIdentity();
bool online();
unsigned pinCount();
bool supportsPullup(bool enabled);
bool recommendedActiveLow();
bool available(int pin);
bool inputAvailable(int pin, bool pullup);
// Writes to an input stage its future output latch without driving it.
// Configure as output only after setting the intended inactive level.
bool configurePin(int pin, bool output, bool pullup = false);
bool write(int pin, bool high);
bool read(int pin, bool& high);
// Both endpoints must be virtual. One atomic device read samples both banks.
bool readPair(int first, int second, uint8_t& state);
bool claimedByOther(int pin, pins::Owner except = pins::Owner::None);
uint16_t held(pins::Owner owner);
bool tryClaim(pins::Owner owner, uint16_t additional);
void release(pins::Owner owner, uint16_t retired);
#else
constexpr bool isPin(int) { return false; }
constexpr uint16_t pinMask(int) { return 0; }
inline void begin(MainBoard&, void*) {}
inline void setAddressConflictCheck(bool (*)(uint8_t)) {}
inline bool ownsAddress(uint8_t) { return false; }
inline bool handleCommand(const char*, char*, size_t) { return false; }
inline void loop() {}
inline void shutdown() {}
inline bool configured() { return false; }
inline uint16_t configIdentity() { return 0; }
inline bool online() { return false; }
inline unsigned pinCount() { return 0; }
inline bool supportsPullup(bool) { return false; }
inline bool recommendedActiveLow() { return false; }
inline bool available(int) { return false; }
inline bool inputAvailable(int, bool) { return false; }
inline bool configurePin(int, bool, bool = false) { return false; }
inline bool write(int, bool) { return false; }
inline bool read(int, bool&) { return false; }
inline bool readPair(int, int, uint8_t&) { return false; }
inline bool claimedByOther(int, pins::Owner = pins::Owner::None) { return true; }
inline uint16_t held(pins::Owner) { return 0; }
inline bool tryClaim(pins::Owner, uint16_t additional) { return additional == 0; }
inline void release(pins::Owner, uint16_t) {}
#endif

}} // namespace mesh::expander
