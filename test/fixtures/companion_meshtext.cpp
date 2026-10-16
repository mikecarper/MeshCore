// Template compiled by test_companion_meshtext.py. Markers insert actual
// production method bodies; all boundary substitutes are visible below.
@FIXTURE@
#include <SHA256.h>
#include <helpers/CompanionMeshtextChannel.h>
#include <helpers/MeshtextDelivery.h>
#include <helpers/CompanionFrameLimits.h>
#include <helpers/UTF8Helpers.h>
#include <cmath>
#include <limits>
using std::isfinite;
@CONSTANTS@
#define MAX_GROUP_CHANNELS 8
#define COMPANION_FEATURE_NOTIFICATIONS 1
#define COMPANION_FEATURE_TEXT_TERMINAL 0
#define TXT_TYPE_PLAIN 0

void mesh::Utils::sha256(uint8_t* out, size_t size, const uint8_t* data, int length) {
  assert(length >= 0 && size <= 32);
  SHA256 hash; hash.update(data, length); hash.finalize(out, size);
}

struct Preferences {
  static inline std::vector<uint8_t> blob;
  static inline bool fail_begin=false, fail_write=false, fail_read=false;
  static inline unsigned writes=0;
  bool begin(const char* name, bool read_only) { assert(!strcmp(name,"mesh-mt") && !read_only); return !fail_begin; }
  size_t getBytesLength(const char* key) { assert(!strcmp(key,"settings")); return blob.size(); }
  size_t getBytes(const char*, void* out, size_t size) {
    if(fail_read) return 0;
    size=std::min(size,blob.size()); memcpy(out,blob.data(),size); return size;
  }
  size_t putBytes(const char*, const void* in, size_t size) {
    ++writes; if(fail_write) return 0; const auto* data=static_cast<const uint8_t*>(in);
    blob.assign(data,data+size); return size;
  }
  void end() {}
  bool remove(const char* key) { assert(!strcmp(key,"settings")); if(fail_write) return false; blob.clear(); return true; }
  static void reset() { blob.clear(); fail_begin=fail_write=fail_read=false; writes=0; }
};
static uint32_t test_random_seed=0x12345678;
static uint32_t esp_random() { return test_random_seed; }

