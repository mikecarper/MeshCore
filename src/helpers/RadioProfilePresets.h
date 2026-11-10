#pragma once
#include <stdint.h>
#include <string.h>

namespace mesh { namespace cli {

inline bool isRadio2Preset(const char* name) {
  return name && (!strcmp(name, "mediamesh") || !strcmp(name, "mediamesh-range"));
}

#if defined(STM32_PLATFORM)
// Keep software-float conversion shared by preset selection and matching.
__attribute__((noinline, noclone))
#endif
inline uint32_t mediaMeshFrequencyKHz(float frequency) {
  // Reject non-finite/out-of-band values before converting to an integer.
  // All named centers use whole kHz; rounding tolerates ordinary readback.
  return frequency >= 400.0f && frequency <= 930.0f
      ? uint32_t(frequency * 1000.0f + 0.5f) : 0;
}

// Match the community primary presets, rather than guessing country from an
// arbitrary 915 MHz frequency. In particular, SoCal retains the US channel.
enum class MediaMeshRegion : uint8_t { Unknown, US, EU868, Shared915, EU433 };

inline MediaMeshRegion radio2PresetRegion(float primary) {
  const uint32_t frequency = mediaMeshFrequencyKHz(primary);
  if (primary >= 869.400f && primary <= 869.650f) return MediaMeshRegion::EU868;
  switch (frequency) {
    case 910525: case 927875: return MediaMeshRegion::US;
    case 915075: case 915800: case 916575: case 917375:
    case 920250: case 921000: case 923125: return MediaMeshRegion::Shared915;
    case 433375: case 433650: return MediaMeshRegion::EU433;
    default: return MediaMeshRegion::Unknown;
  }
}

// Saved profiles keep their forwarding scope even after primary changes.
inline bool isMediaMeshPreset(float frequency, float bandwidth, uint8_t sf, uint8_t cr) {
  switch (mediaMeshFrequencyKHz(frequency)) {
    case 869525:
      return (bandwidth == 125 || bandwidth == 62.5f) && sf == 5 && cr == 7;
    case 911150: case 922500: case 434250:
      return (bandwidth == 250 || bandwidth == 125) && sf == 7 && cr == 5;
    default: return false;
  }
}

} }
