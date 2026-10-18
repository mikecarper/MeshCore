#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#if defined(MESH_MANAGEMENT_PASSWORD_HOST_TEST)
#include <mutex>
#elif defined(ESP_PLATFORM) || defined(ESP32_PLATFORM)
#include <freertos/FreeRTOS.h>
#endif

namespace mesh {
namespace esp32 {
namespace managementPassword {

// The wire/prefs field is sixteen bytes, including its terminator. Do not
// silently truncate a new secret or change an existing node's credential.
inline bool validPassword(const char* value) {
  if (!value) return false;
  size_t n = 0;
  while (n < 16 && value[n]) {
    if (value[n] == '\r' || value[n] == '\n') return false;
    ++n;
  }
  return n != 0 && n < 16;
}

inline bool apPasswordEligible(const char* value) {
  if (!validPassword(value)) return false;
  size_t n = 0;
  for (; value[n]; ++n) {
    const unsigned char c = static_cast<unsigned char>(value[n]);
    if (c < 0x20 || c > 0x7e) return false;
  }
  return n >= 8;
}

// Exact, bounded HTTP Basic credentials: fixed username admin.
inline bool basicAuthorizationMatches(const char* authorization, const char* password) {
  if (!validPassword(password)) return false;
  char input[22] = "admin:";
  memcpy(input + 6, password, strlen(password));
  static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  char expected[35] = "Basic ";
  size_t out = 6;
  const size_t length = 6 + strlen(password);
  for (size_t i = 0; i < length; i += 3) {
    const uint32_t bits = (uint32_t(uint8_t(input[i])) << 16)
        | (i + 1 < length ? uint32_t(uint8_t(input[i + 1])) << 8 : 0)
        | (i + 2 < length ? uint8_t(input[i + 2]) : 0);
    expected[out++] = alphabet[bits >> 18]; expected[out++] = alphabet[(bits >> 12) & 63];
    expected[out++] = i + 1 < length ? alphabet[(bits >> 6) & 63] : '=';
    expected[out++] = i + 2 < length ? alphabet[bits & 63] : '=';
  }
  expected[out] = 0;
  size_t supplied = 0;
  if (authorization) while (supplied < sizeof(expected) && authorization[supplied]) ++supplied;
  uint8_t difference = supplied == out ? 0 : 1;
  for (size_t i = 0; i < out; ++i) {
    unsigned char actual = i < supplied ? static_cast<unsigned char>(authorization[i]) : 0;
    // HTTP auth scheme names are case-insensitive; the Base64 value is not.
    if (i < 5 && actual >= 'a' && actual <= 'z') actual -= 'a' - 'A';
    unsigned char wanted = static_cast<unsigned char>(expected[i]);
    if (i < 5 && wanted >= 'a' && wanted <= 'z') wanted -= 'a' - 'A';
    difference |= actual ^ wanted;
  }
  memset(input, 0, sizeof(input)); memset(expected, 0, sizeof(expected));
  return difference == 0;
}

#if defined(ESP_PLATFORM) || defined(ESP32_PLATFORM) || defined(MESH_MANAGEMENT_PASSWORD_HOST_TEST)
struct Snapshot {
  char password[16] = {};
  uint32_t generation = 0;
};

namespace detail {
struct State {
#if defined(MESH_MANAGEMENT_PASSWORD_HOST_TEST)
  std::mutex mutex;
#else
  portMUX_TYPE mutex = portMUX_INITIALIZER_UNLOCKED;
#endif
  Snapshot current;
  bool changing = false, uploading = false, rotating = false;
  uint8_t failures = 0;
  uint32_t failure_window = 0;
  void* ap_owner = nullptr;
  bool (*rotate_ap)(void*) = nullptr;
  uint32_t ap_generation = 0, pending_generation = 0, pending_at = 0;
};
// An inline function's local static is shared across translation units. The
// mirror is published only by the loop owner after a confirmed persistent save;
// HTTP tasks never read a mutable prefs struct or a factory-password macro.
inline State& state() { static State value; return value; }
class Lock {
  State& value;
public:
  explicit Lock(State& s) : value(s) {
#if defined(MESH_MANAGEMENT_PASSWORD_HOST_TEST)
    value.mutex.lock();
#else
    portENTER_CRITICAL(&value.mutex);
#endif
  }
  ~Lock() {
#if defined(MESH_MANAGEMENT_PASSWORD_HOST_TEST)
    value.mutex.unlock();
#else
    portEXIT_CRITICAL(&value.mutex);
#endif
  }
};
inline void assign(State& s, const char* password) {
  if (s.current.generation && strcmp(s.current.password, password) == 0) return;
  memset(s.current.password, 0, sizeof(s.current.password));
  memcpy(s.current.password, password, strlen(password));
  if (++s.current.generation == 0) ++s.current.generation;
  s.failures = 0;
}
} // namespace detail

inline bool snapshot(Snapshot& out) {
  detail::State& s = detail::state();
  detail::Lock lock(s);
  out = s.current;
  return out.generation != 0;
}
inline bool available() { Snapshot value; return snapshot(value) && value.password[0] != 0; }

// Boot/load only: retain an explicitly empty legacy credential for services
// whose historical policy is passwordless. OTA still requires available().
// Never replace an already-published value with a later mutable prefs snapshot.
inline bool initialize(const char* saved_password) {
  if (!saved_password || (saved_password[0] && !validPassword(saved_password))) return false;
  detail::State& s = detail::state(); detail::Lock lock(s);
  if (s.current.generation) return strcmp(s.current.password, saved_password) == 0;
  if (s.changing || s.uploading || s.rotating) return false;
  detail::assign(s, saved_password);
  return true;
}

inline bool publish(const char* saved_password) {
  if (!validPassword(saved_password)) return false;
  detail::State& s = detail::state();
  detail::Lock lock(s);
  if (strcmp(s.current.password, saved_password) == 0) return true;
  if (s.changing || s.uploading || s.rotating) return false;
  detail::assign(s, saved_password);
  return true;
}

// Reserve before changing RAM or saving, so a new upload cannot slip between
// an upload-busy check and publication. A failed save simply releases the guard.
class ChangeGuard {
  bool held = false;
public:
  ChangeGuard() {
    detail::State& s = detail::state(); detail::Lock lock(s);
    if (!s.changing && !s.uploading && !s.rotating) held = s.changing = true;
  }
  ChangeGuard(const ChangeGuard&) = delete;
  ChangeGuard& operator=(const ChangeGuard&) = delete;
  bool acquired() const { return held; }
  bool publish(const char* saved_password) {
    if (!held || !validPassword(saved_password)) return false;
    detail::State& s = detail::state(); detail::Lock lock(s);
    detail::assign(s, saved_password);
    return true;
  }
  ~ChangeGuard() {
    if (held) { detail::State& s = detail::state(); detail::Lock lock(s); s.changing = false; }
  }
};

inline bool beginUpload(uint32_t authenticated_generation) {
  detail::State& s = detail::state(); detail::Lock lock(s);
  if (!authenticated_generation || authenticated_generation != s.current.generation
      || s.changing || s.uploading || s.rotating) return false;
  s.uploading = true;
  return true;
}
inline void endUpload() {
  detail::State& s = detail::state(); detail::Lock lock(s); s.uploading = false;
}
inline bool uploadActive() {
  detail::State& s = detail::state(); detail::Lock lock(s); return s.uploading;
}
class UploadGuard {
  bool held;
public:
  explicit UploadGuard(uint32_t generation) : held(beginUpload(generation)) {}
  UploadGuard(const UploadGuard&) = delete;
  UploadGuard& operator=(const UploadGuard&) = delete;
  bool acquired() const { return held; }
  ~UploadGuard() { if (held) endUpload(); }
};

enum class AuthResult : uint8_t { Authorized, Unauthorized, Unavailable, Throttled };

// Compare the bounded Basic value rather than storing decoded credentials.
// The browser's normal HTTP Basic prompt/cache also covers same-origin XHR.
inline AuthResult authenticateBasic(const char* authorization, uint32_t now,
                                    uint32_t* generation = nullptr) {
  Snapshot current;
  if (!snapshot(current) || !current.password[0]) return AuthResult::Unavailable;
  {
    detail::State& s = detail::state(); detail::Lock lock(s);
    if (uint32_t(now - s.failure_window) >= 30000U) { s.failures = 0; s.failure_window = now; }
    if (s.failures >= 5) return AuthResult::Throttled;
  }
  const bool matches = basicAuthorizationMatches(authorization, current.password);
  detail::State& s = detail::state(); detail::Lock lock(s);
  if (s.current.generation != current.generation) return AuthResult::Unauthorized;
  if (!matches) {
    // A browser's first unauthenticated request triggers its login prompt and
    // is not a guess. Count only a supplied, rejected Authorization header.
    if (authorization && authorization[0]) {
      if (!s.failures) s.failure_window = now;
      if (s.failures < 5) ++s.failures;
    }
    return AuthResult::Unauthorized;
  }
  s.failures = 0;
  if (generation) *generation = current.generation;
  return AuthResult::Authorized;
}

inline void watchOtaAccessPoint(void* owner, bool (*rotate)(void*), uint32_t generation) {
  detail::State& s = detail::state(); detail::Lock lock(s);
  s.ap_owner = owner; s.rotate_ap = rotate; s.ap_generation = generation; s.pending_generation = 0;
}
inline void forgetOtaAccessPoint(void* owner) {
  detail::State& s = detail::state(); detail::Lock lock(s);
  if (s.ap_owner == owner) { s.ap_owner = nullptr; s.rotate_ap = nullptr; }
}
// Called on the mesh loop. Rekey only this OTA owner's AP after the password
// command's reply has had time to leave; do not race an accepted flash upload.
inline void service(uint32_t now) {
  void* owner; bool (*rotate)(void*); uint32_t generation;
  {
    detail::State& s = detail::state(); detail::Lock lock(s);
    if (!s.ap_owner || !s.rotate_ap || s.ap_generation == s.current.generation) return;
    if (s.pending_generation != s.current.generation) {
      s.pending_generation = s.current.generation; s.pending_at = now; return;
    }
    if (uint32_t(now - s.pending_at) < 750U || s.changing || s.uploading || s.rotating) return;
    s.rotating = true; owner = s.ap_owner; rotate = s.rotate_ap; generation = s.current.generation;
  }
  const bool done = rotate(owner);
  {
    detail::State& s = detail::state(); detail::Lock lock(s);
    s.rotating = false;
    if (done && s.ap_owner == owner && s.current.generation == generation) s.ap_generation = generation;
  }
}
#endif
} // namespace managementPassword
} // namespace esp32
} // namespace mesh
