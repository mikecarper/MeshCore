#include <helpers/radiolib/SX126xRxpsPulse.h>
#include <helpers/radiolib/RxpsPulse.h>
#include <helpers/radiolib/RXPowerSaving.h>
#include <cassert>
#include <cmath>
#include <cstring>
#include <iostream>
#include <limits>
#include <map>
#include <string>
#include <vector>

#define MESH_WARM_RX_REFRESH 1
#define MESH_ENABLE_MORSE_RX 1
#define MESH_ENABLE_TX_BEACONS 1
#define MESH_ENABLE_RADIO_DIRECTION 1
static uint32_t now_us=1000;
uint32_t micros() { return now_us; }
uint32_t millis() { return now_us/1000; }
namespace mesh {
enum class AGCMaintenanceResult { Applied, Deferred, Failed };
namespace radioDirection {
bool allowed=true;
bool radioReceiveAllowed() { return allowed; }
}
}

struct Call {
  bool read;
  std::vector<uint8_t> command,data;
  size_t length;
  bool wait;
  uint8_t status_width;
};
class Module {
public:
  enum BitWidth_t:uint8_t { BITS_0=0,BITS_8=8,BITS_16=16 };
  enum { MODE_IDLE=0,MODE_RX=1 };
  struct Config { BitWidth_t widths[3]={BITS_16,BITS_8,BITS_8}; } spiConfig;
  std::vector<Call> calls;
  std::vector<uint8_t> switches;
  std::map<uint16_t,uint8_t> registers={{0x029F,1},{0x02A0,8},{0x02A1,0xAC},
      {0x08AC,0x96},{0x08B5,0xA5},{0x0736,0xA0},{0x0902,0x7F},{0x0944,0xA5}};
  std::vector<uint8_t> fifo={0xDE,0xAD,0xBE,0xEF};
  bool busy=false,host_irq=false;
  uint16_t irq=0;
  uint8_t packet_type=1,status=0x54;
  size_t fail_at=SIZE_MAX,fail_from=SIZE_MAX;
  int16_t failure=-705;
  unsigned standbys=0,inject_standby=0;
  uint16_t inject_irq=0;
  bool inject_host=false;
  void setRfSwitchState(uint8_t mode) { assert(!busy);switches.push_back(mode); }
  int16_t SPIreadStream(uint16_t command,uint8_t* data,size_t n,bool wait,bool verify) {
    const uint8_t cmd[]={uint8_t(command)};
    return SPIreadStream(cmd,1,data,n,wait,verify);
  }
  int16_t SPIwriteStream(uint16_t command,const uint8_t* data,size_t n,bool wait,bool verify) {
    const uint8_t cmd[]={uint8_t(command)};
    return SPIwriteStream(cmd,1,data,n,wait,verify);
  }
  int16_t SPIreadStream(const uint8_t* cmd,uint8_t len,uint8_t* data,size_t n,bool wait,bool verify) {
    assert(!verify);
    assert(!busy || (!wait && len==1 && cmd[0]==0xC0));
    const size_t index=calls.size();
    calls.push_back(Call{true,{cmd,cmd+len},{},n,wait,uint8_t(spiConfig.widths[2])});
    if(index==fail_at || index>=fail_from) { std::memset(data,0,n);return failure; }
    if(len==3 && cmd[0]==0x1D) { assert(n==1);data[0]=registers.at((uint16_t(cmd[1])<<8)|cmd[2]); }
    else if(cmd[0]==0x12) { assert(n==2);data[0]=uint8_t(irq>>8);data[1]=uint8_t(irq); }
    else if(cmd[0]==0x11) { assert(n==1);data[0]=packet_type; }
    else {
      assert(cmd[0]==0xC0 && n==1 && spiConfig.widths[2]==BITS_0);
      data[0]=status;
      if(status==0 || status==0xFF) return RADIOLIB_ERR_CHIP_NOT_FOUND;
      if((status&14)==6) return -705;
      if((status&14)==8) return -706;
      if((status&14)==10) return -707;
    }
    return 0;
  }
  int16_t SPIwriteStream(const uint8_t* cmd,uint8_t len,const uint8_t* data,size_t n,bool wait,bool verify) {
    assert(wait && !verify && !busy);
    const size_t index=calls.size();
    calls.push_back(Call{false,{cmd,cmd+len},{data,data+n},n,wait,uint8_t(spiConfig.widths[2])});
    if(index==fail_at || index>=fail_from) return failure;
    if(len==3 && cmd[0]==0x0D) { assert(n==1);registers[(uint16_t(cmd[1])<<8)|cmd[2]]=data[0]; }
    if(cmd[0]==0x02) irq=0;
    if(cmd[0]==0x80) {
      assert(n==1 && data[0]==0);++standbys;
      if(standbys==inject_standby) { irq=inject_irq;host_irq=inject_host; }
    }
    return 0;
  }
};

