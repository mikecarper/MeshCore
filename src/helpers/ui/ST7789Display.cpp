#ifdef ST7789

#include "ST7789Display.h"

#if defined(HELTEC_T114) && defined(T114_DISPLAY_AUTODETECT) && T114_DISPLAY_AUTODETECT
#include "T114DisplayProbe.h"
extern const char meshcore_capability_display_autodetect_t114[];
const char meshcore_capability_display_autodetect_t114[] =
    "meshcore.capability.display.autodetect.t114.v1";
#endif

// Native coordinates; controller RAM offsets are handled by ST7789Spi.

// Color scheme
ColorVal UIColor::window_bkg = OLEDDISPLAY_COLOR::BLACK;
ColorVal UIColor::title_bkg = OLEDDISPLAY_COLOR::BLACK;
ColorVal UIColor::title_txt = OLEDDISPLAY_COLOR::WHITE;
ColorVal UIColor::primary_txt = OLEDDISPLAY_COLOR::WHITE;
ColorVal UIColor::secondary_txt = OLEDDISPLAY_COLOR::WHITE;
ColorVal UIColor::warning_txt = OLEDDISPLAY_COLOR::WHITE;
ColorVal UIColor::popup_bkg = OLEDDISPLAY_COLOR::BLACK;
ColorVal UIColor::popup_txt = OLEDDISPLAY_COLOR::WHITE;
ColorVal UIColor::corp_blue = OLEDDISPLAY_COLOR::WHITE;

bool ST7789Display::begin() {
#if defined(HELTEC_T114) && defined(T114_DISPLAY_AUTODETECT) && T114_DISPLAY_AUTODETECT
  if (!_probeComplete) {
    const volatile char* capability = meshcore_capability_display_autodetect_t114;
    (void)*capability;  // Anchor the capability in the executed detection path.
    _probeComplete = true;
    _present = mesh::ui::t114::probe();
  }
  if (!_present) return false;
#endif
  if(!_isOn) {
    pinMode(PIN_TFT_VDD_CTL, OUTPUT);
    pinMode(PIN_TFT_LEDA_CTL, OUTPUT);
    digitalWrite(PIN_TFT_VDD_CTL, LOW);
#if defined(HELTEC_T114) && defined(T114_DISPLAY_AUTODETECT) && T114_DISPLAY_AUTODETECT
  #ifdef PIN_TFT_LEDA_CTL_ACTIVE
    digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);
  #else
    digitalWrite(PIN_TFT_LEDA_CTL, HIGH);
  #endif
#else
  #ifdef PIN_TFT_LEDA_CTL_ACTIVE
    digitalWrite(PIN_TFT_LEDA_CTL, PIN_TFT_LEDA_CTL_ACTIVE);
  #else
    digitalWrite(PIN_TFT_LEDA_CTL, LOW);
  #endif
#endif
    digitalWrite(PIN_TFT_RST, HIGH);

    if (!display.init()) {
#if defined(HELTEC_T114) && defined(T114_DISPLAY_AUTODETECT) && T114_DISPLAY_AUTODETECT
      _present = false;
      mesh::ui::t114::powerOff();
#else
      turnOff();
#endif
      return false;
    }
    display.landscapeScreen();
    #ifdef DISPLAY_FLIP_VERTICALLY
    display.flipScreenVertically();
    #endif
    display.displayOn();
    setCursor(0,0);

    _isOn = true;
#if defined(HELTEC_T114) && defined(T114_DISPLAY_AUTODETECT) && T114_DISPLAY_AUTODETECT
  #ifdef PIN_TFT_LEDA_CTL_ACTIVE
    digitalWrite(PIN_TFT_LEDA_CTL, PIN_TFT_LEDA_CTL_ACTIVE);
  #else
    digitalWrite(PIN_TFT_LEDA_CTL, LOW);
  #endif
#endif
  }
  return true;
}

