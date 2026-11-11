#include "WirelessPolicyStore.h"
#include "CheckedSettingsFile.h"
#include "FilePresence.h"
#include "PersistentStoreFormat.h"
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include "AtomicFileWriter.h"
#else
#include "ContactFileTransaction.h"
#endif

namespace mesh { namespace wireless {
namespace {
constexpr char Path[] = "/wireless_policy";
constexpr size_t ImageSize = 12;
bool validChoice(const Policy& value) {
  return value.mode <= 1 && value.mask <= 7 && value.primary_espnow <= 2;
}
void encode(uint8_t (&image)[ImageSize], const Policy& value) {
  memcpy(image, "WLP1", 4);
  image[4] = value.mode;
  image[5] = value.mask;
  image[6] = value.primary_espnow;
  image[7] = 0;
  storage::writeLE32(image + 8, storage::updateCRC32(0xffffffff, image, 8));
}
bool decode(const uint8_t (&image)[ImageSize], Policy& value) {
  Policy candidate{image[4], image[5], image[6]};
  if (memcmp(image, "WLP1", 4) != 0 || image[7] != 0 || !validChoice(candidate)
      || storage::readLE32(image + 8) != storage::updateCRC32(0xffffffff, image, 8)) return false;
  value = candidate;
  return true;
}
}

bool PolicyStore::begin(FILESYSTEM* fs) {
  fs_ = fs;
  healthy_ = present_ = false;
  value_ = {1, 0, 1};
  if (!fs_) return false;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!ContactFileTransaction::recover(fs_, Path, filePresence<FILESYSTEM>)) return false;
#endif
  bool present = false;
  if (!filePresence(fs_, Path, present)) return false;
  if (!present) {
    healthy_ = true;
    value_ = {};
    return true;
  }
  uint8_t image[ImageSize];
  const auto result = storage::readFixedSettingsFileResult(fs_, Path, image, sizeof(image));
  if (result == storage::FixedSettingsRead::Unreadable) return false;
  healthy_ = true;
  if (result != storage::FixedSettingsRead::Complete || !decode(image, value_)) return false;
  present_ = true;
  return true;
}

bool PolicyStore::save(const Policy& value) {
  if (!fs_ || !healthy_ || !validChoice(value)) return false;
  if (present_ && value == value_) return true;
  uint8_t image[ImageSize];
  encode(image, value);
#if defined(NRF52_PLATFORM)
  const auto outcome = storage::writeCheckedSettingsFile(fs_, Path, image, sizeof(image));
  if (outcome == AtomicFileWriter::Outcome::Indeterminate) healthy_ = false;
  if (outcome != AtomicFileWriter::Outcome::Committed) return false;
#elif defined(STM32_PLATFORM)
  AtomicFileWriter writer(fs_, Path);
  const bool wrote = writer && writer.write(image, sizeof(image)) == sizeof(image);
  const bool saved = writer.commit(wrote);
  if (writer.outcome() == AtomicFileWriter::Outcome::Indeterminate) healthy_ = false;
  if (!saved) return false;
#else
  ContactFileTransaction writer(fs_, Path, filePresence<FILESYSTEM>);
  if (!writer || writer.write(image, sizeof(image)) != sizeof(image)) {
    if (writer.outcome() == ContactFileTransaction::Outcome::Indeterminate) healthy_ = false;
    return false;
  }
  writer.commit();
  if (writer.outcome() == ContactFileTransaction::Outcome::NotCommitted) return false;
#endif
#if !defined(NRF52_PLATFORM)
  uint8_t verified[ImageSize];
  healthy_ = storage::readFixedSettingsFile(fs_, Path, verified, sizeof(verified))
      && memcmp(image, verified, sizeof(image)) == 0;
  if (!healthy_) return false;
#endif
  value_ = value;
  present_ = true;
  return true;
}

}} // namespace mesh::wireless
