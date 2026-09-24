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
#if defined(NRF52_PLATFORM)
  #include <helpers/nrf52/InternalPrimaryFsBoot.h>
  #include <helpers/nrf52/RamFallbackFileSystem.h>
#endif

#ifdef ETHERNET_ENABLED
  #define ETHERNET_CLI_BANNER "MeshCore Room Server CLI"
  #include <helpers/nrf52/EthernetCLI.h>
#endif

#ifdef DISPLAY_CLASS
  #include "UITask.h"
#ifdef DISPLAY_TOUCH_TOGGLE
  static UITask ui_task(board, display);
#else
  static UITask ui_task(display);
#endif
  static bool display_ready = false;
#endif

StdRNG fast_rng;
SimpleMeshTables tables;
MyMesh the_mesh(board, radio_driver, *new ArduinoMillis(), fast_rng, rtc_clock, tables);
#include "../InfrastructureWireless.h"

void halt() {
  while (1) ;
}

static char command[MAX_POST_TEXT_LEN+1];
#ifdef ETHERNET_ENABLED
static char ethernet_command[MAX_POST_TEXT_LEN+1];
#endif

// For power saving
unsigned long POWERSAVING_FIRSTSLEEP_SECS = 120; // The first sleep (if enabled) from boot

void setup() {
  mesh::wireless::control().begin(infrastructure_wireless);
  mesh::prepareUsbLoggingPort();
  Serial.begin(115200);
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
  mesh::beginUsbLoggingPort();
#endif
#if MESH_PACKET_LOGGING
  mesh::serialLogBegin();
#endif
  delay(1000);

  board.begin();

#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.begin();
#endif

#ifdef DISPLAY_CLASS
  display_ready = display.begin();
  if (display_ready) {
    display.turnOff();  // Stay dark until the saved display policy is loaded.
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

  FILESYSTEM* fs;
#if defined(NRF52_PLATFORM)
  bool volatile_primary_fs = false;
  mesh::storage::RamFallbackFileSystem* ram_primary_fs = nullptr;
  const auto primary_fs_boot =
      mesh::storage::beginInternalPrimaryFilesystemSafely(InternalFS);
  if (mesh::storage::internalPrimaryFilesystemReady(primary_fs_boot)) {
    fs = &InternalFS;
  } else {
    ram_primary_fs = mesh::storage::createRamFallbackFileSystem();
    if (ram_primary_fs == nullptr) {
      MESH_DEBUG_PRINTLN("InternalFS and RAM fallback initialization failed; rebooting");
      board.reboot();
      return;
    }
    fs = &ram_primary_fs->filesystem();
    volatile_primary_fs = true;
  }
  IdentityStore store(*fs, "");
#elif defined(RP2040_PLATFORM)
  LittleFS.begin();
  fs = &LittleFS;
  IdentityStore store(LittleFS, "/identity");
  store.begin();
#elif defined(ESP32)
  SPIFFS.begin(true);
  fs = &SPIFFS;
  IdentityStore store(SPIFFS, "/identity");
#else
  #error "need to define filesystem"
#endif
#if defined(NRF52_PLATFORM)
  IdentityLoadResult identity_load = volatile_primary_fs
      ? store.loadResult("_main", the_mesh.self_id)
      : mesh::storage::loadIdentityWithPrimaryRecovery(
            InternalFS,
            [&store]() { return store.loadResult("_main", the_mesh.self_id); });
  if (identity_load == IdentityLoadResult::Unreadable) {
    ram_primary_fs = mesh::storage::createRamFallbackFileSystem();
    if (ram_primary_fs != nullptr) {
      fs = &ram_primary_fs->filesystem();
      store.useFileSystem(*fs);
      volatile_primary_fs = true;
      identity_load = IdentityLoadResult::Missing;
    }
  }
#else
  const IdentityLoadResult identity_load =
      store.loadResult("_main", the_mesh.self_id);
#endif
  const bool needs_identity = identity_load == IdentityLoadResult::Missing
      || (identity_load == IdentityLoadResult::Loaded
          && mesh::hasReservedIdentityPrefix(the_mesh.self_id));
  bool identity_ready = identity_load != IdentityLoadResult::Unreadable;
  if (needs_identity) {
    identity_ready = mesh::generateUsableLocalIdentity(the_mesh.self_id, radio_new_identity);
    if (identity_ready) identity_ready = store.saveWithRetry("_main", the_mesh.self_id);
  }

#if defined(ESP32_PLATFORM)
  mesh::discardESP32TrueRandom();
#endif
  if (!identity_ready) {
    MESH_DEBUG_PRINTLN("Identity generation or persistence failed after retries; rebooting");
    board.reboot();
    return;
  }

  Stream& console = mesh::usbConsolePort();
  console.print("Room ID: ");
  mesh::Utils::printHex(console, the_mesh.self_id.pub_key, PUB_KEY_SIZE); console.println();

  command[0] = 0;
#ifdef ETHERNET_ENABLED
  ethernet_command[0] = 0;
#endif

  sensors.begin();

  the_mesh.begin(fs);

#if defined(NRF52_PLATFORM)
  if (volatile_primary_fs) {
    strncpy(the_mesh.getNodePrefs()->node_name,
            mesh::storage::BAD_FILESYSTEM_NODE_NAME,
            sizeof(the_mesh.getNodePrefs()->node_name));
    the_mesh.getNodePrefs()->node_name[
        sizeof(the_mesh.getNodePrefs()->node_name) - 1] = 0;
  }
#endif

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

void loop() {
  mesh::wireless::control().service(millis());
#if defined(NRF52_PLATFORM)
  board.feedWatchdog(the_mesh.getNodePrefs()->system_watchdog_enabled != 0);
#endif
  bool usb_ready = true;
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
  mesh::serviceUsbLoggingPort();
  mesh::serviceUsbTerminalPort();
  if (mesh::takeUsbTerminalSessionReset()) {
    command[0] = 0;
    the_mesh.cancelPendingSerialOutput();
  }
  usb_ready = mesh::tryCompleteUsbTerminalSessionReset();
  usb_ready = usb_ready && !the_mesh.hasPendingSerialOutput()
      && mesh::canAcceptUsbConsoleCommand();
#endif
  Stream& console = mesh::usbConsolePort();
  int len = strlen(command);
  // `command` must stay NUL-terminated within its bounds. If it ever isn't,
  // strlen() above can return >= sizeof(command) and the loop below would then
  // index past the buffer, so clamp defensively.
  if (len >= (int)sizeof(command)) {
    command[0] = 0;
    len = 0;
  }
  while (usb_ready && console.available() && len < sizeof(command)-1) {
    char c = console.read();
    if (c != '\n') {
      command[len++] = c;
      command[len] = 0;
    }
    console.print(c);
  }
  if (len == sizeof(command)-1) {  // buffer full: treat as a completed line
    command[sizeof(command)-2] = '\r';  // place end-of-line marker inside the buffer
    command[sizeof(command)-1] = 0;     // keep the buffer NUL-terminated
  }

  if (len > 0 && command[len - 1] == '\r') {  // received complete line
    command[len - 1] = 0;  // replace newline with C string null terminator
    char reply[160];
    reply[0] = 0;
#ifdef ETHERNET_ENABLED
    if (!ethernet_handle_command(command, reply)) {
      the_mesh.handleUsbCommand(command, reply);
    }
#else
    the_mesh.handleUsbCommand(command, reply);
#endif
    if (reply[0]) {
      console.printf("  -> %s\r\n", reply);
    }

    command[0] = 0;  // reset command buffer
  }

#ifdef ETHERNET_ENABLED
  ethernet_loop_maintain();
  if (ethernet_take_session_reset() || !ethernet_client.connected()) {
    the_mesh.cancelLocalOutput(ethernet_client);
    ethernet_command[0] = 0;
  }
  if (!the_mesh.hasPendingLocalOutput() && ethernet_read_line(ethernet_command, sizeof(ethernet_command))) {
    char reply[160];
    reply[0] = 0;
    if (!ethernet_handle_command(ethernet_command, reply)) {
      the_mesh.handleLocalCommand(ethernet_command, reply, ethernet_client);
    }
    ethernet_send_reply(reply);
    ethernet_command[0] = 0;
  }
#endif

  the_mesh.loop();
  sensors.loop();
#ifdef DISPLAY_CLASS
  if (display_ready) ui_task.loop();
#endif
  rtc_clock.tick();
  board.loop();
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
  mesh::serviceUsbTerminalPort();
#endif
#ifdef TBEAM_1W
  board.updateFanControl();
#endif
#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.loop();
#endif
  bool can_power_save = the_mesh.getNodePrefs()->powersaving_enabled
      && !board.isUsbDataConnected()
      && !mesh::wireless::control().pending();
#if defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) \
    && MOMENTARY_BUTTON_WAKE_FROM_SLEEP \
    && defined(PIN_USER_BTN) && defined(DISPLAY_CLASS)
  can_power_save = can_power_save && !user_btn.needsPolling();
#endif
  if (can_power_save) {
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
#if defined(ESP32_PLATFORM)
  if (!can_power_save && the_mesh.getNodePrefs()->powersaving_enabled) {
    delay(1); // Keep USB and the button wake interval serviced while idle.
  }
#endif
}
