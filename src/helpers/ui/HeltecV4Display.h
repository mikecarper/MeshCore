#pragma once

#include "DisplayDriver.h"
#include "HeltecV4DisplayPolicy.h"
#include "SSD1306Display.h"
#include "ST7789LCDDisplay.h"

// One display interface for the original V4/V4.3 base board and its original
// touch expansion kit. A boot-latched explicit OLED display preference
// suppresses all TFT/touch lifecycle operations while retaining this image.
// R8 uses a different MCU memory/pin configuration.
class HeltecV4Display : public DisplayDriver {
  using Mode = mesh::ui::HeltecV4DisplayMode;
  using Type = mesh::ui::HeltecV4DisplayType;

  TwoWire _display_wire;
  SSD1306Display _oled;
  ST7789LCDDisplay _tft;
  RefCountedDigitalPin* _power;
  DisplayDriver* _active = nullptr;
  Mode _mode = Mode::Auto;
  Type _type = Type::None;
  const char* _status = "not initialized";
  bool _started = false;
  bool _wire_started = false;
  bool _power_claimed = false;
  bool _known_tft = false;
  bool _flipped = false;
  bool _configuration_fault = false;
  uint16_t _rotation = 0;
  uint32_t _generation = 0;

  void claimPower();
  void releasePower();
  void releasePins();
  void deactivate();
  void setPalette();
  void updateDimensions();
  bool startOledBus();
  bool probeOled();
  bool probeTouchAssembly();
  bool startSelected(Type type);
  ColorVal mapColor(ColorVal color) const;

public:
  explicit HeltecV4Display(RefCountedDigitalPin* power = nullptr)
      : DisplayDriver(128, 64), _display_wire(1),
        _oled(nullptr, &_display_wire), _tft(nullptr), _power(power) {}

  bool begin();
  // Boot loader only: a damaged settings record must not trigger hardware
  // discovery on pins which may still have user accessories attached.
  void setConfigurationFault(bool fault) { _configuration_fault = fault; }
  bool setMode(Mode mode);
  Mode mode() const { return _mode; }
  Type selectedType() const { return _type; }
  const char* status() const { return _status; }
  uint32_t generation() const { return _generation; }

  bool isOn() override { return _active && _active->isOn(); }
  bool supportsRotation() const override { return true; }
  bool setRotationDegrees(uint16_t degrees) override;
  void setFlipped(bool flipped) override;
  void turnOn() override;
  void turnOff() override;
  int textLineHeight() override;
  int renderWidth() const override;
  int renderHeight() const override;
  bool setRenderScale(uint8_t scale, float zoom) override;
  void clear() override;
  void startFrame(ColorVal bkg = UIColor::window_bkg) override;
  void setTextSize(int size) override;
  void setCompactText(bool compact) override;
  void setColor(ColorVal color) override;
  void setCursor(int x, int y) override;
  void print(const char* text) override;
  void printWordWrap(const char* text, int max_width) override;
  void fillRect(int x, int y, int w, int h) override;
  void drawRect(int x, int y, int w, int h) override;
  void drawXbm(int x, int y, const uint8_t* bits, int w, int h) override;
  uint16_t getTextWidth(const char* text) override;
  void endFrame() override;
};
