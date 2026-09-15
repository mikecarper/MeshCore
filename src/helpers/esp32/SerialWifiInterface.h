#pragma once

#include "../BaseSerialInterface.h"
#include "../UsbLogging.h"
#include <WiFi.h>

class SerialWifiInterface : public BaseSerialInterface {
  bool deviceConnected;
  bool _isEnabled;
  unsigned long _last_write;

  WiFiServer server;
  WiFiClient client;

  struct FrameHeader {
    uint8_t type;
    uint16_t length;
  };

  struct Frame {
    uint8_t len;
    uint8_t buf[MAX_FRAME_SIZE];
  };

  FrameHeader received_frame_header;

  #define FRAME_QUEUE_SIZE  4
  // Reuse the former (unused) receive queue for one displaced client's replies.
  // No heap allocation or additional frame buffer is needed on reconnect.
  int held_queue_len;
  Frame held_queue[FRAME_QUEUE_SIZE];
  int send_queue_len;
  Frame send_queue[FRAME_QUEUE_SIZE];
  IPAddress queue_ip;
  IPAddress held_ip;
  bool queue_has_ip = false;
  uint32_t held_since = 0;
  size_t send_offset = 0;
  void (*session_changed)(void*) = nullptr;
  void* session_context = nullptr;

  void clearBuffers();
  void expireHeldQueue(uint32_t now);
  void selectClient(const IPAddress& ip, uint32_t now);

protected:

public:
  SerialWifiInterface() : server(WiFiServer()), client(WiFiClient()) {
    deviceConnected = false;
    _isEnabled = false;
    _last_write = 0;
    clearBuffers();
    received_frame_header.type = 0;
    received_frame_header.length = 0;
  }

  void begin(int port);
  void end();
  // Called synchronously while disconnected, before a different IP can send
  // commands or receive replies. The owner can cancel route-bound operations.
  void setSessionChangedCallback(void (*callback)(void*), void* context) {
    session_changed = callback;
    session_context = context;
  }

  // BaseSerialInterface methods
  void enable() override;
  void disable() override;
  bool isEnabled() const override { return _isEnabled; }
  void loop() override;

  bool isConnected() const override;
  bool isReadBusy() const override;
  bool isWriteBusy() const override;
  // A parked backlog is not runnable work and must not keep the MCU awake.
  bool hasPendingIO() const override { return _isEnabled && deviceConnected && send_queue_len > 0; }

  size_t writeFrame(const uint8_t src[], size_t len) override;
  size_t checkRecvFrame(uint8_t dest[]) override;

  bool hasReceivedFrameHeader();
  void resetReceivedFrameHeader();
};

#if WIFI_DEBUG_LOGGING && ARDUINO
  #include <Arduino.h>
  #define WIFI_DEBUG_PRINT(F, ...) do { if (mesh::isUsbLoggingEnabled()) { mesh::usbLoggingPort().printf("WiFi: " F, ##__VA_ARGS__); } } while(0)
  #define WIFI_DEBUG_PRINTLN(F, ...) do { if (mesh::isUsbLoggingEnabled()) { mesh::usbLoggingPort().printf("WiFi: " F "\n", ##__VA_ARGS__); } } while(0)
#else
  #define WIFI_DEBUG_PRINT(...) {}
  #define WIFI_DEBUG_PRINTLN(...) {}
#endif
