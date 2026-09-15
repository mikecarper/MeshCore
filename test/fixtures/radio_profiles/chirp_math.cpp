#include <RadioProfiles.h>
#include <cassert>
#include <cmath>
#include <utility>

using Profiles=mesh::RadioProfiles;
mesh::RadioProfileParams params(unsigned sf,float bw) {
  mesh::RadioProfileParams p; p.freq=909.5;p.sf=sf;p.bw=bw;p.cr=5;return p;
}
int main() {
  assert(Profiles::MinListenSymbols==4.6);
  assert(Profiles::SlowListenSymbols==4.6);
  assert(Profiles::MinFastListenSymbols==4.6);
  assert(Profiles::SwitchBudgetUs==600);
  assert(Profiles::LoopBudgetUs==300);
  auto a=params(10,125),b=params(10,125);
  auto t=Profiles::calculateChirpTiming(a,b,1,0,1000,4000);
  assert(t.valid && t.slow==0 && t.symbol_us[0]==8192);
  assert(t.listen_us[0]==37684 && t.listen_us[1]==37684);
  assert(t.preamble[0]==32 && t.preamble[1]==32);
  // The function accepts board-specific budgets; a V4 measurement must not
  // become a universal allowance for the GPIO-expander Indicator.
  a=params(6,125);b=a;
  auto fast=Profiles::calculateChirpTiming(a,b,0,0,1000,4000);
  auto indicator=Profiles::calculateChirpTiming(a,b,0,0,9000,4000);
  assert(indicator.preamble[0]>fast.preamble[0]);
  assert(indicator.preamble[1]>fast.preamble[1]);
  assert(fast.listen_us[0]>=4.6*512 && fast.listen_us[1]>=4.6*512);
  b.bw=0;assert(!Profiles::calculateChirpTiming(a,b).valid);
  b.bw=NAN;assert(!Profiles::calculateChirpTiming(a,b).valid);
  b=params(13,125);assert(!Profiles::calculateChirpTiming(a,b).valid);
  b=params(10,1e-30f);assert(Profiles::minimumListenUs(b)==0);
  a=params(7,62.5);b=params(8,500);
  t=Profiles::calculateChirpTiming(a,b);
  assert(t.preamble[1]==88); // Existing measured acquisition floor retained.
  auto swapped=Profiles::calculateChirpTiming(b,a);
  assert(swapped.slow==1 && swapped.preamble[0]==88);
  assert(swapped.preamble[1]==t.preamble[0]);
  for (float bw0: {7.8f,62.5f,125.0f,250.0f,500.0f})
    for (float bw1: {7.8f,62.5f,125.0f,250.0f,500.0f})
      for (unsigned sf0=5;sf0<=12;++sf0) for(unsigned sf1=5;sf1<=12;++sf1) {
        Profiles p;p.primary=params(sf0,bw0);p.secondary.params=params(sf1,bw1);
        p.secondary.mode=mesh::RadioProfileMode::Rx;
        auto minimum=Profiles::calculateChirpTiming(p.primary,p.secondary.params);
        assert(minimum.valid);
        for(unsigned i=0;i<2;++i) {
          const double symbols=4.6;
          assert(minimum.listen_us[i]==std::ceil(symbols*minimum.symbol_us[i]));
          assert(minimum.preamble[i]>=32 && std::fmod(minimum.preamble[i],8)==0);
          assert(p.automaticPreamble(i)==minimum.preamble[i]);
        }
        if (!p.automaticPreambleFits()) continue;
        auto actual=p.chirpTiming();
        assert(actual.valid);
        assert(p.listenUs(actual.slow)==Profiles::minimumListenUs(p.params(actual.slow),true));
        assert(p.listenUs(actual.slow^1)>=Profiles::minimumListenUs(p.params(actual.slow^1)));
        const double available=std::floor(p.preamble(actual.slow,32)*actual.symbol_us[actual.slow]/2
            -p.listenUs(actual.slow)-2*Profiles::SwitchBudgetUs-Profiles::LoopBudgetUs);
        const uint32_t fast_floor=Profiles::minimumListenUs(p.params(actual.slow^1));
        assert(p.listenUs(actual.slow^1)==(available>fast_floor ? (uint32_t)available : fast_floor));
        assert(2*actual.cycle_us<=p.preamble(actual.slow,32)*actual.symbol_us[actual.slow]+1e-6);
        assert(actual.preamble[0]<=p.preamble(0,32));
        assert(actual.preamble[1]<=p.preamble(1,32));
      }
  Profiles p;p.primary=params(7,62.5);p.secondary.params=params(7,500);
  p.secondary.mode=mesh::RadioProfileMode::Rx;
  assert(p.listenUs(0)==9421 && p.listenUs(1)==21847);
  assert(p.chirpTiming().switch_us==600 && p.chirpTiming().loop_us==300 && p.chirpTiming().preamble[1]==64);
  auto before=p.chirpTiming();const auto wire0=p.preamble(0,32),wire1=p.preamble(1,32);
  p.longest_switch_us=8428;auto overrun=p.chirpTiming();
  assert(overrun.switch_us==8428 && overrun.preamble[0]>before.preamble[0]);
  assert(p.preamble(0,32)==wire0 && p.preamble(1,32)==wire1);
  p.longest_switch_us=0;p.primary.preamble=64;
  assert(p.chirpTiming().preamble[0]==64 && p.listenUs(1)>21847);
  p.secondary.params.preamble=32;
  assert(p.preamble(1,32)==32 && p.chirpTiming().preamble[1]>32);
  p.secondary.mode=mesh::RadioProfileMode::Off;
  assert(!p.chirpTiming().valid);
  assert(p.listenUs(0)==9421); // Same minimum helper; single-profile RX does not scan.
  // Exact tested SF8/500 pair, explicit 32 preserved; order is not hard-coded.
  p.primary=params(7,62.5);p.primary.preamble=32;
  p.secondary.params=params(8,500);p.secondary.params.preamble=32;
  p.secondary.mode=mesh::RadioProfileMode::Rx;
  assert(p.automaticPreambleFits() && p.listenUs(0)==9421 && p.listenUs(1)==21847);
  assert(p.preamble(0,32)==32 && p.preamble(1,32)==32);
  assert(p.chirpTiming().preamble[1]==88);
  std::swap(p.primary,p.secondary.params);
  assert(p.slowerProfile()==1 && p.listenUs(1)==9421 && p.listenUs(0)==21847);
  p.secondary.params.preamble=8;
  assert(!p.automaticPreambleFits());
  // This formerly fit the 4.1 fast floor (1050 us), but not 4.6 (1178 us).
  p.primary=params(7,500);p.secondary.params=params(7,500);
  p.primary.preamble=30;
  assert(p.listenUs(0)==1178 && p.listenUs(1)>=1178);
  assert(!p.automaticPreambleFits());
  p.primary.preamble=32;
  assert(p.automaticPreambleFits() && p.listenUs(1)==1418);
  // Long requested visits remain visible, not replaced by policy minima.
  t=Profiles::calculateChirpTiming(params(7,62.5),params(8,500),20000,30000);
  assert(t.listen_us[0]==20000 && t.listen_us[1]==30000 && t.cycle_us==51500);
}
