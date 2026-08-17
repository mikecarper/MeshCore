#include <Arduino.h>   // needed for PlatformIO
#include <Mesh.h>
#include "MyMesh.h"
#include "CompanionWiFi.h"

#ifdef ESP32_PLATFORM
#include "esp_bt.h"
#if defined(COMPANION_RADIO_FULL)
#include <esp_heap_caps.h>
#endif
#endif
#ifdef TBEAM_1W
#include <esp_random.h>
#endif

// Believe it or not, this std C function is busted on some platforms!
static uint32_t _atoi(const char* sp) {
  uint32_t n = 0;
  while (*sp && *sp >= '0' && *sp <= '9') {
    n *= 10;
    n += (*sp++ - '0');
  }
  return n;
}

// interface manager
#include <helpers/MultiSerialInterface.h>
MultiSerialInterface interface_manager;

// include bluetooth interface
#if defined(BLE_PIN_CODE)
  #ifdef ESP32
    // include esp32 bluetooth interface
    #include <helpers/esp32/SerialBLEInterface.h>
    SerialBLEInterface bluetooth_interface;
  #elif defined(NRF52_PLATFORM)
    // include nrf52 bluetooth interface
    #include <helpers/nrf52/SerialBLEInterface.h>
    SerialBLEInterface bluetooth_interface;
  #else
    #error "SerialBLEInterface is not defined for this platform"
  #endif
#endif

// include wifi interface
#ifdef WIFI_SSID
  #ifndef TCP_PORT
    #define TCP_PORT 5000
  #endif
  #ifdef ESP32
    // include esp32 wifi interface
    #include <helpers/esp32/SerialWifiInterface.h>
    #include <helpers/WiFiSetupPortal.h>
    #include <helpers/WiFiReconnectPolicy.h>
    SerialWifiInterface wifi_interface;
    #ifndef WIFI_PWD
      #define WIFI_PWD ""
    #endif
  #else
    #error "SerialWifiInterface is not defined for this platform"
  #endif
#endif

// include usb interface
#if defined(ENABLE_USB_INTERFACE)
  #include <helpers/ArduinoSerialInterface.h>
  #include <helpers/CLICommandUtils.h>
  static const char USB_TERMINAL_START_TOKEN[] = "+++MESHCORE-TERM-START";
  static const char USB_TERMINAL_STOP_TOKEN[] = "+++MESHCORE-TERM-STOP";
#if defined(NRF52_PLATFORM) && defined(COMPANION_RADIO_FULL) && defined(OTA_FOLDER_SERIAL)
  // motatool sends this command automatically when `serve --serial` opens the
  // port. In Binary mode it is an exact, idle-parser control sequence that
  // hands USB ownership to the host-backed mOTA source.
  static const char USB_MOTA_START_TOKEN[] = "ota folder on";
#endif
  ArduinoSerialInterface usb_serial_interface;
#endif

// include ethernet interface
#if defined(ETHERNET_ENABLED)
  #include <helpers/ethernet/EthernetInterface.h>
  ETHERNET_CLASS ethernet_interface;
#endif

// include hardware serial interface
#if defined(SERIAL_RX)
  #include <helpers/ArduinoSerialInterface.h>
  ArduinoSerialInterface hardware_serial_interface;
  HardwareSerial companion_serial(1);
#endif

// platform file system
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  #include <InternalFileSystem.h>
  #if defined(QSPIFLASH)
    #include <CustomLFS_QSPIFlash.h>
    DataStore store(InternalFS, QSPIFlash, rtc_clock);
  #else
    #if defined(EXTRAFS)
      #include <CustomLFS.h>
      CustomLFS ExtraFS(0xD4000, 0x19000, 128);
      DataStore store(InternalFS, ExtraFS, rtc_clock);
    #else
      DataStore store(InternalFS, rtc_clock);
    #endif
  #endif
#elif defined(RP2040_PLATFORM)
  #include <LittleFS.h>
  DataStore store(LittleFS, rtc_clock);
#elif defined(ESP32)
  #include <SPIFFS.h>
  DataStore store(SPIFFS, rtc_clock);
#endif

/* GLOBAL OBJECTS */
#ifdef DISPLAY_CLASS
  #include "UITask.h"
  UITask ui_task(&board, &interface_manager);
#endif

StdRNG fast_rng;
SimpleMeshTables tables;
MyMesh the_mesh(radio_driver, fast_rng, rtc_clock, tables, store
   #ifdef DISPLAY_CLASS
      , &ui_task
   #endif
);

/* END GLOBAL OBJECTS */

#ifdef TBEAM_1W
static bool companion_radio_available = true;
static unsigned long companion_radio_retry_at = 0;
static const unsigned long COMPANION_RADIO_RETRY_MS = 60000UL;

static void serviceCompanionRadioRecovery() {
  if (companion_radio_available
      || (long)(millis() - companion_radio_retry_at) < 0) return;

  companion_radio_retry_at = millis() + COMPANION_RADIO_RETRY_MS;
  Serial.println("Radio recovery probe starting");
  board.powerCycleRadio();
  if (!radio_init()) {
    Serial.println("Radio recovery probe failed; companion services remain available");
    return;
  }

  companion_radio_available = true;
  the_mesh.activateRadio();
  Serial.println("Radio recovered; mesh transport is active");
}
#endif

#ifdef ESP32_PLATFORM
static int8_t applied_power_saving = -1;
static int8_t attempted_power_saving = -1;
static unsigned long power_saving_retry_at = 0;

