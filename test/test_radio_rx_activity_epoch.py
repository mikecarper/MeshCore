"""Execute actual RX entry, activity detection and due-scanner admission."""
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
import test_radio_interrupt_recovery as irq
compile_run=irq.compile_run
from test_radio_receive_contract import method

FAMILIES=('CustomSX1262','CustomSX1268','CustomLLCC68','CustomSTM32WLx','CustomLR1110','CustomLR1121')


def build_source(family='CustomSX1262',remove_reset=False,include_duty=False):
 source=irq.RadioInterruptRecoveryTests().source(sx126x=family)
 # The shared IRQ fixture includes an LR2021-only measurement Module. These
 # activity tests install the selected chip's real RF/IRQ Module below.
 measurement_binding=' MeasurementModule* mod=&measurement_mod;\n'
 assert source.count(measurement_binding)==1
 source=source.replace(measurement_binding,'')
 source=source.replace('struct RadioLibHal {', 'uint32_t millis();\nstruct RadioLibHal { uint32_t millis(){return ::millis();}')
 src=(ROOT/'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
 chip=(ROOT/f'src/helpers/radiolib/{family}.h').read_text()
 entry=method(chip,'int16_t startReceive()')
 if remove_reset:
  assert '_activityAt = 0;'in entry and '_headerSeen = false;'in entry
  entry=entry.replace('_activityAt = 0;','').replace('_headerSeen = false;','')
 if include_duty:
  duty=method(chip,'int16_t startReceiveDutyCycle(')
  if family=='CustomLR1110':
   duty+='\n'+method(chip,'int16_t launchRxDutyCycle(')
  if remove_reset:duty=duty.replace('_activityAt = 0;','').replace('_headerSeen = false;','')
  entry+='\n'+duty
 source=source.replace('#include <cassert>', '''#include <cassert>
#include <cstdio>
#include <RadioProfiles.h>
#include <helpers/RadioSwitchTuning.h>
#include <helpers/radiolib/NoiseFloorEstimator.h>
#include <helpers/radiolib/SX1262ProfileSwitchState.h>
#include <helpers/radiolib/LR1110ProfileSwitchState.h>
#define MC_SX1262_FAST_PACKET_CACHE 0
#define MC_SX1262_FAST_IQ_CACHE 0
#define RADIOLIB_SX126X_RX_TIMEOUT_INF 0xffffff
#define RADIOLIB_IRQ_RX_DEFAULT_FLAGS 1
#define RADIOLIB_IRQ_RX_DEFAULT_MASK 2
#define RADIOLIB_SX126X_PACKET_TYPE_LORA 1
#define RADIOLIB_RADIO_MODE_NONE 0
#define RADIOLIB_LR11X0_RX_TIMEOUT_INF 0xffffff
#define RADIOLIB_LR11X0_IRQ_ALL 0xffffffff
#define RADIOLIB_LR11X0_MAX_PACKET_LENGTH 255
#define RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED 16
#define RADIOLIB_LR11X0_IRQ_SYNC_WORD_HEADER_VALID 32
#define RADIOLIB_LR11X0_IRQ_HEADER_ERR 64
#define RADIOLIB_IRQ_HEADER_ERR 6
#define RADIOLIB_ERR_INVALID_SLEEP_PERIOD -98
#define RADIOLIB_ERR_INVALID_RX_PERIOD -97
#define RADIOLIB_LR11X0_STANDBY_RC 0
#define RADIOLIB_LR11X0_LF_CLK_RC 1
#define RADIOLIB_LR11X0_LF_BUSY_RELEASE_ENABLED 2
#define RADIOLIB_LR11X0_LF_BUSY_RELEASE_DISABLED 0
#define RADIOLIB_RADIO_MODE_RX 1
#define RADIOLIB_LR11X0_RX_DUTY_CYCLE_MODE_RX 0
#define RADIOLIB_ASSERT(state) do { if((state)!=0)return(state); } while(0)
using RadioLibIrqFlags_t=uint32_t;
struct RadioModeConfig_t {struct {uint32_t timeout,irqFlags,irqMask;size_t len;}receive;};
#define MAX_TRANS_UNIT 255
#define NF_CONTINUOUS_TIMEOUT_MS 4000
namespace mesh { enum class RadioParamApplyResult { APPLIED, BUSY, FAILED }; }
''')
 source=source.replace('uint32_t millis(){return now_ms;}', '''uint32_t millis(){return now_ms;}
int64_t esp_timer_get_time(){return int64_t(now_ms)*1000;}
uint32_t profileTimestamp(bool){return now_ms*1000;}
uint32_t profileElapsedUs(uint32_t a,uint32_t b,bool){return b-a;}''')
 source=source.replace('struct Board {', 'struct Board { bool prepareRadioProfileCpuClock(bool, bool& changed) {changed=false;return true;}')
 source=source.replace('struct Module {', '''struct Module {
 enum {MODE_RX=1};void setRfSwitchState(int){}''')
 source=source.replace('struct Chip : LR2021 {', '''using SX1262=LR2021;using SX1268=LR2021;using LLCC68=LR2021;using STM32WLx=LR2021;using LR1110=LR2021;using LR1121=LR2021;
struct Chip : LR2021 {
 SX1262ProfileSwitchState _profileSwitch{true};bool _coldStandby=true;
 uint8_t _profileSwitchFeatures=63;
 uint8_t getProfileSwitchFeatures() const { return _profileSwitchFeatures; }
 uint32_t _profileFastResumes=0;
 bool _profilePacketDirty=false;Module* mod=&module;
 int16_t snapshotIrqAfterRxExit(uint32_t* pending){*pending=irq;return snapshot_failure;}
 int16_t clearIrqState(uint32_t flags){irq &= ~flags;rx_status=0;gpio_levels[module.irq]=false;return clear_status;}
 int16_t setPacketParamsLoRa(size_t,uint8_t,uint8_t,uint8_t,uint8_t){return packet_failure;}
 int snapshot_failure=0,packet_failure=0;
 bool _rx_ps_rf_rx_disabled=false,_lfClockRestoreNeeded=false;
 uint8_t spreadingFactor=7;float bandwidthKhz=62.5;uint32_t tcxoDelay=0;
 int lf_failure=0,stage_failure=0;unsigned lf_calls=0;
 int16_t configLfClock(int){++lf_calls;return lf_failure;}
 int16_t stageMode(int,RadioModeConfig_t*){return stage_failure;}
 int16_t setRxDutyCycle(uint32_t,uint32_t,int){return startDuty();}
 size_t preambleLengthLoRa=32;uint8_t crcTypeLoRa=1,implicitLen=255,headerType=0,invertIQEnabled=0;
 uint32_t rxTimeout=0;int stagedMode=0;
 int16_t clearIrqStatus(){irq=0;rx_status=0;gpio_levels[module.irq]=false;return clear_status;}
 int16_t setPacketParams(size_t,int,int,int,int){return packet_failure;}
 int16_t setRx(uint32_t){if(start_failure)return start_failure;++arms;stopped=false;return 0;}
 uint8_t getPacketType(){return RADIOLIB_SX126X_PACKET_TYPE_LORA;}
 '''+entry)
 source=source.replace('bool stopped=false, inject_read_irq=false;', 'bool stopped=false, inject_read_irq=false;int start_failure=0,duty_failure=0;')
 source=source.replace('int startTransmit(uint8_t*,int)', '''int16_t standby(uint8_t){return standby();}
 int startDuty(){if(duty_failure)return duty_failure;irq=0;rx_status=0;++arms;return 0;}
 int16_t startReceiveDutyCycle(uint32_t,uint32_t,uint32_t,uint32_t){return startDuty();}
 int startTransmit(uint8_t*,int)''')
 old='int16_t startReceive(uint32_t,uint32_t,uint32_t,int){assert(stopped);++arms;stopped=false;return 0;}'
 new='''int16_t startReceive(uint32_t,uint32_t,uint32_t,int){
 ++standbys;stopped=true;irq=0;rx_status=0;gpio_levels[module.irq]=false;
 if(start_failure)return start_failure;++arms;stopped=false;return 0;
 }'''
 assert old in source;source=source.replace(old,new)
 fields=r'''
 bool _params_valid=true,_profile_rxps_suspended=true,_profile_saved_rxps=false;
 bool _noise_floor_valid=true,_noise_floor_secondary_valid=true,_nf_refresh_requested=false;
 bool _nf_window_armed=false,_nf_lazy_refresh=false,_nf_refresh_attempted=false;
 uint8_t _nf_published_valid=0;
 uint32_t _profile_retry_at=0,_profile_generation=1,_profile_visit_stamp=0,_profile_scan_generation[2]={};
 uint8_t _active_profile=0,_cur_sf=7,_cur_cr=5;
 uint16_t _physical_preamble=32;
 float _cur_freq=910.525,_cur_bw=62.5;
 uint64_t _esp32_profile_visit_started_us=0,_esp32_profile_idle_stamp_us=0;
 uint32_t _esp32_profile_idle_visit_us=0,_esp32_profile_idle_switch_us=0;
 uint16_t _esp32_profile_idle_preamble=0;
 uint8_t _esp32_profile_idle_profile=0;
 uint32_t _esp32_profile_idle_generation[2]={};
 uint32_t _nf_last_calib=0,_nf_calib_deadline=0;
 int16_t _noise_floor=0;int32_t _noise_floor_centi_dbm=0,_noise_floor_secondary_centi_dbm=0;
 NoiseFloorEstimator _floor_estimator,_secondary_floor_estimator;
 uint8_t _startrx_fails=0;bool _wd_last_busy=false;
 uint32_t _wd_last_transition=0,_wd_stuck_thresh=0,_wd_observe_ms=0;
 uint32_t _rx_ps_rx_us=10000,_rx_ps_sleep_us=10000;
 unsigned applies=0;
 NoiseFloorEstimator& profileFloorEstimator(uint8_t p){return p?_secondary_floor_estimator:_floor_estimator;}
 bool isInRecvMode()const{return state==STATE_RX;}
 void beginProfileRetune(bool){}void endProfileRetune(bool){}
 bool validateProfile(const mesh::RadioProfileParams&p)const{return mesh::RadioProfiles::valid(p);}
 uint16_t profilePreamble(uint8_t p)const{return _profiles.preamble(p,32);}
 bool applyParams(float,float,uint8_t,uint8_t){++applies;return true;}
 void cacheParams(float f,float b,uint8_t s,uint8_t c){_cur_freq=f;_cur_bw=b;_cur_sf=s;_cur_cr=c;}
 void setProfileStandbyWarm(bool){}
 void recalibrateNoiseFloor(){}
 bool restoreAfterDeepInit(){return false;}
 uint32_t getEstAirtimeFor(int){return 1000;}
 int startReceiveMode(){return chip.startReceive();}
 void endReconfigure(bool);
 void serviceProfileScan();
 bool programProfile(const mesh::RadioProfileParams&, uint16_t);
 mesh::RadioParamApplyResult tuneProfile(uint8_t);
 '''
 source=source.replace('struct RadioLibWrapper {','struct RadioLibWrapper {'+fields)
 old='void startRecv(){if(chip.startReceive()==0)state=STATE_RX;}'
 assert old in source;source=source.replace(old,'void startRecv();')
 source+='\n'+'\n'.join(method(src,sig)for sig in (
  'void RadioLibWrapper::startRecv(', 'void RadioLibWrapper::endReconfigure(',
  'bool RadioLibWrapper::programProfile(', 'mesh::RadioParamApplyResult RadioLibWrapper::tuneProfile(',
  'void RadioLibWrapper::serviceProfileScan('))
 if family=='CustomLR1110':
  source=source.replace('SX1262ProfileSwitchState _profileSwitch','LR1110ProfileSwitchState _profileSwitch')
  clock_methods='\n'.join(method(chip,signature)for signature in (
   'bool needsLfClockRestore()', 'int16_t restoreLfClock()'))
  source=source.replace(' bool _rx_ps_rf_rx_disabled=',clock_methods+'\n bool _rx_ps_rf_rx_disabled=')
  wrapper=(ROOT/'src/helpers/radiolib/CustomLR1110Wrapper.h').read_text()
  continuous=method(wrapper,'int16_t startContinuousReceive()').replace(' override','')
  continuous=continuous.replace('static_cast<CustomLR1110*>','static_cast<Chip*>')
  source=source.replace(' int startReceiveMode(){return chip.startReceive();}',continuous+'\n int startReceiveMode(){return chip.startReceive();}')
 if family.startswith('CustomLR'):
  source+="\n#define ACTIVITY_PREAMBLE 16\n#define ACTIVITY_HEADER 32\n#define ACTIVITY_HEADER_ERROR 64\n"
 else:
  source+="\n#define ACTIVITY_PREAMBLE 4\n#define ACTIVITY_HEADER 16\n#define ACTIVITY_HEADER_ERROR 32\n"
 return source

CHECKS=r'''
int main(){
 uint8_t bytes[16]={};now_ms=100;
 auto* w=new CustomSX1262Wrapper;assert(w->registerInterruptAction());
 w->_profiles.primary={910.525,62.5,32,7,5};
 mesh::RadioProfileConfig other;other.params={916,125,32,9,5};other.mode=mesh::RadioProfileMode::RxTx;
 w->_profiles.setSecondary(other,false);
 for(unsigned n=0;n<4;++n){w->_profiles.sampleSwitch(0,1,1000,now_ms);w->_profiles.sampleSwitch(1,0,1000,now_ms);}
 assert(w->_profiles.switchTestReady());
 w->_profile_scan_generation[0]=w->_profiles.generation[0];
 w->_profile_scan_generation[1]=w->_profiles.generation[1];
 w->_profile_generation=w->_profiles.generation[0];
 // A due scan while packet A has a valid header defers and latches _headerSeen.
 w->chip.rx_status=0;w->chip.irq=ACTIVITY_HEADER;
 w->_esp32_profile_visit_started_us=0;
 w->serviceProfileScan();assert(w->chip._headerSeen&&w->_active_profile==0);
 const unsigned start_arms=w->chip.arms;
 // Actual wrapper read/actual startRecv/actual selected chip startReceive.
 w->chip.rx_status=1;w->chip.irq=ACTIVITY_HEADER|1;gpio_levels[3]=true;w->chip.callback();
 assert(w->recvRaw(bytes,sizeof(bytes))==4&&w->n_recv==1&&w->chip.arms==start_arms+1);
 assert(w->chip.irq==0&&!w->hasPendingRadioInterrupt());
 const bool retired=!w->chip._headerSeen&&w->chip._activityAt==0;
 // App/UI work may outlast one visit; then B begins its preamble before the
 // next scanner pass, with no intermediate idle activity probe.
 const uint32_t visit=w->_profiles.listenUs(0,w->profilePreamble(w->_profiles.slowerProfile()));
 now_ms+=(visit+999)/1000+1;w->chip.irq=ACTIVITY_PREAMBLE;
 const unsigned stands=w->chip.standbys;
 w->serviceProfileScan();
 const bool aborted=w->chip.standbys!=stands;
 assert(!aborted&&w->_active_profile==0&&w->chip.irq==ACTIVITY_PREAMBLE
        &&"new preamble must survive the actual due scanner");
 assert(retired&&"RX entry must retire old activity latches");
 delete w;
}
'''


ENTRY_CHECKS=r'''
static void oldActivity(Chip& chip){chip._headerSeen=true;chip._activityAt=37;}
int main(){
 now_ms=100;Chip chip;
 oldActivity(chip);assert(chip.startReceive()==0);
 assert(!chip._headerSeen&&chip._activityAt==0&&"successful RX must reset activity");
 oldActivity(chip);chip.start_failure=-88;
 assert(chip.startReceive()==-88&&chip._headerSeen&&chip._activityAt==37);
 chip.start_failure=0;assert(chip.startReceive()==0);
 // A fresh false preamble retains the normal finite timeout.
 chip.irq=ACTIVITY_PREAMBLE;assert(chip.isReceiving());
 now_ms+=chip._preambleMillis;assert(chip.isReceiving());
 ++now_ms;assert(!chip.isReceiving()&&!(chip.irq&ACTIVITY_PREAMBLE));
 chip.irq=ACTIVITY_HEADER;assert(chip.isReceiving());
 now_ms+=chip._maxPayloadMillis;assert(chip.isReceiving());
 ++now_ms;assert(!chip.isReceiving()&&!chip._headerSeen);
 chip.irq=ACTIVITY_HEADER|ACTIVITY_HEADER_ERROR;
 assert(!chip.isReceiving()&&!chip._headerSeen&&chip._activityAt==0);
#if MESH_RADIO_PROFILE_CHIP == 1110 || MESH_RADIO_PROFILE_CHIP == 1121
 // LR11x0 deliberately leaves its error IRQ for wrapper-owned recovery.
 assert(chip.irq&ACTIVITY_HEADER_ERROR);
#else
 assert(!(chip.irq&ACTIVITY_HEADER_ERROR));
#endif
}
'''

FAST_CHECKS=r'''
static void fastContext(Chip& chip){
 chip._profileSwitch.rxResult(0,true);
 chip._profileSwitch.begin(true,true);chip._profileSwitch.standbyResult(0);
 assert(chip._profileSwitch.canResumeFast());
 chip._headerSeen=true;chip._activityAt=37;chip.irq=0;
}
int main(){
 Chip chip;fastContext(chip);
 assert(chip.startReceive()==0&&!chip._headerSeen&&chip._activityAt==0);
 assert(chip._profileFastResumes==1);
 for(unsigned failure=0;failure<3;++failure){
  fastContext(chip);
  if(failure==0){chip.irq=1;chip.clear_status=-88;}
  if(failure==1){chip.packet_failure=-88;chip._profilePacketDirty=true;}
  if(failure==2)chip.start_failure=-88;
  assert(chip.startReceive()==-88&&chip._headerSeen&&chip._activityAt==37);
  chip.clear_status=chip.packet_failure=chip.start_failure=0;
 }
}
'''

DUTY_CHECKS=r'''
int main(){
 Chip chip;chip._headerSeen=true;chip._activityAt=37;
 assert(chip.startReceiveDutyCycle(100000,100000)==0);
 assert(!chip._headerSeen&&chip._activityAt==0&&"acknowledged duty-cycle RX must reset activity");
 chip._headerSeen=true;chip._activityAt=37;chip.duty_failure=-88;
 assert(chip.startReceiveDutyCycle(100000,100000)==-88);
 assert(chip._headerSeen&&chip._activityAt==37);
 chip.duty_failure=0;
 assert(chip.startReceiveDutyCycle(100000,100000)==0&&!chip._headerSeen&&chip._activityAt==0);
#if MESH_RADIO_PROFILE_CHIP == 1110 || MESH_RADIO_PROFILE_CHIP == 1121
 // Bounds and every preparatory command can reject duty-cycle entry. None
 // acknowledged a new RX epoch, so none may retire the old local activity.
 chip._headerSeen=true;chip._activityAt=37;
 assert(chip.startReceiveDutyCycle(100000,0)==RADIOLIB_ERR_INVALID_SLEEP_PERIOD);
 assert(chip._headerSeen&&chip._activityAt==37);
 for(unsigned failure=0;failure<3;++failure){
  if(failure==0)chip.standby_status=-88;
  if(failure==1)chip.lf_failure=-88;
  if(failure==2)chip.stage_failure=-88;
  assert(chip.startReceiveDutyCycle(100000,100000)==-88);
  assert(chip._headerSeen&&chip._activityAt==37);
  chip.standby_status=chip.lf_failure=chip.stage_failure=0;
 }
#else
 chip._rx_ps_rf_rx_disabled=true;chip._headerSeen=true;chip._activityAt=37;
 assert(chip.startReceiveDutyCycle(100000,100000)==0&&!chip._headerSeen&&chip._activityAt==0);
#endif
}
'''

LR_CLOCK_CHECKS=r'''
static void fastContext(Chip& chip){
 chip._profileSwitch.rxResult(0,true);
 chip._profileSwitch.begin(true,true);chip._profileSwitch.standbyResult(0);
 assert(chip._profileSwitch.canResumeFast());
}
int main(){
 auto* wrapper=new CustomLR1110Wrapper;
 auto& chip=wrapper->chip;
 fastContext(chip);
 const unsigned standbys=chip.standbys,clocks=chip.lf_calls;
 assert(wrapper->startContinuousReceive()==0);
 assert(chip.standbys==standbys&&"ordinary profile RX must not add a standby");
 assert(chip.lf_calls==clocks&&chip._profileFastResumes==1);
 assert(!chip.needsLfClockRestore());

 // Entering duty cycle changes BUSY release policy. Failed restoration must
 // stay pending, and must not claim that continuous RX has restarted.
 assert(chip.startReceiveDutyCycle(100000,100000)==0);
 assert(chip.needsLfClockRestore());
 chip.lf_failure=-88;
 const unsigned arms=chip.arms;
 assert(wrapper->startContinuousReceive()==-88);
 assert(chip.needsLfClockRestore()&&chip.arms==arms);
 chip.lf_failure=0;
 assert(wrapper->startContinuousReceive()==0);
 assert(!chip.needsLfClockRestore());

 fastContext(chip);
 const unsigned next_standbys=chip.standbys,next_clocks=chip.lf_calls;
 assert(wrapper->startContinuousReceive()==0);
 assert(chip.standbys==next_standbys&&chip.lf_calls==next_clocks);
 assert(chip._profileFastResumes==2);
 delete wrapper;
}
'''

class RadioRxActivityEpochTests(unittest.TestCase):
 def test_lr1110_clock_restore_keeps_ordinary_profile_rx_fast(self):
  source=build_source('CustomLR1110',include_duty=True)
  compile_run(source+LR_CLOCK_CHECKS,['-DESP32_PLATFORM=1','-DRADIO_CLASS=CustomLR1110'])

 def test_lr1110_unconditional_clock_restore_breaks_profile_fast_path(self):
  source=build_source('CustomLR1110',include_duty=True)
  bypass='    if (!radio->needsLfClockRestore()) return _radio->startReceive();'
  self.assertIn(bypass,source)
  with self.assertRaises(AssertionError)as failure:
   compile_run(source.replace(bypass,'')+LR_CLOCK_CHECKS,
               ['-DESP32_PLATFORM=1','-DRADIO_CLASS=CustomLR1110'])
  self.assertIn('ordinary profile RX must not add a standby',str(failure.exception))

 def test_actual_receive_rearm_preserves_new_preamble_at_due_scan(self):
  for family in FAMILIES:
   with self.subTest(family=family):
    compile_run(build_source(family)+CHECKS,['-DESP32_PLATFORM=1','-DRADIO_CLASS='+family])

 def test_removed_epoch_reset_aborts_new_packet_in_actual_due_scan(self):
  for family in FAMILIES:
   with self.subTest(family=family):
    with self.assertRaises(AssertionError)as failure:
     compile_run(build_source(family,remove_reset=True)+CHECKS,
                 ['-DESP32_PLATFORM=1','-DRADIO_CLASS='+family])
    self.assertIn('new preamble must survive the actual due scanner',str(failure.exception))

 def test_actual_normal_receive_success_failure_activity_ttl_and_header_error(self):
  for family in FAMILIES:
   with self.subTest(family=family):
    compile_run(build_source(family)+ENTRY_CHECKS,['-DESP32_PLATFORM=1','-DRADIO_CLASS='+family])

 def test_actual_fast_receive_resets_only_after_acknowledged_entry(self):
  for family in ('CustomSX1262','CustomLR1110'):
   with self.subTest(family=family):
    compile_run(build_source(family)+FAST_CHECKS,['-DESP32_PLATFORM=1','-DRADIO_CLASS='+family])

 def test_actual_duty_cycle_entry_resets_only_after_acknowledged_entry(self):
  for family in ('CustomSX1262','CustomLR1110','CustomLR1121'):
   for remove in (False,True):
    with self.subTest(family=family,removed_reset=remove):
     source=build_source(family,remove_reset=remove,include_duty=True)
     if remove:
      with self.assertRaises(AssertionError)as failure:
       compile_run(source+DUTY_CHECKS,['-DESP32_PLATFORM=1','-DRADIO_CLASS='+family])
      self.assertIn('acknowledged duty-cycle RX must reset activity',str(failure.exception))
     else:compile_run(source+DUTY_CHECKS,['-DESP32_PLATFORM=1','-DRADIO_CLASS='+family])


if __name__=='__main__':unittest.main()
