#include "UITask.h"
#include <helpers/MorseLoopPolicy.h>
#if UI_SMALL_MESSAGE_FONT == 1
  #include <helpers/ui/SmallMessageText.h>
#endif
#include <Arduino.h>
#include <helpers/TxtDataHelpers.h>
#if defined(NRF52_POWER_MANAGEMENT)
#include <helpers/NRF52VoltagePolicy.h>
#endif
#include <helpers/ui/BluetoothPairingUiPolicy.h>
#include <helpers/ui/RadioProfileDisplayPage.h>
#include <helpers/ui/RadioProfileSystemStatus.h>
#include <RadioProfiles.h>
#include "../MyMesh.h"

#define AUTO_OFF_MILLIS     15000   // 15 seconds
#define BOOT_SCREEN_MILLIS   3000   // 3 seconds
#ifndef BLE_PAIRING_DISPLAY_MILLIS
#define BLE_PAIRING_DISPLAY_MILLIS 120000UL
#endif

#if defined(PIN_STATUS_LED) || defined(PIN_STATUS_LED_R)
#define LED_ON_MILLIS     20
#define LED_ON_MSG_MILLIS 200
#define LED_CYCLE_MILLIS  4000
#endif

#if defined(PIN_STATUS_LED_R) && defined(PIN_STATUS_LED_G) && defined(PIN_STATUS_LED_B)
#define STATUS_LED_RGB 1
#ifndef LOW_BATT_MILLIVOLTS
#define LOW_BATT_MILLIVOLTS 3500
#endif
#endif

#ifndef USER_BTN_PRESSED
#define USER_BTN_PRESSED LOW
#endif

