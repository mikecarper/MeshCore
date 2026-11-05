"""Run the real scanner loop and verify safe mode stops an active session."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'test'))
from test_lr2021_apply_transaction import method

source = (ROOT / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
loop = method(source, 'void loop() override').replace(' override', '')
transition = method(source, 'void labStopForProfileTransition()')
code = r'''
#include <cassert>
#include <cstdint>
#include <helpers/radiolib/W12PramScannerCodec.h>
#define RADIOLIB_ERR_NONE 0
#define P_LORA_DIO_1 14
#define HIGH 1
static uint32_t now=100;
static uint32_t millis() { return now; }
static int digitalRead(int) { return 0; }
namespace mesh { namespace lr2021 { bool safe=false; bool safeMode() { return safe; } } }
struct RadioLibWrapper { void loop() {} };
struct Fake : RadioLibWrapper {
  struct { uint32_t generation[2]={1,2}; bool enabled() const { return true; } } _profiles;
  struct { uint32_t generation; } _lab_snap[2]={{1},{2}};
  bool _lab_uncertain=false, _lab_scan_active=true, _lab_transition_waiting=false;
  bool _cw_active=false, _loop_event_pending=false, stop_ok=false;
  bool _lab_exclusive_restore_auto=false;
  uint32_t _lab_transition_at=0, _lab_last_poll=0, _lab_prev_cads=0, _lab_progress_at=0;
  int stops=0, polls=0;
  mesh::w12pram::Status _lab_status={};
  bool labScannerStop() { ++stops; if (stop_ok) _lab_scan_active=false; return stop_ok; }
  void labMarkUncertain() { _lab_uncertain=true; }
  void labServiceUncertain() {}
  void labServiceAutomatic(bool,bool) {}
  bool labExclusivePending() { return false; }
  bool labScannerSetAutomatic(bool) { return true; }
  bool isChipBusy() { return false; }
  bool hasPendingRadioInterrupt() { return false; }
  int16_t labScannerCommand(uint8_t,const uint8_t*,size_t,mesh::w12pram::Status& status) {
    ++polls; status.hooked=1; return 0;
  }
  @TRANSITION@
  @LOOP@
};
int main() {
  Fake f;
  f.loop(); assert(f.stops==0 && f._lab_scan_active);
  mesh::lr2021::safe=true;
  f.loop(); assert(f.stops==1 && f._lab_scan_active && f._lab_transition_waiting);
  ++now; f.stop_ok=true; f.loop();
  assert(f.stops==2 && !f._lab_scan_active && !f._lab_transition_waiting);
  f.loop(); assert(f.stops==2 && !f._lab_scan_active);
}
'''.replace('@LOOP@', loop).replace('@TRANSITION@', transition)
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory)
    (path / 'test.cpp').write_text(code)
    result = subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                             '-I', str(ROOT / 'src'), str(path / 'test.cpp'),
                             '-o', str(path / 'test')], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(result.stderr)
    subprocess.run([str(path / 'test')], check=True)
print('PASS safe mode stops active scanner and retries a busy handoff')
