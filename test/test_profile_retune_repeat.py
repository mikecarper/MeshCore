"""Full HIL retune repeat is real, bounded, and never overrides RX guards."""
from pathlib import Path
import unittest
from test_radio_receive_contract import method
import test_sx1262_batched_modulation as compiler

ROOT=Path(__file__).resolve().parents[1]


class RetuneRepeatTests(unittest.TestCase):
    compile_run=compiler.BatchedModulationTests.compile_run

    def test_second_pass_refresh_and_fail_closed(self):
        body=method((ROOT/'tools/hil/profile_switch.cpp').read_text(),'mesh::RadioParamApplyResult hop(')
        self.compile_run(r'''
#include <cassert>
#include <cstdint>
namespace mesh {
 enum class RadioParamApplyResult {APPLIED,BUSY,FAILED};
 struct RadioProfileParams { float freq=910.5f;uint8_t cr=5; };
}
using Result=mesh::RadioParamApplyResult;
struct { uint32_t rfWord=0,modulationWord=0; } channelTrace;
uint32_t micros(){static uint32_t t=0;return ++t;}
struct { unsigned begins=0,ends=0;bool busyLine=false;bool isChipBusy(){return busyLine;}void beginHop(bool rx){assert(rx);++begins;}void endHop(){++ends;} } chip;
struct Bench {
 unsigned hopPasses=1,calls=0;
 uint32_t firstPassOffsetSteps=10;
 bool firstPassDetour=false,corruptFirst=false,corruptFinal=false;
 uint32_t detourChecks=0,detourErrors=0,detourFirstRf=0,detourFinalRf=0,detourFirstMod=0,detourFinalMod=0;
 struct Profiles {
  mesh::RadioProfileParams primary;
  struct Secondary { mesh::RadioProfileParams params; } secondary;
  const mesh::RadioProfileParams& params(uint8_t target){return target?secondary.params:primary;}
  void setPrimary(const mesh::RadioProfileParams& p,bool){primary=p;}
  void setSecondary(const Secondary& p,bool){secondary=p;}
 } _profiles;
 uint32_t lastFirstPassUs=0,lastSecondPassUs=0,secondPassBlocked=0;
 bool _profile_refresh_required=false;
 Result first=Result::APPLIED,second=Result::APPLIED;
 bool isInRecvMode(){return true;}
 Result tuneProfile(uint8_t target){
  assert(target<=1);++calls;if(calls==2)assert(_profile_refresh_required);
  const auto& p=_profiles.params(target);
  assert(p.freq==910.5f);
  assert(p.cr==(firstPassDetour&&calls==1?6:5));
  Result r=calls==1?first:second;
  if(r==Result::APPLIED){
   channelTrace.rfWord=uint32_t(double(p.freq)*1048576.0)+(firstPassDetour&&calls==1?firstPassOffsetSteps:0);
   channelTrace.modulationWord=p.cr==6?0x0a040200:0x0a040100;
   if((calls==1&&corruptFirst)||(calls==2&&corruptFinal))channelTrace.modulationWord^=0x100;
  }
  return r;
 }
 @METHOD@
};
int main(){
 Bench one;assert(one.hop(1)==Result::APPLIED&&one.calls==1&&one.lastSecondPassUs==0);
 Bench two;two.hopPasses=2;assert(two.hop(1)==Result::APPLIED&&two.calls==2&&two.lastSecondPassUs>0);
 Bench busy;busy.hopPasses=2;busy.first=Result::BUSY;assert(busy.hop(1)==Result::BUSY&&busy.calls==1);
 Bench failed;failed.hopPasses=2;failed.first=Result::FAILED;assert(failed.hop(1)==Result::FAILED&&failed.calls==1);
 Bench mid;mid.hopPasses=2;mid.second=Result::BUSY;assert(mid.hop(1)==Result::FAILED&&mid.calls==2&&mid.secondPassBlocked==1);
 Bench end;end.hopPasses=2;end.second=Result::FAILED;assert(end.hop(1)==Result::FAILED&&end.calls==2);
 Bench stalled;stalled.hopPasses=2;chip.busyLine=true;assert(stalled.hop(1)==Result::FAILED&&stalled.calls==1);
 chip.busyLine=false;
 for(unsigned target=0;target<2;++target){
  Bench detour;detour.hopPasses=2;detour.firstPassDetour=true;
  assert(detour.hop(target)==Result::APPLIED&&detour.calls==2&&detour.detourChecks==1&&detour.detourErrors==0);
  assert(detour._profiles.params(target).freq==910.5f&&detour._profiles.params(target).cr==5);
  Bench hundred;hundred.hopPasses=2;hundred.firstPassDetour=true;hundred.firstPassOffsetSteps=105;
  assert(hundred.hop(target)==Result::APPLIED&&hundred.detourChecks==1&&hundred.detourErrors==0);
  assert(hundred.detourFirstRf-hundred.detourFinalRf==105);
  Bench deferred;deferred.hopPasses=2;deferred.firstPassDetour=true;deferred.first=Result::BUSY;
  assert(deferred.hop(target)==Result::BUSY&&deferred.calls==1);
  assert(deferred._profiles.params(target).freq==910.5f&&deferred._profiles.params(target).cr==5);
  Bench between;between.hopPasses=2;between.firstPassDetour=true;between.second=Result::BUSY;
  assert(between.hop(target)==Result::FAILED&&between.secondPassBlocked==1);
  assert(between._profiles.params(target).freq==910.5f&&between._profiles.params(target).cr==5);
  for(unsigned pass=0;pass<2;++pass){
   Bench bad;bad.hopPasses=2;bad.firstPassDetour=true;bad.corruptFirst=pass==0;bad.corruptFinal=pass==1;
   assert(bad.hop(target)==Result::FAILED&&bad.detourErrors==1);
  }
 }
 assert(chip.begins==chip.ends);
}
'''.replace('@METHOD@',body))

    def test_complete_settings_are_not_skipped_by_modulation_cache(self):
        source=(ROOT/'tools/hil/profile_switch.cpp').read_text()
        self.assertIn('forceModulationWrite || hopPasses==2',method(source,'void beginProfileRetune('))
        self.assertIn('chip.hilInvalidateModulation()',method(source,'void beginProfileRetune('))


if __name__=='__main__': unittest.main()
