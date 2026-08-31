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
};

inline CompanionTransportSelectorLayout makeCompanionTransportSelectorLayout(
    int width, int height) {
  const bool tall_display = height >= 96;
  const int margin = 2;
  const int gap = 4;
  const int box_y = tall_display ? 40 : 20;
  const int prompt_y = height - (tall_display ? 22 : 11);
  int box_height = prompt_y - box_y - 8;
  if (box_height > 72) box_height = 72;
  if (box_height < 20) box_height = 20;
  const int box_width = (width - margin * 2 - gap) / 2;
  const int bluetooth_x = margin + box_width + gap;

  return {
      {margin, box_y, box_width, box_height},
      {bluetooth_x, box_y, box_width, box_height},
      tall_display ? 17 : 0,
      prompt_y,
      tall_display,
  };
}

}  // namespace ui
}  // namespace mesh
