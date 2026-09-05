#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

int flash_nrf5x_write(uint32_t address, const void* data, uint32_t length);
void flash_nrf5x_flush(void);

#ifdef __cplusplus
}
#endif
