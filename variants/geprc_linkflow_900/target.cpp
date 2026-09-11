#include <Arduino.h>
#include "target.h"

ELRSTxBoard board;

static SPIClass spi;
RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_0, P_LORA_RESET, P_LORA_DIO_1, spi);

// The "power_values" array from the ExpressLRS hardware layout for this
// module (ExpressLRS target "GEPRC LINKFLOW 900M TX", firmware
// Unified_ESP32_900_TX), paired with the PowerLevels_e step each index maps
// to. power_min is 2, so index 0 is PWR_50mW. Approved measured calibration:
// https://github.com/ExpressLRS/Targets/blob/504178dcfa469ee32f4290d6ae9ba02e2f2f365e/TX/GEPRC%20900%20Linkflow.json
// The older eight-step table overdrives the PA at its lower settings.
static const DacPaLevel POWER_LEVELS[] = {
  { 17,   0 },   //   50 mW  <- ExpressLRS minimum and default
  { 20,  22 },   //  100 mW
  { 24,  50 },   //  250 mW
  { 27,  75 },   //  500 mW
  { 30, 130 },   // 1000 mW
  { 33, 225 },   // 2000 mW  <- above MAX_LORA_TX_POWER, see platformio.ini
};

// MAX_LORA_TX_POWER is the single definition of this board's ceiling: the CLI
// validates against it, and the wrapper enforces it independently so a request
// that gets past the CLI cannot saturate the amplifier at its top table entry.
WRAPPER_CLASS radio_driver(radio, board, PIN_PA_APC2,
                           POWER_LEVELS,
                           sizeof(POWER_LEVELS) / sizeof(POWER_LEVELS[0]),
                           MAX_LORA_TX_POWER);

ESP32RTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);
SensorManager sensors;

bool radio_init() {
  fallback_clock.begin();
  rtc_clock.begin(Wire);

  if (!radio.std_init(&spi)) return false;

  // std_init() left the SX1276 at LORA_TX_POWER, which on this board is a
  // post-amplifier number. Hand power control over to the amplifier instead.
  return radio_driver.beginPowerControl(LORA_TX_POWER);
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng);  // create new random identity
}
