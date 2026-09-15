#pragma once
#include <stdint.h>
// HIL-only SF7/62.5 + SF8/500 plan. Explicit 32 on both transmitters and RX;
// no inherited 88-symbol override. Same asymmetric visits as production with
// a nominal 600 us/hop and bounded loop allowances (not added delays).
struct PairProfile { unsigned sf; float bw; };
static constexpr PairProfile pairProfiles[2]={{7,62.5f},{8,500.0f}};
static constexpr bool pairPlanSupported(unsigned loopUs,unsigned slowExtraUs) {
  return (loopUs==0 && (slowExtraUs==0 || slowExtraUs==2048))
      || (loopUs==4000 && slowExtraUs==0)
      || (loopUs==300 && slowExtraUs==1024);
}
static constexpr uint32_t pairListenUs(unsigned channel,unsigned loopUs,unsigned slowExtraUs=0) {
  return channel==0 ? 8397+slowExtraUs : 32768-8397-1200-loopUs-slowExtraUs;
}
static constexpr const char* pairInfo=
  "{\"pair_plan\":1,\"case_4p6_300us\":true,\"preamble\":32,\"switch_budget_us\":600,\"floor_chirps\":4.1,\"profiles\":[{\"sf\":7,\"bw_khz\":62.5,\"freq_khz\":909500},{\"sf\":8,\"bw_khz\":500,\"freq_khz\":910500}]}";
