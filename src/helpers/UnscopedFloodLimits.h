#pragma once

#include <stdint.h>
#include <string.h>
#include "DiscoveryResponsePrefs.h"

namespace mesh {

struct UnscopedFloodLimits {
  // The low four bits of the wire payload type index the first 16 entries.
  static constexpr uint8_t Text = 16;
  static constexpr uint8_t Count = Text + 1;
  using Slots = uint8_t[Count];  // 0 inherits, 1..65 encode hop limits 0..64

  static int settingSlot(const char* key, size_t length) {
    static const char prefix[] = "flood.max.unscoped.";
    const size_t prefix_len = sizeof(prefix) - 1;
    if (length <= prefix_len || memcmp(key, prefix, prefix_len)) return -1;
    const char* name = key + prefix_len;
    length -= prefix_len;
    static const char* const names[Count] = {
      "req", "response", "txt_msg", "ack", "advert", "grp_txt", "data", "anon_req",
      "path", "trace", "multipart", "control", "ota", "", "", "raw", "text"
    };
    for (unsigned i = 0; i < Count; ++i) {
      if (strlen(names[i]) == length && !memcmp(name, names[i], length)) return i;
    }
    if (length == 8 && !memcmp(name, "grp_data", 8)) return 6;
    if (length == 10 && !memcmp(name, "raw_custom", 10)) return 15;
    return -1;
  }

  static uint8_t effective(const uint8_t* slots, uint8_t type, bool text, uint8_t fallback) {
    uint8_t value = slots[type];
    if ((value == 0 || value > 65) && text) value = slots[Text];
    return value > 0 && value <= 65 ? value - 1 : fallback;
  }

  // Optional record between discovery preferences and platform EOF footers.
  static constexpr size_t RecordSize = 4 + Count + 2;
  static bool encode(const Slots& slots, uint8_t (&record)[RecordSize]) {
    bool used = false;
    memcpy(record, "UFL\1", 4);
    for (unsigned i = 0; i < Count; ++i) {
      record[4 + i] = slots[i] <= 65 ? slots[i] : 0;
      used |= record[4 + i] != 0;
    }
    if (!used) return false;
    const uint16_t crc = discovery::checksum(record, RecordSize - 2);
    record[RecordSize - 2] = crc >> 8;
    record[RecordSize - 1] = uint8_t(crc);
    return true;
  }

  template <class File>
  static void read(File& file, size_t offset, Slots& slots) {
    memset(slots, 0, sizeof(slots));
    uint8_t record[RecordSize];
    if (file.size() < offset + RecordSize || !file.seek(offset)
        || file.read(record, RecordSize) != RecordSize || memcmp(record, "UFL\1", 4)
        || discovery::checksum(record, RecordSize - 2) !=
            uint16_t(record[RecordSize - 2] << 8 | record[RecordSize - 1])) return;
    for (unsigned i = 0; i < Count; ++i) if (record[4 + i] > 65) return;
    memcpy(slots, record + 4, Count);
  }
};

} // namespace mesh
