"""HIL IRQ capture is bounded, observational and frozen before USB retrieval."""
from pathlib import Path
import unittest
import test_sx1262_batched_modulation as compiler


class ChannelTraceTests(unittest.TestCase):
    compile_run = compiler.BatchedModulationTests.compile_run

    def test_held_occupancy_survives_receive_restart(self):
        source = (Path(__file__).resolve().parents[1] / "tools/hil/profile_switch_channels.h").read_text()
        self.assertIn("visitAge=now-channelSweep.visitStartedUs", source)
        self.assertIn("channelSweep.heldDwell.add(visitAge)", source)
        self.assertIn("channelTrace.add(ChannelTrace::Hop,visitAge", source)
        self.assertEqual(source.count("channelSweep.visitStartedUs=driver.receiveStartedUs();"), 2)

    def test_selected_sf_reaches_initial_hops_tx_and_cache_check(self):
        source = (Path(__file__).resolve().parents[1] / "tools/hil/profile_switch_channels.h").read_text()
        self.assertIn("p.sf=sf", source)
        for expected in ("sweepParams(0,preamble,sf)", "sweepParams(1,preamble,sf)",
                         "setLoRaModulationParams(125,sf,5)", "channelSweep.sf=sf;",
                         "chip.spreadingFactor!=channelSweep.sf"):
            self.assertIn(expected, source)
        self.assertEqual(source.count("sweepParams(next,channelSweep.preamble,channelSweep.sf)"), 2)

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
  uint8_t tx[4]={0x83,0,0,0};t.observe(tx,4,in);
  assert(t.txCommandUs==clockUs && t.txWord==t.rfWord && t.size==1);
  assert(out[0]==0x12 && clear[0]==2 && in[3]==0);
}
'''
        self.compile_run(harness.replace("@SOURCE@", source))


if __name__ == "__main__":
    unittest.main()
