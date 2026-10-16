#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace peripherals {

// RAK4631 only: append this checked policy after every previously published
// preference byte. Missing historical policy means GPS-only; an unreadable or
// damaged new policy must retain the shared supply instead of removing power.
static constexpr size_t FooterSize = 8;
static constexpr size_t CommonLegacySize = 877;
static constexpr size_t LegacyRs232TailSize = 10;

inline uint16_t checksum(const uint8_t* bytes, size_t length) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < length; ++i) {
    crc ^= static_cast<uint16_t>(bytes[i]) << 8;
    for (unsigned bit = 0; bit < 8; ++bit)
      crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                           : static_cast<uint16_t>(crc << 1);
  }
  return crc;
}

inline void encodeFooter(uint8_t out[FooterSize], uint8_t shared) {
  out[0] = 'P'; out[1] = 'M'; out[2] = 'O'; out[3] = 1;
  out[4] = shared == 0 ? 0 : 1;
  out[5] = 0;
  const uint16_t crc = checksum(out, 6);
  out[6] = static_cast<uint8_t>(crc >> 8);
  out[7] = static_cast<uint8_t>(crc);
}

inline bool decodeFooter(const uint8_t* in, size_t size, uint8_t& shared) {
  if (!in || size != FooterSize || in[0] != 'P' || in[1] != 'M'
      || in[2] != 'O' || in[3] != 1 || in[4] > 1 || in[5] != 0
      || checksum(in, 6) != static_cast<uint16_t>((in[6] << 8) | in[7]))
    return false;
  shared = in[4];
  return true;
}

template <typename File> uint8_t readCommonMode(File& file) {
  const size_t size = file.size();
  if (size <= CommonLegacySize) return 0;
  // A historical RS232-only image has no peripheral policy. This check is
  // independent of the current profile: UART -> no-UART upgrades must work.
  if (size == CommonLegacySize + LegacyRs232TailSize) {
    uint8_t header[4] = {};
    if (!file.seek(CommonLegacySize) || file.read(header, sizeof(header)) != sizeof(header))
      return 1;
    if (header[0] == 'R' && header[1] == 'S' && header[2] == 'P' && header[3] == 1)
      return 0;
  }
  uint8_t footer[FooterSize] = {};
  uint8_t shared = 1;
  if (size < FooterSize || !file.seek(size - FooterSize)
      || file.read(footer, sizeof(footer)) != sizeof(footer)
      || !decodeFooter(footer, sizeof(footer), shared)) return 1;
  return shared;
}

template <typename Save>
bool handleCommand(uint8_t& shared, const char* command, char* reply,
                   size_t capacity, bool supported, Save save) {
  if (!command || !reply || capacity == 0) return false;
  const bool query = strcmp(command, "get peripherals.mode") == 0;
  static const char prefix[] = "set peripherals.mode";
  const bool setter = strncmp(command, prefix, sizeof(prefix) - 1) == 0
      && (command[sizeof(prefix) - 1] == 0 || command[sizeof(prefix) - 1] == ' '
          || command[sizeof(prefix) - 1] == '\t');
  if (!query && !setter) return false;
  if (!supported) {
    snprintf(reply, capacity, "Error: peripheral mode unsupported");
    return true;
  }
  if (query) {
    snprintf(reply, capacity, "> %s (saved; applied at boot)", shared ? "shared" : "gps-only");
    return true;
  }
  const char* value = command + sizeof(prefix) - 1;
  while (*value == ' ' || *value == '\t') ++value;
  if (strcmp(value, "gps-only") != 0 && strcmp(value, "shared") != 0) {
    snprintf(reply, capacity, "Error: use set peripherals.mode gps-only|shared");
    return true;
  }
  const uint8_t previous = shared;
  shared = strcmp(value, "shared") == 0 ? 1 : 0;
  if (!save()) {
    shared = previous;
    snprintf(reply, capacity, "Error: save unconfirmed; reboot may differ");
  } else {
    snprintf(reply, capacity, "OK - %s saved; reboot to apply", value);
  }
  return true;
}

} // namespace peripherals
} // namespace mesh
