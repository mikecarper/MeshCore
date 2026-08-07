#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh {

// A command reply should normally clear the BLE stack within a few connection
// intervals. Allow generous transient backpressure, but do not leave the app
// waiting forever on a link whose notification path has stopped making
// progress.
static const uint32_t BLE_TX_STALL_TIMEOUT_MS = 10000UL;
static const uint32_t BLE_DISCONNECT_RETRY_INTERVAL_MS = 1000UL;

inline bool bleElapsedAtLeast(uint32_t now, uint32_t since,
                              uint32_t interval_ms) {
  // Unsigned subtraction remains correct across one millis() rollover.
  return static_cast<uint32_t>(now - since) >= interval_ms;
}

// Submit at most one ATT notification per writer call. This preserves the
// exact number of bytes accepted before a failure; some BLE UART wrappers
// collapse an internally partial multi-notification write to a zero return.
template <typename WriteChunk>
size_t writeBleFrameInChunks(const uint8_t* frame, size_t len,
                             size_t max_payload, WriteChunk write_chunk) {
  if (frame == nullptr || len == 0 || max_payload == 0) return 0;

  size_t total_written = 0;
  while (total_written < len) {
    const size_t remaining = len - total_written;
    const size_t chunk_len = remaining < max_payload ? remaining : max_payload;
    size_t chunk_written = write_chunk(frame + total_written, chunk_len);
    if (chunk_written > chunk_len) chunk_written = chunk_len;
    total_written += chunk_written;
    if (chunk_written != chunk_len) break;
  }
  return total_written;
}

class BleTxStallWatchdog {
  bool _active;
  uint32_t _blocked_since;

public:
  BleTxStallWatchdog() : _active(false), _blocked_since(0) {}

  void reset() {
    _active = false;
    _blocked_since = 0;
  }

  bool noteBlocked(uint32_t now,
                   uint32_t timeout_ms = BLE_TX_STALL_TIMEOUT_MS) {
    if (!_active) {
      _active = true;
      _blocked_since = now;
      return timeout_ms == 0;
    }

    return bleElapsedAtLeast(now, _blocked_since, timeout_ms);
  }

  bool active() const { return _active; }
};

// Disconnect requests are asynchronous and can transiently fail. Keep the
// transport in a recovery state until its disconnect callback arrives, and
// periodically retry the request instead of abandoning a live controller link
// after one failed call.
class BleDisconnectRecovery {
  bool _pending;
  bool _attempted;
  uint32_t _last_attempt;

public:
  BleDisconnectRecovery()
      : _pending(false), _attempted(false), _last_attempt(0) {}

  void begin() {
    _pending = true;
    _attempted = false;
    _last_attempt = 0;
  }

  void complete() {
    _pending = false;
    _attempted = false;
    _last_attempt = 0;
  }

  bool pending() const { return _pending; }

  bool shouldAttempt(
      uint32_t now,
      uint32_t retry_interval_ms = BLE_DISCONNECT_RETRY_INTERVAL_MS) {
    if (!_pending) return false;

    if (!_attempted ||
        bleElapsedAtLeast(now, _last_attempt, retry_interval_ms)) {
      _attempted = true;
      _last_attempt = now;
      return true;
    }
    return false;
  }
};

}  // namespace mesh
