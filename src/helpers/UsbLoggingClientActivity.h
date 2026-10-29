#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// meshcoretomqtt polls these commands every five minutes. A short-lived lease
// proves application input, unlike a cable, DTR, or a kernel USB ACK. It does
// not identify a particular application or prove MQTT/disk health.
constexpr uint32_t USB_LOGGING_CLIENT_LEASE_MS = 15UL * 60UL * 1000UL;

// Firmware detection is passive even before the role strips an optional
// two-byte Companion request prefix or normalizes an uppercase GET verb.
// Unlike stats polling, this query must not establish a logging-reader lease.
inline bool isUsbModeDetectionCommand(const char* command) {
  if (!command) return false;
  while (*command == ' ' || *command == '\t') ++command;
  if (command[0] && command[1] && command[2] == '|' && command[3]) {
    command += 3;
    while (*command == ' ' || *command == '\t') ++command;
  }
  return (command[0] == 'g' || command[0] == 'G')
      && (command[1] == 'e' || command[1] == 'E')
      && (command[2] == 't' || command[2] == 'T')
      && strcmp(command + 3, " usb.mode") == 0;
}

inline bool isUsbLoggingStatsCommand(const char* command) {
  if (!command) return false;
  while (*command == ' ' || *command == '\t') ++command;
  const char* const keys[] = {"stats-core", "stats-radio", "stats-packets"};
  for (const char* key : keys) {
    const size_t length = strlen(key);
    if (strncmp(command, key, length)) continue;
    const char* end = command + length;
    while (*end == ' ' || *end == '\t') ++end;
    if (!*end) return true;
  }
  return false;
}

// Bounded receive-only parser for the dedicated logging CDC. It recognizes
// stats polls but does not expose a second configuration or binary CLI. A
// truncated/overlong record must never count as a valid poll.
class UsbLoggingStatsLineParser {
 public:
  void reset() { length = 0; discard = false; }
  bool consume(char c) {
    if (c == '\r' || c == '\n') {
      line[length] = 0;
      const bool matched = !discard && isUsbLoggingStatsCommand(line);
      reset();
      return matched;
    }
    if (discard) return false;
    if ((static_cast<uint8_t>(c) < 32 && c != '\t')
        || static_cast<uint8_t>(c) >= 127) {
      discard = true;
      length = 0;
      return false;
    }
    if (length == sizeof(line) - 1) {
      discard = true;
      length = 0;
    } else {
      line[length++] = c;
    }
    return false;
  }

 private:
  char line[32] = {};
  size_t length = 0;
  bool discard = false;
};

// Call only after receiving a complete command on the actual USB logging
// endpoint. BLE, TCP, LoRa, and the primary CDC of a dual-CDC device cannot
// establish a logging-reader lease. These calls never persist preferences.
void noteUsbLoggingStatsCommand(const char* command);
bool isUsbLoggingClientActive();
void clearUsbLoggingClientActivity();

}  // namespace mesh
