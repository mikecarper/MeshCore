#pragma once

// Resolve this before Dispatcher declares its maintenance API and before any
// radio driver is included. Selecting it in a wrapper header is too late: a
// different translation unit may already have compiled the scheduler without
// the pulse API. The concrete driver, not an optional USE_* radio, decides.
#ifndef MESH_WARM_RX_REFRESH
  #define MESH_AGC_RXPS_DRIVER_CustomSX1262 1
  #if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && defined(USE_SX1262)
    // Only the selected SX1262 backend advertises supportsWarmRxRefresh().
    // Other backends keep their existing physical maintenance implementation.
    #define MESH_AGC_RXPS_DRIVER_UniversalLoRaRadio 1
  #else
    #define MESH_AGC_RXPS_DRIVER_UniversalLoRaRadio 0
  #endif
  #define MESH_AGC_RXPS_DRIVER_(driver) MESH_AGC_RXPS_DRIVER_##driver
  #define MESH_AGC_RXPS_DRIVER(driver) MESH_AGC_RXPS_DRIVER_(driver)
  #if defined(RADIO_CLASS) && MESH_AGC_RXPS_DRIVER(RADIO_CLASS)
    #define MESH_WARM_RX_REFRESH 1
  #else
    #define MESH_WARM_RX_REFRESH 0
  #endif
  #undef MESH_AGC_RXPS_DRIVER
  #undef MESH_AGC_RXPS_DRIVER_
  #undef MESH_AGC_RXPS_DRIVER_UniversalLoRaRadio
  #undef MESH_AGC_RXPS_DRIVER_CustomSX1262
#endif
