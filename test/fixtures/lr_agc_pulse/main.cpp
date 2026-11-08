#include <cassert>
#include <cstdint>
#include <cmath>
#include <vector>
#include <cstring>
#define MESH_WARM_RX_REFRESH 1
#define MESH_W12_PRAM_EXPERIMENT 1
#define MESH_LR2021_RXPS_AVAILABLE 0
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNSUPPORTED -20
#define RADIOLIB_ERR_UNKNOWN -1
#define RADIOLIB_ERR_INVALID_SLEEP_PERIOD -10
#define RADIOLIB_ERR_INVALID_RX_PERIOD -11
#define RADIOLIB_ERR_SPI_CMD_FAILED -12
#define RADIOLIB_LR11X0_IRQ_RX_DONE 8
#define RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED 16
#define RADIOLIB_LR11X0_IRQ_SYNC_WORD_HEADER_VALID 32
#define RADIOLIB_LR2021_IRQ_RX_DONE (1UL<<18)
#define RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED 32
#define RADIOLIB_LR2021_IRQ_LORA_HEADER_VALID 64
#define RADIOLIB_LR11X0_STANDBY_RC 0
#define RADIOLIB_LR2021_STANDBY_RC 0
#define RADIOLIB_LR11X0_LF_CLK_RC 0
#define RADIOLIB_LR11X0_LF_BUSY_RELEASE_DISABLED 0
#define RADIOLIB_LR2021_LF_CLOCK_INTERNAL_RC 0
#define RADIOLIB_LR11X0_CMD_SET_RX_DUTY_CYCLE 0x214
#define RADIOLIB_LR2021_CMD_SET_RX_DUTY_CYCLE 0x210
#define RADIOLIB_IRQ_RX_DEFAULT_FLAGS 1
#define RADIOLIB_IRQ_RX_DEFAULT_MASK 1
#define RADIOLIB_IRQ_PREAMBLE_DETECTED 4
#define RADIOLIB_RADIO_MODE_RX 1
#define RADIOLIB_MODULE_SPI_WIDTH_STATUS 2
static uint32_t clock_us=100000;
uint32_t micros(){return clock_us;}
uint32_t millis(){return clock_us/1000;}
struct RadioModeConfig_t { struct {uint32_t timeout,irqFlags,irqMask;} receive; };
#include "RxpsPulse.h"
#include "RxpsPulseTiming.h"
namespace mesh {struct MainBoard{};enum class AGCMaintenanceResult{Applied,Deferred,Failed};enum class RadioParamApplyResult{APPLIED,BUSY,FAILED};}
struct Module {enum {MODE_RX};enum BitWidth_t{BITS_0=0,BITS_8=8,BITS_16=16};};
struct Chip;
struct Mod {
 Chip* chip;unsigned writes=0;uint16_t opcode=0;std::vector<uint8_t> bytes;
 struct {Module::BitWidth_t widths[3]={Module::BITS_0,Module::BITS_16,MC_LR_AGC_2021?Module::BITS_16:Module::BITS_8};} spiConfig;
 void setRfSwitchState(int){}
 int16_t SPIwriteStream(uint16_t,const uint8_t*,size_t,bool,bool);
};
struct Chip {
 uint32_t tcxoDelay=1600,_maxPayloadMillis=100,_activityAt=0,irq=0;
 bool _headerSeen=false,_lfClockRestoreNeeded=false,busy=false,race=false;
 int status_error=0,clock_error=0,arm_error=0,rx_error=0,mode=4,reentry_mode=4;
 unsigned spi=0,stages=0,resumes=0,standbys=0,standby_fail_at=0;Mod module{this};Mod* mod=&module;
 int16_t getStatus(uint8_t* a,uint8_t* b,uint32_t* c){assert(!busy);assert(mod->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS]==Module::BITS_0);++spi;*a=4;*b=uint8_t(0x60|(MC_LR_AGC_2021?mode:mode<<1));*c=irq;return status_error;}
 int16_t standby(int){++spi;++standbys;busy=false;mode=1;if(race)irq=MC_LR_AGC_2021?1UL<<18:8;return standbys==standby_fail_at?-92:0;}
 int16_t configLfClock(int value){assert(value==0);++spi;return clock_error;}
 int16_t restoreLfClock(){int rc=configLfClock(0);if(!rc)_lfClockRestoreNeeded=false;return rc;}
 int16_t stageMode(int,RadioModeConfig_t*){assert(!busy);++spi;++stages;irq=0;return 0;}
 int16_t startReceive(){assert(!busy);++spi;++resumes;mode=reentry_mode;irq=0;return rx_error;}
