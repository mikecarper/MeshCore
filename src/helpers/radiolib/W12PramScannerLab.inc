// Isolated lab only: custom MIT scanner occupies PRAM instead of Semtech patch.
// Guarded scanner handoff is experimental; uncertain ownership triggers reset.
// The chip stays on LDO for scanner and normal TX until explicit exit.
private:
  bool _lab_scan_active = false;
  bool _lab_pram_loaded = false; // invalidated by every radio hard reset
#if defined(MESH_LR2021_PRAM_MANUAL_START) && MESH_LR2021_PRAM_MANUAL_START
  mesh::w12pram::AutoPolicy _lab_auto{false};
#else
  mesh::w12pram::AutoPolicy _lab_auto;
#endif
  bool _lab_resume_pending = false;
  bool _lab_sleeping = false;
  bool _lab_session_ldo = false;
  bool _lab_uncertain = false; // unread/ambiguous IRQ preserved for controlled recovery
  uint32_t _lab_uncertain_at=0, _lab_ambiguous_recoveries=0, _lab_cold_starts=0;
  bool _lab_transition_waiting=false;
  uint32_t _lab_transition_at=0;
  bool _lab_bench_complete = false;
  uint32_t _lab_pause_last=0, _lab_pause_min=UINT32_MAX, _lab_pause_max=0;
  uint32_t _lab_resume_last=0, _lab_resume_min=UINT32_MAX, _lab_resume_max=0;
  struct LabBench { uint32_t n=0, min=UINT32_MAX, max=0; uint64_t sum=0;
    void add(uint32_t v) { ++n; sum+=v; if(v<min) min=v; if(v>max) max=v; }
    uint32_t mean() const { return n ? uint32_t(sum/n) : 0; }
  } _lab_bench_up, _lab_bench_down, _lab_bench_control;
  uint32_t _lab_resume_at = 0, _lab_tx_pauses = 0, _lab_tx_resumes = 0;
  // Only the configured logical frequency is needed after admission; the
  // immutable hardware tuple and generation are retained in _lab_snap.
  float _lab_logical_mhz[2] = {};
  mesh::w12pram::Profile _lab_snap[2] = {};
  mesh::w12pram::Profile _lab_last = {};
  float _lab_last_logical_mhz = 0;
  int _lab_last_profile = -1;
  int16_t _lab_error = 0;
  uint32_t _lab_resume_dropped = 0; // valid FIFO reads not delivered after resume failure
  int16_t _lab_last_recovery_error = 0;
  mesh::w12pram::RecoveryOrigin _lab_recovery_origin = mesh::w12pram::RecoveryOrigin::None;
  uint8_t _lab_recovery_timer = 0; // last cached observation before recovery
  uint32_t _lab_last_poll = 0, _lab_progress_at = 0, _lab_prev_cads = 0;
  uint32_t _lab_good = 0, _lab_bad = 0, _lab_recoveries = 0;
  uint32_t _lab_good_profile[2] = {};
#if defined(MESH_W12_RXPS_EXPERIMENT) && MESH_W12_RXPS_EXPERIMENT
  uint32_t _lab_busy_high=0, _lab_busy_low=0, _lab_busy_edges=0;
  bool _lab_busy_seen=false, _lab_busy_previous=false;
#endif
  uint8_t _lab_rx_preamble[2] = {}; // 0 uses normal profile auto/fixed choice
  mesh::w12pram::Status _lab_status = {};

  int16_t labScannerCommand(uint8_t op, const uint8_t* extra, size_t n, mesh::w12pram::Status& out) {
    if (n > 4) return RADIOLIB_ERR_UNKNOWN;
    uint8_t req[6] = {4, op, 0, 0, 0, 0};
    for (size_t i=0;i<n;i++) req[2+i]=extra[i];
    uint8_t response[40] = {};
    int16_t rc=static_cast<CustomLR2021*>(_radio)->labSpiCommand(0x012D, false, response, sizeof(response), req, n+2);
    if (rc == RADIOLIB_ERR_NONE) out=mesh::w12pram::decode(response);
    return rc;
  }
  bool labReinitialize(bool resume_rx=true) {
    _lab_scan_active=false;
    static_cast<CustomLR2021*>(_radio)->labSetPramScan(false);
    const auto& p=_profiles.params(0);
    cacheParams(p.freq,p.bw,p.sf,p.cr);
    _active_profile=0;
    _profile_generation=_profiles.generation[0];
    _physical_preamble=profilePreamble(0);
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
    _physical_protocol=p.protocol;
#endif
    _profile_refresh_required=true;
    bool ok=restoreAfterDeepInit();
    if (!ok) { _profile_repair_required=true; return false; }
    _lab_session_ldo=false;
    _profile_refresh_required=true; // regular scanner will republish its state
    if (!resume_rx) return true;
#if MESH_ENABLE_RADIO_DIRECTION
    if (!mesh::radioDirection::radioReceiveAllowed()) return true;
#endif
    startRecv();
    return isInRecvMode();
  }
  void labMarkUncertain() {
    if (!_lab_uncertain) _lab_uncertain_at=millis();
    _lab_uncertain=true; _lab_resume_pending=false;
    _profile_repair_required=true; _lab_error=RADIOLIB_ERR_UNKNOWN;
  }
  // SCN3 stopped its handler. Checked standby ends any active CAD; rebuild
  // the complete host tuple and RX IRQ mapping without overwriting PRAM.
  bool labWarmRestore(uint8_t target=0) {
    if (target>1 || (target==1 && !_profiles.enabled())) return false;
    auto* r=static_cast<CustomLR2021*>(_radio);
    if (r->standby()!=RADIOLIB_ERR_NONE) return false;
    if (hasPendingRadioInterrupt() || isPacketPendingOrReceiving() || hasPendingRadioInterrupt()) {
      // Do not reset/clear an IRQ or FIFO that raced the handoff. Ownership
      // is ambiguous after unhooking; stop all automatic actions for review.
      labMarkUncertain(); _lab_scan_active=false; state=0;
      return false;
    }
    _lab_scan_active=false; r->labSetPramScan(false);
    const auto& p=_profiles.params(target);
    _physical_preamble=profilePreamble(target);
    invalidateScanTuple();
    if (!applyParams(p.freq,p.bw,p.sf,p.cr)) return false;
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
    if (!applyProtocol(p.protocol)) return false;
    _physical_protocol=p.protocol;
#endif
    cacheParams(p.freq,p.bw,p.sf,p.cr);
    _active_profile=target; _profile_generation=_profiles.generation[target];
    _profile_refresh_required=false; _profile_repair_required=false;
    state=0;
#if MESH_ENABLE_RADIO_DIRECTION
    if (!mesh::radioDirection::radioReceiveAllowed()) return true;
#endif
    startRecv(); return isInRecvMode();
  }
  void labRecover(int16_t err, mesh::w12pram::RecoveryOrigin origin=mesh::w12pram::RecoveryOrigin::Other) {
    _lab_error=err; _lab_last_recovery_error=err; _lab_recovery_origin=origin;
    _lab_recovery_timer=mesh::w12pram::timeoutObservation(_lab_status);
    ++_lab_recoveries; _lab_resume_pending=false;
    _lab_auto.observe(_profiles.generation[0],_profiles.generation[1]);
    _lab_auto.block(mesh::w12pram::AutoPolicy::Reason::Failed);
    // A chip reset tears down even a stuck PRAM hook and all custom code.
    if (!labReinitialize()) _profile_repair_required=true;
  }

