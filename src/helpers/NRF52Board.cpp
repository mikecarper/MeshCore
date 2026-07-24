#if defined(NRF52_PLATFORM)
#include "NRF52Board.h"
#include "PowerManagementUtils.h"
#include <target.h>

#include <bluefruit.h>
#include "ble_gap.h"
#include "ble_hci.h"
#include <nrf.h>
#include <nrf_soc.h>
#ifdef USE_TINYUSB
#include <Adafruit_TinyUSB.h>
#endif

static BLEDfu bledfu;
static uint16_t ota_conn_handle = BLE_CONN_HANDLE_INVALID;
static bool ota_active = false;
static bool ota_ble_started = false;

// A normal internal-flash operation completes in milliseconds. One minute is
// deliberately generous for other legitimate application work while still
// recovering an indefinitely blocked SoftDevice flash wait without requiring
// a physical power cycle. Builds can override this, or set it to 0 to disable
// the watchdog for diagnostics.
#ifndef NRF52_WATCHDOG_TIMEOUT_SECONDS
#define NRF52_WATCHDOG_TIMEOUT_SECONDS 60UL
#endif

#if NRF52_WATCHDOG_TIMEOUT_SECONDS > 131071UL
#error "NRF52_WATCHDOG_TIMEOUT_SECONDS exceeds the nRF52 WDT counter range"
#endif

static void format_ota_reply(char reply[]) {
  uint8_t mac_addr[6];
  memset(mac_addr, 0, sizeof(mac_addr));
  Bluefruit.getAddr(mac_addr);
  sprintf(reply, "OK - mac: %02X:%02X:%02X:%02X:%02X:%02X", mac_addr[5], mac_addr[4], mac_addr[3],
          mac_addr[2], mac_addr[1], mac_addr[0]);
}

static void connect_callback(uint16_t conn_handle) {
  ota_conn_handle = conn_handle;
  MESH_DEBUG_PRINTLN("BLE client connected");
}

static void disconnect_callback(uint16_t conn_handle, uint8_t reason) {
  (void)reason;
  if (ota_conn_handle == conn_handle) {
    ota_conn_handle = BLE_CONN_HANDLE_INVALID;
  }

  MESH_DEBUG_PRINTLN("BLE client disconnected");
}

void NRF52Board::begin() {
  startup_reason = BD_STARTUP_NORMAL;
}

#if NRF52_WATCHDOG_TIMEOUT_SECONDS > 0
static void reloadWatchdogChannels() {
  // Ordinarily only RR0 is enabled. If a bootloader left the watchdog running
  // with a different reload channel, service every enabled channel.
  const uint32_t enabled_channels = NRF_WDT->RREN & 0xFFUL;
  for (uint8_t channel = 0; channel < 8; channel++) {
    if (enabled_channels & (1UL << channel)) {
      NRF_WDT->RR[channel] = WDT_RR_RR_Reload;
    }
  }
}
#endif

void NRF52Board::feedWatchdog(bool enabled) {
#if NRF52_WATCHDOG_TIMEOUT_SECONDS > 0
  // The nRF52 watchdog cannot be stopped after it starts. When the persisted
  // setting is turned off, deliberately stop reloading it; the resulting
  // watchdog reset is the only software-only way to return it to the stopped
  // state. On that next boot the disabled preference prevents it starting.
  if (!enabled) return;

  const bool running = NRF_WDT->RUNSTATUS != 0;
  if (!running) {
    // Keep running during CPU sleep: the flash-driver failure this protects
    // against sleeps in sd_app_evt_wait(). Pause while halted so breakpoints
    // do not reset a board being debugged.
    NRF_WDT->CONFIG =
        (WDT_CONFIG_SLEEP_Run << WDT_CONFIG_SLEEP_Pos) |
        (WDT_CONFIG_HALT_Pause << WDT_CONFIG_HALT_Pos);
    NRF_WDT->CRV = (uint32_t)(NRF52_WATCHDOG_TIMEOUT_SECONDS * 32768UL);
    NRF_WDT->RREN = WDT_RREN_RR0_Msk;
    NRF_WDT->TASKS_START = 1;
  }

  reloadWatchdogChannels();
#else
  (void)enabled;
#endif
}

