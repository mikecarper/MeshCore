"""HIL V4 front-end selection and restoration around every direct TX path."""
from pathlib import Path
import unittest
from test_radio_receive_contract import method
import test_sx1262_batched_modulation as compiler

ROOT = Path(__file__).resolve().parents[1]


class HeltecHilTests(unittest.TestCase):
    compile_run = compiler.BatchedModulationTests.compile_run

    def test_gc_bypass_kct_tx_and_restore_even_on_error(self):
        source = (ROOT / "tools/hil/ProfileHeltecV4.h").read_text().replace(
            '#include "LoRaFEMControl.h"', '').replace('#pragma once', '')
        tx = method((ROOT / "tools/hil/profile_switch.cpp").read_text(), 'int16_t benchTransmit(')
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <string>
enum { GC1109_PA, KCT8103L_PA };
struct LoRaFEMControl {
  int type=GC1109_PA; std::string calls;
  void init() { calls+='I'; }
  void setRxModeEnable() { calls+='R'; }
  void setTxModeEnable() { calls+='T'; }
  int getFEMType() const { return type; }
};
@SOURCE@
ProfileHeltecV4 v4FrontEnd;
struct {
  int16_t rc=0;
  int16_t transmit(uint8_t*,size_t n) {
    assert(n==16);
    assert(v4FrontEnd.fem.calls.back()==(v4FrontEnd.fem.type==GC1109_PA?'R':'T'));
    v4FrontEnd.fem.calls+='X'; return rc;
  }
} chip;
#define HIL_HELTEC_V4
@TX@
int main() {
  uint8_t data[16]={};
  for(int fem : {GC1109_PA,KCT8103L_PA}) {
    v4FrontEnd.fem.type=fem; v4FrontEnd.fem.calls.clear();
    v4FrontEnd.begin(); assert(v4FrontEnd.fem.calls=="IR");
    assert(!strcmp(v4FrontEnd.type(),fem==GC1109_PA?"GC1109":"KCT8103L"));
    assert(!strcmp(v4FrontEnd.txMode(),fem==GC1109_PA?"bypass":"amplified"));
    for(int error : {0,-3}) {
      chip.rc=error; v4FrontEnd.fem.calls.clear();
      assert(benchTransmit(data,16)==error);
      assert(v4FrontEnd.fem.calls==(fem==GC1109_PA?"RXR":"TXR"));
    }
  }
}
'''
        self.compile_run(harness.replace('@SOURCE@',source).replace('@TX@',tx))

    def test_all_direct_probe_transmits_use_frontend_adapter(self):
        for name in ('profile_switch_channels.h','profile_switch_packets.h','profile_preamble_diagnostic.h'):
            source=(ROOT/'tools/hil'/name).read_text()
            self.assertNotIn('chip.transmit(',source)
            self.assertIn('benchTransmit(',source)
        source=(ROOT/'tools/hil/profile_switch.cpp').read_text()
        setup=method(source,'void setup()')
        self.assertLess(setup.index('v4FrontEnd.begin()'),setup.index('chip.std_init('))


if __name__ == '__main__':
    unittest.main()
