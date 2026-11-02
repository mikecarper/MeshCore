#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/CompanionRadioDiagnostics.h>
#include <helpers/radiolib/RXPowerSaving.h>
namespace mesh { enum class RadioParamApplyResult : uint8_t { APPLIED, BUSY, FAILED }; }

constexpr int RADIOLIB_NC=-1;
constexpr int RADIOLIB_ERR_NONE=0;
constexpr uint8_t STATE_IDLE=0,STATE_RX=1,STATE_TX_WAIT=3;
struct CustomSX1262 {
  bool disabled=false;
  bool pending=false;
  bool irq_during_standby=false;
  int standby_result=RADIOLIB_ERR_NONE;
  unsigned standby_calls=0,flag_writes=0;
  int standby() {++standby_calls;if(irq_during_standby)pending=true;return standby_result;}
  void setRxPowerSavingRfRxDisabled(bool value) {++flag_writes;disabled=value;}
  bool isRxPowerSavingRfRxDisabled() const {return disabled;}
};
class RadioLibWrapper {
public:
  CustomSX1262 physical;
  CustomSX1262* _radio=&physical;
  uint8_t state=STATE_RX;
  bool _profile_repair_required=false,_profile_refresh_required=false;
  bool _beacon_owned=false,_beacon_restoring=false,_cw_active=false;
  bool _rx_hold_continuous=false,_rx_ps_armed=true;
  bool chip_busy=false,receiving=false,rearm_fails=false,retry_pending=false;
  bool _profile_rxps_suspended=false,_profile_saved_rxps=false;
  bool _rx_ps_enabled=true;
  uint32_t _rx_ps_rx_us=123456,_rx_ps_sleep_us=765432;
  uint32_t n_recv=7,n_sent=9,n_recv_errors=3,n_wd_soft=11,n_wd_hard=13;
  uint32_t _rx_ps_arm_failures=23;
  RxPowerSavingArmRetryState _rx_ps_arm_retry;
  uint32_t last_rx=1234;
  unsigned rearm_calls=0;
  bool hasPendingRadioInterrupt() const {return physical.pending;}
  void claimRadioInterrupt() {physical.pending=false;}
  bool isChipBusy() const {return chip_busy;}
  bool isReceivingPacket() const {return receiving;}
  bool isCarrierWaveActive() const {return _cw_active;}
  bool isBeaconRadioOwned() const {return _beacon_owned;}
  void startRecv() {
    ++rearm_calls;
    state=rearm_fails?STATE_IDLE:STATE_RX;
    retry_pending=rearm_fails;
    _rx_ps_armed=!rearm_fails&&_rx_ps_enabled;
  }
  uint8_t beginReconfigure();
  void endReconfigure(bool);
  bool isInRecvMode() const;
  virtual bool supportsRxPowerSavingRfRxDisable() const {return false;}
  virtual bool setRxPowerSavingRfRxDisabled(bool) {return false;}
  virtual mesh::RadioParamApplyResult trySetRxPowerSavingRfRxDisabled(bool) {
    return mesh::RadioParamApplyResult::FAILED;
  }
  virtual bool isRxPowerSavingRfRxDisabled() const {return false;}
  uint32_t getRxPsWatchdogSoftCount() const {return n_wd_soft;}
  uint32_t getRxPsWatchdogHardCount() const {return n_wd_hard;}
#include "production_radio_reset.inc"
};
#include "production_admission.inc"
struct Driver:RadioLibWrapper {
#include "production_rxen.inc"
};
struct Dispatcher {
  uint32_t n_sent_flood=1,n_sent_direct=2,n_recv_flood=3,n_recv_direct=4;
  uint16_t _err_flags=0x12;
  uint32_t last_meshcore_rx=23456;
  std::vector<int> outbound{42};
#include "production_dispatcher_reset.inc"
};
struct Tables {
  uint32_t _direct_dups=8,_flood_dups=10;
  std::vector<int> dedup_history{1,2,3};
#include "production_table_reset.inc"
};
struct Harness {
  Driver driver;Dispatcher dispatcher;Tables tables;
  unsigned clears=0;
  bool command(const char* command,char* reply,size_t cap,bool available=true) {
    return mesh::companion::handleRadioDiagnosticsCommand(driver,available,command,reply,cap,[this](){
      ++clears;driver.resetStats();dispatcher.resetStats();tables.resetStats();
    });
  }
};
static void bounds(Harness& h,const char* command,const std::string& expected,bool available=true) {
  for(size_t cap=1;cap<=176;++cap) {
    std::vector<unsigned char> bytes(cap+16,0xa5);
    char* reply=reinterpret_cast<char*>(bytes.data()+8);
    assert(h.command(command,reply,cap,available));
    assert(std::string(reply)==expected.substr(0,cap-1));
    for(size_t i=0;i<8;++i)assert(bytes[i]==0xa5);
    for(size_t i=8+cap;i<bytes.size();++i)assert(bytes[i]==0xa5);
  }
}
static void backend_guards() {
  for(unsigned cause=0;cause<7;++cause) {
    Driver d;
    if(cause==0)d.chip_busy=true;
    if(cause==1)d.physical.pending=true;
    if(cause==2)d.receiving=true;
    if(cause==3)d.state=STATE_TX_WAIT;
    if(cause==4)d._cw_active=true;
    if(cause==5)d.physical.irq_during_standby=true;
    if(cause==6)d.physical.standby_result=-1;
    assert(!d.setRxPowerSavingRfRxDisabled(true));
    assert(!d.physical.disabled&&d.physical.flag_writes==0&&d.rearm_calls==0);
    assert(d._rx_ps_enabled&&d._rx_ps_rx_us==123456&&d._rx_ps_sleep_us==765432);
    if(cause==1||(cause==5&&d.supportsRxPowerSavingRfRxDisable()))assert(d.physical.pending);
  }
#if MESH_ENABLE_TX_BEACONS
  Driver beacon;beacon._beacon_owned=true;
  assert(!beacon.setRxPowerSavingRfRxDisabled(true));
  assert(beacon.physical.standby_calls==0&&beacon.physical.flag_writes==0);
#endif
  Driver d;
#if defined(SX126X_RXEN) && SX126X_RXEN != -1
  assert(d.setRxPowerSavingRfRxDisabled(true));
  assert(d.physical.disabled&&d.rearm_calls==1&&d.physical.standby_calls==1);
  assert(d._rx_ps_enabled&&d._rx_ps_armed&&d.state==STATE_RX);
  d.rearm_fails=true;
  assert(!d.setRxPowerSavingRfRxDisabled(false));
  assert(d.physical.disabled&&d.state==STATE_IDLE&&d.retry_pending);
  assert(d.physical.flag_writes==3); // Accepted true; failed false followed by true rollback.
  assert(d._rx_ps_enabled&&d._rx_ps_rx_us==123456&&d._rx_ps_sleep_us==765432);
  // Existing normal receive recovery reuses the restored old policy.
  d.rearm_fails=false;d.startRecv();assert(d.isInRecvMode()&&d.physical.disabled);
  d.state=STATE_IDLE;d._rx_ps_armed=false;
  assert(d.setRxPowerSavingRfRxDisabled(false));assert(!d.physical.disabled);
#else
  assert(!d.supportsRxPowerSavingRfRxDisable());
  assert(!d.setRxPowerSavingRfRxDisabled(true));
  assert(d.physical.standby_calls==0&&d.physical.flag_writes==0);
#endif
}
static void radio2_suspended_rxps() {
#if defined(SX126X_RXEN) && SX126X_RXEN != -1
  Driver d;
  d._rx_ps_enabled=false;d._rx_ps_armed=false;
  d._profile_rxps_suspended=true;d._profile_saved_rxps=true;
  auto retained=[&]() {
    assert(!d._rx_ps_enabled&&!d._rx_ps_armed);
    assert(d._profile_rxps_suspended&&d._profile_saved_rxps);
    assert(d._rx_ps_rx_us==123456&&d._rx_ps_sleep_us==765432);
  };
  // Radio2 receives continuously while retaining the user's RXPS-on policy.
  // Calling setRxPowerSaving(false,...) here would overwrite that saved policy.
  assert(d.setRxPowerSavingRfRxDisabled(true));retained();
  assert(d.isInRecvMode()&&d.physical.disabled);
  d.chip_busy=true;
  assert(!d.setRxPowerSavingRfRxDisabled(false));retained();
  assert(d.physical.disabled);
  d.chip_busy=false;d.rearm_fails=true;
  assert(!d.setRxPowerSavingRfRxDisabled(false));retained();
  assert(d.physical.disabled&&d.state==STATE_IDLE&&d.retry_pending);
  d.rearm_fails=false;d.startRecv();retained();
  assert(d.isInRecvMode()&&d.physical.disabled);
  assert(d.setRxPowerSavingRfRxDisabled(false));retained();
  assert(d.isInRecvMode()&&!d.physical.disabled);
#endif
}
int main() {
  {
    Driver driver;
    char reply[80] = "untouched";
    const auto before = driver.physical.flag_writes;
    assert(!mesh::companion::handleRadioDiagnosticsCommand<false>(driver, true,
        "set radio.rxps.rfrx_disabled on", reply, sizeof(reply), []() {}));
    assert(driver.physical.flag_writes == before && !strcmp(reply, "untouched"));
    assert(mesh::companion::handleRadioDiagnosticsCommand<false>(driver, true,
        "get rxps.wd", reply, sizeof(reply), []() {}));
    assert(strncmp(reply, "> soft=", 7) == 0);
  }
  backend_guards();radio2_suspended_rxps();Harness h;char reply[160];
  bounds(h,"get rxps.wd","> soft=11,hard=13");
#if defined(SX126X_RXEN) && SX126X_RXEN != -1
  bounds(h,"get radio.rxps.rfrx_disabled","> off");
  bounds(h,"get radio.rxps.rfrx_disabled","Error: radio unavailable",false);
  assert(h.command("set radio.rxps.rfrx_disabled on",reply,sizeof(reply)));
  assert(!strcmp(reply,"OK - radio.rxps.rfrx_disabled on")&&h.driver.physical.disabled);
  h.driver.chip_busy=true;
  assert(h.command("set radio.rxps.rfrx_disabled off",reply,sizeof(reply)));
  assert(!strcmp(reply,"Error: radio busy or RXEN change failed")&&h.driver.physical.disabled);
  h.driver.chip_busy=false;
  h.driver.rearm_fails=true;
  assert(h.command("set radio.rxps.rfrx_disabled off",reply,sizeof(reply)));
  assert(!strcmp(reply,"Error: radio busy or RXEN change failed")&&h.driver.physical.disabled);
  h.driver.rearm_fails=false;h.driver.startRecv();
  assert(h.command("set radio.rxps.rfrx_disabled off",reply,sizeof(reply)));
  assert(!h.driver.physical.disabled);
#else
  bounds(h,"get radio.rxps.rfrx_disabled","Error: unsupported");
  bounds(h,"set radio.rxps.rfrx_disabled on","Error: unsupported");
  assert(h.driver.physical.flag_writes==0);
#endif
  const unsigned writes=h.driver.physical.flag_writes;
  for(const char* command:{"set radio.rxps.rfrx_disabled", "set radio.rxps.rfrx_disabled ",
        "set radio.rxps.rfrx_disabled banana","set radio.rxps.rfrx_disabled on extra",
        "set radio.rxps.rfrx_disabled\ton"}) {
    assert(h.command(command,reply,sizeof(reply)));
    assert(!strncmp(reply,"Error: use ",11));
  }
  for(const char* command:{"get rxps.wdx","get rxps.wd extra","get radio.rxps.rfrx_disabledx",
        "set radio.rxps.rfrx_disabledx on","clear stats extra","clear stats ","CLEAR stats"}) {
    memset(reply,'X',sizeof(reply));assert(!h.command(command,reply,sizeof(reply)));
    for(char c:reply)assert(c=='X');
  }
  assert(h.driver.physical.flag_writes==writes);
  assert(!h.command(nullptr,reply,sizeof(reply)));
  assert(!h.command("clear stats",nullptr,sizeof(reply)));
  assert(!h.command("clear stats",reply,0));assert(h.clears==0);
  assert(h.driver._rx_ps_arm_failures==23);
  for(unsigned i=0;i<3;++i)
    h.driver._rx_ps_arm_retry.recordFailure(1000);
  assert(!h.driver._rx_ps_arm_retry.canAttempt(1000));
  assert(h.command("clear stats",reply,sizeof(reply)));
  assert(!strcmp(reply,"(OK - stats reset)")&&h.clears==1);
  assert(h.driver.n_recv==0&&h.driver.n_sent==0&&h.driver.n_recv_errors==0);
  assert(h.driver._rx_ps_arm_failures==0);
  assert(h.driver._rx_ps_arm_retry.consecutiveFailures()==0&&h.driver._rx_ps_arm_retry.canAttempt(1000));
  assert(h.driver._rx_ps_enabled&&h.driver._rx_ps_rx_us==123456&&h.driver._rx_ps_sleep_us==765432);
  assert(h.dispatcher.n_sent_flood==0&&h.dispatcher.n_sent_direct==0&&h.dispatcher.n_recv_flood==0&&h.dispatcher.n_recv_direct==0);
  assert(h.dispatcher._err_flags==0&&h.tables._direct_dups==0&&h.tables._flood_dups==0);
  assert(h.driver.n_wd_soft==11&&h.driver.n_wd_hard==13&&h.driver.last_rx==1234);
  assert(h.dispatcher.last_meshcore_rx==23456&&h.dispatcher.outbound==std::vector<int>{42});
  assert(h.tables.dedup_history==std::vector<int>({1,2,3}));
  const std::string cad="radio.cad on, scan=auto(3500) ms, retry=auto ms, max=60000 ms, busy=4294967295";
  for(size_t cap=1;cap<=176;++cap) {
    std::vector<unsigned char> bytes(cap+16,0xa5);char* out=reinterpret_cast<char*>(bytes.data()+8);
    mesh::companion::formatCadDiagnostics(out,cap,true,"auto(3500)","auto","60000",UINT32_MAX);
    assert(std::string(out)==cad.substr(0,cap-1));
    for(size_t i=0;i<8;++i)assert(bytes[i]==0xa5);
    for(size_t i=8+cap;i<bytes.size();++i)assert(bytes[i]==0xa5);
  }
  mesh::companion::formatCadDiagnostics(nullptr,0,true,"a","b","c",1);
  puts("Companion radio diagnostics cases passed");
}
