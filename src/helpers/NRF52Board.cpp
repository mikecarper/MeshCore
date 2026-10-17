#if defined(NRF52_PLATFORM)
#include "NRF52Board.h"
#include "PowerManagementUtils.h"
#include "NRF52VoltagePolicy.h"
#include "Nrf52BootloaderVersion.h"
#include "nrf52/SoftDeviceState.h"
#include "nrf52/SleepCycleState.h"
#include "nrf52/ShutdownLeds.h"
#include <target.h>
#include "UserGpioPinPolicy.h"
#include "PowerSavingDiagnostics.h"

#include <bluefruit.h>
#include "ble_gap.h"
#include "ble_hci.h"
#include <nrf.h>
#include <nrf_nvic.h>
#include <nrf_soc.h>
#ifdef USE_TINYUSB
#include <Adafruit_TinyUSB.h>
#endif

#ifdef USE_CC310_HW_CRYPTO
#include <Adafruit_nRFCrypto.h>
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

#ifndef DFU_MAGIC_UF2_RESET
#define DFU_MAGIC_UF2_RESET 0x57
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

namespace {

bool isExposedNrf52UserGpio(uint8_t pin) {
#if defined(HELTEC_T096)
  // Physical P2/P3 header GPIOs from the T096 schematic. Firmware-owned
  // radio, display, GPS, power, button, and I2C pins are removed separately.
  static const uint8_t exposed[] = {
    2, 4, 7, 8, 9, 10, 13, 15, 17, 20, 22, 23, 24, 25, 27, 29, 31,
    32, 33, 34, 35, 36, 37, 38, 39, 42, 43, 45, 47
  };
#elif defined(PROMICRO)
  // D0-D17 are broken out on the ProMicro form factor.
  if (pin <= 17) return true;
  return false;
#elif defined(RAK_3401) || defined(RAK_4631)
  // GPIO and bus signals exposed by the WisBlock base/IO connector.
  static const uint8_t exposed[] = {
    2, 3, 4, 5, 9, 10, 13, 14, 15, 16, 17, 19, 20, 21, 24, 25,
    26, 28, 29, 30, 31, 33, 34
  };
#else
  return false;
#endif

#if defined(HELTEC_T096) || defined(RAK_3401) || defined(RAK_4631)
  for (size_t i = 0; i < sizeof(exposed) / sizeof(exposed[0]); i++) {
    if (pin == exposed[i]) return true;
  }
#endif
  return false;
}

} // namespace

bool NRF52Board::isUserGpioAvailable(uint8_t pin) const {
  // This board approval query also serves Companion alert outputs. CommonCLI's
  // separate USER_GPIO_CONTROL flag still controls its GPIO command exposure.
  if (pin >= PINS_COUNT || digitalPinToPinName(pin) == 0xFF) return false;
#if defined(RAK_3401) || defined(RAK_4631)
  // Sensor startup can toggle these WisBlock slot pins while detecting GPS,
  // and WB_IO2 also controls the switched peripheral rail on supported bases.
  if (pin == WB_IO2 || pin == WB_IO4 || pin == WB_IO5) return false;
#endif
  return isExposedNrf52UserGpio(pin) && !UserGpioPinPolicy::isFirmwareReserved(pin);
}

bool NRF52Board::rebootToUf2Bootloader() {
  uint8_t sd_enabled = 0;
  if (mesh_nrf52::softdeviceIsEnabled(sd_enabled) != NRF_SUCCESS) return false;

  if (sd_enabled) {
    if (sd_power_gpregret_clr(0, 0xFF) != NRF_SUCCESS
        || sd_power_gpregret_set(0, DFU_MAGIC_UF2_RESET) != NRF_SUCCESS) {
      return false;
    }
  } else {
    NRF_POWER->GPREGRET = DFU_MAGIC_UF2_RESET;
  }

  NVIC_SystemReset();
  return true;
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

  #ifdef USE_CC310_HW_CRYPTO
    // CC310 TRNG is higher quality and environment-independent vs radio RSSI noise.
    nRFCrypto.begin();
  #endif
}

