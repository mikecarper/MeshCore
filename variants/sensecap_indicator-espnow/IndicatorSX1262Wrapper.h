#pragma once

#include <helpers/radiolib/CustomSX1262Wrapper.h>
#include "IndicatorRadioHal.h"

class IndicatorSX1262Wrapper : public CustomSX1262Wrapper {
public:
  IndicatorSX1262Wrapper(CustomSX1262& radio, mesh::MainBoard& board,
                         IndicatorRadioHal& hal)
      : CustomSX1262Wrapper(radio, board), _hal(hal) {}

  void loop() override {
    _hal.serviceInterrupt();
    CustomSX1262Wrapper::loop();
  }

#if MESH_WARM_RX_REFRESH
protected:
  bool startAGCPulse() override {
    _pulseContext.generation=_profile_generation;
    _pulseContext.primary=_profiles.generation[0];
    _pulseContext.secondary=_profiles.generation[1];
    _pulseContext.preamble=_physical_preamble;
    _pulseContext.profile=_active_profile;
    _pulseContext.dual=_profiles.enabled();
    _pulseContext.freq=_cur_freq;_pulseContext.bw=_cur_bw;
    _pulseContext.sf=_cur_sf;_pulseContext.cr=_cur_cr;
    const bool started=CustomSX1262Wrapper::startAGCPulse();
    _pulseContext.valid=started && _rxps_pulse.pending;
    return started;
  }
  bool pulseContextMatches() const {
    return _pulseContext.valid && _params_valid && !_profile_refresh_required
        && !_profile_repair_required && _pulseContext.generation==_profile_generation
        && _pulseContext.primary==_profiles.generation[0]
        && _pulseContext.secondary==_profiles.generation[1]
        && _pulseContext.preamble==_physical_preamble && _pulseContext.profile==_active_profile
        && _pulseContext.dual==_profiles.enabled() && _pulseContext.freq==_cur_freq
        && _pulseContext.bw==_cur_bw && _pulseContext.sf==_cur_sf && _pulseContext.cr==_cur_cr;
  }
  uint32_t agcInputReadGeneration() const override {
    return _hal.cachedInputSnapshot().generation;
  }
  bool prepareStaleAGCIrqWake(uint32_t sampled_at,uint32_t& generation) override {
    const auto snapshot=_hal.cachedInputSnapshot();
    const uint32_t after=static_cast<uint32_t>(micros());
    // Only the actual BUSY read from this sample may supply the first low.
    // Its timestamp can follow sampled_at because that read itself uses I2C.
    // Bound that observation to the existing 2ms witness uncertainty budget.
    const bool fresh=snapshot.generation==generation+uint32_t(1)
        && after-snapshot.at<=after-sampled_at && after-sampled_at<=2000UL;
    if(!fresh || !snapshot.valid || (snapshot.inputs&0x0C) || !pulseContextMatches()) return false;
    validateAGCScanLease();
    if(_rxps_pulse.failed || _rxps_pulse.cancel) return false;
    generation=snapshot.generation;
    return true;
  }
  bool retireStaleAGCIrqWake(uint32_t generation) override {
    const auto snapshot=_hal.cachedInputSnapshot();
    // Existing standby/IRQ commands must have refreshed the same physical
    // input bank. Never retire an edge from an old, high or failed read.
    if(!snapshot.valid || snapshot.generation==generation || (snapshot.inputs&0x0C)
        || static_cast<uint32_t>(micros())-snapshot.at>2000UL
        || !pulseContextMatches() || _rxps_pulse.failed || _rxps_pulse.cancel) return false;
    validateAGCScanLease();
    return !_rxps_pulse.cancel && claimAGCSoftwareInterrupt();
  }
#endif

private:
#if MESH_WARM_RX_REFRESH
  struct {
    uint32_t generation=0,primary=0,secondary=0;
    float freq=0,bw=0;
    uint16_t preamble=0;
    uint8_t profile=0,sf=0,cr=0;
    bool dual=false,valid=false;
  } _pulseContext;
#endif
  IndicatorRadioHal& _hal;
};