static uint32_t companionNominalCpuMhz() {
#ifdef ESP32_CPU_FREQ
  return ESP32_CPU_FREQ;
#else
  return F_CPU / 1000000UL;
#endif
}

static bool applyCompanionPowerSaving(bool enabled) {
  const uint32_t nominal_mhz = companionNominalCpuMhz();
  const uint32_t target_mhz = enabled && nominal_mhz > 80 ? 80 : nominal_mhz;
  if (!setCpuFrequencyMhz(target_mhz)) {
    Serial.printf("Device power saving failed: CPU %lu MHz is unsupported\r\n",
                  (unsigned long)target_mhz);
    return false;
  }

#if defined(BLE_PIN_CODE) && !CONFIG_IDF_TARGET_ESP32C6
  esp_err_t bt_result = enabled ? esp_bt_sleep_enable()
                                : esp_bt_sleep_disable();
  if (bt_result != ESP_OK) {
    Serial.printf("Bluetooth sleep %s failed: %s\r\n",
                  enabled ? "enable" : "disable",
                  esp_err_to_name(bt_result));
  }
#endif

#ifdef WIFI_SSID
#if defined(BLE_PIN_CODE)
  // WiFi modem sleep is required for WiFi/BLE coexistence in Full Companion.
  WiFi.setSleep(true);
#else
  WiFi.setSleep(enabled);
#endif
#endif

  Serial.printf("Device power saving %s: CPU %lu MHz\r\n",
                enabled ? "on" : "off", (unsigned long)target_mhz);
  return true;
}

static void serviceCompanionPowerSaving(bool force = false) {
  const int8_t requested = the_mesh.getNodePrefs()->powersaving_enabled ? 1 : 0;
  if (!force && applied_power_saving == requested) return;

  const unsigned long now = millis();
  if (!force && attempted_power_saving == requested
      && power_saving_retry_at != 0
      && (int32_t)(now - power_saving_retry_at) < 0) {
    return;
  }

  attempted_power_saving = requested;
  if (applyCompanionPowerSaving(requested != 0)) {
    applied_power_saving = requested;
    power_saving_retry_at = 0;
  } else {
    power_saving_retry_at = now + 5000;
    if (power_saving_retry_at == 0) power_saving_retry_at = 1;
  }
}
#endif

#if defined(ENABLE_USB_INTERFACE)
static char usb_terminal_line[MAX_TRANS_UNIT * 2 + 32];
static size_t usb_terminal_line_len = 0;
static bool usb_terminal_discard_line = false;
static bool usb_terminal_disconnect_armed = false;
#if defined(NRF52_PLATFORM) && defined(COMPANION_RADIO_FULL) && defined(OTA_FOLDER_SERIAL)
static bool usb_mota_mode = false;
static char usb_mota_line[32];
static size_t usb_mota_line_len = 0;
static bool usb_mota_disconnect_armed = false;
#endif

static void clearUsbTerminalLine() {
  memset(usb_terminal_line, 0, sizeof(usb_terminal_line));
  usb_terminal_line_len = 0;
}

static void printUsbTerminalInputEcho() {
  const char* password = nullptr;
  size_t visible_len = usb_terminal_line_len;
  if (mesh::cli::parseTerminalArgumentCommand(
          usb_terminal_line, "login", password)
      == mesh::cli::TerminalArgumentCommandMatch::Valid) {
    visible_len = static_cast<size_t>(password - usb_terminal_line);
  }

  if (visible_len > 0) {
    Serial.write(reinterpret_cast<const uint8_t*>(usb_terminal_line),
                 visible_len);
  }
  for (size_t i = visible_len; i < usb_terminal_line_len; i++) {
    Serial.print('*');
  }
}

static void redrawUsbTerminalInput() {
  // The documented picocom `--imap spchex` converts an echoed BS to "[08]".
  // It deliberately leaves CR untouched, so redraw the edited line with CR
  // and printable bytes only. Padding clears a removed tab or wide glyph.
  Serial.print("\r> ");
  printUsbTerminalInputEcho();
  Serial.print("        ");
  Serial.print("\r> ");
  printUsbTerminalInputEcho();
}

static bool isUsbTerminalDataConnected() {
#if defined(RP2040_PLATFORM)
  return (bool)Serial;
#else
  return board.isUsbDataConnected();
#endif
}

static void enterUsbTerminalMode() {
  usb_serial_interface.setPassthroughMode(true);
  clearUsbTerminalLine();
  usb_terminal_discard_line = false;
  usb_terminal_disconnect_armed = isUsbTerminalDataConnected();
  the_mesh.enterTerminalMode();
}

static void leaveUsbTerminalMode(bool acknowledge) {
  if (acknowledge) {
    Serial.print("\r\nOK - Binary mode\r\n");
  }
  the_mesh.exitTerminalMode();
  usb_serial_interface.setPassthroughMode(false);
  clearUsbTerminalLine();
  usb_terminal_discard_line = false;
  usb_terminal_disconnect_armed = false;
}

#if defined(NRF52_PLATFORM) && defined(COMPANION_RADIO_FULL) && defined(OTA_FOLDER_SERIAL)
static void resetUsbMotaMode() {
  usb_mota_mode = false;
  usb_mota_line_len = 0;
  usb_mota_line[0] = 0;
  usb_mota_disconnect_armed = false;
  usb_serial_interface.setPassthroughMode(false);
}

