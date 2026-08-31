#pragma once

#include "DisplayDriver.h"

// Shared default palette for displays that can render real colour. Monochrome
// OLED and e-paper drivers deliberately keep their own black/white palettes:
// e-paper in particular should retain its white idle background.
namespace mesh {
namespace ui {
namespace color_theme {

constexpr ColorVal rgb565(uint8_t red, uint8_t green, uint8_t blue) {
  return (ColorVal)(((red & 0xF8) << 8)
      | ((green & 0xFC) << 3) | (blue >> 3));
}

// Expand a rendered RGB565 colour for indexed canvases. Keeping the indexed
// palette derived from the semantic RGB565 slots prevents the Indicator's
// low-memory 4-bit canvas from silently drifting to a different theme.
constexpr uint32_t rgb888(ColorVal color) {
  return ((uint32_t)(((color >> 11) & 0x1F) * 255 / 31) << 16)
      | ((uint32_t)(((color >> 5) & 0x3F) * 255 / 63) << 8)
      | (uint32_t)((color & 0x1F) * 255 / 31);
}

constexpr ColorVal WINDOW_BACKGROUND = rgb565(10, 12, 16);
constexpr ColorVal TITLE_BACKGROUND = rgb565(18, 38, 74);
constexpr ColorVal TEXT = rgb565(232, 238, 246);
constexpr ColorVal SECONDARY_TEXT = rgb565(122, 134, 150);
constexpr ColorVal WARNING_TEXT = rgb565(248, 176, 72);
// Keep this distinct from TITLE_BACKGROUND. Indexed-colour drivers identify
// semantic slots by their RGB565 value before selecting a palette entry.
constexpr ColorVal POPUP_BACKGROUND = rgb565(20, 48, 70);
constexpr ColorVal ACCENT = rgb565(64, 176, 240);

enum IndexedColor : uint8_t {
  INDEX_BACKGROUND = 0,
  INDEX_TEXT = 1,
  INDEX_TITLE_BACKGROUND = 2,
  INDEX_SECONDARY_TEXT = 3,
  INDEX_WARNING_TEXT = 4,
  INDEX_POPUP_BACKGROUND = 5,
  INDEX_ACCENT = 6,
};

}  // namespace color_theme
}  // namespace ui
}  // namespace mesh
