#include <cstring>

using Result = mesh::AGCMaintenanceResult;
using Reason = WarmRxRefresh::Reason;
using Step = WarmRxRefresh::Step;

static void prime(CustomSX1262Wrapper& wrapper) {
  mesh::radioDirection::allowed = true;
  assert(wrapper.chip.CustomSX1262::startReceive() == 0);
  assert(wrapper.chip._profileSwitch.rxValid && wrapper.chip._profileSwitch.packetValid);
  assert(wrapper.chip.warmRxRefreshReady());
  wrapper.chip.trace.clear();
  wrapper._nf_refresh_requested = true; // Ordinary literal collection is not exclusive radio ownership.
  for (unsigned i = 0; i < 9; ++i) {
    assert(!wrapper._agessaman_floor.loop(true, []() { return false; },
                                        []() { return -100.0f; }, wrapper._noise_floor));
  }
  assert(wrapper._agessaman_floor.count() == 9 && wrapper._noise_floor == -105);
}

static void unchangedCollector(const RadioLibWrapper& wrapper) {
  assert(wrapper._agessaman_floor.count() == 9 && wrapper._noise_floor == -105);
  assert(wrapper._nf_refresh_requested && wrapper._noise_floor_valid);
  assert(wrapper.floor_resets == 0);
}

static void closed(const CustomSX1262Wrapper& wrapper, bool warm, uint8_t features) {
  const auto& radio = *wrapper._radio;
  assert(radio.standbyXOSC == warm && radio.getProfileSwitchFeatures() == features);
  assert(!radio._profileSwitch.open && !radio._profileSwitch.active && !radio._profileSwitch.standbyReady);
  assert(!radio._warm_refresh_tracking && !wrapper._warm_refresh.attempting);
  assert(!wrapper._profile_standby_held);
}

static void success() {
  for (bool warm : {false, true}) {
    CustomSX1262Wrapper w;
    prime(w);
    w.chip.standbyXOSC = warm;
    const uint8_t features = w.chip.getProfileSwitchFeatures();
    assert(w.getOldAGCInterval() == 0);
    assert(w.periodicAGCMaintenance(false) == Result::Applied);
    assert(w.chip.trace == (MC_SX1262_FAST_FS ? "IfCWR" : "sCWR"));
    assert(w.chip.actual_rx_commands == 1 && w.chip._profileFastResumes == 1);
    assert(w.state == STATE_RX && w._warm_refresh.requests == 1);
    assert(w._warm_refresh.admissions == 1 && w._warm_refresh.successes == 1);
    assert(w._warm_refresh.failures == 0 && w._warm_refresh.deferrals == 0);
    assert(w._warm_refresh.warm_gap.count == 1 && !w._warm_refresh.pending);
    assert(w.old_resets == 0 && w.reconfigure_ends == 0);
    closed(w, warm, features);
    unchangedCollector(w);
  }
  CustomSX1262Wrapper short_frame;
  prime(short_frame);
  short_frame.chip.inject_rx_irq = true;
  assert(short_frame.periodicAGCMaintenance(true) == Result::Applied);
  assert(short_frame.hasPendingRadioInterrupt() && short_frame.chip.pending_fifo);
  assert(!short_frame.chip.lost_fifo);
  unchangedCollector(short_frame);
}

