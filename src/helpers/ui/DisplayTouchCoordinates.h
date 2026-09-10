#pragma once

#include <stdint.h>

namespace mesh {
namespace ui {

// Map the rotated physical panel to the logical UI, exactly once. Both the
// native canvas and the zoomed fallback fill this same panel; their internal
// render resolution must not change the hit targets. Reject invalid samples
// before division (e.g. -1 / 3 would otherwise become a valid edge tap).
inline bool panelTouchToLogical(int panel_x, int panel_y,
                                int panel_width, int panel_height,
                                int logical_width, int logical_height,
                                int* x, int* y) {
  if (x == nullptr || y == nullptr) return false;
  *x = *y = -1;
  if (panel_width <= 0 || panel_height <= 0
      || logical_width <= 0 || logical_height <= 0
      || panel_x < 0 || panel_x >= panel_width
      || panel_y < 0 || panel_y >= panel_height) return false;
  *x = static_cast<int>(static_cast<int64_t>(panel_x) * logical_width / panel_width);
  *y = static_cast<int>(static_cast<int64_t>(panel_y) * logical_height / panel_height);
  return true;
}

}  // namespace ui
}  // namespace mesh
