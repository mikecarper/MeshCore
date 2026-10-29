#pragma once

// Build switch for named primary-radio presets ("get/set/load/save preset").
// Define MESH_ENABLE_RADIO_PRESETS=0/1 in build_flags to override the default.
#ifndef MESH_ENABLE_RADIO_PRESETS
#if defined(STM32_PLATFORM)
// STM32WL images have too little application flash (wio-e5-mini repeater overflows).
#define MESH_ENABLE_RADIO_PRESETS 0
#elif defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
// ESP-NOW-only builds have no LoRa primary radio for a preset to switch.
#define MESH_ENABLE_RADIO_PRESETS 0
#else
#define MESH_ENABLE_RADIO_PRESETS 1
#endif
#endif