static void guards() {
  for (unsigned gate = 0; gate < 24; ++gate) {
    CustomSX1262Wrapper w;
    prime(w);
    w.chip.standbyXOSC = false;
    const uint8_t features = w.chip.getProfileSwitchFeatures();
    switch (gate) {
      case 0: w.state = STATE_IDLE; break;
      case 1: w.state = STATE_TX_WAIT; break;
      case 2: w._params_valid = false; break;
      case 3: w._warm_refresh.pending = true; break;
      case 4: {
        mesh::RadioProfileConfig second;
        second.params = w._profiles.primary;
        second.params.freq += 1.0f;
        second.mode = mesh::RadioProfileMode::RxTx;
        w._profiles.setSecondary(second, false);
        break;
      }
      case 5: w._active_profile = 1; break;
      case 6: ++w._profile_generation; break;
      case 7: w._profile_refresh_required = true; break;
      case 8: w._profile_repair_required = true; break;
      case 9: w._profile_retry_at = 200; break;
      case 10: w._profile_rxps_suspended = true; break;
      case 11: w._rx_ps_enabled = true; break;
      case 12: w._rx_ps_armed = true; break;
      case 13: w._rx_hold_continuous = true; break;
      case 14: w._rx_retry_pending = true; break;
      case 15: w._nf_calib_active = true; break;
      case 16: w._nf_window_armed = true; break;
      case 17: w._cw_active = true; break;
      case 18: w._morse_owned = true; break;
      case 19: w._morse_rx_owned = true; break;
      case 20: w._beacon_owned = true; break;
      case 21: mesh::radioDirection::allowed = false; break;
      case 22: w.busy = true; break;
      case 23: w.packet = true; break;
    }
    assert(w.periodicAGCMaintenance(false) == Result::Deferred);
    assert(w.chip.trace.empty() && w.chip.actual_rx_commands == 0);
    assert(w._warm_refresh.requests == 1 && w._warm_refresh.deferrals == 1);
    assert(w._warm_refresh.admissions == 0 && w._warm_refresh.successes == 0);
    assert(w.old_resets == 0 && w.reconfigure_ends == 0);
    closed(w, false, features);
    unchangedCollector(w);
  }
  mesh::radioDirection::allowed = true;
}

static void ownership() {
  for (unsigned race = 0; race < 3; ++race) {
    CustomSX1262Wrapper w;
    prime(w);
    w.chip.standbyXOSC = false;
    const uint8_t features = w.chip.getProfileSwitchFeatures();
    if (race == 0) { w.events.record(); w.chip.pending_fifo = true; }
    if (race == 1) w.inject_probe_irq = true;
    if (race == 2) w.chip.inject_standby_irq = true;
    assert(w.periodicAGCMaintenance(true) == Result::Deferred);
    assert(w.chip.trace == (race == 2 ? (MC_SX1262_FAST_FS ? "If" : "s") : ""));
    assert(w.hasPendingRadioInterrupt() && w.chip.pending_fifo && !w.chip.lost_fifo);
    assert(w.chip.actual_rx_commands == 0 && w.reconfigure_ends == 0);
    assert(w._warm_refresh.admissions == (race == 2 ? 1U : 0U));
    assert(w._warm_refresh.successes == 0 && w._warm_refresh.deferrals == 1);
    closed(w, false, features);
    unchangedCollector(w);
  }
}

static void caches() {
  for (unsigned cache = 0; cache < 11; ++cache) {
    CustomSX1262Wrapper w;
    prime(w);
    switch (cache) {
      case 0: w.chip.setProfileSwitchFeatures(w.chip.getProfileSwitchFeatures() & ~mesh::RadioSwitchTuning::Warm); break;
      case 1: w.chip.setProfileSwitchFeatures(w.chip.getProfileSwitchFeatures() & ~mesh::RadioSwitchTuning::Packet); break;
      case 2: w.chip._profileSwitch.rxValid = false; break;
      case 3: w.chip._profileSwitch.packetValid = false; break;
      case 4: w.chip._coldStandby = true; break;
      case 5: w.chip._tcxoWakePending = true; break;
      case 6: ++w.chip.preambleLengthLoRa; break;
      case 7: ++w.chip.crcTypeLoRa; break;
      case 8: --w.chip.implicitLen; break;
      case 9: ++w.chip.headerType; break;
      case 10: ++w.chip.invertIQEnabled; break;
    }
    const uint8_t features = w.chip.getProfileSwitchFeatures();
    assert(w.periodicAGCMaintenance(true) == Result::Deferred);
    assert(w._warm_refresh.reason == Reason::Cache && w.chip.trace.empty());
    assert(w._warm_refresh.admissions == 0 && w.chip.actual_rx_commands == 0);
    closed(w, true, features);
    unchangedCollector(w);
  }
  CustomSX1262Wrapper owned;
  prime(owned);
  owned.chip.beginProfileSwitch(true);
  assert(owned.periodicAGCMaintenance(true) == Result::Deferred);
  assert(owned.chip._profileSwitch.open && owned.chip.trace.empty());
  owned.chip.endProfileSwitch(true);
}

