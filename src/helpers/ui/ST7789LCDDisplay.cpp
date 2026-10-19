#include "ST7789LCDDisplay.h"
#include "ColorTheme.h"

#ifdef LILYGO_TDECK
#include <esp_heap_caps.h>
#include <new>
#include <string.h>
#include <helpers/UsbLogging.h>

// Adafruit_GFX supplies text and bitmap rasterization without allocating its
// own framebuffer. The 153,600-byte RGB565 frame must live in external RAM.
class TDeckCanvas : public Adafruit_GFX {
  ST7789LCDDisplay& _owner;
  uint16_t* _pixels;
  uint16_t _pixel_work = 0;

  void cooperate(uint16_t pixels) {
    _pixel_work += pixels;
    if (_pixel_work >= 128) {
      _pixel_work = 0;
      _owner.serviceRender();
    }
  }

public:
  TDeckCanvas(ST7789LCDDisplay& owner, int w, int h)
      : Adafruit_GFX(w, h), _owner(owner),
        _pixels(static_cast<uint16_t*>(heap_caps_calloc(
            size_t(w) * h, sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT))) {
    setTextWrap(false);
    cp437(true);
  }
  ~TDeckCanvas() { heap_caps_free(_pixels); }
  uint16_t* pixels() const { return _pixels; }

  void drawPixel(int16_t x, int16_t y, uint16_t color) override {
    if (_pixels && x >= 0 && y >= 0 && x < WIDTH && y < HEIGHT)
      _pixels[size_t(y) * WIDTH + x] = color;
    cooperate(1);
  }
  void drawFastHLine(int16_t x, int16_t y, int16_t w, uint16_t color) override {
    if (!_pixels || y < 0 || y >= HEIGHT || w <= 0) return;
    const int left = x < 0 ? 0 : x;
    const int right = int(x) + w > WIDTH ? WIDTH : int(x) + w;
    if (left >= right) return;
    uint16_t* row = _pixels + size_t(y) * WIDTH;
    for (int xx = left; xx < right; ++xx) row[xx] = color;
    cooperate(right - left);
  }
  void drawFastVLine(int16_t x, int16_t y, int16_t h, uint16_t color) override {
    if (!_pixels || x < 0 || x >= WIDTH || h <= 0) return;
    const int top = y < 0 ? 0 : y;
    const int bottom = int(y) + h > HEIGHT ? HEIGHT : int(y) + h;
    for (int yy = top; yy < bottom; ++yy) {
      _pixels[size_t(yy) * WIDTH + x] = color;
      cooperate(1);
    }
  }
  void fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) override {
    if (w <= 0 || h <= 0) return;
    const int top = y < 0 ? 0 : y;
    const int bottom = int(y) + h > HEIGHT ? HEIGHT : int(y) + h;
    for (int yy = top; yy < bottom; ++yy) drawFastHLine(x, yy, w, color);
  }
  void fillScreen(uint16_t color) override { fillRect(0, 0, WIDTH, HEIGHT, color); }
  void writeFastHLine(int16_t x, int16_t y, int16_t w, uint16_t color) override {
    drawFastHLine(x, y, w, color);
  }
  void writeFastVLine(int16_t x, int16_t y, int16_t h, uint16_t color) override {
    drawFastVLine(x, y, h, color);
  }
  void writeFillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) override {
    fillRect(x, y, w, h, color);
  }
};

ST7789LCDDisplay::~ST7789LCDDisplay() { delete _canvas; }

bool ST7789LCDDisplay::prepareCanvas() {
  if (_canvas_attempted) return _canvas != nullptr;
  _canvas_attempted = true;
  _canvas = new (std::nothrow) TDeckCanvas(*this, width(), height());
  if (!_canvas || !_canvas->pixels()) {
    delete _canvas;
    _canvas = nullptr;
    mesh::usbConsolePort().println("WARN: T-Deck display PSRAM allocation failed; using direct redraw");
    return false;
  }
  return true;
}

bool ST7789LCDDisplay::isBufferedRendering() const {
  return _buffered_rendering && _canvas != nullptr;
}

void ST7789LCDDisplay::setBufferedRendering(bool enabled) {
  _frame_active = false;
  _buffered_rendering = enabled;
  if (enabled && _panel_ready) prepareCanvas();
}

