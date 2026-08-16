#pragma once

#if defined(ESP32_PLATFORM) && defined(WIFI_SSID)

#include <Arduino.h>
#include <IPAddress.h>

class WiFiSetupPortal {
public:
  typedef bool (*SaveCallback)(void* context, const char* ssid, const char* password);

  WiFiSetupPortal();

  // Starts an open setup AP and captive HTTP form. The AP remains available
  // after a failed association and shuts down shortly after a successful one.
  bool begin(const char* ap_name, SaveCallback save_callback, void* context);
  void stop();
  // Retry interval is the steady-state cadence. initial_delay_ms defaults to
  // that interval, but callers that opened the portal partway through an
  // outage can preserve the original reconnect schedule with a shorter delay.
  void configureRecovery(const char* ssid, const char* password,
                         uint32_t interval_ms, uint32_t initial_delay_ms = 0);

  bool isActive() const { return _active; }
  bool isStopping() const;
  IPAddress apIP() const { return IPAddress(192, 168, 4, 1); }

  // Companion WiFi builds keep their runtime credentials in NVS.
  static bool loadStoredCredentials(char* ssid, size_t ssid_size,
                                    char* password, size_t password_size);
  static bool saveStoredCredentials(const char* ssid, const char* password);
  static bool isPlaceholderSSID(const char* ssid);

private:
  volatile bool _active;
  void* _impl;
};

WiFiSetupPortal& wifiSetupPortal();

#endif
