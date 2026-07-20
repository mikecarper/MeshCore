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

#ifdef ESP32
  #ifdef WIFI_SSID
    #include <helpers/esp32/SerialWifiInterface.h>
    #include <helpers/WiFiSetupPortal.h>
    SerialWifiInterface serial_interface;
    #ifndef TCP_PORT
      #define TCP_PORT 5000
    #endif
    #ifndef WIFI_PWD
      #define WIFI_PWD ""
    #endif
  #elif defined(BLE_PIN_CODE)
    #include <helpers/esp32/SerialBLEInterface.h>
    SerialBLEInterface serial_interface;
  #elif defined(SERIAL_RX)
    #include <helpers/ArduinoSerialInterface.h>
    ArduinoSerialInterface serial_interface;
    HardwareSerial companion_serial(1);
  #else
    #include <helpers/ArduinoSerialInterface.h>
    ArduinoSerialInterface serial_interface;
  #endif
#elif defined(RP2040_PLATFORM)
  //#ifdef WIFI_SSID
  //  #include <helpers/rp2040/SerialWifiInterface.h>
  //  SerialWifiInterface serial_interface;
  //  #ifndef TCP_PORT
  //    #define TCP_PORT 5000
  //  #endif
  // #elif defined(BLE_PIN_CODE)
  //   #include <helpers/rp2040/SerialBLEInterface.h>
  //   SerialBLEInterface serial_interface;
  #if defined(SERIAL_RX)
    #include <helpers/ArduinoSerialInterface.h>
    ArduinoSerialInterface serial_interface;
    HardwareSerial companion_serial(1);
  #else
    #include <helpers/ArduinoSerialInterface.h>
    ArduinoSerialInterface serial_interface;
  #endif
#elif defined(NRF52_PLATFORM)
  #ifdef BLE_PIN_CODE
    #include <helpers/nrf52/SerialBLEInterface.h>
    SerialBLEInterface serial_interface;
  #elif defined(ETHERNET_ENABLED)
    #include <helpers/nrf52/SerialEthernetInterface.h>
    SerialEthernetInterface serial_interface;
  #else
    #include <helpers/ArduinoSerialInterface.h>
    ArduinoSerialInterface serial_interface;
  #endif
#elif defined(STM32_PLATFORM)
  #include <helpers/ArduinoSerialInterface.h>
  ArduinoSerialInterface serial_interface;
#else
  #error "need to define a serial interface"
#endif

/* GLOBAL OBJECTS */
#ifdef DISPLAY_CLASS
  #include "UITask.h"
  UITask ui_task(&board, &serial_interface);
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

/* WIFI OTA SEEDER — relay a host folder of .mota over WiFi (motatool `serve --tcp`), on a DEDICATED port
   separate from the companion (TCP_PORT), so a phone app stays connected while motatool feeds updates. */
#if defined(ESP32) && defined(WIFI_SSID) && defined(ENABLE_OTA)
  #include <helpers/ota/OtaContext.h>
  #include <helpers/ota/MotaSourceSerial.h>
  #include <helpers/ota/FolderMotaStore.h>   // `ota pull <#> folder` destination over this same connection
  #ifndef OTA_SEEDER_TCP_PORT
    #define OTA_SEEDER_TCP_PORT 5001
  #endif
  static WiFiServer ota_seeder_server(OTA_SEEDER_TCP_PORT);
  static WiFiClient ota_seeder_client;                                  // the live seeder connection (reused)
  static mesh::ota::SerialMotaSource ota_seeder_source(ota_seeder_client, 3000);   // SERVE: read folder -> relay
  static mesh::ota::FolderMotaStore  ota_folder_store(ota_seeder_client, 3000);    // PULL: capture .mota -> folder
  static bool ota_seeder_attached = false;

  // Accept one motatool connection at a time. While connected, the same link both SERVES the host folder
  // over LoRa (register it as a source) and is offered as a `folder` PULL destination (`ota pull <#> folder`
  // captures a fetched .mota back to that folder). Drop both the moment the connection closes.
  static void ota_seeder_loop() {
    if (ota_seeder_client && ota_seeder_client.connected()) return;     // still serving the current client
    if (ota_seeder_attached) {                                          // previous client just disconnected
      mesh::ota::ota_ctx().detach_folder();
      mesh::ota::ota_ctx().clear_folder_dest();  // the `folder` pull destination is gone too
      mesh::ota::ota_ctx().manager.announce();   // served set shrank back to our own fw -> re-advertise
      ota_seeder_attached = false;
      WIFI_DEBUG_PRINTLN("OTA seeder: client disconnected, relay stopped");
    }
    WiFiClient c = ota_seeder_server.available();
    if (c) {
      ota_seeder_client = c;                                            // rebind the persistent Stream to it
      if (mesh::ota::ota_ctx().manager.add_source(&ota_seeder_source)) {
        ota_seeder_attached = true;
        char di[24]; snprintf(di, sizeof di, "tcp %s", ota_seeder_client.remoteIP().toString().c_str());
        mesh::ota::ota_ctx().set_folder_dest(&ota_folder_store, di);   // offer `ota pull <#> folder`
        // if a folder pull PAUSED when the link dropped, the host still holds the partial: rescan + resume
        if (mesh::ota::ota_ctx().manager.fetchState() == mesh::ota::OtaManager::PAUSED)
          mesh::ota::ota_ctx().manager.resumeStaged(nullptr);
        mesh::ota::ota_ctx().manager.announce();   // new served set -> advertise the folder's fw to peers
        WIFI_DEBUG_PRINTLN("OTA seeder: client connected (%s) — relay + folder pull-dest ready", di);
      } else {
        ota_seeder_client.stop();                                      // no free source slot
      }
    }
  }