void ST7789LCDDisplay::serviceRender(bool force) {
  if (!_buffered_rendering || !_render_yield || _render_callback_active) return;
  if (!force && uint32_t(micros() - _last_render_yield_us) < 250) return;
  _render_callback_active = true;
  _render_yield();
  _render_callback_active = false;
  _last_render_yield_us = micros();
}

void ST7789LCDDisplay::flushCanvas(int x, int y, int w, int h) {
  if (!isBufferedRendering() || !_isOn || w <= 0 || h <= 0) return;
  const int right = int32_t(x) + w > width() ? width() : int32_t(x) + w;
  const int bottom = int32_t(y) + h > height() ? height() : int32_t(y) + h;
  x = x < 0 ? 0 : x;
  y = y < 0 ? 0 : y;
  w = right - x;
  if (w <= 0 || y >= bottom) return;

  // At most 960 pixels (0.384 ms at 40 MHz), plus command/driver overhead,
  // per transaction. Copy out of PSRAM so SPI never depends on DMA from it.
  constexpr size_t StripPixels = 960;
  uint16_t strip[StripPixels];
  const int rows_per_strip = StripPixels / w;
  serviceRender(true);
  while (y < bottom) {
    const int rows = bottom - y < rows_per_strip ? bottom - y : rows_per_strip;
    for (int row = 0; row < rows; ++row) {
      memcpy(strip + size_t(row) * w,
             _canvas->pixels() + size_t(y + row) * width() + x,
             size_t(w) * sizeof(uint16_t));
    }
    display.startWrite();
    display.setAddrWindow(x, y, w, rows);
    display.writePixels(strip, size_t(w) * rows, true);
    display.endWrite();
    // CS is high and the transfer is complete before the radio can use the
    // shared physical clock/data pins. No recursive application-loop call.
    serviceRender(true);
    y += rows;
  }
}

void ST7789LCDDisplay::finishCanvasPrimitive(int x, int y, int w, int h) {
  serviceRender();
  // Dashboard rows can be updated without startFrame/endFrame. Preserve their
  // immediate behavior while sending only their changed rectangle in strips.
  if (!_frame_active) flushCanvas(x, y, w, h);
}
#endif

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
  #ifdef LILYGO_TDECK
    // This board has no TFT reset pin or separately switched panel supply.
    // Blanking its backlight does not require a slow controller reinit on wake.
    if (_panel_ready) {
      if (_peripher_power) _peripher_power->claim();
      if (PIN_TFT_LEDA_CTL != -1) digitalWrite(PIN_TFT_LEDA_CTL, HIGH);
      _isOn = true;
      return true;
    }
  #endif
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
#ifdef LILYGO_TDECK
    if (_buffered_rendering) prepareCanvas();
#endif
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
#if defined(LILYGO_TDECK) || defined(MESHCORE_HELTEC_R8_TFT_DETECTION)
  if (!_isOn) return;
#endif
#ifdef LILYGO_TDECK
  if (isBufferedRendering()) {
    if (!_isOn) return;
    _canvas->fillScreen(ST77XX_BLACK);
    finishCanvasPrimitive(0, 0, width(), height());
    return;
  }
#endif
  display.fillScreen(ST77XX_BLACK);
}

void ST7789LCDDisplay::startFrame(ColorVal bkg) {
#if defined(LILYGO_TDECK) || defined(MESHCORE_HELTEC_R8_TFT_DETECTION)
  if (!_isOn) return;
#endif
#ifdef LILYGO_TDECK
  if (isBufferedRendering()) {
    if (!_isOn) return;
    _frame_active = true;
    serviceRender(true);
    _canvas->fillScreen(bkg);
    _canvas->setTextColor(_color = UIColor::primary_txt);
    _canvas->setTextSize(1);
    _canvas->cp437(true);
    return;
  }
#endif
  display.fillScreen(bkg);
  display.setTextColor(_color = UIColor::primary_txt);
  display.setTextSize(1);
  display.cp437(true); // Use full 256 char 'Code Page 437' font
}

void ST7789LCDDisplay::setTextSize(int sz) {
#if defined(LILYGO_TDECK) || defined(MESHCORE_HELTEC_R8_TFT_DETECTION)
  if (!_isOn) return;
#endif
#ifdef LILYGO_TDECK
  if (isBufferedRendering()) {
    _canvas->setTextSize(sz > 0 ? sz : 1);
    return;
  }
#endif
  display.setTextSize(sz > 0 ? sz : 1);
}