class CustomSX1262 {
public:
  struct Cache { bool valid=true;void invalidate() { valid=false; } } _profileSwitch;
  bool _coldStandby=false,_tcxoWakePending=true,_headerSeen=true,fast_fs=TEST_FS;
  uint32_t _activityAt=123,_preambleMillis=100,_maxPayloadMillis=1000,rxTimeout=0;
  uint16_t preambleLengthLoRa=32;
  uint8_t headerType=0,implicitLen=255,crcTypeLoRa=1,invertIQEnabled=0;
  Module* mod;
  explicit CustomSX1262(Module* value):mod(value) {}
  Module* getMod() { return mod; }
  uint32_t getTcxoDelay() const { return 1600; }
  bool isChipBusy() const { return mod->busy; }
  uint16_t getIrqMapped(uint32_t flags) const { return uint16_t(flags); }
  @DRIVER@
};

class Wrapper {
public:
  static constexpr uint8_t STATE_RX=1;
  static constexpr uint32_t NoiseFloorSettleMillis=20;
  Module module;
  CustomSX1262 driver;
  CustomSX1262* _radio;
  RxpsPulse _rxps_pulse;
  uint8_t _rxps_pulse_retries=0,state=STATE_RX,_cur_sf=7,_active_profile=0,_startrx_fails=0;
  float _cur_bw=62.5f;
  uint32_t _profile_generation=0,_profile_retry_at=0,_rx_retry_delay_ms=0,_nf_sample_from=0;
  bool _profile_repair_required=false,_profile_refresh_required=false,_profile_rxps_suspended=false;
  bool _rx_retry_pending=false,_params_valid=true,_rx_ps_enabled=false,_rx_ps_armed=false;
  bool _rx_hold_continuous=false,_nf_calib_active=false,_nf_window_armed=false;
  bool _cw_active=false,_morse_owned=false,_morse_rx_owned=false,_beacon_owned=false,supported=true;
  unsigned legacy=0;
  struct Profiles { bool active=false;uint32_t generation[2]={0,0};bool enabled() const { return active; } } _profiles;
  Wrapper():driver(&module),_radio(&driver) { _rxps_pulse.cadence(60000); }
  bool hasPendingRadioInterrupt() const { return module.host_irq; }
  bool isChipBusy() const { return module.busy; }
  uint32_t currentPreambleLength() const { return driver.preambleLengthLoRa; }
  bool supportsWarmRxRefresh() const { return supported; }
  bool isCalibratingNoiseFloor() const { return _nf_calib_active; }
  bool tryResetAGC() { ++legacy;return true; }
  bool resetAGCIfIdle() { ++legacy;return true; }
  int startReceivePowerSaving() { assert(false);return -1; }
  @WRAPPER@
};

