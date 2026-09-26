#include "UITask.h"
#if UI_SMALL_MESSAGE_FONT == 1
  #include <helpers/ui/SmallMessageText.h>
#endif
#include <helpers/TxtDataHelpers.h>
#include <helpers/ui/BluetoothPairingUiPolicy.h>
#include <helpers/ui/RadioProfileDisplayPage.h>
#include <helpers/ui/RadioProfileSystemStatus.h>
#include "../MyMesh.h"
#include <RadioProfiles.h>
#include "target.h"
#include "u8g2_icons.h"

#if defined(ENABLE_WIFI_INTERFACE) || defined(WIFI_SSID)
  #include <WiFi.h>
#endif

#ifndef AUTO_OFF_MILLIS
  #define AUTO_OFF_MILLIS     15000   // 15 seconds
#endif
#ifndef BLE_PAIRING_DISPLAY_MILLIS
  #define BLE_PAIRING_DISPLAY_MILLIS 120000UL
#endif
#define BOOT_SCREEN_MILLIS   4000   // 4 seconds

#ifdef PIN_STATUS_LED
#define LED_ON_MILLIS     20
#define LED_ON_MSG_MILLIS 200
#define LED_CYCLE_MILLIS  4000
#endif

#define LONG_PRESS_MILLIS   1200

namespace {

mesh::ui::RadioProfileSystemStatus radioProfileSystemStatus(
    const CompanionNodePrefs& prefs) {
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

}  // namespace

#ifndef UI_RECENT_LIST_SIZE
  #define UI_RECENT_LIST_SIZE 4
#endif

#if UI_HAS_JOYSTICK
  #define PRESS_LABEL "press Enter"
#else
  #define PRESS_LABEL "long press"
#endif

class SplashScreen : public UIScreen {
  UITask* _task;
  unsigned long dismiss_after;
  unsigned long version_after;
  char _version_info[12];

public:
  SplashScreen(UITask* task) : _task(task) {
    // strip off dash and commit hash by changing dash to null terminator
    // e.g: v1.2.3-abcdef -> v1.2.3
    const char *ver = FIRMWARE_VERSION;
    const char *dash = strchr(ver, '-');

    int len = dash ? dash - ver : strlen(ver);
    if (len >= sizeof(_version_info)) len = sizeof(_version_info) - 1;
    memcpy(_version_info, ver, len);
    _version_info[len] = 0;

    version_after = millis() + BOOT_SCREEN_MILLIS / 2;
    dismiss_after = millis() + BOOT_SCREEN_MILLIS;
  }

  int render(DisplayDriver& display) override {
    if (millis() < version_after) {
    // meshcore logo
    display.setColor(UIColor::corp_blue);
    int logoWidth = 72;
    display.drawXbm(0, 0, meshcore_logo, 72, 36);
    } else {

    // meshcore website
    const char* website = "meshcore.io";
    display.setColor(UIColor::primary_txt);
    display.setTextSize(1);
    uint16_t websiteWidth = display.getTextWidth(website);
    display.setCursor((display.width() - websiteWidth) / 2, 9);
    display.print(website);

    // version info
    display.setTextSize(1);
    display.drawTextCentered(display.width()/2, 18, _version_info);

    display.setColor(UIColor::secondary_txt);
    display.setTextSize(1);
    display.drawTextCentered(display.width()/2, 27, FIRMWARE_BUILD_DATE);
    }
    return 1000;
  }

  void poll() override {
    if (millis() >= dismiss_after) {
      _task->gotoHomeScreen();
    }
  }
};

class HomeScreen : public UIScreen {
  enum HomePage {
    FIRST,
    RECENT,
    RADIO,
    // Only visible when the secondary radio profile is configured.
    RADIO2,
    RADIO_STATUS,
    BLUETOOTH,
    ADVERT,
#if ENV_INCLUDE_GPS == 1
    GPS,
#endif
#if UI_SENSORS_PAGE == 1
    SENSORS,
#endif
    SHUTDOWN,
    Count    // keep as last
  };

  UITask* _task;
  mesh::RTCClock* _rtc;
  SensorManager* _sensors;
  CompanionNodePrefs* _node_prefs;
  uint8_t _page;
  bool _shutdown_init;
  AdvertPath recent[UI_RECENT_LIST_SIZE];

  CayenneLPP sensors_lpp;
  int sensors_nb = 0;
  bool sensors_scroll = false;
  int sensors_scroll_offset = 0;
  int next_sensors_refresh = 0;
  uint32_t _radio_profile_page_started_at = 0;
  uint8_t _radio_status_page = 0;
  bool _dual_radio_enabled_seen = false;

