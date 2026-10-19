#pragma once

#include <SPI.h>
#include <Wire.h>

#define ENABLE_GxEPD2_GFX 0

#include <GxEPD2_BW.h>
#include <GxEPD2_3C.h>
#include <GxEPD2_4C.h>
#include <GxEPD2_7C.h>
#include <Fonts/FreeSans9pt7b.h>
#include <Fonts/FreeSansBold12pt7b.h>
#include <Fonts/FreeSans18pt7b.h>
#include <CRC32.h>

#include "DisplayDriver.h"

#ifndef EINK_MAX_PARTIAL_REFRESH
  // 60 to prevent ghosting when refreshing every minute ...
  // set to -1 to disable the counter
  #define EINK_MAX_PARTIAL_REFRESH -1
#endif

#ifndef DISPLAY_ROTATION
  #define DISPLAY_ROTATION 3
#endif

#if defined(LILYGO_TECHO_LITE) && defined(TECHO_LITE_DISPLAY_AUTODETECT) \
    && TECHO_LITE_DISPLAY_AUTODETECT == 1
  #define MESHCORE_TECHO_LITE_DISPLAY_DETECTION 1
#endif

class GxEPDDisplay : public DisplayDriver {

#if defined(EINK_DISPLAY_MODEL)
  GxEPD2_BW<EINK_DISPLAY_MODEL, EINK_DISPLAY_MODEL::HEIGHT> display;
#else
  GxEPD2_BW<GxEPD2_150_BN, 200> display;
#endif
#if defined(EINK_DISPLAY_MODEL)
  using Panel = EINK_DISPLAY_MODEL;
#else
  using Panel = GxEPD2_150_BN;
#endif
  static constexpr int kWidth = (DISPLAY_ROTATION & 1) ? Panel::HEIGHT : Panel::WIDTH;
  static constexpr int kHeight = (DISPLAY_ROTATION & 1) ? Panel::WIDTH : Panel::HEIGHT;
  int _text_ascent = 13;
  int _line_height = 22;
  bool _init = false;
  bool _isOn = false;
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  bool _probe_complete = false;
  bool _present = false;
#endif
  uint16_t _curr_color;
  CRC32 display_crc;
  int last_display_crc_value = 0;
  int _cycles_before_full_refresh;
  int max_partial_refresh = EINK_MAX_PARTIAL_REFRESH; // so this can be changed by an option after

public:
#if defined(EINK_DISPLAY_MODEL)
  GxEPDDisplay() : DisplayDriver(kWidth, kHeight), display(EINK_DISPLAY_MODEL(PIN_DISPLAY_CS, PIN_DISPLAY_DC, PIN_DISPLAY_RST, PIN_DISPLAY_BUSY)) {}
#else
  GxEPDDisplay() : DisplayDriver(kWidth, kHeight), display(GxEPD2_150_BN(DISP_CS, DISP_DC, DISP_RST, DISP_BUSY)) {}
#endif

  bool begin();

  bool isOn() override { return _isOn; }
  bool isEink() override { return true; }
  void forceFullRefresh() override { _cycles_before_full_refresh = 0; };
  void resetPartialRefreshCounter() { _cycles_before_full_refresh = max_partial_refresh; };
  void turnOn() override;
  void turnOff() override;
  void clear() override;
  void startFrame(ColorVal bkg = UIColor::window_bkg) override;
  void setTextSize(int sz) override;
  int textLineHeight() override { return _line_height; }
  void setColor(ColorVal c) override;
  void setCursor(int x, int y) override;
  void print(const char* str) override;
  void fillRect(int x, int y, int w, int h) override;
  void drawRect(int x, int y, int w, int h) override;
  void drawXbm(int x, int y, const uint8_t* bits, int w, int h) override;
  uint16_t getTextWidth(const char* str) override;
  void endFrame() override;
};
