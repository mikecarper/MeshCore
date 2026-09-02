#include "UITask.h"
#include <helpers/TxtDataHelpers.h>
#include <helpers/ui/BluetoothPairingUiPolicy.h>
#include <helpers/ui/CompanionHomeLayout.h>
#include <helpers/ui/CompanionMessageHistory.h>
#include <helpers/ui/CompanionTransportSelectorLayout.h>
#include "../MyMesh.h"
#include "../CompanionWiFi.h"
#include "target.h"
#include <time.h>
#ifdef WIFI_SSID
  #include <WiFi.h>
#endif
#if UI_WIFI_SETUP_HOME_PAGE == 1
  #include <helpers/esp32/WebConfigServer.h>
  #include <helpers/ui/WiFiSetupQrPayload.h>
#endif
#if defined(ESP32)
  #include <esp_timer.h>
#endif

#ifndef UI_TZ_OFFSET
  #define UI_TZ_OFFSET 0
#endif

#ifndef AUTO_OFF_MILLIS
  #define AUTO_OFF_MILLIS     15000   // 15 seconds
#endif
#ifndef USB_MESSAGE_PREVIEW_MILLIS
  #define USB_MESSAGE_PREVIEW_MILLIS 15000UL
#endif
#ifndef UI_RADIO_REFRESH_MILLIS
  #define UI_RADIO_REFRESH_MILLIS 2000UL
#endif
#ifndef BLE_PAIRING_DISPLAY_MILLIS
  #define BLE_PAIRING_DISPLAY_MILLIS 120000UL
#endif
#define BOOT_SCREEN_MILLIS   3000   // 3 seconds

#ifdef PIN_STATUS_LED
#define LED_ON_MILLIS     20
#define LED_ON_MSG_MILLIS 200
#define LED_CYCLE_MILLIS  4000
#endif

#define LONG_PRESS_MILLIS   1200

static uint64_t companionMessageNowMillis() {
#if defined(ESP32)
  // Unlike Arduino millis(), this does not wrap after roughly 49.7 days.
  return static_cast<uint64_t>(esp_timer_get_time()) / 1000ULL;
#else
  return static_cast<uint64_t>(millis());
#endif
}

static uint64_t companionMessageElapsedMillis(uint64_t heard_millis) {
#if defined(ESP32)
  return companionMessageNowMillis() - heard_millis;
#else
  // Preserve Arduino's rollover-safe 32-bit subtraction on other platforms.
  return static_cast<uint32_t>(millis()
      - static_cast<uint32_t>(heard_millis));
#endif
}

#ifndef UI_RECENT_LIST_SIZE
  #define UI_RECENT_LIST_SIZE 4
#endif

#if UI_HAS_JOYSTICK || UI_HAS_ROTARY_INPUT
  #define PRESS_LABEL "press Enter"
#else
  #define PRESS_LABEL "long press"
#endif

#ifdef COMPANION_EXCLUSIVE_WIFI_BLE
static void drawCompanionTransportChoice(DisplayDriver& display,
                                         int x, int y, int width, int height,
                                         const char* label, bool active,
                                         bool selected) {
  if (active) {
    display.setColor(UIColor::corp_blue);
    display.fillRect(x, y, width, height);
    display.setColor(UIColor::window_bkg);
  } else {
    display.setColor(UIColor::secondary_txt);
    display.drawRect(x, y, width, height);
    display.setColor(UIColor::primary_txt);
  }

  const bool large_transport_text = display.height() >= 96;
  const bool show_status_label = height >= 44;
  if (large_transport_text) {
    display.setTextSize(4);
    if (strcmp(label, "WiFi") == 0) {
      // A single size-4 "WiFi" row is wider than one half of the screen.
      // Reflow it into two large rows instead of shrinking both choices.
      display.drawTextCentered(x + width / 2, y + 2, "Wi");
      display.drawTextCentered(x + width / 2, y + 36, "Fi");
    } else {
      display.drawTextCentered(x + width / 2, y + 25, label);
    }
  } else {
    display.setTextSize(1);
    const int label_y = show_status_label ? y + 12 : y + (height - 8) / 2;
    display.drawTextCentered(x + width / 2, label_y, label);
  }
  if (show_status_label && (active || selected)) {
    display.setTextSize(large_transport_text ? 3 : 1);
    display.drawTextCentered(
        x + width / 2,
        y + height - (large_transport_text ? 25 : 17),
        active ? (large_transport_text ? "ON" : "ACTIVE")
               : (large_transport_text ? "NEXT" : "NEXT BOOT"));
  }
}
#endif

#if UI_WIFI_SETUP_HOME_PAGE == 1
static void drawCompanionWiFiSetupPage(DisplayDriver& display) {
  char setup_ssid[33] = {0};
  char setup_ip[16] = {0};
  const bool setup_active = WebConfigServer::getSetupInfo(
      setup_ssid, sizeof(setup_ssid), setup_ip, sizeof(setup_ip));
  const bool wifi_enabled = isCompanionWiFiEnabled();
  const bool wifi_connected = isCompanionWiFiConnected();
  const bool wifi_configured = hasCompanionWiFiCredentials();
  const CompanionWiFiDisplayState state = setup_active
      ? CompanionWiFiDisplayState::Setup
      : !wifi_enabled
          ? CompanionWiFiDisplayState::Off
          : wifi_connected
              ? CompanionWiFiDisplayState::Ready
              : !wifi_configured
                  ? CompanionWiFiDisplayState::NotConfigured
                  : CompanionWiFiDisplayState::Connecting;
  static CompanionWiFiDisplayState previous_state =
      CompanionWiFiDisplayState::NotRendered;

  // Frame hashing normally suppresses identical LCD transfers. Explicitly
  // invalidate that cache on a network-state transition so a retained
  // CONNECTING frame can never survive a READY render.
  if (state != previous_state) {
    display.clear();
    previous_state = state;
  }
  noteCompanionWiFiDisplayState(state);

  // Keep the setup content below the shared title bar and page dots. Compact
  // text makes its physical size identical on the 480 canvas and the
  // 320-to-480 emergency canvas.
  display.setCompactText(true);
  display.setColor(UIColor::primary_txt);

  if (setup_active) {
    display.setTextSize(2);
    display.drawTextCentered(display.width() / 2, 20, "WIFI SETUP");
    display.drawTextCentered(display.width() / 2, 37, setup_ssid);

    char payload[256];
    const char* setup_password = nullptr;
#ifdef WEBCONFIG_AP_PASSWORD
    setup_password = WEBCONFIG_AP_PASSWORD;
#endif
    if (mesh::ui::buildWiFiSetupQrPayload(
            payload, sizeof(payload), setup_ssid, setup_password)) {
      static constexpr int qr_size = 105;
      const int qr_x = (display.width() - qr_size) / 2;
      if (display.drawQrCode(payload, qr_x, 55, qr_size)) {
        display.setCompactText(false);
        return;
      }
    }

    display.setColor(UIColor::warning_txt);
    display.setTextSize(2);
    display.drawTextCentered(display.width() / 2, 70, "QR UNAVAILABLE");
    display.setColor(UIColor::secondary_txt);
    display.drawTextCentered(display.width() / 2, 100, setup_ip);
  } else if (!wifi_enabled) {
    display.setTextSize(3);
    display.drawTextCentered(display.width() / 2, 45, "WIFI OFF");
    display.setTextSize(2);
    display.setColor(UIColor::secondary_txt);
    display.drawTextCentered(display.width() / 2, 90, "SELECT WIFI");
    display.drawTextCentered(display.width() / 2, 112, "THEN REBOOT");
  } else if (wifi_connected) {
    display.setTextSize(2);
    display.drawTextCentered(display.width() / 2, 25, "WIFI READY");
    const String ssid = WiFi.SSID();
    display.drawTextEllipsized(4, 60, display.width() - 8, ssid.c_str());
    const String ip = WiFi.localIP().toString();
    display.drawTextCentered(display.width() / 2, 95, ip.c_str());
  } else if (!wifi_configured) {
    display.setTextSize(3);
    display.drawTextCentered(display.width() / 2, 45, "WIFI SETUP");
    display.setTextSize(2);
    display.setColor(UIColor::secondary_txt);
    display.drawTextCentered(display.width() / 2, 90, "TAP TO START");
  } else {
    display.setTextSize(3);
    display.drawTextCentered(display.width() / 2, 45, "WIFI");
    display.setTextSize(2);
    display.setColor(UIColor::secondary_txt);
    display.drawTextCentered(display.width() / 2, 90, "CONNECTING");
  }

  display.setCompactText(false);
}
#endif

