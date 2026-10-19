#include "SH1107Display.h"
#include <Adafruit_GrayOLED.h>
#include "Adafruit_SH110X.h"

#ifndef DISPLAY_ROTATION
#define DISPLAY_ROTATION 0
#endif

#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
extern const char meshcore_capability_display_autodetect_muzi_superio[];
const char meshcore_capability_display_autodetect_muzi_superio[] =
    "meshcore.capability.display.autodetect.muzi_superio.v1";
#endif

ColorVal UIColor::window_bkg = SH110X_BLACK;
ColorVal UIColor::title_bkg = SH110X_BLACK;
ColorVal UIColor::title_txt = SH110X_WHITE;
ColorVal UIColor::primary_txt = SH110X_WHITE;
ColorVal UIColor::secondary_txt = SH110X_WHITE;
ColorVal UIColor::warning_txt = SH110X_WHITE;
ColorVal UIColor::popup_bkg = SH110X_BLACK;
ColorVal UIColor::popup_txt = SH110X_WHITE;
ColorVal UIColor::corp_blue = SH110X_WHITE;

bool SH1107Display::i2c_probe(TwoWire &wire, uint8_t addr)
{
  wire.beginTransmission(addr);
  uint8_t error = wire.endTransmission();
  return (error == 0);
}

bool SH1107Display::begin()
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (_probe_complete && !_present) return false;
  if (_isOn && _panel_ready) return true;
  const volatile char* capability = meshcore_capability_display_autodetect_muzi_superio;
  (void)*capability;
  // SuperIO's panel needs its own 12 V boost supply, separate from GPS/radio.
  digitalWrite(SCREEN_12V_ENABLE, HIGH);
  pinMode(SCREEN_12V_ENABLE, OUTPUT);
  delay(250);
  if (_panel_ready) {
    // Only OLED VCC is switched; the 3.3 V controller logic retains setup.
    // Avoid Adafruit_GrayOLED::_init allocating another BusIO adapter on
    // every wake. Display-off is sent before VCC removal below.
    display.oled_command(SH110X_DISPLAYON);
    _isOn = true;
    return true;
  }
  _probe_complete = true;
  if (!i2c_probe(Wire, DISPLAY_ADDRESS)) {
    digitalWrite(SCREEN_12V_ENABLE, LOW);
    _present = _panel_ready = _isOn = false;
    return false;  // No library objects/framebuffer are allocated when absent.
  }
#endif
  bool result = display.begin(DISPLAY_ADDRESS, true) && i2c_probe(Wire, DISPLAY_ADDRESS);
  if (result) {
    display.setRotation(DISPLAY_ROTATION);
  }
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  _present = _panel_ready = _isOn = result;
  if (!result) digitalWrite(SCREEN_12V_ENABLE, LOW);
#endif
  return result;
}

void SH1107Display::turnOn()
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!begin()) return;
#endif
  display.oled_command(SH110X_DISPLAYON);
  uint8_t cmd[] = {0xD5, 0xF0};
  display.oled_commandList(cmd, 2);
  _isOn = true;
}

void SH1107Display::turnOff()
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_panel_ready) return;
#endif
  display.oled_command(SH110X_DISPLAYOFF);
  _isOn = false;
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  digitalWrite(SCREEN_12V_ENABLE, LOW);
#endif
}

void SH1107Display::clear()
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_isOn) return;
#endif
  display.clearDisplay();
  display.display();
}

void SH1107Display::startFrame(ColorVal bkg)
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_isOn) return;
#endif
  display.clearDisplay(); // TODO: apply 'bkg'
  display.setContrast(120); // 0-127. default setting was causing some flickering.
  // display.SH110X_SETPRECHARGE(255);
  _color = SH110X_WHITE;
  display.setTextColor(_color);
  display.setTextSize(1);
  display.cp437(true); // Use full 256 char 'Code Page 437' font
}

void SH1107Display::setTextSize(int sz)
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_isOn) return;
#endif
  display.setTextSize(sz);
}

void SH1107Display::setColor(ColorVal c)
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_isOn) return;
#endif
  _color = (c != 0) ? SH110X_WHITE : SH110X_BLACK;
  display.setTextColor(_color);
}

void SH1107Display::setCursor(int x, int y)
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_isOn) return;
#endif
  display.setCursor(x, y);
}

void SH1107Display::print(const char *str)
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_isOn) return;
#endif
  display.print(str);
}

void SH1107Display::fillRect(int x, int y, int w, int h)
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_isOn) return;
#endif
  display.fillRect(x, y, w, h, _color);
}

void SH1107Display::drawRect(int x, int y, int w, int h)
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_isOn) return;
#endif
  display.drawRect(x, y, w, h, _color);
}

void SH1107Display::drawXbm(int x, int y, const uint8_t *bits, int w, int h)
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_isOn) return;
#endif
  display.drawBitmap(x, y, bits, w, h, SH110X_WHITE);
}

uint16_t SH1107Display::getTextWidth(const char *str)
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_isOn) return 0;
#endif
  int16_t x1, y1;
  uint16_t w, h;
  display.getTextBounds(str, 0, 0, &x1, &y1, &w, &h);
  return w;
}

void SH1107Display::endFrame()
{
#ifdef MESHCORE_MUZI_ACCESSORY_DETECTION
  if (!_isOn) return;
#endif
  display.display();
}