void NRF52Board::serviceWatchdog() {
#if NRF52_WATCHDOG_TIMEOUT_SECONDS > 0
  if (NRF_WDT->RUNSTATUS != 0) reloadWatchdogChannels();
#endif
}

#ifdef NRF52_POWER_MANAGEMENT
// Power Management global variables
uint32_t g_nrf52_reset_reason = 0;     // Reset/Startup reason
uint8_t g_nrf52_shutdown_reason = 0;   // Shutdown reason

// Early constructor - runs before SystemInit() clears the registers
// Priority 101 ensures this runs before SystemInit (102) and before
// any C++ static constructors (default 65535)
static void __attribute__((constructor(101))) nrf52_early_reset_capture() {
  g_nrf52_reset_reason = NRF_POWER->RESETREAS;
  g_nrf52_shutdown_reason = NRF_POWER->GPREGRET2;
}

void NRF52Board::initPowerMgr() {
  if (power_mgr_initialized) return;

  // Copy early-captured register values
  reset_reason = g_nrf52_reset_reason;
  shutdown_reason = g_nrf52_shutdown_reason;
  boot_voltage_mv = 0;  // Will be set by checkBootVoltage()

  // Clear registers for next boot
  // Note: At this point SoftDevice may or may not be enabled
  uint8_t sd_enabled = 0;
  sd_softdevice_is_enabled(&sd_enabled);
  if (sd_enabled) {
    sd_power_reset_reason_clr(0xFFFFFFFF);
    sd_power_gpregret_clr(1, 0xFF);
  } else {
    NRF_POWER->RESETREAS = 0xFFFFFFFF;  // Write 1s to clear
    NRF_POWER->GPREGRET2 = 0;
  }

  // Log reset/shutdown info
  if (shutdown_reason != SHUTDOWN_REASON_NONE) {
    MESH_DEBUG_PRINTLN("PWRMGT: Reset = %s (0x%lX); Shutdown = %s (0x%02X)",
      getResetReasonString(reset_reason), (unsigned long)reset_reason,
      getShutdownReasonString(shutdown_reason), shutdown_reason);
  } else {
    MESH_DEBUG_PRINTLN("PWRMGT: Reset = %s (0x%lX)",
      getResetReasonString(reset_reason), (unsigned long)reset_reason);
  }
  power_mgr_initialized = true;
}

const char* NRF52Board::getResetReasonString(uint32_t reason) {
  if (reason & POWER_RESETREAS_RESETPIN_Msk) return "Reset Pin";
  if (reason & POWER_RESETREAS_DOG_Msk) return "Watchdog";
  if (reason & POWER_RESETREAS_SREQ_Msk) return "Soft Reset";
  if (reason & POWER_RESETREAS_LOCKUP_Msk) return "CPU Lockup";
  #ifdef POWER_RESETREAS_LPCOMP_Msk
    if (reason & POWER_RESETREAS_LPCOMP_Msk) return "Wake from LPCOMP";
  #endif
  #ifdef POWER_RESETREAS_VBUS_Msk
    if (reason & POWER_RESETREAS_VBUS_Msk) return "Wake from VBUS";
  #endif
  #ifdef POWER_RESETREAS_OFF_Msk
    if (reason & POWER_RESETREAS_OFF_Msk) return "Wake from GPIO";
  #endif
  #ifdef POWER_RESETREAS_DIF_Msk
    if (reason & POWER_RESETREAS_DIF_Msk) return "Debug Interface";
  #endif
  return "Cold Boot";
}

const char* NRF52Board::getShutdownReasonString(uint8_t reason) {
  switch (reason) {
    case SHUTDOWN_REASON_NONE:         return "None";
    case SHUTDOWN_REASON_LOW_VOLTAGE:  return "Low Voltage";
    case SHUTDOWN_REASON_USER:         return "User Request";
    case SHUTDOWN_REASON_BOOT_PROTECT: return "Boot Protection";
  }
  return "Unknown";
}

