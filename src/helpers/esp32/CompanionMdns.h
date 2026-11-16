#pragma once

#if defined(ESP32_PLATFORM) && defined(ENABLE_WIFI_INTERFACE) && defined(ENABLE_ESP_MDNS)

#include <Arduino.h>
#include <ESPmDNS.h>
#include <helpers/NetworkHostname.h>
#include <stdio.h>
#include <string.h>

// Adapt upstream's meshcore/TCP discovery to the fork's deferred WiFi startup.
// Start only with a live listener, retry failed starts, and follow node renames.
class CompanionMdns {
  char _hostname[NetworkHostname::kBufferSize] = {};
  bool _running = false;
  bool _attempted = false;
  uint32_t _last_attempt = 0;

public:
  void loop(bool link_up, const char* node_name, const uint8_t* public_key,
            const char* firmware, unsigned protocol_version, uint16_t port) {
    if (!link_up) {
      if (_running) MDNS.end();
      _running = _attempted = false;
      return;
    }
    char hostname[sizeof(_hostname)];
    NetworkHostname::build(hostname, sizeof(hostname), node_name, public_key, 32);
    if (strcmp(hostname, _hostname) != 0) {
      if (_running) MDNS.end();
      strcpy(_hostname, hostname);
      _running = _attempted = false;
    }
    if (_running) return;
    const uint32_t now = millis();
    if (_attempted && static_cast<uint32_t>(now - _last_attempt) < 5000) return;
    _attempted = true;
    _last_attempt = now;
    if (!MDNS.begin(_hostname)) return;
    MDNS.addService("meshcore", "tcp", port);
    MDNS.addServiceTxt("meshcore", "tcp", "node-name", node_name);
    MDNS.addServiceTxt("meshcore", "tcp", "firmware-version", firmware);
    char protocol[12];
    snprintf(protocol, sizeof(protocol), "%u", protocol_version);
    MDNS.addServiceTxt("meshcore", "tcp", "protocol-version", static_cast<const char*>(protocol));
    _running = true;
  }
};

#endif
