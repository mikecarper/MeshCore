
static const char* scenario;
#define CHECK(expression, diagnostic) do { if (!(expression)) { \
  std::fprintf(stderr, "FAIL %s: %s (line %d)\n", scenario, diagnostic, __LINE__); \
  return 1; } } while (0)

static void prepare(RadioLibWrapper& w, unsigned mode, uint32_t start = 100) {
  now_ms = start;
  w._rx_ps_enabled = mode != 0;
  w._rx_ps_continuous_fallback = mode == 2;
  w._rx_ps_armed = mode == 1;
  w._noise_floor_valid = false;
  w._nf_refresh_requested = true;
  w._nf_last_calib = 0;
  w.startRecv();
  w.loop();
  if (w.state == STATE_IDLE) w.startRecv();
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  scenario = argv[1];
  if (!std::strcmp(scenario, "quiet")) {
    for (unsigned mode : {0U, 1U, 2U}) {
      RadioLibWrapper w;
      prepare(w, mode);
      const uint32_t start = now_ms;
      uint32_t eligible_ms = 0;
      unsigned iterations = 0;
      while (w._nf_refresh_requested || w._nf_calib_active) {
        CHECK(++iterations < 200, "deadline service must remain bounded");
        uint32_t wait = UINT32_MAX;
        CHECK(w.getNoiseFloorWakeDelay(now_ms, wait), "active batch lost wake deadline");
        CHECK(wait > 0 || w._floor_estimator.complete(),
              "pending batch must permit idle between samples");
        CHECK(wait <= NoiseFloorEstimator::SAMPLE_INTERVAL_MS,
              "sample deadline exceeded original spacing");
        eligible_ms += wait;
        now_ms += wait;
        w.loop();
        if (w.state == STATE_IDLE) w.startRecv();
      }
      CHECK(w.reads == NoiseFloorEstimator::SAMPLE_COUNT,
            "idle scheduling changed accepted sample count");
      CHECK(w._noise_floor_valid && w._noise_floor_centi_dbm == -10000,
            "idle scheduling changed published estimator value");
      CHECK(w._rx_ps_armed == (mode == 1), "calibration did not restore receive policy");
      CHECK(eligible_ms >= 3150 && uint32_t(now_ms - start) < 3300,
            "quiet collection must retain original quality and bounded cadence");
    }
  } else if (!std::strcmp(scenario, "rollover")) {
    for (uint32_t start : {100U, UINT32_MAX - 3U, uint32_t(0U - NF_CALIB_SETTLE_MS)}) {
      RadioLibWrapper w;
      prepare(w, 0, start);
      uint32_t wait = 0;
      CHECK(w.reads == 0, "RX settling must precede every first sample");
      CHECK(w.getNoiseFloorWakeDelay(now_ms, wait) && wait == NF_CALIB_SETTLE_MS,
            "settle deadline must survive rollover, including timestamp zero");
      now_ms += wait - 1;
      w.loop();
      CHECK(w.reads == 0 && w.getNoiseFloorWakeDelay(now_ms, wait) && wait == 1,
            "sample accepted before settle deadline");
      ++now_ms;
      CHECK(w.getNoiseFloorWakeDelay(now_ms, wait) && wait == 0,
            "exact sample boundary must request service");
      w.loop();
      CHECK(w.reads == 1 && w.getNoiseFloorWakeDelay(now_ms, wait) && wait == 50,
            "next sample spacing must survive rollover");
      now_ms += 49;
      w.loop();
      CHECK(w.reads == 1, "early sample changed original sample spacing");
      ++now_ms;
      w.loop();
      CHECK(w.reads == 2, "next sample deadline not serviced");
    }
  } else if (!std::strcmp(scenario, "queries")) {
    RadioLibWrapper w;
    prepare(w, 1);
    uint32_t wait = 0;
    const unsigned reads = w.reads, modes = w.mode_reads;
    const unsigned busy = w.busy_probes, packets = w.packet_probes;
    const auto count = w._floor_estimator.count();
    for (unsigned i = 0; i < 100; ++i)
      CHECK(w.getNoiseFloorWakeDelay(now_ms, wait) && wait > 0,
            "query must preserve pending sample deadline");
    CHECK(w.reads == reads && w.mode_reads == modes && w.busy_probes == busy
          && w.packet_probes == packets && w._floor_estimator.count() == count,
          "deadline query must not access hardware or mutate samples");
    w.events.record();
    CHECK(w.getNoiseFloorWakeDelay(now_ms, wait) && w.hasPendingRadioInterrupt(),
          "deadline query consumed radio IRQ");
    now_ms += NF_CALIB_SETTLE_MS;
    w.loop();
    CHECK(w.hasPendingRadioInterrupt() && w.reads == reads,
          "pending packet IRQ must retain ownership during sampling service");
    w.claimRadioInterrupt();
    now_ms += NoiseFloorEstimator::SAMPLE_INTERVAL_MS;
    w.inject_mode_irq = true;
    w.loop();
    CHECK(w.hasPendingRadioInterrupt() && w.reads == reads,
          "IRQ during status probe must prevent RSSI read and remain pending");
  } else if (!std::strcmp(scenario, "rejected")) {
    for (bool busy : {false, true}) for (uint32_t start : {100U, UINT32_MAX - 17U}) {
      RadioLibWrapper w;
      prepare(w, 1, start);
      w._noise_floor_valid = true;
      w.packet = !busy;
      w.busy = busy;
      unsigned iterations = 0;
      while (w._nf_refresh_requested || w._nf_calib_active) {
        CHECK(++iterations < 1000, "rejected-sample retries must not hot poll");
        uint32_t wait = 0;
        CHECK(w.getNoiseFloorWakeDelay(now_ms, wait), "rejected sample lost timeout wake");
        CHECK(wait > 0, "rejected sample must permit bounded idle before retry");
        now_ms += wait;
        w.loop();
        if (w.state == STATE_IDLE) w.startRecv();
      }
      CHECK(w.reads == 0 && w._noise_floor_centi_dbm == -10500,
            "rejected reads must retain published floor");
      CHECK(uint32_t(now_ms - start) <= NoiseFloorEstimator::WINDOW_TIMEOUT_MS + 50,
            "idle must not extend calibration timeout");
    }
  } else if (!std::strcmp(scenario, "lazy")) {
    for (unsigned path : {0U, 1U, 2U}) for (bool accepted : {false, true})
    for (uint32_t start : {1000U, UINT32_MAX - 3149U}) {
      RadioLibWrapper w;
      now_ms = start;
      for (unsigned i = 0; i < NoiseFloorEstimator::SAMPLE_COUNT; ++i) {
        now_ms = start + i * NoiseFloorEstimator::SAMPLE_INTERVAL_MS;
        CHECK(w._floor_estimator.add(accepted ? -105 : -70, now_ms),
              "policy fixture must use original complete spaced blocks");
      }
      w.loop();
      CHECK(!w._nf_refresh_requested && w._nf_refresh_attempted,
            "complete publish attempt must retire calibration");
      CHECK(w._nf_lazy_refresh == accepted && w._noise_floor_valid,
            "held rise must not earn slow refresh merely because old floor is valid");
      const uint32_t finish = now_ms;
      const uint32_t interval = accepted ? NF_LAZY_REFRESH_INTERVAL_MS : NF_CALIB_INTERVAL_MS;
      for (uint32_t age : {1U, interval - 1}) {
        now_ms = finish + age;
        if (path == 0) w.triggerNoiseFloorCalibrate(7);
        else if (path == 1) w.isReceivingPassive(7);
        else w.requestPeriodicNoiseFloorRefresh();
        CHECK(!w._nf_refresh_requested, "routine caller ignored lazy/retry cooldown");
      }
      now_ms = finish + interval;
      if (path == 0) w.triggerNoiseFloorCalibrate(7);
      else if (path == 1) w.isReceivingPassive(7);
      else w.requestPeriodicNoiseFloorRefresh();
      CHECK(w._nf_refresh_requested, "routine refresh missed its selected cadence");
      w._nf_refresh_requested = false;
      w._nf_lazy_refresh = true;
      w._nf_last_calib = now_ms;
      w.recalibrateNoiseFloor();
      CHECK(w._nf_refresh_requested && !w._nf_lazy_refresh && !w._noise_floor_valid,
            "forced tuning/gain baseline must bypass lazy routine cadence");
    }
  } else if (!std::strcmp(scenario, "agc-idle")) {
    for (unsigned mode : {0U, 1U, 2U}) for (bool lazy : {false, true})
    for (uint32_t last : {0U, 100U, UINT32_MAX - 2000U}) {
      RadioLibWrapper w;
      w._rx_ps_enabled = mode != 0;
      w._rx_ps_continuous_fallback = mode == 2;
      w._rx_ps_armed = mode == 1;
      w._noise_floor_valid = true;
      w._nf_refresh_requested = w._nf_calib_active = false;
      w._nf_lazy_refresh = lazy;
      w._nf_refresh_attempted = true;
      w._nf_last_calib = last;
      const uint32_t interval = lazy ? NF_LAZY_REFRESH_INTERVAL_MS : NF_CALIB_INTERVAL_MS;
      for (uint32_t age : {1000U, 2000U, interval - 1}) {
        now_ms = last + age;
        w.requestPeriodicNoiseFloorRefresh();
        CHECK(w.tryResetAGC(), "idle maintenance must run the reset sequence");
        CHECK(!w._nf_refresh_requested && w._noise_floor_valid
              && w._nf_lazy_refresh == lazy && w._nf_last_calib == last,
              "maintenance must preserve the floor and original refresh deadline");
        w.startRecv();
        CHECK(w._rx_ps_armed == (mode == 1) && !w._nf_calib_active,
              "maintenance must return immediately to the selected RX policy");
      }
      now_ms = last + interval;
      w.requestPeriodicNoiseFloorRefresh();
      CHECK(w._nf_refresh_requested, "maintenance postponed a routine refresh or failed retry");
    }
    for (unsigned reason : {0U, 1U, 2U, 3U, 4U, 5U}) {
      RadioLibWrapper w;
      w._nf_refresh_requested = w._nf_calib_active = false;
      w._noise_floor_valid = true;
      w._nf_last_calib = now_ms;
      if (reason == 0) w._noise_floor_valid = false;
      if (reason == 1) w._nf_refresh_requested = true;
      if (reason == 2) w._nf_calib_active = true;
      if (reason == 3) w._profiles.secondary.mode = mesh::RadioProfileMode::RxTx;
      if (reason == 5) { w._rx_boosted_gain_valid = true; w.gain_apply_success = false; }
      if (reason == 4) w.resetAGC(); // Recovery/legacy uses the forced API.
      else CHECK(w.tryResetAGC(), "idle reset was unexpectedly refused");
      CHECK(w._nf_refresh_requested && w._nf_last_calib == 0,
            "startup, pending, dual-profile, failed gain and recovery floors must still refresh");
    }
  } else if (!std::strcmp(scenario, "timeout-zero")) {
    RadioLibWrapper w;
    // A legitimate 30-second timeout lands exactly on uint32_t zero.
    const uint32_t start = uint32_t(0U - NoiseFloorEstimator::WINDOW_TIMEOUT_MS);
    prepare(w, 0, start);
    w.packet = true;
    w._noise_floor_valid = false;
    CHECK(w._nf_window_armed && uint32_t(w._nf_calib_deadline) == 0,
          "wrapped timeout must retain explicit armed state");
    now_ms = UINT32_MAX;
    w.loop();
    uint32_t wait = 0;
    CHECK(w._nf_refresh_requested && w.getNoiseFloorWakeDelay(now_ms, wait) && wait == 1,
          "last timeout millisecond must remain idle eligible");
    now_ms = 0;
    CHECK(w.getNoiseFloorWakeDelay(now_ms, wait) && wait == 0,
          "wrapped timeout must request exact-boundary service");
    w.loop();
    CHECK(!w._nf_refresh_requested && w._nf_refresh_attempted && w._nf_last_calib == 0,
          "timeout at timestamp zero must retire failed attempt");
    w.packet = false;
    now_ms = NF_CALIB_INTERVAL_MS - 1;
    w.requestPeriodicNoiseFloorRefresh();
    CHECK(!w._nf_refresh_requested, "timestamp-zero failure must not masquerade as startup");
    ++now_ms;
    w.requestPeriodicNoiseFloorRefresh();
    CHECK(w._nf_refresh_requested, "timestamp-zero failure missed bounded retry");
  } else return 2;
  std::printf("PASS: %s\n", scenario);
}