static void begin(Wrapper& w,uint32_t sleep=4000) {
  now_us=1000;mesh::radioDirection::allowed=true;
  assert(w._rxps_pulse.configure(sleep,2600));
  assert(w.periodicAGCMaintenance(false)==mesh::AGCMaintenanceResult::Deferred);
}
static void naturalWake(Wrapper& w,uint32_t span=1000) {
  const uint32_t armed=w._rxps_pulse.armed_at;
  now_us=armed+1;w.serviceAGCPulse();
  now_us=armed+w._rxps_pulse.rx_us;w.module.busy=true;w.serviceAGCPulse();
  now_us+=span;w.module.busy=false;w.serviceAGCPulse();
}
static std::vector<uint8_t> opcodes(const Module& m) {
  std::vector<uint8_t> out;for(const auto& call:m.calls) out.push_back(call.command[0]);return out;
}
static void unchanged(const Module& m,const std::map<uint16_t,uint8_t>& before) {
  for(uint16_t addr:{0x029F,0x02A0,0x02A1,0x08AC,0x08B5}) assert(m.registers.at(addr)==before.at(addr));
  assert((m.fifo==std::vector<uint8_t>{0xDE,0xAD,0xBE,0xEF}));
}

static void normal() {
  for(uint32_t sleep:{2616U,3000U,4000U,6554U}) {
    Wrapper w;const auto before=w.module.registers;begin(w,sleep);
    assert(w._rxps_pulse.pending && w._rxps_pulse.ok==0 && w.module.calls.size()==11);
    assert(!w.driver._profileSwitch.valid && w.driver._coldStandby && !w.driver._tcxoWakePending);
    naturalWake(w);
    assert(!w._rxps_pulse.pending && w._rxps_pulse.ok==1 && w._rxps_pulse.fail==0);
    assert(w._rxps_pulse.rtc==1 && w._rxps_pulse.verified==1 && w._rxps_pulse.cycles==1);
    const std::vector<uint8_t> expected={0x12,0x80,0x12,0x08,0x8F,0x02,0x11,0x1D,0x0D,0x8C,0x94,
      0x80,0x12,0x0D,0x1D,0x0D,0x08,0x8F,0x02,0x11,0x1D,0x0D,0x8C,0x82,0xC0};
    assert(opcodes(w.module)==expected);
    assert((w.module.calls[3].data==std::vector<uint8_t>{2,0x76,2,0x62,0,0,0,0}));
    assert((w.module.calls[23].data==std::vector<uint8_t>{0xFF,0xFF,0xFF}));
    assert(w.module.calls[24].status_width==Module::BITS_0 && !w.module.calls[24].wait);
    assert(w.module.spiConfig.widths[2]==Module::BITS_8);
    assert((w.module.switches==std::vector<uint8_t>{0,1,0,1}));
    assert(w.module.registers.at(0x0902)==0 && w.module.registers.at(0x0944)==0xA7);
    unchanged(w.module,before);
    const size_t count=w.module.calls.size();
    assert(w.periodicAGCMaintenance(false)==mesh::AGCMaintenanceResult::Applied);
    assert(w.module.calls.size()==count);
  }
}

static void faults() {
  using S=RxpsPulse::Step;
  const S steps[]={S::Irq,S::Standby,S::Irq,S::Stage,S::Stage,S::Stage,S::Stage,S::Stage,S::Stage,S::Stage,S::Arm,
    S::Standby,S::Irq,S::RtcStop,S::RtcRead,S::RtcEvent,S::SetRx,S::SetRx,S::SetRx,S::SetRx,S::SetRx,S::SetRx,S::SetRx,S::SetRx,S::Mode};
  for(size_t index=0;index<25;++index) for(int16_t failure:{-705,-706,-707}) {
    Wrapper w;w.module.fail_at=index;w.module.failure=failure;begin(w);
    if(index>=11) naturalWake(w);
    assert(w.module.calls.size()==index+1);
    assert(w._rxps_pulse.failed && w._rxps_pulse.error==failure && w._rxps_pulse.failed_step==steps[index]);
    assert(w._rxps_pulse.pending && w._rxps_pulse.ok==0);
    w.module.fail_at=SIZE_MAX;now_us+=250001;w.serviceAGCPulse();
    assert(!w._rxps_pulse.pending && w._rxps_pulse.fail==1 && w._rxps_pulse.ok==0);
    assert(w._rxps_pulse.error==failure && w._rxps_pulse.failed_step==steps[index]);
    assert(w.periodicAGCMaintenance(false)==mesh::AGCMaintenanceResult::Failed);
    assert(w._rxps_pulse.takeCompletion()==RxpsPulse::Result::None);
  }
}

