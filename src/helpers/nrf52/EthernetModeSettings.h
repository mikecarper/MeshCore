#pragma once

#include <helpers/CheckedSettingsFile.h>
#include <helpers/FilePresence.h>
#include <helpers/PersistentStoreFormat.h>

namespace mesh { namespace nrf52 {

// Separate from NodePrefs so existing RAK images keep their binary layout.
// Only the optional/shared Ethernet recipes instantiate this record.
class EthernetModeSettings {
  FILESYSTEM* fs_ = nullptr;
  bool enabled_ = false, healthy_ = false, valid_ = false;
  static const char* path() { return "/eth_mode"; }

  static void encode(uint8_t (&image)[12], bool enabled) {
    memset(image, 0, sizeof(image));
    memcpy(image, "ETH1", 4);
    image[4] = uint8_t(enabled);
    storage::writeLE32(image + 8, storage::updateCRC32(0xffffffff, image, 8));
  }

public:
  bool begin(FILESYSTEM* fs) {
    fs_ = fs;
    enabled_ = healthy_ = valid_ = false;
    bool present = false;
    if (!fs || !filePresence(fs, path(), present)) return false;
    healthy_ = true;
    if (!present) { valid_ = true; return true; } // Older installs stay off.
    uint8_t image[12], expected[12];
    const auto read = storage::readFixedSettingsFileResult(fs, path(), image, sizeof(image));
    if (read == storage::FixedSettingsRead::Unreadable) { healthy_ = false; return false; }
    if (read != storage::FixedSettingsRead::Complete || image[4] > 1) return false;
    encode(expected, image[4] != 0);
    if (memcmp(image, expected, sizeof(image))) return false;
    enabled_ = image[4] != 0;
    valid_ = true;
    return true;
  }

  bool enabled() const { return healthy_ && enabled_; }
  bool healthy() const { return healthy_; }

  bool setEnabled(bool enabled) {
    if (!fs_ || !healthy_) return false;
    if (valid_ && enabled_ == enabled) return true;
    uint8_t image[12];
    encode(image, enabled);
    const auto result = storage::writeCheckedSettingsFile(fs_, path(), image, sizeof(image));
    if (result == AtomicFileWriter::Outcome::Indeterminate) healthy_ = false;
    if (result != AtomicFileWriter::Outcome::Committed) return false;
    enabled_ = enabled;
    valid_ = true;
    return true;
  }
};

}} // namespace mesh::nrf52
