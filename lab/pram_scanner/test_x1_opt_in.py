#!/usr/bin/env python3
"""Test X1's scanner configuration and conservative nRF idle behavior."""
import configparser
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'test'))
from test_lr2021_apply_transaction import method

config = configparser.ConfigParser(interpolation=None)
config.read(root / 'variants/meshtracker_x1/platformio.ini', encoding='utf-8')
lab = config['MeshTracker_X1_pram']['build_flags']
assert 'MESH_W12_PRAM_EXPERIMENT=1' in lab
assert 'MESH_LR2021_PRAM_MANUAL_START' not in lab
assert 'MESH_W12_RXPS_EXPERIMENT' not in lab
assert 'LR2021_TCXO_VOLTAGE=1.6f' in config['MeshTracker_X1']['build_flags']
for section in config.sections():
    if section.startswith('env:'):
        enabled = 'MeshTracker_X1_pram' in config[section].get('build_flags', '')
        assert enabled == (section in ('env:MeshTracker_X1_repeater', 'env:MeshTracker_X1_room_server'))

source = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text(encoding='utf-8')
assert 'AutoPolicy _lab_auto{false};' in source
idle = method(source, 'bool getProfileIdleDelay(').replace(' override', '')
autonomous = method(source, 'bool hasAutonomousProfileIdle(').replace(' override', '')
code = r'''
#include <cassert>
#include <cstdint>
#include <helpers/radiolib/W12PramAutoPolicy.h>
struct RadioLibWrapper {
  bool getProfileIdleDelay(uint32_t& out) const { out=17; return true; }
};
struct Scanner : RadioLibWrapper {
  bool _lab_scan_active=false;
@IDLE@
@AUTONOMOUS@
};
int main() {
  mesh::w12pram::AutoPolicy normal, opt_in(false);
  assert(normal.enabled() && !opt_in.enabled());
  assert(!opt_in.due(100));
  opt_in.setEnabled(true); assert(opt_in.due(100));
  Scanner s; uint32_t delay=0;
  assert(s.getProfileIdleDelay(delay) && delay==17);
  s._lab_scan_active=true; delay=100;
  assert(!s.getProfileIdleDelay(delay) && delay==0);
  assert(!s.hasAutonomousProfileIdle());
}
'''.replace('@IDLE@', idle).replace('@AUTONOMOUS@', autonomous)
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'test.cpp').write_text(code, encoding='utf-8')
    subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-DNRF52_PLATFORM', '-I', str(root / 'src'),
                    str(tmp / 'test.cpp'), '-o', str(tmp / 'test')], check=True)
    subprocess.run([str(tmp / 'test')], check=True)
print('PASS X1 scanner configuration, startup policy and nRF host idle ownership')