  void refresh_sensors() {
    if (millis() > next_sensors_refresh) {
      sensors_lpp.reset();
      sensors_nb = 0;
      sensors_lpp.addVoltage(TELEM_CHANNEL_SELF, (float)board.getBattMilliVolts() / 1000.0f);
      sensors.querySensors(0xFF, sensors_lpp);
      LPPReader reader (sensors_lpp.getBuffer(), sensors_lpp.getSize());
      uint8_t channel, type;
      while(reader.readHeader(channel, type)) {
        reader.skipData(type);
        sensors_nb ++;
      }
      sensors_scroll = sensors_nb > UI_RECENT_LIST_SIZE;
#if AUTO_OFF_MILLIS > 0
      next_sensors_refresh = millis() + 5000; // refresh sensor values every 5 sec
#else
      next_sensors_refresh = millis() + 60000; // refresh sensor values every 1 min
#endif
    }
  }

  void resetRadioProfileDisplayPage() {
    _radio_profile_page_started_at = millis();
    _radio_status_page = 0;
    _dual_radio_enabled_seen = the_mesh.isDualRadioActive();
  }

  bool isPageVisible(uint8_t page) const {
    return the_mesh.isDualRadioActive()
        || (page != HomePage::RADIO2 && page != HomePage::RADIO_STATUS);
  }

  void movePage(int direction) {
    do {
      _page = (_page + HomePage::Count + direction) % HomePage::Count;
    } while (!isPageVisible(_page));
    if (_page == HomePage::RADIO || _page == HomePage::RADIO_STATUS) {
      resetRadioProfileDisplayPage();
    }
  }

  int radioProfileRefreshMillis() const {
    const uint32_t elapsed = millis() - _radio_profile_page_started_at;
    const uint32_t remaining = mesh::ui::RADIO_PROFILE_DISPLAY_PAGE_MILLIS
        - elapsed % mesh::ui::RADIO_PROFILE_DISPLAY_PAGE_MILLIS;
    return remaining < 5000 ? static_cast<int>(remaining) : 5000;
  }

public:
  HomeScreen(UITask* task, mesh::RTCClock* rtc, SensorManager* sensors, CompanionNodePrefs* node_prefs)
     : _task(task), _rtc(rtc), _sensors(sensors), _node_prefs(node_prefs), _page(0),
       _shutdown_init(false), sensors_lpp(200) {
    resetRadioProfileDisplayPage();
  }

  void showFirstPage() { _page = HomePage::FIRST; }

  void poll() override {
    if (_dual_radio_enabled_seen != the_mesh.isDualRadioActive()) {
      resetRadioProfileDisplayPage();
    }
    if (!isPageVisible(_page)) {
      _page = HomePage::RADIO;
      resetRadioProfileDisplayPage();
    }
    if (_shutdown_init && !_task->isButtonPressed()) {  // must wait for USR button to be released
      _task->shutdown();
    }
  }

