#pragma once

// Board opt-in: keep the short retune path resident instead of refetching it
// from flash after application/UI/network work has displaced the I-cache.
// This is code placement only, not permission to call the driver in an ISR or
// with flash disabled: its unannotated RadioLib/SDK callees can still use flash.
#ifndef MC_ESP32_RADIO_FAST_CODE
#define MC_ESP32_RADIO_FAST_CODE 0
#endif
#if defined(ESP32_PLATFORM) && MC_ESP32_RADIO_FAST_CODE
#include <esp_attr.h>
// Xtensa inline class methods also need -mtext-section-literals so their
// literal pools reside beside their IRAM instructions. Keep the functions
// out of line so callers do not duplicate them back into flash.
#define MC_RADIO_FAST_CODE_ATTR IRAM_ATTR __attribute__((noinline))
#else
#define MC_RADIO_FAST_CODE_ATTR
#endif
