#pragma once

#include "../../../mocks/Arduino.h"
#include <cassert>

using esp_event_base_t = const char*;
using esp_event_handler_t = void (*)(void*, esp_event_base_t, int32_t, void*);
constexpr int ARDUINO_USB_CDC_ANY_EVENT = -1;
constexpr int ARDUINO_USB_CDC_DISCONNECTED_EVENT = 1;
constexpr int ARDUINO_USB_CDC_LINE_STATE_EVENT = 2;
struct arduino_usb_cdc_event_data_t {
  struct { bool dtr; bool rts; } line_state{};
};

inline bool mock_isr = false;
inline bool xPortInIsrContext() { return mock_isr; }

class MockSerial : public Stream {
 public:
  int available() override { return rx_count; }
  int read() override { return rx_count > 0 ? (--rx_count, 'v') : -1; }
  int peek() override { return rx_count > 0 ? 'v' : -1; }
  int availableForWrite() override {
    ++blocking_calls;
    assert(false && "mode0 must not acquire the USBCDC TX mutex");
    return 0;
  }
  size_t write(const uint8_t*, size_t) override {
    ++blocking_calls;
    assert(false && "mode0 must never call USBCDC::write");
    return 0;
  }
  void flush() override {
    ++blocking_calls;
    assert(false && "mode0 must never call USBCDC::flush");
  }
  void setDebugOutput(bool enabled) { debug_enabled = enabled; }
  void onEvent(int event, esp_event_handler_t handler) {
    assert(event == ARDUINO_USB_CDC_ANY_EVENT);
    callback = handler;
    ++registrations;
  }
  int blocking_calls = 0;
  int rx_count = 0;
  int registrations = 0;
  bool debug_enabled = true;
  esp_event_handler_t callback = nullptr;
};

extern MockSerial Serial;
