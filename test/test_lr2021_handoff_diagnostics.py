"""Compile the real PRAM handoff with diagnostics both disabled and enabled."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class LR2021HandoffDiagnosticsTests(unittest.TestCase):
    def test_observation_preserves_short_circuit_fifo_and_first_failure(self):
        compiler = os.environ.get('CXX') or shutil.which('g++')
        if not compiler:
            self.skipTest('Host C++ compiler required')
        source = (ROOT / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
        shared = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
        methods = '#if MESH_AGC_TIMING_DIAGNOSTICS\n' + body(source, 'void labCaptureHandoffFailure(') + '\n#endif\n'
        methods += '#if MESH_WARM_RX_REFRESH\n' + body(source, 'bool labMaintenanceColdLeaseCurrent(') + '\n' + body(source, 'bool labQuiesceMaintenanceColdReset(') + '\n#endif\n'
        methods += '\n'.join(body(source, signature) for signature in (
            'bool labWarmRestore(',
            'void labScannerHandoffDiag(', 'mesh::RadioParamApplyResult labPauseScanner(', 'bool labScannerStart('))
        code = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <vector>
#include <string>
#include <helpers/radiolib/W12PramScannerCodec.h>
#include <helpers/radiolib/LR2021HandoffDiagnostics.h>
#include <helpers/radiolib/LR2021ApplyStatus.h>
#include <helpers/radiolib/RxpsPulse.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -1
#define HIGH 1
#define P_LORA_DIO_1 14
#define LR2021_IRQ_DIO 1
#define RADIOLIB_LR2021_IRQ_ERROR (1u<<16)
#define RADIOLIB_LR2021_IRQ_CMD_ERROR (1u<<17)
static const uint32_t w12_pram_scan_blob[]={0};
static bool pin_high=false;
int digitalRead(int){return pin_high;}
uint32_t micros(){return 123000;}
uint32_t millis(){return 123;}
namespace mesh {
enum class RadioParamApplyResult{APPLIED,BUSY,FAILED};
enum class RadioProfileProtocol{MeshCore};
namespace lr2021 {bool safeMode(){return false;}}
}
struct CustomLR2021{
  int16_t standby_rc=0,fifo_rc=0;
  uint32_t raw_irq=0x20;uint16_t fifo=0;
  unsigned raw_reads=0,fifo_reads=0,dccm_reads=0;
  unsigned image_writes=0,activations=0;bool registered=true;
  std::vector<std::string> events;
  int fail_stage=0;bool correct_ident=true;
  unsigned hard_resets=0,calibrations=0,status_reads=0;
  uint8_t mode=4;int16_t status_rc=0,calibration_rc=0;
  bool inject_standby_irq=false;
  std::vector<float> calibrated;
  int16_t agcPulseStatus(uint32_t& irq,uint8_t& observed){++status_reads;irq=raw_irq;observed=mode;return status_rc;}
  int16_t resetReceiverCalibration(const float* frequencies,size_t count,bool sleep){
    events.push_back("calibrate");++calibrations;assert(count==2&&!sleep);
    calibrated.assign(frequencies,frequencies+count);return calibration_rc;
  }
  int16_t setRegulatorLDO(){return fail_stage==3?-703:0;}
  int16_t getVersion(uint8_t* major,uint8_t* minor){events.push_back("version");*major=1;*minor=0x18;return fail_stage==4?-704:0;}
  int16_t writeRegMem32(uint32_t addr,const uint32_t*,size_t){if(addr==0x801000){++image_writes;events.push_back("image");}return fail_stage==10?-710:0;}
  int16_t readRegMem32(uint32_t address,uint32_t* out,size_t count){
    ++dccm_reads;assert((address==0x8007F0&&count==2)||(address==0x800FF8&&count==2)||(address==0x806034&&count==1));
    for(size_t i=0;i<count;++i)out[i]=address+uint32_t(i*4);
    return 0;
  }
  int16_t activatePram(){events.push_back("activate");++activations;if(fail_stage==5)return -705;registered=true;return 0;}
  int16_t setRxTxFallbackMode(int){return fail_stage==7?-707:0;}
  int16_t setDioIrqConfig(int,uint32_t){return fail_stage==9?-709:0;}
  int16_t labSpiCommand(uint16_t op,bool,uint8_t* data,size_t,const uint8_t* =nullptr,size_t=0){
    if(op==0x012D){events.push_back("hello");memcpy(data,correct_ident?"SCN3":"BAD!",4);return !registered?-707:fail_stage==6?-706:0;}
    assert(op==0x0209);return fail_stage==8?-708:0;
  }
  int16_t standby(){events.push_back("standby");mode=1;if(inject_standby_irq)raw_irq|=1u<<18;return standby_rc;}
  void labSetPramScan(bool){}
  uint32_t getIrqStatus(){++raw_reads;return raw_irq;}
  int16_t getRxFifoLevel(uint16_t* out){++fifo_reads;*out=fifo;return fifo_rc;}
};
struct Fake{
 CustomLR2021 radio;CustomLR2021* _radio=&radio;
 bool _lab_scan_active=true,_lab_uncertain=false,_params_valid=true;
 bool _profile_refresh_required=true,_profile_repair_required=false;
 bool _lab_sleeping=false,_lab_session_ldo=false,_lab_pram_loaded=true,_rx_ps_enabled=false,_cw_active=false,_lab_resume_pending=false;
 bool _agc_pram_restore_owned=false;
 bool _agc_pram_paused=false,_agc_pram_cold_restore_owned=false,_agc_pram_cold_tried=false,_agc_pram_arm_issued=false;
 uint32_t _agc_pram_generations[2]={11,22},_profile_retry_at=0;
 RxpsPulse _rxps_pulse;
 mesh::lr2021::ApplyStatus _apply_status;
 bool reinit_ok=true,exclusive=false;
 float correction=0;
 int16_t _lab_error=0;int _lab_last_profile=-1;
 uint32_t _lab_cold_starts=0,_lab_good=0,_lab_bad=0,_lab_good_profile[2]={},_lab_tx_pauses=0,_lab_tx_resumes=0;
 uint32_t _lab_resume_last=0,_lab_resume_min=UINT32_MAX,_lab_resume_max=0,_lab_last_poll=0,_lab_progress_at=0,_lab_prev_cads=0;
 float _lab_logical_mhz[2]={};uint8_t _lab_rx_preamble[2]={};mesh::w12pram::Profile _lab_snap[2]={};
 struct Auto{void active(){}}_lab_auto;
 mesh::w12pram::Status _lab_status={};
 bool activity=false,busy=false,software=false,params_ok=true,receive_ok=true;
 std::vector<bool> pending;
 unsigned pending_calls=0,activity_calls=0,param_calls=0;
 uint8_t _physical_preamble=32,_active_profile=1,state=1;
 uint32_t _profile_generation=22;
 uint32_t _lab_pause_last=0,_lab_pause_min=UINT32_MAX,_lab_pause_max=0;
 struct Profiles{
  struct Params{float freq=910.525f,bw=62.5f;uint8_t sf=7,cr=5;mesh::RadioProfileProtocol protocol=mesh::RadioProfileProtocol::MeshCore;};
  Params p;uint32_t generation[2]={11,22};bool enabled()const{return true;}
  const Params& params(uint8_t)const{return p;}
 }_profiles;
 mesh::w12pram::Status reply={};int16_t command_rc=0;
 int16_t start_command_rc=0;bool start_hooked=true;
#if MESH_AGC_TIMING_DIAGNOSTICS
 mesh::w12pram::HandoffDiagnostics _lab_handoff;
#endif
 bool hasPendingRadioInterrupt(){return pending_calls<pending.size()?pending[pending_calls++]: (++pending_calls,false);}
 bool hasPendingSoftwareRadioInterrupt(){return software;}
 bool isReceivingPacket(){++activity_calls;return activity;}
 bool isChipBusy(){return busy;}
 bool validateProfile(const Profiles::Params&){return true;}
 float hardwareFrequencyMHz(float f){return f+correction;}
 bool labExclusivePending(){return exclusive;}
 int16_t recordApply(mesh::lr2021::ApplyStatus::Stage stage,int16_t rc){return _apply_status.record(stage,rc);}
 uint8_t beginReconfigure(){return radio.standby()==0?1:3;}
 bool labQuiesceForReset(){return true;}
 bool labReinitialize(bool){radio.events.push_back("reset");++radio.hard_resets;_lab_pram_loaded=false;_lab_session_ldo=false;
   radio.registered=false;radio.mode=1;radio.raw_irq=0;return reinit_ok;}
 @PENDING@
 uint16_t profilePreamble(uint8_t){return 32;}
 void invalidateScanTuple(){}
 bool applyParams(float,float,uint8_t,uint8_t){++param_calls;return params_ok;}
 void cacheParams(float,float,uint8_t,uint8_t){}
 void startRecv(){state=receive_ok?1:0;}
 bool isInRecvMode(){return state==1;}
 void labMarkUncertain(){_lab_uncertain=true;}
 int16_t labScannerCommand(uint8_t op,const uint8_t*,size_t,mesh::w12pram::Status& out){
   if(op==1){radio.events.push_back("hook");out.hooked=start_hooked;return start_command_rc;}
   assert(op==6);out=reply;return command_rc;
 }
 @METHODS@
 Fake(){reply.stopped=true;reply.current=1;reply.irq=0x100000;}
 mesh::RadioParamApplyResult pause(){
#if MESH_AGC_TIMING_DIAGNOSTICS
  return labPauseScanner(false,0,"old");
#else
  return labPauseScanner(false,0);
#endif
 }
};
int main(){using R=mesh::RadioParamApplyResult;
 for(unsigned blocker=0;blocker<3;++blocker){
  Fake f;f.pending={false,false,false,false};
  if(blocker==0)f.pending={false,true};
  if(blocker==1)f.activity=true;
  if(blocker==2)f.pending={false,false,false,true};
  assert(f.pause()==R::BUSY&&f._lab_uncertain&&!f._lab_scan_active&&!f.param_calls);
  assert(f.pending_calls==(blocker==0?2:blocker==1?3:4));
  assert(f.activity_calls==(blocker==0?0:1));
#if MESH_AGC_TIMING_DIAGNOSTICS
  const auto& d=f._lab_handoff.first_failure;
  assert(d.reason==1&&d.detail==2&&d.current==1&&d.state==0&&d.op_irq==0x100000&&d.standby_rc==0);
  assert(d.guards[0]==(blocker==0?1:0));
  assert(d.guards[1]==(blocker==0?-1:blocker==1?1:0));
  assert(d.guards[2]==(blocker<2?-1:1));
  assert(d.raw&&d.raw_irq==0x20&&d.fifo==0&&d.fifo_rc==0);
  assert(f.radio.raw_reads==1&&f.radio.fifo_reads==1);
  char out[160];f.labScannerHandoffDiag(out,sizeof(out));assert(strstr(out,"old")&&strstr(out,"why=1/2")&&strstr(out,"bad=1"));
  f._lab_scan_active=true;f._lab_uncertain=false;f.pending.clear();f.activity=false;
  assert(f.pause()==R::APPLIED);f.labScannerHandoffDiag(out,sizeof(out));
  assert(strstr(out,"why=1/2")&&f._lab_handoff.first_failure.number==1&&f._lab_handoff.attempts==2);
  f.labCaptureHandoffFailure(2,0,false);
  assert(f.radio.raw_reads==1&&f.radio.fifo_reads==1&&f._lab_handoff.failures==2);
  assert(f._lab_handoff.first_failure.reason==1); // recovery cannot overwrite evidence
#else
  assert(!f.radio.raw_reads&&!f.radio.fifo_reads&&!f.radio.dccm_reads);
  char out[64];f.labScannerHandoffDiag(out,sizeof(out));assert(strstr(out,"not built"));
#endif
 }
 {Fake f;assert(f.pause()==R::APPLIED&&!f._lab_uncertain&&f.param_calls==1&&f._active_profile==0&&f._profile_generation==11);
  assert(!f.radio.raw_reads&&!f.radio.fifo_reads);}
#if MESH_AGC_TIMING_DIAGNOSTICS
 {Fake f;f.command_rc=-705;assert(f.pause()==R::FAILED&&f._lab_uncertain);
  assert(f._lab_handoff.first_failure.reason==3&&!f.radio.raw_reads&&!f.radio.fifo_reads);}
 {Fake f;f.pending={false,true};f.software=true;pin_high=true;f.radio.fifo=13;f.radio.fifo_rc=-706;
  assert(f.pause()==R::BUSY);pin_high=false;const auto& d=f._lab_handoff.first_failure;
  assert(d.software&&d.dio&&d.fifo==13&&d.fifo_rc==-706);}
 {Fake f;f._lab_handoff.begin("old",123);f.labCaptureHandoffFailure(2,0,true);
  assert(f._lab_handoff.first_failure.reason==2&&f.radio.raw_reads==1&&f.radio.fifo_reads==1);}
 {Fake f;f._lab_handoff.begin("old",123);f.busy=true;f.labCaptureHandoffFailure(2,0,true);
  assert(!f._lab_handoff.first_failure.raw&&!f.radio.raw_reads&&!f.radio.fifo_reads);}
 for(int stage:{3,4,6,7,8,9,10}){
  Fake f;f._lab_scan_active=false;f._lab_handoff.begin("old",123);f.radio.fail_stage=stage;
  assert(!f.labScannerStart(true)&&f._lab_uncertain);
  const auto& d=f._lab_handoff.first_failure;
  assert(d.reason==2&&d.restart_stage==stage&&d.restart_rc==-700-stage);
  assert(f.radio.raw_reads==1&&f.radio.fifo_reads==1);
  assert(f.radio.dccm_reads==(stage==6?3u:0u));
 }
 {Fake f;f._lab_scan_active=false;f._lab_handoff.begin("old",123);f.radio.correct_ident=false;
  assert(!f.labScannerStart(true));const auto& d=f._lab_handoff.first_failure;
  assert(d.restart_stage==6&&d.restart_rc==-1&&d.major==1&&d.minor==0x18&&!memcmp(d.ident,"BAD!",4));
  assert(f.radio.dccm_reads==3&&d.dccm[0]==0x8007F0&&d.dccm[4]==0x806034);
  char out[160];f.labScannerHandoffDiag(out,sizeof(out));assert(strstr(out,"m=008007F0/")&&strstr(out,"mr=0/0/0")&&strstr(out,"bad=1"));
  f.labCaptureHandoffFailure(2,0,false);assert(f._lab_handoff.failures==1);}
 for(bool command_error:{false,true}){
  Fake f;f._lab_scan_active=false;f._lab_handoff.begin("old",123);f.start_command_rc=command_error?-711:0;f.start_hooked=false;
  assert(!f.labScannerStart(true));const auto& d=f._lab_handoff.first_failure;
  assert(d.reason==2&&d.restart_stage==11&&d.restart_rc==(command_error?-711:-1)&&!d.restart_hook);
  assert(!memcmp(d.ident,"SCN3",4)&&!f.radio.raw_reads&&!f.radio.fifo_reads);
  char out[160];f.labScannerHandoffDiag(out,sizeof(out));assert(strstr(out,"step=11")&&strstr(out,"id=53434E33"));
 }
#endif
 // Exercise the real shared restart method, not a mirrored legacy fixture.
 // All callers (old/new maintenance, pending TX lease, generic recovery) use it.
 for(bool pending_lease:{false,true})for(bool pulse_owner:{false,true}){
  Fake f;f._lab_scan_active=false;f._lab_resume_pending=pending_lease;f._agc_pram_restore_owned=pulse_owner;
  assert(f.labScannerStart(true)&&f._lab_scan_active&&f._lab_pram_loaded&&f.radio.registered);
  assert(f.radio.activations==0&&f.radio.image_writes==0);
  const auto& e=f.radio.events;
  auto pos=[&](const char* s){for(size_t i=0;i<e.size();++i)if(e[i]==s)return i;return e.size();};
  assert(pos("standby")<pos("hello")&&pos("hello")<pos("hook"));
 }
 {Fake f;f._lab_scan_active=false;f._lab_pram_loaded=false;f.radio.registered=false;
  assert(!f.labScannerStart(true)&&!f.radio.activations&&!f.radio.image_writes);}
 {Fake f;f._lab_scan_active=false;f._lab_pram_loaded=false;f.radio.registered=false;f.radio.fail_stage=5;
  assert(!f.labScannerStart(false)&&f._lab_uncertain&&!f._lab_scan_active&&!f._lab_pram_loaded);
  assert(f.radio.activations==1&&f.radio.image_writes==1);
  for(const auto& event:f.radio.events)assert(event!="hello"&&event!="hook");}
 {Fake f;f._lab_scan_active=false;f._lab_pram_loaded=false;f.radio.registered=false;
  assert(f.labScannerStart(false)&&f._lab_pram_loaded&&f.radio.activations==1&&f.radio.image_writes==1);}
#if MESH_WARM_RX_REFRESH
 const auto owned=[](Fake& f){
  f._lab_scan_active=false;f._agc_pram_paused=true;f._agc_pram_restore_owned=true;
  f._agc_pram_cold_restore_owned=true;f._agc_pram_arm_issued=true;f._lab_resume_pending=true;f._lab_session_ldo=true;
  f._rxps_pulse.pending=true;f._rxps_pulse.had_cycle=true;f._rxps_pulse.phase=RxpsPulse::Phase::Rearm;
  f.radio.raw_irq=0;f.correction=0.125f;
 };
 for(bool error_flags:{false,true}){
  Fake f;owned(f);if(error_flags)f.radio.raw_irq=0x30000;
  assert(f.labScannerStart(false)&&f._agc_pram_cold_tried&&f._lab_scan_active);
  assert(f.radio.hard_resets==1&&f.radio.calibrations==1&&f.radio.activations==1&&f.radio.image_writes==1);
  assert(f.radio.calibrated.size()==2);
  for(size_t i=0;i<2;++i)assert(f.radio.calibrated[i]==f._lab_snap[i].freq_hz/1000000.0f);
  const auto& e=f.radio.events;
  auto pos=[&](const char* s){for(size_t i=0;i<e.size();++i)if(e[i]==s)return i;return e.size();};
  assert(pos("standby")<pos("reset")&&pos("reset")<pos("calibrate")&&pos("calibrate")<pos("image"));
  assert(pos("image")<pos("activate")&&pos("activate")<pos("hello")&&pos("hello")<pos("hook"));
 }
 for(unsigned blocker=0;blocker<15;++blocker){
  Fake f;owned(f);
  if(blocker==0)f.busy=true;
  if(blocker==1)f.pending={true};
  if(blocker==2)f.radio.raw_irq=1u<<18;
  if(blocker==3)f.radio.raw_irq=1u<<5;
  if(blocker==4)f.radio.raw_irq=1u<<20; // CAD_DONE is not silently cleared
  if(blocker==5)f.radio.fifo=23;
  if(blocker==6)f.radio.fifo_rc=-705;
  if(blocker==7)f.radio.status_rc=-706;
  if(blocker==8)f.radio.inject_standby_irq=true;
  if(blocker==9)f._agc_pram_generations[1]++;
  if(blocker==10)f._rxps_pulse.phase=RxpsPulse::Phase::Packet;
  if(blocker==11)f._agc_pram_arm_issued=false;
  if(blocker==12)f._lab_uncertain=true;
  if(blocker==13)f.exclusive=true;
  if(blocker==14)f._agc_pram_cold_tried=true;
  assert(!f.labScannerStart(false)&&f.radio.hard_resets==0&&f.radio.calibrations==0&&f.radio.image_writes==0);
  if(blocker!=14)assert(!f._agc_pram_cold_tried);
  if(blocker==5)assert(f.radio.fifo==23);
  if(blocker==8)assert(f.radio.raw_irq&(1u<<18));
 }
 {Fake f;owned(f);f.busy=true;assert(!f.labScannerStart(false)&&!f._agc_pram_cold_tried);
  f.busy=false;assert(f.labScannerStart(false)&&f.radio.hard_resets==1);}
 for(unsigned failure=0;failure<3;++failure){
  Fake f;owned(f);if(failure==0)f.reinit_ok=false;if(failure==1)f.radio.calibration_rc=-712;if(failure==2)f.radio.fail_stage=5;
  assert(!f.labScannerStart(false)&&f._agc_pram_cold_tried&&f.radio.hard_resets==1);
  const auto writes=f.radio.image_writes;
  if(failure<2)assert(writes==0);
  assert(!f.labScannerStart(false)&&f.radio.hard_resets==1&&f.radio.image_writes==writes);
 }
#endif
}
'''
        code = code.replace('@METHODS@', methods)
        code = code.replace('@PENDING@', body(shared, 'bool RadioLibWrapper::isPacketPendingOrReceiving()').replace('RadioLibWrapper::', ''))
        with tempfile.TemporaryDirectory(prefix='lr2021-handoff-') as temp:
            path = Path(temp)
            for enabled, warm, mutation in ((0, 0, None), (0, 1, None), (1, 0, None), (1, 1, None),
                                           (1, 1, 'missing'), (1, 1, 'ignored'), (1, 1, 'cold_calibration_missing'),
                                           (1, 1, 'cold_fifo_missing'), (1, 1, 'cold_reset_missing')):
                current = code
                if mutation:
                    if mutation=='cold_calibration_missing':
                        current=code.replace('r->resetReceiverCalibration(frequencies,2,false)', '(static_cast<void>(frequencies),RADIOLIB_ERR_NONE)')
                    elif mutation=='cold_fifo_missing':
                        current=code.replace(' || fifo) return false;', ') return false;')
                    elif mutation=='cold_reset_missing':
                        current=code.replace('if (!labReinitialize(false)) return false;', '')
                    else:
                        current = code.replace('rc=r->activatePram();',
                            'rc=RADIOLIB_ERR_NONE;' if mutation == 'missing' else 'rc=(r->activatePram(),RADIOLIB_ERR_NONE);')
                    self.assertNotEqual(current, code)
                (path / 'main.cpp').write_text(current)
                built = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie',
                    '-DMESH_AGC_TIMING_DIAGNOSTICS=' + str(enabled), '-DMESH_WARM_RX_REFRESH=' + str(warm), '-I' + str(ROOT / 'src'),
                    str(path / 'main.cpp'), '-o', str(path / 'test')], capture_output=True, text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                result = subprocess.run([str(path / 'test')], capture_output=True, text=True, timeout=20)
                if mutation:
                    self.assertNotEqual(result.returncode, 0, 'Incorrect cold/warm activation must fail the real restart harness')
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
