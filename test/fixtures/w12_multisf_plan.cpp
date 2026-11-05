#include <assert.h>
#include "helpers/radiolib/W12MultiSfPlan.h"

int main() {
  using namespace mesh::w12multisf;
  Plan plan;
  assert(makePlan(true, 5, 8, 7, 62.5f, false, false, false, plan) == Admission::Ready);
  assert(plan.rx_primary == 5 && plan.tx_sf == 7 && plan.side_count == 3);
  assert(plan.side_sf[0] == 6 && plan.side_sf[1] == 7 && plan.side_sf[2] == 8);
  assert(makePlan(true, 6, 9, 7, 250, false, false, false, plan) == Admission::Ready);
  assert(plan.rx_primary == 6 && plan.tx_sf == 7 && plan.side_sf[2] == 9);
  assert(makePlan(true, 7, 10, 7, 500, false, false, false, plan) == Admission::Ready);
  assert(makePlan(true, 5, 8, 7, 812.5, false, false, false, plan) == Admission::InvalidBandwidth);
  assert(plan.side_count == 0);
  assert(makePlan(true, 10, 12, 7, 62.5, false, false, false, plan) == Admission::UnsupportedRange);
  assert(makePlan(true, 5, 5, 7, 0, false, false, false, plan) == Admission::InvalidBandwidth);
  assert(makePlan(true, 5, 5, 7, 44, false, false, false, plan) == Admission::InvalidBandwidth);
  assert(makePlan(true, 5, 9, 7, 62.5, false, false, false, plan) == Admission::InvalidRange);
  assert(makePlan(true, 8, 5, 7, 62.5, false, false, false, plan) == Admission::InvalidRange);
  assert(makePlan(false, 5, 8, 7, 62.5, false, false, false, plan) == Admission::Unsupported);
  assert(makePlan(true, 5, 8, 7, 62.5, true, false, false, plan) == Admission::DualRadio);
  assert(makePlan(true, 5, 8, 7, 62.5, false, true, false, plan) == Admission::RxPowerSaving);
  assert(makePlan(true, 5, 8, 7, 62.5, false, false, true, plan) == Admission::ExtraDetectors);
}