static void leaveUsbMotaMode(bool acknowledge) {
  char reply[160] = {0};
  the_mesh.handleFullOtaCommand("ota folder off", reply, sizeof(reply));
  if (acknowledge) {
    Serial.print("\r\n");
    Serial.print(reply);
    Serial.print("\r\nOK - Binary mode\r\n");
  }
  resetUsbMotaMode();
}

static void enterUsbMotaMode() {
  usb_serial_interface.setPassthroughMode(true);
  usb_mota_mode = true;
  usb_mota_line_len = 0;
  usb_mota_line[0] = 0;
  usb_mota_disconnect_armed = isUsbTerminalDataConnected();

  char reply[160] = {0};
  if (!the_mesh.handleFullOtaCommand("ota folder on", reply, sizeof(reply))
      || strncmp(reply, "ERR", 3) == 0) {
    Serial.print("\r\n");
    Serial.print(reply[0] ? reply : "ERR could not enter mOTA seeder mode");
    Serial.print("\r\n");
    resetUsbMotaMode();
    return;
  }
  Serial.print("\r\n");
  Serial.print(reply);
  Serial.print("\r\n");
}

static void serviceUsbMota() {
  if (isUsbTerminalDataConnected()) {
    usb_mota_disconnect_armed = true;
  } else if (usb_mota_disconnect_armed) {
    leaveUsbMotaMode(false);
    return;
  }

  // SerialMotaSource consumes framed responses synchronously while serving a
  // block. Bytes left here are host control text, notably motatool's automatic
  // `ota folder off` on a clean shutdown.
  while (Serial.available()) {
    int value = Serial.read();
    if (value < 0) break;
    char c = (char)value;
    if (c == '\r' || c == '\n') {
      if (usb_mota_line_len == 0) continue;
      usb_mota_line[usb_mota_line_len] = 0;
      bool stop = strcmp(usb_mota_line, "ota folder off") == 0;
      usb_mota_line_len = 0;
      usb_mota_line[0] = 0;
      if (stop) {
        leaveUsbMotaMode(true);
        return;
      }
      continue;
    }
    if (usb_mota_line_len < sizeof(usb_mota_line) - 1) {
      usb_mota_line[usb_mota_line_len++] = c;
      usb_mota_line[usb_mota_line_len] = 0;
    } else {
      usb_mota_line_len = 0;
      usb_mota_line[0] = 0;
    }
  }
}
#endif

static void serviceUsbTerminal() {
#if defined(NRF52_PLATFORM) && defined(COMPANION_RADIO_FULL) && defined(OTA_FOLDER_SERIAL)
  if (usb_mota_mode) {
    serviceUsbMota();
    return;
  }
#endif
  if (!the_mesh.isTerminalMode()) {
    if (usb_serial_interface.takeControlSequence()) {
      enterUsbTerminalMode();
#if defined(NRF52_PLATFORM) && defined(COMPANION_RADIO_FULL) && defined(OTA_FOLDER_SERIAL)
    } else if (usb_serial_interface.takeSecondaryControlSequence()) {
      enterUsbMotaMode();
#endif
    }
    return;
  }

  if (isUsbTerminalDataConnected()) {
    usb_terminal_disconnect_armed = true;
  } else if (usb_terminal_disconnect_armed) {
    leaveUsbTerminalMode(false);
    return;
  }

  while (Serial.available()) {
    int value = Serial.read();
    if (value < 0) break;
    char c = (char)value;

    if (usb_terminal_discard_line) {
      if (c == '\r' || c == '\n') {
        usb_terminal_discard_line = false;
        Serial.print("> ");
      }
      continue;
    }

    if (c == '\b' || c == 0x7F) {
      if (usb_terminal_line_len > 0) {
        usb_terminal_line_len = mesh::cli::eraseLastTerminalInput(
            usb_terminal_line, usb_terminal_line_len);
        redrawUsbTerminalInput();
      }
      continue;
    }

    if (c == '\r' || c == '\n') {
      if (usb_terminal_line_len == 0) continue;
      Serial.print("\r\n");
      the_mesh.handleTerminalCommand(usb_terminal_line);
      clearUsbTerminalLine();
      Serial.print("> ");
      return; // service at most one command per mesh loop
    }

    if (usb_terminal_line_len >= sizeof(usb_terminal_line) - 1) {
      clearUsbTerminalLine();
      usb_terminal_discard_line = true;
      Serial.print("\r\n  ERROR: command too long\r\n");
      continue;
    }

    usb_terminal_line[usb_terminal_line_len++] = c;
    usb_terminal_line[usb_terminal_line_len] = 0;
    Serial.print(mesh::cli::shouldMaskTerminalInput(usb_terminal_line) ? '*'
                                                                      : c);

    if (strcmp(usb_terminal_line, USB_TERMINAL_STOP_TOKEN) == 0) {
      leaveUsbTerminalMode(true);
      return;
    }
  }
}
#endif

void halt() {
  while (1) ;
}

