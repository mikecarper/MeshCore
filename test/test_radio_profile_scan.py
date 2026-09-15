"""Exercise production profile transitions with a controllable physical radio."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <cassert>
#include <initializer_list>
#include <RadioProfiles.h>
#include <helpers/radiolib/RXPowerSaving.h>
#define RADIOLIB_ERR_NONE 0
#define STATE_IDLE 0
#define STATE_RX 1
#define STATE_TX_WAIT 3
#define STATE_INT_READY 16
#define MESH_DEBUG_PRINTLN(...) ((void)0)
namespace mesh { enum class RadioParamApplyResult { APPLIED, BUSY, FAILED }; }
static uint64_t elapsed_us;
static uint8_t state;
uint32_t micros() { return (uint32_t)elapsed_us; }
uint32_t millis() { return (uint32_t)(elapsed_us / 1000); }
struct Chip { bool standbyXOSC=false; int standby() { return 0; } };
using CustomSX1262 = Chip;
struct RadioLibWrapper {
  bool _cw_active=false;
  Chip chip; Chip* _radio = &chip;
  mesh::RadioProfiles _profiles;
  bool _params_valid=true, packet=false, busy=false, fail=false;
  bool _rx_ps_enabled=true, _rx_ps_armed=true, _rx_ps_continuous_fallback=false;
  bool _profile_saved_rxps=false, _profile_rxps_suspended=false;
  bool _profile_standby_held=false, _saved_standby_xosc=false;
  bool _nf_calib_active=false, _noise_floor_valid=true, _profile_refresh_required=false;
  uint32_t _rx_ps_rx_us=50000, _rx_ps_sleep_us=50000;
  uint8_t _active_profile=0, _cur_sf=7, _cur_cr=5;
  uint32_t _profile_generation=1, _profile_visit_us=0, _profile_retry_at=0;
  uint32_t _profile_scan_generation[2]={};
  uint16_t _physical_preamble=32;
  float _cur_freq=909.5, _cur_bw=62.5;
  unsigned applies=0, failRxStarts=0;
  unsigned coding_writes=0;
  bool coding_success=true;
  bool setCodingRate(uint8_t) { ++coding_writes; return coding_success; }
  RadioLibWrapper() {
    state=STATE_RX; elapsed_us=100000;
    auto& p=_profiles.primary;
    p.freq=_cur_freq; p.bw=_cur_bw; p.sf=_cur_sf; p.cr=_cur_cr;
    _profile_visit_us=micros();
  }
  bool isChipBusy() { return busy; }
  bool isInRecvMode() const { return (state & ~STATE_INT_READY) == STATE_RX; }
  void beginProfileRetune(bool) {}
  void endProfileRetune(bool) {}
  bool isReceivingPacket() { return packet; }
  bool isPacketPendingOrReceiving() { return packet || (state & STATE_INT_READY); }
  bool supportsRxPowerSaving() { return true; }
  void startRecv() {
    if (failRxStarts) { --failRxStarts; state=STATE_IDLE; return; }
    if (_profile_rxps_suspended) assert(chip.standbyXOSC && !_rx_ps_enabled);
    state=STATE_RX; _profile_visit_us=micros(); _rx_ps_armed=_rx_ps_enabled;
  }
  void stopReceiveDutyCycle() { _rx_ps_armed=false; }
  bool applyParams(float f,float,uint8_t,uint8_t) { ++applies; elapsed_us+=1200; return !fail || f==909.5f; }
  void cacheParams(float f,float b,uint8_t s,uint8_t c) { _cur_freq=f;_cur_bw=b;_cur_sf=s;_cur_cr=c; }
  bool restoreAfterDeepInit() { return true; }
  void recalibrateNoiseFloor() { _noise_floor_valid=false; }
  bool validateProfile(const mesh::RadioProfileParams& p) const { return mesh::RadioProfiles::valid(p); }
  uint16_t profilePreamble(uint8_t n) const { return _profiles.preamble(n,32); }
  uint8_t beginReconfigure();
  void endReconfigure(bool);
  void setProfileStandbyWarm(bool);
  void serviceProfileScan();
  mesh::RadioParamApplyResult tuneProfile(uint8_t);
  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t);
  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t, bool);
  mesh::RadioParamApplyResult tryRestoreCodingRate(uint8_t);
  mesh::RadioParamApplyResult trySetParams(float,float,uint8_t,uint8_t,const uint32_t* = nullptr);
  mesh::RadioParamApplyResult trySetPrimaryParams(const mesh::RadioProfileParams&,bool,const uint32_t* = nullptr);
  void enable() {
    mesh::RadioProfileConfig second;
    second.params=_profiles.primary;second.params.freq=910.5;second.params.bw=500;
    second.mode=mesh::RadioProfileMode::RxTx;_profiles.setSecondary(second,false);
    serviceProfileScan();
  }
};
@METHODS@
int main() {
  using Result=mesh::RadioParamApplyResult;
  for (unsigned blocked=0; blocked<5; ++blocked) {
    RadioLibWrapper w;
    w._cw_active = blocked==0; w.busy = blocked==1; w.packet = blocked==2;
    if (blocked==3) state |= STATE_INT_READY;
    if (blocked==4) state = STATE_TX_WAIT;
    assert(w.tryRestoreCodingRate(5)==Result::BUSY && w.coding_writes==0);
  }
  for (bool success : {false,true}) {
    RadioLibWrapper w; w.coding_success=success;
    assert(w.tryRestoreCodingRate(5)==(success?Result::APPLIED:Result::FAILED));
    assert(w.coding_writes==1 && state==STATE_RX);
    assert(w._profile_refresh_required==!success);
  }
  {
    // Force only admits a reply on an active RX-only profile. It cannot
    // enable a disabled profile or erase an in-progress reception.
    RadioLibWrapper w; w.enable();
    w._profiles.secondary.mode=mesh::RadioProfileMode::Rx;
    assert(w.prepareTransmitProfile(1)==Result::FAILED);
    assert(w.prepareTransmitProfile(1,false)==Result::FAILED);
    w.packet=true;
    assert(w.prepareTransmitProfile(1,true)==Result::BUSY);
    w.packet=false; w.busy=true;
    assert(w.prepareTransmitProfile(1,true)==Result::BUSY);
    w.busy=false;
    assert(w.prepareTransmitProfile(1,true)==Result::APPLIED);
    assert(w._active_profile==1 && w._profiles.secondary.mode==mesh::RadioProfileMode::Rx);
    assert(w.prepareTransmitProfile(1)==Result::FAILED);
    w._profiles.setSecondary({},false);
    assert(w.prepareTransmitProfile(1,true)==Result::FAILED);
    assert(w.prepareTransmitProfile(2,true)==Result::FAILED);
  }
  {
    // Applying the tuple is not success if RX resume failed. Roll back the
    // profile and caches, and request recovery if rollback RX also fails.
    for (unsigned failures: {1u,2u}) {
      RadioLibWrapper w; w.enable(); w.failRxStarts=failures;
      const auto switches=w._profiles.switches;
      const auto generation=w._profile_generation;
      assert(w.tuneProfile(1)==Result::FAILED);
      assert(w._active_profile==0 && w._cur_freq==909.5f && w._cur_bw==62.5f);
      assert(w._profile_generation==generation && w._profiles.switches==switches);
      assert(w._profiles.switch_failures==1);
      assert(w.isInRecvMode()==(failures==1));
      assert(w._profile_refresh_required==(failures==2));
    }
  }
  {
    // A primary command must not publish a new profile if restarting RX
    // fails. Restore the active tuple (which may be radio2) and RXPS policy.
    for (unsigned initial=0; initial<4; ++initial) {
      const bool secondary=(initial & 1), powersaving=(initial & 2);
      for (bool primary_api: {false,true}) {
        for (unsigned failures: {1u,2u}) {
          RadioLibWrapper w;
          w._rx_ps_enabled=w._rx_ps_armed=powersaving;
          if (secondary) { w.enable(); assert(w.tuneProfile(1)==Result::APPLIED); }
          const auto primary=w._profiles.primary;
          const auto generation=w._profiles.generation[0];
          const auto active=w._active_profile;
          const auto physical_generation=w._profile_generation;
          const auto freq=w._cur_freq, bw=w._cur_bw;
          const auto preamble=w._physical_preamble;
          const auto rxps=w._rx_ps_enabled, saved_rxps=w._profile_saved_rxps;
          const auto rx_us=w._rx_ps_rx_us, sleep_us=w._rx_ps_sleep_us;
          const auto fallback=w._rx_ps_continuous_fallback;
          auto requested=primary; requested.freq=910.25; requested.preamble=64;
          const uint32_t timings[]={100000,200000};
          w.failRxStarts=failures;
          const auto result=primary_api ? w.trySetPrimaryParams(requested,true,timings)
              : w.trySetParams(requested.freq,requested.bw,requested.sf,requested.cr,timings);
          assert(result==Result::FAILED);
          assert(w._profiles.primary==primary && !w._profiles.primary_temporary);
          assert(w._profiles.generation[0]==generation && w._profile_generation==physical_generation);
          assert(w._active_profile==active && w._cur_freq==freq && w._cur_bw==bw);
          assert(w._physical_preamble==preamble);
          assert(w._rx_ps_enabled==rxps && w._profile_saved_rxps==saved_rxps);
          assert(w._rx_ps_rx_us==rx_us && w._rx_ps_sleep_us==sleep_us);
          assert(w._rx_ps_continuous_fallback==fallback);
          assert(w.isInRecvMode()==(failures==1));
          assert(w._profile_refresh_required==(failures==2));
          if (!w.isInRecvMode()) w.startRecv(); // normal receive recovery
          assert(w.trySetPrimaryParams(requested,true,timings)==Result::APPLIED);
          assert(w._profiles.primary==requested && w._profiles.primary_temporary);
          assert(w._profiles.generation[0]==generation+1);
          assert(w._profile_generation==generation+1 && w._active_profile==0);
          assert(w.isInRecvMode() && !w._profile_refresh_required);
          assert(w._rx_ps_rx_us==timings[0] && w._rx_ps_sleep_us==timings[1]);
          assert(secondary ? w._profile_saved_rxps : w._rx_ps_enabled);
        }
      }
    }
  }
  {
    RadioLibWrapper w; w.enable();
    assert(w._profile_rxps_suspended && !w._rx_ps_enabled && !w._rx_ps_armed);
    assert(w.chip.standbyXOSC && w._profile_standby_held && !w._saved_standby_xosc);
    w.setProfileStandbyWarm(true); // repeated requests must not overwrite the saved RC policy
    elapsed_us+=w._profiles.listenUs(0)-1;w.serviceProfileScan();assert(w._active_profile==0);
    elapsed_us++;w.serviceProfileScan();assert(w._active_profile==1);
    elapsed_us+=w._profiles.listenUs(1)-1;w.serviceProfileScan();assert(w._active_profile==1);
    elapsed_us++;w.serviceProfileScan();assert(w._active_profile==0);
    elapsed_us+=w._profiles.listenUs(0);w.serviceProfileScan();assert(w._active_profile==1);
    const auto generation=w._profile_generation;
    w.packet=true;elapsed_us+=100000;w.serviceProfileScan();assert(w._active_profile==1);
    assert(w.prepareTransmitProfile(0)==Result::BUSY);
    w._profiles.setSecondary({},false);w.serviceProfileScan();
    assert(w._active_profile==1 && w._profile_generation==generation);
    assert(w.chip.standbyXOSC); // cannot change oscillator policy during a packet
    w.packet=false;w.serviceProfileScan();
    assert(w._active_profile==0 && w._rx_ps_enabled && !w._profile_rxps_suspended);
    assert(w._rx_ps_armed);
    assert(!w.chip.standbyXOSC && !w._profile_standby_held);
    w.serviceProfileScan(); assert(!w._profile_refresh_required);
  }
  {
    RadioLibWrapper w;
    w._profiles.primary.bw=500; w._cur_bw=500;
    w.enable();
    w._profiles.secondary.params.bw=62.5;
    ++w._profiles.generation[1];
    w.serviceProfileScan();assert(w._active_profile==1); // slower channel first
    elapsed_us+=w._profiles.listenUs(1)-1;w.serviceProfileScan();assert(w._active_profile==1);
    elapsed_us++;w.serviceProfileScan();assert(w._active_profile==0);
  }
  {
    RadioLibWrapper w;w.enable();w.fail=true;
    elapsed_us+=100000;w.serviceProfileScan();
    assert(w._profiles.switch_failures==1 && w._active_profile==0);
    assert(w.chip.standbyXOSC && w._profile_standby_held);
    const auto applies=w.applies;
    for (int i=0;i<10;++i) w.serviceProfileScan();
    assert(w.applies==applies); // failed target is not hammered every loop
    elapsed_us+=1001000;w.fail=false;w.serviceProfileScan();assert(w._active_profile==1);
  }
  {
    RadioLibWrapper w;
    // Zero is the inactive retry timer even after 24.8 days of uptime.
    elapsed_us=uint64_t(0xf0000000UL)*1000;w.enable();
    assert(w._profile_rxps_suspended);
    elapsed_us+=100000;w.serviceProfileScan();assert(w._profiles.switches>=1);
  }
  {
    RadioLibWrapper w;auto primary=w._profiles.primary;
    auto temp=primary;temp.freq=910.5;temp.preamble=64;
    w.packet=true;
    assert(w.trySetPrimaryParams(temp,true)==Result::BUSY);
    assert(w._profiles.primary==primary && !w._profiles.primary_temporary);
    w.packet=false;w.fail=true;
    assert(w.trySetPrimaryParams(temp,true)==Result::FAILED);
    assert(w._profiles.primary==primary && w._physical_preamble==32);
    w.fail=false;
    assert(w.trySetPrimaryParams(temp,true)==Result::APPLIED);
    assert(w._profiles.primary==temp && w._physical_preamble==64);
    assert(w._profile_generation==w._profiles.generation[0]);
  }
  {
    RadioLibWrapper w; w.chip.standbyXOSC=true; w._rx_ps_enabled=false; w._rx_ps_armed=false;
    w.enable(); assert(w._saved_standby_xosc);
    w._profiles.setSecondary({},false); w.serviceProfileScan();
    assert(w.chip.standbyXOSC && !w._profile_standby_held && !w._rx_ps_enabled);
    w.setProfileStandbyWarm(false); assert(w.chip.standbyXOSC);
  }
  for (int reason=0;reason<4;++reason) {
    RadioLibWrapper w;
    if (reason==0) w.busy=true;
    if (reason==1) w.packet=true;
    if (reason==2) state=STATE_RX|STATE_INT_READY;
    if (reason==3) state=STATE_TX_WAIT;
    w.enable();
    assert(!w._profile_standby_held && !w.chip.standbyXOSC && w._rx_ps_enabled);
    w.busy=w.packet=false; state=STATE_RX; w.serviceProfileScan();
    assert(w.chip.standbyXOSC && w._profile_rxps_suspended);
    w._profiles.setSecondary({},false); w.busy=true; w.serviceProfileScan();
    assert(w.chip.standbyXOSC && w._profile_rxps_suspended);
    w.busy=false; w.serviceProfileScan();
    assert(!w.chip.standbyXOSC && !w._profile_rxps_suspended && w._rx_ps_enabled);
  }
}
'''

class ProfileScanTest(unittest.TestCase):
    def test_production_scan_and_transitions(self):
        source=(ROOT/'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
        names=[('uint8_t','beginReconfigure'),('void','endReconfigure'),
            ('void','serviceProfileScan')]+[('mesh::RadioParamApplyResult',name) for name in
            ['tuneProfile','prepareTransmitProfile','tryRestoreCodingRate','trySetParams','trySetPrimaryParams']]
        methods='\n'.join(method(source,f'{kind} RadioLibWrapper::{name}(') for kind,name in names)
        methods+='\n'+method(source,
            'mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile, bool')
        wrapper=(ROOT/'src/helpers/radiolib/CustomSX1262Wrapper.h').read_text()
        methods+='\n'+method(wrapper,'void setProfileStandbyWarm(').replace(
            'void setProfileStandbyWarm(bool enabled) override',
            'void RadioLibWrapper::setProfileStandbyWarm(bool enabled)')
        with tempfile.TemporaryDirectory() as folder:
            cpp=Path(folder)/'test.cpp'; exe=Path(folder)/'test.exe'
            (Path(folder)/'Arduino.h').write_text('#pragma once\n#include <cstdint>\n#include <cmath>\n')
            cpp.write_text(HARNESS.replace('@METHODS@',methods))
            result=subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra',
                '-I',folder,'-I',str(ROOT/'src'),str(cpp),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(exe)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_warm_oscillator_is_not_used_before_tcxo_reinitialization(self):
        source=(ROOT/'src/helpers/radiolib/CustomSX1262.h').read_text()
        begin=method(source,'int16_t begin(')
        harness=r'''
#include <cassert>
#include <cstdint>
#define RADIOLIB_SX126X_SYNC_WORD_PRIVATE 0x12
#define RADIOLIB_ERR_NONE 0
struct SX1262 {
  bool standbyXOSC=false;
  int16_t beginResult=0, calibrationResult=0;
  unsigned calibrations=0;
  int16_t begin(float,float,uint8_t,uint8_t,uint8_t,int8_t,uint16_t,float,bool) {
    assert(!standbyXOSC); // every init command must use RC until TCXO is configured
    return beginResult;
  }
};
struct CustomSX1262: SX1262 {
  bool _coldStandby=false;
  bool _tcxoWakePending=true;
  struct { bool invalidated=false; void invalidate() { invalidated=true; } } _profileSwitch;
  int16_t applyMeshCoreTcxoDelay() {
    assert(!standbyXOSC); ++calibrations; return calibrationResult;
  }
  @BEGIN@
};
int main() {
  for (bool warm: {false,true}) {
    for (int failure=0; failure<3; ++failure) {
      CustomSX1262 radio; radio.standbyXOSC=warm;
      radio.beginResult=failure==1 ? -1 : 0;
      radio.calibrationResult=failure==2 ? -2 : 0;
      assert(radio.begin()==-failure);
      assert(radio._profileSwitch.invalidated);
      assert(radio._coldStandby);
      assert(!radio._tcxoWakePending);
      assert(radio.standbyXOSC==warm);
      assert(radio.calibrations==(failure==1 ? 0u : 1u));
    }
  }
}
'''
        with tempfile.TemporaryDirectory() as folder:
            cpp=Path(folder)/'test.cpp'; exe=Path(folder)/'test.exe'
            cpp.write_text('#include <initializer_list>\n'+harness.replace('@BEGIN@',begin))
            result=subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra',
                str(cpp),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(exe)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__': unittest.main()