  int render(DisplayDriver& display) override {
    char tmp[80];

    if (_page == HomePage::FIRST) {
      // // node name
      // display.setTextSize(1);
      // display.setColor(DisplayDriver::GREEN);
      // char filtered_name[sizeof(_node_prefs->node_name)];
      // display.translateUTF8ToBlocks(filtered_name, _node_prefs->node_name, sizeof(filtered_name));
      // display.setCursor(0, 0);
      // display.print(filtered_name);

      display.setColor(UIColor::primary_txt);
      display.setTextSize(2);
      sprintf(tmp, "MSG: %d", _task->getMsgCount());
      display.setCursor(0, 10);
      display.print(tmp);

      sprintf(tmp, "BATT: %.2fV", _task->getCachedBattMV() / 1000.0f);
      display.setCursor(0, 19);
      display.print(tmp);

      #ifdef ENABLE_WIFI_INTERFACE
        IPAddress ip = WiFi.localIP();
        snprintf(tmp, sizeof(tmp), "IP: %d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3]);
        display.setTextSize(1);
        display.drawTextCentered(display.width() / 2, 54, tmp);
      #endif
      const bool bluetooth_connected = _task->hasBluetoothConnection();
      const uint32_t bluetooth_pin = the_mesh.getBLEPin();
      if (bluetooth_connected) {
        display.setColor(UIColor::warning_txt);
        display.setTextSize(1);
        display.drawTextCentered(display.width() / 2, display.height()-8, "< Connected >");

      } else if (mesh::ui::shouldDisplayBluetoothPairingPin(
                     _task->isBluetoothEnabled(), bluetooth_connected,
                     bluetooth_pin)) { // BT pin
        display.setColor(UIColor::warning_txt);
        display.setTextSize(2);
        sprintf(tmp, "Pin:%u", (unsigned int)bluetooth_pin);
        display.drawTextCentered(display.width() / 2, display.height()-8, tmp);
      }
    } else if (_page == HomePage::RECENT) {
      the_mesh.getRecentlyHeard(recent, UI_RECENT_LIST_SIZE);
      display.setColor(UIColor::primary_txt);
      int y = 8;
      for (int i = 0; i < UI_RECENT_LIST_SIZE; i++, y += 11) {
        auto a = &recent[i];
        if (a->name[0] == 0) continue;  // empty slot
        int secs = _rtc->getCurrentTime() - a->recv_timestamp;
        if (secs < 60) {
          sprintf(tmp, "%ds", secs);
        } else if (secs < 60*60) {
          sprintf(tmp, "%dm", secs / 60);
        } else {
          sprintf(tmp, "%dh", secs / (60*60));
        }

        int timestamp_width = display.getTextWidth(tmp);
        int max_name_width = display.width() - timestamp_width - 1;

        char filtered_recent_name[sizeof(a->name)];
        display.translateUTF8ToBlocks(filtered_recent_name, a->name, sizeof(filtered_recent_name));
        display.drawTextEllipsized(0, y, max_name_width, filtered_recent_name);
        display.setCursor(display.width() - timestamp_width - 1, y);
        display.print(tmp);
      }
    } else if (_page == HomePage::RADIO || _page == HomePage::RADIO2
               || _page == HomePage::RADIO_STATUS) {
      display.setColor(UIColor::primary_txt);
      display.setTextSize(1);
      const auto* radio = the_mesh.getProfileRadio();
      const mesh::RadioProfiles* profiles = radio ? radio->profiles() : NULL;
      const bool dual_radio = profiles != NULL && profiles->enabled();
      const uint8_t status_pages = mesh::ui::radioProfileSystemStatusPageCount(
          display, dual_radio, 8);
      uint8_t status_page_index = 0;
      const bool status_page = dual_radio
          ? _page == HomePage::RADIO_STATUS
          : mesh::ui::showRadioProfileSystemStatusPage(
                false, status_pages,
                millis() - _radio_profile_page_started_at,
                &status_page_index);
      if (status_page) {
        if (dual_radio && status_pages != 0) {
          status_page_index = _radio_status_page % status_pages;
        }
        mesh::ui::drawRadioProfileSystemStatusPage(display,
            radioProfileSystemStatus(*_node_prefs), status_page_index, 8);
        return dual_radio ? 5000 : radioProfileRefreshMillis();
      }
      const bool secondary_page = dual_radio && _page == HomePage::RADIO2;
      const uint8_t profile = dual_radio && secondary_page ? 1 : 0;
      float freq = _node_prefs->freq;
      float bw = _node_prefs->bw;
      uint8_t sf = _node_prefs->sf;
      uint8_t cr = _node_prefs->cr;
      const char* profile_tag = "";
      if (dual_radio) {
        const auto& params = profiles->params(profile);
        freq = params.freq;
        bw = params.bw;
        sf = params.sf;
        cr = params.cr;
        profile_tag = mesh::ui::radioProfileDisplayTag(profile,
            profile == 1 ? profiles->secondary_temporary
                         : profiles->primary_temporary);
      }
      // frequency and spreading factor
      display.setCursor(0, 8);
      if (dual_radio) {
        snprintf(tmp, sizeof(tmp), "%s F:%06.3f", profile_tag, freq);
      } else {
        snprintf(tmp, sizeof(tmp), "FQ %06.3f", freq);
      }
      display.print(tmp);
      snprintf(tmp, sizeof(tmp), "S:%d", sf);
      display.drawTextRightAlign(display.width(), 8, tmp);
      // bandwidth and coding rate
      display.setCursor(0, 17);
      if (dual_radio) {
        snprintf(tmp, sizeof(tmp), "%s B:%03.2f", profile_tag, bw);
      } else {
        snprintf(tmp, sizeof(tmp), "BW %03.2f", bw);
      }
      display.print(tmp);
      snprintf(tmp, sizeof(tmp), "C:%d", cr);
      display.drawTextRightAlign(display.width(), 17, tmp);
      // tx power and noise floor
      display.setCursor(0, 26);
      const char* noise_label = dual_radio ? (profile == 0 ? "N1" : "N2") : "NF";
      const float noise_floor = radio_driver.getNoiseFloorDbm(profile);
      if (noise_floor == 0.0f) {
        const float seconds = radio_driver
            .getNoiseFloorCalibrationSecondsRemaining(profile);
        if (seconds > 0.0f) {
          snprintf(tmp, sizeof(tmp), "%s:%.1fs", noise_label, seconds);
        } else {
          snprintf(tmp, sizeof(tmp), "%s:WAIT", noise_label);
        }
      } else {
        snprintf(tmp, sizeof(tmp), "%s:%.1f", noise_label, noise_floor);
      }
      display.print(tmp);
      snprintf(tmp, sizeof(tmp), "TX:%d", _node_prefs->tx_power_dbm);
      display.drawTextRightAlign(display.width(), 26, tmp);

    } else if (_page == HomePage::BLUETOOTH) {
      display.setColor(UIColor::corp_blue);
      display.drawXbm((display.width() - 32) / 2, 8,
          _task->isBluetoothEnabled() ? bluetooth_on : bluetooth_off,
          32, 32);
      display.setTextSize(1);
      // display.drawTextCentered(display.width() / 2, 40 - 11, "toggle: " PRESS_LABEL);
    } else if (_page == HomePage::ADVERT) {
      display.setColor(UIColor::corp_blue);
      display.drawXbm((display.width() - 32) / 2, 8, advert_icon, 32, 32);
      // display.drawTextCentered(display.width() / 2, 40 - 11, "advert: " PRESS_LABEL);
#if ENV_INCLUDE_GPS == 1
    } else if (_page == HomePage::GPS) {
      LocationProvider* nmea = sensors.getLocationProvider();
      char buf[50];
      int y = 8;
      bool gps_state = _task->getGPSState();
#ifdef PIN_GPS_SWITCH
      bool hw_gps_state = digitalRead(PIN_GPS_SWITCH);
      if (gps_state != hw_gps_state) {
        strcpy(buf, gps_state ? "gps off(hw)" : "gps off(sw)");
      } else {
        strcpy(buf, gps_state ? "gps on" : "gps off");
      }
#else
      strcpy(buf, gps_state ? "gps on" : "gps off");
#endif
      display.setColor(UIColor::primary_txt);
      display.drawTextLeftAlign(0, y, buf);
      if (nmea == NULL) {
        // y = y + 8;
        display.setColor(UIColor::warning_txt);
        display.drawTextLeftAlign(0, y, "Can't access GPS");
      } else {
        if (!gps_state || !nmea->isValid()) {
            strcpy(buf, "no fix");
        } else {
            sprintf(buf, "%d sat", nmea->satellitesCount());
        }
        display.setColor(UIColor::primary_txt);
        display.drawTextRightAlign(display.width()-1, y, buf);
        y = y + 8;
        sprintf(buf, "lat %.4f",
          nmea->getLatitude()/1000000.);
        display.drawTextLeftAlign(0, y, buf);
        y = y + 8;
        sprintf(buf, "lon %.4f",
          nmea->getLongitude()/1000000.);
        display.drawTextLeftAlign(0, y, buf);
        y = y + 8;
        sprintf(buf, "alt %.1f", nmea->getAltitude()/1000.);
        display.drawTextLeftAlign(0, y, buf);
      }
#endif
#if UI_SENSORS_PAGE == 1
    } else if (_page == HomePage::SENSORS) {
      int y = 8;
      refresh_sensors();
      char buf[30];
      char name[30];
      LPPReader r(sensors_lpp.getBuffer(), sensors_lpp.getSize());

      for (int i = 0; i < sensors_scroll_offset; i++) {
        uint8_t channel, type;
        r.readHeader(channel, type);
        r.skipData(type);
      }

      for (int i = 0; i < (sensors_scroll?UI_RECENT_LIST_SIZE:sensors_nb); i++) {
        uint8_t channel, type;
        if (!r.readHeader(channel, type)) { // reached end, reset
          r.reset();
          r.readHeader(channel, type);
        }

        display.setCursor(0, y);
        float v;
        switch (type) {
          case LPP_GPS: // GPS
            float lat, lon, alt;
            r.readGPS(lat, lon, alt);
            strcpy(name, "gps"); sprintf(buf, "%.4f %.4f", lat, lon);
            break;
          case LPP_VOLTAGE:
            r.readVoltage(v);
            strcpy(name, "voltage"); sprintf(buf, "%6.2f", v);
            break;
          case LPP_CURRENT:
            r.readCurrent(v);
            strcpy(name, "current"); sprintf(buf, "%.3f", v);
            break;
          case LPP_TEMPERATURE:
            r.readTemperature(v);
            strcpy(name, "temperature"); sprintf(buf, "%.2f", v);
            break;
          case LPP_RELATIVE_HUMIDITY:
            r.readRelativeHumidity(v);
            strcpy(name, "humidity"); sprintf(buf, "%.2f", v);
            break;
          case LPP_BAROMETRIC_PRESSURE:
            r.readPressure(v);
            strcpy(name, "pressure"); sprintf(buf, "%.2f", v);
            break;
          case LPP_ALTITUDE:
            r.readAltitude(v);
            strcpy(name, "altitude"); sprintf(buf, "%.0f", v);
            break;
          case LPP_POWER:
            r.readPower(v);
            strcpy(name, "power"); sprintf(buf, "%6.2f", v);
            break;
          default:
            r.skipData(type);
            strcpy(name, "unk"); sprintf(buf, "");
        }
        display.setColor(UIColor::secondary_txt);
        display.setCursor(0, y);
        display.print(name);
        display.setColor(UIColor::primary_txt);
        display.setCursor(
          display.width()-display.getTextWidth(buf)-1, y
        );
        display.print(buf);
        y = y + 12;
      }
      if (sensors_scroll) sensors_scroll_offset = (sensors_scroll_offset+1)%sensors_nb;
      else sensors_scroll_offset = 0;
#endif
    } else if (_page == HomePage::SHUTDOWN) {
      display.setColor(UIColor::secondary_txt);
      display.setTextSize(1);
      if (_shutdown_init) {
        display.drawTextCentered(display.width() / 2, 20, "hibernating...");
      } else {
        display.drawXbm((display.width() - 32) / 2, 8, power_icon, 32, 32);
        // display.drawTextCentered(display.width() / 2, 40 - 11, "hibernate:" PRESS_LABEL);
      }
    }
    return _page == HomePage::RADIO && !the_mesh.isDualRadioActive()
        ? radioProfileRefreshMillis() : 5000;
  }

  bool handleInput(char c) override {
    if (c == KEY_LEFT || c == KEY_PREV) {
      movePage(-1);
      return true;
    }
    if (c == KEY_NEXT || c == KEY_RIGHT) {
      movePage(1);
      if (_page == HomePage::RECENT) {
        _task->showAlert("Recent adverts", 800);
      }
      return true;
    }
    if (c == KEY_ENTER && _page == HomePage::RADIO_STATUS) {
      _radio_status_page++;
      return true;
    }
    if (c == KEY_ENTER && _page == HomePage::BLUETOOTH) {
      if (_task->isBluetoothEnabled()) {  // toggle Bluetooth on/off
        _task->disableBluetooth();
      } else {
        _task->enableBluetooth();
      }
      return true;
    }
    if (c == KEY_ENTER && _page == HomePage::ADVERT) {
      _task->notify(UIEventType::ack);
      if (the_mesh.advert()) {
        _task->showAlert("Advert sent!", 1000);
      } else {
        _task->showAlert("Advert failed..", 1000);
      }
      return true;
    }
#if ENV_INCLUDE_GPS == 1
    if (c == KEY_ENTER && _page == HomePage::GPS) {
      _task->toggleGPS();
      return true;
    }
#endif
#if UI_SENSORS_PAGE == 1
    if (c == KEY_ENTER && _page == HomePage::SENSORS) {
      _task->toggleGPS();
      next_sensors_refresh=0;
      return true;
    }
#endif
    if (c == KEY_ENTER && _page == HomePage::SHUTDOWN) {
      _shutdown_init = true;  // need to wait for button to be released
      return true;
    }
    return false;
  }
};

#if UI_SMALL_MESSAGE_FONT == 1
class TinyMessageScreen : public UIScreen {
  UITask* _task;
  char _origin[62] = {};
  char _message[161] = {};
public:
  explicit TinyMessageScreen(UITask* task) : _task(task) {}
  void setMessage(uint8_t path_len, const char* from, const char* message) {
    if (path_len == 0xFF) {
      snprintf(_origin, sizeof(_origin), "%s [direct]:", from);
    } else {
      snprintf(_origin, sizeof(_origin), "%s [%uh]:", from,
                 (unsigned int)path_len);
    }
    StrHelper::strncpy(_message, message, sizeof(_message));
  }
  int render(DisplayDriver& display) override {
    // The 72x40 interface reserves its top 8px for the scrolling status bar.
    mesh::ui::drawSmallMessageBody(display, _origin, _message, 10);
    return 1000;
  }
  bool handleInput(char c) override {
    if (c == KEY_NEXT || c == KEY_PREV || c == KEY_LEFT || c == KEY_RIGHT
        || c == KEY_ENTER) {
      _task->gotoHomeScreen();
      return true;
    }
    return false;
  }
};
#endif

void UITask::begin(DisplayDriver* display, SensorManager* sensors, CompanionNodePrefs* node_prefs) {
  _display = display;
  _sensors = sensors;
  if (_display != NULL) {
    _display->setRotationDegrees(node_prefs->display_rotation_degrees);
  }

  _cached_batt_mv = getBattMilliVolts();

#if defined(PIN_USER_BTN)
  user_btn.begin();
#endif
#if defined(PIN_USER_BTN_ANA)
  analog_btn.begin();
#endif

  _node_prefs = node_prefs;

  if (_display != NULL) {
    _display->servicePower(_board->isExternalPowered() || _board->isUsbHostConnected(), hasConnection());
  }
  _statusBar.begin(_display->width());

#ifdef PIN_BUZZER
  buzzer.begin();
  buzzer.quiet(_node_prefs->buzzer_quiet);
  buzzer.startup();
#endif

#ifdef PIN_VIBRATION
  vibration.begin();
#endif

  ui_started_at = millis();
  _alert_expiry = 0;

  splash = new SplashScreen(this);
  home = new HomeScreen(this, &rtc_clock, sensors, node_prefs);
#if UI_SMALL_MESSAGE_FONT == 1
  msg_preview = new TinyMessageScreen(this);
#endif
  setCurrScreen(splash);
}

void UITask::showAlert(const char* text, int duration_millis) {
  strcpy(_alert, text);
  _alert_expiry = millis() + duration_millis;
}

void UITask::notify(UIEventType t) {
#if defined(PIN_BUZZER)
switch(t){
  case UIEventType::contactMessage:
    // gemini's pick
    buzzer.play("MsgRcv3:d=4,o=6,b=200:32e,32g,32b,16c7");
    break;
  case UIEventType::channelMessage:
    buzzer.play("kerplop:d=16,o=6,b=120:32g#,32c#");
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

#ifdef PIN_VIBRATION
  // Trigger vibration for all UI events except none
  if (t != UIEventType::none) {
    vibration.trigger();
  }
#endif
}

void UITask::msgRead(int msgcount) {
  _msgcount = msgcount;
  if (msgcount == 0) {
#if UI_SMALL_MESSAGE_FONT == 1
    if (curr == msg_preview
        && static_cast<int32_t>(millis() - _msg_preview_until) < 0) return;
    _deferred_msg_preview = false;
#endif
    gotoHomeScreen();
  }
}

void UITask::newMsg(uint8_t path_len, const char* from_name, const char* text,
                    int msgcount, int channel_idx,
                    const char* channel_name, int queue_index) {
  (void)queue_index;
  (void)channel_idx;
  (void)channel_name;
  _msgcount = msgcount;
#if UI_SMALL_MESSAGE_FONT == 1
  static_cast<TinyMessageScreen*>(msg_preview)->setMessage(path_len, from_name, text);
  if (isPairingScreenActive()) _deferred_msg_preview = true;
  else setCurrScreen(msg_preview);
  _msg_preview_until = millis() + 15000UL;
#endif

  if (_display != NULL) {
    if (shouldWakeDisplayForMessage()) {
      _display->wake(mesh::ui::DisplayWake::Message);
    }
    if (_display->isOn()) _next_refresh = 0;
  }
}

void UITask::userLedHandler() {
#ifdef PIN_STATUS_LED
  int cur_time = millis();
  if (cur_time > next_led_change) {
    if (led_state == 0) {
      led_state = 1;
      if (_msgcount > 0) {
        last_led_increment = LED_ON_MSG_MILLIS;
      } else {
        last_led_increment = LED_ON_MILLIS;
      }
      next_led_change = cur_time + last_led_increment;
    } else {
      led_state = 0;
      next_led_change = cur_time + LED_CYCLE_MILLIS - last_led_increment;
    }
    digitalWrite(PIN_STATUS_LED, led_state == LED_STATE_ON);
  }
#endif
}

void UITask::setCurrScreen(UIScreen* c) {
  curr = c;
  _next_refresh = 100;
}

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
  static_cast<HomeScreen*>(home)->showFirstPage();
  setCurrScreen(home);
  if (_display != NULL) {
    _display->servicePower(_board->isExternalPowered() || _board->isUsbHostConnected(), hasConnection(), true);
    _next_refresh = 0;
  }
}

void UITask::finishPairingScreen(bool timed_out) {
  _pairing_screen_until = 0;
  _next_refresh = 0;
#if UI_SMALL_MESSAGE_FONT == 1
  if (_deferred_msg_preview) {
    _deferred_msg_preview = false;
    setCurrScreen(msg_preview);
  }
#endif
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
      hasConnection(), isPairingScreenActive())) _next_refresh = 0;
}

