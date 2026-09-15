"""HIL IRQ capture is bounded, observational and frozen before USB retrieval."""
from pathlib import Path
import unittest
import test_sx1262_batched_modulation as compiler


class ChannelTraceTests(unittest.TestCase):
    compile_run = compiler.BatchedModulationTests.compile_run

    def test_settle_is_additional_and_cycles_exclude_held_visits(self):
        source = (Path(__file__).resolve().parents[1] / "tools/hil/ProfileChannelVisitClock.h").read_text().replace("#pragma once", "")
        self.compile_run(r'''
#include <cassert>
@SOURCE@
int main() {
  ChannelVisitClock v;
  v.begin(100,200,false);assert(v.dwell(500,100)==400); // zero-delay legacy clock
  v.begin(100,1100,true);assert(v.dwell(3711,100)==2611);
  assert(v.dwell(3712,100)==2612); // settling did not consume the 5.1-symbol visit
  assert(v.dwell(4100,4000)==100); // packet restart has no channel-settle delay
  v.begin(0xfffffff0u,0xfffffffau,true);assert(v.dwell(10,0xfffffff0u)==16);
  ChannelCycleClock c;uint32_t elapsed=0;
  assert(!c.hop(0,100,false,elapsed));
  for(unsigned ch=1;ch<4;++ch) assert(!c.hop(ch,100+ch*3000,false,elapsed));
  assert(c.hop(0,12100,false,elapsed) && elapsed==12000);
  assert(!c.hop(2,15000,true,elapsed));
  assert(!c.hop(0,25000,false,elapsed)); // a held visit invalidates this full cycle
  assert(c.hop(0,37000,false,elapsed) && elapsed==12000);
}
'''.replace("@SOURCE@", source))

    def test_held_occupancy_survives_receive_restart(self):
        source = (Path(__file__).resolve().parents[1] / "tools/hil/profile_switch_channels.h").read_text()
        self.assertIn("visitAge=now-channelSweep.visitStartedUs", source)
        self.assertIn("channelSweep.heldDwell.add(visitAge)", source)
        self.assertIn("channelTrace.add(ChannelTrace::Hop,visitAge", source)
        self.assertEqual(source.count("channelSweep.visitStartedUs=driver.receiveStartedUs();"), 2)

    def test_selected_sf_reaches_initial_hops_tx_and_cache_check(self):
        source = (Path(__file__).resolve().parents[1] / "tools/hil/profile_switch_channels.h").read_text()
        self.assertIn("p.sf=sf", source)
        for expected in ("sweepParams(0,preamble,sf,bwKhz)", "sweepParams(1,preamble,sf,bwKhz)",
                         "setLoRaModulationParams(bwKhz,sf,5)", "channelSweep.sf=sf;",
                         "channelSweep.bwKhz=bwKhz;", "p.bw=bwKhz",
                         "chip.bandwidthKhz!=expectedParams.bw",
                         "chip.spreadingFactor!=expectedParams.sf"):
            self.assertIn(expected, source)
        self.assertEqual(source.count("sweepParams(next,channelSweep.preamble,channelSweep.sf,channelSweep.bwKhz)"), 3)

    def test_passive_irq_decode_bounds_and_freeze(self):
        source = (Path(__file__).resolve().parents[1] / "tools/hil/ProfileChannelTrace.h").read_text().replace("#pragma once", "")
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
uint32_t clockUs=1000;
uint32_t micros() { return clockUs; }
@SOURCE@
int main() {
  auto& t=channelTrace;
  t.arm(5,0,3,40);assert(t.size==0);
  t.enabled=true;t.arm(6,1,3,50);
  assert(t.size==1 && t.events[0].a==3 && t.events[0].b==50);
  uint8_t out[4]={0x12,0,0,0},in[4]={0,0,0,4};
  clockUs+=20;t.observe(out,4,in);
  assert(t.size==2 && t.events[1].irq==4 && t.events[1].us==20);
  t.observe(out,4,in);assert(t.size==2); // unchanged observations deduplicate
  uint8_t clear[3]={2,0,4};t.observe(clear,3,in);
  assert(t.events[2].kind==ChannelTrace::Clear && t.irq==4);
  in[3]=0;t.observe(out,4,in);assert(t.size==4 && t.irq==0);
  t.active=false;t.observe(out,4,in);t.add(ChannelTrace::Hop);assert(t.size==4);
  t.arm(7,2,0,0);
  for(unsigned i=0;i<ChannelTrace::Capacity+10;++i) t.add(ChannelTrace::Hop,i);
  assert(t.size==ChannelTrace::Capacity && t.overflow==11);
  t.arm(8,0,0,0);assert(t.size==1 && !t.overflow && t.sequence==8);
  t.active=false;
  uint8_t rf[5]={0x86,0x38,0xe0,0,0};t.observe(rf,5,in);
  assert(t.rfWord==0x38e00000);
  assert(t.rfWrites==1 && t.modulationWrites==0);
  uint8_t modulation[5]={0x8b,6,4,1,0};t.observe(modulation,5,in);
  assert(t.modulationWrites==1 && t.size==1);
  assert(t.modulationWord==0x06040100);
  uint8_t tx[4]={0x83,0,0,0};t.observe(tx,4,in);
  assert(t.txCommandUs==clockUs && t.txWord==t.rfWord && t.size==1);
  assert(out[0]==0x12 && clear[0]==2 && in[3]==0);
}
'''
        self.compile_run(harness.replace("@SOURCE@", source))


if __name__ == "__main__":
    unittest.main()
