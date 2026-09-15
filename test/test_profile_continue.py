"""Fixed-sample HIL continuation never erases misses or resets the scanner."""
from pathlib import Path
import unittest
from test_radio_receive_contract import method
import test_sx1262_batched_modulation as compiler

ROOT=Path(__file__).resolve().parents[1]

class ContinueTests(unittest.TestCase):
    compile_run=compiler.BatchedModulationTests.compile_run

    def test_completion_changes_only_expectation_counts_and_stop_policy(self):
        source=(ROOT/'tools/hil/profile_switch_channels.h').read_text()
        body=method(source,'void completeChannelExpectation(')
        self.compile_run(r'''
#include <cassert>
bool channelContinueOnMiss=false;
struct {bool active=true,expecting=true;unsigned received=0,missed=0;} channelSweep;
@METHOD@
int main(){
 completeChannelExpectation(false);
 assert(!channelSweep.active&&!channelSweep.expecting&&channelSweep.missed==1);
 channelSweep={};channelContinueOnMiss=true;
 for(unsigned i=0;i<400;++i){
  channelSweep.expecting=true;completeChannelExpectation(i%4==0);
  assert(channelSweep.active&&!channelSweep.expecting);
 }
 assert(channelSweep.received==100&&channelSweep.missed==300);
}
'''.replace('@METHOD@',body))
        service=method(source,'void serviceChannelSweep(')
        self.assertIn('completeChannelExpectation(accepted)',service)
        self.assertIn('completeChannelExpectation(false)',service)
        self.assertIn('++channelSweep.failures;channelSweep.active=false',service)

    def test_expectations_preserve_scanner_phase_and_use_mode_timeout(self):
        source=(ROOT/'tools/hil/profile_switch_channels.h').read_text()
        self.compile_run(r'''
#include <cassert>
#include <cstdint>
bool channelPair=false;
uint32_t nowMs=0xfffffff0u;
uint32_t millis(){return nowMs;}
uint32_t micros(){return 900;}
struct {
 bool active=true,expecting=false;
 unsigned channels=2,current=1,expectedChannel=0;
 uint32_t sequence=0,deadline=0,visitStartedUs=100,received=7,missed=3;
} channelSweep;
struct {
 unsigned calls=0;
 void arm(unsigned,unsigned current,unsigned,uint32_t age){
   ++calls;assert(current==1&&age==800);
 }
} channelTrace;
struct {unsigned errors=0;void println(const char*){++errors;}} Serial;
int lastListenResult=0;
unsigned recorded=0;
void recordBenchResult(int&,unsigned sequence,const char*,...){recorded=sequence;}
@METHOD@
int main(){
 for(unsigned pair=0;pair<2;++pair){
   channelPair=pair;channelSweep.expecting=false;
   expectChannelPacket(0,42+pair);
   assert(channelSweep.expecting&&channelSweep.sequence==42+pair);
   assert(channelSweep.deadline==uint32_t(nowMs+(pair?1000:10000)));
   assert(recorded==42+pair&&channelTrace.calls==pair+1);
   assert(channelSweep.current==1&&channelSweep.visitStartedUs==100);
   assert(channelSweep.received==7&&channelSweep.missed==3);
   expectChannelPacket(1,99); // overlapping requests cannot restart a deadline
   assert(channelSweep.sequence==42+pair&&recorded==42+pair);
 }
 channelSweep.expecting=false;
 expectChannelPacket(2,99);
 assert(!channelSweep.expecting&&channelTrace.calls==2&&Serial.errors==3);
}
'''.replace('@METHOD@',method(source,'void expectChannelPacket(')))

if __name__=='__main__': unittest.main()