void NRF52Board::loop() {
#ifdef NRF52_POWER_MANAGEMENT
  if (!mesh::power::voltagePolicySupported()) return;
  static uint32_t last_check = 0;
  static uint8_t low_count = 0;
  static uint16_t last_threshold = 0;
  const uint16_t threshold = mesh::power::configuredCutoff();
  const uint32_t now = millis();
  if (threshold != last_threshold) {
    last_threshold = threshold;
    low_count = 0;
    last_check = now;
  }
  if (threshold == 0 || now - last_check < 30000UL) return;
  last_check = now;
  if (isExternalPowered()) {
    low_count = 0;
    return;
  }
  low_count = mesh::power::nextLowVoltageCount(
      low_count, getBattMilliVolts(), threshold, false);
  if (low_count >= 3) initiateShutdown(SHUTDOWN_REASON_LOW_VOLTAGE);
#endif
}

#ifdef NRF52_POWER_MANAGEMENT
uint16_t NRF52Board::calibrateBatteryMillivolts(uint16_t raw_mv) const {
  return mesh::power::calibratedMillivolts(
      raw_mv, mesh::power::configuredAdcPermille());
}
#endif

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
  mesh_nrf52::softdeviceIsEnabled(sd_enabled);
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
  pwrmgtInit();

  if (config == nullptr) return true;

  // Use the median of three readings.  A single unsettled ADC sample during a
  // brownout must not put the device into a persistent SYSTEMOFF boot lock.
  uint16_t samples[3];
  for (uint8_t i = 0; i < 3; i++) {
    samples[i] = getBattMilliVolts();
    if (i != 2) delay(5);
  }
  boot_voltage_mv = mesh::power::medianVoltage(samples[0], samples[1], samples[2]);
  
  const bool external = isExternalPowered();
  // Loading the policy still accesses flash. Keep a hard floor ahead of it
  // for boards whose compiled policy enables boot protection.
  const uint16_t preflight = config->voltage_bootlock == 0 ? 0
      : mesh::power::MIN_CUSTOM_MV;
  if (mesh::power::shouldBootLock(boot_voltage_mv, preflight, external)) {
    initiateShutdown(SHUTDOWN_REASON_BOOT_PROTECT);
    return false;
  }

  mesh::power::loadVoltagePolicyAtBoot(config->voltage_bootlock);
  boot_voltage_mv = calibrateBatteryMillivolts(boot_voltage_mv);
  const uint16_t threshold = mesh::power::configuredBootlock();
  if (threshold == 0 || external) return true;

  MESH_DEBUG_PRINTLN("PWRMGT: Boot voltage = %u mV (threshold = %u mV)",
      boot_voltage_mv, threshold);

  // Only valid ADC readings (>1000 mV) can initiate protective shutdown.
  if (mesh::power::shouldBootLock(boot_voltage_mv, threshold, false)) {
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

  // This path can run before BLE begin() during boot voltage protection.
  // autoConnLed(false) is safe then too and stops future automatic LED writes.
  Bluefruit.autoConnLed(false);

  // Record shutdown reason in GPREGRET2
  uint8_t sd_enabled = 0;
  mesh_nrf52::softdeviceIsEnabled(sd_enabled);
  if (sd_enabled) {
    sd_power_gpregret_clr(1, 0xFF);
    sd_power_gpregret_set(1, reason);
  } else {
    NRF_POWER->GPREGRET2 = reason;
  }

  // Flush serial buffers
  Serial.flush();
  delay(100);
  mesh_nrf52::turnOffShutdownLeds();

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

  // Wait for comparator to settle
  for (uint8_t i = 0; i < 20 && !NRF_LPCOMP->EVENTS_READY; i++) {
    delayMicroseconds(50);
  }

  if (refsel == 7) {
    MESH_DEBUG_PRINTLN("PWRMGT: LPCOMP wake armed (AIN%d, ref=ARef)", ain_channel);
  } else if (refsel <= 6) {
    MESH_DEBUG_PRINTLN("PWRMGT: LPCOMP wake armed (AIN%d, ref=%d/8 VDD)", ain_channel, refsel + 1);
  } else {
    uint8_t ref_num = (uint8_t)((refsel - 8) * 2 + 1);
    MESH_DEBUG_PRINTLN("PWRMGT: LPCOMP wake armed (AIN%d, ref=%d/16 VDD)", ain_channel, ref_num);
  }
}

