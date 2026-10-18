#pragma once

#include <driver/gpio.h>
#include <driver/rtc_io.h>

namespace mesh {
namespace esp32 {

// The RTC pad map differs by chip and board pin choice. NSS18 on classic
// ESP32 and NSS43/LNA42 on S3, for example, are ordinary digital GPIOs.
// These RTC-only wrappers are also used outside terminal shutdown. Do not
// replace them with digital holds: that would freeze live peripheral pins.
inline bool supportsRtcGpio(gpio_num_t pin) {
#if defined(SOC_RTCIO_INPUT_OUTPUT_SUPPORTED) && SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
  return static_cast<int>(pin) >= 0 && rtc_gpio_is_valid_gpio(pin);
#else
  (void)pin;
  return false;
#endif
}

inline esp_err_t holdRtcGpioIfSupported(gpio_num_t pin) {
#if defined(SOC_RTCIO_INPUT_OUTPUT_SUPPORTED) && SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
  if (supportsRtcGpio(pin)) return rtc_gpio_hold_en(pin);
#else
  (void)pin;
#endif
  return ESP_OK;
}

inline esp_err_t releaseRtcGpioIfSupported(gpio_num_t pin) {
#if defined(SOC_RTCIO_INPUT_OUTPUT_SUPPORTED) && SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
  if (supportsRtcGpio(pin)) return rtc_gpio_hold_dis(pin);
#else
  (void)pin;
#endif
  return ESP_OK;
}

inline esp_err_t deinitRtcGpioIfSupported(gpio_num_t pin) {
#if defined(SOC_RTCIO_INPUT_OUTPUT_SUPPORTED) && SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
  if (supportsRtcGpio(pin)) return rtc_gpio_deinit(pin);
#else
  (void)pin;
#endif
  return ESP_OK;
}

// An inline function's local static is shared across translation units. A
// namespace-static flag here would silently give each board/FEM its own copy.
inline bool& terminalDigitalHoldPending() {
  static bool pending = false;
  return pending;
}

inline esp_err_t configureHeldOutput(gpio_num_t pin, uint32_t level) {
  if (static_cast<int>(pin) < 0 || !GPIO_IS_VALID_OUTPUT_GPIO(pin)) {
    return ESP_ERR_INVALID_ARG;
  }
  // Preload the latch before enabling the output. gpio_config also selects
  // GPIO rather than a boot-time/peripheral IOMUX function after partial init.
  esp_err_t result = gpio_set_level(pin, level ? 1 : 0);
  if (result != ESP_OK) return result;
  gpio_config_t config = {};
  config.pin_bit_mask = 1ULL << static_cast<unsigned>(pin);
  config.mode = GPIO_MODE_OUTPUT;
  config.pull_up_en = GPIO_PULLUP_DISABLE;
  config.pull_down_en = GPIO_PULLDOWN_DISABLE;
  config.intr_type = GPIO_INTR_DISABLE;
  return gpio_config(&config);
}

// Explicitly select a known output before latching it. RTC pads can also be
// retained for packet-wake sleep (e.g. W12 FEM rails). Non-RTC/digital pads
// on older chips require the terminal-only commit below; chips with per-pad
// deep-sleep retention hold directly. Never globally freeze live pads.
// The IDF GPIO API selects the correct RTC/digital hold implementation.
inline esp_err_t holdOutputForDeepSleep(gpio_num_t pin, uint32_t level) {
  esp_err_t result = configureHeldOutput(pin, level);
  if (result != ESP_OK) return result;
  result = gpio_hold_en(pin);
  if (result == ESP_OK && !supportsRtcGpio(pin)) {
    terminalDigitalHoldPending() = true;
  }
  return result;
}

inline esp_err_t releaseOutputAfterDeepSleep(gpio_num_t pin, uint32_t level) {
  // IDF requires mode/value to be restored BEFORE hold_dis to avoid a low
  // glitch on wake. This also covers software resets while a hold was active.
  esp_err_t result = configureHeldOutput(pin, level);
  return result == ESP_OK ? gpio_hold_dis(pin) : result;
}

inline void resetTerminalDeepSleepHolds() {
#if !defined(SOC_GPIO_SUPPORT_HOLD_SINGLE_IO_IN_DSLP) || !SOC_GPIO_SUPPORT_HOLD_SINGLE_IO_IN_DSLP
  gpio_deep_sleep_hold_dis();
#endif
  terminalDigitalHoldPending() = false;
}

inline void commitTerminalDeepSleepHolds() {
  // Chips such as C6 retain individual gpio_hold_en pads during deep sleep
  // and do not expose the global auto-hold API. Older chips need auto-hold.
  // IDF4.4's digital auto-hold affects all digital pads DURING deep sleep.
  // Arm it only after every virtual shutdown hook and transport quiescence,
  // immediately before terminal deep sleep; never in runtime or light sleep.
#if !defined(SOC_GPIO_SUPPORT_HOLD_SINGLE_IO_IN_DSLP) || !SOC_GPIO_SUPPORT_HOLD_SINGLE_IO_IN_DSLP
  if (terminalDigitalHoldPending()) gpio_deep_sleep_hold_en();
#endif
}

} // namespace esp32
} // namespace mesh
