#!/usr/bin/env python3
"""Exercise the actual PRAM status encoder and host recovery snapshot."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
scan = (root / 'lab/pram_scanner/scan.c').read_text()
host = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()


def definition(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    for pos in range(opening + 1, len(source)):
        if source[pos] == '{':
            depth += 1
        elif source[pos] == '}':
            depth -= 1
            if not depth:
                return source[start:pos + 1]
    raise AssertionError(f'Unterminated definition: {signature}')


program = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include "helpers/radiolib/W12PramScannerCodec.h"
using u32 = uint32_t;
using u8 = uint8_t;
struct resp { u32 len; u8* buf; };
static u32 slot = 0x1234, irq = 0, gate = 0;
static u8 armed = 0;
#define STATE_SLOT slot
#define bg_state 0x1234u
#define IRQ irq
#define IRQ_GATE gate
#define RX_TIMEOUT_ARMED armed
''' + definition(scan, 'struct bgs {') + r''';
''' + definition(scan, 'static void bg_resp(') + r'''
class FakeDriver {
public:
  int16_t _lab_error=0, _lab_last_recovery_error=0;
  mesh::w12pram::RecoveryOrigin _lab_recovery_origin=mesh::w12pram::RecoveryOrigin::None;
  uint8_t _lab_recovery_timer=0;
  unsigned long _lab_recoveries=0;
  bool _lab_resume_pending=false, _profile_repair_required=false;
  mesh::w12pram::Status _lab_status{};
  bool labReinitialize() { _lab_status={}; return true; }
''' + definition(host, '  void labRecover(') + '\n' + definition(host, '  void labScannerRecoveryDiag(') + r'''
};
int main() {
  bgs b{}; b.state=3; b.last_hit=1; b.c=1; b.cads=42; b.lost_irq=0x001003A0;
  irq=0x001001A0; gate=0xFFFF0AE0;
  u8 data[40]={}; resp r{0,data};
  bg_resp(&b, &r);
  assert(r.len==40 && data[3]==0x05); // hooked, timer observation valid and off
  auto s=mesh::w12pram::decode(data);
  assert(s.state==3 && s.current==1 && s.last_hit==1 && s.hooked && !s.stopped);
  assert(s.cads==42 && s.irq==irq && s.lost_irq==b.lost_irq && s.irq_gate==gate);
  assert(mesh::w12pram::timeoutObservation(s)==1);
  armed=1; bg_resp(&b, &r);
  assert(data[3]==0x07 && mesh::w12pram::timeoutObservation(mesh::w12pram::decode(data))==2);
  data[3] |= 0x80; // actual op6 adds the stopped flag after bg_resp
  s=mesh::w12pram::decode(data);
  assert(s.hooked && s.stopped && mesh::w12pram::timeoutObservation(s)==2);
  slot=0; bg_resp(&b, &r);
  assert(data[3]==0x06 && !mesh::w12pram::decode(data).hooked);

  FakeDriver driver; driver._lab_status=mesh::w12pram::decode(data);
  driver.labRecover(-705,mesh::w12pram::RecoveryOrigin::NoCad);
  char out[160]; driver.labScannerRecoveryDiag(out,sizeof(out));
  assert(std::strstr(out,"origin=no-cad") && std::strstr(out,"timer=armed")
      && std::strstr(out,"cached=unknown")); // reinit overwrote live status
  armed=0; bg_resp(&b,&r); driver._lab_status=mesh::w12pram::decode(data);
  driver.labRecover(-1); driver.labScannerRecoveryDiag(out,sizeof(out));
  assert(std::strstr(out,"timer=off") && std::strstr(out,"cached=unknown"));
  data[3]=1; driver._lab_status=mesh::w12pram::decode(data); // older blob
  driver.labRecover(-1); driver.labScannerRecoveryDiag(out,sizeof(out));
  assert(std::strstr(out,"timer=unknown"));
}
'''

with tempfile.TemporaryDirectory() as td:
    cpp = Path(td) / 'timer_observation.cpp'
    exe = Path(td) / 'timer_observation'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', '-I', str(root / 'src'), str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS PRAM timer status encoding and cached recovery observation')
