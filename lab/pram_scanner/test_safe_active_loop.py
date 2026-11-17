"""Run the real scanner loop through safe-mode and exclusive-profile handoffs."""
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
exclusive = method(source, 'mesh::RadioParamApplyResult labReleaseForExclusive()')
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
namespace mesh {
enum class RadioParamApplyResult:uint8_t { APPLIED, BUSY, FAILED };
namespace lr2021 { bool safe=false; bool safeMode() { return safe; } }
}
struct RadioLibWrapper { unsigned host_loops=0; void loop() { ++host_loops; } };
struct Fake : RadioLibWrapper {
  struct {
    uint32_t generation[2]={1,2};
    bool secondary_only=false;
    bool enabled() const { return true; }
    bool secondaryOnly() const { return secondary_only; }
  } _profiles;
  struct { uint32_t generation; } _lab_snap[2]={{1},{2}};
  struct { bool active=true; bool enabled() const { return active; } } _lab_auto;
  bool _lab_uncertain=false, _lab_scan_active=true, _lab_transition_waiting=false;
  bool _lab_session_ldo=false, automatic_ok=true;
  bool _cw_active=false, _loop_event_pending=false, stop_ok=false;
  bool _lab_exclusive_restore_auto=false, _lab_user_auto_off=false;
  uint32_t _lab_exclusive_until=0;
  uint32_t _lab_transition_at=0, _lab_last_poll=0, _lab_prev_cads=0, _lab_progress_at=0;
  int stops=0, polls=0, automatic_calls=0;
  mesh::w12pram::Status _lab_status={};
  bool labScannerStop() { ++stops; if (stop_ok) _lab_scan_active=false; return stop_ok; }
  void labMarkUncertain() { _lab_uncertain=true; }
  void labServiceUncertain() {}
  void labServiceAutomatic(bool,bool) {}
  void labServiceHandoffRetry(bool,bool) {}
  bool labMayRestoreExclusive() const { return true; }
  bool labExclusivePending() const {
    return _lab_exclusive_until && int32_t(millis()-_lab_exclusive_until)<0;
  }
  bool labScannerSetAutomatic(bool enabled) {
    ++automatic_calls;
    if (!automatic_ok) return false;
    _lab_auto.active=enabled;
    if (!enabled) _lab_scan_active=false;
    return true;
  }
  bool isChipBusy() { return false; }
  bool hasPendingRadioInterrupt() { return false; }
  int16_t labScannerCommand(uint8_t,const uint8_t*,size_t,mesh::w12pram::Status& status) {
    ++polls; status.hooked=1; return 0;
  }
  @EXCLUSIVE@
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
  // Exclusive host reception cannot start while the scanner release is BUSY.
  // Once released, the real helper retains the automatic-restoration lease.
  mesh::lr2021::safe=false;
  Fake exclusive;
  exclusive._profiles.secondary_only=true;
  exclusive.automatic_ok=false;
  exclusive.loop();
  assert(exclusive.automatic_calls==1 && exclusive._lab_scan_active);
  assert(exclusive.host_loops==0 && exclusive.polls==0 && exclusive.stops==0);
  assert(!exclusive._lab_exclusive_restore_auto);
  ++now; exclusive.automatic_ok=true; exclusive.loop();
  assert(exclusive.automatic_calls==2 && !exclusive._lab_scan_active);
  assert(exclusive.host_loops==1 && exclusive._lab_exclusive_restore_auto);
  assert(exclusive._lab_exclusive_until==now+5000 && exclusive.labExclusivePending());
  exclusive.loop();
  assert(exclusive.automatic_calls==2 && exclusive.host_loops==2);
}
'''.replace('@LOOP@', loop).replace('@TRANSITION@', transition).replace('@EXCLUSIVE@', exclusive)
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory)
    (path / 'test.cpp').write_text(code)
    result = subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                             '-I', str(ROOT / 'src'), str(path / 'test.cpp'),
                             '-o', str(path / 'test')], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(result.stderr)
    subprocess.run([str(path / 'test')], check=True)
print('PASS safe mode stops active scanner and retries safe/exclusive busy handoffs')
