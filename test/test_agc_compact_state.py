#!/usr/bin/env python3
"""Verify compact diagnostic proof and complete pulse/lease initialization."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from cpp_source import body

ROOT=Path(__file__).resolve().parents[1]

class CompactStateTests(unittest.TestCase):
    def test_actual_compact_formatter_keeps_completion_and_scanner_proof(self):
        wrapper=(ROOT/'src/helpers/radiolib/RadioLibWrappers.h').read_text()
        method=body(wrapper,'bool formatWarmRxRefresh(').replace(' override','')
        source=r'''
#include <helpers/radiolib/RxpsPulse.h>
#include <helpers/radiolib/RxpsPulseScanLease.h>
#include <helpers/radiolib/WarmRxRefresh.h>
#include <new>
#include <cassert>
#include <cstring>
#include <type_traits>
static_assert(std::is_trivially_copyable<RxpsPulse>::value,"Scalar snapshot remains copyable");
struct Wrapper {
  RxpsPulse _rxps_pulse;
  RxpsPulseScanLease _agc_scan_lease;
  struct Profiles { bool enabled() const { return true; } } _profiles;
  bool supportsWarmRxRefresh() const { return true; }
@FORMAT@
};
int main() {
  // Placement construction on dirty storage proves initialization is complete
  // for stack/heap objects, as well as zero-initialized firmware globals.
  alignas(RxpsPulse) unsigned char dirty[sizeof(RxpsPulse)];
  memset(dirty,0xA5,sizeof(dirty));
  auto& pulse=*new(dirty) RxpsPulse;
  assert(pulse.rx_us==117844 && pulse.rx_ticks==7542 && pulse.sleep_us==0);
  assert(!pulse.pending && !pulse.host_timed && !pulse.failed && !pulse.packet);
  assert(pulse.req==0 && pulse.ok==0 && pulse.fail==0 && pulse.cycles==0);
  assert(pulse.phase==RxpsPulse::Phase::Idle && pulse.takeCompletion()==RxpsPulse::Result::None);
  assert(pulse.total.n==0 && pulse.total.minimum==0 && pulse.recovery.maximum==0);
  alignas(RxpsPulseScanLease) unsigned char lease_dirty[sizeof(RxpsPulseScanLease)];
  memset(lease_dirty,0xA5,sizeof(lease_dirty));
  auto& lease=*new(lease_dirty) RxpsPulseScanLease;
  assert(!lease.active && !lease.stale && !lease.fast_retry && !lease.visit_started_us && !lease.completed);
  alignas(WarmRxRefresh) unsigned char warm_dirty[sizeof(WarmRxRefresh)];
  memset(warm_dirty,0xA5,sizeof(warm_dirty));
  auto& warm=*new(warm_dirty) WarmRxRefresh;
  assert(!warm.pending && !warm.attempting && warm.successes==0 && warm.warm_gap.count==0);
  Wrapper w;
  w._rxps_pulse.cadence(16000);
  assert(w._rxps_pulse.configure(3116,2600));
  w._rxps_pulse.request(1);w._rxps_pulse.admitted(2);
  w._rxps_pulse.armed(3,1000000);
  w._rxps_pulse.sample(4,false,false);
  w._rxps_pulse.sample(120000,true,false);
  w._rxps_pulse.sample(120500,false,false);
  w._rxps_pulse.rearming();w._rxps_pulse.receiveEntry(0,120600);
  assert(w._rxps_pulse.ok==1 && w._rxps_pulse.fail==0 && w._rxps_pulse.cycles==1);
  assert(w._rxps_pulse.takeCompletion()==RxpsPulse::Result::Applied);
  w._agc_scan_lease.begin(1,4,5,6,32,7,8);w._agc_scan_lease.finish(100);
  char reply[160];
  assert(w.formatWarmRxRefresh(0,16,reply,sizeof(reply)));
  assert(strstr(reply,"method=rxps,interval=16,req=1,admit=1") && strstr(reply,"fail=0,ok=1"));
  assert(w.formatWarmRxRefresh(3,16,reply,sizeof(reply)) && strstr(reply,"cycles=1"));
  assert(w.formatWarmRxRefresh(6,16,reply,sizeof(reply)) && strstr(reply,"admit=1") && strstr(reply,"n=1"));
  for(unsigned page : {1U,2U,4U,5U}) {
#if MESH_COMPACT_STM32_RADIO
    strcpy(reply,"unchanged");
    assert(!w.formatWarmRxRefresh(page,16,reply,sizeof(reply)) && !strcmp(reply,"unchanged"));
#else
    assert(w.formatWarmRxRefresh(page,16,reply,sizeof(reply)));
#endif
  }
}
'''.replace('@FORMAT@',method).replace('#include <new>','#include <new>\n#include <initializer_list>')
        with tempfile.TemporaryDirectory(prefix='compact-agc-state-') as tmp:
            path=Path(tmp);cpp=path/'main.cpp';cpp.write_text(source)
            for compact in (0,1):
                with self.subTest(compact=compact):
                    binary=path/f'case{compact}'
                    build=subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror',
                        '-fsanitize=address,undefined','-fno-pie','-no-pie',
                        '-DMESH_COMPACT_STM32_RADIO='+str(compact),'-I',str(ROOT/'src'),str(cpp),'-o',str(binary)],
                        capture_output=True,text=True,timeout=30)
                    self.assertEqual(build.returncode,0,build.stdout+build.stderr)
                    run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
                    self.assertEqual(run.returncode,0,run.stdout+run.stderr)

if __name__=='__main__': unittest.main()
