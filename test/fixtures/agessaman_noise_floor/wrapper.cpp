
#include <cstring>

static_assert(MC_AGESSAMAN_NOISE_FLOOR_EXACT == 1,
              "V4 builds must select the requested upstream sampler");

static void quietBlock(RadioLibWrapper& w, float rssi = -100) {
  w.rssi = rssi;
  for (unsigned i = 0; i < 64; ++i) w.loop();
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const char* scenario = argv[1];
  RadioLibWrapper w;
  w.resetAgessamanNoiseFloor();
  now_ms = 0;
  if (!std::strcmp(scenario, "cadence")) {
    assert(w.usesAgessamanNoiseFloor());
    uint32_t delay = 999;
    assert(w.getNoiseFloorWakeDelay(now_ms, delay) && delay == 0);
    quietBlock(w);
    assert(w.reads == 64 && w.mode_reads == 0 && w.arms == 0 && w.stops == 0);
    assert(w.EXACT_COLLECTOR.pendingPublication());
    assert(!w._noise_floor_valid && w._noise_floor == 0 && w._nf_refresh_requested);
    assert(w.getNoiseFloorWakeDelay(now_ms, delay) && delay == 0);
    w.loop();
    assert(w.reads == 64 && w.mode_reads == 0);
    assert(w._noise_floor == -100 && w._noise_floor_centi_dbm == -10000);
    assert(w._noise_floor_valid && !w._nf_refresh_requested);
    assert((w._nf_published_valid & 1U) && w._nf_published_at[0] == 0);
    assert(w._nf_published_generation[0] == w._profiles.generation[0]);
    assert(!w.getNoiseFloorWakeDelay(now_ms, delay));
    w.triggerNoiseFloorCalibrate(7);
    assert(w._threshold == 7 && w.EXACT_COLLECTOR.count() == 0);
    w.rssi = -85; // exceeds the prior -100 + 14 dB gate
    for (unsigned i = 0; i < 10000; ++i) w.loop();
    assert(w.EXACT_COLLECTOR.count() == 0 && w._noise_floor == -100);
    assert(w.reads == 10064 && w.mode_reads == 0);
  } else if (!std::strcmp(scenario, "ownership")) {
    w.events.record();
    for (unsigned i = 0; i < 100; ++i) w.loop();
    assert(w.hasPendingRadioInterrupt() && w.reads == 0 && w.EXACT_COLLECTOR.count() == 0);
    w.claimRadioInterrupt();
    w.packet = true;
    now_ms = 120000; // an old collection timeout must not cancel source sampling
    for (unsigned i = 0; i < 100; ++i) w.loop();
    assert(w.packet && w.reads == 0 && w._nf_refresh_requested);
    w.packet = false;
    now_ms = 0;
    quietBlock(w);
    w.events.record();
    w.state = STATE_TX_WAIT;
    w.loop(); // source publication is not an RSSI read and can occur outside RX
    assert(w.hasPendingRadioInterrupt() && w.state == STATE_TX_WAIT);
    assert(w._noise_floor == -100 && w.reads == 64);
  } else if (!std::strcmp(scenario, "reset_trigger")) {
    for (unsigned i = 0; i < 32; ++i) {
      w.loop(); w.triggerNoiseFloorCalibrate(9);
      assert(w.EXACT_COLLECTOR.count() == i + 1);
    }
    for (unsigned i = 0; i < 32; ++i) w.loop();
    assert(w.EXACT_COLLECTOR.pendingPublication());
    w.triggerNoiseFloorCalibrate(10);
    assert(w._threshold == 10 && w.EXACT_COLLECTOR.count() == 0);
    assert(w._noise_floor == 0 && !w._noise_floor_valid);
    quietBlock(w, -120); w.loop();
    assert(w._noise_floor == -120 && w._noise_floor_valid);
    w.events.record();
    assert(!w.tryResetAGC());
    assert(w._noise_floor == -120 && w.agc_resets == 0);
    w.claimRadioInterrupt();
    w.packet = true;
    assert(!w.tryResetAGC() && w._noise_floor == -120);
    w.packet = false;
    assert(w.tryResetAGC());
    assert(w.state == STATE_IDLE && w.agc_resets == 1);
    assert(w._noise_floor == 0 && w._noise_floor_centi_dbm == 0);
    assert(!w._noise_floor_valid && !(w._nf_published_valid & 1U));
    assert(w.EXACT_COLLECTOR.count() == 0 && w._nf_refresh_requested);
    w.startRecv();
    quietBlock(w, -80); w.loop();
    assert(w._noise_floor == -80 && w._noise_floor_valid);
  } else if (!std::strcmp(scenario, "adapter")) {
    assert(w.usesAgessamanNoiseFloor());
    w._rx_ps_enabled = true;
    assert(!w.usesAgessamanNoiseFloor());
    w._rx_ps_continuous_fallback = true;
    assert(!w.usesAgessamanNoiseFloor());
    mesh::RadioProfileConfig second;
    second.params.freq = 911.15f; second.params.bw = 250;
    second.params.sf = 7; second.params.cr = 5;
    second.mode = mesh::RadioProfileMode::Rx;
    w._profiles.setSecondary(second, false);
    assert(w._profiles.enabled() && !w.usesAgessamanNoiseFloor());
  } else if (!std::strcmp(scenario, "passive")) {
    assert(!w.isCalibratingNoiseFloor());
    quietBlock(w, -120); w.loop();
    w.triggerNoiseFloorCalibrate(7);
    w.rssi = -100; // blocked by the retained -120 + 14 dB admission gate
    for (unsigned i = 0; i < 10000; ++i) w.loop();
    assert(w.EXACT_COLLECTOR.count() == 0 && w._nf_refresh_requested);
    assert(w._noise_floor == -120 && !w.isCalibratingNoiseFloor());
    uint32_t delay = 999;
    assert(w.getNoiseFloorWakeDelay(now_ms, delay) && delay == 0);
    assert(w.tryResetAGC() && w.agc_resets == 1 && w._noise_floor == 0);
    assert(!w.isCalibratingNoiseFloor());
  } else if (!std::strcmp(scenario, "history")) {
    float dbm = 999;
    uint32_t stamp = 999;
    assert(!w.getNoiseFloorReading(0, dbm, stamp));
    assert(w.profileNoiseFloorSecondsRemaining(0) == 0.0f);
    quietBlock(w, -100);
    assert(!w.getNoiseFloorReading(0, dbm, stamp));
    now_ms = 1234;
    w.loop();
    assert(w.getNoiseFloorReading(0, dbm, stamp) && dbm == -100 && stamp == 1234);
    w.requestNoiseFloorRefresh();
    quietBlock(w, -85); // All candidates fail the literal strict gate.
    now_ms = 9876;
    w.loop();
    assert(w.getNoiseFloorReading(0, dbm, stamp) && dbm == -100 && stamp == 1234);
    assert(!w.isCalibratingNoiseFloor());
    assert(w.profileNoiseFloorSecondsRemaining(0) == 0.0f);
    quietBlock(w, -105);
    assert(w.getNoiseFloorReading(0, dbm, stamp) && stamp == 1234);
    w.loop();
    assert(w.getNoiseFloorReading(0, dbm, stamp) && dbm == -105 && stamp == 9876);
    ++w._profiles.generation[0];
    assert(!w.getNoiseFloorReading(0, dbm, stamp));
    w.recalibrateNoiseFloor();
    assert(!w.getNoiseFloorReading(0, dbm, stamp));
    quietBlock(w, -90);
    now_ms = 10234;
    w.loop();
    assert(w.getNoiseFloorReading(0, dbm, stamp) && dbm == -90 && stamp == 10234);
    assert(w._nf_published_generation[0] == w._profiles.generation[0]);
    w.resetAGC();
    assert(!w.getNoiseFloorReading(0, dbm, stamp));
  } else if (!std::strcmp(scenario, "toggles")) {
    using Result = mesh::RadioParamApplyResult;
    for (unsigned i = 0; i < 32; ++i) w.loop();
    assert(w.EXACT_COLLECTOR.count() == 32);
    assert(w.trySetRxPowerSaving(false, 50000, 50000) == Result::APPLIED);
    assert(w.EXACT_COLLECTOR.count() == 32 && !w._rx_ps_enabled);
    assert(w.trySetRxPowerSaving(true, 0, 50000) == Result::FAILED);
    assert(w.EXACT_COLLECTOR.count() == 32 && !w._rx_ps_enabled);
    w._cw_active = true;
    assert(w.trySetRxPowerSaving(true, 50000, 50000) == Result::BUSY);
    assert(w.EXACT_COLLECTOR.count() == 32 && !w._rx_ps_enabled);
    w._cw_active = false;
    // Parameter updates that remain in the same exact collector also preserve
    // its partial block; only a change of collector invalidates the baseline.
    assert(w.trySetRxPowerSaving(false, 60000, 60000) == Result::APPLIED);
    assert(w.EXACT_COLLECTOR.count() == 32 && w.usesAgessamanNoiseFloor());
    for (unsigned i = 0; i < 16; ++i) assert(w._floor_estimator.add(-110, i * 50));
    assert(w._floor_estimator.count() == 16);
    assert(w.trySetRxPowerSaving(true, 50000, 50000) == Result::APPLIED);
    assert(!w.usesAgessamanNoiseFloor() && w._floor_estimator.count() == 0);
    assert(!w._noise_floor_valid && w._nf_refresh_requested);
    assert(w.trySetRxPowerSaving(false, 50000, 50000) == Result::APPLIED);
    assert(w.usesAgessamanNoiseFloor() && w.EXACT_COLLECTOR.count() == 0);
    assert(w._noise_floor == 0 && !w._noise_floor_valid);
    quietBlock(w, -90); w.loop();
    assert(w._noise_floor == -90 && w._noise_floor_valid);
    assert(w.trySetRxPowerSaving(true, RX_POWERSAVING_CONTINUOUS_FALLBACK_US,
                               RX_POWERSAVING_CONTINUOUS_FALLBACK_US) == Result::APPLIED);
    assert(w._rx_ps_continuous_fallback && !w.usesAgessamanNoiseFloor());
    assert(!w._noise_floor_valid && w._floor_estimator.count() == 0);
    assert(w.trySetRxPowerSaving(false, 50000, 50000) == Result::APPLIED);
    assert(w.usesAgessamanNoiseFloor() && w.EXACT_COLLECTOR.count() == 0);
    assert(w._noise_floor == 0 && !w._noise_floor_valid);
  } else assert(false);
  std::printf("PASS: %s\n", scenario);
}
