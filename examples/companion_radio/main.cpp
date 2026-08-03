#include <Arduino.h>   // needed for PlatformIO
#include <Mesh.h>
#include "MyMesh.h"

#ifdef ESP32_PLATFORM
#include "esp_pm.h"
#include "esp_bt.h"
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

void halt() {
  while (1) ;
}

/* WIFI RECONNECT TRACKERS */
#if defined(ESP32) && defined(WIFI_SSID)
  static const unsigned long WIFI_SETUP_FALLBACK_MS = 120000UL;
  static const char COMPANION_WIFI_SETUP_AP[] = "MeshCore-Setup";
  bool wifi_needs_reconnect = false;
  unsigned long last_wifi_reconnect_attempt = 0;
  unsigned long wifi_disconnected_since = 0;
  bool wifi_setup_recovery_mode = false;
  static char configured_wifi_ssid[32];
  static char configured_wifi_password[64];

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
      companion_setup_display->setColor(DisplayDriver::LIGHT);

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
    return true;
  }
#endif

/* WIFI OTA CONSOLE - a tiny text CLI for OTA over WiFi. A WiFi companion has no serial text console, so
   without this its OTA is only reachable through the phone app. Connect with e.g. `nc <ip> 5002` and type
   `ota status` / `ota ls` / `ota announce` / ... - one client at a time, on a DEDICATED port separate from
   the companion (5000) and the seeder (5001). */
#if defined(ESP32) && defined(WIFI_SSID) && defined(ENABLE_OTA)
  #include <helpers/ota/OtaCli.h>          // mesh::ota::handle_ota_command(line, reply, board)
  #ifndef OTA_CONSOLE_TCP_PORT
    #define OTA_CONSOLE_TCP_PORT 5002
  #endif
  static WiFiServer ota_console_server(OTA_CONSOLE_TCP_PORT);
  static WiFiClient ota_console_client;
  static char    ota_console_line[128];
  static uint8_t  ota_console_len = 0;

  static void ota_console_loop() {
    if (!ota_console_client || !ota_console_client.connected()) {
      WiFiClient c = ota_console_server.available();
      if (c) { ota_console_client = c; ota_console_len = 0;
               ota_console_client.print("OTA console - type `ota ...` (e.g. ota status / ota ls / ota announce)\r\n> "); }
      return;
    }
    while (ota_console_client.available()) {
      char ch = (char)ota_console_client.read();
      if (ch == '\r' || ch == '\n') {
        if (ota_console_len == 0) continue;                            // ignore blanks / the CRLF pair
        ota_console_line[ota_console_len] = 0;
        char reply[160]; reply[0] = 0;
        if (!mesh::ota::handle_ota_command(ota_console_line, reply, board))
          strcpy(reply, "only `ota ...` commands are supported on this console");
        ota_console_client.print("  -> "); ota_console_client.print(reply); ota_console_client.print("\r\n> ");
        ota_console_len = 0;
      } else if (ota_console_len < sizeof(ota_console_line) - 1) {
        ota_console_line[ota_console_len++] = ch;
      }
    }
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
  while (!radio_init()) {
    ++radioinit_attempts;
    MESH_DEBUG_PRINTLN("Radio init failed! (attempt %d)", radioinit_attempts);
    if (radioinit_attempts >= 3) {
      MESH_DEBUG_PRINTLN("Radio init failed 3x - rebooting");
      board.reboot();
    }
    delay(500);
  }

  fast_rng.begin(radio_driver.getRngSeed());

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
  );
#else
  #error "need to define filesystem"
#endif

// add bluetooth interface
#if defined(BLE_PIN_CODE)
  bluetooth_interface.begin(BLE_NAME_PREFIX, the_mesh.getNodePrefs()->node_name, the_mesh.getBLEPin());
  interface_manager.addInterface(InterfaceType::Bluetooth, &bluetooth_interface);
#endif