#endif

/* WIFI OTA CONSOLE — a tiny text CLI for OTA over WiFi. A WiFi companion has no serial text console, so
   without this its OTA is only reachable through the phone app. Connect with e.g. `nc <ip> 5002` and type
   `ota status` / `ota ls` / `ota announce` / ... — one client at a time, on a DEDICATED port separate from
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
               ota_console_client.print("OTA console — type `ota ...` (e.g. ota status / ota ls / ota announce)\r\n> "); }
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

  if (!radio_init()) { halt(); }

  fast_rng.begin(radio_driver.getRngSeed());

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  InternalFS.begin();
  #if defined(QSPIFLASH)
    if (!QSPIFlash.begin()) {
      // debug output might not be available at this point, might be too early. maybe should fall back to InternalFS here?
      MESH_DEBUG_PRINTLN("CustomLFS_QSPIFlash: failed to initialize");
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

#ifdef BLE_PIN_CODE
  serial_interface.begin(BLE_NAME_PREFIX, the_mesh.getNodePrefs()->node_name, the_mesh.getBLEPin());
  the_mesh.startInterface(serial_interface);
#elif defined(ETHERNET_ENABLED)
  Serial.print("Waiting for serial to connect...\n");
  unsigned long timeout = millis();
  while (!Serial) {
    if ((millis() - timeout) < 5000) { delay(100); } else { break; }
  }
  Serial.println("Initializing Ethernet adapter...");
  if (serial_interface.begin()) {
    the_mesh.startInterface(serial_interface);
  } else {
    Serial.println("ETH: Init failed, continuing without Ethernet (mesh only)");
  }
#else
  serial_interface.begin(Serial);
  the_mesh.startInterface(serial_interface);
#endif
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

  //#ifdef WIFI_SSID
  //  WiFi.begin(WIFI_SSID, WIFI_PWD);
  //  serial_interface.begin(TCP_PORT);
  // #elif defined(BLE_PIN_CODE)
  //   char dev_name[32+16];
  //   sprintf(dev_name, "%s%s", BLE_NAME_PREFIX, the_mesh.getNodeName());
  //   serial_interface.begin(dev_name, the_mesh.getBLEPin());
  #if defined(SERIAL_RX)
    companion_serial.setPins(SERIAL_RX, SERIAL_TX);
    companion_serial.begin(115200);
    serial_interface.begin(companion_serial);
  #else
    serial_interface.begin(Serial);
  #endif
    the_mesh.startInterface(serial_interface);
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
  serial_interface.begin(TCP_PORT);
  #ifdef ENABLE_OTA
    ota_seeder_server.begin();   // dedicated OTA seeder port for `motatool serve --tcp` (relay over LoRa)
    WIFI_DEBUG_PRINTLN("OTA seeder listening on :%d  (motatool serve --tcp)", OTA_SEEDER_TCP_PORT);
    ota_console_server.begin();  // dedicated OTA text-console port (`nc <ip> 5002` -> `ota ...`)
    WIFI_DEBUG_PRINTLN("OTA console listening on :%d  (nc <ip> %d, type `ota ...`)", OTA_CONSOLE_TCP_PORT, OTA_CONSOLE_TCP_PORT);
  #endif
#elif defined(BLE_PIN_CODE)
  serial_interface.begin(BLE_NAME_PREFIX, the_mesh.getNodePrefs()->node_name, the_mesh.getBLEPin());
#elif defined(SERIAL_RX)
  companion_serial.setPins(SERIAL_RX, SERIAL_TX);
  companion_serial.begin(115200);
  serial_interface.begin(companion_serial);
#else
  serial_interface.begin(Serial);
#endif
  the_mesh.startInterface(serial_interface);
#else
  #error "need to define filesystem"
#endif

  sensors.begin();

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
  the_mesh.loop();
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

#ifdef ETHERNET_ENABLED
  serial_interface.loop();
#endif

  // USB power alone (for example, a wall charger) must not disable power
  // saving. Stay awake only while a computer has an active USB data session.
  if (!board.isUsbHostConnected() && !the_mesh.hasPendingWork()) {
#if defined(NRF52_PLATFORM)
    board.sleep(0); // nrf ignores seconds param, sleeps whenever possible
#elif defined(ESP32_PLATFORM)
    if (!serial_interface.isReadBusy() && !serial_interface.isWriteBusy()) { // BLE is not busy
      vTaskDelay(pdMS_TO_TICKS(10));  // attempt to sleep
    }
#endif
  }

#if defined(ESP32) && defined(WIFI_SSID)
  #ifdef WITH_WEBCONFIG
    the_mesh.serviceWebConfig();
  #endif
  #ifdef ENABLE_OTA
    ota_seeder_loop();   // accept/drop a motatool `serve --tcp` connection on the dedicated seeder port
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