#include "icons.h"

class SplashScreen : public UIScreen {
  UITask* _task;
  unsigned long dismiss_after;
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

    dismiss_after = millis() + BOOT_SCREEN_MILLIS;
  }

  int render(DisplayDriver& display) override {
    // meshcore logo
    display.setColor(UIColor::corp_blue);
    int logoWidth = 128;
    display.drawXbm((display.width() - logoWidth) / 2, 3, meshcore_logo, logoWidth, 13);

    // meshcore website
    const char* website = "https://meshcore.io";
    display.setColor(UIColor::primary_txt);
    display.setTextSize(1);
    uint16_t websiteWidth = display.getTextWidth(website);
    display.setCursor((display.width() - websiteWidth) / 2, 22);
    display.print(website);

    // version info
    display.setColor(UIColor::primary_txt);
    display.setTextSize(1);
    display.drawTextCentered(display.width()/2, 35, _version_info);

    display.setColor(UIColor::secondary_txt);
    display.setTextSize(1);
    display.drawTextCentered(display.width()/2, 48, FIRMWARE_BUILD_DATE);

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
#if UI_MESSAGES_HOME_PAGE == 1
    MESSAGES,
#endif
    RECENT,
    RADIO,
#ifdef COMPANION_EXCLUSIVE_WIFI_BLE
    TRANSPORT,
#else
    BLUETOOTH,
#endif
    ADVERT,
#if ENV_INCLUDE_GPS == 1
    GPS,
#endif
#if UI_SENSORS_PAGE == 1
    SENSORS,
#endif
#ifndef UI_NO_HIBERNATE
    SHUTDOWN,
#endif
#if UI_WIFI_SETUP_HOME_PAGE == 1
    WIFI_SETUP,
#endif
    Count    // keep as last
  };

  UITask* _task;
  mesh::RTCClock* _rtc;
  SensorManager* _sensors;
  CompanionNodePrefs* _node_prefs;
  uint8_t _page;
  bool _shutdown_init;
#if UI_WIFI_SETUP_HOME_PAGE == 1
  bool _wifi_setup_was_active;
  bool _wifi_was_connected;
#endif
  uint32_t _uptime_last_millis;
  uint64_t _uptime_millis;
  AdvertPath recent[UI_RECENT_LIST_SIZE];


  int renderBatteryIndicator(DisplayDriver& display, uint16_t batteryMilliVolts) {
    // Convert millivolts to percentage
#ifndef BATT_MIN_MILLIVOLTS
  #define BATT_MIN_MILLIVOLTS 3000
#endif
#ifndef BATT_MAX_MILLIVOLTS
  #define BATT_MAX_MILLIVOLTS 4200
#endif
    const int minMilliVolts = BATT_MIN_MILLIVOLTS;
    const int maxMilliVolts = BATT_MAX_MILLIVOLTS;
    const bool showBattery = batteryMilliVolts != 0;
    int batteryPercentage = showBattery
      ? ((batteryMilliVolts - minMilliVolts) * 100) / (maxMilliVolts - minMilliVolts)
      : 0;
    if (batteryPercentage < 0) batteryPercentage = 0; // Clamp to 0%
    if (batteryPercentage > 100) batteryPercentage = 100; // Clamp to 100%

    // battery icon
    int iconWidth = 24;
    int iconHeight = 10;
    int iconX = showBattery
      ? display.width() - iconWidth - 5
      : display.width() - 5;
    int iconY = 0;
    display.setColor(UIColor::title_txt);

    // Track uptime across the 32-bit millis() rollover and show the two most
    // useful units beside the battery icon.
    uint32_t now = millis();
    _uptime_millis += (uint32_t)(now - _uptime_last_millis);
    _uptime_last_millis = now;

    uint64_t uptime_minutes = _uptime_millis / 60000ULL;
    unsigned long days = (unsigned long)(uptime_minutes / 1440ULL);
    unsigned long hours = (unsigned long)((uptime_minutes % 1440ULL) / 60ULL);
    unsigned long minutes = (unsigned long)(uptime_minutes % 60ULL);
    char uptime[16];
    if (days > 0) {
      snprintf(uptime, sizeof(uptime), "%lud %luh", days, hours);
    } else if (hours > 0) {
      snprintf(uptime, sizeof(uptime), "%luh %lum", hours, minutes);
    } else {
      snprintf(uptime, sizeof(uptime), "%lum", minutes);
    }

    display.setTextSize(1);
    bool charging = board.isExternalPowered();
#ifdef PIN_BUZZER
    bool muted = _task->isBuzzerQuiet();
#endif

    int uptimeWidth = display.getTextWidth(uptime);
    int spaceWidth = display.getTextWidth(" ");
    int statusWidth = charging ? 9 : 0;
#ifdef PIN_BUZZER
    if (muted) statusWidth += 9;
#endif
    int statusStartX = iconX - statusWidth - uptimeWidth - spaceWidth;
    display.setCursor(statusStartX, iconY);
    display.print(uptime);

    if (showBattery) {
      // battery outline
      display.drawRect(iconX, iconY, iconWidth, iconHeight);

      // battery "cap"
      display.fillRect(iconX + iconWidth, iconY + (iconHeight / 4), 3, iconHeight / 2);

      // fill the battery based on the percentage
      int fillWidth = (batteryPercentage * (iconWidth - 4)) / 100;
      display.fillRect(iconX + 2, iconY + 2, fillWidth, iconHeight - 4);
    }

    // Show external power beside the battery. Most boards have no
    // charge-complete signal, so use a high percentage band for the plug.
    if (charging) {
      static constexpr int BATT_FULL_PCT = 95;
      const uint8_t* symbol =
        !showBattery || batteryPercentage >= BATT_FULL_PCT ? plug_icon : charging_icon;
      display.setColor(UIColor::title_txt);
      display.drawXbm(iconX - 9, iconY + 1, symbol, 8, 8);
    }

    // Keep the mute icon left of the charging icon when both are present.
#ifdef PIN_BUZZER
    if (muted) {
      display.setColor(UIColor::warning_txt);
      display.drawXbm(iconX - (charging ? 18 : 9), iconY + 1, muted_icon, 8, 8);
    }
#endif

    return statusStartX;
  }

  CayenneLPP sensors_lpp;
  int sensors_nb = 0;
  bool sensors_scroll = false;
  int sensors_scroll_offset = 0;
  int next_sensors_refresh = 0;

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

public:
  HomeScreen(UITask* task, mesh::RTCClock* rtc, SensorManager* sensors, CompanionNodePrefs* node_prefs)
     : _task(task), _rtc(rtc), _sensors(sensors), _node_prefs(node_prefs), _page(0),
       _shutdown_init(false),
#if UI_WIFI_SETUP_HOME_PAGE == 1
       _wifi_setup_was_active(false), _wifi_was_connected(false),
