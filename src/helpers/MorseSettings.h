#pragma once

#include <helpers/IdentityStore.h>
#include <helpers/MorseTx.h>
#include <helpers/PersistentStoreFormat.h>
#include <stddef.h>
#include <stdint.h>

namespace mesh {

struct MorseSettings {
  // A distinct TX-only channel. Neither packet-radio profile is modified.
  uint32_t frequency_khz = 902100;
  uint8_t wpm = MorseTx::DefaultWpm;
  float frequencyMHz() const { return float(frequency_khz) / 1000.0f; }
};

constexpr uint32_t MorseMinFrequencyKHz = 100000;
constexpr uint32_t MorseMaxFrequencyKHz = 1000000;
constexpr uint8_t MorseMinWpm = MorseTx::MinWpm;
constexpr uint8_t MorseMaxWpm = MorseTx::MaxWpm;
constexpr size_t MorseSettingsImageSize = 12;

inline bool validMorseSettings(const MorseSettings& prefs) {
  return prefs.frequency_khz >= MorseMinFrequencyKHz
      && prefs.frequency_khz <= MorseMaxFrequencyKHz
      && prefs.wpm >= MorseMinWpm && prefs.wpm <= MorseMaxWpm;
}

inline uint32_t morseSettingsCRC(const uint8_t* data, size_t size) {
  return storage::updateCRC32(0xffffffffUL, data, size);
}

inline void encodeMorseSettings(const MorseSettings& prefs,
                                uint8_t (&image)[MorseSettingsImageSize]) {
  image[0] = 'M'; image[1] = 'C'; image[2] = 1; image[3] = prefs.wpm;
  for (unsigned i = 0; i < 4; ++i)
    image[4 + i] = uint8_t(prefs.frequency_khz >> (i * 8));
  const uint32_t crc = morseSettingsCRC(image, 8);
  for (unsigned i = 0; i < 4; ++i) image[8 + i] = uint8_t(crc >> (i * 8));
}

inline bool decodeMorseSettings(const uint8_t* image, size_t size,
                                MorseSettings& prefs) {
  if (!image || size != MorseSettingsImageSize || image[0] != 'M'
      || image[1] != 'C' || image[2] != 1) return false;
  uint32_t frequency = 0, crc = 0;
  for (unsigned i = 0; i < 4; ++i) {
    frequency |= uint32_t(image[4 + i]) << (i * 8);
    crc |= uint32_t(image[8 + i]) << (i * 8);
  }
  MorseSettings candidate;
  candidate.frequency_khz = frequency;
  candidate.wpm = image[3];
  if (crc != morseSettingsCRC(image, 8) || !validMorseSettings(candidate))
    return false;
  prefs = candidate;
  return true;
}

// Independent checked storage leaves legacy node/Companion layouts intact.
// Defaults and saved settings never begin transmitting automatically.
bool loadMorseSettings(FILESYSTEM* fs);
const MorseSettings& morseSettings();
// A missing fresh store is ready with defaults. Unreadable/corrupt saved
// frequency must not silently become the default transmit frequency.
bool morseSettingsUsable();
bool saveMorseSettings(const MorseSettings& candidate);

}  // namespace mesh
