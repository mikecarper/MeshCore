#pragma once

#include <MeshCore.h>
#include <Arduino.h>

#ifndef USER_BTN_PRESSED
#define USER_BTN_PRESSED LOW
#endif

#if defined(ESP_PLATFORM)

#include <rom/rtc.h>
#include <sys/time.h>
#include <Wire.h>
#include "soc/rtc.h"
#include "esp_system.h"
#include <driver/rtc_io.h>
#include "ESP32TrueRandom.h"
#include "ESP32GpioHold.h"
#include "UsbLogging.h"
#include "UsbHostSleepPolicy.h"
#include "PowerSavingDiagnostics.h"
#include "esp32/LightSleepPolicy.h"
#include "esp32/PowerTestControls.h"
#include "esp32/CpuClockPolicy.h"
#include <esp_pm.h>
#if CONFIG_IDF_TARGET_ESP32
#include <soc/efuse_reg.h>
#endif
#if defined(STATION_G3_ESP32)
#include <StationG3RadioSlot.h>
#endif

#ifndef MESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS
  #define MESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS 120000UL
#endif
static_assert(MESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS <= 0x7fffffffUL,
              "USB host-loss sleep grace must fit a bounded millis interval");

#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT && \
    (!defined(ARDUINO_USB_MODE) || !ARDUINO_USB_MODE)
#include <USB.h>
#endif

#if !defined(LIGHTWEIGHT_WIFI_OTA)
class AsyncWebServer;
#endif
#include <helpers/KeyValueStore.h>

class ESP32Board : public mesh::MainBoard {
protected:
  uint8_t startup_reason;
  bool inhibit_sleep = false;
#if defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) && MOMENTARY_BUTTON_WAKE_FROM_SLEEP
  bool button_wake_hold_active = false;
  uint32_t button_wake_at = 0;
#endif
  bool ota_started_ap = false;
  bool ota_started_radio = false;
  bool companion_cpu_clock_policy = false;
  bool radio_profile_cpu_clock_policy = false;
  bool applied_profile_cpu_clock_policy = false;
  bool applied_cpu_clock_valid = false;
  uint16_t requested_cpu_clock_selection = 0;
  bool requested_cpu_clock_powersaving = false;
  uint16_t sampled_profile_cpu_mhz = 0;
  uint32_t profile_cpu_clock_retry_at = 0;
  uint32_t uart_quiet_start_at = 0;
  uint32_t uart_quiet_restore_at = 0;
  uint16_t uart_quiet_seconds = 0;
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
  mesh::UsbHostSleepPolicy usb_host_sleep_policy;
#endif
#if defined(LIGHTWEIGHT_WIFI_OTA)
  void* ota_server = nullptr;
#else
  AsyncWebServer* ota_server = nullptr;
#endif
  static inline portMUX_TYPE sleepMux = portMUX_INITIALIZER_UNLOCKED;

public:
  void begin() {
    // Digital deep-sleep auto-hold survives wake. Clear it before runtime,
    // restoring the dedicated radio select before releasing its per-pin hold.
    mesh::esp32::resetTerminalDeepSleepHolds();
  #if defined(STATION_G3_ESP32)
    mesh::esp32::releaseOutputAfterDeepSleep(
        (gpio_num_t)station_g3::activePins().nss, HIGH);
  #elif defined(P_LORA_NSS)
    mesh::esp32::releaseOutputAfterDeepSleep((gpio_num_t)P_LORA_NSS, HIGH);
  #endif

    // Arduino's early init hook normally captured this before initVariant().
    // Keep this idempotent fallback before this class touches ADC peripherals.
    mesh::initializeESP32TrueRandom();

    // for future use, sub-classes SHOULD call this from their begin()
    startup_reason = BD_STARTUP_NORMAL;    

    mesh::esp32::registerUartQuietControl(this,
        [](void* owner, mesh::esp32::UartQuietAction action, uint16_t seconds,
           char* reply, size_t capacity) {
          return static_cast<ESP32Board*>(owner)->handleUartQuiet(action, seconds, reply, capacity);
        });

  #ifdef ESP32_CPU_FREQ
    setCpuFrequencyMhz(ESP32_CPU_FREQ);
  #endif

  #ifdef PIN_VBAT_READ
    // battery read support
    pinMode(PIN_VBAT_READ, INPUT);
  #if ESP_ARDUINO_VERSION_MAJOR < 3
    adcAttachPin(PIN_VBAT_READ);
  #endif
  #endif

  #ifdef P_LORA_TX_LED
    pinMode(P_LORA_TX_LED, OUTPUT);
    digitalWrite(P_LORA_TX_LED, LOW);
  #endif

  #if defined(PIN_BOARD_SDA) && defined(PIN_BOARD_SCL)
   #if PIN_BOARD_SDA >= 0 && PIN_BOARD_SCL >= 0
    Wire.begin(PIN_BOARD_SDA, PIN_BOARD_SCL);
   #endif
  #else
    Wire.begin();
  #endif    
  }

