#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {
namespace ui {

class ColorEmojiAtlas {
  static constexpr size_t FOOTER_SIZE = 40;
  static constexpr size_t ATLAS_HEADER_SIZE = 12;
  static constexpr uint16_t FIRST_EMOJI = 0xE000;

  const uint8_t* _pixels = nullptr;
  uint16_t _count = 0;
  uint16_t _stride = 0;
  uint8_t _width = 0;
  uint8_t _height = 0;
  uint8_t _transparent = 0;
  uint8_t _text_scale = 1;

  static uint16_t read16(const uint8_t* p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
  }

  static uint32_t read32(const uint8_t* p) {
    return (uint32_t)p[0]
        | ((uint32_t)p[1] << 8)
        | ((uint32_t)p[2] << 16)
        | ((uint32_t)p[3] << 24);
  }

  static uint32_t crc32(const uint8_t* data, size_t size) {
    uint32_t crc = 0xFFFFFFFFUL;
    for (size_t i = 0; i < size; ++i) {
      crc ^= data[i];
      for (uint8_t bit = 0; bit < 8; ++bit) {
        crc = (crc >> 1) ^ (0xEDB88320UL & (0U - (crc & 1U)));
      }
    }
    return ~crc;
  }

public:
  void reset() {
    _pixels = nullptr;
    _count = 0;
    _stride = 0;
    _width = 0;
    _height = 0;
    _transparent = 0;
    _text_scale = 1;
  }

  bool begin(const uint8_t* font_data, size_t font_size) {
    reset();
    if (font_data == nullptr || font_size < FOOTER_SIZE) return false;

    const uint8_t* footer = font_data + font_size - FOOTER_SIZE;
    static const uint8_t FOOTER_MAGIC[8] = {
      'M', 'C', 'E', 'M', 'A', 'P', '2', 0
    };
    if (memcmp(footer, FOOTER_MAGIC, sizeof(FOOTER_MAGIC)) != 0) {
      return false;
    }

    uint32_t atlas_offset = read32(footer + 20);
    uint32_t atlas_size = read32(footer + 24);
    uint32_t atlas_crc = read32(footer + 28);
    if (atlas_offset > font_size - FOOTER_SIZE
        || atlas_size > font_size - FOOTER_SIZE - atlas_offset
        || atlas_size < ATLAS_HEADER_SIZE) {
      return false;
    }

    const uint8_t* atlas = font_data + atlas_offset;
    if (crc32(atlas, atlas_size) != atlas_crc
        || memcmp(atlas, "CE01", 4) != 0) {
      return false;
    }

    uint16_t count = read16(atlas + 4);
    uint8_t width = atlas[6];
    uint8_t height = atlas[7];
    uint8_t transparent = atlas[8];
    uint8_t text_scale = atlas[9];
    uint16_t stride = read16(atlas + 10);
    if (count == 0 || width == 0 || height == 0
        || width > 64 || height > 64
        || text_scale == 0 || text_scale > 8
        || stride != (uint16_t)width * height
        || (size_t)count * stride != atlas_size - ATLAS_HEADER_SIZE) {
      return false;
    }

    _pixels = atlas + ATLAS_HEADER_SIZE;
    _count = count;
    _stride = stride;
    _width = width;
    _height = height;
    _transparent = transparent;
    _text_scale = text_scale;
    return true;
  }

  bool isReady() const { return _pixels != nullptr; }
  uint8_t width() const { return _width; }
  uint8_t height() const { return _height; }
  uint8_t transparent() const { return _transparent; }
  uint8_t textScale() const { return _text_scale; }
  uint16_t count() const { return _count; }

  const uint8_t* glyph(uint16_t mapped_codepoint) const {
    if (!isReady() || mapped_codepoint < FIRST_EMOJI) return nullptr;
    uint16_t index = mapped_codepoint - FIRST_EMOJI;
    if (index >= _count) return nullptr;
    return _pixels + (size_t)index * _stride;
  }
};

}  // namespace ui
}  // namespace mesh
