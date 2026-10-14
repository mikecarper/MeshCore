#pragma once

// Fixed STM32 images retain their existing features and filesystem boundary.
// Host tests explicitly opt in when exercising the beacon implementation.
#ifndef MESH_ENABLE_TX_BEACONS
  #if !defined(STM32_PLATFORM) && (defined(ESP32_PLATFORM) \
      || defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM))
    #define MESH_ENABLE_TX_BEACONS 1
  #else
    #define MESH_ENABLE_TX_BEACONS 0
  #endif
#endif
