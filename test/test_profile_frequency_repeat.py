"""Check the actual HIL frequency override's scope, order and error propagation."""
from pathlib import Path
import unittest
from test_radio_receive_contract import method
import test_sx1262_batched_modulation as compiler

ROOT=Path(__file__).resolve().parents[1]


class FrequencyRepeatTests(unittest.TestCase):
    compile_run=compiler.BatchedModulationTests.compile_run

    def test_only_owned_rx_hops_repeat_and_failures_short_circuit(self):
        source=method((ROOT/'tools/hil/profile_switch_experiments.h').read_text(),
                      'int16_t setFrequency(')
        harness=r'''
#include <cassert>
#include <cstdint>
#include <vector>
#define RADIOLIB_ERR_NONE 0
struct SX1262 {
  std::vector<bool> calibrationSkipped; int failAt=0;
  virtual int16_t setFrequency(float f) { return setFrequency(f,false); }
  int16_t setFrequency(float f,bool skip) {
    assert(f==909.5f); calibrationSkipped.push_back(skip);
    return int(calibrationSkipped.size())==failAt ? -3:0;
  }
};
struct Radio : SX1262 {
  bool repeatFrequency=false, inHop=false, resumeRx=false;
  @METHOD@
};
int main() {
  for(bool enabled : {false,true}) for(bool hop : {false,true}) for(bool rx : {false,true}) {
    Radio r;r.repeatFrequency=enabled;r.inHop=hop;r.resumeRx=rx;
    assert(r.setFrequency(909.5f)==0);
    assert(r.calibrationSkipped.size()==(enabled&&hop&&rx?2u:1u));
    assert(!r.calibrationSkipped.front());
    if(r.calibrationSkipped.size()==2) assert(r.calibrationSkipped.back());
  }
  for(int fail : {1,2}) {
    Radio r;r.repeatFrequency=r.inHop=r.resumeRx=true;r.failAt=fail;
    assert(r.setFrequency(909.5f)==-3);
    assert(int(r.calibrationSkipped.size())==fail);
  }
}
'''
        self.compile_run(harness.replace('@METHOD@',source))


if __name__=='__main__':
    unittest.main()
