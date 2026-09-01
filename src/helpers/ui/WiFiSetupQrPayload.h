#pragma once

#include <stddef.h>
#include <string.h>

namespace mesh {
namespace ui {

inline bool appendWiFiQrText(char*& output, size_t& remaining,
                             const char* value, bool escape) {
  if (value == nullptr) return false;
  while (*value != 0) {
    const bool special = escape && strchr("\\;,\":", *value) != nullptr;
    const size_t required = special ? 2 : 1;
    if (remaining <= required) {
      *output = 0;
      return false;
    }
    if (special) {
      *output++ = '\\';
      --remaining;
    }
    *output++ = *value++;
    --remaining;
  }
  *output = 0;
  return true;
}

// Build the de-facto WiFi QR payload understood by Android and iOS cameras.
// A null or empty password denotes an open network.
inline bool buildWiFiSetupQrPayload(char* payload, size_t payload_size,
                                    const char* ssid,
                                    const char* password = nullptr) {
  if (payload == nullptr || payload_size == 0
      || ssid == nullptr || ssid[0] == 0) {
    return false;
  }

  char* output = payload;
  size_t remaining = payload_size;
  payload[0] = 0;
  const bool protected_network = password != nullptr && password[0] != 0;

  if (!appendWiFiQrText(output, remaining,
                        protected_network ? "WIFI:T:WPA;S:"
                                          : "WIFI:T:nopass;S:",
                        false)
      || !appendWiFiQrText(output, remaining, ssid, true)) {
    return false;
  }
  if (protected_network
      && (!appendWiFiQrText(output, remaining, ";P:", false)
          || !appendWiFiQrText(output, remaining, password, true))) {
    return false;
  }
  return appendWiFiQrText(output, remaining, ";;", false);
}

}  // namespace ui
}  // namespace mesh
