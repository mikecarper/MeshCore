#include <Arduino.h>
#include "target.h"
#include <helpers/radiolib/ESP32BufferedRadioHal.h>

CardputerAdvBoard board;

// FSPI (SPI2): the display owns SPI3 (HSPI, the SPIClass default on S3).
SPIClass lora_spi(FSPI);
static ESP32BufferedRadioHal radio_hal(lora_spi);
RADIO_CLASS radio = new Module(&radio_hal, P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);

WRAPPER_CLASS radio_driver(radio, board);

ESP32RTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);

#if ENV_INCLUDE_GPS
  #include <helpers/sensors/MicroNMEALocationProvider.h>
  static MicroNMEALocationProvider nmea(Serial1, &rtc_clock);
  CardputerSensorManager sensors(nmea);
#else
  CardputerSensorManager sensors;
#endif
#if CARDPUTER_ADV_UI
  CardputerDisplay display;
#endif

bool radio_init() {
  fallback_clock.begin();
  rtc_clock.begin(Wire);
#ifdef PIN_SD_CS
  // Keep the SD card deselected while the radio owns the shared bus.
  pinMode(PIN_SD_CS, OUTPUT);
  digitalWrite(PIN_SD_CS, HIGH);
#endif
  lora_spi.begin(P_LORA_SCLK, P_LORA_MISO, P_LORA_MOSI);
  return radio.std_init(&lora_spi);
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng);
}