#endif
       _uptime_last_millis(millis()), _uptime_millis(0), sensors_lpp(200) {
#if UI_WIFI_SETUP_HOME_PAGE == 1
    _wifi_setup_was_active = WebConfigServer::getSetupInfo(
        nullptr, 0, nullptr, 0);
    _wifi_was_connected = isCompanionWiFiConnected();
    if (_wifi_setup_was_active) _page = HomePage::WIFI_SETUP;
#endif
  }

  void showFirstPage() { _page = HomePage::FIRST; }

  bool isTransportSelectorPage() const {
#ifdef COMPANION_EXCLUSIVE_WIFI_BLE
    return _page == HomePage::TRANSPORT;
#else
    return false;
#endif
  }

  void poll() override {
#if UI_WIFI_SETUP_HOME_PAGE == 1
    const bool wifi_setup_active = WebConfigServer::getSetupInfo(
        nullptr, 0, nullptr, 0);
    if (wifi_setup_active && !_wifi_setup_was_active) {
      // Focus a newly opened setup portal once. Keeping this edge-triggered
      // lets the user swipe away while setup and LoRa continue running.
      _page = HomePage::WIFI_SETUP;
      _task->gotoHomeScreen();
    }
    _wifi_setup_was_active = wifi_setup_active;
    const bool wifi_connected = isCompanionWiFiConnected();
    if (wifi_connected != _wifi_was_connected) {
      // A network transition must invalidate the page immediately. This also
      // recovers from a previously retained CONNECTING frame even if the
      // ordinary one-second render deadline was delayed by another service.
      _wifi_was_connected = wifi_connected;
      _task->gotoHomeScreen();
    }
#endif
    if (_shutdown_init && !_task->isButtonPressed()) {  // must wait for USR button to be released
      _task->shutdown();
    }
  }

  int render(DisplayDriver& display) override {
    display.setColor(UIColor::title_bkg);
    display.fillRect(0, 0, display.width(), 12);
    char tmp[80];
    // status indicators
    display.setTextSize(1);
    display.setColor(UIColor::title_txt);
    int statusStartX = renderBatteryIndicator(display, _task->getBattMilliVolts());

    // node name
    char filtered_name[sizeof(_node_prefs->node_name)];
    display.translateUTF8ToBlocks(filtered_name, _node_prefs->node_name, sizeof(filtered_name));
    int availableNameWidth = statusStartX - 2;
    if (availableNameWidth > 0) {
      display.drawTextEllipsized(0, 2, availableNameWidth, filtered_name);
    }

    // curr page indicator
    if (UIColor::title_bkg == UIColor::window_bkg) {
      display.setColor(UIColor::title_txt);
    } else {
      // A dark title panel is only subtly different from a dark page. Use the
      // high-contrast accent for these tiny dots; monochrome palettes take the
      // equal-background branch above and retain their light text colour.
      display.setColor(UIColor::corp_blue);
    }
    int y = 14;
    int x = display.width() / 2 - 5 * (HomePage::Count-1);
    for (uint8_t i = 0; i < HomePage::Count; i++, x += 10) {
      if (i == _page) {
        display.fillRect(x-1, y-1, 4, 4);
      } else {
        display.fillRect(x, y, 2, 2);
      }
    }

    if (_page == HomePage::FIRST) {
      display.setColor(UIColor::primary_txt);
#ifdef UI_DEDICATED_PAIRING_BLOCK
      const bool expanded_home =
          mesh::ui::usesExpandedCompanionHomeTypography(
              display.width(), display.height(),
              display.renderWidth(), display.renderHeight());
      if (expanded_home) {
        display.setTextSize(4);
        display.drawTextCentered(display.width() / 2, 20, "INBOX");
      } else {
        sprintf(tmp, "INBOX: %d", _task->getPreviewCount());
        display.setTextSize(2);
        display.drawTextCentered(display.width() / 2, 22, tmp);
      }
#else
      display.setTextSize(2);
      sprintf(tmp, "INBOX: %d", _task->getPreviewCount());
      display.drawTextCentered(display.width() / 2, 22, tmp);
#endif
#ifdef UI_DEDICATED_PAIRING_BLOCK
      const mesh::ui::CompanionHomeLayout layout =
          mesh::ui::makeLargeCompanionHomeLayout(display.width(),
                                                 display.height(),
                                                 expanded_home);

      // These are distinct repaint regions. In particular, removing or
      // replacing a pairing PIN cannot leave old glyphs behind, and long
      // instruction/network strings cannot enter the pairing block.
      display.setColor(UIColor::window_bkg);
      mesh::ui::clearDisplayRegion(display, layout.info);
      mesh::ui::clearDisplayRegion(display, layout.pairing);

      if (expanded_home) {
        snprintf(tmp, sizeof(tmp), "%d", _task->getPreviewCount());
        display.setTextSize(4);
        if (display.getTextWidth(tmp) > layout.info.width) {
          display.setTextSize(3);
        }
        display.setColor(UIColor::primary_txt);
        mesh::ui::drawTextCenteredEllipsized(
            display, layout.info, layout.instruction_y, tmp);
      } else {
        display.setTextSize(1);
        display.setColor(UIColor::secondary_txt);
        mesh::ui::drawTextCenteredEllipsized(
            display, layout.info, layout.instruction_y,
            "tap center: inbox");

        #ifdef WIFI_SSID
          if (!isCompanionWiFiEnabled()) {
            strcpy(tmp, "WiFi: OFF");
          } else if (isCompanionWiFiConnected()) {
            IPAddress ip = WiFi.localIP();
            snprintf(tmp, sizeof(tmp), "IP: %d.%d.%d.%d",
                     ip[0], ip[1], ip[2], ip[3]);
          } else if (hasCompanionWiFiCredentials()) {
            strcpy(tmp, "WiFi: CONNECTING");
          } else {
            strcpy(tmp, "WiFi: SETUP");
          }
          display.setTextSize(1);
          mesh::ui::drawTextCenteredEllipsized(
              display, layout.info, layout.network_y, tmp);
        #endif
      }

      const bool bluetooth_enabled = _task->isBluetoothEnabled();
      const bool bluetooth_connected = _task->hasBluetoothConnection();
      const uint32_t bluetooth_pin = the_mesh.getBLEPin();
      const char* pairing_label = nullptr;
      const char* pairing_value = nullptr;
      int pairing_value_size = 2;
      char pairing_pin[16];
      if (bluetooth_connected) {
        pairing_label = expanded_home ? "BLE" : "BLUETOOTH";
        pairing_value = expanded_home ? "LINKED" : "CONNECTED";
        if (expanded_home) pairing_value_size = 3;
      } else if (mesh::ui::shouldDisplayBluetoothPairingPin(
                     bluetooth_enabled, bluetooth_connected, bluetooth_pin)
                 && (!_task->isPairingPromptActive()
                     || display.height() > 64)) {
        pairing_label = expanded_home ? "PIN" : "BLUETOOTH PIN";
        snprintf(pairing_pin, sizeof(pairing_pin), "%06u",
                 (unsigned int)bluetooth_pin);
        pairing_value = pairing_pin;
        pairing_value_size = 3;
      }

      if (pairing_value != nullptr) {
        display.setColor(UIColor::title_bkg);
        mesh::ui::clearDisplayRegion(display, layout.pairing);
        display.setColor(UIColor::title_txt);
        display.setTextSize(expanded_home ? 3 : 1);
        mesh::ui::drawTextCenteredEllipsized(
            display, layout.pairing, layout.pairing_label_y, pairing_label);
        display.setTextSize(pairing_value_size);
        mesh::ui::drawTextCenteredEllipsized(
            display, layout.pairing, layout.pairing_value_y, pairing_value);
      } else if (expanded_home) {
        display.setColor(UIColor::secondary_txt);
        display.setTextSize(3);
        mesh::ui::drawTextCenteredEllipsized(
            display, layout.pairing, layout.pairing_label_y, "TAP");
#ifdef WIFI_SSID
        if (!isCompanionWiFiEnabled()) {
          strcpy(tmp, "OFF");
          display.setTextSize(3);
        } else if (isCompanionWiFiConnected()) {
          IPAddress ip = WiFi.localIP();
          snprintf(tmp, sizeof(tmp), "IP: %d.%d.%d.%d",
                   ip[0], ip[1], ip[2], ip[3]);
          display.setTextSize(1);
        } else if (hasCompanionWiFiCredentials()) {
          strcpy(tmp, "WAIT");
          display.setTextSize(3);
        } else {
          strcpy(tmp, "SETUP");
          display.setTextSize(2);
        }
        mesh::ui::drawTextCenteredEllipsized(
            display, layout.pairing, layout.pairing_value_y, tmp);
#endif
      }
#else
      const bool bluetooth_enabled = _task->isBluetoothEnabled();
      const bool bluetooth_connected = _task->hasBluetoothConnection();
      const uint32_t bluetooth_pin = the_mesh.getBLEPin();
      const bool show_bluetooth_pin =
          mesh::ui::shouldDisplayBluetoothPairingPin(
              bluetooth_enabled, bluetooth_connected, bluetooth_pin)
          && (!_task->isPairingPromptActive() || display.height() > 64);
      const bool compact_pairing =
          mesh::ui::usesCompactCompanionPairingLayout(
              display.width(), display.height())
          && (bluetooth_connected || show_bluetooth_pin);

      if (compact_pairing) {
        const mesh::ui::CompactCompanionPairingLayout layout =
            mesh::ui::makeCompactCompanionPairingLayout(
                display.width(), display.height());
        char pairing_pin[16];
        const char* pairing_label = bluetooth_connected
            ? "BLUETOOTH" : "BLUETOOTH PIN";
        const char* pairing_value = "CONNECTED";
        if (!bluetooth_connected) {
          snprintf(pairing_pin, sizeof(pairing_pin), "%06u",
                   (unsigned int)bluetooth_pin);
          pairing_value = pairing_pin;
        }

        // This repaint deliberately removes the ordinary instruction and
        // Wi-Fi/IP rows. Both occupy this same lower area on a 128x64 screen.
        display.setColor(UIColor::title_bkg);
        mesh::ui::clearDisplayRegion(display, layout.pairing);
        display.setColor(UIColor::title_txt);
        display.setTextSize(1);
        mesh::ui::drawTextCenteredEllipsized(
            display, layout.pairing, layout.pairing_label_y, pairing_label);
        display.setTextSize(2);
        mesh::ui::drawTextCenteredEllipsized(
            display, layout.pairing, layout.pairing_value_y, pairing_value);
      } else {
        display.setTextSize(1);
        display.setColor(UIColor::secondary_txt);
        display.drawTextCentered(display.width() / 2, 43,
                                 "tap center: inbox");

        #ifdef UI_SHOW_CLOCK
        display.setTextSize(3);
        uint32_t now = _rtc->getCurrentTime();
        int8_t tz = UI_TZ_OFFSET; // for now draw time from Santo Domingo ...
        now += (int32_t)tz * 3600;
        DateTime dt (now);
        sprintf(tmp, "%02d:%02d", dt.hour(), dt.minute());
        display.drawTextCentered(display.width() / 2, 60, tmp);
        display.setTextSize(1);
        sprintf(tmp, "%02d/%02d/%d", dt.day(), dt.month(), dt.year());
        display.drawTextCentered(display.width() / 2, 80, tmp);
        #endif

        #ifdef WIFI_SSID
          if (!isCompanionWiFiEnabled()) {
            strcpy(tmp, "WiFi: OFF");
          } else if (isCompanionWiFiConnected()) {
            IPAddress ip = WiFi.localIP();
            snprintf(tmp, sizeof(tmp), "IP: %d.%d.%d.%d",
                     ip[0], ip[1], ip[2], ip[3]);
          } else if (hasCompanionWiFiCredentials()) {
            strcpy(tmp, "WiFi: CONNECTING");
          } else {
            strcpy(tmp, "WiFi: SETUP");
          }
          display.setTextSize(1);
          display.drawTextCentered(display.width() / 2, 54, tmp);
        #endif

        if (bluetooth_connected) {
          display.setColor(UIColor::warning_txt);
          display.setTextSize(1);
          #ifdef UI_SHOW_CLOCK
          display.drawTextCentered(display.width() / 2, 110,
                                   "< Connected >");
          #else
          display.drawTextCentered(display.width() / 2, 43,
                                   "< Connected >");
          #endif
        } else if (show_bluetooth_pin) { // BT pin
          display.setColor(UIColor::warning_txt);
          snprintf(tmp, sizeof(tmp), "Pin:%06u",
                   (unsigned int)bluetooth_pin);
        #ifdef UI_SHOW_CLOCK
          display.setTextSize(1);
          display.drawTextCentered(display.width() / 2, 110, tmp);
        #else
          display.setTextSize(2);
          display.drawTextCentered(display.width() / 2, 43, tmp);
        #endif
        }
      }
#endif
#if UI_MESSAGES_HOME_PAGE == 1
    } else if (_page == HomePage::MESSAGES) {
      _task->renderMessageSummary(display);
#endif
    } else if (_page == HomePage::RECENT) {
      the_mesh.getRecentlyHeard(recent, UI_RECENT_LIST_SIZE);
      display.setColor(UIColor::primary_txt);
      int y = 20;
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
    } else if (_page == HomePage::RADIO) {
      display.setColor(UIColor::primary_txt);
      display.setTextSize(1);
      // freq / sf
      display.setCursor(0, 20);
      sprintf(tmp, "FQ: %06.3f   SF: %d", _node_prefs->freq, _node_prefs->sf);
      display.print(tmp);

      display.setCursor(0, 31);
      sprintf(tmp, "BW: %03.2f     CR: %d", _node_prefs->bw, _node_prefs->cr);
      display.print(tmp);

      // tx power,  noise floor
      display.setCursor(0, 42);
      sprintf(tmp, "TX: %ddBm", _node_prefs->tx_power_dbm);
      display.print(tmp);
      display.setCursor(0, 53);
      float noise_floor = radio_driver.getNoiseFloorDbm();
      if (noise_floor == 0.0f) {
        strcpy(tmp, "Noise floor: measuring");
      } else {
        snprintf(tmp, sizeof(tmp), "Noise floor: %.1f", noise_floor);
      }
      display.print(tmp);
#ifdef COMPANION_EXCLUSIVE_WIFI_BLE
    } else if (_page == HomePage::TRANSPORT) {
      const bool wifi_active = isCompanionWiFiEnabled();
      const bool wifi_selected = getCompanionTransportMode()
          == CompanionTransportMode::WiFi;
      const mesh::ui::CompanionTransportSelectorLayout layout =
          mesh::ui::makeCompanionTransportSelectorLayout(
              display.width(), display.height());

      // Keep this page independent of the additional native-480 text boost.
      // Its deliberately reflowed size-4 choices then retain the same large
      // physical dimensions in the 320 and 480 render profiles.
      display.setCompactText(true);
      if (layout.show_title) {
        display.setColor(UIColor::primary_txt);
        display.setTextSize(3);
        display.drawTextCentered(
            display.width() / 2, layout.title_y, "MODE");
      }
      drawCompanionTransportChoice(
          display, layout.wifi.x, layout.wifi.y,
          layout.wifi.width, layout.wifi.height,
          "WiFi", wifi_active, wifi_selected);
      drawCompanionTransportChoice(
          display, layout.bluetooth.x, layout.bluetooth.y,
          layout.bluetooth.width, layout.bluetooth.height,
          "BLE", !wifi_active, !wifi_selected);

      display.setColor(UIColor::secondary_txt);
      display.setTextSize(layout.show_title ? 2 : 1);
#ifdef HAS_TOUCH
      display.drawTextCentered(
          display.width() / 2, layout.prompt_y,
          layout.show_title ? "TAP SIDE" : "tap a box");
#else
      display.drawTextCentered(
          display.width() / 2, layout.prompt_y, PRESS_LABEL);
#endif
      display.setCompactText(false);
#else
    } else if (_page == HomePage::BLUETOOTH) {
      display.setColor(UIColor::corp_blue);
      display.drawXbm((display.width() - 32) / 2, 18,
          _task->isBluetoothEnabled() ? bluetooth_on : bluetooth_off,
          32, 32);
      display.setColor(UIColor::secondary_txt);
      display.setTextSize(1);
      display.drawTextCentered(display.width() / 2, 64 - 11, "toggle: " PRESS_LABEL);
#endif
    } else if (_page == HomePage::ADVERT) {
      display.setColor(UIColor::corp_blue);
      display.drawXbm((display.width() - 32) / 2, 18, advert_icon, 32, 32);
      display.setColor(UIColor::secondary_txt);
      display.drawTextCentered(display.width() / 2, 64 - 11, "advert: " PRESS_LABEL);
#if ENV_INCLUDE_GPS == 1
    } else if (_page == HomePage::GPS) {
      LocationProvider* nmea = sensors.getLocationProvider();
      char buf[50];
      int y = 18;
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
        y = y + 12;
        display.setColor(UIColor::secondary_txt);
        display.drawTextLeftAlign(0, y, "Can't access GPS");
      } else {
        display.setColor(UIColor::primary_txt);
        strcpy(buf, nmea->isValid()?"fix":"no fix");
        display.drawTextRightAlign(display.width()-1, y, buf);
        y = y + 12;
        display.setColor(UIColor::secondary_txt);
        display.drawTextLeftAlign(0, y, "sat");
        display.setColor(UIColor::primary_txt);
        sprintf(buf, "%d", nmea->satellitesCount());
        display.drawTextRightAlign(display.width()-1, y, buf);
        y = y + 12;
        display.setColor(UIColor::secondary_txt);
        display.drawTextLeftAlign(0, y, "pos");
        display.setColor(UIColor::primary_txt);
        sprintf(buf, "%.4f %.4f",
          nmea->getLatitude()/1000000., nmea->getLongitude()/1000000.);
        display.drawTextRightAlign(display.width()-1, y, buf);
        y = y + 12;
        display.setColor(UIColor::secondary_txt);
        display.drawTextLeftAlign(0, y, "alt");
        display.setColor(UIColor::primary_txt);
        sprintf(buf, "%.2f", nmea->getAltitude()/1000.);
        display.drawTextRightAlign(display.width()-1, y, buf);
        y = y + 12;
      }
#endif
#if UI_SENSORS_PAGE == 1
    } else if (_page == HomePage::SENSORS) {
      int y = 18;
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
        display.setCursor(0, y);
        display.setColor(UIColor::secondary_txt);
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
#if UI_WIFI_SETUP_HOME_PAGE == 1
    } else if (_page == HomePage::WIFI_SETUP) {
      drawCompanionWiFiSetupPage(display);
#endif
#ifndef UI_NO_HIBERNATE
    } else if (_page == HomePage::SHUTDOWN) {
      display.setColor(UIColor::corp_blue);
      display.setTextSize(1);
      if (_shutdown_init) {
        display.setColor(UIColor::warning_txt);
        display.drawTextCentered(display.width() / 2, 34, "hibernating...");
      } else {
        display.setColor(UIColor::secondary_txt);
        display.drawXbm((display.width() - 32) / 2, 18, power_icon, 32, 32);
        display.drawTextCentered(display.width() / 2, 64 - 11, "hibernate:" PRESS_LABEL);
      }
#endif
    }
    if (_page == HomePage::RADIO) return UI_RADIO_REFRESH_MILLIS;
