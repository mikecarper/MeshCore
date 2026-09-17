#pragma once

#ifndef COMPANION_FEATURE_READER
  #if defined(COMPANION_RADIO_FULL) && defined(ENABLE_USB_INTERFACE) \
      && (defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM))
    #define COMPANION_FEATURE_READER 1
  #else
    #define COMPANION_FEATURE_READER 0
  #endif
#endif

#if COMPANION_FEATURE_READER \
    && !(defined(COMPANION_RADIO_FULL) && defined(ENABLE_USB_INTERFACE) \
         && (defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM)))
  #error "Reader lookup requires an ESP32 or nRF52 Full Companion USB terminal"
#endif