/* WIFI RECONNECT TRACKERS */
#if defined(ESP32) && defined(WIFI_SSID)
  static const unsigned long WIFI_SETUP_FALLBACK_MS = 120000UL;
  static const char COMPANION_WIFI_SETUP_AP[] = "MeshCore-Setup";
  WiFiReconnectPolicy::Tracker wifi_reconnect_tracker;
  bool wifi_setup_attempted = false;
  unsigned long last_wifi_setup_attempt = 0;
  bool wifi_setup_recovery_mode = false;
  static char configured_wifi_ssid[32];
  static char configured_wifi_password[64];
  static bool companion_wifi_has_credentials = false;
  static bool companion_wifi_requested = true;
  static bool companion_wifi_active = false;
  static bool companion_wifi_disable_in_progress = false;
  static bool companion_wifi_services_stopped = false;

  static void loadCompanionWiFiCredentials() {
    companion_wifi_has_credentials = WiFiSetupPortal::loadStoredCredentials(
        configured_wifi_ssid, sizeof(configured_wifi_ssid),
        configured_wifi_password, sizeof(configured_wifi_password));
    if (!companion_wifi_has_credentials
        && !WiFiSetupPortal::isPlaceholderSSID(WIFI_SSID)) {
      strncpy(configured_wifi_ssid, WIFI_SSID, sizeof(configured_wifi_ssid) - 1);
      configured_wifi_ssid[sizeof(configured_wifi_ssid) - 1] = '\0';
      strncpy(configured_wifi_password, WIFI_PWD, sizeof(configured_wifi_password) - 1);
      configured_wifi_password[sizeof(configured_wifi_password) - 1] = '\0';
      companion_wifi_has_credentials = true;
    }
  }

  bool isCompanionWiFiEnabled() {
    return companion_wifi_requested;
  }

  bool toggleCompanionWiFi() {
    companion_wifi_requested = !companion_wifi_requested;
    the_mesh.getNodePrefs()->wifi_enabled = companion_wifi_requested ? 1 : 0;
    the_mesh.savePrefs();
    if (!companion_wifi_requested && companion_wifi_active) {
      companion_wifi_disable_in_progress = true;
    }
    WIFI_DEBUG_PRINTLN("BOOT/GPIO 0 click requested WiFi %s",
                       companion_wifi_requested ? "on" : "off");
    return companion_wifi_requested;
  }

  #if defined(WITH_WEBCONFIG) && defined(DISPLAY_CLASS)
    static DisplayDriver* companion_setup_display = nullptr;
    static unsigned long companion_setup_display_refresh = 0;

    static void renderCompanionSetupDisplay() {
      if (!companion_setup_display
          || static_cast<int32_t>(millis() - companion_setup_display_refresh) < 0) return;
      companion_setup_display_refresh = millis() + 1000;

      companion_setup_display->turnOn();
      companion_setup_display->startFrame();
      companion_setup_display->setTextSize(1);
      companion_setup_display->setColor(UIColor::primary_txt);

      char setup_ssid[33] = {0};
      char setup_ip[16] = {0};
      if (WebConfigServer::getSetupInfo(setup_ssid, sizeof(setup_ssid),
                                        setup_ip, sizeof(setup_ip))) {
        companion_setup_display->drawTextCentered(
            companion_setup_display->width() / 2, 0, "WebUI setup");
        companion_setup_display->setCursor(0, 14);
        companion_setup_display->print("Join open WiFi:");
        companion_setup_display->drawTextEllipsized(
            0, 25, companion_setup_display->width(), setup_ssid);
        companion_setup_display->setCursor(0, 39);
        companion_setup_display->print("Open in browser:");
        companion_setup_display->drawTextCentered(
            companion_setup_display->width() / 2, 51, setup_ip);
      } else if (WiFi.status() == WL_CONNECTED) {
        companion_setup_display->drawTextCentered(
            companion_setup_display->width() / 2, 0, "WebUI");
        companion_setup_display->setCursor(0, 14);
        companion_setup_display->print("Join WiFi:");
        companion_setup_display->drawTextEllipsized(
            0, 25, companion_setup_display->width(), configured_wifi_ssid);
        companion_setup_display->setCursor(0, 39);
        companion_setup_display->print("Open in browser:");
        const String ip = WiFi.localIP().toString();
        companion_setup_display->drawTextCentered(
            companion_setup_display->width() / 2, 51, ip.c_str());
      } else {
        companion_setup_display->drawTextCentered(
            companion_setup_display->width() / 2, 8, "WiFi connecting");
        companion_setup_display->setCursor(0, 25);
        companion_setup_display->print("SSID:");
        companion_setup_display->drawTextEllipsized(
            0, 38, companion_setup_display->width(), configured_wifi_ssid);
        companion_setup_display->drawTextCentered(
            companion_setup_display->width() / 2, 52, "Please wait...");
      }
      companion_setup_display->endFrame();
    }
  #endif

  static bool saveCompanionWiFi(void*, const char* ssid, const char* password) {
    if (!WiFiSetupPortal::saveStoredCredentials(ssid, password)) return false;
    strncpy(configured_wifi_ssid, ssid, sizeof(configured_wifi_ssid) - 1);
    configured_wifi_ssid[sizeof(configured_wifi_ssid) - 1] = '\0';
    strncpy(configured_wifi_password, password ? password : "", sizeof(configured_wifi_password) - 1);
    configured_wifi_password[sizeof(configured_wifi_password) - 1] = '\0';
    companion_wifi_has_credentials = true;
    return true;
  }
#endif

/* WIFI OTA CONSOLE - a tiny text CLI for OTA over WiFi. Connect with e.g. `nc <ip> 5002` and type
   `ota status` / `ota ls` / `ota announce` / ... - one client at a time, on a DEDICATED port separate from
   the companion (5000) and the seeder (5001). Full companions also accept `tempradio ...` and
   `normalradio` here so a host can run an end-to-end mOTA source without occupying the binary port. */