  void attachDynamicPrefs(KeyValueStore* prefs) { (void)prefs; }  // no-op

  void setCompanionCpuClockPolicy(bool enabled) { companion_cpu_clock_policy = enabled; }
  void setRadioProfileCpuClockPolicy(bool enabled) {
    radio_profile_cpu_clock_policy = mesh::esp32::cpuclock::profilePerformanceEnabled(enabled);
  }
  uint16_t getCpuFrequencyMHz() const override { return ::getCpuFrequencyMhz(); }
  bool supportsCpuFrequencyMHz(uint16_t mhz) const override {
#if CONFIG_IDF_TARGET_ESP32H2
    (void)mhz;
    return false;
#else
    if (mhz == 80) return true;
#if CONFIG_IDF_TARGET_ESP32 || CONFIG_IDF_TARGET_ESP32S2 || CONFIG_IDF_TARGET_ESP32S3 \
    || CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32C6
    if (mhz == 160) return true;
#endif
#if CONFIG_IDF_TARGET_ESP32 || CONFIG_IDF_TARGET_ESP32S2 || CONFIG_IDF_TARGET_ESP32S3
    if (mhz == 240) {
#if CONFIG_IDF_TARGET_ESP32
      if (REG_GET_BIT(EFUSE_BLK0_RDATA3_REG, EFUSE_RD_CHIP_CPU_FREQ_RATED)
          && REG_GET_BIT(EFUSE_BLK0_RDATA3_REG, EFUSE_RD_CHIP_CPU_FREQ_LOW)) return false;
#endif
      return true;
    }
#endif
    return false;
#endif
  }

  uint16_t resolveCpuFrequencyMHz(uint16_t selected, bool powersaving) const {
    uint16_t maximum = mesh::esp32::cpuclock::resolveMHz(
        selected, powersaving, companion_cpu_clock_policy);
    if (!selected && radio_profile_cpu_clock_policy) {
      const uint16_t candidates[] = {240, 160, 80};
      for (uint16_t candidate : candidates) {
        if (supportsCpuFrequencyMHz(candidate)) return candidate;
      }
    }
    if (!selected && maximum == 240 && !supportsCpuFrequencyMHz(240)
        && supportsCpuFrequencyMHz(160)) maximum = 160;
    return maximum;
  }

  // Seed the loaded choice before saved profiles can retune the radio. Keep
  // this request even when the SDK cannot apply it on the first attempt.
  void setCpuFrequencyRequest(uint16_t selected, bool powersaving) override {
    if (requested_cpu_clock_selection != selected || requested_cpu_clock_powersaving != powersaving) {
      requested_cpu_clock_selection = selected;
      requested_cpu_clock_powersaving = powersaving;
      applied_cpu_clock_valid = false;
      profile_cpu_clock_retry_at = 0;
    }
  }

  bool applyCpuFrequencyMHz(uint16_t selected, bool powersaving) override {
    if (!mesh::esp32::cpuclock::validSelection(selected)
        || (selected && !supportsCpuFrequencyMHz(selected))) return false;
    setCpuFrequencyRequest(selected, powersaving);
    const uint16_t maximum = resolveCpuFrequencyMHz(selected, powersaving);
    applied_cpu_clock_valid = false;
#if defined(CONFIG_PM_ENABLE) && CONFIG_PM_ENABLE
    const uint16_t minimum = !selected && !radio_profile_cpu_clock_policy
        && companion_cpu_clock_policy && powersaving && maximum > 40
        ? 40 : maximum;
#if CONFIG_IDF_TARGET_ESP32C3
    esp_pm_config_esp32c3_t config;
#elif CONFIG_IDF_TARGET_ESP32S3
    esp_pm_config_esp32s3_t config;
#elif CONFIG_IDF_TARGET_ESP32
    esp_pm_config_esp32_t config;
#else
    esp_pm_config_t config;
#endif
    config.max_freq_mhz = maximum;
    config.min_freq_mhz = minimum;
#if (defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT) || defined(ENABLE_USB_INTERFACE)
    config.light_sleep_enable = false;
#else
    config.light_sleep_enable = companion_cpu_clock_policy && powersaving;
#endif
    if (esp_pm_configure(&config) != ESP_OK) return false;
#else
    if (!setCpuFrequencyMhz(maximum)) return false;
#endif
    if ((selected || radio_profile_cpu_clock_policy) && getCpuFrequencyMHz() != maximum) return false;
    applied_profile_cpu_clock_policy = radio_profile_cpu_clock_policy;
    applied_cpu_clock_valid = true;
    profile_cpu_clock_retry_at = 0;
    return true;
  }