public:
  bool getW12PramRxSnapshot(mesh::W12PramRxSnapshot& out) const override {
    out={};
    if (!_lab_scan_active || _lab_last_profile<0) return false;
    out.logical_freq_hz=uint32_t(_lab_last_logical_mhz*1000000.0f);
    out.actual_freq_hz=_lab_last.freq_hz; out.bw_hz=_lab_last.bw_hz;
    out.generation=_lab_last.generation; out.preamble=_lab_last.preamble;
    out.profile=uint8_t(_lab_last_profile); out.sf=_lab_last.sf; out.cr=_lab_last.cr; out.valid=true;
    return true;
  }
  uint32_t getEstAirtimeFor(int len) override {
    // A profile can be disabled before scanner ownership is released. Pass
    // the validated snapshot's coding rate so the single-profile fallback
    // in getProfileAirtime does not call this virtual method recursively.
    if (_lab_scan_active && _lab_last_profile>=0)
      return getProfileAirtime(uint8_t(_lab_last_profile),len,_lab_last.cr);
    // LR2021::getTimeOnAir reads the packet type over SPI. Once RXPS is
    // armed that NSS pulse can wake/stop the autonomous duty-cycle loop.
    // The active profile is LoRa, so use the cached tuple instead. Supplying
    // the coding rate also avoids getProfileAirtime's single-radio fallback.
    if (_rx_ps_armed && _params_valid && _active_profile < 2 && _cur_cr >= 5 && _cur_cr <= 8)
      return getProfileAirtime(_active_profile,len,_cur_cr);
    return RadioLibWrapper::getEstAirtimeFor(len);
  }
  float packetScore(float snr, int len) override {
    if (_lab_scan_active && _lab_last_profile>=0) return packetScoreInt(snr,_lab_last.sf,len);
    return RadioLibWrapper::packetScore(snr,len);
  }
  bool labScannerUncertain() const { return _lab_uncertain; }
  bool labScannerActive() const { return _lab_scan_active; }
  bool hasAutonomousProfileIdle() const override {
#if defined(NRF52_PLATFORM)
    // No nRF wake/IRQ ownership has been qualified for this scanner.
    return false;
#else
    return _lab_scan_active && _lab_status.hooked;
#endif
  }
  // A deferred attempt is not a successful reset. Retry on the health-poll
  // cadence; once scanner ownership ends the same deadline remains due.
  uint32_t labAGCMaintenanceRetryMs() const override {
    return (_lab_scan_active || _lab_uncertain) ? 1000U : 0U;
  }
#if defined(MESH_W12_RXPS_EXPERIMENT) && MESH_W12_RXPS_EXPERIMENT
  void labRxpsBusyStatus(char* out, size_t capacity) const {
    const auto& trace=mesh::w12pram::spiTrace();
    snprintf(out,capacity,"RXPS busy=%lu/%lu edges=%lu armed=%u spi=%lu first=%04X,%04X,%04X,%04X modes=%u,%u,%u,%u,%u,%u bad=%u",
        (unsigned long)_lab_busy_high,(unsigned long)_lab_busy_low,
        (unsigned long)_lab_busy_edges,unsigned(_rx_ps_armed), (unsigned long)trace.count,
        trace.first[0],trace.first[1],trace.first[2],trace.first[3],
        unsigned(trace.modes[0]),unsigned(trace.modes[1]),unsigned(trace.modes[2]),
        unsigned(trace.modes[3]),unsigned(trace.modes[4]),unsigned(trace.modes[5]),
        unsigned(trace.invalid_status));
  }
