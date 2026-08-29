#include <Arduino.h>   // needed for PlatformIO
#include <Mesh.h>
#include <helpers/IdentityGeneration.h>
#if MESH_PACKET_LOGGING
  #include <helpers/SerialPacketLog.h>
#endif

#include "MyMesh.h"
#if defined(ESP32_PLATFORM)
  #include <helpers/ESP32TrueRandom.h>
#endif
#if defined(ESP32) && MAX_RECENT_REPEATERS > 0
  #include <new>
#endif

#ifdef DISPLAY_CLASS
  #include "UITask.h"
  static UITask ui_task(board, display);
  static bool display_ready = false;
#endif

#ifdef ETHERNET_ENABLED
  #define ETHERNET_CLI_BANNER "MeshCore Repeater CLI"
  #include <helpers/nrf52/EthernetCLI.h>
#endif

StdRNG fast_rng;
#if MAX_RECENT_REPEATERS > 0
  #if defined(ESP32)
// Classic ESP32 has a much smaller link-time DRAM window than its runtime heap
// map. Allocate this large, fixed-capacity history at startup so enabling WiFi
// features (including WebConfig) does not overflow that static DRAM window.
// Global constructors run before setup(), while the heap is still
// unfragmented; a failed allocation safely falls back to no history table.
SimpleMeshTables::RecentRepeaterInfo* recent_repeater_storage =
    new (std::nothrow) SimpleMeshTables::RecentRepeaterInfo[MAX_RECENT_REPEATERS];
SimpleMeshTables tables(recent_repeater_storage,
                        recent_repeater_storage ? MAX_RECENT_REPEATERS : 0);
  #else
SimpleMeshTables::RecentRepeaterInfo recent_repeater_storage[MAX_RECENT_REPEATERS];
SimpleMeshTables tables(recent_repeater_storage, MAX_RECENT_REPEATERS);
  #endif
#else
SimpleMeshTables tables;
#endif

MyMesh the_mesh(board, radio_driver, *new ArduinoMillis(), fast_rng, rtc_clock, tables);

void halt() {
  while (1) ;
}

#if MESH_ENABLE_HOST_CLI
static constexpr size_t LOCAL_SERIAL_COMMAND_MAX =
    mesh::HostCliBridge::SERIAL_REPLY_COMMAND_MAX;
#else
static constexpr size_t LOCAL_SERIAL_COMMAND_MAX = 159U;
#endif
static char command[LOCAL_SERIAL_COMMAND_MAX + 2U];
static bool command_overflow = false;
#ifdef ETHERNET_ENABLED
static char ethernet_command[160];
#endif

// For power saving
unsigned long POWERSAVING_FIRSTSLEEP_SECS = 120; // The first sleep (if enabled) from boot

#if defined(PIN_USER_BTN) && defined(_SEEED_SENSECAP_SOLAR_H_)
static unsigned long userBtnDownAt = 0;
#define USER_BTN_HOLD_OFF_MILLIS 1500
#endif

