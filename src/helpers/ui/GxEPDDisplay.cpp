
#include "GxEPDDisplay.h"

#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
#include "TechoLiteDisplayProbe.h"
extern const char meshcore_capability_display_autodetect_techo_lite[];
const char meshcore_capability_display_autodetect_techo_lite[] =
    "meshcore.capability.display.autodetect.techo_lite.v1";
#endif

#ifdef EXP_PIN_BACKLIGHT
  #include <PCA9557.h>
  extern PCA9557 expander;
#endif

#ifndef DISPLAY_ROTATION
  #define DISPLAY_ROTATION 3
#endif

#ifdef ESP32
  SPIClass SPI1 = SPIClass(FSPI);
#endif

#ifndef EPD_WASHING_MACHINE_CYCLES
  #define EPD_WASHING_MACHINE_CYCLES 0
#endif

// Color scheme
ColorVal UIColor::window_bkg = GxEPD_WHITE;
ColorVal UIColor::title_bkg = GxEPD_WHITE;
ColorVal UIColor::title_txt = GxEPD_BLACK;
ColorVal UIColor::primary_txt = GxEPD_BLACK;
ColorVal UIColor::secondary_txt = GxEPD_BLACK;
ColorVal UIColor::warning_txt = GxEPD_BLACK;
ColorVal UIColor::popup_bkg = GxEPD_WHITE;
ColorVal UIColor::popup_txt = GxEPD_BLACK;
ColorVal UIColor::corp_blue = GxEPD_BLACK;

bool GxEPDDisplay::begin() {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_probe_complete) {
    const volatile char* capability = meshcore_capability_display_autodetect_techo_lite;
    (void)*capability;
    _probe_complete = true;
    _present = mesh::ui::techo_lite::probe();
  }
  if (!_present) return false;
  if (_init) return true;
#endif
  display.epd2.selectSPI(SPI1, SPISettings(4000000, MSBFIRST, SPI_MODE0));
#ifdef ESP32
  SPI1.begin(PIN_DISPLAY_SCLK, PIN_DISPLAY_MISO, PIN_DISPLAY_MOSI, PIN_DISPLAY_CS);
#else
  SPI1.begin();
#endif
  display.init(115200, true, 2, false);
  display.setRotation(DISPLAY_ROTATION);
  setDimensions(display.width(), display.height());
  display.setTextWrap(false);
  setTextSize(1);  // Default to size 1

  display.setFullWindow();

  for (int i = 0; i < EPD_WASHING_MACHINE_CYCLES; i++) {
    display.fillScreen(GxEPD_BLACK);
    display.display(false);
    delay(2000);
    display.fillScreen(GxEPD_WHITE);
    display.display(false);
    delay(2000);
  }

  display.setPartialWindow(0, 0, display.width(), display.height());
  resetPartialRefreshCounter();

  #if DISP_BACKLIGHT
  digitalWrite(DISP_BACKLIGHT, LOW);
  pinMode(DISP_BACKLIGHT, OUTPUT);
  #endif
  _init = true;
  _isOn = true;
  return true;
}

void GxEPDDisplay::turnOn() {
  if (!_init) {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
    if (!begin()) return;
#else
    begin();
#endif
  }
#if defined(DISP_BACKLIGHT) && !defined(BACKLIGHT_BTN)
  digitalWrite(DISP_BACKLIGHT, HIGH);
#elif defined(EXP_PIN_BACKLIGHT) && !defined(BACKLIGHT_BTN)
  expander.digitalWrite(EXP_PIN_BACKLIGHT, HIGH);
#endif
  if (!_isOn) {
    _isOn = true;
  }
}

void GxEPDDisplay::turnOff() {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_init || !_present || !_isOn) return;
#endif
#if defined(DISP_BACKLIGHT) && !defined(BACKLIGHT_BTN)
  digitalWrite(DISP_BACKLIGHT, LOW);
#elif defined(EXP_PIN_BACKLIGHT) && !defined(BACKLIGHT_BTN)
  expander.digitalWrite(EXP_PIN_BACKLIGHT, LOW);
#endif
  _isOn = false;
  // do full refresh before powering off to clear screen
  // no full refresh needed at wakeup
  display.clearScreen(0xFF); // Clears microcontroller side RAM
  display.writeScreenBuffer(0xFF); // Forces 0xFF (White) into the display controller's history registers
  resetPartialRefreshCounter();
  last_display_crc_value=0;
  display.hibernate();
}

