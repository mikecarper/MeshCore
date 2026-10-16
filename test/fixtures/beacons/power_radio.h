#pragma once
#include <RadioProfiles.h>
#include <helpers/radiolib/RadioProfileChip.h>
#include <helpers/radiolib/RadioPowerLimits.h>
#include <algorithm>
#include <cassert>
#include <cstdint>

#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_INVALID_OUTPUT_POWER -1
#define RADIOLIB_MIN(a, b) std::min(int(a), int(b))
#define RADIOLIB_MAX(a, b) std::max(int(a), int(b))
#define RADIOLIB_CHECK_RANGE(value, low, high, error) \
  if ((value) < (low) || (value) > (high)) return (error)

extern unsigned power_hardware_calls;
inline void dacWrite(uint8_t, uint8_t) { ++power_hardware_calls; }
namespace mesh {
struct MainBoard {};
struct Radio {
  @RADIO_CAPABILITY@
};
}
struct PhysicalLayer {
  bool highFreq = false;
  float frequency = 909.5f;
  virtual int16_t checkOutputPower(int8_t, int8_t*) = 0;
  int16_t setOutputPower(int8_t, bool) { ++power_hardware_calls; return 0; }
};
struct SX1262 : PhysicalLayer {
  int16_t checkOutputPower(int8_t, int8_t*) override;
};
struct SX1276 : PhysicalLayer {
  int16_t checkOutputPower(int8_t, int8_t*) override;
  int16_t checkOutputPower(int8_t, int8_t*, bool);
};
struct SX1272 : PhysicalLayer {
  int16_t checkOutputPower(int8_t, int8_t*) override;
  int16_t checkOutputPower(int8_t, int8_t*, bool);
};
struct LR1110 : PhysicalLayer {
  int16_t checkOutputPower(int8_t, int8_t*) override;
  int16_t checkOutputPower(int8_t, int8_t*, bool);
};
struct LR1121 : PhysicalLayer {
  int16_t checkOutputPower(int8_t, int8_t*) override;
  int16_t checkOutputPower(int8_t, int8_t*, bool);
};
struct LR2021 : PhysicalLayer {
  int16_t checkOutputPower(int8_t, int8_t*) override;
};
struct Module {
  bool hp = true, lp = true;
  bool findRfSwitchMode(int mode) const { return mode == 1 ? hp : lp; }
};
struct STM32WLx : PhysicalLayer {
  static constexpr int MODE_TX_HP = 1, MODE_TX_LP = 2;
  Module module;
  const Module* getMod() const { return &module; }
  int16_t checkOutputPower(int8_t, int8_t*) override;
};
@DRIVER_CHECKS@

class RadioLibWrapper : public mesh::Radio {
protected:
  PhysicalLayer* _radio;
  virtual bool checkBeaconOutputPower(float, int8_t) const;
public:
  RadioLibWrapper(PhysicalLayer& radio, mesh::MainBoard&) : _radio(&radio) {}
  virtual void loop() { ++power_hardware_calls; }
  virtual int16_t applyCachedTxPower(int8_t) { ++power_hardware_calls; return 0; }
  bool setTxPower(int8_t power) { return applyCachedTxPower(power) == 0; }
  bool validateBeaconPower(float, int8_t) const override;
  bool validateProfile(const mesh::RadioProfileParams&) const;
  bool supportsMeshtext() const { return true; }
  bool supportsEthernetTunnel() const { return true; }
};
using CustomSX1276 = SX1276;
using CustomSX1262 = SX1262;
struct CustomSX1276Wrapper : RadioLibWrapper { using RadioLibWrapper::RadioLibWrapper; };
struct CustomSX1262Wrapper : RadioLibWrapper { using RadioLibWrapper::RadioLibWrapper; };
struct IndicatorRadioHal {
  unsigned services = 0;
  void serviceInterrupt() { ++services; ++power_hardware_calls; }
};
@INDICATOR_WRAPPER@
@DAC_LEVEL@
#define DAC_PA_DEFAULT_DRIVE_DBM 2
#define DAC_PA_TABLE_MAX INT8_MAX
@DAC_WRAPPER@