/*
  hardware-agnostic pre-shutdown activity should be done here
*/
void UITask::shutdown(bool restart){
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
}

bool UITask::isButtonPressed() const {
#ifdef PIN_USER_BTN
  return user_btn.isPressed();
#else
  return false;
#endif
}

void UITask::loop() {
  servicePairingState();

  char c = 0;
#if UI_HAS_JOYSTICK
  int ev = user_btn.check();
  if (ev == BUTTON_EVENT_CLICK) {
    c = checkDisplayOn(KEY_ENTER);
  } else if (ev == BUTTON_EVENT_LONG_PRESS) {
    c = handleLongPress(KEY_ENTER);  // REVISIT: could be mapped to different key code
  }
  ev = joystick_left.check();
  if (ev == BUTTON_EVENT_CLICK) {
    c = checkDisplayOn(KEY_LEFT);
  } else if (ev == BUTTON_EVENT_LONG_PRESS) {
    c = handleLongPress(KEY_LEFT);
  }
  ev = joystick_right.check();
  if (ev == BUTTON_EVENT_CLICK) {
    c = checkDisplayOn(KEY_RIGHT);
  } else if (ev == BUTTON_EVENT_LONG_PRESS) {
    c = handleLongPress(KEY_RIGHT);
  }
  ev = back_btn.check();
  if (ev == BUTTON_EVENT_TRIPLE_CLICK) {
    c = handleTripleClick(KEY_SELECT);
  }
#elif defined(PIN_USER_BTN)
  int ev = user_btn.check();
  if (ev == BUTTON_EVENT_CLICK) {
    c = checkDisplayOn(KEY_NEXT);
  } else if (ev == BUTTON_EVENT_LONG_PRESS) {
    c = handleLongPress(KEY_ENTER);
  } else if (ev == BUTTON_EVENT_DOUBLE_CLICK) {
    c = handleDoubleClick(KEY_PREV);
  } else if (ev == BUTTON_EVENT_TRIPLE_CLICK) {
    c = handleTripleClick(KEY_SELECT);
  }
#endif
#if defined(PIN_USER_BTN_ANA)
  if (abs(millis() - _analogue_pin_read_millis) > 10) {
    int ev = analog_btn.check();
    if (ev == BUTTON_EVENT_CLICK) {
      c = checkDisplayOn(KEY_NEXT);
    } else if (ev == BUTTON_EVENT_LONG_PRESS) {
      c = handleLongPress(KEY_ENTER);
    } else if (ev == BUTTON_EVENT_DOUBLE_CLICK) {
      c = handleDoubleClick(KEY_PREV);
    } else if (ev == BUTTON_EVENT_TRIPLE_CLICK) {
      c = handleTripleClick(KEY_SELECT);
    }
    _analogue_pin_read_millis = millis();
  }
#endif
#if defined(BACKLIGHT_BTN)
  if (millis() > next_backlight_btn_check) {
    static bool was_pressed = false;
    const bool pressed = digitalRead(PIN_BUTTON2) == LOW;
    if (pressed && !was_pressed && _display != NULL)
      _display->wake(mesh::ui::DisplayWake::Button);
    was_pressed = pressed;
    next_backlight_btn_check = millis() + 300;
  }
#endif
#if defined(HAS_TORCH)
  ev = back_btn.check();
  if (ev == BUTTON_EVENT_CLICK && c == 0) {
    c = checkDisplayOn(KEY_PREV);
  } else if (ev == BUTTON_EVENT_DOUBLE_CLICK) {
    board.toggleTorch();
    c = 0;
  }
#endif

  if (isPairingScreenActive()) {
    static_cast<HomeScreen*>(home)->showFirstPage();
    if (curr != home) setCurrScreen(home);
    c = 0;
  }

  if (c != 0 && curr) {
    _display->wake(mesh::ui::DisplayWake::Button);
    curr->handleInput(c);
    _next_refresh = 100;  // trigger refresh
  }

  userLedHandler();

#ifdef PIN_BUZZER
  if (buzzer.isPlaying())  buzzer.loop();
#endif

  if (curr) curr->poll();

  if (_display != NULL && _display->isOn()) {
        _statusBar.update(*_display,
        _node_prefs->node_name,
        _cached_batt_mv,
        isBuzzerQuiet(),
        getGPSState(),
        isBluetoothEnabled());

    bool status_dirty = _statusBar.needsRedraw();
    bool content_dirty = (millis() >= _next_refresh && curr);

    if (status_dirty || content_dirty) {
      _display->startFrame();
      _statusBar.render(*_display);

      if (curr) {
        int delay_millis = curr->render(*_display);
        if (content_dirty) {
          _next_refresh = millis() + delay_millis;
        }
      }

      if (!isPairingScreenActive() && millis() < _alert_expiry) {  // render alert popup
        _display->setTextSize(1);
        int y = _display->height() / 3;
        int p = _display->height() / 32;
        _display->setColor(UIColor::popup_bkg);
        _display->fillRect(p, y, _display->width() - p*2, y);
        _display->setColor(UIColor::popup_txt);  // draw box border
        _display->drawRect(p, y, _display->width() - p*2, y);
        _display->drawTextCentered(_display->width() / 2, y + p*3, _alert);
        _next_refresh = _alert_expiry;   // will need refresh when alert is dismissed
      }

      _display->endFrame();
    }
#if AUTO_OFF_MILLIS > 0
#if MOMENTARY_BUTTON_WAKE_HOLD_MS > 0 && defined(PIN_USER_BTN)
    if (user_btn.isWakeHoldActive()) _display->wake(mesh::ui::DisplayWake::Button);
#endif
#endif
  }

#ifdef PIN_VIBRATION
  vibration.loop();
#endif

#ifdef AUTO_SHUTDOWN_MILLIVOLTS
  if (millis() > next_batt_chck) {
    _cached_batt_mv = getBattMilliVolts();
    if (_cached_batt_mv > 0 && _cached_batt_mv < AUTO_SHUTDOWN_MILLIVOLTS) {
      if(!board.isExternalPowered()) {
        if (_display != NULL) {
        _display->startFrame();
        _display->setTextSize(2);
        _display->drawTextCentered(_display->width() / 2, 6, "Low battery!");
        _display->setTextSize(1);
        _display->drawTextCentered(_display->width() / 2, 18, "Shutting down!");
        _display->endFrame();
        if (_display->isEink() == false) { delay(3000); }
        }
        shutdown();
      }
    }
    next_batt_chck = millis() + 8000;
  }
#else
  if (_display != NULL && _display->isOn() && millis() >= next_batt_chck) {
    _cached_batt_mv = getBattMilliVolts();
    next_batt_chck = millis() + 8000;
  }
#endif
}

