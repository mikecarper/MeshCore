#include "CardputerDisplay.h"

#include "font.h"
#include "font_16.h"
#include "font_19.h"
#include "globals.h"

#include <MeshCore.h>
#include <helpers/ui/ColorTheme.h>

ColorVal UIColor::window_bkg = CardputerDisplay::P_BLACK;
ColorVal UIColor::title_bkg = CardputerDisplay::P_BLACK;
ColorVal UIColor::title_txt = CardputerDisplay::P_WHITE;
ColorVal UIColor::primary_txt = CardputerDisplay::P_WHITE;
ColorVal UIColor::secondary_txt = CardputerDisplay::P_GRAY;
ColorVal UIColor::warning_txt = CardputerDisplay::P_ORANGE;
ColorVal UIColor::popup_bkg = CardputerDisplay::P_BLACK;
ColorVal UIColor::popup_txt = CardputerDisplay::P_WHITE;
ColorVal UIColor::corp_blue = CardputerDisplay::P_BLUE;

int32_t emoji_draw_callback(lgfx::LGFXBase *gfx, int32_t x, int32_t y, uint32_t code, int32_t font_height) {
  int w = gfx->textWidth("O");
  int h = gfx->fontHeight();
  gfx->drawRect(x, y + (h - w - w / 2), w, w, mesh::ui::color_theme::render(TFT_DARKGRAY));
  return w;
}

LGFX::LGFX() {
  {
    auto cfg = bus_instance_.config();
    cfg.spi_host = TFT_SPI_HOST;
    cfg.spi_mode = 0;
    cfg.freq_write = 40000000; // 40 MHz write clock
    cfg.spi_3wire = true;
    cfg.use_lock = true;
    cfg.dma_channel = SPI_DMA_CH_AUTO;
    cfg.pin_sclk = PIN_TFT_SCK;
    cfg.pin_mosi = PIN_TFT_MOSI;
    cfg.pin_miso = -1;
    cfg.pin_dc = PIN_TFT_DC;
    bus_instance_.config(cfg);
    panel_instance_.setBus(&bus_instance_);
  }

  {
    auto cfg = panel_instance_.config();
    cfg.pin_cs = PIN_TFT_CS;
    cfg.pin_rst = PIN_TFT_RST;
    cfg.panel_width = 135;
    cfg.panel_height = 240;
    cfg.offset_x = 52; // values from M5GFX
    cfg.offset_y = 40;
    cfg.readable = false;
    cfg.invert = true;
    // cfg.rgb_order = false;
    // cfg.dlen_16bit = false;
    // cfg.bus_shared = true;
    panel_instance_.config(cfg);
  }

  {
    auto cfg = light_instance_.config();
    cfg.pin_bl = PIN_TFT_BL;
    cfg.invert = false;
    cfg.freq = 256;
    cfg.offset = 16;
    cfg.pwm_channel = 7;
    light_instance_.config(cfg);
    panel_instance_.setLight(&light_instance_);
  }

  setPanel(&panel_instance_);
}

static const uint8_t *const kFonts[CardputerDisplay::TEXT_LEVEL_COUNT] = { DejaVuSans_11, DejaVuSans_16,
                                                                          DejaVuSans_19 };
static const uint8_t kFontPx[CardputerDisplay::TEXT_LEVEL_COUNT] = { 12, 16, 19 };
static const uint8_t kNormalPx = 12;

const char *CardputerDisplay::textLevelName(uint8_t level) {
  static const char *const names[TEXT_LEVEL_COUNT] = { "Normal", "Large", "Extra large" };
  return level < TEXT_LEVEL_COUNT ? names[level] : names[TEXT_LARGE];
}

void CardputerDisplay::setTextLevel(uint8_t level) {
  if (level >= TEXT_LEVEL_COUNT) level = TEXT_LARGE;
  _textLevel = level;
  _fontPx = kFontPx[level];
  LCD.loadFont(kFonts[level]);
  setTextSize(1);
}

