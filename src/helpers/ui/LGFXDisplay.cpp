#include "LGFXDisplay.h"

#ifndef DISPLAY_ROTATION
  #define DISPLAY_ROTATION 1
#endif
#ifndef DISPLAY_BRIGHTNESS
  #define DISPLAY_BRIGHTNESS 64
#endif

namespace {

uint32_t readBigEndian32(const uint8_t* data) {
  return ((uint32_t)data[0] << 24)
      | ((uint32_t)data[1] << 16)
      | ((uint32_t)data[2] << 8)
      | (uint32_t)data[3];
}

uint32_t readLittleEndian32(const uint8_t* data) {
  return (uint32_t)data[0]
      | ((uint32_t)data[1] << 8)
      | ((uint32_t)data[2] << 16)
      | ((uint32_t)data[3] << 24);
}

bool validateVlw(const uint8_t* data, size_t size) {
  static const size_t HEADER_SIZE = 24;
  static const size_t RECORD_SIZE = 28;
  static const size_t FOOTER_V1_SIZE = 24;
  static const size_t FOOTER_V2_SIZE = 40;
  static const uint8_t FOOTER_V1_MAGIC[8] = {
    'M', 'C', 'E', 'M', 'A', 'P', '1', 0
  };
  static const uint8_t FOOTER_V2_MAGIC[8] = {
    'M', 'C', 'E', 'M', 'A', 'P', '2', 0
  };
  if (data == nullptr || size < HEADER_SIZE + FOOTER_V1_SIZE) {
    return false;
  }

  size_t footer_size = FOOTER_V1_SIZE;
  const uint8_t* footer = data + size - footer_size;
  if (size >= HEADER_SIZE + FOOTER_V2_SIZE
      && memcmp(data + size - FOOTER_V2_SIZE, FOOTER_V2_MAGIC,
                sizeof(FOOTER_V2_MAGIC)) == 0) {
    footer_size = FOOTER_V2_SIZE;
    footer = data + size - footer_size;
  } else if (memcmp(footer, FOOTER_V1_MAGIC,
                    sizeof(FOOTER_V1_MAGIC)) != 0) {
    return false;
  }

  uint32_t glyph_count = readBigEndian32(data);
  uint32_t map_offset = readLittleEndian32(footer + 8);
  if (glyph_count == 0 || glyph_count > 10000
      || map_offset < HEADER_SIZE
      || map_offset > size - footer_size
      || glyph_count > (map_offset - HEADER_SIZE) / RECORD_SIZE) {
    return false;
  }

  size_t bitmap_offset = HEADER_SIZE + (size_t)glyph_count * RECORD_SIZE;
  uint32_t previous_codepoint = 0;
  for (uint32_t i = 0; i < glyph_count; ++i) {
    const uint8_t* record = data + HEADER_SIZE + (size_t)i * RECORD_SIZE;
    uint32_t codepoint = readBigEndian32(record);
    uint32_t height = readBigEndian32(record + 4);
    uint32_t width = readBigEndian32(record + 8);
    uint32_t advance = readBigEndian32(record + 12);
    if ((i != 0 && codepoint <= previous_codepoint)
        || codepoint > 0xFFFF
        || width > 64
        || height > 64
        || advance > 64
        || width > SIZE_MAX / (height == 0 ? 1 : height)
        || bitmap_offset > map_offset
        || width * height > map_offset - bitmap_offset) {
      return false;
    }
    previous_codepoint = codepoint;
    bitmap_offset += width * height;
  }
  return bitmap_offset == map_offset;
}

LGFXDisplay* activeEmojiDisplay = nullptr;

static const uint32_t UI_PALETTE[16] = {
  0x000000, 0xFFFFFF, 0x0000FF, 0x929292,
  0xFFAA00, 0x00FFFF, 0x0000D6, 0xE53935,
  0x43A047, 0x1E88E5, 0x8E24AA, 0xFDD835,
  0x6D4C41, 0x00ACC1, 0xF06292, 0xFF00FF,
};

static constexpr uint8_t TRANSPARENT_EMOJI_PALETTE_INDEX = 15;
static constexpr uint32_t TRANSPARENT_EMOJI_COLOR = 0xFF00FF;

bool isSequence(const uint8_t* text, size_t remaining,
                const uint8_t* sequence, size_t sequence_size) {
  return remaining >= sequence_size
      && memcmp(text, sequence, sequence_size) == 0;
}

}  // namespace

