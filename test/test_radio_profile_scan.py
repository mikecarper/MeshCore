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
struct Chip { int standby() { return 0; } };
struct RadioLibWrapper {
  Chip chip; Chip* _radio = &chip;
  mesh::RadioProfiles _profiles;
  bool _params_valid=true, packet=false, busy=false, fail=false;
  bool _rx_ps_enabled=true, _rx_ps_armed=true, _rx_ps_continuous_fallback=false;
  bool _profile_saved_rxps=false, _profile_rxps_suspended=false;
  bool _nf_calib_active=false, _noise_floor_valid=true, _profile_refresh_required=false;
  uint32_t _rx_ps_rx_us=50000, _rx_ps_sleep_us=50000;
  uint8_t _active_profile=0, _cur_sf=7, _cur_cr=5;
  uint32_t _profile_generation=1, _profile_visit_us=0, _profile_retry_at=0;
  uint32_t _profile_scan_generation[2]={};
  uint16_t _physical_preamble=32;
  float _cur_freq=909.5, _cur_bw=62.5;
  unsigned applies=0;
  RadioLibWrapper() {
    state=STATE_RX; elapsed_us=100000;
    auto& p=_profiles.primary;
    p.freq=_cur_freq; p.bw=_cur_bw; p.sf=_cur_sf; p.cr=_cur_cr;
    _profile_visit_us=micros();
  }
  bool isChipBusy() { return busy; }
  bool isReceivingPacket() { return packet; }
  bool isPacketPendingOrReceiving() { return packet || (state & STATE_INT_READY); }
  bool supportsRxPowerSaving() { return true; }
  void startRecv() { state=STATE_RX; _profile_visit_us=micros(); _rx_ps_armed=_rx_ps_enabled; }
  void stopReceiveDutyCycle() { _rx_ps_armed=false; }
  bool applyParams(float f,float,uint8_t,uint8_t) { ++applies; elapsed_us+=1200; return !fail || f==909.5f; }
  void cacheParams(float f,float b,uint8_t s,uint8_t c) { _cur_freq=f;_cur_bw=b;_cur_sf=s;_cur_cr=c; }
  bool restoreAfterDeepInit() { return true; }
  void recalibrateNoiseFloor() { _noise_floor_valid=false; }
  bool validateProfile(const mesh::RadioProfileParams& p) const { return mesh::RadioProfiles::valid(p); }
  uint16_t profilePreamble(uint8_t n) const { return _profiles.preamble(n,32); }
  uint8_t beginReconfigure();
  void endReconfigure(bool);
  void serviceProfileScan();
  mesh::RadioParamApplyResult tuneProfile(uint8_t);
  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t);
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
  {
    RadioLibWrapper w; w.enable();
    assert(w._profile_rxps_suspended && !w._rx_ps_enabled && !w._rx_ps_armed);
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
    w.packet=false;w.serviceProfileScan();
    assert(w._active_profile==0 && w._rx_ps_enabled && !w._profile_rxps_suspended);
    assert(w._rx_ps_armed);
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
}
'''

class ProfileScanTest(unittest.TestCase):
    def test_production_scan_and_transitions(self):
        source=(ROOT/'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
        names=[('uint8_t','beginReconfigure'),('void','endReconfigure'),
            ('void','serviceProfileScan')]+[('mesh::RadioParamApplyResult',name) for name in
            ['tuneProfile','prepareTransmitProfile','trySetParams','trySetPrimaryParams']]
        methods='\n'.join(method(source,f'{kind} RadioLibWrapper::{name}(') for kind,name in names)
        with tempfile.TemporaryDirectory() as folder:
            cpp=Path(folder)/'test.cpp'; exe=Path(folder)/'test.exe'
            (Path(folder)/'Arduino.h').write_text('#pragma once\n#include <cstdint>\n#include <cmath>\n')
            cpp.write_text(HARNESS.replace('@METHODS@',methods))
            result=subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra',
                '-I',folder,'-I',str(ROOT/'src'),str(cpp),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(exe)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__': unittest.main()
