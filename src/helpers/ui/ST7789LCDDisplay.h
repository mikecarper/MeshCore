#pragma once

#include "DisplayDriver.h"
#include <Wire.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#ifdef LILYGO_TLORA_PAGER
#include <Adafruit_ST7796S.h>
#else
#include <Adafruit_ST7789.h>
#endif
#if defined(LILYGO_TDECK) || defined(LILYGO_TLORA_PAGER)
#define MESHCORE_BUFFERED_SPI_DISPLAY 1
#endif
#include <helpers/RefCountedDigitalPin.h>

#ifdef MESHCORE_BUFFERED_SPI_DISPLAY
class TDeckCanvas;
#endif

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

#if defined(MESHCORE_HELTEC_R8_RUNTIME_ROTATION) || \
    (defined(HELTEC_V4_DISPLAY_UNIFIED) && HELTEC_V4_DISPLAY_UNIFIED)
  #define MESHCORE_HELTEC_TFT_RUNTIME_ROTATION 1
#endif

class ST7789LCDDisplay : public DisplayDriver {
  #if defined(LILYGO_TDECK) || defined(HELTEC_LORA_V4_TFT) || defined(HELTEC_V4_R8_TFT)
    SPIClass displaySPI;
  #endif
#ifdef LILYGO_TLORA_PAGER
  Adafruit_ST7796S display;
#else
  Adafruit_ST7789 display;
#endif
  bool _isOn;
  bool _panel_ready = false;   // panel has been configured at least once
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  bool _probe_complete = false;
  bool _present = false;
#endif
#if defined(HELTEC_V4_R8_TFT) || defined(HELTEC_V4_DISPLAY_UNIFIED)
#ifdef MESHCORE_HELTEC_TFT_RUNTIME_ROTATION
  uint8_t _base_rotation = DISPLAY_ROTATION & 3;
#endif
  bool _rotation_pending = false;
#endif
  bool _flipped = false;
  uint16_t _color;
  RefCountedDigitalPin* _peripher_power;

#ifdef LILYGO_TLORA_PAGER
  static constexpr int kWidth = 480;
  static constexpr int kHeight = 222;
#else
  static constexpr int kWidth = (DISPLAY_ROTATION & 1) ? 320 : 240;
  static constexpr int kHeight = (DISPLAY_ROTATION & 1) ? 240 : 320;
#endif

#ifdef MESHCORE_BUFFERED_SPI_DISPLAY
  friend class TDeckCanvas;
  TDeckCanvas* _canvas = nullptr;
  bool _buffered_rendering = true;
  bool _canvas_attempted = false;
  bool _frame_active = false;
  bool _render_callback_active = false;
  void (*_render_yield)() = nullptr;
  uint32_t _last_render_yield_us = 0;

  bool prepareCanvas();
  void serviceRender(bool force = false);
  void flushCanvas(int x, int y, int w, int h);
  void finishCanvasPrimitive(int x, int y, int w, int h);
#endif

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
#if defined(HELTEC_V4_DISPLAY_UNIFIED) && HELTEC_V4_DISPLAY_UNIFIED
      // The V4 radio uses SPIClass's default HSPI controller. A separate
      // controller is required so TFT setup/sleep cannot remap or stop it.
      displaySPI(FSPI),
#else
      displaySPI(HSPI),
#endif
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

#ifdef MESHCORE_BUFFERED_SPI_DISPLAY
  ~ST7789LCDDisplay();
  // Call only between frames. Disabling buffering retains the allocation for
  // same-image comparisons with the original direct-to-panel renderer.
  void setBufferedRendering(bool enabled);
  bool isBufferedRendering() const;
  // The callback runs synchronously with TFT CS released and no SPI transaction.
  // It may service the radio, but must not redraw or re-enter the application loop.
  void setRenderYieldCallback(void (*callback)()) { _render_yield = callback; }
#endif

  bool isOn() override { return _isOn; }
#ifdef MESHCORE_HELTEC_TFT_RUNTIME_ROTATION
  bool supportsRotation() const override { return true; }
#endif
#if defined(HELTEC_V4_DISPLAY_UNIFIED) && HELTEC_V4_DISPLAY_UNIFIED
  // The combined V4 image releases the LCD SPI peripheral before the shared
  // GPIO17/18 pair is used by the OLED bus again.
  void releaseBus() {
    displaySPI.end();
    _panel_ready = false;
    _isOn = false;
  }
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