bool NRF52Board::checkBootVoltage(const PowerMgtConfig* config) {
  initPowerMgr();

  if (config == nullptr) return true;

  // Use the median of three readings.  A single unsettled ADC sample during a
  // brownout must not put the device into a persistent SYSTEMOFF boot lock.
  uint16_t samples[3];
  for (uint8_t i = 0; i < 3; i++) {
    samples[i] = getBattMilliVolts();
    if (i != 2) delay(5);
  }
  boot_voltage_mv = mesh::power::medianVoltage(samples[0], samples[1], samples[2]);
  
  if (config->voltage_bootlock == 0) return true;  // Protection disabled

  // Skip check if externally powered
  if (isExternalPowered()) {
    MESH_DEBUG_PRINTLN("PWRMGT: Boot check skipped (external power)");
    return true;
  }

  MESH_DEBUG_PRINTLN("PWRMGT: Boot voltage = %u mV (threshold = %u mV)",
      boot_voltage_mv, config->voltage_bootlock);

  // Only trigger shutdown if reading is valid (>1000mV) AND below threshold
  // This prevents spurious shutdowns on ADC glitches or uninitialized reads
  if (mesh::power::shouldBootLock(boot_voltage_mv, config->voltage_bootlock, false)) {
    MESH_DEBUG_PRINTLN("PWRMGT: Boot voltage too low - entering protective shutdown");

    initiateShutdown(SHUTDOWN_REASON_BOOT_PROTECT);
    return false;  // Should never reach this
  }

  return true;
}

void NRF52Board::initiateShutdown(uint8_t reason) {
  enterSystemOff(reason);
}

void NRF52Board::enterSystemOff(uint8_t reason) {
  MESH_DEBUG_PRINTLN("PWRMGT: Entering SYSTEMOFF (%s)", getShutdownReasonString(reason));

  // Record shutdown reason in GPREGRET2
  uint8_t sd_enabled = 0;
  sd_softdevice_is_enabled(&sd_enabled);
  if (sd_enabled) {
    sd_power_gpregret_clr(1, 0xFF);
    sd_power_gpregret_set(1, reason);
  } else {
    NRF_POWER->GPREGRET2 = reason;
  }

  // Flush serial buffers
  Serial.flush();
  delay(100);

  // Enter SYSTEMOFF
  if (sd_enabled) {
    uint32_t err = sd_power_system_off();
    if (err == NRF_ERROR_SOFTDEVICE_NOT_ENABLED) {  //SoftDevice not enabled
      sd_enabled = 0;
    }
  }

  if (!sd_enabled) {
    // SoftDevice not available; write directly to POWER->SYSTEMOFF
    NRF_POWER->SYSTEMOFF = POWER_SYSTEMOFF_SYSTEMOFF_Enter;
  }

  // If we get here, something went wrong. Reset to recover.
  NVIC_SystemReset();
}

