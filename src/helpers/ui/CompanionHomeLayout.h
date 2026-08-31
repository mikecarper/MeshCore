#pragma once

#include <helpers/ui/DisplayDriver.h>
#include <helpers/ui/IndicatorRenderProfile.h>

namespace mesh {
namespace ui {

struct DisplayRegion {
  int x;
  int y;
  int width;
  int height;

  int right() const { return x + width; }
  int bottom() const { return y + height; }
};

struct CompanionHomeLayout {
  DisplayRegion info;
  DisplayRegion pairing;
  int instruction_y;
  int network_y;
  int pairing_label_y;
  int pairing_value_y;
};

struct CompactCompanionPairingLayout {
  DisplayRegion pairing;
  int pairing_label_y;
  int pairing_value_y;
};

// Full-size Indicator builds keep the established 160x160 logical coordinate
// system even when they obtain a native 480x480 render buffer. Expose that
// distinction to the home page so it can spend the extra pixel density on
// larger, deliberately reflowed text without changing the 320 BLE profile or
// unrelated displays that happen to have a tall viewport.
inline bool usesExpandedCompanionHomeTypography(
    int width, int height, int render_width, int render_height) {
  return usesNativeIndicatorTypography(
      width, height, render_width, render_height);
}

// The common Companion OLED/TFT viewport is 128x64.  Its normal instruction
// and network rows occupy the same lower area needed by the Bluetooth status,
// so an active PIN/connection replaces those rows with this compact block.
// The INBOX title at y=22 with a size-2 fallback font ends at y=38; the block
// begins exactly there and keeps a size-1 label plus size-2 value on-screen.
inline bool usesCompactCompanionPairingLayout(int width, int height) {
  return width == 128 && height == 64;
}

inline CompactCompanionPairingLayout makeCompactCompanionPairingLayout(
    int width, int height) {
  const int pairing_height = height >= 26 ? 26 : height;
  const int pairing_y = height - pairing_height;
  const int pairing_margin = width >= 16 ? 4 : 0;
  return {
      {pairing_margin, pairing_y, width - pairing_margin * 2, pairing_height},
      pairing_y,
      pairing_y + 10,
  };
}

// The large Companion home page keeps ordinary instructions/network status in
// the upper half and reserves a visually separate pairing block around four
// fifths of the display height.  The Indicator renders a 160x160 logical UI,
// for which this produces a 48-pixel-tall block centered at y=128.
inline CompanionHomeLayout makeLargeCompanionHomeLayout(int width,
                                                        int height,
                                                        bool expanded = false) {
  if (expanded && width == 160 && height == 160) {
    // Native 480 uses three large vertical bands: an INBOX title above this
    // count region, then one lower region that is either the BLE state or a
    // large tap/network hint. Reusing the lower region conditionally gives
    // the prominent text room to grow without overlapping another status.
    return {
        {2, 61, 156, 39},
        {8, 102, 144, 58},
        61,
        61,
        102,
        131,
    };
  }

  // Ordinary one-line status needs almost the full logical width when the
  // Indicator's recovered mono font is active. The PIN block gets the larger
  // inset so it remains visibly distinct from the page background.
  const int info_margin = width >= 80 ? 2 : 0;
  const int pairing_margin = width >= 80 ? 8 : 2;
  // Native 480 rendering enlarges non-compact Indicator text by 20 percent.
  // Four extra logical pixels keep its size-3 PIN inside this block while the
  // 320 fallback retains the same geometry and gains a little more breathing
  // room.
  const int pairing_height = height >= 128 ? 48 : 28;
  const int pairing_center = (height * 4) / 5;
  int pairing_y = pairing_center - pairing_height / 2;
  if (pairing_y < 0) pairing_y = 0;
  if (pairing_y + pairing_height > height) {
    pairing_y = height - pairing_height;
  }

  const int info_y = height >= 128 ? 40 : 18;
  int info_bottom = pairing_y - 12;
  if (info_bottom < info_y) info_bottom = info_y;

  CompanionHomeLayout layout = {
      {info_margin, info_y, width - info_margin * 2,
       info_bottom - info_y},
      {pairing_margin, pairing_y, width - pairing_margin * 2,
       pairing_height},
      info_y + 2,
      info_y + 18,
      pairing_y + 4,
      pairing_y + 17,
  };
  return layout;
}

inline bool displayRegionsOverlap(const DisplayRegion& left,
                                  const DisplayRegion& right) {
  return left.x < right.right() && right.x < left.right()
      && left.y < right.bottom() && right.y < left.bottom();
}

inline bool displayRegionContainsLine(const DisplayRegion& region, int y,
                                      int line_height) {
  return y >= region.y && line_height >= 0
      && y + line_height <= region.bottom();
}

// Explicitly repaint a reserved region.  Full frames already clear the
// canvas, but keeping this local clear makes the layout safe if a display
// driver later gains partial home-page redraws or a PIN/status disappears.
inline void clearDisplayRegion(DisplayDriver& display,
                               const DisplayRegion& region) {
  if (region.width > 0 && region.height > 0) {
    display.fillRect(region.x, region.y, region.width, region.height);
  }
}

// Center short text inside the supplied region and ellipsize long text from
// the region's left edge.  In both cases the rendered row is horizontally
// bounded by the region and cannot run into an adjacent UI block.
inline void drawTextCenteredEllipsized(DisplayDriver& display,
                                       const DisplayRegion& region, int y,
                                       const char* text) {
  if (text == nullptr || text[0] == 0 || region.width <= 0) return;
  const int text_width = display.getTextWidth(text);
  if (text_width <= region.width) {
    display.setCursor(region.x + (region.width - text_width) / 2, y);
    display.print(text);
  } else {
    display.drawTextEllipsized(region.x, y, region.width, text);
  }
}

}  // namespace ui
}  // namespace mesh
