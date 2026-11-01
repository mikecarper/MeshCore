#pragma once

namespace mesh { namespace ui { namespace wio_l1 {

inline int visibleRows(int height, int top, int glyph_height, int spacing, int limit) {
  if (spacing <= 0 || glyph_height <= 0 || height - top < glyph_height || limit <= 0)
    return 0;
  const int rows = 1 + (height - top - glyph_height) / spacing;
  return rows < limit ? rows : limit;
}

}}}