void NRF52Board::configureVoltageWake(uint8_t ain_channel, uint8_t refsel) {
  // USB power should always be able to recover a device from SYSTEMOFF, even
  // if voltage comparator setup is unavailable or invalid.
  armVbusWake();
  if (!power_mgr_initialized || !supportsVoltageWake()) {
    MESH_DEBUG_PRINTLN("PWRMGT: LPCOMP wake skipped (power manager not ready/unsupported)");
    return;
  }
  if (ain_channel > 7 || refsel > 15) {
    MESH_DEBUG_PRINTLN("PWRMGT: LPCOMP wake skipped (invalid AIN/ref)");
    return;
  }

  // LPCOMP is not managed by SoftDevice - direct register access required
  // Halt and disable before reconfiguration
  NRF_LPCOMP->TASKS_STOP = 1;
  NRF_LPCOMP->ENABLE = LPCOMP_ENABLE_ENABLE_Disabled;

  // Select analog input (AIN0-7 maps to PSEL 0-7)
  NRF_LPCOMP->PSEL = ((uint32_t)ain_channel << LPCOMP_PSEL_PSEL_Pos) & LPCOMP_PSEL_PSEL_Msk;

  // Reference: REFSEL (0-6=1/8..7/8, 7=ARef, 8-15=1/16..15/16)
  NRF_LPCOMP->REFSEL = ((uint32_t)refsel << LPCOMP_REFSEL_REFSEL_Pos) & LPCOMP_REFSEL_REFSEL_Msk;

  // Detect UP events (voltage rises above threshold for battery recovery)
  NRF_LPCOMP->ANADETECT = LPCOMP_ANADETECT_ANADETECT_Up;

  // Do not add comparator hysteresis here.  On divided battery inputs it can
  // shift the effective wake point enough to strand a valid low-voltage cell.
  NRF_LPCOMP->HYST = LPCOMP_HYST_HYST_NoHyst;

  // Clear stale events/interrupts before enabling wake
  NRF_LPCOMP->EVENTS_READY = 0;
  NRF_LPCOMP->EVENTS_DOWN = 0;
  NRF_LPCOMP->EVENTS_UP = 0;
  NRF_LPCOMP->EVENTS_CROSS = 0;

  NRF_LPCOMP->INTENCLR = 0xFFFFFFFF;
  NRF_LPCOMP->INTENSET = LPCOMP_INTENSET_UP_Msk;

  // Enable LPCOMP
  NRF_LPCOMP->ENABLE = LPCOMP_ENABLE_ENABLE_Enabled;
  NRF_LPCOMP->TASKS_START = 1;

  // Wait for comparator to settle before entering SYSTEMOFF
  for (uint8_t i = 0; i < 20 && !NRF_LPCOMP->EVENTS_READY; i++) {
    delayMicroseconds(50);
  }

  if (refsel == 7) {
    MESH_DEBUG_PRINTLN("PWRMGT: LPCOMP wake configured (AIN%d, ref=ARef)", ain_channel);
  } else if (refsel <= 6) {
    MESH_DEBUG_PRINTLN("PWRMGT: LPCOMP wake configured (AIN%d, ref=%d/8 VDD)",
      ain_channel, refsel + 1);
  } else {
    uint8_t ref_num = (uint8_t)((refsel - 8) * 2 + 1);
    MESH_DEBUG_PRINTLN("PWRMGT: LPCOMP wake configured (AIN%d, ref=%d/16 VDD)",
      ain_channel, ref_num);
  }

}

void NRF52Board::armVbusWake() {
  // Configure VBUS (USB power) wake alongside (or instead of) LPCOMP.
  uint8_t sd_enabled = 0;
  sd_softdevice_is_enabled(&sd_enabled);
  if (sd_enabled) {
    sd_power_usbdetected_enable(1);
  } else {
    NRF_POWER->EVENTS_USBDETECTED = 0;
    NRF_POWER->INTENSET = POWER_INTENSET_USBDETECTED_Msk;
  }

  MESH_DEBUG_PRINTLN("PWRMGT: VBUS wake configured");
}
#endif

void NRF52BoardDCDC::begin() {
  NRF52Board::begin();

  // Enable DC/DC converter for improved power efficiency
  uint8_t sd_enabled = 0;
  sd_softdevice_is_enabled(&sd_enabled);
  if (sd_enabled) {
    sd_power_dcdc_mode_set(NRF_POWER_DCDC_ENABLE);
  } else {
    NRF_POWER->DCDCEN = 1;
  }
}

bool NRF52Board::isExternalPowered() {
  // Check if SoftDevice is enabled before using its API
  uint8_t sd_enabled = 0;
  sd_softdevice_is_enabled(&sd_enabled);

  if (sd_enabled) {
    uint32_t usb_status;
    sd_power_usbregstatus_get(&usb_status);
    return (usb_status & POWER_USBREGSTATUS_VBUSDETECT_Msk) != 0;
  } else {
    return (NRF_POWER->USBREGSTATUS & POWER_USBREGSTATUS_VBUSDETECT_Msk) != 0;
  }
}

bool NRF52Board::isUsbDataConnected() {
#if defined(USE_TINYUSB)
  #if defined(CFG_TUD_CDC) && CFG_TUD_CDC
  return tud_mounted() && tud_cdc_connected();
  #else
  return tud_mounted();
  #endif
#else
  return false;
#endif
}

