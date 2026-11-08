// Included inside CustomLR2021Wrapper. The chip helper retains configuration
// and PRAM; this transaction owns packet admission and checked RX restoration.
private:
  int16_t resetLegacyReceiverCalibration() {
    auto* radio=static_cast<CustomLR2021*>(_radio);
    // Explicit recovery can run before a valid host tuple exists. In that
    // case preserve the driver's checked current-frequency-only behavior.
    if(!_params_valid) return radio->resetReceiverCalibration();
    // Retained calibration must cover every frequency the scanner can visit.
    // Convert nominal profiles exactly once; the driver consumes physical MHz
    // and separately preserves/restores its current physical frequency cache.
    const bool secondary=_profiles.enabled();
    const float hardware_frequencies[2]={hardwareFrequencyMHz(_profiles.params(0).freq),
        secondary ? hardwareFrequencyMHz(_profiles.params(1).freq) : 0.0f};
    return radio->resetReceiverCalibration(hardware_frequencies,secondary ? 2 : 1);
  }
  void legacyCalibrationFailed(int16_t code) {
    recordApply(mesh::lr2021::ApplyStatus::Stage::Calibration, code);
    invalidateScanTuple();
    _dbm_applied=false; _rx_boosted_gain_applied=false;
    state=0; // RadioLibWrapper idle state; retry through the normal repair path.
    _profile_refresh_required=_profile_repair_required=true;
    _profile_retry_at=millis()+250UL;
  }
public:
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
  bool isCalibratingNoiseFloor() const override {
    // A queued host refresh cannot own RF while the PRAM scanner is active.
    // Keep it queued for the next host sampling window without blocking sleep
    // or every subsequent legacy reset in the meantime.
    return !_lab_scan_active && PulseBase::isCalibratingNoiseFloor();
  }
#endif
  bool supportsOldAGC() const override { return MESH_ENABLE_OLD_AGC != 0; }
  void doResetAGC() override {
    // Shared explicit recovery still uses its historical admission-only hook.
    // Periodic legacy maintenance below checks each result through RX restore.
    _apply_status.begin();
    invalidateScanTuple();
    const int16_t rc=resetLegacyReceiverCalibration();
    if(rc!=RADIOLIB_ERR_NONE) legacyCalibrationFailed(rc);
  }
#if MESH_ENABLE_OLD_AGC
  mesh::AGCMaintenanceResult periodicOldAGCMaintenance() override {
    using Result=mesh::AGCMaintenanceResult;
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
    AGCTimingDiagnostics::PeriodicRequest diagnostic_request(_agc_timing);
    _agc_timing.sequenceRequest();
#endif
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.pending) return Result::Deferred;
#endif
    if(!isInRecvMode() || !_params_valid || _active_profile>1
        || _profile_repair_required || _profile_retry_at || isCalibratingNoiseFloor()
        || _profile_generation!=_profiles.generation[_active_profile] || _cw_active) return Result::Deferred;
#if MESH_ENABLE_TX_BEACONS
    if(_beacon_owned) return Result::Deferred;
#endif
#if MESH_ENABLE_CARRIER_TOOLS || MESH_ENABLE_MORSE_RX
    if(_morse_owned
#if MESH_ENABLE_MORSE_RX
        || _morse_rx_owned
#endif
       ) return Result::Deferred;
#endif
#if MESH_ENABLE_RADIO_DIRECTION
    if(!mesh::radioDirection::radioReceiveAllowed()) return Result::Deferred;
#endif
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
    if(_lab_uncertain || _lab_sleeping) return Result::Deferred;
    const bool scanner_owned=_lab_scan_active;
    if(scanner_owned) {
      const auto paused=labPauseScanner(false,0
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
          ,"old"
#endif
          );
      if(paused==mesh::RadioParamApplyResult::BUSY) return Result::Deferred;
      if(paused!=mesh::RadioParamApplyResult::APPLIED) return Result::Failed;
      // Also restore after a late FIFO or a failed command. The ordinary safe
      // service loop waits for packet consumption/repair before rehooking.
      _lab_resume_pending=true; _lab_resume_at=millis();
    }