  bool prepareRadioProfileCpuClock(bool enabled, bool& changed) override {
    changed = false;
    setRadioProfileCpuClockPolicy(enabled);
    uint16_t previous = 0;
    const bool fixed_clock = requested_cpu_clock_selection || radio_profile_cpu_clock_policy;
    const bool clock_drifted = fixed_clock && getCpuFrequencyMHz()
        != resolveCpuFrequencyMHz(requested_cpu_clock_selection, requested_cpu_clock_powersaving);
    if (!applied_cpu_clock_valid || applied_profile_cpu_clock_policy != radio_profile_cpu_clock_policy
        || clock_drifted) {
      const uint32_t now = millis();
      if (profile_cpu_clock_retry_at && static_cast<int32_t>(now - profile_cpu_clock_retry_at) < 0)
        return false;
      previous = getCpuFrequencyMHz();
      if (!applyCpuFrequencyMHz(requested_cpu_clock_selection, requested_cpu_clock_powersaving)) {
        profile_cpu_clock_retry_at = now + 5000;
        if (!profile_cpu_clock_retry_at) profile_cpu_clock_retry_at = 1;
        return false;
      }
      profile_cpu_clock_retry_at = 0;
    }
    // Observe explicit clock changes too. Reset only the pair's timing proof,
    // once per effective transition; hardware failure counters stay intact.
    if (radio_profile_cpu_clock_policy) {
      const uint16_t current = getCpuFrequencyMHz();
      changed = (previous && previous != current)
          || (sampled_profile_cpu_mhz && sampled_profile_cpu_mhz != current);
      sampled_profile_cpu_mhz = current;
    } else sampled_profile_cpu_mhz = 0;
    return true;
  }

  bool handleUartQuiet(mesh::esp32::UartQuietAction action, uint16_t seconds,
                       char* reply, size_t capacity) {
#if (defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT) \
    || defined(SERIAL_RX) || defined(SERIAL_TX)
    (void)seconds;
    const bool harmless = action != mesh::esp32::UartQuietAction::Schedule;
    snprintf(reply, capacity, harmless ? "uart=unsupported"
        : "Error: UART quiet requires a default hardware UART console");
    return harmless;
#else
    if (action == mesh::esp32::UartQuietAction::Cancel) {
      uart_quiet_start_at = 0;
      if (uart_quiet_restore_at) {
        Serial.begin(115200);  // all four roles start this same default console
        if (!Serial) {
          uart_quiet_restore_at = millis() + 1000;
          if (!uart_quiet_restore_at) uart_quiet_restore_at = 1;
          snprintf(reply, capacity, "Error: UART restore failed; retrying automatically");
          return false;
        }
        if (!mesh::esp32::transitionUartQuietSession(
                mesh::esp32::UartQuietSessionAction::Resume)) {
          uart_quiet_restore_at = millis() + 1000;
          if (!uart_quiet_restore_at) uart_quiet_restore_at = 1;
          snprintf(reply, capacity, "Error: UART session restore failed; retrying automatically");
          return false;
        }
        uart_quiet_restore_at = 0;
      }
      uart_quiet_seconds = 0;
    } else if (action == mesh::esp32::UartQuietAction::Schedule) {
      if (seconds < 5 || seconds > 300 || uart_quiet_start_at || uart_quiet_restore_at
          || !Serial || inhibit_sleep || isOTAUpdateRunning() || isRadioTestActive()
          || mesh::isUsbLoggingEnabled()) {
        snprintf(reply, capacity, "Error: UART quiet needs an idle console, logging/OTA off, and 5..300 seconds");
        return false;
      }
      uart_quiet_seconds = seconds;
      uart_quiet_start_at = millis() + 100;
      if (!uart_quiet_start_at) uart_quiet_start_at = 1;
      snprintf(reply, capacity, "OK - UART quiet in 100ms after replies drain, for %us; automatic restore", unsigned(seconds));
      return true;
    }
    const int32_t remaining = static_cast<int32_t>(uart_quiet_restore_at - millis());
    snprintf(reply, capacity, "uart=%s remaining_s=%lu", uart_quiet_start_at ? "armed"
        : uart_quiet_restore_at ? "quiet" : "active", static_cast<unsigned long>(
            uart_quiet_start_at ? uart_quiet_seconds : uart_quiet_restore_at && remaining > 0
                ? (uint32_t(remaining) + 999) / 1000 : 0));
    return true;
#endif
  }

