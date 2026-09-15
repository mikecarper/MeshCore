"""Stationary baseline payload integrity and separation from the hop scheduler."""
from pathlib import Path
import unittest
from test_radio_receive_contract import method
import test_sx1262_batched_modulation as compiler

ROOT = Path(__file__).resolve().parents[1]


class StationaryBaselineTests(unittest.TestCase):
    compile_run = compiler.BatchedModulationTests.compile_run

    def test_complete_payload_identity(self):
        source = (ROOT / 'tools/hil/profile_stationary_baseline.h').read_text()
        self.compile_run(r'''
#include <cassert>
#include <cstdint>
#include <cstring>
@METHOD@
int main() {
  uint8_t bytes[16]; uint32_t seq=123;
  memcpy(bytes,"CHS1",4);memcpy(bytes+4,&seq,4);bytes[8]=3;
  for(unsigned i=9;i<16;++i) bytes[i]=uint8_t(i^seq);
  assert(stationaryPayloadMatches(bytes,16,123,3));
  assert(!stationaryPayloadMatches(bytes,15,123,3));
  assert(!stationaryPayloadMatches(bytes,16,124,3));
  assert(!stationaryPayloadMatches(bytes,16,123,2));
  for(unsigned i=0;i<16;++i) {
    bytes[i]^=1;assert(!stationaryPayloadMatches(bytes,16,123,3));bytes[i]^=1;
  }
}
'''.replace('@METHOD@', method(source, 'bool stationaryPayloadMatches(')))

    def test_no_retuning_or_rf_transmission_after_setup(self):
        source = (ROOT / 'tools/hil/profile_stationary_baseline.h').read_text()
        self.assertNotIn('transmit(', source)
        for signature in ('void expectStationaryPacket(', 'void serviceStationaryBaseline('):
            body = method(source, signature)
            for forbidden in ('setFrequency(', 'std_init(', 'prepareDiagnosticRadio(', 'driver.hop('):
                self.assertNotIn(forbidden, body)
        self.assertIn('stationaryBaseline.rfStart=channelTrace.rfWrites', source)
        self.assertIn('millis()+600000', source)


if __name__ == '__main__':
    unittest.main()
