#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// Tracks one terminal-originated remote command. CLI_DATA replies do not echo
// a request identifier, so the sender must serialize commands and match the
// next reply by the full contact key.
template <size_t KeySize>
class TerminalCommandTracker {
public:
  TerminalCommandTracker() { clear(); }

  bool begin(const uint8_t* key, uint32_t sent_at,
             uint32_t timeout_millis) {
    if (_pending || key == NULL || timeout_millis == 0) return false;
    memcpy(_key, key, KeySize);
    _sent_at = sent_at;
    _timeout_millis = timeout_millis;
    _pending = true;
    return true;
  }

  bool isPending() const { return _pending; }

  bool takeReply(const uint8_t* key, uint32_t received_at,
                 uint32_t& elapsed_millis) {
    elapsed_millis = 0;
    if (!_pending || key == NULL || memcmp(_key, key, KeySize) != 0) {
      return false;
    }
    elapsed_millis = received_at - _sent_at;
    clear();
    return true;
  }

  bool expire(uint32_t now, uint32_t& elapsed_millis) {
    elapsed_millis = 0;
    if (!_pending) return false;
    const uint32_t elapsed = now - _sent_at;
    if (elapsed < _timeout_millis) return false;
    elapsed_millis = elapsed;
    clear();
    return true;
  }

  void clear() {
    _pending = false;
    memset(_key, 0, sizeof(_key));
    _sent_at = 0;
    _timeout_millis = 0;
  }

private:
  bool _pending;
  uint8_t _key[KeySize];
  uint32_t _sent_at;
  uint32_t _timeout_millis;
};

}  // namespace mesh
