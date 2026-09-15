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
#include <helpers/CarrierWaveCLI.h>
#include <cassert>
#include <cstdio>
#include <initializer_list>
#define STATE_IDLE 0
#define STATE_RX 1
#define STATE_TX_WAIT 3
#define STATE_INT_READY 16
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_INVALID_OUTPUT_POWER -1
#define SX127X_CURRENT_LIMIT 120
#define LORA_TX_POWER 17
#define MESH_DEBUG_PRINTLN(...) ((void)0)
static uint8_t state;
static void setFlag() { state |= STATE_INT_READY; }
struct Board {
  bool awake=false; unsigned before=0, after=0;
  void setRadioTestActive(bool value) { awake=value; }
  void onBeforeTransmit() { ++before; }
  void onAfterTransmit() { ++after; }
};
struct Chip {
  int start_result=0, stop_result=0, fsk_result=0, power_result=0, limit_result=0;
  int power=17, initial_fsk_power=0, current_limit=60;
  unsigned starts=0, stops=0, standby_calls=0, fsk_starts=0;
  float frequency=0, deviation=5;
  bool carrier=false, fsk=false, ook=true, rfo=false;
  void (*callback)()=setFlag;
  int standby() { ++standby_calls; carrier=false; return 0; }
  int beginFSK(float freq, float, float dev, float, int8_t dbm, uint16_t, bool enable_ook) {
    ++fsk_starts; fsk=true; carrier=false; frequency=freq; deviation=dev;
    initial_fsk_power=power=dbm; ook=enable_ook; current_limit=60;
    return fsk_result;
  }
  int setCurrentLimit(int value) { current_limit=value; return limit_result; }
  int setOutputPower(int8_t value, bool force_rfo=false) {
    standby(); if (power_result) return power_result;
    power=value; rfo=force_rfo; return 0;
  }
  int clearIrqFlags(uint32_t) { return 0; }
  void clearPacketReceivedAction() { callback=nullptr; }
  void setPacketReceivedAction(void (*fn)()) { callback=fn; }
  int transmitDirect() { carrier=true; ++starts; return start_result; }
  int finishTransmit() { ++stops; if (!stop_result) carrier=false; return stop_result; }
};
using CustomSX1276=Chip;
struct RadioLibWrapper : mesh::Radio {
  Chip chip; Chip* _radio=&chip;
  Board board; Board* _board=&board;
  mesh::RadioProfiles _profiles;
  bool _cw_active=false, _cw_stopping=false, _cw_board_tx=false;
  uint8_t _cw_profile=0, _cw_restore_profile=0, _active_profile=0;
  uint32_t _cw_until=0, _cw_retry_at=0, _cw_generation=0;
  bool _params_valid=true, _rx_ps_armed=true, _rx_hold_continuous=false;
  bool _nf_calib_active=true, _profile_refresh_required=false;
  unsigned long _wd_observe_until=0;
  bool packet=false, busy=false, reset_ok=false, supported=true, deep_supported=true;
  unsigned rtc_stops=0, tunes=0, rx_starts=0, calibrations=0, resets=0;
  unsigned fail_rx_starts=0;
  bool fail_tune=false;
  float frequency=909.5;
  float _cur_freq=909.5;
  int8_t _cur_dbm=17;
  bool _dbm_valid=true;
  RadioLibWrapper() {
    g_mock_millis=100; state=STATE_RX;
    _profiles.primary.freq=909.5;
    _profiles.secondary.params.freq=911.5;
    _profiles.secondary.mode=mesh::RadioProfileMode::RxTx;
  }
  mesh::RadioProfiles* profiles() override { return &_profiles; }
  bool supportsCarrierWave() const override { return supported; }
  bool isCarrierWaveActive() const override { return _cw_active; }
  uint8_t carrierWaveProfile() const override { return _cw_profile; }
  uint32_t carrierWaveRemainingMillis() const override;
  mesh::RadioParamApplyResult setCarrierWave(uint8_t,uint32_t) override;
  bool stopCarrierWave();
  bool serviceCarrierWave();
  bool setTxPower(int8_t dbm);
  @NATIVE_HOOKS@
  virtual bool supportsRadioDeepInit() const { return deep_supported; }
  uint8_t beginReconfigure();
  void endReconfigure(bool resume) { if (resume) startRecv(); }
  bool isChipBusy() { return busy; }
  bool isReceivingPacket() { return packet; }
  bool isInRecvMode() const override { return (state & ~STATE_INT_READY)==STATE_RX; }
  void stopReceiveDutyCycle() { ++rtc_stops; _rx_ps_armed=false; }
  mesh::RadioParamApplyResult tuneProfile(uint8_t target) {
    ++tunes;
    if (fail_tune) return mesh::RadioParamApplyResult::FAILED;
    _active_profile=target; frequency=_cur_freq=_profiles.params(target).freq;
    return mesh::RadioParamApplyResult::APPLIED;
  }
  void startRecv() override {
    ++rx_starts;
    if (fail_rx_starts) { --fail_rx_starts; return; }
    state=STATE_RX; _rx_ps_armed=true;
  }
  bool restoreAfterDeepInit() {
    ++resets;
    if (reset_ok) {
      chip.carrier=false; chip.fsk=false; chip.stop_result=0;
      return applyCachedTxPower(_cur_dbm)==0;
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
static void advance(uint32_t ms) { g_mock_millis+=ms; }
int main() {
  using Result=mesh::RadioParamApplyResult;
  for (uint8_t profile : {0,1}) {
    RadioLibWrapper w;
    assert(w.setCarrierWave(profile,1250)==Result::APPLIED);
    assert(w.frequency==(profile ? 911.5f : 909.5f));
    assert(w.chip.carrier && !w.chip.callback && w.board.before==1 && w.board.awake);
    assert(w.rtc_stops==1 && !w._rx_ps_armed && !w._nf_calib_active);
    assert(w.beginReconfigure()==2);
    advance(1249); assert(w.serviceCarrierWave() && w.chip.carrier);
    advance(1); assert(w.serviceCarrierWave() && !w.chip.carrier);
    assert(!w._cw_active && w.isInRecvMode() && w._rx_ps_armed);
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
    if(reason==0) state=STATE_TX_WAIT;
    if(reason==1) state|=STATE_INT_READY;
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
    assert(w.setCarrierWave(0,0)==Result::FAILED);
    assert(w._cw_active && w._cw_stopping && w.board.awake && w.board.after==1);
    const auto attempts=w.chip.stops;
    advance(99); w.serviceCarrierWave(); assert(w.chip.stops==attempts);
    w.reset_ok=true;
    advance(1); w.serviceCarrierWave();
    assert(!w._cw_active && !w.chip.carrier && w.board.after==1 && w.isInRecvMode());
  }
  {
    RadioLibWrapper w; w.setCarrierWave(0,100);
    w.fail_rx_starts=2;
    assert(w.setCarrierWave(0,0)==Result::FAILED); // never claim RX resumed
    assert(!w._cw_active && !w.isInRecvMode());
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


class CarrierWaveTest(unittest.TestCase):
    def test_production_transitions_and_commands(self):
        source = (ROOT/'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
        signatures = ('uint8_t RadioLibWrapper::beginReconfigure(',
                      'bool RadioLibWrapper::setTxPower(',
                      'mesh::RadioParamApplyResult RadioLibWrapper::setCarrierWave(',
                      'uint32_t RadioLibWrapper::carrierWaveRemainingMillis(',
                      'bool RadioLibWrapper::stopCarrierWave(',
                      'bool RadioLibWrapper::serviceCarrierWave(')
        code = HARNESS.replace('@METHODS@', '\n'.join(method(source, s) for s in signatures))
        header = (ROOT/'src/helpers/radiolib/RadioLibWrappers.h').read_text()
        code = code.replace('@NATIVE_HOOKS@', '\n'.join(method(header, s) for s in (
            'virtual int16_t enterCarrierWave(', 'virtual bool restoreCarrierWaveModem(',
            'virtual int16_t applyCachedTxPower(')))
        sx1276 = (ROOT/'src/helpers/radiolib/CustomSX1276Wrapper.h').read_text()
        code = code.replace('@SX1276_METHODS@', '\n'.join(method(sx1276, s) for s in (
            'bool supportsCarrierWave(', 'int16_t enterCarrierWave(',
            'bool restoreCarrierWaveModem(')))
        dac = (ROOT/'src/helpers/radiolib/DacPaSX1276Wrapper.h').read_text()
        code = code.replace('@DAC_METHODS@', '\n'.join(method(dac, s) for s in (
            'int16_t applyCachedTxPower(', 'uint8_t indexForDbm(')))
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
