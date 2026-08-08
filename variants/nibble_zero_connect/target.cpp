#include <Arduino.h>
#include "target.h"
#include <helpers/radiolib/SX126xBufferedRadioHal.h>

ESP32Board board;

static SPIClass spi;
static SX126xBufferedRadioHal radio_hal(spi);
RADIO_CLASS radio = new Module(&radio_hal, P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);
WRAPPER_CLASS radio_driver(radio, board);

ESP32RTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);
SensorManager sensors;

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
  // active-low button on GPIO1: enable the internal pull-up (4th arg) so a
  // floating input can't read as a phantom long-press → spurious CLI rescue at boot.
  MomentaryButton user_btn(PIN_USER_BTN, 1000, true, true);
#endif

#ifndef LORA_CR
  #define LORA_CR      5
#endif

bool radio_init() {
  fallback_clock.begin();
  rtc_clock.begin(Wire);
  
  return radio.std_init(&spi);
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng);
}


