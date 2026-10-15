"""Exercise actual Companion Ethernet settings/lifecycle at host boundaries.

No PlatformIO, hardware, network listener or NVS device is used. The portable
settings image and extracted Companion method bodies remain production code.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]


SETTINGS_HARNESS = r'''
#include <cassert>
#include <cmath>
#include <cstring>
#include <initializer_list>
#include <limits>
#include <helpers/EthernetTunnelSettings.h>
using namespace mesh;
using namespace mesh::ethernet;

static void checksum(uint8_t* bytes) {
  const uint32_t crc=settingsChecksum(bytes,SettingsSize-4);
  memcpy(bytes+SettingsSize-4,&crc,sizeof(crc));
}
static Settings configured() {
  Settings value;
  value.configured=true;
  value.radio.freq=909.5;
  value.radio.bw=500;
  value.radio.sf=5;
  value.radio.cr=5;
  value.radio.preamble=32;
  return value;
}
int main() {
  uint8_t bytes[SettingsSize];
  Settings value,loaded;
  assert(validSettings(value));
  encodeSettings(bytes,value);
  assert(decodeSettings(bytes,sizeof(bytes),loaded));
  assert(!loaded.enabled && !loaded.configured);
  assert(loaded.radio.protocol==RadioProfileProtocol::Ethernet);
  assert(!decodeSettings(nullptr,sizeof(bytes),loaded));
  for (size_t size=0;size<SettingsSize;++size) assert(!decodeSettings(bytes,size,loaded));
  assert(!decodeSettings(bytes,SettingsSize+1,loaded));
  value=configured(); value.enabled=true;
  encodeSettings(bytes,value);
  assert(decodeSettings(bytes,sizeof(bytes),loaded));
  assert(loaded.enabled && loaded.configured && loaded.radio==value.radio);
  const auto original=loaded;
  for(size_t at=0;at<sizeof(bytes);++at) {
    bytes[at]^=1;
    assert(!decodeSettings(bytes,sizeof(bytes),loaded));
    assert(loaded.enabled==original.enabled && loaded.configured==original.configured
        && loaded.radio==original.radio);
    bytes[at]^=1;
  }
  for(size_t at : {size_t(4),size_t(5)}) {
    bytes[at]=2; checksum(bytes); assert(!decodeSettings(bytes,sizeof(bytes),loaded));
    encodeSettings(bytes,value);
  }
  for(size_t at=18;at<SettingsSize-4;++at) {
    bytes[at]=1; checksum(bytes); assert(!decodeSettings(bytes,sizeof(bytes),loaded));
    encodeSettings(bytes,value);
  }
  value.configured=false;
  assert(!validSettings(value));
  encodeSettings(bytes,value); assert(!decodeSettings(bytes,sizeof(bytes),loaded));
  value=configured();
  for(float bad : {0.f,149.f,2501.f,std::numeric_limits<float>::infinity(),
                  std::numeric_limits<float>::quiet_NaN()}) {
    value.radio.freq=bad; assert(!validSettings(value));
    encodeSettings(bytes,value); assert(!decodeSettings(bytes,sizeof(bytes),loaded));
  }
  value=configured();
  for(float bad : {0.f,124.f,std::numeric_limits<float>::infinity(),
                  std::numeric_limits<float>::quiet_NaN()}) {
    value.radio.bw=bad; assert(!validSettings(value));
    encodeSettings(bytes,value); assert(!decodeSettings(bytes,sizeof(bytes),loaded));
  }
  value=configured();
  for(uint16_t bad : {0,1,7,65529,65535}) {
    value.radio.preamble=bad; assert(!validSettings(value));
    encodeSettings(bytes,value); assert(!decodeSettings(bytes,sizeof(bytes),loaded));
  }
  value=configured(); value.radio.protocol=RadioProfileProtocol::MeshCore;
  assert(!validSettings(value));
  // Foreign profiles bypass MeshCore's scan-preamble recommendation, not the
  // RadioLib 32-bit microsecond airtime bound. Reject even a CRC-valid tuple
  // that would overflow that clock before it can be persisted or used at boot.
  value=configured(); value.radio.bw=7.8f; value.radio.sf=12;
  value.radio.preamble=65528;
  assert(!validSettings(value));
  encodeSettings(bytes,value); assert(!decodeSettings(bytes,sizeof(bytes),loaded));
  value=configured();
  for(uint8_t bad : {0,4,13,255}) {
    value.radio.sf=bad; assert(!validSettings(value));
    encodeSettings(bytes,value); assert(!decodeSettings(bytes,sizeof(bytes),loaded));
  }
  value=configured();
  for(uint8_t bad : {0,4,9,255}) {
    value.radio.cr=bad; assert(!validSettings(value));
    encodeSettings(bytes,value); assert(!decodeSettings(bytes,sizeof(bytes),loaded));
  }
}
'''


COMPANION_HARNESS = r'''
@RADIO_FIXTURE@
#include <helpers/EthernetTunnelSettings.h>
#include <helpers/EthernetKiss.h>
#include <helpers/CLICommandUtils.h>
#include <helpers/CompanionChannelAlias.h>
#include <deque>
#include <vector>

struct Preferences {
  static inline std::vector<uint8_t> blob;
  static inline bool fail_begin=false,fail_write=false,fail_read=false,fail_remove=false;
  static inline unsigned writes=0;
  bool begin(const char* name,bool read_only) {
    assert(!strcmp(name,"mesh-eth") && !read_only); return !fail_begin;
  }
  size_t getBytesLength(const char* key) { assert(!strcmp(key,"settings")); return blob.size(); }
  size_t getBytes(const char*,void* out,size_t size) {
    if(fail_read)return 0;
    size=std::min(size,blob.size()); memcpy(out,blob.data(),size); return size;
  }
  size_t putBytes(const char*,const void* in,size_t size) {
    ++writes; if(fail_write)return 0;
    const auto* bytes=static_cast<const uint8_t*>(in); blob.assign(bytes,bytes+size); return size;
  }
  bool remove(const char*) { if(fail_remove)return false; blob.clear(); return true; }
  void end() {}
  static void reset() { blob.clear(); writes=0; fail_begin=fail_write=fail_read=fail_remove=false; }
};

namespace mesh { namespace ethernet {
// Socket lifecycle is a boundary substitute. Every byte in both directions
// still traverses the production bounded KISS codec.
class KissServer {
  KissCodec codec;
public:
  static constexpr uint16_t Port=5003;
  struct Status { bool enabled=false,listening=false,connected=false; uint32_t accepted=0,disconnects=0; };
  Status state;
  bool wifi=true;
  bool attach_pending=false,detach_pending=false;
  unsigned resets=0;
  void resetPending() {
    ++resets;
    if(state.connected)++state.disconnects;
    state.connected=false;
    codec.resetPending();
  }
  void loop(bool enabled) {
    state.enabled=enabled; state.listening=enabled && wifi;
    if(!state.listening) {resetPending();attach_pending=detach_pending=false;return;}
    if(detach_pending) {resetPending();detach_pending=false;}
    if(attach_pending) {codec.resetPending();state.connected=true;++state.accepted;attach_pending=false;}
  }
  Status status() const { return state; }
  void connect() { assert(state.listening);attach_pending=true; }
  void disconnect() { detach_pending=true; }
  bool peekTransmit(const uint8_t*& bytes,size_t& size) const { return codec.peekTransmit(bytes,size); }
  void consumeTransmit() { codec.consumeTransmit(); }
  bool queueReceive(const uint8_t* bytes,size_t size) { return state.connected && codec.queueReceive(bytes,size); }
  void feed(const std::vector<uint8_t>& frame) {
    assert(state.connected);
    for(uint8_t byte:frame)assert(codec.feed(byte));
  }
  std::vector<uint8_t> output() {
    const uint8_t* bytes;size_t size;
    if(!codec.peekOutput(bytes,size))return {};
    std::vector<uint8_t> out(bytes,bytes+size);codec.consumeOutput(size);return out;
  }
};
} }

static constexpr int MAX_GROUP_CHANNELS=4;
struct ChannelDetails {char name[32]={};};
struct BaseChatMesh {
  ChannelDetails channels[MAX_GROUP_CHANNELS];
  unsigned channel_reads=0;
  bool getChannel(int index,ChannelDetails& out) {++channel_reads;out=channels[index];return true;}
  bool setChannel(int index,const ChannelDetails& value) {channels[index]=value;return true;}
};
struct EthernetRadio : Radio {
  bool supported=true;
  mutable unsigned validations=0;
  bool supportsEthernetTunnel() const override {return supported;}
  bool validateProfile(const mesh::RadioProfileParams& value) const override {
    ++validations;return Radio::validateProfile(value);
  }
};
class MyMesh : public BaseChatMesh {
public:
  MemoryFS fs;Clock clock;EthernetRadio radio;mesh::Radio* _radio=&radio;
  mesh::RadioProfileCLI _radio_profiles;
  mesh::ethernet::Settings _ethernet_settings;
  bool _ethernet_settings_valid=true,_ethernet_active=false,_ethernet_owned=false;
  uint32_t _ethernet_generation=0;
  uint32_t _ethernet_intent_generation=0,_ethernet_channel_generation=0;
  uint32_t _ethernet_checked_channels=UINT32_MAX;
  bool _ethernet_channels_conflict=false;
  const char* _ethernet_status="off";
  mesh::ethernet::KissServer _ethernet_kiss;
  uint32_t _ethernet_received=0,_ethernet_sent=0,_ethernet_failed=0;
  bool _radio_available=true,foreign_pending=false,admit_foreign=true;
  mesh::RadioProfileProtocol foreign_protocol=mesh::RadioProfileProtocol::MeshCore;
  uint8_t foreign_profile=255;
  uint32_t foreign_generation=0;
  unsigned admissions=0,discards=0;
  std::vector<uint8_t> foreign_bytes;
  MyMesh() {g_mock_millis=0;_radio_profiles.begin(&fs,&radio,&clock);}
  void beginEthernetTunnel();
  bool ethernetChannelConflict();
  void releaseEthernetProfile();
  bool saveEthernetSettings(const mesh::ethernet::Settings&);
  bool eraseEthernetSettings();
  bool ethernetRadioConflict(const mesh::ethernet::Settings&);
  bool handleEthernetTunnelCommand(const char*,char*,size_t);
  void loopEthernetTunnel();
  bool onEthernetFrame(const uint8_t*,size_t,uint8_t,uint32_t);
  bool setChannel(int,const ChannelDetails&);
  bool onForeignRadioFrame(const uint8_t*,size_t,uint8_t,uint32_t,float,float);
  void onForeignRadioSendComplete(bool);
  mesh::RadioProfileProtocol queuedForeignRadioProtocol() const {return foreign_protocol;}
  bool hasForeignRadioFrame() const {return foreign_pending;}
  void discardQueuedForeignRadioFrame(mesh::RadioProfileProtocol protocol) {
    if(foreign_pending && protocol==foreign_protocol) {foreign_pending=false;++discards;}
  }
  bool queueForeignRadioFrame(const uint8_t* bytes,size_t size,uint8_t profile,uint32_t generation,
                             mesh::RadioProfileProtocol protocol) {
    if(foreign_pending || !admit_foreign)return false;
    foreign_pending=true;++admissions;foreign_bytes.assign(bytes,bytes+size);
    foreign_profile=profile;foreign_generation=generation;foreign_protocol=protocol;return true;
  }
  const char* command(const char* text) {
    static char reply[256];memset(reply,0x55,sizeof(reply));
    assert(handleEthernetTunnelCommand(text,reply,sizeof(reply)));
    assert(memchr(reply,0,sizeof(reply)));return reply;
  }
  void cli(const char* text) {
    char reply[160];assert(_radio_profiles.handle(text,reply,sizeof(reply)));
    if(strncmp(reply,"OK",2)) {fprintf(stderr,"%s -> %s\n",text,reply);assert(false);}
    g_mock_millis+=2000;clock.epoch+=2;_radio_profiles.loop();
  }
};
@METHODS@

static void ok(MyMesh& node,const char* text) {assert(!strncmp(node.command(text),"OK",2));}
static void error(MyMesh& node,const char* text) {assert(!strncmp(node.command(text),"Error",5));}
static void active(MyMesh& node) {
  node.beginEthernetTunnel();
  ok(node,"set ethernet.radio 909.5,500,5,5");
  ok(node,"set ethernet on");node.loopEthernetTunnel();
  assert(node._ethernet_active && node._ethernet_owned && node.radio.p.enabled());
  assert(node.radio.p.secondary.params.protocol==mesh::RadioProfileProtocol::Ethernet);
}
static std::vector<uint8_t> ethernet(uint16_t type=0x0800,size_t size=244) {
  std::vector<uint8_t> frame(size);
  for(size_t i=0;i<size;++i)frame[i]=static_cast<uint8_t>(i);
  frame[12]=type>>8;frame[13]=type&255;frame[14]=0xc0;frame[15]=0xdb;
  return frame;
}
static std::vector<uint8_t> kiss(const std::vector<uint8_t>& ethernet) {
  std::vector<uint8_t> out={0xc0,0};
  for(auto byte:ethernet) {
    if(byte==0xc0)out.insert(out.end(),{0xdb,0xdc});
    else if(byte==0xdb)out.insert(out.end(),{0xdb,0xdd});
    else out.push_back(byte);
  }
  out.push_back(0xc0);return out;
}
static void defaults_cli_and_reboot() {
  Preferences::reset();MyMesh node;node.beginEthernetTunnel();node.loopEthernetTunnel();
  assert(node._ethernet_settings_valid && !node._ethernet_active && !node.radio.p.enabled());
  assert(strstr(node.command("get ethernet"),"ethernet=off"));
  assert(strstr(node.command("get ethernet.radio"),"unset"));
  error(node,"set ethernet on");assert(Preferences::writes==0);
  for(const char* bad:{"set ethernet","set ethernet ON","set ethernet on extra",
      "set ethernet.radio","set ethernet.radio 909.5,500,5",
      "set ethernet.radio 909.5,500,5,5,garbage","set ethernet.radio 909.5,500,4,5",
      "set ethernet.radio 909.5,500,5,5,7","set ethernet.radio 0,500,5,5"})error(node,bad);
  assert(Preferences::writes==0);
  char reply[8];assert(!node.handleEthernetTunnelCommand("get ethernetx",reply,sizeof(reply)));
  assert(!node.handleEthernetTunnelCommand("set ethernetx on",reply,sizeof(reply)));
  ok(node,"set ethernet.radio 909.5,500,5,5");
  assert(node._ethernet_settings.radio.preamble==32 && !node._ethernet_settings.enabled);
  ok(node,"set ethernet on");node.loopEthernetTunnel();
  const auto generation=node.radio.p.generation[1];
  assert(node._ethernet_active && node._ethernet_kiss.status().listening);
  assert(node._ethernet_settings.radio.protocol==mesh::RadioProfileProtocol::Ethernet);
  assert(node.fs.files.empty());
  const auto validations=node.radio.validations;
  const auto scans=node.channel_reads;
  for(int i=0;i<100;++i)node.loopEthernetTunnel();
  assert(node.radio.p.generation[1]==generation && Preferences::writes==2);
  assert(node.radio.validations==validations && node.channel_reads==scans);
  for(const char* command:{"get ethernet","get ethernet.radio","get ethernet.status"}) {
    char bounded[4]={'a','b','c','d'};
    assert(node.handleEthernetTunnelCommand(command,bounded+1,1));
    assert(bounded[0]=='a' && bounded[1]==0 && bounded[2]=='c' && bounded[3]=='d');
    bounded[1]='b';assert(node.handleEthernetTunnelCommand(command,bounded+1,0));
    assert(bounded[0]=='a' && bounded[1]=='b' && bounded[2]=='c' && bounded[3]=='d');
  }
  MyMesh reboot;reboot.beginEthernetTunnel();reboot.loopEthernetTunnel();
  assert(reboot._ethernet_active && reboot._ethernet_settings.radio==node._ethernet_settings.radio);
  ok(node,"set ethernet off");node.loopEthernetTunnel();
  assert(!node._ethernet_active && !node.radio.p.enabled() && !node._ethernet_kiss.status().listening);
  MyMesh off;off.beginEthernetTunnel();off.loopEthernetTunnel();assert(!off._ethernet_active);
}
static void persistence_faults() {
  for(int fault=0;fault<3;++fault) {
    Preferences::reset();MyMesh node;active(node);
    const auto before=Preferences::blob;
    if(fault==0)Preferences::fail_begin=true;
    if(fault==1)Preferences::fail_write=true;
    if(fault==2)Preferences::fail_read=true;
    error(node,"set ethernet off");
    if(fault==0)assert(node._ethernet_active && node._ethernet_settings_valid);
    else assert(!node._ethernet_active && !node._ethernet_settings_valid && !node.radio.p.enabled());
    Preferences::fail_begin=Preferences::fail_write=Preferences::fail_read=false;
    if(fault<2)assert(Preferences::blob==before);
    MyMesh reboot;reboot.beginEthernetTunnel();reboot.loopEthernetTunnel();
    assert(reboot._ethernet_active==(fault!=2));
  }
  Preferences::reset();MyMesh source;active(source);const auto good=Preferences::blob;
  for(size_t at=0;at<good.size();++at) {
    Preferences::blob=good;Preferences::blob[at]^=1;
    MyMesh boot;boot.beginEthernetTunnel();boot.loopEthernetTunnel();
    assert(!boot._ethernet_settings_valid && !boot._ethernet_active && !boot.radio.p.enabled());
  }
  for(size_t size:{size_t(1),size_t(31),size_t(33)}) {
    Preferences::blob=good;Preferences::blob.resize(size);
    MyMesh boot;boot.beginEthernetTunnel();boot.loopEthernetTunnel();assert(!boot._ethernet_active);
  }
  Preferences::blob=good;MyMesh erase;erase.beginEthernetTunnel();erase.loopEthernetTunnel();
  Preferences::fail_remove=true;assert(!erase.eraseEthernetSettings());
  assert(!erase._ethernet_active && !erase._ethernet_settings_valid);
  Preferences::fail_remove=false;assert(erase.eraseEthernetSettings());
  assert(Preferences::blob.empty() && !erase._ethernet_settings.enabled && erase._ethernet_settings_valid);
  MyMesh reboot;reboot.beginEthernetTunnel();reboot.loopEthernetTunnel();assert(!reboot._ethernet_active);
}
static void profile_conflicts_and_recovery() {
  for(const char* name:{"#r2-emergency","#mt-longfast"}) {
    Preferences::reset();MyMesh node;node.beginEthernetTunnel();
    ok(node,"set ethernet.radio 909.5,500,5,5,32");strcpy(node.channels[0].name,name);++node._ethernet_channel_generation;
    error(node,"set ethernet on");assert(!node._ethernet_active);
    node.channels[0].name[0]=0;++node._ethernet_channel_generation;ok(node,"set ethernet on");node.loopEthernetTunnel();
    assert(node._ethernet_active);strcpy(node.channels[0].name,name);++node._ethernet_channel_generation;node.loopEthernetTunnel();
    assert(!node._ethernet_active && !node.radio.p.enabled());
    node.channels[0].name[0]=0;++node._ethernet_channel_generation;node.loopEthernetTunnel();assert(node._ethernet_active);
  }
  for(const char* tuple:{"set radio2 off","set radio2 909.5,500,5,5,rx,32",
      "set radio2 909.6,500,5,5,rxtx,32","set radio2 909.5,500,5,5,rxtx,40"}) {
    Preferences::reset();MyMesh node;node.beginEthernetTunnel();node.cli(tuple);
    const auto saved=node.radio.p.secondary;
    ok(node,"set ethernet.radio 909.5,500,5,5,32");error(node,"set ethernet on");
    assert(node.radio.p.secondary.mode==saved.mode && node.radio.p.secondary.params==saved.params);
  }
  Preferences::reset();MyMesh manual;manual.beginEthernetTunnel();
  manual.cli("set radio2 909.5,500,5,5,rxtx,32");const auto saved_files=manual.fs.files;
  ok(manual,"set ethernet.radio 909.5,500,5,5,32");ok(manual,"set ethernet on");manual.loopEthernetTunnel();
  assert(manual._ethernet_active && manual.fs.files==saved_files);
  ok(manual,"set ethernet off");manual.loopEthernetTunnel();
  assert(manual.radio.p.secondary.mode==mesh::RadioProfileMode::RxTx
      && manual.radio.p.secondary.params.protocol==mesh::RadioProfileProtocol::MeshCore);
  assert(manual.fs.files==saved_files);
  Preferences::reset();MyMesh unavailable;active(unavailable);
  unavailable._radio_available=false;unavailable.loopEthernetTunnel();assert(!unavailable._ethernet_active);
  unavailable._radio_available=true;unavailable.loopEthernetTunnel();assert(unavailable._ethernet_active);
  unavailable.radio.supported=false;unavailable.loopEthernetTunnel();assert(!unavailable._ethernet_active);
  unavailable.radio.supported=true;unavailable.loopEthernetTunnel();assert(unavailable._ethernet_active);
  unavailable._ethernet_kiss.wifi=false;unavailable.loopEthernetTunnel();
  assert(unavailable._ethernet_active && !unavailable._ethernet_kiss.status().listening);
  unavailable._ethernet_kiss.wifi=true;unavailable.loopEthernetTunnel();
  assert(unavailable._ethernet_kiss.status().listening);
}
static void opaque_frames_and_no_primary_fallback() {
  Preferences::reset();MyMesh node;active(node);node._ethernet_kiss.connect();node.loopEthernetTunnel();
  for(uint16_t type:{0x0800,0x0806,0x86dd,0x1234}) {
    const auto frame=ethernet(type);
    node._ethernet_kiss.feed(kiss(frame));node.admit_foreign=false;node.loopEthernetTunnel();
    assert(!node.foreign_pending);node.admit_foreign=true;node.loopEthernetTunnel();
    assert(node.foreign_pending && node.foreign_bytes==frame && node.foreign_profile==1);
    assert(node.foreign_protocol==mesh::RadioProfileProtocol::Ethernet);
    assert(node.foreign_generation==node.radio.p.generation[1]);
    error(node,"set ethernet off");assert(node._ethernet_active);
    node.foreign_pending=false;
    assert(node.onEthernetFrame(frame.data(),frame.size(),1,node.radio.p.generation[1]));
    assert(node._ethernet_kiss.output()==kiss(frame));
    assert(!node.onEthernetFrame(frame.data(),frame.size(),0,node.radio.p.generation[0]));
    assert(!node.onEthernetFrame(frame.data(),frame.size(),1,node.radio.p.generation[1]-1));
  }
  assert(node.admissions==4 && node._ethernet_received==4);
  node.foreign_protocol=mesh::RadioProfileProtocol::Ethernet;
  node.onForeignRadioSendComplete(true);node.onForeignRadioSendComplete(false);
  assert(node._ethernet_sent==1 && node._ethernet_failed==1);
  node.foreign_protocol=mesh::RadioProfileProtocol::Meshtext;
  node.onForeignRadioSendComplete(true);node.onForeignRadioSendComplete(false);
  assert(node._ethernet_sent==1 && node._ethernet_failed==1);
  for(size_t size:{size_t(13),size_t(256)}) {
    const std::vector<uint8_t> invalid(size,0x11);
    assert(!node.onEthernetFrame(invalid.data(),invalid.size(),1,node.radio.p.generation[1]));
  }
  const auto frame=ethernet();node._ethernet_kiss.feed(kiss(frame));node.loopEthernetTunnel();
  const auto admissions=node.admissions;assert(node.foreign_pending);
  node._radio_available=false;node.loopEthernetTunnel();
  assert(!node.foreign_pending && node.discards>=1 && node.admissions==admissions && !node._ethernet_active);
  node._radio_available=true;node.loopEthernetTunnel();
  assert(node._ethernet_active && !node._ethernet_kiss.status().connected);
  assert(!node.onEthernetFrame(frame.data(),frame.size(),1,node.radio.p.generation[1]));
  node._ethernet_kiss.connect();node.loopEthernetTunnel();
  node._ethernet_kiss.feed(kiss(frame));node.loopEthernetTunnel();assert(node.foreign_pending);
  node._ethernet_kiss.disconnect();node.loopEthernetTunnel();
  assert(!node.foreign_pending && !node._ethernet_kiss.status().connected);
  node._ethernet_kiss.connect();node.loopEthernetTunnel();
  node._ethernet_kiss.feed(kiss(frame));node.loopEthernetTunnel();assert(node.foreign_pending);
  assert(node.eraseEthernetSettings());assert(!node.foreign_pending && !node._ethernet_active);
}
static void temporary_and_scheduled_ownership() {
  Preferences::reset();MyMesh node;active(node);
  node.cli("set tempradio2 910.5,500,5,5,rxtx,1,32");node.loopEthernetTunnel();
  assert(!node._ethernet_active && node.radio.p.secondary_temporary
      && node.radio.p.secondary.params.freq==910.5f);
  const auto temporary=node.radio.p.secondary.params;
  error(node,"set ethernet on");assert(node.radio.p.secondary.params==temporary);
  node.cli("set tempradio2 off");node.loopEthernetTunnel();assert(node._ethernet_active);
  char command[128];
  snprintf(command,sizeof(command),"set radioat2 911.5,500,5,5,rxtx,%lu,32",
      static_cast<unsigned long>(node.clock.epoch+120));
  node.cli(command);node.loopEthernetTunnel();assert(!node._ethernet_active);
  assert(!node.radio.p.enabled());error(node,"set ethernet on");
  node.cli("del radioat2 all");node.loopEthernetTunnel();assert(node._ethernet_active);
}
static void unused_meshtext_owner_handoff() {
  Preferences::reset();MyMesh node;node.beginEthernetTunnel();
  mesh::RadioProfileConfig old;
  old.mode=mesh::RadioProfileMode::RxTx;old.params.freq=906.875;old.params.bw=250;
  old.params.sf=11;old.params.cr=5;old.params.preamble=16;
  old.params.protocol=mesh::RadioProfileProtocol::Meshtext;
  assert(node._radio_profiles.requestAutoSecondary(old));
  assert(node._radio_profiles.autoSecondaryActive());
  ok(node,"set ethernet.radio 909.5,500,5,5,32");
  assert(node.radio.p.secondary.params.protocol==mesh::RadioProfileProtocol::Meshtext);
  ok(node,"set ethernet on");node.loopEthernetTunnel();
  assert(node._ethernet_active && node.radio.p.secondary.params.protocol==mesh::RadioProfileProtocol::Ethernet);
}
static void real_channel_setter_invalidates_cache_without_evicting() {
  Preferences::reset();MyMesh node;active(node);
  const auto generation=node._ethernet_channel_generation;
  ChannelDetails candidate;
  for(const char* name:{"#r2-emergency","#mt-longfast","#r2-"}) {
    strcpy(candidate.name,name);assert(!node.setChannel(0,candidate));
    assert(node._ethernet_channel_generation==generation && !node.channels[0].name[0]);
  }
  strcpy(candidate.name,"#emergency");assert(node.setChannel(0,candidate));
  assert(node._ethernet_channel_generation==generation+1);
  node.loopEthernetTunnel();assert(node._ethernet_active && !node.ethernetChannelConflict());
  candidate={};assert(node.setChannel(0,candidate));
  assert(node._ethernet_channel_generation==generation+2);
  node.loopEthernetTunnel();assert(node._ethernet_active);
}
int main() {
  defaults_cli_and_reboot();persistence_faults();profile_conflicts_and_recovery();
  opaque_frames_and_no_primary_fallback();temporary_and_scheduled_ownership();unused_meshtext_owner_handoff();
  real_channel_setter_invalidates_cache_without_evicting();
}
'''


class CompanionEthernetTest(unittest.TestCase):
    def compile_run(self, text, sources=(), includes=(), definitions=()):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-ethernet-companion-") as directory:
            work = Path(directory)
            (work / "test.cpp").write_text(text, encoding="utf-8")
            command = [compiler, "-std=c++17", "-Wall", "-Wextra",
                       "-fsanitize=address,undefined", "-fno-pie", "-no-pie"]
            command.extend("-D" + item for item in definitions)
            for path in (*includes, ROOT / "src"):
                command.extend(("-I", str(path)))
            command.extend((str(work / "test.cpp"), *(str(path) for path in sources),
                            "-o", str(work / "test")))
            build = subprocess.run(command, capture_output=True, text=True)
            diagnostics = build.stdout + build.stderr
            errors = "\n".join(line for line in diagnostics.splitlines()
                               if "error:" in line or "undefined reference" in line)
            self.assertEqual(build.returncode, 0, errors or diagnostics)
            run = subprocess.run([str(work / "test")], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_checked_settings_image_is_atomic_and_fail_closed(self):
        self.compile_run(SETTINGS_HARNESS)

    def test_production_companion_lifecycle_routing_and_persistence(self):
        source = (ROOT / "examples/companion_radio/EthernetCompanion.cpp").read_text(encoding="utf-8")
        methods = source[source.index("void MyMesh::beginEthernetTunnel()"):].rsplit("#endif", 1)[0]
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        methods += "\n" + body(companion, "bool MyMesh::setChannel(")
        fixture = (ROOT / "test/fixtures/radio_profiles/cli_test.cpp").read_text(encoding="utf-8")
        fixture = fixture[:fixture.index("struct Fixture {")]
        harness = COMPANION_HARNESS.replace("@RADIO_FIXTURE@", fixture).replace("@METHODS@", methods)
        self.compile_run(
            harness,
            sources=(ROOT / "src/helpers/RadioProfileCLI.cpp",),
            includes=(ROOT / "test/fixtures/radio_profiles/mocks", ROOT / "test/mocks"),
            definitions=("MESH_ENABLE_MESHTEXT_CHAT=0",
                         "MESH_ENABLE_TX_BEACONS=0", "MESH_ENABLE_ETHERNET_TUNNEL=1"),
        )

    def test_full_esp32_wifi_only_compile_gate(self):
        harness = ("#include <helpers/EthernetTunnelConfig.h>\n"
                   "static_assert(MESH_ENABLE_ETHERNET_TUNNEL==EXPECTED,\"feature gate\");\n"
                   "int main() {}\n")
        for definitions, expected in (
            (("ESP32_PLATFORM=1", "COMPANION_RADIO_FULL=1", "WIFI_SSID=1"), 1),
            (("ESP32_PLATFORM=1", "COMPANION_RADIO_FULL=1"), 0),
            (("ESP32_PLATFORM=1", "WIFI_SSID=1"), 0),
            (("NRF52_PLATFORM=1", "COMPANION_RADIO_FULL=1", "WIFI_SSID=1"), 0),
            (("ESP32_PLATFORM=1", "COMPANION_RADIO_FULL=1", "WIFI_SSID=1", "MESH_PRIMARY_ESPNOW=1"), 0),
        ):
            with self.subTest(definitions=definitions):
                self.compile_run(harness, definitions=(*definitions, f"EXPECTED={expected}"))

    def test_production_companion_wiring_keeps_transports_isolated(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        # begin() contains old alternative platform branches that intentionally
        # split a common outer body; take its stable next-method boundary.
        begin = source[source.index("void MyMesh::begin("):source.index("bool MyMesh::handleTxRoutingCommand(")]
        self.assertLess(begin.index("beginEthernetTunnel();"), begin.index("beginMeshtext();"))
        loop = body(source, "void MyMesh::loop()")
        self.assertLess(loop.index("loopEthernetTunnel();"), loop.index("BaseChatMesh::loop();"))
        self.assertIn("handleEthernetTunnelCommand(command, reply, reply_size)",
                      body(source, "bool MyMesh::handleLocalControlCommand("))
        self.assertEqual(source.count("if (!eraseEthernetSettings())"), 1)
        self.assertEqual(source.count("if (success && !eraseEthernetSettings())"), 1)
        setting = body(source, "bool MyMesh::setChannel(")
        self.assertIn("_ethernet_settings.enabled", setting)
        self.assertIn("CompanionChannelKind::MeshCore", setting)
        mt = (ROOT / "examples/companion_radio/MeshtextCompanion.cpp").read_text(encoding="utf-8")
        refresh = body(mt, "void MyMesh::refreshMeshtextProfile()")
        self.assertLess(refresh.index("if (_ethernet_settings.enabled)"),
                        refresh.index("_radio_profiles.clearAutoSecondary();"))
        self.assertIn("onEthernetFrame(bytes, length, profile, generation)",
                      body(mt, "bool MyMesh::onForeignRadioFrame("))
        self.assertIn("queuedForeignRadioProtocol() == mesh::RadioProfileProtocol::Ethernet",
                      body(mt, "void MyMesh::onForeignRadioSendComplete("))


if __name__ == "__main__":
    unittest.main()
