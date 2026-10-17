struct RetryClock : mesh::MillisecondClock {
  unsigned long getMillis() override { return now_ms; }
};
struct RetryDispatcher : mesh::Dispatcher {
  int interval = 8000;
  bool adaptive = true;
  RetryDispatcher(RadioLibWrapper& r, RetryClock& c, StaticPoolPacketManager& m)
      : Dispatcher(r, c, m) {}
  mesh::DispatcherAction onRecvPacket(mesh::Packet*) override { return ACTION_RELEASE; }
  int getAGCResetInterval() const override { return interval; }
  bool useAdaptiveAGC() const override { return adaptive; }
  uint32_t getCADFailRetryDelay() const override { return 5000; }
  using Dispatcher::getNextQueueWakeDelay;
  using Dispatcher::hasQueuedWorkDue;
};

static uint32_t retryDelay(const RadioLibWrapper& w) {
  uint32_t delay = UINT32_MAX;
  assert(w.getReceiveRetryDelay(now_ms, delay));
  return delay;
}

static void noiseFloorDeadlines() {
  for (unsigned mode : {0U, 1U, 2U}) {
    RadioLibWrapper w; RetryClock clock; StaticPoolPacketManager manager{4};
    RetryDispatcher d(w, clock, manager); d.interval = 0;
    now_ms = 1000; d.begin(); selectMode(w, mode);
    d.loop(); d.loop();
    uint32_t wait = 0;
    assert(d.getNextNoiseFloorWakeDelay(wait) && wait == NF_CALIB_SETTLE_MS);
    // A subsecond sample is not a whole-second queue veto on timed sleepers.
    uint32_t queue = UINT32_MAX;
    assert(!d.getNextQueueWakeDelay(queue) && !d.hasQueuedWorkDue());
    const unsigned reads = w.reads, probes = w.mode_reads;
    const unsigned busy = w.busy_probes, packets = w.packet_probes;
    for (unsigned i = 0; i < 100; ++i)
      assert(d.getNextNoiseFloorWakeDelay(wait) && wait == NF_CALIB_SETTLE_MS);
    assert(w.reads == reads && w.mode_reads == probes && w.busy_probes == busy
           && w.packet_probes == packets);
    now_ms += wait; d.loop();
    assert(w.reads == 1 && d.getNextNoiseFloorWakeDelay(wait) && wait == 50);
    w.events.record();
    assert(d.getNextNoiseFloorWakeDelay(wait) && w.hasPendingRadioInterrupt());
    w.radio.packet_length = 1;
    d.loop();
    assert(w.n_recv == 1 && !w.hasPendingRadioInterrupt());
    assert(!d.hasQueuedWorkDue());
  }
  std::puts("PASS: actual Dispatcher noise-floor deadlines (3 modes)");
}

static void persistentFailure() {
  uint8_t bytes[8];
  for (unsigned mode : {0U, 1U, 2U}) {
    RadioLibWrapper w; RetryClock clock; StaticPoolPacketManager manager{4};
    RetryDispatcher d(w, clock, manager);
    now_ms = 1000; d.begin(); selectMode(w, mode); w.arm_result = -5;
    now_ms = 31000; // application setup may outlast the sampling timeout
    unsigned sleep_windows = 0;
    for (unsigned i = 0; i <= 60000; ++i, ++now_ms) {
      d.loop();
      assert(!w.isCalibratingNoiseFloor());
      uint32_t delay = 0;
      assert(d.getNextQueueWakeDelay(delay));
      sleep_windows += mesh::power::powerSaveSleepSeconds(delay) > 0;
      assert(w.arms <= 37); // initial ramp followed by at most one attempt/2s
    }
    assert(w.arms == 36 && sleep_windows > 20000);
    assert(w._nf_refresh_requested && w._nf_calib_deadline == 0 && w.reads == 0);
    assert(w.getRecoveryCount() >= 7);
    std::printf("60s actual Dispatcher mode=%u: arms=%u recoveries=%u sleep_windows=%u\n",
                mode, w.arms, unsigned(w.getRecoveryCount()), sleep_windows);
    // A real success clears the ramp and enters normal settled calibration.
    w.arm_result = 0; now_ms += retryDelay(w); w.recvRaw(bytes, sizeof(bytes));
    assert(w.state == STATE_RX && !w._rx_retry_pending && w._rx_retry_delay_ms == 0);
    if (mode == 1) { w.loop(); w.recvRaw(bytes, sizeof(bytes)); }
    assert(w.isCalibratingNoiseFloor());
    w.loop();
    assert(w._nf_calib_deadline == now_ms + NF_CONTINUOUS_TIMEOUT_MS);
    now_ms += NF_CALIB_SETTLE_MS; w.loop();
    assert(w._floor_estimator.count() == 1);
  }
}