#if defined(ESP32) && defined(WIFI_SSID) && defined(ENABLE_OTA)
  #include <helpers/ota/OtaCli.h>          // mesh::ota::handle_ota_command(line, reply, board)
  #include <helpers/esp32/WiFiOtaSeeder.h>
  #ifndef OTA_CONSOLE_TCP_PORT
    #define OTA_CONSOLE_TCP_PORT 5002
  #endif
  static WiFiServer ota_console_server(OTA_CONSOLE_TCP_PORT);
  static WiFiClient ota_console_client;
  static char    ota_console_line[128];
  static uint8_t  ota_console_len = 0;

  static void ota_console_start() {
    ota_console_server.begin();
    WIFI_DEBUG_PRINTLN("OTA console listening on :%d  (nc <ip> %d, type `ota ...`)",
                       OTA_CONSOLE_TCP_PORT, OTA_CONSOLE_TCP_PORT);
  }

  static void ota_console_stop() {
    if (ota_console_client) ota_console_client.stop();
    ota_console_server.end();
    ota_console_len = 0;
  }

  static void ota_console_loop() {
    if (!ota_console_client || !ota_console_client.connected()) {
      WiFiClient c = ota_console_server.available();
      if (c) { ota_console_client = c; ota_console_len = 0;
               ota_console_client.print("OTA console - type `ota ...`");
#if defined(COMPANION_RADIO_FULL)
               ota_console_client.print(" or `tempradio freq,bw,sf,cr,minutes`");
#endif
               ota_console_client.print("\r\n> "); }
      return;
    }
    while (ota_console_client.available()) {
      char ch = (char)ota_console_client.read();
      if (ch == '\r' || ch == '\n') {
        if (ota_console_len == 0) continue;                            // ignore blanks / the CRLF pair
        ota_console_line[ota_console_len] = 0;
        char reply[160]; reply[0] = 0;
#if defined(COMPANION_RADIO_FULL)
        if (!the_mesh.handleFullOtaCommand(ota_console_line, reply, sizeof(reply)))
          strcpy(reply, "supported: ota ... | tempradio ... | normalradio");
#else
        if (!mesh::ota::handle_ota_command(ota_console_line, reply, board))
          strcpy(reply, "only `ota ...` commands are supported on this console");
#endif
        ota_console_client.print("  -> "); ota_console_client.print(reply); ota_console_client.print("\r\n> ");
        ota_console_len = 0;
      } else if (ota_console_len < sizeof(ota_console_line) - 1) {
        ota_console_line[ota_console_len++] = ch;
      }
    }
  }
#endif

#if defined(ESP32) && defined(WIFI_SSID)
  static void resetCompanionWiFiRecoveryState() {
    wifi_reconnect_tracker = WiFiReconnectPolicy::Tracker();
    wifi_setup_attempted = false;
    last_wifi_setup_attempt = 0;
    wifi_setup_recovery_mode = false;
  }

  static void startCompanionWiFi() {
    if (companion_wifi_active) return;

    board.setInhibitSleep(true);
    WiFi.setAutoReconnect(true);
    resetCompanionWiFiRecoveryState();

    if (companion_wifi_has_credentials) {
      WiFi.mode(WIFI_STA);
      wifi_reconnect_tracker.noteDisconnected(millis());
      WiFi.begin(configured_wifi_ssid, configured_wifi_password);
    }
#ifndef WITH_WEBCONFIG
    else if (!wifiSetupPortal().begin(COMPANION_WIFI_SETUP_AP,
                                      saveCompanionWiFi, nullptr)) {
      WIFI_DEBUG_PRINTLN("WiFi setup: could not start setup portal");
    }
#endif
#ifdef WITH_WEBCONFIG
    if (WebConfigServer::loadEnabled(true)) {
      char web_reply[160];
      the_mesh.startWebConfig(!companion_wifi_has_credentials, web_reply);
      WIFI_DEBUG_PRINTLN("%s", web_reply);
    }
#endif

#if defined(BLE_PIN_CODE)
    WiFi.setSleep(true);
#else
    WiFi.setSleep(false);
#endif
    wifi_interface.begin(TCP_PORT);
    wifi_interface.enable();
#ifdef ENABLE_OTA
    ota_console_start();
#endif
    companion_wifi_active = true;
    companion_wifi_services_stopped = false;
    WIFI_DEBUG_PRINTLN("WiFi enabled by BOOT/GPIO 0 control");
  }

  static void stopCompanionWiFiServices() {
    if (companion_wifi_services_stopped) return;
    wifi_interface.end();
#ifdef ENABLE_OTA
    ota_console_stop();
    mesh::ota::WiFiOtaSeeder::stop();
#endif
#ifdef WITH_MQTT_BRIDGE
    the_mesh.stopMQTT();
#endif
    companion_wifi_services_stopped = true;
  }

  static bool finishStoppingCompanionWiFi() {
    stopCompanionWiFiServices();
#ifdef WITH_WEBCONFIG
    if (the_mesh.isWebConfigActiveOrStopping()) {
      the_mesh.stopWebConfig();
      return false;
    }
#else
    if (wifiSetupPortal().isActive()) {
      wifiSetupPortal().stop();
      return false;
    }
    if (wifiSetupPortal().isStopping()) return false;
#endif

    WiFi.setAutoReconnect(false);
    WiFi.disconnect(true, false);
    WiFi.mode(WIFI_OFF);
    board.setInhibitSleep(false);
    resetCompanionWiFiRecoveryState();
    companion_wifi_active = false;
    WIFI_DEBUG_PRINTLN("WiFi radio disabled by BOOT/GPIO 0 control");
    return true;
  }

  static void serviceCompanionWiFiState() {
    if (!companion_wifi_requested && companion_wifi_active) {
      companion_wifi_disable_in_progress = true;
    }
    if (companion_wifi_disable_in_progress) {
      if (!finishStoppingCompanionWiFi()) return;
      companion_wifi_disable_in_progress = false;
    }
    if (companion_wifi_requested && !companion_wifi_active) {
      startCompanionWiFi();
    }
  }
