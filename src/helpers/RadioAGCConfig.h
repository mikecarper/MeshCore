#pragma once

// Resolve this before Dispatcher declares its maintenance API and before any
// radio driver is included. Selecting it in a wrapper header is too late: a
// different translation unit may already have compiled the scheduler without
// the pulse API. The concrete driver, not an optional USE_* radio, decides.
#ifndef MESH_WARM_RX_REFRESH
  #define MESH_AGC_RXPS_DRIVER_CustomSX1262 1
  #define MESH_AGC_RXPS_DRIVER_CustomSX1268 1
  #define MESH_AGC_RXPS_DRIVER_CustomLLCC68 1
  #define MESH_AGC_RXPS_DRIVER_CustomSTM32WLx 1
  #define MESH_AGC_RXPS_DRIVER_CustomLR1110 1
  #define MESH_AGC_RXPS_DRIVER_CustomLR1121 1
  #define MESH_AGC_RXPS_DRIVER_CustomLR2021 1
  #define MESH_AGC_RXPS_DRIVER_CustomSX1276 1
  #if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && (defined(USE_SX1262) || defined(USE_LR1110) || defined(USE_LR1121) || defined(USE_LR2021) || defined(USE_SX1276))
    // The selected backend advertises its own physical pulse capability.
    // Compiling the interface never grants it to an unsupported backend.
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
  #undef MESH_AGC_RXPS_DRIVER_CustomSX1268
  #undef MESH_AGC_RXPS_DRIVER_CustomLLCC68
  #undef MESH_AGC_RXPS_DRIVER_CustomSTM32WLx
  #undef MESH_AGC_RXPS_DRIVER_CustomLR1110
  #undef MESH_AGC_RXPS_DRIVER_CustomLR1121
  #undef MESH_AGC_RXPS_DRIVER_CustomLR2021
  #undef MESH_AGC_RXPS_DRIVER_CustomSX1276
#endif
