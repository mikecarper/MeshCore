#include <Arduino.h>
#include "target.h"
#include <helpers/radiolib/ESP32LoRaChipProbe.h>
#include <helpers/radiolib/SX126xBufferedRadioHal.h>
#include <helpers/radiolib/TunableRadioHal.h>

UniversalT3S3Board board;

static SPIClass spi;
static SX126xBufferedRadioHal sx126x_hal(spi);
static TunableRadioHal sx127x_hal(spi, 2000000);
static Module sx126x_module(&sx126x_hal, P_LORA_NSS, P_LORA_DIO_1,
                           P_LORA_RESET, P_LORA_BUSY);
static Module sx127x_module(&sx127x_hal, P_LORA_NSS, P_LORA_DIO_0,
                           P_LORA_RESET, P_LORA_DIO_1);
UniversalLoRaRadio radio_driver(sx126x_module, sx127x_module, board);

ESP32RTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);
SensorManager sensors;

#ifdef DISPLAY_CLASS
DISPLAY_CLASS display;
MomentaryButton user_btn(PIN_USER_BTN, 1000, true);
#endif

mesh::LoRaRadioFamily universalLoRaRadioFamily() {
  return radio_driver.family();
}

bool prepareUniversalLoRaRadio() {
  spi.begin(P_LORA_SCLK, P_LORA_MISO, P_LORA_MOSI);
  const mesh::LoRaChipProbePins pins{P_LORA_NSS, P_LORA_RESET, P_LORA_BUSY};
  return radio_driver.selectFamily(mesh::probeUniversalLoRaChip(spi, pins));
}

bool radio_init() {
  fallback_clock.begin();
  rtc_clock.begin(Wire);
  return radio_driver.stdInit(&spi);
}

mesh::LocalIdentity radio_new_identity() {
  auto* physical = radio_driver.physicalLayer();
  if (!physical) return mesh::LocalIdentity();
  RadioNoiseListener rng(*physical);
  return mesh::LocalIdentity(&rng);
}
