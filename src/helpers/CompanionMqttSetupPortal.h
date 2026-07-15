#pragma once

#if defined(ESP32_PLATFORM) && defined(WIFI_SSID) && defined(WITH_MQTT_BRIDGE)

#include <helpers/MQTTPrefs.h>

class CompanionMqttSetupPortal {
public:
  CompanionMqttSetupPortal();

  // Starts a station-only settings page on port 80. The caller must ensure
  // the captive setup AP is no longer active. The page stays available until
  // WiFi disconnects or stop() is called.
  bool begin(MQTTPrefs* prefs);
  void stop();

  // Polls the background HTTP task. Returns true after a valid MQTT
  // configuration has been copied into prefs; the server remains active.
  bool loop();
  bool isActive() const { return _active; }

  static bool loadStoredConfig(MQTTPrefs& prefs);
  static bool saveStoredConfig(const MQTTPrefs& prefs);

private:
  volatile bool _active;
  void* _impl;
};

#endif