  // Call after the role's reply pump, and regardless of its sleep decision.
  // A quiet console is always restored by a timer; no permanent guard bypass.
  void servicePowerTestControls(bool replies_drained = true) {
#if (!defined(ARDUINO_USB_CDC_ON_BOOT) || !ARDUINO_USB_CDC_ON_BOOT) \
    && !defined(SERIAL_RX) && !defined(SERIAL_TX)
    char ignored[96];
    const uint32_t now = millis();
    if (uart_quiet_restore_at && (static_cast<int32_t>(now - uart_quiet_restore_at) >= 0
        || inhibit_sleep || isOTAUpdateRunning() || isRadioTestActive() || mesh::isUsbLoggingEnabled())) {
      handleUartQuiet(mesh::esp32::UartQuietAction::Cancel, 0, ignored, sizeof(ignored));
    }
    if (!uart_quiet_start_at || static_cast<int32_t>(now - uart_quiet_start_at) < 0) return;
    if (static_cast<int32_t>(now - uart_quiet_start_at) >= 1900
        || inhibit_sleep || isOTAUpdateRunning() || isRadioTestActive() || mesh::isUsbLoggingEnabled()) {
      uart_quiet_start_at = 0; uart_quiet_seconds = 0;
      return;
    }
    if (!replies_drained || mesh::hasPendingUsbTerminalOutput()) return;
    Serial.flush();  // drain the hardware FIFO containing the acknowledgement
    if (!mesh::esp32::transitionUartQuietSession(
            mesh::esp32::UartQuietSessionAction::Park)) return;
    Serial.end();
    uart_quiet_restore_at = millis() + uint32_t(uart_quiet_seconds) * 1000;
    if (!uart_quiet_restore_at) uart_quiet_restore_at = 1;
    uart_quiet_start_at = 0;
#else
    (void)replies_drained;
#endif
  }

  // Temperature from ESP32 MCU
  float getMCUTemperature() override {
    uint32_t raw = 0;

    // To get and average the temperature so it is more accurate, especially in low temperature
    for (int i = 0; i < 4; i++) {
      raw += temperatureRead();
    }

    return raw / 4;
  }

  virtual void shutdownPeripherals();
  virtual void powerOff() override;
  virtual void enterDeepSleep(uint32_t secs) override;
#if MESH_ENABLE_EMERGENCY_POWER
  bool supportsEmergencySleep() const override { return true; }
  bool emergencySleepMilliseconds(uint32_t millisecs) override;
#endif

  uint32_t getIRQGpio() override {
  #ifdef P_LORA_DIO_1
    return P_LORA_DIO_1; // default for SX1262
  #else
    return -1;
  #endif
  }

  void sleep(uint32_t secs) override {
    sleepMicroseconds(static_cast<uint64_t>(secs) * 1000000ULL);
  }

  void sleepMilliseconds(uint32_t millisecs) override {
    if (millisecs != 0)
      sleepMicroseconds(static_cast<uint64_t>(millisecs) * 1000ULL, true);
  }

  void sleepMicroseconds(uint64_t wake_us, bool require_timer = false) {
    sleepMicrosecondsImpl(wake_us, require_timer, false, 0);
  }

  // Radio-profile visits use the low 32 bits of esp_timer_get_time()/micros().
  // An absolute deadline prevents setup work or a task preemption from making
  // the sleep slice outlast the visit which the scanner proved safe.
  void sleepUntilMicroseconds(uint32_t deadline_us) {
    sleepMicrosecondsImpl(0, true, true, deadline_us);
  }

