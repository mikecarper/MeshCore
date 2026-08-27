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
    #include <helpers/WiFiReconnectPolicy.h>
    #include <helpers/WiFiPowerSave.h>
    #include <Preferences.h>
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
#ifdef ESP32_CPU_FREQ
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

static void enterUsbTerminalMode() {
#if defined(COMPANION_RADIO_FULL)
  usb_binary_startup_probe.cancel();
#endif
  the_mesh.cancelSerialResponseStream();
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
    Serial.print("\r\nOK - Binary mode\r\n");
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
    Serial.print("\r\n");
    Serial.print(reply);
    Serial.print("\r\nOK - Binary mode\r\n");
  }
  resetUsbMotaMode();
}

static bool enterUsbMotaMode(mesh::UsbMotaEntryOrigin origin) {
  the_mesh.cancelSerialResponseStream();
  usb_serial_interface.setPassthroughMode(true);
  usb_mota_mode = true;
  usb_mota_line_len = 0;
  usb_mota_line[0] = 0;
  usb_mota_disconnect_armed = isUsbTerminalDataConnected();

  char reply[160] = {0};
  if (!the_mesh.handleLocalControlCommand("ota folder on", reply, sizeof(reply))
      || strncmp(reply, "ERR", 3) == 0) {
    Serial.print("\r\n");
    Serial.print(reply[0] ? reply : "ERR could not enter mOTA seeder mode");
    Serial.print("\r\n");
    resetUsbMotaMode();
    if (mesh::shouldRestoreAsciiAfterMotaFailure(origin)) {
      enterUsbTerminalMode();
    }
    return false;
  }
  Serial.print("\r\n");
  Serial.print(reply);
  Serial.print("\r\n");
  return true;
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
#if COMPANION_FEATURE_USB_MOTA_SOURCE
  if (usb_mota_mode) {
    serviceUsbMota();
    return;
  }
#endif
  // A saved logging-on preference makes the one available TTY behave like a
  // logging repeater: plaintext diagnostics plus an input-capable CLI. Put the
  // Companion interface into passthrough before it can mix framed traffic with
  // logs. `set usb.logging off` remains available here and returns this TTY to
  // Binary Companion after its command reply, even on USB-UART bridges that
  // cannot detect a host disconnect.
#if MESH_USB_LOGGING_AVAILABLE
  if (!mesh::hasDedicatedUsbLoggingPort()) {
    if (mesh::isUsbLoggingEnabled()) {
      if (!the_mesh.isTerminalMode()) {
        enterUsbLoggingTerminalMode();
        return;
      }
      usb_logging_terminal_mode = true;
    } else if (usb_logging_terminal_mode && the_mesh.isTerminalMode()) {
      leaveUsbTerminalMode(true);
      return;
    }
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

  if (isUsbTerminalDataConnected()) {
    usb_terminal_disconnect_armed = true;
  } else if (usb_terminal_disconnect_armed) {
    leaveUsbTerminalMode(false);
    return;
  }

#if defined(COMPANION_RADIO_FULL)
  // Full Companion boots as a useful ASCII terminal. MeshCLI's first framed
  // command begins with '<'; hand that byte over untouched at an empty prompt.
  // A malformed or accidental probe times out and restores the terminal.
  if (!usb_logging_terminal_mode
      && usb_binary_startup_probe.shouldStart(
          usb_terminal_line_len == 0, usb_terminal_discard_line,
          Serial.peek())) {
    const uint32_t frame_count = usb_serial_interface.getCompletedFrameCount();
    leaveUsbTerminalMode(false);
    usb_binary_startup_probe.start(millis(), frame_count);
    return;
  }
#endif

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
        leaveUsbTerminalMode(true);
        return;
      }
#endif
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

#if defined(COMPANION_RADIO_FULL)
static void expireUsbBinaryStartupProbeBeforeDispatch() {
  const uint32_t now = millis();
  if (!usb_binary_startup_probe.hasTimedOut(now)) return;

  // The dispatcher normally consumes Binary Companion input before the ASCII
  // terminal service runs. Enforce the advertised deadline here so bytes that
  // are still incomplete at one second cannot complete a frame afterwards.
  // Entering passthrough resets the partial binary parser; drain only bytes
  // already queued for that expired attempt so they cannot begin a new probe.
  int pending = Serial.available();
  while (pending-- > 0) Serial.read();
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
  static char configured_wifi_password[64];
  static bool companion_wifi_has_credentials = false;
  static bool companion_wifi_requested = true;
  static bool companion_wifi_active = false;
  static bool companion_wifi_disable_in_progress = false;
  static bool companion_wifi_services_stopped = false;
  static bool companion_wifi_credential_reload_pending = false;
  static unsigned long companion_wifi_credential_reload_at = 0;
  static bool companion_wifi_power_save_loaded = false;
  static uint8_t companion_wifi_power_save = mesh::wifi::kDefaultPowerSave;

  static bool companionWiFiBluetoothActive() {
#if defined(BLE_PIN_CODE)
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
        configured, companionWiFiBluetoothActive());
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
    if (mesh::wifi::effectivePowerSave(mode, companionWiFiBluetoothActive())
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
        && !WiFiSetupPortal::isPlaceholderSSID(WIFI_SSID)) {
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
    WiFi.setAutoReconnect(true);
    WiFi.disconnect(false, false);
    WiFi.begin(configured_wifi_ssid, configured_wifi_password);
    wifi_reconnect_tracker.noteAttempt(millis());
    WIFI_DEBUG_PRINTLN("WiFi credentials reloaded; reconnecting to saved SSID");
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
  Serial.begin(115200);
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

#if COMPANION_FEATURE_BLE_MOTA_SOURCE
  the_mesh.setMotaSourceControl(&ble_mota_source_control);
#endif

  // nRF52 cannot decide whether to add its optional logging CDC interface
  // until the saved Companion preferences above are available. ESP32 already
  // fixed its descriptor from the early NVS mirror, so this is harmless there.
  mesh::beginUsbLoggingPort();

// Load WiFi state before bringing up either wireless stack.
#ifdef WIFI_SSID
  loadCompanionWiFiCredentials();
#endif

#if defined(ESP32_PLATFORM) && COMPANION_FEATURE_MEMORY_DIAGNOSTICS
  logFullCompanionMemory("before interfaces");
#endif

// NimBLE must reserve its internal controller/host memory before WiFi starts
// its AP and WebConfig services. Starting WiFi first can leave plenty of total
// heap but no internal block large enough for nimble_port_init().
#if defined(BLE_PIN_CODE) && defined(ESP32) && defined(WIFI_SSID)
  startCompanionBluetooth();
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
  usb_serial_interface.begin(Serial, USB_TERMINAL_START_TOKEN, USB_MOTA_START_TOKEN);
#else
  usb_serial_interface.begin(Serial, USB_TERMINAL_START_TOKEN);
#endif
  // keep frames intact and pace the contact stream when the host is slow
  usb_serial_interface.enableFlowControl(true);
#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  // a 256 byte TX buffer overflows during a contact sync, and write() blocks
  // up to tx_timeout_ms per call against a stalled host (same reasoning as
  // the kiss_modem tuning)
  Serial.setTxBufferSize(4096);
  Serial.setTxTimeoutMs(5);
  // The ESP32 USB-Serial-JTAG peripheral (HWCDC) has no DTR concept at all:
  // (bool)Serial only tells us the host has enumerated the device, which is
  // already true when the cable is plugged into a powered port. Fall back to
  // activity: a real client has to send us frames.
  usb_serial_interface.setConnectedCheck([]() {
    uint32_t last = usb_serial_interface.getLastFrameMillis();
    return (bool)Serial && usb_serial_interface.hasReceivedFrame()
        && (millis() - last) < USB_CLIENT_IDLE_TIMEOUT;
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
  // Identify CDC 1 when a terminal opens it. Doing this on the connection edge
  // avoids losing the marker during boot before the host has opened the port.
  mesh::serviceUsbLoggingPort();
#if defined(ENABLE_USB_INTERFACE) && defined(COMPANION_RADIO_FULL)
  expireUsbBinaryStartupProbeBeforeDispatch();
#endif
  the_mesh.loop();
#ifdef RECOVERABLE_EXTERNAL_RADIO
  serviceCompanionRadioRecovery();
#endif
#if defined(ENABLE_USB_INTERFACE)
  serviceUsbTerminal();
#endif
  interface_manager.loop();
#if COMPANION_FEATURE_BLE_MOTA_SOURCE
  ble_mota_source_control.loop();
#endif
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
  // saving. Stay awake only while an enumerated USB host is attached.
  bool can_sleep = the_mesh.getNodePrefs()->powersaving_enabled
      && !the_mesh.hasPendingWork();
#if defined(NRF52_PLATFORM) \
    || (defined(ESP32_PLATFORM) && defined(ENABLE_USB_INTERFACE))
  can_sleep = can_sleep && !board.isUsbHostConnected();
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
  #endif
  serviceCompanionWiFiCredentialReload();
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
