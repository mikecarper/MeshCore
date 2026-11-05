#!/usr/bin/env python3
"""Exercise the real scanner watchdog loop with simulated IRQ races."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
loop = source.index('  void loop() override {')
start = source.index('    const uint32_t now=millis();', loop)
end = source.index('\n  }\n  int recvRaw(', start)
watchdog = source[start:end]

program = r'''
#include <cassert>
#include <cstdint>
#include "helpers/radiolib/W12PramScannerCodec.h"
constexpr int HIGH=1, P_LORA_DIO_1=14;
constexpr int16_t RADIOLIB_ERR_NONE=0, RADIOLIB_ERR_UNKNOWN=-1;
constexpr int16_t RADIOLIB_ERR_SPI_CMD_TIMEOUT=-705;
static uint32_t clock_ms=25000;
static bool physical=false, race_high=false;
static int pin_reads=0;
static uint32_t millis() { return clock_ms; }
static int digitalRead(int) {
  ++pin_reads;
  return (physical || (race_high && pin_reads >= 2)) ? HIGH : 0;
}
class Driver {
public:
  uint32_t _lab_last_poll=0, _lab_progress_at=0, _lab_prev_cads=0;
  mesh::w12pram::Status _lab_status{}, response{}, repaired{};
  bool _loop_event_pending=false, pending=false;
  int recoveries=0, command_result=0, repair_result=0, repair_calls=0, last_error=0;
  mesh::w12pram::RecoveryOrigin origin=mesh::w12pram::RecoveryOrigin::None;
  bool isChipBusy() const { return false; }
  bool hasPendingRadioInterrupt() const { return pending || _loop_event_pending; }
  int16_t labScannerCommand(int op, const void*, int, mesh::w12pram::Status& out) {
    if (op==7) { ++repair_calls; out=repaired; return repair_result; }
    out=response; return command_result;
  }
  void labRecover(int16_t reason, mesh::w12pram::RecoveryOrigin why) {
    last_error=reason; origin=why; ++recoveries;
  }
  void poll() {
''' + watchdog + r'''
  }
};
static Driver stalled() {
  Driver d; d.response.hooked=1; d.response.state=3;
  physical=false; race_high=false; pin_reads=0;
  return d;
}
int main() {
  auto d=stalled(); d.poll(); assert(d.recoveries==1 && d.last_error==-705
      && d.origin==mesh::w12pram::RecoveryOrigin::NoCad); // genuine stall
  d=stalled(); d.command_result=-705; d.poll();
  assert(d.recoveries==1 && d.last_error==-705
      && d.origin==mesh::w12pram::RecoveryOrigin::StatusPoll); // same error, different source
  d=stalled(); d.response.hooked=0; d.poll();
  assert(d.recoveries==1 && d.last_error==-1
      && d.origin==mesh::w12pram::RecoveryOrigin::StatusPoll);
  d=stalled(); d.pending=true; d.poll(); assert(d.recoveries==0);
  d=stalled(); physical=true; d.poll(); assert(d.recoveries==0);
  d=stalled(); race_high=true; d.poll(); assert(d.recoveries==0); // late DIO edge
  d=stalled(); d.response.state=4; d.response.irq=1u<<18;
  d.poll(); assert(d.recoveries==0 && d._loop_event_pending); // parked RxDone
  d=stalled(); d.response.irq=1u<<18;
  d.poll(); assert(d.recoveries==0); // RxDone before the park transition
  d=stalled(); d.response.cads=1; d.poll();
  assert(d.recoveries==0 && d._lab_progress_at==clock_ms);
  d=stalled(); d.response.timeout_known=true; d.repaired=d.response;
  d.repaired.timeout_armed=true; d.repaired.timer_repaired=true; d.poll();
  assert(d.repair_calls==1 && d.recoveries==0 && d._lab_progress_at==clock_ms);
  d=stalled(); d.response.timeout_known=true; d.repaired=d.response;
  d.repaired.timeout_armed=true; d.poll(); // no-op must not refresh watchdog
  assert(d.repair_calls==1 && d.recoveries==1);
  d=stalled(); d.response.timeout_known=true; d.repaired=d.response;
  d.repaired.state=4; d.repaired.irq=1u<<18; d.poll();
  assert(d.repair_calls==1 && d.recoveries==0 && d._loop_event_pending);
  d=stalled(); d.response.timeout_known=true; d.response.irq=1u<<21; d.poll();
  assert(d.repair_calls==0); // already-pending timeout is left to the chip
}
'''

with tempfile.TemporaryDirectory() as td:
    cpp = Path(td) / 'watchdog.cpp'
    exe = Path(td) / 'watchdog'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', '-I', str(root / 'src'), str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS scanner watchdog preserves pending RX and recovers a true stall')
