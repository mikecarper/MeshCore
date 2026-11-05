#include <assert.h>
#include "helpers/radiolib/W12MultiSfTransaction.h"

using namespace mesh::w12multisf;

struct FakeRadio {
  Transition admission = Transition::Applied;
  int fail_at = 0, calls = 0, writes = 0;
  bool restored = true, committed = false;
  uint8_t sf = 7, sides = 0;
  uint8_t saved_sf = 0, saved_sides = 0;
  Transition begin() { saved_sf = sf; saved_sides = sides; return admission; }
  bool step() { ++writes; return ++calls != fail_at; }
  bool setSpreadingFactor(uint8_t value) { if (!step()) return false; sf = value; return true; }
  bool setDetectors(const uint8_t*, uint8_t count) { if (!step()) return false; sides = count; return true; }
  bool startReceive() { return step(); }
  bool prepareTransmit() { return step(); }
  void commit() { committed = true; }
  bool rollback() { if (!restored) return false; sf = saved_sf; sides = saved_sides; return true; }
};

int main() {
  Plan plan;
  assert(makePlan(true, 5, 8, 7, 62.5f, false, false, false, plan) == Admission::Ready);
  FakeRadio busy; busy.admission = Transition::Busy;
  assert(setReceive(busy, plan) == Transition::Busy && busy.writes == 0);
  FakeRadio receive;
  assert(setReceive(receive, plan) == Transition::Applied);
  assert(receive.committed && receive.sf == 5 && receive.sides == 3);
  FakeRadio tx;
  assert(prepareTransmit(tx, plan) == Transition::Applied);
  assert(tx.committed && tx.sf == 7 && tx.sides == 0);
  for (int failure = 1; failure <= 3; ++failure) {
    FakeRadio a; a.fail_at = failure;
    assert(setReceive(a, plan) == Transition::Failed);
    assert(!a.committed && a.sf == 7 && a.sides == 0);
    FakeRadio normal_tx_failure; normal_tx_failure.sf = 5; normal_tx_failure.sides = 3;
    normal_tx_failure.fail_at = failure;
    assert(prepareTransmit(normal_tx_failure, plan) == Transition::Failed);
    assert(normal_tx_failure.sf == 5 && normal_tx_failure.sides == 3);
    FakeRadio b; b.fail_at = failure; b.restored = false;
    assert(prepareTransmit(b, plan) == Transition::RecoveryRequired);
    assert(!b.committed);
  }
}
