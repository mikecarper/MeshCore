"""Execute Companion radio commands with production commit/recovery methods."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'examples/companion_radio/MyMesh.cpp'

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <initializer_list>
#include <vector>
#include <helpers/CLICommandUtils.h>
#include <helpers/RadioProfileCommandUtils.h>
#include <helpers/TempRadioReplyBarrier.h>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define MAX_LORA_TX_POWER 22
#define PUB_KEY_SIZE 32
#define COMPANION_FEATURE_TEMP_RADIO 1
#define COMMAND_RADIO_APPLY_TIMEOUT_MS 3000
@CODES@
static bool tx_inflight=false;
static std::vector<unsigned> completion_order;
namespace mesh {
enum class RadioParamApplyResult { APPLIED, BUSY, FAILED };
struct Packet {bool radio_reply=false;};
struct Mesh {
  void onSendComplete(Packet*) { completion_order.push_back(1); }
  void onSendFail(Packet*) {}
  void onRadioProfileCopyQueued(Packet*,const Packet*,uint8_t) {}
  bool allowDirectRetry(const Packet*,const uint8_t*,uint8_t) const {return true;}
};
}
using Apply = mesh::RadioParamApplyResult;
static uint32_t now_ms = 100;
struct Clock { uint32_t getMillis() const { return now_ms; } } clock_source;
struct BaseSerialInterface {
  BaseSerialInterface* current = this;
  BaseSerialInterface* disconnected = nullptr;
  bool busy = false;
  BaseSerialInterface* captureReplyRoute() { return current; }
  bool isReplyRouteAvailable(BaseSerialInterface* route) { return route && route != disconnected; }
  bool isReplyRouteWriteBusy(BaseSerialInterface*) { return busy; }
};
struct Prefs {
  float freq=915, bw=250;
  uint8_t sf=7, cr=5, client_repeat=0, rx_ps_level=1, rx_ps_preamble=16;
  uint32_t rx_ps_rx_us=700, rx_ps_sleep_us=25000;
  int8_t tx_power_dbm=3;
  bool rx_boosted_gain=false, rx_powersaving_enabled=false;
};
bool recalcRxPowerSavingFromLevel(uint8_t,uint8_t sf,float bw,uint8_t,
                                 uint32_t* rx,uint32_t* sleep) {
  *rx=sf*100; *sleep=uint32_t(bw*100);return true;
}
struct Radio {
  bool carrier=false, morse_tx=false, morse_rx=false;
  bool isCarrierWaveActive() const { return carrier; }
  bool isMorseTxActive() const { return morse_tx; }
  bool isMorseRxActive() const { return morse_rx; }
  struct {struct {uint8_t sf=7;float bw=250;} primary;} p;
  auto* profiles() {return &p;}
} radio;
struct ContactInfo {
  struct {uint8_t pub_key[32]={};} id;
  bool isRemoteCLIAllowed() const {return true;}
};
namespace mesh {
struct RadioProfileCLI {
  static bool parseSuffix(const char* input,unsigned fields,char* out,size_t size,uint16_t& preamble) {
    return cli::parseRadioPreambleSuffix(input,fields,out,size,preamble);
  }
};
}
#define TXT_TYPE_CLI_COMMAND 1
struct Driver {
  bool partial_failure=false, power_verified=true, rf_supported=true, rf_disabled=false;
  std::deque<Apply> rf_results;
  bool supportsRxPowerSavingRfRxDisable() const {return rf_supported;}
  Apply trySetRxPowerSavingRfRxDisabled(bool disabled) {
    const auto result=rf_results.empty()?Apply::APPLIED:rf_results.front();
    if(!rf_results.empty())rf_results.pop_front();
    if(result==Apply::APPLIED)rf_disabled=disabled;
    return result;
  }
  int8_t power=3, invalid_power=127;
  unsigned calls=0;
  std::deque<Apply> accepted;
  bool isTxPowerValid(int8_t requested) const { return requested!=invalid_power; }
  bool isTxPowerApplied(int8_t requested) const { return power_verified && power==requested; }
  Apply trySetTxPower(int8_t requested) {
    ++calls;
    Apply result=Apply::APPLIED;
    if (!accepted.empty()) { result=accepted.front(); accepted.pop_front(); }
    if (tx_inflight || radio.carrier || radio.morse_tx || radio.morse_rx) result=Apply::BUSY;
    if (result!=Apply::BUSY) power_verified=result==Apply::APPLIED;
    if (result==Apply::APPLIED || (result==Apply::FAILED && partial_failure)) power=requested;
    if (result==Apply::APPLIED) completion_order.push_back(2);
    return result;
  }
  bool setTxPower(int8_t requested) { return trySetTxPower(requested)==Apply::APPLIED; }
  Apply trySetRxBoostedGainMode(bool) { return Apply::APPLIED; }
  bool rxps_supported=false;
  uint32_t rx_us=0,sleep_us=0;
  Apply trySetRxPowerSaving(bool,uint32_t rx,uint32_t sleep) {
    rx_us=rx;sleep_us=sleep;return Apply::APPLIED;
  }
  bool supportsRxBoostedGainMode() const { return false; }
  bool setRxBoostedGainMode(bool) { return true; }
  bool supportsRxPowerSaving() const { return rxps_supported; }
  bool setRxPowerSaving(bool,uint32_t,uint32_t) { return true; }
} radio_driver;
@RETRY_DELAY@
struct CommonRadioPrefs {static bool isValidBandwidth(float){return true;}};
struct MyMesh : mesh::Mesh {
  int8_t rf_rx_disable_pending=-1;uint32_t rf_rx_retry_at=0;
  struct Profiles {
    uint16_t preamble=0,persisted_preamble=0;
    bool storage_ok=true,dirty=false;
    std::deque<bool> save_results;
    bool acceptsPrimary(float f,float bw,uint8_t sf,uint8_t cr,uint16_t) const {
      return f>=150 && f<=2500 && bw>=7 && bw<=500 && sf>=5 && sf<=12 && cr>=5 && cr<=8;
    }
    uint16_t primaryPreamble() const { return preamble; }
    bool savePrimaryPreamble(uint16_t next) {
      bool ok=storage_ok;
      if(!save_results.empty()){ok=save_results.front();save_results.pop_front();}
      if(!ok)return false;
      preamble=persisted_preamble=next;dirty=false;return true;
    }
    bool restorePrimaryPreamble(uint16_t previous) {
      if(savePrimaryPreamble(previous))return true;
      preamble=previous;dirty=true;return false;
    }
    void servicePendingTuning() {if(dirty)savePrimaryPreamble(preamble);}
    bool hasPendingTuning() const {return dirty;}
    void appendPrimaryChirpWarning(char*,size_t,uint8_t,float,uint16_t) {}
  } _radio_profiles;
  mesh::TempRadioReplyBarrier _primary_radio_reply;
  bool _primary_radio_blocked=false,_primary_radio_reply_admission=false;
  Prefs _prefs, durable, live;
  uint16_t live_preamble=0;
  float _temp_radio_freq=916,_temp_radio_bw=125;
  uint8_t _temp_radio_sf=8,_temp_radio_cr=6;
  uint16_t _temp_radio_preamble=16;
  uint32_t _temp_radio_retry_at=0;
  uint8_t _temp_radio_failures=0;
  BaseSerialInterface serial, original, other;
  BaseSerialInterface* _serial=&serial;
  Clock* _ms=&clock_source;
  bool storage_accepts=true, _radio_available=true, outbound=false;
  unsigned saves=0, applies=0;
  std::deque<Apply> accepted;
  bool saved_radio_apply_pending=false;
  bool tx_power_apply_pending=false, _temp_radio_applied=false;
  uint32_t tx_power_apply_retry_at=0, _temp_radio_set_at=0, _temp_radio_revert_at=0;
  Radio* _radio=&radio;
  bool command_radio_apply_pending=false;
  uint32_t radio_apply_retry_at=0, command_radio_apply_deadline=0;
  uint8_t radio_apply_failures=0;
  float command_radio_freq=0, command_radio_bw=0;
  uint8_t command_radio_sf=0, command_radio_cr=0, command_radio_repeat=0;
  BaseSerialInterface* command_radio_reply_route=nullptr;
  uint8_t cmd_frame[180]={};
  struct Reply { uint8_t error; BaseSerialInterface* route; };
  std::vector<Reply> replies;
  struct {
    void complete(mesh::Packet*) { completion_order.push_back(3); }
    void fail(mesh::Packet*) {}
    void copy(const mesh::Packet*,const mesh::Packet*) {}
    bool ownsReply(const mesh::Packet*) const {return false;}
    uint32_t generation() const {return 0;}
    void beginCommand(const uint8_t*) {}
    void endCommand() {}
    bool pending() const {return false;}
    const uint8_t* actor() const {static uint8_t key[32]={};return key;}
    void prepareReply(mesh::Packet*) {}
    void armReply(mesh::Packet*,bool) {}
  } _morse_reply;
  bool _morse_reply_admission=false;
  @RESULT_ENUM@;
  static const char* radioSettingReply(RadioSettingResult result);
  MyMesh() {
    serial.current=&original;radio_driver=Driver();radio=Radio();now_ms=100;
    tx_inflight=false;completion_order.clear();
  }
  uint32_t futureMillis(uint32_t delay) const { return now_ms+delay; }
  bool millisHasNowPassed(uint32_t deadline) const { return int32_t(now_ms-deadline)>=0; }
  bool hasOutbound() const { return outbound; }
  bool isTempRadioActive() const { return _temp_radio_applied; }
  bool isValidClientRepeatFreq(uint32_t) const { return true; }
  bool savePrefs() { ++saves; if(!storage_accepts)return false; durable=_prefs; return true; }
  void writeOKFrame(BaseSerialInterface* route=nullptr) { replies.push_back({0,route?route:serial.current}); }
  void writeErrFrame(uint8_t code,BaseSerialInterface* route=nullptr) { replies.push_back({code,route?route:serial.current}); }
  Apply tryApplyRadioParams(float freq,float bw,uint8_t sf,uint8_t cr,bool=false,uint16_t=0) {
    ++applies;
    Apply result=Apply::APPLIED;
    if(!accepted.empty()) { result=accepted.front(); accepted.pop_front(); }
    if(result==Apply::APPLIED) {
      live.freq=freq;live.bw=bw;live.sf=sf;live.cr=cr;
      radio.p.primary.sf=sf;radio.p.primary.bw=bw;live_preamble=_radio_profiles.preamble;
    }
    return result;
  }
  bool applySavedRadioParams();
  RadioSettingResult applyAndSaveTxPower(int8_t);
  void servicePendingTxPower();
  void onSendComplete(mesh::Packet*);
  void onSendFail(mesh::Packet*);
  void onRadioProfileCopyQueued(mesh::Packet*,const mesh::Packet*,uint8_t);
  bool allowDirectRetry(const mesh::Packet*,const uint8_t*,uint8_t) const;
  void finishPrimaryRadioReply(bool);
  void onCLICommandRecv(const ContactInfo&,mesh::Packet*,uint32_t,const char*,char*);
  void onCLIReplyPrepared(const ContactInfo&,mesh::Packet*);
  void onCLIReplyQueued(const ContactInfo&,mesh::Packet*,bool);
  void markConnectionActive(const ContactInfo&) {}
  template<typename... T> void queueMessage(T&&...) {}
  bool handleCommand(const char* command,uint32_t sender_timestamp,char* reply) {
    const size_t reply_capacity=160;
    @RADIO_CLI_BRANCH@
    @FREQ_CLI_BRANCH@
    return false;
  }
  RadioSettingResult applyAndSaveRadioParams(float,float,uint8_t,uint8_t,uint8_t,uint16_t,bool=false);
  bool scheduleTempRadio(float,float,uint8_t,uint8_t,uint32_t,char*,size_t);
  void scheduleNormalRadio(char*,size_t);
  void appendRxPowerSavingAdjustmentNote(char*,size_t,uint8_t,float) const {}
  void serviceTempRadio();
  void queueSavedRadioApply();
  void servicePendingLocalRadioSettings();
  bool applyRxPowerSavingRfRxDisabled(const char*,char*,size_t);
  void frame(size_t len) { @FRAME_BRANCHES@ }
  void recover() { servicePendingLocalRadioSettings(); }
  bool command(const char* command,char* reply) {
    const size_t reply_capacity=160;
#if defined(MESH_HIL_TX_FREQ_MHZ)
    @TX_STATUS_BRANCH@
#endif
    @CLI_BRANCH@
    return false;
  }
  void powerFrame(uint8_t power,size_t size=2) {
    cmd_frame[0]=CMD_SET_RADIO_TX_POWER;cmd_frame[1]=power;frame(size);
  }
  void tupleFrame() {
    cmd_frame[0]=CMD_SET_RADIO_PARAMS;
    uint32_t freq=920000,bw=125000;
    memcpy(cmd_frame+1,&freq,4);memcpy(cmd_frame+5,&bw,4);
    cmd_frame[9]=9;cmd_frame[10]=6;cmd_frame[11]=1;
    frame(12);
  }
  void expectError(uint8_t code,BaseSerialInterface* route=nullptr) {
    if(replies.size()!=1 || replies[0].error!=code) {
      fprintf(stderr,"Expected reply %u; count=%zu first=%u pending=%u saves=%u now=%u\n",
              unsigned(code),replies.size(),replies.empty()?255u:unsigned(replies[0].error),
              unsigned(command_radio_apply_pending),saves,unsigned(now_ms));
    }
    assert(replies.size()==1 && replies[0].error==code);
    assert(replies[0].route==(route?route:&original));
  }
  void expectOriginalPrefs() {
    assert(_prefs.freq==915 && _prefs.bw==250 && _prefs.sf==7 && _prefs.cr==5);
    assert(_prefs.client_repeat==0 && _prefs.rx_ps_rx_us==700 && _prefs.rx_ps_sleep_us==25000);
    assert(durable.freq==915 && durable.bw==250 && durable.sf==7 && durable.cr==5);
  }
};
@METHODS@
int main() {
#if defined(MESH_HIL_TX_FREQ_MHZ)
  // Saved power alone is not proof of the current hardware state. Startup,
  // recovery and unavailable radios must not report an unverified target.
  {
    MyMesh m;char reply[160]={};
    assert(m.command("get tx.status",reply) && !strcmp(reply,"> desired=3,apply=applied"));
    radio_driver.power_verified=false;
    assert(m.command("get tx.status",reply) && !strcmp(reply,"> desired=3,apply=pending"));
    radio_driver.power_verified=true;radio_driver.power=7;
    assert(m.command("get tx.status",reply) && !strcmp(reply,"> desired=3,apply=pending"));
    radio_driver.power=3;m._radio_available=false;
    assert(m.command("get tx.status",reply) && !strcmp(reply,"> desired=3,apply=pending"));
    m._radio_available=true;m.tx_power_apply_pending=true;
    assert(m.command("get tx.status",reply) && !strcmp(reply,"> desired=3,apply=pending"));
    m.tx_power_apply_pending=false;
    assert(m.command("get tx.status",reply) && !strcmp(reply,"> desired=3,apply=applied"));
    assert(m.saves==0 && radio_driver.calls==0);
  }
#else
  {
    MyMesh m;char reply[160]={};
    assert(!m.command("get tx.status",reply));
  }
#endif
  // Runtime RXEN admission is automatic but never writes persistent prefs.
  {
    MyMesh m;char reply[160]={};radio_driver.rf_results={Apply::BUSY,Apply::BUSY,Apply::APPLIED};
    assert(m.applyRxPowerSavingRfRxDisabled("on",reply,sizeof(reply)) && !strcmp(reply,"OK"));
    assert(m.rf_rx_disable_pending==1 && !radio_driver.rf_disabled && !m.saves);
    assert(m.applyRxPowerSavingRfRxDisabled("off",reply,sizeof(reply)) && !strcmp(reply,"OK"));
    assert(m.rf_rx_disable_pending==0);
    m.servicePendingLocalRadioSettings();
    assert(m.rf_rx_disable_pending<0 && !radio_driver.rf_disabled && !m.saves);
    for(const char* invalid:{"", "ON", "off extra", "2"}) {
      assert(m.applyRxPowerSavingRfRxDisabled(invalid,reply,sizeof(reply)) && strstr(reply,"Error"));
      assert(m.rf_rx_disable_pending<0 && !m.saves);
    }
    radio_driver.rf_supported=false;
    assert(m.applyRxPowerSavingRfRxDisabled("on",reply,sizeof(reply)) && strstr(reply,"unsupported"));
    assert(m.rf_rx_disable_pending<0 && !m.saves);
  }
  // Invalid input never reaches hardware or storage.
  for (int value : {-10,23}) {
    MyMesh m;m.powerFrame(uint8_t(value));m.expectError(ERR_CODE_ILLEGAL_ARG);
    assert(m.saves==0 && radio_driver.calls==0);
  }
  {
    MyMesh m;m.powerFrame(17,1);m.expectError(ERR_CODE_ILLEGAL_ARG);
    assert(m.saves==0 && radio_driver.calls==0);
  }
  // Hardware-specific invalid values cannot become saved intent, even while
  // a pending tuple prevents the ordinary hardware setter from being called.
  for(bool tuple_pending : {false,true}) {
    MyMesh m;radio_driver.accepted={Apply::BUSY};m.powerFrame(17);
    m.replies.clear();m.saved_radio_apply_pending=tuple_pending;
    m.tx_power_apply_retry_at=now_ms+1000;radio_driver.invalid_power=18;
    const unsigned calls=radio_driver.calls;
    m.powerFrame(18);m.expectError(ERR_CODE_BAD_STATE);
    assert(m.tx_power_apply_pending && m.tx_power_apply_retry_at==now_ms+1000);
    assert(m._prefs.tx_power_dbm==17 && m.durable.tx_power_dbm==17);
    assert(m.saves==1 && radio_driver.calls==calls);
    assert(m.saved_radio_apply_pending==tuple_pending);
  }
  // Immediate application still commits before acknowledging the request.
  {
    MyMesh m;m.powerFrame(17);m.expectError(0);
    assert(m.saves==1 && m.durable.tx_power_dbm==17 && radio_driver.power==17);
    assert(!m.tx_power_apply_pending && !m.command_radio_apply_pending);
  }
  // Busy reception accepts one durable intent. It completes automatically,
  // without a second command, additional save or delayed protocol reply.
  for (int power : {-9,17,22}) {
    MyMesh m;radio_driver.accepted={Apply::BUSY,Apply::BUSY,Apply::APPLIED};
    m.powerFrame(uint8_t(power));m.expectError(0);
    assert(m.tx_power_apply_pending && !m.command_radio_apply_pending);
    assert(m._prefs.tx_power_dbm==power && m.durable.tx_power_dbm==power);
    assert(m.saves==1 && radio_driver.power==3);
    m.serial.current=&m.other;m.serial.disconnected=&m.original;m.serial.busy=true;
    m.servicePendingTxPower();
    assert(m.tx_power_apply_pending && m.saves==1 && radio_driver.power==3);
    m.servicePendingTxPower();
    assert(!m.tx_power_apply_pending && m.saves==1 && radio_driver.power==power);
    assert(m.replies.size()==1); // It is saved intent, not an owned reply slot.
  }
  // A long receive lease cannot expire accepted power intent. Keep servicing
  // it for more than 30 seconds, then apply once the radio is free.
  {
    MyMesh m;radio_driver.accepted={Apply::BUSY};m.powerFrame(17);m.expectError(0);
    for(uint32_t elapsed=0;elapsed<=31000;elapsed+=1000) {
      now_ms=100+elapsed;radio_driver.accepted={Apply::BUSY};m.servicePendingTxPower();
      assert(m.tx_power_apply_pending && m.durable.tx_power_dbm==17);
      assert(m.saves==1 && radio_driver.power==3 && m.replies.size()==1);
    }
    ++now_ms;radio_driver.accepted={Apply::APPLIED};m.servicePendingTxPower();
    assert(!m.tx_power_apply_pending && radio_driver.power==17 && m.saves==1);
    assert(m.replies.size()==1); // No resend or reboot was required.
  }
  // Recovery-mode firmware can accept a saved target before radio startup.
  {
    MyMesh m;m._radio_available=false;m.powerFrame(17);m.expectError(0);
    assert(m.tx_power_apply_pending && m.durable.tx_power_dbm==17 && radio_driver.calls==0);
    m.servicePendingTxPower();assert(radio_driver.calls==0);
    m._radio_available=true;m.servicePendingTxPower();
    assert(!m.tx_power_apply_pending && radio_driver.power==17 && m.saves==1);
  }
  // A saturated TX queue can start its next packet before the outer loop gets
  // another chance. Apply pending power in the real completion callback's gap.
  {
    MyMesh m;mesh::Packet completed;tx_inflight=true;
    m.powerFrame(17);m.expectError(0);
    assert(m.tx_power_apply_pending && m.saves==1 && radio_driver.power==3);
    completion_order.clear();tx_inflight=false; // The completed TX is now idle.
    m.onSendComplete(&completed);
    assert((completion_order==std::vector<unsigned>{1,2,3}));
    assert(!m.tx_power_apply_pending && radio_driver.power==17 && m.durable.tx_power_dbm==17);
    assert(m.saves==1 && m.replies.size()==1);
    completion_order.push_back(4);tx_inflight=true; // Dispatcher starts next TX.
    const unsigned calls=radio_driver.calls;m.servicePendingTxPower();
    assert(radio_driver.calls==calls && radio_driver.power==17 && m.saves==1);
  }
  // Newer accepted values replace saved pending intent, across frontends.
  {
    MyMesh m;radio_driver.accepted={Apply::BUSY,Apply::BUSY,Apply::APPLIED};
    m.powerFrame(17);m.expectError(0);
    char reply[160]={};
    assert(m.command("set tx 12",reply) && !strncmp(reply,"OK",2));
    assert(strstr(reply,"pending"));
    assert(m.tx_power_apply_pending && m._prefs.tx_power_dbm==12 && m.durable.tx_power_dbm==12);
    m.servicePendingTxPower();
    assert(!m.tx_power_apply_pending && radio_driver.power==12 && m.saves==2);
    m.servicePendingTxPower();assert(m.saves==2 && radio_driver.power==12);
  }
  // A rejected second save preserves the previously accepted pending target.
  {
    MyMesh m;radio_driver.accepted={Apply::BUSY,Apply::BUSY,Apply::APPLIED};
    m.powerFrame(17);m.replies.clear();m.storage_accepts=false;
    m.tx_power_apply_retry_at=now_ms+1000;
    m.powerFrame(12);m.expectError(ERR_CODE_FILE_IO_ERROR);
    assert(m.tx_power_apply_pending && m._prefs.tx_power_dbm==17 && m.durable.tx_power_dbm==17);
    assert(m.tx_power_apply_retry_at==now_ms+1000);
    const unsigned calls=radio_driver.calls;
    m.servicePendingTxPower();assert(radio_driver.calls==calls);
    now_ms=m.tx_power_apply_retry_at;
    m.storage_accepts=true;m.servicePendingTxPower();
    assert(!m.tx_power_apply_pending && radio_driver.power==17 && m.saves==2);
  }
  // Storage failure restores both the durable value and physical output.
  {
    MyMesh m;m.storage_accepts=false;m.powerFrame(17);m.expectError(ERR_CODE_FILE_IO_ERROR);
    assert(m._prefs.tx_power_dbm==3 && m.durable.tx_power_dbm==3 && radio_driver.power==3);
    assert(radio_driver.calls==2 && !m.saved_radio_apply_pending && !m.tx_power_apply_pending);
  }
  // A rollback blocked by RX is retried without restoring the whole tuple or
  // waiting for a long-running temporary profile to expire.
  {
    MyMesh m;m.storage_accepts=false;
    radio_driver.accepted={Apply::APPLIED,Apply::BUSY,Apply::APPLIED};
    m.powerFrame(17);m.expectError(ERR_CODE_FILE_IO_ERROR);
    assert(m._prefs.tx_power_dbm==3 && radio_driver.power==17 && m.tx_power_apply_pending);
    assert(!m.saved_radio_apply_pending);
    m._temp_radio_applied=true;m.servicePendingTxPower();
    assert(!m.tx_power_apply_pending && radio_driver.power==3 && m.saves==1 && m.applies==0);
    assert(m.replies.size()==1); // A failed command receives no later success.
  }
  // A hardware-confirmed rollback clears prior pending intent. If rollback
  // is busy, retain the durable target for automatic physical recovery.
  for(bool rollback_busy : {false,true}) {
    MyMesh m;radio_driver.accepted={Apply::BUSY,Apply::APPLIED,
                                  rollback_busy?Apply::BUSY:Apply::APPLIED,Apply::APPLIED};
    m.powerFrame(17);m.replies.clear();m.storage_accepts=false;
    m.tx_power_apply_retry_at=now_ms+1000;
    m.powerFrame(12);m.expectError(ERR_CODE_FILE_IO_ERROR);
    assert(m._prefs.tx_power_dbm==17 && m.durable.tx_power_dbm==17);
    assert(m.tx_power_apply_pending==rollback_busy && m.tx_power_apply_retry_at==0);
    assert(radio_driver.power==(rollback_busy?12:17));
    const unsigned calls=radio_driver.calls;
    m.servicePendingTxPower();
    assert(!m.tx_power_apply_pending && radio_driver.power==17 && m.saves==2);
    assert(radio_driver.calls==calls+unsigned(rollback_busy));
  }
  // A genuine hardware rejection remains an error, including partial writes.
  // The previous durable value is reasserted automatically after rejection.
  for(bool partial : {false,true}) {
    MyMesh m;radio_driver.partial_failure=partial;
    radio_driver.accepted={Apply::FAILED,Apply::APPLIED};m.powerFrame(17);
    m.expectError(ERR_CODE_BAD_STATE);
    assert(m.saves==0 && m._prefs.tx_power_dbm==3 && m.durable.tx_power_dbm==3);
    assert(m.tx_power_apply_pending && !m.saved_radio_apply_pending);
    assert(radio_driver.power==(partial?17:3));
    m.servicePendingTxPower();
    assert(!m.tx_power_apply_pending && radio_driver.power==3 && m.saves==0);
  }
  // Repeated hardware failures back off, then complete without losing intent.
  // Verify wrap-safe retry deadlines as well as the ordinary clock.
  for(uint32_t start : {100u,UINT32_MAX-500u,UINT32_MAX-999u}) {
    MyMesh m;now_ms=start;
    radio_driver.accepted={Apply::BUSY,Apply::FAILED,Apply::APPLIED};
    m.powerFrame(17);m.servicePendingTxPower();
    assert(m.tx_power_apply_pending && m.tx_power_apply_retry_at!=0);
    const unsigned calls=radio_driver.calls;
    assert(m.tx_power_apply_retry_at==(uint32_t(start+1000u)?uint32_t(start+1000u):1u));
    now_ms=m.tx_power_apply_retry_at-1;m.servicePendingTxPower();
    assert(radio_driver.calls==calls && m.tx_power_apply_pending);
    ++now_ms;m.servicePendingTxPower();
    assert(!m.tx_power_apply_pending && radio_driver.power==17 && m.saves==1);
  }
  // CW and Morse ownership are retained. Temporary channel leases do not
  // block TX-only updates: changing power must not revert their radio tuple.
  for(unsigned owner=0;owner<3;++owner) {
    MyMesh m;radio_driver.accepted={Apply::BUSY};m.powerFrame(17);
    if(owner==0)radio.carrier=true;
    else if(owner==1)radio.morse_tx=true;
    else radio.morse_rx=true;
    m.servicePendingTxPower();assert(m.tx_power_apply_pending && radio_driver.power==3);
    radio=Radio();m._temp_radio_applied=true;m.servicePendingTxPower();
    assert(!m.tx_power_apply_pending && radio_driver.power==17 && m.applies==0);
  }
  // A tuple and power setting accepted during RX share durable latest intent.
  {
    MyMesh m;m.accepted={Apply::BUSY,Apply::APPLIED};m.tupleFrame();m.expectError(0);
    assert(m.saved_radio_apply_pending && m.saves==1 && m.durable.freq==920);
    m.replies.clear();m.serial.current=&m.other;radio_driver.accepted={Apply::BUSY};
    m.powerFrame(17);m.expectError(0,&m.other);
    assert(m.tx_power_apply_pending && m.durable.tx_power_dbm==17);
    m.servicePendingLocalRadioSettings();
    assert(!m.saved_radio_apply_pending && !m.tx_power_apply_pending);
    assert(m.live.freq==920 && radio_driver.power==17 && m.saves==2);
  }
  // Frontends retain strict validation and report accepted pending intent.
  {
    MyMesh m;char reply[160]={};radio_driver.accepted={Apply::BUSY,Apply::APPLIED};
    assert(m.command("set tx 17",reply) && !strncmp(reply,"OK",2) && strstr(reply,"pending"));
    assert(m.command("set tx 17junk",reply) && strstr(reply,"Error"));
    assert(m.saves==1 && m.durable.tx_power_dbm==17);
    m.servicePendingTxPower();assert(!m.tx_power_apply_pending && radio_driver.power==17);
  }
  // Immediate tuple changes commit before OK; failed saves restore hardware.
  {
    MyMesh m;m.tupleFrame();m.expectError(0);
    assert(m._prefs.freq==920 && m.durable.freq==920 && m.live.freq==920);
    assert(m.durable.rx_ps_rx_us==900 && m.durable.rx_ps_sleep_us==12500);
    assert(!m.saved_radio_apply_pending);
  }
  {
    MyMesh m;m.storage_accepts=false;m.tupleFrame();m.expectError(ERR_CODE_FILE_IO_ERROR);
    m.expectOriginalPrefs();assert(m.live.freq==915 && !m.saved_radio_apply_pending);
  }
  // BUSY tuple acceptance survives disconnect and more than 30 seconds.
  {
    MyMesh m;m.accepted={Apply::BUSY};m.tupleFrame();m.expectError(0);
    assert(m.saved_radio_apply_pending && m.saves==1 && m.durable.freq==920);
    m.serial.disconnected=&m.original;
    for(uint32_t elapsed=0;elapsed<=31000;elapsed+=1000) {
      now_ms=100+elapsed;m.accepted={Apply::BUSY};m.servicePendingLocalRadioSettings();
      assert(m.saved_radio_apply_pending && m.saves==1 && m.replies.size()==1);
    }
    m.accepted={Apply::APPLIED};m.servicePendingLocalRadioSettings();
    assert(!m.saved_radio_apply_pending && m.live.freq==920 && m.saves==1);
  }
  // A newer accepted tuple replaces the desired target. A failed save does not.
  {
    MyMesh m;m.accepted={Apply::BUSY,Apply::BUSY,Apply::BUSY,Apply::APPLIED};
    m.tupleFrame();m.replies.clear();
    assert(m.applyAndSaveRadioParams(921,62.5f,8,5,0,32)==MyMesh::RadioSettingResult::Saved);
    assert(m.durable.freq==921 && m._radio_profiles.preamble==32);
    m.storage_accepts=false;
    assert(m.applyAndSaveRadioParams(922,125,7,5,0,16)==MyMesh::RadioSettingResult::SaveFailed);
    assert(m.durable.freq==921 && m._prefs.freq==921 && m._radio_profiles.preamble==32);
    m.servicePendingLocalRadioSettings();
    assert(!m.saved_radio_apply_pending && m.live.freq==921 && m.saves==3);
  }
  // Rollback failure queues the old durable tuple, not the rejected candidate.
  {
    MyMesh m;m.storage_accepts=false;m.accepted={Apply::APPLIED,Apply::BUSY,Apply::APPLIED};
    m.tupleFrame();m.expectError(ERR_CODE_FILE_IO_ERROR);m.expectOriginalPrefs();
    assert(m.saved_radio_apply_pending && m.live.freq==920);
    m.servicePendingLocalRadioSettings();
    assert(!m.saved_radio_apply_pending && m.live.freq==915 && m.saves==1);
  }
  // A temporary lease retains the modem until it ends; no resend is needed.
  {
    MyMesh m;m._temp_radio_applied=true;m.tupleFrame();m.expectError(0);
    assert(m.saved_radio_apply_pending && m.applies==0 && m.durable.freq==920);
    m.servicePendingLocalRadioSettings();assert(m.applies==0);
    m._temp_radio_applied=false;m.servicePendingLocalRadioSettings();
    assert(!m.saved_radio_apply_pending && m.live.freq==920 && m.saves==1);
  }
  // Saved full settings apply in the completion gap despite queued TX work.
  {
    MyMesh m;mesh::Packet packet;m.accepted={Apply::BUSY,Apply::APPLIED};m.tupleFrame();
    m.outbound=true;m.onSendComplete(&packet);
    assert(!m.saved_radio_apply_pending && m.live.freq==920 && m.saves==1);
  }

  // Both stores can fail in succession. Compensation still restores the old
  // runtime preamble, reports uncertainty, and repairs storage automatically.
  {
    MyMesh m;m._radio_profiles.preamble=m._radio_profiles.persisted_preamble=48;
    m._radio_profiles.save_results={true,false};m.storage_accepts=false;
    char reply[160]={};
    assert(m.handleCommand("set radio 920,125,9,6,32",0,reply));
    assert(!strcmp(reply,"Error: radio save unconfirmed"));
    assert(m._prefs.freq==915 && m._radio_profiles.preamble==48);
    assert(m._radio_profiles.persisted_preamble==32 && m._radio_profiles.dirty);
    assert(m.live.freq==915 && m.live_preamble==48 && m.saved_radio_apply_pending);
    m.recover();
    assert(!m._radio_profiles.dirty && m._radio_profiles.persisted_preamble==48);
    assert(!m.saved_radio_apply_pending && m.saves==1);
  }
  // A remote change shares durable acceptance with local frontends, but its
  // exact two reply copies must drain before the primary can move. RXPS still
  // follows the old active SF/BW until that handoff finishes.
  {
    MyMesh m;ContactInfo from;mesh::Packet a,b,unrelated;char reply[160]={};
    m.saved_radio_apply_pending=true;m._prefs.rx_powersaving_enabled=true;
    radio_driver.rxps_supported=true;
    m.onCLICommandRecv(from,nullptr,10,"set radio 920,125,9,6",reply);
    assert(!strcmp(reply,"OK") && m.saves==1 && m._primary_radio_blocked);
    m.onCLIReplyPrepared(from,&a);m.onRadioProfileCopyQueued(&b,&a,0);
    m.onCLIReplyQueued(from,&a,true);
    assert(a.radio_reply && !m.allowDirectRetry(&a,nullptr,0));
    m.recover();
    assert(m.applies==0 && !m.saved_radio_apply_pending);
    assert(radio_driver.rx_us==700 && radio_driver.sleep_us==25000);
    m.onSendComplete(&unrelated);assert(m._primary_radio_blocked && m.applies==0);
    m.onSendComplete(&a);assert(m._primary_radio_blocked && m.applies==0);
    m.onSendFail(&b);assert(!m._primary_radio_blocked);
    m.recover();
    assert(m.live.freq==920 && m.applies==1 && m.saves==1);
    assert(radio_driver.rx_us==900 && radio_driver.sleep_us==12500);
  }
  // Every outcome pair uses the actual completion/failure hooks: one
  // successful copy is enough only after both exact copies have drained.
  for(unsigned successes=0;successes<4;++successes) {
    MyMesh m;ContactInfo from;mesh::Packet a,b;char reply[160]={};
    m.onCLICommandRecv(from,nullptr,10,"set freq 920",reply);
    m.onCLIReplyPrepared(from,&a);m.onRadioProfileCopyQueued(&b,&a,0);
    m.onCLIReplyQueued(from,&a,true);
    if(successes&1)m.onSendComplete(&a);else m.onSendFail(&a);
    assert(m._primary_radio_blocked && m.applies==0);
    if(successes&2)m.onSendComplete(&b);else m.onSendFail(&b);
    m.recover();
    assert(m._primary_radio_blocked==(successes==0));
    assert(m.live.freq==(successes?920:915) && m.saves==1);
  }
  // Losing every response retains the old runtime channel. A duplicate of
  // the saved request replays acknowledgement ownership without another save;
  // an old or unrelated completion cannot release the new response barrier.
  {
    MyMesh m;ContactInfo from;mesh::Packet a,b,replay,unrelated;char reply[160]={};
    m.onCLICommandRecv(from,nullptr,10,"set freq 920",reply);
    m.onCLIReplyPrepared(from,&a);m.onRadioProfileCopyQueued(&b,&a,0);
    m.onCLIReplyQueued(from,&a,true);m.onSendFail(&a);m.onSendFail(&b);
    now_ms+=31000;m.recover();
    assert(m._primary_radio_blocked && m.live.freq==915 && m.applies==0 && m.saves==1);
    m.onCLICommandRecv(from,nullptr,11,"set freq 920",reply);
    assert(!strcmp(reply,"OK") && m.saves==1);
    m.onCLIReplyPrepared(from,&replay);m.onCLIReplyQueued(from,&replay,true);
    m.onSendComplete(&a);m.onSendComplete(&unrelated);
    assert(m._primary_radio_blocked && m.applies==0);
    m.onSendComplete(&replay);
    assert(!m._primary_radio_blocked && m.live.freq==920 && m.saves==1);
  }
  // A failed newer save cannot replace the older exact reply. A successful
  // newer remote request does replace it; late older completions are inert.
  for(bool save_new:{false,true}) {
    MyMesh m;ContactInfo from;mesh::Packet old,newer;char reply[160]={};
    m.onCLICommandRecv(from,nullptr,10,"set freq 920",reply);
    m.onCLIReplyPrepared(from,&old);m.onCLIReplyQueued(from,&old,true);
    m.storage_accepts=save_new;
    m.onCLICommandRecv(from,nullptr,11,"set freq 921",reply);
    assert((!strncmp(reply,"OK",2))==save_new);
    m.onCLIReplyPrepared(from,&newer);m.onCLIReplyQueued(from,&newer,true);
    m.onSendComplete(&old);
    if(save_new) {
      assert(m._primary_radio_blocked && m.applies==0);
      m.onSendComplete(&newer);
      assert(!m._primary_radio_blocked && m.live.freq==921);
    } else assert(!m._primary_radio_blocked && m.live.freq==920);
    assert(m.saves==2);
  }
  // No packet allocation is not evidence of delivery. A later local primary
  // setting can deliberately replace that blocked remote intent.
  {
    MyMesh m;ContactInfo from;mesh::Packet unrelated;char reply[160]={};
    m.onCLICommandRecv(from,nullptr,10,"set freq 920",reply);
    m.onCLIReplyQueued(from,nullptr,false);m.onSendComplete(&unrelated);
    assert(m._primary_radio_blocked && m.applies==0 && m.live.freq==915);
    assert(m.handleCommand("set freq 922",0,reply) && !strcmp(reply,"OK"));
    assert(!m._primary_radio_blocked && m.saved_radio_apply_pending);
    m.recover();assert(m.live.freq==922 && m.saves==2);
  }
  // TempRadio owns both its active tuple and its future normal restoration.
  // A remote permanent command is rejected before saving, and failing its
  // error reply cannot strand an active lease when that lease expires.
  for(unsigned phase=0;phase<3;++phase) {
    MyMesh m;ContactInfo from;mesh::Packet error;char reply[160]={};
    if(phase==0)m._temp_radio_set_at=now_ms+10;
    if(phase==1)m._temp_radio_applied=true;
    if(phase==2)m._temp_radio_revert_at=now_ms+10;
    m.onCLICommandRecv(from,nullptr,10,"set radio 920,125,9,6",reply);
    assert(strstr(reply,"Error") && m.saves==0 && !m._primary_radio_blocked);
    m.onCLIReplyPrepared(from,&error);m.onCLIReplyQueued(from,&error,true);
    m.onSendFail(&error);
    m._temp_radio_set_at=0;m._temp_radio_applied=true;m._temp_radio_revert_at=now_ms;
    m.live.freq=916;m.serviceTempRadio();
    assert(!m._temp_radio_applied && !m._temp_radio_revert_at && m.live.freq==915);
  }
  {
    MyMesh m;ContactInfo from;char reply[160]={};
    m.onCLICommandRecv(from,nullptr,10,"set freq 920",reply);
    assert(!m.scheduleTempRadio(916,125,8,6,1,reply,sizeof(reply)));
    assert(strstr(reply,"acknowledgement") && !m._temp_radio_set_at);
    m.scheduleNormalRadio(reply,sizeof(reply));
    assert(strstr(reply,"acknowledgement") && !m._temp_radio_revert_at);
  }
  puts("Companion automatic TX-power commit and recovery regressions passed");
}

'''

SLEEP_HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <helpers/CompanionDelayedReplies.h>
#include <helpers/PowerSavingDiagnostics.h>
static uint32_t now_ms=100;
struct Serial {
  bool hasPendingIO() const { return false; }
  bool isConnected() const { return false; }
  bool isReplyRouteAvailable(const Serial* route) const {
    return route==this&&isConnected();
  }
};
struct {
  bool isWatchdogObserving() const { return false; }
  bool isCalibratingNoiseFloor() const { return false; }
} radio_driver;
struct ContactStore {
@CONTACT_RECOVERY_API@
};
struct MyMesh {
  struct {bool hasPendingTuning() const{return false;}} _radio_profiles;
  ContactStore store;
  ContactStore* _store=&store;
  bool _radio_available=true, _iter_started=false;
  bool command_radio_apply_pending=false;
  bool saved_radio_apply_pending=false, _terminal_trace_pending=false;
  bool tx_power_apply_pending=false;
  int8_t rf_rx_disable_pending=-1;uint32_t rf_rx_retry_at=0;
  uint32_t tx_power_apply_retry_at=0;
  uint32_t radio_apply_retry_at=0, _scheduled_reboot_at=0;
  uint32_t emergency_client_repeat_send_at=0, dirty_contacts_expiry=0;
  uint8_t dirty_contacts_failures=0;
  mesh::CompanionDelayedReplies _delayed_replies;
  const void *sign_data=nullptr;
  const void *emergency_client_repeat_packet=nullptr;
  Serial* _serial=nullptr;
  Serial* _iter_reply_route=nullptr;
#if COMPANION_FEATURE_TEMP_RADIO
  bool _temp_radio_applied=false;
  uint32_t _temp_radio_set_at=0, _temp_radio_revert_at=0, _temp_radio_retry_at=0;
#endif
  bool millisHasNowPassed(uint32_t at) const { return int32_t(now_ms-at)>=0; }
  bool isDualRadioActive() const { return false; }
  bool canIdleDualRadio() const { return true; }
  bool isContactWriteDue() const { return false; }
  bool hasPendingOtaApply() const { return false; }
  bool hasPendingReqs() const { return _delayed_replies.hasRequest(); }
  bool hasQueuedWorkDue() const { return false; }
  bool getNextNoiseFloorWakeDelay(uint32_t&) const { return false; }
  bool hasRetryWorkDue() const { return false; }
  bool hasPendingWork() const;
  mesh::powerdiag::Mask pendingWorkReason() const;
};
@PENDING_WORK@
int main() {
  MyMesh m;
  assert(!m.hasPendingWork());
  m.tx_power_apply_pending=true;
  assert(m.hasPendingWork()); // TX-only intent must be serviced automatically.
  assert(m.pendingWorkReason()==mesh::powerdiag::mask(mesh::powerdiag::Reason::Scheduled));
  m.tx_power_apply_retry_at=now_ms+1000;
  assert(m.hasPendingWork()); // Event-driven sleep must not strand the retry.
  m.tx_power_apply_pending=false;m.tx_power_apply_retry_at=0;
  assert(!m.hasPendingWork());
  m.store._contact_recovery=ContactStore::ContactRecoveryPending;
  assert(m.hasPendingWork()); // Storage repair still needs the outer loop.
  m.store._contact_recovery=ContactStore::ContactRecoveryLegacy;
  assert(!m.hasPendingWork()); // A source flag alone must not keep it awake.
  m.store._contact_recovery=0;
  m.saved_radio_apply_pending=true;
  m._radio_available=false;
  assert(!m.hasPendingWork()); // Absent hardware cannot service the saved intent.
  m.radio_apply_retry_at=now_ms;
  assert(!m.hasPendingWork()); // An overdue retry must not spin in recovery mode.
  m._radio_available=true;
  m.radio_apply_retry_at=0;
  assert(m.hasPendingWork());
  m.radio_apply_retry_at=now_ms+10;
  assert(m.hasPendingWork()); // Retry backoff must not strand accepted intent.
  now_ms+=10;
  assert(m.hasPendingWork());
  m.radio_apply_retry_at=0;
#if COMPANION_FEATURE_TEMP_RADIO
  // An already active lease can last hours; a rejected power-save rollback
  // must not keep spinning solely because saved radio restoration is pending.
  m._temp_radio_applied=true;
  m._temp_radio_revert_at=now_ms+3600000;
  assert(!m.hasPendingWork());
  // A future scheduled TempRadio entry also blocks normal-radio restoration.
  m._temp_radio_applied=false;m._temp_radio_revert_at=0;
  m._temp_radio_set_at=now_ms+20;
  assert(!m.hasPendingWork());
  now_ms=m._temp_radio_set_at;
  assert(m.hasPendingWork()); // the entry timer, not the blocked restore
  m._temp_radio_set_at=0;m._temp_radio_applied=true;
  m._temp_radio_revert_at=now_ms+20;
  assert(!m.hasPendingWork());
  now_ms=m._temp_radio_revert_at;
  assert(m.hasPendingWork()); // lease expiry still gets serviced
  m._temp_radio_applied=false;m._temp_radio_revert_at=0;
  assert(m.hasPendingWork()); // rollback is runnable after the lease clears
#endif
  m.saved_radio_apply_pending=false;
  assert(!m.hasPendingWork());
  // Terminal TRACE has no binary slot but must keep event-driven nodes awake
  // until its independent timeout releases the reserved history entry.
  m._terminal_trace_pending=true;
  assert(m.hasPendingWork()==bool(COMPANION_FEATURE_TEXT_TERMINAL));
  m._terminal_trace_pending=false;
  assert(!m.hasPendingWork());
}
'''


class CompanionRadioSettingsTransactionTests(unittest.TestCase):
    def test_lease_blocked_recovery_does_not_prevent_sleep(self):
        text = SOURCE.read_text(encoding='utf-8')
        pending = '\n'.join(extract_braced(text, signature) for signature in (
            'bool MyMesh::hasPendingWork() const',
            'mesh::powerdiag::Mask MyMesh::pendingWorkReason() const'))
        harness = SLEEP_HARNESS.replace('@PENDING_WORK@', pending)
        header = (SOURCE.parent / 'DataStore.h').read_text(encoding='utf-8')
        start = header.index('  enum : uint8_t { ContactRecoveryPending')
        field = '  uint8_t _contact_recovery = 0;'
        end = header.index(field, start) + len(field)
        recovery = header[start:end] + '\n' + extract_braced(
            header, '  bool contactRecoveryPending() const')
        harness = harness.replace('@CONTACT_RECOVERY_API@', recovery)
        with tempfile.TemporaryDirectory(prefix='mesh-companion-radio-sleep-') as tmp:
            cpp = Path(tmp) / 'test.cpp'
            binary = Path(tmp) / ('test.exe' if os.name == 'nt' else 'test')
            cpp.write_text(harness, encoding='utf-8')
            for macros in (['-DCOMPANION_FEATURE_TEMP_RADIO=0'],
                           ['-DCOMPANION_FEATURE_TEMP_RADIO=1'],
                           ['-DCOMPANION_FEATURE_TEMP_RADIO=1', '-DNRF52_PLATFORM=1']):
                for terminal in (0, 1):
                    with self.subTest(macros=macros, terminal=terminal):
                        subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                                        '-Wall', '-Wextra', '-Werror', *macros,
                                        '-DCOMPANION_FEATURE_TEXT_TERMINAL=' + str(terminal),
                                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                                        '-fno-pie', '-no-pie',
                                        '-isystem', str(ROOT / "test/mocks"), f'-I{ROOT / "src"}',
                                        str(cpp), str(ROOT / 'src/helpers/CompanionDelayedReplies.cpp'),
                                        '-o', str(binary)], check=True)
                        subprocess.run([str(binary)], check=True)

    def test_production_commands_commit_and_restore(self):
        text = SOURCE.read_text(encoding='utf-8')
        header = (SOURCE.parent / 'MyMesh.h').read_text(encoding='utf-8')
        codes = '\n'.join(re.findall(
            r'^#define (?:CMD_SET_RADIO_PARAMS|CMD_SET_RADIO_TX_POWER|ERR_CODE_\w+)\s+\d+',
            text, flags=re.MULTILINE))
        methods = '\n'.join(extract_braced(text, signature) for signature in (
            'const char* MyMesh::radioSettingReply(',
            'bool MyMesh::applySavedRadioParams(',
            'MyMesh::RadioSettingResult MyMesh::applyAndSaveTxPower(',
            'void MyMesh::servicePendingTxPower(',
            'void MyMesh::queueSavedRadioApply(',
            'bool MyMesh::applyRxPowerSavingRfRxDisabled(',
            'MyMesh::RadioSettingResult MyMesh::applyAndSaveRadioParams(',
            'void MyMesh::servicePendingLocalRadioSettings(',
            'void MyMesh::onSendComplete(',
            'void MyMesh::onSendFail(',
            'void MyMesh::onRadioProfileCopyQueued(',
            'bool MyMesh::allowDirectRetry(',
            'void MyMesh::finishPrimaryRadioReply(',
            'void MyMesh::onCLICommandRecv(',
            'void MyMesh::onCLIReplyPrepared(',
            'void MyMesh::onCLIReplyQueued(',
            'bool MyMesh::scheduleTempRadio(',
            'void MyMesh::scheduleNormalRadio(',
            'void MyMesh::serviceTempRadio(',
        ))
        frame_branches = ' else '.join(extract_braced(text, f'if (cmd_frame[0] == {command})')
                                      for command in ('CMD_SET_RADIO_PARAMS', 'CMD_SET_RADIO_TX_POWER'))
        replacements = {
            '@CODES@': codes,
            '@RESULT_ENUM@': extract_braced(header, 'enum class RadioSettingResult'),
            '@RETRY_DELAY@': extract_braced(text, 'static uint32_t nextRadioApplyRetryDelay('),
            '@METHODS@': methods,
            '@FRAME_BRANCHES@': frame_branches,
            '@TX_STATUS_BRANCH@': extract_braced(text, 'if (strcmp(command, "get tx.status") == 0)'),
            '@CLI_BRANCH@': extract_braced(text, 'if (strncmp(command, "set tx ", 7) == 0)'),
            '@RADIO_CLI_BRANCH@': extract_braced(extract_braced(text, 'bool MyMesh::handleCommand('), 'if (!strncmp(command, "set radio ", 10))'),
            '@FREQ_CLI_BRANCH@': extract_braced(text, 'if (strncmp(command, "set freq ", 9) == 0)'),
        }
        harness = HARNESS
        for marker, value in replacements.items():
            harness = harness.replace(marker, value)
        with tempfile.TemporaryDirectory(prefix='mesh-companion-radio-settings-') as tmp:
            cpp = Path(tmp) / 'test.cpp'
            binary = Path(tmp) / ('test.exe' if os.name == 'nt' else 'test')
            cpp.write_text(harness, encoding='utf-8')
            for hil in (False, True):
                with self.subTest(hil=hil):
                    subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                                    '-Wno-sign-compare',  # existing frame parser compares size_t and byte offsets
                                    *(['-DMESH_HIL_TX_FREQ_MHZ=907.125f'] if hil else []),
                                    '-I', str(ROOT / 'src'), str(cpp), '-o', str(binary)], check=True)
                    subprocess.run([str(binary)], check=True)

    def test_all_tx_power_frontends_use_checked_handler(self):
        text = SOURCE.read_text(encoding='utf-8')
        terminal = extract_braced(text, 'if (strncmp(config, "tx ", 3) == 0)')
        self.assertIn('handleCommand(command, 0, local_reply)', terminal)
        web = extract_braced(text, 'if (strcmp(key, "tx") == 0)')
        self.assertIn('applyAndSaveTxPower(', web)
        self.assertIn('servicePendingLocalRadioSettings();', extract_braced(text, 'void MyMesh::loop()'))
        self.assertIn('RadioSettingResult::SaveFailed',
                      extract_braced(text, 'if (strncmp(command, "set tx ", 7) == 0)'))


if __name__ == '__main__':
    unittest.main()
