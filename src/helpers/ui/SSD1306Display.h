#pragma once

#include "DisplayDriver.h"
#include <Wire.h>
#include <Adafruit_GFX.h>
#define SSD1306_NO_SPLASH
#include <Adafruit_SSD1306.h>
#include <helpers/RefCountedDigitalPin.h>

#ifndef PIN_OLED_RESET
  #define PIN_OLED_RESET        21 // Reset pin # (or -1 if sharing Arduino reset pin)
#endif

#ifndef DISPLAY_ADDRESS
  #define DISPLAY_ADDRESS   0x3C
#endif

class SSD1306Display : public DisplayDriver {
public:
  static constexpr int16_t PANEL_WIDTH = 128;
  static constexpr int16_t PANEL_HEIGHT = 64;
  static constexpr size_t FRAMEBUFFER_BYTES = PANEL_WIDTH * ((PANEL_HEIGHT + 7) / 8);
private:
  Adafruit_SSD1306 display;
  bool _isOn;
  bool _panel_ready = false;
  bool _flipped = false;
#ifdef DISPLAY_ROTATION
  uint8_t _base_rotation = DISPLAY_ROTATION & 3;
#else
  uint8_t _base_rotation = 0;
#endif
  uint8_t _color;
  int _text_size = 1;
  RefCountedDigitalPin* _peripher_power;

  bool i2c_probe(TwoWire& wire, uint8_t addr);
  void applyRotation();
public:
  SSD1306Display(RefCountedDigitalPin* peripher_power=NULL) : DisplayDriver(PANEL_WIDTH, PANEL_HEIGHT),
      display(PANEL_WIDTH, PANEL_HEIGHT, &Wire, PIN_OLED_RESET),
      _peripher_power(peripher_power)
  {
    _isOn = false; 
  }
  bool begin();

  bool isOn() override { return _isOn; }
#if defined(COMPANION_PAIRING_UI_HIL)
  // Test-only access to the same pixels sent by endFrame(), never a mock UI.
  const uint8_t* hilFramebuffer() { return display.getBuffer(); }
#endif
  bool supportsRotation() const override { return true; }
  bool setRotationDegrees(uint16_t degrees) override;
  void setFlipped(bool flipped) override;
  void turnOn() override;
  void turnOff() override;
  void clear() override;
  void startFrame(ColorVal bkg = UIColor::window_bkg) override;
  void setTextSize(int sz) override;
  int textLineHeight() override { return 8 * _text_size; }
  void setColor(ColorVal c) override;
  void setCursor(int x, int y) override;
  void print(const char* str) override;
  void fillRect(int x, int y, int w, int h) override;
  void drawRect(int x, int y, int w, int h) override;
  void drawXbm(int x, int y, const uint8_t* bits, int w, int h) override;
  uint16_t getTextWidth(const char* str) override;
  void endFrame() override;
};
