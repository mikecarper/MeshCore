#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <helpers/UTF8Helpers.h>

namespace mesh {
namespace companion {

// Bluetooth names are stored as UTF-8 bytes. Keeping the override the same
// size as the node name makes its limit predictable on every Companion target.
static constexpr size_t BLUETOOTH_NAME_SIZE = 32;
static constexpr size_t BLUETOOTH_NAME_MAX_BYTES = BLUETOOTH_NAME_SIZE - 1;

inline bool isValidBluetoothName(const char* name) {
  if (name == nullptr) return false;

  const size_t length = strnlen(name, BLUETOOTH_NAME_SIZE);
  if (length == 0 || length >= BLUETOOTH_NAME_SIZE
      || validUtf8PrefixLength(name, length) != length) {
    return false;
  }

  bool has_visible_character = false;
  for (size_t i = 0; i < length; i++) {
    const uint8_t byte = static_cast<uint8_t>(name[i]);
    if (byte < 0x20 || byte == 0x7F) return false;
    if (byte != 0x20) has_visible_character = true;
  }
  return has_visible_character;
}

inline bool hasCustomBluetoothName(const char* name) {
  return name != nullptr && name[0] != '\0' && isValidBluetoothName(name);
}

inline void formatBluetoothName(char* output, size_t output_size,
                                const char* custom_name,
                                const char* default_prefix,
                                const char* node_name) {
  if (output == nullptr || output_size == 0) return;

  if (hasCustomBluetoothName(custom_name)) {
    snprintf(output, output_size, "%s", custom_name);
  } else {
    snprintf(output, output_size, "%s%s",
             default_prefix == nullptr ? "" : default_prefix,
             node_name == nullptr ? "" : node_name);
  }
}

}  // namespace companion
}  // namespace mesh
