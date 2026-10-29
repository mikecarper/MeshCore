"""Compile production CW transitions and command parser with fault-injected hardware."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <Arduino.h>
#include <helpers/CarrierTools.h>
#include <helpers/RadioDirectionPolicy.h>
#include <helpers/CarrierWaveCLI.h>
#include <cassert>
#include <helpers/radiolib/RadioInterruptEvents.h>
#include <cstdio>
#include <initializer_list>
#include <new>
#include <helpers/MorseRx.h>
#define STATE_IDLE 0
#define STATE_RX 1
#define STATE_TX_WAIT 3
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_INVALID_OUTPUT_POWER -1
#define RADIOLIB_ERR_INVALID_FREQUENCY -2
#define RADIOLIB_ERR_UNKNOWN -16
#define SX127X_CURRENT_LIMIT 120
#define LORA_TX_POWER 17
#define MESH_DEBUG_PRINTLN(...) ((void)0)
static void setFlag() {}
struct Board {
  bool awake=false; unsigned before=0, after=0;
  bool frequency_ok=true; unsigned frequency_prepares=0;
  bool prepareRadioFrequency(float) { ++frequency_prepares; return frequency_ok; }
  void setRadioTestActive(bool value) { awake=value; }
  void onBeforeTransmit() { ++before; }
  void onAfterTransmit() { ++after; }
};
struct Chip {
  int start_result=0, stop_result=0, fsk_result=0, power_result=0, limit_result=0;
  int standby_result=0, frequency_result=0, irq_result=0, ramp_result=0, rx_result=0;
  int warm_standby_result=0; unsigned warm_standby_calls=0;
  uint8_t last_standby_mode=0; bool standbyXOSC=false;
  uint32_t tx_latency=0, standby_latency=0;
  int power=17, initial_fsk_power=0, current_limit=60;
  int ramp_time=4;
  unsigned ramp_calls=0, ramp_power_calls=0;
  unsigned starts=0, stops=0, standby_calls=0, fsk_starts=0, standby_fail_call=0;
  unsigned raw_rx_starts=0;
  unsigned admission_order=0,last_receive_order=0;
  unsigned morse_ramp_calls=0;
  float frequency=0, deviation=5;
  bool carrier=false, fsk=false, ook=true, rfo=false;
  int startReceive() { last_receive_order=++admission_order; ++raw_rx_starts; return rx_result; }
  int startReceive(uint32_t timeout,uint32_t flags,uint32_t mask,size_t length) {
    assert(timeout==0xffffffU && !flags && !mask && !length); return startReceive();
  }
  int receiveDirect() { return startReceive(); }
  void (*callback)()=setFlag;
  int standby() {
    ++standby_calls; g_mock_millis+=standby_latency;
    const int result=standby_fail_call==standby_calls ? -705 : standby_result;
    if (!result) carrier=false;
    return result;
  }
  int standby(uint8_t mode) {
    ++warm_standby_calls; last_standby_mode=mode;
    return warm_standby_result ? warm_standby_result : standby();
  }
  int setFrequency(float value) { frequency=value; return frequency_result; }
  int beginFSK(float freq, float, float dev, float, int8_t dbm, uint16_t, bool enable_ook) {
    ++fsk_starts; fsk=true; carrier=false; frequency=freq; deviation=dev;
    initial_fsk_power=power=dbm; ook=enable_ook; current_limit=60;
    return fsk_result;
  }
  int setCurrentLimit(int value) { current_limit=value; return limit_result; }
  int setMorsePaRamp() { ++morse_ramp_calls; return ramp_result; }
  int setPaRampTime(uint8_t ramp) {
    ++ramp_calls;
    if (!ramp_result) ramp_time=ramp;
    return ramp_result;
  }
  int setTxParams(int8_t value, uint8_t ramp) {
    const int result=setOutputPower(value);
    if (!result) ramp_time=ramp;
    return result;
  }
  int setOutputPower(int8_t value, bool force_rfo=false) {
    standby(); if (power_result) return power_result;
    power=value; rfo=force_rfo; ramp_time=4; return 0;
  }
  int checkOutputPower(int8_t value, int8_t* clipped, bool force_hp=false) {
    const int minimum=frequency>1000 ? -18 : force_hp || value>14 ? -9 : -17;
    const int maximum=frequency>1000 ? 13 : force_hp || value>14 ? 22 : 14;
    if (clipped) *clipped=value<minimum ? minimum : value>maximum ? maximum : value;
    return value<minimum || value>maximum ? -1 : 0;
  }
  int setOutputPower(int8_t value, bool force_rfo, uint16_t ramp) {
    ++ramp_power_calls;
    const int result=setOutputPower(value,force_rfo);
    if (!result) ramp_time=ramp;
    return result;
  }
  int setOutputPower(int8_t value, int ramp) {
    ++ramp_power_calls;
    const int result=setOutputPower(value,false);
    if (!result) ramp_time=ramp;
    return result;
  }
  int clearIrqFlags(uint32_t) { return irq_result; }
  void clearPacketReceivedAction() { callback=nullptr; }
  void setPacketReceivedAction(void (*fn)()) { callback=fn; }
  int transmitDirect() { g_mock_millis+=tx_latency; carrier=true; ++starts; return start_result; }
  int finishTransmit() { ++stops; if (!stop_result) carrier=false; return stop_result; }
};
using CustomSX1276=Chip;
struct RadioLibWrapper : mesh::Radio {
  uint8_t state = STATE_RX;
  RadioInterruptEvents events;
  bool hasPendingRadioInterrupt() const { return events.pending(); }
  bool claimRadioInterrupt() { return events.claim(); }
  unsigned register_failures=0;
  bool registerInterruptAction() {
    if (register_failures) { --register_failures; return false; }
    chip.setPacketReceivedAction(setFlag); events.claim(); return true;
  }
  Chip chip; Chip* _radio=&chip;
  Board board; Board* _board=&board;
  mesh::RadioProfiles _profiles;
  bool _cw_active=false, _cw_stopping=false, _cw_board_tx=false;
  uint8_t _cw_profile=0, _cw_restore_profile=0, _active_profile=0;
  uint32_t _cw_until=0, _cw_retry_at=0, _cw_generation=0;
  mesh::MorseTx _morse;
  bool _morse_owned=false;
  float _morse_frequency=mesh::MorseTx::DefaultFrequencyMHz;
  uint8_t _morse_wpm=mesh::MorseTx::DefaultWpm;
  uint32_t _morse_generation[2]={};
  @MORSE_RX_FIELDS@
  bool receiver_allocation_ok=true, fail_morse_params=false;
  float rssi=-110, morse_bandwidth=0;
  uint32_t rssi_latency=0;
  unsigned rssi_reads=0;
  bool _params_valid=true, _rx_ps_armed=true, _rx_hold_continuous=false;
  bool _primary_receive_preempt=false;
  bool _rx_boosted_gain_valid=false, _cur_rx_boosted_gain=false, _rx_boosted_gain_applied=false;
  bool rx_gain_apply_ok=true, physical_rx_gain=false;
  unsigned rx_gain_applies=0,rx_gain_order=0;
  virtual bool applyRxBoostedGainMode(bool enabled) {
    rx_gain_order=++chip.admission_order;
    ++rx_gain_applies;
    if (rx_gain_apply_ok) physical_rx_gain=enabled;
    return rx_gain_apply_ok;
  }
  bool _nf_calib_active=true, _profile_refresh_required=false, _profile_repair_required=false;
  unsigned long _wd_observe_until=0;
  bool packet=false, busy=false, reset_ok=false, supported=true, deep_supported=true;
  unsigned rtc_stops=0, tunes=0, rx_starts=0, calibrations=0, resets=0;
  unsigned fail_rx_starts=0;
  bool fail_tune=false;
  float frequency=909.5;
  float _cur_freq=909.5;
  int8_t _cur_dbm=17;
  bool _dbm_valid=true, _dbm_applied=true;
  RadioLibWrapper() {
    g_mock_millis=100; state=STATE_RX;
    _profiles.primary.freq=909.5;
    _profiles.primary.bw=500;
    _profiles.secondary.params.freq=911.5;
    _profiles.secondary.params.bw=500;
    _profiles.secondary.mode=mesh::RadioProfileMode::RxTx;
  }
  @MORSE_DESTRUCTOR@
  mesh::RadioProfiles* profiles() override { return &_profiles; }
  bool supportsCarrierWave() const override { return supported; }
  bool isCarrierWaveActive() const override { return _cw_active; }
  uint8_t carrierWaveProfile() const override { return _cw_profile; }
  uint32_t carrierWaveRemainingMillis() const override;
  mesh::RadioParamApplyResult setCarrierWave(uint8_t,uint32_t) override;
  bool stopCarrierWave();
  bool serviceCarrierWave();
  bool serviceMorseTx();
  @RX_SERVICE_DECLARATION@
  bool supportsMorseTx() const override;
  mesh::MorseTxResult startMorseTx(float,uint8_t,const char*) override;
  mesh::MorseTxResult stopMorseTx() override;
  @MORSE_STATUS@
  @MORSE_RX_STATUS@
#if MESH_ENABLE_MORSE_RX
  virtual bool canAllocateMorseReceiver() const { return receiver_allocation_ok; }
#endif
  virtual bool applyParams(float freq,float bw,uint8_t,uint8_t) {
    morse_bandwidth=bw;
    return !fail_morse_params && chip.setFrequency(freq)==0;
  }
  virtual float getCurrentRSSI() { ++rssi_reads; g_mock_millis+=rssi_latency; return rssi; }
  bool setTxPower(int8_t dbm);
  bool isTxPowerValid(int8_t dbm) const;
  bool isTxPowerApplied(int8_t dbm) const;
  virtual bool checkBeaconOutputPower(float, int8_t) const;
  mesh::RadioParamApplyResult trySetTxPower(int8_t dbm);
  @NATIVE_HOOKS@
#if MESH_ENABLE_MORSE_RX || !defined(STM32_PLATFORM)
  @RX_HOOKS@
  @RX_START_HOOKS@
  // Explicit stand-in for a proven native-RSSI modem. Concrete production
  // FSK preparation is executed separately; the abstract base rejects RX.
  virtual float morseReceiverBandwidthKHz() const { return 4.8f; }
  virtual int16_t prepareMorseReceiver(float freq) {
    if (!board.prepareRadioFrequency(freq)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    if (!applyParams(freq,morseReceiverBandwidthKHz(),7,5)) return RADIOLIB_ERR_UNKNOWN;
    return RADIOLIB_ERR_NONE;
  }
#endif
  virtual bool supportsRadioDeepInit() const { return deep_supported; }
  uint8_t beginReconfigure();
  void noteReconfigureAdmission(const char*, int16_t) {}
  void endReconfigure(bool resume) { if (resume) startRecv(); }
  bool isChipBusy() { return busy; }
  bool isReceivingPacket() { return packet; }
  bool isInRecvMode() const override { return state==STATE_RX; }
  bool validateProfile(const mesh::RadioProfileParams& p) const override {
    return std::isfinite(p.freq) && p.freq >= 100 && p.freq <= 1000;
  }
  void stopReceiveDutyCycle() { ++rtc_stops; _rx_ps_armed=false; }
  mesh::RadioParamApplyResult tuneProfile(uint8_t target) {
    ++tunes;
    if (fail_tune || chip.frequency_result) return mesh::RadioParamApplyResult::FAILED;
    _active_profile=target; frequency=_cur_freq=_profiles.params(target).freq;
    chip.frequency=frequency;
    return mesh::RadioParamApplyResult::APPLIED;
  }
  void startRecv() override {
    ++rx_starts;
    if (fail_rx_starts) { --fail_rx_starts; return; }
    state=STATE_RX; _rx_ps_armed=true;
  }
  bool restoreAfterDeepInit() {
    ++resets;
    _dbm_applied=false;
    if (reset_ok) {
      chip.carrier=false; chip.fsk=false; chip.stop_result=chip.standby_result=0;
      _dbm_applied=applyCachedTxPower(_cur_dbm)==0;
      return _dbm_applied;
    }
    return reset_ok;
  }
  void recalibrateNoiseFloor() override { ++calibrations; }
  int recvRaw(uint8_t*,int) override { return 0; }
  uint32_t getEstAirtimeFor(int) override { return 1; }
  float packetScore(float,int) override { return 0; }
  bool startSendRaw(const uint8_t*,int) override { return false; }
  bool isSendComplete() override { return false; }
  void onSendFinished() override {}
};
@METHODS@
#if MESH_ENABLE_MORSE_RX || !defined(STM32_PLATFORM)
// SX1276/DAC declarations are exercised on their actual non-STM targets;
// compact integrated-STM builds do not compile these other native chips.
struct CustomSX1276Wrapper : RadioLibWrapper {
  CustomSX1276Wrapper() { reset_ok=true; }
  @SX1276_METHODS@
};
struct DacPaLevel { int8_t dbm; uint8_t dac; };
struct DacCarrier : CustomSX1276Wrapper {
  int8_t _min_dbm=17, _max_dbm=30, _drive_dbm=2;
  DacPaLevel _levels[3]={{17,0},{24,50},{30,130}};
  uint8_t _num_levels=3;
  const int8_t* _radio_dbm=nullptr;
  bool _force_rfo=false;
  int gain=-1;
  void writeGainControl(uint8_t value) { gain=value; }
  @DAC_METHODS@
};
#endif
static void advance(uint32_t ms) { g_mock_millis+=ms; }
int main() {
  using Result=mesh::RadioParamApplyResult;
  for (uint8_t profile : {0,1}) {
    RadioLibWrapper w;
    assert(w.isTxPowerApplied(17));
    assert(w.setCarrierWave(profile,1250)==Result::APPLIED);
    assert(!w.isTxPowerApplied(17));
    assert(w.frequency==(profile ? 911.5f : 909.5f));
    assert(w.chip.carrier && !w.chip.callback && w.board.before==1 && w.board.awake);
    assert(w.chip.standby_calls==2 && !w._rx_ps_armed && !w._nf_calib_active);
    assert(w.beginReconfigure()==2);
    advance(1249); assert(w.serviceCarrierWave() && w.chip.carrier);
    advance(1); assert(w.serviceCarrierWave() && !w.chip.carrier);
    assert(!w._cw_active && w.isInRecvMode() && w._rx_ps_armed);
    assert(w.isTxPowerApplied(17));
    assert(w.frequency==909.5f && w.board.after==1 && !w.board.awake && w.chip.callback);
    assert(!w.serviceCarrierWave());
  }
  {
    RadioLibWrapper w; w.setCarrierWave(0,1250); advance(250);
    assert(w.setTxPower(10) && w.chip.carrier && w.chip.power==10);
    assert(w.carrierWaveRemainingMillis()==1000);
    assert(w.setCarrierWave(0,0)==Result::APPLIED);
  }
  // No mutation of packets already transmitting, completed RX, busy or receiving hardware.
  for (int reason : {0,1,2,3}) {
    RadioLibWrapper w;
    if(reason==0) w.state=STATE_TX_WAIT;
    if(reason==1) w.events.record();
    if(reason==2) w.busy=true;
    if(reason==3) w.packet=true;
    assert(w.setCarrierWave(1,100)==Result::BUSY);
    assert(!w.chip.starts && !w.chip.standby_calls && !w.board.before && !w.tunes);
  }
  {
    RadioLibWrapper w;
    g_mock_millis=0xfffffff0;
    assert(w.setCarrierWave(0,25)==Result::APPLIED);
    advance(20); assert(w.carrierWaveRemainingMillis()==5);
    assert(w.setCarrierWave(1,50)==Result::BUSY); // never switch an active carrier
    assert(w.setCarrierWave(0,50)==Result::APPLIED && w.chip.starts==1);
    advance(49); w.serviceCarrierWave(); assert(w.chip.carrier);
    advance(1); w.serviceCarrierWave(); assert(!w.chip.carrier);
  }
  {
    RadioLibWrapper w; w.chip.start_result=-705;
    assert(w.setCarrierWave(0,100)==Result::FAILED);
    assert(!w.chip.carrier && w.board.before==1 && w.board.after==1 && w.isInRecvMode());
  }
  {
    RadioLibWrapper w;
    assert(w.setCarrierWave(1,100)==Result::APPLIED);
    w.chip.stop_result=-705;
    w.chip.standby_result=-705;
    assert(w.setCarrierWave(0,0)==Result::FAILED);
    assert(w._cw_active && w._cw_stopping && w.board.awake && w.board.after==1);
    assert(!w._dbm_applied && !w.isTxPowerApplied(17));
    const auto attempts=w.chip.stops;
    advance(99); w.serviceCarrierWave(); assert(w.chip.stops==attempts);
    w.reset_ok=true;
    advance(1); w.serviceCarrierWave();
    assert(!w._cw_active && !w.chip.carrier && w.board.after==1 && w.isInRecvMode());
    assert(w.isTxPowerApplied(17));
  }
  {
    RadioLibWrapper w; w.setCarrierWave(0,100);
    w.fail_rx_starts=2;
    assert(w.setCarrierWave(0,0)==Result::FAILED); // never claim RX resumed
    assert(w._cw_active && w._cw_stopping && w.board.awake && !w.isInRecvMode());
    advance(100); w.serviceCarrierWave();
    assert(w._cw_active && w._cw_stopping && w.board.awake && !w.isInRecvMode());
    advance(100); w.serviceCarrierWave();
    assert(!w._cw_active && !w.board.awake && w.isInRecvMode());
  }
  {
    RadioLibWrapper w; w.setCarrierWave(0,100);
    w.fail_rx_starts=1; w.reset_ok=true;
    assert(w.setCarrierWave(0,0)==Result::APPLIED && w.isInRecvMode());
  }
  {
    RadioLibWrapper w; w.setCarrierWave(1,1000);
    w._profiles.setSecondary({},false); // expired temporary radio2
    w.serviceCarrierWave(); assert(!w._cw_active && w.frequency==909.5f);
  }
  {
    RadioLibWrapper w; w.setCarrierWave(1,1000);
    ++w._profiles.generation[1]; // scheduled profile replacement ends old carrier
    w.serviceCarrierWave(); assert(!w._cw_active);
  }
  {
    RadioLibWrapper w; char reply[160];
    const auto run=[&](const char* command) {
      assert(mesh::handleCarrierWaveCommand(&w,command,reply,sizeof(reply)));
      return reply;
    };
    assert(strstr(run("cw on"),"10.000 seconds") && w.carrierWaveRemainingMillis()==10000);
    assert(strstr(run("cw2 off"),"receive resumed")); // either name can stop the one transmitter
    assert(strstr(run("set cw2 on 2.5"),"2.500 seconds") && w.frequency==911.5f);
    assert(strstr(run("get cw2"),"cw2 on; 2.500 seconds left"));
    run("cw off");
    for (const char* invalid : {"cw on 0", "cw on -1", "cw on nan", "cw on inf",
        "cw on 60.1", "cw on 1e2", "cw on 2.5junk", "cw on 1 2", "cw off 2",
        "get cw on", "cw yes", "cw on .0001"}) {
      const unsigned starts=w.chip.starts;
      assert(!strncmp(run(invalid),"Error:",6) && w.chip.starts==starts);
    }
    assert(strstr(run("cw on .001"),"0.001 seconds")); run("cw off");
    assert(strstr(run("cw on 60"),"60.000 seconds")); run("cw off");
    w._profiles.secondary.mode=mesh::RadioProfileMode::Rx;
    assert(strstr(run("cw2 on 1"),"requires active radio2 rxtx"));
    w.supported=false; assert(strstr(run("cw on"),"unsupported"));
    assert(!mesh::handleCarrierWaveCommand(&w,"cw22 on",reply,sizeof(reply)));
    char small[8]; mesh::handleCarrierWaveCommand(&w,"cw on",small,sizeof(small));
    assert(small[7]==0);
  }
  // Execute SX1276's production FSK entry/LoRa restoration, including the
  // production external-PA power mapping. Native CW paths above stay covered.
  for (uint8_t profile : {0,1}) {
    CustomSX1276Wrapper w;
    w._cur_dbm=14;
    assert(w.supportsCarrierWave() && w.setCarrierWave(profile,1250)==Result::APPLIED);
    assert(w.chip.fsk && w.chip.deviation==0 && !w.chip.ook && w.chip.carrier);
    assert(w.chip.frequency==(profile ? 911.5f : 909.5f));
    assert(w.chip.initial_fsk_power==2 && w.chip.power==14 && w.chip.current_limit==120);
    advance(250);
    assert(w.setTxPower(10) && w.chip.carrier && w.chip.power==10);
    assert(w.chip.fsk_starts==1 && w.carrierWaveRemainingMillis()==1000);
    advance(1000); w.serviceCarrierWave();
    assert(!w._cw_active && !w.chip.fsk && w.isInRecvMode() && w.frequency==909.5f);
    assert(w.resets==1 && w.chip.power==10 && !w.board.awake && w.board.after==1);
  }
  {
    DacCarrier w; w._cur_dbm=30;
    assert(w.setCarrierWave(1,1000)==Result::APPLIED);
    assert(w.chip.initial_fsk_power==2 && w.chip.power==2 && w.gain==130 && w.chip.carrier);
    advance(200);
    assert(w.setTxPower(24) && w.chip.power==2 && w.gain==50 && w.chip.carrier);
    assert(w.carrierWaveRemainingMillis()==800 && w.chip.fsk_starts==1);
    assert(w.setCarrierWave(0,0)==Result::APPLIED);
    assert(w.chip.power==2 && w.gain==50 && !w.chip.fsk && w.resets==1);
  }
  {
    DacCarrier w; w._force_rfo=true; w._drive_dbm=-4;
    assert(w.setCarrierWave(0,100)==Result::APPLIED);
    assert(w.chip.rfo && w.chip.power==-4);
    assert(w.setCarrierWave(0,0)==Result::APPLIED && w.chip.rfo && w.chip.power==-4);
  }
  {
    CustomSX1276Wrapper w; w.deep_supported=false;
    assert(!w.supportsCarrierWave() && w.setCarrierWave(0,100)==Result::FAILED);
    assert(!w.chip.fsk_starts);
  }
  // Partial FSK entry and current-limit failures restore LoRa without keying.
  for (int reason : {0,1,2}) {
    CustomSX1276Wrapper w;
    if (reason==0) w.chip.fsk_result=-707;
    if (reason==1) w.chip.limit_result=-705;
    if (reason==2) w.chip.power_result=-705;
    assert(w.setCarrierWave(0,100)==Result::FAILED && !w.chip.starts && !w.chip.carrier);
    w.chip.power_result=0;
    if (w._cw_active) { advance(100); w.serviceCarrierWave(); }
    assert(!w._cw_active && !w.chip.fsk && w.isInRecvMode() && !w.board.awake);
  }
  {
    CustomSX1276Wrapper w; w.setCarrierWave(1,1000); w.reset_ok=false;
    assert(w.setCarrierWave(0,0)==Result::FAILED);
    assert(w._cw_active && w._cw_stopping && !w.chip.carrier && w.chip.fsk);
    assert(!w.setTxPower(10)); // recovery cannot accidentally re-key the PA
    w.reset_ok=true; advance(100); w.serviceCarrierWave();
    assert(!w._cw_active && !w.chip.fsk && w.isInRecvMode() && w.board.after==1);
  }
  for (int reason : {0,1}) {
    CustomSX1276Wrapper w; w.setCarrierWave(0,1000);
    if (reason==0) w.chip.power_result=-705;
    if (reason==1) w.chip.start_result=-705;
    assert(!w.setTxPower(10) && !w.chip.carrier);
    w.chip.power_result=0; advance(100); w.serviceCarrierWave();
    assert(!w._cw_active && !w.chip.fsk && w.isInRecvMode());
    assert(w._cur_dbm==17 && w.chip.power==17); // failed sweep retains old power
  }
  {
    RadioLibWrapper w; w.setCarrierWave(0,1000);
    w.chip.start_result=-705;
    assert(!w.setTxPower(10) && !w.chip.carrier && w.isInRecvMode());
    assert(w._cur_dbm==17 && w.chip.power==17);
  }
  puts("CW: native/SX1276 transitions, fractional CLI, PA sweeps, restoration and fault recovery passed");
}
'''


def production_harness():
    source = (ROOT/'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
    signatures = ('uint8_t RadioLibWrapper::beginReconfigure(',
                      'bool RadioLibWrapper::setTxPower(',
                      'bool RadioLibWrapper::isTxPowerValid(',
                      'bool RadioLibWrapper::isTxPowerApplied(',
                      'bool RadioLibWrapper::checkBeaconOutputPower(',
                      'mesh::RadioParamApplyResult RadioLibWrapper::trySetTxPower(',
                      'mesh::RadioParamApplyResult RadioLibWrapper::setCarrierWave(',
                      'uint32_t RadioLibWrapper::carrierWaveRemainingMillis(',
                      'bool RadioLibWrapper::stopCarrierWave(',
                      'bool RadioLibWrapper::serviceCarrierWave(',
                      'bool RadioLibWrapper::supportsMorseTx(',
                      'mesh::MorseTxResult RadioLibWrapper::startMorseTx(',
                      'mesh::MorseTxResult RadioLibWrapper::stopMorseTx(',
                      'bool RadioLibWrapper::serviceMorseTx(',
                      'mesh::MorseRxResult RadioLibWrapper::startMorseRx(',
                      'mesh::MorseRxResult RadioLibWrapper::stopMorseRx(',
                      'size_t RadioLibWrapper::readMorseCapture(',
                      'void RadioLibWrapper::clearMorseCapture(',
                      'bool RadioLibWrapper::serviceMorseRx(')
    def extract_transition(signature):
        if signature == 'bool RadioLibWrapper::stopCarrierWave(':
            # The production enabled/disabled RX branches share a closing
            # block; bound the complete actual method instead of counting
            # mutually exclusive preprocessor braces together.
            start = source.index(signature)
            end = source.index('bool RadioLibWrapper::serviceCarrierWave(',start)
            return source[start:end].strip()
        if signature in ('mesh::MorseRxResult RadioLibWrapper::startMorseRx(',
                          'mesh::MorseRxResult RadioLibWrapper::stopMorseRx(',
                          'size_t RadioLibWrapper::readMorseCapture(',
                          'void RadioLibWrapper::clearMorseCapture(',
                          'bool RadioLibWrapper::serviceMorseRx('):
            return '#if MESH_ENABLE_MORSE_RX\n'+method(source,signature)+'\n#endif'
        return method(source,signature)
    code = HARNESS.replace('@METHODS@', '\n'.join(extract_transition(s) for s in signatures))
    header = (ROOT/'src/helpers/radiolib/RadioLibWrappers.h').read_text()
    service_start = header.rfind('#if MESH_ENABLE_MORSE_RX\n',0,header.index('  bool serviceMorseRx();'))
    service_end = header.index('\n#endif',service_start) + len('\n#endif')
    code = code.replace('@RX_SERVICE_DECLARATION@',header[service_start:service_end])
    fields_start = header.rfind('#if MESH_ENABLE_MORSE_RX',0,header.index('mesh::MorseRx* _morse_rx'))
    fields_end = header.index('  // End Morse RX state.',fields_start)
    code = code.replace('@MORSE_RX_FIELDS@',header[fields_start:fields_end])
    code = code.replace('@MORSE_DESTRUCTOR@',method(header,'~RadioLibWrapper('))
    code = code.replace('@NATIVE_HOOKS@', '\n'.join(method(header, s) for s in (
            'virtual int16_t enterCarrierWave(', 'virtual bool restoreCarrierWaveModem(',
            'virtual int16_t applyCachedTxPower(', 'virtual int16_t prepareMorseCarrier(',
            'virtual int16_t enterMorseCarrier(', 'virtual int16_t leaveMorseCarrier(',
            'virtual bool validateMorseFrequency(')))
    code = code.replace('@RX_HOOKS@', method(header, 'virtual float readMorseRssi('))
    code = code.replace('@RX_START_HOOKS@',method(header,'virtual int16_t startMorseReceiver('))
    code = code.replace('@MORSE_STATUS@', '\n'.join(method(header, s) for s in (
            'bool isMorseTxActive(', 'float morseTxFrequency(', 'uint8_t morseTxWpm(',
            'uint32_t morseTxRemainingMillis(')))
    rx_status_start = header.rfind('#if MESH_ENABLE_MORSE_RX',0,header.index('bool isMorseRxActive('))
    rx_status_end = header.index('  mesh::RadioProfiles* profiles()',rx_status_start)
    rx_status = header[rx_status_start:rx_status_end].replace(
        'bool supportsMorseRx() const override { return false; }',
        'bool supportsMorseRx() const override { return supported && deep_supported; }')
    code = code.replace('@MORSE_RX_STATUS@',rx_status)
    sx1276 = (ROOT/'src/helpers/radiolib/CustomSX1276Wrapper.h').read_text()
    code = code.replace('@SX1276_METHODS@', '\n'.join(method(sx1276, s) for s in (
            'bool supportsCarrierWave(', 'int16_t enterCarrierWave(',
            'bool restoreCarrierWaveModem(', 'int16_t prepareMorseCarrier(',
            'bool validateMorseFrequency(')))
    dac = (ROOT/'src/helpers/radiolib/DacPaSX1276Wrapper.h').read_text()
    code = code.replace('@DAC_METHODS@', '\n'.join(method(dac, s) for s in (
            'int16_t applyCachedTxPower(', 'uint8_t indexForDbm(',
            'bool checkBeaconOutputPower(')))
    return code


class CarrierWaveTest(unittest.TestCase):
    def test_production_transitions_and_commands(self):
        code = production_harness()
        with tempfile.TemporaryDirectory() as folder:
            cpp = Path(folder)/'cw.cpp'; exe = Path(folder)/'cw.exe'
            cpp.write_text(code)
            built = subprocess.run([os.environ.get('CXX','g++'), '-std=c++17', '-Wall', '-Wextra',
                '-I', str(ROOT/'test/mocks'), '-I', str(ROOT/'src'), str(cpp), '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            print(result.stdout, end='')


if __name__ == '__main__':
    unittest.main()
