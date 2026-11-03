#pragma once

#ifndef COMPANION_FEATURE_READER
  #define COMPANION_FEATURE_READER 0
#endif

#ifndef COMPANION_READER_COMPACT_STREAM
  #define COMPANION_READER_COMPACT_STREAM 0
#endif

#if COMPANION_FEATURE_READER \
    && !(defined(COMPANION_RADIO_FULL) && defined(ENABLE_USB_INTERFACE) \
         && (defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM)))
  #error "Reader lookup requires an ESP32 or nRF52 Full Companion USB terminal"
#endif