void GxEPDDisplay::clear() {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_isOn) return;
#endif
  display.fillScreen(GxEPD_WHITE);
  display.setTextColor(GxEPD_BLACK);
  display_crc.reset();
}

void GxEPDDisplay::startFrame(ColorVal bkg) {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_isOn) return;
#endif
  display.fillScreen(bkg);
  display.setTextColor(_curr_color = UIColor::primary_txt);
  display_crc.reset();
  if (_cycles_before_full_refresh != 0) {
    display.setPartialWindow(0, 0, display.width(), display.height());
  } else {
    // forces a full wipe of the screen ...
    display.clearScreen(0xFF); // Clears microcontroller side RAM
    display.writeScreenBuffer(0xFF); // Forces 0xFF (White) into the display controller's history registers
    // we'll need a partial refresh after that (whatever crc value is)
    last_display_crc_value = 0;
    resetPartialRefreshCounter();
  }
}

void GxEPDDisplay::setTextSize(int sz) {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_present || (!_isOn && _init)) return;
#endif
  display_crc.update<int>(sz);
  switch(sz) {
    case 1:  // Small
      display.setFont(&FreeSans9pt7b);
      break;
    case 2:  // Medium Bold
      display.setFont(&FreeSansBold12pt7b);
      break;
    case 3:  // Large
      display.setFont(&FreeSans18pt7b);
      break;
    default:
      display.setFont(&FreeSans9pt7b);
      break;
  }
  // GFX uses a baseline; the UI supplies the top of the text line.
  int16_t x1, y1;
  uint16_t w, h;
  display.getTextBounds("Ag", 0, 0, &x1, &y1, &w, &h);
  _text_ascent = -y1;
  _line_height = h + 4;
}

void GxEPDDisplay::setColor(ColorVal c) {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_isOn) return;
#endif
  display_crc.update<ColorVal> (c);
  display.setTextColor(_curr_color = c);
}

void GxEPDDisplay::setCursor(int x, int y) {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_isOn) return;
#endif
  display_crc.update<int>(x);
  display_crc.update<int>(y);
  display.setCursor(x, y + _text_ascent);
}

void GxEPDDisplay::print(const char* str) {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_isOn) return;
#endif
  display_crc.update<char>(str, strlen(str));
  display.print(str);
}

void GxEPDDisplay::fillRect(int x, int y, int w, int h) {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_isOn) return;
#endif
  display_crc.update<int>(x);
  display_crc.update<int>(y);
  display_crc.update<int>(w);
  display_crc.update<int>(h);
  display.fillRect(x, y, w, h, _curr_color);
}

void GxEPDDisplay::drawRect(int x, int y, int w, int h) {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_isOn) return;
#endif
  display_crc.update<int>(x);
  display_crc.update<int>(y);
  display_crc.update<int>(w);
  display_crc.update<int>(h);
  display.drawRect(x, y, w, h, _curr_color);
}

void GxEPDDisplay::drawXbm(int x, int y, const uint8_t* bits, int w, int h) {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_isOn) return;
#endif
  if (!bits || w <= 0 || h <= 0) return;
  display_crc.update<int>(x);
  display_crc.update<int>(y);
  display_crc.update<int>(w);
  display_crc.update<int>(h);
  display_crc.update<uint8_t>(bits, ((w + 7) / 8) * h);
  display.drawBitmap(x, y, bits, w, h, _curr_color);
}

uint16_t GxEPDDisplay::getTextWidth(const char* str) {
#ifdef MESHCORE_TECHO_LITE_DISPLAY_DETECTION
  if (!_present) return 0;
#endif
  int16_t x1, y1;
  uint16_t w, h;
  display.getTextBounds(str, 0, 0, &x1, &y1, &w, &h);
  return w ? w + 1 : 0;
}

void GxEPDDisplay::endFrame() {
  if (_isOn == false) return;
  uint32_t crc = display_crc.finalize();
  if (crc != last_display_crc_value) {
    display.display(true);
    if (_cycles_before_full_refresh > 0) {
      _cycles_before_full_refresh--;
    }
  }
  last_display_crc_value = crc;
}
