#include "UITask.h"
#include "target.h"
#include <Arduino.h>
#include <helpers/CommonCLI.h>
#include <helpers/ui/RadioProfileDisplayPage.h>
#include <helpers/ui/RadioProfileSystemStatus.h>
#include <RadioProfiles.h>
#include "SensorMesh.h"

namespace {

const mesh::RadioProfiles* configuredRadioProfiles() {
  const auto* radio = activeSensorMesh().getProfileRadio();
  return radio ? radio->profiles() : NULL;
}

mesh::ui::RadioProfileSystemStatus radioProfileSystemStatus(
    const NodePrefs& prefs) {
  mesh::ui::RadioProfileSystemStatus status;
  const auto* profiles = configuredRadioProfiles();
  status.public_key = activeSensorMesh().getSelfId().pub_key;
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

#ifndef USER_BTN_PRESSED
#define USER_BTN_PRESSED LOW
#endif

#define BOOT_SCREEN_MILLIS   4000   // 4 seconds

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
  _node_prefs = node_prefs;
  resetRadioProfileDisplayPage();
  _display->servicePower(board.isExternalPowered() || board.isUsbHostConnected());
#if defined(PIN_USER_BTN) && defined(DISPLAY_CLASS) \
    && defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) \
    && MOMENTARY_BUTTON_WAKE_FROM_SLEEP
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
  _radio_profile_page_started_at = millis();
  _dual_radio_enabled_seen = activeSensorMesh().isDualRadioActive();
}

bool UITask::showingSecondaryRadioProfilePage() {
  const bool dual_radio_enabled = activeSensorMesh().isDualRadioActive();
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
  if (millis() < BOOT_SCREEN_MILLIS) { // boot screen
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
    const char* node_type = "< Sensor >";
    uint16_t typeWidth = _display->getTextWidth(node_type);
    _display->setCursor((_display->width() - typeWidth) / 2, 48);
    _display->print(node_type);
  } else {  // home screen
    const bool dual_radio_enabled = activeSensorMesh().isDualRadioActive();
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
  }
}

void UITask::loop() {
  if (_display->servicePower(board.isExternalPowered() || board.isUsbHostConnected())) {
    resetRadioProfileDisplayPage();
    _next_refresh = 0;
  }
#if defined(PIN_USER_BTN) && defined(DISPLAY_CLASS) \
    && defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) \
    && MOMENTARY_BUTTON_WAKE_FROM_SLEEP
  int ev = user_btn.check();
  if (ev != BUTTON_EVENT_NONE) {
    if (!_display->isOn()) {
      _display->wake(mesh::ui::DisplayWake::Button);
      resetRadioProfileDisplayPage();
      _next_refresh = 0;
    }
    _display->wake(mesh::ui::DisplayWake::Button);
  }
#elif defined(PIN_USER_BTN)
  if (millis() >= _next_read) {
    int btnState = digitalRead(PIN_USER_BTN);
    if (btnState != _prevBtnState) {
      if (btnState == USER_BTN_PRESSED) {  // pressed?
        if (_display->isOn()) {
          // TODO: any action ?
        } else {
          _display->wake(mesh::ui::DisplayWake::Button);
          resetRadioProfileDisplayPage();
        }
        _display->wake(mesh::ui::DisplayWake::Button);   // extend auto-off timer
      }
      _prevBtnState = btnState;
    }
    _next_read = millis() + 200;  // 5 reads per second
  }
#endif

  if (_display->isOn()) {
    if (millis() >= _next_refresh) {
      _display->startFrame();
      renderCurrScreen();
      _display->endFrame();

      _next_refresh = millis() + 1000;   // refresh every second
    }
#if MOMENTARY_BUTTON_WAKE_HOLD_MS > 0 && defined(PIN_USER_BTN)
    if (user_btn.isWakeHoldActive()) _display->wake(mesh::ui::DisplayWake::Button);
#endif

  }
}
