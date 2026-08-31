#pragma once

#include <stdint.h>

namespace mesh {
namespace ui {

struct IndicatorRenderProfile {
  uint8_t coordinate_scale;
  float output_zoom;
  uint16_t canvas_size;
};

// Full-size Indicator builds retain the established 160x160 logical
// coordinate system even when the backing canvas is the panel's native
// 480x480. Screens with enough room to reflow can use this distinction to
// choose substantially larger text without changing the 320px BLE fallback.
inline bool usesNativeIndicatorTypography(
    int width, int height, int render_width, int render_height) {
  return width == 160 && height == 160
      && render_width == 480 && render_height == 480;
}

// ESP-NOW and BLE are the Indicator's tightest internal-RAM combination.
// Every other exclusive Full-Companion combination can use a native 480px
// internal canvas while preserving the established 160px UI coordinates.
inline IndicatorRenderProfile selectIndicatorRenderProfile(
    bool primary_espnow, bool companion_wifi_active) {
  if (primary_espnow && !companion_wifi_active) {
    return {2, 1.5f, 320};
  }
  return {3, 1.0f, 480};
}

// Native rendering has enough pixel density to make ordinary UI text more
// legible without changing the established 160x160 layout. Keep compact
// message chrome deliberately small, and keep the memory-saving 320 profile
// visually identical to existing Indicator images.
inline float selectIndicatorTextScale(uint16_t canvas_size, bool compact) {
  return canvas_size == 480 && !compact ? 1.2f : 1.0f;
}

}  // namespace ui
}  // namespace mesh
