#include "ST7789LCDDisplay.h"
#include "ColorTheme.h"

#ifndef PIN_TFT_MISO
  #define PIN_TFT_MISO -1
#endif

#ifdef MESHCORE_HELTEC_R8_RUNTIME_ROTATION
extern const char meshcore_capability_display_rotation_heltec_r8[] =
    "meshcore.capability.display.rotation.heltec_r8.v1";
#endif

#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
#include "HeltecR8DisplayProbe.h"
extern const char meshcore_capability_display_autodetect_heltec_r8[];
const char meshcore_capability_display_autodetect_heltec_r8[] =
    "meshcore.capability.display.autodetect.heltec_r8.v1";
#endif

#ifndef DISPLAY_ROTATION
  #define DISPLAY_ROTATION 3
#endif

#define DISPLAY_WIDTH 240
#define DISPLAY_HEIGHT 320

// display.flip remains an additive 180-degree mounting adjustment, independent
// of the selected orientation. Other boards keep their compiled orientation.
uint8_t ST7789LCDDisplay::effectiveRotation() const {
#ifdef MESHCORE_HELTEC_R8_RUNTIME_ROTATION
  return (uint8_t)((_base_rotation + (_flipped ? 2 : 0)) & 3);
#else
  return (uint8_t)((DISPLAY_ROTATION + (_flipped ? 2 : 0)) & 3);
#endif
}

void ST7789LCDDisplay::applyRotation() {
#ifdef HELTEC_V4_R8_TFT
  const auto rotation = effectiveRotation();
  // Layout follows saved intent immediately, including before initialization
  // and while the backlight is off. Defer all panel traffic until awake.
  setDimensions((rotation & 1) ? 320 : 240, (rotation & 1) ? 240 : 320);
  if (!_panel_ready) return;
  if (!_isOn) {
    _rotation_pending = true;
    return;
  }
#endif
  if (_panel_ready) display.setRotation(effectiveRotation());
}

bool ST7789LCDDisplay::setRotationDegrees(uint16_t degrees) {
#ifdef MESHCORE_HELTEC_R8_RUNTIME_ROTATION
  const volatile char* capability = meshcore_capability_display_rotation_heltec_r8;
  (void)*capability;
  if (degrees > 270 || degrees % 90 != 0) return false;
  const uint8_t rotation = (DISPLAY_ROTATION + degrees / 90) & 3;
  if (_base_rotation != rotation) {
    _base_rotation = rotation;
    applyRotation();
  }
  return true;
#else
  (void)degrees;
  return false;
#endif
}

void ST7789LCDDisplay::setFlipped(bool flipped) {
  if (_flipped == flipped) return;
  _flipped = flipped;
  applyRotation();
}

bool ST7789LCDDisplay::i2c_probe(TwoWire& wire, uint8_t addr) {
  return true;
}

// Color scheme
ColorVal UIColor::window_bkg = mesh::ui::color_theme::WINDOW_BACKGROUND;
ColorVal UIColor::title_bkg = mesh::ui::color_theme::TITLE_BACKGROUND;
ColorVal UIColor::title_txt = mesh::ui::color_theme::TEXT;
ColorVal UIColor::primary_txt = mesh::ui::color_theme::TEXT;
ColorVal UIColor::secondary_txt = mesh::ui::color_theme::SECONDARY_TEXT;
ColorVal UIColor::warning_txt = mesh::ui::color_theme::WARNING_TEXT;
ColorVal UIColor::popup_bkg = mesh::ui::color_theme::POPUP_BACKGROUND;
ColorVal UIColor::popup_txt = mesh::ui::color_theme::TEXT;
ColorVal UIColor::corp_blue = mesh::ui::color_theme::ACCENT;

