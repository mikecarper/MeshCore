#!/usr/bin/env python3
"""Exercise actual Dispatcher AGC retry block with the real scheduler."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
dispatcher = (root / 'src/Dispatcher.cpp').read_text()
start = dispatcher.index('  const uint32_t agc_now = _ms->getMillis();')
end = dispatcher.index('  // check inbound (delayed) queue', start)
block = dispatcher[start:end]
scanner = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
start = scanner.index('  uint32_t labAGCMaintenanceRetryMs() const override {')
end = scanner.index('\n  }', start) + len('\n  }')
scanner_method = scanner[start:end].replace(' override', '')

program = r'''
#include <cassert>
#include <cstdint>
#include "helpers/AGCResetScheduler.h"
struct Radio {
  bool _lab_scan_active=false, _lab_uncertain=false;
  bool calibration=false, accepted=true, receive_retry=false;
  uint32_t receive_delay=0; unsigned fixed=0, attempts=0;
@SCANNER_METHOD@
  bool isCalibratingNoiseFloor() const { return calibration; }
  uint32_t agcMaintenanceRetryMs() const { return 50; }
  bool tryResetAGC() { ++attempts; return accepted; }
  void resetAGC() { ++fixed; }
  bool getReceiveRetryDelay(uint32_t, uint32_t& delay) const {
    if (!receive_retry) return false;
    delay=receive_delay; return true;
  }
};
struct Clock { uint32_t now=0; uint32_t getMillis() const { return now; } };
struct Dispatcher {
  Clock clock; Clock* _ms=&clock; Radio radio; Radio* _radio=&radio;
  mesh::AGCResetScheduler agc_reset;
  // Dispatcher::millisHasNowPassed uses a strict comparison in fixed mode.
  bool millisHasNowPassed(uint32_t at) const { return int32_t(clock.now-at)>0; }
  void tick(uint32_t now, bool adaptive, bool recovered_this_loop=false) {
    clock.now=now; const int agc_interval=28000;
    agc_reset.configure(now,agc_interval,adaptive,0);
@BLOCK@
  }
};
int main() {
  Dispatcher d; d.radio._lab_scan_active=true; d.tick(0,true);
  d.tick(28000,true); assert(d.agc_reset.nextAt()==29000);
  assert(d.radio.attempts==0 && d.radio.fixed==0 && d.agc_reset.interval()==28000);
  d.tick(28999,true); assert(d.radio.attempts==0 && d.agc_reset.nextAt()==29000);
  // Leaving scan doesn't postpone the already scheduled retry.
  d.radio._lab_scan_active=false; d.tick(29000,true);
  assert(d.radio.attempts==1 && d.agc_reset.nextAt()==57000);
  // Ambiguous FIFO is just as protected even if scan_active was cleared.
  Dispatcher u; u.radio._lab_uncertain=true; u.tick(0,true); u.tick(28000,true);
  assert(u.radio.attempts==0 && u.agc_reset.nextAt()==29000);
  u.radio._lab_uncertain=false; u.tick(29000,true); assert(u.radio.attempts==1);
  // Nonadaptive legacy mode also must not credit a scanner-owned no-op.
  Dispatcher f; f.radio._lab_scan_active=true; f.tick(0,false); f.tick(28000,false);
  assert(f.radio.fixed==0 && f.agc_reset.nextAt()==28000);
  f.tick(28001,false); assert(f.radio.fixed==0 && f.agc_reset.nextAt()==29001);
  f.radio._lab_scan_active=false; f.tick(29001,false); assert(f.radio.fixed==0);
  f.tick(29002,false); assert(f.radio.fixed==1); // strict >, one tick after deadline
  // Ordinary radios retain 50 ms adaptive deferral on an actual busy result.
  Dispatcher n; n.radio.accepted=false; n.tick(0,true); n.tick(28000,true);
  assert(n.radio.attempts==1 && n.agc_reset.nextAt()==28050);
  // A real receive retry longer than the scanner guard still wins.
  Dispatcher r; r.radio._lab_scan_active=true; r.radio.receive_retry=true;
  r.radio.receive_delay=1500; r.tick(0,true); r.tick(28000,true);
  assert(r.agc_reset.nextAt()==29500 && r.radio.attempts==0);
}
'''.replace('@SCANNER_METHOD@', scanner_method).replace('@BLOCK@', block)

with tempfile.TemporaryDirectory(prefix='w12-agc-deferral-') as td:
    cpp = Path(td) / 'test.cpp'
    exe = Path(td) / 'test'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', '-fno-pie', '-no-pie',
                    '-DMESH_W12_PRAM_EXPERIMENT=1', '-I', str(root / 'src'),
                    str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS scanner AGC deferral retains pending maintenance and ordinary retries')
