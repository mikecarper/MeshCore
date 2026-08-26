#pragma once

// Keep role capabilities independent from partition/profile labels. Existing
// FULL recipes remain compatible, while build tooling can now select this
// feature directly and verify that its CLI survived the link.
#ifndef MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE
  #if defined(MESHCORE_ESP32_FULL_PROFILE)
    #define MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE 1
  #else
    #define MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE 0
  #endif
#endif
