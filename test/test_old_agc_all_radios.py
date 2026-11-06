#!/usr/bin/env python3
"""Compile the real Dispatcher old timer/API for normal and compact radios; no PIO."""
from pathlib import Path
import os,re,shutil,subprocess,tempfile,unittest
from cpp_source import body
ROOT=Path(__file__).resolve().parents[1]
MAIN=r'''
#include <Dispatcher.h>
#include <helpers/StaticPoolPacketManager.h>
#include <cassert>
#include <cstring>
class Clock:public mesh::MillisecondClock {public:unsigned long now=0;unsigned long getMillis()override{return now;}};
class Radio:public mesh::Radio {
 public:
 bool admit=true,calibrating=false,rxps=false;uint32_t configured_cadence_ms=0;unsigned checked_old=0,old_resets=0,primary_resets=0;
 @OLD_FIELDS@
 @OLD_METHODS@
#if MESH_COMPACT_STM32_RADIO
 static constexpr bool SupportsRxPowerSaving=false;
 static_assert(!SupportsRxPowerSaving, "Compact RXPS contract");
 @COMPACT_RXPS_CAPABILITY@
#endif
 void begin()override{}
 int recvRaw(uint8_t*,int)override{return 0;}
 uint32_t getEstAirtimeFor(int)override{return 1;}
 float packetScore(float,int)override{return 0;}
 bool startSendRaw(const uint8_t*,int)override{return false;}
 bool isSendComplete()override{return false;}
 void onSendFinished()override{}
 bool isInRecvMode()const override{return true;}
 bool isCalibratingNoiseFloor()const override{return calibrating;}
#if !MESH_COMPACT_STM32_RADIO
 bool isRxPowerSavingEnabled()const override{return rxps;}
 void configureAGCMaintenance(uint32_t ms)override{configured_cadence_ms=ms;}
#endif
 void resetAGC()override{++primary_resets;}
 bool tryResetAGC()override{return resetAGCIfIdle();}
 bool resetAGCIfIdle(bool=true){++checked_old;if(!admit)return false;++old_resets;return true;}
};
#if MESH_ENABLE_OLD_AGC && !MESH_COMPACT_STM32_RADIO
class Unsupported:public Radio {public:@UNSUPPORTED_CAPABILITY@};
#endif
class Dispatcher:public mesh::Dispatcher {
 public:
 using mesh::Dispatcher::getNextQueueWakeDelay;
 uint32_t primary_ms=0;
 Dispatcher(Radio&r,Clock&c,StaticPoolPacketManager&m):mesh::Dispatcher(r,c,m){}
 mesh::DispatcherAction onRecvPacket(mesh::Packet*)override{return ACTION_RELEASE;}
 int getAGCResetInterval()const override{return primary_ms;}
};
struct Fixture {Clock c;Radio r;StaticPoolPacketManager m{4};Dispatcher d{r,c,m};void loop(uint32_t n){c.now=n;d.loop();}void begin(uint32_t n=0){c.now=n;d.begin();loop(n);}};
int main(int argc,char**argv){assert(argc==2);const char*s=argv[1];
 if(!strcmp(s,"default_range")){Radio r;assert(r.supportsOldAGC()&&r.getOldAGCInterval()==0);assert(r.setOldAGCInterval(86400));assert(r.getOldAGCInterval()==86400);assert(!r.setOldAGCInterval(86401));assert(r.getOldAGCInterval()==86400);assert(r.setOldAGCInterval(0));assert(r.getOldAGCInterval()==0);Radio fresh;assert(fresh.getOldAGCInterval()==0);}
 else if(!strcmp(s,"old_only_wake")){Fixture f;assert(f.r.setOldAGCInterval(600));f.begin(100);uint32_t delta=0;assert(f.d.getNextQueueWakeDelay(delta)&&delta==600000);f.loop(600099);assert(f.r.old_resets==0);f.loop(600100);assert(f.r.old_resets==1&&f.r.primary_resets==0);f.loop(600100);assert(f.r.old_resets==1);}
 else if(!strcmp(s,"disabled")){Fixture f;f.begin();f.loop(999999);assert(!f.r.old_resets&&!f.r.checked_old);uint32_t delta=99;assert(!f.d.getNextQueueWakeDelay(delta));}
 else if(!strcmp(s,"deferred")){Fixture f;f.r.setOldAGCInterval(2);f.r.admit=false;f.begin();f.loop(2000);assert(f.r.checked_old==1&&!f.r.old_resets);f.loop(2249);assert(f.r.checked_old==1);f.r.admit=true;f.loop(2250);assert(f.r.checked_old==2&&f.r.old_resets==1);f.loop(4249);assert(f.r.old_resets==1);f.loop(4250);assert(f.r.old_resets==2);}
 else if(!strcmp(s,"both_due")){Fixture f;f.d.primary_ms=8000;f.r.setOldAGCInterval(8);f.begin();f.loop(8001);assert(f.r.old_resets==1&&f.r.primary_resets==0);f.loop(8252);assert(f.r.old_resets==1&&f.r.primary_resets==1);}
 else if(!strcmp(s,"disable_reenable_wrap")){Fixture f;const uint32_t start=0xfffffff0U;f.r.setOldAGCInterval(2);f.begin(start);f.loop(uint32_t(start+1999));assert(!f.r.old_resets);f.loop(uint32_t(start+2000));assert(f.r.old_resets==1);f.r.setOldAGCInterval(0);f.loop(uint32_t(start+2001));f.loop(uint32_t(start+9000));assert(f.r.old_resets==1);f.r.setOldAGCInterval(2);f.loop(uint32_t(start+9001));f.loop(uint32_t(start+11000));assert(f.r.old_resets==1);f.loop(uint32_t(start+11001));assert(f.r.old_resets==2);}
#if MESH_ENABLE_OLD_AGC && !MESH_COMPACT_STM32_RADIO
 else if(!strcmp(s,"unsupported")){Unsupported r;assert(!r.supportsOldAGC()&&!r.setOldAGCInterval(600)&&r.getOldAGCInterval()==0);assert(r.periodicOldAGCMaintenance()==mesh::AGCMaintenanceResult::Deferred&&!r.checked_old);}
#endif
 else if(!strcmp(s,"rxps_suspends_primary_only")){Fixture f;f.d.primary_ms=16000;f.r.rxps=true;f.begin();f.loop(50000);assert(!f.r.primary_resets);uint32_t delay=0;assert(!f.d.getNextQueueWakeDelay(delay));assert(f.d.primary_ms==16000);f.r.setOldAGCInterval(2);f.loop(50001);f.loop(52001);assert(f.r.old_resets==1&&!f.r.primary_resets);f.r.setOldAGCInterval(0);f.r.rxps=false;f.loop(52002);f.loop(68001);assert(!f.r.primary_resets);f.loop(68003);assert(f.r.primary_resets==1&&f.d.primary_ms==16000);}
 else if(!strcmp(s,"cadence_units")){Fixture f;f.d.primary_ms=16000;f.begin();
#if MESH_WARM_RX_REFRESH
 assert(f.r.configured_cadence_ms==16000);f.r.rxps=true;f.loop(1);assert(f.r.configured_cadence_ms==0);f.r.rxps=false;f.loop(2);assert(f.r.configured_cadence_ms==16000);
#endif
 }
 else if(!strcmp(s,"disabled_api")){Fixture f;assert(!f.r.supportsOldAGC()&&!f.r.setOldAGCInterval(600)&&f.r.getOldAGCInterval()==0);assert(f.r.periodicOldAGCMaintenance()==mesh::AGCMaintenanceResult::Deferred);f.d.primary_ms=16000;
#if MESH_COMPACT_STM32_RADIO
 assert(!f.r.supportsRxPowerSaving()&&!f.r.setRxPowerSaving(true,1000,1000));assert(!f.r.isRxPowerSavingEnabled());f.begin();f.loop(16001);assert(f.r.primary_resets==1&&f.d.primary_ms==16000);
#else
 f.r.rxps=true;f.begin();f.loop(50000);assert(!f.r.primary_resets&&!f.r.checked_old);uint32_t delay=0;assert(!f.d.getNextQueueWakeDelay(delay));f.r.rxps=false;f.loop(50001);f.loop(66002);assert(f.r.primary_resets==1&&f.d.primary_ms==16000);
#endif
 }
 else assert(false);
}
'''
class OldAGCAllRadios(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  c=os.environ.get('CXX')or shutil.which('g++')or shutil.which('clang++');assert c
  cls.tmp=tempfile.TemporaryDirectory(prefix='meshcore-old-all-');cls.addClassCleanup(cls.tmp.cleanup);w=Path(cls.tmp.name);h=(ROOT/'src/helpers/radiolib/RadioLibWrappers.h').read_text();cpp=(ROOT/'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
  unsupported=body((ROOT/'src/helpers/radiolib/CustomLR2021Wrapper.h').read_text(),'bool supportsOldAGC(')
  compact_cap=body((ROOT/'src/helpers/radiolib/CustomSTM32WLxWrapper.h').read_text(),'bool supportsRxPowerSaving(')
  methods='\n'.join(body(h,s).replace(' override','')for s in('bool supportsOldAGC(','bool setOldAGCInterval(','uint32_t getOldAGCInterval('))+'\n'+body(cpp,'mesh::AGCMaintenanceResult RadioLibWrapper::periodicOldAGCMaintenance(').replace('RadioLibWrapper::','')
  cls.binaries=[]
  for mode,flag,pulse,enabled in [('normal',None,0,True),('warm',None,1,True),('wio','WIO_E5_MINI_COMPACT_UNSUPPORTED_AGC',0,True),('stm32_companion','STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC',0,True),('wio_default_off','WIO_E5_MINI_COMPACT_UNSUPPORTED_AGC',0,False),('stm32_default_off','STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC',0,False),('normal_explicit_off',None,0,False),('warm_explicit_off',None,1,False)]:
   src=w/(mode+'.cpp');src.write_text(MAIN.replace('@COMPACT_RXPS_CAPABILITY@',compact_cap).replace('@UNSUPPORTED_CAPABILITY@',unsupported).replace('@OLD_FIELDS@',''if flag or not enabled else'uint32_t _old_agc_seconds=0;').replace('@OLD_METHODS@',''if flag or not enabled else methods),encoding='ascii');exe=w/mode;cmd=[c,'-std=c++17','-Wall','-Wextra','-DMESH_WARM_RX_REFRESH='+str(pulse),'-I'+str(ROOT/'test/mocks'),'-I'+str(ROOT/'src'),str(ROOT/'src/Dispatcher.cpp'),str(ROOT/'src/Packet.cpp'),str(ROOT/'src/helpers/StaticPoolPacketManager.cpp'),str(src),'-o',str(exe)]
   if flag:cmd.insert(4,'-D'+flag+'=1')
   if flag and enabled:cmd.insert(4,'-DMESH_ENABLE_OLD_AGC=1')
   if not flag and not enabled:cmd.insert(4,'-DMESH_ENABLE_OLD_AGC=0')
   built=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
   if built.returncode:raise AssertionError(built.stdout+'\n'.join(x for x in built.stderr.splitlines()if 'error:'in x or 'undefined reference'in x))
   cls.binaries.append((mode,exe,enabled))
 def scenario(self,name):
  for mode,exe,enabled in self.binaries:
   if (name=='disabled_api')==enabled:continue
   if name=='rxps_suspends_primary_only'and mode in('wio','stm32_companion'):continue
   if name=='unsupported'and mode!='normal':continue
   with self.subTest(mode=mode):
    run=subprocess.run([str(exe),name],capture_output=True,text=True,timeout=10);self.assertEqual(run.returncode,0,run.stdout+run.stderr)
 def test_default_range_and_reboot_off(self):self.scenario('default_range')
 def test_old_only_wakes_without_primary(self):self.scenario('old_only_wake')
 def test_disabled(self):self.scenario('disabled')
 def test_ownership_defer_and_exact_long_cadence(self):self.scenario('deferred')
 def test_both_due_never_reset_twice_in_one_loop(self):self.scenario('both_due')
 def test_disable_reenable_and_millis_wrap(self):self.scenario('disable_reenable_wrap')
 def test_rxps_suspends_primary_but_not_old_or_preferences(self):self.scenario('rxps_suspends_primary_only')
 def test_pulse_cadence_uses_getter_milliseconds_without_rescaling(self):self.scenario('cadence_units')
 def test_optional_tiny_default_and_explicit_normal_disable(self):self.scenario('disabled_api')
 def test_real_compact_capability_contract_rejects_future_rxps_enable(self):
  source=(ROOT/'src/helpers/radiolib/CustomSTM32WLxWrapper.h').read_text();assertion=re.search(r'  static_assert\(!SupportsRxPowerSaving,[^;]+;',source).group();capability=body(source,'bool supportsRxPowerSaving(')
  prefix='#include <cassert>\nstruct Base { static constexpr bool SupportsRxPowerSaving=@SUPPORT@; virtual bool supportsRxPowerSaving() const {return false;} };\n'
  compact='struct Compact:Base {'+assertion+capability+'};\n'
  with tempfile.TemporaryDirectory(prefix='meshcore-compact-rxps-contract-')as tmp:
   f=Path(tmp)/'contract.cpp'
   for static_support,override,passed in [('false',False,True),('true',False,False),('false',True,False)]:
    f.write_text(prefix.replace('@SUPPORT@',static_support)+compact+('struct Future:Compact {bool supportsRxPowerSaving()const override{return true;}};\n'if override else'')+'int main(){Compact r;assert(!r.supportsRxPowerSaving());}\n',encoding='ascii')
    result=subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-fsyntax-only',str(f)],capture_output=True,text=True,timeout=10);self.assertEqual(result.returncode==0,passed,result.stderr)
 def test_noop_radio_cannot_claim_admission(self):self.scenario('unsupported')
if __name__=='__main__':unittest.main()