void ST7789Display::turnOn() {
#if defined(HELTEC_T114) && defined(T114_DISPLAY_AUTODETECT) && T114_DISPLAY_AUTODETECT
  if (!_present) return;
#endif
  if (!_isOn) {
    // Restore power to the display but keep backlight off
    digitalWrite(PIN_TFT_VDD_CTL, LOW);
    digitalWrite(PIN_TFT_RST, HIGH);
    
    // Re-initialize the same native landscape geometry after wake.
    if (!display.init()) {
#if defined(HELTEC_T114) && defined(T114_DISPLAY_AUTODETECT) && T114_DISPLAY_AUTODETECT
      _present = false;
      mesh::ui::t114::powerOff();
#else
      turnOff();
#endif
      return;
    }
    display.landscapeScreen();
    display.displayOn();
    #ifdef DISPLAY_FLIP_VERTICALLY
    display.flipScreenVertically();
    #endif
    delay(20);

    // Now turn on the backlight
  #ifdef PIN_TFT_LEDA_CTL_ACTIVE
    digitalWrite(PIN_TFT_LEDA_CTL, PIN_TFT_LEDA_CTL_ACTIVE);
  #else
    digitalWrite(PIN_TFT_LEDA_CTL, LOW);
  #endif    
    _isOn = true;
  }
}

void ST7789Display::turnOff() {
#if defined(HELTEC_T114) && defined(T114_DISPLAY_AUTODETECT) && T114_DISPLAY_AUTODETECT
  if (!_present) return;
#endif
  digitalWrite(PIN_TFT_VDD_CTL, HIGH);
#ifdef PIN_TFT_LEDA_CTL_ACTIVE
  digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);
#else
  digitalWrite(PIN_TFT_LEDA_CTL, HIGH);
#endif
  digitalWrite(PIN_TFT_RST, LOW);
  _isOn = false;
}

void ST7789Display::clear() {
  if (!canDraw()) return;
  display.clear();
}

void ST7789Display::startFrame(ColorVal bkg) {
  if (!canDraw()) return;
  display.clear();  // TODO: use bkg
  setColor(UIColor::primary_txt);
  setTextSize(1);
}

void ST7789Display::setTextSize(int sz) {
  if (!canDraw()) return;
  _line_height = sz == 2 ? 26 : 18;
  switch(sz) {
    case 1 :
      display.setFont(ArialMT_Plain_16);
      break;
    case 2 :
      display.setFont(ArialMT_Plain_24);
      break;
    default:
      display.setFont(ArialMT_Plain_16);
  }
}

void ST7789Display::setColor(ColorVal c) {
  if (!canDraw()) return;
  _color = c;
  display.setColor((OLEDDISPLAY_COLOR)_color);
  display.setRGB(_color == OLEDDISPLAY_COLOR::WHITE ? ST77XX_WHITE : ST77XX_BLACK);
}

void ST7789Display::setCursor(int x, int y) {
  _x = x;
  _y = y;
}

void ST7789Display::print(const char* str) {
  if (!canDraw()) return;
  display.drawString(_x, _y, str);
}

void ST7789Display::printWordWrap(const char* str, int max_width) {
  if (!canDraw()) return;
  display.drawStringMaxWidth(_x, _y, max_width, str);
}

void ST7789Display::fillRect(int x, int y, int w, int h) {
  if (!canDraw()) return;
  display.fillRect(x, y, w, h);
}

void ST7789Display::drawRect(int x, int y, int w, int h) {
  if (!canDraw()) return;
  display.drawRect(x, y, w, h);
}

void ST7789Display::drawXbm(int x, int y, const uint8_t* bits, int w, int h) {
  if (!canDraw()) return;
  if (!bits || w <= 0 || h <= 0) return;
  const int stride = (w + 7) / 8;
  for (int row = 0; row < h; ++row)
    for (int col = 0; col < w; ++col)
      if (pgm_read_byte(bits + row * stride + col / 8) & (0x80 >> (col & 7)))
        display.fillRect(x + col, y + row, 1, 1);
}

uint16_t ST7789Display::getTextWidth(const char* str) {
  if (!canDraw()) return 0;
  return display.getStringWidth(str);
}

void ST7789Display::endFrame() {
  if (!canDraw()) return;
  display.display();
}

#endif