  void sleepMicrosecondsImpl(uint64_t wake_us, bool require_timer,
                            bool has_deadline, uint32_t deadline_us) {
    using mesh::powerdiag::Reason;
    const bool diagnostics = mesh::powerdiag::enabled();
    // Preserve native-host signal history even while a test disables sleep.
    const bool usb_sleep_held = isUsbSleepHeld();
    if (!mesh::esp32::manualCpuSleepEnabled()) {
      if (diagnostics) mesh::powerdiag::boardVeto(Reason::PolicyOff, millis());
      return;
    }
    // A temporary UART quiet interval must not sleep past console recovery.
    // This only shortens the request and leaves every normal wake guard live.
    if (uart_quiet_restore_at) {
      const int32_t remaining_ms = static_cast<int32_t>(uart_quiet_restore_at - millis());
      if (remaining_ms <= 0) return;
      const uint32_t quiet_us = uint32_t(remaining_ms) * 1000UL;
      if (has_deadline) {
        const uint32_t now_us = micros();
        if (static_cast<int32_t>(deadline_us - now_us) > static_cast<int32_t>(quiet_us))
          deadline_us = now_us + quiet_us;
      } else {
        if (!wake_us || wake_us > quiet_us) wake_us = quiet_us;
        require_timer = true;
      }
    }
    // Native USB loses its connection in light sleep. An enumerated host
    // still needs USB serviced when no terminal asserts CDC DTR (for example,
    // after a Pi closes its serial port). Guard every caller here.
    // Sample even when another blocker is active, so an open console/OTA does
    // not leave the last positive native USB host signal stale.
    Reason veto_reason = Reason::Board;
    int32_t veto_detail = 1;  // Explicit inhibit_sleep.
    const auto hold = [&veto_reason, &veto_detail](bool active, Reason reason, int32_t detail = 0) {
      if (active) { veto_reason = reason; veto_detail = detail; }
      return active;
    };
    bool keep_awake = inhibit_sleep
        || hold(usb_sleep_held, Reason::UsbSession, 1)
        || hold(isRadioTestActive(), Reason::Board, 2);
#if defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) && MOMENTARY_BUTTON_WAKE_FROM_SLEEP
    if (button_wake_hold_active) {
      button_wake_hold_active = (uint32_t)(millis() - button_wake_at) < 30000UL;
      keep_awake = hold(button_wake_hold_active, Reason::Button) || keep_awake;
    }
#endif
#if !defined(ARDUINO_USB_CDC_ON_BOOT) || !ARDUINO_USB_CDC_ON_BOOT
    // External USB-to-UART chips cannot report host attachment here. Light
    // sleep loses their first input bytes; UART wake loses the wake character
    // too. HardwareSerial's bool reports an installed driver, not a USB host.
    // Keep an enabled console available even before its first command.
    keep_awake = keep_awake || hold(static_cast<bool>(Serial), Reason::SerialIO);
#endif
#if MESH_USB_LOGGING_AVAILABLE
    // A live logging stream must also remain available before a host opens
    // it and across host disconnects. Compiled-out logging is not a blocker.
    keep_awake = keep_awake || hold(mesh::isUsbLoggingEnabled(), Reason::UsbLogging);
#endif
    if (!keep_awake) {
      const Reason wireless = mesh::esp32::manualLightSleepBlocker();
      keep_awake = hold(wireless != Reason::Count, wireless);
    }
    if (keep_awake) {
      if (diagnostics) mesh::powerdiag::boardVeto(veto_reason, millis(), veto_detail);
      if (!has_deadline) delay(1); // A bounded profile visit must retain polling.
      return;
    }

    // Capture setup failures, but report them only after the wait (or veto).
    // Otherwise its real elapsed time would be mislabelled as blocked time.
    esp_err_t setup_error = ESP_OK;
    // Configure timer wakeup
    if (!has_deadline && wake_us > 0) {
      const esp_err_t error = esp_sleep_enable_timer_wakeup(wake_us);
      // A calibration slice must not inherit an old, longer timer after a
      // setup failure. Keep the legacy seconds API's policy unchanged.
      if (require_timer && error != ESP_OK) {
        if (diagnostics) mesh::powerdiag::boardError(error, millis());
        return;
      }
      if (diagnostics && error != ESP_OK) setup_error = error;
    }

    const uint32_t irqGpio = getIRQGpio();
    const bool radio_wakeup = irqGpio != static_cast<uint32_t>(-1);
    if (has_deadline && !radio_wakeup) {
      // A timer-only wake cannot service a newly arrived profile packet.
      if (diagnostics) mesh::powerdiag::boardVeto(Reason::NoWake, millis());
      return;
    }
#if defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) \
    && MOMENTARY_BUTTON_WAKE_FROM_SLEEP && defined(PIN_USER_BTN)
    const int button_gpio = PIN_USER_BTN;
#else
    const int button_gpio = -1;
#endif
    if (!radio_wakeup && button_gpio < 0) {
      if (wake_us > 0) {
        if (diagnostics) mesh::powerdiag::sleepBegin(micros(), millis());
        const esp_err_t error = esp_light_sleep_start();
        if (diagnostics) {
          mesh::powerdiag::sleepEnd(micros());
          if (setup_error != ESP_OK) mesh::powerdiag::boardError(setup_error, millis());
          if (error != ESP_OK) mesh::powerdiag::boardError(error, millis());
        }
      } else if (diagnostics) {
        mesh::powerdiag::boardVeto(Reason::NoWake, millis());
      }
      return;
    }

    // Set GPIO wakeup
    gpio_num_t wakeupPin = (gpio_num_t)irqGpio;

    // Disable CPU interrupt servicing
    portENTER_CRITICAL(&sleepMux);

    // Do not sleep over a pending packet or a press that needs polling.
    const bool radio_pending = radio_wakeup && gpio_get_level(wakeupPin) == HIGH;
    if (radio_pending || (button_gpio >= 0
            && gpio_get_level((gpio_num_t)button_gpio) == USER_BTN_PRESSED)) {
      portEXIT_CRITICAL(&sleepMux);
      if (diagnostics && setup_error != ESP_OK) mesh::powerdiag::boardError(setup_error, millis());
      if (diagnostics) mesh::powerdiag::boardVeto(
          radio_pending ? Reason::Mesh : Reason::Button, millis(),
          radio_pending ? static_cast<int32_t>(irqGpio) : button_gpio);
      if (!has_deadline) delay(1);
      return;
    }

    // Configure each level source before enabling global GPIO wake. Setup
    // errors must veto sleep even with diagnostics off: a timer alone cannot
    // replace the pending radio/button event which this caller must service.
    esp_err_t gpio_error = ESP_OK;
    if (radio_wakeup) {
      const esp_err_t error = gpio_wakeup_enable(wakeupPin, GPIO_INTR_HIGH_LEVEL);
      if (error != ESP_OK) gpio_error = error;
    }
    if (button_gpio >= 0) {
      const esp_err_t error = gpio_wakeup_enable((gpio_num_t)button_gpio,
          USER_BTN_PRESSED == LOW ? GPIO_INTR_LOW_LEVEL : GPIO_INTR_HIGH_LEVEL);
      if (gpio_error == ESP_OK && error != ESP_OK) gpio_error = error;
    }
    if (gpio_error == ESP_OK) gpio_error = esp_sleep_enable_gpio_wakeup();

    // The level source also catches an IRQ asserted while configuring wake.
    // Avoid entering the SDK at all if its pending state is already visible.
    const bool pending_after_setup = (radio_wakeup && gpio_get_level(wakeupPin) == HIGH)
        || (button_gpio >= 0
            && gpio_get_level((gpio_num_t)button_gpio) == USER_BTN_PRESSED);
    bool deadline_expired = false;
    if (has_deadline && gpio_error == ESP_OK && !pending_after_setup) {
      // Keep this immediately before the SDK wait with local interrupts held.
      // A short/rejected slice polls instead; it must not reuse an older timer.
      const int32_t remaining_us = static_cast<int32_t>(deadline_us - micros());
      if (remaining_us < static_cast<int32_t>(mesh::esp32::profileSleepMinimumMicros())) deadline_expired = true;
      else gpio_error = esp_sleep_enable_timer_wakeup(static_cast<uint32_t>(remaining_us));
    }
    esp_err_t sleep_error = ESP_OK;
    if (gpio_error == ESP_OK && !pending_after_setup && !deadline_expired) {
      if (diagnostics) mesh::powerdiag::sleepBegin(micros(), millis());
      sleep_error = esp_light_sleep_start();
      if (diagnostics) mesh::powerdiag::sleepEnd(micros());
    }

    // Avoid ISR flood during wakeup due to HIGH LEVEL interrupt
    if (radio_wakeup) {
      gpio_wakeup_disable(wakeupPin);
      gpio_set_intr_type(wakeupPin, GPIO_INTR_POSEDGE);
    }
    if (button_gpio >= 0) {
      gpio_wakeup_disable((gpio_num_t)button_gpio);
      // ESP32 MomentaryButton polls; only its sleep wake source is temporary.
      gpio_set_intr_type((gpio_num_t)button_gpio, GPIO_INTR_DISABLE);
#if defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) && MOMENTARY_BUTTON_WAKE_FROM_SLEEP
      if (gpio_get_level((gpio_num_t)button_gpio) == USER_BTN_PRESSED) {
        // Keep the UI responsive after the wake press has been released.
        button_wake_at = millis();
        button_wake_hold_active = true;
      }
#endif
    }

    // Enable CPU interrupt servicing
    portEXIT_CRITICAL(&sleepMux);
    if (diagnostics) {
      if (setup_error != ESP_OK) mesh::powerdiag::boardError(setup_error, millis());
      if (gpio_error != ESP_OK) mesh::powerdiag::boardError(gpio_error, millis());
      if (sleep_error != ESP_OK) mesh::powerdiag::boardError(sleep_error, millis());
      if (pending_after_setup) mesh::powerdiag::boardVeto(
          radio_wakeup && gpio_get_level(wakeupPin) == HIGH ? Reason::Mesh : Reason::Button,
          millis());
      if (deadline_expired) mesh::powerdiag::boardVeto(Reason::Scheduled, millis());
    }
  }