namespace mesh { struct GroupChannel {
  uint8_t hash[1]={},secret[32]={},tx_radio=RADIO_TX_AUTO;
}; }
struct ChannelDetails { mesh::GroupChannel channel; char name[32]={}; };
struct BaseChatMesh {
  ChannelDetails stored[MAX_GROUP_CHANNELS];
  bool getChannel(int index, ChannelDetails& out) {
    if(index<0 || index>=MAX_GROUP_CHANNELS) return false;
    out=stored[index]; return true;
  }
  bool setChannel(int index, const ChannelDetails& in) {
    if(index<0 || index>=MAX_GROUP_CHANNELS) return false;
    stored[index]=in;
    mesh::Utils::sha256(stored[index].channel.hash,1,in.channel.secret,
        mesh::Utils::isZeroes(in.channel.secret+16,16)?16:32); return true;
  }
};
struct MtRadio : Radio {
  bool supported=true;
  bool supportsMeshtext() const override { return supported; }
  uint32_t getProfileAirtime(uint8_t,int,uint8_t=0) override { return 10; }
};
struct Serial {
  bool connected=false;
  std::vector<std::vector<uint8_t>> frames;
  bool isConnected() { return connected; }
  void writeFrame(const uint8_t* bytes,int size) { assert(size>=0); frames.emplace_back(bytes,bytes+size); }
};
struct UI { void setMessageNotificationOverride(bool,bool=false) {} };
struct Notifications {
  uint8_t key[32]={}; unsigned calls=0;
  bool message(const void*,const uint8_t* secret,bool,uint32_t) { ++calls; memcpy(key,secret,32); return false; }
  bool overridesScreen() const { return false; }
};
struct StrHelper {
  static void strncpy(char* out,const char* in,size_t size) { ::strncpy(out,in,size-1); out[size-1]=0; }
};
class MyMesh : public BaseChatMesh {
public:
  MemoryFS fs; Clock clock; MtRadio radio; mesh::Radio* _radio=&radio;
  mesh::RadioProfileCLI _radio_profiles;
  struct {
    float freq=909.5; char node_name[32]="Test";
    uint8_t flood_retry_attempts=15,flood_retry_max_path=1;
    uint8_t flood_retry_group_max_path=0xff,flood_retry_advert_enabled=1;
  } _prefs;
  mesh::LocalIdentity self_id;
  mesh::mt::Region _mt_region=mesh::mt::Region::Unset;
  uint32_t _mt_slot=0,_mt_node=0,_mt_packet_id=0,_mt_sent=0,_mt_failed=0,_mt_received=0;
  uint32_t _mt_default_node=0,_mt_node_override=0;
  uint8_t _mt_hop_limit=7;
  char _mt_short_name[17]={};
  mesh::mt::SeenPackets _mt_seen;
  mesh::mt::PendingFrame _mt_delivery[mesh::mt::DeliverySlots];
  int8_t _mt_tx_slot=-1;
  uint8_t _mt_nodeinfo_fingerprint[MAX_GROUP_CHANNELS][8]={};
  bool _mt_nodeinfo_announced[MAX_GROUP_CHANNELS]={},_mt_nodeinfo_pending[MAX_GROUP_CHANNELS]={};
  uint32_t _mt_nodeinfo_generation=0,_mt_retried=0,_mt_heard=0,_mt_exhausted=0;
  struct Random {
    uint32_t jitter=0;
    uint32_t nextInt(uint32_t low,uint32_t high) {
      assert(low==0 && high==201 && jitter<high); return jitter;
    }
  } random;
  bool _mt_settings_valid=true,_mt_active=false,_mt_profile_dirty=true,_mt_last_radio_ready=false;
  bool _mt_delivery_config_dirty=true;
  uint32_t _mt_primary_generation=0,_mt_secondary_generation=0,_mt_intent_generation=0;
  float _mt_primary_frequency=0; const char* _mt_status="no Meshtext channels";
  bool _radio_available=true,foreign_pending=false,foreign_sending=false,admit_foreign=true,save_result=true,ok=false;
  uint32_t foreign_generation=0; uint8_t foreign_profile=255;
  unsigned foreign_admissions=0,meshcore_sends=0;
  std::vector<std::vector<uint8_t>> foreign,offline;
  size_t offline_capacity=4;
  Serial serial; Serial* _serial=&serial; UI ui; UI* _ui=&ui; Notifications _notifications;
  uint8_t out_frame[MAX_FRAME_SIZE]={}; uint8_t app_target_ver=3;
  int error=-1;
  MyMesh() { g_mock_millis=0; self_id.pub_key[0]=42; _radio_profiles.begin(&fs,&radio,&clock); }
  bool isAliasRadio2Defined() const;
  bool resolveChannelAlias(ChannelDetails&) const;
  bool getChannel(int,ChannelDetails&);
  bool setChannel(int,const ChannelDetails&);
  bool getChannelForSave(uint8_t index,ChannelDetails& value) { return BaseChatMesh::getChannel(index,value); }
  mesh::mt::Region meshtextRegion() const;
  void refreshMeshtextIdentity();
  void beginMeshtext();
  bool saveMeshtextSettings(mesh::mt::Region,uint32_t,uint8_t hops=UINT8_MAX,
                             const char* short_name=nullptr,uint32_t node_override=UINT32_MAX);
  bool eraseMeshtextSettings();
  bool isMeshtextChannel(const ChannelDetails&) const;
  bool validateMeshtextChannelChange(int,const ChannelDetails&);
  void refreshMeshtextProfile();
  bool sendMeshtextText(const ChannelDetails&,const char*,size_t);
  bool sendMeshtextNodeInfo(const ChannelDetails&);
  bool queueMeshtextDelivery(const ChannelDetails&,const uint8_t*,size_t,bool);
  int meshtextChannelIndex(const ChannelDetails&,uint8_t[8]);
  bool meshtextDeliveryCurrent(const mesh::mt::PendingFrame&);
  uint8_t meshtextRetryLimit(const mesh::mt::PendingFrame&) const;
  uint32_t meshtextRetryDelay(const mesh::mt::PendingFrame&);
  void loopMeshtextDelivery();
  bool onForeignRadioFrame(const uint8_t*,size_t,uint8_t,uint32_t,float,float);
  void onForeignRadioSendComplete(bool);
  bool handleMeshtextCommand(const char*,char*,size_t);
  bool queueChannelText(uint8_t,const uint8_t[32],float,uint8_t,uint32_t,const char*,bool);
  bool hasForeignRadioFrame() const { return foreign_pending; }
  bool queueForeignRadioFrame(const uint8_t* bytes,size_t size,uint8_t profile,uint32_t generation) {
    if(!admit_foreign || foreign_pending) return false;
    ++foreign_admissions; foreign.emplace_back(bytes,bytes+size); foreign_profile=profile;
    foreign_generation=generation; foreign_pending=true; return true;
  }
  void completeForeign(bool success=true) {
    assert(foreign_pending);
    // The real Dispatcher releases queue ownership before the application
    // completion callback. Successful RF TX is not a delivery acknowledgement.
    foreign_pending=false;
    foreign_sending=false;
    onForeignRadioSendComplete(success);
  }
  bool discardQueuedForeignRadioFrame(mesh::RadioProfileProtocol protocol) {
    assert(protocol==mesh::RadioProfileProtocol::Meshtext);
    if(!foreign_pending || foreign_sending) return false;
    completeForeign(false); return true;
  }
  void releaseForeign() {
    if(foreign_pending) completeForeign(false);
    loopMeshtextDelivery();
  }
  mesh::RTCClock* getRTCClock() { return &clock; }
  Random* getRNG() { return &random; }
  bool addToOfflineQueue(const uint8_t* bytes,int size) {
    assert(size>=0 && size<=MAX_FRAME_SIZE); if(offline.size()>=offline_capacity) return false;
    offline.emplace_back(bytes,bytes+size); return true;
  }
  bool saveChannels() { return save_result; }
  void writeErrFrame(uint8_t value) { error=value; }
  void writeOKFrame() { ok=true; }
  bool sendGroupMessage(uint32_t,mesh::GroupChannel&,const char*,const char*,int) { ++meshcore_sends; return true; }
  void command(uint8_t* cmd_frame,int len);
  void commit(int index,const ChannelDetails& value) {
    assert(setChannel(index,value)); _mt_profile_dirty=true; refreshMeshtextProfile();
  }
  const char* cli(const char* command,bool expected=true) {
    static char reply[256]; assert(_radio_profiles.handle(command,reply,sizeof(reply)));
    if(strncmp(command,"get ",4) && strncmp(command,"del ",4)) {
      if((!strncmp(reply,"OK",2))!=expected) { fprintf(stderr,"%s -> %s\n",command,reply); assert(false); }
    }
    return reply;
  }
  void advance(uint32_t ms) {
    g_mock_millis+=ms; clock.epoch+=ms/1000;
    _radio_profiles.loop(); refreshMeshtextProfile(); loopMeshtextDelivery();
  }
};
@METHODS@
void MyMesh::command(uint8_t* cmd_frame,int len) {
  error=-1; ok=false; serial.frames.clear();
  @COMMANDS@
}
static ChannelDetails channel(const char* name,int private_bytes=0) {
  ChannelDetails value; assert(strlen(name)<sizeof(value.name)); strcpy(value.name,name);
  if(private_bytes) { for(int i=0;i<private_bytes;++i)value.channel.secret[i]=i+1; }
  else mesh::Utils::sha256(value.channel.secret,16,reinterpret_cast<const uint8_t*>(name),strlen(name));
  return value;
}
static void active(MyMesh& node) {
  node.beginMeshtext(); node.commit(0,channel("#mt-longfast"));
  assert(node._mt_active && node.radio.p.secondary.params.protocol==mesh::RadioProfileProtocol::Meshtext);
}
static void assertUntouched(const MyMesh& node) { assert(node.foreign.empty() && !node.meshcore_sends); }
static void keys() {
  Preferences::reset(); MyMesh node; active(node);
  auto raw=channel("#mt-longfast"); ChannelDetails stored;
  assert(node.getChannelForSave(0,stored) && !memcmp(raw.channel.secret,stored.channel.secret,32));
  mesh::mt::Channel wire; assert(describe(stored,wire));
  assert(!strcmp(wire.name,"LongFast") && wire.named_preset && wire.key_length==16);
  assert(!memcmp(wire.key,mesh::mt::DefaultPsk,16));
  ChannelDetails policy; assert(node.getChannel(0,policy));
  assert(policy.channel.tx_radio==mesh::RADIO_TX_OFF);
  assert(memcmp(policy.channel.secret,raw.channel.secret,16));
  for(const char* name:{"#mt-LoNgFaSt","#mt-shortturbo","#mt-longmoderate"}) {
    auto c=channel(name); const auto before=c;
    assert(describe(c,wire) && !memcmp(wire.key,mesh::mt::DefaultPsk,16));
    assert(!memcmp(c.channel.secret,before.channel.secret,32));
  }
  for(int size:{16,32}) {
    auto c=channel("#mt-longfast",size); const auto before=c;
    assert(describe(c,wire) && wire.key_length==size && !memcmp(wire.key,c.channel.secret,32));
    assert(!memcmp(c.channel.secret,before.channel.secret,32));
    node.releaseForeign(); node.commit(0,c); assert(node.sendMeshtextText(c,"private",7));
    mesh::mt::DecodedText decoded;
    assert(mesh::mt::decodeText(node.foreign.back().data(),node.foreign.back().size(),wire.name,wire.key,size,1,decoded));
    assert(!strcmp(decoded.text,"private"));
  }
  auto custom=channel("#mt-emergency"); assert(describe(custom,wire));
  auto ordinary=channel("#emergency"); assert(!memcmp(wire.key,ordinary.channel.secret,16));
  assert(!wire.named_preset && !strcmp(wire.name,"emergency"));
  auto invalid=channel("#mt-abcdefghijkl"); assert(!describe(invalid,wire));
  puts("PASS keys");
}
static void automatic() {
  Preferences::reset(); MyMesh node; assert(!node.radio.p.enabled()); active(node);
  assert(node.radio.p.secondary.mode==mesh::RadioProfileMode::RxTx);
  assert(node.radio.p.secondary.params.bw==250 && node.radio.p.secondary.params.sf==11);
  assert(node._radio_profiles.autoSecondaryActive() && node.fs.files.empty() && Preferences::writes==0);
  const auto generation=node.radio.p.generation[1]; node.refreshMeshtextProfile();
  assert(node.radio.p.generation[1]==generation); assertUntouched(node);
  MyMesh reboot; reboot.stored[0]=node.stored[0]; reboot.fs=node.fs;
  reboot._radio_profiles.begin(&reboot.fs,&reboot.radio,&reboot.clock); reboot.beginMeshtext();
  assert(reboot._mt_active && reboot._radio_profiles.autoSecondaryActive()); assertUntouched(reboot);
  node.commit(0,{}); assert(!node._mt_active && !node.radio.p.enabled()); assertUntouched(node);
  puts("PASS auto");
}
static void conflicts() {
  Preferences::reset(); MyMesh node; active(node);
  assert(!node.validateMeshtextChannelChange(1,channel("#mt-longturbo")));
  assert(!node.validateMeshtextChannelChange(1,channel("#r2-emergency")));
  node.commit(1,channel("#mt-longturbo")); assert(!node._mt_active && !node.radio.p.enabled());
  node.commit(1,{}); assert(node._mt_active);
  assert(node.validateMeshtextChannelChange(1,channel("#mt-emergency",32)));
  node.commit(1,channel("#mt-emergency",32)); node.commit(0,{});
  assert(!node._mt_active && !node.radio.p.enabled());
  assert(node.isMeshtextChannel(node.stored[1]));
  assert(!node.sendMeshtextText(node.stored[1],"x",1));
  node.commit(1,{}); node._prefs.freq=915; node.commit(0,channel("#mt-longfast"));
  assert(!node._mt_active && strstr(node._mt_status,"ambiguous"));
  assert(!node.validateMeshtextChannelChange(0,channel("#mt-longfast")));
  assert(node.saveMeshtextSettings(mesh::mt::Region::US,0) && node._mt_active);
  assert(node.saveMeshtextSettings(mesh::mt::Region::EU868,0) && node._mt_active);
  assert(!node.validateMeshtextChannelChange(0,channel("#mt-shortturbo")));
  node.commit(0,channel("#mt-shortturbo")); assert(!node._mt_active);
  node.commit(0,channel("#mt-longfast")); assert(node._mt_active);
  assert(node.saveMeshtextSettings(mesh::mt::Region::EU868,2)); assert(!node._mt_active);
  assert(node.saveMeshtextSettings(mesh::mt::Region::EU868,1)); assert(node._mt_active);
  assert(std::fabs(node.radio.p.secondary.params.freq-869.525f)<0.0001f);
  node._radio_available=false; node.refreshMeshtextProfile(); assert(!node._mt_active);
  node._radio_available=true; node.refreshMeshtextProfile(); assert(node._mt_active);
  node.commit(0,channel("ordinary")); assert(!node._mt_active && !node.radio.p.enabled());
  assertUntouched(node); puts("PASS conflicts");
}
static void manual() {
  Preferences::reset(); MyMesh node; node.beginMeshtext();
  node.cli("set radio2 906.875,250,11,5,rxtx,80"); node.advance(2000);
  const auto saved_files=node.fs.files; const auto saved=node._radio_profiles.savedSecondaryConfig();
  node.commit(0,channel("#mt-longfast")); assert(node._mt_active);
  assert(node.radio.p.secondary.params.freq==906.875f && node.radio.p.secondary.params.preamble==16);
  assert(node._radio_profiles.savedSecondaryConfig().params==saved.params && node.fs.files==saved_files);
  node.cli("set tempradio2 909.5,62.5,7,5,rx,1,80"); node.advance(2000);
  assert(!node._mt_active && node.radio.p.secondary_temporary);
  assert(node.radio.p.secondary.params.protocol==mesh::RadioProfileProtocol::MeshCore);
  node.advance(60000); assert(node._mt_active && !node.radio.p.secondary_temporary);
  node.commit(0,{}); assert(!node._mt_active);
  assert(node.radio.p.secondary.params==saved.params && node.fs.files==saved_files);
  node.cli("set radio2 off"); node.advance(2000); const auto off_files=node.fs.files;
  node.commit(0,channel("#mt-longfast")); assert(!node._mt_active && !node.radio.p.enabled());
  node.commit(0,{}); assert(!node.radio.p.enabled() && node.fs.files==off_files);
  node.cli("set radio2 auto"); node.advance(2000); node.commit(0,channel("#mt-longfast"));
  assert(node._mt_active && node._radio_profiles.autoSecondaryActive());
  // An external/temporary owner is not ours to disable when a channel vanishes.
  auto external=node.radio.p.secondary; external.params.freq=910.25; external.params.bw=125;
  external.params.sf=8; external.params.cr=5; external.params.preamble=80;
  external.params.protocol=mesh::RadioProfileProtocol::MeshCore;
  node.radio.p.setSecondary(external,false); node.refreshMeshtextProfile();
  assert(!node._mt_active && node.radio.p.secondary.params==external.params);
  node.commit(0,{}); assert(node.radio.p.secondary.params==external.params);
  assertUntouched(node);
  puts("PASS manual");
}
static void settings() {
  Preferences::reset(); MyMesh node; active(node); assert(node._mt_node && node._mt_packet_id);
  assert(node.saveMeshtextSettings(mesh::mt::Region::ANZ,1));
  assert(Preferences::blob.size()==40 && Preferences::writes==1);
  const auto committed=Preferences::blob; Preferences::fail_write=true;
  assert(!node.saveMeshtextSettings(mesh::mt::Region::US,2));
  assert(node._mt_region==mesh::mt::Region::ANZ && node._mt_slot==1 && Preferences::blob==committed);
  assert(!node._mt_settings_valid && !node._mt_active && !node.radio.p.enabled());
  Preferences::fail_write=false; node.foreign_pending=true;
  const auto writes=Preferences::writes; assert(!node.saveMeshtextSettings(mesh::mt::Region::US,0));
  assert(Preferences::writes==writes); node.foreign_pending=false;
  assert(!node.saveMeshtextSettings(mesh::mt::Region::US,1025));
  assert(node.saveMeshtextSettings(mesh::mt::Region::ANZ,1) && node._mt_active);
  MyMesh reboot; reboot.stored[0]=node.stored[0]; reboot.beginMeshtext();
  assert(reboot._mt_region==mesh::mt::Region::ANZ && reboot._mt_slot==1 && reboot._mt_active);
  Preferences::blob[5]=1; MyMesh corrupt; corrupt.stored[0]=node.stored[0]; corrupt.beginMeshtext();
  assert(!corrupt._mt_settings_valid && !corrupt._mt_active && !corrupt.radio.p.enabled());
  char reply[160]; assert(corrupt.handleMeshtextCommand("set mt.region us",reply,sizeof(reply)));
  assert(!strncmp(reply,"OK",2) && corrupt._mt_active && corrupt._mt_settings_valid);
  assert(corrupt.handleMeshtextCommand("set mt.slot 1junk",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  assert(corrupt.handleMeshtextCommand("get mt.status",reply,sizeof(reply)) && strstr(reply,"RX time-shared"));
  assert(!corrupt.handleMeshtextCommand("get unknown",reply,sizeof(reply)));
  // The atomic blob may have committed despite failed readback: suspend RAM,
  // then reconstruct the committed choice only after a successful new read.
  Preferences::fail_read=true;
  assert(!corrupt.saveMeshtextSettings(mesh::mt::Region::ANZ,2));
  assert(!corrupt._mt_settings_valid && !corrupt._mt_active && !corrupt.radio.p.enabled());
  Preferences::fail_read=false; MyMesh readback;
  readback.stored[0]=corrupt.stored[0]; readback.beginMeshtext();
  assert(readback._mt_active && readback._mt_region==mesh::mt::Region::ANZ && readback._mt_slot==2);
  Preferences::fail_write=true; assert(!readback.eraseMeshtextSettings());
  assert(!readback._mt_active && !readback._mt_settings_valid && !readback.radio.p.enabled());
  Preferences::fail_write=false; assert(readback.eraseMeshtextSettings() && Preferences::blob.empty());
  assert(readback._mt_region==mesh::mt::Region::Unset && readback._mt_slot==0);
  assert(readback._mt_hop_limit==7);
  readback.refreshMeshtextProfile(); assert(!readback._mt_active);
  Preferences::fail_begin=true; assert(!corrupt.eraseMeshtextSettings());
  assert(!corrupt._mt_active && !corrupt._mt_settings_valid && !corrupt.radio.p.enabled());
  Preferences::fail_begin=false;
  assertUntouched(node); assertUntouched(reboot); assertUntouched(corrupt); puts("PASS settings");
}
static const uint8_t rx_frame[]=@RX_FRAME@,rx_retry[]=@RX_RETRY@,rx_dm[]=@RX_DM@,
                     rx_ack[]=@RX_ACK@,rx_other[]=@RX_OTHER@;
static bool receiveFrame(MyMesh& node,const uint8_t* bytes,size_t size,float snr=3) {
  return node.onForeignRadioFrame(bytes,size,1,node.radio.p.generation[1],snr,-90);
}
static bool validUtf8(const uint8_t* text,size_t size) {
  uint8_t frame[255]; return mesh::mt::encodeText(1,1,3,"LongFast",mesh::mt::DefaultPsk,16,
      reinterpret_cast<const char*>(text),size,frame)!=0;
}
static void receive() {
  Preferences::reset(); MyMesh node; active(node);
  assert(!node.onForeignRadioFrame(rx_frame,sizeof(rx_frame),0,node.radio.p.generation[1],3,-90));
  assert(!node.onForeignRadioFrame(rx_frame,sizeof(rx_frame),1,node.radio.p.generation[1]-1,3,-90));
  assert(!receiveFrame(node,rx_dm,sizeof(rx_dm))); assert(!receiveFrame(node,rx_other,sizeof(rx_other)));
  assert(node.offline.empty());
  assert(receiveFrame(node,rx_frame,sizeof(rx_frame),100));
  assert(node.offline.size()==1 && node._mt_received==1 && node._notifications.calls==1);
  auto envelope=node.offline.back(); assert(envelope[0]==RESP_CODE_CHANNEL_MSG_RECV_V3);
  assert(envelope[1]==127 && envelope[2]==0 && envelope[3]==0 && envelope[4]==0);
  assert(envelope[5]==0 && envelope[6]==TXT_TYPE_PLAIN && envelope.size()<=MAX_FRAME_SIZE);
  uint32_t timestamp=0; memcpy(&timestamp,envelope.data()+7,sizeof(timestamp));
  assert(timestamp==node.clock.epoch);
  assert(validUtf8(envelope.data()+11,envelope.size()-11));
  assert(!memcmp(envelope.data()+11,"!11223344: ",11));
  assert(envelope.size()==175); // 153 ASCII bytes remain; no partial euro scalar.
  ChannelDetails policy; assert(node.getChannel(0,policy));
  assert(!memcmp(node._notifications.key,policy.channel.secret,32));
  assert(memcmp(node._notifications.key,mesh::mt::DefaultPsk,16));
  assert(receiveFrame(node,rx_frame,sizeof(rx_frame)) && node.offline.size()==1);
  node.offline_capacity=1; assert(receiveFrame(node,rx_retry,sizeof(rx_retry)));
  assert(node._mt_received==1 && !node._mt_seen.seen(0x11223344,102));
  node.offline_capacity=4; node.app_target_ver=1; node.serial.connected=true;
  assert(receiveFrame(node,rx_retry,sizeof(rx_retry)) && node._mt_received==2);
  assert(node.offline.back()[0]==RESP_CODE_CHANNEL_MSG_RECV && node.offline.back()[1]==0);
  assert(node.serial.frames.size()==1 && node.serial.frames[0][0]==PUSH_CODE_MSG_WAITING);
  assert(receiveFrame(node,rx_ack,sizeof(rx_ack)) && node._mt_received==3);
  assertUntouched(node); // request-ACK produces no radio response, no relay
  node.offline_capacity=6;
  uint8_t ordinary_key[32]={1};
  node.app_target_ver=3;
  assert(node.queueChannelText(0,ordinary_key,-100,255,123,"negative",false));
  assert(node.offline.back()[1]==128 && node.offline.back()[5]==255);
  assert(node.queueChannelText(0,ordinary_key,std::numeric_limits<float>::quiet_NaN(),0,124,"nan",false));
  assert(node.offline.back()[1]==0);
  uint8_t empty_secret[32]={}; assert(!node.queueChannelText(MAX_GROUP_CHANNELS,empty_secret,0,0,0,"x",false));
  assert(!node.queueChannelText(0,empty_secret,0,0,0,nullptr,false));
  puts("PASS receive");
}
static void transmit() {
  Preferences::reset(); MyMesh node; active(node); assertUntouched(node);
  uint8_t request[32]={CMD_SEND_CHANNEL_TXT_MSG,TXT_TYPE_PLAIN,0};
  memcpy(request+7,"hello",5); node.command(request,12);
  assert(node.ok && node.error==-1 && node.foreign.size()==1 && !node.meshcore_sends);
  assert(node.foreign_profile==1 && node.foreign_generation==node.radio.p.generation[1]);
  mesh::mt::DecodedText decoded; auto& frame=node.foreign.back();
  assert(mesh::mt::decodeText(frame.data(),frame.size(),"LongFast",mesh::mt::DefaultPsk,16,1,decoded));
  assert(!strcmp(decoded.text,"hello"));
  assert(decoded.hop_limit==7 && decoded.hop_start==7 && decoded.hops==0);
  assert(frame[12]==0xe7); // limit/start 7, no want-ACK or MQTT flag.
  assert(!mesh::mt::decodeText(frame.data(),frame.size(),"LongFast",mesh::mt::DefaultPsk,16,node._mt_node,decoded));
  node.command(request,12); assert(!node.ok && node.error==ERR_CODE_BAD_STATE && node.foreign.size()==1);
  node.completeForeign(); node.onForeignRadioSendComplete(false);
  assert(node._mt_sent==1 && node._mt_failed==1);
  char oversized[229]; memset(oversized,'x',sizeof(oversized));
  assert(!node.sendMeshtextText(node.stored[0],oversized,sizeof(oversized)));
  assert(!node.sendMeshtextText(node.stored[0],"x\0y",3));
  node._mt_packet_id=UINT32_MAX; assert(node.sendMeshtextText(node.stored[0],"last",4));
  node.foreign_pending=false; assert(node._mt_packet_id==0 && !node.sendMeshtextText(node.stored[0],"x",1));
  assert(node.offline.empty() && node._notifications.calls==0); puts("PASS transmit");
}
static void applySet(MyMesh& node,int index,const ChannelDetails& value,int key_size=16) {
  uint8_t frame[66]={CMD_SET_CHANNEL,static_cast<uint8_t>(index)};
  memcpy(frame+2,value.name,32); memcpy(frame+34,value.channel.secret,key_size); node.command(frame,34+key_size);
}
static void commands() {
  Preferences::reset(); MyMesh node; active(node); ChannelDetails raw;
  auto original=node.stored[0]; original.channel.tx_radio=mesh::RADIO_TX_BOTH; node.commit(0,original);
  uint8_t get[]={CMD_GET_CHANNEL,0}; node.command(get,sizeof(get));
  assert(node.serial.frames.size()==1 && node.serial.frames[0].size()==50);
  assert(!memcmp(node.serial.frames[0].data()+34,original.channel.secret,16));
  applySet(node,0,original); assert(node.ok && node.getChannelForSave(0,raw));
  assert(raw.channel.tx_radio==mesh::RADIO_TX_BOTH && !memcmp(raw.channel.secret,original.channel.secret,32));
  auto custom=channel("#mt-longfast",16); node.save_result=false; applySet(node,0,custom);
  assert(node.error==ERR_CODE_FILE_IO_ERROR && !node.ok && node.getChannelForSave(0,raw));
  assert(!memcmp(raw.channel.secret,original.channel.secret,32)); node.save_result=true;
  applySet(node,0,custom); assert(node.ok && node.getChannelForSave(0,raw));
  assert(!memcmp(raw.channel.secret,custom.channel.secret,32) && raw.channel.tx_radio==mesh::RADIO_TX_AUTO);
  applySet(node,1,channel("#mt-longturbo")); assert(node.error==ERR_CODE_BAD_STATE && !node.ok);
  applySet(node,1,channel("#mt-mt-room")); assert(node.error==ERR_CODE_ILLEGAL_ARG && !node.ok);
  auto aes256=channel("#mt-longfast",32); applySet(node,0,aes256,32);
  assert(node.ok && node.getChannelForSave(0,raw) && !memcmp(raw.channel.secret,aes256.channel.secret,32));
  // Original two-byte GET is still a 16-byte response; opt-in GET exposes 32.
  node.command(get,sizeof(get)); assert(node.serial.frames[0].size()==50);
  uint8_t extended_get[]={CMD_GET_CHANNEL,0,32}; node.command(extended_get,sizeof(extended_get));
  assert(node.serial.frames[0].size()==66 && !memcmp(node.serial.frames[0].data()+34,aes256.channel.secret,32));
  auto rename=aes256; strcpy(rename.name,"#mt-LoNgFaSt"); applySet(node,0,rename);
  assert(node.ok && node.getChannelForSave(0,raw) && !memcmp(raw.channel.secret,aes256.channel.secret,32));
  node.save_result=false; auto changed=channel("#mt-longfast",32); changed.channel.secret[20]^=0x80;
  applySet(node,0,changed,32);
  assert(node.error==ERR_CODE_FILE_IO_ERROR && !node.ok && node.getChannelForSave(0,raw));
  assert(!memcmp(raw.channel.secret,aes256.channel.secret,32)); node.save_result=true;
  auto downgrade=channel("#mt-longfast",16); applySet(node,0,downgrade,32);
  assert(node.ok && node.getChannelForSave(0,raw) && mesh::Utils::isZeroes(raw.channel.secret+16,16));
  auto mc=channel("private",32); applySet(node,1,mc,32);
  assert(node.error==ERR_CODE_UNSUPPORTED_CMD && !node.ok);
  node.commit(1,channel("ordinary")); uint8_t get_mc[]={CMD_GET_CHANNEL,1,32}; node.command(get_mc,sizeof(get_mc));
  assert(node.serial.frames[0].size()==50); node.commit(1,{});
  applySet(node,0,{}); assert(node.ok && !node._mt_active && !node.radio.p.enabled());
  assertUntouched(node); puts("PASS commands");
}
static void privateCLI() {
  Preferences::reset(); MyMesh node; node.beginMeshtext(); char reply[256];
  const char* key128="0102030405060708090a0b0c0d0e0f10";
  const char* key256="0102030405060708090A0B0C0D0E0F101112131415161718191A1B1C1D1E1F20";
  char command[160]; snprintf(command,sizeof(command),"set mt.channel 0 mt-longfast %s",key256);
  assert(node.handleMeshtextCommand(command,reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node._mt_active && node._radio_profiles.autoSecondaryActive());
  auto expected=channel("mt-longfast",32); assert(!memcmp(node.stored[0].channel.secret,expected.channel.secret,32));
  assert(!strstr(reply,key256) && !strstr(reply,key128));
  assert(node.handleMeshtextCommand("get mt.channel 0",reply,sizeof(reply)));
  assert(strstr(reply,"AES-256") && strstr(reply,"key not displayed") && !strstr(reply,key256));
  const auto previous=node.stored[0]; node.save_result=false;
  snprintf(command,sizeof(command),"set mt.channel 0 mt-longfast %s",key128);
  assert(node.handleMeshtextCommand(command,reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  assert(!memcmp(node.stored[0].channel.secret,previous.channel.secret,32));
  assert(node.handleMeshtextCommand("delete mt.channel 0",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  assert(!memcmp(node.stored[0].channel.secret,previous.channel.secret,32)); node.save_result=true;
  for(const char* invalid:{"set mt.channel -1 mt-longfast AQ==", "set mt.channel 8 mt-longfast AQ==",
      "set mt.channel 999999999999999999999 mt-longfast AQ==", "set mt.channel 1 ordinary AQ==",
      "set mt.channel 1 #mt-mt-room AQ==", "set mt.channel 1 mt-longfast BADKEY",
      "set mt.channel 1 mt-longfast 0102030405060708090a0b0c0d0e0f1Z",
      "set mt.channel 1 mt-longfast 00000000000000000000000000000000",
      "set mt.channel 1 mt-longfast AQ== extra", "set mt.channel 1 mt-longfast",
      "delete mt.channel 0 extra", "get mt.channel 0 extra"}) {
    assert(node.handleMeshtextCommand(invalid,reply,sizeof(reply)) && !strncmp(reply,"Error",5));
    assert(!node.stored[1].name[0] && !memcmp(node.stored[0].channel.secret,previous.channel.secret,32));
  }
  assert(node.handleMeshtextCommand("set mt.channel 1 mt-team AQ==",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node._mt_active && !memcmp(node.stored[1].channel.secret,mesh::mt::DefaultPsk,16));
  assert(node.handleMeshtextCommand("delete mt.channel 0",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(!node._mt_active && !node.radio.p.enabled());
  assert(node.handleMeshtextCommand("set mt.preset longfast",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(!node._mt_active); node.advance(2000);
  assert(node._mt_active && node._radio_profiles.manualSecondaryConfigured());
  const auto saved_files=node.fs.files;
  assert(node.handleMeshtextCommand("delete mt.channel 1",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(!node._mt_active && node.radio.p.enabled() && node.fs.files==saved_files);
  assert(node.radio.p.secondary.params.protocol==mesh::RadioProfileProtocol::MeshCore);
  assert(node.handleMeshtextCommand("set mt.channel 1 mt-team AQ==",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node._mt_active);
  assert(node.handleMeshtextCommand("get mt.channel 1",reply,sizeof(reply)) && strstr(reply,"AES-128"));
  assert(node.handleMeshtextCommand("set mt.preset notapreset",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  for(const char* line:{"set mt.channel 0 mt-team ABCD", " SET\tMT.CHANNEL\t0\tmt-team\tABCD"}) {
    const auto* key=mesh::cli::terminalPasswordInput(line); assert(key && !strcmp(key,"ABCD"));
    assert(mesh::cli::shouldMaskTerminalInput(line));
  }
  for(const char* line:{"set mt.channel 0 mt-team", "set mt.channel 0", "get mt.channel 0"}) {
    assert(!mesh::cli::shouldMaskTerminalInput(line));
  }
  assertUntouched(node); puts("PASS cli");
}
static void hops() {
  Preferences::reset(); MyMesh node; active(node); char reply[256];
  assert(node._mt_hop_limit==7 && Preferences::writes==0);
  assert(node.handleMeshtextCommand("get mt.hops",reply,sizeof(reply)) && !strcmp(reply,"> 7"));
  for(uint8_t limit:{0,3,7}) {
    char command[32]; snprintf(command,sizeof(command),"set mt.hops %u",limit);
    assert(node.handleMeshtextCommand(command,reply,sizeof(reply)) && !strncmp(reply,"OK",2));
    assert(node._mt_hop_limit==limit && node._mt_region==mesh::mt::Region::Unset && node._mt_slot==0);
    assert(Preferences::blob.size()==40 && !memcmp(Preferences::blob.data(),"MT03",4));
    assert(Preferences::blob[5]==limit && Preferences::blob[6]==0 && Preferences::blob[7]==0);
    MyMesh reboot; reboot.stored[0]=node.stored[0]; reboot.beginMeshtext();
    assert(reboot._mt_hop_limit==limit && reboot._mt_active && reboot._mt_region==node._mt_region && reboot._mt_slot==node._mt_slot);
    assert(node.sendMeshtextText(node.stored[0],"hop",3));
    const auto& frame=node.foreign.back(); mesh::mt::DecodedText decoded;
    assert(mesh::mt::decodeText(frame.data(),frame.size(),"LongFast",mesh::mt::DefaultPsk,16,1,decoded));
    assert(decoded.hop_limit==limit && decoded.hop_start==limit && decoded.hops==0);
    assert(frame[12]==uint8_t(limit|(limit<<5))); node.foreign_pending=false;
    assertUntouched(reboot);
  }
  const auto writes=Preferences::writes; const auto committed=Preferences::blob;
  for(const char* invalid:{"set mt.hops 8","set mt.hops -1","set mt.hops 3junk",
      "set mt.hops 4294967296","set mt.hops 18446744073709551615","set mt.hops 3 extra"}) {
    assert(node.handleMeshtextCommand(invalid,reply,sizeof(reply)) && !strncmp(reply,"Error",5));
    assert(node._mt_hop_limit==7 && Preferences::writes==writes && Preferences::blob==committed);
  }
  assert(!node.saveMeshtextSettings(mesh::mt::Region::US,1,8));
  assert(node.handleMeshtextCommand("set mt.hops 3",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node.handleMeshtextCommand("set mt.region us",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node.handleMeshtextCommand("set mt.slot 2",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node._mt_hop_limit==3 && node._mt_region==mesh::mt::Region::US && node._mt_slot==2);
  assert(node.saveMeshtextSettings(mesh::mt::Region::ANZ,4)); // Two arguments preserve the hop setting.
  assert(node._mt_hop_limit==3 && node._mt_slot==4 && node._mt_region==mesh::mt::Region::ANZ);
  assert(node.handleMeshtextCommand("set mt.hops 0",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node._mt_slot==4 && node._mt_region==mesh::mt::Region::ANZ && node._mt_hop_limit==0);
  const auto before_pending=Preferences::blob; const auto pending_writes=Preferences::writes;
  node.foreign_pending=true;
  assert(node.handleMeshtextCommand("set mt.hops 7",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  assert(node._mt_hop_limit==0 && node._mt_slot==4 && node._mt_region==mesh::mt::Region::ANZ);
  assert(Preferences::blob==before_pending && Preferences::writes==pending_writes); node.foreign_pending=false;
  Preferences::fail_write=true;
  assert(!node.saveMeshtextSettings(mesh::mt::Region::US,1,7));
  assert(node._mt_hop_limit==0 && node._mt_slot==4 && node._mt_region==mesh::mt::Region::ANZ);
  assert(Preferences::blob==before_pending && !node._mt_active && !node._mt_settings_valid);
  Preferences::fail_write=false;
  assert(node.saveMeshtextSettings(mesh::mt::Region::ANZ,4) && node._mt_hop_limit==0);
  assert(node.eraseMeshtextSettings() && node._mt_hop_limit==7);
  // Python hashlib supplies this legacy MT01 image independently. Loading it
  // defaults hops to seven without silently rewriting persistent settings.
  const uint8_t legacy[]=@LEGACY_SETTINGS@;
  Preferences::reset(); Preferences::blob.assign(legacy,legacy+sizeof(legacy));
  const auto legacy_blob=Preferences::blob; MyMesh migrated;
  migrated.stored[0]=channel("#mt-longfast"); migrated.beginMeshtext();
  assert(migrated._mt_settings_valid && migrated._mt_active && migrated._mt_hop_limit==7);
  assert(migrated._mt_region==mesh::mt::Region::US && migrated._mt_slot==2);
  assert(Preferences::writes==0 && Preferences::blob==legacy_blob);
  assert(migrated.handleMeshtextCommand("set mt.hops 3",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(Preferences::writes==1 && !memcmp(Preferences::blob.data(),"MT03",4) && Preferences::blob[5]==3);
  assert(migrated._mt_region==mesh::mt::Region::US && migrated._mt_slot==2);
  MyMesh migrated_reboot; migrated_reboot.stored[0]=migrated.stored[0]; migrated_reboot.beginMeshtext();
  assert(migrated_reboot._mt_active && migrated_reboot._mt_hop_limit==3);
  assert(migrated_reboot._mt_region==mesh::mt::Region::US && migrated_reboot._mt_slot==2);
  // Even a checksummed legacy reserved-byte change or MT02 out-of-range hop
  // value remains malformed, rather than becoming an undocumented option.
  Preferences::blob=legacy_blob; Preferences::blob[5]=1;
  mesh::Utils::sha256(Preferences::blob.data()+12,4,Preferences::blob.data(),12);
  MyMesh bad_legacy; bad_legacy.stored[0]=migrated.stored[0]; bad_legacy.beginMeshtext();
  assert(!bad_legacy._mt_settings_valid && !bad_legacy._mt_active);
  Preferences::blob=legacy_blob; memcpy(Preferences::blob.data(),"MT02",4); Preferences::blob[5]=8;
  mesh::Utils::sha256(Preferences::blob.data()+12,4,Preferences::blob.data(),12);
  MyMesh bad_hops; bad_hops.stored[0]=migrated.stored[0]; bad_hops.beginMeshtext();
  assert(!bad_hops._mt_settings_valid && !bad_hops._mt_active);
  assertUntouched(migrated); assertUntouched(migrated_reboot); puts("PASS hops");
}
static mesh::mt::DecodedText decodeOwnText(MyMesh& node,const char* text,size_t length) {
  node.releaseForeign();
  const uint32_t expected_packet=node._mt_packet_id;
  assert(node.sendMeshtextText(node.stored[0],text,length));
  const auto& raw=node.foreign.back(); mesh::mt::DecodedText decoded;
  assert(mesh::mt::decodeText(raw.data(),raw.size(),"LongFast",mesh::mt::DefaultPsk,16,1,decoded));
  assert(decoded.from==node._mt_node && decoded.id==expected_packet && node._mt_packet_id==expected_packet+1);
  node.releaseForeign(); return decoded;
}
static void shortname() {
  Preferences::reset(); MyMesh node; active(node); char reply[256];
  uint8_t meshcore_identity[32]; memcpy(meshcore_identity,node.self_id.pub_key,32);
  const uint32_t default_node=@LOCAL_NODE@;
  assert(node._mt_node==default_node && node._mt_default_node==default_node && !node._mt_node_override);
  const uint32_t original_packet=node._mt_packet_id;
  assert(node.handleMeshtextCommand("set mt.shortname MIKE",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(!strcmp(node._mt_short_name,"MIKE") && node._mt_node==default_node && node._mt_packet_id==original_packet);
  assert(!memcmp(meshcore_identity,node.self_id.pub_key,32));
  assert(Preferences::blob.size()==40 && !memcmp(Preferences::blob.data(),"MT03",4));
  assert(!memcmp(Preferences::blob.data()+12,"MIKE\0",5));
  for(unsigned i=17;i<36;++i) assert(Preferences::blob[i]==0);
  assert(node.handleMeshtextCommand("get mt.shortname",reply,sizeof(reply)) && strstr(reply,"MIKE"));
  char node_id[12]; snprintf(node_id,sizeof(node_id),"!%08lx",(unsigned long)default_node);
  assert(node.handleMeshtextCommand("get mt.node",reply,sizeof(reply)) && strstr(reply,node_id));
  assert(node.handleMeshtextCommand("get mt.status",reply,sizeof(reply)) && strstr(reply,node_id));
  auto decoded=decodeOwnText(node,"identity",8);
  assert(!strcmp(decoded.text,"identity") && !strstr(decoded.text,"MIKE") && !strstr(decoded.text,node_id));
  strcpy(node._prefs.node_name,"Different MeshCore name");
  decoded=decodeOwnText(node,"unchanged",9);
  assert(decoded.from==default_node && !strcmp(decoded.text,"unchanged"));
  assert(node.handleMeshtextCommand("set mt.hops 3",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node.handleMeshtextCommand("set mt.region us",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node.handleMeshtextCommand("set mt.slot 2",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(!strcmp(node._mt_short_name,"MIKE") && node._mt_node==default_node && !node._mt_node_override);
  assert(node._mt_region==mesh::mt::Region::US && node._mt_slot==2 && node._mt_hop_limit==3);
  const char* four_emoji="\xf0\x9f\x98\x80\xf0\x9f\x98\x80\xf0\x9f\x98\x80\xf0\x9f\x98\x80";
  assert(node.saveMeshtextSettings(node._mt_region,node._mt_slot,UINT8_MAX,four_emoji));
  assert(strlen(node._mt_short_name)==16 && !strcmp(node._mt_short_name,four_emoji));
  assert(node._mt_hop_limit==3 && node._mt_node==default_node && !memcmp(meshcore_identity,node.self_id.pub_key,32));
  std::string body(229,'x');
  decoded=decodeOwnText(node,body.data(),228); assert(decoded.text_len==228);
  const auto admissions=node.foreign_admissions;
  assert(!node.sendMeshtextText(node.stored[0],body.data(),229) && node.foreign_admissions==admissions);
  auto persisted=Preferences::blob; const auto packet=node._mt_packet_id;
  for(const char* invalid:{"set mt.shortname ABCDE","set mt.shortname    ",
      "set mt.shortname A:B","set mt.shortname A\nB","set mt.shortname \xc0\xaf",
      "set mt.shortname \xed\xa0\x80","set mt.shortname \xf4\x90\x80\x80",
      "set mt.shortname \xf0\x9f\x98", "set mt.shortname \xe2\x80\xae"}) {
    assert(node.handleMeshtextCommand(invalid,reply,sizeof(reply)) && !strncmp(reply,"Error",5));
    assert(Preferences::blob==persisted && !strcmp(node._mt_short_name,four_emoji));
    assert(node._mt_node==default_node && node._mt_packet_id==packet);
  }
  const auto writes=Preferences::writes; node.foreign_pending=true;
  assert(node.handleMeshtextCommand("set mt.shortname BOB",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  assert(Preferences::writes==writes && Preferences::blob==persisted && !strcmp(node._mt_short_name,four_emoji));
  node.foreign_pending=false; Preferences::fail_write=true;
  assert(!node.saveMeshtextSettings(mesh::mt::Region::ANZ,4,7,"BOB"));
  assert(!strcmp(node._mt_short_name,four_emoji) && Preferences::blob==persisted);
  assert(node._mt_region==mesh::mt::Region::US && node._mt_slot==2 && node._mt_hop_limit==3);
  assert(!node._mt_active && !node._mt_settings_valid && node._mt_node==default_node && node._mt_packet_id==packet);
  Preferences::fail_write=false;
  assert(node.saveMeshtextSettings(node._mt_region,node._mt_slot));
  assert(!strcmp(node._mt_short_name,four_emoji));
  test_random_seed=0x87654321; MyMesh reboot; reboot.stored[0]=node.stored[0]; reboot.beginMeshtext();
  assert(reboot._mt_node==default_node && reboot._mt_default_node==default_node && !reboot._mt_node_override);
  assert(reboot._mt_packet_id==test_random_seed && !strcmp(reboot._mt_short_name,four_emoji));
  assert(reboot._mt_region==mesh::mt::Region::US && reboot._mt_slot==2 && reboot._mt_hop_limit==3);
  assertUntouched(reboot);
  assert(node.handleMeshtextCommand("set mt.shortname default",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(!node._mt_short_name[0] && node._mt_node==default_node && node._mt_packet_id==packet);
  memset(node._prefs.node_name,'A',31); node._prefs.node_name[31]=0;
  decoded=decodeOwnText(node,body.data(),228);
  assert(decoded.text_len==228 && !node.sendMeshtextText(node.stored[0],body.data(),229));
  node._prefs.node_name[0]=0;
  decoded=decodeOwnText(node,body.data(),228); assert(decoded.text_len==228 && !strstr(decoded.text,node_id));
  assert(!node.sendMeshtextText(node.stored[0],body.data(),229));
  // Validate both independently supplied 16-byte predecessors without writes.
  const uint8_t mt01[]=@LEGACY_SETTINGS@,mt02[]=@LEGACY_HOPS_SETTINGS@;
  for(unsigned version=0;version<2;++version) {
    Preferences::reset(); const auto* old=version?mt02:mt01; Preferences::blob.assign(old,old+16);
    const auto before=Preferences::blob; MyMesh legacy; legacy.stored[0]=channel("#mt-longfast"); legacy.beginMeshtext();
    assert(legacy._mt_active && legacy._mt_settings_valid && !legacy._mt_short_name[0] && !legacy._mt_node_override);
    assert(legacy._mt_node==default_node && legacy._mt_hop_limit==(version?3:7));
    assert(Preferences::writes==0 && Preferences::blob==before);
    assert(legacy.handleMeshtextCommand("set mt.shortname TEST",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
    assert(Preferences::writes==1 && Preferences::blob.size()==40 && !memcmp(Preferences::blob.data(),"MT03",4));
    assert(legacy._mt_region==mesh::mt::Region::US && legacy._mt_slot==2 && legacy._mt_hop_limit==(version?3:7));
    assertUntouched(legacy);
  }
  const auto valid_image=Preferences::blob;
  for(unsigned fault=0;fault<7;++fault) {
    Preferences::blob=valid_image;
    switch(fault) {
      case 0: memset(Preferences::blob.data()+12,'X',17); break; // no NUL
      case 1: Preferences::blob[14]=0; Preferences::blob[15]='X'; break; // non-canonical tail
      case 2: Preferences::blob[29]=1; break; // reserved padding
      case 3: memset(Preferences::blob.data()+12,0,17); Preferences::blob[12]=0xc0; Preferences::blob[13]=0xaf; break;
      case 4: memset(Preferences::blob.data()+12,0,17); memcpy(Preferences::blob.data()+12,"ABCDE",5); break;
      case 5: Preferences::blob[32]=3; break; // reserved override
      case 6: memset(Preferences::blob.data()+32,0xff,4); break;
    }
    mesh::Utils::sha256(Preferences::blob.data()+36,4,Preferences::blob.data(),36);
    MyMesh invalid; invalid.stored[0]=channel("#mt-longfast"); invalid.beginMeshtext();
    assert(!invalid._mt_settings_valid && !invalid._mt_active && !invalid.radio.p.enabled()); assertUntouched(invalid);
  }
  assert(!memcmp(meshcore_identity,node.self_id.pub_key,32)); puts("PASS shortname");
}
static void nodeOverride() {
  Preferences::reset(); MyMesh node; active(node); char reply[256];
  uint8_t identity[32]; memcpy(identity,node.self_id.pub_key,32);
  const uint32_t derived=@LOCAL_NODE@,packet=node._mt_packet_id;
  assert(node._mt_node==derived && node._mt_default_node==derived && !node._mt_node_override);
  for(const char* value:{"!12345678","ABCDEF01","00000004","fffffffe"}) {
    char command[64]; snprintf(command,sizeof(command),"set mt.node %s",value);
    const char* digits=value[0]=='!'?value+1:value; const uint32_t chosen=std::strtoul(digits,nullptr,16);
    const uint32_t next_packet=node._mt_packet_id;
    assert(node.handleMeshtextCommand(command,reply,sizeof(reply)) && !strncmp(reply,"OK",2));
    assert(node._mt_node==chosen && node._mt_node_override==chosen && node._mt_default_node==derived);
    assert(node._mt_packet_id==next_packet && !memcmp(identity,node.self_id.pub_key,32));
    const uint32_t image_id=uint32_t(Preferences::blob[32])|(uint32_t(Preferences::blob[33])<<8)
        |(uint32_t(Preferences::blob[34])<<16)|(uint32_t(Preferences::blob[35])<<24);
    assert(image_id==chosen);
    char label[12]; snprintf(label,sizeof(label),"!%08lx",(unsigned long)chosen);
    assert(node.handleMeshtextCommand("get mt.node",reply,sizeof(reply)) && strstr(reply,label));
    auto decoded=decodeOwnText(node,"chosen",6); assert(decoded.from==chosen && !strcmp(decoded.text,"chosen"));
    MyMesh reboot; reboot.stored[0]=node.stored[0]; reboot.beginMeshtext();
    assert(reboot._mt_node==chosen && reboot._mt_node_override==chosen && reboot._mt_default_node==derived);
    assertUntouched(reboot);
  }
  assert(node._mt_packet_id==packet+4);
  assert(node.handleMeshtextCommand("set mt.shortname ID",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node.handleMeshtextCommand("set mt.region anz",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node.handleMeshtextCommand("set mt.slot 2",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node.handleMeshtextCommand("set mt.hops 3",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node._mt_node==0xfffffffe && node._mt_node_override==0xfffffffe && !strcmp(node._mt_short_name,"ID"));
  const auto blob=Preferences::blob; const auto next_packet=node._mt_packet_id,writes=Preferences::writes;
  for(const char* invalid:{"set mt.node 00000000","set mt.node !00000001","set mt.node 00000002",
      "set mt.node 00000003","set mt.node FFFFFFFF","set mt.node !ffffffff",
      "set mt.node 1234567","set mt.node 123456789","set mt.node 0x12345678",
      "set mt.node !1234567g","set mt.node !!12345678","set mt.node 12345678 extra"}) {
    assert(node.handleMeshtextCommand(invalid,reply,sizeof(reply)) && !strncmp(reply,"Error",5));
    assert(node._mt_node==0xfffffffe && node._mt_node_override==0xfffffffe && Preferences::blob==blob);
    assert(node._mt_packet_id==next_packet && Preferences::writes==writes);
  }
  node.foreign_pending=true;
  assert(node.handleMeshtextCommand("set mt.node !12345678",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  assert(node._mt_node==0xfffffffe && Preferences::blob==blob && Preferences::writes==writes); node.foreign_pending=false;
  Preferences::fail_write=true;
  assert(!node.saveMeshtextSettings(mesh::mt::Region::US,1,7,"BOB",0x12345678));
  assert(node._mt_node==0xfffffffe && node._mt_node_override==0xfffffffe && !strcmp(node._mt_short_name,"ID"));
  assert(node._mt_region==mesh::mt::Region::ANZ && node._mt_slot==2 && node._mt_hop_limit==3);
  assert(node._mt_packet_id==next_packet && Preferences::blob==blob && !node._mt_active && !node._mt_settings_valid);
  Preferences::fail_write=false; assert(node.saveMeshtextSettings(node._mt_region,node._mt_slot));
  assert(node._mt_node==0xfffffffe && node._mt_node_override==0xfffffffe && node._mt_packet_id==next_packet);
  assert(node.handleMeshtextCommand("set mt.node default",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node._mt_node==derived && !node._mt_node_override && node._mt_packet_id==next_packet);
  assert(!strcmp(node._mt_short_name,"ID") && node._mt_region==mesh::mt::Region::ANZ && node._mt_slot==2 && node._mt_hop_limit==3);
  auto decoded=decodeOwnText(node,"default",7); assert(decoded.from==derived);
  // If NVS commits but readback fails, RAM keeps its prior identity while the
  // backend suspends. A fresh boot may then adopt the committed override.
  Preferences::fail_read=true; const uint32_t before_uncertain=node._mt_packet_id;
  assert(!node.saveMeshtextSettings(node._mt_region,node._mt_slot,UINT8_MAX,nullptr,0x12345678));
  assert(node._mt_node==derived && !node._mt_node_override && node._mt_packet_id==before_uncertain);
  assert(!node._mt_active && !node._mt_settings_valid); Preferences::fail_read=false;
  MyMesh reboot; reboot.stored[0]=node.stored[0]; reboot.beginMeshtext();
  assert(reboot._mt_node==0x12345678 && reboot._mt_node_override==0x12345678 && reboot._mt_default_node==derived);
  assert(!strcmp(reboot._mt_short_name,"ID") && reboot._mt_hop_limit==3);
  assert(!memcmp(identity,node.self_id.pub_key,32) && !memcmp(identity,reboot.self_id.pub_key,32));
  assertUntouched(reboot);
  // Exercise the actual identity-refresh lifecycle after the durable-import
  // boundary, independently hashing the imported public keys in Python.
  Preferences::reset(); MyMesh imported; active(imported);
  const auto original_sequence=imported._mt_packet_id;
  assert(imported._mt_seen.remember(0x11223344,101));
  const auto no_settings=Preferences::blob; const auto original_writes=Preferences::writes;
  imported.self_id.pub_key[0]=43; imported.refreshMeshtextIdentity();
  assert(imported._mt_default_node==@IMPORTED_NODE_43@ && imported._mt_node==@IMPORTED_NODE_43@);
  assert(!imported._mt_node_override && imported._mt_packet_id==original_sequence);
  assert(!imported._mt_seen.seen(0x11223344,101));
  assert(Preferences::blob==no_settings && Preferences::writes==original_writes);
  decoded=decodeOwnText(imported,"imported",8); assert(decoded.from==@IMPORTED_NODE_43@);
  assert(imported.handleMeshtextCommand("set mt.node !12345678",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  const auto overridden_sequence=imported._mt_packet_id; const auto overridden_settings=Preferences::blob;
  const auto overridden_writes=Preferences::writes;
  assert(imported._mt_seen.remember(0x11223344,102));
  imported.self_id.pub_key[0]=44; imported.refreshMeshtextIdentity();
  assert(imported._mt_default_node==@IMPORTED_NODE_44@ && imported._mt_node==0x12345678);
  assert(imported._mt_node_override==0x12345678 && imported._mt_packet_id==overridden_sequence);
  assert(!imported._mt_seen.seen(0x11223344,102));
  assert(Preferences::blob==overridden_settings && Preferences::writes==overridden_writes);
  decoded=decodeOwnText(imported,"override",8); assert(decoded.from==0x12345678);
  const auto reset_sequence=imported._mt_packet_id;
  assert(imported.handleMeshtextCommand("set mt.node default",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(imported._mt_node==@IMPORTED_NODE_44@ && imported._mt_default_node==@IMPORTED_NODE_44@);
  assert(!imported._mt_node_override && imported._mt_packet_id==reset_sequence);
  decoded=decodeOwnText(imported,"reset",5); assert(decoded.from==@IMPORTED_NODE_44@);
  assert(imported._mt_packet_id==original_sequence+3 && imported.self_id.pub_key[0]==44);
  puts("PASS node");
}
static void nodeinfo() {
  test_random_seed=0x12345678;
  Preferences::reset(); MyMesh node; active(node); char reply[256];
  assertUntouched(node);
  assert(node.handleMeshtextCommand("get mt.shortname",reply,sizeof(reply)));
  char fallback[5]; snprintf(fallback,sizeof(fallback),"%04lx",(unsigned long)(node._mt_node&0xffff));
  assert(strstr(reply,fallback) && strstr(reply,"first-chat/manual NodeInfo"));
  const uint8_t expected_default[]=@NODEINFO_DEFAULT@,expected_named[]=@NODEINFO_NAMED@,
      expected_utf8[]=@NODEINFO_UTF8@,expected_private[]=@NODEINFO_PRIVATE@;
  const auto original_settings=Preferences::blob; const auto original_writes=Preferences::writes;
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && strstr(reply,"queued"));
  assert(node.foreign.back()==std::vector<uint8_t>(expected_default,expected_default+sizeof(expected_default)));
  assert(node.foreign_profile==1 && node.foreign_generation==node.radio.p.generation[1]);
  assert(Preferences::blob==original_settings && Preferences::writes==original_writes);
  const auto pending_id=node._mt_packet_id; const auto pending_count=node.foreign.size();
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  assert(node._mt_packet_id==pending_id && node.foreign.size()==pending_count);
  assert(!node.sendMeshtextText(node.stored[0],"busy",4));
  assert(!node.saveMeshtextSettings(mesh::mt::Region::US,0));
  node.releaseForeign();
  assert(node.handleMeshtextCommand("set mt.node !12345678",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node.handleMeshtextCommand("set mt.shortname MIKE",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  strcpy(node._prefs.node_name,"Mike Carper");
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && strstr(reply,"queued"));
  assert(node.foreign.back()==std::vector<uint8_t>(expected_named,expected_named+sizeof(expected_named)));
  node.releaseForeign();
  auto text=decodeOwnText(node,"hello",5);
  assert(text.id==0x1234567a && !strcmp(text.text,"hello"));
  const char* emojis="\xf0\x9f\x98\x80\xf0\x9f\x98\x80\xf0\x9f\x98\x80\xf0\x9f\x98\x80";
  assert(node.saveMeshtextSettings(node._mt_region,node._mt_slot,UINT8_MAX,emojis));
  memset(node._prefs.node_name,'A',22); strcpy(node._prefs.node_name+22,"\xe2\x82\xac" "rest");
  assert(node.handleMeshtextCommand("get mt.shortname",reply,sizeof(reply)) && strstr(reply,"effective=\xf0\x9f\x98\x80 ("));
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && strstr(reply,"queued"));
  assert(node.foreign.back()==std::vector<uint8_t>(expected_utf8,expected_utf8+sizeof(expected_utf8)));
  assert(strlen(node._prefs.node_name)==29 && strlen(node._mt_short_name)==16); // no mutation
  node.releaseForeign(); node.commit(1,channel("mt-Team",32));
  assert(node.handleMeshtextCommand("send mt.nodeinfo 1",reply,sizeof(reply)) && strstr(reply,"queued"));
  assert(node.foreign.back()==std::vector<uint8_t>(expected_private,expected_private+sizeof(expected_private)));
  node.releaseForeign();
  const auto count=node.foreign.size(); const uint32_t packet=node._mt_packet_id;
  node.commit(2,channel("ordinary"));
  for(const char* bad:{"send mt.nodeinfo","send mt.nodeinfo ","send mt.nodeinfo -1",
      "send mt.nodeinfo 8","send mt.nodeinfo 4294967296","send mt.nodeinfo 999999999999999",
      "send mt.nodeinfo 0 extra","send mt.nodeinfo 2","send mt.nodeinfo 3"}) {
    assert(node.handleMeshtextCommand(bad,reply,sizeof(reply)) && !strncmp(reply,"Error",5));
    assert(node.foreign.size()==count && node._mt_packet_id==packet);
  }
  node.advance(3600000);
  assert(node.foreign.size()==count && node._mt_packet_id==packet); // no hourly/periodic advert
  node._radio_available=false; node.refreshMeshtextProfile();
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  node._radio_available=true; node.refreshMeshtextProfile();
  strcpy(node._prefs.node_name,"\xc0\xaf");
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  assert(node.foreign.size()==count && node._mt_packet_id==packet);
  strcpy(node._prefs.node_name,"Test");
  node.admit_foreign=false;
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  assert(node.foreign.size()==count && node._mt_packet_id==packet+1); // failed nonce never reused
  node.admit_foreign=true;
  node.cli("set radio2 906.875,250,11,5,rx,16"); node.advance(2000);
  assert(node._mt_active && !node.radio.p.canTransmit(1));
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  assert(node.foreign.size()==count && node._mt_packet_id==packet+1);
  node.cli("set radio2 off"); node.advance(2000);
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  node.cli("set radio2 auto"); node.advance(2000);
  node._mt_packet_id=UINT32_MAX;
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && strstr(reply,"queued"));
  node.releaseForeign();
  assert(node._mt_packet_id==0 && !node.sendMeshtextText(node.stored[0],"done",4));
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  MyMesh reboot; reboot.stored[0]=node.stored[0]; reboot.beginMeshtext();
  assertUntouched(reboot); assert(!node.meshcore_sends);
  puts("PASS nodeinfo");
}
static mesh::mt::PendingFrame* pendingChat(MyMesh& node) {
  for(unsigned i=0;i<mesh::mt::ChatDeliverySlots;++i)
    if(node._mt_delivery[i].length) return &node._mt_delivery[i];
  return nullptr;
}
static void advanceTo(MyMesh& node,uint32_t due) {
  const int32_t delta=static_cast<int32_t>(due-millis());
  node.advance(delta>0?static_cast<uint32_t>(delta):0);
}
static std::vector<uint8_t> repeated(const std::vector<uint8_t>& original) {
  auto echo=original;
  assert((echo[12]&7)>0);
  echo[12]=static_cast<uint8_t>((echo[12]&0xf8)|((echo[12]&7)-1));
  echo[14]=0x42; echo[15]=0x43; // Forwarder-controlled header fields.
  return echo;
}
static void announce(MyMesh& node) {
  node._prefs.flood_retry_attempts=0;
  assert(node.sendMeshtextNodeInfo(node.stored[0]));
  node.completeForeign(); node.advance(0);
  assert(!node.foreign_pending);
  node._prefs.flood_retry_attempts=15;
}
static void automaticNodeinfo() {
  const uint8_t expected[]=@NODEINFO_AFTER_CHAT@;
  Preferences::reset(); MyMesh node; active(node);
  node._prefs.flood_retry_attempts=0;
  node.advance(3600000); assertUntouched(node);
  assert(node.sendMeshtextText(node.stored[0],"hello",5));
  const auto text=node.foreign.back();
  node.advance(60000); assert(node.foreign.size()==1); // Admission is not RF success.
  node.completeForeign(); assert(node.foreign.size()==1);
  node.advance(1);
  assert(node.foreign.size()==2 && node.foreign.back()==std::vector<uint8_t>(expected,expected+sizeof(expected)));
  // Exact independent protobuf/AES bytes prove only User/NodeInfo was sent:
  // no position packet, coordinates, MAC, public key or custom name envelope.
  node.completeForeign(); node.advance(3600000); assert(node.foreign.size()==2);
  assert(node.sendMeshtextText(node.stored[0],"again",5));
  node.completeForeign(); node.advance(100000); assert(node.foreign.size()==3);
  node.commit(1,channel("mt-Team",32));
  assert(node.sendMeshtextText(node.stored[1],"private",7));
  node.completeForeign(); node.advance(1); assert(node.foreign.size()==5);
  auto private_info=node.foreign.back();
  mesh::mt::DecodedText private_decoded;
  assert(private_info!=text && !mesh::mt::decodeText(private_info.data(),private_info.size(),
      "Team",node.stored[1].channel.secret,32,1,private_decoded));
  node.completeForeign(); node.advance(3600000); assert(node.foreign.size()==5);
  assert(node.offline.empty() && !node.meshcore_sends && !node._notifications.calls);

  Preferences::reset(); MyMesh failure; active(failure); failure._prefs.flood_retry_attempts=0;
  failure.admit_foreign=false; assert(!failure.sendMeshtextText(failure.stored[0],"rejected",8));
  failure.admit_foreign=true; failure.advance(1); assertUntouched(failure);
  assert(failure.sendMeshtextText(failure.stored[0],"failed",6));
  failure.completeForeign(false); failure.advance(100000); assert(failure.foreign.size()==1);
  assert(failure.sendMeshtextText(failure.stored[0],"success",7));
  failure.completeForeign(); failure.advance(1); assert(failure.foreign.size()==3);
  failure.completeForeign();

  Preferences::reset(); MyMesh manual; active(manual); announce(manual);
  manual._prefs.flood_retry_attempts=0;
  assert(manual.sendMeshtextText(manual.stored[0],"already named",13));
  manual.completeForeign(); manual.advance(100000); assert(manual.foreign.size()==2);
  puts("PASS auto-nodeinfo");
}
static void heardRetries() {
  Preferences::reset(); MyMesh node; active(node); announce(node);
  node._prefs.flood_retry_attempts=2;
  assert(node.sendMeshtextText(node.stored[0],"retry this",10));
  const auto original=node.foreign.back(); const auto packet=node._mt_packet_id;
  const auto start=node.foreign.size();
  node.completeForeign(); auto* pending=pendingChat(node); assert(pending);
  uint32_t due=pending->due;
  node.advance(static_cast<uint32_t>(due-millis()-1)); assert(node.foreign.size()==start);
  node.advance(1); assert(node.foreign.size()==start+1 && node.foreign.back()==original);
  assert(node._mt_packet_id==packet && node._mt_retried==0);
  node.completeForeign(); assert(node._mt_retried==1); pending=pendingChat(node); assert(pending);
  advanceTo(node,pending->due);
  assert(node.foreign.size()==start+2 && node.foreign.back()==original && node._mt_retried==1);
  node.completeForeign(); assert(node._mt_retried==2); pending=pendingChat(node); assert(pending); // Final echo window remains.
  auto echo=repeated(original);
  assert(receiveFrame(node,echo.data(),echo.size()));
  assert(node._mt_heard==1 && !node._mt_exhausted && node.offline.empty());
  node.advance(3600000); assert(node.foreign.size()==start+2 && !pendingChat(node));

  Preferences::reset(); MyMesh capped; active(capped); announce(capped);
  capped._prefs.flood_retry_attempts=15;
  assert(capped.sendMeshtextText(capped.stored[0],"bounded",7));
  const auto cap_original=capped.foreign.back(); capped.completeForeign();
  for(unsigned i=0;i<15;++i) {
    auto* current=pendingChat(capped); assert(current);
    advanceTo(capped,current->due);
    assert(capped.foreign_pending && capped.foreign.back()==cap_original);
    capped.completeForeign();
  }
  assert(capped._mt_retried==15 && capped.foreign.size()==17);
  auto* current=pendingChat(capped); assert(current); advanceTo(capped,current->due);
  assert(capped._mt_exhausted==1 && !pendingChat(capped));
  capped.advance(3600000); assert(capped.foreign.size()==17);

  Preferences::reset(); MyMesh disabled; active(disabled); announce(disabled);
  disabled._prefs.flood_retry_attempts=0;
  assert(disabled.sendMeshtextText(disabled.stored[0],"once",4));
  disabled.completeForeign(); disabled.advance(3600000);
  assert(disabled.foreign.size()==2 && !disabled._mt_retried && !pendingChat(disabled));
  disabled._prefs.flood_retry_attempts=15; disabled._mt_hop_limit=0;
  assert(disabled.sendMeshtextText(disabled.stored[0],"no hops",7));
  disabled.completeForeign(); disabled.advance(3600000);
  assert(disabled.foreign.size()==3 && !disabled._mt_retried && !pendingChat(disabled));
  puts("PASS heard-retries");
}
static void strictEcho() {
  Preferences::reset(); MyMesh node; active(node); announce(node);
  assert(node.sendMeshtextText(node.stored[0],"strict echo",11));
  const auto original=node.foreign.back(); auto echo=repeated(original);
  node.completeForeign(); const auto count=node.foreign.size();
  receiveFrame(node,original.data(),original.size()); assert(!node._mt_heard);
  for(unsigned position:{0U,4U,8U,12U,13U,16U,unsigned(echo.size()-1)}) {
    auto wrong=echo; wrong[position]^=position==12?0x20:1;
    receiveFrame(node,wrong.data(),wrong.size()); assert(!node._mt_heard);
  }
  receiveFrame(node,echo.data(),echo.size()-1); assert(!node._mt_heard);
  node.onForeignRadioFrame(echo.data(),echo.size(),0,node.radio.p.generation[1],1,-80);
  node.onForeignRadioFrame(echo.data(),echo.size(),1,node.radio.p.generation[1]-1,1,-80);
  assert(!node._mt_heard && pendingChat(node));
  assert(receiveFrame(node,echo.data(),echo.size()) && node._mt_heard==1);
  receiveFrame(node,echo.data(),echo.size()); assert(node._mt_heard==1);
  node.advance(100000); assert(node.foreign.size()==count && node.offline.empty());
  puts("PASS strict-echo");
}
static void deliveryLifecycle() {
  for(unsigned change=0;change<7;++change) {
    Preferences::reset(); MyMesh node; active(node); announce(node);
    assert(node.sendMeshtextText(node.stored[0],"stale",5));
    node.completeForeign(); assert(pendingChat(node));
    const auto count=node.foreign.size(); char reply[256];
    switch(change) {
      case 0: ++node.radio.p.generation[1]; break;
      case 1: node.cli("set radio2 off"); break;
      case 2: node.cli("set radio2 906.875,250,11,5,rx,16"); break;
      case 3: node.commit(0,channel("#mt-longfast",32)); break;
      case 4:
        assert(node.handleMeshtextCommand("set mt.node !12345678",reply,sizeof(reply))
            && !strncmp(reply,"OK",2)); break;
      case 5: node.commit(0,{}); break;
      case 6: node._radio_available=false; break;
    }
    node.advance(2000); node.advance(100000);
    assert(!pendingChat(node) && !node.foreign_pending && node.foreign.size()==count);
    assert(!node._mt_retried && !node._mt_exhausted);
  }
  // Configuration changes before RF completion cannot arm metadata intent.
  Preferences::reset(); MyMesh inflight; active(inflight);
  assert(inflight.sendMeshtextText(inflight.stored[0],"not sent",8));
  inflight.commit(0,channel("#mt-longfast",32)); inflight.advance(1);
  assert(!inflight.foreign_pending && inflight.foreign.size()==1);
  inflight.advance(100000); assert(inflight.foreign.size()==1);
  // Config edits and RX by themselves remain completely silent.
  inflight.self_id.pub_key[0]=43; inflight.refreshMeshtextIdentity(); inflight.advance(1);
  assert(inflight.foreign.size()==1);
  puts("PASS delivery-lifecycle");
}
static void deliveryContention() {
  Preferences::reset(); MyMesh node; active(node); announce(node);
  assert(node.sendMeshtextText(node.stored[0],"older",5));
  const auto original=node.foreign.back(); node.completeForeign();
  auto* pending=pendingChat(node); assert(pending); const auto due=pending->due;
  const auto count=node.foreign.size();
  node.admit_foreign=false; advanceTo(node,due);
  assert(node.foreign.size()==count && !node._mt_retried && pending->retries==0);
  node.admit_foreign=true;
  assert(node.sendMeshtextText(node.stored[0],"newer",5)); // Don't block users for an echo window.
  node.completeForeign(); node.advance(0);
  assert(node.foreign_pending && node.foreign.back()==original && !node._mt_retried);
  const auto failed=node._mt_failed; auto echo=repeated(original);
  assert(receiveFrame(node,echo.data(),echo.size())); // Arrives while a retry is queued.
  assert(!node.foreign_pending && node._mt_heard==1 && node._mt_failed==failed);
  pending=pendingChat(node); assert(pending); advanceTo(node,pending->due);
  assert(node.foreign_pending); // Newer chat's own timer still works.
  const auto newer=node.foreign.back(); node.foreign_sending=true;
  auto newer_echo=repeated(newer);
  assert(receiveFrame(node,newer_echo.data(),newer_echo.size()));
  assert(node.foreign_pending && node._mt_heard==2); // In-flight RF cannot be discarded.
  node.completeForeign(); node.advance(100000);
  assert(!node.foreign_pending && !pendingChat(node) && node._mt_failed==failed && node._mt_retried==1);
  char reply[256]; assert(node.handleMeshtextCommand("get mt.retry",reply,sizeof(reply)));
  assert(strstr(reply,"heard=2") && strstr(reply,"pending=0"));

  Preferences::reset(); MyMesh full; active(full); announce(full);
  for(unsigned i=0;i<mesh::mt::ChatDeliverySlots;++i) {
    assert(full.sendMeshtextText(full.stored[0],"pending",7)); full.completeForeign();
  }
  const auto full_count=full.foreign.size();
  assert(!full.sendMeshtextText(full.stored[0],"backpressure",12));
  assert(full.foreign.size()==full_count && !full.foreign_pending);
  puts("PASS delivery-contention");
}
static void deliveryTiming() {
  Preferences::reset(); MyMesh node; active(node); announce(node);
  g_mock_millis=UINT32_MAX-100;
  node._prefs.flood_retry_max_path=0; node._prefs.flood_retry_group_max_path=0;
  assert(node.sendMeshtextText(node.stored[0],"wrap",4));
  const auto original=node.foreign.back(); node.completeForeign();
  auto* pending=pendingChat(node); assert(pending && pending->due==109);
  const auto count=node.foreign.size(); node.advance(209);
  assert(node.foreign.size()==count); node.advance(1);
  assert(node.foreign.size()==count+1 && node.foreign.back()==original);
  node.completeForeign(); auto echo=repeated(original);
  assert(receiveFrame(node,echo.data(),echo.size()) && node._mt_heard==1);
  node.advance(100000); assert(!pendingChat(node));

  Preferences::reset(); MyMesh metadata; active(metadata); metadata.random.jitter=200;
  assert(metadata.sendMeshtextNodeInfo(metadata.stored[0]));
  const auto info=metadata.foreign.back(); const auto packet=metadata._mt_packet_id;
  metadata.completeForeign(); auto& advert=metadata._mt_delivery[mesh::mt::ChatDeliverySlots];
  assert(advert.length && advert.due-millis()==60230);
  metadata.advance(60229); assert(metadata.foreign.size()==1);
  metadata.advance(1); assert(metadata.foreign.size()==2 && metadata.foreign.back()==info);
  assert(metadata._mt_packet_id==packet && metadata._mt_retried==0);
  metadata.completeForeign(); assert(metadata._mt_retried==1); advanceTo(metadata,advert.due);
  assert(!advert.length && metadata._mt_exhausted==1);
  metadata.advance(3600000); assert(metadata.foreign.size()==2); // Metadata cap is one, not 15.

  Preferences::reset(); MyMesh no_advert; active(no_advert);
  no_advert._prefs.flood_retry_advert_enabled=0;
  assert(no_advert.sendMeshtextNodeInfo(no_advert.stored[0]));
  no_advert.completeForeign(); no_advert.advance(3600000);
  assert(no_advert.foreign.size()==1 && !no_advert._mt_retried);
  puts("PASS delivery-timing");
}
static void announcementLifecycle() {
  Preferences::reset(); MyMesh node; active(node); announce(node);
  node._prefs.flood_retry_attempts=0;
  char reply[256];
  for(const char* command:{"set mt.hops 7","set mt.hops 3"}) {
    assert(node.handleMeshtextCommand(command,reply,sizeof(reply)) && !strncmp(reply,"OK",2));
    node.advance(1); const auto count=node.foreign.size();
    assert(node.sendMeshtextText(node.stored[0],"no reannounce",13));
    node.completeForeign(); node.advance(1); assert(node.foreign.size()==count+1);
  }
  node.commit(1,channel("#mt-LoNgFaSt"));
  const auto before_alias=node.foreign.size();
  assert(node.sendMeshtextText(node.stored[1],"same wire",9));
  node.completeForeign(); node.advance(1); assert(node.foreign.size()==before_alias+1);
  assert(node.handleMeshtextCommand("set mt.shortname NEW",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  node.advance(100000); assert(node.foreign.size()==before_alias+1); // Name changes do not send alone.
  assert(node.sendMeshtextText(node.stored[0],"new name",8));
  node.completeForeign(); node.advance(1); assert(node.foreign.size()==before_alias+3);
  node.completeForeign();
  auto changed=channel("#mt-longfast",32); changed.channel.secret[0]^=0x40;
  node.commit(0,changed); node.advance(1); const auto before_key=node.foreign.size();
  assert(node.sendMeshtextText(node.stored[0],"new key",7));
  node.completeForeign(); node.advance(1); assert(node.foreign.size()==before_key+2);
  node.completeForeign();

  for(bool fail_admission:{false,true}) {
    Preferences::reset(); MyMesh failure; active(failure);
    failure._prefs.flood_retry_attempts=0;
    assert(failure.sendMeshtextText(failure.stored[0],"first",5)); failure.completeForeign();
    const auto first_count=failure.foreign.size();
    failure.admit_foreign=!fail_admission; failure.advance(1);
    if(!fail_admission) {
      assert(failure.foreign_pending); failure.completeForeign(false);
    }
    const auto attempts=failure.foreign_admissions,packet=failure._mt_packet_id;
    for(unsigned i=0;i<100;++i) failure.advance(1);
    assert(!failure.foreign_pending && failure.foreign_admissions==attempts && failure._mt_packet_id==packet);
    assert(failure.foreign.size()==first_count+(fail_admission?0:1));
    failure.admit_foreign=true;
    assert(failure.sendMeshtextText(failure.stored[0],"another",7)); failure.completeForeign();
    failure.advance(1); assert(failure.foreign_pending);
    failure.completeForeign();
  }
  puts("PASS announcement-lifecycle");
}
int main(int argc,char** argv) {
  assert(argc==2);
  if(!strcmp(argv[1],"keys"))keys();
  else if(!strcmp(argv[1],"auto"))automatic();
  else if(!strcmp(argv[1],"conflicts"))conflicts();
  else if(!strcmp(argv[1],"manual"))manual();
  else if(!strcmp(argv[1],"settings"))settings();
  else if(!strcmp(argv[1],"receive"))receive();
  else if(!strcmp(argv[1],"transmit"))transmit();
  else if(!strcmp(argv[1],"commands"))commands();
  else if(!strcmp(argv[1],"cli"))privateCLI();
  else if(!strcmp(argv[1],"hops"))hops();
  else if(!strcmp(argv[1],"shortname"))shortname();
  else if(!strcmp(argv[1],"node"))nodeOverride();
  else if(!strcmp(argv[1],"nodeinfo"))nodeinfo();
  else if(!strcmp(argv[1],"auto-nodeinfo"))automaticNodeinfo();
  else if(!strcmp(argv[1],"heard-retries"))heardRetries();
  else if(!strcmp(argv[1],"strict-echo"))strictEcho();
  else if(!strcmp(argv[1],"delivery-lifecycle"))deliveryLifecycle();
  else if(!strcmp(argv[1],"delivery-contention"))deliveryContention();
  else if(!strcmp(argv[1],"delivery-timing"))deliveryTiming();
  else if(!strcmp(argv[1],"announcement-lifecycle"))announcementLifecycle();
  else assert(!"unknown test case");
}
