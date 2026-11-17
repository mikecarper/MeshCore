#if defined(HELTEC_V4_DISPLAY_UNIFIED) && HELTEC_V4_DISPLAY_UNIFIED

#include "HeltecV4Display.h"
#include "HeltecV4DisplayProbe.h"
#include <helpers/ui/HeltecV4GpioPolicy.h>
#include "ColorTheme.h"

// Runtime budget guard: both concrete drivers/private Wire are linked globals,
// but count them conservatively with the one retained OLED heap framebuffer.
// The separate 4 KiB SDK/bus allowance covers I2C/SPI locks, I2C driver state,
// command links and both Wire buffers. TFT has no dynamic pixel canvas.
static_assert(SSD1306Display::FRAMEBUFFER_BYTES == 1024,
              "Update the unified Heltec V4 OLED framebuffer budget");
static_assert(sizeof(HeltecV4Display) + SSD1306Display::FRAMEBUFFER_BYTES + 32 <= 2048,
              "Update the unified Heltec V4 display runtime budget");
static_assert(I2C_BUFFER_LENGTH <= 128,
              "Update the unified Heltec V4 private I2C runtime budget");

extern const char meshcore_capability_display_heltec_v4_unified[] =
    "meshcore.capability.display.heltec_v4.unified.v1";
extern const char meshcore_capability_display_autodetect_heltec_v4[] =
    "meshcore.capability.display.autodetect.heltec_v4.v1";

// Both backends are linked into this image. Only the wrapper owns the shared
// palette; monochrome OLED and RGB TFT colors are selected at activation.
ColorVal UIColor::window_bkg = 0;
ColorVal UIColor::title_bkg = 0;
ColorVal UIColor::title_txt = 1;
ColorVal UIColor::primary_txt = 1;
ColorVal UIColor::secondary_txt = 1;
ColorVal UIColor::warning_txt = 1;
ColorVal UIColor::popup_bkg = 0;
ColorVal UIColor::popup_txt = 1;
ColorVal UIColor::corp_blue = 1;

namespace {

constexpr int kOledSda = 17;
constexpr int kOledScl = 18;
constexpr int kTouchSda = 47;
constexpr int kTouchScl = 48;
constexpr int kTouchReset = 44;
constexpr uint8_t kTouchAddress = 0x2E;

// The original expansion kit routes the LCD's SDO to ground, so its LCD
// cannot identify itself through SPI. Read the stock CHSC touch assembly's
// documented identity instead. This identifies the assembly, not LCD health.
struct TouchIo {
  TwoWire& wire;

  void reset() {
    digitalWrite(kTouchReset, LOW);
    pinMode(kTouchReset, OUTPUT);
    delay(20);
    digitalWrite(kTouchReset, HIGH);
  }
  void delayMs(unsigned ms) { delay(ms); }
  bool write(const uint8_t* data, size_t length) {
    wire.beginTransmission(kTouchAddress);
    const bool complete = wire.write(data, length) == length;
    return wire.endTransmission() == 0 && complete;
  }
  bool readRegister(uint16_t address, uint8_t* data, size_t length) {
    wire.beginTransmission(kTouchAddress);
    const uint8_t reg[] = {uint8_t(address >> 8), uint8_t(address)};
    const bool complete = wire.write(reg, sizeof(reg)) == sizeof(reg);
    // The manufacturer's DMA-read sequence uses STOP between the address
    // write and the read, rather than relying on repeated-start support.
    if (wire.endTransmission() != 0 || !complete) return false;
    const size_t received = wire.requestFrom(kTouchAddress, uint8_t(length));
    const bool ok = received == length && wire.available() >= int(length);
    for (size_t i = 0; i < length; ++i) {
      data[i] = wire.available() ? uint8_t(wire.read()) : 0xFF;
    }
    while (wire.available()) wire.read();
    return ok;
  }
};

}  // namespace

void HeltecV4Display::claimPower() {
  if (_power && !_power_claimed) {
    _power->claim();
    _power_claimed = true;
    delay(10);
  }
}

void HeltecV4Display::releasePower() {
  if (_power && _power_claimed) {
    _power->release();
    _power_claimed = false;
  }
}

void HeltecV4Display::releasePins() {
  if (_configuration_fault) return;
  if (_wire_started) {
    _display_wire.end();
    _wire_started = false;
  }
  if (mesh::ui::heltecV4OledExpansionActive()) {
    // These are the only display pads owned by an OLED-expansion boot.
    // In particular, never release or toggle accessory outputs on 33/44/47/48.
    const int pins[] = {kOledSda, kOledScl, PIN_OLED_RESET};
    for (int pin : pins) if (pin >= 0) pinMode(pin, INPUT);
    _known_tft = false;
    return;
  }
  // No inactive driver may back-power a disconnected panel. Wire on GPIO4/3
  // belongs to RTC/environmental sensors and is never stopped or remapped.
  const int pins[] = {PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_SDA, PIN_TFT_SCL,
                      PIN_TFT_RST, PIN_TFT_LEDA_CTL, PIN_OLED_RESET,
                      kTouchSda, kTouchScl, kTouchReset};
  for (int pin : pins) if (pin >= 0) pinMode(pin, INPUT);
  if (_known_tft) {
    // The original kit's LCD has an independent supply. Keep its backlight
    // and reset positively off just like the dedicated TFT driver; floating
    // either output is not an off command. Cold headless/off never does this.
    digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);
    pinMode(PIN_TFT_LEDA_CTL, OUTPUT);
    digitalWrite(PIN_TFT_RST, LOW);
    pinMode(PIN_TFT_RST, OUTPUT);
  }
}