  uint8_t getStartupReason() const override { return startup_reason; }
  bool isUserGpioAvailable(uint8_t pin) const override;

  void applyLedPolicy() override {
    if (mesh::leds::enabled()) return;
#if defined(P_LORA_TX_LED)
    digitalWrite(P_LORA_TX_LED, LOW);
#elif defined(P_LORA_TX_NEOPIXEL_LED)
    neopixelWrite(P_LORA_TX_NEOPIXEL_LED, 0, 0, 0);
#endif
  }

#if defined(P_LORA_TX_LED)
  void onBeforeTransmit() override {
    if (mesh::leds::enabled()) digitalWrite(P_LORA_TX_LED, HIGH);   // turn TX LED on
  }
  void onAfterTransmit() override {
    digitalWrite(P_LORA_TX_LED, LOW);   // turn TX LED off
  }
#elif defined(P_LORA_TX_NEOPIXEL_LED)
  #define NEOPIXEL_BRIGHTNESS    64  // white brightness (max 255)

  void onBeforeTransmit() override {
    if (mesh::leds::enabled()) {
      neopixelWrite(P_LORA_TX_NEOPIXEL_LED, NEOPIXEL_BRIGHTNESS, NEOPIXEL_BRIGHTNESS, NEOPIXEL_BRIGHTNESS);   // turn TX neopixel on (White)
    }
  }
  void onAfterTransmit() override {
    neopixelWrite(P_LORA_TX_NEOPIXEL_LED, 0, 0, 0);   // turn TX neopixel off
  }
#endif