// Color scheme
ColorVal UIColor::window_bkg = 0xFFFF;
ColorVal UIColor::title_bkg = 0x001F;
ColorVal UIColor::title_txt = 0xFFFF;
ColorVal UIColor::primary_txt = 0x0000;
ColorVal UIColor::secondary_txt = (18 << 11) | (36 << 5) | 18;  // mid-gray
ColorVal UIColor::warning_txt = 0xFD20;
ColorVal UIColor::popup_bkg =  0x07FF;  // CYAN
ColorVal UIColor::popup_txt = 0x0000;
ColorVal UIColor::corp_blue = 0x001A;

bool LGFXDisplay::begin() {
  if (!display->init()) return false;
  _isOn = true;
  display->setRotation(DISPLAY_ROTATION);
  display->setBrightness(DISPLAY_BRIGHTNESS);
  display->setColorDepth(8);
  display->setTextColor(TFT_WHITE);

  buffer.setColorDepth(UI_BUFFER_COLOR_DEPTH);
  // The RGB scanout framebuffer must live in PSRAM. Keep this much smaller
  // logical UI sprite in internal DMA-capable RAM so page redraws do not
  // compete with scanout and permanently shift the panel after an underflow.
  buffer.setPsram(false);
  if (buffer.createSprite(width() * UI_COORD_SCALE,
                          height() * UI_COORD_SCALE) == nullptr) {
    return false;
  }
  configurePalette();

  return true;
}

void LGFXDisplay::turnOn() {
  if (!_isOn) {
    // Keep the panel initialized while blanked so touch remains available as
    // a wake source. Restoring brightness is sufficient for RGB panels.
    display->setBrightness(DISPLAY_BRIGHTNESS);
#ifdef HAS_TOUCH
    if (display->touch() != nullptr) display->touch()->wakeup();
#endif
  }
  _isOn = true;
}

void LGFXDisplay::turnOff() {
  if (_isOn) {
    // Do not use LGFX sleep here: a sleeping touch controller cannot provide
    // the event that wakes a touch-capable UI. Blank only the backlight.
    display->setBrightness(0);
  }
  _isOn = false;
}

void LGFXDisplay::clear() {
//  display->clearDisplay();
  buffer.clearDisplay();
  _presentedEmojiOverlayCount = 0;
  _hasLastFrame = false;
}

void LGFXDisplay::startFrame(ColorVal bkg) {
//  display->startWrite();
//  display->getScanLine();
  _emojiOverlayCount = 0;
  _hasTransparentEmojiPixels = false;
  _color = renderColor(bkg);
  buffer.setBaseColor(_color);
  buffer.fillScreen(_color);
  setColor(UIColor::primary_txt);
}

void LGFXDisplay::setTextSize(int sz) {
  int scaled = (sz * UI_COORD_SCALE + _fontNativeScale / 2)
      / _fontNativeScale;
  if (scaled < 1) scaled = 1;
  buffer.setTextSize(scaled);
}

void LGFXDisplay::setColor(ColorVal c) {
  _color = renderColor(c);
  // Every frame starts from a freshly cleared canvas, so transparent glyph
  // backgrounds are both sufficient and unambiguous. In particular,
  // primary_txt and popup_txt are both black and cannot be distinguished by
  // their RGB565 value alone.
  buffer.setTextColor(_color);
}

void LGFXDisplay::setCursor(int x, int y) {
  buffer.setCursor(x * UI_COORD_SCALE, y * UI_COORD_SCALE);
}

void LGFXDisplay::print(const char* str) {
  String mapped = mapText(str);
  buffer.println(mapped.c_str());
//  Serial.println(str);
}