bool NRF52Board::isUsbHostConnected() {
#if defined(USE_TINYUSB)
  return tud_mounted();
#else
  return false;
#endif
}

void NRF52Board::sleep(uint32_t secs) {
  // Clear FPU interrupt flags to avoid insomnia
  // see errata 87 for details https://docs.nordicsemi.com/bundle/errata_nRF52840_Rev3/page/ERR/nRF52840/Rev3/latest/anomaly_840_87.html
  #if (__FPU_USED == 1)
  __set_FPSCR(__get_FPSCR() & ~(0x0000009F)); 
  (void) __get_FPSCR();
  NVIC_ClearPendingIRQ(FPU_IRQn);
  #endif

  // On nRF52, we use event-driven sleep instead of timed sleep
  // The 'secs' parameter is ignored - we wake on any interrupt
  uint8_t sd_enabled = 0;
  sd_softdevice_is_enabled(&sd_enabled);

  if (sd_enabled) {
    // A single call is required here. If an interrupt arrived since the last
    // wait, SoftDevice returns immediately so the main loop can service the
    // flag or BLE queue before sleeping again.
    sd_app_evt_wait();
  } else {
    // softdevice is disabled, use raw WFE
    __SEV();
    __WFE();
    __WFE();
  }
}

// Temperature from NRF52 MCU
float NRF52Board::getMCUTemperature() {
  uint8_t sd_enabled = 0;
  sd_softdevice_is_enabled(&sd_enabled);
  if (sd_enabled) {
    uint32_t err_code;
    int32_t temp;
    err_code = sd_temp_get(&temp);
    if (err_code == NRF_SUCCESS) {
      return (float)temp * 0.25f;
    } else {
      return NAN;
    }
  } else {
    NRF_TEMP->TASKS_START = 1; // Start temperature measurement

    long startTime = millis();
    while (NRF_TEMP->EVENTS_DATARDY == 0) { // Wait for completion. Should complete in 50us
      if(millis() - startTime > 5) {  // To wait 5ms just in case
        NRF_TEMP->TASKS_STOP = 1;
        return NAN;
      }
    }
  }

  NRF_TEMP->EVENTS_DATARDY = 0; // Clear event flag

  int32_t temp = NRF_TEMP->TEMP; // In 0.25 *C units
  NRF_TEMP->TASKS_STOP = 1;

  return temp * 0.25f; // Convert to *C
}

void NRF52Board::shutdownPeripherals() {
  // Power off the display if any
#ifdef DISPLAY_CLASS
  if (display.isOn()) {
    display.turnOff();
  }
#endif
  // Prep LoRa radio for power down
  #ifdef P_LORA_RESET
    digitalWrite(P_LORA_RESET, HIGH);  // preload OUT latch so pinMode can't glitch NRESET low
    pinMode(P_LORA_RESET, OUTPUT);
    digitalWrite(P_LORA_RESET, LOW);   // deliberate hardware reset (datasheet: >=100us)
    delayMicroseconds(200);
    digitalWrite(P_LORA_RESET, HIGH);
  #endif
  #if defined(P_LORA_SCLK) && defined(P_LORA_MISO) && defined(P_LORA_MOSI)
    SPI.setPins(P_LORA_MISO, P_LORA_SCLK, P_LORA_MOSI);
    SPI.begin(); // SPI may not be started on some shutdown paths, need it to shut down radio
  #endif
  #ifdef P_LORA_BUSY
    pinMode(P_LORA_BUSY, INPUT);
    uint32_t started_at = millis();
    while (digitalRead(P_LORA_BUSY) && millis() - started_at < 10) {} //wait for radio to be ready
  #endif
  #ifdef P_LORA_NSS
    pinMode(P_LORA_NSS, OUTPUT);
    digitalWrite(P_LORA_NSS, HIGH);
  #endif
  // Power off LoRa
  radio_driver.powerOff();

  // Keep LoRa inactive during deepsleep
  #ifdef P_LORA_NSS
    digitalWrite(P_LORA_NSS, HIGH);
  #endif

  // Power off GPS if any
  if(sensors.getLocationProvider() != NULL) {
    sensors.getLocationProvider()->stop();
  }

  // Flush serial buffers
  Serial.flush();
  delay(100);
}

