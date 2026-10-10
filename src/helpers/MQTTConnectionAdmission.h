#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Connection admission uses DMA-capable internal heap rather than total heap:
// PSRAM-backed TLS still needs internal AES bounce buffers and WiFi allocations.
// These are headroom bounds, not a guarantee that a later allocation succeeds.
namespace MQTTConnectionAdmission {

static const size_t kDmaReserveBytes = 16384;
static const size_t kDmaLargestBytes = 4096;
// A stopped SDK client needs a new internal task stack/TCB. First starts also
// allocate receive/transmit/reassembly buffers and SDK transport/config objects
// after admission. Budget that ordinary allocation separately from AES headroom.
// Pin the actual SDK stack/priority in the optimizer rather than relying on an
// SDK default or confusing this per-client task with the MQTTBridge worker.
static const size_t kClientTaskStackBytes = 6144;
static const int kClientTaskPriority = 5;
static const size_t kColdStartAllowanceBytes = 16384;
static const size_t kColdStartLargestBytes = 8192;
// SDK stop retains buffers/configuration but releases its task. Restart only
// needs the pinned 6 KiB stack plus TCB/allocator headroom, not another SDK init.
static const size_t kRestartAllowanceBytes = 8192;
static const size_t kRestartLargestBytes = 8192;
static const size_t kTlsInternalFreeBytes = 61440;
static const size_t kTlsRecordAllocationBytes = 17408;

enum class ClientMemoryState : uint8_t {
  Uninitialized,
  Stopped,
  Started,
};

static inline bool canStart(bool psram, bool tls, ClientMemoryState state,
                            size_t dma_free, size_t dma_largest) {
  size_t allocation_allowance;
  size_t largest_required;
  switch (state) {
    case ClientMemoryState::Uninitialized:
      allocation_allowance = kColdStartAllowanceBytes;
      largest_required = kColdStartLargestBytes;
      break;
    case ClientMemoryState::Stopped:
      allocation_allowance = kRestartAllowanceBytes;
      largest_required = kRestartLargestBytes;
      break;
    case ClientMemoryState::Started:
      allocation_allowance = 0;
      largest_required = kDmaLargestBytes;
      break;
    default:
      return false;
  }
  const size_t free_required = kDmaReserveBytes + allocation_allowance;
  if (dma_free < free_required || dma_largest < largest_required) {
    return false;
  }
  return !tls || psram ||
         (dma_free >= kTlsInternalFreeBytes &&
          dma_largest >= kTlsRecordAllocationBytes);
}

// Charge a PSRAM allocation's internal fallback against the DMA reserve even
// when the internal allocator might find a block with fewer capabilities.
static inline bool canFallback(size_t requested, size_t dma_free,
                               size_t dma_largest) {
  return requested > 0 && requested <= dma_largest &&
         dma_free >= kDmaReserveBytes &&
         requested <= dma_free - kDmaReserveBytes;
}

static inline bool checkedCallocSize(size_t count, size_t size, size_t& total) {
  total = 0;
  if (count == 0 || size == 0 || count > SIZE_MAX / size) return false;
  total = count * size;
  return true;
}

// An explicit scheme controls the transport even on an unusual port. Bare
// custom hosts match setupSlot's port-based URI construction.
static inline bool requiresTls(const char* uri, uint16_t port) {
  if (uri != nullptr) {
    if (strncmp(uri, "mqtts://", 8) == 0 || strncmp(uri, "wss://", 6) == 0) {
      return true;
    }
    if (strncmp(uri, "mqtt://", 7) == 0 || strncmp(uri, "ws://", 5) == 0) {
      return false;
    }
  }
  return port == 443 || port == 8883;
}

} // namespace MQTTConnectionAdmission
