from pathlib import Path
import subprocess, sys, tempfile
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'test'))
from test_lr2021_apply_transaction import method
source=(ROOT/'src/helpers/radiolib/W12PramScannerLab.inc').read_text(encoding='utf8')
sigs=['void labServiceAutomatic(', 'void labServiceUncertain()', 'bool labScannerSetAutomatic(', 'bool labReconfigureBlocked()', 'void labStopForProfileTransition()', 'bool labRxProfileCurrent()']
parts=[method(source,x).replace(' override','') for x in sigs]
code=r'''
#include <cassert>
#include <cstdint>
#include <helpers/radiolib/W12PramScannerCodec.h>
#include <helpers/radiolib/W12PramAutoPolicy.h>
#define RADIOLIB_ERR_UNKNOWN -1
static uint32_t now=100; static uint32_t millis(){return now;}
namespace mesh {enum class RadioParamApplyResult {APPLIED,BUSY,FAILED};}
struct Fake {
  mesh::w12pram::AutoPolicy _lab_auto;
  struct {uint32_t generation[2]={1,2}; bool active=true, recommendation=true; bool enabled()const{return active;} bool recommendationReady()const{return recommendation;}} _profiles;
  struct {uint32_t generation;} _lab_snap[2]={{1},{2}};
  bool _lab_uncertain=false,_lab_sleeping=false,_lab_resume_pending=false,_lab_session_ldo=false,_lab_pram_loaded=false;
  bool _profile_repair_required=false,_lab_scan_active=false, supported=true, stop_ok=true, chip_busy=false;
  bool _lab_transition_waiting=false; uint32_t _lab_transition_at=0;
  int state=1, cold=0, warm=0, stops=0, errors=0, start_result=0, reset_calls=0;
  uint32_t _lab_recoveries=0, _lab_resume_at=0, _lab_uncertain_at=0, _lab_ambiguous_recoveries=0, n_recv_errors=0;
  bool labScannerProfilesSupported(){return supported;}
  bool labScannerStart(bool resume){
    if(resume)++warm;else ++cold;
    if(start_result==1)return false; // BUSY, do not count as hardware failure
    if(start_result==2){++_lab_recoveries;_lab_auto.block(mesh::w12pram::AutoPolicy::Reason::Failed);return false;}
    _lab_scan_active=true;_lab_session_ldo=true;_lab_resume_pending=false;_lab_auto.active();return true;
  }
  bool labScannerStop(){++stops;_lab_resume_pending=false;if(!stop_ok)return false;_lab_scan_active=false;_lab_session_ldo=false;return true;}
  mesh::RadioParamApplyResult labPauseScanner(){return stop_ok ? mesh::RadioParamApplyResult::APPLIED : mesh::RadioParamApplyResult::BUSY;}
  bool isChipBusy(){return chip_busy;}
  void labMarkUncertain(){_lab_uncertain=true;}
  void labRecover(int,mesh::w12pram::RecoveryOrigin){++reset_calls;++_lab_recoveries;_lab_auto.block(mesh::w12pram::AutoPolicy::Reason::Failed);}
@AUTO@
@UNCERTAIN@
@SET@
@RECONFIG@
@TRANSITION@
@CURRENT@
};
int main(){using Reason=mesh::w12pram::AutoPolicy::Reason;
  {Fake f;f._profiles.active=false;f.labServiceAutomatic(true,false);assert(f.cold==0);f._profiles.active=true;f.labServiceAutomatic(true,false);assert(f.cold==1&&f._lab_scan_active);}
  {Fake f;f._profiles.recommendation=false;f.labServiceAutomatic(true,false);assert(f.cold==0&&f.warm==0);f._profiles.recommendation=true;f.labServiceAutomatic(true,false);assert(f.cold==1);}
  {Fake f;f._lab_resume_pending=true;f._lab_pram_loaded=true;f._lab_resume_at=now;f._profiles.recommendation=false;f.labServiceAutomatic(true,false);assert(f.warm==0&&f._lab_resume_pending);f._profiles.recommendation=true;f.labServiceAutomatic(true,false);assert(f.warm==1);}
  {Fake f;assert(f.labScannerSetAutomatic(false));f.labServiceAutomatic(true,false);assert(f.cold==0&&!f._lab_auto.enabled());assert(f.labScannerSetAutomatic(true));f.labServiceAutomatic(true,false);assert(f.cold==1);}
  {Fake f;f.supported=false;f.labServiceAutomatic(true,false);assert(f.cold==0&&f._lab_auto.reason()==Reason::Unsupported);f.supported=true;f.labServiceAutomatic(true,false);assert(f.cold==0);++f._profiles.generation[1];f.labServiceAutomatic(true,false);assert(f.cold==1);}
  {Fake f;f.start_result=1;f.labServiceAutomatic(true,false);assert(f.cold==1);now+=999;f.labServiceAutomatic(true,false);assert(f.cold==1);now++;f.start_result=0;f.labServiceAutomatic(true,false);assert(f.cold==2);}
  {Fake f;f.start_result=2;f.labServiceAutomatic(true,false);assert(f.cold==1&&f._lab_auto.reason()==Reason::Failed);now+=5000;f.labServiceAutomatic(true,false);assert(f.cold==1);++f._profiles.generation[0];f.start_result=0;f.labServiceAutomatic(true,false);assert(f.cold==2);}
  {Fake f;f._lab_session_ldo=true;f._lab_resume_pending=true;f._profiles.active=false;f.stop_ok=false;f.labServiceAutomatic(true,false);assert(f.stops==1&&f.warm==0);f.stop_ok=true;f.labServiceAutomatic(true,false);assert(f.stops==2&&!f._lab_session_ldo);}
  {Fake f;f._lab_resume_pending=true;f._lab_session_ldo=true;f._lab_pram_loaded=true;f._lab_resume_at=now;f.labServiceAutomatic(true,false);assert(f.warm==1&&f.cold==0);}
  {Fake f;f._lab_resume_pending=true;f._lab_session_ldo=true;f._lab_pram_loaded=false;f._lab_resume_at=now;f.labServiceAutomatic(true,false);assert(f.warm==0&&f.cold==1);}
  {Fake f;f._lab_uncertain=true;f._lab_uncertain_at=now;f.labServiceAutomatic(true,false);assert(f.cold==0);now+=999;f.labServiceUncertain();assert(f.reset_calls==0);now++;f.chip_busy=true;f.labServiceUncertain();assert(f.reset_calls==0);now+=4000;f.labServiceUncertain();assert(f.reset_calls==1&&!f._lab_uncertain&&f._lab_ambiguous_recoveries==1&&f.n_recv_errors==1&&f._lab_auto.blocked());f.labServiceUncertain();assert(f.reset_calls==1);}
  {Fake f;f._lab_uncertain=true;f._lab_scan_active=true;assert(f.labReconfigureBlocked());f._lab_uncertain=false;f.stop_ok=false;assert(f.labReconfigureBlocked());f.stop_ok=true;assert(!f.labReconfigureBlocked()&&f._lab_resume_pending);}
  {Fake f;f.stop_ok=false;f.labStopForProfileTransition();assert(!f._lab_uncertain&&f.stops==1&&f.reset_calls==0);now+=4999;f.labStopForProfileTransition();assert(!f._lab_uncertain);f.stop_ok=true;f.labStopForProfileTransition();assert(!f._lab_uncertain&&!f._lab_transition_waiting);}
  {Fake f;f.stop_ok=false;f.labStopForProfileTransition();now+=5000;f.labStopForProfileTransition();assert(f._lab_uncertain&&f.reset_calls==0);}
  {Fake f;assert(f.labRxProfileCurrent());++f._profiles.generation[1];assert(!f.labRxProfileCurrent()&&!f._lab_uncertain&&f.reset_calls==0);}
  {Fake f;f._profiles.active=false;assert(!f.labRxProfileCurrent()&&!f._lab_uncertain);}
}
'''
for name,body in zip(['@AUTO@','@UNCERTAIN@','@SET@','@RECONFIG@','@TRANSITION@','@CURRENT@'],parts):code=code.replace(name,body)
recv=method(source, 'int recvRaw(')
assert recv.index('labRxProfileCurrent()') < recv.index('claimRadioInterrupt()'), 'stale RX must be rejected before IRQ claim'
wrapper=(ROOT/'src/helpers/radiolib/CustomLR2021Wrapper.h').read_text(encoding='utf8')
deep_init=method(wrapper, 'bool radioDeepInit()')
assert deep_init.index('_lab_pram_loaded=false') < deep_init.index('prepareRadioHardReset()'), 'all deep resets must invalidate the PRAM image'
with tempfile.TemporaryDirectory() as d:
    p=Path(d);(p/'main.cpp').write_text(code)
    result=subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(ROOT/'src'),str(p/'main.cpp'),'-o',str(p/'test')],capture_output=True,text=True)
    if result.returncode: raise SystemExit(result.stderr)
    subprocess.run([str(p/'test')],check=True)
print('PASS scanner auto, configuration, fallback and bounded uncertain recovery')