static void rampAndRollover() {
  uint8_t bytes[8];
  for (uint32_t origin : {100U, UINT32_MAX - 19U}) {
    RadioLibWrapper w; w.state = STATE_IDLE; w.arm_result = -5; now_ms = origin;
    for (uint32_t expected : {20U, 40U, 80U, 160U, 320U, 640U, 1280U, 2000U, 2000U}) {
      const unsigned arms = w.arms;
      w.recvRaw(bytes, sizeof(bytes));
      assert(w.arms == arms + 1 && retryDelay(w) == expected);
      // Periodic/forced calibration, recovery and watchdog counter resets
      // cannot turn the existing failed-arm deadline into an immediate retry.
      w.triggerNoiseFloorCalibrate(7); w.recalibrateNoiseFloor(); w.resetAGC();
      w.recoverRadio(false); w._startrx_fails = 0;
      assert(retryDelay(w) == expected && w._rx_retry_delay_ms == expected);
      now_ms += expected - 1;
      w.recvRaw(bytes, sizeof(bytes)); assert(w.arms == arms + 1 && retryDelay(w) == 1);
      ++now_ms; assert(retryDelay(w) == 0);
    }
    w.begin(); assert(!w._rx_retry_pending && w._rx_retry_delay_ms == 0);
    w.recvRaw(bytes, sizeof(bytes)); assert(retryDelay(w) == 20);
  }
}

static void ownersAndImmediateRearms() {
  uint8_t bytes[8];
  for (bool busy : {false, true}) {
    RadioLibWrapper w; w.state = STATE_IDLE; w.arm_result = -5;
    w.recvRaw(bytes, sizeof(bytes)); now_ms += retryDelay(w);
    w.busy = busy; w.packet = !busy;
    const unsigned arms = w.arms;
    w.recvRaw(bytes, sizeof(bytes));
    assert(w.arms == arms && retryDelay(w) == 2000 && !w.hasPendingRadioInterrupt());
    w.busy = w.packet = false;
    // A completed packet bypasses the timer and is delivered with its owner.
    w.events.record(); w.radio.packet_length = 1; w.arm_result = 0;
    assert(w.recvRaw(bytes, sizeof(bytes)) == 1 && w.arms == arms + 1);
    assert(!w._rx_retry_pending && w._rx_retry_delay_ms == 0 && !w.hasPendingRadioInterrupt());
  }
  {
    RadioLibWrapper w; w.state = STATE_IDLE; w.arm_result = -5;
    w.recvRaw(bytes, sizeof(bytes));
    w.state = STATE_TX_WAIT; w.events.record();
    const unsigned arms = w.arms;
    assert(w.recvRaw(bytes, sizeof(bytes)) == 0 && w.arms == arms && w.hasPendingRadioInterrupt());
    uint32_t ignored = 0; assert(!w.getReceiveRetryDelay(now_ms, ignored));
    w.onSendFinished(); w.arm_result = 0;
    w.recvRaw(bytes, sizeof(bytes));
    assert(w.arms == arms + 1 && w.state == STATE_RX && !w.hasPendingRadioInterrupt());
  }
  {
    RadioLibWrapper w; w.state = STATE_IDLE; w.arm_result = -5;
    w.recvRaw(bytes, sizeof(bytes)); const unsigned arms = w.arms;
    // Direct rearm, as used by profile/CAD restoration, has no retry wait.
    w.arm_result = 0; w.startRecv();
    assert(w.arms == arms + 1 && w.state == STATE_RX && !w._rx_retry_pending);
  }
  {
    RadioLibWrapper w; w._rx_ps_armed = true; w.arm_result = -5;
    w.startRecv(); assert(w.state == STATE_IDLE && !w._rx_ps_armed);
  }
  {
    RadioLibWrapper w; w._nf_calib_active = true; w._rx_ps_armed = true;
    assert(!w.isCalibratingNoiseFloor());
    w.state = STATE_IDLE; w._rx_ps_armed = false; assert(!w.isCalibratingNoiseFloor());
    w.state = STATE_RX; assert(w.isCalibratingNoiseFloor());
    w._cw_active = true; uint32_t delay = 0; assert(!w.getReceiveRetryDelay(now_ms, delay));
  }
}

