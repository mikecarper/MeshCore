#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

namespace mesh {

struct CompanionMemoryDiagnostics {
  uint32_t heap_free;
  uint32_t heap_min;
  uint32_t heap_max_alloc;
  uint32_t internal_free;
  uint32_t internal_max_alloc;
  uint32_t psram_free;
  uint32_t psram_total;
  int queue_used;
  int queue_capacity;
};

inline bool formatCompanionMemoryDiagnostics(
    char* output, size_t output_size,
    const CompanionMemoryDiagnostics& diagnostics) {
  if (output == nullptr || output_size == 0) return false;
  const int written = snprintf(
      output, output_size,
      "Heap free=%lu min=%lu max=%lu int=%lu/%lu PSRAM=%lu/%lu queue=%d/%d",
      (unsigned long)diagnostics.heap_free,
      (unsigned long)diagnostics.heap_min,
      (unsigned long)diagnostics.heap_max_alloc,
      (unsigned long)diagnostics.internal_free,
      (unsigned long)diagnostics.internal_max_alloc,
      (unsigned long)diagnostics.psram_free,
      (unsigned long)diagnostics.psram_total,
      diagnostics.queue_used, diagnostics.queue_capacity);
  return written >= 0 && static_cast<size_t>(written) < output_size;
}

}  // namespace mesh
