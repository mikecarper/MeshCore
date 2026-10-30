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

// Where the preset table lives while the firmware runs.
//   1 (default): the whole table stays in RAM (~220 B of static RAM).
//   0 (lazy):    each command loads /presets into a stack-local table, works on
//                it, saves and drops it. Only the pending-revert timer stays
//                resident (<= 40 B on 32-bit targets). For RAM-tight images,
//                such as the RAK4631 Repeater qualification builds.
// Behavior and reply text are the same in both modes.
#ifndef MESH_RADIO_PRESETS_RESIDENT
#define MESH_RADIO_PRESETS_RESIDENT 1
#endif