static void packet() {
  for(bool host:{false,true}) {
    Wrapper w;w.module.irq=host?0:2;w.module.host_irq=host;const auto before=w.module.registers;begin(w);
    assert(!w._rxps_pulse.pending && w._rxps_pulse.defer==1 && w.module.calls.size()==1);
    unchanged(w.module,before);
  }
  for(bool host:{false,true}) {
    Wrapper w;w.module.inject_standby=1;w.module.inject_irq=host?0:2;w.module.inject_host=host;
    const auto before=w.module.registers;begin(w);
    assert(w._rxps_pulse.pending && w.module.calls.size()==3);
    w.serviceAGCPulse();
    assert(w._rxps_pulse.phase==RxpsPulse::Phase::Packet && !w._rxps_pulse.owns());
    for(const auto& call:w.module.calls) assert(call.command[0]!=0x02 && call.command[0]!=0x94 && call.command[0]!=0x82);
    unchanged(w.module,before);
    const size_t count=w.module.calls.size();w.serviceAGCPulse();assert(w.module.calls.size()==count);
    // Only the ordinary packet owner may consume its IRQ/FIFO; simulate that
    // ownership handoff before calling the actual packet reentry selector.
    w.module.irq=0;w.module.host_irq=false;
    const int rc=w.startReceiveMode();assert(rc==0);
    w._rxps_pulse.receiveEntry(rc,now_us);
    assert(w._rxps_pulse.defer==1 && w._rxps_pulse.ok==0 && !w._rxps_pulse.pending);
  }
  Wrapper partial;partial.module.inject_standby=1;partial.module.inject_irq=4;begin(partial);
  assert(partial._rxps_pulse.cancel && partial._rxps_pulse.aborts==1);
  partial.serviceAGCPulse();assert(partial._rxps_pulse.ok==0 && partial._rxps_pulse.defer==1);
}

static void sleep() {
  Wrapper w;begin(w);const size_t calls=w.module.calls.size(),switches=w.module.switches.size();
  now_us=w._rxps_pulse.armed_at+1;w.serviceAGCPulse();
  now_us=w._rxps_pulse.armed_at+w._rxps_pulse.rx_us;w.module.busy=true;w.serviceAGCPulse();
  now_us+=500;w.serviceAGCPulse();
  assert(w.module.calls.size()==calls && w.module.switches.size()==switches);
  assert(w._rxps_pulse.pending && !w._rxps_pulse.had_cycle);
  now_us+=500;w.module.busy=false;w.serviceAGCPulse();assert(w._rxps_pulse.ok==1);
}

static void bounded() {
  Wrapper broken;broken.module.fail_from=1;begin(broken);
  for(unsigned i=0;i<8 && broken._rxps_pulse.pending;++i) { now_us+=250001;broken.serviceAGCPulse(); }
  assert(!broken._rxps_pulse.pending && broken._rxps_pulse.fail==1 && broken._profile_repair_required);
  assert(broken.module.calls.size()<=5 && broken._rxps_pulse.ok==0);
  Wrapper busy;begin(busy);const size_t calls=busy.module.calls.size();
  now_us=busy._rxps_pulse.armed_at+busy._rxps_pulse.deadline_us;busy.module.busy=true;busy.serviceAGCPulse();
  assert(busy._rxps_pulse.failed && busy.module.calls.size()==calls);
  now_us=busy._rxps_pulse.armed_at+10000000;busy.serviceAGCPulse();
  assert(busy._rxps_pulse.forced_wake==1 && busy.module.calls.size()==calls+1);
  assert(busy.module.calls.back().command[0]==0xC0 && !busy.module.calls.back().wait);
  now_us+=1999999;busy.serviceAGCPulse();assert(busy._rxps_pulse.pending && busy.module.calls.size()==calls+1);
  ++now_us;busy.serviceAGCPulse();assert(!busy._rxps_pulse.pending && busy._rxps_pulse.fail==1);
  assert(busy._rxps_pulse.ok==0 && busy._rxps_pulse.cycles==0);
}

