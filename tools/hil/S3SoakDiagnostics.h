#pragma once

// Test-build-only controls. Never enabled by a published board recipe.
#if defined(MESH_SOAK_DIAGNOSTICS) && defined(ESP32_PLATFORM)
#include <Arduino.h>
#include <WiFi.h>
#include <esp_heap_caps.h>
#include <helpers/ota/OtaContext.h>
#if defined(MESH_SOAK_WIFI_KEY_OVERRIDE)
#include "S3SoakWiFiKey.h"
#endif

namespace mesh { namespace hil {
inline bool handleSoakCommand(const char* command, char* reply, size_t capacity) {
#if defined(MESH_SOAK_WIFI_KEY_OVERRIDE)
  if (strncmp(command, "soak wifi-key ", 14) == 0) {
    snprintf(reply, capacity, "%s", setSoakWiFiKey(command + 14)
        ? "OK - lab key stored" : "Error - invalid lab key or NVS write failed");
    return true;
  }
#endif
  if (strcmp(command, "soak wifi-drop") == 0) {
    WiFi.disconnect(false, false);
    WiFi.reconnect();
    snprintf(reply, capacity, "OK - WiFi reconnect requested");
    return true;
  }
  if (strcmp(command, "soak ota-cycle") == 0) {
    char error[96] = {};
    if (!ota::ota_acquire_context(error, sizeof(error))) {
      snprintf(reply, capacity, "%s", error);
      return true;
    }
#if OTA_DYNAMIC_CONTEXT
    ota::ota_release_context_if_idle(false);
#endif
    snprintf(reply, capacity, "OK - OTA context cycle");
    return true;
  }
  if (strcmp(command, "get soak") != 0) return false;
  snprintf(reply, capacity,
      "{\"up\":%lu,\"int\":%u,\"max\":%u,\"min\":%u,\"ps\":%u,\"ctx\":%u,\"active\":%u,\"dyn\":%u,\"wifi\":%d}",
      millis() / 1000UL,
      unsigned(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
      unsigned(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
      unsigned(heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
      unsigned(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)),
      unsigned(sizeof(ota::OtaContext)),
      ota::ota_context_if_active() != nullptr, unsigned(OTA_DYNAMIC_CONTEXT),
      int(WiFi.status()));
  return true;
}
} }
#endif
