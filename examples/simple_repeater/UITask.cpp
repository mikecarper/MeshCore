#include "UITask.h"
#include "target.h"
#include <Arduino.h>
#include <helpers/UsbLogging.h>
#include <helpers/CommonCLI.h>
#include <helpers/ui/WiFiSetupQrDisplay.h>
#include <helpers/ui/CompanionHomeLayout.h>
#include <helpers/ui/RadioProfileDisplayPage.h>
#include <RadioProfiles.h>
#include <Utils.h>
#include "MyMesh.h"

extern MyMesh the_mesh;

namespace {

const mesh::RadioProfiles* configuredRadioProfiles() {
  const auto* radio = the_mesh.getProfileRadio();
  return radio ? radio->profiles() : NULL;
}

const char* secondaryRadioProfileTag() {
  const auto* profiles = configuredRadioProfiles();
  return mesh::ui::radioProfileDisplayTag(true,
      profiles != NULL && profiles->secondary_temporary);
}

// The complete system-status view needs four rows on a single-radio node and
// two more when radio2 is configured.  Grouping related switches keeps a
// normal 128x64 OLED to one status page (rather than silently dropping the
// lower rows), while the measured page count below naturally splits this on
// a genuinely short display.
constexpr uint8_t RADIO_SYSTEM_STATUS_ROWS = 8;
constexpr uint8_t RADIO_SYSTEM_STATUS_ROWS_WITHOUT_RADIO2 = 5;

uint8_t radioSystemStatusRowsPerPage(DisplayDriver& display) {
  const int line_height = display.textLineHeight();
  if (line_height <= 0) return 1;
  const int row_height = line_height + 2;
  const int rows = (display.height() + 2) / row_height;
  return rows > 0 ? rows : 1;
}

uint8_t radioSystemStatusPageCount(DisplayDriver& display,
                                   bool dual_radio_enabled) {
  const uint8_t row_count = dual_radio_enabled ? RADIO_SYSTEM_STATUS_ROWS
                                                : RADIO_SYSTEM_STATUS_ROWS_WITHOUT_RADIO2;
  const uint8_t rows_per_page = radioSystemStatusRowsPerPage(display);
  return (row_count + rows_per_page - 1) / rows_per_page;
}

int drawRadioStatusState(DisplayDriver& display, int x, int y,
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

void drawRadioStatusPair(DisplayDriver& display, int y,
                         const char* first_label, bool first_enabled,
                         const char* second_label, bool second_enabled) {
  int x = drawRadioStatusState(display, 0, y, first_label, first_enabled);
  drawRadioStatusState(display, x + display.getTextWidth("  "), y,
                       second_label, second_enabled);
  display.setColor(UIColor::primary_txt);
}

void formatRadioNoiseFloor(char* noise_floor, size_t size, const char* label,
                           uint8_t profile = 0) {
  const float dbm = radio_driver.getNoiseFloorDbm(profile);
  if (dbm == 0.0f) {
    const float seconds = radio_driver.getNoiseFloorCalibrationSecondsRemaining(profile);
    if (seconds > 0.0f) {
      snprintf(noise_floor, size, "%s:%.1fs", label, seconds);
    } else {
      snprintf(noise_floor, size, "%s:WAIT", label);
    }
  } else {
    snprintf(noise_floor, size, "%s:%.1f", label, dbm);
  }
}

void drawRadioNoiseFloor(DisplayDriver& display, int y, const char* label,
                         uint8_t profile = 0) {
  char noise_floor[12];
  formatRadioNoiseFloor(noise_floor, sizeof(noise_floor), label, profile);
  display.setColor(UIColor::primary_txt);
  display.setCursor(0, y);
  display.print(noise_floor);
}

void drawRadioSystemStatusPage(DisplayDriver& display, const NodePrefs& prefs,
                               uint8_t status_page_index) {
  display.setTextSize(1);
  const auto* profiles = configuredRadioProfiles();
  const bool dual_radio = profiles != NULL && profiles->enabled();
  const uint8_t rows_per_page = radioSystemStatusRowsPerPage(display);
  const uint8_t first_row = status_page_index * rows_per_page;
  const uint8_t row_count = dual_radio ? RADIO_SYSTEM_STATUS_ROWS
                                       : RADIO_SYSTEM_STATUS_ROWS_WITHOUT_RADIO2;
  const int row_height = display.textLineHeight() + 2;

  for (uint8_t row = first_row; row < row_count
      && row < first_row + rows_per_page; ++row) {
    const int y = (row - first_row) * row_height;
    switch (row) {
      case 0: {
        char identity_prefix[7];
        mesh::Utils::toHex(identity_prefix, the_mesh.getSelfId().pub_key, 3);
        display.setColor(UIColor::primary_txt);
        display.setCursor(0, y);
        display.print("ID:");
        display.print(identity_prefix);
        break;
      }
      case 1:
#if ENV_INCLUDE_GPS == 1
        drawRadioStatusPair(display, y, "PS:", prefs.powersaving_enabled != 0,
                            "GPS:", prefs.gps_enabled != 0);
#else
        drawRadioStatusPair(display, y, "PS:", prefs.powersaving_enabled != 0,
                            "GPS:", false);
#endif
        break;
      case 2:
        drawRadioStatusPair(display, y, "FEM:",
                            board.canControlLoRaFemLna() && board.isLoRaFemLnaEnabled(),
                            "RXB:", prefs.rx_boosted_gain != 0);
        break;
      case 3:
        drawRadioStatusPair(display, y, "RXPS:", prefs.rx_powersaving_enabled != 0,
                            "CAD:", prefs.cad_enabled != 0);
        break;
      case 4: {
        char mode[10];
        snprintf(mode, sizeof(mode), "%s:%s", secondaryRadioProfileTag(),
            profiles->secondary.mode == mesh::RadioProfileMode::RxTx ? "RXTX" : "RX");
        display.setColor(UIColor::primary_txt);
        display.setCursor(0, y);
        display.print(mode);
        break;
      }
      case 5: {
        const char* cross = profiles->cross == mesh::RadioCrossMode::On ? "X:ON"
            : profiles->cross == mesh::RadioCrossMode::Off ? "X:OFF" : "X:AUTO";
        display.setColor(UIColor::primary_txt);
        display.setCursor(0, y);
        display.print(cross);
        break;
      }
      case 6:
        drawRadioNoiseFloor(display, y, "N1", 0);
        break;
      case 7:
        drawRadioNoiseFloor(display, y, "N2", 1);
        break;
    }
  }
  display.setColor(UIColor::primary_txt);
}

#if defined(HELTEC_T096)

// The native T096 canvas is 160x80. Compact F/B/S/C rows use the first 108
// pixels; this leaves a 50px status column plus a 2px right margin. The
// widest normal 6x8 label, "RXPS:OFF", is 48px, so it has a 2px buffer.
constexpr int T096_STATUS_PANEL_WIDTH = 50;
constexpr int T096_STATUS_RIGHT_MARGIN = 2;
constexpr int T096_STATUS_TOP = 10;
constexpr int T096_STATUS_LINE_HEIGHT = 10;

void drawT096State(DisplayDriver& display, int x, int y,
                   const char* label, bool enabled) {
  const char* state = enabled ? "ON" : "OFF";
  display.setColor(UIColor::primary_txt);
  display.setCursor(x, y);
  display.print(label);
  display.setColor(enabled ? UIColor::primary_txt : UIColor::warning_txt);
  display.setCursor(x + display.getTextWidth(label), y);
  display.print(state);
}

void drawT096StatusRow(DisplayDriver& display, int y,
                       const char* name, bool enabled) {
  char label[8];  // "RXPS:" plus its terminator
  snprintf(label, sizeof(label), "%s:", name);
  const char* state = enabled ? "ON" : "OFF";
  const int x = display.width() - T096_STATUS_RIGHT_MARGIN
      - display.getTextWidth(label) - display.getTextWidth(state);
  drawT096State(display, x, y, label, enabled);
}

void drawT096NoiseFloorRow(DisplayDriver& display, int y, uint8_t profile) {
  char noise_floor[12];
  const bool dual_radio = the_mesh.isDualRadioActive();
  const char* label = dual_radio ? (profile == 1 ? "N2" : "N1") : "N";
  // "N1:-120.0" needs 54px in the normal 6px font. On this final line the
  // left side contains only the short cross-mode marker, so it may safely use
  // four pixels of the otherwise empty gutter while retaining decimal dBm.
  formatRadioNoiseFloor(noise_floor, sizeof(noise_floor), label, profile);
  display.setColor(UIColor::primary_txt);
  display.setCursor(display.width() - T096_STATUS_RIGHT_MARGIN
      - display.getTextWidth(noise_floor), y);
  display.print(noise_floor);
}

void drawT096StatusPanel(DisplayDriver& display, const NodePrefs& prefs,
                          uint8_t profile) {
  // This is the ST7735 driver's standard 6x8 font, rather than Squeezed6.
  display.setTextSize(1);
  int y = T096_STATUS_TOP;
#if ENV_INCLUDE_GPS == 1
  drawT096StatusRow(display, y, "GPS", prefs.gps_enabled != 0);
#else
  drawT096StatusRow(display, y, "GPS", false);
#endif
  y += T096_STATUS_LINE_HEIGHT;
  drawT096StatusRow(display, y, "FEM", board.canControlLoRaFemLna()
      && board.isLoRaFemLnaEnabled());
  y += T096_STATUS_LINE_HEIGHT;
  drawT096StatusRow(display, y, "RXB", prefs.rx_boosted_gain != 0);
  y += T096_STATUS_LINE_HEIGHT;
  drawT096StatusRow(display, y, "RXPS", prefs.rx_powersaving_enabled != 0);

  // A configured secondary profile keeps both profiles live; show it only
  // when it exists, so the panel does not imply a second radio on every T096.
  if (the_mesh.isDualRadioActive()) {
    y += T096_STATUS_LINE_HEIGHT;
    drawT096StatusRow(display, y, secondaryRadioProfileTag(), true);
  }
  y += T096_STATUS_LINE_HEIGHT;
  drawT096StatusRow(display, y, "CAD", prefs.cad_enabled != 0);
  y += T096_STATUS_LINE_HEIGHT;
  drawT096NoiseFloorRow(display, y, profile);
  display.setColor(UIColor::primary_txt);
}

void drawT096Radio2Details(DisplayDriver& display, int mode_y, int cross_y) {
  const auto* profiles = configuredRadioProfiles();
  if (profiles == NULL || !profiles->enabled()) return;

  char mode[10];
  snprintf(mode, sizeof(mode), "%s:%s", secondaryRadioProfileTag(),
      profiles->secondary.mode == mesh::RadioProfileMode::RxTx ? "RXTX" : "RX");
  const char* cross = profiles->cross == mesh::RadioCrossMode::On ? "X:ON"
      : profiles->cross == mesh::RadioCrossMode::Off ? "X:OFF" : "X:AUTO";
  display.setColor(UIColor::primary_txt);
  display.setCursor(0, mode_y);
  display.print(mode);
  display.setCursor(0, cross_y);
  display.print(cross);
}

#endif  // HELTEC_T096

}  // namespace

#ifdef DISPLAY_REDRAW_ON_CHANGE
#include <helpers/ui/DisplayFrameSignature.h>
#endif

#ifndef USER_BTN_PRESSED
#define USER_BTN_PRESSED LOW
#endif

#ifdef ESP_PLATFORM
#include <WiFi.h>
#include <helpers/esp32/WebConfigServer.h>   // defines WITH_WEBCONFIG on ESP32
#endif


#ifdef DISPLAY_TOUCH_TOGGLE
#define TOUCH_POLL_MILLIS    50
#endif

// Wrap-safe deadline test. `millis() >= deadline` fires early for the whole
// interval before a rollover, because the deadline has already wrapped to a
// small value while millis() is still near UINT32_MAX; the signed difference
// stays correct across it.
static inline bool millisReached(unsigned long now, unsigned long deadline) {
  return (int32_t)((uint32_t)now - (uint32_t)deadline) >= 0;
}

// Applies `display.flip` when it changes, forcing a complete repaint because
// the panel's existing contents are now the wrong way up.
void UITask::applyDisplayFlip() {
#ifdef WITH_MQTT_BRIDGE
  if (_observer_prefs == NULL || _observer_prefs->display_flip == _flip_seen) return;
  _flip_seen = _observer_prefs->display_flip;
  _display->setFlipped(_flip_seen != 0);
  // Logged unconditionally: this is persisted config, so it survives a reflash
  // and is otherwise invisible when someone is chasing a wrong orientation.
  mesh::usbConsolePort().printf("Display: flip %s\n", _flip_seen ? "on (rotated 180)" : "off");
#ifdef DISPLAY_REDRAW_ON_CHANGE
  _frame_valid = false;
#endif
#ifdef DISPLAY_ACTIVITY_DASHBOARD
  _rows_valid = false;
#endif
  _next_refresh = 0;
#endif
}

#define BOOT_SCREEN_MILLIS   4000   // 4 seconds

#define POWEROFF_DELAY 3000

// 'meshcore', 128x13px
static const uint8_t meshcore_logo [] PROGMEM = {
    0x3c, 0x01, 0xe3, 0xff, 0xc7, 0xff, 0x8f, 0x03, 0x87, 0xfe, 0x1f, 0xfe, 0x1f, 0xfe, 0x1f, 0xfe, 
    0x3c, 0x03, 0xe3, 0xff, 0xc7, 0xff, 0x8e, 0x03, 0x8f, 0xfe, 0x3f, 0xfe, 0x1f, 0xff, 0x1f, 0xfe, 
    0x3e, 0x03, 0xc3, 0xff, 0x8f, 0xff, 0x0e, 0x07, 0x8f, 0xfe, 0x7f, 0xfe, 0x1f, 0xff, 0x1f, 0xfc, 
    0x3e, 0x07, 0xc7, 0x80, 0x0e, 0x00, 0x0e, 0x07, 0x9e, 0x00, 0x78, 0x0e, 0x3c, 0x0f, 0x1c, 0x00, 
    0x3e, 0x0f, 0xc7, 0x80, 0x1e, 0x00, 0x0e, 0x07, 0x1e, 0x00, 0x70, 0x0e, 0x38, 0x0f, 0x3c, 0x00, 
    0x7f, 0x0f, 0xc7, 0xfe, 0x1f, 0xfc, 0x1f, 0xff, 0x1c, 0x00, 0x70, 0x0e, 0x38, 0x0e, 0x3f, 0xf8, 
    0x7f, 0x1f, 0xc7, 0xfe, 0x0f, 0xff, 0x1f, 0xff, 0x1c, 0x00, 0xf0, 0x0e, 0x38, 0x0e, 0x3f, 0xf8, 
    0x7f, 0x3f, 0xc7, 0xfe, 0x0f, 0xff, 0x1f, 0xff, 0x1c, 0x00, 0xf0, 0x1e, 0x3f, 0xfe, 0x3f, 0xf0, 
    0x77, 0x3b, 0x87, 0x00, 0x00, 0x07, 0x1c, 0x0f, 0x3c, 0x00, 0xe0, 0x1c, 0x7f, 0xfc, 0x38, 0x00, 
    0x77, 0xfb, 0x8f, 0x00, 0x00, 0x07, 0x1c, 0x0f, 0x3c, 0x00, 0xe0, 0x1c, 0x7f, 0xf8, 0x38, 0x00, 
    0x73, 0xf3, 0x8f, 0xff, 0x0f, 0xff, 0x1c, 0x0e, 0x3f, 0xf8, 0xff, 0xfc, 0x70, 0x78, 0x7f, 0xf8, 
    0xe3, 0xe3, 0x8f, 0xff, 0x1f, 0xfe, 0x3c, 0x0e, 0x3f, 0xf8, 0xff, 0xfc, 0x70, 0x3c, 0x7f, 0xf8, 
    0xe3, 0xe3, 0x8f, 0xff, 0x1f, 0xfc, 0x3c, 0x0e, 0x1f, 0xf8, 0xff, 0xf8, 0x70, 0x3c, 0x7f, 0xf8, 
};

void UITask::begin(NodePrefs* node_prefs, const char* build_date, const char* firmware_version) {
  _prevBtnState = HIGH;
  _started_at = millis();
  _node_prefs = node_prefs;
  resetRadioProfileDisplayPage();
#ifdef DISPLAY_ACTIVITY_DASHBOARD
  ObserverDashboard::applyDarkPalette();   // retunes UIColor for this target only
#endif
  _display->servicePower(board.isExternalPowered() || board.isUsbHostConnected());
  applyDisplayFlip();
#ifdef DISPLAY_TOUCH_TOGGLE
  _touch.begin();
#endif
#ifdef DISPLAY_REDRAW_ON_CHANGE
  _frame_valid = false;
#endif

#if defined(PIN_USER_BTN) && defined(DISPLAY_CLASS)
  user_btn.begin();
#endif

  // strip off dash and commit hash by changing dash to null terminator
  // e.g: v1.2.3-abcdef -> v1.2.3
  char *version = strdup(firmware_version);
  char *dash = strchr(version, '-');
  if(dash){
    *dash = 0;
  }

  // v1.2.3 (1 Jan 2025)
  snprintf(_version_info, sizeof(_version_info), "%s (%s)", version, build_date);
  free(version);
}

void UITask::resetRadioProfileDisplayPage() {
  _radio_profile_display_page = 0;
  _dual_radio_enabled_seen = the_mesh.isDualRadioActive();
}

uint8_t UITask::currentRadioProfileDisplayPage() {
  const bool dual_radio_enabled = the_mesh.isDualRadioActive();
  if (dual_radio_enabled != _dual_radio_enabled_seen) {
    _dual_radio_enabled_seen = dual_radio_enabled;
    _radio_profile_display_page = 0;
  }
  return mesh::ui::radioProfileManualPageIndex(dual_radio_enabled,
      radioProfileSystemStatusPageCount(), _radio_profile_display_page);
}

void UITask::advanceRadioProfileDisplayPage() {
  const bool dual_radio_enabled = the_mesh.isDualRadioActive();
  const uint8_t page_count = mesh::ui::radioProfileDisplayPageCount(
      dual_radio_enabled, radioProfileSystemStatusPageCount());
  _radio_profile_display_page = (currentRadioProfileDisplayPage() + 1) % page_count;
  _next_refresh = 0;
#ifdef DISPLAY_REDRAW_ON_CHANGE
  _frame_valid = false;
#endif
#ifdef DISPLAY_ACTIVITY_DASHBOARD
  _rows_valid = false;
#endif
}

bool UITask::showingSecondaryRadioProfilePage() {
  return mesh::ui::showManualSecondaryRadioProfilePage(the_mesh.isDualRadioActive(),
      currentRadioProfileDisplayPage());
}

uint8_t UITask::radioProfileSystemStatusPageCount() const {
#if defined(HELTEC_T096)
  // The T096's dedicated 50px status column already shows every switch.
  return 0;
#else
  _display->setTextSize(1);
  return radioSystemStatusPageCount(*_display, the_mesh.isDualRadioActive());
#endif
}

bool UITask::showingRadioProfileSystemStatusPage(uint8_t* status_page_index) {
  return mesh::ui::showManualRadioProfileSystemStatusPage(the_mesh.isDualRadioActive(),
      radioProfileSystemStatusPageCount(), currentRadioProfileDisplayPage(),
      status_page_index);
}

void UITask::renderCurrScreen() {
  char tmp[80];
#ifdef DISPLAY_ACTIVITY_DASHBOARD
  _rows_valid = false;
#endif
  if ((uint32_t)(millis() - _started_at) < BOOT_SCREEN_MILLIS) { // boot screen
    // meshcore logo
    _display->setColor(UIColor::corp_blue);
    int logoWidth = 128;
    _display->drawXbm((_display->width() - logoWidth) / 2, 3, meshcore_logo, logoWidth, 13);

    // meshcore website
    const char* website = "https://meshcore.io";
    _display->setColor(UIColor::primary_txt);
    _display->setTextSize(1);
    _display->drawTextCentered(_display->width() / 2, 22, website);

    // version info
    _display->setTextSize(1);
    _display->drawTextCentered(_display->width() / 2, 35, _version_info);

    // node type
    const char* node_type = "< Repeater >";
    _display->drawTextCentered(_display->width() / 2, 48, node_type);
  } else if (_powering_off_at > 0) {
    // meshcore logo
    _display->setColor(UIColor::corp_blue);
    int logoWidth = 128;
    _display->drawXbm((_display->width() - logoWidth) / 2, 3, meshcore_logo, logoWidth, 13);

    // meshcore website
    const char* website = "https://meshcore.io";
    _display->setColor(UIColor::primary_txt);
    _display->setTextSize(1);
    _display->drawTextCentered(_display->width()/ 2, 22, website);

    // Powering off
    const char* poweroff_string = "Turning OFF";
    uint16_t poffWidth = _display->getTextWidth(poweroff_string);
    _display->setCursor((_display->width() - poffWidth) / 2, 48);
    _display->drawTextCentered(_display->width()/2, 48, poweroff_string);
  } else {  // home screen
#ifdef WITH_WEBCONFIG
    if (WebConfigServer::isRebootPending()) {
      // save confirmed on-device: show ground truth even if the browser
      // lost its connection before the confirmation reached it
      _display->setTextSize(1);
      _display->setColor(UIColor::corp_blue);
      _display->setCursor(0, 14);
      _display->print("Config saved!");
      _display->setColor(UIColor::primary_txt);
      _display->setCursor(0, 30);
      _display->print("Rebooting...");
      return;
    }
    char wc_ssid[33], wc_ip[16];
    if (WebConfigServer::getSetupInfo(wc_ssid, sizeof(wc_ssid), wc_ip, sizeof(wc_ip))) {
      // setup portal active: show join instructions instead of the home screen
      if (mesh::ui::drawWiFiSetupQr(*_display, wc_ssid, wc_ip)) {
        return;
      }
      _display->setTextSize(1);
      _display->setColor(UIColor::corp_blue);
      _display->setCursor(0, 0);
#ifdef DISPLAY_ACTIVITY_DASHBOARD
      _display->print("Observer WiFi Setup");
#else
      _display->print("WebUI WiFi Setup");
#endif

      _display->setColor(UIColor::primary_txt);
      _display->setCursor(0, 14);
      _display->print("Join WiFi:");
      _display->setColor(UIColor::warning_txt);
      _display->setCursor(6, 24);
      _display->print(wc_ssid);

      _display->setColor(UIColor::primary_txt);
      _display->setCursor(0, 40);
      _display->print("Then browse to:");
      _display->setColor(UIColor::warning_txt);
      _display->setCursor(6, 50);
      _display->print(wc_ip);
      return;
    }
#endif
    uint8_t status_page_index = 0;
    if (showingRadioProfileSystemStatusPage(&status_page_index)) {
      drawRadioSystemStatusPage(*_display, *_node_prefs, status_page_index);
      return;
    }
#ifdef DISPLAY_ACTIVITY_DASHBOARD
    renderDashboard();
    return;
#endif
    // Reserve a full measured font-height for each row on OLED/TFT/e-paper.
#if defined(HELTEC_T096)
    // Never let normal full-size rows overwrite the dedicated normal-font
    // column at the right of the native 160x80 T096 screen.
    const int content_width = _display->width() - T096_STATUS_PANEL_WIDTH
        - T096_STATUS_RIGHT_MARGIN;
#else
    const int content_width = _display->width();
#endif
    _display->setTextSize(1);
    _display->setColor(UIColor::primary_txt);
#if defined(HELTEC_T096)
    // The name gets the entire top row. The status strip begins below it, so
    // no part of the name is covered or forced into a second line.
    _display->drawTextEllipsized(0, 0, _display->width(), _node_prefs->node_name);
    const int rows_top = T096_STATUS_TOP;
    mesh::ui::BoundedTextRows rows(*_display,
        {0, rows_top, content_width, _display->height() - rows_top});
#else
    mesh::ui::BoundedTextRows rows(*_display,
        {0, 0, content_width, _display->height()});
    rows.draw(_node_prefs->node_name, false);
#endif

    const mesh::RadioProfiles* profiles = configuredRadioProfiles();
    const bool secondary_page = showingSecondaryRadioProfilePage();
    const bool dual_radio = profiles != NULL && profiles->enabled();
    float freq = _node_prefs->freq;
    float bw = _node_prefs->bw;
    uint8_t sf = _node_prefs->sf;
    uint8_t cr = _node_prefs->cr;
    const char* profile_tag = "";
    if (dual_radio) {
      const uint8_t profile = secondary_page ? 1 : 0;
      const auto& params = profiles->params(profile);
      freq = params.freq;
      bw = params.bw;
      sf = params.sf;
      cr = params.cr;
      profile_tag = mesh::ui::radioProfileDisplayTag(profile,
          profile == 1 ? profiles->secondary_temporary : profiles->primary_temporary);
    }

    // T096's normal 6x8 right column needs compact radio labels. Other
    // displays retain the longer labels for their roomier layouts.
#if defined(HELTEC_T096)
    snprintf(tmp, sizeof(tmp), "%s%sF:%06.3f S:%d", profile_tag,
             dual_radio ? " " : "", freq, sf);
#else
    if (dual_radio) {
      // R1/R2/T1/T2 needs three extra cells. Compact fields retain every RF
      // value on a 128px OLED even at the widest legal frequency/SF pair.
      snprintf(tmp, sizeof(tmp), "%s F:%06.3f S:%d", profile_tag, freq, sf);
    } else {
      snprintf(tmp, sizeof(tmp), "FREQ: %06.3f SF%d", freq, sf);
    }
#endif
    rows.draw(tmp, false);

    // bandwidth / coding rate
#if defined(HELTEC_T096)
    snprintf(tmp, sizeof(tmp), "%s%sB:%03.2f C:%d", profile_tag,
             dual_radio ? " " : "", bw, cr);
#else
    if (dual_radio) {
      snprintf(tmp, sizeof(tmp), "%s B:%03.2f C:%d", profile_tag, bw, cr);
    } else {
      snprintf(tmp, sizeof(tmp), "BW: %03.2f CR: %d", bw, cr);
    }
#endif
    rows.draw(tmp, false);

#ifdef WITH_MQTT_BRIDGE
    // Display IP address for MQTT bridge devices
    if (WiFi.status() == WL_CONNECTED) {
      IPAddress ip = WiFi.localIP();
      _display->setColor(UIColor::primary_txt);
      snprintf(tmp, sizeof(tmp), "IP: %d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3]);
      rows.draw(tmp, false);
    } else
#endif
    {
      snprintf(tmp, sizeof(tmp), "BAT: %.2fV", _board->getBattMilliVolts() / 1000.0f);
      rows.draw(tmp, false);
    }

    // Keep power-saving state visible even when the MQTT IP replaces battery.
#if defined(HELTEC_T096)
    // This is the fourth left-column row (after F, B, and battery/IP). Keep
    // its label green, with just the saved ON/OFF value carrying the state
    // color used by the status strip.
    const int power_saving_y = rows.nextY();
    if (rows.reserve()) {
      drawT096State(*_display, 0, power_saving_y, "PS: ",
          _node_prefs->powersaving_enabled != 0);
    }
    const int identity_y = rows.nextY();
    if (rows.reserve()) {
      char identity_prefix[7];
      mesh::Utils::toHex(identity_prefix, the_mesh.getSelfId().pub_key, 3);
      _display->setColor(UIColor::primary_txt);
      _display->setCursor(0, identity_y);
      _display->print("ID:");
      _display->print(identity_prefix);
    }
    drawT096Radio2Details(*_display, rows.nextY(),
        rows.nextY() + _display->textLineHeight() + 2);
#else
    snprintf(tmp, sizeof(tmp), "PS: %s", _node_prefs->powersaving_enabled ? "ON" : "OFF");
    rows.draw(tmp, false);
#endif
#if defined(HELTEC_T096)
    drawT096StatusPanel(*_display, *_node_prefs, secondary_page ? 1 : 0);
#else
    // The shared radio-profile layer reports this on every repeater board.
    // BoundedTextRows simply omits this extra row on a screen too short to
    // hold it, rather than overwriting an existing status line.
    if (the_mesh.isDualRadioActive()) rows.draw("DualRadio: ON", false);
#endif
  }
}

#ifdef DISPLAY_REDRAW_ON_CHANGE
uint32_t UITask::getFrameSignature() {
  uint32_t signature = DisplayFrameSignature::INITIAL;
  char tmp[80];

  if ((uint32_t)(millis() - _started_at) < BOOT_SCREEN_MILLIS) {
    signature = DisplayFrameSignature::append(signature, "boot");
    return DisplayFrameSignature::append(signature, _version_info);
  }

  if (_powering_off_at > 0) {
    return DisplayFrameSignature::append(signature, "powering-off");
  }

#ifdef WITH_WEBCONFIG
  if (WebConfigServer::isRebootPending()) {
    return DisplayFrameSignature::append(signature, "rebooting");
  }

  char wc_ssid[33], wc_ip[16];
  if (WebConfigServer::getSetupInfo(wc_ssid, sizeof(wc_ssid), wc_ip, sizeof(wc_ip))) {
    signature = DisplayFrameSignature::append(signature, "setup");
    signature = DisplayFrameSignature::append(signature, wc_ssid);
    return DisplayFrameSignature::append(signature, wc_ip);
  }
#endif

  signature = DisplayFrameSignature::append(signature, "home");
  signature = DisplayFrameSignature::append(signature, _node_prefs->node_name);
  uint8_t status_page_index = 0;
  if (showingRadioProfileSystemStatusPage(&status_page_index)) {
    signature = DisplayFrameSignature::append(signature, "radio-system-status");
    snprintf(tmp, sizeof(tmp), "status-page-%u", status_page_index);
    signature = DisplayFrameSignature::append(signature, tmp);
    signature = DisplayFrameSignature::append(
        signature, _node_prefs->powersaving_enabled ? "powersaving-on" : "powersaving-off");
    signature = DisplayFrameSignature::append(
        signature, _node_prefs->gps_enabled ? "gps-on" : "gps-off");
    signature = DisplayFrameSignature::append(
        signature, _board->canControlLoRaFemLna() && _board->isLoRaFemLnaEnabled()
            ? "fem-on" : "fem-off");
    signature = DisplayFrameSignature::append(
        signature, _node_prefs->rx_boosted_gain ? "rxb-on" : "rxb-off");
    signature = DisplayFrameSignature::append(
        signature, _node_prefs->rx_powersaving_enabled ? "rxps-on" : "rxps-off");
    signature = DisplayFrameSignature::append(
        signature, _node_prefs->cad_enabled ? "cad-on" : "cad-off");
    const mesh::RadioProfiles* status_profiles = configuredRadioProfiles();
    if (status_profiles != NULL && status_profiles->enabled()) {
      formatRadioNoiseFloor(tmp, sizeof(tmp), "N1", 0);
      signature = DisplayFrameSignature::append(signature, tmp);
      formatRadioNoiseFloor(tmp, sizeof(tmp), "N2", 1);
      signature = DisplayFrameSignature::append(signature, tmp);
      signature = DisplayFrameSignature::append(signature,
          status_profiles->secondary.mode == mesh::RadioProfileMode::RxTx
              ? "radio2-rxtx" : "radio2-rx");
      signature = DisplayFrameSignature::append(signature,
          status_profiles->cross == mesh::RadioCrossMode::On ? "cross-on"
              : status_profiles->cross == mesh::RadioCrossMode::Off
                  ? "cross-off" : "cross-auto");
    }
    return signature;
  }
  const mesh::RadioProfiles* profiles = configuredRadioProfiles();
  const bool secondary_page = showingSecondaryRadioProfilePage();
  const bool dual_radio = profiles != NULL && profiles->enabled();
  float freq = _node_prefs->freq;
  float bw = _node_prefs->bw;
  uint8_t sf = _node_prefs->sf;
  uint8_t cr = _node_prefs->cr;
  if (dual_radio) {
    const uint8_t profile = secondary_page ? 1 : 0;
    const auto& params = profiles->params(profile);
    freq = params.freq;
    bw = params.bw;
    sf = params.sf;
    cr = params.cr;
    signature = DisplayFrameSignature::append(signature,
        mesh::ui::radioProfileDisplayTag(profile,
            profile == 1 ? profiles->secondary_temporary : profiles->primary_temporary));
  }
  snprintf(tmp, sizeof(tmp), "FREQ: %06.3f SF%d", freq, sf);
  signature = DisplayFrameSignature::append(signature, tmp);
  snprintf(tmp, sizeof(tmp), "BW: %03.2f CR: %d", bw, cr);
  signature = DisplayFrameSignature::append(signature, tmp);

#if defined(WITH_MQTT_BRIDGE) && !defined(DISPLAY_ACTIVITY_DASHBOARD)
  if (WiFi.status() == WL_CONNECTED) {
    IPAddress ip = WiFi.localIP();
    snprintf(tmp, sizeof(tmp), "IP: %d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3]);
    signature = DisplayFrameSignature::append(signature, tmp);
  } else {
    signature = DisplayFrameSignature::append(signature, "wifi-disconnected");
  }
#endif

#ifndef DISPLAY_ACTIVITY_DASHBOARD
#ifdef WITH_MQTT_BRIDGE
  if (WiFi.status() != WL_CONNECTED) {
    snprintf(tmp, sizeof(tmp), "BAT: %.2fV", _board->getBattMilliVolts() / 1000.0f);
    signature = DisplayFrameSignature::append(signature, tmp);
  }
#else
  snprintf(tmp, sizeof(tmp), "BAT: %.2fV", _board->getBattMilliVolts() / 1000.0f);
  signature = DisplayFrameSignature::append(signature, tmp);
#endif
  signature = DisplayFrameSignature::append(
      signature, _node_prefs->powersaving_enabled ? "powersaving-on" : "powersaving-off");
#if defined(HELTEC_T096)
  signature = DisplayFrameSignature::append(
      signature, _node_prefs->gps_enabled ? "gps-on" : "gps-off");
  signature = DisplayFrameSignature::append(
      signature, _board->canControlLoRaFemLna() && _board->isLoRaFemLnaEnabled()
          ? "fem-on" : "fem-off");
  signature = DisplayFrameSignature::append(
      signature, _node_prefs->rx_boosted_gain ? "rxb-on" : "rxb-off");
  signature = DisplayFrameSignature::append(
      signature, _node_prefs->rx_powersaving_enabled ? "rxps-on" : "rxps-off");
  signature = DisplayFrameSignature::append(
      signature, _node_prefs->cad_enabled ? "cad-on" : "cad-off");
  formatRadioNoiseFloor(tmp, sizeof(tmp), dual_radio
      ? (secondary_page ? "N2" : "N1") : "N", secondary_page ? 1 : 0);
  signature = DisplayFrameSignature::append(signature, tmp);
#endif
  signature = DisplayFrameSignature::append(
      signature, the_mesh.isDualRadioActive() ? "radio2-on" : "radio2-off");
#endif

  return signature;
}
#endif

#ifdef DISPLAY_ACTIVITY_DASHBOARD
#define ACTIVITY_REFRESH_MILLIS 5000

bool UITask::buildDashboardContext(ObserverDashboard::Context* ctx) {
  if (_node_prefs == NULL) return false;
  ctx->node_name = _node_prefs->node_name;
  ctx->role_label = "REPEATER";
  ctx->freq = _node_prefs->freq;
  ctx->sf = _node_prefs->sf;
  ctx->bw = _node_prefs->bw;
  ctx->radio_label = "";
  const mesh::RadioProfiles* profiles = configuredRadioProfiles();
  if (profiles != NULL && profiles->enabled()) {
    const uint8_t profile = showingSecondaryRadioProfilePage() ? 1 : 0;
    const auto& params = profiles->params(profile);
    ctx->freq = params.freq;
    ctx->sf = params.sf;
    ctx->bw = params.bw;
    ctx->radio_label = mesh::ui::radioProfileDisplayTag(profile,
        profile == 1 ? profiles->secondary_temporary : profiles->primary_temporary);
  }
  ctx->dual_radio = the_mesh.isDualRadioActive();
#ifdef WITH_MQTT_BRIDGE
  ctx->link_up = (WiFi.status() == WL_CONNECTED);
#else
  ctx->link_up = false;
#endif
  return true;
}

void UITask::renderDashboard() {
  ObserverDashboard::Context ctx;
  if (!buildDashboardContext(&ctx)) return;

  RadioActivitySnapshot snap;
  if (_activity) {
    _activity->snapshot(millis(), &snap);
  } else {
    memset(&snap, 0, sizeof(snap));
  }

  const ObserverDashboard::Layout& layout = ObserverDashboard::activeLayout();
  ObserverDashboard::drawFull(*_display, layout, ctx, snap);
  ObserverDashboard::allRowSignatures(layout, ctx, snap, _row_signatures);
  _rows_valid = true;
  _next_activity = millis() + ACTIVITY_REFRESH_MILLIS;
}

// Repaints just the analytics rows whose contents moved. No startFrame(), so
// the header, the radio strip and the rest of the panel are never cleared.
void UITask::updateActivityRows() {
  if (!_rows_valid || _activity == NULL) return;   // not showing the dashboard

  ObserverDashboard::Context ctx;
  if (!buildDashboardContext(&ctx)) return;

  RadioActivitySnapshot snap;
  _activity->snapshot(millis(), &snap);
  ObserverDashboard::drawChangedRows(*_display, ObserverDashboard::activeLayout(), ctx, snap,
                                     _row_signatures);
}
#endif

#ifdef DISPLAY_TOUCH_TOGGLE
void UITask::toggleDisplay(const char* source) {
  _display->wake(mesh::ui::DisplayWake::Button);
  resetRadioProfileDisplayPage();
#ifdef DISPLAY_TOUCH_DEBUG
  mesh::usbConsolePort().printf("Display: %s -> %s\n", source, _display->isOn() ? "on" : "off");
#else
  (void)source;
#endif
#ifdef DISPLAY_REDRAW_ON_CHANGE
  _frame_valid = false;   // wake draws one complete current frame
#endif
#ifdef DISPLAY_ACTIVITY_DASHBOARD
  _rows_valid = false;
#endif
  _next_refresh = 0;   // redraw at once rather than showing the stale frame
}
#endif

void UITask::loop() {
  if (_display->servicePower(board.isExternalPowered() || board.isUsbHostConnected())) {
    if (_display->isOn()) resetRadioProfileDisplayPage();
    _next_refresh = 0;
#ifdef DISPLAY_REDRAW_ON_CHANGE
    _frame_valid = false;
#endif
#ifdef DISPLAY_ACTIVITY_DASHBOARD
    _rows_valid = false;
#endif
  }
#if defined(PIN_USER_BTN) && defined(DISPLAY_CLASS)
  int ev = user_btn.check();
  // Multiclick stays enabled on the R8 observer so triple-click can toggle
  // WebConfig. Collapse a completed double-click into one display action
  // instead of silently consuming both presses.
  if (ev == BUTTON_EVENT_CLICK || ev == BUTTON_EVENT_DOUBLE_CLICK) {
    // A page change is still user activity.  Capture the old state first so
    // the first press wakes to R1, while every subsequent press both extends
    // the 15-second timer and advances to the next available detail page.
    const bool display_was_on = _display->isOn();
    _display->wake(mesh::ui::DisplayWake::Button);
    if (display_was_on) {
      advanceRadioProfileDisplayPage();
    } else {
      resetRadioProfileDisplayPage();
#ifdef DISPLAY_REDRAW_ON_CHANGE
      _frame_valid = false;
#endif
#ifdef DISPLAY_ACTIVITY_DASHBOARD
      _rows_valid = false;
#endif
    }
  } else if (ev == BUTTON_EVENT_LONG_PRESS) {
      _display->wake(mesh::ui::DisplayWake::Button);
      resetRadioProfileDisplayPage();
      mesh::usbConsolePort().printf("Powering Off\r\n");
      _powering_off_at = millis() + POWEROFF_DELAY;
#ifdef DISPLAY_REDRAW_ON_CHANGE
      _frame_valid = false;
#endif
#ifdef DISPLAY_ACTIVITY_DASHBOARD
      _rows_valid = false;
#endif
      _next_refresh = 0;
#ifdef WITH_WEBCONFIG
  } else if (ev == BUTTON_EVENT_TRIPLE_CLICK) {
    // Preserve the fork's persistent WebUI toggle. R8 observer repeaters keep
    // multiclick enabled specifically for this action.
    WebConfigServer::requestToggleFromButton();
    _display->wake(mesh::ui::DisplayWake::Button);
    resetRadioProfileDisplayPage();
#endif
  }
#endif

#ifdef DISPLAY_TOUCH_TOGGLE
  {
    unsigned long now = millis();
    if (millisReached(now, _next_touch)) {
      _next_touch = now + TOUCH_POLL_MILLIS;
      if (_powering_off_at == 0 && _touch.checkTap(now)) toggleDisplay("touch");
    }
  }
#endif

  if (_display->isOn()) {
    // Apply a live orientation change before drawing, including the first frame
    // after a preference was changed while the panel was blanked.
    applyDisplayFlip();
    if (millisReached(millis(), _next_refresh)) {
      bool redraw = true;
#ifdef DISPLAY_REDRAW_ON_CHANGE
      uint32_t frame_signature = getFrameSignature();
      redraw = !_frame_valid || frame_signature != _last_frame_signature;
#endif
      if (redraw) {
        _display->startFrame();
        renderCurrScreen();
        _display->endFrame();
#ifdef DISPLAY_REDRAW_ON_CHANGE
        _last_frame_signature = frame_signature;
        _frame_valid = true;
#endif
      }
#ifdef DISPLAY_ACTIVITY_DASHBOARD
      else if (millisReached(millis(), _next_activity)) {
        updateActivityRows();
        _next_activity = millis() + ACTIVITY_REFRESH_MILLIS;
      }
#endif

      unsigned long refresh_interval = 1000;  // check normal status changes every second
#if defined(HELTEC_T096)
      // The T096's TFT can show each tenth while the short, 3.2-second noise
      // floor collection window is active. Keep every other screen at the
      // normal cadence, particularly displays with slow/expensive refreshes.
      if (radio_driver.getNoiseFloorCalibrationSecondsRemaining() > 0.0f) {
        refresh_interval = 100;
      }
#endif
      _next_refresh = millis() + refresh_interval;
    }
  }

  if (_powering_off_at > 0) { // power off timer armed
#if defined(LED_PIN) && defined(LED_STATE_ON)
    digitalWrite(LED_PIN, LED_STATE_ON); // switch on the led until poweroff
    delay(1000);
    digitalWrite(LED_PIN, !LED_STATE_ON); // off LED
#endif
    if (millisReached(millis(), _powering_off_at)) {
      _board->powerOff();  // should not return
    }
  }
}
