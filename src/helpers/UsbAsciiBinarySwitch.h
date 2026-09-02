#pragma once

#include <atomic>
#include <stdint.h>
#include <string.h>

namespace mesh {

// OtaCli accepts both `folder` and `fold`, and collapses repeated ASCII spaces
// between its words. Recognize that same mutating command surface here so no
// alias can attach/detach the CDC0-backed folder outside the USB owner state
// machine. Status/list commands deliberately remain ordinary OTA CLI queries.
inline bool isUsbMotaOwnerTransitionCommand(const char* command,
                                             size_t length) {
  if (command == nullptr) return false;
  size_t pos = 0;
  while (pos < length && (command[pos] == ' ' || command[pos] == '\t')) ++pos;
  if (length - pos < 3 || memcmp(command + pos, "ota", 3) != 0) return false;
  pos += 3;
  if (pos < length && command[pos] != ' ') return false;

  while (pos < length && command[pos] == ' ') ++pos;
  const size_t noun_start = pos;
  while (pos < length && command[pos] != ' ') ++pos;
  const size_t noun_len = pos - noun_start;
  if (!((noun_len == 6
         && memcmp(command + noun_start, "folder", 6) == 0)
        || (noun_len == 4
            && memcmp(command + noun_start, "fold", 4) == 0))) {
    return false;
  }

  while (pos < length && command[pos] == ' ') ++pos;
  const size_t action_len = length - pos;
  return (action_len == 2 && memcmp(command + pos, "on", 2) == 0)
      || (action_len == 3 && memcmp(command + pos, "off", 3) == 0);
}

inline bool isUsbMotaOwnerTransitionCommand(const char* command) {
  return command != nullptr
      && isUsbMotaOwnerTransitionCommand(command, strlen(command));
}

// A deliberate HWCDC detach can produce a burst of BUS_RESET callbacks while
// the host enumerates the clean transport. Keep ignoring that whole burst until
// actual post-clean RX/TX activity proves that a new session has begun. Unlike
// a one-shot boolean exchange, repeated reset callbacks do not consume the
// clean-transport proof and cannot start a detach/reset loop.
class UsbSelfResetBurstGuard {
  std::atomic<bool> _clean_transport_waiting_for_activity{false};

public:
  void expectSelfResetBurst() {
    _clean_transport_waiting_for_activity.store(
        true, std::memory_order_release);
  }

  bool shouldIgnoreBusReset() const {
    return _clean_transport_waiting_for_activity.load(
        std::memory_order_acquire);
  }

  void notePostCleanActivity() {
    _clean_transport_waiting_for_activity.store(
        false, std::memory_order_release);
  }
};

// HWCDC exposes a physical/SOF host signal but no DTR/open state. Its framework
// filter is only a few milliseconds, so apply a second, meaningful debounce
// before reporting a transport boundary. The response route retains its longer
// grace independently, and sustained loss remains a conservative fallback.
class UsbHostPresenceDebouncer {
  bool _host_seen = false;
  bool _physical_host_seen = false;
  bool _loss_observed = false;
  bool _host_loss_edge_reported = false;
  bool _host_loss_edge_pending = false;
  bool _sustained_loss_pending = false;
  bool _completed_frame_seen = false;
  bool _finite_reply_was_pending = false;
  bool _finite_reply_drain_active = false;
  uint32_t _loss_observed_at = 0;
  uint32_t _last_completed_frame_count = 0;
  uint32_t _finite_reply_drain_started_at = 0;