namespace {

mesh::ui::RadioProfileSystemStatus radioProfileSystemStatus(
    const CompanionNodePrefs& prefs, const mesh::MainBoard& board) {
  mesh::ui::RadioProfileSystemStatus status;
  const auto* radio = the_mesh.getProfileRadio();
  const mesh::RadioProfiles* profiles = radio ? radio->profiles() : NULL;
  status.public_key = the_mesh.self_id.pub_key;
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

void UITask::begin(DisplayDriver* display, SensorManager* sensors, CompanionNodePrefs* node_prefs) {
  _display = display;
  _sensors = sensors;
  if (_display != NULL) {
    _display->setRotationDegrees(node_prefs->display_rotation_degrees);
  }

  clearMsgPreview();
  _node_prefs = node_prefs;
  resetRadioProfileDisplayPage();
  if (_display != NULL) {
    _display->servicePower(_board->isExternalPowered() || _board->isUsbHostConnected(), hasConnection());
  }

  // strip off dash and commit hash without allocating a mutable version copy
  // e.g: v1.2.3-abcdef -> v1.2.3
  // v1.2.3 (1 Jan 2025)
  const size_t version_len = strcspn(FIRMWARE_VERSION, "-");
  snprintf(_version_info, sizeof(_version_info), "%.*s (%s)",
           static_cast<int>(version_len), FIRMWARE_VERSION, FIRMWARE_BUILD_DATE);

#ifdef PIN_BUZZER
  buzzer.begin();
  buzzer.quiet(_node_prefs->buzzer_quiet);
  buzzer.startup();
#endif

#ifdef HAS_DRV2605
  vibration.begin();
  vibration.quiet(_node_prefs->vibe_quiet);
#elif defined(PIN_VIBRATION)
  vibration.begin();
#endif

  // Initialize digital button if available
#ifdef PIN_USER_BTN
  _userButton = new Button(PIN_USER_BTN, USER_BTN_PRESSED);
  _userButton->begin();
  
  // Set up digital button callbacks
  _userButton->onShortPress([this]() { handleButtonShortPress(); });
  _userButton->onDoublePress([this]() { handleButtonDoublePress(); });
  _userButton->onTriplePress([this]() { handleButtonTriplePress(); });
  _userButton->onQuadruplePress([this]() { handleButtonQuadruplePress(); });
  _userButton->onLongPress([this]() { handleButtonLongPress(); });
  _userButton->onAnyPress([this]() { handleButtonAnyPress(); });
#endif

  // Initialize analog button if available
#ifdef PIN_USER_BTN_ANA
  _userButtonAnalog = new Button(PIN_USER_BTN_ANA, USER_BTN_PRESSED, true, 20);
  _userButtonAnalog->begin();
  
  // Set up analog button callbacks
  _userButtonAnalog->onShortPress([this]() { handleButtonShortPress(); });
  _userButtonAnalog->onDoublePress([this]() { handleButtonDoublePress(); });
  _userButtonAnalog->onTriplePress([this]() { handleButtonTriplePress(); });
  _userButtonAnalog->onQuadruplePress([this]() { handleButtonQuadruplePress(); });
  _userButtonAnalog->onLongPress([this]() { handleButtonLongPress(); });
  _userButtonAnalog->onAnyPress([this]() { handleButtonAnyPress(); });
#endif
  ui_started_at = millis();
  _next_refresh = ui_started_at;
}

void UITask::resetRadioProfileDisplayPage() {
  _radio_profile_page_started_at = millis();
  _dual_radio_enabled_seen = the_mesh.isDualRadioActive();
  _radio_profile_display_page = 0;
}

bool UITask::showingSecondaryRadioProfilePage() {
  const bool dual_radio_enabled = the_mesh.isDualRadioActive();
  if (dual_radio_enabled) {
    return mesh::ui::showManualSecondaryRadioProfilePage(
        true, _radio_profile_display_page);
  }
  const uint8_t status_pages = radioProfileStatusPageCount(*_display,
                                                            dual_radio_enabled);
  return mesh::ui::showSecondaryRadioProfilePage(dual_radio_enabled,
      status_pages,
      millis() - _radio_profile_page_started_at);
}

void UITask::serviceRadioProfileDisplayPage() {
  const bool dual_radio_enabled = the_mesh.isDualRadioActive();
  if (dual_radio_enabled != _dual_radio_enabled_seen) {
    resetRadioProfileDisplayPage();
    _need_refresh = true;
  }
  if (dual_radio_enabled) return;  // Button presses own all dual-radio pages.
  const uint8_t status_pages = radioProfileStatusPageCount(*_display,
                                                            dual_radio_enabled);
  const uint8_t page = mesh::ui::radioProfileDisplayPageIndex(
      dual_radio_enabled, status_pages,
      millis() - _radio_profile_page_started_at);
  if (page != _radio_profile_display_page) {
    _radio_profile_display_page = page;
    _need_refresh = true;
  }
}

bool UITask::shouldPlayMessageTone() const {
#ifdef BLE_PIN_CODE
  return !hasBluetoothConnection();
#else
  // A USB or Ethernet companion connection is not a Bluetooth connection and
  // should not silence the standalone new-message alert.
  return true;
#endif
}

void UITask::notify(UIEventType t) {
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  if (_shutdown_pending) return;
#endif
#if defined(PIN_BUZZER)
if (_notification_outputs & mesh::notify::Sound) switch(t){
  case UIEventType::contactMessage:
    // gemini's pick
    if (shouldPlayMessageTone()) {
      buzzer.play("MsgRcv3:d=4,o=6,b=200:32e,32g,32b,16c7");
    }
    break;
  case UIEventType::channelMessage:
    if (shouldPlayMessageTone()) {
      buzzer.play("kerplop:d=16,o=6,b=120:32g#,32c#");
    }
    break;
  case UIEventType::ack:
    buzzer.play("ack:d=32,o=8,b=120:c");
    break;
  case UIEventType::roomMessage:
  case UIEventType::newContactMessage:
  case UIEventType::none:
  default:
    break;
}
#endif
//  Serial.print("DBG:  Alert user -> ");
//  Serial.println((int) t);
}

void UITask::msgRead(int msgcount) {
  _msgcount = msgcount;
  if (msgcount == 0) {
    clearMsgPreview();
  }
}

void UITask::clearMsgPreview() {
  _origin[0] = 0;
  _msg[0] = 0;
  _need_refresh = true;
}

void UITask::newMsg(uint8_t path_len, const char* from_name, const char* text,
                    int msgcount, int channel_idx,
                    const char* channel_name, int queue_index) {
  (void)queue_index;
  (void)channel_idx;
  (void)channel_name;
  _msgcount = msgcount;

#if defined(HAS_DRV2605) || defined(PIN_VIBRATION)
  if (!_message_notification_override && (_notification_outputs & mesh::notify::Vibration)) vibration.trigger();
#endif

  if (path_len == 0xFF) {
    sprintf(_origin, "(F) %s", from_name);
  } else {
    sprintf(_origin, "(%d) %s", (uint32_t) path_len, from_name);
  }
  StrHelper::strncpy(_msg, text, sizeof(_msg));

  if (_display != NULL) {
    if (shouldWakeDisplayForMessage()) {
      _display->wake(mesh::ui::DisplayWake::Message);
    }
    if (_display->isOn()) _need_refresh = true;
  }
}

void UITask::renderBatteryIndicator(uint16_t batteryMilliVolts) {
  // Convert millivolts to percentage
#ifndef BATT_MIN_MILLIVOLTS
  #define BATT_MIN_MILLIVOLTS 3000
#endif
#ifndef BATT_MAX_MILLIVOLTS
  #define BATT_MAX_MILLIVOLTS 4200
#endif
  const int minMilliVolts = BATT_MIN_MILLIVOLTS;
  const int maxMilliVolts = BATT_MAX_MILLIVOLTS;
  int batteryPercentage = ((batteryMilliVolts - minMilliVolts) * 100) / (maxMilliVolts - minMilliVolts);
#if defined(NRF52_POWER_MANAGEMENT)
  batteryPercentage = mesh::power::configuredBatteryPercent(batteryMilliVolts);
#endif
  if (batteryPercentage < 0) batteryPercentage = 0; // Clamp to 0%
  if (batteryPercentage > 100) batteryPercentage = 100; // Clamp to 100%

  // battery icon
  int iconWidth = 24;
  int iconHeight = 12;
  int iconX = _display->width() - iconWidth - 5; // Position the icon near the top-right corner
  int iconY = 0;
  _display->setColor(UIColor::primary_txt);

  // battery outline
  _display->drawRect(iconX, iconY, iconWidth, iconHeight);

  // battery "cap"
  _display->fillRect(iconX + iconWidth, iconY + (iconHeight / 4), 3, iconHeight / 2);

  // fill the battery based on the percentage
  int fillWidth = (batteryPercentage * (iconWidth - 4)) / 100;
  _display->fillRect(iconX + 2, iconY + 2, fillWidth, iconHeight - 4);
}

void UITask::renderCurrScreen() {
  if (_display == NULL) return;  // assert() ??

  char tmp[80];
  const bool pairing_screen_active = isPairingScreenActive();
  if (!pairing_screen_active && _alert[0]) {
    _display->setTextSize(1.4);
    uint16_t textWidth = _display->getTextWidth(_alert);
    _display->setCursor((_display->width() - textWidth) / 2, 22);
    _display->setColor(UIColor::warning_txt);
    _display->print(_alert);
    _alert[0] = 0;
    _need_refresh = true;
    return;
  } else if (!pairing_screen_active && _origin[0] && _msg[0]) { // message preview
    // render message preview
    _display->setCursor(0, 0);
    _display->setTextSize(1);
    _display->setColor(UIColor::primary_txt);
    _display->print(_node_prefs->node_name);

#if UI_SMALL_MESSAGE_FONT == 1
    mesh::ui::drawSmallMessageBody(*_display, _origin, _msg, 12);
#else
    _display->setCursor(0, 12);
    _display->setColor(UIColor::secondary_txt);
    _display->print(_origin);
    _display->setCursor(0, 24);
    _display->print(_msg);

    _display->setCursor(_display->width() - 28, 9);
    _display->setTextSize(2);
    _display->setColor(UIColor::primary_txt);
    sprintf(tmp, "%d", _msgcount);
    _display->print(tmp);
#endif
    _display->setColor(UIColor::secondary_txt); // last color will be kept on T114
  } else if (!pairing_screen_active && (millis() - ui_started_at) < BOOT_SCREEN_MILLIS) { // boot screen
    // meshcore logo
    _display->setColor(UIColor::corp_blue);
    int logoWidth = 128;
    _display->drawXbm((_display->width() - logoWidth) / 2, 3, meshcore_logo, logoWidth, 13);

    // version info
    _display->setColor(UIColor::primary_txt);
    _display->setTextSize(1);
    uint16_t textWidth = _display->getTextWidth(_version_info);
    _display->setCursor((_display->width() - textWidth) / 2, 22);
    _display->print(_version_info);
  } else {  // home screen
    const bool dual_radio_enabled = the_mesh.isDualRadioActive();
    const uint8_t status_pages = radioProfileStatusPageCount(
        *_display, dual_radio_enabled);
    uint8_t status_page_index = 0;
    const bool status_page = dual_radio_enabled
        ? mesh::ui::showManualRadioProfileSystemStatusPage(
              true, status_pages, _radio_profile_display_page,
              &status_page_index)
        : mesh::ui::showRadioProfileSystemStatusPage(
              false, status_pages, millis() - _radio_profile_page_started_at,
              &status_page_index);
    if (status_page) {
      mesh::ui::drawRadioProfileSystemStatusPage(*_display,
          radioProfileSystemStatus(*_node_prefs, *_board), status_page_index);
      _need_refresh = false;
      return;
    }
    // node name
    _display->setCursor(0, 0);
    _display->setTextSize(1);
    _display->setColor(UIColor::primary_txt);
    _display->print(_node_prefs->node_name);

    // battery voltage
    renderBatteryIndicator(_board->getBattMilliVolts());

    const auto* radio = the_mesh.getProfileRadio();
    const mesh::RadioProfiles* profiles = radio ? radio->profiles() : NULL;
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
    _display->setColor(UIColor::secondary_txt);
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

    // BT pin
    const bool bluetooth_connected = hasBluetoothConnection();
    const uint32_t bluetooth_pin = the_mesh.getBLEPin();
    if (mesh::ui::shouldDisplayBluetoothPairingPin(
            isBluetoothEnabled(), bluetooth_connected, bluetooth_pin)) {
      _display->setColor(UIColor::warning_txt);
      _display->setTextSize(2);
      _display->setCursor(0, 43);
      sprintf(tmp, "Pin:%u", (unsigned int)bluetooth_pin);
      _display->print(tmp);
      _display->setColor(UIColor::primary_txt);
    } else {
      _display->setColor(UIColor::primary_txt); 
    }
  }
  _need_refresh = false;
}

#ifdef STATUS_LED_RGB
static void statusLedWrite(uint8_t r, uint8_t g, uint8_t b) {
  analogWrite(PIN_STATUS_LED_R, r);
  analogWrite(PIN_STATUS_LED_G, g);
  analogWrite(PIN_STATUS_LED_B, b);
}

static void statusLedWheel(uint8_t pos) {   // smooth hue sweep, 0..255
  if (pos < 85) {
    statusLedWrite(255 - pos * 3, pos * 3, 0);
  } else if (pos < 170) {
    pos -= 85;
    statusLedWrite(0, 255 - pos * 3, pos * 3);
  } else {
    pos -= 170;
    statusLedWrite(pos * 3, 0, 255 - pos * 3);
  }
}
#endif

void UITask::userLedHandler() {
  if (_notification_led_owned || !(_notification_outputs & mesh::notify::Led)) return;
#ifdef STATUS_LED_RGB
  enum class FlourishState : uint8_t { Unseen, Boot, Message, Idle };
  static FlourishState flourish = FlourishState::Unseen;
  static uint32_t flourish_started = 0;
  static int prev_msgcount = 0;
  static int state = 0;
  static uint32_t last_change = 0;
  static uint32_t phase_millis = 0;
  static uint32_t last_batt_check = 0;
  enum class BatteryState : uint8_t { Unseen, Normal, Low };
  static BatteryState battery_state = BatteryState::Unseen;

  const uint32_t cur_time = millis();

  if (flourish == FlourishState::Unseen) {   // color sweep on boot
    flourish_started = cur_time;
    flourish = FlourishState::Boot;
  }
  if (_msgcount > prev_msgcount) {   // color sweep when a new message arrives
    flourish_started = cur_time;
    flourish = FlourishState::Message;
  }
  prev_msgcount = _msgcount;

  if (flourish == FlourishState::Boot || flourish == FlourishState::Message) {
    const uint32_t duration = flourish == FlourishState::Boot ? 1200 : 800;
    if (static_cast<uint32_t>(cur_time - flourish_started) < duration) {
      statusLedWheel((cur_time % 1200) * 255 / 1200);
      return;
    }
    statusLedWrite(0, 0, 0);
    flourish = FlourishState::Idle;
    state = 0;
    last_change = cur_time;
    phase_millis = 0;
  }

  // Keep the cached entry-time cadence, including across wrap and long idle.
  // Sample state distinguishes an initial check from a completed check at zero.
  if (battery_state == BatteryState::Unseen ? cur_time > 0
      : static_cast<uint32_t>(cur_time - last_batt_check) > 60000) {
    uint16_t low_batt_mv = LOW_BATT_MILLIVOLTS;
#if defined(NRF52_POWER_MANAGEMENT)
    low_batt_mv = mesh::power::remapBatteryWarningMillivolts(
        LOW_BATT_MILLIVOLTS, BATT_MIN_MILLIVOLTS, BATT_MAX_MILLIVOLTS,
        mesh::power::configuredEmpty(), mesh::power::configuredFull());
#endif
    battery_state = _board->getBattMilliVolts() < low_batt_mv
        ? BatteryState::Low : BatteryState::Normal;
    last_batt_check = cur_time;
  }

#ifdef EXT_CHRG_DETECT
  // charge status display while docked (skipped when messages are waiting,
  // so the unread indication is never masked)
  static uint32_t last_pwr_check = 0;
  enum class PowerState : uint8_t { Unseen, No, Yes };
  static PowerState power_state = PowerState::Unseen;
  static bool ext_charging = false;
  if (power_state == PowerState::Unseen ? cur_time > 0
      : static_cast<uint32_t>(cur_time - last_pwr_check) > 1000) {
    power_state = _board->isExternalPowered() ? PowerState::Yes : PowerState::No;
    ext_charging = power_state == PowerState::Yes && digitalRead(EXT_CHRG_DETECT) == LOW;
    last_pwr_check = cur_time;
  }
  if (power_state == PowerState::Yes && _msgcount == 0) {
    if (ext_charging) {
      // amber breathing while charging
      int ph = cur_time % 2000;
      int v = ph < 1000 ? ph : 2000 - ph;
      uint8_t lvl = (uint8_t)(v * 255 / 1000);
      statusLedWrite(lvl, (uint8_t)(lvl * 35 / 100), 0);
    } else {
      statusLedWrite(0, 40, 0);   // dim solid green: charge complete
    }
    state = 0;
    last_change = cur_time;
    phase_millis = 0;
    return;
  }
#endif

  if (static_cast<uint32_t>(cur_time - last_change) > phase_millis) {
    if (state == 0) {
      state = 1;
      phase_millis = (_msgcount > 0) ? LED_ON_MSG_MILLIS : LED_ON_MILLIS;
      if (battery_state == BatteryState::Low) {
        statusLedWrite(255, 0, 0);      // red: battery low
      } else if (_msgcount > 0) {
        statusLedWrite(255, 90, 0);     // amber: unread messages
      } else if (_connected) {
        statusLedWrite(0, 0, 255);      // blue: app connected
      } else {
        statusLedWrite(0, 255, 0);      // green: heartbeat
      }
    } else {
      state = 0;
      phase_millis = LED_CYCLE_MILLIS - phase_millis;
      statusLedWrite(0, 0, 0);
    }
    last_change = cur_time;
  }
#elif defined(PIN_STATUS_LED)
  static int state = 0;
  static uint32_t last_change = 0;
  static uint32_t phase_millis = 0;

  const uint32_t cur_time = millis();
  // Phase age also survives long notification-owned or disabled intervals.
  if (static_cast<uint32_t>(cur_time - last_change) > phase_millis) {
    if (state == 0) {
      state = 1;
      if (_msgcount > 0) {
        phase_millis = LED_ON_MSG_MILLIS;
      } else {
        phase_millis = LED_ON_MILLIS;
      }
    } else {
      state = 0;
      phase_millis = LED_CYCLE_MILLIS - phase_millis;
    }
    last_change = cur_time;
    digitalWrite(PIN_STATUS_LED, state == LED_STATE_ON);
  }
#endif
}

/* 
  hardware-agnostic pre-shutdown activity should be done here 
*/
void UITask::shutdown(bool restart){
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  if (_shutdown_pending) return;
  if (!prepareForShutdown()) {
    buzzer.speakButton(ButtonVoicePrompt::ActionFailed);
    return;
  }

  // Keep the main loop running while the tone and spoken confirmation drain,
  // so USB, Bluetooth and radio service are not held inside this callback.
  _shutdown_restart = restart;
  _shutdown_started_at = millis();
  _shutdown_pending = true;
  if (!buzzer.speakButton(restart ? ButtonVoicePrompt::Restarting
                                  : ButtonVoicePrompt::ShuttingDown)) {
    buzzer.shutdown();
  }
#else
  if (!prepareForShutdown()) return;

  #ifdef PIN_BUZZER
  /* note: we have a choice here -
     we can do a blocking buzzer.loop() with non-deterministic consequences
     or we can set a flag and delay the shutdown for a couple of seconds
     while a non-blocking buzzer.loop() plays out in UITask::loop()
  */
  buzzer.shutdown();
  uint32_t buzzer_timer = millis(); // fail-safe shutdown
  while (buzzer.isPlaying() && (millis() - 2500) < buzzer_timer)
    buzzer.loop();

  #endif // PIN_BUZZER

  if (restart) {
    _board->reboot();
  } else {
    // Power off board including radio, display, GPS and components
    _board->powerOff();
  }
#endif
}

#if defined(PIN_BUZZER) && MESH_GPS_VOICE
void UITask::servicePendingShutdown() {
  if (!_shutdown_pending) return;
  if (buzzer.isPlaying()
      && static_cast<uint32_t>(millis() - _shutdown_started_at) < 5000UL) {
    return;
  }

  buzzer.stop();
  _shutdown_pending = false;
  // New radio/host work can arrive during the announcement. Flush it too
  // before entering the board's irreversible shutdown state.
  if (!prepareForShutdown()) {
    buzzer.speakButton(ButtonVoicePrompt::ActionFailed);
    return;
  }
  if (_shutdown_restart) {
    _board->reboot();
  } else {
    _board->powerOff();
  }
}
#endif

bool UITask::isPairingScreenActive() const {
  return mesh::ui::isBluetoothPairingPromptActive(
      isBluetoothEnabled(), hasBluetoothConnection(),
      static_cast<uint32_t>(_pairing_screen_until),
      static_cast<uint32_t>(millis()));
}

void UITask::showPairingPin() {
  if (!isBluetoothEnabled()) return;

  const unsigned long now = millis();
  _pairing_screen_until = now + BLE_PAIRING_DISPLAY_MILLIS;
  _need_refresh = true;
  _next_refresh = millis();
  if (_display != NULL && !_display->isOn()) {
    _display->servicePower(_board->isExternalPowered() || _board->isUsbHostConnected(), hasConnection(), true);
  }
}

void UITask::finishPairingScreen(bool timed_out) {
  _pairing_screen_until = 0;
  _need_refresh = true;
  _next_refresh = millis();
  if (_display == NULL) return;

  if (timed_out) {
    _display->dismiss();
  }
}

void UITask::servicePairingState() {
  if (_interfaceManager->takePairingRequest()) {
    showPairingPin();
  }

  if (_pairing_screen_until != 0) {
    const bool timed_out =
        static_cast<int32_t>(millis() - _pairing_screen_until) >= 0;
    if (!isPairingScreenActive()) {
      finishPairingScreen(timed_out);
    }
  }
  if (_display != NULL && _display->servicePower(
      _board->isExternalPowered() || _board->isUsbHostConnected(),
      hasConnection(), isPairingScreenActive())) {
    _next_refresh = millis();
    _need_refresh = true;
  }
}

void UITask::loop() {
  const bool morse_timing = mesh::morseTimingActive(&radio_driver);
#ifdef PIN_VIBRATION
  vibration.loop();
#endif
  servicePairingState();

  // Keep an already-due refresh due while clean or asleep, without moving
  // a future throttle. It must not age past the signed clock's half range.
  const uint32_t refresh_now = millis();
  if (static_cast<int32_t>(refresh_now - _next_refresh) >= 0) {
    _next_refresh = refresh_now;
  }

  #ifdef PIN_USER_BTN
    if (_userButton) {
      _userButton->update();
    }
  #endif
  #ifdef PIN_USER_BTN_ANA
    if (_userButtonAnalog) {
      _userButtonAnalog->update();
    }
  #endif
  userLedHandler();

#ifdef PIN_BUZZER
  if (buzzer.isPlaying())  buzzer.loop();
#endif
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  servicePendingShutdown();
#endif

  if (!morse_timing && _display != NULL && _display->isOn()) {
    if (!isPairingScreenActive() && !_alert[0] && !(_origin[0] && _msg[0])
        && (millis() - ui_started_at) >= BOOT_SCREEN_MILLIS) {
      serviceRadioProfileDisplayPage();
    }
    static bool _firstBoot = true;
    if(_firstBoot && (millis() - ui_started_at) >= BOOT_SCREEN_MILLIS) {
      _need_refresh = true;
      _firstBoot = false;
    }
    if (static_cast<int32_t>(millis() - _next_refresh) >= 0 && _need_refresh) {
      _display->startFrame();
      renderCurrScreen();
      _display->endFrame();

      _next_refresh = millis() + 1000;   // refresh every second
    }
  }
}

void UITask::handleButtonAnyPress() {
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  if (_shutdown_pending) return;
  _button_notification_cleared = the_mesh.notificationButton()
      || _button_notification_cleared;
#else
  the_mesh.notificationButton();
#endif
  MESH_DEBUG_PRINTLN("UITask: any press triggered");
  // called on any button press before other events, to wake up the display quickly
  // do not refresh the display here, as it may block the button handler
  if (_display != NULL) {
    _displayWasOn = _display->isOn();  // Track display state before any action
    _display->wake(mesh::ui::DisplayWake::Button);
    if (!_displayWasOn) resetRadioProfileDisplayPage();
  }
}

void UITask::handleButtonShortPress() {
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  if (_shutdown_pending) return;
  const bool notification_cleared = _button_notification_cleared;
  _button_notification_cleared = false;
  if (_display == NULL) {
    buzzer.speakButton(notification_cleared
        ? ButtonVoicePrompt::NotificationCleared : ButtonVoicePrompt::Ready);
  }
#endif
  MESH_DEBUG_PRINTLN("UITask: short press triggered");
  if (_display != NULL) {
    // Only clear message preview if display was already on before button press
    if (_displayWasOn) {
      // If display was on and showing message preview, clear it
      if (_origin[0] && _msg[0]) {
        clearMsgPreview();
      } else {
        if (!_alert[0] && !isPairingScreenActive()
            && the_mesh.isDualRadioActive()) {
          const uint8_t status_pages = radioProfileStatusPageCount(
              *_display, true);
          const uint8_t page_count = mesh::ui::radioProfileDisplayPageCount(
              true, status_pages);
          _radio_profile_display_page =
              (_radio_profile_display_page + 1) % page_count;
        }
        _need_refresh = true;
      }
    } else {
      _need_refresh = true; // display just turned on, so we need to refresh
    }
    // Note: Display turn-on and auto-off timer extension are handled by handleButtonAnyPress
  }
}

void UITask::handleButtonDoublePress() {
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  if (_shutdown_pending) return;
  _button_notification_cleared = false;
#endif
  MESH_DEBUG_PRINTLN("UITask: double press triggered, sending advert");
  // ADVERT
#if !(defined(PIN_BUZZER) && MESH_GPS_VOICE)
#ifdef PIN_BUZZER
  notify(UIEventType::ack);
#endif
#endif
  const bool queued = the_mesh.advert();
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  if (!buzzer.speakButton(queued ? ButtonVoicePrompt::AdvertQueued
                               : ButtonVoicePrompt::AdvertFailed)) {
    notify(UIEventType::ack);
  }
#endif
  if (queued) {
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
    MESH_DEBUG_PRINTLN("Advert queued!");
    sprintf(_alert, "Advert queued!");
#else
    MESH_DEBUG_PRINTLN("Advert sent!");
    sprintf(_alert, "Advert sent!");
#endif
  } else {
    MESH_DEBUG_PRINTLN("Advert failed!");
    sprintf(_alert, "Advert failed..");
  }
  _need_refresh = true;
}

void UITask::handleButtonTriplePress() {
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  if (_shutdown_pending) return;
  _button_notification_cleared = false;
#endif
  MESH_DEBUG_PRINTLN("UITask: triple press triggered");
#if defined(PIN_BUZZER) && defined(HAS_DRV2605)
  // cycle alert modes: Buzz+Vibe -> Buzz only -> Vibe only -> Silent
#if MESH_GPS_VOICE
  const uint8_t previous_sound = _node_prefs->buzzer_quiet;
  const uint8_t previous_vibration = _node_prefs->vibe_quiet;
#endif
  int mode = (_node_prefs->buzzer_quiet ? 2 : 0) | (_node_prefs->vibe_quiet ? 1 : 0);
  mode = (mode + 1) & 3;
  _node_prefs->buzzer_quiet = (mode & 2) ? 1 : 0;
  _node_prefs->vibe_quiet = (mode & 1) ? 1 : 0;
#if MESH_GPS_VOICE
  // Announce only committed settings. A failed write must not change either
  // live alert output, including the notification engine's mute masks.
  if (!the_mesh.savePrefs()) {
    _node_prefs->buzzer_quiet = previous_sound;
    _node_prefs->vibe_quiet = previous_vibration;
    sprintf(_alert, "Alerts: Save failed");
    buzzer.speakButton(ButtonVoicePrompt::ActionFailed, true);
    _need_refresh = true;
    return;
  }
#endif
  buzzer.quiet(_node_prefs->buzzer_quiet);
  vibration.quiet(_node_prefs->vibe_quiet);
#if MESH_GPS_VOICE
  if (_node_prefs->vibe_quiet) vibration.stop();
#endif
  the_mesh.setNotificationOutputMute(mesh::notify::Sound, _node_prefs->buzzer_quiet);
  the_mesh.setNotificationOutputMute(mesh::notify::Vibration, _node_prefs->vibe_quiet);
  // audible/tactile confirmation of the new mode (no screen on some boards)
#if !MESH_GPS_VOICE
  if (!_node_prefs->buzzer_quiet) notify(UIEventType::ack);
#endif
  if (!_node_prefs->vibe_quiet) vibration.trigger(true);
  switch (mode) {
    case 0: sprintf(_alert, "Alerts: Buzz+Vibe"); break;
    case 1: sprintf(_alert, "Alerts: Buzz only"); break;
    case 2: sprintf(_alert, "Alerts: Vibe only"); break;
    default: sprintf(_alert, "Alerts: Silent"); break;
  }
#if MESH_GPS_VOICE
  static const ButtonVoicePrompt mode_prompts[] = {
    ButtonVoicePrompt::AlertsSoundVibration,
    ButtonVoicePrompt::AlertsSoundOnly,
    ButtonVoicePrompt::AlertsVibrationOnly,
    ButtonVoicePrompt::AlertsSilent,
  };
  // One courtesy announcement is allowed for vibration-only/silent modes,
  // without unmuting ordinary notifications or changing the saved mode.
  buzzer.speakButton(mode_prompts[mode], true);
#else
  the_mesh.savePrefs();
#endif
  _need_refresh = true;
#elif defined(PIN_BUZZER)
#if MESH_GPS_VOICE
    const bool previous_quiet = buzzer.isQuiet();
    const uint8_t previous_preference = _node_prefs->buzzer_quiet;
    const bool quiet = !previous_quiet;
    _node_prefs->buzzer_quiet = quiet;
    if (the_mesh.savePrefs()) {
      buzzer.quiet(quiet);
      the_mesh.setNotificationOutputMute(mesh::notify::Sound, quiet);
      sprintf(_alert, quiet ? "Buzzer: OFF" : "Buzzer: ON");
      // A user-requested mute gets one final confirmation without changing
      // the stored quiet state or allowing ordinary notifications through.
      buzzer.speakButton(quiet ? ButtonVoicePrompt::SoundOff
                               : ButtonVoicePrompt::SoundOn, true);
    } else {
      _node_prefs->buzzer_quiet = previous_preference;
      buzzer.quiet(previous_quiet);
      sprintf(_alert, "Buzzer: Save failed");
      buzzer.speakButton(ButtonVoicePrompt::ActionFailed, true);
    }
    _need_refresh = true;
#else
    if (buzzer.isQuiet()) {
      buzzer.quiet(false);
      notify(UIEventType::ack);
      sprintf(_alert, "Buzzer: ON");
    } else {
      buzzer.quiet(true);
      sprintf(_alert, "Buzzer: OFF");
    }
    _node_prefs->buzzer_quiet = buzzer.isQuiet();
    the_mesh.setNotificationOutputMute(mesh::notify::Sound, _node_prefs->buzzer_quiet);
    the_mesh.savePrefs();
    _need_refresh = true;
#endif
#endif
}

void UITask::handleButtonQuadruplePress() {
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  if (_shutdown_pending) return;
  _button_notification_cleared = false;
  bool gps_changed = false;
  sprintf(_alert, "GPS: Unavailable");
#endif
  MESH_DEBUG_PRINTLN("UITask: quad press triggered");
#if ENV_INCLUDE_GPS == 1
  if (_sensors != NULL) {
    const char* gps = _sensors->getSettingByKey("gps");
    if (gps != nullptr) {
      const bool enabled = gps[0] != '1';
      if (the_mesh.setGpsEnabled(enabled)) {
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
        gps_changed = true;
#endif
#ifdef PIN_BUZZER
        if (!buzzer.speakGps(enabled)) notify(UIEventType::ack);
#else
        notify(UIEventType::ack);
#endif
        sprintf(_alert, enabled ? "GPS: Enabled" : "GPS: Disabled");
      } else {
        sprintf(_alert, "GPS: Save failed");
      }
    }
  }
#endif
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  if (!gps_changed) buzzer.speakButton(ButtonVoicePrompt::ActionFailed);
#endif
  _need_refresh = true;
}

void UITask::handleButtonLongPress() {
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  if (_shutdown_pending) return;
  _button_notification_cleared = false;
#endif
  MESH_DEBUG_PRINTLN("UITask: long press triggered");
  if (millis() - ui_started_at < 8000) {   // long press in first 8 seconds since startup -> CLI/rescue
    the_mesh.enterCLIRescue();
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
    // enterCLIRescue is supported independently of the framed USB interface.
    buzzer.speakButton(ButtonVoicePrompt::UsbSetup);
#endif
  } else {
    shutdown();
  }
}

#ifdef MESH_BUTTON_AUDIO_HIL
bool UITask::handleButtonAudioTest(const char* command, char* reply, size_t size) {
  if (strncmp(command, "hil voice play ", 15) == 0) {
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
    if (_shutdown_pending) {
      snprintf(reply, size, "Error: shutdown pending; preview refused");
      return true;
    }
    struct Preview {
      const char* name;
      ButtonVoicePrompt prompt;
    };
    static const Preview previews[] = {
      {"ready", ButtonVoicePrompt::Ready},
      {"notificationCleared", ButtonVoicePrompt::NotificationCleared},
      {"advertQueued", ButtonVoicePrompt::AdvertQueued},
      {"advertFailed", ButtonVoicePrompt::AdvertFailed},
      {"soundOn", ButtonVoicePrompt::SoundOn},
      {"soundOff", ButtonVoicePrompt::SoundOff},
      {"gpsOn", ButtonVoicePrompt::GpsOn},
      {"gpsOff", ButtonVoicePrompt::GpsOff},
      {"actionFailed", ButtonVoicePrompt::ActionFailed},
      {"usbSetup", ButtonVoicePrompt::UsbSetup},
      {"shuttingDown", ButtonVoicePrompt::ShuttingDown},
      {"restarting", ButtonVoicePrompt::Restarting},
#ifdef HAS_DRV2605
      {"alertsSoundVibration", ButtonVoicePrompt::AlertsSoundVibration},
      {"alertsSoundOnly", ButtonVoicePrompt::AlertsSoundOnly},
      {"alertsVibrationOnly", ButtonVoicePrompt::AlertsVibrationOnly},
      {"alertsSilent", ButtonVoicePrompt::AlertsSilent},
#endif
    };
    const char* name = command + 15;
    for (const auto& preview : previews) {
      if (strcmp(name, preview.name) != 0) continue;
      // This laboratory path only previews audio. It never invokes the
      // button action or changes its saved sound/GPS/shutdown preference.
      if (buzzer.speakButton(preview.prompt, true)) {
        snprintf(reply, size, "OK - preview only: %s", name);
      } else {
        snprintf(reply, size, "Error: voice preview unavailable");
      }
      return true;
    }
    snprintf(reply, size, "Error: unknown voice preview name");
#else
    snprintf(reply, size, "Error: voice preview unsupported");
#endif
    return true;
  }
  if (strncmp(command, "hil voice gain ", 15) == 0) {
#ifdef PIN_BUZZER
    const char* value = command + 15;
    if (value[0] >= '1' && value[0] <= '8' && value[1] == 0
        && buzzer.voiceGain(value[0] - '0')) {
      snprintf(reply, size, "OK - speech gain %c", value[0]);
      return true;
    }
#endif
    snprintf(reply, size, "Error: voice busy/unavailable or use gain 1..8");
    return true;
  }
  if (strcmp(command, "hil voice status") == 0) {
#ifdef PIN_BUZZER
    buzzer.voiceStatus(reply, size);
#else
    snprintf(reply, size, "voice unsupported");
#endif
    return true;
  }
  if (strncmp(command, "hil button ", 11) != 0) return false;
  const char* action = command + 11;
  uint8_t clicks = 0;
  uint32_t hold_ms = 120;
  if (action[0] >= '1' && action[0] <= '4' && action[1] == 0) clicks = action[0] - '0';
  else if (strcmp(action, "hold") == 0) { clicks = 1; hold_ms = 3500; }
#ifdef PIN_USER_BTN
  if (clicks != 0 && _userButton && _userButton->injectPresses(clicks, hold_ms)) {
    snprintf(reply, size, "OK - injected %u press(es), hold %lu ms", clicks, (unsigned long)hold_ms);
    return true;
  }
#endif
  snprintf(reply, size, "Error: button busy or use hil button 1|2|3|4|hold");
  return true;
}
#endif


uint8_t UITask::notificationCapabilities() const {
  uint8_t bits = 0;
#ifdef PIN_BUZZER
  bits |= mesh::notify::Sound;
#endif
#if defined(PIN_VIBRATION) || defined(HAS_DRV2605)
  bits |= mesh::notify::Vibration;
#endif
#ifdef MESHCORE_HAS_REAL_DISPLAY
  if (_display) bits |= mesh::notify::Screen;
#endif
  return bits;
}
void UITask::notificationMelody(const char* text) {
#ifdef PIN_BUZZER
#if MESH_GPS_VOICE
  if (_shutdown_pending) return;
  // The physical sound preference is authoritative even if the separate
  // programmable-notification settings file could not be saved.
  if (text && buzzer.isQuiet()) return;
#endif
  if (text) buzzer.playNotification(text); else buzzer.stopNotification();
#endif
}
void UITask::notificationScreen(int8_t mode) {
  if (!_display) return;
  _display->notificationPower(mode);
  _need_refresh = true;
}
void UITask::notificationVibration(bool on) {
#ifdef HAS_DRV2605
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  // The saved physical alert mode stays authoritative even if the separate
  // notification-mask write fails. Off callbacks must still stop the driver.
  if (on && _node_prefs && _node_prefs->vibe_quiet) {
    vibration.stop();
    return;
  }
#endif
  vibration.pulse(on);
#elif defined(PIN_VIBRATION)
  vibration.stop();
  digitalWrite(PIN_VIBRATION, on ? HIGH : LOW);
#endif
}