static void failures() {
  const char standby = MC_SX1262_FAST_FS ? 'f' : 's';
  for (char command : std::string(1, standby) + "CR") {
    for (bool warm : {false, true}) {
      CustomSX1262Wrapper w;
      prime(w);
      w.chip.standbyXOSC = warm;
      const uint8_t features = w.chip.getProfileSwitchFeatures();
      w.chip.fail = command;
      assert(w.periodicAGCMaintenance(true) == Result::Failed);
      assert(w._warm_refresh.requests == 1 && w._warm_refresh.admissions == 1);
      assert(w._warm_refresh.failures == 1 && w._warm_refresh.successes == 0);
      assert(w._warm_refresh.pending && w._warm_refresh.last_error == -100);
      assert(w._warm_refresh.last_failed_step == (command == standby ? (MC_SX1262_FAST_FS ? Step::Fs : Step::Standby)
                                              : command == 'C' ? Step::ClearIrq : Step::SetRx));
      assert(w.state == STATE_IDLE && w._profile_repair_required && w._profile_refresh_required);
      assert(uint32_t(w._profile_retry_at - millis()) == 250);
      assert(!w.chip._profileSwitch.rxValid && !w.chip._profileSwitch.packetValid);
      assert(!w.chip._profileSwitch.modulationValid);
      closed(w, warm, features);
      unchangedCollector(w);
      const std::string stopped = w.chip.trace;
      assert(w.periodicAGCMaintenance(true) == Result::Deferred);
      assert(w.chip.trace == stopped && w.old_resets == 0);
    }
  }
  // An error acknowledgement does not change the proven packet tuple. A
  // completion racing that standby must remain readable before any repair.
  for (char command : std::string(1, standby) + "R") {
    CustomSX1262Wrapper raced;
    prime(raced);
    raced.chip.fail = command;
    raced.chip.inject_standby_irq = command == standby;
    raced.chip.inject_rx_irq = command == 'R';
    assert(raced.periodicAGCMaintenance(true) == Result::Failed);
    assert(raced.hasPendingRadioInterrupt() && raced.chip.pending_fifo && !raced.chip.lost_fifo);
    assert(!raced._profile_repair_required && raced.state == STATE_RX);
    assert(raced._warm_refresh.last_error == -100 && raced._warm_refresh.successes == 0);
    assert(!raced.chip._profileSwitch.open && !raced.chip._profileSwitch.rxValid);
    unchangedCollector(raced);
    raced.chip.fail = 0;
    raced.chip.inject_standby_irq = raced.chip.inject_rx_irq = false;
    uint8_t packet[8] = {};
    assert(raced.recvRaw(packet, sizeof(packet)) == 5);
    assert(std::memcmp(packet, "abcde", 5) == 0 && raced.n_recv == 1 && raced.n_recv_errors == 0);
    assert(!raced.hasPendingRadioInterrupt() && !raced.chip.pending_fifo && !raced.chip.lost_fifo);
    assert(raced.state == STATE_RX && !raced._warm_refresh.pending);
    assert(raced._warm_refresh.recovery_gap.count == 1 && raced._warm_refresh.warm_gap.count == 0);
    assert(raced.chip.trace.find('d') < raced.chip.trace.find('G'));
    unchangedCollector(raced);
  }
}

struct NoOpRadio : CustomSX1262 {
  int16_t startReceive() override { return 0; }
};

