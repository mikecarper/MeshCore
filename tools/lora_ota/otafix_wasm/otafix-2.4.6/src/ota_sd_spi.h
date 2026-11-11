#ifndef OTA_SD_SPI_H_
#define OTA_SD_SPI_H_

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

bool ota_sd_init(void);
void ota_sd_deinit(void);
bool ota_sd_read_sector(uint32_t sector, uint8_t out[512]);
bool ota_sd_read_bytes(uint32_t first_sector, uint32_t offset,
                       void* out, uint32_t len);

#ifdef __cplusplus
}
#endif

#endif