#endif
  bool getProfileIdleDelay(uint32_t& delay_ms) const override {
    if (!_lab_scan_active) return RadioLibWrapper::getProfileIdleDelay(delay_ms);
#if defined(NRF52_PLATFORM)
    // Poll through the normal host loop: no radio-owned idle sleep on nRF yet.
    delay_ms = 0;
    return false;
#else
    // The LR2021 owns both visits. The ESP32 may sleep without stopping CAD;
    // the board's level-triggered GPIO check protects an arriving RxDone.
    // Wake at least once per second to service the existing progress watchdog.
    const bool blocked = _lab_uncertain || _lab_sleeping || _lab_resume_pending
        || _profile_repair_required || _cw_active || !isInRecvMode()
        || !_profiles.enabled() || hasPendingRadioInterrupt()
        || digitalRead(P_LORA_DIO_1) == HIGH;
    const bool generations_match = _profiles.generation[0] == _lab_snap[0].generation
        && _profiles.generation[1] == _lab_snap[1].generation;
    delay_ms = mesh::w12pram::idleBudget(_lab_scan_active, _lab_status.hooked,
        blocked, generations_match, uint32_t(millis()), _lab_last_poll,
        mesh::esp32::autonomousProfileSleepSliceMillis());
    return delay_ms != 0;
#endif
  }
  void labScannerStatus(char* out, size_t cap) const {
    const auto reason=_lab_uncertain ? mesh::w12pram::AutoPolicy::Reason::Uncertain : _lab_auto.reason();
    snprintf(out,cap,"PRAM %s auto=%s fallback=%s uncertain=%u hook=%u state=%u ch=%u hit=%u cad=%lu good=%lu bad=%lu lost=%lu err=%d rec=%lu tx=%lu/%lu resume=%u",
      _lab_scan_active?"on":"off",_lab_auto.enabled()?"on":"off",mesh::w12pram::autoReasonName(reason),_lab_uncertain,_lab_status.hooked,_lab_status.state,_lab_status.current,_lab_status.last_hit,
      (unsigned long)_lab_status.cads,(unsigned long)_lab_good,(unsigned long)_lab_bad,
      (unsigned long)_lab_status.lost,(int)_lab_error,(unsigned long)_lab_recoveries,
      (unsigned long)_lab_tx_pauses,(unsigned long)_lab_tx_resumes,_lab_resume_pending);
  }
  void labScannerDiag(char* out, size_t cap) const {
    snprintf(out,cap,"PRAM diag cached irq=%08lX lost_irq=%08lX gate=%08lX events=%lu good=%lu/%lu rxpre=%u/%u (0=profile)",
      (unsigned long)_lab_status.irq,(unsigned long)_lab_status.lost_irq,(unsigned long)_lab_status.irq_gate,
      (unsigned long)_lab_status.events,(unsigned long)_lab_good_profile[0],(unsigned long)_lab_good_profile[1],
      _lab_rx_preamble[0],_lab_rx_preamble[1]);
  }
  void labScannerRecoveryDiag(char* out, size_t cap) const {
    snprintf(out,cap,"PRAM recovery origin=%s err=%d count=%lu ambiguous=%lu lost_read=%lu cold=%lu timer=%s cached=%s",
      mesh::w12pram::recoveryOriginName(_lab_recovery_origin),
      int(_lab_last_recovery_error),(unsigned long)_lab_recoveries,(unsigned long)_lab_ambiguous_recoveries,(unsigned long)_lab_resume_dropped,(unsigned long)_lab_cold_starts,
      mesh::w12pram::timeoutObservationName(_lab_recovery_timer),
      mesh::w12pram::timeoutObservationName(mesh::w12pram::timeoutObservation(_lab_status)));
  }
  bool labScannerSetRxPreamble(unsigned profile, unsigned preamble) {
    if (_lab_scan_active || _lab_resume_pending || _lab_uncertain || profile<1 || profile>2 || (preamble && (preamble<8 || preamble>255))) return false;
    _lab_rx_preamble[profile-1]=uint8_t(preamble); return true;
  }
  void labScannerTiming(char* out, size_t cap) const {
    snprintf(out,cap,"PRAM handoff us: pause=%lu/%lu/%lu resume=%lu/%lu/%lu n=%lu/%lu",
      (unsigned long)_lab_pause_last,(unsigned long)(_lab_pause_min==UINT32_MAX?0:_lab_pause_min),(unsigned long)_lab_pause_max,
      (unsigned long)_lab_resume_last,(unsigned long)(_lab_resume_min==UINT32_MAX?0:_lab_resume_min),(unsigned long)_lab_resume_max,
      (unsigned long)_lab_tx_pauses,(unsigned long)_lab_tx_resumes);
  }
  void labScannerBenchStatus(char* out, size_t cap) const {
    if (!_lab_bench_complete) { snprintf(out,cap,"PRAM bench: no completed run"); return; }
    snprintf(out,cap,"PRAM cfg SPI us 1>2=%lu/%lu/%lu 2>1=%lu/%lu/%lu noop=%lu/%lu/%lu n=%lu; excludes CAD dwell/RX lock",
      (unsigned long)_lab_bench_up.min,(unsigned long)_lab_bench_up.mean(),(unsigned long)_lab_bench_up.max,
      (unsigned long)_lab_bench_down.min,(unsigned long)_lab_bench_down.mean(),(unsigned long)_lab_bench_down.max,
      (unsigned long)_lab_bench_control.min,(unsigned long)_lab_bench_control.mean(),(unsigned long)_lab_bench_control.max,
      (unsigned long)_lab_bench_up.n);
  }
  void labScannerLast(char* out, size_t cap) const {
    if (_lab_last_profile < 0) { snprintf(out,cap,"PRAM last: none"); return; }
    snprintf(out,cap,"PRAM last: radio=%d freq=%.6f MHz actual=%lu Hz bw=%lu Hz sf=%u cr=%u pre=%u gen=%lu",
      _lab_last_profile+1,(double)_lab_last_logical_mhz,(unsigned long)_lab_last.freq_hz,
      (unsigned long)_lab_last.bw_hz,_lab_last.sf,_lab_last.cr,_lab_last.preamble,(unsigned long)_lab_last.generation);
  }
  mesh::RadioParamApplyResult labPauseScanner(bool for_tx=false, uint8_t target=0) {
    using Result=mesh::RadioParamApplyResult;
    if (!_lab_scan_active) return Result::APPLIED;
    const uint32_t started=micros();
    if (hasPendingRadioInterrupt() || isChipBusy()) return Result::BUSY;
    mesh::w12pram::Status reply={};
    const int16_t rc=labScannerCommand(6,nullptr,0,reply);
    // A failed SPI transaction may still have stopped the chip; preserve the
    // ambiguous state until bounded recovery rather than resetting it here.
    if (rc!=RADIOLIB_ERR_NONE) { labMarkUncertain(); return Result::FAILED; }
    const auto decision=mesh::w12pram::pauseResult(reply);
    if (decision==mesh::w12pram::PauseResult::Busy) return Result::BUSY;
    if (decision!=mesh::w12pram::PauseResult::Stopped) { labMarkUncertain(); return Result::FAILED; }
    // The guarded stop ends ROM ownership, but CAD hardware may still run.
    // Checked standby and full tuple restore precede channel check and TX.
    if (!labWarmRestore(target)) {
      if (_lab_uncertain) return Result::BUSY; // preserve ambiguous FIFO, no reset
      labMarkUncertain(); return Result::FAILED;
    }
    if (for_tx) {
      _lab_pause_last=uint32_t(micros()-started);
      if (_lab_pause_last<_lab_pause_min) _lab_pause_min=_lab_pause_last;
      if (_lab_pause_last>_lab_pause_max) _lab_pause_max=_lab_pause_last;
    }
    return Result::APPLIED;
  }
  bool labQuiesceForReset() {
    if (beginReconfigure()>1) return false;
    if (hasPendingRadioInterrupt() || digitalRead(P_LORA_DIO_1)==HIGH) {
      labMarkUncertain(); return false;
    }
    return true;
  }
  bool labScannerStop() {
    _lab_resume_pending=false; // manual off remains off even during TX pause
    if (_lab_uncertain) return false;
    if (state==3 || isChipBusy() || hasPendingRadioInterrupt()) return false;
    if (!_lab_scan_active && !_lab_session_ldo) return true;
    const auto rc=labPauseScanner();
    if (rc!=mesh::RadioParamApplyResult::APPLIED) return false;
    // The warm handoff has resumed RX. Quiesce it again before the reset;
    // a packet arriving during that window must not be silently erased.
    if (!labQuiesceForReset()) return false;
    // Explicit exit restores the configured regulator and tears down PRAM.
    return labReinitialize();
  }
  bool labScannerProfilesSupported() {
    if (!_profiles.enabled()) return false;
    mesh::w12pram::Profile scan[2] = {};
    for (uint8_t i=0;i<2;i++) {
      const auto& p=_profiles.params(i);
      const uint16_t pre=_lab_rx_preamble[i] ? _lab_rx_preamble[i] : profilePreamble(i);
      if (p.protocol != mesh::RadioProfileProtocol::MeshCore || !validateProfile(p) || pre==0 || pre>255) return false;
      scan[i]={uint32_t(hardwareFrequencyMHz(p.freq)*1000000.0f),uint32_t(p.bw*1000.0f),p.sf,p.cr,uint8_t(pre),_profiles.generation[i]};
      mesh::w12pram::Packed packed;
      if (!mesh::w12pram::pack(scan[i],packed)) return false;
    }
    return labs((long)scan[0].freq_hz-(long)scan[1].freq_hz) < 20000000L;
  }
  // The setting is intentionally RAM-only: explicit off holds for this boot.
  // Initial activation is queued so the radio can finish pending work safely.
  bool labScannerSetAutomatic(bool enabled) {
    if (!enabled) {
      if (!labScannerStop()) return false;
      _lab_auto.setEnabled(false);
      return true;
    }
    if (_lab_uncertain || mesh::lr2021::safeMode()) return false;
    _lab_auto.setEnabled(true);
    return true;
  }
  bool labScannerStart(bool automatic=false) {
    const uint32_t started=micros();
    if (mesh::lr2021::safeMode()) return false;
#if MESH_ENABLE_RADIO_DIRECTION
    if (!mesh::radioDirection::radioReceiveAllowed()) return false;
#endif
    if (_lab_uncertain || _lab_sleeping || _lab_scan_active || !_profiles.enabled() || !_params_valid || _rx_ps_enabled || _cw_active
        || state == 3 || isChipBusy() || hasPendingRadioInterrupt() || isPacketPendingOrReceiving()) return false;
#if MESH_ENABLE_TX_BEACONS
    if (isBeaconRadioOwned()) return false;
#endif
    mesh::w12pram::Packed packed[2] = {};
    for (uint8_t i=0;i<2;i++) {
      const auto& p=_profiles.params(i);
      const uint16_t pre=_lab_rx_preamble[i] ? _lab_rx_preamble[i] : profilePreamble(i);
      if (p.protocol != mesh::RadioProfileProtocol::MeshCore || !validateProfile(p) || pre==0 || pre>255) return false;
      _lab_logical_mhz[i]=p.freq;
      _lab_snap[i]={uint32_t(hardwareFrequencyMHz(p.freq)*1000000.0f),uint32_t(p.bw*1000.0f),p.sf,p.cr,uint8_t(pre),_profiles.generation[i]};
      if (!mesh::w12pram::pack(_lab_snap[i],packed[i])) return false;
    }
    if (labs((long)_lab_snap[0].freq_hz-(long)_lab_snap[1].freq_hz) >= 20000000L) return false;
    auto* r=static_cast<CustomLR2021*>(_radio);
    int16_t rc=RADIOLIB_ERR_NONE;
    if (automatic) {
      // Includes pre- and post-standby RxDone checks; never reset a late FIFO.
      const uint8_t admitted=beginReconfigure();
      if (admitted>1) return false;
    } else {
      // Quiesce RX before the hard reset; a completion racing standby keeps
      // ownership of its FIFO. A late physical IRQ after admission is
      // ambiguous, so retain it for bounded recovery rather than clearing it.
      if (!labQuiesceForReset()) return false;
      ++_lab_cold_starts;
      // Stay in standby after reinitialization: briefly entering RX here
      // could admit a packet immediately before the PRAM load.
      if (!labReinitialize(false)) return false;
    }
    if (rc==RADIOLIB_ERR_NONE) {
      rc=r->setRegulatorLDO(); // scanner bypasses DCDC workaround
      if (rc==RADIOLIB_ERR_NONE) _lab_session_ldo=true;
    }
    uint8_t major=0,minor=0;
    if (rc==RADIOLIB_ERR_NONE) rc=r->getVersion(&major,&minor);
    if (rc==RADIOLIB_ERR_NONE && (major!=1 || minor!=0x18)) rc=RADIOLIB_ERR_UNKNOWN;
    if (!automatic) {
      const size_t words=sizeof(w12_pram_scan_blob)/sizeof(w12_pram_scan_blob[0]);
      for (size_t at=0;rc==RADIOLIB_ERR_NONE && at<words;) {
        size_t count=words-at<32 ? words-at : 32;
        rc=r->writeRegMem32(0x801000u+uint32_t(at*4),w12_pram_scan_blob+at,count);
        at+=count;
      }
      if (rc==RADIOLIB_ERR_NONE) rc=r->activatePram();
      if (rc==RADIOLIB_ERR_NONE) _lab_pram_loaded=true;
    }
    uint8_t req=2,ident[4]={};
    if (rc==RADIOLIB_ERR_NONE) rc=r->labSpiCommand(0x012D,false,ident,4,&req,1);
    if (rc==RADIOLIB_ERR_NONE && (ident[0]!='S'||ident[1]!='C'||ident[2]!='N'||ident[3]!='3')) rc=RADIOLIB_ERR_UNKNOWN;
    if (rc==RADIOLIB_ERR_NONE) rc=r->setRxTxFallbackMode(3); // FS
    uint8_t enable=1;
    if (rc==RADIOLIB_ERR_NONE) rc=r->labSpiCommand(0x0209,true,&enable,1);
    if (rc==RADIOLIB_ERR_NONE) rc=r->setDioIrqConfig(LR2021_IRQ_DIO,1u<<18); // RxDone only
    uint32_t table[4]={packed[0].frequency,packed[0].modem,packed[1].frequency,packed[1].modem};
    if (rc==RADIOLIB_ERR_NONE) rc=r->writeRegMem32(0x807800,table,4);
    const uint8_t args[]={2,1,8,0x10}; // 2 profiles; 1-symbol preamble-only CAD; 8-symbol wait
    if (rc==RADIOLIB_ERR_NONE) rc=labScannerCommand(1,args,sizeof(args),_lab_status);
    if (rc==RADIOLIB_ERR_NONE && !_lab_status.hooked) rc=RADIOLIB_ERR_UNKNOWN;
    if (rc!=RADIOLIB_ERR_NONE) { labMarkUncertain(); return false; }
    _lab_error=0;
    if (!automatic) {
      _lab_last_profile=-1; _lab_good=_lab_bad=0; _lab_good_profile[0]=_lab_good_profile[1]=0; _lab_tx_pauses=_lab_tx_resumes=0;
      _lab_pause_last=_lab_pause_max=_lab_resume_last=_lab_resume_max=0;
      _lab_pause_min=_lab_resume_min=UINT32_MAX;
    } else {
      ++_lab_tx_resumes; _lab_resume_last=uint32_t(micros()-started);
      if (_lab_resume_last<_lab_resume_min) _lab_resume_min=_lab_resume_last;
      if (_lab_resume_last>_lab_resume_max) _lab_resume_max=_lab_resume_last;
    }
    _lab_resume_pending=false;
    _lab_scan_active=true; _lab_auto.active(); r->labSetPramScan(true); _lab_last_poll=millis(); _lab_progress_at=millis(); _lab_prev_cads=_lab_status.cads;
    state=1; // lab scanner owns the radio and has armed CAD/RX
    return true;
  }
  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t p) override {
    return prepareTransmitProfile(p,false);
  }
  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t p, bool reply) override {
    using Result=mesh::RadioParamApplyResult;
    if (_lab_uncertain) return Result::BUSY;
    if (p>1 || !_profiles.canTransmit(p,reply)) return Result::FAILED;
    if (_lab_scan_active) {
      const Result paused=labPauseScanner(true,p);
      if (paused!=Result::APPLIED) return paused;
      ++_lab_tx_pauses;
      _lab_resume_pending=true;
    }
    // Dispatcher runs the configured channel check and start in this same
    // synchronous turn. Never restart the scanner in the middle of that turn.
    if (_lab_resume_pending) _lab_resume_at=millis()+600;
    return RadioLibWrapper::prepareTransmitProfile(p,reply);
  }
  mesh::RadioParamApplyResult tryRestoreCodingRate(uint8_t cr) override {
    if (_lab_scan_active || _lab_uncertain) return mesh::RadioParamApplyResult::BUSY;
    const auto rc=RadioLibWrapper::tryRestoreCodingRate(cr);
    if (_lab_resume_pending) _lab_resume_at=millis()+600;
    return rc;
  }
  bool labScannerBench() {
    // Explicit user request only; no CAD or RF transmit in this measurement.
    _lab_bench_complete=false;
    if (mesh::lr2021::safeMode() || !_lab_scan_active || !_profiles.enabled() || _profiles.generation[0]!=_lab_snap[0].generation
        || _profiles.generation[1]!=_lab_snap[1].generation || hasPendingRadioInterrupt() || isChipBusy()) return false;
    if (labPauseScanner()!=mesh::RadioParamApplyResult::APPLIED) return false;
    _lab_resume_pending=true; _lab_resume_at=millis()+600;
    if (beginReconfigure()>1) return false; // retain pending RxDone for normal receiver
    mesh::w12pram::Packed p[2]={};
    if (!mesh::w12pram::pack(_lab_snap[0],p[0]) || !mesh::w12pram::pack(_lab_snap[1],p[1])) { labRecover(RADIOLIB_ERR_UNKNOWN); return false; }
    auto* r=static_cast<CustomLR2021*>(_radio);
    auto call=[&](uint8_t action,const mesh::w12pram::Packed& profile,uint32_t& elapsed)->int16_t {
      uint8_t req[9]={}; mesh::w12pram::benchRequest(action,profile,req);
      uint8_t response=0; const uint32_t at=micros();
      const int16_t rc=r->labSpiCommand(0x012D,false,&response,1,req,sizeof(req));
      elapsed=uint32_t(micros()-at);
      return rc==RADIOLIB_ERR_NONE && response!=0x42 ? RADIOLIB_ERR_UNKNOWN : rc;
    };
    _lab_bench_up={}; _lab_bench_down={}; _lab_bench_control={};
    uint32_t elapsed=0; int16_t rc=call(1,p[0],elapsed); // establish primary
    for (unsigned i=0;rc==RADIOLIB_ERR_NONE && i<20;i++) {
      rc=call(0,p[0],elapsed); if (rc==RADIOLIB_ERR_NONE) _lab_bench_control.add(elapsed);
      if (rc==RADIOLIB_ERR_NONE) { rc=call(1,p[1],elapsed); if(rc==RADIOLIB_ERR_NONE) _lab_bench_up.add(elapsed); }
      if (rc==RADIOLIB_ERR_NONE) { rc=call(1,p[0],elapsed); if(rc==RADIOLIB_ERR_NONE) _lab_bench_down.add(elapsed); }
    }
    if (rc!=RADIOLIB_ERR_NONE) { labRecover(rc); return false; }
    if (!_profiles.enabled() || _profiles.generation[0]!=_lab_snap[0].generation || _profiles.generation[1]!=_lab_snap[1].generation) { labRecover(RADIOLIB_ERR_UNKNOWN); return false; }
    if (!labWarmRestore()) { if (!_lab_uncertain) labRecover(RADIOLIB_ERR_UNKNOWN); return false; }
    _lab_bench_complete=(_lab_bench_up.n==20 && _lab_bench_down.n==20 && _lab_bench_control.n==20);
    _lab_resume_at=millis()+50;
    return true;
  }
