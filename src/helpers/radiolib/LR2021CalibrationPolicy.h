#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include "LR2021Band.h"
#include "LR2021FrequencyCorrection.h"

namespace mesh { namespace lr2021 {

// CalibFE has three stored frequency/path slots. Replacing only slot 1 can
// leave an enabled second profile without a suitable frontend calibration.
// Accept physical frequencies only: the caller applies oscillator correction
// once, before creating this plan. Match Semtech's upward 4 MHz encoding.
struct FrontEndCalibrationPlan {
  uint16_t frequencies[3] = {};
  uint8_t count = 0;

  bool add(float hardware_mhz) {
    if (!isfinite(hardware_mhz) || hardware_mhz <= 0.0f || hardware_mhz > 2600.0f)
      return false;
    uint16_t encoded = static_cast<uint16_t>(ceilf(hardware_mhz / 4.0f));
    if (isHighBand(hardware_mhz)) encoded |= 0x8000U;
    for (uint8_t i = 0; i < count; ++i)
      if (frequencies[i] == encoded) return true;
    if (count == 3) return false;
    frequencies[count++] = encoded;
    return true;
  }
};

constexpr uint8_t XoscTrimMaximum = 47;

struct XoscTrimSelection {
  bool enabled = false;
  uint8_t a = 0;
  uint8_t b = 0;
  int32_t correction_ppb = 0;
};

inline bool sameCalibration(const XoscTrimSelection& a, const XoscTrimSelection& b) {
  return a.enabled == b.enabled && (!a.enabled || (a.a == b.a && a.b == b.b))
      && a.correction_ppb == b.correction_ppb;
}

inline bool validXoscTrim(uint8_t a, uint8_t b) {
  return a <= XoscTrimMaximum && b <= XoscTrimMaximum;
}

// GET_LORA_PACKET_STATUS returns a big-endian signed 24-bit value in Hz.
inline int32_t decodeFrequencyErrorHz(const uint8_t* bytes) {
  const uint32_t raw = (uint32_t(bytes[0]) << 16) | (uint32_t(bytes[1]) << 8) | bytes[2];
  return (raw & 0x800000U) ? int32_t(raw) - 0x1000000L : int32_t(raw);
}

inline bool sameXoscTrim(const XoscTrimSelection& a, const XoscTrimSelection& b) {
  return a.enabled == b.enabled && (!a.enabled || (a.a == b.a && a.b == b.b));
}

// Parse decimal capacitor codes, not a guessed frequency correction in Hz.
inline bool parseXoscTrim(const char* value, XoscTrimSelection& selection) {
  if (!value) return false;
  if (!strcmp(value, "default")) {
    selection = XoscTrimSelection{};
    return true;
  }
  const char* cursor = value;
  unsigned pair[2] = {};
  for (unsigned field = 0; field < 2; ++field) {
    if (*cursor < '0' || *cursor > '9') return false;
    do {
      pair[field] = pair[field] * 10 + unsigned(*cursor++ - '0');
      if (pair[field] > XoscTrimMaximum) return false;
    } while (*cursor >= '0' && *cursor <= '9');
    if (field == 0 && *cursor == 0) {
      pair[1] = pair[0];
      break;
    }
    if (field == 0) {
      if (*cursor++ != ',') return false;
    } else if (*cursor != 0) return false;
  }
  selection.enabled = true;
  selection.a = static_cast<uint8_t>(pair[0]);
  selection.b = static_cast<uint8_t>(pair[1]);
  return true;
}

constexpr size_t XoscTrimLegacyRecordSize = 8;
inline void encodeLegacyXoscTrim(const XoscTrimSelection& selection,
                                 uint8_t (&record)[XoscTrimLegacyRecordSize]) {
  record[0] = 'X'; record[1] = 'T'; record[2] = 1;
  record[3] = selection.enabled ? 1 : 0;
  record[4] = selection.enabled ? selection.a : 0;
  record[5] = selection.enabled ? selection.b : 0;
  record[6] = record[7] = 0;
  for (unsigned i = 0; i < 7; ++i) record[7] ^= record[i];
}
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
constexpr size_t XoscTrimRecordSize = 12;
#else
constexpr size_t XoscTrimRecordSize = XoscTrimLegacyRecordSize;
#endif

inline void encodeXoscTrim(const XoscTrimSelection& selection,
                          uint8_t (&record)[XoscTrimRecordSize]) {
  record[0] = 'X'; record[1] = 'T'; record[2] = XoscTrimRecordSize == 12 ? 2 : 1;
  record[3] = selection.enabled ? 1 : 0;
  record[4] = selection.enabled ? selection.a : 0;
  record[5] = selection.enabled ? selection.b : 0;
  memset(record + 6, 0, XoscTrimRecordSize - 6);
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  const uint32_t bits = static_cast<uint32_t>(selection.correction_ppb);
  for (unsigned i = 0; i < 4; ++i) record[6+i] = static_cast<uint8_t>(bits >> (i*8));
#endif
  for (size_t i = 0; i < XoscTrimRecordSize - 1; ++i) record[XoscTrimRecordSize-1] ^= record[i];
}

inline bool decodeXoscTrim(const uint8_t* record, size_t length,
                          XoscTrimSelection& selection) {
  if (!record || (length != XoscTrimRecordSize && length != XoscTrimLegacyRecordSize) || record[3] > 1
      || !validXoscTrim(record[4], record[5])) return false;
  XoscTrimSelection candidate;
  candidate.enabled = record[3] != 0;
  candidate.a = record[4];
  candidate.b = record[5];
  if (record[0] != 'X' || record[1] != 'T' || record[2] != (length == 12 ? 2 : 1)) return false;
  uint8_t checksum = 0;
  for (size_t i = 0; i < length - 1; ++i) checksum ^= record[i];
  if (checksum != record[length-1] || (!candidate.enabled && (candidate.a || candidate.b))) return false;
  if (length == XoscTrimLegacyRecordSize) {
    if (record[6]) return false;
  } else {
    if (record[10]) return false;
    const uint32_t bits = uint32_t(record[6]) | (uint32_t(record[7]) << 8)
        | (uint32_t(record[8]) << 16) | (uint32_t(record[9]) << 24);
    candidate.correction_ppb = bits <= INT32_MAX ? static_cast<int32_t>(bits)
        : static_cast<int32_t>(static_cast<int64_t>(bits) - 0x100000000LL);
    if (!validFrequencyCorrection(candidate.correction_ppb)) return false;
  }
  selection = candidate;
  return true;
}

}}  // namespace mesh::lr2021