void HeltecV4Display::deactivate() {
  if (_active && _active->isOn()) _active->turnOff();
  if (_type == Type::Tft && !mesh::ui::heltecV4OledExpansionActive()) _tft.releaseBus();
  _active = nullptr;
  _type = Type::None;
  releasePins();
  releasePower();
}

bool HeltecV4Display::startOledBus() {
  if (_wire_started) return true;
  _wire_started = _display_wire.begin(kOledSda, kOledScl, 100000);
  if (_wire_started) _display_wire.setTimeOut(20);
  return _wire_started;
}

bool HeltecV4Display::probeOled() {
  if (_configuration_fault) return false;
  // Keep the TFT deselected while the same GPIO17/18 pair is briefly used
  // as OLED I2C. GPIO21 is OLED reset / TFT backlight; release it before ACK.
  if (!mesh::ui::heltecV4OledExpansionActive()) {
    digitalWrite(PIN_TFT_CS, HIGH);
    pinMode(PIN_TFT_CS, OUTPUT);
  }
  digitalWrite(PIN_OLED_RESET, LOW);
  pinMode(PIN_OLED_RESET, OUTPUT);
  delay(10);
  digitalWrite(PIN_OLED_RESET, HIGH);
  delay(10);
  if (!startOledBus()) return false;
  _display_wire.beginTransmission(DISPLAY_ADDRESS);
  return _display_wire.endTransmission() == 0;
}

bool HeltecV4Display::probeTouchAssembly() {
  if (_configuration_fault || mesh::ui::heltecV4OledExpansionActive()) return false;
  if (_wire_started) {
    _display_wire.end();
    _wire_started = false;
  }
  pinMode(kOledSda, INPUT);
  pinMode(kOledScl, INPUT);
  // Avoid flashing the backlight while resetting and identifying touch.
  digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);
  pinMode(PIN_TFT_LEDA_CTL, OUTPUT);
  if (!_display_wire.begin(kTouchSda, kTouchScl, 100000)) return false;
  _display_wire.setTimeOut(20);
  TouchIo io{_display_wire};
  const bool present = mesh::ui::probeHeltecV4Touch(io);
  _display_wire.end();
  pinMode(kTouchSda, INPUT);
  pinMode(kTouchScl, INPUT);
  if (!present) pinMode(kTouchReset, INPUT);
  return present;
}

void HeltecV4Display::setPalette() {
  using namespace mesh::ui::color_theme;
  const bool color = _type == Type::Tft;
  UIColor::window_bkg = color ? WINDOW_BACKGROUND : 0;
  UIColor::title_bkg = color ? TITLE_BACKGROUND : 0;
  UIColor::title_txt = color ? TEXT : 1;
  UIColor::primary_txt = color ? TEXT : 1;
  UIColor::secondary_txt = color ? SECONDARY_TEXT : 1;
  UIColor::warning_txt = color ? WARNING_TEXT : 1;
  UIColor::popup_bkg = color ? POPUP_BACKGROUND : 0;
  UIColor::popup_txt = color ? TEXT : 1;
  UIColor::corp_blue = color ? ACCENT : 1;
}

void HeltecV4Display::updateDimensions() {
  setDimensions(_active ? _active->width() : 128,
                _active ? _active->height() : 64);
}

bool HeltecV4Display::startSelected(Type type) {
  if (_configuration_fault) return false;
  if (type == Type::Tft && mesh::ui::heltecV4OledExpansionActive()) return false;
  bool ready = false;
  if (type == Type::Oled && startOledBus()) {
    _oled.setRotationDegrees(_rotation);
    _oled.setFlipped(_flipped);
    ready = _oled.begin();
    if (ready) {
      _active = &_oled;
      _known_tft = false;
    }
  } else if (type == Type::Tft) {
    _known_tft = true;
    if (_wire_started) {
      _display_wire.end();
      _wire_started = false;
    }
    _tft.setRotationDegrees(_rotation);
    _tft.setFlipped(_flipped);
    ready = _tft.begin();
    if (ready) _active = &_tft;
  }
  _type = ready ? type : Type::None;
  if (!ready) {
    releasePins();
    releasePower();
  }
  setPalette();
  updateDimensions();
  return ready;
}

