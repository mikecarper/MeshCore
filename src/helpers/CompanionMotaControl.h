#pragma once

#include <stddef.h>
#include <stdint.h>
#include "UsbAsciiBinarySwitch.h"

namespace mesh {
namespace companion {

static constexpr uint8_t CMD_EXEC_LOCAL_OTA_CONTROL = 0x4A;
static constexpr uint8_t CMD_BLE_MOTA_SOURCE = 0x4B;

enum class MotaSourceAction : uint8_t {
  Status = 0,
  Start = 1,
  Stop = 2,
};

static constexpr uint8_t MOTA_SOURCE_FLAG_CHANNEL_READY = 0x01;
static constexpr uint8_t MOTA_SOURCE_FLAG_ATTACHED = 0x02;
static constexpr uint8_t MOTA_SOURCE_FLAG_ANOTHER_LINK_ACTIVE = 0x04;

struct MotaSourceStatus {
  bool channel_ready = false;
  bool attached = false;
  bool another_link_active = false;
  uint16_t offered = 0;
  uint16_t advertised = 0;
  uint32_t packets_sent = 0;
};

class MotaSourceControl {
public:
  virtual ~MotaSourceControl() = default;
  virtual bool start(char* reply, size_t reply_size) = 0;
  virtual bool stop(char* reply, size_t reply_size) = 0;
  virtual MotaSourceStatus status() const = 0;
};

// Bluetooth may expose only the local commands needed to coordinate a LoRa
// mOTA session. The command arrives as one length-delimited Companion frame,
// so reject embedded NUL/control bytes and USB folder ownership commands.
inline bool isBleOtaControlCommandAllowed(const uint8_t* command,
                                          size_t length) {
  if (command == nullptr || length == 0 || length > 174) return false;
  for (size_t i = 0; i < length; ++i) {
    if (command[i] < 0x20 || command[i] > 0x7E) return false;
    switch (command[i]) {
      case ';':
      case '&':
      case '|':
      case '`':
      case '$':
      case '\\':
      case '\'':
      case '"':
        return false;
      default:
        break;
    }
  }

  const auto exact = [command, length](const char* text, size_t text_length) {
    if (length != text_length) return false;
    for (size_t i = 0; i < length; ++i) {
      if (command[i] != static_cast<uint8_t>(text[i])) return false;
    }
    return true;
  };
  const auto token = [command, length](const char* text, size_t text_length) {
    if (length < text_length) return false;
    for (size_t i = 0; i < text_length; ++i) {
      if (command[i] != static_cast<uint8_t>(text[i])) return false;
    }
    return length == text_length || command[text_length] == ' ';
  };

  if (exact("normalradio", 11)) return true;
  // Bare `tempradio` is a read-only status query.  Keep the parameterized
  // form strict, but let a BLE controller prove that cleanup completed.
  if (exact("tempradio", 9)) return true;
  if (token("tempradio", 9)) return length > 10;
  if (!token("ota", 3)) return false;
  if (mesh::isUsbMotaOwnerTransitionCommand(
          reinterpret_cast<const char*>(command), length)) return false;
  if (token("ota folder", 10)) return false;
  return true;
}

}  // namespace companion
}  // namespace mesh
