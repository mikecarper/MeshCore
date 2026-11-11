#!/usr/bin/env python3
"""Compile the actual SX1276 host-timed backend against a register/IRQ spy."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


def harness():
    wrapper = (ROOT/'src/helpers/radiolib/CustomSX1276Wrapper.h').read_text()
    methods = '\n'.join(body(wrapper, name).replace(' override', '') for name in (
        'uint32_t agcPulseTimerHz(', 'uint32_t defaultAGCPulseSleepUs(',
        'uint32_t rxPowerSavingTransitionUs(', 'int16_t pulseSX127xMode(',
        'int16_t pulseSX127xSetMode(', 'int16_t pulseSX127xSnapshot(',
        'void pulseSX127xFailure(', 'bool startAGCPulse(',
        'void retireSX127xPulse(', 'void serviceAGCPulse('))
    return CPP.replace('@METHODS@', methods)


CPP = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>
#include <helpers/radiolib/RxpsPulse.h>
#include <helpers/radiolib/RxpsPulseTiming.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_WRONG_MODEM -20
#define RADIOLIB_ERR_INVALID_SLEEP_PERIOD -21
#define RADIOLIB_SX127X_REG_OP_MODE 0x01
#define RADIOLIB_SX127X_REG_IRQ_FLAGS 0x12
#define RADIOLIB_SX127X_REG_MODEM_STAT 0x18
#define RADIOLIB_SX127X_REG_HOP_CHANNEL 0x1c
#define RADIOLIB_SX127X_LORA 0x80
#define RADIOLIB_SX127X_RXCONTINUOUS 5
#define RADIOLIB_SX127X_STANDBY 1
#define RADIOLIB_SX127X_SLEEP 0
#define RADIOLIB_SX127X_CLEAR_IRQ_FLAG_RX_DONE 0x40
#define RADIOLIB_SX127X_CLEAR_IRQ_FLAG_VALID_HEADER 0x10
static uint32_t now_us=100;
static uint32_t micros() { return now_us; }
static uint32_t millis() { return now_us/1000; }
struct Module {
  enum { MODE_IDLE=0, MODE_RX=1 };
  uint8_t registers[256]={};
  int calls=0, fail_call=0, fail_from=0, switch_writes=0, mode_writes=0, sleep_writes=0;
  bool pending=false, packet_at_standby=false, partial_at_standby=false, packet_at_rx=false;
  bool ignore_writes=false;
  std::vector<uint8_t> fifo;
  std::vector<uint8_t> modes;
  Module() {
    registers[1]=0x8d; // LoRa, low-frequency bit retained, continuous RX.
    registers[0x40]=0x51;registers[0x41]=0x27; // DIO mappings must stay intact.
    registers[0x0d]=0x35;registers[0x0e]=0x80;registers[0x0f]=0x40;
    registers[0x11]=0x8f;registers[0x1d]=0x73;registers[0x1e]=0x74;
  }
  int16_t fault() { ++calls;return calls==fail_call||(fail_from&&calls>=fail_from)?-321:0; }
  int16_t SPIgetRegValue(uint8_t address) {
    int16_t rc=fault();return rc?rc:registers[address];
  }
  int16_t SPIsetRegValue(uint8_t address,uint8_t value,uint8_t msb,uint8_t lsb,uint8_t check) {
    assert(address==1&&msb==2&&lsb==0&&check==5);
    int16_t rc=fault();if(rc)return rc;
    ++mode_writes;modes.push_back(value);
    if(ignore_writes)return 0;
    registers[1]=(registers[1]&0xf8)|value;
    if(value==0) { ++sleep_writes;fifo.clear(); }
    if(value==1&&packet_at_standby) { packet_at_standby=false;packet(); }
    if(value==1&&partial_at_standby) { partial_at_standby=false;registers[0x18]=2;registers[0x12]|=0x10; }
    if(value==5&&packet_at_rx) { packet_at_rx=false;packet(); }
    return 0;
  }
  void packet() { pending=true;registers[0x12]|=0x40;fifo={1,2,3,4,5}; }
  void setRfSwitchState(int) { ++switch_writes; }
};
struct CustomSX1276 { Module* mod;explicit CustomSX1276(Module* m):mod(m){} Module* getMod(){return mod;} };
struct Wrapper {
  CustomSX1276* _radio;
  RxpsPulse _rxps_pulse;
  uint8_t _sx127x_pulse_mode_prefix=0x80,_sx127x_pulse_resume_stage=0,_rxps_pulse_retries=0;
  uint32_t _sx127x_pulse_wake_at=0,_profile_retry_at=0,_nf_sample_from=0,_rx_retry_delay_ms=0;
  int state=1,_startrx_fails=2,lease_admit=0,lease_finish=0,lease_validate=0;
  bool _profile_refresh_required=false,_profile_repair_required=false,_rx_retry_pending=true,stale=false;
  static constexpr uint32_t NoiseFloorSettleMillis=20;
  explicit Wrapper(CustomSX1276& radio):_radio(&radio) {_rxps_pulse.host_timed=true;_rxps_pulse.cadence(16000);}
  bool hasPendingRadioInterrupt()const{return _radio->mod->pending;}
  uint32_t getAGCPulseSleep()const{return defaultAGCPulseSleepUs();}
  void admitAGCPulse(uint32_t now){++lease_admit;_rxps_pulse.admitted(now);}
  void validateAGCScanLease(){++lease_validate;if(stale)_rxps_pulse.cancelRequest();}
  void finishAGCScanLease(){if(!_rxps_pulse.pending)++lease_finish;}
  @METHODS@
  bool start(){_rxps_pulse.request(now_us);return startAGCPulse();}
  void drain(){for(unsigned i=0;i<40&&_rxps_pulse.pending&&_rxps_pulse.phase!=RxpsPulse::Phase::Packet;++i){now_us+=1000;serviceAGCPulse();}}
};
static void unchanged(const Module& m) {
  assert(m.registers[0x40]==0x51&&m.registers[0x41]==0x27);
  assert(m.registers[0x0d]==0x35&&m.registers[0x0e]==0x80&&m.registers[0x0f]==0x40);
  assert(m.registers[0x11]==0x8f&&m.registers[0x1d]==0x73&&m.registers[0x1e]==0x74);
  assert((m.registers[1]&0xf8)==0x88);
}
static void normal() {
  Module m;CustomSX1276 r(&m);Wrapper w(r);
  assert(w.defaultAGCPulseSleepUs()==1501&&w.agcPulseTimerHz()==1000000);
  assert(w.start());assert(w._rxps_pulse.phase==RxpsPulse::Phase::Sleep);
  assert(w._rxps_pulse.sleep_ticks==1501&&m.sleep_writes==1);
  const int calls=m.calls,switches=m.switch_writes;
  w.serviceAGCPulse();now_us+=1500;w.serviceAGCPulse();
  assert(m.calls==calls&&m.switch_writes==switches&&w._rxps_pulse.cycles==0);
  ++now_us;w.serviceAGCPulse();assert(w._rxps_pulse.cycles==1);
  const int waking_calls=m.calls;now_us+=999;w.serviceAGCPulse();assert(m.calls==waking_calls);
  ++now_us;w.serviceAGCPulse();assert((m.registers[1]&7)==5);
  w.drain();assert(!w._rxps_pulse.pending&&w._rxps_pulse.ok==1&&w._rxps_pulse.verified==1);
  assert(w._rxps_pulse.takeCompletion()==RxpsPulse::Result::Applied);
  assert(!w._rxps_pulse.low&&!w._rxps_pulse.high&&!w._rxps_pulse.wake&&!w._rxps_pulse.rtc&&!w._rxps_pulse.busy_high.n);
  assert(w.lease_admit==1&&w.lease_validate==1&&w.lease_finish==1);
  assert(!w._rx_retry_pending&&!w._startrx_fails);unchanged(m);
  char page[180];w._rxps_pulse.format(0,16,page,sizeof(page));assert(std::strstr(page,"method=hostsleep"));
}
static void packets() {
  for(int scenario=0;scenario<3;++scenario){
    Module m;CustomSX1276 r(&m);Wrapper w(r);
    if(scenario==0)m.packet();else if(scenario==1)m.packet_at_standby=true;else m.pending=true;
    const bool admitted=w.start();
    if(scenario==1){assert(admitted&&w._rxps_pulse.phase==RxpsPulse::Phase::Packet);}
    else {assert(!admitted&&!w._rxps_pulse.pending&&m.mode_writes==0);}
    assert(!m.sleep_writes);if(scenario!=2)assert(m.fifo.size()==5&&(m.registers[0x12]&0x40));
    const int calls=m.calls;w.serviceAGCPulse();assert(m.calls==calls);unchanged(m);
  }
  Module m;CustomSX1276 r(&m);Wrapper w(r);m.packet_at_rx=true;
  assert(w.start());w.drain();assert(!w._rxps_pulse.pending&&w._rxps_pulse.ok==1);
  assert(m.pending&&m.fifo.size()==5&&(m.registers[0x12]&0x40));unchanged(m);
}
static void activity() {
  for(uint8_t bit:std::vector<uint8_t>{1,2,4,8}){
    Module m;CustomSX1276 r(&m);Wrapper w(r);m.registers[0x18]=bit;
    assert(!w.start()&&!m.mode_writes&&!w._rxps_pulse.pending);
  }
  Module m;CustomSX1276 r(&m);Wrapper w(r);m.partial_at_standby=true;
  assert(w.start());assert(!m.sleep_writes);w.drain();
  assert(!w._rxps_pulse.pending&&!w._rxps_pulse.ok&&w._rxps_pulse.cancel);
  assert(w._rxps_pulse.takeCompletion()==RxpsPulse::Result::Deferred);unchanged(m);
}
static void faults() {
  Module baseline;CustomSX1276 b(&baseline);Wrapper normal_wrapper(b);assert(normal_wrapper.start());normal_wrapper.drain();
  const int count=baseline.calls;
  for(int call=1;call<=count;++call){
    Module m;CustomSX1276 r(&m);Wrapper w(r);m.fail_call=call;
    assert(w.start());w.drain();
    assert(!w._rxps_pulse.pending&&w._rxps_pulse.fail==1&&!w._rxps_pulse.ok);
    assert(w._rxps_pulse.error==-321&&w._rxps_pulse.takeCompletion()==RxpsPulse::Result::Failed);
    assert((m.registers[1]&7)==5);unchanged(m);
  }
}
static void bounded() {
  Module m;CustomSX1276 r(&m);Wrapper w(r);m.fail_from=1;
  assert(w.start());w.drain();assert(!w._rxps_pulse.pending&&w._rxps_pulse.fail==1&&m.calls<=6);
  assert(w._profile_refresh_required&&w._profile_repair_required&&w._rxps_pulse.error==-321);
  Module early;CustomSX1276 re(&early);Wrapper we(re);assert(we.start());
  early.registers[1]=0x8d;now_us+=1501;we.serviceAGCPulse();we.drain();
  assert(!we._rxps_pulse.pending&&!we._rxps_pulse.ok&&!we._rxps_pulse.cycles&&we._rxps_pulse.error==RxpsPulse::ModeError);
  Module bad;CustomSX1276 rb(&bad);Wrapper wb(rb);bad.ignore_writes=true;
  assert(wb.start());wb.drain();assert(!wb._rxps_pulse.pending&&!wb._rxps_pulse.ok&&wb._rxps_pulse.error==RxpsPulse::ModeError);
  Module pll;CustomSX1276 rp(&pll);Wrapper wp(rp);pll.registers[0x1c]=0x80;
  assert(wp.start());wp.drain();assert(!wp._rxps_pulse.pending&&!wp._rxps_pulse.ok&&wp._rxps_pulse.error==RxpsPulse::ModeError);
}
static void context() {
  Module m;CustomSX1276 r(&m);Wrapper w(r);assert(w.start());w.stale=true;w.drain();
  assert(!w._rxps_pulse.pending&&w._rxps_pulse.cycles==1&&!w._rxps_pulse.ok&&w._rxps_pulse.cancel);
  assert(w._rxps_pulse.takeCompletion()==RxpsPulse::Result::Deferred);
  Module wrong;CustomSX1276 rw(&wrong);Wrapper ww(rw);wrong.registers[1]=1;
  assert(ww.start());ww.drain();assert(!ww._rxps_pulse.pending&&!wrong.mode_writes&&ww._rxps_pulse.error==RADIOLIB_ERR_WRONG_MODEM);
  Module cad;CustomSX1276 rc(&cad);Wrapper wc(rc);cad.registers[1]=0x8f;
  assert(!wc.start()&&!cad.mode_writes);
  Module wrap;CustomSX1276 rr(&wrap);Wrapper wr(rr);now_us=0xffffff00UL;
  assert(wr.start());wr.drain();assert(!wr._rxps_pulse.pending&&wr._rxps_pulse.ok==1);
}
int main(int argc,char** argv){
  assert(argc==2);std::string name=argv[1];
  if(name=="normal")normal();else if(name=="packets")packets();else if(name=="activity")activity();
  else if(name=="faults")faults();else if(name=="bounded")bounded();else if(name=="context")context();else assert(false);
  std::cout<<"PASS "<<name<<"\n";
}
'''


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=shutil.which(os.environ.get('CXX','c++'))
        if not compiler:raise unittest.SkipTest('A C++ compiler is required')
        cls.temp=tempfile.TemporaryDirectory(prefix='sx1276-pulse-');cls.addClassCleanup(cls.temp.cleanup)
        path=Path(cls.temp.name);source=path/'main.cpp';source.write_text(harness())
        cls.binary=path/'test'
        result=subprocess.run([compiler,'-std=c++11','-Wall','-Wextra','-Werror','-pedantic',
            '-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie',
            '-I',str(ROOT/'src'),str(source),'-o',str(cls.binary)],capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stderr)
    def case(self,name):
        env=os.environ.copy();env['ASAN_OPTIONS']='detect_leaks=1:abort_on_error=1';env['UBSAN_OPTIONS']='halt_on_error=1'
        result=subprocess.run([str(self.binary),name],capture_output=True,text=True,env=env)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'PASS '+name)
    def test_checked_sleep_host_timer_and_RX_restore_without_fake_busy_witness(self):self.case('normal')
    def test_packet_before_during_standby_and_after_RX_preserves_FIFO_IRQ(self):self.case('packets')
    def test_activity_defers_before_writes_and_partial_standby_cannot_claim_applied(self):self.case('activity')
    def test_every_actual_SPI_failure_preserves_first_error_and_checked_recovery(self):self.case('faults')
    def test_persistent_read_write_or_PLL_failure_is_bounded_and_not_applied(self):self.case('bounded')
    def test_profile_cancellation_wrong_modem_CAD_and_timer_wrap_stay_honest(self):self.case('context')


if __name__=='__main__':unittest.main()