#if UI_MESSAGES_HOME_PAGE == 1
    if (_page == HomePage::MESSAGES) return 1000;
#endif
#if UI_WIFI_SETUP_HOME_PAGE == 1
    if (_page == HomePage::WIFI_SETUP) return 1000;
#endif
    return 5000;
  }

  bool handleInput(char c) override {
    if (c == KEY_LEFT || c == KEY_PREV) {
      _page = (_page + HomePage::Count - 1) % HomePage::Count;
      return true;
    }
    if (c == KEY_NEXT || c == KEY_RIGHT) {
      _page = (_page + 1) % HomePage::Count;
      if (_page == HomePage::RECENT) {
        _task->showAlert("Recent adverts", 800);
      }
      return true;
    }
#ifdef COMPANION_EXCLUSIVE_WIFI_BLE
    if (_page == HomePage::TRANSPORT
        && (c == KEY_ENTER || c == KEY_UP || c == KEY_DOWN)) {
      const CompanionTransportMode selected = getCompanionTransportMode();
      const CompanionTransportMode active = isCompanionWiFiEnabled()
          ? CompanionTransportMode::WiFi
          : CompanionTransportMode::Bluetooth;
      CompanionTransportMode requested = selected;
      if (c == KEY_UP) {
        requested = CompanionTransportMode::WiFi;
      } else if (c == KEY_DOWN) {
        requested = CompanionTransportMode::Bluetooth;
      } else {
        requested = active == CompanionTransportMode::WiFi
            ? CompanionTransportMode::Bluetooth
            : CompanionTransportMode::WiFi;
      }

      if (requested != selected
          && !selectCompanionTransportMode(requested)) {
        _task->showAlert("Transport save failed", 1500);
        return true;
      }
      if (requested != active) _task->shutdown(true);
      return true;
    }
#else
    if (c == KEY_ENTER && _page == HomePage::BLUETOOTH) {
      if (_task->isBluetoothEnabled()) {  // toggle Bluetooth on/off
        _task->disableBluetooth();
      } else {
        _task->enableBluetooth();
      }
      return true;
    }
#endif
    if (c == KEY_ENTER && _page == HomePage::FIRST) {
      _task->showMessages();
      return true;
    }
#if UI_MESSAGES_HOME_PAGE == 1
    if (c == KEY_ENTER && _page == HomePage::MESSAGES) {
      _task->showMessages();
      return true;
    }
#endif
#if UI_WIFI_SETUP_HOME_PAGE == 1
    if (c == KEY_ENTER && _page == HomePage::WIFI_SETUP) {
      if (isCompanionWiFiEnabled() && !isCompanionWiFiConnected()
          && !WebConfigServer::getSetupInfo(nullptr, 0, nullptr, 0)) {
        requestCompanionWiFiSetup();
        _task->showAlert("Starting WiFi setup", 1000);
      }
      return true;
    }
#endif
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
#ifndef UI_NO_HIBERNATE
    if (c == KEY_ENTER && _page == HomePage::SHUTDOWN) {
      _shutdown_init = true;  // need to wait for button to be released
      return true;
    }
#endif
    return false;
  }
};

