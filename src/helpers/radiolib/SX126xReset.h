#pragma once

#include <RadioLib.h>
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
#include "AGCTimingDiagnostics.h"
#endif

// Full receiver reset for all SX126x-family chips (SX1262, SX1268, LLCC68, STM32WLx).
// Warm sleep powers down analog, Calibrate(0x7F) refreshes ADC/PLL/image calibration,
// then re-applies RX settings that calibration may reset.
inline void sx126xResetAGC(SX126x* radio, bool rx_boost_gain
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
    , AGCTimingDiagnostics* timing = nullptr
#endif
) {
  // Observe existing return values only. Preserve the command order and the
  // original continue-on-error behavior for this comparison build.
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
#define MESH_AGC_COMMAND(step, command) do { \
  const int16_t result = (command); \
  if (timing) timing->commandResult(AGCTimingDiagnostics::Step::step, result); \
} while (0)
#else
#define MESH_AGC_COMMAND(step, command) (void)(command)
#endif
  MESH_AGC_COMMAND(Sleep, radio->sleep(true));
  MESH_AGC_COMMAND(Standby, radio->standby(RADIOLIB_SX126X_STANDBY_RC, true));

  uint8_t calData = RADIOLIB_SX126X_CALIBRATE_ALL;
  MESH_AGC_COMMAND(Calibrate, radio->mod->SPIwriteStream(RADIOLIB_SX126X_CMD_CALIBRATE, &calData, 1, true, false));
  radio->mod->hal->delay(5);
  uint32_t start = millis();
  while (radio->mod->hal->digitalRead(radio->mod->getGpio())) {
    if (millis() - start > 50) {
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
      if (timing) timing->pollTimeout();
#endif
      break;
    }
    radio->mod->hal->yield();
  }

  // Calibrate(0x7F) defaults image calibration to 902-928MHz band.
  // Re-calibrate for the actual operating frequency.
  MESH_AGC_COMMAND(Image, radio->calibrateImage(radio->freqMHz));

#ifdef SX126X_DIO2_AS_RF_SWITCH
  MESH_AGC_COMMAND(Dio2, radio->setDio2AsRfSwitch(SX126X_DIO2_AS_RF_SWITCH));
#endif
#ifdef SX126X_RX_BOOSTED_GAIN
  MESH_AGC_COMMAND(Gain, radio->setRxBoostedGainMode(rx_boost_gain));
#endif
#ifdef SX126X_REGISTER_PATCH
  uint8_t r_data = 0;
  MESH_AGC_COMMAND(ReadPatch, radio->readRegister(0x8B5, &r_data, 1));
  r_data |= 0x01;
  MESH_AGC_COMMAND(WritePatch, radio->writeRegister(0x8B5, &r_data, 1));
#endif
#undef MESH_AGC_COMMAND
}
