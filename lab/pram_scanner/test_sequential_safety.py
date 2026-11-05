#!/usr/bin/env python3
"""Exercise the real receive-only lab admission with fake ownership state."""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'test'))
from cpp_source import body

source = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
wrapper = (root / 'src/helpers/radiolib/CustomLR2021Wrapper.h').read_text()
configure = body(source, 'bool labSequentialConfigure(')
assert 'if (_lab_seq_requested) return Result::FAILED;' in body(
    source, 'mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t p, bool reply)')
assert 'if (_lab_seq_requested) return Result::FAILED;' in body(
    source, 'mesh::RadioParamApplyResult prepareBeaconTransmit(')
assert 'if (_lab_seq_requested) return false;' in body(wrapper, 'bool startSendRaw(')
assert 'if (_lab_seq_requested && duration)' in body(wrapper, 'mesh::RadioParamApplyResult setCarrierWave(')
assert 'if (_lab_seq_requested)' in body(wrapper, 'mesh::MorseTxResult startMorseTx(')
assert 'mode() != mesh::radioDirection::Mode::Rx' in body(source, 'bool labScannerStart(')
program = r'''
#include <cassert>
#include <cstdint>
namespace mesh { namespace radioDirection {
enum class Mode { Both, Rx }; static Mode value=Mode::Rx; Mode mode() { return value; }
} }
struct Scanner {
  bool _lab_scan_active=false, _lab_uncertain=false, _lab_seq_requested=false, _cw_active=false;
  uint8_t state=1;
  uint8_t _lab_seq_selected=0;
  struct { bool on=false; bool enabled() const { return on; } } _lab_auto;
  struct { bool on=true; bool enabled() const { return on; } } _profiles;
  struct { uint8_t count=3; } _lab_seq_plan;
  CONFIGURE
};
int main() {
  Scanner s;
  s._lab_auto.on=true; assert(!s.labSequentialConfigure(0));
  s._lab_auto.on=false; s._lab_scan_active=true; assert(!s.labSequentialConfigure(0));
  s._lab_scan_active=false; s._lab_uncertain=true; assert(!s.labSequentialConfigure(0));
  s._lab_uncertain=false; s.state=3; assert(!s.labSequentialConfigure(0));
  s.state=1; s._cw_active=true; assert(!s.labSequentialConfigure(0));
  s._cw_active=false; s._profiles.on=false; assert(!s.labSequentialConfigure(0));
  s._profiles.on=true; mesh::radioDirection::value=mesh::radioDirection::Mode::Both;
  assert(!s.labSequentialConfigure(0));
  mesh::radioDirection::value=mesh::radioDirection::Mode::Rx;
  assert(s.labSequentialConfigure(0) && s._lab_seq_requested && s._lab_seq_selected==0);
  assert(s.labSequentialConfigure(1) && s._lab_seq_selected==1);
  assert(!s.labSequentialConfigure(2));
  assert(s.labSequentialConfigure(-1) && !s._lab_seq_requested && !s._lab_seq_plan.count);
}
'''.replace('CONFIGURE', configure)
with tempfile.TemporaryDirectory() as directory:
    cpp, exe = Path(directory) / 'admission.cpp', Path(directory) / 'admission'
    cpp.write_text(program)
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
print('PASS sequential receive-only admission and TX guards')
