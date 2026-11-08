#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <initializer_list>
#include <helpers/radiolib/LR2021ApplyStatus.h>
#include <helpers/radiolib/AGCTimingDiagnostics.h>
#include <helpers/OldAGCPolicy.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -1
static uint32_t now=1234;
static uint32_t millis(){return now;}
static uint32_t micros(){return now*1000;}
namespace mesh {
enum class AGCMaintenanceResult {Applied,Deferred,Failed};
enum class RadioParamApplyResult {APPLIED,BUSY,FAILED};
namespace radioDirection {static bool allowed=true;static bool radioReceiveAllowed(){return allowed;}}
}
struct Base {
 virtual ~Base()=default;
 virtual bool supportsOldAGC()const{return false;}
 bool nf_active=false,nf_requested=false;
 virtual bool isCalibratingNoiseFloor()const{return nf_active||nf_requested;}
 virtual void doResetAGC(){}
 virtual mesh::AGCMaintenanceResult periodicOldAGCMaintenance(){return mesh::AGCMaintenanceResult::Deferred;}
};
struct CustomLR2021 {
 std::vector<std::string> calls;
 std::vector<float> frontend_frequencies;
 bool busy=false,irq=false,receiving=false,inject_probe_irq=false,inject_standby_irq=false;
 int16_t standby_rc=0,calibration_rc=0,activation_rc=0;
 unsigned calibrations=0,activations=0;
 bool pram_registered=true;
 int16_t standby(){calls.push_back("standby");if(inject_standby_irq)irq=true;return standby_rc;}
 int16_t resetReceiverCalibration(const float* frequencies=nullptr,size_t count=0){
   calls.push_back("calibrate");++calibrations;assert((frequencies&&count>=1&&count<=2)||(!frequencies&&!count));
   if(count)frontend_frequencies.assign(frequencies,frequencies+count);
   if(calibration_rc==RADIOLIB_ERR_NONE)pram_registered=false; // code retained, ROM dispatch lost on sleep
   return calibration_rc;
 }
 int16_t activatePram(){calls.push_back("activate");++activations;if(activation_rc==RADIOLIB_ERR_NONE)pram_registered=true;return activation_rc;}
};
struct Wrapper:Base {
 using PulseBase=Base;
 static constexpr uint8_t STATE_IDLE=0,STATE_RX=1,STATE_TX_WAIT=3;
 CustomLR2021 radio;CustomLR2021* _radio=&radio;
 bool _params_valid=true,_profile_refresh_required=false,_profile_repair_required=false;
 uint8_t state=STATE_RX,_active_profile=0;
 struct Profiles {
  struct Params{float freq;};Params configured[2]={{910.525f},{911.15f}};
  uint32_t generation[2]={11,22};bool active=false;
  bool enabled()const{return active;}const Params& params(uint8_t p)const{assert(p<2);return configured[p];}
 } _profiles;
 std::vector<float> correction_inputs;
 float hardwareFrequencyMHz(float nominal){correction_inputs.push_back(nominal);return nominal+0.125f;}
 uint32_t _profile_generation=11,_profile_retry_at=0;
 bool _cw_active=false,_beacon_owned=false,_beacon_restoring=false,_morse_owned=false,_morse_rx_owned=false;
 bool _primary_receive_preempt=false,_rx_ps_armed=false,_rx_hold_continuous=false;
 struct Pulse {bool pending=false;} _rxps_pulse;
 struct Warm {bool attempting=false;void transition(uint32_t,bool){assert(false);}} _warm_refresh;
 bool _lab_uncertain=false,_lab_sleeping=false,_lab_scan_active=false,_lab_resume_pending=false;
 bool _agc_pram_paused=false;
 bool auto_enabled=true,_lab_pram_loaded=true;
 uint32_t _lab_resume_at=0;
 struct Status{bool hooked=false;} _lab_status;
 mesh::RadioParamApplyResult pause_result=mesh::RadioParamApplyResult::APPLIED;
 bool restart_result=true,inject_restart_irq=false,mutate_generation=false;
 float _cur_freq=910.525f,_cur_bw=62.5f;uint8_t _cur_sf=7,_cur_cr=5;
 int _physical_protocol=2,_cur_dbm=9;
 bool _dbm_valid=true,_dbm_applied=true,_rx_boosted_gain_valid=true,_rx_boosted_gain_applied=true,_cur_rx_boosted_gain=true;
 bool _noise_floor_valid=true,_noise_floor_secondary_valid=true;
 bool params_ok=true,protocol_ok=true,gain_ok=true,receive_ok=true;
 int16_t power_rc=0;
 unsigned noise_requests=0,claims=0,params_calls=0,protocol_calls=0,gain_calls=0,power_calls=0,receive_calls=0;
 bool scan_tuple=true,multi_sf=false,rxps_requested=false;
 unsigned side_detector_restores=0;bool restored_multi_sf=false;
 mesh::lr2021::ApplyStatus _apply_status;
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
 AGCTimingDiagnostics _agc_timing;
#endif
 void invalidateScanTuple(){scan_tuple=false;}
 int16_t recordApply(mesh::lr2021::ApplyStatus::Stage stage,int16_t code){return _apply_status.record(stage,code);}
 bool hasPendingRadioInterrupt(){return radio.irq;}
 bool isChipBusy(){return radio.busy;}
 bool isReceivingPacket(){if(radio.inject_probe_irq)radio.irq=true;return radio.receiving;}
 bool warmRxRefreshUsesFs(){return false;}
 void claimRadioInterrupt(){++claims;radio.irq=false;}
 void noteReconfigureAdmission(const char*,int16_t){}
 void labCaptureHandoffFailure(uint8_t,uint8_t,bool){}
 mesh::RadioParamApplyResult labPauseScanner(bool=false,uint8_t target=0,const char* =nullptr){
   if(radio.irq||radio.busy)return mesh::RadioParamApplyResult::BUSY;
   radio.calls.push_back("pause");assert(target==0);
   if(pause_result==mesh::RadioParamApplyResult::APPLIED){_lab_scan_active=false;_lab_status.hooked=false;_active_profile=0;_profile_generation=_profiles.generation[0];_profile_refresh_required=false;}
   return pause_result;
 }
 bool labReconfigureBlocked(){return _lab_uncertain||_lab_scan_active;}
 bool labScannerStart(bool warm){
   radio.calls.push_back("scanner");assert(warm);
   if(inject_restart_irq)radio.irq=true;
   if(!restart_result||radio.irq||radio.busy||!_lab_pram_loaded)return false;
   if(radio.activatePram()!=RADIOLIB_ERR_NONE){_lab_uncertain=true;_lab_resume_pending=false;_profile_repair_required=true;return false;}
   if(!radio.pram_registered)return false;
   radio.calls.push_back("hook");
   _lab_scan_active=true;_lab_status.hooked=true;_lab_resume_pending=false;return true;
 }
 bool applyParams(float f,float bw,uint8_t sf,uint8_t cr){
   radio.calls.push_back("params");++params_calls;
   assert(f==_cur_freq&&bw==_cur_bw&&sf==_cur_sf&&cr==_cur_cr);
   if(mutate_generation)++_profiles.generation[_active_profile];
   return params_ok;
 }
 bool applyProtocol(int value){radio.calls.push_back("protocol");++protocol_calls;assert(value==_physical_protocol);return protocol_ok;}
 int16_t applyCachedTxPower(int value){radio.calls.push_back("power");++power_calls;assert(value==_cur_dbm);return power_rc;}
 bool applyRxBoostedGainMode(bool value){radio.calls.push_back("gain");++gain_calls;assert(value==_cur_rx_boosted_gain);return gain_ok;}
 void startRecv(){
   radio.calls.push_back("receive");++receive_calls;
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
   _agc_timing.receiveEntry(receive_ok?0:RADIOLIB_ERR_UNKNOWN,micros());
#endif
   if(!receive_ok){state=STATE_IDLE;return;}
   ++side_detector_restores;restored_multi_sf=multi_sf;
   _rx_ps_armed=rxps_requested&&!multi_sf&&!_profiles.enabled();state=STATE_RX;
 }
 bool isInRecvMode()const{return state==STATE_RX;}
 void recalibrateNoiseFloor(){++noise_requests;nf_requested=true;_noise_floor_valid=false;_noise_floor_secondary_valid=false;}
 @BEGIN_RECONFIGURE@
 @END_RECONFIGURE@
 #include <helpers/radiolib/LR2021LegacyMaintenance.inc>
};
using Result=mesh::AGCMaintenanceResult;
static void check_saved(const Wrapper& w){
 if(w._active_profile==1)assert(w._cur_freq==911.15f&&w._cur_bw==250.0f&&w._cur_sf==9&&w._cur_cr==6);
 else assert(w._cur_freq==910.525f&&w._cur_bw==62.5f&&w._cur_sf==7&&w._cur_cr==5);
 assert(w._physical_protocol==2&&w._cur_dbm==9&&w._cur_rx_boosted_gain);
 assert(w._profiles.generation[0]==11&&w._profiles.generation[1]==22);
}
static size_t position(const Wrapper&w,const char*value){for(size_t i=0;i<w.radio.calls.size();++i)if(w.radio.calls[i]==value)return i;return w.radio.calls.size();}
static void frontendCoverage(){
 for(bool secondary:{false,true})for(bool explicit_reset:{false,true}){
  Wrapper w;w._profiles.active=secondary;
  // An independent current tuple must not replace the enabled profile list.
  w._cur_freq=915.25f;
  if(explicit_reset)w.doResetAGC();
  else assert(w.periodicOldAGCMaintenance()==Result::Applied);
  const size_t count=secondary?2:1;
  assert(w.radio.calibrations==1&&w.radio.frontend_frequencies.size()==count&&w.correction_inputs.size()==count);
  for(size_t i=0;i<count;++i){
   assert(w.correction_inputs[i]==w._profiles.params(i).freq);
   assert(w.radio.frontend_frequencies[i]==w._profiles.params(i).freq+0.125f);
  }
 }
 for(bool secondary:{false,true}){
  Wrapper w;w._params_valid=false;w._profiles.active=secondary;
  w._profiles.configured[0].freq=0;w._profiles.configured[1].freq=0;
  w.doResetAGC();
  assert(w.radio.calibrations==1&&w.radio.frontend_frequencies.empty()&&w.correction_inputs.empty());
 }
}
static void successful(){
 for(unsigned profile:{0U,1U})for(bool multi:{false,true})for(bool rxps:{false,true}){
  Wrapper w;w._active_profile=profile;w._profile_generation=w._profiles.generation[profile];w._profiles.active=profile==1;w.multi_sf=multi;w.rxps_requested=rxps;
  if(profile==1){w._cur_freq=911.15f;w._cur_bw=250.0f;w._cur_sf=9;w._cur_cr=6;}
  assert(w.periodicOldAGCMaintenance()==Result::Applied);
  assert(w.radio.calibrations==1&&w.params_calls==1&&w.gain_calls==1&&w.power_calls==1&&w.receive_calls==1);
  assert(w.radio.activations==0); // ordinary host RX never activates a scanner
  assert(w.noise_requests==1&&w._noise_floor_valid&&w._noise_floor_secondary_valid);
  assert(!w.scan_tuple&&w._apply_status.stage==mesh::lr2021::ApplyStatus::Stage::Applied);
  assert(w.side_detector_restores==1&&w.restored_multi_sf==multi);
  assert(w._rx_ps_armed==(rxps&&!multi&&!w._profiles.active));
  assert(position(w,"standby")<position(w,"calibrate")&&position(w,"calibrate")<position(w,"params")&&position(w,"gain")<position(w,"receive"));
#if MESH_ENABLE_HOST_KISS
  assert(w.protocol_calls==1&&position(w,"params")<position(w,"protocol"));
#else
  assert(w.protocol_calls==0);
#endif
  check_saved(w);
 }
}
static void guards(){
 for(unsigned guard=0;guard<15;++guard){
  Wrapper w;
#if MESH_W12_PRAM_EXPERIMENT
  w._profiles.active=true;w._lab_scan_active=true;w._lab_status.hooked=true;
#endif
  if(guard==0)w.state=Wrapper::STATE_IDLE;
  if(guard==1)w._params_valid=false;
  if(guard==2)w._active_profile=2;
  if(guard==3){w._profile_refresh_required=true;w._lab_scan_active=false;}
  if(guard==4)w._profile_repair_required=true;
  if(guard==5)w._profile_retry_at=123;
  if(guard==6){w.nf_active=true;w._lab_scan_active=false;}
  if(guard==7)++w._profile_generation;
  if(guard==8)w._cw_active=true;
  if(guard==9)w._beacon_owned=true;
  if(guard==10)w._morse_owned=true;
  if(guard==11)w._morse_rx_owned=true;
  if(guard==12)mesh::radioDirection::allowed=false;
  if(guard==13){
#if MESH_WARM_RX_REFRESH
    w._rxps_pulse.pending=true;
#else
    continue;
#endif
  }
  if(guard==14)w.state=Wrapper::STATE_TX_WAIT;
  assert(w.periodicOldAGCMaintenance()==Result::Deferred);
  assert(w.radio.calls.empty()&&w.radio.calibrations==0&&w.noise_requests==0);
  mesh::radioDirection::allowed=true;
 }
}
static void ownership(){
 for(unsigned guard=0;guard<5;++guard){
  Wrapper w;
  if(guard==0)w.radio.busy=true;
  if(guard==1)w.radio.irq=true;
  if(guard==2)w.radio.receiving=true;
  if(guard==3)w.radio.inject_probe_irq=true;
  if(guard==4)w.radio.inject_standby_irq=true;
  assert(w.periodicOldAGCMaintenance()==Result::Deferred);
  assert(!w.radio.calibrations&&!w.claims&&!w.noise_requests&&!w.receive_calls);
  if(guard==4){assert(w.radio.irq&&w.radio.calls.size()==1&&w.radio.calls[0]=="standby");}
  else assert(w.radio.calls.empty());
 }
}
static void failures(){
 for(unsigned failure=0;failure<8;++failure){
  Wrapper w;
  if(failure==0)w.radio.standby_rc=-701;
  if(failure==1)w.radio.calibration_rc=-702;
  if(failure==2)w.params_ok=false;
  if(failure==3){
#if MESH_ENABLE_HOST_KISS
   w.protocol_ok=false;
#else
   continue;
#endif
  }
  if(failure==4)w.power_rc=-703;
  if(failure==5)w.gain_ok=false;
  if(failure==6)w.receive_ok=false;
  if(failure==7)w.mutate_generation=true;
  assert(w.periodicOldAGCMaintenance()==Result::Failed);
  assert(w._profile_refresh_required&&w._profile_repair_required&&w._profile_retry_at==now+250&&w.state==Wrapper::STATE_IDLE);
  assert(!w._dbm_applied&&!w._rx_boosted_gain_applied);
  assert(w._apply_status.failed&&w.noise_requests==0);
  if(failure==1)assert(w._apply_status.code==-702&&w.params_calls==0);
  if(failure==4)assert(w._apply_status.code==-703&&w.gain_calls==0);
  if(failure<6)assert(w.receive_calls==0);
 }
}
static void scanner(){
#if MESH_W12_PRAM_EXPERIMENT
 // Cold scanner initialization intentionally leaves the host tuple dirty.
 // Only its successful checked pause may clear the host refresh requirement.
 for(const auto pause:{mesh::RadioParamApplyResult::APPLIED,mesh::RadioParamApplyResult::BUSY,mesh::RadioParamApplyResult::FAILED}){
  Wrapper w;w._profiles.active=true;w._lab_scan_active=true;w._lab_status.hooked=true;w._profile_refresh_required=true;w.pause_result=pause;
  const auto result=w.periodicOldAGCMaintenance();
  assert(result==(pause==mesh::RadioParamApplyResult::APPLIED?Result::Applied:pause==mesh::RadioParamApplyResult::BUSY?Result::Deferred:Result::Failed));
  assert(w.radio.calibrations==(pause==mesh::RadioParamApplyResult::APPLIED?1u:0u));
  if(pause==mesh::RadioParamApplyResult::APPLIED)assert(!w._profile_refresh_required&&w._lab_scan_active&&w._lab_status.hooked);
  else assert(w._profile_refresh_required&&w._lab_scan_active);
 }
 for(bool automatic:{false,true})for(bool outer:{false,true})for(uint8_t last_profile:{0,1}){
  Wrapper w;w.auto_enabled=automatic;w._profiles.active=true;w._lab_scan_active=true;w._lab_status.hooked=true;w._agc_pram_paused=outer;
  w._active_profile=last_profile;w._profile_generation=w._profiles.generation[last_profile];
  assert(w.periodicOldAGCMaintenance()==Result::Applied);
  assert(w._lab_scan_active&&w._lab_status.hooked&&w._lab_pram_loaded&&!w._lab_resume_pending);
  assert(w.auto_enabled==automatic&&w._agc_pram_paused==outer&&w.radio.calibrations==1);
  assert(w._active_profile==0&&w._profile_generation==w._profiles.generation[0]);
  assert(position(w,"pause")<position(w,"calibrate")&&position(w,"receive")<position(w,"scanner"));check_saved(w);
 }
 for(unsigned failure=0;failure<7;++failure){
  Wrapper w;w.auto_enabled=false;w._profiles.active=true;w._lab_scan_active=true;w._lab_status.hooked=true;
  if(failure==0)w._lab_uncertain=true;
  if(failure==1)w._lab_sleeping=true;
  if(failure==2)w.pause_result=mesh::RadioParamApplyResult::BUSY;
  if(failure==3)w.pause_result=mesh::RadioParamApplyResult::FAILED;
  if(failure==4)w.radio.inject_standby_irq=true;
  if(failure==5)w.radio.calibration_rc=-706;
  if(failure==6)w.inject_restart_irq=true;
  const auto result=w.periodicOldAGCMaintenance();
  assert(result==(failure==3||failure>=5?Result::Failed:Result::Deferred));
  if(failure<4)assert(!w._lab_resume_pending&&w.radio.calibrations==0);
  else assert(w._lab_resume_pending&&!w._lab_scan_active&&w._lab_pram_loaded&&!w.auto_enabled);
  if(failure==4)assert(w.radio.irq&&!w.claims&&w.radio.calibrations==0);
  if(failure==5)assert(w._profile_repair_required&&!w._dbm_applied&&!w._rx_boosted_gain_applied);
  if(failure==6)assert(w.radio.irq&&!w.claims&&w.radio.calibrations==1&&w._lab_resume_at==now+250);
 }
#endif
}
static void activation(){
#if MESH_W12_PRAM_EXPERIMENT
 for(bool automatic:{false,true}){
  Wrapper w;w.auto_enabled=automatic;w._profiles.active=true;w._lab_scan_active=true;w._lab_status.hooked=true;
  assert(w.periodicOldAGCMaintenance()==Result::Applied);
  assert(w.radio.calibrations==1&&w.radio.activations==1&&w.radio.pram_registered);
  assert(w._lab_scan_active&&w._lab_status.hooked&&!w._lab_uncertain&&w._lab_pram_loaded);
  assert(position(w,"calibrate")<position(w,"params")&&position(w,"params")<position(w,"receive"));
  assert(position(w,"receive")<position(w,"activate")&&position(w,"activate")<position(w,"hook"));
 }
 for(unsigned failure=0;failure<3;++failure){
  Wrapper w;w._profiles.active=true;w._lab_scan_active=true;w._lab_status.hooked=true;
  if(failure==0)w.radio.activation_rc=-709;
  if(failure==1)w._lab_pram_loaded=false;
  if(failure==2)w.radio.calibration_rc=-710;
  assert(w.periodicOldAGCMaintenance()==Result::Failed);
  assert(w.radio.calibrations==1&&w.radio.activations==(failure==0?1u:0u));
  assert(!w.noise_requests&&!w.claims&&!w._lab_scan_active&&w._apply_status.failed);
  if(failure<2){assert(w.params_calls==1&&w.receive_calls==1);assert(w._lab_resume_pending==(failure==1));}
  if(failure==0)assert(w._lab_uncertain&&w._profile_repair_required);
  if(failure==2){assert(w._apply_status.code==-710&&!w.params_calls&&!w.receive_calls&&w._lab_resume_pending);
    assert(w._profile_repair_required&&w._profile_refresh_required&&w.state==Wrapper::STATE_IDLE&&!w._dbm_applied&&!w._rx_boosted_gain_applied);}
 }
#endif
}
static void noiseOwnership(){
 Wrapper host;host.nf_requested=true;
 assert(host.isCalibratingNoiseFloor());
 assert(host.periodicOldAGCMaintenance()==Result::Deferred&&host.radio.calibrations==0);
 host.nf_requested=false;host.nf_active=true;
 assert(host.isCalibratingNoiseFloor());
 assert(host.periodicOldAGCMaintenance()==Result::Deferred&&host.radio.calibrations==0);
#if MESH_W12_PRAM_EXPERIMENT
 Wrapper w;w._profiles.active=true;w._lab_scan_active=true;w._lab_status.hooked=true;w.nf_requested=true;
 assert(!w.isCalibratingNoiseFloor()&&w.nf_requested&&!w.nf_active);
 assert(w.periodicOldAGCMaintenance()==Result::Applied);
 assert(w._lab_scan_active&&w.nf_requested&&!w.nf_active&&w.noise_requests==1);
 assert(!w.isCalibratingNoiseFloor());
 assert(w.periodicOldAGCMaintenance()==Result::Applied);
 assert(w.radio.calibrations==2&&w.noise_requests==2&&w.nf_requested&&!w.nf_active);
 // Once PRAM relinquishes RF, the retained host request owns the next sample
 // window again. Neither the override nor maintenance discarded that request.
 w._lab_scan_active=false;w._lab_status.hooked=false;
 assert(w.isCalibratingNoiseFloor());
 assert(w.periodicOldAGCMaintenance()==Result::Deferred&&w.radio.calibrations==2);
 w.nf_requested=false;assert(!w.isCalibratingNoiseFloor());
#endif
}
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
static void diagnostics(){
 {Wrapper w;w.radio.busy=true;assert(w.periodicOldAGCMaintenance()==Result::Deferred);
  const auto& d=w._agc_timing;assert(d.periodic_requests==1&&d.periodic_admitted==0&&d.periodic_skipped==1);
  assert(d.helper_us.count==0&&d.rx_gap_us.count==0&&d.command_failures==0);}
 {Wrapper w;assert(w.periodicOldAGCMaintenance()==Result::Applied);const auto& d=w._agc_timing;
  assert(d.periodic_requests==1&&d.periodic_admitted==1&&d.periodic_skipped==0);
  assert(d.helper_us.count==1&&d.rx_gap_us.count==1&&!d.pending);
  assert(d.command_failures==0&&d.failed_sequences==0&&d.shared_requests==0&&d.shared_admitted==0);}
 {Wrapper w;w.radio.calibration_rc=-706;assert(w.periodicOldAGCMaintenance()==Result::Failed);const auto& d=w._agc_timing;
  assert(d.periodic_requests==1&&d.periodic_admitted==1&&d.helper_us.count==1&&d.rx_gap_us.count==0&&d.pending);
  assert(d.command_failures==1&&d.failed_sequences==1&&d.last_error==-706&&d.last_error_step==AGCTimingDiagnostics::Step::Calibrate);
  assert(d.rearm_failures==0);w.startRecv();assert(d.rx_gap_us.count==1&&!d.pending&&d.command_failures==1);}
 {Wrapper w;w.params_ok=false;assert(w.periodicOldAGCMaintenance()==Result::Failed);const auto& d=w._agc_timing;
  assert(d.periodic_admitted==1&&d.helper_us.count==1&&d.rx_gap_us.count==0&&d.pending);
  assert(d.command_failures==1&&d.failed_sequences==0&&d.last_error_step==AGCTimingDiagnostics::Step::None);}
 {Wrapper w;w.receive_ok=false;assert(w.periodicOldAGCMaintenance()==Result::Failed);const auto& d=w._agc_timing;
  assert(d.helper_us.count==1&&d.rx_gap_us.count==0&&d.rearm_failures==1&&d.pending);
  assert(d.command_failures==1&&d.failed_sequences==0);}
 {Wrapper w;w._profiles.active=true;w._lab_scan_active=true;w._lab_status.hooked=true;w.restart_result=false;
  assert(w.periodicOldAGCMaintenance()==Result::Failed);const auto& d=w._agc_timing;
  // Continuous RX genuinely restarted; this is not evidence that PRAM rehooked.
  assert(d.helper_us.count==1&&d.rx_gap_us.count==1&&!d.pending&&d.command_failures==1);
  assert(w._lab_resume_pending&&!w._lab_scan_active);}
 {Wrapper w;w._profiles.active=true;w._lab_scan_active=true;w._lab_status.hooked=true;
  assert(w.periodicOldAGCMaintenance()==Result::Applied);now+=4000;
  assert(w.periodicOldAGCMaintenance()==Result::Applied);const auto& d=w._agc_timing;
  assert(d.periodic_admitted==2&&d.helper_us.count==2&&d.rx_gap_us.count==2&&w.radio.calibrations==2);
  assert(!d.pending&&d.command_failures==0&&d.failed_sequences==0&&d.superseded_pending==0);}
}
#endif
int main(int argc,char**argv){
 assert(argc==2);assert(Wrapper().supportsOldAGC()==bool(MESH_ENABLE_OLD_AGC));
#if MESH_ENABLE_OLD_AGC
 const std::string name=argv[1];
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
 if(name=="diagnostics"){diagnostics();return 0;}
#endif
 if(name=="success")successful();else if(name=="frontend")frontendCoverage();else if(name=="guards")guards();else if(name=="ownership")ownership();else if(name=="failures")failures();else if(name=="scanner")scanner();else if(name=="activation")activation();else if(name=="noise")noiseOwnership();else assert(false);
#else
 (void)argv;Wrapper w;assert(w.periodicOldAGCMaintenance()==Result::Deferred&&w.radio.calls.empty());
#endif
}
