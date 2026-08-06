#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {
namespace lr2021 {

static constexpr uint8_t MAX_SIDE_DETECTORS = 3;
static constexpr uint8_t STORED_SIDE_DETECTOR_BYTES = MAX_SIDE_DETECTORS + 1;

// Parse the CLI's comma-separated SF list without copying it into CommonCLI's
// shared scratch buffer. An empty string deliberately means "disable".
inline bool parseSideDetectorSFList(const char* text,
                                    uint8_t (&side_sfs)[STORED_SIDE_DETECTOR_BYTES],
                                    uint8_t& num) {
  memset(side_sfs, 0, sizeof(side_sfs));
  num = 0;
  if (text == nullptr) return false;
  if (*text == '\0') return true;

  const char* cursor = text;
  while (*cursor != '\0') {
    if (num >= MAX_SIDE_DETECTORS) return false;

    uint16_t value = 0;
    uint8_t digits = 0;
    while (*cursor >= '0' && *cursor <= '9') {
      value = static_cast<uint16_t>(value * 10U + static_cast<uint8_t>(*cursor - '0'));
      if (value > 12U) return false;
      cursor++;
      digits++;
    }
    if (digits == 0 || value < 5U) return false;
    side_sfs[num++] = static_cast<uint8_t>(value);

    if (*cursor == '\0') break;
    if (*cursor != ',') return false;
    cursor++;
    if (*cursor == '\0') return false;  // reject a trailing comma
  }

  side_sfs[num] = 0;
  return true;
}

inline bool storedSideDetectorCount(
    const uint8_t (&stored)[STORED_SIDE_DETECTOR_BYTES], uint8_t& num) {
  for (uint8_t i = 0; i < STORED_SIDE_DETECTOR_BYTES; i++) {
    if (stored[i] == 0) {
      num = i;
      return i <= MAX_SIDE_DETECTORS;
    }
  }
  num = 0;
  return false;
}

inline bool validateSideDetectorSFs(const uint8_t* side_sfs, uint8_t num,
                                    uint8_t primary_sf, float bandwidth_khz) {
  if (num > MAX_SIDE_DETECTORS || (num > 0 && side_sfs == nullptr)) return false;
  if (num == 0) return true;
  if (primary_sf < 5 || primary_sf > 12 || bandwidth_khz <= 0.0f) return false;

  // These limits mirror LR2021::setSideDetector(). A primary SF of 10 or
  // greater permits two side detectors, not one.
  if ((primary_sf >= 10 || bandwidth_khz > 500.0f) && num > 2) return false;

  uint32_t detector_factor_sum = 0;
  for (uint8_t i = 0; i < num; i++) {
    const uint8_t sf = side_sfs[i];
    if (sf < 5 || sf > 12 || sf <= primary_sf || sf > primary_sf + 4) return false;
    for (uint8_t j = 0; j < i; j++) {
      if (side_sfs[j] == sf) return false;
    }
    detector_factor_sum += 10U + static_cast<uint32_t>(((sf - 5U) >> 1U) << 1U);
  }

  return static_cast<float>(detector_factor_sum) * bandwidth_khz < 32000.0f;
}

inline bool sideDetectorLDRO(uint8_t sf, float bandwidth_khz) {
  if (sf < 5 || sf > 12 || bandwidth_khz <= 0.0f) return false;
  return static_cast<float>(uint32_t(1) << sf) / bandwidth_khz >= 16.0f;
}

}  // namespace lr2021
}  // namespace mesh
