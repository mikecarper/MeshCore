#pragma once

#include "DisplayDriver.h"
#include <Wire.h>
#include <Adafruit_GFX.h>
#define SH110X_NO_SPLASH
#include <Adafruit_SH110X.h>

#ifndef PIN_OLED_RESET
#define PIN_OLED_RESET -1
#endif

#ifndef DISPLAY_ADDRESS
#define DISPLAY_ADDRESS 0x3C
#endif

// GrayOLED reuses the framebuffer but replaces i2c_dev on every begin().
// Retire the previous adapter first so success and failure retries stay bounded.
class BoundedSH1106G : public Adafruit_SH1106G
{
public:
  using Adafruit_SH1106G::Adafruit_SH1106G;

  bool begin(uint8_t addr, bool reset) {
    delete i2c_dev;
    i2c_dev = nullptr;
    return Adafruit_SH1106G::begin(addr, reset);
  }
};

class SH1106Display : public DisplayDriver
{
  BoundedSH1106G display;
  bool _initialized;
  bool _isOn;
  uint8_t _color;

  bool i2c_probe(TwoWire &wire, uint8_t addr);

public:
  static constexpr uint16_t DISPLAY_WIDTH = 128;
  static constexpr uint16_t DISPLAY_HEIGHT = 64;
  static constexpr uint16_t FRAMEBUFFER_BYTES = DISPLAY_WIDTH * ((DISPLAY_HEIGHT + 7) / 8);

  SH1106Display() : DisplayDriver(DISPLAY_WIDTH, DISPLAY_HEIGHT),
                    display(DISPLAY_WIDTH, DISPLAY_HEIGHT, &Wire, PIN_OLED_RESET) {
    _initialized = false;
    _isOn = false;
  }
  bool begin();

  bool isOn() override { return _initialized && _isOn; }
  void turnOn() override;
  void turnOff() override;
  void clear() override;
  void startFrame(ColorVal bkg = UIColor::window_bkg) override;
  void setTextSize(int sz) override;
  void setColor(ColorVal c) override;
  void setCursor(int x, int y) override;
  void print(const char *str) override;
  void fillRect(int x, int y, int w, int h) override;
  void drawRect(int x, int y, int w, int h) override;
  void drawXbm(int x, int y, const uint8_t *bits, int w, int h) override;
  uint16_t getTextWidth(const char *str) override;
  void endFrame() override;
};
