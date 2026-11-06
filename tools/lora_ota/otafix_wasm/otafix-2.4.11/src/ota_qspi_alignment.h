#ifndef OTA_QSPI_ALIGNMENT_H_
#define OTA_QSPI_ALIGNMENT_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define OTA_QSPI_DMA_ALIGNMENT 4u

typedef struct {
  uint32_t offset;
  uint32_t length;
  uint32_t prefix;
  uint32_t copy_length;
} ota_qspi_dma_window_t;

// Plan one EasyDMA transfer that covers the requested byte range. nRF52840
// QSPI requires the external address, RAM address, and transfer count to be
// word aligned. The caller supplies a word-aligned bounce buffer.
static inline bool ota_qspi_dma_window(uint32_t offset, uint32_t requested_length,
                                       uint32_t capacity, uint32_t bounce_size,
                                       ota_qspi_dma_window_t *window) {
  if (window == NULL || requested_length == 0 || bounce_size < OTA_QSPI_DMA_ALIGNMENT ||
      (bounce_size & (OTA_QSPI_DMA_ALIGNMENT - 1u)) != 0 ||
      (capacity & (OTA_QSPI_DMA_ALIGNMENT - 1u)) != 0 ||
      offset > capacity || requested_length > capacity - offset) {
    return false;
  }

  const uint32_t aligned_offset = offset & ~(OTA_QSPI_DMA_ALIGNMENT - 1u);
  const uint32_t prefix = offset - aligned_offset;
  const uint32_t available = bounce_size - prefix;
  const uint32_t copy_length = requested_length < available ? requested_length : available;
  const uint32_t covered = prefix + copy_length;
  const uint32_t dma_length =
    (covered + OTA_QSPI_DMA_ALIGNMENT - 1u) & ~(OTA_QSPI_DMA_ALIGNMENT - 1u);

  if (aligned_offset > capacity || dma_length > capacity - aligned_offset) {
    return false;
  }

  window->offset = aligned_offset;
  window->length = dma_length;
  window->prefix = prefix;
  window->copy_length = copy_length;
  return true;
}

// Overlay bytes into an aligned readback window without asking NOR flash to
// change a programmed zero back to one. Validation completes before mutation.
static inline bool ota_qspi_nor_overlay(uint8_t *window_data, uint32_t window_length,
                                        uint32_t prefix, const uint8_t *src,
                                        uint32_t src_length) {
  if (window_data == NULL || src == NULL || prefix > window_length ||
      src_length > window_length - prefix) {
    return false;
  }

  for (uint32_t i = 0; i < src_length; i++) {
    if ((src[i] | window_data[prefix + i]) != window_data[prefix + i]) {
      return false;
    }
  }
  for (uint32_t i = 0; i < src_length; i++) {
    window_data[prefix + i] = src[i];
  }
  return true;
}

#endif // OTA_QSPI_ALIGNMENT_H_
