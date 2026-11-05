#include <cassert>
#include "helpers/radiolib/W12PramSequentialLab.h"
using namespace mesh::w12pram;
int main() {
  Profile profiles[2] = {{904525000,62500,7,5,32,11}, {904850000,250000,7,5,32,12}};
  SequentialPlan plan;
  assert(sequentialPlan(profiles,0,5,8,plan) && plan.count==5);
  for (uint8_t i=0;i<4;++i) {
    assert(plan.slots[i].logical_profile==0 && plan.slots[i].physical.sf==5+i);
    assert(plan.slots[i].packed.frequency==904525000);
  }
  assert(plan.slots[4].logical_profile==1 && plan.slots[4].physical.sf==7);
  SequentialSlot hit;
  assert(sequentialHit(plan,0,11,12,hit) && hit.logical_profile==0 && hit.physical.sf==5);
  assert(sequentialHit(plan,3,11,12,hit) && hit.physical.sf==8);
  assert(sequentialHit(plan,4,11,12,hit) && hit.logical_profile==1 && hit.physical.sf==7);
  assert(!sequentialHit(plan,5,11,12,hit) && !sequentialHit(plan,0,10,12,hit));
  assert(!sequentialHit(plan,0,11,13,hit) && !sequentialHit(plan,4,10,12,hit));
  Status status={}; status.hooked=1; status.state=4; status.last_hit=4; status.irq=1u<<18;
  assert(classify(status)==RxEvent::Invalid && classify(status,plan.count)==RxEvent::Deliver);
  status.last_hit=5; assert(classify(status,plan.count)==RxEvent::Invalid);
  assert(sequentialPlan(profiles,1,5,8,plan) && plan.count==5);
  assert(plan.slots[0].logical_profile==1 && plan.slots[0].physical.bw_hz==250000);
  assert(plan.slots[4].logical_profile==0 && plan.slots[4].physical.bw_hz==62500);
  assert(!sequentialPlan(profiles,0,4,8,plan) && !plan.count);
  assert(!sequentialPlan(profiles,0,5,9,plan) && !plan.count);
  assert(sequentialPlan(profiles,0,6,9,plan) && plan.count==5);
  for (uint8_t i=0;i<4;++i) assert(plan.slots[i].physical.sf==6+i);
  assert(sequentialPlan(profiles,1,7,10,plan) && plan.count==5);
  assert(plan.slots[3].physical.sf==10 && plan.slots[3].logical_profile==1);
  assert(!sequentialPlan(profiles,0,6,10,plan) && !plan.count);
  assert(!sequentialPlan(profiles,0,11,11,plan) && !plan.count);
  profiles[1].freq_hz=924525000;
  assert(!sequentialPlan(profiles,0,5,8,plan) && !plan.count);
}