void setup() {
  Serial.begin(115200);
#if MESH_PACKET_LOGGING
  mesh::serialLogBegin();
#endif
  delay(1000);

  board.begin();

#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.begin();
#endif

#if defined(MESH_DEBUG) && defined(NRF52_PLATFORM)
  // give some extra time for serial to settle so
  // boot debug messages can be seen on terminal
  delay(5000);
#endif

#ifdef DISPLAY_CLASS
  display_ready = display.begin();
  if (display_ready) {
    display.startFrame();
    display.setCursor(0, 0);
    display.print("Please wait...");
    display.endFrame();
  }
#endif

  int radioinit_attempts = 0;
  while (!radio_init()) {
    ++radioinit_attempts;
    MESH_DEBUG_PRINTLN("Radio init failed! (attempt %d)", radioinit_attempts);
    if (radioinit_attempts >= 3) {
#ifdef RECOVERABLE_EXTERNAL_RADIO
      // A remote external-radio node must not churn USB or require a physical
      // power cut merely because its radio is temporarily unavailable. Keep
      // the MCU alive and retry in place; target radio_init() performs the
      // board-specific regulator/reset/wake recovery on each attempt.
      Serial.println("Radio unavailable; retrying in 60 seconds");
      radioinit_attempts = 0;
      const uint32_t retry_started = millis();
      while (millis() - retry_started < 60000UL) {
#if defined(NRF52_PLATFORM)
        board.feedWatchdog();
#endif
#ifdef HAS_EXTERNAL_WATCHDOG
        external_watchdog.loop();
#endif
        delay(10);
      }
#else
      MESH_DEBUG_PRINTLN("Radio init failed 3x - rebooting");
      board.reboot();
#endif
    }
    delay(500);
  }

  fast_rng.begin(radio_driver.getRngSeed());

  FILESYSTEM* fs;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  InternalFS.begin();
  fs = &InternalFS;
  IdentityStore store(InternalFS, "");
#elif defined(ESP32)
  SPIFFS.begin(true);
  fs = &SPIFFS;
  IdentityStore store(SPIFFS, "/identity");
#elif defined(RP2040_PLATFORM)
  LittleFS.begin();
  fs = &LittleFS;
  IdentityStore store(LittleFS, "/identity");
  store.begin();
#else
  #error "need to define filesystem"
#endif
  const bool needs_identity = !store.load("_main", the_mesh.self_id)
      || mesh::hasReservedIdentityPrefix(the_mesh.self_id);
  bool identity_ready = true;
  if (needs_identity) {
    MESH_DEBUG_PRINTLN("Generating new keypair");
    identity_ready = mesh::generateUsableLocalIdentity(the_mesh.self_id, radio_new_identity);
    if (identity_ready) store.save("_main", the_mesh.self_id);
  }

#if defined(ESP32_PLATFORM)
  mesh::discardESP32TrueRandom();
#endif
  if (!identity_ready) {
    MESH_DEBUG_PRINTLN("Identity generation exhausted all attempts; rebooting");
    board.reboot();
    return;
  }

  // Print the running firmware version at boot so it's visible after an OTA
  // reboot without having to issue `ver` manually.
  Serial.print("Firmware: "); Serial.print(FIRMWARE_VERSION);
  Serial.print(" (built "); Serial.print(FIRMWARE_BUILD_DATE); Serial.println(")");

  Serial.print("Repeater ID: ");
  mesh::Utils::printHex(Serial, the_mesh.self_id.pub_key, PUB_KEY_SIZE); Serial.println();

  command[0] = 0;
#ifdef ETHERNET_ENABLED
  ethernet_command[0] = 0;
#endif

#if ENV_INCLUDE_GPS == 1
  if (sensors.getLocationProvider() != NULL) {
    // Keep GPS awake for at most 10 minutes, then asleep for one day.
    sensors.getLocationProvider()->setPowerSavingProfile(600, 86400);
  }
#endif

  sensors.begin();

  the_mesh.begin(fs);

#ifdef DISPLAY_CLASS
  if (display_ready) {
#ifdef WITH_MQTT_BRIDGE
    ui_task.setObserverPrefs(the_mesh.getObserverPrefs());
#endif
#ifdef DISPLAY_ACTIVITY_DASHBOARD
    ui_task.setActivityWindow(the_mesh.getActivityWindow());
#endif
    ui_task.begin(the_mesh.getNodePrefs(), FIRMWARE_BUILD_DATE, FIRMWARE_VERSION);
  }
#endif

#ifdef ETHERNET_ENABLED
  ethernet_start_task();
#endif

  // send out initial zero hop Advertisement to the mesh
#if ENABLE_ADVERT_ON_BOOT == 1
  the_mesh.sendSelfAdvertisement(16000, false);
#endif

  board.onBootComplete();
}

