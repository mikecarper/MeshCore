#pragma once

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
