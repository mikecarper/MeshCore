#include <Arduino.h>   // needed for PlatformIO
#include <Mesh.h>
#include "MyMesh.h"
#include "CompanionWiFi.h"
#if MESH_PACKET_LOGGING
  #include <helpers/SerialPacketLog.h>
#endif

#ifdef ESP32_PLATFORM
#include "esp_bt.h"
#include "esp_pm.h"
#include "esp_sleep.h"
#if defined(CONFIG_PM_ENABLE) && CONFIG_PM_ENABLE
#define COMPANION_IDF_PM_AVAILABLE 1
#else
#define COMPANION_IDF_PM_AVAILABLE 0
#endif
#if defined(BLE_PIN_CODE) && !CONFIG_IDF_TARGET_ESP32C6 \
    && (defined(CONFIG_BT_CTRL_MODEM_SLEEP) \
        || defined(CONFIG_BTDM_CTRL_MODEM_SLEEP))
#define COMPANION_BT_MODEM_SLEEP_AVAILABLE 1
#else
#define COMPANION_BT_MODEM_SLEEP_AVAILABLE 0
#endif
#if COMPANION_FEATURE_MEMORY_DIAGNOSTICS
#include <esp_heap_caps.h>
#endif
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
    #include <helpers/CLICommandUtils.h>
    #include <helpers/WiFiReconnectPolicy.h>
    #include <helpers/WiFiPowerSave.h>
    #include <helpers/CompanionWiFiNtpPolicy.h>
    #include <helpers/ui/DisplayTextLayout.h>
    #include <helpers/ui/IndicatorRenderProfile.h>
    #include <helpers/ui/WiFiSetupQrDisplay.h>
    #include <helpers/esp32/SntpOperationCoordinator.h>
    #include <helpers/esp32/TlsClockValidity.h>
    #include <helpers/esp32/WiFiRadioPolicy.h>
    #include <helpers/esp32/WiFiStationPolicy.h>
    #include <Preferences.h>
    #include <atomic>
    #include <esp_sntp.h>
    #include <esp_wifi.h>
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
  #include <helpers/UsbAsciiBinarySwitch.h>
  #include <helpers/UsbLogging.h>
  static const char USB_TERMINAL_START_TOKEN[] = "+++MESHCORE-TERM-START";
  static const char USB_TERMINAL_STOP_TOKEN[] = "+++MESHCORE-TERM-STOP";
#if COMPANION_FEATURE_USB_MOTA_SOURCE
  // motatool sends this command automatically when `serve --serial` opens the
  // port. It hands USB ownership to the host-backed mOTA source from either
  // the completed ASCII line or Binary mode's idle control-sequence parser.
  static const char USB_MOTA_START_TOKEN[] = "ota folder on";
#endif
  ArduinoSerialInterface usb_serial_interface;
  #ifndef USB_CLIENT_IDLE_TIMEOUT
    // how long a USB client is still considered present after its last frame,
    // for targets which cannot report DTR (see setConnectedCheck below)
    #define USB_CLIENT_IDLE_TIMEOUT   (10*60*1000UL)
  #endif
  #ifndef USB_FRAME_REPLY_GRACE_MS
    // A complete command frame proves that a Binary Companion client is
    // listening even if an ESP32 HWCDC host-presence flag briefly flaps while
    // the response is being queued. Keep this much time to finish the reply.
    #define USB_FRAME_REPLY_GRACE_MS  2000UL
  #endif
  #ifndef USB_HOST_LOSS_GRACE_MS
    // The bundled ESP32 HWCDC SOF detector declares loss after only a few
    // missed ticks. Debounce that advisory signal before ending a host session.
    #define USB_HOST_LOSS_GRACE_MS    2000UL
  #endif
  #ifndef USB_HOST_LOSS_EDGE_MS
    // HWCDC's framework filter declares loss after roughly 5 ms without SOFs.
    // Require a longer continuous gap before using that signal as a fallback
    // session boundary when the finite BUS_RESET event queue drops its event.
    #define USB_HOST_LOSS_EDGE_MS      100UL
  #endif
  #ifndef USB_TRANSPORT_RESET_RETRY_MS
    // A failed purge can spend up to roughly 150 ms waiting on the live HWCDC
    // writer/ring. Back off enough to keep WiFi and mesh service responsive.
    #define USB_TRANSPORT_RESET_RETRY_MS 1000UL
  #endif
#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  mesh::UsbHostPresenceDebouncer usb_hwcdc_host_presence;
#endif
#endif

#if COMPANION_FEATURE_NETWORK_TERMINAL
static bool isNetworkTerminalActive();
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
  #if defined(NRF52_PLATFORM)
    #include <flash/flash_nrf5x.h>
    #include <helpers/nrf52/InternalSecondaryFsRepair.h>
    #if defined(NRF52840_XXAA)
      static const uint32_t INTERNAL_PRIMARY_FS_START = 0x000ED000UL;
    #else
      static const uint32_t INTERNAL_PRIMARY_FS_START = 0x0006D000UL;
    #endif
    static const uint32_t INTERNAL_PRIMARY_FS_SIZE =
        7UL * FLASH_NRF52_PAGE_SIZE;
  #endif
  #if defined(QSPIFLASH)
    #include <CustomLFS_QSPIFlash.h>
    DataStore store(InternalFS, QSPIFlash, rtc_clock);
  #else
    #if defined(EXTRAFS)
      #include <CustomLFS.h>
      extern "C" uint32_t __flash_arduino_end[];
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

#if COMPANION_FEATURE_BLE_MOTA_SOURCE
#include <helpers/ota/MotaSourceSerial.h>
#include <helpers/ota/OtaContext.h>

class Nrf52BleMotaSourceControl : public mesh::companion::MotaSourceControl {
public:
  Nrf52BleMotaSourceControl()
      : _source(bluetooth_interface.motaStream(),
                mesh::ota::MotaStreamWritePolicy::NoFlush, 3000),
        _packets_sent_at_start(0), _last_packets_sent(0) {}

  bool start(char* reply, size_t reply_size) override {
    if (!reply || reply_size == 0) return false;
    if (!bluetooth_interface.isMotaChannelReady()) {
      snprintf(reply, reply_size,
               "ERR subscribe to the Bluetooth mOTA request characteristic first");
      return false;
    }

    mesh::ota::OtaContext& context = mesh::ota::ota_ctx();
    if (context.folder_active
        && context.folderLink() != mesh::ota::OtaContext::FOLDER_LINK_BLE) {
      snprintf(reply, reply_size, "ERR mOTA source already uses %s",
               mesh::ota::OtaContext::folderLinkName(context.folderLink()));
      return false;
    }

    _packets_sent_at_start = context.manager.packetsSent();
    _last_packets_sent = 0;
    bluetooth_interface.setMotaStreamActive(true);
    if (!context.attach_folder_source(
            &_source, mesh::ota::OtaContext::FOLDER_LINK_BLE, "ble",
            reply, reply_size)) {
      bluetooth_interface.setMotaStreamActive(false);
      return false;
    }

    context.manager.announce();
    mesh::usbLoggingPort().println("Bluetooth mOTA source attached");
    return true;
  }

  bool stop(char* reply, size_t reply_size) override {
    if (!reply || reply_size == 0) return false;
    mesh::ota::OtaContext& context = mesh::ota::ota_ctx();
    bluetooth_interface.setMotaStreamActive(false);
    if (context.folder_active
        && context.folderLink() == mesh::ota::OtaContext::FOLDER_LINK_BLE) {
      context.detach_folder();
      context.manager.announce();
      mesh::usbLoggingPort().println("Bluetooth mOTA source detached");
    }
    _last_packets_sent = context.manager.packetsSent()
        - _packets_sent_at_start;
    snprintf(reply, reply_size, "OK Bluetooth mOTA source stopped");
    return true;
  }

  mesh::companion::MotaSourceStatus status() const override {
    const mesh::ota::OtaContext& context = mesh::ota::ota_ctx();
    mesh::companion::MotaSourceStatus result;
    result.channel_ready = bluetooth_interface.isMotaChannelReady();
    result.attached = context.folder_active
        && context.folderLink() == mesh::ota::OtaContext::FOLDER_LINK_BLE
        && bluetooth_interface.isMotaStreamActive();
    result.another_link_active = context.folder_active
        && context.folderLink() != mesh::ota::OtaContext::FOLDER_LINK_BLE;
    if (result.attached) {
      context.folderSourceStats(result.offered, result.advertised);
      result.packets_sent = context.manager.packetsSent()
          - _packets_sent_at_start;
    } else {
      result.packets_sent = _last_packets_sent;
    }
    return result;
  }

  void loop() {
    mesh::ota::OtaContext& context = mesh::ota::ota_ctx();
    const bool owns_folder = context.folder_active
        && context.folderLink() == mesh::ota::OtaContext::FOLDER_LINK_BLE;
    if (!owns_folder) {
      if (bluetooth_interface.isMotaStreamActive()) {
        bluetooth_interface.setMotaStreamActive(false);
      }
      return;
    }
    if (bluetooth_interface.isMotaChannelReady()
        && bluetooth_interface.isMotaStreamActive()) {
      return;
    }

    bluetooth_interface.setMotaStreamActive(false);
    context.detach_folder();
    context.manager.announce();
    _last_packets_sent = context.manager.packetsSent()
        - _packets_sent_at_start;
    mesh::usbLoggingPort().println(
        "Bluetooth mOTA source disconnected and was detached");
  }

private:
  mesh::ota::SerialMotaSource _source;
  uint32_t _packets_sent_at_start;
  uint32_t _last_packets_sent;
};

static Nrf52BleMotaSourceControl ble_mota_source_control;
#endif

/* END GLOBAL OBJECTS */

#ifdef RECOVERABLE_EXTERNAL_RADIO
static bool companion_radio_available = true;
static unsigned long companion_radio_retry_at = 0;
static const unsigned long COMPANION_RADIO_RETRY_MS = 60000UL;

static void serviceCompanionRadioRecovery() {
  if (companion_radio_available
      || (long)(millis() - companion_radio_retry_at) < 0) return;

  companion_radio_retry_at = millis() + COMPANION_RADIO_RETRY_MS;
  mesh::usbLoggingPort().println("Radio recovery probe starting");
  if (!radio_init()) {
    mesh::usbLoggingPort().println(
        "Radio recovery probe failed; companion services remain available");
    return;
  }

  companion_radio_available = true;
  the_mesh.activateRadio();
  mesh::usbLoggingPort().println("Radio recovered; mesh transport is active");
}
#endif

#ifdef ESP32_PLATFORM
static int8_t applied_power_saving = -1;
static int8_t attempted_power_saving = -1;
static unsigned long power_saving_retry_at = 0;

static uint32_t companionNominalCpuMhz() {
#ifdef ESP32_POST_BOOT_CPU_FREQ
  return ESP32_POST_BOOT_CPU_FREQ;
#elif defined(ESP32_CPU_FREQ)
  return ESP32_CPU_FREQ;
#else
  return F_CPU / 1000000UL;
#endif
}

#if COMPANION_BT_MODEM_SLEEP_AVAILABLE
static void applyCompanionBluetoothSleep(bool enabled,
                                         bool allow_uninitialized) {
  esp_err_t result = enabled ? esp_bt_sleep_enable()
                             : esp_bt_sleep_disable();
  if (result == ESP_OK
      || (allow_uninitialized && result == ESP_ERR_INVALID_STATE)) {
    return;
  }

  mesh::usbLoggingPort().printf("Bluetooth sleep %s failed: %s\r\n",
                                enabled ? "enable" : "disable",
                                esp_err_to_name(result));
}
#endif