#ifndef UI_MSG_PREVIEW_SIZE
  #define UI_MSG_PREVIEW_SIZE 78
#endif
#ifndef UI_COMPACT_MESSAGE_STATUS
  #define UI_COMPACT_MESSAGE_STATUS 0
#endif
#ifndef UI_MESSAGE_CHANNEL_FOOTER
  #define UI_MESSAGE_CHANNEL_FOOTER 1
#endif

class MsgPreviewScreen : public UIScreen {
  UITask* _task;

  static constexpr int CHANNEL_FILTER_ALL = -2;
  static constexpr int CHANNEL_FILTER_DIRECT = -1;
  static constexpr size_t MAX_RECENT_MESSAGES = 32;
  using MessageHistory = mesh::ui::CompanionMessageHistory<
      MAX_RECENT_MESSAGES, UI_MSG_PREVIEW_SIZE>;
  using MsgEntry = MessageHistory::Entry;

  MessageHistory history;
  int view_offset;
  int channel_filter;

  bool matchesFilter(const MsgEntry& entry) const {
    return channel_filter == CHANNEL_FILTER_ALL
        || entry.channel_idx == channel_filter;
  }

  int filteredCount() const {
    int count = 0;
    for (size_t age = 0; age < history.count(); ++age) {
      const MsgEntry* entry = history.newest(age);
      if (entry != nullptr && matchesFilter(*entry)) ++count;
    }
    return count;
  }

