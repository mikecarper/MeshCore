#include "target.h"

#include <Arduino.h>
#include <helpers/ArduinoHelpers.h>

#ifdef ENV_INCLUDE_GPS
#include <helpers/sensors/MicroNMEALocationProvider.h>
#endif

T096Board board;

#if defined(P_LORA_SCLK)
RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, SPI);
#else
RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);
#endif

WRAPPER_CLASS radio_driver(radio, board);

VolatileRTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);

#if ENV_INCLUDE_GPS
#include <helpers/sensors/MicroNMEALocationProvider.h>
MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock, GPS_RESET, GPS_EN, &board.periph_power);
EnvironmentSensorManager sensors = EnvironmentSensorManager(nmea);
#else
EnvironmentSensorManager sensors;
#endif

#ifdef DISPLAY_CLASS
DISPLAY_CLASS display(&board.periph_power);
// The T096 USER switch is active-low. Keep the nRF52 pull-up enabled even
// though the schematic also shows an external 10K pull-up: the OTAFIX
// bootloader uses the same defensive configuration, and it prevents a weak or
// missing board resistor from leaving the application button input floating.
// Keep multi-click enabled now that the shared state machine rejects switch
// bounce and the event-driven loop remains awake through gesture deadlines.
MomentaryButton user_btn(PIN_USER_BTN, 1000, true, true, true);
#endif

bool radio_init() {
  rtc_clock.begin(Wire);

#if defined(P_LORA_SCLK)
  return radio.std_init(&SPI);
#else
  return radio.std_init();
#endif
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng); // create new random identity
}
