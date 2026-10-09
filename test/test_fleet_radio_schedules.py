#!/usr/bin/env python3
"""Run signed fleet commands through the actual primary and secondary schedulers.

The receiver, signed codec, secondary scheduler and storage transactions are
production code; primary CommonCLI branches and repeater methods are extracted
unchanged. Clock, filesystem I/O, physical radio, primary-save endpoint and
packet admission are mocked; signatures, AES and MAC checks remain real.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body
from test_fleet_channel_runtime import UTILS, FILESYSTEM, MESH, REGION_MAP, HARNESS as RUNTIME_HARNESS
from test_repeater_radio_timing_integration import HARNESS as PRIMARY_HARNESS

ROOT = Path(__file__).resolve().parents[1]
HARDWARE = r'''
#pragma once
#include <RadioProfiles.h>
namespace mesh {
class RTCClock {public:virtual ~RTCClock()=default;virtual uint32_t getCurrentTime()=0;};
enum class RadioParamApplyResult:uint8_t {APPLIED,BUSY,FAILED};
class Radio {public:
 virtual ~Radio()=default;
 static constexpr uint32_t CarrierWaveDefaultMillis=10000,CarrierWaveMaxMillis=60000;
 virtual bool supportsCarrierWave()const{return false;}
 virtual bool isCarrierWaveActive()const{return false;}
 virtual uint32_t carrierWaveRemainingMillis()const{return 0;}
 virtual uint8_t carrierWaveProfile()const{return 0;}
 virtual RadioParamApplyResult setCarrierWave(uint8_t,uint32_t){return RadioParamApplyResult::FAILED;}
 virtual bool isInRecvMode()const{return true;}
 virtual RadioProfiles* profiles()=0;
 virtual const RadioProfiles* profiles()const=0;
 virtual bool validateProfile(const RadioProfileParams&)const=0;
 virtual uint16_t profilePreamble(uint8_t)const=0;
 virtual RadioParamApplyResult trySetPrimaryParams(const RadioProfileParams&,bool,const uint32_t*)=0;
};
}
'''

HARNESS = r'''
struct PhysicalRadio:mesh::Radio {
 mesh::RadioProfiles state;
 PhysicalRadio(){state.primary.freq=909.5;state.primary.bw=62.5;state.primary.sf=7;state.primary.cr=5;}
 mesh::RadioProfiles* profiles()override{return &state;}
 const mesh::RadioProfiles* profiles()const override{return &state;}
 bool validateProfile(const mesh::RadioProfileParams& p)const override{return mesh::RadioProfiles::valid(p)&&p.bw<=500;}
 uint16_t profilePreamble(uint8_t profile)const override{return state.preamble(profile,32);}
 mesh::RadioParamApplyResult trySetPrimaryParams(const mesh::RadioProfileParams&,bool,const uint32_t*)override{
  assert(false&&"radio2 scheduler must never mutate primary");return mesh::RadioParamApplyResult::FAILED;}
};
static uint32_t stageDirectProfile(RadioProfileCLI& profiles){
 const uint32_t before=profiles.replyMutationGeneration();char reply[160]={};
 profiles.beginReplyCommand();
 assert(profiles.handle("set radio2 912.5,500,8,5,rxtx",reply,sizeof(reply),true));
 profiles.endReplyCommand();
 assert(!strncmp(reply,"OK",2)&&profiles.hasReplyMutation());
 assert(profiles.replyMutationGeneration()!=before);return profiles.replyMutationGeneration();
}
struct Fixture {
 fs::FS disk;Mesh mesh;PhysicalRadio radio;RadioProfileCLI profiles;FleetChannel fleet{&disk};LocalIdentity publisher;
 unsigned dispatched=0;char reply[160]={};
 Fixture(){profiles.begin(&disk,&radio,&mesh.clock,true);enroll(fleet,publisher);bind(fleet,mesh,profiles);}
 const char* get(const char* text){assert(profiles.handle(text,reply,sizeof(reply)));return reply;}
 Packet send(const char* text,bool mutation_expected=true){
  fake_ms+=1000; // Each request occupies a fresh verification-budget interval.
  Packet packet=command(publisher,mesh.clock.now,text);fleet.receive(&packet,mesh);
  const unsigned before=dispatched;
  fleet.service(mesh,profiles,"scheduler",[&](uint32_t sequence,const char* command,char* response){
   ++dispatched;assert(storage::readLE32(disk.files[Path].data()+56)==sequence);
   assert(profiles.handle(command,response,160,true));strcpy(reply,response);
  });
  assert(dispatched==before+1);assert(!strncmp(reply,"OK",2)||!strncmp(text,"get ",4));
  assert(fleet.waiting()==mutation_expected);return packet;
 }
 void drain(bool success){
  while(!mesh.manager.outbound.empty()){
   Packet* p=mesh.manager.removeOutboundByIdx(0);
   if(success)fleet.complete(p,profiles);else fleet.fail(p,profiles);
  }
  profiles.loop();
 }
 void advance(uint32_t ms,bool wall=true){fake_ms+=ms;if(wall)mesh.clock.now+=ms/1000;profiles.loop();}
};
int main(int argc,char** argv){
 assert(argc==2);const std::string scenario=argv[1];Fixture f;
 if(scenario=="permanent_ack"){
  Packet packet=f.send("set radioat2 910.5,500,8,5,rxtx,+1");
  assert(!f.radio.state.enabled());f.advance(61000);assert(!f.radio.state.enabled());
  assert(f.profiles.hasReplyMutation());f.drain(true);
  assert(!f.fleet.waiting()&&!f.profiles.hasReplyMutation());
  assert(f.radio.state.secondary.params.freq==910.5f&&!f.radio.state.secondary_temporary);
  PhysicalRadio reboot;RadioProfileCLI restored;restored.begin(&f.disk,&reboot,&f.mesh.clock,true);
  assert(reboot.state.secondary.params.freq==910.5f);
  const unsigned before=f.dispatched;f.fleet.receive(&packet,f.mesh);
  f.fleet.service(f.mesh,f.profiles,"node",[&](uint32_t,const char*,char*){++f.dispatched;});
  assert(f.dispatched==before);
 }else if(scenario=="temporary_ack"){
  f.send("set tempradioat2 910.5,500,8,5,rxtx,+1,+2");
  f.advance(61000);assert(!f.radio.state.enabled());f.drain(true);
  assert(f.radio.state.secondary_temporary&&f.radio.state.secondary.params.freq==910.5f);
  f.advance(59000);assert(!f.radio.state.enabled());
 }else if(scenario=="ack_fail"){
  f.send("set radioat2 910.5,500,8,5,rxtx,+1");f.drain(false);
  f.advance(61000);assert(!f.radio.state.enabled());assert(!strcmp(f.get("get radioat2 all"),"> slots: "));
  ++f.mesh.clock.now;f.send("set tempradioat2 910.5,500,8,5,rxtx,+1,+2");f.drain(false);
  f.advance(61000);assert(!f.radio.state.enabled());
 }else if(scenario=="queue_fail"){
  f.mesh.queue_ok=false;f.send("set radioat2 910.5,500,8,5,rxtx,+1",false);
  f.advance(61000);assert(!f.radio.state.enabled());assert(!f.profiles.hasReplyMutation());
 }else if(scenario=="ack_timeout"){
  f.send("set radioat2 910.5,500,8,5,rxtx,+1");f.advance(301000);
  f.fleet.service(f.mesh,f.profiles,"scheduler",[](uint32_t,const char*,char*){assert(false);});
  f.profiles.loop();assert(!f.fleet.waiting()&&!f.profiles.hasReplyMutation());
  assert(!f.radio.state.enabled()&&f.mesh.manager.outbound.empty());
 }else if(scenario=="both_copies"){
  f.mesh.fanout=true;f.send("set radioat2 910.5,500,8,5,rxtx,+1");f.advance(61000);
  Packet* one=f.mesh.manager.removeOutboundByIdx(0);f.fleet.complete(one,f.profiles);f.profiles.loop();
  assert(f.fleet.waiting()&&!f.radio.state.enabled());f.drain(true);
  assert(f.radio.state.secondary.params.freq==910.5f);
 }else if(scenario=="expired_lease"){
  f.send("set tempradioat2 910.5,500,8,5,rxtx,+1,+2");
  f.advance(121000);assert(!f.radio.state.enabled());f.drain(true);
  assert(!f.radio.state.enabled());assert(!strcmp(f.get("get tempradioat2 all"),"> slots: "));
 }else if(scenario=="save_fail"){
  f.send("set radioat2 910.5,500,8,5,rxtx,+1");f.drain(true);f.disk.fail_write=true;
  f.advance(61000);assert(!f.radio.state.enabled());assert(strstr(f.get("get radioat2 all"),"1@"));
  f.disk.fail_write=false;f.advance(60000);assert(f.radio.state.secondary.params.freq==910.5f);
 }else if(scenario=="delete_ack"){
  assert(f.profiles.handle("set tempradioat2 910.5,500,8,5,rxtx,+1,+3",f.reply,160));
  f.advance(61000);assert(f.radio.state.secondary_temporary);
  f.send("del tempradioat2 1");assert(f.radio.state.secondary_temporary);
  f.drain(false);assert(f.radio.state.secondary_temporary);
  ++f.mesh.clock.now;f.send("del tempradioat2 all");f.drain(true);
  assert(!f.radio.state.enabled());
  ++f.mesh.clock.now;f.send("set radioat2 911.5,500,8,5,rxtx,+1");f.drain(true);
  ++f.mesh.clock.now;f.send("del radioat2 all");f.drain(false);
  assert(strstr(f.get("get radioat2 all"),"1@"));
  ++f.mesh.clock.now;f.send("del radioat2 1");f.drain(true);
  assert(!strcmp(f.get("get radioat2 all"),"> slots: "));
 }else if(scenario=="unsigned_no_schedule"){
  Packet packet=command(f.publisher,f.mesh.clock.now,"set radioat2 910.5,500,8,5,rxtx,+1");
  packet.payload[1]^=1;f.fleet.receive(&packet,f.mesh);
  f.fleet.service(f.mesh,f.profiles,"node",[&](uint32_t,const char*,char*){assert(false);});
  f.advance(61000);assert(!f.radio.state.enabled());assert(f.disk.files.count("/radio_profiles")==0);
 }else if(scenario=="bad_signature_no_schedule"){
  Packet packet=command(f.publisher,f.mesh.clock.now,"set radioat2 910.5,500,8,5,rxtx,+1");
  uint8_t clear[184];const int size=Utils::MACThenDecrypt(channel_key,clear,packet.payload+1,packet.payload_len-1);
  assert(size>0);clear[3+clear[2]-1]^=1;packet=raw(clear,size);
  const auto before=f.disk.files;f.fleet.receive(&packet,f.mesh);
  f.fleet.service(f.mesh,f.profiles,"node",[&](uint32_t,const char*,char*){assert(false);});
  assert(verify_calls&&f.disk.files==before);f.advance(61000);assert(!f.radio.state.enabled());
 }else if(scenario=="clock_backwards"){
  f.send("set tempradioat2 910.5,500,8,5,rxtx,+1,+2");f.drain(true);
  f.advance(61000);assert(f.radio.state.secondary_temporary);f.mesh.clock.now-=3600;
  f.advance(59000,false);assert(!f.radio.state.enabled());
 }else if(scenario=="get_slots"){
  f.send("set radioat2 910.5,500,8,5,rxtx,+1");f.drain(true);
  ++f.mesh.clock.now;f.send("get radioat2 all",false);assert(strstr(f.reply,"1@"));
  ++f.mesh.clock.now;f.send("get radioat2 1",false);assert(strstr(f.reply,"910.5"));
 }else if(scenario=="replaced_secondary_ack"||scenario=="replaced_secondary_fail"||scenario=="replaced_secondary_timeout"){
  f.send("set radioat2 910.5,500,8,5,rxtx,+1");
  // Local primary recovery cancels the old secondary transition. A newer
  // direct admin command then stages an independent, separately owned reply.
  assert(f.profiles.finishReplyMutation(false));const uint32_t generation=stageDirectProfile(f.profiles);
  if(scenario=="replaced_secondary_timeout"){
   f.advance(301000);f.fleet.service(f.mesh,f.profiles,"node",[](uint32_t,const char*,char*){assert(false);});
  }else f.drain(scenario=="replaced_secondary_ack");
  f.profiles.loop();assert(!f.fleet.waiting()&&f.profiles.hasReplyMutation());
  assert(f.profiles.replyMutationGeneration()==generation&&!f.radio.state.enabled());
  assert(f.disk.files.count("/radio_profiles")==0);
  assert(f.profiles.finishReplyMutation(true));f.profiles.loop();
  assert(!f.profiles.hasReplyMutation()&&f.radio.state.secondary.params.freq==912.5f);
 }else assert(false);
 printf("fleet scheduled radio %s passed\n",argv[1]);
}
'''

PRIMARY_EXTRA = r'''
struct PrimaryFixture {
 fs::FS disk;Mesh mesh;PhysicalRadio physical;RadioProfileCLI profiles;MyMesh node;
 FleetChannel fleet{&disk,&node};LocalIdentity publisher;unsigned dispatched=0;char reply[160]={};
 PrimaryFixture(){fake_ms=1000;node.rtc.now=mesh.clock.now;profiles.begin(&disk,&physical,&mesh.clock,true);
  enroll(fleet,publisher);bind(fleet,mesh,profiles);}
 void handle(const char* text,char* reply){
  if(!strncmp(text,"set ",4)){
   const char* config=text+4;RTC* _rtc=&node.rtc;MyMesh* _callbacks=&node;
   RadioProfileCLI& _radio_profiles=profiles;RadioPrefs* _prefs=&node._prefs;
   auto appendRxPowerSavingAdjustmentNote=[](char*,RadioPrefs*,uint8_t,float){};
   if(false){
@PRIMARY_SET@
   }else assert(false);
  }else if(!strncmp(text,"del tempradioat",15))node.deleteScheduledRadioParams(true,text+15,reply);
  else if(!strncmp(text,"del radioat",11))node.deleteScheduledRadioParams(false,text+11,reply);
  else if(!strncmp(text,"get tempradioat",15))node.formatScheduledRadioParams(true,text+15,reply);
  else if(!strncmp(text,"get radioat",11))node.formatScheduledRadioParams(false,text+11,reply);
  else assert(false);
 }
 void send(const char* text,bool mutation_expected=true){
  fake_ms+=1000;Packet packet=command(publisher,mesh.clock.now,text);fleet.receive(&packet,mesh);
  const unsigned before=dispatched;
  fleet.service(mesh,profiles,"primary",[&](uint32_t seq,const char* text,char* response){
   ++dispatched;assert(storage::readLE32(disk.files[Path].data()+56)==seq);
   assert(node.fleet_command_);handle(text,response);strcpy(reply,response);
  });
  assert(dispatched==before+1&&!node.fleet_command_);assert(!strncmp(reply,"OK",2)||!strncmp(text,"get ",4));
  assert(fleet.waiting()==mutation_expected);
 }
 void advance(uint32_t seconds,bool wall=true){
  fake_ms+=seconds*1000;node.uptime_millis+=uint64_t(seconds)*1000;node.last_millis=fake_ms;
  if(wall){node.rtc.now+=seconds;mesh.clock.now+=seconds;}node.processScheduledRadioSettings();
 }
 void drain(bool success){
  while(!mesh.manager.outbound.empty()){
   Packet* p=mesh.manager.removeOutboundByIdx(0);
   if(success)fleet.complete(p,profiles);else fleet.fail(p,profiles);
  }
 }
};
static int primary(int argc,char** argv){
 assert(argc==2);const std::string scenario=argv[1];PrimaryFixture f;
 if(scenario=="primary_permanent"){
  f.send("set radioat 910.5,500,8,5,+1");assert(f.node.countScheduledRadioSettings(false)==0);
  f.advance(61);assert(f.node.saves==0&&f.node.restores==0);f.drain(true);
  assert(f.node.saves==0&&f.node.restores==0);f.node.processScheduledRadioSettings();
  assert(f.node._prefs.freq==910.5f&&f.node.saves==1&&f.node.restores==1);
 }else if(scenario=="primary_both_copies"){
  f.mesh.fanout=true;f.send("set tempradioat 910.5,500,8,5,+1,+2");f.advance(61);
  Packet* one=f.mesh.manager.removeOutboundByIdx(0);f.fleet.complete(one,f.profiles);
  f.node.processScheduledRadioSettings();assert(!f.node.temp_radio_applied&&f.fleet.waiting());
  f.drain(true);assert(!f.node.temp_radio_applied);f.node.processScheduledRadioSettings();
  assert(f.node.temp_radio_applied&&f.node.applies==1);f.advance(59);assert(!f.node.temp_radio_applied);
 }else if(scenario=="primary_queue_failure"){
  f.mesh.queue_ok=false;f.send("set radioat 910.5,500,8,5,+1",false);
  assert(!f.node.hasFleetReplyMutation());f.advance(61);assert(f.node.saves==0&&f.node.restores==0);
 }else if(scenario=="primary_tx_failure"){
  f.send("set tempradioat 910.5,500,8,5,+1,+2");f.drain(false);f.advance(61);
  assert(!f.node.temp_radio_applied&&f.node.countScheduledRadioSettings(true)==0);
 }else if(scenario=="primary_ack_timeout"){
  f.send("set radioat 910.5,500,8,5,+1");f.advance(301);
  f.fleet.service(f.mesh,f.profiles,"primary",[](uint32_t,const char*,char*){assert(false);});
  f.node.processScheduledRadioSettings();assert(!f.fleet.waiting()&&!f.node.hasFleetReplyMutation());
  assert(f.node.saves==0&&f.node.restores==0&&f.mesh.manager.outbound.empty());
 }else if(scenario=="primary_expired_window"){
  f.send("set tempradioat 910.5,500,8,5,+1,+2");f.advance(121);f.drain(true);
  f.node.processScheduledRadioSettings();assert(!f.node.temp_radio_applied&&f.node.applies==0);
 }else if(scenario=="primary_local_busy"){
  f.send("set radioat 910.5,500,8,5,+1");
  f.handle("set radioat 911.5,500,8,5,+2",f.reply);
  assert(strstr(f.reply,"acknowledgement pending")&&f.node.countScheduledRadioSettings(false)==0);
  f.handle("del radioat all",f.reply);assert(strstr(f.reply,"acknowledgement pending"));f.drain(false);
  f.handle("set radioat 911.5,500,8,5,+2",f.reply);assert(!strncmp(f.reply,"OK",2));
  assert(f.node.countScheduledRadioSettings(false)==1);
 }else if(scenario=="primary_active_delete"){
  f.handle("set tempradioat 910.5,500,8,5,+1,+3",f.reply);f.advance(61);assert(f.node.temp_radio_applied);
  f.send("del tempradioat all");f.drain(false);f.node.processScheduledRadioSettings();assert(f.node.temp_radio_applied);
  ++f.mesh.clock.now;++f.node.rtc.now;f.send("del tempradioat 1");f.drain(true);
  assert(f.node.temp_radio_applied);f.node.processScheduledRadioSettings();assert(!f.node.temp_radio_applied);
 }else if(scenario=="primary_expiry_during_pending"){
  f.handle("set tempradioat 910.5,500,8,5,+1,+2",f.reply);f.advance(61);assert(f.node.temp_radio_applied);
  f.send("set radioat 911.5,500,8,5,+2");f.advance(60);
  assert(!f.node.temp_radio_applied&&f.node._prefs.freq==909.5f&&f.fleet.waiting());f.drain(false);
 }else if(scenario=="primary_save_failure"){
  f.send("set radioat 910.5,500,8,5,+1");f.drain(true);f.node._cli.save_success=false;f.advance(61);
  assert(f.node._prefs.freq==909.5f&&f.node.restores==0);f.node._cli.save_success=true;
  f.advance(61);assert(f.node._prefs.freq==910.5f&&f.node.restores==1);
 }else if(scenario=="primary_get_slots"){
  f.send("set radioat 910.5,500,8,5,+1");f.drain(true);f.node.processScheduledRadioSettings();
  ++f.mesh.clock.now;++f.node.rtc.now;f.send("get radioat all",false);assert(strstr(f.reply,"910.5"));f.drain(true);
  ++f.mesh.clock.now;++f.node.rtc.now;f.send("get radioat 1",false);assert(strstr(f.reply,"910.5"));
  assert(f.node.countScheduledRadioSettings(false)==1&&f.node.saves==0);
 }else if(scenario=="primary_bad_signature"){
  Packet packet=command(f.publisher,f.mesh.clock.now,"set radioat 910.5,500,8,5,+1");
  uint8_t clear[184];const int length=Utils::MACThenDecrypt(channel_key,clear,packet.payload+1,packet.payload_len-1);
  assert(length>0);clear[3+clear[2]-1]^=1;packet=raw(clear,length);const auto before=f.disk.files;
  f.fleet.receive(&packet,f.mesh);f.fleet.service(f.mesh,f.profiles,"primary",[&](uint32_t,const char*,char*){assert(false);});
  assert(verify_calls&&f.disk.files==before&&!f.node.fleet_command_&&!f.node.hasFleetReplyMutation());
  f.advance(61);assert(f.node.saves==0&&f.node.restores==0);
 }else if(scenario=="primary_recovery_supersedes_delete"){
  f.handle("set tempradioat 910.5,500,8,5,+1,+3",f.reply);f.advance(61);
  f.send("del tempradioat all");f.node.applyTempRadioParams(911.5f,500,8,5,20);
  f.drain(true);f.node.processScheduledRadioSettings();
  assert(f.node.countScheduledRadioSettings(true)==1);
  assert(f.node.scheduled_radio_settings[0].freq==911.5f);
  f.advance(3);assert(f.node.temp_radio_applied&&f.node.applies==2);
 }else if(scenario=="primary_recovery_supersedes_add"){
  f.send("set tempradioat 910.5,500,8,5,+1,+2");assert(f.node.scheduleNormalRadio());
  f.drain(true);f.node.processScheduledRadioSettings();
  assert(f.node.countScheduledRadioSettings(true)==0);f.advance(61);assert(!f.node.temp_radio_applied);
 }else if(scenario=="primary_ack_ignores_secondary"||scenario=="primary_fail_ignores_secondary"||scenario=="primary_timeout_ignores_secondary"){
  f.send("set radioat 910.5,500,8,5,+1");const uint32_t generation=stageDirectProfile(f.profiles);
  if(scenario=="primary_timeout_ignores_secondary"){
   f.advance(301);f.fleet.service(f.mesh,f.profiles,"primary",[](uint32_t,const char*,char*){assert(false);});
  }else f.drain(scenario=="primary_ack_ignores_secondary");
  f.node.processScheduledRadioSettings();f.profiles.loop();
  assert(!f.fleet.waiting()&&f.profiles.hasReplyMutation());
  assert(f.profiles.replyMutationGeneration()==generation&&!f.physical.state.enabled());
  assert(f.disk.files.count("/radio_profiles")==0);
  assert(f.profiles.finishReplyMutation(true));f.profiles.loop();
  assert(!f.profiles.hasReplyMutation()&&f.physical.state.secondary.params.freq==912.5f);
 }else assert(false);
 printf("fleet primary scheduled radio %s passed\n",argv[1]);return 0;
}
'''

class FleetRadioSchedulesTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  compiler=shutil.which("g++") or shutil.which("clang++")
  if not compiler:raise unittest.SkipTest("host C++ compiler required")
  cls.work=tempfile.TemporaryDirectory(prefix="fleet-radio-schedules-");work=Path(cls.work.name)
  (work/"helpers").mkdir()
  mesh=MESH.replace('#include <Identity.h>','#include <Hardware.h>\n#include <Identity.h>')
  mesh=mesh.replace('struct Clock {','struct Clock :RTCClock {').replace('uint32_t getCurrentTime() const{return now;}','uint32_t getCurrentTime()override{return now;}')
  utils=UTILS.replace('class Utils { public:', 'class Utils { public:\n static int parseTextParts(char*,const char*[],int,char);')
  for name,content in {"Utils.h":utils,"FS.h":FILESYSTEM,"Mesh.h":mesh,"Hardware.h":HARDWARE,
                       "helpers/RegionMap.h":REGION_MAP,
                       "Dispatcher.h":'#pragma once\n#include <Mesh.h>\n',
                       "Packet.h":'#pragma once\n#include <Mesh.h>\n',
                       "Arduino.h":'#pragma once\n#include <Mesh.h>\n#include <cstdlib>\n'}.items():
   (work/name).write_text(content)
  # Preserve production transport-key MAC algorithms with host Crypto void* parity.
  sha=ROOT/"test/mocks/SHA256.h"
  (work/"SHA256.h").write_text(f'''#pragma once\n#define SHA256 SHA256Base\n#include "{sha}"\n#undef SHA256
class SHA256:public SHA256Base{{public:
void finalize(void* out,size_t n){{SHA256Base::finalize(static_cast<uint8_t*>(out),n);}}
void finalizeHMAC(const uint8_t* key,size_t size,void* out,size_t n){{SHA256Base::finalizeHMAC(key,size,static_cast<uint8_t*>(out),n);}}
}};''')
  transport=(ROOT/"src/helpers/TransportKeyStore.cpp").read_text()
  methods="\n".join(body(transport,sig) for sig in ["uint16_t TransportKey::calcTransportCode(","bool TransportKey::isNull("])
  prefix=RUNTIME_HARNESS[:RUNTIME_HARNESS.index('static unsigned apply(')]
  source=(ROOT/"examples/simple_repeater/MyMesh.cpp").read_text()
  primary=PRIMARY_HARNESS[:PRIMARY_HARNESS.index('static constexpr uint32_t HOUR')]
  primary=primary.replace('static uint32_t now_ms = 1;\nuint32_t millis() { return now_ms; }','#define now_ms fake_ms')
  primary=primary.replace('struct Packet {};','').replace('class MyMesh {','class MyMesh :public FleetReplyHooks {')
  primary=primary.replace('struct Radio { uint32_t last_rx = 0; uint32_t getLastRecvMillis() { return last_rx; } };',
   'struct PrimaryPHY { uint32_t last_rx=0; mesh::RadioProfiles state; uint32_t getLastRecvMillis(){return last_rx;} mesh::RadioProfiles* profiles(){return &state;} };')
  primary=primary.replace('Radio radio; Radio* _radio','PrimaryPHY radio; PrimaryPHY* _radio')
  primary=primary.replace('  MyMesh() {',r'''
  ScheduledRadioSetting fleet_schedule_setting_{};
  uint8_t fleet_schedule_delete_mask_[(MAX_SCHEDULED_RADIO_SETTINGS+7)/8]{};
  int fleet_schedule_slot_=-1;
  bool fleet_command_=false,fleet_schedule_pending_=false,fleet_schedule_delivered_=false,fleet_schedule_delete_all_temp_=false;
  void beginFleetCommand()override{fleet_command_=true;}
  void endFleetCommand()override{fleet_command_=false;}
  bool hasFleetReplyMutation()const override{return fleet_schedule_pending_;}
  void finishFleetReplyMutation(bool)override;
  void serviceFleetScheduleReply();
  int findScheduledRadioSettingByIndex(bool,int)const;
  void deleteScheduledRadioParams(bool,const char*,char*);
  void formatScheduledRadioParams(bool,const char*,char*);
  void formatScheduledRadioSetting(char*,int,int)const;
  void formatRadioParamTuple(char*,size_t,const ScheduledRadioSetting&)const;
  MyMesh() {''')
  signatures=[
   'static const char* skipLocalSpaces(', 'static bool selectorIsEmpty(',
   'static bool selectorIsAll(', 'static bool parsePositiveSelector(', 'static void formatFixed3(',
   'void MyMesh::checkRxInactivityWatchdog()', 'void MyMesh::setTempRadioTiming(',
   'void MyMesh::updateAdvertTimer()', 'void MyMesh::updateFloodAdvertTimer()',
   'void MyMesh::queueSavedRadioApply()', 'bool MyMesh::applySavedRadioParams()',
   'void MyMesh::refreshScheduledRadioState()', 'void MyMesh::processScheduledRadioSettings()',
   'void MyMesh::applyTempRadioParams(', 'bool MyMesh::scheduleNormalRadio()',
   'void MyMesh::clearScheduledRadioSetting(', 'int MyMesh::findFreeScheduledRadioSlot() const',
   'int MyMesh::countScheduledRadioSettings(', 'bool MyMesh::scheduledRadioConflicts(',
   'void MyMesh::addScheduledRadioParams(', 'int MyMesh::findScheduledRadioSettingByIndex(',
   'void MyMesh::deleteScheduledRadioParams(', 'void MyMesh::formatScheduledRadioParams(',
   'void MyMesh::formatScheduledRadioSetting(', 'void MyMesh::formatRadioParamTuple(',
   'void MyMesh::finishFleetReplyMutation(', 'void MyMesh::serviceFleetScheduleReply(',
  ]
  primary=primary.replace('@METHODS@','\n'.join(body(source,sig) for sig in signatures))
  cli=(ROOT/'src/helpers/CommonCLI.cpp').read_text()
  parsers='\n'.join(body(cli,sig) for sig in [
   'static bool looksUnsignedInteger(', 'static const char* skipSpacesConst(',
   'static bool parseUint32Strict(', 'static bool bwMatches(', 'static bool isValidLoRaBandwidth(',
   'static int countSeparatedParts(', 'static bool parseScheduledRadioArgs(',
  ])
  parts=body((ROOT/'src/Utils.cpp').read_text(),'int Utils::parseTextParts(')
  formatting='''\nstruct StrHelper {
static void strncpy(char* dst,const char* src,size_t n){if(n){size_t len=strlen(src);if(len>=n)len=n-1;memcpy(dst,src,len);dst[len]=0;}}
static const char* ftoa3(float value){static char text[32];snprintf(text,sizeof(text),"%.3f",double(value));return text;}
};
uint16_t rxPowerSavingPreambleForParams(uint8_t,float){return 32;}
'''
  parsing='\n#include <helpers/CLICommandUtils.h>\nnamespace mesh {\n'+parts+'\n}\n'+parsers
  start=cli.index('  } else if (memcmp(config, "radioat ", 8) == 0)')
  end=cli.index('  } else if (memcmp(config, "lat ", 4) == 0)',start)
  primary_extra=PRIMARY_EXTRA.replace('@PRIMARY_SET@',cli[start:end])
  physical,secondary=HARNESS.split('struct Fixture {',1)
  secondary=('struct Fixture {'+secondary).replace(' assert(argc==2);const std::string scenario=argv[1];Fixture f;',
   ' assert(argc==2);if(!strncmp(argv[1],"primary_",8))return primary(argc,argv);const std::string scenario=argv[1];Fixture f;')
  fixture=work/"fixture.cpp";fixture.write_text('#define MESH_ENABLE_FLEET_CONTROL 1\n'+
   prefix.replace("@TRANSPORT_METHODS@",methods)+physical+formatting+primary+parsing+primary_extra+secondary)
  cls.binary=work/"schedule-test"
  sanitizer=["-fsanitize=address,undefined","-fno-omit-frame-pointer","-no-pie"] if os.environ.get("MESHCORE_FLEET_SANITIZERS")=="1" else []
  built=subprocess.run([compiler,"-std=c++17","-O2","-g","-DRP2040_PLATFORM",*sanitizer,
    "-Wall","-Wextra","-Werror","-Wno-unused-function","-Wno-unused-parameter","-Wno-sign-compare","-Wno-misleading-indentation",
    *(["-Wno-format-truncation"] if "g++" in compiler else []),
    "-I",str(work),"-isystem",str(ROOT/"test/mocks"),"-I",str(ROOT/"src"),str(fixture),
    str(ROOT/"src/helpers/FleetCommand.cpp"),str(ROOT/"src/helpers/FleetChannel.cpp"),
    str(ROOT/"src/helpers/RadioProfileCLI.cpp"),"-lcrypto","-o",str(cls.binary)],capture_output=True,text=True,timeout=60)
  if built.returncode:cls.work.cleanup();raise AssertionError(built.stdout+built.stderr)
 @classmethod
 def tearDownClass(cls):cls.work.cleanup()
 def scenario(self,name):
  result=subprocess.run([str(self.binary),name],capture_output=True,text=True,timeout=10)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_permanent_schedule_waits_for_ack_then_persists_and_rejects_replay(self):self.scenario("permanent_ack")
 def test_temporary_schedule_waits_for_ack_and_restores_at_original_end(self):self.scenario("temporary_ack")
 def test_failed_ack_does_not_add_any_schedule(self):self.scenario("ack_fail")
 def test_failed_queue_does_not_add_a_schedule(self):self.scenario("queue_fail")
 def test_secondary_ack_timeout_removes_packet_and_cancels_schedule(self):self.scenario("ack_timeout")
 def test_secondary_schedule_waits_for_both_physical_ack_copies(self):self.scenario("both_copies")
 def test_delayed_ack_cannot_resurrect_an_expired_temporary_lease(self):self.scenario("expired_lease")
 def test_due_permanent_schedule_keeps_old_radio_until_atomic_save_succeeds(self):self.scenario("save_fail")
 def test_delete_active_or_future_schedule_is_delayed_until_ack_and_cancelled_on_failure(self):self.scenario("delete_ack")
 def test_invalid_mac_never_reaches_actual_scheduler(self):self.scenario("unsigned_no_schedule")
 def test_valid_mac_invalid_signature_cannot_reserve_or_schedule(self):self.scenario("bad_signature_no_schedule")
 def test_backward_clock_correction_cannot_extend_temporary_lease(self):self.scenario("clock_backwards")
 def test_get_all_and_individual_slot_reply_keeps_schedule_and_needs_no_barrier(self):self.scenario("get_slots")
 def test_primary_permanent_schedule_runs_only_after_ack_and_deferred_loop(self):self.scenario("primary_permanent")
 def test_primary_waits_for_both_physical_ack_copies_and_keeps_original_lease_end(self):self.scenario("primary_both_copies")
 def test_primary_queue_failure_cancels_staged_schedule(self):self.scenario("primary_queue_failure")
 def test_primary_failed_tx_cancels_staged_schedule(self):self.scenario("primary_tx_failure")
 def test_primary_ack_timeout_removes_packet_and_cancels_schedule(self):self.scenario("primary_ack_timeout")
 def test_primary_expired_window_is_discarded_after_late_ack(self):self.scenario("primary_expired_window")
 def test_primary_local_schedule_edits_are_busy_until_fleet_ack_finishes(self):self.scenario("primary_local_busy")
 def test_primary_active_delete_waits_for_ack_and_deferred_loop(self):self.scenario("primary_active_delete")
 def test_primary_older_lease_expires_even_while_fleet_ack_is_pending(self):self.scenario("primary_expiry_during_pending")
 def test_primary_permanent_radio_keeps_old_tuple_when_save_fails(self):self.scenario("primary_save_failure")
 def test_primary_get_list_and_slot_do_not_mutate_schedule(self):self.scenario("primary_get_slots")
 def test_primary_signed_handler_never_runs_for_forged_signature(self):self.scenario("primary_bad_signature")
 def test_primary_direct_recovery_is_not_deleted_by_an_older_fleet_ack(self):self.scenario("primary_recovery_supersedes_delete")
 def test_primary_return_to_saved_radio_cancels_older_pending_fleet_schedule(self):self.scenario("primary_recovery_supersedes_add")
 def test_canceled_secondary_fleet_ack_does_not_release_new_direct_admin_mutation(self):self.scenario("replaced_secondary_ack")
 def test_canceled_secondary_fleet_failure_does_not_cancel_new_direct_admin_mutation(self):self.scenario("replaced_secondary_fail")
 def test_canceled_secondary_fleet_timeout_does_not_cancel_new_direct_admin_mutation(self):self.scenario("replaced_secondary_timeout")
 def test_primary_only_fleet_ack_does_not_release_new_direct_secondary_mutation(self):self.scenario("primary_ack_ignores_secondary")
 def test_primary_only_fleet_failure_does_not_cancel_new_direct_secondary_mutation(self):self.scenario("primary_fail_ignores_secondary")
 def test_primary_only_fleet_timeout_does_not_cancel_new_direct_secondary_mutation(self):self.scenario("primary_timeout_ignores_secondary")

if __name__=="__main__":unittest.main()
