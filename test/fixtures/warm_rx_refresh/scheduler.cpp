static void scheduler() {
  // The actual saved-byte getter retains its four-second representation.
  for (uint8_t units : {uint8_t(4), uint8_t(7), uint8_t(15)}) {
    for (uint32_t started : {0U, 0xfffffff0U}) {
      now_ms = started;
      CustomSX1262Wrapper w;
      prime(w);
      assert(w.setOldAGCInterval(0));
      Dispatcher node(w);
      node._prefs.agc_reset_interval = units;
      const uint32_t interval = uint32_t(units) * 4000;
      assert(uint32_t(node.getAGCResetInterval()) == interval);
      node.service();
      assert(node.agc_reset.interval() == interval && node.old_agc_reset.interval() == 0);
      assert(node.agc_reset.nextAt() == started + interval);
      now_ms = started + interval - 1;
      node.service();
      assert(w._warm_refresh.requests == 0);
      now_ms = started + interval + 1; // Existing fixed-mode strict deadline comparison.
      w.busy = true;
      node.service();
      assert(w._warm_refresh.requests == 1 && w._warm_refresh.deferrals == 1);
      assert(w._warm_refresh.successes == 0 && w.chip.trace.empty());
      assert(node.agc_reset.interval() == interval);
      assert(node.agc_reset.nextAt() == now_ms + 250);
      const uint32_t deferred = now_ms;
      now_ms = deferred + 249;
      node.service();
      assert(w._warm_refresh.requests == 1);
      w.busy = false;
      now_ms = deferred + 251;
      node.service();
      assert(w._warm_refresh.requests == 2 && w._warm_refresh.successes == 1);
      assert(node.agc_reset.nextAt() == now_ms + interval);
      unchangedCollector(w);
    }
  }

  for (uint32_t started : {0U, 0xfffffff0U}) {
    now_ms = started;
    CustomSX1262Wrapper w;
    prime(w);
    Dispatcher node(w);
    node.service();
    uint32_t wake;
    assert(node.agc_reset.interval() == 0 && node.old_agc_reset.interval() == 600000);
    assert(node.old_agc_reset.nextAt() == started + 600000);
    assert(node.oldMaintenanceWake(wake) && wake == 600000);
    now_ms = started + 599999;
    node.service();
    assert(node.oldMaintenanceWake(wake) && wake == 1);
    assert(w.old_resets == 0 && w._warm_refresh.requests == 0);
    now_ms = started + 600000;
    node.service();
    assert(w.old_resets == 1 && w._warm_refresh.requests == 0);
    assert(node.old_agc_reset.interval() == 600000);
    assert(node.old_agc_reset.nextAt() == now_ms + 600000);
    now_ms = started + 6000000;
    node.service();
    assert(w.old_resets == 2); // One accepted attempt, with no catch-up loop.
    assert(node.old_agc_reset.nextAt() == now_ms + 600000);
    node.service();
    assert(w.old_resets == 2);
  }

  now_ms = 0;
  CustomSX1262Wrapper simultaneous;
  prime(simultaneous);
  Dispatcher node(simultaneous);
  node._prefs.agc_reset_interval = 15; // Main interval remains 60 seconds.
  node.service();
  now_ms = 600000;
  node.service();
  assert(simultaneous.old_resets == 1 && simultaneous._warm_refresh.requests == 0);
  assert(node.old_agc_reset.nextAt() == 1200000 && node.agc_reset.nextAt() == 600250);
  assert(node._prefs.agc_reset_interval == 15 && node.getAGCResetInterval() == 60000);
  now_ms = 600249;
  node.service();
  assert(simultaneous._warm_refresh.requests == 0);
  now_ms = 600251;
  simultaneous.startRecv(); // Ordinary receive processing re-arms after the old reset.
  node.service();
  assert(simultaneous._warm_refresh.successes == 1 && simultaneous.old_resets == 1);
  assert(node.agc_reset.nextAt() == 660251);

  now_ms = 0;
  CustomSX1262Wrapper disabled;
  prime(disabled);
  disabled.setOldAGCInterval(0);
  Dispatcher off(disabled);
  off.service();
  now_ms = 2000000;
  off.service();
  assert(disabled.old_resets == 0 && disabled._warm_refresh.requests == 0);
  assert(off.agc_reset.interval() == 0 && off.old_agc_reset.interval() == 0);
  uint32_t wake;
  assert(!off.oldMaintenanceWake(wake));

  now_ms = 0;
  CustomSX1262Wrapper recovered;
  prime(recovered);
  Dispatcher recovery(recovered);
  recovery.service();
  now_ms = 600000;
  recovery.recovered_this_loop = true;
  recovery.service();
  assert(recovered.old_resets == 0 && recovered._warm_refresh.requests == 0);
  assert(recovery.old_agc_reset.nextAt() == now_ms + 250);
  assert(recovery.oldMaintenanceWake(wake) && wake == 250);
  recovery.recovered_this_loop = false;
  now_ms += 249;
  recovery.service();
  assert(recovered.old_resets == 0);
  ++now_ms;
  recovery.service();
  assert(recovered.old_resets == 1 && recovered._warm_refresh.requests == 0);

  now_ms = 0;
  CustomSX1262Wrapper fixed_recovered;
  prime(fixed_recovered);
  fixed_recovered.setOldAGCInterval(0);
  Dispatcher fixed(fixed_recovered);
  fixed._prefs.agc_reset_interval = 4;
  fixed.service();
  now_ms = 16001;
  fixed.recovered_this_loop = true;
  fixed.service();
  assert(fixed_recovered.old_resets == 0 && fixed_recovered._warm_refresh.requests == 0);
  assert(fixed.agc_reset.nextAt() == now_ms + 250 && fixed.agc_reset.interval() == 16000);
}