#if MESH_ENABLE_TX_BEACONS
  mesh::RadioParamApplyResult prepareBeaconTransmit(const mesh::RadioProfileParams& p, int8_t power) override {
    using Result=mesh::RadioParamApplyResult;
    if (_lab_uncertain) return Result::BUSY;
    if (_lab_scan_active) {
      const Result paused=labPauseScanner(true,0);
      if (paused!=Result::APPLIED) return paused;
      ++_lab_tx_pauses; _lab_resume_pending=true;
    }
    if (_lab_resume_pending) _lab_resume_at=millis()+600;
    return RadioLibWrapper::prepareBeaconTransmit(p,power);
  }
#endif
  bool labReconfigureBlocked() override {
    if (_lab_uncertain) return true; // retain ambiguous FIFO for explicit recovery
    if (!_lab_scan_active) return false;
    const auto paused=labPauseScanner();
    if (paused!=mesh::RadioParamApplyResult::APPLIED) return true;
    _lab_resume_pending=_lab_auto.enabled();
    _lab_resume_at=millis()+600;
    return false;
  }
  void idle() override { if (!_lab_scan_active && !_lab_uncertain) RadioLibWrapper::idle(); }
  void startRecv() override { if (!_lab_scan_active && !_lab_uncertain) RadioLibWrapper::startRecv(); }
  void resetAGC() override { if (!_lab_scan_active && !_lab_uncertain) RadioLibWrapper::resetAGC(); }
  bool tryResetAGC() override { return !_lab_scan_active && !_lab_uncertain && RadioLibWrapper::tryResetAGC(); }
  void triggerNoiseFloorCalibrate(int threshold) override {
    if (!_lab_scan_active && !_lab_uncertain) RadioLibWrapper::triggerNoiseFloorCalibrate(threshold);
  }
  bool labScannerRecover() {
    if (!_lab_uncertain) return false;
    _lab_uncertain=false; // explicit operator action discards ambiguous FIFO
    ++_lab_ambiguous_recoveries; ++n_recv_errors;
    labRecover(RADIOLIB_ERR_UNKNOWN, mesh::w12pram::RecoveryOrigin::Handoff);
    return !_profile_repair_required;
  }
  bool recoverRadio(bool hard) override {
    if (_lab_uncertain || _lab_scan_active) return false; // do not discard/replace scanner-owned state
    return RadioLibWrapper::recoverRadio(hard);
  }
  bool setTrackerSleep(bool sleeping) override {
    if (_lab_uncertain) return false;
    if (sleeping) {
      _lab_resume_pending=false;
      if (_lab_scan_active && labPauseScanner()!=mesh::RadioParamApplyResult::APPLIED) return false;
      _lab_sleeping=true;
    }
    const bool ok=RadioLibWrapper::setTrackerSleep(sleeping);
    if (!sleeping || !ok) _lab_sleeping=false;
    return ok;
  }
  void labServiceAutomatic(bool rx_allowed, bool radio_owned) {
    if (_lab_uncertain) return; // never discard an ambiguous FIFO
    if (mesh::lr2021::safeMode()) {
      if (_lab_scan_active || _lab_session_ldo || _lab_resume_pending) labScannerStop();
      return;
    }
    _lab_auto.observe(_profiles.generation[0],_profiles.generation[1]);
    if (!_profiles.enabled()) {
      // An off second profile must not leave the radio in LDO.
      if (_lab_session_ldo && !labScannerStop()) return;
      _lab_resume_pending=false;
      _lab_auto.clearUnsupported();
      return;
    }
    // The recommendation uses real host retunes for a 120-second window.
    // Starting PRAM earlier stops those retunes and leaves its status pending
    // forever. The provisional 512-symbol preamble isn't a final profile and
    // must not be latched as an unsupported scanner setting either.
    if (!_profiles.recommendationReady()) { _lab_auto.clearUnsupported(); return; }
    if (!labScannerProfilesSupported()) {
      if (_lab_session_ldo && !labScannerStop()) return;
      _lab_resume_pending=false;
      if (!_lab_auto.blocked()) _lab_auto.block(mesh::w12pram::AutoPolicy::Reason::Unsupported);
      return;
    }
    // Effective automatic preambles can change without a profile generation
    // change. Clear only this eligibility result; never clear a failed reset.
    _lab_auto.clearUnsupported();
    if (!_lab_auto.enabled() || _lab_auto.blocked()) return;
    if (mesh::w12pram::mayResume(_lab_resume_pending,_lab_sleeping,rx_allowed,state==3,radio_owned,
            _profile_repair_required,(int32_t)(millis()-_lab_resume_at)>=0)) {
      const uint32_t recoveries=_lab_recoveries;
      if (!labScannerStart(_lab_pram_loaded)) {
        if (_lab_recoveries!=recoveries || _profile_repair_required) {
          _lab_resume_pending=false;
          _lab_auto.block(mesh::w12pram::AutoPolicy::Reason::Failed);
        } else _lab_resume_at=millis()+1000;
      }
    } else if (!_lab_resume_pending && _lab_auto.due(millis()) && !_lab_sleeping
               && rx_allowed && state!=3 && !radio_owned && !_profile_repair_required) {
      // First activation needs a hard reset and PRAM load. A pending packet,
      // BUSY or carrier only delays it; a failed hardware transaction latches
      // host scanning for this generation pair rather than resetting forever.
      const uint32_t recoveries=_lab_recoveries;
      if (!labScannerStart(false)) {
        if (_lab_recoveries!=recoveries || _profile_repair_required)
          _lab_auto.block(mesh::w12pram::AutoPolicy::Reason::Failed);
        else _lab_auto.busy(millis());
      }
    }
  }
  void labServiceUncertain() {
    if (!_lab_uncertain) return;
    // The stopped PRAM hook may have raced an RF IRQ. Give the radio a
    // bounded settling period, but do not leave an unattended node frozen.
    // This reset explicitly discards an ambiguous FIFO; it does not claim a
    // confirmed lost packet. Block scanner restart until configuration changes.
    const uint32_t elapsed=uint32_t(millis()-_lab_uncertain_at);
    if (elapsed<1000 || (isChipBusy() && elapsed<5000)) return;
    _lab_uncertain=false;
    ++_lab_ambiguous_recoveries; ++n_recv_errors;
    labRecover(RADIOLIB_ERR_UNKNOWN, mesh::w12pram::RecoveryOrigin::Handoff);
  }
  void labStopForProfileTransition() {
    if (labScannerStop()) { _lab_transition_waiting=false; return; }
    if (_lab_uncertain) { _lab_transition_waiting=false; return; }
    // Ordinary BUSY, including pending RxDone, is not a failed handoff.
    // Let the dispatcher drain a still-current frame and retry the stop.
    // If a stale FIFO or stuck chip cannot drain, recover once after a bound.
    const uint32_t now=millis();
    if (!_lab_transition_waiting) { _lab_transition_waiting=true; _lab_transition_at=now; }
    else if (uint32_t(now-_lab_transition_at)>=5000) {
      _lab_transition_waiting=false; labMarkUncertain();
    }
  }
  bool labRxProfileCurrent() {
    if (_profiles.generation[0]!=_lab_snap[0].generation ||
        _profiles.generation[1]!=_lab_snap[1].generation || !_profiles.enabled()) {
      return false; // never claim or misattribute a stale FIFO
    }
    return true;
  }
  void loop() override {
    if (_lab_uncertain) { labServiceUncertain(); return; }
    if (!_lab_scan_active) {
#if defined(MESH_W12_RXPS_EXPERIMENT) && MESH_W12_RXPS_EXPERIMENT
      if (_rx_ps_armed) {
        // Sample the pin without SPI so observation cannot wake/cancel RXPS.
        const bool busy=digitalRead(P_LORA_BUSY) == HIGH;
        if (busy) ++_lab_busy_high; else ++_lab_busy_low;
        if (_lab_busy_seen && busy!=_lab_busy_previous) ++_lab_busy_edges;
        _lab_busy_seen=true; _lab_busy_previous=busy;
      }
#endif
      RadioLibWrapper::loop();
      bool rx_allowed=true;
      bool radio_owned=_cw_active;
#if MESH_ENABLE_TX_BEACONS
      radio_owned=radio_owned || isBeaconRadioOwned();
#endif
#if MESH_ENABLE_RADIO_DIRECTION
      rx_allowed=mesh::radioDirection::radioReceiveAllowed();
#endif
      labServiceAutomatic(rx_allowed, radio_owned);
      return;
    }
    bool must_stop = _profiles.generation[0]!=_lab_snap[0].generation
        || _profiles.generation[1]!=_lab_snap[1].generation || !_profiles.enabled();
#if MESH_ENABLE_RADIO_DIRECTION
    must_stop = must_stop || !mesh::radioDirection::radioReceiveAllowed();
#endif
    if (must_stop) {
      // A stale or disabled profile cannot be used to attribute a packet.
      // Stop only through the guarded handoff; a pending IRQ is retained for
      // bounded, counted recovery rather than silently reset here.
      labStopForProfileTransition();
      return;
    }
    _lab_transition_waiting=false;
    const uint32_t now=millis();
    if ((uint32_t)(now-_lab_last_poll)<1000 || isChipBusy()) return;
    _lab_last_poll=now;
    mesh::w12pram::Status s={}; int16_t rc=labScannerCommand(2,nullptr,0,s);
    if (rc!=RADIOLIB_ERR_NONE || !s.hooked) { labMarkUncertain(); return; }
    _lab_status=s;
    // Op2 is a read-only snapshot. A separate guarded op7 can repair a lost
    // header timer, without resetting a live timer or consuming pending RX.
    if (mesh::w12pram::mayRepairHeaderTimer(s, hasPendingRadioInterrupt(),
            digitalRead(P_LORA_DIO_1) == HIGH, isChipBusy())) {
      mesh::w12pram::Status repaired={};
      const int16_t repair_rc=labScannerCommand(7,nullptr,0,repaired);
      // A failed or ambiguous repair leaves the original snapshot intact;
      // the normal, IRQ-guarded watchdog remains the bounded fallback.
      if (repair_rc==RADIOLIB_ERR_NONE && repaired.hooked) {
        if (repaired.timer_repaired && repaired.state==3 && repaired.timeout_known && repaired.timeout_armed)
          _lab_progress_at=now; // bounded: at most two real repairs per CAD visit
        s=_lab_status=repaired;
      }
    }
    if (s.state==4 && (s.irq & (1u<<18))) _loop_event_pending=true; // recover missed ISR
    if (s.cads != _lab_prev_cads) { _lab_prev_cads=s.cads; _lab_progress_at=now; }
    else if ((uint32_t)(now-_lab_progress_at)>10000) {
      // RxDone can arrive as the watchdog expires. Keep its FIFO for
      // recvRaw(), and recheck the interrupt immediately before any reset.
      const bool pin_high = digitalRead(P_LORA_DIO_1) == HIGH;
      if (mesh::w12pram::mayRecoverStalledScan(s, hasPendingRadioInterrupt(), pin_high)
          && !hasPendingRadioInterrupt() && digitalRead(P_LORA_DIO_1) != HIGH) {
        labMarkUncertain(); // bounded recovery accounts for a late IRQ race
      }
    }
  }
  int recvRaw(uint8_t* bytes,int sz) override {
    if (_lab_uncertain) return 0;
    if (!_lab_scan_active) return RadioLibWrapper::recvRaw(bytes,sz);
    // Check attribution before claiming the IRQ; stale FIFO is ambiguous.
    if (!labRxProfileCurrent()) return 0;
    if (!claimRadioInterrupt()) return 0;
    auto* r=static_cast<CustomLR2021*>(_radio);
    mesh::w12pram::Status s={}; int16_t rc=labScannerCommand(2,nullptr,0,s);
    if (rc!=RADIOLIB_ERR_NONE) { labMarkUncertain(); return 0; }
    const auto event=mesh::w12pram::classify(s);
    if (event==mesh::w12pram::RxEvent::Ignore) return 0; // late ISR after resume
    if (event!=mesh::w12pram::RxEvent::Deliver) { labMarkUncertain(); return 0; }
    _lab_status=s; const uint8_t ch=s.last_hit;
    const int len=r->getPacketLength(); int delivered=0;
    if (len>0 && len<=sz) {
      _last_snr=r->getSNR(); _last_rssi=r->getRSSI();
      rc=r->readData(bytes,len); // validates FIFO size, header and CRC
      if (rc==RADIOLIB_ERR_NONE) {
        delivered=len; // publish attribution only after resume succeeds
      } else { ++_lab_bad; ++n_recv_errors; }
    } else { ++_lab_bad; ++n_recv_errors; (void)r->clearRxFifo(); }
    rc=labScannerCommand(3,nullptr,0,_lab_status); // parked until data has been consumed
    if (rc!=RADIOLIB_ERR_NONE || !_lab_status.hooked || _profiles.generation[0]!=_lab_snap[0].generation || _profiles.generation[1]!=_lab_snap[1].generation || !_profiles.enabled()) {
      // A validated frame cannot yet be delivered across the reset without
      // losing its immutable profile attribution. Count it explicitly.
      if (delivered) { ++_lab_resume_dropped; ++_lab_bad; ++n_recv_errors; }
      labRecover(rc ? rc : RADIOLIB_ERR_UNKNOWN, mesh::w12pram::RecoveryOrigin::RxResume);
      return 0; // restore changes the live profile; never mislabel this frame
    }
    _lab_prev_cads=_lab_status.cads; _lab_progress_at=millis();
    if (delivered) {
      _lab_last_profile=ch; _lab_last=_lab_snap[ch]; _lab_last_logical_mhz=_lab_logical_mhz[ch];
      _active_profile=ch; _profile_generation=_lab_snap[ch].generation; // Dispatcher tags Packet::radio_profile
      ++_lab_good; ++_lab_good_profile[ch]; ++n_recv; last_recv_millis=millis();
    }
    return delivered;
  }
private:
