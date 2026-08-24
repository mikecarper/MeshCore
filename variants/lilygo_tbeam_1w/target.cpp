#include <Arduino.h>
#include <esp_random.h>
#include <helpers/ESP32TrueRandom.h>
#include "target.h"

TBeam1WBoard board;

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
  MomentaryButton user_btn(PIN_USER_BTN, 1000, true);
  #ifdef PIN_WIFI_BTN
    // The BOOT switch is also a normal active-low GPIO after startup. Treat it
    // as a dedicated single-click control; the board supplies a 10K pull-up,
    // and enabling the ESP32 pull-up as well keeps the input deterministic.
    MomentaryButton wifi_btn(PIN_WIFI_BTN, 0, true, true, false);
  #endif
#endif

static SPIClass spi;
static bool target_peripherals_initialized = false;
static bool target_radio_available = false;

RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, spi);

WRAPPER_CLASS radio_driver(radio, board);

ESP32RTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);

#if ENV_INCLUDE_GPS
  #include <helpers/sensors/MicroNMEALocationProvider.h>
  MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock);
  EnvironmentSensorManager sensors = EnvironmentSensorManager(nmea);
#else
  EnvironmentSensorManager sensors;
#endif

bool radio_init() {
  if (target_peripherals_initialized && !target_radio_available) {
    board.powerCycleRadio();
  }

  if (!target_peripherals_initialized) {
    fallback_clock.begin();
    rtc_clock.begin(Wire);

    // Initialize the shared SPI bus once. Repeated radio recovery probes only
    // reset and initialize the SX1262 itself.
    spi.begin(P_LORA_SCLK, P_LORA_MISO, P_LORA_MOSI);
    target_peripherals_initialized = true;
  }

  // GPS serial initialized by EnvironmentSensorManager::begin()

  bool success = radio.std_init(&spi);
  target_radio_available = success;
  if (success) {
    board.attachRadioDriver(&radio_driver);
    // T-Beam 1W has external PA requiring longer ramp time (>800us recommended)
    // RADIOLIB_SX126X_PA_RAMP_800U = 0x05
    radio.setTxParams(LORA_TX_POWER, RADIOLIB_SX126X_PA_RAMP_800U);
  } else {
    MESH_DEBUG_PRINTLN(
        "T-Beam 1W radio pins: LDO=%d NSS=%d RST=%d BUSY=%d MISO=%d DIO1=%d CTRL=%d",
        digitalRead(SX126X_POWER_EN), digitalRead(P_LORA_NSS),
        digitalRead(P_LORA_RESET), digitalRead(P_LORA_BUSY),
        digitalRead(P_LORA_MISO), digitalRead(P_LORA_DIO_1),
        digitalRead(SX126X_RXEN));
  }
  return success;
}

uint32_t radio_fallback_rng_seed() {
  return esp_random();
}

mesh::LocalIdentity radio_new_identity() {
  if (target_radio_available) {
    RadioNoiseListener rng(radio);
    return mesh::LocalIdentity(&rng);
  }

  class ESP32IdentityRNG : public mesh::RNG {
  public:
    void random(uint8_t* dest, size_t sz) override {
      esp_fill_random(dest, sz);
      mesh::mixESP32TrueRandom(dest, sz);
    }
  } rng;
  return mesh::LocalIdentity(&rng);
}
