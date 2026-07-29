#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace WiFiOtaSeederStatus {

inline bool append(char* reply, size_t capacity, bool listening,
                   bool client_attached, uint16_t port) {
  if (!reply || capacity == 0 || !listening) return false;
  const size_t used = strnlen(reply, capacity);
  if (used >= capacity) return false;
  const int written = snprintf(
      reply + used, capacity - used, ", OTA TCP %u: %s",
      static_cast<unsigned>(port),
      client_attached ? "client connected" : "listening");
  return written >= 0 && static_cast<size_t>(written) < capacity - used;
}

}  // namespace WiFiOtaSeederStatus
