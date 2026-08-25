#include <Arduino.h>
#include "target.h"
#include <helpers/ArduinoHelpers.h>
#ifdef USE_CC310_HW_CRYPTO
  #include <helpers/NRF52Crypto.h>
#endif

RAK3401Board board;

#ifndef PIN_USER_BTN
  #define PIN_USER_BTN (-1)
#endif

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
  MomentaryButton user_btn(PIN_USER_BTN, 1000, true, true);

  #if defined(PIN_USER_BTN_ANA)
  MomentaryButton analog_btn(PIN_USER_BTN_ANA, 1000, 20);
  #endif
#endif

// The RAK13302 is wired to the second WisBlock SPI controller. Keep the same
// controller instance for startup and watchdog recovery; resetting the radio
// must not replace or tear down the SPI object used by its Module.
RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, SPI1);

WRAPPER_CLASS radio_driver(radio, board);

VolatileRTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);

#if ENV_INCLUDE_GPS
  #include <helpers/sensors/MicroNMEALocationProvider.h>
  MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock);
  EnvironmentSensorManager sensors = EnvironmentSensorManager(nmea);
#else
  EnvironmentSensorManager sensors;
#endif

static bool target_radio_available = false;

bool radio_init() {
  rtc_clock.begin(Wire);
  // A successful physical reset proves that the external SX1262 can release
  // BUSY before RadioLib is allowed to issue SPI commands. This also prevents
  // a missing RAK13302 from turning an initialization retry into a watchdog
  // reset loop.
  if (!board.recoverRadio()) {
    target_radio_available = false;
    mesh::usbLoggingPort().println(
        "ERROR: RAK13302 BUSY stayed high after reset; check module seating and power");
    return false;
  }

  if (radio.std_init(&SPI1)) {
    target_radio_available = true;
    board.enableRadioFrontend();
    return true;
  }

  // Retry once after another physical reset. Keep the shared GPS/sensor rail
  // and the SPI controller instance intact throughout recovery.
  if (!board.recoverRadio()) {
    target_radio_available = false;
    return false;
  }
  if (!radio.std_init(&SPI1)) {
    target_radio_available = false;
    return false;
  }
  target_radio_available = true;
  board.enableRadioFrontend();
  return true;
}

uint32_t radio_fallback_rng_seed() {
  uint32_t seed = micros() ^ NRF_FICR->DEVICEID[0] ^ NRF_FICR->DEVICEID[1];
#ifdef USE_CC310_HW_CRYPTO
  mesh::mixCC310Random(reinterpret_cast<uint8_t*>(&seed), sizeof(seed));
#endif
  return seed;
}

mesh::LocalIdentity radio_new_identity() {
  if (target_radio_available) {
    RadioNoiseListener rng(radio);
    return mesh::LocalIdentity(&rng);
  }

  class RAK3401IdentityRNG : public mesh::RNG {
  public:
    void random(uint8_t* dest, size_t sz) override {
      for (size_t i = 0; i < sz; ++i) {
        dest[i] = static_cast<uint8_t>(::random(0, 256));
      }
#ifdef USE_CC310_HW_CRYPTO
      mesh::mixCC310Random(dest, sz);
#endif
    }
  } rng;
  return mesh::LocalIdentity(&rng);
}
