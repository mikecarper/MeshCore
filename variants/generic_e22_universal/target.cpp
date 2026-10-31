#include <Arduino.h>
#include "target.h"
#include <helpers/radiolib/ESP32LoRaChipProbe.h>
#include <helpers/radiolib/SX126xBufferedRadioHal.h>

UniversalE22Board board;
static SPIClass spi;
static SX126xBufferedRadioHal radio_hal(spi);
static Module radio_module(&radio_hal, P_LORA_NSS, P_LORA_DIO_1,
                           P_LORA_RESET, P_LORA_BUSY);
// Both chips use the same control pins; the SX127x backend is excluded.
UniversalLoRaRadio radio_driver(radio_module, radio_module, board);

ESP32RTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);
SensorManager sensors;

mesh::LoRaRadioFamily universalLoRaRadioFamily() {
  return radio_driver.family();
}

bool prepareUniversalLoRaRadio() {
  if (radio_driver.family() != mesh::LoRaRadioFamily::None) return true;
  spi.begin(P_LORA_SCLK, P_LORA_MISO, P_LORA_MOSI);
  const mesh::LoRaChipProbePins pins{P_LORA_NSS, P_LORA_RESET, P_LORA_BUSY};
  return radio_driver.selectFamily(mesh::probeUniversalLoRaChip(spi, pins));
}

bool radio_init() {
  fallback_clock.begin();
  rtc_clock.begin(Wire);
  return prepareUniversalLoRaRadio() && radio_driver.stdInit(&spi);
}

mesh::LocalIdentity radio_new_identity() {
  auto* physical = radio_driver.physicalLayer();
  if (!physical) return mesh::LocalIdentity();
  RadioNoiseListener rng(*physical);
  return mesh::LocalIdentity(&rng);
}