static void pendingSetupAndTx() {
  RadioLibWrapper w; w.begin(); now_ms += 100000;
  assert(!w.isCalibratingNoiseFloor() && w._nf_calib_deadline == 0);
  w.loop(); assert(w._nf_refresh_requested && w._nf_calib_deadline == 0);
  w.state = STATE_TX_WAIT; now_ms += 100000; w.loop();
  assert(!w.isCalibratingNoiseFloor() && w._nf_refresh_requested && w._nf_calib_deadline == 0);
  w.onSendFinished(); w.startRecv(); w.loop();
  assert(w._nf_calib_deadline == now_ms + NF_CONTINUOUS_TIMEOUT_MS);
}

static void retainedOutboundWake() {
  RadioLibWrapper w; RetryClock clock; StaticPoolPacketManager manager{4};
  RetryDispatcher d(w, clock, manager);
  now_ms = 1000; d.begin(); w.arm_result = -5;
  d.loop();
  auto* packet = d.obtainNewPacket(); assert(packet);
  packet->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
  packet->payload_len = 1; packet->payload[0] = 0x42;
  assert(d.sendPacket(packet, 0)); d.loop(); assert(w.sends == 1 && d.hasOutbound());
  uint32_t delay = 0;
  assert(d.getNextQueueWakeDelay(delay) && delay == 20);
  now_ms += 20; d.loop();
  assert(w.arms == 2 && d.getNextQueueWakeDelay(delay) && delay == 40);
}

static void busyAfterFailure() {
  RadioLibWrapper w; RetryClock clock; StaticPoolPacketManager manager{4};
  RetryDispatcher d(w, clock, manager);
  now_ms = 1000; d.begin(); w.arm_result = -5; d.loop(); w.busy = true;
  unsigned sleep_windows = 0;
  for (unsigned elapsed = 1; elapsed <= 30000; ++elapsed) {
    now_ms = 1000 + elapsed; d.loop();
    uint32_t delay = 0; assert(d.getNextQueueWakeDelay(delay));
    if (elapsed > 8000) sleep_windows += mesh::power::powerSaveSleepSeconds(delay) > 0;
    assert(w.arms == 1 && !w.isCalibratingNoiseFloor());
  }
  // A due AGC reset cannot admit SPI on BUSY. Its deferred deadline must join
  // the failed-RX deadline instead of vetoing every timed sleep at 50 ms.
  assert(sleep_windows > 8000);
  w.busy = false; w.arm_result = 0; now_ms += retryDelay(w); d.loop();
  assert(w.state == STATE_RX && !w._rx_retry_pending && w._rx_retry_delay_ms == 0);
  w._nf_refresh_requested = false; w._nf_calib_active = false;
  now_ms += 1; d.loop();
  uint32_t ignored = 0; assert(!w.getReceiveRetryDelay(now_ms, ignored));
  // The healthy non-RX-retry deferral contract remains 50 ms.
  w.packet = true; now_ms += 10000; d.loop();
  assert(d.getNextQueueWakeDelay(ignored) && ignored == 50);
  std::printf("30s BUSY after RX error: arms=%u later_sleep_windows=%u\n", w.arms, sleep_windows);
}

