#pragma once

// One bounded raw-radio slot, shared with the existing KISS/foreign scheduler.
// Normal Packet storage, routed OTA and non-OTA builds remain unchanged.
#ifndef MESH_ENABLE_OTA_KISS_DIRECT
  #if defined(ENABLE_OTA) && !(defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW) \
      && !(defined(MESH_ENABLE_FOREIGN_RADIO) && !MESH_ENABLE_FOREIGN_RADIO)
    #define MESH_ENABLE_OTA_KISS_DIRECT 1
  #else
    #define MESH_ENABLE_OTA_KISS_DIRECT 0
  #endif
#endif
#if MESH_ENABLE_OTA_KISS_DIRECT != 0 && MESH_ENABLE_OTA_KISS_DIRECT != 1
#error "MESH_ENABLE_OTA_KISS_DIRECT must be 0 or 1"
#endif
#if MESH_ENABLE_OTA_KISS_DIRECT && !defined(ENABLE_OTA)
#error "direct KISS OTA requires ENABLE_OTA"
#endif
#if MESH_ENABLE_OTA_KISS_DIRECT && defined(MESH_ENABLE_FOREIGN_RADIO) && !MESH_ENABLE_FOREIGN_RADIO
#error "direct KISS OTA requires the shared raw-radio scheduler"
#endif
