#!/usr/bin/env python3
"""Exercise the actual chip-scanner sleep admission with pending IRQs."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
start = source.index('  bool getProfileIdleDelay(')
end = source.index('  void labScannerStatus(', start)
method = source[start:end]
program = r'''
#include <cassert>
#include <cstdint>
#include "helpers/radiolib/W12PramScannerCodec.h"
#define P_LORA_DIO_1 14
#define HIGH 1
uint32_t now_ms=0; int pin_high=0;
uint32_t millis() { return now_ms; }
int digitalRead(int p) { assert(p==14); return pin_high; }
namespace mesh { namespace esp32 { uint32_t profileSleepSliceMillis() { return 10; } } }
class RadioLibWrapper { public: virtual bool getProfileIdleDelay(uint32_t& d) const { d=7; return true; } };
class Wrapper : public RadioLibWrapper {
public:
  bool _lab_scan_active=true, _lab_uncertain=false, _lab_sleeping=false, _lab_resume_pending=false;
  bool _profile_repair_required=false, _cw_active=false, receiving=true, pending=false;
  uint32_t _lab_last_poll=0; mesh::w12pram::Status _lab_status{};
  struct Profiles { bool active=true; uint32_t generation[2]={1,2}; bool enabled() const { return active; } } _profiles;
  mesh::w12pram::Profile _lab_snap[2]{};
  bool isInRecvMode() const { return receiving; }
  bool hasPendingRadioInterrupt() const { return pending; }
''' + method + r'''
};
int main() {
  Wrapper w; uint32_t ms=0; w._lab_status.hooked=1; w._lab_snap[0].generation=1; w._lab_snap[1].generation=2;
  assert(w.getProfileIdleDelay(ms) && ms==10);
  pin_high=1; assert(!w.getProfileIdleDelay(ms)); pin_high=0;
  w.pending=true; assert(!w.getProfileIdleDelay(ms)); w.pending=false;
  w._lab_uncertain=true; assert(!w.getProfileIdleDelay(ms)); w._lab_uncertain=false;
  w._lab_resume_pending=true; assert(!w.getProfileIdleDelay(ms)); w._lab_resume_pending=false;
  w._profiles.generation[1]++; assert(!w.getProfileIdleDelay(ms)); w._profiles.generation[1]--;
  now_ms=997; assert(w.getProfileIdleDelay(ms) && ms==3);
  now_ms=1000; assert(!w.getProfileIdleDelay(ms));
  w._lab_scan_active=false; assert(w.getProfileIdleDelay(ms) && ms==7);
}
'''
with tempfile.TemporaryDirectory() as td:
    cpp = Path(td) / 'test.cpp'
    exe = Path(td) / 'test'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', '-I', str(root / 'src'), str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS scanner sleep admission, IRQ and watchdog deadline')
