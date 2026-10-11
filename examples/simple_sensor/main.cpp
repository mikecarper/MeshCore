#include "SensorMesh.h"
#include <math.h>
#include <helpers/IdentityGeneration.h>
#include <helpers/ui/StartupScreen.h>
#include <helpers/ui/DisplayPowerSettings.h>
#if defined(NRF52_PLATFORM)
  #include <helpers/nrf52/InternalPrimaryFsBoot.h>
  #include <helpers/nrf52/RamFallbackFileSystem.h>
#endif
#include <helpers/UsbLogging.h>
#include <helpers/UsbLoggingWatchdog.h>
#include <helpers/UsbLoggingClientActivity.h>
#if defined(NRF52_PLATFORM) && MESH_ENABLE_SENSOR_TRACKER
#include <utility/SoftwareTimer.h>
static SoftwareTimer tracker_wake_timer;
static void trackerWakeCallback(TimerHandle_t) { }

// SYSTEMON event sleep has no built-in deadline. Arm a real timer before
// leaving the loop; allocation/queue failure keeps polling instead of hanging.
static bool armTrackerSleepWake(uint32_t seconds) {
  if (!tracker_wake_timer.getHandle()) {
    tracker_wake_timer.begin(1000, trackerWakeCallback, nullptr, false);
  }
  const TimerHandle_t handle = tracker_wake_timer.getHandle();
  if (!handle || seconds == 0 || seconds > 30) return false;
  return xTimerChangePeriod(handle, pdMS_TO_TICKS(seconds * 1000UL), 0) == pdPASS;
}
#endif

#if defined(ESP32_PLATFORM)
  #include <helpers/ESP32TrueRandom.h>
#endif

#ifdef DISPLAY_CLASS
  #include "UITask.h"
  static UITask ui_task(display);
  static bool display_ready = false;
#endif

class MyMesh : public SensorMesh {
public:
  MyMesh(mesh::MainBoard& board, mesh::Radio& radio, mesh::MillisecondClock& ms, mesh::RNG& rng, mesh::RTCClock& rtc, mesh::MeshTables& tables)
     : SensorMesh(board, radio, ms, rng, rtc, tables), 
       battery_data(12*24, 5*60)    // 24 hours worth of battery data, every 5 minutes
  {
  }

protected:
  /* ========================== custom logic here ========================== */
  Trigger low_batt, critical_batt;
  TimeSeriesData  battery_data;

  void onSensorDataRead() override {
    float batt_voltage = getVoltage(TELEM_CHANNEL_SELF);
    // Boards without battery measurement return zero; do not alarm on it.
    const bool battery_available = batt_voltage > 0.0f && isfinite(batt_voltage);

    battery_data.recordData(getRTCClock(), batt_voltage);   // record battery
    alertIf(battery_available && batt_voltage < 3.4f, critical_batt, HIGH_PRI_ALERT, "Battery is critical!");
    alertIf(battery_available && batt_voltage < 3.6f, low_batt, LOW_PRI_ALERT, "Battery is low");
  }

  int querySeriesData(uint32_t start_secs_ago, uint32_t end_secs_ago, MinMaxAvg dest[], int max_num) override {
    battery_data.calcMinMaxAvg(getRTCClock(), start_secs_ago, end_secs_ago, &dest[0], TELEM_CHANNEL_SELF, LPP_VOLTAGE);
    return 1;
  }

  bool handleCustomCommand(uint32_t sender_timestamp, char* command, char* reply) override {
    if (strcmp(command, "magic") == 0) {    // example 'custom' command handling
      strcpy(reply, "**Magic now done**");
      return true;   // handled
    }
    return false;  // not handled
  }
  /* ======================================================================= */
};

StdRNG fast_rng;
SimpleMeshTables tables;

MyMesh the_mesh(board, radio_driver, *new ArduinoMillis(), fast_rng, rtc_clock, tables);

SensorMesh& activeSensorMesh() {
  return the_mesh;
}

void halt() {
  while (1) ;
}

static char command[160];
static const unsigned long POWERSAVING_FIRST_SLEEP_SECS = 120;