  bool supportsBatteryCalibration() const override {
#ifdef PIN_VBAT_READ
    return true;
#else
    return false;
#endif
  }

  uint16_t getBattMilliVolts() override {
    #ifdef PIN_VBAT_READ
    analogReadResolution(12);

    uint32_t raw = 0;
    for (int i = 0; i < 4; i++) {
      raw += analogReadMilliVolts(PIN_VBAT_READ);
    }
    raw = raw / 4;

    return calibrateBatteryMillivolts(2 * raw);
  #else
    return 0;  // not supported
  #endif
  }

  const char* getManufacturerName() const override {
    return "Generic ESP32";
  }

  void reboot() override {
    esp_restart();
  }

  bool startOTAUpdate(const char* id, char reply[], bool force_ap = false) override;
  static bool rotateOTAAccessPoint(void* owner);
  bool stopOTAUpdate(char reply[]) override;
  bool isOTAUpdateRunning() const override { return ota_server != nullptr; }
  bool otaFromManifest(const char* manifest_base, const char* current_ver, bool dry_run, char reply[]) override;
  // Heavy body (TLS + JSON + flash streaming). Runs in a dedicated large-stack task
  // spawned by otaFromManifest() - public only so that task entry point can call
  // it; not meant to be invoked directly.
  bool otaFromManifestImpl(const char* manifest_base, const char* current_ver, bool dry_run, char reply[]);