static void geometry() {
  for(uint8_t sf:{7,12}) for(float bw:{500.0f,62.5f}) for(uint32_t sleep:{2616U,4000U,6554U}) {
    Wrapper w;w._cur_sf=sf;w._cur_bw=bw;begin(w,sleep);
    assert(!w._rxps_pulse.failed && w._rxps_pulse.waiting());
    const double symbol=double(uint32_t(1)<<sf)*1000.0/bw;
    const double required=(w.currentPreambleLength()+4.25+8+1)*symbol;
    const double available=(2.0*w._rxps_pulse.rx_ticks+w._rxps_pulse.sleep_ticks)*15.625;
    assert(available>=required && w._rxps_pulse.rx_us<=3000000);
    assert(w._rxps_pulse.sleep_ticks==(sleep==2616?1U:sleep==4000?89U:253U));
  }
}

static void invalidGeometry() {
  for(float bw:{0.0f,-1.0f,std::numeric_limits<float>::infinity(),
      std::numeric_limits<float>::quiet_NaN(),1e-30f,1e-5f}) {
    Wrapper w;w._cur_bw=bw;begin(w);
    assert(w._rxps_pulse.failed && w._rxps_pulse.failed_step==RxpsPulse::Step::Stage);
    assert(w.module.calls.size()==1 && w.module.calls[0].command[0]==0x12);
  }
  for(uint8_t sf:{4,13}) { Wrapper w;w._cur_sf=sf;begin(w);assert(w._rxps_pulse.failed && w.module.calls.size()==1); }
  Wrapper preamble;preamble.driver.preambleLengthLoRa=7;begin(preamble);assert(preamble._rxps_pulse.failed && preamble.module.calls.size()==1);
  Wrapper huge;huge.driver.preambleLengthLoRa=65535;huge._cur_sf=12;begin(huge);assert(huge._rxps_pulse.failed && huge.module.calls.size()==1);
  Wrapper overflow;overflow._rxps_pulse.sleep_us=UINT32_MAX;
  assert(overflow.periodicAGCMaintenance(false)==mesh::AGCMaintenanceResult::Deferred);
  assert(overflow._rxps_pulse.failed && overflow.module.calls.size()==1);
}

static void mode() {
  for(uint8_t status:{0x24,0x34,0x44,0x64,0x00,0xFF,0x56,0x58,0x5A}) {
    Wrapper w;w.module.status=status;const auto before=w.module.spiConfig.widths[2];
    assert(w.driver.verifyPulseReceive()!=0 && w.module.spiConfig.widths[2]==before);
  }
  Wrapper busy;busy.module.busy=true;assert(busy.driver.verifyPulseReceive()==-705 && busy.module.calls.empty());
  Wrapper error;error.module.fail_at=0;error.module.failure=-999;
  assert(error.driver.verifyPulseReceive()==-999 && error.module.spiConfig.widths[2]==Module::BITS_8);
}

static void iq() {
  for(uint8_t inverted:{0,1}) {
    Wrapper w;w.driver.invertIQEnabled=inverted;w.module.registers[0x0736]=0xA5;
    const auto before=w.module.registers;assert(w.driver.stagePulseReceive()==0);
    assert(w.module.registers[0x0736]==(inverted?0xA1:0xA5));unchanged(w.module,before);
    assert((w.module.calls.back().data==std::vector<uint8_t>{0,32,0,255,1,inverted}));
  }
  Wrapper wrong;wrong.module.packet_type=0;assert(wrong.driver.stagePulseReceive()==-20);
  assert(wrong.module.calls.size()==4);
}