void LGFXDisplay::fillRect(int x, int y, int w, int h) {
  buffer.fillRect(x * UI_COORD_SCALE, y * UI_COORD_SCALE,
                  w * UI_COORD_SCALE, h * UI_COORD_SCALE, _color);
}

void LGFXDisplay::drawRect(int x, int y, int w, int h) {
  if (w <= 0 || h <= 0) return;
  if (UI_COORD_SCALE == 1) {
    buffer.drawRect(x, y, w, h, _color);
    return;
  }
  int left = x * UI_COORD_SCALE;
  int top = y * UI_COORD_SCALE;
  int scaled_width = w * UI_COORD_SCALE;
  int scaled_height = h * UI_COORD_SCALE;
  buffer.fillRect(left, top, scaled_width, UI_COORD_SCALE, _color);
  buffer.fillRect(left, top + scaled_height - UI_COORD_SCALE,
                  scaled_width, UI_COORD_SCALE, _color);
  buffer.fillRect(left, top, UI_COORD_SCALE, scaled_height, _color);
  buffer.fillRect(left + scaled_width - UI_COORD_SCALE, top,
                  UI_COORD_SCALE, scaled_height, _color);
}

void LGFXDisplay::drawXbm(int x, int y, const uint8_t* bits, int w, int h) {
  if (UI_COORD_SCALE == 1) {
    buffer.drawBitmap(x, y, bits, w, h, _color);
    return;
  }
  const int row_bytes = (w + 7) / 8;
  for (int row = 0; row < h; ++row) {
    int column = 0;
    while (column < w) {
      while (column < w
             && !(bits[row * row_bytes + column / 8]
                  & (0x80 >> (column & 7)))) {
        ++column;
      }
      int first = column;
      while (column < w
             && (bits[row * row_bytes + column / 8]
                 & (0x80 >> (column & 7)))) {
        ++column;
      }
      if (first != column) {
        buffer.fillRect((x + first) * UI_COORD_SCALE,
                        (y + row) * UI_COORD_SCALE,
                        (column - first) * UI_COORD_SCALE,
                        UI_COORD_SCALE, _color);
      }
    }
  }
}

uint16_t LGFXDisplay::getTextWidth(const char* str) {
  String mapped = mapText(str);
  return (buffer.textWidth(mapped.c_str()) + UI_COORD_SCALE - 1)
      / UI_COORD_SCALE;
}

void LGFXDisplay::translateUTF8ToBlocks(char* dest, const char* src,
                                        size_t dest_size) {
  if (!_emojiMap.isReady()) {
    DisplayDriver::translateUTF8ToBlocks(dest, src, dest_size);
    return;
  }
  if (dest_size == 0) return;

  size_t output = 0;
  for (size_t input = 0; src[input] != 0 && output + 1 < dest_size;) {
    uint8_t first = (uint8_t)src[input];
    size_t length = 1;
    if (first >= 0xC2 && first <= 0xDF) {
      length = 2;
    } else if (first >= 0xE0 && first <= 0xEF) {
      length = 3;
    } else if (first >= 0xF0 && first <= 0xF4) {
      length = 4;
    } else if (first < 32 || first > 126) {
      ++input;
      continue;
    }
    bool valid = true;
    for (size_t i = 1; i < length; ++i) {
      if (src[input + i] == 0
          || ((uint8_t)src[input + i] & 0xC0) != 0x80) {
        valid = false;
        break;
      }
    }
    if (!valid || output + length >= dest_size) break;
    memcpy(dest + output, src + input, length);
    output += length;
    input += length;
  }
  dest[output] = 0;
}

