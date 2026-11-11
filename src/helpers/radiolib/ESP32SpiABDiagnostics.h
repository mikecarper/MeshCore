#pragma once

// Private, read-only instrumentation for same-image SPI clock comparisons.
// Normal firmware omits both the capture and these CLI commands.
#if defined(MESH_HIL_ESP_SPI_AB) && MESH_HIL_ESP_SPI_AB
#if !defined(ESP32_PLATFORM) || !defined(MESH_HIL_PROFILE_TIMING) || !MESH_HIL_PROFILE_TIMING
#error "ESP SPI A/B diagnostics require ESP32 and profile timing"
#endif

#include <Arduino.h>
#include "ProfileSwitchTiming.h"
#include <stdio.h>
#include <string.h>

namespace mesh { namespace esp_spiab {

struct ClockCapture {
  uint32_t divider = 0, hz = 0, changes = 0;
};

inline ClockCapture& clockCapture() {
  static ClockCapture capture;
  return capture;
}

inline void recordClock(uint32_t divider, uint32_t hz) {
  auto& capture = clockCapture();
  capture.divider = divider;
  capture.hz = hz;
  ++capture.changes;
}

// All callers and profile retunes run in the application thread. Copy each
// bounded page before formatting; there is no ISR writer and no extra radio
// transaction, lock, reset, or statistics mutation here.
inline bool handle(const char* suffix, const char* args, int verb,
                   char* reply, size_t capacity) {
#if defined(MESH_HIL_ESP_CACHE_TIMING) && MESH_HIL_ESP_CACHE_TIMING
  if (!strcmp(suffix, "cache")) {
    if (verb != 0 || *args) {
      snprintf(reply, capacity, "Error: get radio.fast.cache (read-only)");
      return true;
    }
    const auto snapshot = mesh::radio_cache_timing::capture();
    snprintf(reply, capacity, "> count=%lu sum_hi=%lu sum_lo=%lu min=%lu max=%lu last=%lu",
        (unsigned long)snapshot.count, (unsigned long)(snapshot.misses >> 32),
        (unsigned long)uint32_t(snapshot.misses), (unsigned long)snapshot.minimum,
        (unsigned long)snapshot.maximum, (unsigned long)snapshot.last);
    return true;
  }
#endif
  const bool hw = !strcmp(suffix, "hw"), info = !strcmp(suffix, "hil");
  const bool timing = !strcmp(suffix, "time"), hist = !strcmp(suffix, "hist");
  if (!hw && !info && !timing && !hist) return false;
  if (verb != 0) {
    snprintf(reply, capacity, "Error: SPI A/B diagnostics are read-only");
    return true;
  }
  unsigned direction = 0, stage = 0, page = 0;
  char extra;
  if (((hw || info) && *args)
      || (timing && (sscanf(args, "%u %u %c", &direction, &stage, &extra) != 2
          || direction > 1 || stage >= profiletiming::StageCount))
      || (hist && (sscanf(args, "%u %u %u %c", &direction, &stage, &page, &extra) != 3
          || direction > 1 || stage >= profiletiming::StageCount
          || page >= profiletiming::HistogramCount / 8))) {
    snprintf(reply, capacity, "Error: get radio.fast.hw|hil; time D S; hist D S P");
    return true;
  }
  if (hw) {
    const ClockCapture snapshot = clockCapture();
    snprintf(reply, capacity, "> divider=0x%08lX hz=%lu changes=%lu",
        (unsigned long)snapshot.divider, (unsigned long)snapshot.hz,
        (unsigned long)snapshot.changes);
    return true;
  }
  const auto& capture = profiletiming::mesh_hil_profile_timing;
  if (info) {
    snprintf(reply, capacity, "> magic=0x%08lX ver=%lu bytes=%lu seq=%lu stages=%lu bins=%lu width=%lu",
        (unsigned long)capture.magic, (unsigned long)capture.version,
        (unsigned long)capture.size_bytes, (unsigned long)capture.sequence,
        (unsigned long)capture.stage_count, (unsigned long)capture.histogram_count,
        (unsigned long)capture.histogram_width_us);
  } else {
    const auto& stats = capture.direction[direction].stage[stage];
    if (timing) {
      const uint32_t count = stats.count, high = stats.sum_high_us, low = stats.sum_low_us;
      const uint32_t minimum = stats.min_us, maximum = stats.max_us, last = stats.last_us;
      snprintf(reply, capacity, "> d=%u s=%u count=%lu sum_hi=%lu sum_lo=%lu min=%lu max=%lu last=%lu",
          direction, stage, (unsigned long)count, (unsigned long)high,
          (unsigned long)low, (unsigned long)minimum, (unsigned long)maximum,
          (unsigned long)last);
    } else {
      uint32_t bins[8];
      for (unsigned i = 0; i < 8; ++i) bins[i] = stats.histogram[page * 8 + i];
      snprintf(reply, capacity, "> d=%u s=%u p=%u bins=%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu",
          direction, stage, page, (unsigned long)bins[0], (unsigned long)bins[1],
          (unsigned long)bins[2], (unsigned long)bins[3], (unsigned long)bins[4],
          (unsigned long)bins[5], (unsigned long)bins[6], (unsigned long)bins[7]);
    }
  }
  return true;
}

} } // namespace mesh::esp_spiab
#endif