void ST7789LCDDisplay::setColor(ColorVal c) {
#if defined(LILYGO_TDECK) || defined(MESHCORE_HELTEC_R8_TFT_DETECTION)
  if (!_isOn) return;
#endif
#ifdef LILYGO_TDECK
  if (isBufferedRendering()) {
    _canvas->setTextColor(_color = c);
    return;
  }
#endif
  display.setTextColor(_color = c);
}

void ST7789LCDDisplay::setCursor(int x, int y) {
#if defined(LILYGO_TDECK) || defined(MESHCORE_HELTEC_R8_TFT_DETECTION)
  if (!_isOn) return;
#endif
#ifdef LILYGO_TDECK
  if (isBufferedRendering()) {
    _canvas->setCursor(x, y);
    return;
  }
#endif
  display.setCursor(x, y);
}

void ST7789LCDDisplay::print(const char* str) {
#if defined(LILYGO_TDECK) || defined(MESHCORE_HELTEC_R8_TFT_DETECTION)
  if (!_isOn) return;
#endif
#ifdef LILYGO_TDECK
  if (isBufferedRendering()) {
    if (!_isOn) return;
    int16_t x, y;
    uint16_t w, h;
    _canvas->getTextBounds(str, _canvas->getCursorX(), _canvas->getCursorY(), &x, &y, &w, &h);
    _canvas->print(str);
    finishCanvasPrimitive(x, y, w, h);
    return;
  }
#endif
  display.print(str);
}

void ST7789LCDDisplay::fillRect(int x, int y, int w, int h) {
#if defined(LILYGO_TDECK) || defined(MESHCORE_HELTEC_R8_TFT_DETECTION)
  if (!_isOn) return;
#endif
#ifdef LILYGO_TDECK
  if (isBufferedRendering()) {
    if (!_isOn) return;
    _canvas->fillRect(x, y, w, h, _color);
    finishCanvasPrimitive(x, y, w, h);
    return;
  }
#endif
  display.fillRect(x, y, w, h, _color);
}

void ST7789LCDDisplay::drawRect(int x, int y, int w, int h) {
#if defined(LILYGO_TDECK) || defined(MESHCORE_HELTEC_R8_TFT_DETECTION)
  if (!_isOn) return;
#endif
#ifdef LILYGO_TDECK
  if (isBufferedRendering()) {
    if (!_isOn) return;
    _canvas->drawRect(x, y, w, h, _color);
    finishCanvasPrimitive(x, y, w, h);
    return;
  }
#endif
  display.drawRect(x, y, w, h, _color);
}

void ST7789LCDDisplay::drawXbm(int x, int y, const uint8_t* bits, int w, int h) {
#if defined(LILYGO_TDECK) || defined(MESHCORE_HELTEC_R8_TFT_DETECTION)
  if (!_isOn) return;
#endif
#ifdef LILYGO_TDECK
  if (isBufferedRendering()) {
    if (!_isOn) return;
    _canvas->drawBitmap(x, y, bits, w, h, _color);
    finishCanvasPrimitive(x, y, w, h);
    return;
  }
#endif
  // The common UI bitmaps are MSB-first, as expected by drawBitmap.
  display.drawBitmap(x, y, bits, w, h, _color);
}

uint16_t ST7789LCDDisplay::getTextWidth(const char* str) {
#if defined(LILYGO_TDECK) || defined(MESHCORE_HELTEC_R8_TFT_DETECTION)
  if (!_isOn) return 0;
#endif
#ifdef LILYGO_TDECK
  if (isBufferedRendering()) {
    int16_t x, y;
    uint16_t w, h;
    _canvas->getTextBounds(str, 0, 0, &x, &y, &w, &h);
    return w;
  }
#endif
  int16_t x1, y1;
  uint16_t w, h;
  display.getTextBounds(str, 0, 0, &x1, &y1, &w, &h);
  return w;
}

void ST7789LCDDisplay::endFrame() {
#ifdef LILYGO_TDECK
  if (!_isOn) {
    _frame_active = false;
    return;
  }
  if (isBufferedRendering()) {
    _frame_active = false;
    flushCanvas(0, 0, width(), height());
  }
#endif
  // display.display();
}
