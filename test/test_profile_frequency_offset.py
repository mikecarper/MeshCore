"""Sub-float HIL detour changes the actual RF command, not the MHz float."""
from pathlib import Path
import unittest
from test_radio_receive_contract import method
import test_sx1262_batched_modulation as compiler

ROOT = Path(__file__).resolve().parents[1]


class FrequencyOffsetTests(unittest.TestCase):
    compile_run = compiler.BatchedModulationTests.compile_run

    def test_integer_word_offset_and_scoped_restore(self):
        source = (ROOT / 'tools/hil/ProfileFrequencyOffset.h').read_text().replace('#pragma once', '')
        self.compile_run(r'''
#include <cassert>
#include <cstring>
@SOURCE@
void earlyReturn() { HilFrequencyOffsetScope offset(10); }
int main() {
  uint8_t original[5]={0x86,0x38,0xd8,0xff,0xfc}, shifted[5]={};
  uint8_t saved[5];memcpy(saved,original,5);
  assert(hilFrequencyCommand(original,5,shifted)==original);
  {
    HilFrequencyOffsetScope offset(10);
    assert(hilFrequencyCommand(original,5,shifted)==shifted);
    const uint8_t expected[5]={0x86,0x38,0xd9,0,6};
    assert(memcmp(shifted,expected,5)==0 && memcmp(original,saved,5)==0);
    assert(hilFrequencyCommand(original,4,shifted)==original);
    assert(hilFrequencyCommand(nullptr,5,shifted)==nullptr);
    uint8_t other[5]={0x8b,10,4,2,0};
    assert(hilFrequencyCommand(other,5,shifted)==other);
    uint8_t overflow[5]={0x86,0xff,0xff,0xff,0xfc};
    assert(hilFrequencyCommand(overflow,5,shifted)==overflow);
    { HilFrequencyOffsetScope zero(0);assert(hilFrequencyCommand(original,5,shifted)==original); }
    assert(hilFrequencyOffsetSteps==10);
  }
  assert(hilFrequencyOffsetSteps==0);
  earlyReturn();assert(hilFrequencyOffsetSteps==0);
  assert(hilFrequencyCommand(original,5,shifted)==original);
  const float mhz=909.5f;
  assert(float(mhz+0.00001f)==mhz); // float API cannot encode requested 10 Hz
  assert(10.0*32000000.0/33554432.0==9.5367431640625);
  { HilFrequencyOffsetScope offset(105);
    hilFrequencyCommand(original,5,shifted);
    const uint8_t expected[5]={0x86,0x38,0xd9,0,101};
    assert(memcmp(shifted,expected,5)==0 && memcmp(original,saved,5)==0);
  }
  assert(105.0*32000000.0/33554432.0==100.13580322265625);
}
'''.replace('@SOURCE@', source))

    def test_wire_and_observer_use_shifted_bytes(self):
        source = (ROOT / 'tools/hil/profile_switch.cpp').read_text()
        transfer = method(source, 'void spiTransfer(')
        self.assertIn('hilFrequencyCommand(out,len,shifted)', transfer)
        self.assertIn('ESP32BufferedRadioHal::spiTransfer(const_cast<uint8_t*>(wire)', transfer)
        self.assertIn('ArduinoHal::spiTransfer(const_cast<uint8_t*>(wire)', transfer)
        self.assertIn('channelTrace.observe(wire,len,in)', transfer)
        apply = method(source, 'bool applyParams(')
        self.assertIn('HilFrequencyOffsetScope offset(firstPassDetour && hopPasses==2 && cr==6 ? firstPassOffsetSteps : 0)', apply)

    def test_capability_reply_avoids_exact_usb_packet_boundary(self):
        import json
        import re
        source = (ROOT / 'tools/hil/profile_switch.cpp').read_text()
        branch = source.split('!strcmp(line,"detourinfo")', 1)[1].split('} else', 1)[0]
        literal = re.search(r'Serial\.printf\(("(?:\\.|[^"\\])*")', branch).group(1)
        template = json.loads(literal)
        for hz, steps in ((10,10),(100,105)):
            reply = template % (hz/1000,steps,steps*32000000/33554432)
            self.assertNotEqual(len(reply.encode('ascii'))%64, 0)
            self.assertTrue(reply.endswith('\n'))
            self.assertEqual(json.loads(reply)['rf_offset_steps'], steps)
            self.assertEqual(json.loads(reply)['offset_hz'],steps*32000000/33554432)


if __name__ == '__main__':
    unittest.main()