char UITask::checkDisplayOn(char c) {
  if (_display != NULL) {
    const bool was_on = _display->isOn();
    _display->wake(mesh::ui::DisplayWake::Button);
    if (!was_on) {
      c = 0;
    }
    _next_refresh = 0;  // trigger refresh
  }
  return c;
}

char UITask::handleLongPress(char c) {
  if (millis() - ui_started_at < 8000) {   // long press in first 8 seconds since startup -> CLI/rescue
    the_mesh.enterCLIRescue();
    c = 0;   // consume event
  }
  return c;
}

char UITask::handleDoubleClick(char c) {
  MESH_DEBUG_PRINTLN("UITask: double-click triggered");
  checkDisplayOn(c);
  return c;
}

char UITask::handleTripleClick(char c) {
  MESH_DEBUG_PRINTLN("UITask: triple click triggered");
  checkDisplayOn(c);
  toggleBuzzer();
  c = 0;
  return c;
}

bool UITask::getGPSState() {
  if (_sensors != NULL) {
    int num = _sensors->getNumSettings();
    for (int i = 0; i < num; i++) {
      if (strcmp(_sensors->getSettingName(i), "gps") == 0) {
        return !strcmp(_sensors->getSettingValue(i), "1");
      }
    }
  }
  return false;
}

