#pragma once

#include <RadioProfiles.h>
#include <string.h>

namespace mesh { namespace ethernet {

struct Settings {
  bool enabled = false;
  bool configured = false;
  RadioProfileParams radio;
  Settings() { radio.protocol = RadioProfileProtocol::Ethernet; }
};
static constexpr size_t SettingsSize = 32;

inline uint32_t settingsChecksum(const uint8_t* bytes, size_t length) {
  uint32_t crc = 0xffffffff;
  while (length--) {
    crc ^= *bytes++;
    for (unsigned i = 0; i < 8; ++i) crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
  }
  return crc;
}

inline bool validSettings(const Settings& value) {
  return (!value.enabled || value.configured)
      && (!value.configured || (value.radio.protocol == RadioProfileProtocol::Ethernet
          && RadioProfiles::valid(value.radio) && value.radio.preamble >= 8
          && RadioProfiles::safePreamble(value.radio, value.radio.preamble)));
}

inline void encodeSettings(uint8_t image[SettingsSize], const Settings& value) {
  memset(image, 0, SettingsSize);
  memcpy(image, "ETH1", 4);
  image[4] = value.enabled; image[5] = value.configured;
  image[6] = value.radio.sf; image[7] = value.radio.cr;
  memcpy(image + 8, &value.radio.freq, 4);
  memcpy(image + 12, &value.radio.bw, 4);
  memcpy(image + 16, &value.radio.preamble, 2);
  const uint32_t crc = settingsChecksum(image, SettingsSize - 4);
  memcpy(image + SettingsSize - 4, &crc, 4);
}

inline bool decodeSettings(const uint8_t* image, size_t length, Settings& value) {
  if (!image || length != SettingsSize || memcmp(image, "ETH1", 4)
      || image[4] > 1 || image[5] > 1) return false;
  uint32_t crc;
  memcpy(&crc, image + SettingsSize - 4, 4);
  if (crc != settingsChecksum(image, SettingsSize - 4)) return false;
  for (size_t i = 18; i < SettingsSize - 4; ++i) if (image[i]) return false;
  Settings loaded;
  loaded.enabled = image[4]; loaded.configured = image[5];
  loaded.radio.sf = image[6]; loaded.radio.cr = image[7];
  memcpy(&loaded.radio.freq, image + 8, 4);
  memcpy(&loaded.radio.bw, image + 12, 4);
  memcpy(&loaded.radio.preamble, image + 16, 2);
  if (!validSettings(loaded)) return false;
  value = loaded;
  return true;
}

} } // namespace mesh::ethernet
