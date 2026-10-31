#pragma once

#include "SH1106Display.h"
#include "GxEPDDisplay.h"

// L1/Lite/Pro and L1 E-Ink share the same MCU, storage and radio wiring.
// Construct only the detected backend, sharing the larger static allocation.
// No OLED framebuffer is allocated on e-paper or headless hardware.
class WioL1Display : public DisplayDriver {
public:
  enum class Type : uint8_t { None, Oled, Eink };

private:
  union Storage {
    SH1106Display oled;
    GxEPDDisplay eink;
    Storage() {}
    ~Storage() {}
  } _storage;
  DisplayDriver* _active = nullptr;
  Type _type = Type::None;
  Type _constructed = Type::None;
  bool _detected = false;
  bool _started = false;

public:
  WioL1Display() : DisplayDriver(128, 64) {}
  ~WioL1Display();
  Type detect();
  Type selectedType() const { return _type; }
  uint16_t defaultTimeoutSeconds() const { return _type == Type::Eink ? 900 : 15; }
  bool begin();
  bool isOn() override { return _active && _active->isOn(); }
  bool isEink() override { return _type == Type::Eink; }
  void forceFullRefresh() override { if (_active) _active->forceFullRefresh(); }
  void turnOn() override { if (_active) _active->turnOn(); }
  void turnOff() override { if (isOn()) _active->turnOff(); }
  int textLineHeight() override { return _active ? _active->textLineHeight() : 10; }
  void clear() override { if (isOn()) _active->clear(); }
  void startFrame(ColorVal bkg = UIColor::window_bkg) override { if (isOn()) _active->startFrame(bkg); }
  void setTextSize(int size) override { if (_active) _active->setTextSize(size); }
  void setColor(ColorVal color) override { if (_active) _active->setColor(color); }
  void setCursor(int x, int y) override { if (isOn()) _active->setCursor(x, y); }
  void print(const char* text) override { if (isOn()) _active->print(text); }
  void fillRect(int x, int y, int w, int h) override { if (isOn()) _active->fillRect(x, y, w, h); }
  void drawRect(int x, int y, int w, int h) override { if (isOn()) _active->drawRect(x, y, w, h); }
  void drawXbm(int x, int y, const uint8_t* bits, int w, int h) override {
    if (isOn()) _active->drawXbm(x, y, bits, w, h);
  }
  uint16_t getTextWidth(const char* text) override { return _active ? _active->getTextWidth(text) : 0; }
  void endFrame() override { if (isOn()) _active->endFrame(); }
};
