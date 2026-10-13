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
#include "UsbLogging.h"
#include "UsbHostSleepPolicy.h"
#include <helpers/esp32/WiFiOtaSession.h>

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
  bool ota_started_ap = false;
  bool ota_started_radio = false;
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
  mesh::UsbHostSleepPolicy usb_host_sleep_policy;
#endif
#if defined(LIGHTWEIGHT_WIFI_OTA)
  void* ota_server = nullptr;
  mesh::wifi::OtaSessionIdentity ota_identity;
  friend class LightweightOTAServer;
#else
  AsyncWebServer* ota_server = nullptr;
#endif
  static inline portMUX_TYPE sleepMux = portMUX_INITIALIZER_UNLOCKED;

public:
  void begin() {
    // Arduino's early init hook normally captured this before initVariant().
    // Keep this idempotent fallback before this class touches ADC peripherals.
    mesh::initializeESP32TrueRandom();

    // for future use, sub-classes SHOULD call this from their begin()
    startup_reason = BD_STARTUP_NORMAL;    

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

  uint32_t getIRQGpio() override {
  #ifdef P_LORA_DIO_1
    return P_LORA_DIO_1; // default for SX1262
  #else
    return -1;
  #endif
  }

  void sleep(uint32_t secs) override {
    // Native USB loses its connection in light sleep. An enumerated host
    // still needs USB serviced when no terminal asserts CDC DTR (for example,
    // after a Pi closes its serial port). Guard every caller here.
    // Sample even when another blocker is active, so an open console/OTA does
    // not leave the last positive native USB host signal stale.
    const bool usb_sleep_held = isUsbSleepHeld();
    bool keep_awake = inhibit_sleep || usb_sleep_held || isRadioTestActive();
#if !defined(ARDUINO_USB_CDC_ON_BOOT) || !ARDUINO_USB_CDC_ON_BOOT
    // External USB-to-UART chips cannot report host attachment here. Light
    // sleep loses their first input bytes; UART wake loses the wake character
    // too. HardwareSerial's bool reports an installed driver, not a USB host.
    // Keep an enabled console available even before its first command.
    keep_awake = keep_awake || static_cast<bool>(Serial);
#endif
#if MESH_USB_LOGGING_AVAILABLE
    // A live logging stream must also remain available before a host opens
    // it and across host disconnects. Compiled-out logging is not a blocker.
    keep_awake = keep_awake || mesh::isUsbLoggingEnabled();
#endif
    if (keep_awake) {
      delay(1); // Keep USB and OTA tasks running.
      return;
    }

    // Configure timer wakeup
    if (secs > 0) {
      esp_sleep_enable_timer_wakeup(secs * 1000000ULL); // Wake up periodically to do scheduled jobs
    }

    const uint32_t irqGpio = getIRQGpio();
    const bool radio_wakeup = irqGpio != static_cast<uint32_t>(-1);
#if defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) \
    && MOMENTARY_BUTTON_WAKE_FROM_SLEEP && defined(PIN_USER_BTN)
    const int button_gpio = PIN_USER_BTN;
#else
    const int button_gpio = -1;
#endif
    if (!radio_wakeup && button_gpio < 0) {
      if (secs > 0) {
        esp_light_sleep_start();
      }
      return;
    }

    // Set GPIO wakeup
    gpio_num_t wakeupPin = (gpio_num_t)irqGpio;

    // Disable CPU interrupt servicing
    portENTER_CRITICAL(&sleepMux);

    // Do not sleep over a pending packet or a press that needs polling.
    if ((radio_wakeup && gpio_get_level(wakeupPin) == HIGH)
        || (button_gpio >= 0
            && gpio_get_level((gpio_num_t)button_gpio) == USER_BTN_PRESSED)) {
      portEXIT_CRITICAL(&sleepMux);
      delay(1);
      return;
    }

    // Configure GPIO wakeup
    esp_sleep_enable_gpio_wakeup();
    if (radio_wakeup) {
      gpio_wakeup_enable(wakeupPin, GPIO_INTR_HIGH_LEVEL);
    }
    if (button_gpio >= 0) {
      gpio_wakeup_enable((gpio_num_t)button_gpio,
          USER_BTN_PRESSED == LOW ? GPIO_INTR_LOW_LEVEL : GPIO_INTR_HIGH_LEVEL);
    }

    // MCU enters light sleep
    esp_light_sleep_start();

    // Avoid ISR flood during wakeup due to HIGH LEVEL interrupt
    if (radio_wakeup) {
      gpio_wakeup_disable(wakeupPin);
      gpio_set_intr_type(wakeupPin, GPIO_INTR_POSEDGE);
    }
    if (button_gpio >= 0) {
      gpio_wakeup_disable((gpio_num_t)button_gpio);
      // ESP32 MomentaryButton polls; only its sleep wake source is temporary.
      gpio_set_intr_type((gpio_num_t)button_gpio, GPIO_INTR_DISABLE);
    }

    // Enable CPU interrupt servicing
    portEXIT_CRITICAL(&sleepMux);
  }

  uint8_t getStartupReason() const override { return startup_reason; }
  bool isUserGpioAvailable(uint8_t pin) const override;

#if defined(P_LORA_TX_LED)
  void onBeforeTransmit() override {
    digitalWrite(P_LORA_TX_LED, HIGH);   // turn TX LED on
  }
  void onAfterTransmit() override {
    digitalWrite(P_LORA_TX_LED, LOW);   // turn TX LED off
  }
#elif defined(P_LORA_TX_NEOPIXEL_LED)
  #define NEOPIXEL_BRIGHTNESS    64  // white brightness (max 255)

  void onBeforeTransmit() override {
    neopixelWrite(P_LORA_TX_NEOPIXEL_LED, NEOPIXEL_BRIGHTNESS, NEOPIXEL_BRIGHTNESS, NEOPIXEL_BRIGHTNESS);   // turn TX neopixel on (White)
  }
  void onAfterTransmit() override {
    neopixelWrite(P_LORA_TX_NEOPIXEL_LED, 0, 0, 0);   // turn TX neopixel off
  }
#endif

  uint16_t getBattMilliVolts() override {
    #ifdef PIN_VBAT_READ
    analogReadResolution(12);

    uint32_t raw = 0;
    for (int i = 0; i < 4; i++) {
      raw += analogReadMilliVolts(PIN_VBAT_READ);
    }
    raw = raw / 4;

    return (2 * raw);
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
  bool getOTAIdentity(char reply[], size_t max_len) override;
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