bool HeltecV4Display::begin() {
  const volatile char* unified = meshcore_capability_display_heltec_v4_unified;
  (void)*unified;
  if (_configuration_fault) {
    _started = true; ++_generation;
    _active = nullptr; _type = Type::None;
    _status = "saved display config invalid";
    setPalette(); updateDimensions(); return false;
  }
  // A boot with display.type=off must not enable any display/probe outputs.
  if (_started) deactivate();
  _started = true;
  ++_generation;
  if (_mode == Mode::Off) {
    _status = "off";
    setPalette();
    updateDimensions();
    return false;
  }
  claimPower();
  bool oled = false, tft = false;
  if (_mode == Mode::Auto) {
    const volatile char* autodetect = meshcore_capability_display_autodetect_heltec_v4;
    (void)*autodetect;
    oled = probeOled();
    if (!oled && !mesh::ui::heltecV4OledExpansionActive()) tft = probeTouchAssembly();
  }
  const Type selected = mesh::ui::selectHeltecV4Display(_mode, oled, tft);
  if (selected == Type::None) {
    _known_tft = false;  // A rescan found no assembly; leave header pins free.
    releasePins();
    releasePower();
    _status = "none detected";
    setPalette();
    updateDimensions();
    return false;
  }
  const bool ready = startSelected(selected);
  _status = !ready ? "display initialization failed"
      : selected == Type::Oled ? "OLED"
      : _mode == Mode::Tft ? "TFT (forced)" : "TFT";
  return ready;
}

bool HeltecV4Display::setMode(Mode mode) {
  if (unsigned(mode) > unsigned(Mode::Off)) return false;
  if (_configuration_fault && mode != Mode::Off) return false;
  if (mode == Mode::Tft && mesh::ui::heltecV4OledExpansionActive()) return false;
  _mode = mode;
  if (_started) begin();
  // A valid off/headless choice is successful even though begin() reports no
  // active screen. The CLI can report status() independently of setting save.
  return true;
}

bool HeltecV4Display::setRotationDegrees(uint16_t degrees) {
  if (degrees > 270 || degrees % 90 != 0) return false;
  _rotation = degrees;
  // Only touch the selected backend: a dormant OLED may retain an allocated
  // framebuffer and must not write into a bus now owned by the TFT.
  if (isOn()) _active->setRotationDegrees(degrees);
  updateDimensions();
  return true;
}

void HeltecV4Display::setFlipped(bool flipped) {
  _flipped = flipped;
  if (isOn()) _active->setFlipped(flipped);
  updateDimensions();
}

void HeltecV4Display::turnOn() {
  if (_configuration_fault) return;
  if (!_active || _active->isOn()) return;
  claimPower();
  // The wrapper owns the rail. Reinitialize after it was cut, even though
  // each embedded driver's nullptr power owner would otherwise skip reset.
  const Type type = _type;
  _active = nullptr;
  if (!startSelected(type)) _status = "display initialization failed";
}

void HeltecV4Display::turnOff() {
  if (_configuration_fault) return;
  if (_active && _active->isOn()) _active->turnOff();
  if (_type == Type::Tft && !mesh::ui::heltecV4OledExpansionActive()) _tft.releaseBus();
  releasePins();
  releasePower();
}

ColorVal HeltecV4Display::mapColor(ColorVal color) const {
  if (_type != Type::Oled) return color;
  // SSD1306 uses literal0/1/2, not RGB565 or just the RGB value's low bit.
  return mesh::ui::color_theme::isBackground(color) ? 0 : 1;
}

int HeltecV4Display::textLineHeight() { return _active ? _active->textLineHeight() : 8; }
int HeltecV4Display::renderWidth() const { return _active ? _active->renderWidth() : width(); }
int HeltecV4Display::renderHeight() const { return _active ? _active->renderHeight() : height(); }
bool HeltecV4Display::setRenderScale(uint8_t scale, float zoom) {
  return _active && _active->setRenderScale(scale, zoom);
}
void HeltecV4Display::clear() { if (isOn()) _active->clear(); }
void HeltecV4Display::startFrame(ColorVal bkg) { if (isOn()) _active->startFrame(mapColor(bkg)); }
void HeltecV4Display::setTextSize(int size) { if (_active) _active->setTextSize(size); }
void HeltecV4Display::setCompactText(bool compact) { if (_active) _active->setCompactText(compact); }
void HeltecV4Display::setColor(ColorVal color) { if (_active) _active->setColor(mapColor(color)); }
void HeltecV4Display::setCursor(int x, int y) { if (isOn()) _active->setCursor(x, y); }
void HeltecV4Display::print(const char* text) { if (isOn()) _active->print(text); }
void HeltecV4Display::printWordWrap(const char* text, int max_width) {
  if (isOn()) _active->printWordWrap(text, max_width);
}
void HeltecV4Display::fillRect(int x, int y, int w, int h) { if (isOn()) _active->fillRect(x, y, w, h); }
void HeltecV4Display::drawRect(int x, int y, int w, int h) { if (isOn()) _active->drawRect(x, y, w, h); }
void HeltecV4Display::drawXbm(int x, int y, const uint8_t* bits, int w, int h) {
  if (isOn()) _active->drawXbm(x, y, bits, w, h);
}
uint16_t HeltecV4Display::getTextWidth(const char* text) { return _active ? _active->getTextWidth(text) : 0; }
void HeltecV4Display::endFrame() { if (isOn()) _active->endFrame(); }

#endif  // HELTEC_V4_DISPLAY_UNIFIED
