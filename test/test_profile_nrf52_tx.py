"""Reference transmitter: cache replay never retransmits and failures stay failures."""
from pathlib import Path
import unittest
from test_radio_receive_contract import method
import test_sx1262_batched_modulation as compiler

ROOT=Path(__file__).resolve().parents[1]


class NrfReferenceTxTests(unittest.TestCase):
    compile_run=compiler.BatchedModulationTests.compile_run

    def test_duplicate_result_replay_and_failed_setup_do_not_transmit(self):
        source=method((ROOT/'tools/hil/profile_switch_nrf52_tx.cpp').read_text(),'void referenceTransmit(')
        harness=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstddef>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_CHIP_NOT_FOUND -2
unsigned ticks=0;uint32_t micros(){ return ++ticks; }
int SPI;
unsigned channelStepKhz=250;
float expectedFrequency=910.25f;
float expectedBw=125;
unsigned expectedSf=10;
struct Result { unsigned sequence=0; } lastTxResult;
unsigned replayed=0;
void emitBenchResult(const Result&) { ++replayed; }
template<class... T> void recordBenchResult(Result& r,unsigned seq,const char*,T...) { r.sequence=seq; }
struct {
  bool init=true; unsigned sent=0; int txResult=0;
  bool std_init(int*) { return init; }
  int setFrequency(float f) { assert(f==expectedFrequency);return 0; }
  int setLoRaModulationParams(float bw,unsigned sf,unsigned cr) { assert(bw==expectedBw&&sf==expectedSf&&cr==5);return 0; }
  int setPreambleLength(unsigned p) { assert(p==32);return 0; }
  int setOutputPower(int p) { assert(p==-9);return 0; }
  int transmit(uint8_t* p,unsigned len) { assert(len==16&&!memcmp(p,"CHS1",4)&&p[8]==3);++sent;return txResult; }
} chip;
@METHOD@
int main() {
  referenceTransmit(3,42,16,32,10,125);assert(chip.sent==1&&lastTxResult.sequence==42);
  referenceTransmit(3,42,16,32,10,125);assert(chip.sent==1&&replayed==1);
  chip.init=false;referenceTransmit(3,43,16,32,10,125);assert(chip.sent==1&&lastTxResult.sequence==43);
  chip.init=true;chip.txResult=-3;referenceTransmit(3,44,16,32,10,125);assert(chip.sent==2);
  referenceTransmit(3,44,16,32,10,125);assert(chip.sent==2&&replayed==2);
  channelStepKhz=1000;expectedFrequency=912.5f;chip.txResult=0;
  referenceTransmit(3,45,16,32,10,125);assert(chip.sent==3);
  channelStepKhz=250;referenceTransmit(3,45,16,32,10,125);assert(chip.sent==3&&replayed==3);
  expectedFrequency=910.25f;
  for(unsigned i=0;i<4;++i) {
    expectedSf=9+i;expectedBw=62.5f*(1u<<i);
    referenceTransmit(3,46+i,16,32,expectedSf,expectedBw);assert(chip.sent==4+i);
  }
}
'''
        self.compile_run(harness.replace('@METHOD@',source))

    def test_scope_and_host_command_bounds(self):
        source=(ROOT/'tools/hil/profile_switch_nrf52_tx.cpp').read_text()
        self.assertNotIn('transmit',method(source,'void setup()'))
        loop=method(source,'void loop()')
        for guard in ('channel<4','len==16','preamble==32','sf>=5','sf<=10','bw==125','bw==250'):
            self.assertIn(guard,loop)
        self.assertIn('static bool overflow=false',loop)


if __name__=='__main__':
    unittest.main()
