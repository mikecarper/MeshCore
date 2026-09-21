#include "UITask.h"
#include "target.h"
#include <Arduino.h>
#include <helpers/UsbLogging.h>
#include <helpers/CommonCLI.h>
#include <helpers/ui/WiFiSetupQrDisplay.h>
#include <helpers/ui/RadioProfileDisplayPage.h>
#include <helpers/ui/RadioProfileSystemStatus.h>
#include <RadioProfiles.h>
#include "MyMesh.h"

extern MyMesh the_mesh;

namespace {

const mesh::RadioProfiles* configuredRadioProfiles() {
  const auto* radio = the_mesh.getProfileRadio();
  return radio ? radio->profiles() : NULL;
}

mesh::ui::RadioProfileSystemStatus radioProfileSystemStatus(
    const NodePrefs& prefs) {
  mesh::ui::RadioProfileSystemStatus status;
  const auto* profiles = configuredRadioProfiles();
  status.public_key = the_mesh.getSelfId().pub_key;
  status.powersaving_enabled = prefs.powersaving_enabled != 0;
#if ENV_INCLUDE_GPS == 1
  status.gps_enabled = prefs.gps_enabled != 0;
#endif
  status.fem_enabled = board.canControlLoRaFemLna()
      && board.isLoRaFemLnaEnabled();
  status.rx_boosted_gain = prefs.rx_boosted_gain != 0;
  status.rx_powersaving_enabled = prefs.rx_powersaving_enabled != 0;
  status.cad_enabled = prefs.cad_enabled != 0;
  status.dual_radio_enabled = profiles != NULL && profiles->enabled();
  if (status.dual_radio_enabled) {
    status.secondary_temporary = profiles->secondary_temporary;
    status.secondary_mode = profiles->secondary.mode;
    status.cross = profiles->cross;
  }
  status.noise_floor_1 = radio_driver.getNoiseFloorDbm(0);
  status.noise_floor_1_seconds =
      radio_driver.getNoiseFloorCalibrationSecondsRemaining(0);
  if (status.dual_radio_enabled) {
    status.noise_floor_2 = radio_driver.getNoiseFloorDbm(1);
    status.noise_floor_2_seconds =
        radio_driver.getNoiseFloorCalibrationSecondsRemaining(1);
  }
  return status;
}

uint8_t radioProfileStatusPageCount(DisplayDriver& display,
                                    bool dual_radio_enabled) {
  display.setTextSize(1);
  return mesh::ui::radioProfileSystemStatusPageCount(display,
                                                       dual_radio_enabled);
}

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

#ifdef DISPLAY_TOUCH_TOGGLE
#define POWEROFF_DELAY       3000
#endif

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
#if defined(PIN_USER_BTN) && defined(DISPLAY_CLASS) \
    && (defined(DISPLAY_TOUCH_TOGGLE) \
        || (defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) \
            && MOMENTARY_BUTTON_WAKE_FROM_SLEEP))
  user_btn.begin();
#endif
#ifdef DISPLAY_REDRAW_ON_CHANGE
  _frame_valid = false;
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
  _radio_profile_page_started_at = millis();
  _dual_radio_enabled_seen = the_mesh.isDualRadioActive();
}