  const MsgEntry* filteredEntry(int offset) const {
    int match = 0;
    for (size_t age = 0; age < history.count(); ++age) {
      const MsgEntry* entry = history.newest(age);
      if (entry == nullptr || !matchesFilter(*entry)) continue;
      if (match++ == offset) return entry;
    }
    return nullptr;
  }

  int buildChannelFilters(int* filters, int capacity) const {
    int count = 0;
    if (count < capacity) filters[count++] = CHANNEL_FILTER_ALL;
    for (int channel_idx = 0;
         channel_idx < MAX_GROUP_CHANNELS && count < capacity;
         ++channel_idx) {
      ChannelDetails details;
      if (the_mesh.getChannel(channel_idx, details)
          && details.name[0] != 0) {
        filters[count++] = channel_idx;
      }
    }
    if (count < capacity) filters[count++] = CHANNEL_FILTER_DIRECT;
    return count;
  }

  void cycleChannelFilter(int direction) {
    int filters[MAX_GROUP_CHANNELS + 2];
    int count = buildChannelFilters(
        filters, sizeof(filters) / sizeof(filters[0]));
    if (count == 0) return;

    int selected = 0;
    while (selected < count && filters[selected] != channel_filter) {
      ++selected;
    }
    if (selected == count) selected = 0;
    selected = (selected + direction + count) % count;
    channel_filter = filters[selected];
    view_offset = 0;
  }

  void channelFilterLabel(char* label, size_t size) const {
    if (channel_filter == CHANNEL_FILTER_ALL) {
      StrHelper::strncpy(label, "All channels", size);
      return;
    }
    if (channel_filter == CHANNEL_FILTER_DIRECT) {
      StrHelper::strncpy(label, "Direct", size);
      return;
    }

    ChannelDetails details;
    if (the_mesh.getChannel(channel_filter, details)
        && details.name[0] != 0) {
      snprintf(label, size, "Ch %d %s", channel_filter, details.name);
      return;
    }
    for (size_t age = 0; age < history.count(); ++age) {
      const MsgEntry* entry = history.newest(age);
      if (entry != nullptr && entry->channel_idx == channel_filter
          && entry->channel_name[0] != 0) {
        snprintf(label, size, "Ch %d %s", channel_filter,
                 entry->channel_name);
        return;
      }
    }
    snprintf(label, size, "Ch %d", channel_filter);
  }

  void renderChannelFilter(DisplayDriver& display) const {
#if UI_MESSAGE_CHANNEL_FOOTER == 1
    const mesh::ui::CompanionMessageChromeLayout layout =
        mesh::ui::makeCompanionMessageChromeLayout(
            UI_COMPACT_MESSAGE_STATUS == 1);
    const int bar_height = layout.filter_height;
    const int bar_y = display.height() - bar_height;
    display.setColor(UIColor::window_bkg);
    display.fillRect(0, bar_y, display.width(), bar_height);
    display.setColor(UIColor::corp_blue);
    display.drawRect(0, bar_y, display.width(), 1);

    char channel[48];
    channelFilterLabel(channel, sizeof(channel));
    display.setCompactText(layout.compact_text);
    display.setTextSize(1);
    const int text_y = bar_y + layout.filter_text_offset;
    display.setCursor(8, text_y);
    display.print("<");
    display.drawTextCentered(display.width() / 2, text_y, channel);
    display.setCursor(display.width() - display.getTextWidth(">") - 8,
                      text_y);
    display.print(">");
    display.setCompactText(false);
#else
    (void)display;
#endif
  }

public:
  explicit MsgPreviewScreen(UITask* task)
      : _task(task), view_offset(0), channel_filter(CHANNEL_FILTER_ALL) {}

  bool hasMessages() const { return !history.empty(); }
  int messageCount() const { return (int)history.count(); }

  void addPreview(uint8_t path_len, const char* from_name, const char* msg,
                  int channel_idx, const char* channel_name) {
    if (channel_idx < CHANNEL_FILTER_DIRECT
        || channel_idx >= MAX_GROUP_CHANNELS) {
      channel_idx = CHANNEL_FILTER_DIRECT;
      channel_name = nullptr;
    }
    view_offset = 0;
    channel_filter = channel_idx;

    char origin[62];
    if (path_len == 0xFF) {
      snprintf(origin, sizeof(origin), "%s [direct]:", from_name);
    } else {
      snprintf(origin, sizeof(origin), "%s [%uh]:", from_name,
               (unsigned int)path_len);
    }
    history.add(companionMessageNowMillis(), channel_idx, channel_name,
                origin, msg);
  }

  void renderSummary(DisplayDriver& display) const {
    const mesh::ui::CompanionMessageListLayout layout =
        mesh::ui::makeCompanionMessageListLayout(display.height());
    int rendered = 0;

    display.setTextSize(1);
    for (size_t age = 0;
         age < history.count() && rendered < layout.visible_rows;
         ++age) {
      if (history.hasNewerEntryForThread(age)) continue;
      const MsgEntry* entry = history.newest(age);
      if (entry == nullptr) continue;

      const int row_y = layout.top + rendered * layout.row_height;
      char label[48];
      MessageHistory::threadLabel(*entry, label, sizeof(label));
      char filtered_label[sizeof(label)];
      display.translateUTF8ToBlocks(filtered_label, label,
                                    sizeof(filtered_label));

      char age_text[16];
      mesh::ui::formatCompanionMessageAge(
          age_text, sizeof(age_text),
          companionMessageElapsedMillis(entry->heard_millis));
      const int age_width = display.getTextWidth(age_text);
      int label_width = display.width() - age_width - 5;
      if (label_width < 0) label_width = 0;

      display.setColor(UIColor::primary_txt);
      display.drawTextEllipsized(0, row_y + layout.title_offset,
                                 label_width, filtered_label);
      display.setColor(UIColor::secondary_txt);
      display.setCursor(display.width() - age_width - 1,
                        row_y + layout.title_offset);
      display.print(age_text);

      char filtered_message[sizeof(entry->message)];
      display.translateUTF8ToBlocks(filtered_message, entry->message,
                                    sizeof(filtered_message));
      mesh::ui::makeCompanionMessagePreviewSingleLine(filtered_message);
      display.drawTextEllipsized(0, row_y + layout.preview_offset,
                                 display.width() - 1, filtered_message);

      display.setColor(UIColor::title_bkg);
      display.drawRect(0, row_y + layout.divider_offset,
                       display.width(), 1);
      ++rendered;
    }

    if (rendered == 0) {
      display.setColor(UIColor::secondary_txt);
      display.drawTextCentered(display.width() / 2,
                               layout.top + layout.row_height,
                               "No messages heard");
    }
  }