void LGFXDisplay::endFrame() {
  uint32_t hash = frameHash();
  if (_hasLastFrame && hash == _lastFrameHash) return;
  _lastFrameHash = hash;
  _hasLastFrame = true;

  display->startWrite();
  if (UI_ZOOM != 1) {
    if (_hasTransparentEmojiPixels) {
      buffer.pushRotateZoom(display, display->width() / 2,
                            display->height() / 2, 0, UI_ZOOM, UI_ZOOM,
                            TRANSPARENT_EMOJI_COLOR);
    } else {
      buffer.pushRotateZoom(display, display->width() / 2,
                            display->height() / 2, 0, UI_ZOOM, UI_ZOOM);
    }
  } else {
    if (_hasTransparentEmojiPixels) {
      buffer.pushSprite(display, 0, 0, TRANSPARENT_EMOJI_COLOR);
    } else {
      buffer.pushSprite(display, 0, 0);
    }
  }
  if (_emojiAtlas.isReady()) {
    const float source_x = (_emojiAtlas.width() - 1) * 0.5f;
    const float source_y = (_emojiAtlas.height() - 1) * 0.5f;
    for (size_t i = 0; i < _emojiOverlayCount; ++i) {
      const EmojiOverlay& overlay = _emojiOverlays[i];
      const uint8_t* pixels = _emojiAtlas.glyph(overlay.codepoint);
      if (pixels == nullptr) continue;
      float size = overlay.size * UI_ZOOM;
      float zoom_x = size / _emojiAtlas.width();
      float zoom_y = size / _emojiAtlas.height();
      float x = overlay.x * UI_ZOOM + (size - 1) * 0.5f;
      float y = overlay.y * UI_ZOOM + (size - 1) * 0.5f;
      display->pushImageRotateZoom(
          x, y, source_x, source_y, 0, zoom_x, zoom_y,
          _emojiAtlas.width(), _emojiAtlas.height(),
          reinterpret_cast<const lgfx::rgb332_t*>(pixels),
          lgfx::rgb332_t(_emojiAtlas.transparent()));
    }
  }
  display->endWrite();

  _presentedEmojiOverlayCount = _emojiOverlayCount;
  memcpy(_presentedEmojiOverlays, _emojiOverlays,
         _emojiOverlayCount * sizeof(EmojiOverlay));
}

bool LGFXDisplay::getTouch(int* x, int* y) {
  lgfx::v1::touch_point_t point = {};
  if (display->getTouch(&point) == 0) return false;
  if (UI_ZOOM * UI_COORD_SCALE != 1) {
    *x = point.x / (UI_ZOOM * UI_COORD_SCALE);
    *y = point.y / (UI_ZOOM * UI_COORD_SCALE);
  } else {
    *x = point.x;
    *y = point.y;
  }
  return *x >= 0 && *x < width() && *y >= 0 && *y < height();
}

bool LGFXDisplay::installRuntimeFont(uint8_t* data, size_t size) {
  mesh::ui::EmojiFontMap map;
  mesh::ui::ColorEmojiAtlas atlas;
  if (!validateVlw(data, size)
      || !map.begin(data, size)
      || !buffer.loadFont(data)) {
    free(data);
    return false;
  }

  if (_fontData != nullptr) free(_fontData);
  _fontData = data;
  _fontDataSize = size;
  _emojiMap = map;
  if (atlas.begin(data, size)) {
    _emojiAtlas = atlas;
    _fontNativeScale = atlas.textScale();
    activeEmojiDisplay = this;
    buffer.setEmojiCallback(drawEmoji);
  } else {
    _emojiAtlas.reset();
    _fontNativeScale = 1;
    buffer.setEmojiCallback(nullptr);
    if (activeEmojiDisplay == this) activeEmojiDisplay = nullptr;
  }
  return true;
}

uint32_t LGFXDisplay::renderColor(ColorVal color) const {
#if UI_BUFFER_COLOR_DEPTH < 8
  if (color == UIColor::primary_txt) return 0;
  if (color == UIColor::window_bkg || color == UIColor::title_txt) return 1;
  if (color == UIColor::title_bkg) return 2;
  if (color == UIColor::secondary_txt) return 3;
  if (color == UIColor::warning_txt) return 4;
  if (color == UIColor::popup_bkg) return 5;
  if (color == UIColor::popup_txt) return 0;
  if (color == UIColor::corp_blue) return 6;
  return color & 0x0F;
#else
  return color;
#endif
}

