#include <Arduino.h>
#include "target.h"
#include <helpers/ArduinoHelpers.h>
#include <helpers/ESP32TrueRandom.h>

SenseCapIndicatorBoard board;

#ifdef SENSECAP_INDICATOR_LORA
static SPIClass radio_spi(FSPI);
static IndicatorRadioHal radio_hal(radio_spi);
RADIO_CLASS radio = new Module(&radio_hal, P_LORA_NSS, P_LORA_DIO_1,
                               P_LORA_RESET, P_LORA_BUSY);
WRAPPER_CLASS radio_driver(radio, board, radio_hal);
static bool target_radio_available = false;
#else
ESPNOWRadio radio_driver;
#endif

ESP32RTCClock rtc_clock;
#if defined(ENV_INCLUDE_GPS)
MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, (mesh::RTCClock*)&rtc_clock);
EnvironmentSensorManager sensors = EnvironmentSensorManager(nmea);
#else
EnvironmentSensorManager sensors = EnvironmentSensorManager();
#endif

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
  #ifdef PIN_USER_BTN
  // Touch supplies the other navigation actions, so dispatch the physical
  // button's single click immediately instead of waiting for a multi-click.
  MomentaryButton user_btn(PIN_USER_BTN, 1000, true, true, false);
  #endif
#endif

bool radio_init() {
  rtc_clock.begin();

#ifdef SENSECAP_INDICATOR_LORA
  if (!radio_hal.beginExpander()) {
    mesh::usbLoggingPort().println("ERROR: radio I/O expander unavailable");
    target_radio_available = false;
    return false;
  }
  target_radio_available = radio.std_init(&radio_spi);
  return target_radio_available;
#else
  radio_driver.init();

  return true;  // success
#endif
}

// Combine the normal software source with true entropy captured before RF/ADC
// initialization. The hardware source is never read in pseudo-random-only mode.
class ESP_RNG : public mesh::RNG {
public:
  void random(uint8_t* dest, size_t sz) override {
    for (size_t i = 0; i < sz; ++i) {
      dest[i] = (::random(0, 256) & 0xFF);
    }
    mesh::mixESP32TrueRandom(dest, sz);
  }
};

#ifdef RECOVERABLE_EXTERNAL_RADIO
uint32_t radio_fallback_rng_seed() { return esp_random(); }
#endif

mesh::LocalIdentity radio_new_identity() {
#ifdef SENSECAP_INDICATOR_LORA
  if (target_radio_available) {
    RadioNoiseListener rng(radio);
    return mesh::LocalIdentity(&rng);
  }
  ESP_RNG rng;
  return mesh::LocalIdentity(&rng);
#else
  ESP_RNG rng;
  return mesh::LocalIdentity(&rng);  // create new random identity
#endif
}
