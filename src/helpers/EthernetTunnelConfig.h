#pragma once

// Ethernet tunnelling is a host-assisted ESP32 Full Companion transport.
// Deliberately independent of radio-chip macros: some Arduino variants reveal
// their chip only after this header is consumed in other translation units.
// A concrete driver must additionally advertise the wire capability.
#ifndef MESH_ENABLE_ETHERNET_TUNNEL
  #if defined(ESP32_PLATFORM) && defined(COMPANION_RADIO_FULL) \
      && defined(WIFI_SSID) \
      && !(defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW)
    #define MESH_ENABLE_ETHERNET_TUNNEL 1
  #else
    #define MESH_ENABLE_ETHERNET_TUNNEL 0
  #endif
#endif

// Raw KISS modems may opt into the same LoRa wire format without including
// Companion ownership, its foreign-frame dispatcher slot, or a WiFi server.
// Keep this independent of board macros so every translation unit agrees.
#ifndef MESH_ENABLE_ETHERNET_WIRE
  #define MESH_ENABLE_ETHERNET_WIRE MESH_ENABLE_ETHERNET_TUNNEL
#endif
