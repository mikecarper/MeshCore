#pragma once

// Ordinary Full Companion images support optional user-wired controls.
// Constrained/headless legacy recipes retain their existing footprint unless
// explicitly opted in. Native board-provided rotary backends are independent.
#ifndef MESH_CUSTOM_PERIPHERALS
#if (defined(COMPANION_RADIO_FULL) && COMPANION_RADIO_FULL \
     && (defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM))) \
    || (defined(UI_ROTARY_INPUT_GPIO) && UI_ROTARY_INPUT_GPIO)
#define MESH_CUSTOM_PERIPHERALS 1
#else
#define MESH_CUSTOM_PERIPHERALS 0
#endif
#endif
