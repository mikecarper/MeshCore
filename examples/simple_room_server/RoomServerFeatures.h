#pragma once

// All ESP32 Rooms use the rule engine, including standard-layout builds.
#ifndef MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE
  #if defined(ESP32_PLATFORM) || defined(MESHCORE_ESP32_FULL_PROFILE)
    #define MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE 1
  #else
    #define MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE 0
  #endif
#endif
