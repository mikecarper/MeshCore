#pragma once

// Optional lab-only evidence: read the S3 instruction-cache miss counter at
// the existing profile timing boundaries. Never clear shared hardware counters.
// This counter includes both CPUs; it is not a per-task attribution mechanism.
#if defined(MESH_HIL_ESP_CACHE_TIMING) && MESH_HIL_ESP_CACHE_TIMING
#include <stdint.h>
#include <soc/extmem_reg.h>
#include <soc/soc.h>
#if !defined(CONFIG_IDF_TARGET_ESP32S3) || !CONFIG_IDF_TARGET_ESP32S3
#error "Radio cache timing is qualified only on ESP32-S3"
#endif
namespace mesh { namespace radio_cache_timing {
struct Capture {
  uint32_t count = 0, minimum = UINT32_MAX, maximum = 0, last = 0;
  uint64_t misses = 0;
  uint32_t start = 0;
};
inline Capture& capture() { static Capture value; return value; }
inline void begin() { capture().start = REG_READ(EXTMEM_IBUS_ACS_MISS_CNT_REG); }
inline void finish(bool success) {
  const uint32_t end = REG_READ(EXTMEM_IBUS_ACS_MISS_CNT_REG);
  auto& value = capture();
  if (!success) return;
  const uint32_t misses = end - value.start;
  ++value.count;
  value.misses += misses;
  value.last = misses;
  if (misses < value.minimum) value.minimum = misses;
  if (misses > value.maximum) value.maximum = misses;
}
} }
#endif