// add wifi interface
#ifdef WIFI_SSID
  board.setInhibitSleep(true);   // prevent sleep when WiFi is active
  WiFi.setAutoReconnect(true);

  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info){
      if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
          WIFI_DEBUG_PRINTLN("WiFi disconnected. Flagging for reconnect...");
          wifi_needs_reconnect = true;
          if (wifi_disconnected_since == 0) {
            wifi_disconnected_since = millis() ? millis() : 1;
          }
      } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
          WIFI_DEBUG_PRINTLN("connected! IP %s  (companion app on :%d)",
                             WiFi.localIP().toString().c_str(), TCP_PORT);
          wifi_needs_reconnect = false;
          wifi_disconnected_since = 0;
      }
  });

  bool have_wifi = WiFiSetupPortal::loadStoredCredentials(
      configured_wifi_ssid, sizeof(configured_wifi_ssid),
      configured_wifi_password, sizeof(configured_wifi_password));
  if (!have_wifi && !WiFiSetupPortal::isPlaceholderSSID(WIFI_SSID)) {
    strncpy(configured_wifi_ssid, WIFI_SSID, sizeof(configured_wifi_ssid) - 1);
    configured_wifi_ssid[sizeof(configured_wifi_ssid) - 1] = '\0';
    strncpy(configured_wifi_password, WIFI_PWD, sizeof(configured_wifi_password) - 1);
    configured_wifi_password[sizeof(configured_wifi_password) - 1] = '\0';
    have_wifi = true;
  }

  if (have_wifi) {
    WiFi.mode(WIFI_STA);
    wifi_disconnected_since = millis() ? millis() : 1;
    WiFi.begin(configured_wifi_ssid, configured_wifi_password);
  }
#ifndef WITH_WEBCONFIG
  else if (!wifiSetupPortal().begin(COMPANION_WIFI_SETUP_AP, saveCompanionWiFi, nullptr)) {
    WIFI_DEBUG_PRINTLN("WiFi setup: could not start setup portal");
  }
#endif
  #ifdef WITH_WEBCONFIG
    // WiFi companions expose the shared WebUI by default. With no stored
    // credentials it opens the captive setup AP; otherwise it waits for the
    // station connection and serves the same page on the LAN.
    if (WebConfigServer::loadEnabled(true)) {
      char web_reply[160];
      the_mesh.startWebConfig(!have_wifi, web_reply);
      WIFI_DEBUG_PRINTLN("%s", web_reply);
    }
  #endif
  // Disable WiFi modem power-save after starting WebConfig: that startup may
  // restore a saved ESP-IDF WiFi power policy. Companion radios must keep
  // modem sleep disabled because its pauses can stall SX1262 SPI/DIO service.
  WiFi.setSleep(false);
  wifi_interface.begin(TCP_PORT);
  interface_manager.addInterface(InterfaceType::WiFi, &wifi_interface);
  #ifdef ENABLE_OTA
    ota_console_server.begin();  // dedicated OTA text-console port (`nc <ip> 5002` -> `ota ...`)
    WIFI_DEBUG_PRINTLN("OTA console listening on :%d  (nc <ip> %d, type `ota ...`)", OTA_CONSOLE_TCP_PORT, OTA_CONSOLE_TCP_PORT);
  #endif
#endif

// add usb interface
#if defined(ENABLE_USB_INTERFACE)
  usb_serial_interface.begin(Serial);
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
  sensors.begin();