static void budget() {
  Wrapper w;w.driver._preambleMillis=1000;w.driver._maxPayloadMillis=20000;w._cur_sf=12;begin(w);
  assert(w._rxps_pulse.deadline_us==2*w._rxps_pulse.rx_us+w._rxps_pulse.sleep_us+23000000UL);
  Wrapper max;max.driver._preambleMillis=max.driver._maxPayloadMillis=UINT32_MAX;
  assert(max.driver.pulsePacketBudgetUs()==uint64_t(UINT32_MAX)*2000ULL);begin(max);
  assert(max._rxps_pulse.deadline_us==60000000UL);
}

static void admission() {
  for(unsigned index=0;index<16;++index) {
    Wrapper w;
    bool* guards[]={&w._params_valid,&w._profile_refresh_required,&w._profile_repair_required,
      &w._profile_rxps_suspended,&w._rx_ps_enabled,&w._rx_ps_armed,&w._rx_hold_continuous,
      &w._rx_retry_pending,&w._nf_calib_active,&w._nf_window_armed,&w._cw_active,
      &w._morse_owned,&w._morse_rx_owned,&w._beacon_owned,&w._profiles.active,&w.module.busy};
    *guards[index]=index!=0;begin(w);
    assert(!w._rxps_pulse.pending && w._rxps_pulse.defer==1 && w.module.calls.empty());
  }
  Wrapper pending;begin(pending);const size_t count=pending.module.calls.size();
  assert(pending.periodicAGCMaintenance(false)==mesh::AGCMaintenanceResult::Deferred);
  assert(pending.module.calls.size()==count && pending._rxps_pulse.req==1);
  pending._rxps_pulse.cadence(0);naturalWake(pending);
  assert(pending._rxps_pulse.takeCompletion()==RxpsPulse::Result::None && pending._rxps_pulse.ok==0);
  Wrapper failed;failed.module.fail_at=1;begin(failed);failed.module.fail_at=SIZE_MAX;
  now_us+=250001;failed.serviceAGCPulse();assert(failed.periodicAGCMaintenance(false)==mesh::AGCMaintenanceResult::Failed);
  assert(failed._rxps_pulse.takeCompletion()==RxpsPulse::Result::None);
  Wrapper unsupported;unsupported.supported=false;
  assert(unsupported.periodicAGCMaintenance(false)==mesh::AGCMaintenanceResult::Applied && unsupported.legacy==1 && unsupported.module.calls.empty());
}

static void tuples() {
  for(uint8_t sf:{7,12}) for(float bw:{500.0f,62.5f}) for(uint32_t requested:{6554U,4000U,3000U,2616U}) {
    Wrapper w;w._cur_sf=sf;w._cur_bw=bw;w.driver.preambleLengthLoRa=16;begin(w,requested);
    assert(!w._rxps_pulse.failed && w._rxps_pulse.waiting());
    std::cout<<"{\"sf\":"<<unsigned(sf)<<",\"bw_khz\":"<<bw
      <<",\"preamble\":16,\"requested_sleep_us\":"<<requested
      <<",\"rx_us\":"<<w._rxps_pulse.rx_us<<",\"rx_ticks\":"<<w._rxps_pulse.rx_ticks
      <<",\"sleep_ticks\":"<<w._rxps_pulse.sleep_ticks<<"}\n";
  }
}

int main(int argc,char** argv) {
  assert(argc==2);const std::string name=argv[1];
  if(name=="tuples") { tuples();return 0; }
  if(name=="normal") normal();else if(name=="faults") faults();else if(name=="packet") packet();
  else if(name=="sleep") sleep();else if(name=="bounded") bounded();else if(name=="geometry") geometry();
  else if(name=="invalid_geometry") invalidGeometry();else if(name=="mode") mode();else if(name=="iq") iq();
  else if(name=="budget") budget();else if(name=="admission") admission();else assert(false);
  std::cout<<"PASS "<<name<<'\n';
}