void setup() {
  mesh::prepareUsbLoggingPort();
  Serial.begin(115200);
#if MESH_USB_CONSOLE_COOPERATIVE
  mesh::beginUsbLoggingPort();
#endif
  board.begin();

#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.begin();
#endif

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
#elif defined(STM32_PLATFORM)
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
#ifdef DISPLAY_CLASS
  mesh::ui::loadDisplayPowerSettings(fs, false);
  mesh::ui::StartupScreen startup_screen;
  display_ready = display.begin();
  if (display_ready) {
    startup_screen.begin(&display,
        board.isExternalPowered() || board.isUsbHostConnected());
  }
#endif

  // Let serial settle after the display is already showing startup.
  delay(1000);

#if defined(MESH_DEBUG) && defined(NRF52_PLATFORM)
  // Allow the USB console to settle before printing boot diagnostics.
  delay(5000);
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
#ifdef DISPLAY_CLASS
    startup_screen.generatingKey();
    mesh::ScopedIdentityGenerationProgress progress(
        mesh::ui::StartupScreen::progress, &startup_screen);
#endif
    MESH_DEBUG_PRINTLN("Generating new keypair");
    identity_ready = mesh::generateUsableLocalIdentity(the_mesh.self_id, radio_new_identity);
#ifdef DISPLAY_CLASS
    if (identity_ready) startup_screen.savingIdentity();
    else startup_screen.starting();
#endif
    if (identity_ready) identity_ready = store.saveWithRetry("_main", the_mesh.self_id);
#ifdef DISPLAY_CLASS
    startup_screen.starting();
#endif
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
  console.print("Sensor ID: ");
  mesh::Utils::printHex(console, the_mesh.self_id.pub_key, PUB_KEY_SIZE);
  console.println();

  command[0] = 0;

  sensors.begin();

#if defined(NRF52_PLATFORM)
  the_mesh.setTrackerStorageAvailable(!volatile_primary_fs);
#endif
  the_mesh.begin(fs);

#if defined(NRF52_PLATFORM)
  mesh::loadUsbLoggingWatchdog(fs, !volatile_primary_fs,
      []() -> uint32_t { return rtc_clock.getCurrentTime(); });
#else
  mesh::loadUsbLoggingWatchdog(fs, true,
      []() -> uint32_t { return rtc_clock.getCurrentTime(); });
#endif

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
    ui_task.begin(the_mesh.getNodePrefs(), FIRMWARE_BUILD_DATE, FIRMWARE_VERSION);
  }
#endif

  // send out initial zero hop Advertisement to the mesh
#if ENABLE_ADVERT_ON_BOOT == 1
  the_mesh.sendSelfAdvertisement(16000, false);
#endif

  board.onBootComplete();
}

static bool usbLoggingRecoverySafe(void*) {
  if (board.isOTAUpdateRunning() || board.isRadioTestActive()
      || radio_driver.isWatchdogObserving() || radio_driver.isCalibratingNoiseFloor()
      || !the_mesh.canRecoverUsbLogging()) return false;
  const auto usb = mesh::usbLoggingStatus();
  return !usb.reader_connected || usb.stalled || !command[0];
}

void loop() {
  mesh::serviceUsbLoggingPort();
#if defined(NRF52_PLATFORM)
  board.feedWatchdog(the_mesh.getNodePrefs()->system_watchdog_enabled != 0);
#endif
  bool usb_ready = true;
#if MESH_USB_CONSOLE_COOPERATIVE
  mesh::serviceUsbLoggingPort();
  mesh::serviceUsbTerminalPort();
  if (mesh::takeUsbTerminalSessionReset()) command[0] = 0;
  usb_ready = mesh::tryCompleteUsbTerminalSessionReset()
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
    if (len < static_cast<int>(sizeof(command) - 1)
        && strlen(command) == static_cast<size_t>(len - 1))
      mesh::noteUsbLoggingStatsCommand(command);
    the_mesh.handleCommand(0, command, reply);  // NOTE: there is no sender_timestamp via serial!
    if (reply[0]) {
      console.print("  -> ");
      console.println(reply);
    }

    command[0] = 0;  // reset command buffer
  }

  board.loop();   // let the board feed its watchdog, run periodic housekeeping

  the_mesh.loop();
  sensors.loop();
#ifdef DISPLAY_CLASS
  if (display_ready) ui_task.loop();
#endif
  rtc_clock.tick();
  if (mesh::serviceUsbLoggingWatchdog(usbLoggingRecoverySafe)) board.reboot();
#if MESH_USB_CONSOLE_COOPERATIVE
  mesh::serviceUsbTerminalPort();
#endif
#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.loop();
#endif

  bool can_power_save = (the_mesh.getNodePrefs()->powersaving_enabled
                        || the_mesh.isTrackerModeEnabled())
      && !board.isUsbDataConnected()
      && !mesh::isUsbLoggingWatchdogArmed();
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
#if MESH_ENABLE_SENSOR_TRACKER
      if (!the_mesh.isTrackerModeEnabled() || armTrackerSleepWake(sleep_secs)) board.sleep(0);
      else delay(1);
#else
      board.sleep(0);
#endif
    }
#else
    if (sleep_secs > 0
        && the_mesh.millisHasNowPassed(
            POWERSAVING_FIRST_SLEEP_SECS * 1000UL)) {
      board.sleep(sleep_secs);
    }
#endif
  }
#if defined(ESP32_PLATFORM)
  if (!can_power_save && (the_mesh.getNodePrefs()->powersaving_enabled
                         || the_mesh.isTrackerModeEnabled())) {
    delay(1); // Keep USB and the button wake interval serviced while idle.
  }
#endif
}
