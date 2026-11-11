#include "AGCSettings.h"
#if MESH_ENABLE_OLD_AGC
#include "CheckedSettingsFile.h"
#include "PersistentStoreFormat.h"
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include "AtomicFileWriter.h"
#else
#include "ContactFileTransaction.h"
#endif

namespace mesh {
namespace {
FILESYSTEM* agc_fs = nullptr;
constexpr const char* agc_path = "/agc_prefs";
bool readSetting(uint32_t& setting) {
  uint8_t data[12];
  if (!storage::readFixedSettingsFile(agc_fs, agc_path, data, sizeof(data))
      || memcmp(data, "AGC1", 4) != 0
      || storage::readLE32(data + 8) != storage::updateCRC32(0xffffffffUL, data, 8)) return false;
  setting = storage::readLE32(data + 4);
  return validOldAGCInterval(setting);
}
bool saveSetting(uint32_t setting) {
  if (!agc_fs) return false;
  uint32_t previous;
  if (readSetting(previous) && previous == setting) return true;
  uint8_t data[12];
  memcpy(data, "AGC1", 4);
  storage::writeLE32(data + 4, setting);
  storage::writeLE32(data + 8, storage::updateCRC32(0xffffffffUL, data, 8));
#if defined(NRF52_PLATFORM)
  return storage::writeCheckedSettingsFile(agc_fs, agc_path, data, sizeof(data))
      == AtomicFileWriter::Outcome::Committed;
#else
#if defined(STM32_PLATFORM)
  AtomicFileWriter file(agc_fs, agc_path);
#else
  ContactFileTransaction file(agc_fs, agc_path);
#endif
  if (!file || file.write(data, sizeof(data)) != sizeof(data) || !file.commit()) return false;
  uint32_t verified;
  return readSetting(verified) && verified == setting;
#endif
}
} // namespace

bool beginAGCSettings(Radio* radio, FILESYSTEM* fs) {
  agc_fs = fs;
  if (!radio || !radio->supportsOldAGC()) return true;
  // An unreadable saved override must not silently re-enable legacy resets.
  radio->setOldAGCInterval(0);
  if (!fs) return false;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!ContactFileTransaction::recover(fs, agc_path)) return false;
#endif
  uint32_t setting = AGC_OLD_AUTO;
  if (fs->exists(agc_path) && !readSetting(setting)) return false;
  return radio->setOldAGCInterval(setting);
}

bool setStoredOldAGCInterval(Radio* radio, const char* value, char* reply, size_t capacity) {
  const uint32_t previous = radio ? radio->getOldAGCInterval() : 0;
  if (!applyOldAGCIntervalValue(radio, value, reply, capacity)) return false;
  if (!saveSetting(radio->getOldAGCInterval())) {
    const bool restored = radio->setOldAGCInterval(previous);
    cli::formatError(reply, capacity, restored ? "save unconfirmed; reboot may differ"
        : "save unconfirmed; runtime restore failed; reboot may differ");
    return false;
  }
  const size_t used = strlen(reply);
  if (used < capacity) snprintf(reply + used, capacity - used, " (saved)");
  return true;
}
} // namespace mesh
#endif