  int render(DisplayDriver& display) override {
    const mesh::ui::CompanionMessageChromeLayout layout =
        mesh::ui::makeCompanionMessageChromeLayout(
            UI_COMPACT_MESSAGE_STATUS == 1);
    char tmp[24];
    int filtered_count = filteredCount();
    if (view_offset >= filtered_count) view_offset = 0;
    display.setCompactText(layout.compact_text);
    display.setCursor(0, 0);
    display.setTextSize(1);
    display.setColor(UIColor::corp_blue);
    snprintf(tmp, sizeof(tmp), "Message %d/%d",
             filtered_count == 0 ? 0 : view_offset + 1, filtered_count);
    display.print(tmp);

    const MsgEntry* p = filteredEntry(view_offset);

    if (p == nullptr) {
      display.drawRect(0, layout.header_divider_y, display.width(), 1);
      display.setCompactText(false);
      display.setColor(UIColor::secondary_txt);
      display.drawTextCentered(display.width() / 2, 40,
                               "No buffered messages");
      renderChannelFilter(display);
      return 5000;
    }

    mesh::ui::formatCompanionMessageAge(
        tmp, sizeof(tmp), companionMessageElapsedMillis(p->heard_millis));
    display.setCursor(display.width() - display.getTextWidth(tmp) - 2, 0);
    display.print(tmp);

    display.drawRect(0, layout.header_divider_y, display.width(), 1);
    display.setCompactText(false);

    display.setCursor(0, layout.origin_y);
    display.setColor(UIColor::secondary_txt);
    char filtered_origin[sizeof(p->origin)];
    display.translateUTF8ToBlocks(filtered_origin, p->origin, sizeof(filtered_origin));
    display.print(filtered_origin);

    display.setCursor(0, layout.message_y);
    display.setColor(UIColor::primary_txt);
    char filtered_msg[sizeof(p->message)];
    display.translateUTF8ToBlocks(filtered_msg, p->message, sizeof(filtered_msg));
    display.printWordWrap(filtered_msg, display.width());

    renderChannelFilter(display);

#if AUTO_OFF_MILLIS==0 // probably e-ink
    return 10000; // 10 s
#else
    return 1000;  // next render after 1000 ms
#endif
  }

  bool handleInput(char c) override {
    if (c == KEY_NEXT || c == KEY_RIGHT) {
      if (view_offset + 1 < filteredCount()) {
        ++view_offset;
      } else {
        // The historical inbox flow returned Home after its final item. Keep
        // that wrap behavior now that previews persist in MessageHistory;
        // otherwise a valid navigation press is a silent no-op at the end.
        _task->gotoHomeScreen();
      }
      return true;
    }
    if (c == KEY_PREV || c == KEY_LEFT) {
      if (view_offset > 0) --view_offset;
      return true;
    }
    if (c == KEY_DOWN) {
      cycleChannelFilter(1);
      return true;
    }
    if (c == KEY_UP) {
      cycleChannelFilter(-1);
      return true;
    }
    if (c == KEY_ENTER) {
      _task->gotoHomeScreen();
      return true;
    }
    return false;
  }
};

void UITask::begin(DisplayDriver* display, SensorManager* sensors, CompanionNodePrefs* node_prefs) {
  _display = display;
  _sensors = sensors;
  if (_display != NULL) {
    _display->setRotationDegrees(node_prefs->display_rotation_degrees);
  }
  _auto_off = millis() + AUTO_OFF_MILLIS;

#if defined(PIN_USER_BTN)
  user_btn.begin();
#endif
#if defined(TBEAM_1W) && defined(WIFI_SSID) && defined(PIN_WIFI_BTN)
  wifi_btn.begin();
#endif
#if defined(PIN_USER_BTN_ANA)
  analog_btn.begin();
#endif

  _node_prefs = node_prefs;

  if (_display != NULL) {
    _display->turnOn();
  }

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
  msg_preview = new MsgPreviewScreen(this);
  setCurrScreen(splash);
}

void UITask::serviceWiFiToggleButton() {
#if defined(TBEAM_1W) && defined(WIFI_SSID) && defined(PIN_WIFI_BTN)
  if (wifi_btn.check() == BUTTON_EVENT_CLICK) {
    const bool enabled = toggleCompanionWiFi();
    showAlert(enabled ? "WiFi: ON" : "WiFi: OFF", 1200);
    _next_refresh = 0;
  }
#endif
}

void UITask::showAlert(const char* text, int duration_millis) {
  strcpy(_alert, text);
  _alert_expiry = millis() + duration_millis;
}

void UITask::showMessages() {
  setCurrScreen(msg_preview);
}

int UITask::getPreviewCount() const {
  return static_cast<const MsgPreviewScreen*>(msg_preview)->messageCount();
}

void UITask::renderMessageSummary(DisplayDriver& display) const {
  static_cast<const MsgPreviewScreen*>(msg_preview)->renderSummary(display);
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
    _deferred_msg_preview = false;
    const bool holding_usb_preview = curr == msg_preview && _msg_preview_until != 0
        && static_cast<int32_t>(millis() - _msg_preview_until) < 0;
    if (!holding_usb_preview) {
      gotoHomeScreen();
    }
  }
}