void LGFXDisplay::configurePalette() {
#if UI_BUFFER_COLOR_DEPTH < 8
  buffer.createPalette(UI_PALETTE,
                       sizeof(UI_PALETTE) / sizeof(UI_PALETTE[0]));
#endif
}

uint32_t LGFXDisplay::frameHash() const {
  const uint8_t* data = static_cast<const uint8_t*>(buffer.getBuffer());
  size_t length = buffer.bufferLength();
  uint32_t hash = 2166136261UL;
  for (size_t i = 0; i < length; ++i) {
    hash = (hash ^ data[i]) * 16777619UL;
  }
  for (size_t i = 0; i < _emojiOverlayCount; ++i) {
    const EmojiOverlay& overlay = _emojiOverlays[i];
    const uint16_t values[] = {
      overlay.codepoint,
      static_cast<uint16_t>(overlay.x),
      static_cast<uint16_t>(overlay.y),
      overlay.size,
    };
    for (uint16_t value : values) {
      hash = (hash ^ static_cast<uint8_t>(value)) * 16777619UL;
      hash = (hash ^ static_cast<uint8_t>(value >> 8)) * 16777619UL;
    }
  }
  return hash;
}

bool LGFXDisplay::wasEmojiPresented(const EmojiOverlay& overlay) const {
  for (size_t i = 0; i < _presentedEmojiOverlayCount; ++i) {
    const EmojiOverlay& presented = _presentedEmojiOverlays[i];
    if (presented.codepoint == overlay.codepoint
        && presented.x == overlay.x
        && presented.y == overlay.y
        && presented.size == overlay.size) {
      return true;
    }
  }
  return false;
}

void LGFXDisplay::drawEmojiMask(int32_t x, int32_t y, int32_t size,
                                const uint8_t* pixels) {
#if UI_BUFFER_COLOR_DEPTH < 8
  if (pixels == nullptr || size <= 0) return;
  const int source_width = _emojiAtlas.width();
  const int source_height = _emojiAtlas.height();
  const uint8_t transparent = _emojiAtlas.transparent();
  for (int source_y = 0; source_y < source_height; ++source_y) {
    int top = y + source_y * size / source_height;
    int bottom = y + (source_y + 1) * size / source_height;
    for (int source_x = 0; source_x < source_width; ++source_x) {
      if (pixels[source_y * source_width + source_x] == transparent) continue;
      int left = x + source_x * size / source_width;
      int right = x + (source_x + 1) * size / source_width;
      buffer.fillRect(left, top, right - left, bottom - top,
                      TRANSPARENT_EMOJI_PALETTE_INDEX);
    }
  }
  _hasTransparentEmojiPixels = true;
#else
  (void)x;
  (void)y;
  (void)size;
  (void)pixels;
#endif
}

void LGFXDisplay::drawEmojiUnderlay(int32_t x, int32_t y, int32_t size,
                                    const uint8_t* pixels) {
#if UI_BUFFER_COLOR_DEPTH < 8
  if (pixels == nullptr || size <= 0) return;
  const int source_width = _emojiAtlas.width();
  const int source_height = _emojiAtlas.height();
  const uint8_t transparent = _emojiAtlas.transparent();
  for (int source_y = 0; source_y < source_height; ++source_y) {
    int top = y + source_y * size / source_height;
    int bottom = y + (source_y + 1) * size / source_height;
    for (int source_x = 0; source_x < source_width; ++source_x) {
      uint8_t color = pixels[source_y * source_width + source_x];
      if (color == transparent) continue;
      int red = ((color >> 5) & 7) * 255 / 7;
      int green = ((color >> 2) & 7) * 255 / 7;
      int blue = (color & 3) * 255 / 3;
      int best_index = 0;
      uint32_t best_distance = UINT32_MAX;
      // The final palette entry is reserved as the transparent color used to
      // preserve an unchanged full-color emoji in the panel framebuffer.
      for (int index = 0; index < TRANSPARENT_EMOJI_PALETTE_INDEX; ++index) {
        int delta_red = red - (int)((UI_PALETTE[index] >> 16) & 0xFF);
        int delta_green = green - (int)((UI_PALETTE[index] >> 8) & 0xFF);
        int delta_blue = blue - (int)(UI_PALETTE[index] & 0xFF);
        uint32_t distance = delta_red * delta_red
            + delta_green * delta_green + delta_blue * delta_blue;
        if (distance < best_distance) {
          best_distance = distance;
          best_index = index;
        }
      }
      int left = x + source_x * size / source_width;
      int right = x + (source_x + 1) * size / source_width;
      buffer.fillRect(left, top, right - left, bottom - top, best_index);
    }
  }
#else
  (void)x;
  (void)y;
  (void)size;
  (void)pixels;
#endif
}

