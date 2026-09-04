#include "StorageLayout.h"

#if defined(ESP32_PLATFORM)
#include <Arduino.h>
#include <esp_ota_ops.h>
#include <esp_partition.h>
#elif defined(NRF52_PLATFORM)
#include <stdarg.h>
#include <nrf.h>
#include "ota/OtaFlashLayout_nrf52.h"
#elif defined(STM32_PLATFORM)
#include <Arduino.h>
#include <InternalFileSystem.h>
#endif

#if defined(ENABLE_OTA)
#include "ota/OtaContext.h"
#endif

namespace mesh {
namespace cli {

#if defined(NRF52_PLATFORM)
static void appendStorageLayout(char* reply, size_t reply_size,
                                const char* format, ...) {
  if (reply == nullptr || reply_size == 0) return;
  const size_t used = strlen(reply);
  if (used >= reply_size) return;

  va_list args;
  va_start(args, format);
  vsnprintf(reply + used, reply_size - used, format, args);
  va_end(args);
}
#endif

void formatStorageLayout(MainBoard& board, char* reply, size_t reply_size) {
  if (reply == nullptr || reply_size == 0) return;
  reply[0] = 0;

#if defined(ESP32_PLATFORM)
  (void)board;
  Esp32StorageLayoutWriter writer(reply, reply_size,
                                  ESP.getFlashChipSize());
  const esp_partition_t* running = esp_ota_get_running_partition();
  esp_partition_iterator_t iterator = esp_partition_find(
      ESP_PARTITION_TYPE_ANY, ESP_PARTITION_SUBTYPE_ANY, NULL);
  while (iterator != NULL) {
    const esp_partition_t* partition = esp_partition_get(iterator);
    if (!writer.append(
            partition->label, partition->address, partition->size,
            running != NULL && partition->address == running->address)) {
      break;
    }
    iterator = esp_partition_next(iterator);
  }
  if (iterator != NULL) esp_partition_iterator_release(iterator);
  writer.finish();

#elif defined(NRF52_PLATFORM)
  const uint32_t flash_bytes =
      (uint32_t)NRF_FICR->CODEPAGESIZE * (uint32_t)NRF_FICR->CODESIZE;
  const uint32_t app_start = mesh::ota::mota_nrf52_app_base();
  const uint32_t app_end =
      (uint32_t)(uintptr_t)mesh::ota::__flash_arduino_end;
#if defined(NRF52840_XXAA)
  const uint32_t internal_fs_start = 0x000ED000UL;
#else
  const uint32_t internal_fs_start = 0x0006D000UL;
#endif
  const uint32_t internal_fs_size = 7UL * 4096UL;
  snprintf(reply, reply_size,
           "> int:nrf52=%luK app=0x%lX-0x%lX ifs=0x%lX+%luK",
           (unsigned long)(flash_bytes / 1024UL),
           (unsigned long)app_start, (unsigned long)app_end,
           (unsigned long)internal_fs_start,
           (unsigned long)(internal_fs_size / 1024UL));

#if defined(ENABLE_OTA) && defined(OTA_QSPI_STORE)
  (void)board;
  mesh::ota::OtaStoreQspiNrf52& store = mesh::ota::ota_ctx().fetch_store;
  const uint32_t capacity = store.capacity();
  if (capacity != 0) {
    appendStorageLayout(reply, reply_size,
                        "; ext:qspi=%luK owner=ota-raw id=%06lX",
                        (unsigned long)(capacity / 1024UL),
                        (unsigned long)store.jedec_id());
  } else {
    appendStorageLayout(reply, reply_size, "; ext:qspi=error(%s)",
                        store.last_error());
  }
#elif defined(ENABLE_OTA) && defined(OTA_SD_STORE)
  uint64_t used_bytes = 0;
  uint64_t free_bytes = 0;
  mesh::ota::OtaStoreSdNrf52& store = mesh::ota::ota_ctx().fetch_store;
  if (store.getSpace(board, used_bytes, free_bytes)) {
    char total[24];
    char used[24];
    char free[24];
    formatStorageBytes(total, sizeof(total), used_bytes + free_bytes);
    formatStorageBytes(used, sizeof(used), used_bytes);
    formatStorageBytes(free, sizeof(free), free_bytes);
    appendStorageLayout(reply, reply_size,
                        "; ext:sd=%s used=%s free=%s owner=ota", total,
                        used, free);
  } else {
    appendStorageLayout(reply, reply_size, "; ext:sd=error(%s)",
                        store.last_error());
  }
#elif defined(QSPIFLASH)
  (void)board;
  appendStorageLayout(reply, reply_size,
                      "; ext:qspi=configured owner=littlefs");
#else
  (void)board;
  appendStorageLayout(reply, reply_size, "; ext:none");
#endif

#elif defined(RP2040_PLATFORM)
  (void)board;
#if defined(PICO_FLASH_SIZE_BYTES) && defined(FS_START) && defined(FS_END)
  snprintf(reply, reply_size,
           "> int:rp2040=%luK ifs=%luK layout=fixed; ext:none",
           (unsigned long)(PICO_FLASH_SIZE_BYTES / 1024UL),
           (unsigned long)(((uintptr_t)FS_END - (uintptr_t)FS_START) /
                           1024UL));
#elif defined(PICO_FLASH_SIZE_BYTES)
  snprintf(reply, reply_size,
           "> int:rp2040=%luK layout=fixed; ext:none",
           (unsigned long)(PICO_FLASH_SIZE_BYTES / 1024UL));
#else
  snprintf(reply, reply_size, "> int:rp2040 layout=fixed; ext:none");
#endif

#elif defined(STM32_PLATFORM)
  (void)board;
  snprintf(reply, reply_size,
           "> int:stm32=%luK ifs=0x%lX+%luK layout=fixed; ext:none",
           (unsigned long)((FLASH_END_ADDR - FLASH_BASE + 1UL) / 1024UL),
           (unsigned long)LFS_FLASH_ADDR_BASE,
           (unsigned long)(LFS_FLASH_TOTAL_SIZE / 1024UL));
#else
  (void)board;
  snprintf(reply, reply_size,
           "Error: storage layout unsupported on this platform");
#endif
}

bool handleStorageLayoutGet(const char* config, MainBoard& board, char* reply,
                            size_t reply_size) {
  const StorageLayoutGetMatch match = classifyStorageLayoutGet(config);
  if (match == StorageLayoutGetMatch::NotMatched) return false;
  if (reply == nullptr || reply_size == 0) return true;
  if (match == StorageLayoutGetMatch::InvalidArguments) {
    snprintf(reply, reply_size, "Error: use get storage.layout");
  } else {
    formatStorageLayout(board, reply, reply_size);
  }
  return true;
}

} // namespace cli
} // namespace mesh
