#!/usr/bin/env python3
"""Exercise actual airtime methods across a radio2-off scanner transition."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
scanner = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
start = scanner.index('  uint32_t getEstAirtimeFor(')
end = scanner.index('  float packetScore(', start)
derived = scanner[start:end]

wrapper = (root / 'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
start = wrapper.index('uint32_t RadioLibWrapper::getProfileAirtime(')
end = wrapper.index('\nbool RadioLibWrapper::setParams(', start)
base = wrapper[start:end]
# A bounded assertion makes accidental recursion fail deterministically instead
# of exhausting the test process's stack. The production logic is otherwise
# unchanged and calls the actual derived method through a virtual dispatch.
base = base.replace('{', '{ assert(++profile_calls <= 3);', 1)

program = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#define MAX_TRANS_UNIT 255
namespace mesh {
struct Profile { float freq=910.525f; uint8_t sf=7, cr=5; };
struct RadioProfiles {
  bool on=false; Profile profiles[2];
  bool enabled() const { return on; }
  const Profile& params(uint8_t i) const { return profiles[i]; }
  static double symbolUs(const Profile&) { return 2048.0; }
};
}
class RadioLibWrapper {
public:
  mesh::RadioProfiles _profiles;
  unsigned profile_calls=0, driver_calls=0;
  virtual uint32_t getEstAirtimeFor(int) { ++driver_calls; return 99; }
  uint32_t getProfileAirtime(uint8_t, int, uint8_t = 0);
  bool validateProfile(const mesh::Profile&) const { return true; }
  uint16_t profilePreamble(uint8_t) const { return 32; }
};
''' + base + r'''
class Wrapper : public RadioLibWrapper {
public:
  bool _lab_scan_active=false, _rx_ps_armed=false, _params_valid=true;
  int _lab_last_profile=-1; uint8_t _active_profile=0, _cur_cr=5;
  struct { uint8_t cr=5; } _lab_last;
''' + derived + r'''
};
int main() {
  Wrapper w;
  // Radio2 is already off but the PRAM handoff has not released ownership.
  // A packet from primary was just received, so virtual dispatch is active.
  w._lab_scan_active=true; w._lab_last_profile=0; w._profiles.on=false;
  assert(w.getEstAirtimeFor(20)>0 && w.profile_calls==1 && w.driver_calls==0);
  // Secondary attribution and ordinary active dual-profile use also work.
  w.profile_calls=0; w._lab_last_profile=1;
  assert(w.getEstAirtimeFor(20)>0 && w.profile_calls==1);
  w.profile_calls=0; w._lab_last_profile=0; w._profiles.on=true;
  assert(w.getEstAirtimeFor(20)>0 && w.profile_calls==1);
  // After scanner shutdown, RXPS uses the cache and normal RX uses the driver.
  w.profile_calls=0; w._lab_scan_active=false; w._rx_ps_armed=true;
  w._profiles.on=false;
  assert(w.getEstAirtimeFor(20)>0 && w.profile_calls==1 && w.driver_calls==0);
  w._rx_ps_armed=false;
  assert(w.getEstAirtimeFor(20)==99 && w.driver_calls==1);
}
'''
with tempfile.TemporaryDirectory() as td:
    cpp = Path(td) / 'test.cpp'
    exe = Path(td) / 'test'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True, timeout=5)
print('PASS actual W12 airtime dispatch when radio2 is disabled')
