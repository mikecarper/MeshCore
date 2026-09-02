#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {

class MainBoard;

namespace cli {

enum class StorageLayoutGetMatch : uint8_t {
  NotMatched = 0,
  Valid,
  InvalidArguments,
};

// `config` is the portion following "get ". Keep recognition independent of
// any role's CLI wrapper so CommonCLI and Companion command 0x42 expose the
// same diagnostic and the same malformed-command response.
inline StorageLayoutGetMatch classifyStorageLayoutGet(const char* config) {
  static const char key[] = "storage.layout";
  const size_t key_len = sizeof(key) - 1;
  if (config == nullptr || strncmp(config, key, key_len) != 0 ||
      (config[key_len] != 0 && config[key_len] != ' ' &&
       config[key_len] != '\t')) {
    return StorageLayoutGetMatch::NotMatched;
  }
  return config[key_len] == 0 ? StorageLayoutGetMatch::Valid
                              : StorageLayoutGetMatch::InvalidArguments;
}

inline void formatStorageBytes(char* out, size_t out_len, uint64_t bytes) {
  if (out == nullptr || out_len == 0) return;
  static const char* const units[] = {"B", "KiB", "MiB", "GiB", "TiB"};
  uint64_t unit_size = 1;
  uint8_t unit = 0;
  while (unit < 4 && bytes >= unit_size * 1024ULL) {
    unit_size *= 1024ULL;
    unit++;
  }

  if (unit == 0) {
    snprintf(out, out_len, "%lu B", (unsigned long)bytes);
    return;
  }

  uint64_t whole = bytes / unit_size;
  uint32_t tenths =
      (uint32_t)(((bytes % unit_size) * 10ULL + unit_size / 2ULL) /
                 unit_size);
  if (tenths == 10) {
    whole++;
    tenths = 0;
  }
  snprintf(out, out_len, "%lu.%lu %s", (unsigned long)whole,
           (unsigned long)tenths, units[unit]);
}

// The hardware-facing formatter walks ESP-IDF's live partition iterator. This
// small writer keeps its bounded text policy host-testable without mocking IDF.
class Esp32StorageLayoutWriter {
public:
  Esp32StorageLayoutWriter(char* out, size_t out_len, uint32_t flash_bytes)
      : _out(out), _out_len(out_len), _has_partition(false),
        _truncated(false) {
    if (_out == nullptr || _out_len == 0) return;
    snprintf(_out, _out_len, "> int:esp32=%luK ext:none; ",
             (unsigned long)(flash_bytes / 1024UL));
  }

  bool append(const char* label, uint32_t address, uint32_t size,
              bool running) {
    if (_out == nullptr || _out_len == 0 || _truncated) return false;

    char entry[48];
    snprintf(entry, sizeof(entry), "%s%s%s@0x%lX+%luK",
             _has_partition ? "," : "", label != nullptr ? label : "",
             running ? "*" : "", (unsigned long)address,
             (unsigned long)(size / 1024UL));

    const size_t used = strlen(_out);
    const size_t entry_len = strlen(entry);
    // Preserve one spare byte beyond the terminator, matching the original
    // CommonCLI response policy and leaving room for an ellipsis when possible.
    if (used + entry_len + 1 >= _out_len) {
      _truncated = true;
      return false;
    }
    memcpy(_out + used, entry, entry_len + 1);
    _has_partition = true;
    return true;
  }

  void finish() {
    if (_out == nullptr || _out_len == 0) return;
    const size_t used = strlen(_out);
    if (!_has_partition) {
      if (used < _out_len) {
        snprintf(_out + used, _out_len - used, "partitions=none");
      }
    } else if (_truncated && used + 4 < _out_len) {
      memcpy(_out + used, ",...", 5);
    }
  }

  bool truncated() const { return _truncated; }

private:
  char* _out;
  size_t _out_len;
  bool _has_partition;
  bool _truncated;
};

void formatStorageLayout(MainBoard& board, char* reply, size_t reply_size);

// Returns false only when `config` is not this command. Recognized malformed
// requests are consumed and receive the canonical usage response.
bool handleStorageLayoutGet(const char* config, MainBoard& board, char* reply,
                            size_t reply_size);

} // namespace cli
} // namespace mesh
