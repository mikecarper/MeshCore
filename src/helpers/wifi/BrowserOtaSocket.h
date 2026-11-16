#pragma once

#if defined(ESP32)
#include <Arduino.h>
#include <WiFi.h>
#include "SerialWifiInterface.h"

// Small, bounded WebSocket front end for the existing local Companion socket.
// The radio owner remains SerialWifiInterface, including its OTA session
// cancellation. Never exposes an arbitrary outbound TCP target.
class BrowserOtaSocket {
  friend class BrowserOtaSocketTest;
  static constexpr size_t MaxHttp = 1024, MaxWire = 520, MaxPayload = 512;
  WiFiServer server{5005};
  WiFiClient candidate, peer, upstream;
  SerialWifiInterface& companion;
  bool listening = false, reserved = false, promote = false;
  bool candidate_active = false, peer_active = false, attempted = false;
  bool fragment = false;
  uint32_t accepted_at = 0, frame_started = 0, tx_started = 0, last_attempt = 0;
  char http[MaxHttp + 1] = {};
  size_t http_len = 0;
  uint8_t reply[512] = {};
  size_t reply_len = 0, reply_at = 0;
  uint8_t rx[MaxWire] = {}, tx[MaxWire] = {};
  size_t rx_len = 0, tx_len = 0, tx_at = 0;
  size_t forward_at = 0, forward_len = 0;

  void closeCandidate();
  void closePeer();
  void serviceCandidate();
  void handleHttp();
  void servicePeer();
  bool queueFrame(uint8_t opcode, const uint8_t* data, size_t length);
  bool parseFrame();
public:
  explicit BrowserOtaSocket(SerialWifiInterface& source) : companion(source) {}
  void begin();
  void stop();
  void loop();
};
#endif
