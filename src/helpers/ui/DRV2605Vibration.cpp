#ifdef HAS_DRV2605

#include "DRV2605Vibration.h"
#include <Wire.h>

namespace {
// TI DRV2605L datasheet 8.4.1.4: software standby retains configuration and
// I2C access. Idle INTTRIG/RTP mode alone does not enter this low-power state.
constexpr uint8_t Standby = 0x40;
constexpr uint32_t EffectPollMillis = 20;
}

void DRV2605Vibration::begin() {
  _ready = false;
  _effect_active = _rtp_active = false;
  _last_trigger = 0;
#ifdef PIN_DRV_EN
  pinMode(PIN_DRV_EN, OUTPUT);
  digitalWrite(PIN_DRV_EN, HIGH);   // power up the haptic driver
  delay(10);
#endif
  if (!drv.begin(&Wire)) {
#ifdef PIN_DRV_EN
    digitalWrite(PIN_DRV_EN, LOW);
#endif
    return;   // no haptic driver found, stay silent
  }
#ifdef DRV2605_USE_LRA
  // LRA mode: 4x brake factor, medium loop gain, back-EMF gain 2
  drv.writeRegister8(DRV2605_REG_FEEDBACK, 0xB6);
#endif
  drv.selectLibrary(1);
  drv.setMode(Standby);
  _ready = true;
}

void DRV2605Vibration::trigger(bool force) {
  if (!_ready || _quiet) return;
  unsigned long now = millis();
  if (!force && _last_trigger != 0 && now - _last_trigger < VIBRATION_TIMEOUT) return;
  _last_trigger = now;
  drv.setMode(DRV2605_MODE_INTTRIG);
  drv.setWaveform(0, DRV2605_EFFECT);
  drv.setWaveform(1, 0);   // pause
  drv.setWaveform(2, DRV2605_EFFECT);
  drv.setWaveform(3, 0);   // end of sequence
  drv.go();
  _effect_active = true;
  _rtp_active = false;
  _last_effect_check = now;
}

void DRV2605Vibration::loop() {
  if (!_ready || !_effect_active) return;
  const uint32_t now = millis();
  if (now - _last_effect_check < EffectPollMillis) return;
  _last_effect_check = now;
  // GO self-clears only after the complete waveform sequence. Do not infer
  // completion from a fixed duration or poll GO in RTP mode (datasheet 8.4.3).
  if ((drv.readRegister8(DRV2605_REG_GO) & 1) == 0) {
    drv.setMode(Standby);
    _effect_active = false;
  }
}

bool DRV2605Vibration::isVibrating() {
  return _effect_active || _rtp_active;
}

void DRV2605Vibration::stop() {
  if (!_ready) return;
  drv.stop();
  drv.setRealtimeValue(0);
  drv.setMode(Standby);
  _effect_active = _rtp_active = false;
}

void DRV2605Vibration::pulse(bool on) {
  if (!_ready) return;
  if (!on) {
    stop();
    return;
  }
  drv.setMode(DRV2605_MODE_REALTIME);
  drv.setRealtimeValue(100);
  _effect_active = false;
  _rtp_active = true;
}

#endif // ifdef HAS_DRV2605