  bool retainFiniteReplyLease(bool pending, uint32_t now,
                              uint32_t drain_grace_ms) {
    if (pending) {
      _finite_reply_was_pending = true;
      _finite_reply_drain_active = false;
      return true;
    }
    if (_finite_reply_was_pending) {
      _finite_reply_was_pending = false;
      _finite_reply_drain_active = true;
      _finite_reply_drain_started_at = now;
    }
    if (!_finite_reply_drain_active) return false;
    if ((uint32_t)(now - _finite_reply_drain_started_at) < drain_grace_ms) {
      return true;
    }
    _finite_reply_drain_active = false;
    return false;
  }

public:
  bool observeHost(bool host_present, uint32_t now,
                   uint32_t host_loss_edge_ms,
                   uint32_t host_loss_grace_ms) {
    if (_sustained_loss_pending) return false;
    if (host_present) {
      _host_seen = true;
      _physical_host_seen = true;
      _loss_observed = false;
      _host_loss_edge_reported = false;
      return true;
    }
    if (!_host_seen) return false;
    if (!_loss_observed) {
      _loss_observed = true;
      _loss_observed_at = now;
      return true;
    }
    const uint32_t loss_age = (uint32_t)(now - _loss_observed_at);
    if (!_host_loss_edge_reported && _physical_host_seen
        && loss_age >= host_loss_edge_ms) {
      // Keep this shorter than the response-affinity grace, but long enough to
      // reject the framework's transient five-millisecond SOF gaps. This still
      // recovers an actual unplug/re-enumeration when its BUS_RESET event was
      // dropped by the framework's finite event queue.
      _host_loss_edge_reported = true;
      _host_loss_edge_pending = true;
    }
    if (loss_age < host_loss_grace_ms) {
      return true;
    }
    _host_seen = false;
    _physical_host_seen = false;
    _loss_observed = false;
    _sustained_loss_pending = true;
    return false;
  }

  void noteClientActivity() {
    if (_sustained_loss_pending) return;
    _host_seen = true;
    _loss_observed = false;
  }

  bool isClientConnected(bool host_present, uint32_t now,
                         bool has_received_frame,
                         uint32_t last_frame_at,
                         uint32_t completed_frame_count,
                          uint32_t frame_reply_grace_ms,
                          uint32_t client_idle_timeout_ms,
                          uint32_t host_loss_edge_ms,
                          uint32_t host_loss_grace_ms,
                          bool finite_reply_pending) {
    // A newly completed Binary frame proves a host session even when HWCDC's
    // SOF signal was already false. Arm loss tracking once per frame count;
    // doing this on every poll would continually restart the debounce timer.
    if (has_received_frame
        && (!_completed_frame_seen
            || completed_frame_count != _last_completed_frame_count)) {
      _completed_frame_seen = true;
      _last_completed_frame_count = completed_frame_count;
      noteClientActivity();
    }

    // Sample the physical signal even during the immediate-frame lease so a
    // delayed operation has a fresh last-positive timestamp to rely on.
    const bool host_recent =
        observeHost(host_present, now, host_loss_edge_ms,
                    host_loss_grace_ms);
    if (_sustained_loss_pending) return false;
    if (!has_received_frame) return false;

    const uint32_t frame_age = (uint32_t)(now - last_frame_at);
    // Only a bounded, already-routed operation may extend the absolute idle
    // cap. Its true->false transition grants one short drain window for the
    // final frame which was queued just before the operation cleared itself.
    const bool finite_reply_lease = retainFiniteReplyLease(
        finite_reply_pending, now, frame_reply_grace_ms);
    if (frame_age < frame_reply_grace_ms) return true;
    return host_recent
        && (frame_age < client_idle_timeout_ms || finite_reply_lease);
  }

  bool takeSustainedHostLoss() {
    const bool pending = _sustained_loss_pending;
    _sustained_loss_pending = false;
    return pending;
  }

  bool takeHostLossEdge() {
    const bool pending = _host_loss_edge_pending;
    _host_loss_edge_pending = false;
    return pending;
  }

  void reset() {
    _host_seen = false;
    _physical_host_seen = false;
    _loss_observed = false;
    _host_loss_edge_reported = false;
    _host_loss_edge_pending = false;
    _sustained_loss_pending = false;
    _completed_frame_seen = false;
    _finite_reply_was_pending = false;
    _finite_reply_drain_active = false;
    _loss_observed_at = 0;
    _last_completed_frame_count = 0;
    _finite_reply_drain_started_at = 0;
  }
};

// Coordinates the Full Companion USB startup handoff without consuming the
// '<' byte. ArduinoSerialInterface remains the only binary protocol parser.
class UsbBinaryStartupProbe {
public:
  enum class Result : uint8_t {
    INACTIVE,
    WAITING,
    BINARY_CONFIRMED,
    RETURN_TO_ASCII,
  };

