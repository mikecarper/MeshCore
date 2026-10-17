#pragma once

// PSRAM boards already keep the complete contact table outside internal RAM.
// High-contact nRF52 USB/BLE recipes need the same cache as Full Companion:
// inline paths/secrets can otherwise leave too little heap for startup.
// An explicit 0/1 override is useful for matched qualification builds.
#ifndef MESH_CONTACT_CACHE
#if !defined(BOARD_HAS_PSRAM) && \
    ((defined(COMPANION_RADIO_FULL) && \
      (defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM))) || \
     (defined(NRF52_PLATFORM) && defined(MAX_CONTACTS) && MAX_CONTACTS > 300))
#define MESH_CONTACT_CACHE 1
#else
#define MESH_CONTACT_CACHE 0
#endif
#endif

// A measured ESP32-S3 SPIFFS lookup took ~132 ms versus ~22 ms for key
// exchange. Recalculate cold ESP32 entries instead of spending more time and
// writes on flash. On a RAK3401, LittleFS took ~8 ms versus ~31 ms for key
// exchange, so nRF52 reuses saved secrets when available.
#ifndef MESH_CONTACT_SECRET_FLASH_CACHE
#if defined(NRF52_PLATFORM)
#define MESH_CONTACT_SECRET_FLASH_CACHE 1
#else
#define MESH_CONTACT_SECRET_FLASH_CACHE 0
#endif
#endif
