"""Exercise the real ESP32 admission and on-chip scanner idle budget."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CODE = r'''
#include <cassert>
#include <cstdint>
#include <helpers/esp32/ProfileIdleSleep.h>
#include <helpers/radiolib/W12PramScannerCodec.h>
static uint32_t clock_us = 0;
int64_t esp_timer_get_time() { return clock_us; }
struct Mesh {
  bool autonomous=true, active=true, hooked=true, blocked=false, generations=true;
  uint32_t now_ms=0, last_poll_ms=0, forced=0;
  bool hasAutonomousDualRadioIdle() const { return autonomous && active && hooked; }
  bool getDualRadioIdleDelay(uint32_t& delay) const {
    if (forced) { delay=forced; return true; }
    delay=mesh::w12pram::idleBudget(active,hooked,blocked,generations,now_ms,
        last_poll_ms,autonomous ? mesh::esp32::autonomousProfileSleepSliceMillis()
                                : mesh::esp32::profileSleepSliceMillis());
    return delay != 0;
  }
};
struct Board {
  unsigned attempts=0; uint32_t deadline=0;
  void sleepUntilMicroseconds(uint32_t value) { ++attempts; deadline=value; }
};
int main() {
  using namespace mesh::esp32;
  Mesh m; Board b;
  assert(profileSleepSliceMillis()==10);
  assert(autonomousProfileSleepSliceMillis()==1000);
  clock_us=100000;
  assert(tryProfileIdleSleep(m,b,clock_us,30000));
  assert(b.attempts==1 && b.deadline==1100000); // one-second health poll
  assert(tryProfileIdleSleep(m,b,clock_us,23));
  assert(b.deadline==123000); // queue, retry, advert or other role deadline wins
  m.now_ms=997;
  assert(tryProfileIdleSleep(m,b,clock_us,30000));
  assert(b.deadline==103000); // poll imminent
  m.now_ms=1000; assert(!tryProfileIdleSleep(m,b,clock_us,30000));
  m.now_ms=0; m.blocked=true; assert(!tryProfileIdleSleep(m,b,clock_us,30000));
  m.blocked=false; m.generations=false; assert(!tryProfileIdleSleep(m,b,clock_us,30000));
  m.generations=true; m.hooked=false; assert(!tryProfileIdleSleep(m,b,clock_us,30000));
  m.hooked=true; m.active=false; assert(!tryProfileIdleSleep(m,b,clock_us,30000));
  m.active=true; m.autonomous=false;
  assert(tryProfileIdleSleep(m,b,clock_us,30000) && b.deadline==110000);
  m.forced=11; assert(!tryProfileIdleSleep(m,b,clock_us,30000)); // ordinary cap intact
  m.autonomous=true; m.forced=1001; assert(!tryProfileIdleSleep(m,b,clock_us,30000));
  m.forced=0; clock_us=0xfffffff0u;
  assert(tryProfileIdleSleep(m,b,clock_us,30) && b.deadline==uint32_t(clock_us+30000));
  assert(!tryProfileIdleSleep(m,b,clock_us-30000,30)); // role deadline consumed
  powerTestControls().sleep_enabled=false;
  assert(!tryProfileIdleSleep(m,b,clock_us,30000));
}
'''

# Also exercise the actual W12 override, including the physical IRQ check,
# rather than just a test model of its admission rules.
scanner_source = (ROOT / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
start = scanner_source.index('  bool getProfileIdleDelay(uint32_t& delay_ms) const override {')
end = scanner_source.index('\n  }', start) + len('\n  }')
scanner_method = scanner_source[start:end]
SCANNER_CODE = r'''
#include <cassert>
#include <cstdint>
#include <helpers/esp32/PowerTestControls.h>
#include <helpers/radiolib/W12PramScannerCodec.h>
constexpr int HIGH=1, P_LORA_DIO_1=14;
static uint32_t now=0;
static bool physical_irq=false;
uint32_t millis() { return now; }
int digitalRead(int) { return physical_irq ? HIGH : 0; }
struct RadioLibWrapper {
  virtual bool getProfileIdleDelay(uint32_t& out) const { out=10; return true; }
};
struct Profiles { bool on=true; uint32_t generation[2]={1,2}; bool enabled() const { return on; } };
struct Snapshot { uint32_t generation=0; };
struct Scanner:RadioLibWrapper {
  bool _lab_scan_active=true, _lab_uncertain=false, _lab_sleeping=false, _lab_resume_pending=false;
  bool _profile_repair_required=false, _cw_active=false, receiving=true, pending=false;
  mesh::w12pram::Status _lab_status{}; Profiles _profiles; Snapshot _lab_snap[2]={{1},{2}};
  uint32_t _lab_last_poll=0;
  Scanner() { _lab_status.hooked=1; }
  bool isInRecvMode() const { return receiving; }
  bool hasPendingRadioInterrupt() const { return pending; }
@METHOD@
};
int main() {
  Scanner s; uint32_t ms=0;
  assert(s.getProfileIdleDelay(ms) && ms==1000);
  now=995; assert(s.getProfileIdleDelay(ms) && ms==5);
  now=1000; assert(!s.getProfileIdleDelay(ms) && ms==0);
  now=0; s.pending=true; assert(!s.getProfileIdleDelay(ms)); s.pending=false;
  physical_irq=true; assert(!s.getProfileIdleDelay(ms)); physical_irq=false;
  s._lab_uncertain=true; assert(!s.getProfileIdleDelay(ms)); s._lab_uncertain=false;
  s._lab_resume_pending=true; assert(!s.getProfileIdleDelay(ms)); s._lab_resume_pending=false;
  ++s._profiles.generation[1]; assert(!s.getProfileIdleDelay(ms)); --s._profiles.generation[1];
  s._lab_status.hooked=0; assert(!s.getProfileIdleDelay(ms)); s._lab_status.hooked=1;
  s._lab_scan_active=false; assert(s.getProfileIdleDelay(ms) && ms==10);
}
'''.replace('@METHOD@', scanner_method)

with tempfile.TemporaryDirectory(prefix='w12-pram-sleep-') as directory:
    folder = Path(directory)
    (folder / 'esp_timer.h').write_text('#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time();\n')
    source = folder / 'test.cpp'
    source.write_text(CODE)
    executable = folder / 'test'
    subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', '-fno-pie', '-no-pie', '-DMESH_W12_PRAM_EXPERIMENT=1',
                    '-I', str(folder), '-I', str(ROOT / 'src'), str(source), '-o', str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
    source.write_text(SCANNER_CODE)
    subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', '-fno-pie', '-no-pie', '-DMESH_W12_PRAM_EXPERIMENT=1',
                    '-I', str(folder), '-I', str(ROOT / 'src'), str(source), '-o', str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
print('PASS autonomous scanner bounded sleep and ordinary scanner cap')