  static constexpr uint32_t TIMEOUT_MS = 1000;

private:
  bool _active = false;
  uint32_t _started_at = 0;
  uint32_t _frame_count_at_start = 0;

public:
  bool shouldStart(bool line_empty, bool discarding_line,
                   int next_byte) const {
    return !_active && line_empty && !discarding_line && next_byte == '<';
  }

  void start(uint32_t now, uint32_t completed_frame_count) {
    _active = true;
    _started_at = now;
    _frame_count_at_start = completed_frame_count;
  }

  void cancel() { _active = false; }
  bool isActive() const { return _active; }

  bool hasTimedOut(uint32_t now) const {
    return _active && (uint32_t)(now - _started_at) >= TIMEOUT_MS;
  }

  Result poll(uint32_t now, uint32_t completed_frame_count,
              uint32_t completed_frame_at = 0) {
    if (!_active) return Result::INACTIVE;
    if (completed_frame_count != _frame_count_at_start) {
      _active = false;
      // A frame parsed before the deadline remains valid even if other mesh
      // work delays this poll. A frame completed at/after the boundary loses to
      // the timeout, which keeps the advertised one-second window strict.
      return (uint32_t)(completed_frame_at - _started_at) < TIMEOUT_MS
          ? Result::BINARY_CONFIRMED : Result::RETURN_TO_ASCII;
    }
    if (hasTimedOut(now)) {
      _active = false;
      return Result::RETURN_TO_ASCII;
    }
    return Result::WAITING;
  }
};

// Tracks the narrow case where TCP temporarily borrows an idle startup ASCII
// terminal. A complete USB Binary frame during the TCP session wins ownership,
// so closing TCP must not force that USB client back to ASCII.
class UsbTcpTerminalHandoff {
  bool _restore_ascii = false;
  uint32_t _frame_count_at_start = 0;

public:
  bool begin(bool usb_ascii_selected, bool usb_data_connected,
             bool usb_input_idle, uint32_t completed_frame_count) {
    _restore_ascii = false;
    if (!usb_ascii_selected) return true;
    if (usb_data_connected || !usb_input_idle) return false;
    _restore_ascii = true;
    _frame_count_at_start = completed_frame_count;
    return true;
  }

  bool shouldRestoreAscii(uint32_t completed_frame_count) {
    const bool restore = _restore_ascii
        && completed_frame_count == _frame_count_at_start;
    _restore_ascii = false;
    return restore;
  }

  void cancel() { _restore_ascii = false; }
  bool isBorrowingAscii() const { return _restore_ascii; }
};

// Keeps the single-TTY logging transitions independent from the platform's
// stream objects. In particular, an active TCP terminal owns the role CLI and
// must not be displaced just because USB logging is enabled.
enum class UsbLoggingTerminalAction : uint8_t {
  NO_ACTION,
  CLAIM_USB,
  RETURN_TO_BINARY,
  KEEP_ASCII,
};

inline UsbLoggingTerminalAction selectUsbLoggingTerminalAction(
    bool has_dedicated_logging_port, bool logging_enabled,
    bool usb_terminal_active, bool usb_logging_terminal_active,
    bool full_companion, bool network_terminal_active) {
  if (has_dedicated_logging_port || network_terminal_active) {
    return UsbLoggingTerminalAction::NO_ACTION;
  }
  if (logging_enabled) {
    return UsbLoggingTerminalAction::CLAIM_USB;
  }
  if (!usb_terminal_active || !usb_logging_terminal_active) {
    return UsbLoggingTerminalAction::NO_ACTION;
  }
  return full_companion ? UsbLoggingTerminalAction::KEEP_ASCII
                        : UsbLoggingTerminalAction::RETURN_TO_BINARY;
}

enum class UsbMotaEntryOrigin : uint8_t { BINARY, ASCII };

inline bool shouldRestoreAsciiAfterMotaFailure(UsbMotaEntryOrigin origin) {
  return origin == UsbMotaEntryOrigin::ASCII;
}

} // namespace mesh
