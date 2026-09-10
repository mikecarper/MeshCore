#pragma once

namespace mesh {
namespace ui {

struct CompanionTransportChoiceRegion {
  int x;
  int y;
  int width;
  int height;
};

struct CompanionTransportSelectorLayout {
  CompanionTransportChoiceRegion wifi;
  CompanionTransportChoiceRegion bluetooth;
  int title_y;
  int prompt_y;
  bool show_title;
  int side_nav_width;
};

inline CompanionTransportSelectorLayout makeCompanionTransportSelectorLayout(
    int width, int height, int center_zone_percent = 100) {
  if (center_zone_percent < 0) center_zone_percent = 0;
  if (center_zone_percent > 100) center_zone_percent = 100;
  const int side_nav_width = width * ((100 - center_zone_percent) / 2) / 100;
  const bool tall_display = height >= 96;
  const int margin = 2;
  const int gap = 4;
  const int box_y = tall_display ? 40 : 20;
  const int prompt_y = height - (tall_display ? 17 : 11);
  int box_height = prompt_y - box_y - (tall_display ? 3 : 8);
  if (box_height > 100) box_height = 100;
  if (box_height < 20) box_height = 20;
  const int available = width - side_nav_width * 2 - margin * 2 - gap;
  const int box_width = available > 0 ? available / 2 : 0;
  const int bluetooth_x = side_nav_width + margin + box_width + gap;

  return {
      {side_nav_width + margin, box_y, box_width, box_height},
      {bluetooth_x, box_y, box_width, box_height},
      tall_display ? 14 : 0,
      prompt_y,
      tall_display,
      side_nav_width,
  };
}

}  // namespace ui
}  // namespace mesh
