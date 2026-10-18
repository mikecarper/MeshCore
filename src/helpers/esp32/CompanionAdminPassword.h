#pragma once

#if defined(ESP32_PLATFORM) && defined(WIFI_SSID)

#include <Preferences.h>
#include <stddef.h>
#include <string.h>

namespace mesh { namespace esp32 {

// ESP32 Companion management credentials are separate from the fixed binary
// Companion preferences image. Existing identities, contacts and wire offsets
// do not change when management authentication is added.
namespace companionAdminPassword {
static constexpr size_t CAPACITY = 16;

inline bool valid(const char* value) {
  if (!value) return false;
  const size_t length = strnlen(value, CAPACITY);
  if (length == 0 || length >= CAPACITY) return false;
  for (size_t i = 0; i < length; ++i) {
    if (value[i] == '\r' || value[i] == '\n') return false;
  }
  return true;
}

inline bool load(char (&out)[CAPACITY], const char* factory_password) {
  memset(out, 0, sizeof(out));
  Preferences prefs;
  if (!prefs.begin("mesh-admin", false)) return false;
  const bool has_password = prefs.isKey("password");
  const bool has_pending = prefs.isKey("pending");
  bool ok = !has_pending || (prefs.getType("pending") == PT_U8
                            && prefs.getUChar("pending", 1) == 0);
  if (ok && !has_password && !has_pending) {
    ok = valid(factory_password);
    if (ok) memcpy(out, factory_password, strlen(factory_password) + 1);
  } else if (ok && has_password && prefs.getType("password") == PT_STR) {
    const size_t count = prefs.getString("password", out, sizeof(out));
    ok = count > 1 && count <= sizeof(out) && out[count - 1] == 0
        && count == strnlen(out, sizeof(out)) + 1 && valid(out);
  } else {
    ok = false;
  }
  prefs.end();
  if (!ok) memset(out, 0, sizeof(out));
  return ok;
}

inline bool save(const char* password) {
  if (!valid(password)) return false;
  Preferences prefs;
  if (!prefs.begin("mesh-admin", false)) return false;
  // Each NVS operation commits. An interrupted multi-key change must not
  // reopen the web interface using a guessed factory or half-saved password.
  bool ok = prefs.putUChar("pending", 1) == 1
      && prefs.getUChar("pending", 0) == 1;
  char verified[CAPACITY] = {};
  if (ok) {
    const size_t length = strlen(password);
    ok = prefs.putString("password", password) == length
        && prefs.getType("password") == PT_STR
        && prefs.getString("password", verified, sizeof(verified)) == length + 1
        && memcmp(verified, password, length + 1) == 0;
  }
  if (ok) {
    ok = prefs.putUChar("pending", 0) == 1
        && prefs.getUChar("pending", 1) == 0;
  }
  prefs.end();
  memset(verified, 0, sizeof(verified));
  return ok;
}

}  // namespace companionAdminPassword
} }  // namespace mesh::esp32

#endif