static void deferredCalibrationRestore() {
  enum Owner {
    Receiving, PacketIRQ, Tx, DisableAfter, FallbackAfter, FailedRearm,
    EmptyIRQ, DisableBefore, FallbackBefore
  };
  unsigned cases = 0;
  for (bool complete : {false, true})
  for (Owner owner : {Receiving, PacketIRQ, Tx, DisableAfter, FallbackAfter,
                      FailedRearm, EmptyIRQ, DisableBefore, FallbackBefore}) {
    RadioLibWrapper w; RetryClock clock; StaticPoolPacketManager manager{4};
    RetryDispatcher d(w, clock, manager); d.interval = 0;
    now_ms = 1000; d.begin(); selectMode(w, 1);
    d.loop(); ++now_ms; d.loop();
    assert(w._nf_calib_active && w.state == STATE_RX && !w._rx_ps_armed);
    if (complete) {
      for (unsigned i = 0; i < NoiseFloorEstimator::SAMPLE_COUNT; ++i) {
        now_ms += NoiseFloorEstimator::SAMPLE_INTERVAL_MS;
        assert(w._floor_estimator.add(-105, now_ms));
      }
    } else {
      now_ms = w._nf_calib_deadline;
    }
    const unsigned arms = w.arms;
    if (owner == PacketIRQ || owner == EmptyIRQ) {
      w.events.record(); w.radio.packet_length = owner == PacketIRQ ? 1 : 0;
    } else if (owner == Tx) { w.state = STATE_TX_WAIT; w.events.record(); }
    else w.packet = true;
    if (owner == DisableBefore) assert(w.setRxPowerSaving(false, 50000, 50000));
    if (owner == FallbackBefore) {
      assert(w.setRxPowerSaving(true, RX_POWERSAVING_CONTINUOUS_FALLBACK_US,
                               RX_POWERSAVING_CONTINUOUS_FALLBACK_US));
    }
    const unsigned processed = w.processed;
    d.loop();
    assert(!w._nf_calib_active && !w._nf_refresh_requested);
    assert(w.processed == processed + 1); // includes empty receive polls
    if (owner == PacketIRQ || owner == EmptyIRQ) {
      assert(w.n_recv == (owner == PacketIRQ ? 1U : 0U) && !w.hasPendingRadioInterrupt());
      assert(w._rx_ps_armed && !w._rx_hold_continuous && w.arms == arms + 1);
    } else if (owner == Tx) {
      assert(w.state == STATE_TX_WAIT && w.hasPendingRadioInterrupt());
      assert(!w._rx_hold_continuous && w.arms == arms);
      w.onSendFinished(); ++now_ms; d.loop();
      assert(w._rx_ps_armed && w.arms == arms + 1);
    } else if (owner == DisableBefore || owner == FallbackBefore) {
      assert(!w._rx_hold_continuous && !w._rx_ps_armed && w.arms == arms);
      w.packet = false; ++now_ms; d.loop();
      assert(!w._rx_ps_armed && w.arms == arms); // no RXPS restoration is wanted
    } else {
      assert(w._rx_hold_continuous && !w._rx_ps_armed && w.arms == arms);
      // Match the application pending-work gate for event-driven nRF52 sleep
      // as well as ESP32 timed sleep. Calibration itself is already finished.
      uint32_t wake = UINT32_MAX;
      assert(!w.isCalibratingNoiseFloor());
      assert(d.getNextQueueWakeDelay(wake) && wake == 0 && d.hasQueuedWorkDue());
      assert(mesh::power::powerSaveSleepSeconds(wake) == 0);
      // The const wake query must not read RSSI, IRQ status or other SPI state.
      const unsigned reads = w.reads, probes = w.mode_reads;
      const unsigned busy_probes = w.busy_probes, packet_probes = w.packet_probes;
      assert(w.getReceiveRetryDelay(now_ms, wake) && wake == 0);
      assert(w.reads == reads && w.mode_reads == probes
             && w.busy_probes == busy_probes && w.packet_probes == packet_probes);
      assert(w._rx_hold_continuous && !w._rx_retry_pending);
      // Known owners and intentional continuous RX do not request an extra
      // prompt poll; active calibration already has its separate sleep veto.
      for (unsigned excluded : {0U, 1U, 2U, 3U, 4U, 5U, 6U}) {
        if (excluded == 0) w._cw_active = true;
        if (excluded == 1) w._rx_ps_enabled = false;
        if (excluded == 2) w._rx_ps_continuous_fallback = true;
        if (excluded == 3) w._rx_ps_armed = true;
        if (excluded == 4) w.state = STATE_TX_WAIT;
        if (excluded == 5) w.state = STATE_IDLE;
        if (excluded == 6) w._nf_calib_active = true;
        assert(!w.getReceiveRetryDelay(now_ms, wake) && w._rx_hold_continuous);
        w._cw_active = false; w._rx_ps_enabled = true;
        w._rx_ps_continuous_fallback = w._rx_ps_armed = w._nf_calib_active = false;
        w.state = STATE_RX;
      }
      // Keep a partial/false reception intact. No IRQ is needed to clear it.
      for (unsigned i = 0; i < 100; ++i) { ++now_ms; d.loop(); }
      assert(w._rx_hold_continuous && w.arms == arms);
      if (owner == DisableAfter) assert(w.setRxPowerSaving(false, 50000, 50000));
      if (owner == FallbackAfter) {
        assert(w.setRxPowerSaving(true, RX_POWERSAVING_CONTINUOUS_FALLBACK_US,
                                 RX_POWERSAVING_CONTINUOUS_FALLBACK_US));
      }
      if (owner == DisableAfter || owner == FallbackAfter) {
        assert(!w.getReceiveRetryDelay(now_ms, wake));
      }
      w.packet = false;
      if (owner == FailedRearm) w.arm_result = -5;
      ++now_ms; d.loop();
      assert(!w._rx_hold_continuous);
      if (owner == DisableAfter || owner == FallbackAfter) {
        assert(!w._rx_ps_armed && w.arms == arms); // continuous RX is now intentional
      } else if (owner == FailedRearm) {
        assert(w.state == STATE_IDLE && !w._rx_ps_armed && w.arms == arms + 1);
        assert(retryDelay(w) == 20);
        ++now_ms; d.loop(); assert(w.arms == arms + 1 && retryDelay(w) == 19);
        w.arm_result = 0; now_ms += 19; d.loop();
        assert(w._rx_ps_armed && w.arms == arms + 2 && !w._rx_retry_pending);
      } else {
        assert(w._rx_ps_armed && w.arms == arms + 1);
      }
      assert(!w._rx_hold_continuous && !d.hasQueuedWorkDue());
    }
    ++cases;
  }
  std::printf("PASS: deferred calibration restoration (%u cases)\n", cases);
}