static void noOp() {
  CustomSX1262Wrapper w;
  prime(w);
  NoOpRadio radio;
  radio.events = &w.events;
  assert(radio.CustomSX1262::startReceive() == 0);
  radio.trace.clear();
  w._radio = &radio;
  assert(w.periodicAGCMaintenance(true) == Result::Failed);
  assert(radio.actual_rx_commands == 0 && radio._profileFastResumes == 0);
  assert(w._warm_refresh.last_error == WarmRxRefresh::ContextError);
  assert(w._warm_refresh.last_failed_step == Step::Context);
  assert(w._warm_refresh.successes == 0 && w._warm_refresh.warm_gap.count == 0);
  assert(!radio._profileSwitch.rxValid && !radio._profileSwitch.open);
  unchangedCollector(w);

  WarmRxRefresh diagnostic;
  diagnostic.request(10);
  diagnostic.transition(20, false);
  assert(diagnostic.finish(WarmRxRefresh::Outcome::Applied, Reason::None, 30) == WarmRxRefresh::Outcome::Failed);
  assert(diagnostic.successes == 0 && diagnostic.failures == 1);
  assert(diagnostic.last_error == WarmRxRefresh::ContextError);
}

static void legacy() {
  CustomSX1262Wrapper w;
  prime(w);
  assert(w.getOldAGCInterval() == 0);
  assert(w.periodicOldAGCMaintenance() == Result::Applied && w.old_resets == 1);
  w.startRecv();
  assert(w.periodicOldAGCMaintenance() == Result::Applied && w.old_resets == 2);
  w.startRecv();
  w.chip.trace.clear();
  assert(w._warm_refresh.requests == 0);
  assert(w.setOldAGCInterval(0) && w.getOldAGCInterval() == 0);
  assert(w.periodicAGCMaintenance(false) == Result::Applied);
  assert(w.old_resets == 2 && w._warm_refresh.successes == 1);
  assert(!w.setOldAGCInterval(86401) && w.getOldAGCInterval() == 0);
  assert(w.setOldAGCInterval(86400) && w.getOldAGCInterval() == 86400);
  RadioLibWrapper unsupported;
  assert(!unsupported.supportsWarmRxRefresh() && unsupported.getOldAGCInterval() == 0);
  assert(!unsupported.setOldAGCInterval(600));
  assert(unsupported.periodicAGCMaintenance(false) == Result::Applied);
  assert(unsupported.old_resets == 1 && unsupported._warm_refresh.requests == 0);
  for (unsigned guard = 0; guard < 5; ++guard) {
    CustomSX1262Wrapper owned;
    prime(owned);
    if (guard == 0) owned._beacon_owned = true;
    if (guard == 1) owned._cw_active = true;
    if (guard == 2) owned.packet = true;
    if (guard == 3) { owned.events.record(); owned.chip.pending_fifo = true; }
    if (guard == 4) owned.state = STATE_TX_WAIT;
    assert(owned.periodicOldAGCMaintenance() == Result::Deferred);
    assert(owned.old_resets == 0 && owned.chip.trace.empty());
    unchangedCollector(owned);
    if (guard == 3) assert(owned.hasPendingRadioInterrupt() && owned.chip.pending_fifo);
  }
}

@SCHEDULER_CASES@
@LAB_TIMER_CASES@

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string scenario = argv[1];
  if (scenario == "success") success();
  else if (scenario == "guards") guards();
  else if (scenario == "ownership") ownership();
  else if (scenario == "caches") caches();
  else if (scenario == "failures") failures();
  else if (scenario == "noop") noOp();
  else if (scenario == "legacy") legacy();
  else if (scenario == "scheduler") scheduler();
#if MESH_WARM_RX_LAB_TIMER
  else if (scenario == "lab-cadence") labTimerCadence();
  else if (scenario == "lab-changes") labTimerChanges();
  else if (scenario == "lab-deferral") labTimerDeferralAndIndependence();
  else if (scenario == "lab-unsupported") labTimerUnsupported();
#endif
  else assert(false);
  printf("PASS: %s\n", scenario.c_str());
}