#endif
    // A cold PRAM start deliberately leaves the host tuple dirty. Its checked
    // pause republishes that tuple before maintenance; rejecting the flag
    // earlier would starve legacy maintenance for the scanner's whole lease.
    if(_profile_refresh_required) return Result::Deferred;
    _apply_status.begin();
    const uint8_t resume=beginReconfigure();
    if(resume==2) return Result::Deferred;
    if(resume>2) { legacyCalibrationFailed(RADIOLIB_ERR_UNKNOWN);return Result::Failed; }
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
    _agc_timing.admit(static_cast<uint32_t>(micros()));
#endif
    invalidateScanTuple();
    const uint32_t generation=_profile_generation;
    const uint8_t profile=_active_profile;
    int16_t rc=recordApply(mesh::lr2021::ApplyStatus::Stage::Calibration,
                           resetLegacyReceiverCalibration());
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
    // One aggregate native helper sample, not inferred per-command timing.
    _agc_timing.commandResult(AGCTimingDiagnostics::Step::Calibrate,rc);
    _agc_timing.helperFinished(static_cast<uint32_t>(micros()));
    const bool native_helper_failed=rc!=RADIOLIB_ERR_NONE;
#endif
    if(rc==RADIOLIB_ERR_NONE && !applyParams(_cur_freq,_cur_bw,_cur_sf,_cur_cr)) rc=RADIOLIB_ERR_UNKNOWN;
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
    if(rc==RADIOLIB_ERR_NONE && !applyProtocol(_physical_protocol)) rc=RADIOLIB_ERR_UNKNOWN;
#endif
    if(rc==RADIOLIB_ERR_NONE && _dbm_valid) {
      rc=recordApply(mesh::lr2021::ApplyStatus::Stage::Power,applyCachedTxPower(_cur_dbm));
      _dbm_applied=rc==RADIOLIB_ERR_NONE;
    }
    if(rc==RADIOLIB_ERR_NONE && _rx_boosted_gain_valid) {
      _rx_boosted_gain_applied=applyRxBoostedGainMode(_cur_rx_boosted_gain);
      if(!_rx_boosted_gain_applied) rc=RADIOLIB_ERR_UNKNOWN;
    }
    if(rc!=RADIOLIB_ERR_NONE) {
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
      // Restoration is an aggregate failure here; radio.apply retains its
      // precise stage. Do not count the native helper error a second time.
      if(!native_helper_failed) _agc_timing.commandResult(AGCTimingDiagnostics::Step::None,rc);
#endif
      legacyCalibrationFailed(rc);return Result::Failed;
    }
    // Reenter through the ordinary path: it reinstates side detectors,
    // multi-SF, RXPS and the checked IRQ/RF routing for the current profile.
    endReconfigure(resume);
    if((resume && !isInRecvMode()) || profile!=_active_profile
        || generation!=_profile_generation || generation!=_profiles.generation[profile]) {
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
      _agc_timing.commandResult(AGCTimingDiagnostics::Step::None,RADIOLIB_ERR_UNKNOWN);
#endif
      legacyCalibrationFailed(RADIOLIB_ERR_UNKNOWN);return Result::Failed;
    }
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
    if(scanner_owned) {
      // A completion racing RX reentry retains its FIFO; retry the scanner
      // separately rather than recalibrating or clearing the packet here.
      if(!labScannerStart(true) || !_lab_scan_active || !_lab_status.hooked) {
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
        labCaptureHandoffFailure(2,0,!_lab_uncertain && !_lab_scan_active);
#endif
        if(!_lab_uncertain) { _lab_resume_pending=true;_lab_resume_at=millis()+250UL; }
        recordApply(mesh::lr2021::ApplyStatus::Stage::Restore,RADIOLIB_ERR_UNKNOWN);
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
        _agc_timing.commandResult(AGCTimingDiagnostics::Step::None,RADIOLIB_ERR_UNKNOWN);
#endif
        return Result::Failed;
      }
    }
#endif
    const bool primary_valid=_noise_floor_valid, secondary_valid=_noise_floor_secondary_valid;
    recalibrateNoiseFloor();
    _noise_floor_valid=primary_valid; _noise_floor_secondary_valid=secondary_valid;
    _apply_status.applied();
    return Result::Applied;
  }
#endif