void UITask::newMsg(uint8_t path_len, const char* from_name, const char* text,
                    int msgcount, int channel_idx,
                    const char* channel_name) {
  _msgcount = msgcount;

  ((MsgPreviewScreen *)msg_preview)
      ->addPreview(path_len, from_name, text, channel_idx, channel_name);
  if (isPairingScreenActive()) {
    // Keep the PIN visible, but retain the preview so it can be shown after
    // pairing completes or the pairing display window expires.
    _deferred_msg_preview = true;
  } else {
    setCurrScreen(msg_preview);
  }

  // A connected app drains the offline queue almost immediately, which calls
  // msgRead(0). While attached to a computer, retain the actual message screen
  // for the configured preview interval even though the app has already
  // consumed the message.
  _msg_preview_until = _board->isUsbHostConnected()
      ? millis() + USB_MESSAGE_PREVIEW_MILLIS
      : 0;

  if (_display != NULL) {
    if (!_display->isOn() && shouldWakeDisplayForMessage()) {
      _display->turnOn();
    }
    if (_display->isOn()) {
    _auto_off = millis() + AUTO_OFF_MILLIS;  // extend the auto-off timer
    _next_refresh = 100;  // trigger refresh
    }
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

void UITask::renderPairingBanner() {
  if (_display == NULL || !isPairingScreenActive()) return;
  const uint32_t pairing_pin = the_mesh.getBLEPin();
  if (pairing_pin == 0) return;

  char prompt[24];
  snprintf(prompt, sizeof(prompt), "PAIR PIN %06u",
           (unsigned int)pairing_pin);
  _display->setColor(UIColor::popup_bkg);
  _display->fillRect(0, 0, _display->width(), 12);
  _display->setColor(UIColor::popup_txt);
  _display->setTextSize(1);
  _display->drawTextCentered(_display->width() / 2, 2, prompt);
}

void UITask::showPairingPin() {
  if (!isBluetoothEnabled()) return;

  const unsigned long now = millis();
  _pairing_screen_until = now + BLE_PAIRING_DISPLAY_MILLIS;
  if (curr == msg_preview && _msgcount > 0) {
    _deferred_msg_preview = true;
  }
  static_cast<HomeScreen*>(home)->showFirstPage();
  setCurrScreen(home);

  if (_display != NULL) {
    if (!_display->isOn()) _display->turnOn();
    _auto_off = now + AUTO_OFF_MILLIS;
    _next_refresh = 0;
  }
}

void UITask::finishPairingScreen(bool timed_out) {
  _pairing_screen_until = 0;

  if (_deferred_msg_preview && _msgcount > 0) {
    _deferred_msg_preview = false;
    setCurrScreen(msg_preview);
    _auto_off = millis() + AUTO_OFF_MILLIS;
  } else {
    _deferred_msg_preview = false;
    gotoHomeScreen();
    if (timed_out && _display != NULL) {
      _display->turnOff();
    } else {
      _auto_off = millis() + AUTO_OFF_MILLIS;
      _next_refresh = 0;
    }
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
}

/*
  hardware-agnostic pre-shutdown activity should be done here
*/
void UITask::shutdown(bool restart){

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
    display.forceFullRefresh();
    display.clear();
    display.endFrame();
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
  serviceWiFiToggleButton();
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
  #ifdef UI_HAS_NAV_INPUT
  if (ev == BUTTON_EVENT_CLICK) {
    c = checkDisplayOn(KEY_ENTER);
  } else if (ev == BUTTON_EVENT_LONG_PRESS) {
    display.turnOff();
  } else if (ev == BUTTON_EVENT_DOUBLE_CLICK) {
    c = handleDoubleClick(KEY_SELECT);
  } else if (ev == BUTTON_EVENT_TRIPLE_CLICK) {
    c = handleTripleClick(KEY_SELECT);
  }
  #else
  if (ev == BUTTON_EVENT_CLICK) {
    c = checkDisplayOn(KEY_NEXT);
  } else if (ev == BUTTON_EVENT_LONG_PRESS) {
    c = (_display != NULL && !_display->isOn())
        ? checkDisplayOn(KEY_ENTER)
        : handleLongPress(KEY_ENTER);
  } else if (ev == BUTTON_EVENT_DOUBLE_CLICK) {
    c = (_display != NULL && !_display->isOn())
        ? checkDisplayOn(KEY_ENTER)
        : handleDoubleClick(KEY_PREV);
  } else if (ev == BUTTON_EVENT_TRIPLE_CLICK) {
    c = (_display != NULL && !_display->isOn())
        ? checkDisplayOn(KEY_ENTER)
        : handleTripleClick(KEY_SELECT);
  }
  #endif  
#endif
#if defined(UI_HAS_ROTARY_INPUT)
  RotaryInputEvent rotaryEv = rotary_input.poll();
  if (c == 0 && _display != NULL && _display->isOn()) {
    if (rotaryEv == RotaryInputEvent::Next) {
      c = KEY_NEXT;
    } else if (rotaryEv == RotaryInputEvent::Prev) {
      c = KEY_PREV;
    }
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
#ifdef HAS_TOUCH
  if (_display != NULL
      && (int32_t)(millis() - next_touch_check) >= 0) {
    next_touch_check = millis() + 25;
    int touch_x = -1;
    int touch_y = -1;
    const bool touched = _display->getTouch(&touch_x, &touch_y);
    mesh::ui::TouchSplitSelector transport_touch_selector = {};
    const mesh::ui::TouchSplitSelector* split_transport_selector = nullptr;
#ifdef COMPANION_EXCLUSIVE_WIFI_BLE
    if (curr == home
        && static_cast<HomeScreen*>(home)->isTransportSelectorPage()) {
      const mesh::ui::CompanionTransportSelectorLayout layout =
          mesh::ui::makeCompanionTransportSelectorLayout(
              _display->width(), _display->height());
      transport_touch_selector = {
          layout.wifi.x,
          layout.wifi.width,
          layout.bluetooth.x,
          layout.bluetooth.width,
          layout.wifi.y,
          layout.wifi.height,
      };
      split_transport_selector = &transport_touch_selector;
    }
#endif
    const mesh::ui::TouchAction action = touch_input.update(
        touched, touch_x, touch_y, _display->width(), _display->height(),
        curr == msg_preview, split_transport_selector);
    const bool on_transport_selector = split_transport_selector != nullptr;
    if (c == 0) {
      switch (action) {
        case mesh::ui::TouchAction::Previous:
          c = checkDisplayOn(KEY_PREV);
          break;
        case mesh::ui::TouchAction::Next:
          c = checkDisplayOn(KEY_NEXT);
          break;
        case mesh::ui::TouchAction::Select:
          c = checkDisplayOn(KEY_ENTER);
          break;
        case mesh::ui::TouchAction::SelectLeft:
          c = checkDisplayOn(KEY_UP);
          break;
        case mesh::ui::TouchAction::SelectRight:
          c = checkDisplayOn(KEY_DOWN);
          break;
        case mesh::ui::TouchAction::VerticalPrevious:
          if (!on_transport_selector) c = checkDisplayOn(KEY_UP);
          break;
        case mesh::ui::TouchAction::VerticalNext:
          if (!on_transport_selector) c = checkDisplayOn(KEY_DOWN);
          break;
        case mesh::ui::TouchAction::None:
          break;
      }
    }
  }
#endif
#if defined(BACKLIGHT_BTN)
  if (millis() > next_backlight_btn_check) {
    bool touch_state = digitalRead(PIN_BUTTON2);
#if defined(DISP_BACKLIGHT)
    digitalWrite(DISP_BACKLIGHT, !touch_state);
#elif defined(EXP_PIN_BACKLIGHT)
    expander.digitalWrite(EXP_PIN_BACKLIGHT, !touch_state);
#endif
    next_backlight_btn_check = millis() + 300;
  }
#endif

  if (isPairingScreenActive()) {
    // Pairing has visual priority over navigation and asynchronous screens.
    static_cast<HomeScreen*>(home)->showFirstPage();
    if (curr != home) setCurrScreen(home);
    c = 0;
  }

  if (c != 0 && curr) {
    curr->handleInput(c);
    _auto_off = millis() + AUTO_OFF_MILLIS;   // extend auto-off timer
    _next_refresh = 100;  // trigger refresh
  }

  userLedHandler();

#ifdef PIN_BUZZER
  if (buzzer.isPlaying())  buzzer.loop();
#endif

  if (curr) curr->poll();

  if (_msgcount == 0 && _msg_preview_until != 0
      && static_cast<int32_t>(millis() - _msg_preview_until) >= 0) {
    _msg_preview_until = 0;
    if (curr == msg_preview) gotoHomeScreen();
  }

  if (_display != NULL && _display->isOn()) {
    if (millis() >= _next_refresh && curr) {
      _display->startFrame();
      int delay_millis = curr->render(*_display);
      renderPairingBanner();
      if (millis() < _alert_expiry) {  // render alert popup
        _display->setTextSize(1);
        int y = _display->height() / 3;
        int p = _display->height() / 32;
        _display->setColor(UIColor::popup_bkg);
        _display->fillRect(p, y, _display->width() - p*2, y);
        _display->setColor(UIColor::popup_txt);  // draw box border
        _display->drawRect(p, y, _display->width() - p*2, y);
        _display->drawTextCentered(_display->width() / 2, y + p*3, _alert);
        _next_refresh = _alert_expiry;   // will need refresh when alert is dismissed
      } else {
        _next_refresh = millis() + delay_millis;
      }
      _display->endFrame();
    }
#if AUTO_OFF_MILLIS > 0
#ifdef KEEP_DISPLAY_ON_USB
    // Opt-in: refresh the auto-off deadline while externally powered, so the
    // timer counts from the moment external power is removed. Off by default
    // because OLED panels burn in quickly; only enable for LCD targets or
    // where the display is replaceable.
    if (board.isExternalPowered()) {
      _auto_off = millis() + AUTO_OFF_MILLIS;
    }
#endif
    if (!isPairingScreenActive() && isDisplayAutoOffDue(_auto_off, AUTO_OFF_MILLIS)) {
      _display->turnOff();
    }
#endif
  }

#ifdef PIN_VIBRATION
  vibration.loop();
#endif

#ifdef AUTO_SHUTDOWN_MILLIVOLTS
  if (millis() > next_batt_chck) {
    uint16_t milliVolts = getBattMilliVolts();
    if (milliVolts > 0 && milliVolts < AUTO_SHUTDOWN_MILLIVOLTS) {
      if(!board.isExternalPowered()) {
        if (_display != NULL) {
          _display->startFrame();
          _display->setTextSize(2);
          _display->setColor(UIColor::warning_txt);
          _display->drawTextCentered(_display->width() / 2, 20, "Low Battery.");
          _display->drawTextCentered(_display->width() / 2, 40, "Shutting Down!");
          _display->endFrame();
          if (_display->isEink() == false) { delay(3000); }
        }
        shutdown();
      }
    }
    next_batt_chck = millis() + 8000;
  }
#endif
}

char UITask::checkDisplayOn(char c) {
  if (_display != NULL) {
    if (!_display->isOn()) {
      _display->turnOn();   // turn display on and consume event
      c = 0;
    }
    _auto_off = millis() + AUTO_OFF_MILLIS;   // extend auto-off timer
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
