#pragma once

#if defined(ESP32_PLATFORM) && defined(ENABLE_OTA) && \
    (defined(WIFI_OTA_SEEDER) || defined(WIFI_SSID))

#include <stdint.h>

#ifndef OTA_SEEDER_TCP_PORT
#define OTA_SEEDER_TCP_PORT 5001
#endif

namespace mesh {
namespace ota {

class WiFiOtaSeeder {
public:
  static void loop();
  static void stop();
  static bool isListening();
  static bool isAttached();
  static uint16_t port() { return OTA_SEEDER_TCP_PORT; }
};

}  // namespace ota
}  // namespace mesh

#endif
