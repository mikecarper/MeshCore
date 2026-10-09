#pragma once

#ifndef MESH_ENABLE_FLEET_CONTROL
  // Fixed 240 KiB STM32 images have no room for fleet control.
  #if defined(STM32_PLATFORM)
    #define MESH_ENABLE_FLEET_CONTROL 0
  #else
    #define MESH_ENABLE_FLEET_CONTROL 1
  #endif
#endif
