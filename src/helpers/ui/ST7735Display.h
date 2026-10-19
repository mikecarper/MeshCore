#pragma once

#include "DisplayDriver.h"
#include <Wire.h>
#include <SPI.h>
#include <helpers/RefCountedDigitalPin.h>

// The optional-display hardware is Tracker V1/V1.1. Other boards using this
// renderer (including Tracker V2, T096 and T1) keep their existing behavior.
#if defined(WIRELESS_TRACKER_DISPLAY_AUTODETECT) && WIRELESS_TRACKER_DISPLAY_AUTODETECT \
    && (defined(HELTEC_LORA_V3) || defined(HELTEC_TRACKER_V1_1)) \
    && !defined(HELTEC_TRACKER_V2) && !defined(HELTEC_T096) && !defined(HELTEC_T1)
  #define MESHCORE_TRACKER_DISPLAY_AUTODETECT 1
#endif

class ST7735Display : public DisplayDriver {
  // One UI coordinate is one pixel on every 160x80 ST7735 panel.
  static constexpr int kLogicalWidth = 160, kLogicalHeight = 80;
  bool _isOn;
  RefCountedDigitalPin* _peripher_power;
#ifdef MESHCORE_TRACKER_DISPLAY_AUTODETECT
  bool _probeComplete = false;
  bool _present = false;
#endif

  bool i2c_probe(TwoWire& wire, uint8_t addr);
public:
#ifdef USE_PIN_TFT
  ST7735Display(RefCountedDigitalPin* peripher_power=NULL) : DisplayDriver(kLogicalWidth, kLogicalHeight),
    //  display(PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_SDA, PIN_TFT_SCL, PIN_TFT_RST),
      _peripher_power(peripher_power)
  {
    _isOn = false;
  }
#else
  ST7735Display(RefCountedDigitalPin* peripher_power=NULL) : DisplayDriver(kLogicalWidth, kLogicalHeight),
    //  display(&SPI1, PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_RST),
      _peripher_power(peripher_power)
  {
    _isOn = false;
  }
#endif
  bool begin();

  bool isOn() override { return _isOn; }
  void turnOn() override;
  void turnOff() override;
  void clear() override;
  void startFrame(ColorVal bkg = UIColor::window_bkg) override;
  void setTextSize(int sz) override;
  int textLineHeight() override;
  void setColor(ColorVal c) override;
  void setCursor(int x, int y) override;
  void print(const char* str) override;
  void fillRect(int x, int y, int w, int h) override;
  void drawRect(int x, int y, int w, int h) override;
  void drawXbm(int x, int y, const uint8_t* bits, int w, int h) override;
  uint16_t getTextWidth(const char* str) override;
  void endFrame() override;

protected:
  void _resetAndInit();
};
