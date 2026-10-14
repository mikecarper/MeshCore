#pragma once

#include "BeaconFeatureConfig.h"

// Resolve the chip in every translation unit, including Dispatcher.cpp which
// includes this before Arduino.h. Otherwise different class layouts could be
// selected for the same build depending on include order.
#if !defined(MESH_ENABLE_MESHTEXT_CHAT) && defined(ESP32_PLATFORM) && defined(ARDUINO)
  #include <sdkconfig.h>
#endif

// Channel chat is an optional Companion transport, never a bridge. Qualified
// families are ESP32-S3 Full/SX1262 and RAK4631 Full; other builds retain their
// features and do not acquire the codec or its bounded raw-frame queue. A new
// family must qualify its modem implementation, flash and runtime RAM first.
#ifndef MESH_ENABLE_MESHTEXT_CHAT
  #if defined(ESP32_PLATFORM) && defined(CONFIG_IDF_TARGET_ESP32S3) \
      && defined(COMPANION_RADIO_FULL) && defined(USE_SX1262) \
      && !(defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW)
    #define MESH_ENABLE_MESHTEXT_CHAT 1
  #elif defined(NRF52_PLATFORM) && defined(RAK_4631) \
      && defined(COMPANION_RADIO_FULL) && defined(USE_SX1262) \
      && !(defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW)
    #define MESH_ENABLE_MESHTEXT_CHAT 1
  #else
    #define MESH_ENABLE_MESHTEXT_CHAT 0
  #endif
#endif

// Compile the bounded wire codec uniformly for beacon-enabled builds. Some
// variants discover their radio chip only when Arduino includes the variant
// header; using USE_SX1262 here would select inconsistent class layouts and
// omit codec definitions in translation units that do not include Arduino.
// Concrete driver capability still gates Meshtext TX independently. This
// does not enable Companion chat, RX handling, or automatic radio2 management.
#ifndef MESH_ENABLE_MESHTEXT_WIRE
  #if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_TX_BEACONS
    #define MESH_ENABLE_MESHTEXT_WIRE 1
  #else
    #define MESH_ENABLE_MESHTEXT_WIRE 0
  #endif
#endif