void UITask::toggleGPS() {
    if (_sensors != NULL) {
    // toggle GPS on/off
    int num = _sensors->getNumSettings();
    for (int i = 0; i < num; i++) {
      if (strcmp(_sensors->getSettingName(i), "gps") == 0) {
        if (strcmp(_sensors->getSettingValue(i), "1") == 0) {
          _sensors->setSettingValue("gps", "0");
          _node_prefs->gps_enabled = 0;
          notify(UIEventType::ack);
        } else {
          _sensors->setSettingValue("gps", "1");
          _node_prefs->gps_enabled = 1;
          notify(UIEventType::ack);
        }
        the_mesh.savePrefs();
        showAlert(_node_prefs->gps_enabled ? "GPS: Enabled" : "GPS: Disabled", 800);
        _next_refresh = 0;
        break;
      }
    }
  }
}

void UITask::toggleBuzzer() {
    // Toggle buzzer quiet mode
  #ifdef PIN_BUZZER
    if (buzzer.isQuiet()) {
      buzzer.quiet(false);
      notify(UIEventType::ack);
    } else {
      buzzer.quiet(true);
    }
    _node_prefs->buzzer_quiet = buzzer.isQuiet();
    the_mesh.savePrefs();
    showAlert(buzzer.isQuiet() ? "Buzzer: OFF" : "Buzzer: ON", 800);
    _next_refresh = 0;  // trigger refresh
  #endif
}