static bool applyCompanionPowerSaving(bool enabled) {
  const uint32_t nominal_mhz = companionNominalCpuMhz();
  const uint32_t max_mhz = enabled && nominal_mhz > 80 ? 80 : nominal_mhz;

#if COMPANION_IDF_PM_AVAILABLE
  const uint32_t min_mhz = enabled && max_mhz > 40 ? 40 : max_mhz;
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  // Automatic light sleep interrupts native USB CDC on ESP32 companions.
  // Frequency scaling remains active when device power saving is enabled.
  const bool automatic_light_sleep = false;
#else
  const bool automatic_light_sleep = enabled;
#endif
#if CONFIG_IDF_TARGET_ESP32C3
  esp_pm_config_esp32c3_t pm_config;
#elif CONFIG_IDF_TARGET_ESP32S3
  esp_pm_config_esp32s3_t pm_config;
#elif CONFIG_IDF_TARGET_ESP32
  esp_pm_config_esp32_t pm_config;
#else
  esp_pm_config_t pm_config;
#endif
  pm_config.max_freq_mhz = max_mhz;
  pm_config.min_freq_mhz = min_mhz;
  pm_config.light_sleep_enable = automatic_light_sleep;

  esp_err_t pm_result = esp_pm_configure(&pm_config);
  if (pm_result != ESP_OK) {
    mesh::usbLoggingPort().printf("Device power saving failed: %s\r\n",
                                  esp_err_to_name(pm_result));
    return false;
  }
#else
  // Arduino's prebuilt ESP-IDF normally has CONFIG_PM_ENABLE disabled, in
  // which case esp_pm_configure() is a stub returning ESP_ERR_NOT_SUPPORTED.
  // Keep frequency throttling functional instead of retrying that stub.
  if (!setCpuFrequencyMhz(max_mhz)) {
    mesh::usbLoggingPort().printf(
        "Device power saving failed: CPU %lu MHz is unsupported\r\n",
        (unsigned long)max_mhz);
    return false;
  }
#endif

#if COMPANION_BT_MODEM_SLEEP_AVAILABLE
  // Power saving is applied before the wireless interfaces are constructed.
  // An uninitialized controller is expected here; apply the saved setting
  // again immediately after BLEDevice::init() below. Some prebuilt ESP-IDF
  // targets omit Bluetooth modem sleep entirely; the compile-time guard avoids
  // calling an API which can only return ESP_ERR_NOT_SUPPORTED in those builds.
  applyCompanionBluetoothSleep(enabled, true);
#endif

#if COMPANION_IDF_PM_AVAILABLE
  mesh::usbLoggingPort().printf(
      "Device power saving %s: CPU %lu-%lu MHz, automatic light sleep %s\r\n",
      enabled ? "on" : "off", (unsigned long)min_mhz,
      (unsigned long)max_mhz, automatic_light_sleep ? "on" : "off");
#else
  mesh::usbLoggingPort().printf("Device power saving %s: CPU %lu MHz\r\n",
                                enabled ? "on" : "off",
                                (unsigned long)max_mhz);
#endif
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
static bool usb_logging_terminal_mode = false;
#if defined(COMPANION_RADIO_FULL)
static mesh::UsbBinaryStartupProbe usb_binary_startup_probe;
#endif
#if COMPANION_FEATURE_USB_MOTA_SOURCE
static bool usb_mota_mode = false;
static char usb_mota_line[32];
static size_t usb_mota_line_len = 0;
static bool usb_mota_disconnect_armed = false;
#endif

// nRF52's Adafruit CDC Stream::write() waits and yields until the complete
// request is queued. Functional terminal text uses a bounded queue over the
// single-attempt CDC0 facade so a normal host receives complete replies while
// an open but unread host cannot starve the mesh loop. Other platforms retain
// their ordinary stream.
static Stream& usbTerminalOutput() {
  return mesh::usbTerminalPort();
}

static void clearUsbTerminalLine() {
  memset(usb_terminal_line, 0, sizeof(usb_terminal_line));
  usb_terminal_line_len = 0;
}

static void printUsbTerminalInputEcho() {
  const char* password =
      mesh::cli::terminalPasswordInput(usb_terminal_line);
  size_t visible_len = usb_terminal_line_len;
  if (password != nullptr) {
    visible_len = static_cast<size_t>(password - usb_terminal_line);
  }

  if (visible_len > 0) {
    usbTerminalOutput().write(
        reinterpret_cast<const uint8_t*>(usb_terminal_line), visible_len);
  }
  for (size_t i = visible_len; i < usb_terminal_line_len; i++) {
    usbTerminalOutput().print('*');
  }
}

static void redrawUsbTerminalInput() {
  // The documented picocom `--imap spchex` converts an echoed BS to "[08]".
  // It deliberately leaves CR untouched, so redraw the edited line with CR
  // and printable bytes only. Padding clears a removed tab or wide glyph.
  usbTerminalOutput().print("\r> ");
  printUsbTerminalInputEcho();
  usbTerminalOutput().print("        ");
  usbTerminalOutput().print("\r> ");
  printUsbTerminalInputEcho();
}

static bool isUsbTerminalDataConnected() {
#if defined(RP2040_PLATFORM)
  return (bool)Serial;
#else
  return board.isUsbDataConnected();
#endif
}

static constexpr uint32_t USB_TERMINAL_PROTOCOL_DRAIN_MS = 250;

static void queueUsbTerminalControlReply(const char* reply) {
  if (reply == nullptr) return;
  const size_t size = strlen(reply);
  if (usbTerminalOutput().write(
          reinterpret_cast<const uint8_t*>(reply), size) != size) {
    // A transition reply takes priority over stale best-effort terminal text.
    // Clear only the application queue; TinyUSB retains any prefix already
    // accepted, so Binary/mOTA still cannot overtake it.
    mesh::discardUsbTerminalOutput();
    (void)usbTerminalOutput().write(
        reinterpret_cast<const uint8_t*>(reply), size);
  }
}

static void drainUsbTerminalOutputBeforeProtocolSwitch() {
  const uint32_t started = millis();
  while (mesh::hasPendingUsbTerminalOutput()
#if !(defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
      && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT)
         && isUsbTerminalDataConnected()
#endif
         && (uint32_t)(millis() - started)
                < USB_TERMINAL_PROTOCOL_DRAIN_MS) {
    mesh::serviceUsbTerminalPort();
#if defined(NRF52_PLATFORM)
    board.feedWatchdog();
#endif
    delay(1);
  }
  // Never let terminal bytes remain queued after another CDC0 protocol is
  // enabled. A draining host normally empties this in a few milliseconds; an
  // unread host loses only the residual text after the bounded deadline.
  mesh::discardUsbTerminalOutput();
}

static bool hasObservableActiveUsbTerminalClient() {
#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  // HWCDC exposes only whether USB is plugged in, not whether a host process
  // has opened the terminal. Treat actual buffered activity as ownership; an
  // idle physical cable must not permanently lock TCP port 5002.
  return usb_terminal_line_len != 0 || usb_terminal_discard_line;
#else
  return isUsbTerminalDataConnected();
#endif
}

static void cancelUsbSerialOperations() {
  // Contact enumeration uses the manager's pinned streaming route; delayed
  // single replies capture their own route inside MyMesh. Cancel only USB's
  // ownership so a simultaneous BLE/WiFi operation keeps running.
  if (interface_manager.isReplyRouteFor(&usb_serial_interface)) {
    the_mesh.cancelSerialResponseStream();
  }
  the_mesh.cancelSerialOperationsForRoute(&usb_serial_interface);
  interface_manager.forgetReplyRouteForDisconnected(&usb_serial_interface);
}

static void enterUsbTerminalMode() {
#if defined(COMPANION_RADIO_FULL)
  usb_binary_startup_probe.cancel();
#endif
  cancelUsbSerialOperations();
  mesh::discardUsbTerminalOutput();
  usb_serial_interface.setPassthroughMode(true);
  clearUsbTerminalLine();
  usb_terminal_discard_line = false;
  usb_terminal_disconnect_armed = isUsbTerminalDataConnected();
  usb_logging_terminal_mode = false;
  the_mesh.enterTerminalMode();
}

static void enterUsbLoggingTerminalMode() {
  enterUsbTerminalMode();
  usb_logging_terminal_mode = true;
}

static void leaveUsbTerminalMode(bool acknowledge) {
  if (acknowledge) {
    queueUsbTerminalControlReply("\r\nOK - Binary mode\r\n");
    drainUsbTerminalOutputBeforeProtocolSwitch();
  } else {
    mesh::discardUsbTerminalOutput();
  }
  the_mesh.exitTerminalMode();
  usb_serial_interface.setPassthroughMode(false);
  clearUsbTerminalLine();
  usb_terminal_discard_line = false;
  usb_terminal_disconnect_armed = false;
  usb_logging_terminal_mode = false;
}

#if COMPANION_FEATURE_USB_MOTA_SOURCE
static void resetUsbMotaMode() {
  mesh::discardUsbTerminalOutput();
  usb_mota_mode = false;
  usb_mota_line_len = 0;
  usb_mota_line[0] = 0;
  usb_mota_disconnect_armed = false;
  usb_serial_interface.setPassthroughMode(false);
}

static void leaveUsbMotaMode(bool acknowledge) {
  char reply[160] = {0};
  the_mesh.handleLocalControlCommand("ota folder off", reply, sizeof(reply));
  if (acknowledge) {
    char transition_reply[224];
    snprintf(transition_reply, sizeof(transition_reply),
             "\r\n%s\r\nOK - Binary mode\r\n", reply);
    queueUsbTerminalControlReply(transition_reply);
    drainUsbTerminalOutputBeforeProtocolSwitch();
  }
  resetUsbMotaMode();
}

static bool enterUsbMotaMode(mesh::UsbMotaEntryOrigin origin) {
  cancelUsbSerialOperations();
  mesh::discardUsbTerminalOutput();
  usb_serial_interface.setPassthroughMode(true);
  usb_mota_mode = true;
  usb_mota_line_len = 0;
  usb_mota_line[0] = 0;
  usb_mota_disconnect_armed = isUsbTerminalDataConnected();
#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  // A valid attach command is stronger host proof than HWCDC's advisory SOF
  // sample, which may be low in the same loop as the received command.
  usb_hwcdc_host_presence.noteClientActivity();
#endif

  char reply[160] = {0};
  if (!the_mesh.handleLocalControlCommand("ota folder on", reply, sizeof(reply))
      || strncmp(reply, "ERR", 3) == 0) {
    char failure_reply[224];
    snprintf(failure_reply, sizeof(failure_reply), "\r\n%s\r\n",
             reply[0] ? reply : "ERR could not enter mOTA seeder mode");
    queueUsbTerminalControlReply(failure_reply);
    drainUsbTerminalOutputBeforeProtocolSwitch();
    resetUsbMotaMode();
    if (mesh::shouldRestoreAsciiAfterMotaFailure(origin)) {
      enterUsbTerminalMode();
    }
    return false;
  }
  char attached_reply[224];
  snprintf(attached_reply, sizeof(attached_reply), "\r\n%s\r\n", reply);
  queueUsbTerminalControlReply(attached_reply);
  // The mOTA request stream writes directly through the single-attempt CDC0
  // facade. Finish this text barrier first so its first binary frame can never
  // overtake a queued attach reply.
  drainUsbTerminalOutputBeforeProtocolSwitch();
  return true;
}

