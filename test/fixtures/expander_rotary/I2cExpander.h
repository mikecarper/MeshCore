#pragma once
// Instrument only the external I/O boundary; rotary settings, filesystem
// transactions, physical native claims and quadrature decoding stay real.
#include <stdint.h>
#include <helpers/RuntimePinClaims.h>
namespace mesh { namespace expander {
struct TestState {
  bool online = false, require_pullups = false;
  uint8_t count = 16;
  uint16_t identity = 0x0127;
  uint16_t levels = 0xffff, inputs = 0xffff, pullups = 0, input_allowed = 0xffff;
  uint16_t claims[4] = {};
  unsigned single_reads = 0, pair_reads = 0, configurations = 0;
  bool fail_read = false, fail_configuration = false;
};
inline TestState& testState() { static TestState value; return value; }
inline bool isPin(int pin) { return pin >= 64 && pin < 80; }
inline uint16_t pinMask(int pin) { return isPin(pin) ? uint16_t(1u << (pin - 64)) : 0; }
inline bool online() { return testState().online; }
inline bool configured() { return online(); }
inline uint16_t configIdentity() { return testState().identity; }
inline bool available(int pin) { return online() && isPin(pin) && pin - 64 < testState().count; }
inline bool inputAvailable(int pin, bool pullup) {
  return available(pin) && (testState().input_allowed & pinMask(pin)) &&
      (pullup || !testState().require_pullups);
}
inline uint16_t held(pins::Owner owner) {
  const unsigned i = unsigned(owner); return i < 4 ? testState().claims[i] : 0;
}
inline bool claimedByOther(int pin, pins::Owner owner) {
  for (unsigned i = 0; i < 4; ++i)
    if (i != unsigned(owner) && (testState().claims[i] & pinMask(pin))) return true;
  return false;
}
inline bool tryClaim(pins::Owner owner, uint16_t mask) {
  if (unsigned(owner) >= 4 || (mask && !online())) return false;
  for (unsigned i = 0; i < 4; ++i)
    if (i != unsigned(owner) && (testState().claims[i] & mask)) return false;
  testState().claims[unsigned(owner)] |= mask; return true;
}
inline void release(pins::Owner owner, uint16_t mask) {
  if (unsigned(owner) < 4) testState().claims[unsigned(owner)] &= ~mask;
}
inline bool configurePin(int pin, bool output, bool pullup = false) {
  if (!available(pin) || testState().fail_configuration || (!output && !inputAvailable(pin, pullup))) return false;
  ++testState().configurations;
  if (output) testState().inputs &= ~pinMask(pin); else testState().inputs |= pinMask(pin);
  if (pullup) testState().pullups |= pinMask(pin); else testState().pullups &= ~pinMask(pin);
  return true;
}
inline bool read(int pin, bool& high) {
  ++testState().single_reads;
  if (testState().fail_read) testState().online = false;
  if (!available(pin)) return false;
  high = (testState().levels & pinMask(pin)) != 0; return true;
}
inline bool readPair(int first, int second, uint8_t& state) {
  ++testState().pair_reads;
  if (testState().fail_read) testState().online = false;
  if (!available(first) || !available(second)) return false;
  const uint16_t snapshot = testState().levels;
  state = ((snapshot & pinMask(first)) ? 2 : 0) | ((snapshot & pinMask(second)) ? 1 : 0);
  return true;
}
}} // namespace mesh::expander