void NRF52Board::armVbusWake() {
  // Configure VBUS (USB power) wake alongside (or instead of) LPCOMP.
  uint8_t sd_enabled = 0;
  mesh_nrf52::softdeviceIsEnabled(sd_enabled);
  if (sd_enabled) {
    sd_power_usbdetected_enable(1);
  } else {
    NRF_POWER->EVENTS_USBDETECTED = 0;
    NRF_POWER->INTENSET = POWER_INTENSET_USBDETECTED_Msk;
  }
  MESH_DEBUG_PRINTLN("PWRMGT: VBUS wake armed");
}
#endif

void NRF52BoardDCDC::begin() {
  NRF52Board::begin();

  // Enable DC/DC converter for improved power efficiency
  uint8_t sd_enabled = 0;
  mesh_nrf52::softdeviceIsEnabled(sd_enabled);
  if (sd_enabled) {
    sd_power_dcdc_mode_set(NRF_POWER_DCDC_ENABLE);
  } else {
    NRF_POWER->DCDCEN = 1;
  }
}

bool NRF52Board::isExternalPowered() {
  mesh_nrf52::SleepCycleSnapshot cycle;
  if (mesh_nrf52::sleepCycleState().snapshot(cycle))
    return (cycle.usb_status & POWER_USBREGSTATUS_VBUSDETECT_Msk) != 0;

  // Setup and callers outside a prepared loop retain the SDK access policy.
  // Never reuse a cached value after a SoftDevice transition invalidated it.
  uint8_t sd_enabled = 0;
  if (mesh_nrf52::softdeviceIsEnabled(sd_enabled) != NRF_SUCCESS) return false;

  if (sd_enabled) {
    volatile uint32_t status = 0;
    const uint32_t error = sd_power_usbregstatus_get((uint32_t*)&status);
    __asm volatile ("" ::: "memory");
    if (error != NRF_SUCCESS) return false;
    const uint32_t usb_status = status;
    return (usb_status & POWER_USBREGSTATUS_VBUSDETECT_Msk) != 0;
  } else {
    return (NRF_POWER->USBREGSTATUS & POWER_USBREGSTATUS_VBUSDETECT_Msk) != 0;
  }
}