static void serviceUsbMota() {
#if !(defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
      && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT)
  if (isUsbTerminalDataConnected()) {
    usb_mota_disconnect_armed = true;
  } else if (usb_mota_disconnect_armed) {
    leaveUsbMotaMode(false);
    return;
  }
#endif

  // SerialMotaSource consumes framed responses synchronously while serving a
  // block. Bytes left here are host control text, notably motatool's automatic
  // `ota folder off` on a clean shutdown.
  Stream& usb_input = mesh::usbCompanionPort();
  while (usb_input.available()) {
    int value = usb_input.read();
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

// Consume an exact TinyUSB owner-task edge, or HWCDC's framework-debounced SOF
// loss edge, before any protocol parser runs. No line, login, recipient, queued
// text, or mOTA attachment may survive into the next host session.
static bool usb_terminal_host_reset_completion_pending = false;
static uint32_t usb_terminal_host_reset_retry_at = 0;

static void resetUsbTerminalHostSession(bool preserve_ascii_terminal) {
  // Stop only work routed to the old USB host; a simultaneous BLE/WiFi
  // transaction is independent and must continue. Clear the route after
  // stopping its producer so no response can fall back to another transport.
  cancelUsbSerialOperations();
  the_mesh.resetUsbHostSessionInput();
  bool protocol_owner_reset = false;
#if COMPANION_FEATURE_USB_MOTA_SOURCE
  if (usb_mota_mode) {
    leaveUsbMotaMode(false);
    protocol_owner_reset = true;
  }
#endif
  if (!protocol_owner_reset && the_mesh.isTerminalMode()) {
    if (preserve_ascii_terminal) {
      // Full Companion's ordinary HWCDC terminal is the default protocol.
      // Clear the old host's input/output without turning the next physical
      // session into a silent Binary-only port.
      the_mesh.resetTerminalSession();
      mesh::discardUsbTerminalOutput();
      clearUsbTerminalLine();
      usb_terminal_discard_line = false;
      usb_terminal_disconnect_armed = false;
#if defined(COMPANION_RADIO_FULL)
      usb_binary_startup_probe.cancel();
#endif
    } else {
      leaveUsbTerminalMode(false);
    }
    protocol_owner_reset = true;
  }

  if (!protocol_owner_reset) {
    mesh::discardUsbTerminalOutput();
    usb_serial_interface.setPassthroughMode(false);
    clearUsbTerminalLine();
    usb_terminal_discard_line = false;
    usb_terminal_disconnect_armed = false;
    usb_logging_terminal_mode = false;
#if defined(COMPANION_RADIO_FULL)
    usb_binary_startup_probe.cancel();
#endif
  }
  // setPassthroughMode() clears parser/queue state but frame ownership is a
  // separate proof. Revoke all three explicitly at the session boundary.
  usb_serial_interface.resetSessionState();
#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  // Purge HWCDC's queues behind a temporary diagnostics gate. This keeps the
  // core's shared RTOS objects alive while other firmware tasks are running.
  if (!mesh::resetUsbCompanionTransport()) {
    // Keep the producer gate and PHY quarantined, then retry from later loops.
    // Rebooting on repeatable allocation/contention failures creates a host-
    // enumeration restart loop and still does not prove stale bytes were gone.
    usb_terminal_host_reset_completion_pending = true;
    usb_terminal_host_reset_retry_at = millis() + USB_TRANSPORT_RESET_RETRY_MS;
    return;
  }
  usb_terminal_host_reset_completion_pending = false;
  usb_hwcdc_host_presence.reset();
#endif
}

static void serviceUsbTerminalHostSessionReset() {
#if defined(NRF52_PLATFORM)
  if (mesh::takeUsbTerminalSessionReset()) {
    usb_terminal_host_reset_completion_pending = true;
    resetUsbTerminalHostSession(false);
  }

  if (usb_terminal_host_reset_completion_pending
      && mesh::tryCompleteUsbTerminalSessionReset()) {
    usb_terminal_host_reset_completion_pending = false;
  }
#elif defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  if (usb_terminal_host_reset_completion_pending) {
    if ((int32_t)(millis() - usb_terminal_host_reset_retry_at) >= 0) {
      if (mesh::resetUsbCompanionTransport()) {
        usb_terminal_host_reset_completion_pending = false;
        usb_hwcdc_host_presence.reset();
      } else {
        usb_terminal_host_reset_retry_at =
            millis() + USB_TRANSPORT_RESET_RETRY_MS;
      }
    }
    return;
  }

  const bool host_present = board.isUsbHostConnected();
  (void)usb_hwcdc_host_presence.observeHost(
      host_present, millis(), USB_HOST_LOSS_EDGE_MS,
      USB_HOST_LOSS_GRACE_MS);
  const bool hardware_bus_reset = mesh::takeUsbTerminalSessionReset();
  const bool physical_host_loss =
      usb_hwcdc_host_presence.takeHostLossEdge();
  const bool sustained_host_loss =
      usb_hwcdc_host_presence.takeSustainedHostLoss();
  if (hardware_bus_reset || physical_host_loss || sustained_host_loss) {
    resetUsbTerminalHostSession(true);
  }
#endif
}