  bool isUsbDataConnected() override {
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
    // Role loops may never call sleep() while a CDC client is open. Keep the
    // native-host signal history current here too; host detection never calls us.
    (void)isUsbHostConnected();
#endif
    return (bool)Serial;
#else
    return false;
#endif
  }

  bool isUsbHostConnected() override {
    bool host_connected = false;
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
#if defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE
    host_connected = Serial.isPlugged();
#elif defined(CONFIG_TINYUSB_ENABLED) && CONFIG_TINYUSB_ENABLED
    host_connected = (bool)USB;
#else
    host_connected = (bool)Serial;
#endif
#endif
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
    usb_host_sleep_policy.observe(host_connected, millis());
#endif
    // The grace is a sleep policy, not proof that a USB host is still present.
    return host_connected;
  }

  void setInhibitSleep(bool inhibit) {
    inhibit_sleep = inhibit;
  }

  bool isUsbSleepHeld() {
    // Share the native host-loss grace with short Companion sleep slices.
    // Sample even when another inhibitor is active, preserving reconnects.
    const bool host_connected = isUsbHostConnected();
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
    return host_connected || usb_host_sleep_policy.shouldKeepAwake(
        millis(), MESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS);
#else
    return host_connected;
#endif
  }

  uint32_t getResetReason() const override {
    return esp_reset_reason();
  }

  // https://docs.espressif.com/projects/esp-idf/en/v4.4.7/esp32/api-reference/system/system.html
  const char* getResetReasonString(uint32_t reason) {
    switch (reason) {
      case ESP_RST_UNKNOWN:
        return "Unknown or first boot";
      case ESP_RST_POWERON:
        return "Power-on reset";
      case ESP_RST_EXT:
        return "External reset";
      case ESP_RST_SW:
        return "Software reset";
      case ESP_RST_PANIC:
        return "Panic / exception reset";
      case ESP_RST_INT_WDT:
        return "Interrupt watchdog reset";
      case ESP_RST_TASK_WDT:
        return "Task watchdog reset";
      case ESP_RST_WDT:
        return "Other watchdog reset";
      case ESP_RST_DEEPSLEEP:
        return "Wake from deep sleep";
      case ESP_RST_BROWNOUT:
        return "Brownout (low voltage)";
      case ESP_RST_SDIO:
        return "SDIO reset";
      default:
        static char buf[40];
        snprintf(buf, sizeof(buf), "Unknown reset reason (%d)", reason);
        return buf;
    }
  }
};

namespace mesh {
namespace esp32_clock {
extern uint32_t rtc_backup_time;
extern uint32_t rtc_backup_magic;
}  // namespace esp32_clock
}  // namespace mesh
#define RTC_BACKUP_MAGIC  0xAA55CC33
#define RTC_TIME_MIN      1772323200  // 1 Mar 2026

class ESP32RTCClock : public mesh::RTCClock {
public:
  ESP32RTCClock() { }
  void begin() {
    esp_reset_reason_t reason = esp_reset_reason();
    if (reason == ESP_RST_DEEPSLEEP) {
      return;  // ESP-IDF preserves system time across deep sleep
    }
    // All other resets (power-on, crash, WDT, brownout) lose system time.
    // Restore from RTC backup if valid, otherwise use hardcoded seed.
    struct timeval tv;
    if (mesh::esp32_clock::rtc_backup_magic == RTC_BACKUP_MAGIC
        && mesh::esp32_clock::rtc_backup_time > RTC_TIME_MIN) {
      tv.tv_sec = mesh::esp32_clock::rtc_backup_time;
    } else {
      tv.tv_sec = RTC_TIME_MIN;
    }
    tv.tv_usec = 0;
    settimeofday(&tv, NULL);
  }
  uint32_t getCurrentTime() override {
    time_t _now;
    time(&_now);
    return _now;
  }
  void setCurrentTime(uint32_t time) override {
    struct timeval tv;
    tv.tv_sec = time;
    tv.tv_usec = 0;
    settimeofday(&tv, NULL);
    mesh::esp32_clock::rtc_backup_time = time;
    mesh::esp32_clock::rtc_backup_magic = RTC_BACKUP_MAGIC;
  }
  void tick() override {
    time_t now;
    time(&now);
    if (now > RTC_TIME_MIN
        && (uint32_t)now != mesh::esp32_clock::rtc_backup_time) {
      mesh::esp32_clock::rtc_backup_time = (uint32_t)now;
      mesh::esp32_clock::rtc_backup_magic = RTC_BACKUP_MAGIC;
    }
  }
};

#endif