void NRF52Board::powerOff() {
  shutdownPeripherals();

  // Enter SYSTEMOFF
  uint8_t sd_enabled = 0;
  sd_softdevice_is_enabled(&sd_enabled);
  if (sd_enabled) { // SoftDevice is enabled
    sd_power_system_off();
  } else { // SoftDevice is not enable
    NRF_POWER->SYSTEMOFF = POWER_SYSTEMOFF_SYSTEMOFF_Enter;
  }
}

bool NRF52Board::getBootloaderVersion(char* out, size_t max_len) {
    static const char BOOTLOADER_MARKER[] = "UF2 Bootloader ";
    const uint8_t* flash = (const uint8_t*)0x000FB000; // earliest known info.txt location is 0xFB90B, latest is 0xFCC4B

    for (uint32_t i = 0; i < 0x3000 - (sizeof(BOOTLOADER_MARKER) - 1); i++) {
        if (memcmp(&flash[i], BOOTLOADER_MARKER, sizeof(BOOTLOADER_MARKER) - 1) == 0) {
            const char* ver = (const char*)&flash[i + sizeof(BOOTLOADER_MARKER) - 1];
            size_t len = 0;
            while (len < max_len - 1 && ver[len] != '\0' && ver[len] != ' ' && ver[len] != '\n' && ver[len] != '\r') {
                out[len] = ver[len];
                len++;
            }
            out[len] = '\0';
            return len > 0; // bootloader string is non-empty
        }
    }
    return false;
}

bool NRF52Board::startOTAUpdate(const char *id, char reply[], bool force_ap) {
  (void)id;
  (void)force_ap;

  if (ota_active) {
    format_ota_reply(reply);
    return true;
  }

  if (!ota_ble_started) {
    // Config the peripheral connection with maximum bandwidth
    // more SRAM required by SoftDevice
    // Note: All config***() function must be called before begin()
    Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);
    Bluefruit.configPrphConn(92, BLE_GAP_EVENT_LENGTH_MIN, 16, 16);

    Bluefruit.begin(1, 0);
    ota_ble_started = true;

    // To be consistent OTA DFU should be added first if it exists
    bledfu.begin();
  }

  // Set max power. Accepted values are: -40, -30, -20, -16, -12, -8, -4, 0, 4
  Bluefruit.setTxPower(4);
  // Set the BLE device name
  Bluefruit.setName(ota_name);

  Bluefruit.Periph.setConnectCallback(connect_callback);
  Bluefruit.Periph.setDisconnectCallback(disconnect_callback);

  Bluefruit.Advertising.clearData();
  Bluefruit.ScanResponse.clearData();

  // Set up and start advertising
  // Advertising packet
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addTxPower();
  Bluefruit.Advertising.addName();

  /* Start Advertising
    - Enable auto advertising if disconnected
    - Interval:  fast mode = 20 ms, slow mode = 152.5 ms
    - Timeout for fast mode is 30 seconds
    - Start(timeout) with timeout = 0 will advertise forever (until connected)

    For recommended advertising interval
    https://developer.apple.com/library/content/qa/qa1931/_index.html
  */
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(32, 244); // in unit of 0.625 ms
  Bluefruit.Advertising.setFastTimeout(30);   // number of seconds in fast mode
  Bluefruit.Advertising.start(0);             // 0 = Don't stop advertising after n seconds

  ota_active = true;
  format_ota_reply(reply);

  return true;
}

bool NRF52Board::stopOTAUpdate(char reply[]) {
  if (!ota_active) {
    strcpy(reply, "OK - OTA not running");
    return true;
  }

  Bluefruit.Advertising.restartOnDisconnect(false);
  Bluefruit.Advertising.stop();
  if (ota_conn_handle != BLE_CONN_HANDLE_INVALID) {
    sd_ble_gap_disconnect(ota_conn_handle, BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
    ota_conn_handle = BLE_CONN_HANDLE_INVALID;
  }
  ota_active = false;

  strcpy(reply, "OK - OTA stopped");
  return true;
}
#endif