#include "LRxxxxAGCChip.inc"
};
int16_t Mod::SPIwriteStream(uint16_t op,const uint8_t* p,size_t n,bool wait,bool verify){
 assert(!chip->busy);assert(wait&&!verify);++chip->spi;++writes;opcode=op;bytes.assign(p,p+n);chip->mode=4;return chip->arm_error;
}
struct Profiles {uint32_t generation[2]={2,3};bool enabled_=true;bool enabled()const{return enabled_;}};
struct RadioLibWrapper {
 Chip* _radio;RxpsPulse _rxps_pulse;uint8_t _rxps_pulse_retries=0;
 float _cur_bw=62.5f;uint8_t _cur_sf=7,state=1;uint16_t preamble=32;
 uint32_t _startrx_fails=0,_rx_retry_delay_ms=0,_nf_sample_from=0;bool _rx_retry_pending=false;
 bool _profile_refresh_required=false,_profile_repair_required=false;uint32_t _profile_retry_at=0;Profiles _profiles;
 static constexpr uint32_t NoiseFloorSettleMillis=20;
 RadioLibWrapper(Chip& r,mesh::MainBoard&):_radio(&r){_rxps_pulse.cadence(16000);}
 virtual ~RadioLibWrapper()=default;
 virtual uint32_t agcPulseTimerHz()const{return 64000;}
 virtual uint32_t defaultAGCPulseSleepUs()const{return 0;}
 virtual uint32_t rxPowerSavingTransitionUs()const{return 0;}
 virtual bool supportsWarmRxRefresh()const{return false;}
 virtual bool startAGCPulse(){return false;}
 virtual void serviceAGCPulse(){}
 virtual mesh::AGCMaintenanceResult periodicAGCMaintenance(bool){
  auto c=_rxps_pulse.takeCompletion();
  if(c==RxpsPulse::Result::Applied)return mesh::AGCMaintenanceResult::Applied;
  if(c==RxpsPulse::Result::Failed)return mesh::AGCMaintenanceResult::Failed;
  if(!_rxps_pulse.pending){_rxps_pulse.request(micros());startAGCPulse();}
  return mesh::AGCMaintenanceResult::Deferred;
 }
 uint32_t getAGCPulseSleep()const{return defaultAGCPulseSleepUs();}
 uint16_t currentPreambleLength()const{return preamble;}
 bool isChipBusy(){return _radio->busy;}
 bool hasPendingRadioInterrupt(){return _radio->irq&(_radio->agcPulsePacketDone(_radio->irq)?_radio->irq:0);}
 void admitAGCPulse(uint32_t t){_rxps_pulse.admitted(t);}
 void validateAGCScanLease(){}
 void finishAGCScanLease(){}
};
#include "ACTUAL_LR_WRAPPER"
struct Radio:LRxxxxPulseWrapper<Chip> {
 using LRxxxxPulseWrapper<Chip>::LRxxxxPulseWrapper;
 using LRxxxxPulseWrapper<Chip>::startAGCPulse;
 using LRxxxxPulseWrapper<Chip>::serviceAGCPulse;
 using LRxxxxPulseWrapper<Chip>::defaultAGCPulseSleepUs;
};
struct Pram:Radio {
 using Radio::Radio;using PulseBase=Radio;
 bool _agc_pram_paused=false,_agc_pram_restore_owned=false;
 bool _lab_resume_pending=false;uint32_t _lab_resume_at=0;
 uint32_t _agc_pram_generations[2]={0,0};
 bool _lab_scan_active=true,_lab_uncertain=false,_lab_pram_loaded=true,fail_resume=false;
 struct {bool enabled_=true;bool enabled()const{return enabled_;}}_lab_auto;
 struct {bool hooked=true;}_lab_status;
 unsigned pauses=0,starts=0,cold_resets=0;
 mesh::RadioParamApplyResult pause_result=mesh::RadioParamApplyResult::APPLIED;
 mesh::RadioParamApplyResult labPauseScanner(bool,uint8_t){++pauses;if(pause_result==mesh::RadioParamApplyResult::APPLIED)_lab_scan_active=false;return pause_result;}
 bool labScannerStart(bool automatic){assert(automatic);assert(_agc_pram_restore_owned);++starts;if(fail_resume)return false;_lab_scan_active=true;_lab_status.hooked=true;_lab_resume_pending=false;return true;}
 ACTUAL_PRAM_PERIODIC
 ACTUAL_PRAM_FINISH
};
static void cycle(Radio& r,Chip& chip){
 auto& p=r._rxps_pulse;uint32_t arm=p.armed_at;
 clock_us=arm+100;r.serviceAGCPulse();unsigned quiet=chip.spi;
 clock_us=arm+p.rx_us;chip.busy=true;r.serviceAGCPulse();
 clock_us+=300;r.serviceAGCPulse();assert(chip.spi==quiet);
 clock_us+=300;chip.busy=false;r.serviceAGCPulse();
}
int main(){
 mesh::MainBoard board;
 {Chip c;Radio r(c,board);assert(r.defaultAGCPulseSleepUs()==3150);r._rxps_pulse.request(micros());assert(r.startAGCPulse());
  auto& bytes=c.module.bytes;assert(bytes.size()==7);assert(c.module.opcode==(MC_LR_AGC_2021?0x210:0x214));
  uint32_t rx=(bytes[0]<<16)|(bytes[1]<<8)|bytes[2];uint32_t period=(bytes[3]<<16)|(bytes[4]<<8)|bytes[5];
  assert(r._rxps_pulse.sleep_ticks==18);assert(period==(MC_LR_AGC_2021?rx+18:18));cycle(r,c);
  assert(r._rxps_pulse.ok==1&&r._rxps_pulse.cycles==1&&r._rxps_pulse.verified==1&&r._rxps_pulse.rtc==1);}
 for(int mode:{0,1,2,3,5,6,7}){Chip c;c.reentry_mode=mode;Radio r(c,board);
  r._rxps_pulse.request(micros());r.startAGCPulse();cycle(r,c);
  assert(r._rxps_pulse.cycles==1&&!r._rxps_pulse.ok&&!r._rxps_pulse.verified
      &&r._rxps_pulse.error==RxpsPulse::ModeError&&r._rxps_pulse.failed_step==RxpsPulse::Step::SetRx);}
 {Chip c;c.irq=MC_LR_AGC_2021?32:16;Radio r(c,board);r._rxps_pulse.request(micros());assert(!r.startAGCPulse());assert(!c.module.writes&&r._rxps_pulse.defer==1);}
 {Chip c;c.race=true;Radio r(c,board);r._rxps_pulse.request(micros());r.startAGCPulse();assert(r._rxps_pulse.phase==RxpsPulse::Phase::Packet);assert(c.irq&&!c.stages&&!c.module.writes);}
 {Chip c;c.clock_error=-91;Radio r(c,board);r._rxps_pulse.request(micros());r.startAGCPulse();for(int i=0;i<4;++i)r.serviceAGCPulse();assert(r._rxps_pulse.fail==1&&!r._rxps_pulse.ok&&!r._rxps_pulse.pending);}
 {Chip c;c.clock_error=-91;Radio r(c,board);r._rxps_pulse.request(micros());r.startAGCPulse();for(int i=0;i<3;++i)r.serviceAGCPulse();
  c.irq=MC_LR_AGC_2021?1UL<<18:8;unsigned before=c.spi;r.serviceAGCPulse();
  assert(c.spi==before&&c.irq&&r.state==1&&!r._profile_repair_required&&!r._profile_refresh_required&&!r._profile_retry_at);
  assert(r._rxps_pulse.fail==1&&!r._rxps_pulse.ok&&!r._rxps_pulse.pending);}
 {Chip c;Radio r(c,board);r._rxps_pulse.request(micros());r.startAGCPulse();clock_us=r._rxps_pulse.armed_at+r._rxps_pulse.deadline_us;c.busy=true;r.serviceAGCPulse();assert(!r._rxps_pulse.ok);clock_us=r._rxps_pulse.armed_at+10000001;r.serviceAGCPulse();r.serviceAGCPulse();
  assert(r._rxps_pulse.forced_wake==1&&r._rxps_pulse.fail==0&&!r._rxps_pulse.ok&&!r._rxps_pulse.cycles);
  assert(!r._rxps_pulse.pending&&!r._rxps_pulse.error&&r._rxps_pulse.missed==1&&r._rxps_pulse.defer==1);
  assert(r._rxps_pulse.rtc==1&&r._rxps_pulse.verified==1&&r._rxps_pulse.takeCompletion()==RxpsPulse::Result::Deferred);}
 // An unconfirmed timeout is quiet, but a real command failure in the
 // exceptional wake must survive the following successful RX recovery.
 {Chip c;c.standby_fail_at=2;Radio r(c,board);r._rxps_pulse.request(micros());r.startAGCPulse();
  clock_us=r._rxps_pulse.armed_at+r._rxps_pulse.deadline_us;c.busy=true;r.serviceAGCPulse();
  assert(!r._rxps_pulse.failed&&!r._rxps_pulse.error&&r._rxps_pulse.missed==1);
  clock_us=r._rxps_pulse.armed_at+10000001;r.serviceAGCPulse();
  assert(r._rxps_pulse.pending&&r._rxps_pulse.failed&&r._rxps_pulse.error==-92);
  assert(r._rxps_pulse.failed_step==RxpsPulse::Step::Recovery&&r._rxps_pulse.forced_wake==1);
  r.serviceAGCPulse();
  assert(!r._rxps_pulse.pending&&r._rxps_pulse.fail==1&&!r._rxps_pulse.ok&&!r._rxps_pulse.cycles);
  assert(!r._rxps_pulse.defer&&!r._rxps_pulse.rearm_fail&&r._rxps_pulse.error==-92);
  assert(r._rxps_pulse.failed_step==RxpsPulse::Step::Recovery&&r._rxps_pulse.rtc==1&&r._rxps_pulse.verified==1);
  assert(r._rxps_pulse.takeCompletion()==RxpsPulse::Result::Failed);}
 {Chip c;Pram r(c,board);r.periodicAGCMaintenance(false);assert(r.pauses==1&&!r._lab_scan_active&&r._rxps_pulse.pending);cycle(r,c);assert(r.starts==1&&r._lab_scan_active&&r._lab_pram_loaded&&!r.cold_resets&&r._rxps_pulse.ok==1);}
 {Chip c;Pram r(c,board);r._lab_scan_active=false;r.periodicAGCMaintenance(false);cycle(r,c);assert(!r.pauses&&!r.starts&&r._rxps_pulse.ok==1);}
 {Chip c;Pram r(c,board);r._lab_auto.enabled_=false;r.periodicAGCMaintenance(false);assert(r._lab_resume_pending);cycle(r,c);assert(r.starts==1&&r._lab_scan_active&&!r._lab_resume_pending&&!r._lab_auto.enabled_&&r._rxps_pulse.ok==1);}
 {Chip c;Pram r(c,board);r.periodicAGCMaintenance(false);r._lab_resume_pending=false;cycle(r,c);assert(!r.starts&&!r._lab_scan_active&&!r._agc_pram_paused&&!r._rxps_pulse.ok&&r._rxps_pulse.cancel);}
 {Chip c;Pram r(c,board);r.pause_result=mesh::RadioParamApplyResult::FAILED;assert(r.periodicAGCMaintenance(false)==mesh::AGCMaintenanceResult::Failed);assert(!c.module.writes&&!r._rxps_pulse.ok);}
 {Chip c;Pram r(c,board);r.fail_resume=true;r.periodicAGCMaintenance(false);cycle(r,c);assert(!r._rxps_pulse.ok);for(int i=0;i<4;++i)r.serviceAGCPulse();assert(r._rxps_pulse.fail==1&&!r._rxps_pulse.ok&&!r.cold_resets);}
 {Chip c;Pram r(c,board);r.periodicAGCMaintenance(false);++r._profiles.generation[1];cycle(r,c);assert(!r.starts&&!r._rxps_pulse.ok&&r._rxps_pulse.cancel);}
 return 0;
}