static void serviceUsbTerminal() {
#if COMPANION_FEATURE_USB_MOTA_SOURCE
  if (usb_mota_mode) {
    serviceUsbMota();
    return;
  }
#endif
  // A saved logging-on preference makes the one available TTY behave like a
  // logging repeater: plaintext diagnostics plus an input-capable CLI. Put the
  // Companion interface into passthrough before it can mix framed traffic with
  // logs. An active TCP terminal owns the role CLI, so logging must not reclaim
  // it. Turning logging off restores the build's normal USB mode: ASCII for
  // Full Companion and Binary Companion for every other single-TTY build.
#if MESH_USB_LOGGING_AVAILABLE
  const mesh::UsbLoggingTerminalAction logging_action =
      mesh::selectUsbLoggingTerminalAction(
          mesh::hasDedicatedUsbLoggingPort(), mesh::isUsbLoggingEnabled(),
          the_mesh.isTerminalMode(), usb_logging_terminal_mode,
#if defined(COMPANION_RADIO_FULL)
          true,
#else
          false,
#endif
#if COMPANION_FEATURE_NETWORK_TERMINAL
          isNetworkTerminalActive()
#else
          false
#endif
      );
  switch (logging_action) {
    case mesh::UsbLoggingTerminalAction::CLAIM_USB:
      if (!the_mesh.isTerminalMode()) {
        enterUsbLoggingTerminalMode();
        return;
      }
      usb_logging_terminal_mode = true;
      break;
    case mesh::UsbLoggingTerminalAction::RETURN_TO_BINARY:
      leaveUsbTerminalMode(true);
      return;
    case mesh::UsbLoggingTerminalAction::KEEP_ASCII:
      // Logging may also be disabled over BLE/WiFi. Stop treating this session
      // as the logging terminal, but keep the ordinary ASCII terminal active;
      // do not silently change the USB protocol underneath an idle host. A
      // remote mode change also cancels any partially typed USB command before
      // drawing a fresh prompt.
      usb_logging_terminal_mode = false;
      clearUsbTerminalLine();
      usb_terminal_discard_line = false;
      usbTerminalOutput().print(
          "\r\nUSB logging off; ASCII terminal active\r\n> ");
      break;
    case mesh::UsbLoggingTerminalAction::NO_ACTION:
      break;
  }
#endif
  if (!the_mesh.isTerminalMode()) {
#if defined(COMPANION_RADIO_FULL)
    const mesh::UsbBinaryStartupProbe::Result probe_result =
        usb_binary_startup_probe.poll(
            millis(), usb_serial_interface.getCompletedFrameCount(),
            usb_serial_interface.getLastFrameMillis());
    if (probe_result
        == mesh::UsbBinaryStartupProbe::Result::RETURN_TO_ASCII) {
      enterUsbTerminalMode();
      return;
    }
#endif
    if (usb_serial_interface.takeControlSequence()) {
      enterUsbTerminalMode();
#if COMPANION_FEATURE_USB_MOTA_SOURCE
    } else if (usb_serial_interface.takeSecondaryControlSequence()) {
      enterUsbMotaMode(mesh::UsbMotaEntryOrigin::BINARY);
#endif
    }
    return;
  }

#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  // HWCDC has no DTR/open signal, and its bool operator is only a transient
  // RX/TX activity heuristic. Do not interpret an idle interval as the host
  // closing Full Companion's startup terminal. TCP handoff is separately
  // arbitrated by hasObservableActiveUsbTerminalClient(), using actual input.
#else
  if (isUsbTerminalDataConnected()) {
    usb_terminal_disconnect_armed = true;
  } else if (usb_terminal_disconnect_armed) {
    leaveUsbTerminalMode(false);
    return;
  }
#endif

#if defined(COMPANION_RADIO_FULL)
  // Full Companion boots as a useful ASCII terminal. MeshCLI's first framed
  // command begins with '<'; hand that byte over untouched at an empty prompt.
  // A malformed or accidental probe times out and restores the terminal.
  if (!usb_logging_terminal_mode
      && usb_binary_startup_probe.shouldStart(
          usb_terminal_line_len == 0, usb_terminal_discard_line,
          mesh::usbCompanionPort().peek())) {
    const uint32_t frame_count = usb_serial_interface.getCompletedFrameCount();
    leaveUsbTerminalMode(false);
    usb_binary_startup_probe.start(millis(), frame_count);
    return;
  }
#endif

  Stream& usb_input = mesh::usbCompanionPort();
  while (usb_input.available()) {
    int value = usb_input.read();
    if (value < 0) break;
    char c = (char)value;

    if (usb_terminal_discard_line) {
      if (c == '\r' || c == '\n') {
        usb_terminal_discard_line = false;
        usbTerminalOutput().print("> ");
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
      usbTerminalOutput().print("\r\n");
#if COMPANION_FEATURE_USB_MOTA_SOURCE
      // motatool is deliberately text-first: `serve --serial` opens the port
      // and sends this command before its binary mOTA request/reply traffic.
      // Full Companion now boots in ASCII, so transfer ownership directly
      // instead of letting the ordinary terminal command handler leave the
      // stream in line-oriented mode.
      if (strcmp(usb_terminal_line, USB_MOTA_START_TOKEN) == 0) {
        leaveUsbTerminalMode(false);
        enterUsbMotaMode(mesh::UsbMotaEntryOrigin::ASCII);
        return;
      }
#endif
      the_mesh.handleTerminalCommand(usb_terminal_line);
      clearUsbTerminalLine();
#if MESH_USB_LOGGING_AVAILABLE
      if (usb_logging_terminal_mode
          && !mesh::isUsbLoggingEnabled()) {
#if defined(COMPANION_RADIO_FULL)
        // The command reply belongs to the Full Companion's normal ASCII
        // session. A separate, observable mode switch is required before the
        // port accepts framed Binary Companion traffic again.
        usb_logging_terminal_mode = false;
#else
        leaveUsbTerminalMode(true);
        return;
#endif
      }
#endif
      usbTerminalOutput().print("> ");
      return; // service at most one command per mesh loop
    }

    if (usb_terminal_line_len >= sizeof(usb_terminal_line) - 1) {
      clearUsbTerminalLine();
      usb_terminal_discard_line = true;
      usbTerminalOutput().print("\r\n  ERROR: command too long\r\n");
      continue;
    }

    usb_terminal_line[usb_terminal_line_len++] = c;
    usb_terminal_line[usb_terminal_line_len] = 0;
    usbTerminalOutput().print(
        mesh::cli::shouldMaskTerminalInput(usb_terminal_line) ? '*' : c);

    if (strcmp(usb_terminal_line, USB_TERMINAL_STOP_TOKEN) == 0) {
      leaveUsbTerminalMode(true);
      return;
    }
  }
}

#if defined(COMPANION_RADIO_FULL)
static void expireUsbBinaryStartupProbeBeforeDispatch() {
  const uint32_t now = millis();
  if (!usb_binary_startup_probe.hasTimedOut(now)) return;

  // The dispatcher normally consumes Binary Companion input before the ASCII
  // terminal service runs. Enforce the advertised deadline here so bytes that
  // are still incomplete at one second cannot complete a frame afterwards.
  // Entering passthrough resets the partial binary parser; drain only bytes
  // already queued for that expired attempt so they cannot begin a new probe.
  Stream& usb_input = mesh::usbCompanionPort();
  int pending = usb_input.available();
  while (pending-- > 0) usb_input.read();
  enterUsbTerminalMode();
}
#endif
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
  static char configured_wifi_password[65];
  static bool companion_wifi_has_credentials = false;
  static bool companion_wifi_requested = true;
  static bool companion_wifi_active = false;
  static bool companion_wifi_disable_in_progress = false;
  static bool companion_wifi_services_stopped = false;
  static volatile bool companion_wifi_setup_requested = false;
  static volatile bool companion_wifi_setup_stop_requested = false;
  static bool companion_wifi_credential_reload_pending = false;
  static unsigned long companion_wifi_credential_reload_at = 0;
  static bool companion_wifi_power_save_loaded = false;
  static uint8_t companion_wifi_power_save = mesh::wifi::kDefaultPowerSave;
  static CompanionWiFiDisplayState companion_wifi_display_state =
      CompanionWiFiDisplayState::NotRendered;
  static uint32_t companion_wifi_display_frames = 0;
  static bool companion_display_available = false;
  static uint8_t companion_display_begin_status = 0;

#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  static CompanionTransportMode companion_transport_boot_mode =
      CompanionTransportMode::WiFi;
  static bool companion_transport_boot_mode_loaded = false;

  CompanionTransportMode getCompanionTransportMode() {
    return the_mesh.getNodePrefs()->wifi_enabled != 0
        ? CompanionTransportMode::WiFi
        : CompanionTransportMode::Bluetooth;
  }

  bool selectCompanionTransportMode(CompanionTransportMode mode) {
    if (mode != CompanionTransportMode::Bluetooth
        && mode != CompanionTransportMode::WiFi) {
      return false;
    }

    CompanionNodePrefs* prefs = the_mesh.getNodePrefs();
    const uint8_t previous = prefs->wifi_enabled;
    const uint8_t selected = mode == CompanionTransportMode::WiFi ? 1 : 0;
    if (previous == selected) return true;

    prefs->wifi_enabled = selected;
    if (the_mesh.savePrefs()) return true;
    prefs->wifi_enabled = previous;
    return false;
  }

  static bool companionTransportWiFiActiveAtBoot() {
    return companion_transport_boot_mode == CompanionTransportMode::WiFi;
  }

  static void loadCompanionTransportModeForBoot() {
    companion_transport_boot_mode = getCompanionTransportMode();
    companion_transport_boot_mode_loaded = true;
    companion_wifi_requested = companionTransportWiFiActiveAtBoot();
    mesh::usbLoggingPort().printf(
        "Companion transport: exclusive %s selected for this boot\r\n",
        companion_wifi_requested ? "WiFi" : "Bluetooth");
  }

  static void releaseCompanionBluetoothMemoryForWiFi() {
    const esp_err_t result = esp_bt_mem_release(ESP_BT_MODE_BTDM);
    if (result == ESP_OK) {
      mesh::usbLoggingPort().println(
          "Companion transport: released Bluetooth memory for exclusive WiFi mode");
    } else {
      mesh::usbLoggingPort().printf(
          "Companion transport: Bluetooth memory release failed: %s\r\n",
          esp_err_to_name(result));
    }
  }
#endif

  static constexpr uint32_t COMPANION_WIFI_NTP_TIMEOUT_MS = 15000UL;
  static mesh::wifi::CompanionWiFiNtpPolicy companion_wifi_ntp_policy;
  static std::atomic<uint32_t> companion_wifi_ntp_operation_generation{0};
  static std::atomic<uint32_t> companion_wifi_ntp_proof_generation{0};
  static std::atomic<uint32_t> companion_wifi_ntp_epoch{0};
  static std::atomic<uint32_t> companion_wifi_ntp_proven_at{0};
  static bool companion_wifi_ntp_pending = false;
  static uint32_t companion_wifi_ntp_started = 0;
  static uint32_t companion_wifi_ntp_previous_epoch = 0;

  static void clearCompanionWiFiNtpCallback() {
    companion_wifi_ntp_operation_generation.store(
        0, std::memory_order_release);
    esp_sntp_set_time_sync_notification_cb(nullptr);
    // The policy below owns the cadence. Leave no SDK-default (three-hour in
    // the current ESP32 toolchain) background poll running between the boot
    // request and the explicit 24-hour refresh.
    esp_sntp_stop();
  }

  static mesh::sntp_coord::OperationLease companion_wifi_ntp_operation(
      mesh::sntp_coord::processWideCoordinator(),
      clearCompanionWiFiNtpCallback);

  static void noteCompanionWiFiNtpTime(struct timeval* value) {
    const uint32_t generation =
        companion_wifi_ntp_operation_generation.load(
            std::memory_order_acquire);
    if (!mesh::sntp_coord::processWideCoordinator().owns(generation)) return;
    if (value == nullptr || !mesh::tls_clock::timeIsValid(value->tv_sec)
        || static_cast<uint64_t>(value->tv_sec) > UINT32_MAX) {
      return;
    }
    companion_wifi_ntp_epoch.store(
        static_cast<uint32_t>(value->tv_sec), std::memory_order_release);
    companion_wifi_ntp_proven_at.store(millis(), std::memory_order_release);
    companion_wifi_ntp_proof_generation.store(
        generation, std::memory_order_release);
  }

  static bool companionWiFiMqttOwnsNtp() {
#ifdef WITH_MQTT_BRIDGE
    const bool running = the_mesh.isMQTTRunning();
    if (running && the_mesh.hasFreshMQTTNtpThisBoot()) {
      the_mesh.noteInternetClockSet();
    }
    return running;
#else
    return false;
#endif
  }

  static void cancelCompanionWiFiNtp(bool retry_on_reconnect) {
    if (!companion_wifi_ntp_pending) return;
    companion_wifi_ntp_pending = false;
    companion_wifi_ntp_operation.release();
    if (retry_on_reconnect) companion_wifi_ntp_policy.requestNow();
  }

  static void serviceCompanionWiFiNtp() {
    // Configured MQTT builds already perform a fresh startup NTP sync and a
    // daily refresh. The common Companion owner covers every non-MQTT or
    // runtime-unconfigured WiFi node without racing MQTT's SNTP proof.
    if (companionWiFiMqttOwnsNtp()) {
      cancelCompanionWiFiNtp(false);
      companion_wifi_ntp_policy.requestNow();
      return;
    }

    if (WiFi.status() != WL_CONNECTED) {
      cancelCompanionWiFiNtp(true);
      return;
    }

    const uint32_t now = millis();
    if (companion_wifi_ntp_pending) {
      const uint32_t proof_epoch = companion_wifi_ntp_epoch.load(
          std::memory_order_acquire);
      const uint32_t proof_generation =
          companion_wifi_ntp_proof_generation.load(
              std::memory_order_acquire);
      const uint32_t proven_at = companion_wifi_ntp_proven_at.load(
          std::memory_order_acquire);
      if (proof_epoch != 0 && companion_wifi_ntp_operation.owns()
          && proof_generation == companion_wifi_ntp_operation.generation()
          && proven_at - companion_wifi_ntp_started <=
              COMPANION_WIFI_NTP_TIMEOUT_MS) {
        // The callback's epoch is freshness proof, not the value to install:
        // the main loop may consume it one or more seconds later. Copy the
        // current ESP system clock so an external RTC is not immediately
        // written behind the time that SNTP established.
        const time_t raw_system_time = time(nullptr);
        if (mesh::tls_clock::timeIsValid(raw_system_time)
            && static_cast<uint64_t>(raw_system_time) <= UINT32_MAX) {
          const uint32_t epoch = static_cast<uint32_t>(raw_system_time);
          const bool moved_backward =
              epoch < companion_wifi_ntp_previous_epoch;
          rtc_clock.setCurrentTime(epoch);
          if (moved_backward) rtc_clock.resetUniqueTime(epoch);
          the_mesh.noteInternetClockSet();

          const int64_t adjustment = static_cast<int64_t>(epoch)
              - static_cast<int64_t>(companion_wifi_ntp_previous_epoch);
          mesh::usbLoggingPort().printf(
              "Companion WiFi NTP: clock synchronized (%lu, adjustment %+lld seconds)\r\n",
              (unsigned long)epoch, (long long)adjustment);
          companion_wifi_ntp_pending = false;
          companion_wifi_ntp_operation.release();
          companion_wifi_ntp_policy.noteSuccess(now);
          return;
        }
      }

      if (now - companion_wifi_ntp_started >=
          COMPANION_WIFI_NTP_TIMEOUT_MS) {
        mesh::usbLoggingPort().println(
            "Companion WiFi NTP: timed out; retrying in 5 minutes");
        companion_wifi_ntp_pending = false;
        companion_wifi_ntp_operation.release();
        companion_wifi_ntp_policy.noteFailure(now);
      }
      return;
    }

    if (!companion_wifi_ntp_policy.attemptDue(now)) return;
    if (!companion_wifi_ntp_operation.tryAcquire()) {
      companion_wifi_ntp_policy.noteBusy(now);
      return;
    }

    companion_wifi_ntp_epoch.store(0, std::memory_order_release);
    companion_wifi_ntp_proven_at.store(0, std::memory_order_release);
    companion_wifi_ntp_proof_generation.store(0, std::memory_order_release);
    companion_wifi_ntp_operation_generation.store(
        companion_wifi_ntp_operation.generation(),
        std::memory_order_release);
    companion_wifi_ntp_started = now;
    companion_wifi_ntp_previous_epoch = rtc_clock.getCurrentTime();
    companion_wifi_ntp_pending = true;
    esp_sntp_set_time_sync_notification_cb(noteCompanionWiFiNtpTime);
    esp_sntp_set_sync_status(SNTP_SYNC_STATUS_RESET);
    configTime(0, 0, "time.cloudflare.com", "time.google.com",
               "pool.ntp.org");
    mesh::usbLoggingPort().println(
        "Companion WiFi NTP: requesting fresh UTC time");
  }

  static bool companionWiFiBluetoothActive() {
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
    const CompanionTransportMode active_mode =
        companion_transport_boot_mode_loaded
            ? companion_transport_boot_mode
            : getCompanionTransportMode();
    return active_mode == CompanionTransportMode::Bluetooth;
#elif defined(BLE_PIN_CODE)
    return true;
#else
    return false;
#endif
  }

  const char* companionWiFiPowerSaveName(uint8_t mode) {
    if (mode == mesh::wifi::kPowerSaveMin) return "min";
    if (mode == mesh::wifi::kPowerSaveMax) return "max";
    return "none";
  }

  static wifi_ps_type_t companionWiFiPowerSaveType(uint8_t mode) {
    if (mode == mesh::wifi::kPowerSaveNone) return WIFI_PS_NONE;
    if (mode == mesh::wifi::kPowerSaveMax) return WIFI_PS_MAX_MODEM;
    return WIFI_PS_MIN_MODEM;
  }

  void reloadCompanionWiFiPowerSave() {
    uint8_t configured = mesh::wifi::kDefaultPowerSave;
    Preferences nvs;
    if (nvs.begin("mesh-wifi", true)) {
      configured = nvs.getUChar("powersave", mesh::wifi::kDefaultPowerSave);
      nvs.end();
    }
    companion_wifi_power_save = mesh::wifi::effectivePowerSave(
        configured, companionWiFiBluetoothActive(),
        mesh::wifi::kPrimaryEspNowRadio);
    companion_wifi_power_save_loaded = true;
    applyCompanionWiFiPowerSave();
  }

  uint8_t getCompanionWiFiPowerSave() {
    if (!companion_wifi_power_save_loaded) reloadCompanionWiFiPowerSave();
    return companion_wifi_power_save;
  }

  const char* getCompanionWiFiPowerSaveName() {
    return companionWiFiPowerSaveName(getCompanionWiFiPowerSave());
  }

  bool applyCompanionWiFiPowerSave() {
    if (!companion_wifi_power_save_loaded) {
      reloadCompanionWiFiPowerSave();
      return true;
    }
    if (WiFi.getMode() == WIFI_OFF) return true;
    return esp_wifi_set_ps(
        companionWiFiPowerSaveType(companion_wifi_power_save)) == ESP_OK;
  }

  CompanionWiFiPowerSaveResult setCompanionWiFiPowerSave(uint8_t mode) {
    if (mode > mesh::wifi::kPowerSaveMax) {
      return CompanionWiFiPowerSaveResult::InvalidMode;
    }
    if (mesh::wifi::kPrimaryEspNowRadio
        && mode == mesh::wifi::kPowerSaveMax) {
      return CompanionWiFiPowerSaveResult::PrimaryEspNowConflict;
    }
    if (mesh::wifi::effectivePowerSave(
            mode, companionWiFiBluetoothActive(),
            mesh::wifi::kPrimaryEspNowRadio)
        != mode) {
      return CompanionWiFiPowerSaveResult::BluetoothConflict;
    }
    Preferences nvs;
    if (!nvs.begin("mesh-wifi", false)) {
      return CompanionWiFiPowerSaveResult::StorageError;
    }
    const bool saved =
        nvs.putUChar("powersave", mode) == sizeof(uint8_t);
    nvs.end();
    if (!saved) return CompanionWiFiPowerSaveResult::StorageError;

    companion_wifi_power_save = mode;
    companion_wifi_power_save_loaded = true;
    if (WiFi.getMode() == WIFI_OFF) {
      return CompanionWiFiPowerSaveResult::SavedForNextConnection;
    }
    return applyCompanionWiFiPowerSave()
        ? CompanionWiFiPowerSaveResult::Applied
        : CompanionWiFiPowerSaveResult::SavedForNextConnection;
  }

  static void loadCompanionWiFiCredentials() {
    companion_wifi_has_credentials = WiFiSetupPortal::loadStoredCredentials(
        configured_wifi_ssid, sizeof(configured_wifi_ssid),
        configured_wifi_password, sizeof(configured_wifi_password));
    if (!companion_wifi_has_credentials
        && !WiFiSetupPortal::isPlaceholderSSID(WIFI_SSID)
        && mesh::cli::standaloneWiFiPasswordValid(WIFI_PWD)) {
      strncpy(configured_wifi_ssid, WIFI_SSID, sizeof(configured_wifi_ssid) - 1);
      configured_wifi_ssid[sizeof(configured_wifi_ssid) - 1] = '\0';
      strncpy(configured_wifi_password, WIFI_PWD, sizeof(configured_wifi_password) - 1);
      configured_wifi_password[sizeof(configured_wifi_password) - 1] = '\0';
      companion_wifi_has_credentials = true;
    }
  }

  void scheduleCompanionWiFiCredentialReload() {
    loadCompanionWiFiCredentials();
    companion_wifi_credential_reload_pending = true;
    companion_wifi_credential_reload_at = millis() + 1500UL;
    if (companion_wifi_credential_reload_at == 0) {
      companion_wifi_credential_reload_at = 1;
    }
  }

  bool isCompanionWiFiEnabled() {
    return companion_wifi_requested;
  }

  bool hasCompanionWiFiCredentials() {
    return companion_wifi_has_credentials;
  }

  bool isCompanionWiFiConnected() {
    // The display describes the station link, not the lifecycle of the
    // Companion TCP services. A valid station association remains the source
    // of truth even if service bookkeeping is delayed or being recovered.
    if (!companion_wifi_requested) return false;
    if (WiFi.status() == WL_CONNECTED) return true;
    wifi_ap_record_t access_point = {};
    return esp_wifi_sta_get_ap_info(&access_point) == ESP_OK;
  }

  void noteCompanionWiFiDisplayState(CompanionWiFiDisplayState state) {
    companion_wifi_display_state = state;
    companion_wifi_display_frames++;
  }

  void formatCompanionWiFiDisplayStatus(char* reply, size_t reply_size) {
    if (reply == nullptr || reply_size == 0) return;
    static const char* const names[] = {
      "not-rendered", "setup", "off", "ready", "not-configured",
      "connecting",
    };
    uint8_t state = static_cast<uint8_t>(companion_wifi_display_state);
    if (state >= sizeof(names) / sizeof(names[0])) state = 0;
    wifi_ap_record_t access_point = {};
    const bool idf_associated =
        esp_wifi_sta_get_ap_info(&access_point) == ESP_OK;
    snprintf(reply, reply_size,
             "> state=%s, frames=%lu, display=%u/%02X, requested=%u, services=%u, "
             "wl=%d, idf=%u, IP=%s",
             names[state], (unsigned long)companion_wifi_display_frames,
             companion_display_available ? 1U : 0U,
             (unsigned int)companion_display_begin_status,
             companion_wifi_requested ? 1U : 0U,
             companion_wifi_active ? 1U : 0U, (int)WiFi.status(),
             idf_associated ? 1U : 0U,
             WiFi.localIP().toString().c_str());
  }

#ifdef WITH_WEBCONFIG
  void formatCompanionWiFiStatus(char* reply, size_t reply_size) {
    const mesh::wifi::CompanionWiFiRuntimeState runtime = {
        companion_wifi_requested,
        companion_wifi_active,
        companion_wifi_credential_reload_pending,
    };
    WebConfigServer::formatWiFiStatus(reply, reply_size, &runtime);
  }
#endif

  void requestCompanionWiFiSetup() {
    companion_wifi_setup_stop_requested = false;
    companion_wifi_setup_requested = true;
  }

  void requestCompanionWiFiSetupStop() {
    companion_wifi_setup_requested = false;
    companion_wifi_setup_stop_requested = true;
  }

  bool toggleCompanionWiFi() {
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
    const CompanionTransportMode current = getCompanionTransportMode();
    const CompanionTransportMode selected =
        current == CompanionTransportMode::WiFi
            ? CompanionTransportMode::Bluetooth
            : CompanionTransportMode::WiFi;
    if (!selectCompanionTransportMode(selected)) {
      WIFI_DEBUG_PRINTLN(
          "Companion transport selection could not be saved");
      return current == CompanionTransportMode::WiFi;
    }
    WIFI_DEBUG_PRINTLN(
        "Companion transport %s saved; reboot required",
        selected == CompanionTransportMode::WiFi ? "WiFi" : "Bluetooth");
    return selected == CompanionTransportMode::WiFi;
#else
    companion_wifi_requested = !companion_wifi_requested;
    the_mesh.getNodePrefs()->wifi_enabled = companion_wifi_requested ? 1 : 0;
    the_mesh.savePrefs();
    if (!companion_wifi_requested && companion_wifi_active) {
      companion_wifi_disable_in_progress = true;
    }
    WIFI_DEBUG_PRINTLN("BOOT/GPIO 0 click requested WiFi %s",
                       companion_wifi_requested ? "on" : "off");
    return companion_wifi_requested;
#endif
  }

  #if defined(WITH_WEBCONFIG) && defined(DISPLAY_CLASS) \
      && UI_WIFI_SETUP_HOME_PAGE != 1
    static DisplayDriver* companion_setup_display = nullptr;
    static unsigned long companion_setup_display_refresh = 0;

    static bool hasLargeCompanionSetupDisplay() {
      return companion_setup_display->width() >= 128
          && companion_setup_display->height() >= 128;
    }

    static int drawCompanionSetupValue(int y, int max_lines,
                                       const char* value) {
      static constexpr int margin = 6;
      static constexpr int line_height = 13;
      return mesh::ui::drawTextWrapped(
          *companion_setup_display, margin, y,
          companion_setup_display->width() - margin * 2, line_height,
          max_lines, value != nullptr && value[0] != 0 ? value : "(not set)");
    }

    static void renderLargeCompanionSetupDisplay(const char* title,
                                                  const char* wifi_label,
                                                  const char* wifi_name,
                                                  const char* address,
                                                  bool connecting) {
      static constexpr int line_height = 13;
      companion_setup_display->drawTextCentered(
          companion_setup_display->width() / 2, 4, title);
      companion_setup_display->setCursor(6, 24);
      companion_setup_display->print(wifi_label);
      const int wifi_lines = drawCompanionSetupValue(38, 3, wifi_name);

      int next_y = 38 + (wifi_lines > 0 ? wifi_lines : 1) * line_height + 8;
      if (connecting) {
        companion_setup_display->drawTextCentered(
            companion_setup_display->width() / 2, next_y, "Please wait...");
        return;
      }

      companion_setup_display->setCursor(6, next_y);
      companion_setup_display->print("Open in browser:");
      char url[32];
      snprintf(url, sizeof(url), "http://%s/", address ? address : "");
      drawCompanionSetupValue(next_y + 14, 2, url);
    }

    static void renderCompanionSetupDisplay() {
      if (!companion_setup_display
          || static_cast<int32_t>(millis() - companion_setup_display_refresh) < 0) return;
      companion_setup_display_refresh = millis() + 1000;

      companion_setup_display->turnOn();
      companion_setup_display->startFrame();
      companion_setup_display->setCompactText(false);
      companion_setup_display->setTextSize(1);
      companion_setup_display->setColor(UIColor::primary_txt);

      char setup_ssid[33] = {0};
      char setup_ip[16] = {0};
      if (WebConfigServer::getSetupInfo(setup_ssid, sizeof(setup_ssid),
                                        setup_ip, sizeof(setup_ip))) {
        if (mesh::ui::drawWiFiSetupQr(
                *companion_setup_display, setup_ssid, setup_ip)) {
          companion_setup_display->endFrame();
          return;
        }
        if (hasLargeCompanionSetupDisplay()) {
          renderLargeCompanionSetupDisplay(
              "WebUI setup", "Join open WiFi:", setup_ssid, setup_ip, false);
        } else {
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
        }
      } else if (WiFi.status() == WL_CONNECTED) {
        const String ip = WiFi.localIP().toString();
        if (hasLargeCompanionSetupDisplay()) {
          renderLargeCompanionSetupDisplay(
              "WebUI ready", "Join WiFi:", configured_wifi_ssid, ip.c_str(),
              false);
        } else {
          companion_setup_display->drawTextCentered(
              companion_setup_display->width() / 2, 0, "WebUI");
          companion_setup_display->setCursor(0, 14);
          companion_setup_display->print("Join WiFi:");
          companion_setup_display->drawTextEllipsized(
              0, 25, companion_setup_display->width(), configured_wifi_ssid);
          companion_setup_display->setCursor(0, 39);
          companion_setup_display->print("Open in browser:");
          companion_setup_display->drawTextCentered(
              companion_setup_display->width() / 2, 51, ip.c_str());
        }
      } else {
        if (hasLargeCompanionSetupDisplay()) {
          renderLargeCompanionSetupDisplay(
              "WiFi connecting", "WiFi name:", configured_wifi_ssid, nullptr,
              true);
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
      }
      companion_setup_display->setCompactText(false);
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

/* WIFI TEXT CONSOLE - one client at a time on a dedicated port, separate from
   Binary Companion (5000) and the mOTA seeder (5001). A Full Companion exposes
   the same role CLI here as its USB terminal. Other OTA-enabled Companion
   builds retain the bounded `ota ...` management console. */
#if defined(ESP32) && defined(WIFI_SSID) && defined(ENABLE_OTA)
  #include <helpers/ota/OtaCli.h>          // mesh::ota::handle_ota_command(line, reply, board)
  #include <helpers/esp32/WiFiOtaSeeder.h>
  #ifndef OTA_CONSOLE_TCP_PORT
    #define OTA_CONSOLE_TCP_PORT 5002
  #endif
  static WiFiServer ota_console_server(OTA_CONSOLE_TCP_PORT);
  static WiFiClient ota_console_client;
  static char    ota_console_line[MAX_TRANS_UNIT * 2 + 32];
  static size_t ota_console_len = 0;
  static bool ota_console_discard_line = false;
#if COMPANION_FEATURE_NETWORK_TERMINAL && defined(ENABLE_USB_INTERFACE)
  static mesh::UsbTcpTerminalHandoff ota_console_usb_handoff;
#endif

#if COMPANION_FEATURE_NETWORK_TERMINAL
  static bool isNetworkTerminalActive() {
    return the_mesh.isNetworkTerminalMode(ota_console_client);
  }
#endif

  static void ota_console_clear_line() {
    memset(ota_console_line, 0, sizeof(ota_console_line));
    ota_console_len = 0;
  }

  static void ota_console_start() {
    ota_console_server.begin();
#if COMPANION_FEATURE_NETWORK_TERMINAL
    WIFI_DEBUG_PRINTLN("Full Companion terminal listening on :%d  (nc <ip> %d)",
                       OTA_CONSOLE_TCP_PORT, OTA_CONSOLE_TCP_PORT);
#else
    WIFI_DEBUG_PRINTLN("OTA console listening on :%d  (nc <ip> %d, type `ota ...`)",
                       OTA_CONSOLE_TCP_PORT, OTA_CONSOLE_TCP_PORT);
#endif
  }

#if COMPANION_FEATURE_NETWORK_TERMINAL
  static void ota_console_release_terminal() {
    the_mesh.exitNetworkTerminalMode(ota_console_client);
#if defined(ENABLE_USB_INTERFACE)
    if (ota_console_usb_handoff.shouldRestoreAscii(
            usb_serial_interface.getCompletedFrameCount())) {
      enterUsbTerminalMode();
    }
#endif
  }
#endif

  static void ota_console_stop() {
#if COMPANION_FEATURE_NETWORK_TERMINAL
    ota_console_release_terminal();
#endif
    if (ota_console_client) ota_console_client.stop();
    ota_console_server.end();
    ota_console_clear_line();
    ota_console_discard_line = false;
  }

  static void ota_console_loop() {
    if (!ota_console_client || !ota_console_client.connected()) {
#if COMPANION_FEATURE_NETWORK_TERMINAL
      ota_console_release_terminal();
#endif
      WiFiClient c = ota_console_server.available();
      if (c) {
        ota_console_client = c;
        ota_console_clear_line();
        ota_console_discard_line = false;
#if COMPANION_FEATURE_NETWORK_TERMINAL
#if defined(ENABLE_USB_INTERFACE)
        const bool usb_ascii_selected = the_mesh.isTerminalMode();
        const bool usb_input_idle = usb_terminal_line_len == 0
            && !usb_terminal_discard_line
            && !usb_binary_startup_probe.isActive();
        if (!ota_console_usb_handoff.begin(
                usb_ascii_selected, hasObservableActiveUsbTerminalClient(),
                usb_input_idle,
                usb_serial_interface.getCompletedFrameCount())) {
          ota_console_client.print(
              "ERROR: active USB currently owns the Full Companion terminal\r\n");
          ota_console_client.stop();
          return;
        }
        if (usb_ascii_selected) leaveUsbTerminalMode(false);
#endif
        if (!the_mesh.enterNetworkTerminalMode(ota_console_client)) {
#if defined(ENABLE_USB_INTERFACE)
          if (ota_console_usb_handoff.shouldRestoreAscii(
                  usb_serial_interface.getCompletedFrameCount())) {
            enterUsbTerminalMode();
          }
#endif
          ota_console_client.print(
              "ERROR: another client currently owns the Full Companion terminal\r\n");
          ota_console_client.stop();
        }
#else
        ota_console_client.print("OTA console - type `ota ...`\r\n> ");
#endif
      }
      return;
    }
#if COMPANION_FEATURE_NETWORK_TERMINAL
    if (!the_mesh.isNetworkTerminalMode(ota_console_client)) {
#if defined(ENABLE_USB_INTERFACE)
      ota_console_usb_handoff.cancel();
#endif
      ota_console_client.print(
          "\r\nERROR: terminal ownership moved to USB; closing\r\n");
      ota_console_client.stop();
      ota_console_clear_line();
      ota_console_discard_line = false;
      return;
    }
#endif
    while (ota_console_client.available()) {
      char ch = (char)ota_console_client.read();
      if (ota_console_discard_line) {
        if (ch == '\r' || ch == '\n') {
          ota_console_discard_line = false;
          ota_console_client.print("  ERROR: command too long\r\n> ");
        }
        continue;
      }
      if (ch == '\r' || ch == '\n') {
        if (ota_console_len == 0) continue;                            // ignore blanks / the CRLF pair
        ota_console_line[ota_console_len] = 0;
#if COMPANION_FEATURE_NETWORK_TERMINAL
        if (strcmp(ota_console_line, "disconnect") == 0) {
          ota_console_client.print("  OK - disconnecting\r\n");
          ota_console_release_terminal();
          ota_console_client.stop();
          ota_console_clear_line();
          ota_console_discard_line = false;
          return;
        }
        the_mesh.handleTerminalCommand(ota_console_line);
        ota_console_client.print("> ");
#else
        char reply[160]; reply[0] = 0;
        if (!mesh::ota::handle_ota_command(ota_console_line, reply, board))
          strcpy(reply, "only `ota ...` commands are supported on this console");
        ota_console_client.print("  -> "); ota_console_client.print(reply); ota_console_client.print("\r\n> ");
#endif
        ota_console_clear_line();
      } else if (ota_console_len < sizeof(ota_console_line) - 1) {
        ota_console_line[ota_console_len++] = ch;
      } else {
        ota_console_clear_line();
        ota_console_discard_line = true;
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

  static void serviceCompanionWiFiCredentialReload() {
    if (!companion_wifi_credential_reload_pending
        || static_cast<int32_t>(millis() - companion_wifi_credential_reload_at) < 0) {
      return;
    }
#ifdef WITH_WEBCONFIG
    if (the_mesh.isWebConfigActiveOrStopping()) return;
#endif

    companion_wifi_credential_reload_pending = false;
    companion_wifi_credential_reload_at = 0;
    loadCompanionWiFiCredentials();
    resetCompanionWiFiRecoveryState();
    if (!companion_wifi_requested || !companion_wifi_active
        || !companion_wifi_has_credentials) {
      return;
    }

    WiFi.mode(WIFI_STA);
    mesh::wifi::applyProtocolMask(WIFI_IF_STA);
    mesh::wifi::setStationAutoReconnect(true);
    WiFi.disconnect(false, false);
    mesh::wifi::beginStation(
        configured_wifi_ssid, configured_wifi_password);
    wifi_reconnect_tracker.noteAttempt(millis());
    WIFI_DEBUG_PRINTLN("WiFi credentials reloaded; reconnecting to saved SSID");
  }

  static void startCompanionWiFi() {
    if (companion_wifi_active) return;

    board.setInhibitSleep(true);
    mesh::wifi::setStationAutoReconnect(true);
    resetCompanionWiFiRecoveryState();

    if (companion_wifi_has_credentials) {
      WiFi.mode(WIFI_STA);
      mesh::wifi::applyProtocolMask(WIFI_IF_STA);
      wifi_reconnect_tracker.noteDisconnected(millis());
      mesh::wifi::beginStation(
          configured_wifi_ssid, configured_wifi_password);
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

    applyCompanionWiFiPowerSave();
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

  static void stopCompanionInfrastructureWiFi() {
    WiFi.setAutoReconnect(false);
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
    // ESP-NOW is the mesh radio on this target. Drop the station/AP services
    // without stopping the driver, then restore its fixed LR mesh channel.
    WiFi.disconnect(false, false);
    WiFi.mode(WIFI_STA);
    mesh::wifi::applyProtocolMask(WIFI_IF_STA);
    mesh::wifi::restoreEspNowChannel();
#else
    WiFi.disconnect(true, false);
    WiFi.mode(WIFI_OFF);
#endif
  }

  static bool finishStoppingCompanionWiFi() {
    cancelCompanionWiFiNtp(false);
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

    stopCompanionInfrastructureWiFi();
    board.setInhibitSleep(false);
    resetCompanionWiFiRecoveryState();
    companion_wifi_active = false;
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
    WIFI_DEBUG_PRINTLN(
        "Companion WiFi services disabled; ESP-NOW mesh radio remains active");
#else
    WIFI_DEBUG_PRINTLN("WiFi radio disabled by BOOT/GPIO 0 control");
#endif
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
  static uint32_t companion_bluetooth_start_at = 0;
  static constexpr uint32_t COMPANION_BLUETOOTH_RETRY_MS = 5000UL;

  static void scheduleCompanionBluetoothRetry() {
    companion_bluetooth_start_at = millis() + COMPANION_BLUETOOTH_RETRY_MS;
    if (companion_bluetooth_start_at == 0) companion_bluetooth_start_at = 1;
  }

  static void startCompanionBluetooth() {
    if (companion_bluetooth_initialized) return;
    companion_bluetooth_start_at = 0;

    mesh::usbLoggingPort().println("Companion: starting Bluetooth");
    if (!interface_manager.addInterface(InterfaceType::Bluetooth,
                                        &bluetooth_interface)) {
      mesh::usbLoggingPort().println(
          "Companion: no interface slot available for Bluetooth; retrying");
      scheduleCompanionBluetoothRetry();
      return;
    }
    CompanionNodePrefs* prefs = the_mesh.getNodePrefs();
    const bool custom_bluetooth_name =
        mesh::companion::hasCustomBluetoothName(prefs->bluetooth_name);
    if (!bluetooth_interface.begin(custom_bluetooth_name ? "" : BLE_NAME_PREFIX,
                                   custom_bluetooth_name
                                       ? prefs->bluetooth_name
                                       : prefs->node_name,
                                   the_mesh.getBLEPin())) {
      interface_manager.removeInterface(&bluetooth_interface);
      mesh::usbLoggingPort().println(
          "Companion: Bluetooth initialization failed; retrying in 5 seconds");
      scheduleCompanionBluetoothRetry();
      return;
    }
#if defined(ESP32_PLATFORM) && COMPANION_BT_MODEM_SLEEP_AVAILABLE
    applyCompanionBluetoothSleep(
        the_mesh.getNodePrefs()->powersaving_enabled != 0, false);
#endif
    companion_bluetooth_initialized = true;
    if (interface_manager.isEnabled()) bluetooth_interface.enable();
  }

  static void serviceDeferredCompanionBluetooth() {
    if (companion_bluetooth_initialized) return;
    if (companion_bluetooth_start_at == 0
        || (int32_t)(millis() - companion_bluetooth_start_at) < 0) return;
    startCompanionBluetooth();
  }
#endif

#if defined(ESP32_PLATFORM) && COMPANION_FEATURE_MEMORY_DIAGNOSTICS
  static void logFullCompanionMemory(const char* stage) {
    mesh::usbLoggingPort().printf(
        "Full Companion memory %s: heap=%u largest_internal=%u psram_free=%u/%u offline_queue=%d\r\n",
        stage, (unsigned)ESP.getFreeHeap(),
        (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
        (unsigned)ESP.getFreePsram(), (unsigned)ESP.getPsramSize(),
        the_mesh.getOfflineQueueCapacity());
  }
#endif

void setup() {
#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT \
    && defined(ENABLE_USB_INTERFACE)
  // Configure HWCDC before begin() allocates its mutex and enables the USB ISR.
  // The bundled setTxBufferSize() deletes/recreates its ring without taking the
  // TX mutex or masking that ISR, so even an otherwise-quiet post-begin resize
  // can race the interrupt handler.
  // A failed resize deletes HWCDC's existing ring. Try progressively smaller
  // queues while setup still has no producers, and tell reset cleanup the exact
  // capacity it must observe before reopening a host session.
  static const size_t usb_tx_sizes[] = {
      MESH_ESP32_USB_TX_BUFFER_SIZE, 2048, 1024, 512, 256};
  size_t usb_tx_capacity = 0;
  for (size_t candidate : usb_tx_sizes) {
    usb_tx_capacity = Serial.setTxBufferSize(candidate);
    if (usb_tx_capacity == candidate) break;
  }
  Serial.setTxTimeoutMs(5);
#endif
  Serial.begin(115200);
#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT \
    && defined(ENABLE_USB_INTERFACE)
  // If every pre-begin allocation failed, begin() may have recovered by
  // creating its built-in 256-byte ring. Discover that ring without replacing
  // it after the ISR is live; a zero result leaves reset cleanup quarantined.
  if (usb_tx_capacity == 0) {
    usb_tx_capacity = Serial.availableForWrite();
  }
  mesh::setUsbCompanionTxBufferCapacity(usb_tx_capacity);
#endif
#if MESH_PACKET_LOGGING
  mesh::serialLogBegin();
#endif
  mesh::beginUsbLoggingPort();
  board.begin();

#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.begin();
#endif

#ifdef DISPLAY_CLASS
  DisplayDriver* disp = NULL;
  if (display.begin()) {
    disp = &display;
  #if defined(ESP32) && defined(WIFI_SSID) && defined(WITH_WEBCONFIG) \
      && UI_WIFI_SETUP_HOME_PAGE != 1
    companion_setup_display = disp;
  #endif
    disp->startFrame();
  #ifdef ST7789
    disp->setTextSize(2);
  #endif
    disp->drawTextCentered(disp->width() / 2, 28, "Loading...");
    disp->endFrame();
  }
#if defined(ESP32) && defined(WIFI_SSID)
  companion_display_available = disp != NULL;
#if defined(SENSECAP_INDICATOR_LORA)
  companion_display_begin_status = display.beginStatus();
#else
  companion_display_begin_status = companion_display_available ? 1 : 0;
#endif
#endif
#endif

  int radioinit_attempts = 0;
  bool radio_available = false;
  while (!(radio_available = radio_init())) {
    ++radioinit_attempts;
    MESH_DEBUG_PRINTLN("Radio init failed! (attempt %d)", radioinit_attempts);
    if (radioinit_attempts >= 3) {
#if defined(RECOVERABLE_EXTERNAL_RADIO)
      // Continue into a recovery-capable Companion instead of trapping native
      // USB in a reboot loop. The main loop retries the radio independently.
      mesh::usbLoggingPort().println(
          "Radio unavailable; starting companion management services");
      break;
#else
      MESH_DEBUG_PRINTLN("Radio init failed 3x - rebooting");
      board.reboot();
#endif
    }
    delay(500);
  }

#ifdef RECOVERABLE_EXTERNAL_RADIO
  companion_radio_available = radio_available;
  companion_radio_retry_at = millis() + COMPANION_RADIO_RETRY_MS;
  fast_rng.begin(radio_available ? radio_driver.getRngSeed()
                                 : radio_fallback_rng_seed());
#ifdef DISPLAY_CLASS
  if (!radio_available && disp != NULL) {
    disp->startFrame();
    disp->drawTextCentered(disp->width() / 2, 20, "Radio unavailable");
    disp->drawTextCentered(disp->width() / 2, 40, "Starting interfaces...");
    disp->endFrame();
  }
#endif
#else
  fast_rng.begin(radio_driver.getRngSeed());
#endif

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#if defined(NRF52_PLATFORM)
  // InternalFileSystem::begin() auto-formats the entire primary store after a
  // mount failure. The base mount plus a full raw scan distinguishes a virgin
  // erased device from nonblank/corrupt identity storage. Only proven-erased
  // media may be initialized automatically; format is followed by a required
  // base remount because Adafruit_LittleFS::format() does not remount an
  // already-unmounted instance.
  const mesh::storage::InternalSecondaryFsBootResult primary_fs_boot =
      mesh::storage::prepareInternalSecondaryFilesystem(
          []() -> bool {
            return InternalFS.Adafruit_LittleFS::begin();
          },
          []() -> bool {
            return mesh::storage::isErasedFlashRange(
                INTERNAL_PRIMARY_FS_START, INTERNAL_PRIMARY_FS_SIZE,
                [](uint32_t address) -> uint32_t {
                  return *reinterpret_cast<const volatile uint32_t*>(address);
                });
          },
          []() -> bool {
            InternalFS.end();
            return InternalFS.format();
          });
  if (primary_fs_boot
          == mesh::storage::InternalSecondaryFsBootResult::PreservedNonBlank
      || primary_fs_boot
          == mesh::storage::InternalSecondaryFsBootResult::InitializationFailed) {
    if (primary_fs_boot
        == mesh::storage::InternalSecondaryFsBootResult::PreservedNonBlank) {
      MESH_DEBUG_PRINTLN(
          "InternalFS: mount failed; preserving nonblank primary storage and blocking startup writes");
    } else {
      MESH_DEBUG_PRINTLN(
          "InternalFS: erased primary storage initialization failed; blocking startup writes");
    }
    store.markPrimaryFSUnavailable();
  }
#else
  InternalFS.begin();
#endif
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
      store.disableSecondaryFS(true);
    } else {
      MESH_DEBUG_PRINTLN("CustomLFS_QSPIFlash: initialized successfully");
    }
  #else
  #if defined(EXTRAFS)
      const bool extra_fs_geometry_valid =
          mesh::storage::isExpectedInternalExtraFsGeometry(
              ExtraFS.getFlashAddr(), ExtraFS.getFlashSize(),
              ExtraFS.getBlockSize())
          && mesh::storage::isInternalExtraFsReservedByApplication(
              (uint32_t)(uintptr_t)__flash_arduino_end);
      // Initial mount is always non-destructive, even for blank media.
      // DataStore validates primary first, then retries/rebuilds ExtraFS.
      if (!extra_fs_geometry_valid
          || !ExtraFS.Adafruit_LittleFS::begin()) {
        if (!extra_fs_geometry_valid) {
          MESH_DEBUG_PRINTLN("CustomLFS: internal ExtraFS geometry is not 100 KiB; refusing to mount or erase it");
        } else {
          MESH_DEBUG_PRINTLN("CustomLFS: internal ExtraFS mount failed; deferring automatic recovery until primary validation");
        }
        store.disableSecondaryFS(true);
      }
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

#if COMPANION_FEATURE_BLE_MOTA_SOURCE
  the_mesh.setMotaSourceControl(&ble_mota_source_control);
#endif

  // nRF52 cannot decide whether to add its optional logging CDC interface
  // until the saved Companion preferences above are available. Single-TTY
  // platforms have no separate port, so this is a harmless no-op there.
  mesh::beginUsbLoggingPort();

// Lock the saved transport selection before bringing up either wireless stack.
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  loadCompanionTransportModeForBoot();
  if (companionTransportWiFiActiveAtBoot()) {
    // This boot will never initialize Bluetooth. Reclaim both the controller
    // and host allocations before WiFi starts; the release is irreversible
    // until reboot, which is why transport changes are next-boot-only.
    releaseCompanionBluetoothMemoryForWiFi();
  }
#if defined(INDICATOR_TRANSPORT_RENDER_PROFILE) && defined(DISPLAY_CLASS)
  if (disp != nullptr) {
    const mesh::ui::IndicatorRenderProfile render_profile =
        mesh::ui::selectIndicatorRenderProfile(
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
            true,
#else
            false,
#endif
            companionTransportWiFiActiveAtBoot());
    const bool selected = disp->setRenderScale(
        render_profile.coordinate_scale, render_profile.output_zoom);
    mesh::usbLoggingPort().printf(
        "Indicator render: %dx%d internal canvas%s\r\n",
        disp->renderWidth(), disp->renderHeight(),
        selected ? "" : " (requested profile unavailable; restored fallback)");
  }
#endif
#endif

// Load WiFi credentials without starting the radio.
#ifdef WIFI_SSID
  loadCompanionWiFiCredentials();
#endif

#if defined(ESP32_PLATFORM) && COMPANION_FEATURE_MEMORY_DIAGNOSTICS
  logFullCompanionMemory("before interfaces");
#endif

// Generic WiFi+BLE builds reserve NimBLE first. The Indicator exclusive
// profile instead starts Bluetooth only when it was selected for this boot.
#if defined(BLE_PIN_CODE) && defined(ESP32) && defined(WIFI_SSID)
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  if (!companionTransportWiFiActiveAtBoot()) startCompanionBluetooth();
#else
  startCompanionBluetooth();
#endif
#endif

// add wifi interface
#ifdef WIFI_SSID
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  if (companionTransportWiFiActiveAtBoot()) {
#endif
  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info){
      if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
          mesh::wifi::restoreEspNowChannel();
#endif
          if (companion_wifi_requested) {
            WIFI_DEBUG_PRINTLN("WiFi disconnected; automatic recovery is active");
          }
      } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
          if (!mesh::wifi::enforceStationChannel()) {
            WIFI_DEBUG_PRINTLN(
                "Rejected WiFi association outside ESP-NOW channel %u",
                (unsigned)mesh::wifi::activeEspNowChannel());
            return;
          }
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
          // Association can replace the station protocol bitmap after the
          // pre-connect setup. Restore LR receive support once the AP has
          // finished configuring the shared radio; B/G/N remain enabled for
          // the infrastructure connection.
          if (mesh::wifi::applyProtocolMask(WIFI_IF_STA) != ESP_OK) {
            WIFI_DEBUG_PRINTLN(
                "Failed to restore ESP-NOW/WiFi protocol coexistence");
          }
#endif
          WIFI_DEBUG_PRINTLN("connected! IP %s  (companion app on :%d)",
                             WiFi.localIP().toString().c_str(), TCP_PORT);
      }
  });

  interface_manager.addInterface(InterfaceType::WiFi, &wifi_interface);
#if !defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  companion_wifi_requested = the_mesh.getNodePrefs()->wifi_enabled != 0;
#endif
  if (companion_wifi_requested) {
    startCompanionWiFi();
  } else {
    stopCompanionInfrastructureWiFi();
    board.setInhibitSleep(false);
    WIFI_DEBUG_PRINTLN("WiFi remains off from the saved BOOT/GPIO 0 setting");
  }
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  }
#endif
#endif

#if defined(ESP32_PLATFORM) && COMPANION_FEATURE_MEMORY_DIAGNOSTICS
  logFullCompanionMemory("after WiFi start");
#endif

// ESP32 WiFi+BLE companions started BLE above so its controller memory could
// not be fragmented by WiFi. Other BLE companions start here.
#if defined(BLE_PIN_CODE)
  #if !(defined(ESP32) && defined(WIFI_SSID))
    startCompanionBluetooth();
  #endif
#endif

// add usb interface
#if defined(ENABLE_USB_INTERFACE)
#if COMPANION_FEATURE_USB_MOTA_SOURCE
  usb_serial_interface.begin(mesh::usbCompanionPort(),
                             USB_TERMINAL_START_TOKEN, USB_MOTA_START_TOKEN);
#else
  usb_serial_interface.begin(mesh::usbCompanionPort(),
                             USB_TERMINAL_START_TOKEN);
#endif
  // keep frames intact and pace the contact stream when the host is slow
  usb_serial_interface.enableFlowControl(true);
#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  // The ESP32 USB-Serial-JTAG peripheral (HWCDC) has no DTR concept at all.
  // Its bool operator is a transient RX/TX activity heuristic, and even the
  // SOF-based host-presence flag can briefly flap around a bus reset. A
  // complete command frame is stronger proof that a client is listening, so
  // grant it a short lease in which the response can be queued and drained.
  // Outside that lease, require the physical host signal as well as recent
  // Binary Companion activity so an idle cable does not claim the interface.
  // The idle cap stays absolute: an abandoned multi-frame response must not
  // lock BLE or WiFi dispatch forever merely because SOFs continue.
  usb_serial_interface.setConnectedCheck([]() {
    return usb_hwcdc_host_presence.isClientConnected(
        board.isUsbHostConnected(), millis(),
        usb_serial_interface.hasReceivedFrame(),
        usb_serial_interface.getLastFrameMillis(),
        usb_serial_interface.getCompletedFrameCount(),
        USB_FRAME_REPLY_GRACE_MS, USB_CLIENT_IDLE_TIMEOUT,
        USB_HOST_LOSS_EDGE_MS,
        USB_HOST_LOSS_GRACE_MS,
        the_mesh.hasFiniteDelayedReplyForRoute(&usb_serial_interface));
  });
#elif (defined(ESP32) && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT) \
    || defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
  // native USB-CDC (TinyUSB): (bool)Serial reflects DTR, ie. the host really
  // has the port open. A classic ESP32 behind a UART bridge has no such
  // signal and keeps the assume-connected default.
  usb_serial_interface.setConnectedCheck([]() { return (bool)Serial; });
#endif
  interface_manager.addInterface(InterfaceType::USB, &usb_serial_interface);
#if MESH_USB_LOGGING_AVAILABLE
  if (!mesh::hasDedicatedUsbLoggingPort()
      && mesh::isUsbLoggingEnabled()) {
    // Apply a saved single-TTY logging preference before the dispatcher can
    // emit its first framed Companion response on this interface.
    enterUsbLoggingTerminalMode();
  }
#endif
#if defined(COMPANION_RADIO_FULL)
  if (!the_mesh.isTerminalMode()) {
    // Full Companion's primary USB port is an ASCII CLI until a Companion
    // client presents a valid binary frame. BLE/WiFi/Ethernet stay binary.
    enterUsbTerminalMode();
  }
#endif
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
#if defined(ENABLE_USB_INTERFACE)
  serviceUsbTerminalHostSessionReset();
#endif
  // Identify nRF52 CDC 1 when a terminal opens it. Doing this on the connection
  // edge avoids losing the marker before the host has opened the port.
  mesh::serviceUsbLoggingPort();
#if defined(ENABLE_USB_INTERFACE)
  // Drain functional CDC0 text before the mesh can emit a Binary Companion or
  // serial-mOTA frame in this iteration.
  mesh::serviceUsbTerminalPort();
#endif
#if defined(ENABLE_USB_INTERFACE) && defined(COMPANION_RADIO_FULL)
  expireUsbBinaryStartupProbeBeforeDispatch();
#endif
  the_mesh.loop();
#ifdef RECOVERABLE_EXTERNAL_RADIO
  serviceCompanionRadioRecovery();
#endif
#if defined(ENABLE_USB_INTERFACE)
  serviceUsbTerminal();
  // Commands can synchronously enqueue a multi-line reply. Give it the
  // remaining CDC FIFO capacity now; later loops retain any unwritten suffix.
  mesh::serviceUsbTerminalPort();
#endif
  interface_manager.loop();
#if COMPANION_FEATURE_BLE_MOTA_SOURCE
  ble_mota_source_control.loop();
#endif
  sensors.loop();
#ifdef DISPLAY_CLASS
  #ifdef INDICATOR_WIFI_FONT_RECOVERY
  // The Indicator keeps rendering with its built-in fallback while a missing
  // SD font is recovered by a background TLS task.  This poll only launches
  // work after station Wi-Fi connects and installs a completed font on the
  // main/UI task.
  display.serviceFontRecovery();
  #endif
  #if defined(ESP32) && defined(WIFI_SSID) && defined(WITH_WEBCONFIG) \
      && UI_WIFI_SETUP_HOME_PAGE != 1
  ui_task.servicePairingState();
  if (ui_task.isPairingPromptActive()) {
    ui_task.loop();
  } else if (isCompanionWiFiEnabled()
      && the_mesh.isWebConfigSetupActive()) {
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
#ifdef TBEAM_1W
  board.updateFanControl();
#endif
#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.loop();
#endif

#ifdef ESP32_PLATFORM
  serviceCompanionPowerSaving();
#endif

  // USB power alone (for example, a wall charger) must not disable power
  // saving. Stay awake only while an enumerated USB host is attached.
  bool can_sleep = the_mesh.getNodePrefs()->powersaving_enabled
      && !the_mesh.hasPendingWork();
#if defined(NRF52_PLATFORM) \
    || (defined(ESP32_PLATFORM) && defined(ENABLE_USB_INTERFACE))
  can_sleep = can_sleep && !board.isUsbHostConnected();
#endif
#if defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) \
    && MOMENTARY_BUTTON_WAKE_FROM_SLEEP \
    && defined(PIN_USER_BTN) && defined(DISPLAY_CLASS)
  // GPIO edges wake the nRF52 poller, but multi-click and long-press results
  // are due after a time interval rather than another edge. Stay awake only
  // while that small gesture state machine has a pending deadline.
  can_sleep = can_sleep && !user_btn.needsPolling();
#endif
  if (can_sleep) {
#if defined(NRF52_PLATFORM)
    board.sleep(0); // nrf ignores seconds param, sleeps whenever possible
#elif defined(ESP32_PLATFORM)
#if COMPANION_IDF_PM_AVAILABLE
    // Yield long enough for ESP-IDF automatic light sleep to enter when no
    // driver holds a power-management lock.
    vTaskDelay(pdMS_TO_TICKS(10));
#elif defined(ENABLE_USB_INTERFACE) \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT \
    && !defined(BLE_PIN_CODE) && !defined(WIFI_SSID) \
    && !defined(ETHERNET_ENABLED) && !defined(SERIAL_RX)
    // The stock Arduino core has no automatic light sleep. A short timer
    // slice gives native-USB-only battery builds real light sleep without
    // delaying radio, GPS, button, or newly attached USB work by more than the
    // normal 10 ms loop cadence. can_sleep already proved no USB host is up.
    if (esp_sleep_enable_timer_wakeup(10000ULL) != ESP_OK
        || esp_light_sleep_start() != ESP_OK) {
      vTaskDelay(pdMS_TO_TICKS(10));
    }
#else
    // Connected transports need their own modem sleep and must retain the
    // normal FreeRTOS idle behavior.
    vTaskDelay(pdMS_TO_TICKS(10));
#endif
#elif defined(RP2040_PLATFORM) || defined(STM32_PLATFORM)
    board.sleep(0); // event-driven idle; interrupts wake the main loop
#endif
  }

#if defined(ESP32) && defined(WIFI_SSID)
  #ifdef WITH_WEBCONFIG
    the_mesh.serviceWebConfig();
    if (companion_wifi_setup_stop_requested) {
      companion_wifi_setup_stop_requested = false;
      the_mesh.stopWebConfig();
    }
    if (companion_wifi_setup_requested) {
      companion_wifi_setup_requested = false;
      char web_reply[160];
      if (!the_mesh.startWebConfig(true, web_reply)) {
        WIFI_DEBUG_PRINTLN("Display WiFi setup request: %s", web_reply);
      }
    }
  #endif
  serviceCompanionWiFiCredentialReload();
  serviceCompanionWiFiState();
  if (companion_wifi_requested && companion_wifi_active) {
  #ifdef ENABLE_OTA
    ota_console_loop();  // service the OTA text console (port 5002)
  #endif
  const unsigned long wifi_now = millis();
  const bool station_channel_ok = mesh::wifi::enforceStationChannel();
  if (station_channel_ok && WiFi.status() == WL_CONNECTED) {
    wifi_reconnect_tracker.noteConnected();
    wifi_setup_attempted = false;
#ifdef WITH_WEBCONFIG
    // startAutoMode() can raise the setup AP after its own short connection
    // timeout, before the main-loop fallback marks recovery mode. A successful
    // saved-SSID retry should close either kind of recovery AP.
    if (configured_wifi_ssid[0] && the_mesh.isWebConfigSetupActive()
        && (wifi_setup_recovery_mode
            || the_mesh.isWebConfigWiFiRecoveryActive())) {
      mesh::wifi::setStationAutoReconnect(true);
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

  // Reassert the saved credentials every five minutes after an AP outage.
  // Primary ESP-NOW builds deliberately disable the driver's unconstrained
  // auto-reconnect; ordinary builds retain it and use this as a recovery path.
  // WebConfig's
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
      mesh::wifi::setStationAutoReconnect(true);
    }
#else
    WiFi.mode(WIFI_STA);
    mesh::wifi::setStationAutoReconnect(true);
#endif
    WiFi.disconnect(false, false);
    mesh::wifi::beginStation(
        configured_wifi_ssid, configured_wifi_password);
  }
#ifdef WITH_MQTT_BRIDGE
  the_mesh.serviceMQTT(configured_wifi_ssid, configured_wifi_password);
  serviceCompanionWiFiNtp();
#else
  serviceCompanionWiFiNtp();
#endif
  }
#endif
#if defined(BLE_PIN_CODE)
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  if (!companionTransportWiFiActiveAtBoot()) {
    serviceDeferredCompanionBluetooth();
  }
#else
  serviceDeferredCompanionBluetooth();
#endif
#endif
}