bool UITask::showingSecondaryRadioProfilePage() {
  const bool dual_radio_enabled = the_mesh.isDualRadioActive();
  const uint32_t now = millis();
  if (dual_radio_enabled != _dual_radio_enabled_seen) {
    _dual_radio_enabled_seen = dual_radio_enabled;
    _radio_profile_page_started_at = now;
  }
  const uint8_t status_pages = radioProfileStatusPageCount(*_display,
                                                            dual_radio_enabled);
  return mesh::ui::showSecondaryRadioProfilePage(dual_radio_enabled,
      status_pages,
      now - _radio_profile_page_started_at);
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
    uint16_t websiteWidth = _display->getTextWidth(website);
    _display->setCursor((_display->width() - websiteWidth) / 2, 22);
    _display->print(website);

    // version info
    _display->setTextSize(1);
    uint16_t versionWidth = _display->getTextWidth(_version_info);
    _display->setCursor((_display->width() - versionWidth) / 2, 35);
    _display->print(_version_info);

    // node type
    const char* node_type = "< Room Server >";
    uint16_t typeWidth = _display->getTextWidth(node_type);
    _display->setCursor((_display->width() - typeWidth) / 2, 48);
    _display->print(node_type);
#ifdef DISPLAY_TOUCH_TOGGLE
  } else if (_powering_off_at > 0) {
    _display->setColor(UIColor::corp_blue);
    _display->drawXbm(0, 3, meshcore_logo, 128, 13);
    _display->setTextSize(1);
    _display->setColor(UIColor::primary_txt);
    _display->drawTextCentered(_display->width() / 2, 48, "Turning OFF");
#endif
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
#ifdef DISPLAY_ACTIVITY_DASHBOARD
    renderDashboard();
    return;
#endif
    const bool dual_radio_enabled = the_mesh.isDualRadioActive();
    const uint8_t status_pages = radioProfileStatusPageCount(
        *_display, dual_radio_enabled);
    uint8_t status_page_index = 0;
    if (mesh::ui::showRadioProfileSystemStatusPage(dual_radio_enabled,
            status_pages, millis() - _radio_profile_page_started_at,
            &status_page_index)) {
      mesh::ui::drawRadioProfileSystemStatusPage(*_display,
          radioProfileSystemStatus(*_node_prefs), status_page_index);
      return;
    }
    // node name
    _display->setCursor(0, 0);
    _display->setTextSize(1);
    _display->setColor(UIColor::primary_txt);
    _display->print(_node_prefs->node_name);

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

    // Compact R1/R2 rows preserve every RF field on 128px displays.
    _display->setCursor(0, 20);
    if (dual_radio) {
      snprintf(tmp, sizeof(tmp), "%s F:%06.3f S:%d", profile_tag, freq, sf);
    } else {
      snprintf(tmp, sizeof(tmp), "FREQ: %06.3f SF%d", freq, sf);
    }
    _display->print(tmp);

    _display->setCursor(0, 30);
    if (dual_radio) {
      snprintf(tmp, sizeof(tmp), "%s B:%03.2f C:%d", profile_tag, bw, cr);
    } else {
      snprintf(tmp, sizeof(tmp), "BW: %03.2f CR: %d", bw, cr);
    }
    _display->print(tmp);

#ifdef WITH_MQTT_BRIDGE
    // Display IP address for MQTT bridge devices
    if (WiFi.status() == WL_CONNECTED) {
      IPAddress ip = WiFi.localIP();
      _display->setCursor(0, 40);
      _display->setColor(UIColor::primary_txt);
      snprintf(tmp, sizeof(tmp), "IP: %d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3]);
      _display->print(tmp);
    }
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

#ifdef DISPLAY_TOUCH_TOGGLE
  if (_powering_off_at > 0) {
    return DisplayFrameSignature::append(signature, "powering-off");
  }
#endif

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
  const mesh::RadioProfiles* profiles = configuredRadioProfiles();
  const bool dual_radio = profiles != NULL && profiles->enabled();
  const uint8_t status_pages = radioProfileStatusPageCount(*_display,
                                                            dual_radio);
  uint8_t status_page_index = 0;
  if (mesh::ui::showRadioProfileSystemStatusPage(dual_radio, status_pages,
          millis() - _radio_profile_page_started_at, &status_page_index)) {
    snprintf(tmp, sizeof(tmp), "radio-status:%u:%lu", status_page_index,
             (unsigned long)(millis() / 1000UL));
    return DisplayFrameSignature::append(signature, tmp);
  }
  const bool secondary_page = showingSecondaryRadioProfilePage();
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

  return signature;
}
#endif

#ifdef DISPLAY_ACTIVITY_DASHBOARD
#define ACTIVITY_REFRESH_MILLIS 5000

bool UITask::buildDashboardContext(ObserverDashboard::Context* ctx) {
  if (_node_prefs == NULL) return false;
  ctx->node_name = _node_prefs->node_name;
  ctx->role_label = "ROOM SERVER";
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
    resetRadioProfileDisplayPage();
    _next_refresh = 0;
#ifdef DISPLAY_REDRAW_ON_CHANGE
    _frame_valid = false;
#endif
#ifdef DISPLAY_ACTIVITY_DASHBOARD
    _rows_valid = false;
#endif
  }
#if defined(PIN_USER_BTN) && defined(DISPLAY_TOUCH_TOGGLE)
  int ev = user_btn.check();
  if (ev == BUTTON_EVENT_CLICK) {
    toggleDisplay("button");
  } else if (ev == BUTTON_EVENT_LONG_PRESS) {
    _display->wake(mesh::ui::DisplayWake::Button);
    mesh::usbConsolePort().printf("Powering Off\r\n");
    _powering_off_at = millis() + POWEROFF_DELAY;
#ifdef DISPLAY_REDRAW_ON_CHANGE
    _frame_valid = false;
#endif
#ifdef DISPLAY_ACTIVITY_DASHBOARD
    _rows_valid = false;
#endif
    _next_refresh = 0;
  }
#elif defined(PIN_USER_BTN) \
    && defined(DISPLAY_CLASS) \
    && defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) \
    && MOMENTARY_BUTTON_WAKE_FROM_SLEEP
  // Sleep-capable targets must use the shared debounced button state
  // machine. Raw 200 ms polling can go back to sleep after the GPIO edge and
  // miss both a short press and its release.
  int ev = user_btn.check();
  if (ev != BUTTON_EVENT_NONE) {
    if (!_display->isOn()) {
      _display->wake(mesh::ui::DisplayWake::Button);
      resetRadioProfileDisplayPage();
#ifdef DISPLAY_REDRAW_ON_CHANGE
      _frame_valid = false;
#endif
#ifdef DISPLAY_ACTIVITY_DASHBOARD
      _rows_valid = false;
#endif
      _next_refresh = 0;
    }
    _display->wake(mesh::ui::DisplayWake::Button);
  }
#elif defined(PIN_USER_BTN)
  if (millisReached(millis(), _next_read)) {
    int btnState = digitalRead(PIN_USER_BTN);
    if (btnState != _prevBtnState) {
      if (btnState == USER_BTN_PRESSED) {  // pressed?
#ifdef DISPLAY_TOUCH_TOGGLE
        toggleDisplay("button");   // same action as tapping the panel
#else
        if (_display->isOn()) {
          // TODO: any action ?
        } else {
          _display->wake(mesh::ui::DisplayWake::Button);
          resetRadioProfileDisplayPage();
#ifdef DISPLAY_REDRAW_ON_CHANGE
          _frame_valid = false;
#endif
#ifdef DISPLAY_ACTIVITY_DASHBOARD
          _rows_valid = false;
#endif
        }
        _display->wake(mesh::ui::DisplayWake::Button);
#endif
      }
      _prevBtnState = btnState;
    }
    _next_read = millis() + 200;  // 5 reads per second
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

      _next_refresh = millis() + 1000;   // check for visible changes every second
    }
  }

#ifdef DISPLAY_TOUCH_TOGGLE
  if (_powering_off_at > 0 && millisReached(millis(), _powering_off_at)) {
    _board->powerOff();   // current board path performs the full radio/GPS shutdown
  }
#endif
}
