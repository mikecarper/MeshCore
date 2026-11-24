#pragma once

#include <string.h>
#include "OtaByteIO.h"
#include "OtaFlashLayout_nrf52.h"

#if (!defined(OTA_PREPARE_RAW_BOOTLOADER) || OTA_PREPARE_RAW_BOOTLOADER) && \
    defined(NRF52_PLATFORM) && defined(ENABLE_OTA) && !defined(OTA_SEEDER_ONLY) && \
    !MESH_ROLE_COMPANION && \
    ((defined(OTA_INTERNAL_BOOTLOADER_UPDATE) && defined(OTA_FLASH_STORE)) || \
     (defined(OTA_QSPI_BOOTLOADER_UPDATE) && defined(OTA_QSPI_STORE)) || \
     (defined(OTA_SD_BOOTLOADER_UPDATE) && defined(OTA_SD_STORE)))
#define MESHCORE_RAW_BOOT_STAGE_ENABLED 1
#endif

namespace mesh {
namespace ota {

enum class OtaRawBootLayout : uint8_t {
  ExternalScratch = 0xE0u,
  SharedInternal = 0xE2u,
};

static constexpr uint32_t RAW_BOOT_IMAGE_SIZE = MOTA_NRF52_BL_END - MOTA_NRF52_BL_START;
static constexpr uint32_t RAW_BOOT_RECORD_MAGIC = 0x31574152u;          // RAW1
static constexpr uint32_t RAW_BOOT_EXTERNAL_RECORD_MAGIC = 0x32574152u; // RAW2
static constexpr uint32_t RAW_BOOT_RECORD_SIZE = 40u;
static constexpr uint32_t RAW_BOOT_EXTERNAL_RECORD_SIZE = 44u;
static constexpr uint32_t RAW_BOOT_PAYLOAD_OFFSET =
    MOTA_NRF52_BOOT_CONTAINER_SIZE - RAW_BOOT_IMAGE_SIZE - 5u;
static_assert(RAW_BOOT_IMAGE_SIZE == 0xA000u && RAW_BOOT_PAYLOAD_OFFSET == 365u,
              "RAW1 requires the exact v3 bootloader package layout");
static_assert(MOTA_NRF52_SHARED_BOOT_STAGE_START + RAW_BOOT_IMAGE_SIZE +
                  MOTA_NRF52_FLASH_PAGE == MOTA_NRF52_APP_END,
              "RAW1 requires a separate final record page in the shared slot");
static_assert(MOTA_NRF52_BOOT_SCRATCH_START + RAW_BOOT_IMAGE_SIZE == MOTA_NRF52_BOOT_SCRATCH_END &&
              MOTA_NRF52_BOOT_SCRATCH_END + MOTA_NRF52_FLASH_PAGE <= MOTA_NRF52_APP_END,
              "external RAW1 record must sit after the existing scratch and before InternalFS");

constexpr uint32_t ota_raw_boot_start(OtaRawBootLayout layout) {
  return layout == OtaRawBootLayout::SharedInternal ? MOTA_NRF52_SHARED_BOOT_STAGE_START
       : layout == OtaRawBootLayout::ExternalScratch ? MOTA_NRF52_BOOT_SCRATCH_START : 0u;
}

inline bool ota_raw_boot_layout_fits(OtaRawBootLayout layout, uint32_t app_base,
                                    uint32_t app_size, uint32_t stage_ceiling) {
  const uint32_t start = ota_raw_boot_start(layout);
  return start && (app_base == MOTA_NRF52_APP_BASE_S140_V6 ||
                   app_base == MOTA_NRF52_APP_BASE_S140_V7) && app_size &&
         app_base < start && app_size <= start - app_base &&
         stage_ceiling == MOTA_NRF52_APP_END &&
         start + RAW_BOOT_IMAGE_SIZE + MOTA_NRF52_FLASH_PAGE <= stage_ceiling;
}

// A future OTAFIX must explicitly advertise the exact handoff ABI in its
// CRC-checked installed image. The release's existing MOTABLDR is not enough.
inline bool ota_raw_boot_stage_supported(const uint8_t* image, size_t size,
                                         OtaRawBootLayout layout) {
  static const uint8_t magic[] = {'M', 'O', 'T', 'A', 'R', 'A', 'W'};
  if (!image || size != RAW_BOOT_IMAGE_SIZE || !ota_raw_boot_start(layout)) return false;
  const bool external = layout == OtaRawBootLayout::ExternalScratch;
  const uint8_t abi = external ? 2u : 1u;
  bool found = false;
  for (size_t i = 0; i <= size - 16u; i += 4u) {
    const uint8_t* p = image + i;
    if (p[7] != '0' + abi || memcmp(p, magic, sizeof(magic)) != 0) continue;
    if (found || rd_u16le(p + 8u) != abi ||
        rd_u16le(p + 10u) != (external ? RAW_BOOT_EXTERNAL_RECORD_SIZE : RAW_BOOT_RECORD_SIZE) ||
        rd_u32le(p + 12u) != static_cast<uint8_t>(layout)) return false;
    found = true;
  }
  return found;
}

template <typename ReadPayload, typename WritePage, typename VerifyImage>
bool ota_stage_raw_bootloader(OtaRawBootLayout layout, uint32_t size, uint32_t payload_offset,
                              uint32_t version, uint32_t board_id, const uint8_t* expected_hash, uint8_t* page,
                              ReadPayload read_payload, WritePage write_page,
                              VerifyImage verify_image) {
  const bool external = layout == OtaRawBootLayout::ExternalScratch;
  if (!ota_raw_boot_start(layout) || (external && !board_id) || !page ||
      size != MOTA_NRF52_BOOT_CONTAINER_SIZE || payload_offset != RAW_BOOT_PAYLOAD_OFFSET ||
      !version || !expected_hash) return false;
  uint8_t record[RAW_BOOT_EXTERNAL_RECORD_SIZE];
  wr_u32le(record, external ? RAW_BOOT_EXTERNAL_RECORD_MAGIC : RAW_BOOT_RECORD_MAGIC);
  wr_u32le(record + 4u, version);
  memcpy(record + 8u, expected_hash, 32u);
  wr_u32le(record + RAW_BOOT_RECORD_SIZE, board_id);

  constexpr uint32_t page_size = MOTA_NRF52_FLASH_PAGE;
  for (uint32_t offset = 0; offset < RAW_BOOT_IMAGE_SIZE; offset += page_size) {
    // Source overlaps destination. Read the entire page before erasing and
    // move forward; even the last page reads from the future record page.
    if (!read_payload(payload_offset + offset, page, page_size) ||
        !write_page(offset / page_size, page)) return false;
  }
  if (!verify_image(record + 8u)) return false;

  memset(page, 0xFF, page_size);
  memcpy(page, record, external ? RAW_BOOT_EXTERNAL_RECORD_SIZE : RAW_BOOT_RECORD_SIZE);
  return write_page(RAW_BOOT_IMAGE_SIZE / page_size, page);
}

#if defined(MESHCORE_RAW_BOOT_STAGE_ENABLED)
bool ota_raw_boot_rollback_busy(uint32_t app_base);
bool ota_write_raw_boot_page(OtaRawBootLayout layout, uint32_t index, const uint8_t* page);
bool ota_verify_raw_boot_image(OtaRawBootLayout layout, const uint8_t* expected);
#endif

} // namespace ota
} // namespace mesh
