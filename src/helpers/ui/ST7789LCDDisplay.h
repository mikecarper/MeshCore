#pragma once

#include "DisplayDriver.h"
#include <Wire.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <helpers/RefCountedDigitalPin.h>

#ifndef DISPLAY_ROTATION
  #define DISPLAY_ROTATION 3
#endif

#if defined(HELTEC_V4_R8_TFT) && defined(HELTEC_TFT_DISPLAY_AUTODETECT) \
    && HELTEC_TFT_DISPLAY_AUTODETECT == 1
  #define MESHCORE_HELTEC_R8_TFT_DETECTION 1
#endif

#if defined(HELTEC_V4_R8_TFT) && defined(HELTEC_TFT_RUNTIME_ROTATION) \
    && HELTEC_TFT_RUNTIME_ROTATION == 1
  #define MESHCORE_HELTEC_R8_RUNTIME_ROTATION 1
#endif

class ST7789LCDDisplay : public DisplayDriver {
  #if defined(LILYGO_TDECK) || defined(HELTEC_LORA_V4_TFT) || defined(HELTEC_V4_R8_TFT)
    SPIClass displaySPI;
  #endif
  Adafruit_ST7789 display;
  bool _isOn;
  bool _panel_ready = false;   // panel has been configured at least once
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  bool _probe_complete = false;
  bool _present = false;
#endif
#ifdef HELTEC_V4_R8_TFT
#ifdef MESHCORE_HELTEC_R8_RUNTIME_ROTATION
  uint8_t _base_rotation = DISPLAY_ROTATION & 3;
#endif
  bool _rotation_pending = false;
#endif
  bool _flipped = false;
  uint16_t _color;
  RefCountedDigitalPin* _peripher_power;

  static constexpr int kWidth = (DISPLAY_ROTATION & 1) ? 320 : 240;
  static constexpr int kHeight = (DISPLAY_ROTATION & 1) ? 240 : 320;

  uint8_t effectiveRotation() const;
  void applyRotation();
  bool i2c_probe(TwoWire& wire, uint8_t addr);
public:
#ifdef USE_PIN_TFT
  ST7789LCDDisplay(RefCountedDigitalPin* peripher_power=NULL) : DisplayDriver(kWidth, kHeight),
      display(PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_SDA, PIN_TFT_SCL, PIN_TFT_RST),
      _peripher_power(peripher_power)
  {
    _isOn = false;
  }
#elif defined(LILYGO_TDECK) || defined(HELTEC_LORA_V4_TFT) || defined(HELTEC_V4_R8_TFT)
  ST7789LCDDisplay(RefCountedDigitalPin* peripher_power=NULL) : DisplayDriver(kWidth, kHeight),
      displaySPI(HSPI),
      display(&displaySPI, PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_RST),
      _peripher_power(peripher_power)
  {
    _isOn = false;
  }
#else
  ST7789LCDDisplay(RefCountedDigitalPin* peripher_power=NULL) : DisplayDriver(kWidth, kHeight),
      display(&SPI, PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_RST),
      _peripher_power(peripher_power)
  {
    _isOn = false;
  }
#endif
  bool begin();

  bool isOn() override { return _isOn; }
#ifdef MESHCORE_HELTEC_R8_RUNTIME_ROTATION
  bool supportsRotation() const override { return true; }
#endif
  bool setRotationDegrees(uint16_t degrees) override;
  void turnOn() override;
  void turnOff() override;
  void clear() override;
  void startFrame(ColorVal bkg = UIColor::window_bkg) override;
  void setFlipped(bool flipped) override;
  void setTextSize(int sz) override;
  void setColor(ColorVal c) override;
  void setCursor(int x, int y) override;
  void print(const char* str) override;
  void fillRect(int x, int y, int w, int h) override;
  void drawRect(int x, int y, int w, int h) override;
  void drawXbm(int x, int y, const uint8_t* bits, int w, int h) override;
  uint16_t getTextWidth(const char* str) override;
  void endFrame() override;
};
