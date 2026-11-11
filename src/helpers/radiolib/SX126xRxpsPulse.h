#pragma once

#include <RadioLib.h>

namespace mesh {
namespace rxpspulse {

enum class Step : uint8_t {
  None,
  ValidateRx,
  ValidateSleep,
  ReadIrq,
  StandbyRc,
  StopRtc,
  ReadRtcEvent,
  ClearRtcEvent,
  ArmDutyCycle,
};

struct Result {
  int16_t error;
  Step step;

  bool ok() const { return error == RADIOLIB_ERR_NONE; }
};

struct Periods {
  uint32_t rxTicks = 0;
  uint32_t sleepTicks = 0;
};

// Requested sleep includes RadioLib's TCXO + 1000 us transition allowance.
// The one-tick API minimum is not a characterized safe sleep/wake duration.
inline Result encodePeriods(uint32_t rx_us, uint32_t sleep_us,
                            uint32_t transition_us, Periods& periods) {
  periods = Periods{};
  const uint64_t rx_ticks = (uint64_t(rx_us) * 8) / 125;
  if (rx_ticks == 0 || rx_ticks > 0xFFFFFFUL) {
    return {RADIOLIB_ERR_INVALID_RX_PERIOD, Step::ValidateRx};
  }
  if (sleep_us <= transition_us) {
    return {RADIOLIB_ERR_INVALID_SLEEP_PERIOD, Step::ValidateSleep};
  }
  const uint64_t sleep_ticks = (uint64_t(sleep_us - transition_us) * 8) / 125;
  if (sleep_ticks == 0 || sleep_ticks > 0xFFFFFFUL) {
    return {RADIOLIB_ERR_INVALID_SLEEP_PERIOD, Step::ValidateSleep};
  }
  periods.rxTicks = static_cast<uint32_t>(rx_ticks);
  periods.sleepTicks = static_cast<uint32_t>(sleep_ticks);
  return {RADIOLIB_ERR_NONE, Step::None};
}

// Caller owns the physical radio and admits SPI only in a trusted awake window.
// No wakeup-NOP is sent. Caller must not issue these during RXPS sleep; these
// helpers do not establish packet/phase ownership or exclude a phase race.
// Direct stream calls preserve the original BUSY/status result, unlike the
// RadioLib register wrappers. verify=false does not skip that original check.
template <typename ModuleType>
inline Result readIrq(ModuleType* module, uint16_t& irq) {
  uint8_t data[2] = {0, 0};
  const int16_t error = module->SPIreadStream(
      RADIOLIB_SX126X_CMD_GET_IRQ_STATUS, data, sizeof(data), true, false);
  if (error != RADIOLIB_ERR_NONE) return {error, Step::ReadIrq};
  irq = (uint16_t(data[0]) << 8) | data[1];
  return {RADIOLIB_ERR_NONE, Step::None};
}

template <typename ModuleType>
inline Result standbyRc(ModuleType* module) {
  // Do not use standby(mode)'s implicit, unchecked NSS wakeup-NOP.
  // Caller must separately invalidate CustomSX1262's reusable context.
  const uint8_t mode = RADIOLIB_SX126X_STANDBY_RC;
  const int16_t error = module->SPIwriteStream(
      RADIOLIB_SX126X_CMD_SET_STANDBY, &mode, 1, true, false);
  return {error, error == RADIOLIB_ERR_NONE ? Step::None : Step::StandbyRc};
}

template <typename ModuleType>
inline Result stopRtc(ModuleType* module) {
  // SX1261/2 erratum 15.3: stop the RTC, then clear its potential event while
  // preserving unrelated event-mask bits. Never continue after a failed step.
  const uint8_t stop_cmd[] = {RADIOLIB_SX126X_CMD_WRITE_REGISTER, 0x09, 0x02};
  const uint8_t stop = 0;
  int16_t error = module->SPIwriteStream(
      stop_cmd, sizeof(stop_cmd), &stop, 1, true, false);
  if (error != RADIOLIB_ERR_NONE) return {error, Step::StopRtc};

  const uint8_t read_cmd[] = {RADIOLIB_SX126X_CMD_READ_REGISTER, 0x09, 0x44};
  uint8_t event = 0;
  error = module->SPIreadStream(read_cmd, sizeof(read_cmd), &event, 1, true, false);
  if (error != RADIOLIB_ERR_NONE) return {error, Step::ReadRtcEvent};

  event |= 0x02;
  const uint8_t clear_cmd[] = {RADIOLIB_SX126X_CMD_WRITE_REGISTER, 0x09, 0x44};
  error = module->SPIwriteStream(clear_cmd, sizeof(clear_cmd), &event, 1, true, false);
  return {error, error == RADIOLIB_ERR_NONE ? Step::None : Step::ClearRtcEvent};
}

template <typename ModuleType>
inline Result armDutyCycle(ModuleType* module, const Periods& periods) {
  if (periods.rxTicks == 0 || periods.rxTicks > 0xFFFFFFUL) {
    return {RADIOLIB_ERR_INVALID_RX_PERIOD, Step::ValidateRx};
  }
  if (periods.sleepTicks == 0 || periods.sleepTicks > 0xFFFFFFUL) {
    return {RADIOLIB_ERR_INVALID_SLEEP_PERIOD, Step::ValidateSleep};
  }
  // Caller first runs guarded ordinary RadioLib RX staging. This sends only
  // opcode 0x94, never Sleep, calibration, gain, retention or packet writes.
  const uint8_t data[] = {
      uint8_t(periods.rxTicks >> 16), uint8_t(periods.rxTicks >> 8), uint8_t(periods.rxTicks),
      uint8_t(periods.sleepTicks >> 16), uint8_t(periods.sleepTicks >> 8), uint8_t(periods.sleepTicks),
  };
  const int16_t error = module->SPIwriteStream(
      RADIOLIB_SX126X_CMD_SET_RX_DUTY_CYCLE, data, sizeof(data), true, false);
  return {error, error == RADIOLIB_ERR_NONE ? Step::None : Step::ArmDutyCycle};
}

}  // namespace rxpspulse
}  // namespace mesh