void NRF52Board::prepareSleepCycle() {
  auto& state = mesh_nrf52::sleepCycleState();
  const uint32_t token = state.begin();
  const auto application_context = []() {
    return __get_IPSR() == 0 && __get_PRIMASK() == 0
        && __get_BASEPRI() == 0 && __get_FAULTMASK() == 0
        // SDK critical regions can mask application IRQs in the NVIC even
        // while the CPU mask registers are clear. This public field is
        // volatile: read it afresh before queries and after the forced yield.
        && nrf_nvic_state.__cr_flag == 0
        && xTaskGetSchedulerState() == taskSCHEDULER_RUNNING;
  };
  if (!application_context()) {
    state.publish(token, false, 0, NRF_ERROR_INVALID_STATE);
    return;
  }
#ifdef NRF_CRYPTOCELL
  // The pinned Adafruit CC310 library polls (no-interrupts archive), but can
  // leave its disabled NVIC IRQ pending. The SDK treats that as application
  // work forever. Match the core's waitForEvent cleanup only at loop entry;
  // never clear an enabled IRQ belonging to an interrupt-driven owner.
  if (NVIC_GetEnableIRQ(CRYPTOCELL_IRQn) == 0)
    NVIC_ClearPendingIRQ(CRYPTOCELL_IRQn);
#endif
  uint8_t sd_enabled = 0;
  uint32_t error = mesh_nrf52::softdeviceIsEnabled(sd_enabled);
  uint32_t usb_status = 0;
  if (error == NRF_SUCCESS) {
    if (sd_enabled) {
      volatile uint32_t status = 0;
      error = sd_power_usbregstatus_get((uint32_t*)&status);
      __asm volatile ("" ::: "memory");
      usb_status = status;
    } else {
      usb_status = NRF_POWER->USBREGSTATUS;
    }
  }
  if (error == NRF_SUCCESS && sd_enabled && state.current(token)) {
    // Raw SEV/WFE does not acknowledge the SDK's separate application-IRQ
    // bookkeeping. First force a real application PendSV while the loop task
    // remains ready (so the RTOS idle task cannot consume that acknowledgement).
    // The public SDK contract then makes this one early wait non-blocking.
    // The pinned S140 6.1.1/7.3.0 forwarding/wait code confirms its private IRQ
    // sequence cannot wrap back to the last acknowledged value. Do not access
    // those SDK-private counters or rely on SEV alone to release an SDK wait.
    taskYIELD();
    if (!application_context() || (SCB->ICSR & SCB_ICSR_PENDSVSET_Msk)) {
      error = NRF_ERROR_INVALID_STATE;
    } else if (state.current(token)) {
      error = sd_app_evt_wait();
    }
  }
  state.publish(token, sd_enabled != 0, usb_status, error);

  // FreeRTOS yields and the state-query SVC can both leave a stale event.
  // Clear it ONLY at loop entry, before servicing/checking application work.
  // A radio interrupt after this point must remain visible to the late wait.
  __SEV();
  __WFE();
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
  (void)secs;
  const bool diagnostics = mesh::powerdiag::enabled();
  auto& state = mesh_nrf52::sleepCycleState();
  mesh_nrf52::SleepCycleSnapshot cycle;
  const bool prepared = state.take(cycle);
  if (isRadioTestActive()) {
    if (diagnostics) mesh::powerdiag::boardVeto(mesh::powerdiag::Reason::Board, millis(), 2);
    delay(1);
    return;
  }
  if (!prepared || !state.current(cycle.generation)) {
    if (diagnostics) {
      const uint32_t error = state.lastError();
      if (error != NRF_SUCCESS) mesh::powerdiag::boardError(static_cast<int32_t>(error), millis());
      else mesh::powerdiag::boardVeto(mesh::powerdiag::Reason::Board, millis(), 3);
    }
    delay(1);
    return;
  }
  // Clear FPU interrupt flags to avoid insomnia
  // see errata 87 for details https://docs.nordicsemi.com/bundle/errata_nRF52840_Rev3/page/ERR/nRF52840/Rev3/latest/anomaly_840_87.html
  #if (__FPU_USED == 1)
  __set_FPSCR(__get_FPSCR() & ~(0x0000009F)); 
  (void) __get_FPSCR();
  NVIC_ClearPendingIRQ(FPU_IRQn);
  #endif

  // On nRF52, we use event-driven sleep instead of timed sleep
  // The 'secs' parameter is ignored - we wake on any interrupt
  if (cycle.softdevice_enabled) {
    // A single call is required here. If an interrupt arrived since the last
    // wait, SoftDevice returns immediately so the main loop can service the
    // flag or BLE queue before sleeping again.
    if (diagnostics) mesh::powerdiag::sleepBegin(micros());
    const uint32_t error = sd_app_evt_wait();
    if (diagnostics) {
      mesh::powerdiag::sleepEnd(micros());
      if (error != NRF_SUCCESS) mesh::powerdiag::boardError(static_cast<int32_t>(error), millis());
    }
  } else {
    // SoftDevice is disabled. Match the Adafruit core's race-preserving
    // waitForEvent() sequence: the first WFE sleeps or consumes an already-
    // pending event, then SEV/WFE reliably clears the event register before
    // returning. SEV/WFE/WFE can erase the only GPIO edge that arrived between
    // the caller's predicate and here.
    if (diagnostics) mesh::powerdiag::sleepBegin(micros());
    __WFE();
    __SEV();
    __WFE();
    if (diagnostics) mesh::powerdiag::sleepEnd(micros());
  }
}

// Temperature from NRF52 MCU
float NRF52Board::getMCUTemperature() {
  uint8_t sd_enabled = 0;
  mesh_nrf52::softdeviceIsEnabled(sd_enabled);
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
  // Stop Bluefruit's LED owner before setting a final OFF latch. Its timer
  // stop is queued, so allow the existing shutdown delay to drain a pending
  // blinky callback before the final write below (also safe before BLE begin).
  Bluefruit.autoConnLed(false);

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

  // RadioLib's IRQ setup can leave DIO1 as a GPIO wake source. Once the radio
  // is asleep that line may float or remain asserted, immediately waking an
  // nRF52 that is trying to enter SYSTEMOFF. Release the interrupt channel and
  // explicitly disable pin sensing; board-specific code can then arm only its
  // intended wake source (for example, the SenseCAP user button or LPCOMP).
  #ifdef P_LORA_DIO_1
    detachInterrupt(P_LORA_DIO_1);
    pinMode(P_LORA_DIO_1, INPUT);
  #endif

  // Keep LoRa inactive during deepsleep
  #ifdef P_LORA_NSS
    digitalWrite(P_LORA_NSS, HIGH);
  #endif

  // Power off GPS if any
  if(sensors.getLocationProvider() != NULL) {
    sensors.getLocationProvider()->stop();
  }

#ifdef USE_CC310_HW_CRYPTO
    nRFCrypto.end();
#endif

  // Flush serial buffers
  Serial.flush();
  delay(100);
  mesh_nrf52::turnOffShutdownLeds();
}

