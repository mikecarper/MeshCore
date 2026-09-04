#pragma once

#include <cstdint>

bool tud_cdc_n_connected(uint8_t instance);
uint32_t tud_cdc_n_write_available(uint8_t instance);
uint32_t tud_cdc_n_write(uint8_t instance, const void* data, uint32_t size);
uint32_t tud_cdc_n_write_flush(uint8_t instance);
bool tud_cdc_n_write_clear(uint8_t instance);
