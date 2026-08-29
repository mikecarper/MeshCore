#pragma once

#include <stdint.h>

namespace mesh {
namespace ota {

// Physical placement of one ESP32 flash-store container. Keep this independent
// of ESP-IDF so reopen/discard geometry can be tested on the native host.
struct MotaEsp32StageLayout {
  uint32_t total = 0;
  uint32_t meta_span = 0;
  uint32_t meta_flush = 0;
  uint32_t write_start = 0;
  uint32_t meta_part = 0;
  uint32_t pay_log0 = 0;
  uint32_t pay_part0 = 0;
};

inline bool mota_esp32_align_up(uint32_t value, uint32_t alignment,
                                uint32_t& result) {
  if (alignment == 0 || value > UINT32_MAX - (alignment - 1u)) return false;
  result = ((value + alignment - 1u) / alignment) * alignment;
  return true;
}

inline uint32_t mota_esp32_align_down(uint32_t value,
                                      uint32_t alignment) {
  return alignment ? (value / alignment) * alignment : 0u;
}

inline bool mota_esp32_stage_layout(
    uint32_t partition_size, uint32_t sector_size, uint32_t meta_capacity,
    bool is_full, uint32_t image_size, uint32_t meta_bytes,
    uint32_t payload_size, MotaEsp32StageLayout& out) {
  if (sector_size == 0 || partition_size < sector_size ||
      partition_size % sector_size != 0) {
    return false;
  }
  const uint64_t total64 =
      (uint64_t)meta_bytes + (uint64_t)payload_size + 5u;
  if (total64 > UINT32_MAX) return false;

  MotaEsp32StageLayout layout;
  layout.total = (uint32_t)total64;
  if (is_full) {
    // A FULL payload is the final application image, byte for byte. Reject a
    // malformed manifest before begin() can erase or stream anything, and
    // prove that the complete logical container fits the inactive partition.
    if (payload_size != image_size || layout.total > partition_size) {
      return false;
    }
    layout.meta_span = meta_bytes;
    if (meta_bytes > UINT32_MAX - 5u ||
        !mota_esp32_align_up(meta_bytes + 5u, sector_size,
                             layout.meta_flush) ||
        layout.meta_flush > meta_capacity ||
        layout.meta_flush > partition_size) {
      return false;
    }
    layout.meta_part = mota_esp32_align_down(
        partition_size - layout.meta_flush, sector_size);
    layout.pay_log0 = meta_bytes;
    layout.pay_part0 = 0;
    layout.write_start = 0;
    if (image_size > layout.meta_part) return false;
  } else {
    if (!mota_esp32_align_up(meta_bytes, sector_size,
                             layout.meta_span) ||
        layout.meta_span > meta_capacity || layout.total > partition_size) {
      return false;
    }
    layout.meta_flush = layout.meta_span;
    layout.write_start = mota_esp32_align_down(
        partition_size - layout.total, sector_size);
    layout.meta_part = layout.write_start;
    layout.pay_log0 = layout.meta_span;
    if (layout.write_start > UINT32_MAX - layout.meta_span) return false;
    layout.pay_part0 = layout.write_start + layout.meta_span;
    if (image_size > layout.write_start) return false;
  }
  out = layout;
  return true;
}

// The probe separates ordinary non-candidates from partition I/O failure:
// true/false is I/O success, while `reopenable` says whether this sector holds
// a header that the store's reopen path would adopt.
typedef bool (*MotaEsp32ProbeStagedHeader)(void* context, uint32_t offset,
                                           bool& reopenable);
typedef bool (*MotaEsp32InvalidateStagedHeader)(void* context,
                                                uint32_t offset);

inline bool mota_esp32_discard_staged_headers(
    uint32_t partition_size, uint32_t sector_size, void* context,
    MotaEsp32ProbeStagedHeader probe_header,
    MotaEsp32InvalidateStagedHeader invalidate_header,
    uint32_t* invalidated_count = nullptr) {
  if (invalidated_count) *invalidated_count = 0;
  if (sector_size == 0 || partition_size < sector_size ||
      partition_size % sector_size != 0 || !probe_header ||
      !invalidate_header) {
    return false;
  }

  bool ok = true;
  uint32_t count = 0;
  uint32_t offset = mota_esp32_align_down(
      partition_size - sector_size, sector_size);
  for (;;) {
    bool reopenable = false;
    if (!probe_header(context, offset, reopenable)) {
      ok = false;
    } else if (reopenable) {
      if (invalidate_header(context, offset)) {
        ++count;
      } else {
        ok = false;
      }
    }
    if (offset < sector_size) break;
    offset -= sector_size;
  }
  if (invalidated_count) *invalidated_count = count;
  return ok;
}

} // namespace ota
} // namespace mesh