void NRF52Board::powerOff() {
  shutdownPeripherals();
  // Board-specific hooks may signal button release after calling the base.
  mesh_nrf52::turnOffShutdownLeds();

  // Enter SYSTEMOFF
  uint8_t sd_enabled = 0;
  mesh_nrf52::softdeviceIsEnabled(sd_enabled);
  if (sd_enabled) { // SoftDevice is enabled
    sd_power_system_off();
  } else { // SoftDevice is not enable
    NRF_POWER->SYSTEMOFF = POWER_SYSTEMOFF_SYSTEMOFF_Enter;
  }
}

void NRF52Board::enterDeepSleep(uint32_t secs) {
  if (secs == 0) {
    powerOff(); // Not wake up
  } else {
    shutdownPeripherals();
    mesh_nrf52::turnOffShutdownLeds();

// Clear FPU interrupt flags to avoid insomnia
// see errata 87 for details
// https://docs.nordicsemi.com/bundle/errata_nRF52840_Rev3/page/ERR/nRF52840/Rev3/latest/anomaly_840_87.html
#if (__FPU_USED == 1)
    __set_FPSCR(__get_FPSCR() & ~(0x0000009F));
    (void)__get_FPSCR();
    NVIC_ClearPendingIRQ(FPU_IRQn);
#endif

    // Attemp to sleep
    vTaskDelay(pdMS_TO_TICKS(secs * 1000));

    // Reboot for a fresh recovery
    reboot();
  }
}

bool NRF52Board::getBootloaderVersion(char* out, size_t max_len) {
  if (!out || max_len == 0u) return false;
  out[0] = 0;
  mesh::Nrf52BootloaderRegion region;
  const uint32_t mbr_start = *(const volatile uint32_t*)(uintptr_t)0xFF8u;
  if (!mesh::nrf52BootloaderRegion(NRF_FICR->CODEPAGESIZE, NRF_FICR->CODESIZE,
                                  mbr_start, NRF_UICR->NRFFW[0], region)) return false;
  return mesh::nrf52BootloaderVersion((const uint8_t*)(uintptr_t)region.start,
                                    region.end - region.start, region.start,
                                    bootloaderVersion, out, max_len);
}

bool NRF52Board::startOTAUpdate(const char *id, char reply[], bool force_ap) {
  (void)id;
  (void)force_ap;

  if (ota_active) {
    format_ota_reply(reply);
    return true;
  }

  if (!ota_ble_started) {
    // The application only needs the buttonless DFU control connection; the
    // bootloader handles firmware data. Large ATT and queue reservations can
    // make SoftDevice startup fail on an otherwise healthy repeater.
    // All config***() calls must precede begin().
    Bluefruit.configPrphConn(BLE_GATT_ATT_MTU_DEFAULT,
                             BLE_GAP_EVENT_LENGTH_MIN, 2, 1);

    // Even a failed begin can leave SoftDevice enabled partway through SDK
    // startup. Require a fresh authoritative query before the next sleep.
    mesh_nrf52::sleepCycleState().invalidate();
    if (!Bluefruit.begin(1, 0)) {
      return false;
    }
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

  // Bluefruit.addName() can copy bytes past the GAP name when the optimized
  // getName() reports the buffer capacity. Advertise the board's known name
  // directly so DFU scanners see an exact, complete local name.
  const size_t ota_name_len = strlen(ota_name);
  if (ota_name_len > UINT8_MAX
      || !Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE)
      || !Bluefruit.Advertising.addTxPower()
      || !Bluefruit.Advertising.addData(BLE_GAP_AD_TYPE_COMPLETE_LOCAL_NAME,
                                       ota_name, (uint8_t)ota_name_len)
      || !Bluefruit.ScanResponse.addService(bledfu)) {
    return false;
  }

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
  if (!Bluefruit.Advertising.start(0)) return false;  // 0 = advertise until stopped

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