#if ENV_INCLUDE_GPS == 1 && defined(BLE_PIN_CODE)
  // BLE companions keep GPS duty cycling enabled by default: at most 10
  // minutes awake, followed by 5 minutes asleep.
  if (sensors.getLocationProvider() != NULL) {
    sensors.getLocationProvider()->setPowerSavingProfile(600, 300);
    sensors.setPowerSavingEnabled(true);
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
#if !CONFIG_IDF_TARGET_ESP32C6
  // Enable BLE sleep
  esp_err_t errBLESleep = esp_bt_sleep_enable();
  if (errBLESleep == ESP_OK) {
    Serial.println("Bluetooth sleep enabled successfully");
  } else {
    Serial.printf("Bluetooth sleep enable failed: %s\n", esp_err_to_name(errBLESleep));
  }
#endif

#if CONFIG_IDF_TARGET_ESP32C3
  esp_pm_config_esp32c3_t pm_config;
#elif CONFIG_IDF_TARGET_ESP32S3
  esp_pm_config_esp32s3_t pm_config;
#elif CONFIG_IDF_TARGET_ESP32
  esp_pm_config_esp32_t pm_config;
#elif CONFIG_IDF_TARGET_ESP32C6
  esp_pm_config_t pm_config;
#endif

  // Configure Power Management
  pm_config = { .max_freq_mhz = 80, .min_freq_mhz = 40, .light_sleep_enable = true };
  esp_err_t errPM = esp_pm_configure(&pm_config);
  if (errPM == ESP_OK) {
    Serial.println("Power Management configured successfully");
  } else {
    Serial.printf("Power Management failed to configure: %d\r\n", errPM);
  }
#endif
}

void loop() {
#if defined(NRF52_PLATFORM)
  board.feedWatchdog();
#endif
  the_mesh.loop();
  interface_manager.loop();
  sensors.loop();
#ifdef DISPLAY_CLASS
  #if defined(ESP32) && defined(WIFI_SSID) && defined(WITH_WEBCONFIG)
  if (the_mesh.isWebConfigSetupActive()
  #ifdef WITH_MQTT_BRIDGE
      || !the_mesh.isMQTTConfigured()
  #endif
     ) {
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

  // USB power alone (for example, a wall charger) must not disable power
  // saving. Stay awake only while a computer has an active USB data session.
  bool can_sleep = !the_mesh.hasPendingWork();
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
  #ifdef ENABLE_OTA
    ota_console_loop();  // service the OTA text console (port 5002)
  #endif
  if (WiFi.status() == WL_CONNECTED) {
    wifi_disconnected_since = 0;
#ifdef WITH_WEBCONFIG
    if (wifi_setup_recovery_mode && the_mesh.isWebConfigSetupActive()) {
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
    unsigned long now = millis();
    if (wifi_disconnected_since == 0) wifi_disconnected_since = now ? now : 1;
    if (!wifi_setup_recovery_mode
#ifdef WITH_WEBCONFIG
        && !the_mesh.isWebConfigSetupActive()
#else
        && !wifiSetupPortal().isActive()
#endif
        && now - wifi_disconnected_since >= WIFI_SETUP_FALLBACK_MS) {
#ifdef WITH_WEBCONFIG
      if (WebConfigServer::loadEnabled(true)) {
        char web_reply[160];
        if (the_mesh.startWebConfig(true, web_reply)) {
          wifi_setup_recovery_mode = true;
          WIFI_DEBUG_PRINTLN("WiFi unavailable for two minutes; %s", web_reply);
        } else {
          wifi_disconnected_since = now;
        }
      }
#else
      if (wifiSetupPortal().begin(COMPANION_WIFI_SETUP_AP, saveCompanionWiFi, nullptr)) {
        wifiSetupPortal().configureRecovery(
            configured_wifi_ssid, configured_wifi_password, WIFI_SETUP_FALLBACK_MS);
        wifi_setup_recovery_mode = true;
        WIFI_DEBUG_PRINTLN("WiFi unavailable for two minutes; setup AP started");
      } else {
        // Avoid retrying portal creation on every pass through loop().
        wifi_disconnected_since = now;
      }
#endif
    }
  }
  // Safely attempt to reconnect every 10 seconds if flagged
  if (
#ifdef WITH_WEBCONFIG
      !the_mesh.isWebConfigSetupActive()
#else
      !wifiSetupPortal().isActive()
#endif
      && wifi_needs_reconnect
      && (millis() - last_wifi_reconnect_attempt > 10000)) {
    WIFI_DEBUG_PRINTLN("Attempting manual WiFi reconnect...");
    WiFi.disconnect();
    WiFi.reconnect();
    last_wifi_reconnect_attempt = millis();
  }
#ifdef WITH_MQTT_BRIDGE
  the_mesh.serviceMQTT(configured_wifi_ssid, configured_wifi_password);
#endif
#endif
}
