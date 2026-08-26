#pragma once

// Features are selected independently so adding a transport or changing an
// image-size profile cannot accidentally remove unrelated Companion behavior.
// Keep the legacy umbrella as a compatibility input for existing target files;
// application code must use the capability macros below.
#if defined(COMPANION_RADIO_FULL)
  #ifndef COMPANION_FEATURE_TEMP_RADIO
    #define COMPANION_FEATURE_TEMP_RADIO 1
  #endif
  #ifndef COMPANION_FEATURE_OTA_CLI
    #define COMPANION_FEATURE_OTA_CLI 1
  #endif
  #ifndef COMPANION_FEATURE_NETWORK_TERMINAL
    #define COMPANION_FEATURE_NETWORK_TERMINAL 1
  #endif
  #ifndef COMPANION_FEATURE_MEMORY_DIAGNOSTICS
    #define COMPANION_FEATURE_MEMORY_DIAGNOSTICS 1
  #endif
  #if defined(MESH_DUAL_CDC_LOGGING)
    #ifndef COMPANION_FEATURE_DEDICATED_USB_LOGGING
      #define COMPANION_FEATURE_DEDICATED_USB_LOGGING 1
    #endif
  #endif
  #if defined(NRF52_PLATFORM) && defined(OTA_FOLDER_SERIAL)
    #ifndef COMPANION_FEATURE_USB_MOTA_SOURCE
      #define COMPANION_FEATURE_USB_MOTA_SOURCE 1
    #endif
  #endif
#endif

#ifndef COMPANION_FEATURE_TEMP_RADIO
  #define COMPANION_FEATURE_TEMP_RADIO 0
#endif
#ifndef COMPANION_FEATURE_OTA_CLI
  #define COMPANION_FEATURE_OTA_CLI 0
#endif
#ifndef COMPANION_FEATURE_NETWORK_TERMINAL
  #define COMPANION_FEATURE_NETWORK_TERMINAL 0
#endif
#ifndef COMPANION_FEATURE_MEMORY_DIAGNOSTICS
  #define COMPANION_FEATURE_MEMORY_DIAGNOSTICS 0
#endif
#ifndef COMPANION_FEATURE_USB_MOTA_SOURCE
  #define COMPANION_FEATURE_USB_MOTA_SOURCE 0
#endif
#ifndef COMPANION_FEATURE_DEDICATED_USB_LOGGING
  #define COMPANION_FEATURE_DEDICATED_USB_LOGGING 0
#endif

#if COMPANION_FEATURE_NETWORK_TERMINAL && !defined(ENABLE_USB_INTERFACE)
  #error "COMPANION_FEATURE_NETWORK_TERMINAL requires ENABLE_USB_INTERFACE"
#endif

#if COMPANION_FEATURE_USB_MOTA_SOURCE \
    && !(defined(NRF52_PLATFORM) && defined(OTA_FOLDER_SERIAL) \
         && defined(ENABLE_USB_INTERFACE))
  #error "COMPANION_FEATURE_USB_MOTA_SOURCE requires nRF52 USB folder seeding"
#endif
