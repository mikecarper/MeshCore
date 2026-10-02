#pragma once

#include "DisplayDriver.h"
#include <Arduino.h>

namespace mesh { namespace ui {

// Used before UITask exists. Do not initialize the normal power policy here:
// its boot timeout must start when the real UI is ready, not during keygen.
class StartupScreen {
  DisplayDriver* _display = nullptr;
  bool _generating = false;
  uint8_t _phase = 0;
  uint32_t _last_frame = 0;

  void draw() {
    if (_display == nullptr) return;
    DisplayDriver& display = *_display;
    display.startFrame();
    display.setTextSize(1);
    display.setCompactText(false);
    display.setColor(UIColor::primary_txt);
    const int line = display.textLineHeight();
    const char* first = _generating ? "Generating key" : "Starting...";
    const char* second = nullptr;
    if (display.getTextWidth(first) > display.width()) {
      first = _generating ? "Generating" : "Starting";
      if (_generating) second = "key";
      if (display.getTextWidth(first) > display.width()) {
        first = _generating ? "Key" : "Boot";
        if (_generating) second = "gen";
      }
    }
    const int spinner_size = 12;
    const int text_height = line * (second == nullptr ? 1 : 2);
    const int total_height = text_height + (_generating ? spinner_size + 2 : 0);
    int y = (display.height() - total_height) / 2;
    if (y < 0) y = 0;
    display.drawTextCentered(display.width() / 2, y, first);
    if (second != nullptr)
      display.drawTextCentered(display.width() / 2, y + line, second);
    if (_generating) {
      // A rotating bright pair on an eight-dot ring works on monochrome and
      // color panels, without depending on a font's spinner characters.
      static const uint8_t dots[8][2] = {
          {5, 0}, {9, 1}, {10, 5}, {9, 9},
          {5, 10}, {1, 9}, {0, 5}, {1, 1}};
      const int x = (display.width() - spinner_size) / 2;
      const int top = y + text_height + 2;
      if (x >= 0 && top + spinner_size <= display.height()) {
        for (uint8_t i = 0; i < 2; ++i) {
          const uint8_t index = (_phase + i) & 7;
          display.fillRect(x + dots[index][0], top + dots[index][1], 2, 2);
        }
      }
    }
    display.endFrame();
    _last_frame = millis();
  }

public:
  void begin(DisplayDriver* display, bool usb_power) {
    const auto& prefs = displayPowerPrefs();
    const auto mode = (usb_power ? prefs.usb : prefs.battery).mode;
    _display = display;
    if (mode == DisplayMode::Off || mode == DisplayMode::Pairing) {
      if (_display != nullptr && _display->isOn()) _display->turnOff();
      _display = nullptr;
      return;
    }
    if (_display == nullptr) return;
    if (!_display->isOn()) _display->turnOn();
    starting();
  }

  void starting() {
    _generating = false;
    draw();
  }

  void generatingKey() {
    _generating = true;
    _phase = 0;
    draw();
  }

  void service() {
    if (_display == nullptr || !_generating) return;
    // E-paper's partial refresh is slower and needs a gentler refresh cadence.
    const uint32_t interval = _display->isEink() ? 1000UL : 150UL;
    if (uint32_t(millis() - _last_frame) < interval) return;
    _phase = (_phase + 1) & 7;
    draw();
  }

  static void progress(void* context) {
    if (context != nullptr) static_cast<StartupScreen*>(context)->service();
  }
};

} }