static void __attribute__((noinline)) serviceCommandInterfaces() {
  // Handle Serial CLI
  int len = strlen(command);
  bool line_complete = false;
  bool overlong_line_complete = false;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') continue;
    Serial.print(c);

    if (command_overflow) {
      if (c == '\r') {
        command_overflow = false;
        overlong_line_complete = true;
        break;
      }
      continue;
    }
    if (c == '\r') {
      line_complete = true;
      break;
    }
    if ((size_t)len < LOCAL_SERIAL_COMMAND_MAX) {
      command[len++] = c;
      command[len] = 0;
    } else {
      // Discard the entire record through its delimiter. Never parse a
      // truncated prefix as one command and its tail as a second command.
      command[0] = 0;
      len = 0;
      command_overflow = true;
    }
  }

  if (overlong_line_complete) {
    Serial.print('\n');
    Serial.println("  -> Err - command too long");
    command[0] = 0;
    return;
  }

  if (line_complete) {
    Serial.print('\n');
    char reply[160];
    reply[0] = 0;
#ifdef ETHERNET_ENABLED
    if (!ethernet_handle_command(command, reply)) {
#if MESH_ENABLE_HOST_CLI
      if (!the_mesh.handleHostCliSerialReply(command, reply)) {
        the_mesh.handleCommand(0, command, reply);
      }
#else
      the_mesh.handleCommand(0, command, reply);
#endif
    }
#else
#if MESH_ENABLE_HOST_CLI
    if (!the_mesh.handleHostCliSerialReply(command, reply)) {
      the_mesh.handleCommand(0, command, reply);  // NOTE: there is no sender_timestamp via serial!
    }
#else
    the_mesh.handleCommand(0, command, reply);  // NOTE: there is no sender_timestamp via serial!
#endif
#endif
    if (reply[0]) {
      Serial.print("  -> "); Serial.println(reply);
    }

    command[0] = 0;  // reset command buffer
  }

#ifdef ETHERNET_ENABLED
  ethernet_loop_maintain();
  if (ethernet_read_line(ethernet_command, sizeof(ethernet_command))) {
    char reply[160];
    reply[0] = 0;
    if (!ethernet_handle_command(ethernet_command, reply)) {
      the_mesh.handleCommand(0, ethernet_command, reply);
    }
    ethernet_send_reply(reply);
    ethernet_command[0] = 0;
  }
#endif
}

void loop() {
#if defined(NRF52_PLATFORM)
  board.feedWatchdog(the_mesh.getNodePrefs()->system_watchdog_enabled != 0);
#endif
  serviceCommandInterfaces();

#if defined(PIN_USER_BTN) && defined(_SEEED_SENSECAP_SOLAR_H_) && !defined(DISPLAY_CLASS)
  // Hold the user button to power off the SenseCAP Solar repeater.
  int btnState = digitalRead(PIN_USER_BTN);
  if (btnState == LOW) {
    if (userBtnDownAt == 0) {
      userBtnDownAt = millis();
    } else if ((unsigned long)(millis() - userBtnDownAt) >= USER_BTN_HOLD_OFF_MILLIS) {
      Serial.println("Powering off...");
      board.powerOff();  // does not return
    }
  } else {
    userBtnDownAt = 0;
  }
#endif

  the_mesh.loop();
  sensors.loop();
#ifdef DISPLAY_CLASS
  if (display_ready) ui_task.loop();
#endif
  rtc_clock.tick();

#ifdef TBEAM_1W
  board.updateFanControl();
#endif

#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.loop();
#endif
  if (the_mesh.getNodePrefs()->powersaving_enabled && !board.isUsbDataConnected()) {
    uint32_t sleep_secs = the_mesh.getPowerSaveSleepSeconds(30);
#ifdef HAS_EXTERNAL_WATCHDOG
    if (sleep_secs > 0) external_watchdog.feed();
#endif
#if defined(NRF52_PLATFORM)
    if (sleep_secs > 0) {
      board.sleep(0); // nrf ignores seconds param, sleeps whenever possible
    }
#else
    if (sleep_secs > 0 && the_mesh.millisHasNowPassed(POWERSAVING_FIRSTSLEEP_SECS * 1000)) { // To check if it is time to sleep
      board.sleep(sleep_secs); // Sleep. Wake up for scheduled jobs or when receiving a LoRa packet
    }
#endif
  }

  if (the_mesh.getNodePrefs()->reboot_interval > 0 &&
      the_mesh.millisHasNowPassed(the_mesh.getNodePrefs()->reboot_interval * 3600000)) {
    board.reboot();
  }
}
