#pragma once

#ifdef HAS_DRV2605

#include <Arduino.h>
#include <Adafruit_DRV2605.h>

/*
 * Vibration control class for boards where the motor is behind a
 * DRV2605 haptic driver on I2C (e.g. Seeed SenseCAP MeshTracker X1).
 * Same interface as GenericVibration.
 */

#ifndef VIBRATION_TIMEOUT
#define VIBRATION_TIMEOUT 5000 // cooldown between vibrations
#endif

#ifndef DRV2605_EFFECT
#define DRV2605_EFFECT 16   // "1000 ms alert" from the DRV2605 effect library
#endif

class DRV2605Vibration {
public:
  void begin();       // power up and init the DRV2605
  void trigger(bool force = false);   // trigger vibration; force skips the cooldown
  void loop();        // return to standby after autonomous effects complete
  bool isVibrating();
  void stop();        // stop vibration immediately
  void pulse(bool on); // continuous RTP amplitude for scheduled ms pulses
  void quiet(bool q) { _quiet = q; }
  bool isQuiet() const { return _quiet; }

private:
  Adafruit_DRV2605 drv;
  bool _ready = false;
  bool _quiet = false;
  bool _effect_active = false;
  bool _rtp_active = false;
  uint32_t _last_effect_check = 0;
  unsigned long _last_trigger = 0;
};

#endif // ifdef HAS_DRV2605
