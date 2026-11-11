#if MESH_WARM_RX_LAB_TIMER
static void labTimerCadence() {
  for (uint32_t started : {0U, 0xfffffff0U}) {
    for (uint32_t interval : {1000U, 4000U}) {
      now_ms = started;
      CustomSX1262Wrapper radio;
      prime(radio);
      Dispatcher node(radio);
      node._prefs.agc_reset_interval = 7;
      node.adaptive_mode = true;
      uint32_t value = UINT32_MAX, wake = UINT32_MAX;
      assert(!radio.getWarmRxLabInterval(value));
      assert(radio.setWarmRxLabInterval(true, interval));
      assert(radio.getWarmRxLabInterval(value) && value == interval);
      assert(radio.chip.trace.empty() && radio._warm_refresh.requests == 0);
      node.service();
      assert(node.agc_reset.interval() == interval);
      assert(node.agc_reset.nextAt() == started + interval);
      assert(node.oldMaintenanceWake(wake) && wake == interval);
      for (unsigned attempt = 0; attempt < 4; ++attempt) {
        const uint32_t deadline = node.agc_reset.nextAt();
        now_ms = deadline - 1;
        node.service();
        assert(radio._warm_refresh.successes == attempt);
        assert(node.oldMaintenanceWake(wake) && wake == 1);
        now_ms = deadline + 1;
        node.service();
        assert(radio._warm_refresh.successes == attempt + 1);
        assert(node.agc_reset.interval() == interval);
        assert(node.agc_reset.nextAt() == now_ms + interval);
        assert(node.oldMaintenanceWake(wake) && wake == interval);
      }
      assert(node.getAGCResetInterval() == 28000 && node._prefs.agc_reset_interval == 7);
      assert(radio.old_resets == 0 && node.old_agc_reset.interval() == 0);
      unchangedCollector(radio);
    }
  }
}

static void labTimerChanges() {
  for (uint32_t started : {0U, 0xfffffff0U}) {
    now_ms = started;
    CustomSX1262Wrapper radio;
    prime(radio);
    Dispatcher node(radio);
    node._prefs.agc_reset_interval = 1;
    node.service();
    assert(node.agc_reset.interval() == 4000);
    now_ms = started + 3000;
    const uint32_t initial_revision = radio.warmRxLabTimerRevision();
    assert(radio.setWarmRxLabInterval(true, 4000));
    assert(radio.warmRxLabTimerRevision() != initial_revision);
    assert(radio._warm_refresh.requests == 0 && radio.chip.trace.empty());
    node.service();
    assert(node.agc_reset.nextAt() == now_ms + 4000);
    const uint32_t active_revision = radio.warmRxLabTimerRevision();
    now_ms += 100;
    assert(radio.setWarmRxLabInterval(true, 4000));
    assert(radio.warmRxLabTimerRevision() == active_revision);
    node.service();
    assert(node.agc_reset.nextAt() == started + 7000);
    assert(radio.setWarmRxLabInterval(true, 1000));
    node.service();
    assert(node.agc_reset.interval() == 1000 && node.agc_reset.nextAt() == now_ms + 1000);
    now_ms += 900;
    assert(radio.setWarmRxLabInterval(false, 0));
    node.service();
    assert(node.agc_reset.interval() == 4000 && node.agc_reset.nextAt() == now_ms + 4000);
    assert(radio._warm_refresh.requests == 0);
    // A numeric zero is an active override even when the saved timer is on.
    assert(radio.setWarmRxLabInterval(true, 0));
    node.service();
    uint32_t value = UINT32_MAX, wake = UINT32_MAX;
    assert(radio.getWarmRxLabInterval(value) && value == 0);
    assert(node.agc_reset.interval() == 0 && !node.oldMaintenanceWake(wake));
    now_ms += 60000;
    node.service();
    assert(radio._warm_refresh.requests == 0);
    assert(radio.setWarmRxLabInterval(false, 0));
    node.service();
    assert(node.agc_reset.interval() == 4000 && node.agc_reset.nextAt() == now_ms + 4000);
    // Clearing the override also restores the original adaptive policy.
    node.adaptive_mode = true;
    node.service();
    for (unsigned attempt = 0; attempt < 2; ++attempt) {
      now_ms = node.agc_reset.nextAt() + 1;
      node.service();
    }
    assert(node.agc_reset.interval() == 8000 && radio._warm_refresh.successes == 2);
    assert(radio.setWarmRxLabInterval(true, 4000));
    node.service();
    assert(node.agc_reset.interval() == 4000 && node.agc_reset.nextAt() == now_ms + 4000);
    assert(radio.setWarmRxLabInterval(false, 0));
    node.service();
    assert(node.agc_reset.interval() == 4000 && node.agc_reset.nextAt() == now_ms + 4000);
    unchangedCollector(radio);
  }
}

static void labTimerDeferralAndIndependence() {
  for (uint32_t started : {0U, 0xfffffff0U}) {
    now_ms = started;
    CustomSX1262Wrapper radio;
    prime(radio);
    assert(radio.setOldAGCInterval(600));
    assert(radio.setWarmRxLabInterval(true, 1000));
    Dispatcher node(radio);
    node._prefs.agc_reset_interval = 7;
    node.service();
    const uint32_t old_deadline = started + 600000;
    assert(node.old_agc_reset.nextAt() == old_deadline);
    now_ms = started + 1001;
    radio.busy = true;
    node.service();
    assert(radio._warm_refresh.requests == 1 && radio._warm_refresh.deferrals == 1);
    assert(node.agc_reset.nextAt() == now_ms + 250);
    uint32_t wake;
    assert(node.oldMaintenanceWake(wake) && wake == 250);
    const uint32_t deferred = now_ms;
    now_ms = deferred + 249;
    node.service();
    assert(radio._warm_refresh.requests == 1);
    radio.busy = false;
    now_ms = deferred + 251;
    node.service();
    assert(radio._warm_refresh.successes == 1 && radio._warm_refresh.requests == 2);
    assert(node.agc_reset.nextAt() == now_ms + 1000);
    assert(node.old_agc_reset.nextAt() == old_deadline && radio.getOldAGCInterval() == 600);
    unchangedCollector(radio);
    assert(radio.setWarmRxLabInterval(true, 0));
    node.service();
    assert(node.agc_reset.interval() == 0 && node.old_agc_reset.nextAt() == old_deadline);
    assert(node.oldMaintenanceWake(wake) && wake == old_deadline - now_ms);
    now_ms = old_deadline;
    node.service();
    assert(radio.old_resets == 1 && radio._warm_refresh.requests == 2);
    assert(node.old_agc_reset.nextAt() == now_ms + 600000 && node.agc_reset.interval() == 0);
    assert(radio.floor_resets == 1); // The independent old method deliberately relearns the floor.
  }
}

static void labTimerUnsupported() {
  now_ms = 0;
  RadioLibWrapper radio;
  assert(!radio.supportsWarmRxRefresh());
  uint32_t value = 123;
  assert(!radio.setWarmRxLabInterval(true, 1000));
  assert(!radio.setWarmRxLabInterval(false, 0));
  assert(!radio.getWarmRxLabInterval(value) && value == 123);
  assert(radio.warmRxLabTimerRevision() == 0);
  Dispatcher node(radio);
  node._prefs.agc_reset_interval = 1;
  node.service();
  assert(node.agc_reset.interval() == 4000);
  now_ms = 4001;
  node.service();
  assert(radio.old_resets == 1 && radio._warm_refresh.requests == 0);
}
#endif