void CardputerDisplay::setSmallText() {
  _scale = (float)kNormalPx / _fontPx;
  LCD.setTextSize(_scale);
}

bool CardputerDisplay::begin() {
  LCD.setBaseColor(TFT_BLACK);

  bool success = LCD.begin();

  if (!success) {
    return false;
  }

  LCD.setRotation(1);
  LCD.setColorDepth(16); // lower SPI bandwidth
  LCD.setBrightness(128);

  LCD.setTextColor(mesh::ui::color_theme::render(P_WHITE),
                   mesh::ui::color_theme::background(P_BLACK));
  LCD.setEmojiCallback(emoji_draw_callback);
  setTextLevel(_textLevel);
  return true;
}

void CardputerDisplay::turnOn() {
  _isOn = true;
  LCD.wakeup();
}

void CardputerDisplay::turnOff() {
  _isOn = false;
  LCD.sleep();
}

void CardputerDisplay::clear() {
  LCD.clear(mesh::ui::color_theme::background(P_BLACK));
}

void CardputerDisplay::startFrame(ColorVal bkg) {
  LCD.startWrite();
  LCD.clear(mesh::ui::color_theme::background(bkg));
  setColor(P_WHITE);
}

void CardputerDisplay::endFrame() {
  LCD.endWrite();
}

int32_t CardputerDisplay::getFontHeight() const {
  return LCD.fontHeight();
}

int16_t CardputerDisplay::getFontLineHeight() const {
  return LCD.fontHeight();
}

void CardputerDisplay::drawTextLeftAlignWithScroll(int x, int y, int available_width, const char *text) {
  int text_len = strlen(text);
  int offset = 0;

  // Calculate how many characters from the start to skip so the text fits
  while (offset < text_len) {
    if (getTextWidth(&text[offset]) <= available_width) {
      break;
    }
    // Skip to next UTF-8 character boundary
    unsigned char b = text[offset];
    if ((b & 0x80) == 0) {
      // Single-byte character (ASCII)
      offset += 1;
    } else if ((b & 0xE0) == 0xC0) {
      // 2-byte character
      offset += 2;
    } else if ((b & 0xF0) == 0xE0) {
      // 3-byte character
      offset += 3;
    } else if ((b & 0xF8) == 0xF0) {
      // 4-byte character
      offset += 4;
    } else {
      offset += 1; // Invalid UTF-8, skip single byte
    }

    if (offset >= text_len) {
      offset = text_len;
      break;
    }
  }

  drawTextLeftAlign(x, y, &text[offset]);
}

// Size 1 is the chosen text level; larger sizes are headings, scaled so a
// heading is about the same height (sz x Normal) at every level.
void CardputerDisplay::setTextSize(int sz) {
  _scale = sz <= 1 ? 1.0f : (float)(kNormalPx * sz) / _fontPx;
  LCD.setTextSize(_scale);
}

void CardputerDisplay::setColor(ColorVal c) {
  _lastColor = mesh::ui::color_theme::render(c);
  LCD.setColor(_lastColor);
  LCD.setTextColor(_lastColor);
}

void CardputerDisplay::setCursor(int x, int y) {
  LCD.setCursor(x, y);
}

void CardputerDisplay::print(const char *str) {
  LCD.print(str);
}

void CardputerDisplay::fillRect(int x, int y, int w, int h) {
  LCD.fillRect(x, y, w, h);
}

void CardputerDisplay::drawRect(int x, int y, int w, int h) {
  LCD.drawRect(x, y, w, h);
}

void CardputerDisplay::drawXbm(int x, int y, const uint8_t *bits, int w, int h) {
  LCD.drawBitmap(x, y, bits, w, h, _lastColor);
}

uint16_t CardputerDisplay::getTextWidth(const char *str) {
  return LCD.textWidth(str);
}
