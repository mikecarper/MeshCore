#pragma once

#include "../BaseSerialInterface.h"
#include "../UsbLogging.h"

#ifndef ETHERNET_TCP_PORT
  #define ETHERNET_TCP_PORT 5000
#endif

// define ETHERNET_RAW_LINE=1 to use raw line-based CLI instead of framed packets

class SerialEthernetInterface : public BaseSerialInterface {
  bool _isEnabled;
  unsigned long _last_write;
  uint8_t _state;
  uint16_t _frame_len;
  uint16_t _rx_len;
  uint8_t _rx_buf[MAX_FRAME_SIZE];
  uint32_t _rx_started = 0;
  size_t send_offset = 0;
  bool session_active = false;
  bool queue_has_ip = false;
  uint32_t queue_ip = 0;
  void (*session_changed)(void*) = nullptr;
  void* session_context = nullptr;

  struct Frame {
    uint8_t len;
    uint8_t buf[MAX_FRAME_SIZE];
  };

  #define FRAME_QUEUE_SIZE 4
  int send_queue_len;
  Frame send_queue[FRAME_QUEUE_SIZE];

  void clearBuffers() {
    send_queue_len = 0;
    queue_has_ip = false;
    resetInput();
    send_offset = 0;
  }

  void resetInput() {
    _state = 0;
    _frame_len = 0;
    _rx_len = 0;
    _rx_started = 0;
  }
  void rejectInput();

  protected:
    virtual void disconnectClient() = 0;

  public:
    SerialEthernetInterface() {
        _isEnabled = false;
        _last_write = 0;
        send_queue_len = 0;
        _state = 0;
        _frame_len = 0;
        _rx_len = 0;
    }
    bool begin();

    // Drivers call this while isConnected() is false and BEFORE exposing the
    // new socket. Same-IP live replacement retains the existing logical owner.
    void onClientConnected(uint32_t remote_ip);
    void onClientDisconnected();
    void setSessionChangedCallback(void (*callback)(void*), void* context) {
      session_changed = callback;
      session_context = context;
    }

    // BaseSerialInterface methods
    void enable() override;
    void disable() override;
    bool isEnabled() const override { return _isEnabled; }

    bool isConnected() const override;
    bool isReadBusy() const override;
    bool isWriteBusy() const override;
    bool hasPendingIO() const override;

    size_t writeFrame(const uint8_t src[], size_t len) override;
    size_t checkRecvFrame(uint8_t dest[]) override;

    virtual int available() = 0;
    virtual int read() = 0;
    virtual size_t write(const uint8_t *buf, size_t size) = 0;
};


#if ETHERNET_DEBUG_LOGGING && ARDUINO
  #include <Arduino.h>
  #define ETHERNET_DEBUG_PRINT(F, ...) do { if (mesh::isUsbLoggingEnabled()) { mesh::usbLoggingPort().printf("ETH: " F, ##__VA_ARGS__); } } while(0)
  #define ETHERNET_DEBUG_PRINTLN(F, ...) do { if (mesh::isUsbLoggingEnabled()) { mesh::usbLoggingPort().printf("ETH: " F "\n", ##__VA_ARGS__); } } while(0)
  #define ETHERNET_DEBUG_PRINT_IP(name, ip) do { if (mesh::isUsbLoggingEnabled()) { mesh::usbLoggingPort().printf("ETH: " name ": %u.%u.%u.%u" "\n", ip[0], ip[1], ip[2], ip[3]); } } while(0)
#else
  #define ETHERNET_DEBUG_PRINT(...) {}
  #define ETHERNET_DEBUG_PRINTLN(...) {}
  #define ETHERNET_DEBUG_PRINT_IP(...) {}
#endif