static void recoveryOwnershipAdmission() {
  unsigned cases = 0;
  for (unsigned mode : {0U, 1U, 2U}) for (bool hard : {false, true})
  for (bool between_checks : {false, true}) {
    RadioLibWrapper w; selectMode(w, mode);
    w._rx_ps_armed = mode == 1;
    w._rx_boosted_gain_valid = w._cur_rx_boosted_gain = true;
    w._nf_refresh_requested = false; w._nf_last_calib = now_ms;
    assert(w._floor_estimator.add(-105, now_ms));
    const uint8_t payload[] = {0x18, 0x27, 0x36, 0x45, 0x54};
    memcpy(w.radio.fifo, payload, sizeof(payload));
    w.radio.packet_length = sizeof(payload);
    if (between_checks) w.inject_packet_irq_at = 1;
    else w.events.record();
    const auto state = w.state;
    const auto armed = w._rx_ps_armed;
    assert(!w.recoverRadio(hard) && "refused recovery must not report success");
    assert(w.hasPendingRadioInterrupt() && w.state == state && w._rx_ps_armed == armed);
    assert(w.agc_resets == 0 && w.gain_changes == 0 && w.arms == 0 && w.stops == 0);
    assert(w._floor_estimator.count() == 1 && !w._nf_refresh_requested);
    assert(w.radio.fifo_reads == 0 && w.radio.packet_length == sizeof(payload));
    assert(!memcmp(w.radio.fifo, payload, sizeof(payload)));
    assert(w.packet_probes == unsigned(between_checks));
    // The receive owner still drains the original FIFO once, then a clean
    // recovery can be admitted normally. Neither refusal consumes its IRQ.
    uint8_t received[sizeof(payload)] = {};
    assert(w.recvRaw(received, sizeof(received)) == int(sizeof(payload)));
    assert(!memcmp(received, payload, sizeof(payload)) && w.radio.fifo_reads == 1);
    assert(!w.hasPendingRadioInterrupt() && w.n_recv == 1);
    w.onReceiveProcessed();
    assert(w.recoverRadio(hard));
    assert(w.agc_resets == 1 && w.gain_changes == 1 && w.state == STATE_IDLE);
    assert(w._floor_estimator.count() == 0 && w._nf_refresh_requested);
    ++cases;
  }
  std::printf("PASS: recovery ownership admission (%u cases)\n", cases);
}

int main(int argc, char** argv) {
  if (argc == 2) {
    assert(!strcmp(argv[1], "recovery"));
    recoveryOwnershipAdmission();
    return 0;
  }
  assert(argc == 1);
  noiseFloorDeadlines(); persistentFailure(); rampAndRollover(); ownersAndImmediateRearms();
  pendingSetupAndTx(); retainedOutboundWake(); busyAfterFailure();
  deferredCalibrationRestore();
  std::puts("PASS: receive retry lifecycle");
}