#endif

#if defined(BLE_PIN_CODE)
  static bool companion_bluetooth_initialized = false;
#if defined(ESP32) && defined(COMPANION_RADIO_FULL) && defined(WIFI_SSID)
  static uint32_t companion_bluetooth_start_at = 0;
#endif

  static void startCompanionBluetooth() {
    if (companion_bluetooth_initialized) return;

    Serial.println("Companion: starting Bluetooth");
    bluetooth_interface.begin(BLE_NAME_PREFIX, the_mesh.getNodePrefs()->node_name,
                              the_mesh.getBLEPin());
    if (!interface_manager.addInterface(InterfaceType::Bluetooth,
                                        &bluetooth_interface)) {
      Serial.println("Companion: no interface slot available for Bluetooth");
      return;
    }
    companion_bluetooth_initialized = true;
    if (interface_manager.isEnabled()) bluetooth_interface.enable();
  }

  static void serviceDeferredCompanionBluetooth() {
    if (companion_bluetooth_initialized) return;
#if defined(ESP32) && defined(COMPANION_RADIO_FULL) && defined(WIFI_SSID)
    if (companion_bluetooth_start_at == 0
        || (int32_t)(millis() - companion_bluetooth_start_at) < 0) return;
#endif
    startCompanionBluetooth();
  }
#endif

#if defined(ESP32_PLATFORM) && defined(COMPANION_RADIO_FULL)
  static void logFullCompanionMemory(const char* stage) {
    Serial.printf(
        "Full Companion memory %s: heap=%u largest_internal=%u psram_free=%u/%u\r\n",
        stage, (unsigned)ESP.getFreeHeap(),
        (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
        (unsigned)ESP.getFreePsram(), (unsigned)ESP.getPsramSize());
  }
#endif

void setup() {
  Serial.begin(115200);
  board.begin();

#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.begin();
#endif

#ifdef DISPLAY_CLASS
  DisplayDriver* disp = NULL;
  if (display.begin()) {
    disp = &display;
  #if defined(ESP32) && defined(WIFI_SSID) && defined(WITH_WEBCONFIG)
    companion_setup_display = disp;
  #endif
    disp->startFrame();
  #ifdef ST7789
    disp->setTextSize(2);
  #endif
    disp->drawTextCentered(disp->width() / 2, 28, "Loading...");
    disp->endFrame();
  }
#endif

  int radioinit_attempts = 0;
  bool radio_available = false;
  while (!(radio_available = radio_init())) {
    ++radioinit_attempts;
    MESH_DEBUG_PRINTLN("Radio init failed! (attempt %d)", radioinit_attempts);
    if (radioinit_attempts >= 3) {
#if defined(TBEAM_1W)
      // Continue into a recovery-capable Companion instead of trapping native
      // USB in a reboot loop. The main loop retries the radio independently.
      Serial.println("Radio unavailable; starting display, USB, BLE, and WiFi recovery services");
      break;
#else
      MESH_DEBUG_PRINTLN("Radio init failed 3x - rebooting");
      board.reboot();
#endif
    }
    delay(500);
  }

#ifdef TBEAM_1W
  companion_radio_available = radio_available;
  companion_radio_retry_at = millis() + COMPANION_RADIO_RETRY_MS;
  fast_rng.begin(radio_available ? radio_driver.getRngSeed() : esp_random());
#ifdef DISPLAY_CLASS
  if (!radio_available && disp != NULL) {
    disp->startFrame();
    disp->drawTextCentered(disp->width() / 2, 20, "Radio unavailable");
    disp->drawTextCentered(disp->width() / 2, 40, "Starting WiFi...");
    disp->endFrame();
  }
#endif
#else
  fast_rng.begin(radio_driver.getRngSeed());
#endif

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  InternalFS.begin();
  #if defined(QSPIFLASH)
    if (!QSPIFlash.begin()) {
      // debug output might not be available at this point, might be too early. maybe should fall back to InternalFS here?
      MESH_DEBUG_PRINTLN("CustomLFS_QSPIFlash: failed to initialize");
#if defined(NRF52_PLATFORM)
      // A failed nrfx QSPI init can leave its IRQ pending/enabled.  That IRQ
      // storm starves BLE and the main loop even though this build can fall
      // back to internal storage.  Fully release the peripheral on failure.
      NVIC_DisableIRQ(QSPI_IRQn);
      NVIC_ClearPendingIRQ(QSPI_IRQn);
      NRF_QSPI->TASKS_DEACTIVATE = 1;
      NRF_QSPI->ENABLE = QSPI_ENABLE_ENABLE_Disabled;
#endif
      store.disableSecondaryFS();
    } else {
      MESH_DEBUG_PRINTLN("CustomLFS_QSPIFlash: initialized successfully");
    }
  #else
  #if defined(EXTRAFS)
      ExtraFS.begin();
  #endif
  #endif
  store.begin();
  the_mesh.begin(
    #ifdef DISPLAY_CLASS
        disp != NULL
    #else
        false
    #endif
        ,
        radio_available
  );
#elif defined(RP2040_PLATFORM)
  LittleFS.begin();
  store.begin();
  the_mesh.begin(
    #ifdef DISPLAY_CLASS
        disp != NULL
    #else
        false
    #endif
        ,
        radio_available
  );
#elif defined(ESP32)
  SPIFFS.begin(true);
  store.begin();
  the_mesh.begin(
    #ifdef DISPLAY_CLASS
        disp != NULL
    #else
        false
    #endif
        ,
        radio_available
  );
#else
  #error "need to define filesystem"
#endif

// Load WiFi state before bringing up either wireless stack.
#ifdef WIFI_SSID
  loadCompanionWiFiCredentials();
#endif

#if defined(ESP32_PLATFORM) && defined(COMPANION_RADIO_FULL)
  logFullCompanionMemory("before interfaces");
#endif

// add wifi interface
#ifdef WIFI_SSID
  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info){
      if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
          if (companion_wifi_requested) {
            WIFI_DEBUG_PRINTLN("WiFi disconnected; automatic recovery is active");
          }
      } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
          WIFI_DEBUG_PRINTLN("connected! IP %s  (companion app on :%d)",
                             WiFi.localIP().toString().c_str(), TCP_PORT);
      }
  });

  interface_manager.addInterface(InterfaceType::WiFi, &wifi_interface);
  companion_wifi_requested = the_mesh.getNodePrefs()->wifi_enabled != 0;
  if (companion_wifi_requested) {
    startCompanionWiFi();
  } else {
    WiFi.setAutoReconnect(false);
    WiFi.mode(WIFI_OFF);
    board.setInhibitSleep(false);
    WIFI_DEBUG_PRINTLN("WiFi remains off from the saved BOOT/GPIO 0 setting");
  }
