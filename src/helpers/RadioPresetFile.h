#pragma once

// Flash storage for the radio preset table (/presets). The image itself is
// CRC-protected by RadioPresetTable; this layer only moves bytes. Saves are
// power-loss safe: mesh::AtomicFileWriter on LittleFS (nRF52/STM32), and the
// .bak-preserving ContactFileTransaction on SPIFFS/Pico LittleFS (ESP32/RP2040),
// whose rename gap is recovered before every load.

#include <stddef.h>
#include <stdint.h>

#include <helpers/IdentityStore.h>
#include <helpers/FilePresence.h>
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include <helpers/AtomicFileWriter.h>
#elif defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
#include <helpers/ContactFileTransaction.h>
#endif

namespace mesh {

static constexpr const char* RADIO_PRESET_FILE = "/presets";

// Reads the whole file into buf. False when missing, unreadable or larger
// than cap; the caller then starts from a clean table.
inline bool loadRadioPresetFile(FILESYSTEM* fs, uint8_t* buf, size_t cap, size_t& len) {
  len = 0;
  if (!fs || !buf || cap == 0) return false;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  ContactFileTransaction::recover(fs, RADIO_PRESET_FILE, filePresence<FILESYSTEM>);
#endif
  bool present = false;
  if (!filePresence(fs, RADIO_PRESET_FILE, present) || !present) return false;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  File file(*fs);
  if (!file.open(RADIO_PRESET_FILE, FILE_O_READ)) return false;
#else
  File file = fs->open(RADIO_PRESET_FILE, "r");
  if (!file) return false;
#endif
  const size_t size = file.size();
  const bool ok = size > 0 && size <= cap && int(file.read(buf, size)) == int(size);
  file.close();
  if (!ok) return false;
  len = size;
  return true;
}

inline bool saveRadioPresetFile(FILESYSTEM* fs, const uint8_t* buf, size_t len) {
  if (!fs || !buf || len == 0) return false;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  AtomicFileWriter writer(fs, RADIO_PRESET_FILE);
#elif defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  ContactFileTransaction writer(fs, RADIO_PRESET_FILE, filePresence<FILESYSTEM>);
#else
  return false;
#endif
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM) || defined(ESP32_PLATFORM) \
    || defined(RP2040_PLATFORM)
  return writer && writer.write(buf, len) == len && writer.commit();
#endif
}

}  // namespace mesh
