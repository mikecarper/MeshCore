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
#include "PocketGuiConfig.h"
#if UI_POCKET_GUI
  #include "MeshPocketEinkPanel.h"
#endif

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

#if UI_POCKET_GUI
  using Panel = MeshPocketEinkPanel;
  static int partialRefreshLimit(int count) { return count < 0 || count > 10 ? 10 : count; }
#elif defined(EINK_DISPLAY_MODEL)
  using Panel = EINK_DISPLAY_MODEL;
#else
  using Panel = GxEPD2_150_BN;
#endif
  GxEPD2_BW<Panel, Panel::HEIGHT> display;
  static constexpr int kWidth = (DISPLAY_ROTATION & 1) ? Panel::HEIGHT : Panel::WIDTH;
  static constexpr int kHeight = (DISPLAY_ROTATION & 1) ? Panel::WIDTH : Panel::HEIGHT;
  int _text_ascent = 13;
  int _line_height = 22;
  bool _init = false;
  bool _isOn = false;
#if MESH_EINK_BACKLIGHT_CONTROLS
  bool retain_frame = false;
  bool backlight_on = false;
  void turnOffAtTimeout() override;
#endif
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  bool _probe_complete = false;
  bool _present = false;
#endif
  uint16_t _curr_color;
  CRC32 display_crc;
  int last_display_crc_value = 0;
#if UI_POCKET_GUI
  int _cycles_before_full_refresh = partialRefreshLimit(EINK_MAX_PARTIAL_REFRESH);
  int max_partial_refresh = partialRefreshLimit(EINK_MAX_PARTIAL_REFRESH);
#else
  int _cycles_before_full_refresh = EINK_MAX_PARTIAL_REFRESH;
  int max_partial_refresh = EINK_MAX_PARTIAL_REFRESH;
#endif
#if UI_POCKET_GUI
  bool _full_frame = false;
#endif

public:
#if defined(EINK_DISPLAY_MODEL)
  GxEPDDisplay() : DisplayDriver(kWidth, kHeight), display(Panel(PIN_DISPLAY_CS, PIN_DISPLAY_DC, PIN_DISPLAY_RST, PIN_DISPLAY_BUSY)) {}
#else
  GxEPDDisplay() : DisplayDriver(kWidth, kHeight), display(GxEPD2_150_BN(DISP_CS, DISP_DC, DISP_RST, DISP_BUSY)) {}
#endif

#if MESH_EINK_BACKLIGHT_CONTROLS
  void retainFrameAtTimeout(bool retain) { retain_frame = retain; }
  void setBacklight(bool on);
#endif
  bool begin();
#if UI_POCKET_GUI
  Adafruit_GFX& gfx() { return display; }
  void crcAdd(const void* bytes, size_t length) {
    display_crc.update(static_cast<const uint8_t*>(bytes), length);
  }
  void setMaxPartialRefresh(int count) {
    count = partialRefreshLimit(count);
    if (count == max_partial_refresh) return;
    max_partial_refresh = count;
    // Keep a clean refresh that is already due or explicitly requested.
    if (!_full_frame && _cycles_before_full_refresh != 0) resetPartialRefreshCounter();
  }
  void setNativeRotation(int rotation) {
    rotation &= 3;
    if (display.getRotation() == rotation) return;
    display.setRotation(rotation);
    setDimensions(display.width(), display.height());
    display.setFullWindow();
    _full_frame = true;
    forceFullRefresh();
  }
  void setFlipped(bool flipped) override {
    setNativeRotation((DISPLAY_ROTATION + (flipped ? 2 : 0)) & 3);
  }
#endif

  bool isOn() override { return _isOn; }
  bool isEink() override { return true; }
  void forceFullRefresh() override {
    _cycles_before_full_refresh = 0;
#if UI_POCKET_GUI
    // Rotation and GUI changes can request this after startFrame().
    _full_frame = true;
#endif
  }
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