#endif

#if defined(ESP32_PLATFORM) && defined(COMPANION_RADIO_FULL)
  logFullCompanionMemory("after WiFi start");
#endif

// The Full ESP32 profile starts WiFi/WebConfig first, gives its AP/server two
// seconds to settle and reserve heap, then starts BLE from loop(). Other BLE
// companions keep their existing immediate startup.
#if defined(BLE_PIN_CODE)
  #if defined(ESP32) && defined(COMPANION_RADIO_FULL) && defined(WIFI_SSID)
    companion_bluetooth_start_at = millis() + 2000UL;
    if (companion_bluetooth_start_at == 0) companion_bluetooth_start_at = 1;
    Serial.println("Companion: Bluetooth starts in 2 seconds");
  #else
    startCompanionBluetooth();
  #endif
#endif

// add usb interface
#if defined(ENABLE_USB_INTERFACE)
#if defined(NRF52_PLATFORM) && defined(COMPANION_RADIO_FULL) && defined(OTA_FOLDER_SERIAL)
  usb_serial_interface.begin(Serial, USB_TERMINAL_START_TOKEN, USB_MOTA_START_TOKEN);
#else
  usb_serial_interface.begin(Serial, USB_TERMINAL_START_TOKEN);
#endif
  interface_manager.addInterface(InterfaceType::USB, &usb_serial_interface);
#endif

// add ethernet interface
#if defined(ETHERNET_ENABLED)
  ethernet_interface.begin();
  interface_manager.addInterface(InterfaceType::Ethernet, &ethernet_interface);
#endif

// add hardware serial interface
#if defined(SERIAL_RX)
  companion_serial.setPins(SERIAL_RX, SERIAL_TX);
  companion_serial.begin(115200);
  hardware_serial_interface.begin(companion_serial);
  interface_manager.addInterface(InterfaceType::HardwareSerial, &hardware_serial_interface);
#endif

  the_mesh.startInterface(interface_manager);
#if defined(ESP32) && defined(WIFI_SSID)
  if (!companion_wifi_requested) wifi_interface.disable();
#endif
  sensors.begin();

#if ENV_INCLUDE_GPS == 1
  // Device power saving applies a 10-minute awake, 5-minute sleep GPS cycle.
  if (sensors.getLocationProvider() != NULL) {
    sensors.getLocationProvider()->setPowerSavingProfile(600, 300);
    sensors.setPowerSavingEnabled(
        the_mesh.getNodePrefs()->powersaving_enabled != 0);
  }
#endif
#if ENV_INCLUDE_GPS == 1
  the_mesh.applyGpsPrefs();
#endif

#ifdef DISPLAY_CLASS
  ui_task.begin(disp, &sensors, the_mesh.getNodePrefs());  // still want to pass this in as dependency, as prefs might be moved
#endif

  board.onBootComplete();

#ifdef ESP32_PLATFORM
  serviceCompanionPowerSaving(true);
#endif
}

