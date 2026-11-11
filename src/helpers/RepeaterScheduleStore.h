#pragma once

#include "IdentityStore.h"
#include "AtomicFileWriter.h"
#include "ContactFileTransaction.h"
#include "CheckedSettingsFile.h"
#include "FilePresence.h"
#include "PersistentStoreFormat.h"
#include <RadioProfiles.h>

namespace mesh {

// Separate from NodePrefs: only permanent radioat rows and the requested GPS
// history capacity survive restart. Temporary windows and samples never do.
template <typename Row, size_t Capacity>
class RepeaterScheduleStore {
  static constexpr const char* Path = "/radioat";
  static constexpr size_t Size = 10 + 16 * Capacity;
  FILESYSTEM* fs_ = nullptr;
  bool ready_ = false;
  uint8_t days_ = 7;

  static void encodeRow(uint8_t* out, const Row& row) {
    uint32_t bits;
    memcpy(&bits, &row.freq, 4); storage::writeLE32(out, bits);
    memcpy(&bits, &row.bw, 4); storage::writeLE32(out + 4, bits);
    storage::writeLE32(out + 8, row.start_time);
    storage::writeLE16(out + 12, row.preamble);
    out[14] = row.sf; out[15] = row.cr;
  }
  static bool decodeRow(const uint8_t* in, Row& row) {
    row = {};
    uint32_t bits = storage::readLE32(in); memcpy(&row.freq, &bits, 4);
    bits = storage::readLE32(in + 4); memcpy(&row.bw, &bits, 4);
    row.start_time = storage::readLE32(in + 8);
    row.preamble = storage::readLE16(in + 12);
    row.sf = in[14]; row.cr = in[15];
    RadioProfileParams params;
    params.freq = row.freq; params.bw = row.bw;
    params.sf = row.sf; params.cr = row.cr; params.preamble = row.preamble;
    row.active = true;
    return row.start_time >= 1577836800UL && RadioProfiles::valid(params);
  }
public:
  bool ready() const { return ready_; }
  uint8_t days() const { return days_; }

  // Keep boot-only decoding out of the large application startup frame.
  __attribute__((noinline)) bool begin(FILESYSTEM* fs, Row* rows) {
    fs_ = fs; ready_ = false; days_ = 7;
    if (!fs_) return false;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
    if (!ContactFileTransaction::recover(fs_, Path, filePresence<FILESYSTEM>)) return false;
#endif
    bool present = false;
    if (!filePresence(fs_, Path, present)) return false;
    if (!present) return ready_ = true;
    uint8_t image[Size];
    const auto read = storage::readFixedSettingsFileResult(fs_, Path, image, Size);
    if (read == storage::FixedSettingsRead::Unreadable) return false;
    // A known malformed record is preserved until an explicit setter or
    // deletion repairs it. An unreadable record must never be overwritten.
    ready_ = true;
    if (read != storage::FixedSettingsRead::Complete
        || memcmp(image, "RAT\1", 4) || image[4] < 1 || image[4] > 30
        || image[5] > Capacity
        || storage::readLE32(image + Size - 4)
            != ~storage::updateCRC32(0xffffffff, image, Size - 4)) return false;
    // Decode exactly once and publish only a fully validated queue. At the
    // default three slots this uses 96 bytes, 64 more than a single scratch row.
    Row candidates[Capacity];
    for (size_t i = 0; i < image[5]; ++i) {
      if (!decodeRow(image + 6 + 16 * i, candidates[i])) return false;
      for (size_t j = 0; j < i; ++j)
        if (candidates[j].start_time == candidates[i].start_time) return false;
    }
    memcpy(rows, candidates, image[5] * sizeof(Row));
    days_ = image[4];
    return ready_ = true;
  }

  bool save(const Row* rows, size_t count, uint8_t days,
            int replacement_slot = -1, const Row* replacement = nullptr,
            const uint8_t* deleted = nullptr) {
    if (!ready_ || days < 1 || days > 30) return false;
    uint8_t image[Size] = {'R', 'A', 'T', 1, days, 0};
    for (size_t i = 0; i < count; ++i) {
      const Row& row = int(i) == replacement_slot && replacement ? *replacement : rows[i];
      if (!row.active || row.temporary || (deleted && (deleted[i / 8] & (1U << (i % 8))))) continue;
      if (image[5] == Capacity) return false;
      encodeRow(image + 6 + 16 * image[5]++, row);
    }
    storage::writeLE32(image + Size - 4, ~storage::updateCRC32(0xffffffff, image, Size - 4));
    bool saved;
    {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
      AtomicFileWriter writer(fs_, Path);
#else
      ContactFileTransaction writer(fs_, Path, filePresence<FILESYSTEM>);
#endif
      saved = writer && writer.write(image, Size) == Size && writer.commit();
      if (!saved && writer.outcome() == decltype(writer)::Outcome::Indeterminate) ready_ = false;
    }
    if (!saved) return false;
#if !defined(NRF52_PLATFORM) && !defined(STM32_PLATFORM)
    // AtomicFileWriter already verifies the staged and published bytes with
    // CRC32C. ContactFileTransaction requires this public-image readback.
    uint8_t verified[Size];
    if (!storage::readFixedSettingsFile(fs_, Path, verified, Size)
        || memcmp(verified, image, Size)) { ready_ = false; return false; }
#endif
    days_ = days;
    return true;
  }
};

} // namespace mesh