int32_t LGFXDisplay::queueEmoji(lgfx::v1::LGFXBase* gfx,
                                int32_t x, int32_t y,
                                uint32_t codepoint,
                                int32_t font_height) {
  if (gfx != &buffer || codepoint > 0xFFFF || font_height <= 0
      || _emojiAtlas.glyph((uint16_t)codepoint) == nullptr) {
    return 0;
  }
  if (_emojiOverlayCount >= MAX_EMOJI_OVERLAYS) return font_height;

  EmojiOverlay& overlay = _emojiOverlays[_emojiOverlayCount++];
  overlay.codepoint = (uint16_t)codepoint;
  overlay.x = x;
  overlay.y = y;
  overlay.size = (uint16_t)font_height;
  const uint8_t* pixels = _emojiAtlas.glyph(overlay.codepoint);
  if (wasEmojiPresented(overlay)) {
    // Do not overwrite an unchanged full-color emoji with the indexed canvas
    // during periodic message-age redraws. The following direct overlay is
    // then idempotent instead of visibly blinking once per second.
    drawEmojiMask(x, y, font_height, pixels);
  } else {
    drawEmojiUnderlay(x, y, font_height, pixels);
  }
  return font_height;
}

int32_t LGFXDisplay::drawEmoji(lgfx::v1::LGFXBase* gfx,
                               int32_t x, int32_t y,
                               uint32_t codepoint,
                               int32_t font_height) {
  if (activeEmojiDisplay == nullptr) return 0;
  return activeEmojiDisplay->queueEmoji(gfx, x, y, codepoint, font_height);
}

String LGFXDisplay::mapText(const char* str) const {
  if (str == nullptr || !_emojiMap.isReady()) return String(str == nullptr ? "" : str);

  const uint8_t* text = (const uint8_t*)str;
  size_t length = strlen(str);
  String mapped;
  mapped.reserve(length + 8);
  static const uint8_t VS15[] = {0xEF, 0xB8, 0x8E};
  static const uint8_t VS16[] = {0xEF, 0xB8, 0x8F};
  static const uint8_t ZWJ[] = {0xE2, 0x80, 0x8D};

  for (size_t offset = 0; offset < length;) {
    uint16_t codepoint;
    size_t match = _emojiMap.longestMatch(text + offset, length - offset, codepoint);
    if (match != 0) {
      mapped += (char)(0xE0 | (codepoint >> 12));
      mapped += (char)(0x80 | ((codepoint >> 6) & 0x3F));
      mapped += (char)(0x80 | (codepoint & 0x3F));
      offset += match;
      continue;
    }
    if (text[offset] == ' ') {
      mapped += (char)0xC2;
      mapped += (char)0xA0;
      ++offset;
      continue;
    }
    if (isSequence(text + offset, length - offset, VS15, sizeof(VS15))
        || isSequence(text + offset, length - offset, VS16, sizeof(VS16))
        || isSequence(text + offset, length - offset, ZWJ, sizeof(ZWJ))) {
      offset += 3;
      continue;
    }
    mapped += (char)text[offset++];
  }
  return mapped;
}