void loop() {
#if defined(NRF52_PLATFORM)
  board.feedWatchdog();
#endif
  the_mesh.loop();
#ifdef TBEAM_1W
  serviceCompanionRadioRecovery();
#endif
#if defined(ENABLE_USB_INTERFACE)
  serviceUsbTerminal();
#endif
  interface_manager.loop();
  sensors.loop();
#ifdef DISPLAY_CLASS
  #if defined(ESP32) && defined(WIFI_SSID) && defined(WITH_WEBCONFIG)
  if (isCompanionWiFiEnabled() && (the_mesh.isWebConfigSetupActive()
  #ifdef WITH_MQTT_BRIDGE
      || !the_mesh.isMQTTConfigured()
  #endif
     )) {
  #if defined(TBEAM_1W) && defined(PIN_WIFI_BTN)
    ui_task.serviceWiFiToggleButton();
  #endif
    renderCompanionSetupDisplay();
  } else {
    ui_task.loop();
  }
  #else
  ui_task.loop();
  #endif
#endif
  rtc_clock.tick();
#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.loop();
#endif

#ifdef ESP32_PLATFORM
  serviceCompanionPowerSaving();
#endif

  // USB power alone (for example, a wall charger) must not disable power
  // saving. Stay awake only while a computer has an active USB data session.
  bool can_sleep = the_mesh.getNodePrefs()->powersaving_enabled
      && !the_mesh.hasPendingWork();
#if defined(NRF52_PLATFORM)
  can_sleep = can_sleep && !board.isUsbHostConnected();
#endif
  if (can_sleep) {
#if defined(NRF52_PLATFORM)
    board.sleep(0); // nrf ignores seconds param, sleeps whenever possible
#elif defined(ESP32_PLATFORM)
    vTaskDelay(pdMS_TO_TICKS(10));  // attempt to sleep
#endif
  }

#if defined(ESP32) && defined(WIFI_SSID)
  #ifdef WITH_WEBCONFIG
    the_mesh.serviceWebConfig();
  #endif
  serviceCompanionWiFiState();
  if (companion_wifi_requested && companion_wifi_active) {
  #ifdef ENABLE_OTA
    ota_console_loop();  // service the OTA text console (port 5002)
  #endif
  const unsigned long wifi_now = millis();
  if (WiFi.status() == WL_CONNECTED) {
    wifi_reconnect_tracker.noteConnected();
    wifi_setup_attempted = false;
#ifdef WITH_WEBCONFIG
    // startAutoMode() can raise the setup AP after its own short connection
    // timeout, before the main-loop fallback marks recovery mode. A successful
    // saved-SSID retry should close either kind of recovery AP.
    if (configured_wifi_ssid[0] && the_mesh.isWebConfigSetupActive()
        && (wifi_setup_recovery_mode
            || the_mesh.isWebConfigWiFiRecoveryActive())) {
      WiFi.setAutoReconnect(true);
      the_mesh.stopWebConfig();
      wifi_setup_recovery_mode = false;
    }
#endif
    if (wifi_setup_recovery_mode
#ifdef WITH_WEBCONFIG
        && !the_mesh.isWebConfigSetupActive()
#else
        && !wifiSetupPortal().isActive()
#endif
       ) {
      wifi_setup_recovery_mode = false;
    }
  } else if (configured_wifi_ssid[0]) {
    wifi_reconnect_tracker.noteDisconnected(wifi_now);
    if (!wifi_setup_recovery_mode
#ifdef WITH_WEBCONFIG
        && !the_mesh.isWebConfigSetupActive()
#else
        && !wifiSetupPortal().isActive()
#endif
        && wifi_reconnect_tracker.disconnectedFor(
            wifi_now, WIFI_SETUP_FALLBACK_MS)
        && (!wifi_setup_attempted
            || WiFiReconnectPolicy::elapsedMs(
                wifi_now, last_wifi_setup_attempt) >= WIFI_SETUP_FALLBACK_MS)) {
      wifi_setup_attempted = true;
      last_wifi_setup_attempt = wifi_now;
#ifdef WITH_WEBCONFIG
      if (WebConfigServer::loadEnabled(true)) {
        char web_reply[160];
        if (the_mesh.startWebConfig(true, web_reply)) {
          wifi_setup_recovery_mode = true;
          WIFI_DEBUG_PRINTLN("WiFi unavailable for two minutes; %s", web_reply);
        }
      }
#else
      if (wifiSetupPortal().begin(COMPANION_WIFI_SETUP_AP, saveCompanionWiFi, nullptr)) {
        wifiSetupPortal().configureRecovery(
            configured_wifi_ssid, configured_wifi_password,
            WiFiReconnectPolicy::kRetryIntervalMs,
            WiFiReconnectPolicy::kRetryIntervalMs - WIFI_SETUP_FALLBACK_MS);
        wifi_setup_recovery_mode = true;
        WIFI_DEBUG_PRINTLN("WiFi unavailable for two minutes; setup AP started");
      }
#endif
    }
  }

  // The ESP stack normally reconnects by itself. If it gets wedged after an AP
  // outage, reassert the saved credentials every five minutes. WebConfig's
  // setup AP uses AP+STA mode, so the station retry can run without taking the
  // recovery page down. The legacy portal owns its own identical retry timer.
  const bool reconnect_owned_here =
#ifdef WITH_WEBCONFIG
      !the_mesh.isWebConfigSetupActive()
          || wifi_setup_recovery_mode
          || the_mesh.isWebConfigWiFiRecoveryActive();
#else
      !wifiSetupPortal().isActive();
#endif
  if (configured_wifi_ssid[0] && reconnect_owned_here
      && WiFi.status() != WL_CONNECTED
      && wifi_reconnect_tracker.retryDue(wifi_now)) {
    WIFI_DEBUG_PRINTLN("WiFi still unavailable; retrying saved SSID");
    wifi_reconnect_tracker.noteAttempt(wifi_now);
#ifdef WITH_WEBCONFIG
    if (!the_mesh.isWebConfigSetupActive()) {
      WiFi.mode(WIFI_STA);
      WiFi.setAutoReconnect(true);
    }
#else
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
#endif
    WiFi.disconnect(false, false);
    WiFi.begin(configured_wifi_ssid, configured_wifi_password);
  }
#ifdef WITH_MQTT_BRIDGE
  the_mesh.serviceMQTT(configured_wifi_ssid, configured_wifi_password);
#endif
  }
#endif
#if defined(BLE_PIN_CODE)
  serviceDeferredCompanionBluetooth();
#endif
}
