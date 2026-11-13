#pragma once

#include <RadioLib.h>
#include "SX126xAgcRetention.h"
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
#include "AGCTimingDiagnostics.h"
#endif

// Full receiver reset for all SX126x-family chips (SX1262, SX1268, LLCC68, STM32WLx).
// Warm sleep powers down analog, Calibrate(0x7F) refreshes ADC/PLL/image calibration,
// then re-applies RX settings that calibration may reset.
// Only CustomSX1262Wrapper opts into the SX1262-specific register workaround.
// Other SX126x-family chips retain their existing reset behavior.
template<bool protect_sx1262 = false>
inline void sx126xResetAGC(SX126x* radio, bool rx_boost_gain
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
    , AGCTimingDiagnostics* timing = nullptr
#endif
) {
  // Record command failures in diagnostic builds. Never write a patch from
  // an unsuccessful register read.
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
  constexpr bool restore_gain = true;
#else
  constexpr bool restore_gain = protect_sx1262;
#endif
  if (restore_gain) {
    // Preserve the runtime gain even without a build-time gain default.
    // SX1262 merges retention entries in the patch step instead of replacing.
    MESH_AGC_COMMAND(Gain, radio->setRxBoostedGainMode(rx_boost_gain, !protect_sx1262));
  }
  if (protect_sx1262) {
    uint8_t r_data = 0;
    int16_t patch_status = RADIOLIB_ERR_NONE;
    MESH_AGC_COMMAND(ReadPatch, (patch_status = radio->readRegister(0x8B5, &r_data, 1)));
    if (patch_status == RADIOLIB_ERR_NONE) {
      MESH_AGC_COMMAND(WritePatch, sx126xWriteRetainedAgcPatch(radio, r_data, true));
    }
  }
#undef MESH_AGC_COMMAND
}
