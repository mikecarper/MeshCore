#pragma once

// The ARM linker scripts collect these input sections in initialized
// RAM, which the core copies from flash before setup(). Use separate sections
// so the linker can discard unused functions. This is task-context code.
#if defined(ESP32_PLATFORM)
#define MC_RADIO_RETUNE_ATTR(name) __attribute__((section(".iram1.radio_" #name), noinline))
#elif defined(NRF52_PLATFORM)
#define MC_RADIO_RETUNE_ATTR(name) __attribute__((section(".data.radio_" #name), noinline))
#elif defined(STM32_PLATFORM)
#define MC_RADIO_RETUNE_ATTR(name) __attribute__((section(".RamFunc.radio_" #name), noinline))
#elif defined(RP2040_PLATFORM)
#define MC_RADIO_RETUNE_ATTR(name) __attribute__((section(".time_critical.radio_" #name), noinline))
#else
#define MC_RADIO_RETUNE_ATTR(name)
#endif

// ESP32 builds with additional instruction RAM can also keep chip-specific
// helpers resident.
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

// Size-limited images spend their RAM on the chip parameter writes. The
// default places the switching controller and receive restart in RAM.
#if defined(MC_RADIO_RETUNE_DRIVER_ONLY) && MC_RADIO_RETUNE_DRIVER_ONLY
#define MC_RADIO_SWITCH_CONTROL(name)
#else
#define MC_RADIO_SWITCH_CONTROL(name) MC_RADIO_RETUNE_ATTR(name)
#endif
