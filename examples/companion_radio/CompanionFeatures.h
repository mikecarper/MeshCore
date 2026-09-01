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
  #if defined(ESP32_PLATFORM)
    #ifndef COMPANION_FEATURE_NETWORK_TERMINAL
      #define COMPANION_FEATURE_NETWORK_TERMINAL 1
    #endif
    #ifndef COMPANION_FEATURE_MEMORY_DIAGNOSTICS
      #define COMPANION_FEATURE_MEMORY_DIAGNOSTICS 1
    #endif
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
#ifndef COMPANION_FEATURE_BLE_MOTA_SOURCE
  #define COMPANION_FEATURE_BLE_MOTA_SOURCE 0
#endif
#ifndef COMPANION_FEATURE_DEDICATED_USB_LOGGING
  #define COMPANION_FEATURE_DEDICATED_USB_LOGGING 0
#endif

#if COMPANION_FEATURE_OTA_CLI && !defined(ENABLE_OTA)
  #error "COMPANION_FEATURE_OTA_CLI requires ENABLE_OTA"
#endif

#if COMPANION_FEATURE_NETWORK_TERMINAL && !defined(ENABLE_USB_INTERFACE)
  #error "COMPANION_FEATURE_NETWORK_TERMINAL requires ENABLE_USB_INTERFACE"
#endif

#if COMPANION_FEATURE_NETWORK_TERMINAL \
    && !(defined(ESP32_PLATFORM) && defined(WIFI_SSID) \
         && defined(ENABLE_OTA))
  #error "COMPANION_FEATURE_NETWORK_TERMINAL requires ESP32 WiFi and the OTA source overlay"
#endif

#if COMPANION_FEATURE_USB_MOTA_SOURCE \
    && !(defined(NRF52_PLATFORM) && defined(OTA_FOLDER_SERIAL) \
         && defined(ENABLE_USB_INTERFACE) && defined(ENABLE_OTA))
  #error "COMPANION_FEATURE_USB_MOTA_SOURCE requires nRF52 USB folder seeding"
#endif

#if COMPANION_FEATURE_BLE_MOTA_SOURCE \
    && !(defined(COMPANION_RADIO_FULL) && defined(NRF52_PLATFORM) \
         && defined(BLE_PIN_CODE) && defined(ENABLE_OTA))
  #error "COMPANION_FEATURE_BLE_MOTA_SOURCE requires an nRF52 Full Companion with BLE and OTA"
#endif

#if COMPANION_FEATURE_MEMORY_DIAGNOSTICS && !defined(ESP32_PLATFORM)
  #error "COMPANION_FEATURE_MEMORY_DIAGNOSTICS requires ESP32"
#endif

#if COMPANION_FEATURE_DEDICATED_USB_LOGGING \
    && !(defined(MESH_DUAL_CDC_LOGGING) \
         && defined(ENABLE_USB_INTERFACE))
  #error "COMPANION_FEATURE_DEDICATED_USB_LOGGING requires nRF52 dual-CDC USB"
#endif

#if defined(MESHCORE_REQUIRES_COMPANION_RADIO_FULL) \
    && !defined(COMPANION_RADIO_FULL)
  #error "This target requires build.sh to apply the Full Companion profile"
#endif

#if defined(COMPANION_RADIO_FULL) && !defined(BLE_PIN_CODE)
  #error "COMPANION_RADIO_FULL requires the Bluetooth Companion transport"
#endif
