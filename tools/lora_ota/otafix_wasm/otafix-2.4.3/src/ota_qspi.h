#ifndef OTA_QSPI_H_
#define OTA_QSPI_H_

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

bool     ota_qspi_init(void);
void     ota_qspi_deinit(void);
uint32_t ota_qspi_capacity(void);
bool     ota_qspi_read(uint32_t offset, void *dst, uint32_t len);
bool     ota_qspi_write(uint32_t offset, const void *src, uint32_t len);

#ifdef __cplusplus
}
#endif

#endif // OTA_QSPI_H_