bool ST7789LCDDisplay::begin() {
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  if (_probe_complete && !_present) return false;
  if (!_probe_complete) mesh::ui::heltec_r8::backlightOff();
#endif
  if (!_isOn) {
  #ifdef HELTEC_V4_R8_TFT
    // turnOff() leaves this panel configured and powered - its reset line is
    // shared with the touch controller, so it is never parked low - which makes
    // waking just a backlight switch. Re-running the init below would re-enter
    // SPI setup and pulse GPIO 21, resetting the touch controller on every wake
    // and stalling the UI loop for ~500 ms of reset delays.
    if (_panel_ready) {
      if (_peripher_power) _peripher_power->claim();
      // A preference may change while the backlight is off. Apply only that
      // deferred change; ordinary wakes remain free of SPI/reset work.
      if (_rotation_pending) {
        display.setRotation(effectiveRotation());
        _rotation_pending = false;
      }
      if (PIN_TFT_LEDA_CTL != -1) {
        digitalWrite(PIN_TFT_LEDA_CTL, PIN_TFT_LEDA_CTL_ACTIVE);
      }
      _isOn = true;
      return true;
    }
  #endif

    if (_peripher_power) {
      _peripher_power->claim();
    #ifdef HELTEC_V4_R8_TFT
      delay(100);
    #endif
    }

#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
    if (!_probe_complete) {
      const volatile char* capability = meshcore_capability_display_autodetect_heltec_r8;
      (void)*capability;  // Keep the marker bound to the executed probe path.
      _probe_complete = true;
      _present = mesh::ui::heltec_r8::probe();
      if (!_present) {
        // Release exactly this display's claim. VEXT also powers the radio
        // and other peripherals; never turn the shared rail off directly.
        if (_peripher_power) _peripher_power->release();
        return false;
      }
    }
#endif

    if (PIN_TFT_LEDA_CTL != -1) {
      pinMode(PIN_TFT_LEDA_CTL, OUTPUT);
    #ifdef HELTEC_V4_R8_TFT
      digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);
    #else
      digitalWrite(PIN_TFT_LEDA_CTL, HIGH);
    #endif
    }

    // Im not sure if this is just a t-deck problem or not, if your display is slow try this.
    #if defined(LILYGO_TDECK) || defined(HELTEC_LORA_V4_TFT) || defined(HELTEC_V4_R8_TFT)
      displaySPI.begin(PIN_TFT_SCL, PIN_TFT_MISO, PIN_TFT_SDA, PIN_TFT_CS);
    #endif
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
    // Arduino-ESP32 2.x begin() returns void, so inspect its public bus handle
    // before handing an unavailable SPI peripheral to the panel library.
    if (!displaySPI.bus()) {
      displaySPI.end();
      _present = false;  // Latch failure just like an absent LCD.
      mesh::ui::heltec_r8::backlightOff();
      mesh::ui::heltec_r8::releaseBus();
      if (_peripher_power) _peripher_power->release();
      return false;
    }
#endif

    display.init(DISPLAY_WIDTH, DISPLAY_HEIGHT);
    display.setRotation(effectiveRotation());
    setDimensions(display.width(), display.height());
#ifdef HELTEC_V4_R8_TFT
    _rotation_pending = false;
#endif

    display.setSPISpeed(40e6);

    display.fillScreen(ST77XX_BLACK);
    display.setTextColor(ST77XX_WHITE);
    display.setTextSize(1);
    display.setTextWrap(false);
    display.cp437(true); // Use full 256 char 'Code Page 437' font

  #ifdef HELTEC_V4_R8_TFT
    if (PIN_TFT_LEDA_CTL != -1) {
      digitalWrite(PIN_TFT_LEDA_CTL, PIN_TFT_LEDA_CTL_ACTIVE);
    }
  #endif

    _panel_ready = true;
    _isOn = true;
  }

  return true;
}

void ST7789LCDDisplay::turnOn() {
  ST7789LCDDisplay::begin();
}

void ST7789LCDDisplay::turnOff() {
  if (_isOn) {
    if (PIN_TFT_LEDA_CTL != -1) {
    #ifdef HELTEC_V4_R8_TFT
      digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);
    #else
      digitalWrite(PIN_TFT_LEDA_CTL, HIGH);
    #endif
    }
  #ifndef HELTEC_V4_R8_TFT
    if (PIN_TFT_RST != -1) {
      digitalWrite(PIN_TFT_RST, LOW);
    }
    if (PIN_TFT_LEDA_CTL != -1) {
      digitalWrite(PIN_TFT_LEDA_CTL, LOW);
    }
  #else
    // On the V4 R8 Expansion Kit this reset line is shared with the touch
    // panel's TP_RST, so parking it low would hold the touch controller in
    // reset for as long as the display is off. Killing the backlight is what
    // "off" means for this LCD anyway.
  #endif
    _isOn = false;

    if (_peripher_power) _peripher_power->release();
  }
}

void ST7789LCDDisplay::clear() {
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  if (!_isOn) return;
#endif
  display.fillScreen(ST77XX_BLACK);
}

void ST7789LCDDisplay::startFrame(ColorVal bkg) {
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  if (!_isOn) return;
#endif
  display.fillScreen(bkg);
  display.setTextColor(_color = UIColor::primary_txt);
  display.setTextSize(1);
  display.cp437(true); // Use full 256 char 'Code Page 437' font
}

void ST7789LCDDisplay::setTextSize(int sz) {
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  if (!_isOn) return;
#endif
  display.setTextSize(sz > 0 ? sz : 1);
}

void ST7789LCDDisplay::setColor(ColorVal c) {
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  if (!_isOn) return;
#endif
  display.setTextColor(_color = c);
}

void ST7789LCDDisplay::setCursor(int x, int y) {
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  if (!_isOn) return;
#endif
  display.setCursor(x, y);
}

void ST7789LCDDisplay::print(const char* str) {
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  if (!_isOn) return;
#endif
  display.print(str);
}

void ST7789LCDDisplay::fillRect(int x, int y, int w, int h) {
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  if (!_isOn) return;
#endif
  display.fillRect(x, y, w, h, _color);
}

void ST7789LCDDisplay::drawRect(int x, int y, int w, int h) {
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  if (!_isOn) return;
#endif
  display.drawRect(x, y, w, h, _color);
}

void ST7789LCDDisplay::drawXbm(int x, int y, const uint8_t* bits, int w, int h) {
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  if (!_isOn) return;
#endif
  // The common UI bitmaps are MSB-first, as expected by drawBitmap.
  display.drawBitmap(x, y, bits, w, h, _color);
}

uint16_t ST7789LCDDisplay::getTextWidth(const char* str) {
#ifdef MESHCORE_HELTEC_R8_TFT_DETECTION
  if (!_isOn) return 0;
#endif
  int16_t x1, y1;
  uint16_t w, h;
  display.getTextBounds(str, 0, 0, &x1, &y1, &w, &h);
  return w;
}

void ST7789LCDDisplay::endFrame() {
  // display.display();
}
