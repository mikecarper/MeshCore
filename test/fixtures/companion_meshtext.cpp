// Template compiled by test_companion_meshtext.py. Markers insert actual
// production method bodies; all boundary substitutes are visible below.
@FIXTURE@
#include <SHA256.h>
#include <helpers/CompanionChannelAlias.h>
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
  bool physical_airtime=false;
  uint32_t fixed_airtime=10;
  std::vector<std::pair<uint8_t,int>> airtime_calls;
  mesh::RadioProfiles& _profiles=p;
  bool supportsMeshtext() const override { return supported; }
  @PROFILE_AIRTIME@
  uint32_t getProfileAirtime(uint8_t profile,int length,uint8_t cr=0) override {
    airtime_calls.emplace_back(profile,length);
    return physical_airtime ? actualProfileAirtime(profile,length,cr):fixed_airtime;
  }
};
struct Serial {
  bool connected=false;
  std::vector<std::vector<uint8_t>> frames;
  bool isConnected() { return connected; }
  void writeFrame(const uint8_t* bytes,int size) { assert(size>=0); frames.emplace_back(bytes,bytes+size); }
};
struct UI {
  bool managed=false,screen=false;
  unsigned quiet_overrides=0;
  void setMessageNotificationOverride(bool value,bool suppress_screen=false) {
    managed=value;screen=value && suppress_screen;
    if(managed && screen) ++quiet_overrides;
  }
};
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
    uint8_t flood_retry_attempts=@DEFAULT_FLOOD_RETRIES@,flood_retry_max_path=1;
    uint8_t flood_retry_group_max_path=0xff,flood_retry_advert_enabled=1;
  } _prefs;
  mesh::LocalIdentity self_id;
  mesh::mt::Region _mt_region=mesh::mt::Region::Unset;
  uint32_t _mt_slot=0,_mt_node=0,_mt_packet_id=0,_mt_sent=0,_mt_failed=0,_mt_received=0;
  uint32_t _mt_default_node=0,_mt_node_override=0;
  uint8_t _mt_hop_limit=7;
  bool _mt_rebroadcast=false;
  char _mt_short_name[17]={};
  mesh::mt::SeenPackets _mt_seen;
  mesh::mt::PendingFrame _mt_delivery[mesh::mt::DeliverySlots];
  int8_t _mt_tx_slot=-1;
  uint8_t _mt_nodeinfo_fingerprint[MAX_GROUP_CHANNELS][8]={};
  bool _mt_nodeinfo_announced[MAX_GROUP_CHANNELS]={},_mt_nodeinfo_pending[MAX_GROUP_CHANNELS]={};
  uint32_t _mt_nodeinfo_generation=0,_mt_retried=0,_mt_heard=0,_mt_exhausted=0;
  uint32_t _mt_last_tx_completed_at=0;
  struct Random {
    uint32_t jitter=0;
    uint32_t nextInt(uint32_t low,uint32_t high) {
      assert(low==0 && high==201 && jitter<high); return jitter;
    }
  } random;
  bool _mt_settings_valid=true,_mt_active=false,_mt_profile_dirty=true,_mt_last_radio_ready=false;
  bool _mt_last_secondary_ready=false;
  bool _mt_delivery_config_dirty=true;
  uint32_t _mt_primary_generation=0,_mt_secondary_generation=0,_mt_intent_generation=0;
  float _mt_primary_frequency=0; const char* _mt_status="no Meshtext channels";
  bool _radio_available=true,foreign_pending=false,foreign_sending=false,admit_foreign=true,save_result=true,ok=false;
  uint32_t foreign_generation=0; uint8_t foreign_profile=255;
  unsigned foreign_admissions=0,meshcore_sends=0;
  std::vector<std::vector<uint8_t>> foreign,offline;
  size_t offline_capacity=4;
  int offline_queue_len=0;
  uint8_t pending_emergency_notices[(MAX_GROUP_CHANNELS+7)/8]={};
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
                             const char* short_name=nullptr,uint32_t node_override=UINT32_MAX,
                             uint8_t rebroadcast=UINT8_MAX);
  bool eraseMeshtextSettings();
  bool isMeshtextChannel(const ChannelDetails&) const;
  bool validateMeshtextChannelChange(int,const ChannelDetails&);
  void refreshMeshtextProfile();
  bool sendMeshtextText(const ChannelDetails&,const char*,size_t);
  bool sendMeshtextNodeInfo(const ChannelDetails&);
  bool queueMeshtextDelivery(const ChannelDetails&,const uint8_t*,size_t,bool);
  void maybeRebroadcastMeshtext(const ChannelDetails&,const uint8_t*,size_t);
  bool preemptMeshtextRelay();
  int meshtextChannelIndex(const ChannelDetails&,uint8_t[8]);
  bool meshtextDeliveryCurrent(const mesh::mt::PendingFrame&);
  uint8_t meshtextRetryLimit(const mesh::mt::PendingFrame&) const;
  uint32_t meshtextRetryDelay(const mesh::mt::PendingFrame&);
  void loopMeshtextDelivery();
  bool onForeignRadioFrame(const uint8_t*,size_t,uint8_t,uint32_t,float,float);
  void onForeignRadioSendComplete(bool);
  bool handleMeshtextCommand(const char*,char*,size_t);
  bool queueChannelText(uint8_t,const uint8_t[32],float,uint8_t,uint32_t,const char*,bool,bool=true);
  void seedEmergencyChannelNotice(uint8_t,const ChannelDetails&);
  void servicePendingChannelNotices();
  int getOfflineQueueCapacity() const { return offline_capacity; }
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
    offline.emplace_back(bytes,bytes+size); offline_queue_len=offline.size(); return true;
  }
  bool saveChannels() { return save_result; }
  bool savePrefs() { return save_result; }
  bool saveAdvertName(const char*);
  void writeErrFrame(uint8_t value) { error=value; }
  void writeOKFrame() { ok=true; }
  bool sendGroupMessage(uint32_t,mesh::GroupChannel&,const char*,const char*,int) { ++meshcore_sends; return true; }
  void handleSetChannelFrame(const uint8_t*);
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
  node.offline_capacity=8;
  const unsigned alerts=node._notifications.calls,quiet_overrides=node.ui.quiet_overrides;
  const size_t queued=node.offline.size(),pushes=node.serial.frames.size();
  assert(node.queueChannelText(0,ordinary_key,0,255,125,"#EMERGENCY: local notice",true,false));
  assert(node.offline.size()==queued+1 && node.serial.frames.size()==pushes+1);
  assert(node.serial.frames.back()==std::vector<uint8_t>{PUSH_CODE_MSG_WAITING});
  assert(node._notifications.calls==alerts && node.ui.quiet_overrides==quiet_overrides+1);
  assert(!node.ui.managed && !node.ui.screen); // Quiet suppression is scoped to this message.
  assert(node.queueChannelText(0,ordinary_key,0,0,126,"ordinary message",false));
  assert(node._notifications.calls==alerts+1 && node.ui.quiet_overrides==quiet_overrides+1);
  assert(!node.ui.managed && !node.ui.screen);
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
static void assertMediaMeshConfig(const mesh::RadioProfileConfig& config,float bandwidth) {
  assert(config.mode==mesh::RadioProfileMode::RxTx);
  assert(std::fabs(config.params.freq-911.150f)<0.0001f && config.params.bw==bandwidth);
  assert(config.params.sf==7 && config.params.cr==5 && !config.params.preamble);
  assert(config.params.protocol==mesh::RadioProfileProtocol::MeshCore);
}
static void channelPresets() {
  // These use the actual binary command branch and RadioProfileCLI image
  // writer. Channel persistence itself remains this fixture's save boundary.
  for(bool range:{false,true}) {
    Preferences::reset(); MyMesh node; node.beginMeshtext();
    const auto primary=node.radio.p.primary;
    const auto requested=channel(range?"r2-mediamesh-range":"#r2-mediamesh");
    applySet(node,0,requested);
    assert(node.ok && node.error==-1 && !node.radio.p.enabled());
    assertMediaMeshConfig(node._radio_profiles.savedSecondaryConfig(),range?125:250);
    assert(node._radio_profiles.manualSecondaryConfigured() && node.fs.files.count("/radio_profiles"));
    assert(!node._radio_profiles.hasReplyMutation() && !node.fs.files.count("/radio_profiles.reply"));
    ChannelDetails raw; assert(node.getChannelForSave(0,raw));
    assert(!strcmp(raw.name,requested.name) && !memcmp(raw.channel.secret,requested.channel.secret,32));
    node.advance(1999); assert(!node.radio.p.enabled());
    node.advance(1); assertMediaMeshConfig(node.radio.p.secondary,range?125:250);
    assert(node.radio.p.primary==primary && node.radio.p.cross==mesh::RadioCrossMode::Off);
    assert(!node.radio.p.secondary_temporary && !node._mt_active);
    MyMesh reboot; reboot.fs=node.fs; reboot.stored[0]=raw;
    reboot._radio_profiles.begin(&reboot.fs,&reboot.radio,&reboot.clock); reboot.beginMeshtext();
    assertMediaMeshConfig(reboot.radio.p.secondary,range?125:250);
    assert(reboot._radio_profiles.manualSecondaryConfigured() && !reboot._mt_active);
    assertUntouched(node); assertUntouched(reboot);
  }

  Preferences::reset(); MyMesh node; node.beginMeshtext();
  applySet(node,0,channel("#r2-mediamesh")); assert(node.ok); node.advance(2000);
  // The newest join/rename chooses the tuple, even with another named alias
  // present. Names do not continuously force RF over later operator choices.
  applySet(node,1,channel("#r2-mediamesh-range")); assert(node.ok);
  assertMediaMeshConfig(node._radio_profiles.savedSecondaryConfig(),125);
  assertMediaMeshConfig(node.radio.p.secondary,250);
  node.advance(2000); assertMediaMeshConfig(node.radio.p.secondary,125);
  auto renamed=channel("r2-mediamesh",16);
  applySet(node,1,renamed); assert(node.ok);
  assertMediaMeshConfig(node._radio_profiles.savedSecondaryConfig(),250);
  assertMediaMeshConfig(node.radio.p.secondary,125);
  node.advance(2000); assertMediaMeshConfig(node.radio.p.secondary,250);
  for(const char* command:{"set radio2 off","set radio2 912.5,250,8,5,rx,80"}) {
    node.cli(command); node.advance(2000);
    const auto saved=node._radio_profiles.savedSecondaryConfig();
    const auto running=node.radio.p.secondary;
    const auto disk=node.fs.files;
    for(bool edit_key:{false,true}) {
      if(edit_key) renamed.channel.secret[0]^=0x80;
      applySet(node,1,renamed); assert(node.ok);
      assert(node._radio_profiles.savedSecondaryConfig().mode==saved.mode
          && node._radio_profiles.savedSecondaryConfig().params==saved.params);
      node.advance(2000);
      assert(node.radio.p.secondary.mode==running.mode && node.radio.p.secondary.params==running.params);
      assert(node.fs.files==disk && !node._radio_profiles.hasReplyMutation());
    }
  }
  assertUntouched(node);

  for(bool fail_rf_commit:{false,true}) {
    Preferences::reset(); MyMesh failed; failed.beginMeshtext();
    failed.cli("set radio2 912.5,250,8,5,rx,80"); failed.advance(2000);
    failed.commit(0,channel("ordinary",16));
    const auto old_channel=failed.stored[0];
    const auto old_profile=failed.radio.p.secondary;
    const auto old_disk=failed.fs.files;
    if(fail_rf_commit) failed.fs.fail_rename_from={"/radio_profiles.reply"};
    else failed.save_result=false;
    applySet(failed,0,channel("#r2-mediamesh"));
    assert(!failed.ok && failed.error==ERR_CODE_FILE_IO_ERROR);
    assert(!strcmp(failed.stored[0].name,old_channel.name)
        && !memcmp(failed.stored[0].channel.secret,old_channel.channel.secret,32)
        && failed.stored[0].channel.tx_radio==old_channel.channel.tx_radio);
    assert(failed.fs.files==old_disk && !failed._radio_profiles.hasReplyMutation());
    assert(failed._radio_profiles.savedSecondaryConfig().mode==old_profile.mode
        && failed._radio_profiles.savedSecondaryConfig().params==old_profile.params);
    failed.advance(2000);
    assert(failed.radio.p.secondary.mode==old_profile.mode && failed.radio.p.secondary.params==old_profile.params);
    assertUntouched(failed);
  }
  Preferences::reset(); MyMesh conflicting; active(conflicting);
  const auto old_profile=conflicting.radio.p.secondary;
  const auto old_disk=conflicting.fs.files;
  applySet(conflicting,1,channel("#r2-mediamesh"));
  assert(!conflicting.ok && conflicting.error==ERR_CODE_BAD_STATE && !conflicting.stored[1].name[0]);
  assert(conflicting.radio.p.secondary.mode==old_profile.mode
      && conflicting.radio.p.secondary.params==old_profile.params);
  assert(conflicting.fs.files==old_disk && conflicting._mt_active);
  assert(!conflicting._radio_profiles.hasReplyMutation()); assertUntouched(conflicting);
  puts("PASS channel-presets");
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
    assert(Preferences::blob.size()==40 && !memcmp(Preferences::blob.data(),"MT04",4));
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
  assert(Preferences::writes==1 && !memcmp(Preferences::blob.data(),"MT04",4) && Preferences::blob[5]==3);
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
  assert(Preferences::blob.size()==40 && !memcmp(Preferences::blob.data(),"MT04",4));
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
    assert(Preferences::writes==1 && Preferences::blob.size()==40 && !memcmp(Preferences::blob.data(),"MT04",4));
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
  // A fully encoded local attempt can lose TX admission, but its CTR nonce
  // must remain consumed rather than being reused by the following metadata.
  assert(node._mt_packet_id==pending_id+1 && node.foreign.size()==pending_count);
  assert(!node.saveMeshtextSettings(mesh::mt::Region::US,0));
  node.releaseForeign();
  assert(node.handleMeshtextCommand("set mt.node !12345678",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node.handleMeshtextCommand("set mt.shortname MIKE",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  strcpy(node._prefs.node_name,"Mike Carper");
  assert(node.handleMeshtextCommand("send mt.nodeinfo 0",reply,sizeof(reply)) && strstr(reply,"queued"));
  assert(node.foreign.back()==std::vector<uint8_t>(expected_named,expected_named+sizeof(expected_named)));
  node.releaseForeign();
  auto text=decodeOwnText(node,"hello",5);
  assert(text.id==0x1234567b && !strcmp(text.text,"hello"));
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
  node.advance(250);
  assert(node.foreign.size()==2 && node.foreign.back()==std::vector<uint8_t>(expected,expected+sizeof(expected)));
  // Exact independent protobuf/AES bytes prove only User/NodeInfo was sent:
  // no position packet, coordinates, MAC, public key or custom name envelope.
  node.completeForeign(); node.advance(3600000); assert(node.foreign.size()==2);
  assert(node.sendMeshtextText(node.stored[0],"again",5));
  node.completeForeign(); node.advance(100000); assert(node.foreign.size()==3);
  node.commit(1,channel("mt-Team",32));
  assert(node.sendMeshtextText(node.stored[1],"private",7));
  node.completeForeign(); node.advance(250); assert(node.foreign.size()==5);
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
  failure.completeForeign(); failure.advance(250); assert(failure.foreign.size()==3);
  failure.completeForeign();

  Preferences::reset(); MyMesh manual; active(manual); announce(manual);
  manual._prefs.flood_retry_attempts=0;
  assert(manual.sendMeshtextText(manual.stored[0],"already named",13));
  manual.completeForeign(); manual.advance(100000); assert(manual.foreign.size()==2);
  puts("PASS auto-nodeinfo");
}
static void automaticNodeinfoQuiet() {
  Preferences::reset(); MyMesh node; active(node);
  node._prefs.flood_retry_attempts=0;
  assert(node.sendMeshtextText(node.stored[0],"first",5)); node.completeForeign();
  assert(node._mt_nodeinfo_pending[0] && !node._mt_last_tx_completed_at);
  node.advance(0); assert(node.foreign.size()==1 && !node.foreign_pending);
  node.advance(249); assert(node.foreign.size()==1 && node._mt_nodeinfo_pending[0]);
  node.advance(1); assert(node.foreign.size()==2 && node.foreign_pending);
  node.completeForeign();

  // Retrying chats are not blocked by the metadata quiet gap. Every actual
  // completion restarts that gap, so the announcement follows the burst.
  Preferences::reset(); MyMesh retries; active(retries);
  retries._prefs.flood_retry_attempts=2;
  assert(retries.sendMeshtextText(retries.stored[0],"retry",5));
  const auto original=retries.foreign.back(); retries.completeForeign();
  auto* pending=pendingChat(retries); assert(pending && pending->due==210);
  for(unsigned attempt=0;attempt<2;++attempt) {
    advanceTo(retries,pending->due);
    assert(retries.foreign_pending && retries.foreign.back()==original
        && retries._mt_nodeinfo_pending[0]);
    const auto prior=retries._mt_last_tx_completed_at;
    retries.completeForeign();
    assert(retries._mt_last_tx_completed_at==millis()
        && retries._mt_last_tx_completed_at!=prior);
    pending=pendingChat(retries); assert(pending);
  }
  const auto count=retries.foreign.size();
  retries.advance(249); assert(retries.foreign.size()==count && !retries.foreign_pending);
  retries.advance(1); assert(retries.foreign.size()==count+1
      && retries.foreign.back()!=original && retries.foreign_pending);
  retries.completeForeign();

  Preferences::reset(); MyMesh wrap; active(wrap); wrap._prefs.flood_retry_attempts=0;
  g_mock_millis=UINT32_MAX-100;
  assert(wrap.sendMeshtextText(wrap.stored[0],"wrap",4)); wrap.completeForeign();
  wrap.advance(249); assert(wrap.foreign.size()==1 && !wrap.foreign_pending);
  wrap.advance(1); assert(wrap.foreign.size()==2 && wrap.foreign_pending);
  wrap.completeForeign();

  // A failed TX does not arm metadata or move the successful-completion
  // timestamp. Explicit manual NodeInfo still admits immediately, by choice.
  Preferences::reset(); MyMesh failure; active(failure); failure._prefs.flood_retry_attempts=0;
  failure.advance(1000);
  assert(failure.sendMeshtextText(failure.stored[0],"failed",6)); failure.completeForeign(false);
  assert(!failure._mt_last_tx_completed_at && !failure._mt_nodeinfo_pending[0]);
  failure.advance(1000); assert(failure.foreign.size()==1);
  assert(failure.sendMeshtextText(failure.stored[0],"success",7)); failure.completeForeign();
  assert(failure._mt_last_tx_completed_at==millis());
  assert(failure.sendMeshtextNodeInfo(failure.stored[0]) && failure.foreign.size()==3);
  failure.completeForeign(); failure.advance(250); assert(failure.foreign.size()==3);
  puts("PASS auto-nodeinfo-quiet");
}
static void defaultTries() {
  for (bool echo_on_last : {false, true}) {
    Preferences::reset(); MyMesh node; active(node); announce(node);
    assert(node._prefs.flood_retry_attempts==15);
    char reply[256];
    assert(node.handleMeshtextCommand("get mt.retry",reply,sizeof(reply)));
    assert(strstr(reply,"count=15"));
    const auto before=node.foreign.size();
    assert(node.sendMeshtextText(node.stored[0],"fifteen tries",13));
    const auto original=node.foreign.back();
    const auto next_id=node._mt_packet_id;
    node.completeForeign();
    for (unsigned attempt=1;attempt<15;++attempt) {
      auto* frame=pendingChat(node); assert(frame);
      advanceTo(node,frame->due);
      assert(node.foreign_pending && node.foreign.back()==original);
      assert(node._mt_packet_id==next_id);
      node.completeForeign();
    }
    assert(node.foreign.size()==before+15 && node._mt_retried==14);
    auto* frame=pendingChat(node); assert(frame); // Final echo window remains.
    if (echo_on_last) {
      const auto echo=repeated(original);
      assert(receiveFrame(node,echo.data(),echo.size()));
      assert(node._mt_heard==1 && node._mt_exhausted==0);
    } else {
      advanceTo(node,frame->due);
      assert(node._mt_exhausted==1 && node._mt_heard==0);
    }
    node.advance(3600000);
    assert(!pendingChat(node) && !node.foreign_pending && node.foreign.size()==before+15);
  }
  puts("PASS default-tries");
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
  for(unsigned i=0;i<14;++i) {
    auto* current=pendingChat(capped); assert(current);
    advanceTo(capped,current->due);
    assert(capped.foreign_pending && capped.foreign.back()==cap_original);
    capped.completeForeign();
  }
  assert(capped._mt_retried==14 && capped.foreign.size()==16);
  auto* current=pendingChat(capped); assert(current); advanceTo(capped,current->due);
  assert(capped._mt_exhausted==1 && !pendingChat(capped));
  capped.advance(3600000); assert(capped.foreign.size()==16);

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
  node.completeForeign(); node.advance(250); assert(node.foreign.size()==before_alias+3);
  node.completeForeign();
  auto changed=channel("#mt-longfast",32); changed.channel.secret[0]^=0x40;
  node.commit(0,changed); node.advance(1); const auto before_key=node.foreign.size();
  assert(node.sendMeshtextText(node.stored[0],"new key",7));
  node.completeForeign(); node.advance(250); assert(node.foreign.size()==before_key+2);
  node.completeForeign();

  for(bool fail_admission:{false,true}) {
    Preferences::reset(); MyMesh failure; active(failure);
    failure._prefs.flood_retry_attempts=0;
    assert(failure.sendMeshtextText(failure.stored[0],"first",5)); failure.completeForeign();
    const auto first_count=failure.foreign.size();
    failure.admit_foreign=!fail_admission; failure.advance(250);
    if(!fail_admission) {
      assert(failure.foreign_pending); failure.completeForeign(false);
    }
    const auto attempts=failure.foreign_admissions,packet=failure._mt_packet_id;
    for(unsigned i=0;i<100;++i) failure.advance(1);
    assert(!failure.foreign_pending && failure.foreign_admissions==attempts && failure._mt_packet_id==packet);
    assert(failure.foreign.size()==first_count+(fail_admission?0:1));
    failure.admit_foreign=true;
    assert(failure.sendMeshtextText(failure.stored[0],"another",7)); failure.completeForeign();
    failure.advance(250); assert(failure.foreign_pending);
    failure.completeForeign();
  }
  puts("PASS announcement-lifecycle");
}
static void advertNameDelivery() {
  // A failed save or unchanged effective name must preserve the announced
  // state and the exact retained packet awaiting its one metadata retry.
  Preferences::reset(); MyMesh node; active(node);
  assert(node.sendMeshtextNodeInfo(node.stored[0]));
  const auto original=node.foreign.back(); node.completeForeign();
  auto& metadata=node._mt_delivery[mesh::mt::ChatDeliverySlots];
  assert(metadata.length && node._mt_nodeinfo_announced[0]);
  const auto old_due=metadata.due;
  node.save_result=false;
  assert(!node.saveAdvertName("Rejected") && !strcmp(node._prefs.node_name,"Test"));
  assert(metadata.length && !metadata.cancelled && node._mt_nodeinfo_announced[0]);
  node.save_result=true;
  assert(node.saveAdvertName("Test"));
  assert(metadata.length && !metadata.cancelled && metadata.due==old_due
      && node._mt_nodeinfo_announced[0]);
  assert(node.saveAdvertName("Renamed") && !strcmp(node._prefs.node_name,"Renamed"));
  assert(metadata.cancelled && !node._mt_nodeinfo_announced[0] && !node._mt_nodeinfo_pending[0]);
  advanceTo(node,old_due); node.advance(100000);
  assert(!metadata.length && node.foreign.size()==1 && !node.foreign_pending
      && !node._mt_retried && !node._mt_failed);
  // No transmission just from renaming. The next new chat advertises the
  // updated name with independently generated standard User/AES bytes.
  node._prefs.flood_retry_attempts=0;
  assert(node.sendMeshtextText(node.stored[0],"new name",8));
  node.completeForeign(); node.advance(250);
  const uint8_t expected[]=@NODEINFO_RENAMED@;
  assert(node.foreign.size()==3 && node.foreign.back()!=original
      && node.foreign.back()==std::vector<uint8_t>(expected,expected+sizeof(expected)));
  node.completeForeign(); node.advance(100000); assert(node.foreign.size()==3);

  // A queued old-name frame is cancelled. One which is already physically
  // transmitting may finish, but cannot re-arm metadata or its old retry.
  for(bool sending:{false,true}) {
    Preferences::reset(); MyMesh busy; active(busy);
    assert(busy.sendMeshtextNodeInfo(busy.stored[0]));
    busy.foreign_sending=sending;
    assert(busy.saveAdvertName("Renamed")); busy.advance(1);
    assert(busy.foreign_pending==sending);
    if(sending) busy.completeForeign();
    busy.advance(100000);
    assert(busy.foreign.size()==1 && !busy._mt_nodeinfo_announced[0]
        && !busy._mt_nodeinfo_pending[0] && !busy._mt_failed && !busy._mt_retried);
  }

  // Clearing a deferred first-use intent does not turn the edit into TX, and
  // old text ciphertext remains independently eligible for its own retry.
  Preferences::reset(); MyMesh chat; active(chat);
  assert(chat.sendMeshtextText(chat.stored[0],"text",4));
  const auto text=chat.foreign.back(); chat.completeForeign();
  auto* pending=pendingChat(chat); assert(pending && chat._mt_nodeinfo_pending[0]);
  assert(chat.saveAdvertName("Renamed") && !pending->cancelled
      && !chat._mt_nodeinfo_pending[0]);
  chat.advance(1); assert(chat.foreign.size()==1);
  advanceTo(chat,pending->due);
  assert(chat.foreign.size()==2 && chat.foreign.back()==text);
  chat.completeForeign(); chat.advance(1);
  assert(!chat._mt_nodeinfo_pending[0] && chat.foreign.size()==2);
  auto echo=repeated(text); assert(receiveFrame(chat,echo.data(),echo.size()));
  chat.advance(100000); assert(chat.foreign.size()==2);
  puts("PASS advert-name-delivery");
}
static void readinessCache() {
  // A delayed same-tuple publication releases its busy state without changing
  // either RF generation. An MT channel installed during that delay must not
  // remain permanently stuck behind a cached busy result.
  for(const char* command:{"set radio2 909.5,500,5,5,rxtx,32","set tempradio2 off"}) {
    Preferences::reset(); MyMesh node; node.beginMeshtext();
    node.cli("set radio2 909.5,500,5,5,rxtx,32"); node.advance(2001);
    const auto generation=node.radio.p.generation[1];
    const auto initial=node.radio.p.secondary;
    assert(initial.params.protocol==mesh::RadioProfileProtocol::MeshCore);
    node.cli(command); node.commit(0,channel("mt-labtest",16));
    assert(!node._mt_active && !node._radio_profiles.secondaryRuntimeAvailable());
    node.advance(1999);
    assert(!node._mt_active && node.radio.p.generation[1]==generation);
    node.advance(1);
    assert(node._radio_profiles.secondaryRuntimeAvailable() && node._mt_active
        && node.radio.p.secondary.params.protocol==mesh::RadioProfileProtocol::Meshtext
        && node.radio.p.secondary.params.preamble==16);
    assertUntouched(node); // Readiness/configuration alone still never transmits.
    const auto active_generation=node.radio.p.generation[1];
    node.advance(1000);
    assert(node._mt_active && node.radio.p.generation[1]==active_generation);

    // Readiness caching must remain read-only and preserve external ownership:
    // do not force the overlay back when another runtime owner changed radio2.
    auto external=initial; external.params.freq=910.5f;
    node.radio.p.setSecondary(external,false);
    const auto external_generation=node.radio.p.generation[1];
    node.advance(1);
    assert(!node._mt_active && !node._radio_profiles.secondaryRuntimeAvailable());
    node.commit(0,channel("mt-other",16)); node.advance(100000);
    assert(!node._mt_active && node.radio.p.generation[1]==external_generation
        && node.radio.p.secondary.params==external.params
        && node.radio.p.secondary.mode==external.mode);
    assertUntouched(node);
  }
  puts("PASS readiness-cache");
}
static const uint8_t relay_short[]=@RELAY_SHORT@,relay_long[]=@RELAY_LONG@,
    relay_nodeinfo[]=@RELAY_NODEINFO@,relay_private128[]=@RELAY_PRIVATE128@,
    relay_private256[]=@RELAY_PRIVATE256@;
static mesh::mt::PendingFrame* pendingRelay(MyMesh& node) {
  mesh::mt::PendingFrame* found=nullptr;
  for(auto& frame:node._mt_delivery) if(frame.length && frame.relayed) {
    assert(!found);found=&frame; // Retained forwarding is bounded to ONE copy.
  }
  return found;
}
static void enableRelay(MyMesh& node) {
  char reply[256];assert(node.handleMeshtextCommand("set mt.rebroadcast on",reply,sizeof(reply)));
  assert(!strncmp(reply,"OK",2) && node._mt_rebroadcast);node.advance(0);
}
static std::vector<uint8_t> plannedRelay(MyMesh& node,const uint8_t* raw,size_t length) {
  std::vector<uint8_t> expected(raw,raw+length);
  expected[12]=(raw[12]&0xf8)|((raw[12]&7)-1);expected[15]=uint8_t(node._mt_node);
  return expected;
}
static void assertRelayBytes(MyMesh& node,const uint8_t* raw,size_t length) {
  const auto* frame=pendingRelay(node);assert(frame && frame->length==length);
  const auto expected=plannedRelay(node,raw,length);
  assert(!memcmp(frame->bytes,expected.data(),length));
  assert(!frame->nodeinfo && !frame->sent && !frame->retries && !frame->cancelled);
  assert(frame->generation==node.radio.p.generation[1]);
}
static void rebroadcastSettings() {
  Preferences::reset();MyMesh node;active(node);char reply[256];
  assert(!node._mt_rebroadcast);
  for(const char* get:{"get mt.rebroadcast","get mt.maybe_rebroadcast"}) {
    assert(node.handleMeshtextCommand(get,reply,sizeof(reply)) && strstr(reply,"> off;"));
  }
  assert(node.handleMeshtextCommand("get mt.status",reply,sizeof(reply)) && strstr(reply,"rebroadcast=off"));
  const auto original=Preferences::blob;const auto packet=node._mt_packet_id;
  for(const char* invalid:{"set mt.rebroadcast ","set mt.rebroadcast yes","set mt.rebroadcast true",
      "set mt.rebroadcast 1","set mt.rebroadcast ON","set mt.rebroadcast on junk",
      "set mt.maybe_rebroadcast -1"}) {
    assert(node.handleMeshtextCommand(invalid,reply,sizeof(reply)) && !strncmp(reply,"Error",5));
    assert(!node._mt_rebroadcast && Preferences::blob==original);
  }
  assert(!node.saveMeshtextSettings(node._mt_region,node._mt_slot,UINT8_MAX,nullptr,UINT32_MAX,2));
  assert(node.handleMeshtextCommand("set mt.maybe_rebroadcast on",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node._mt_rebroadcast && Preferences::blob.size()==40 && !memcmp(Preferences::blob.data(),"MT04",4));
  assert(Preferences::blob[6]==1 && Preferences::blob[7]==0 && node._mt_packet_id==packet);
  uint8_t digest[4];mesh::Utils::sha256(digest,4,Preferences::blob.data(),36);
  assert(!memcmp(digest,Preferences::blob.data()+36,4));
  assert(node.handleMeshtextCommand("set mt.hops 3",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(node._mt_rebroadcast && node._mt_hop_limit==3 && Preferences::blob[6]==1);
  MyMesh reboot;reboot.stored[0]=node.stored[0];reboot.beginMeshtext();
  assert(reboot._mt_active && reboot._mt_rebroadcast && reboot._mt_hop_limit==3);assertUntouched(reboot);
  const auto valid=Preferences::blob;
  for(unsigned fault=0;fault<3;++fault) {
    Preferences::blob=valid;
    if(fault==0) Preferences::blob[6]=2;
    if(fault==1) Preferences::blob[7]=1;
    if(fault==2) {memcpy(Preferences::blob.data(),"MT03",4);Preferences::blob[6]=1;}
    mesh::Utils::sha256(Preferences::blob.data()+36,4,Preferences::blob.data(),36);
    MyMesh invalid;invalid.stored[0]=node.stored[0];invalid.beginMeshtext();
    assert(!invalid._mt_settings_valid && !invalid._mt_active && !invalid._mt_rebroadcast);assertUntouched(invalid);
  }
  Preferences::blob=valid;Preferences::fail_write=true;
  assert(node.handleMeshtextCommand("set mt.rebroadcast off",reply,sizeof(reply)) && !strncmp(reply,"Error",5));
  assert(node._mt_rebroadcast && Preferences::blob==valid && !node._mt_active && !node._mt_settings_valid);
  Preferences::fail_write=false;Preferences::fail_read=true;
  assert(!node.saveMeshtextSettings(node._mt_region,node._mt_slot,UINT8_MAX,nullptr,UINT32_MAX,0));
  assert(node._mt_rebroadcast && !node._mt_active && !node._mt_settings_valid && Preferences::blob[6]==0);
  Preferences::fail_read=false;MyMesh recovered;recovered.stored[0]=node.stored[0];recovered.beginMeshtext();
  assert(recovered._mt_active && recovered._mt_settings_valid && !recovered._mt_rebroadcast);
  assert(recovered.handleMeshtextCommand("set mt.rebroadcast on",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
  assert(recovered.eraseMeshtextSettings() && !recovered._mt_rebroadcast && Preferences::blob.empty());
  const uint8_t mt01[]=@LEGACY_SETTINGS@,mt02[]=@LEGACY_HOPS_SETTINGS@,mt03[]=@LEGACY_MT03_SETTINGS@,
      mt03_named[]=@LEGACY_MT03_NAMED@;
  const std::vector<std::vector<uint8_t>> legacy={
      {mt01,mt01+sizeof(mt01)},{mt02,mt02+sizeof(mt02)},{mt03,mt03+sizeof(mt03)},
      {mt03_named,mt03_named+sizeof(mt03_named)}};
  for(const auto& image:legacy) {
    Preferences::reset();Preferences::blob=image;MyMesh old;old.stored[0]=channel("#mt-longfast");old.beginMeshtext();
    assert(old._mt_active && old._mt_settings_valid && !old._mt_rebroadcast && Preferences::writes==0);
    assert(Preferences::blob==image && receiveFrame(old,relay_short,sizeof(relay_short)));
    old.advance(100000);assertUntouched(old);assert(!pendingRelay(old));
    if(image.size()==40 && image[12]) {
      assert(!strcmp(old._mt_short_name,"OLD") && old._mt_node_override==0x12345678 && old._mt_node==0x12345678);
    }
    const uint32_t old_node=old._mt_node,old_override=old._mt_node_override,old_slot=old._mt_slot;
    const uint8_t old_hops=old._mt_hop_limit;const std::string old_name=old._mt_short_name;
    assert(old.handleMeshtextCommand("set mt.rebroadcast on",reply,sizeof(reply)) && !strncmp(reply,"OK",2));
    assert(old._mt_rebroadcast && !memcmp(Preferences::blob.data(),"MT04",4) && Preferences::writes==1);
    assert(old._mt_node==old_node && old._mt_node_override==old_override && old._mt_slot==old_slot
        && old._mt_hop_limit==old_hops && old_name==old._mt_short_name);assertUntouched(old);
  }
  puts("PASS rebroadcast-settings");
}
static void rebroadcastTiming() {
  Preferences::reset();MyMesh off;active(off);
  assert(receiveFrame(off,relay_short,sizeof(relay_short)));off.advance(100000);
  assert(!pendingRelay(off) && off.offline.size()==1);assertUntouched(off);
  for(uint32_t jitter:{0U,200U}) {
    Preferences::reset();MyMesh node;active(node);enableRelay(node);node.random.jitter=jitter;
    const auto packet=node._mt_packet_id;
    assert(receiveFrame(node,relay_short,sizeof(relay_short)));assertRelayBytes(node,relay_short,sizeof(relay_short));
    const auto due=pendingRelay(node)->due;assert(uint32_t(due-millis())==50+jitter/10);
    assert(node.radio.airtime_calls.back()==std::make_pair(uint8_t(1),int(sizeof(relay_short))));
    node.advance(uint32_t(due-millis())-1);assertUntouched(node);
    node.advance(1);assert(node.foreign_pending && node.foreign.size()==1);
    assert(node.foreign.back()==plannedRelay(node,relay_short,sizeof(relay_short)));
    assert(node.foreign_profile==1 && node.foreign_generation==node.radio.p.generation[1]);
    assert(node._mt_packet_id==packet && node._mt_received==1);
    node.completeForeign();assert(!pendingRelay(node) && node._mt_sent==1 && !node._mt_retried && !node._mt_exhausted);
    for(bool pending:node._mt_nodeinfo_pending) assert(!pending);
    for(bool announced:node._mt_nodeinfo_announced) assert(!announced);
    node.advance(3600000);assert(node.foreign.size()==1 && node._mt_packet_id==packet && !node.meshcore_sends);
  }
  Preferences::reset();MyMesh wrap;active(wrap);enableRelay(wrap);g_mock_millis=UINT32_MAX-20;
  assert(receiveFrame(wrap,relay_short,sizeof(relay_short)));assert(uint32_t(pendingRelay(wrap)->due-millis())==50);
  wrap.advance(49);assertUntouched(wrap);wrap.advance(1);assert(wrap.foreign_pending);wrap.completeForeign();
  Preferences::reset();MyMesh zero;active(zero);enableRelay(zero);zero.radio.fixed_airtime=0;
  assert(receiveFrame(zero,relay_short,sizeof(relay_short)));zero.advance(100000);assert(!pendingRelay(zero));assertUntouched(zero);
  // A later explicit chat, rather than relaying by itself, announces the
  // standard CLIENT role with no position / direct-message capability.
  const uint8_t expected_info[]=@NODEINFO_REBROADCAST@;
  Preferences::reset();MyMesh named;active(named);enableRelay(named);named._prefs.flood_retry_attempts=0;
  assert(receiveFrame(named,relay_short,sizeof(relay_short)));advanceTo(named,pendingRelay(named)->due);named.completeForeign();
  named.advance(1000);assert(named.foreign.size()==1);
  assert(named.sendMeshtextText(named.stored[0],"human",5));named.completeForeign();named.advance(250);
  assert(named.foreign.size()==3 && named.foreign.back()==std::vector<uint8_t>(expected_info,expected_info+sizeof(expected_info)));
  puts("PASS rebroadcast-timing");
}
static void rebroadcastDuplicates() {
  for(uint8_t remaining:{uint8_t(2),uint8_t(0)}) {
    Preferences::reset();MyMesh node;active(node);enableRelay(node);
    assert(receiveFrame(node,relay_short,sizeof(relay_short)));const auto due=pendingRelay(node)->due;
    assert(receiveFrame(node,relay_short,sizeof(relay_short))); // Original sender's retry is not a relay.
    assert(pendingRelay(node) && pendingRelay(node)->due==due && node.offline.size()==1);
    auto peer=plannedRelay(node,relay_short,sizeof(relay_short));peer[12]=(peer[12]&0xf8)|remaining;
    peer[14]=0x42;peer[15]=0x43;assert(receiveFrame(node,peer.data(),peer.size()));
    assert(!pendingRelay(node) && node.offline.size()==1 && !node._mt_heard);
    assert(receiveFrame(node,relay_short,sizeof(relay_short)));node.advance(100000);
    assert(!pendingRelay(node));assertUntouched(node);
  }
  Preferences::reset();MyMesh exact;active(exact);enableRelay(exact);
  assert(receiveFrame(exact,relay_short,sizeof(relay_short)));
  const auto expected=plannedRelay(exact,relay_short,sizeof(relay_short));
  for(unsigned changed:{0U,4U,8U,12U,13U,16U}) {
    auto wrong=expected;wrong[changed]^=changed==12 ? 0x10:1;
    receiveFrame(exact,wrong.data(),wrong.size());assert(pendingRelay(exact));
  }
  auto shorter=expected;shorter.pop_back();receiveFrame(exact,shorter.data(),shorter.size());assert(pendingRelay(exact));
  assert(!exact.onForeignRadioFrame(expected.data(),expected.size(),0,exact.radio.p.generation[1],3,-90));
  assert(!exact.onForeignRadioFrame(expected.data(),expected.size(),1,exact.radio.p.generation[1]-1,3,-90));
  assert(pendingRelay(exact));
  for(bool inflight:{false,true}) {
    Preferences::reset();MyMesh node;active(node);enableRelay(node);
    assert(receiveFrame(node,relay_short,sizeof(relay_short)));advanceTo(node,pendingRelay(node)->due);
    assert(node.foreign_pending);node.foreign_sending=inflight;
    auto peer=plannedRelay(node,relay_short,sizeof(relay_short));const auto failed=node._mt_failed;
    assert(receiveFrame(node,peer.data(),peer.size()));
    assert(node.foreign_pending==inflight);
    if(inflight) {assert(pendingRelay(node)->cancelled);node.completeForeign();}
    assert(!pendingRelay(node) && node._mt_failed==failed && !node._mt_retried);
    node.advance(100000);assert(node.foreign.size()==1);
    for(bool pending:node._mt_nodeinfo_pending) assert(!pending);
  }
  puts("PASS rebroadcast-duplicates");
}
static void rebroadcastLifecycle() {
  for(unsigned change=0;change<7;++change) {
    Preferences::reset();MyMesh node;active(node);enableRelay(node);
    assert(receiveFrame(node,relay_short,sizeof(relay_short)));assert(pendingRelay(node));
    char reply[256];
    switch(change) {
      case 0:assert(node.handleMeshtextCommand("set mt.rebroadcast off",reply,sizeof(reply)) && !strncmp(reply,"OK",2));break;
      case 1:node.commit(0,{});break;
      case 2:node.commit(0,channel("#mt-longfast",32));break;
      case 3:node.self_id.pub_key[0]=43;node.refreshMeshtextIdentity();break;
      case 4:node._radio_available=false;break;
      case 5:node.cli("set radio2 off");break;
      case 6:node.cli("set tempradio2 909.5,500,5,5,rx,1,32");break;
    }
    node.advance(2000);node.advance(100000);
    assert(!pendingRelay(node) && !node.foreign_pending && !node._mt_retried && !node._mt_exhausted);assertUntouched(node);
  }
  Preferences::reset();MyMesh expired;active(expired);enableRelay(expired);
  assert(receiveFrame(expired,relay_short,sizeof(relay_short)));expired.admit_foreign=false;
  advanceTo(expired,pendingRelay(expired)->due);assert(pendingRelay(expired));
  expired.advance(59999);assert(pendingRelay(expired));expired.advance(2);assert(!pendingRelay(expired));
  expired.admit_foreign=true;expired.advance(100000);assertUntouched(expired);
  Preferences::reset();MyMesh priority;active(priority);enableRelay(priority);
  assert(receiveFrame(priority,relay_short,sizeof(relay_short)));const auto packet=priority._mt_packet_id;
  assert(priority.sendMeshtextText(priority.stored[0],"human",5));
  assert(!pendingRelay(priority) && priority.foreign_pending && priority.foreign.size()==1 && priority._mt_packet_id==packet+1);
  mesh::mt::DecodedText decoded;const auto& raw=priority.foreign.back();
  assert(mesh::mt::decodeText(raw.data(),raw.size(),"LongFast",mesh::mt::DefaultPsk,16,1,decoded));
  assert(!strcmp(decoded.text,"human") && decoded.from==priority._mt_node);
  for(unsigned invalid=0;invalid<3;++invalid) {
    Preferences::reset();MyMesh node;active(node);enableRelay(node);
    assert(receiveFrame(node,relay_short,sizeof(relay_short)));
    const auto retained=*pendingRelay(node);std::string oversized(229,'x');
    const char invalid_utf8[]={char(0xc0),char(0xaf)};const char embedded_nul[]={'x',0,'y'};
    const char* text=invalid==0 ? oversized.data():invalid==1 ? invalid_utf8:embedded_nul;
    const size_t length=invalid==0 ? oversized.size():invalid==1 ? sizeof(invalid_utf8):sizeof(embedded_nul);
    assert(!node.sendMeshtextText(node.stored[0],text,length));
    const auto* current=pendingRelay(node);
    assert(current && current->length==retained.length && current->due==retained.due && !current->cancelled);
    assert(!memcmp(current->bytes,retained.bytes,retained.length));assertUntouched(node);
  }
  for(bool inflight:{false,true}) {
    Preferences::reset();MyMesh node;active(node);enableRelay(node);
    assert(receiveFrame(node,relay_short,sizeof(relay_short)));advanceTo(node,pendingRelay(node)->due);
    node.foreign_sending=inflight;const auto packet=node._mt_packet_id,failed=node._mt_failed;
    const bool accepted=node.sendMeshtextText(node.stored[0],"priority",8);
    const uint32_t after_attempt=node._mt_packet_id;
    assert(after_attempt>=packet && after_attempt<=packet+1);
    assert(accepted==!inflight);
    if(inflight) {
      assert(node.foreign_pending && pendingRelay(node)->cancelled);
      node.completeForeign();assert(!pendingRelay(node));
      assert(node.sendMeshtextText(node.stored[0],"priority",8));
    }
    assert(node.foreign_pending && !pendingRelay(node) && node._mt_packet_id==after_attempt+unsigned(inflight) && node._mt_failed==failed);
    mesh::mt::DecodedText sent;const auto& bytes=node.foreign.back();
    assert(mesh::mt::decodeText(bytes.data(),bytes.size(),"LongFast",mesh::mt::DefaultPsk,16,1,sent));
    assert(!strcmp(sent.text,"priority") && sent.from==node._mt_node && sent.id==node._mt_packet_id-1);
  }
  Preferences::reset();MyMesh bounded;active(bounded);enableRelay(bounded);
  assert(receiveFrame(bounded,relay_short,sizeof(relay_short)));const auto retained=pendingRelay(bounded)->length;
  assert(receiveFrame(bounded,relay_long,sizeof(relay_long)));
  assert(pendingRelay(bounded)->length==retained && bounded.offline.size()==2);
  Preferences::reset();MyMesh full;active(full);enableRelay(full);
  for(unsigned i=0;i<mesh::mt::ChatDeliverySlots;++i) {
    assert(full.sendMeshtextText(full.stored[0],"local",5));full.completeForeign();
  }
  const auto count=full.foreign.size();assert(receiveFrame(full,relay_short,sizeof(relay_short)));
  assert(!pendingRelay(full) && full.foreign.size()==count && full.offline.size()==1);
  puts("PASS rebroadcast-lifecycle");
}
static void rebroadcastGuardsAndPhy() {
  for(unsigned invalid=0;invalid<7;++invalid) {
    Preferences::reset();MyMesh node;active(node);enableRelay(node);
    std::vector<uint8_t> raw(relay_short,relay_short+sizeof(relay_short));
    if(invalid==0) raw[12]&=0xf8; // No hops left.
    if(invalid==1) raw[12]=(1U<<5)|3; // Malformed hop_start < hop_limit.
    if(invalid==2) raw[14]=1; // Explicit next-hop routing is not a broadcast relay.
    if(invalid==3) raw[0]=1; // A DM / non-broadcast destination.
    if(invalid==4) raw.assign(relay_nodeinfo,relay_nodeinfo+sizeof(relay_nodeinfo));
    if(invalid==5) raw.assign(rx_other,rx_other+sizeof(rx_other));
    if(invalid==6) raw.resize(mesh::mt::HeaderSize); // Opaque/truncated ciphertext.
    receiveFrame(node,raw.data(),raw.size());node.advance(100000);
    assert(!pendingRelay(node));assertUntouched(node);
  }
  Preferences::reset();MyMesh capacity;active(capacity);enableRelay(capacity);capacity.offline_capacity=0;
  assert(receiveFrame(capacity,relay_short,sizeof(relay_short)));
  assert(!pendingRelay(capacity) && !capacity._mt_seen.seen(0x11223344,201));
  capacity.offline_capacity=4;assert(receiveFrame(capacity,relay_short,sizeof(relay_short)));assert(pendingRelay(capacity));
  for(unsigned key_bytes:{16U,32U}) {
    Preferences::reset();MyMesh node;node.beginMeshtext();
    node.cli(key_bytes==16 ? "set radio2 909.5,500,7,5,rxtx,32":"set radio2 909.5,125,9,5,rxtx,32");
    node.advance(2000);node.commit(0,channel("mt-Team",key_bytes));assert(node._mt_active);enableRelay(node);
    node.radio.physical_airtime=true;node.radio.airtime_calls.clear();node.random.jitter=key_bytes==16 ? 0:200;
    const auto* raw=key_bytes==16 ? relay_private128:relay_private256;
    const size_t length=key_bytes==16 ? sizeof(relay_private128):sizeof(relay_private256);
    const uint32_t airtime=node.radio.actualProfileAirtime(1,length);
    const auto packet=node._mt_packet_id;
    assert(receiveFrame(node,raw,length));assertRelayBytes(node,raw,length);
    assert((node.radio.airtime_calls==std::vector<std::pair<uint8_t,int>>({{1,int(length)}})));
    const uint32_t delay=airtime*(key_bytes==16 ? 5:7);
    assert(uint32_t(pendingRelay(node)->due-millis())==delay && delay>0);
    node.advance(delay-1);assertUntouched(node);node.advance(1);
    assert(node.foreign_pending && node.foreign.back()==plannedRelay(node,raw,length));
    node.completeForeign();node.advance(100000);
    assert(node.foreign.size()==1 && node._mt_packet_id==packet && !node._mt_retried && !node._mt_exhausted);
  }
  Preferences::reset();MyMesh wrong_key;active(wrong_key);enableRelay(wrong_key);
  assert(!receiveFrame(wrong_key,relay_private256,sizeof(relay_private256)));
  wrong_key.advance(100000);assert(!pendingRelay(wrong_key));assertUntouched(wrong_key);
  puts("PASS rebroadcast-guards-phy");
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
  else if(!strcmp(argv[1],"channel-presets"))channelPresets();
  else if(!strcmp(argv[1],"cli"))privateCLI();
  else if(!strcmp(argv[1],"hops"))hops();
  else if(!strcmp(argv[1],"shortname"))shortname();
  else if(!strcmp(argv[1],"node"))nodeOverride();
  else if(!strcmp(argv[1],"nodeinfo"))nodeinfo();
  else if(!strcmp(argv[1],"auto-nodeinfo"))automaticNodeinfo();
  else if(!strcmp(argv[1],"auto-nodeinfo-quiet"))automaticNodeinfoQuiet();
  else if(!strcmp(argv[1],"heard-retries"))heardRetries();
  else if(!strcmp(argv[1],"default-tries"))defaultTries();
  else if(!strcmp(argv[1],"strict-echo"))strictEcho();
  else if(!strcmp(argv[1],"delivery-lifecycle"))deliveryLifecycle();
  else if(!strcmp(argv[1],"delivery-contention"))deliveryContention();
  else if(!strcmp(argv[1],"delivery-timing"))deliveryTiming();
  else if(!strcmp(argv[1],"announcement-lifecycle"))announcementLifecycle();
  else if(!strcmp(argv[1],"advert-name-delivery"))advertNameDelivery();
  else if(!strcmp(argv[1],"readiness-cache"))readinessCache();
  else if(!strcmp(argv[1],"rebroadcast-settings"))rebroadcastSettings();
  else if(!strcmp(argv[1],"rebroadcast-timing"))rebroadcastTiming();
  else if(!strcmp(argv[1],"rebroadcast-duplicates"))rebroadcastDuplicates();
  else if(!strcmp(argv[1],"rebroadcast-lifecycle"))rebroadcastLifecycle();
  else if(!strcmp(argv[1],"rebroadcast-guards-phy"))rebroadcastGuardsAndPhy();
  else assert(!"unknown test case");
}
