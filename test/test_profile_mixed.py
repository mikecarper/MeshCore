"""Mixed HIL channels preserve fractional BW and normal uniform defaults."""
from pathlib import Path
import unittest
from test_radio_receive_contract import method
import test_sx1262_batched_modulation as compiler

ROOT=Path(__file__).resolve().parents[1]


class MixedTests(unittest.TestCase):
    compile_run=compiler.BatchedModulationTests.compile_run

    def test_profile_mapping_and_equal_symbols(self):
        source=(ROOT/'tools/hil/profile_switch_channels.h').read_text()
        header=(ROOT/'tools/hil/ProfileMixedChannels.h').read_text().replace('#pragma once','')
        pair_header=(ROOT/'tools/hil/ProfilePairPlan.h').read_text().replace('#pragma once','')
        self.compile_run(r'''
#include <cassert>
@HEADER@
@PAIR_HEADER@
namespace mesh { struct RadioProfileParams { float freq,bw; unsigned sf,cr,preamble; }; }
bool channelMixed=false,channelPair=false;
float sweepFrequency(unsigned channel){return 909.5f+channel;}
@METHOD@
int main(){
 for(unsigned ch=0;ch<4;++ch){
   auto uniform=sweepParams(ch,32,6,125);
   assert(uniform.sf==6&&uniform.bw==125);
   channelMixed=true;auto p=sweepParams(ch,32,10,125);channelMixed=false;
   assert(p.sf==9+ch&&p.bw==62.5f*(1u<<ch)&&p.cr==5&&p.preamble==32);
   assert((1u<<p.sf)*1000/p.bw==8192);
 }
 channelPair=true;
 auto slow=sweepParams(0,32,10,125), fast=sweepParams(1,32,10,125);
 assert(slow.sf==7&&slow.bw==62.5f&&slow.freq==909.5f);
 assert(fast.sf==8&&fast.bw==500&&fast.freq==910.5f);
 assert(slow.cr==5&&fast.cr==5&&slow.preamble==32&&fast.preamble==32);
}
'''.replace('@HEADER@',header).replace('@PAIR_HEADER@',pair_header)
   .replace('@METHOD@',method(source,'mesh::RadioProfileParams sweepParams(')))


if __name__=='__main__': unittest.main()
