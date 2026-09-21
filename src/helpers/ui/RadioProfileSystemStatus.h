#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include <RadioProfiles.h>
#include <helpers/ui/DisplayDriver.h>
#include <helpers/ui/RadioProfileDisplayPage.h>

namespace mesh { namespace ui {

// A compact, paged rendering of the radio/system state shared by the role
// UIs.  Keeping the rows discrete lets a 72x40 screen page them safely while
// a 128x64 panel uses only two status pages.
struct RadioProfileSystemStatus {
  const uint8_t* public_key = nullptr;
  bool powersaving_enabled = false;
  bool gps_enabled = false;
  bool fem_enabled = false;
  bool rx_boosted_gain = false;
  bool rx_powersaving_enabled = false;
  bool cad_enabled = false;
  bool dual_radio_enabled = false;
  bool secondary_temporary = false;
  RadioProfileMode secondary_mode = RadioProfileMode::Rx;
  RadioCrossMode cross = RadioCrossMode::Auto;
  float noise_floor_1 = 0.0f;
  float noise_floor_2 = 0.0f;
  float noise_floor_1_seconds = 0.0f;
  float noise_floor_2_seconds = 0.0f;
};

inline uint8_t radioProfileSystemStatusRowCount(bool dual_radio_enabled,
                                                bool narrow = false) {
  // ID, PS/GPS, FEM/RXB, RXPS/CAD, N1, plus R2 mode, cross, and N2.
  // Narrow panels cannot fit a state pair on one line, so each switch gets
  // its own row instead of clipping the right-hand state.
  if (narrow) return dual_radio_enabled ? 11 : 8;
  return dual_radio_enabled ? 8 : 5;
}

inline uint8_t radioProfileSystemStatusRowsPerPage(
    DisplayDriver& display, int top = 0) {
  const int line_height = display.textLineHeight();
  if (line_height <= 0 || top >= display.height()) return 1;
  const int row_height = line_height + 2;
  const int rows = (display.height() - top + 2) / row_height;
  return rows > 0 ? rows : 1;
}

inline uint8_t radioProfileSystemStatusPageCount(
    DisplayDriver& display, bool dual_radio_enabled, int top = 0) {
  const uint8_t rows = radioProfileSystemStatusRowsPerPage(display, top);
  const uint8_t count = radioProfileSystemStatusRowCount(
      dual_radio_enabled, display.width() < 104);
  return (count + rows - 1) / rows;
}

inline int drawRadioProfileStatusState(DisplayDriver& display, int x, int y,
                                       const char* label, bool enabled) {
  const char* state = enabled ? "ON" : "OFF";
  display.setColor(UIColor::primary_txt);
  display.setCursor(x, y);
  display.print(label);
  x += display.getTextWidth(label);
  display.setColor(enabled ? UIColor::primary_txt : UIColor::warning_txt);
  display.setCursor(x, y);
  display.print(state);
  return x + display.getTextWidth(state);
}

inline void drawRadioProfileStatusPair(DisplayDriver& display, int y,
                                       const char* first_label,
                                       bool first_enabled,
                                       const char* second_label,
                                       bool second_enabled) {
  const int x = drawRadioProfileStatusState(display, 0, y, first_label,
                                            first_enabled);
  drawRadioProfileStatusState(display, x + display.getTextWidth("  "), y,
                              second_label, second_enabled);
  display.setColor(UIColor::primary_txt);
}

inline void formatRadioProfileNoiseFloor(char* buffer, size_t size,
                                         const char* label, float dbm,
                                         float seconds_remaining) {
  if (dbm != 0.0f) {
    snprintf(buffer, size, "%s:%.1f", label, dbm);
  } else if (seconds_remaining > 0.0f) {
    snprintf(buffer, size, "%s:%.1fs", label, seconds_remaining);
  } else {
    snprintf(buffer, size, "%s:WAIT", label);
  }
}

inline void drawRadioProfileSystemStatusPage(
    DisplayDriver& display, const RadioProfileSystemStatus& status,
    uint8_t page_index, int top = 0) {
  display.setTextSize(1);
  const uint8_t rows_per_page = radioProfileSystemStatusRowsPerPage(display,
                                                                      top);
  const uint8_t first_row = page_index * rows_per_page;
  // The widest paired row (RXPS/CAD) occupies 102 pixels in the standard
  // six-pixel font, so 96-pixel panels must use the narrow layout too.
  const bool narrow = display.width() < 104;
  const uint8_t row_count = radioProfileSystemStatusRowCount(
      status.dual_radio_enabled, narrow);
  const int row_height = display.textLineHeight() + 2;

  for (uint8_t row = first_row; row < row_count
      && row < first_row + rows_per_page; ++row) {
    const int y = top + (row - first_row) * row_height;
    if (narrow) {
      if (row == 0) {
        char identity[10] = "ID:------";
        if (status.public_key != nullptr) {
          snprintf(identity, sizeof(identity), "ID:%02X%02X%02X",
                   status.public_key[0], status.public_key[1],
                   status.public_key[2]);
        }
        display.setColor(UIColor::primary_txt);
        display.setCursor(0, y);
        display.print(identity);
      } else if (row == 1) {
        drawRadioProfileStatusState(display, 0, y, "PS:",
                                    status.powersaving_enabled);
      } else if (row == 2) {
        drawRadioProfileStatusState(display, 0, y, "GPS:",
                                    status.gps_enabled);
      } else if (row == 3) {
        drawRadioProfileStatusState(display, 0, y, "FEM:",
                                    status.fem_enabled);
      } else if (row == 4) {
        drawRadioProfileStatusState(display, 0, y, "RXB:",
                                    status.rx_boosted_gain);
      } else if (row == 5) {
        drawRadioProfileStatusState(display, 0, y, "RXPS:",
                                    status.rx_powersaving_enabled);
      } else if (row == 6) {
        drawRadioProfileStatusState(display, 0, y, "CAD:",
                                    status.cad_enabled);
      } else if (status.dual_radio_enabled && row == 7) {
        char mode[12];
        snprintf(mode, sizeof(mode), "%s:%s",
                 radioProfileDisplayTag(true, status.secondary_temporary),
                 status.secondary_mode == RadioProfileMode::RxTx ? "RXTX"
                                                                  : "RX");
        display.setColor(UIColor::primary_txt);
        display.setCursor(0, y);
        display.print(mode);
      } else if (status.dual_radio_enabled && row == 8) {
        const char* cross = status.cross == RadioCrossMode::On ? "X:ON"
            : status.cross == RadioCrossMode::Off ? "X:OFF" : "X:AUTO";
        display.setColor(UIColor::primary_txt);
        display.setCursor(0, y);
        display.print(cross);
      } else {
        const bool second_noise = status.dual_radio_enabled && row == 10;
        char noise[12];
        formatRadioProfileNoiseFloor(noise, sizeof(noise),
            second_noise ? "N2" : "N1",
            second_noise ? status.noise_floor_2 : status.noise_floor_1,
            second_noise ? status.noise_floor_2_seconds
                         : status.noise_floor_1_seconds);
        display.setColor(UIColor::primary_txt);
        display.setCursor(0, y);
        display.print(noise);
      }
      continue;
    }
    switch (row) {
      case 0: {
        char identity[10] = "ID:------";
        if (status.public_key != nullptr) {
          snprintf(identity, sizeof(identity), "ID:%02X%02X%02X",
                   status.public_key[0], status.public_key[1],
                   status.public_key[2]);
        }
        display.setColor(UIColor::primary_txt);
        display.setCursor(0, y);
        display.print(identity);
        break;
      }
      case 1:
        drawRadioProfileStatusPair(display, y, "PS:",
                                   status.powersaving_enabled, "GPS:",
                                   status.gps_enabled);
        break;
      case 2:
        drawRadioProfileStatusPair(display, y, "FEM:", status.fem_enabled,
                                   "RXB:", status.rx_boosted_gain);
        break;
      case 3:
        drawRadioProfileStatusPair(display, y, "RXPS:",
                                   status.rx_powersaving_enabled, "CAD:",
                                   status.cad_enabled);
        break;
      case 4:
        if (status.dual_radio_enabled) {
          char mode[12];
          snprintf(mode, sizeof(mode), "%s:%s",
                   radioProfileDisplayTag(true, status.secondary_temporary),
                   status.secondary_mode == RadioProfileMode::RxTx ? "RXTX"
                                                                    : "RX");
          display.setColor(UIColor::primary_txt);
          display.setCursor(0, y);
          display.print(mode);
        } else {
          char noise[12];
          formatRadioProfileNoiseFloor(noise, sizeof(noise), "N1",
                                       status.noise_floor_1,
                                       status.noise_floor_1_seconds);
          display.setColor(UIColor::primary_txt);
          display.setCursor(0, y);
          display.print(noise);
        }
        break;
      case 5: {
        const char* cross = status.cross == RadioCrossMode::On ? "X:ON"
            : status.cross == RadioCrossMode::Off ? "X:OFF" : "X:AUTO";
        display.setColor(UIColor::primary_txt);
        display.setCursor(0, y);
        display.print(cross);
        break;
      }
      case 6: {
        char noise[12];
        formatRadioProfileNoiseFloor(noise, sizeof(noise), "N1",
                                     status.noise_floor_1,
                                     status.noise_floor_1_seconds);
        display.setColor(UIColor::primary_txt);
        display.setCursor(0, y);
        display.print(noise);
        break;
      }
      case 7: {
        char noise[12];
        formatRadioProfileNoiseFloor(noise, sizeof(noise), "N2",
                                     status.noise_floor_2,
                                     status.noise_floor_2_seconds);
        display.setColor(UIColor::primary_txt);
        display.setCursor(0, y);
        display.print(noise);
        break;
      }
    }
  }
  display.setColor(UIColor::primary_txt);
}

} }  // namespace mesh::ui
