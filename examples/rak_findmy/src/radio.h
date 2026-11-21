#pragma once
#include <stdint.h>
#include <stddef.h>

struct TagRadioProfile {
  uint32_t hz;
  uint16_t bw_tenths;
  uint8_t sf, cr;
};
bool tag_radio_available();
int tag_radio_apply(const TagRadioProfile& profile);
int tag_radio_send(const uint8_t* data, size_t size);
int tag_radio_listen();
int tag_radio_read(uint8_t* data, size_t capacity);
void tag_radio_idle();
