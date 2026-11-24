#include "OtaRawBootStage.h"

#if defined(MESHCORE_RAW_BOOT_STAGE_ENABLED)
#include "OtaRollbackRecord.h"
#include "Utils.h"
#include "../nrf52/InternalFlashStatus.h"
#include "flash/flash_nrf5x.h"

namespace mesh {
namespace ota {

bool ota_raw_boot_rollback_busy(uint32_t app_base) {
  const uint32_t pages[] = {MOTA_ROLLBACK_EXPANDED_PAGE, MOTA_ROLLBACK_LEGACY_PAGE};
  for (uint32_t page : pages) {
    mota_rollback_record_t record;
    memcpy(&record, reinterpret_cast<const void*>(page), sizeof(record));
    if ((record.phase == MOTA_ROLLBACK_TRIAL || mota_rollback_should_restore(record.phase)) &&
        mota_rollback_valid(&record, app_base, page)) return true;
  }
  return false;
}

bool ota_write_raw_boot_page(OtaRawBootLayout layout, uint32_t index, const uint8_t* page) {
  const uint32_t start = ota_raw_boot_start(layout);
  constexpr uint32_t page_size = MOTA_NRF52_FLASH_PAGE;
  if (!start || index > RAW_BOOT_IMAGE_SIZE / page_size) return false;
  const uint32_t address = start + index * page_size;
  if (flash_nrf5x_write(address, page, page_size) != static_cast<int>(page_size) ||
      !mesh_flash_nrf5x_flush_checked()) return false;
  const auto* actual = reinterpret_cast<const volatile uint8_t*>(address);
  for (uint32_t i = 0; i < page_size; ++i) {
    if (actual[i] != page[i]) return false;
  }
  return true;
}

bool ota_verify_raw_boot_image(OtaRawBootLayout layout, const uint8_t* expected) {
  const uint32_t start = ota_raw_boot_start(layout);
  if (!start) return false;
  uint8_t actual[32];
  Utils::sha256(actual, sizeof(actual), reinterpret_cast<const uint8_t*>(start), RAW_BOOT_IMAGE_SIZE);
  return memcmp(actual, expected, sizeof(actual)) == 0;
}

} // namespace ota
} // namespace mesh
#endif
