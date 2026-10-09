#!/usr/bin/env python3
"""Execute production fleet receiver/storage/crypto boundaries with host mocks.

FleetChannel.cpp and FleetCommand.cpp are compiled unchanged. Only hardware,
packet admission, clock and filesystem I/O are mocked. Ed25519 signatures and
SHA-256 use real implementations; AES encryption/MAC are supplied by OpenSSL.
The real RP2040 ContactFileTransaction and nRF52 AtomicFileWriter validate and
publish persisted state using their actual platform-specific implementations.
"""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

UTILS = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <cstdio>
#include <cassert>
#include <SHA256.h>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#define PUB_KEY_SIZE 32
#define PRV_KEY_SIZE 64
#define SIGNATURE_SIZE 64
#define PATH_HASH_SIZE 1
#define CIPHER_MAC_SIZE 2
#define CIPHER_BLOCK_SIZE 16
inline unsigned hash_calls = 0, decrypt_calls = 0, verify_calls = 0, last_jitter_max = 0;
namespace mesh {
class RNG { public: uint32_t nextInt(uint32_t low, uint32_t high) {
  assert(low == 500 && (high == 60500 || high == 15500 || high == 1500)); last_jitter_max=high; return 1000;
}};
class Utils { public:
 static void sha256(uint8_t* out, size_t count, const uint8_t* data, int length) {
  ++hash_calls; SHA256 hash; hash.update(data, length); hash.finalize(out, count);
 }
 static void toHex(char* out, const uint8_t* data, size_t length) {
  for (size_t i = 0; i < length; ++i) sprintf(out + 2*i, "%02x", data[i]);
  out[2*length] = 0;
 }
 static bool fromHex(uint8_t* out, int length, const char* hex) {
  if (strlen(hex) != size_t(2*length)) return false;
  for (int i=0;i<length;++i) {
   auto digit=[](char c) { return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:
    c>='A'&&c<='F'?c-'A'+10:-1; };
   int a=digit(hex[2*i]), b=digit(hex[2*i+1]); if(a<0||b<0) return false;
   out[i]=uint8_t(a*16+b);
  } return true;
 }
 static int encryptThenMAC(const uint8_t* key, uint8_t* out, const uint8_t* data, int size) {
  uint8_t padded[256] = {}; const int rounded = (size+15)/16*16;
  assert(rounded <= int(sizeof(padded))); memcpy(padded,data,size);
  EVP_CIPHER_CTX* ctx=EVP_CIPHER_CTX_new(); assert(ctx);
  assert(EVP_EncryptInit_ex(ctx,EVP_aes_128_ecb(),nullptr,key,nullptr));
  EVP_CIPHER_CTX_set_padding(ctx,0); int produced=0, tail=0;
  assert(EVP_EncryptUpdate(ctx,out+CIPHER_MAC_SIZE,&produced,padded,rounded));
  assert(EVP_EncryptFinal_ex(ctx,out+CIPHER_MAC_SIZE+produced,&tail));
  EVP_CIPHER_CTX_free(ctx); assert(produced+tail==rounded);
  uint8_t mac[32]; unsigned mac_len=0;
  assert(HMAC(EVP_sha256(),key,32,out+CIPHER_MAC_SIZE,rounded,mac,&mac_len));
  memcpy(out,mac,CIPHER_MAC_SIZE); return rounded+CIPHER_MAC_SIZE;
 }
 static int MACThenDecrypt(const uint8_t* key,uint8_t* out,const uint8_t* data,int size) {
  ++decrypt_calls; int cipher_size=size-CIPHER_MAC_SIZE;
  if(cipher_size<=0 || cipher_size%16) return 0;
  uint8_t mac[32]; unsigned mac_len=0;
  assert(HMAC(EVP_sha256(),key,32,data+CIPHER_MAC_SIZE,cipher_size,mac,&mac_len));
  if(memcmp(mac,data,CIPHER_MAC_SIZE)) return 0;
  EVP_CIPHER_CTX* ctx=EVP_CIPHER_CTX_new(); assert(ctx);
  assert(EVP_DecryptInit_ex(ctx,EVP_aes_128_ecb(),nullptr,key,nullptr));
  EVP_CIPHER_CTX_set_padding(ctx,0); int produced=0,tail=0;
  assert(EVP_DecryptUpdate(ctx,out,&produced,data+CIPHER_MAC_SIZE,cipher_size));
  assert(EVP_DecryptFinal_ex(ctx,out+produced,&tail)); EVP_CIPHER_CTX_free(ctx);
  return produced+tail;
 }
};
}
'''

FILESYSTEM = r'''
#pragma once
#include <map>
#include <vector>
#include <string>
#include <memory>
#include <cstring>
#include <algorithm>
namespace fs { class FS; }
class File {
 fs::FS* owner_=nullptr; std::string path_; size_t cursor_=0; bool live_=false;
 public:
 File() = default;
 File(fs::FS* owner,const char* path,bool live):owner_(owner),path_(path),live_(live){}
 explicit File(fs::FS& owner):owner_(&owner){}
 bool open(const char* path,int flags);
 explicit operator bool() const { return live_; }
 bool isDirectory() const { return false; }
 size_t size() const;
 size_t read(uint8_t* data,size_t length);
 size_t write(const uint8_t* data,size_t length);
 void flush() {}
 void close() {live_=false;}
};
namespace fs {
class FS { public:
 std::map<std::string,std::vector<uint8_t>> files;
 bool fail_open=false, fail_read=false, fail_write=false, fail_rename=false;
 unsigned reads=0,writes=0,renames=0;
 std::string fail_rename_from;
 void _lockFS() {} void _unlockFS() {} FS* _getFS(){return this;}
 bool exists(const char* path) {return files.count(path)!=0;}
 bool remove(const char* path) {return files.erase(path)!=0;}
 bool mkdir(const char*) {return true;}
 bool rename(const char* old,const char* next) {
  ++renames; if(fail_rename || fail_rename_from==old || !exists(old)) return false;
  files[next]=std::move(files[old]);files.erase(old);return true;
 }
 File open(const char* path,const char* mode="r") {
  if(!path||fail_open) return {};
  if(mode[0]=='w') {files[path].clear();return File(this,path,true);}
  return File(this,path,exists(path));
 }
};
}
inline bool File::open(const char* path,int flags){
 if(!owner_)return false;*this=owner_->open(path,flags==2?"w":"r");return live_;
}
inline size_t File::size() const {return live_?owner_->files[path_].size():0;}
inline size_t File::read(uint8_t* data,size_t length) {
 ++owner_->reads; if(!live_||owner_->fail_read) return 0;
 auto& bytes=owner_->files[path_];size_t n=std::min(length,bytes.size()-cursor_);
 memcpy(data,bytes.data()+cursor_,n);cursor_+=n;return n;
}
inline size_t File::write(const uint8_t* data,size_t length) {
 ++owner_->writes; if(!live_||owner_->fail_write) return 0;
 auto& bytes=owner_->files[path_];bytes.insert(bytes.end(),data,data+length);return length;
}
'''

MESH = r'''
#pragma once
#include <Identity.h>
#include <FS.h>
#include <vector>
#include <functional>
#define MAX_PACKET_PAYLOAD 184
#define PAYLOAD_TYPE_GRP_TXT 5
#define PAYLOAD_TYPE_GRP_DATA 6
#define FLOOD_RETRY_POLICY_DENY 1
inline uint32_t fake_ms = 1000;
inline uint32_t millis() {return fake_ms;}
namespace mesh {
struct Packet {
 uint8_t type=PAYLOAD_TYPE_GRP_DATA,payload[MAX_PACKET_PAYLOAD]={},flood_retry_policy=0;
 uint16_t payload_len=0,transport_codes[2]={};
 uint32_t radio_generation=0;uint8_t radio_profile=0,path_hash_size=1;
 bool scoped=false,radio_reply=false,radio_bound=false;
 uint8_t getPayloadType() const{return type;}
 uint8_t getPathHashSize() const{return path_hash_size;}
 bool hasTransportCodes() const{return scoped;}
};
struct GroupChannel {uint8_t hash[PATH_HASH_SIZE]={},secret[32]={};};
struct Clock {uint32_t now=1735689600UL+1000;
 uint32_t getCurrentTime() const{return now;}};
class Mesh;
class Dispatcher {public:
 class ReceiveProfileScope {Mesh& mesh_;uint8_t old_;uint32_t generation_;
  public:ReceiveProfileScope(Mesh&,uint8_t,uint32_t);~ReceiveProfileScope();};
 virtual void onSendFail(Packet*)=0;
};
class PacketManager {public:
 std::vector<Packet*> outbound;
 int getOutboundTotal() const{return int(outbound.size());}
 Packet* getOutboundByIdx(int i){return outbound.at(i);}
 Packet* removeOutboundByIdx(int i){Packet* p=outbound.at(i);outbound.erase(outbound.begin()+i);return p;}
};
class Mesh:public Dispatcher {public:
 LocalIdentity self_id;Clock clock;RNG rng;PacketManager manager;PacketManager* _mgr=&manager;
 std::function<void(Packet*,const Packet*)> copying;
 std::function<void(Packet*)> failure;
 std::vector<Packet*> allocated;
 Packet* in_flight=nullptr;bool queue_ok=true,allocation_ok=true,fanout=false;
 unsigned queued=0,cancelled=0,released=0;
 uint8_t scoped_profile=0,ack_path_size=0;uint32_t scoped_generation=0,ack_delay=0;
 bool ack_scoped=false;uint16_t ack_codes[2]={};
 ~Mesh(){for(Packet* p:allocated)delete p;}
 Clock* getRTCClock(){return &clock;}RNG* getRNG(){return &rng;}
 Packet* createGroupDatagram(uint8_t type,const GroupChannel& channel,const uint8_t* data,size_t length){
  assert(length<=168);if(!allocation_ok)return nullptr;Packet* p=new Packet;allocated.push_back(p);p->type=type;
  p->payload[0]=channel.hash[0];p->payload_len=1+Utils::encryptThenMAC(channel.secret,p->payload+1,data,int(length));return p;
 }
 bool sendFlood(Packet* p,uint32_t delay,uint8_t hashes){
  ack_delay=delay;ack_path_size=hashes;
  if(!queue_ok)return false;manager.outbound.push_back(p);++queued;
  if(fanout){Packet* second=new Packet(*p);allocated.push_back(second);manager.outbound.push_back(second);
   if(copying)copying(second,p);}
  return true;
 }
 bool sendFlood(Packet* p,const uint16_t* codes,uint32_t delay,uint8_t hashes){
  ack_scoped=true;memcpy(ack_codes,codes,sizeof(ack_codes));return sendFlood(p,delay,hashes);
 }
 const Packet* getOutboundInFlight()const{return in_flight;}
 void cancelOutboundRadioRetry(const Packet* p){assert(p==in_flight);++cancelled;}
 void onSendFail(Packet* p)override{if(failure)failure(p);}
 void releasePacket(Packet*){++released;}
};
inline Dispatcher::ReceiveProfileScope::ReceiveProfileScope(Mesh& m,uint8_t profile,uint32_t generation)
 :mesh_(m),old_(m.scoped_profile),generation_(m.scoped_generation){m.scoped_profile=profile;m.scoped_generation=generation;}
inline Dispatcher::ReceiveProfileScope::~ReceiveProfileScope(){mesh_.scoped_profile=old_;mesh_.scoped_generation=generation_;}
}
'''

PROFILE = r'''
#pragma once
namespace mesh {
class RadioProfileCLI {public:
 bool mutation=false,command=false;uint32_t generation=0;
 unsigned finishes=0,commits=0,rollbacks=0;
 void beginReplyCommand(){command=true;}void endReplyCommand(){command=false;}
 bool hasReplyMutation()const{return mutation;}
 uint32_t replyMutationGeneration()const{return generation;}
 void stage(){assert(command);mutation=true;++generation;}
 bool finishReplyMutation(bool delivered){assert(mutation);mutation=false;++finishes;
  if(delivered)++commits;else ++rollbacks;return true;}
};
}
'''

HARNESS = r'''
#include <helpers/FleetChannel.h>
#include <helpers/PersistentStoreFormat.h>
#include <cassert>
#include <string>
#include <algorithm>
#include <cstdio>
@TRANSPORT_METHODS@

inline unsigned identity_counter=0;
namespace mesh {
Identity::Identity(){memset(pub_key,0,sizeof(pub_key));}
LocalIdentity::LocalIdentity():Identity(){
 memset(prv_key,0xAA+identity_counter++,32);EVP_PKEY* key=EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519,nullptr,prv_key,32);
 assert(key);size_t length=sizeof(pub_key);assert(EVP_PKEY_get_raw_public_key(key,pub_key,&length)==1);
 memcpy(prv_key+32,pub_key,32);EVP_PKEY_free(key);
}
void LocalIdentity::sign(uint8_t* sig,const uint8_t* data,int length)const{
 EVP_PKEY* key=EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519,nullptr,prv_key,32);
 EVP_MD_CTX* ctx=EVP_MD_CTX_new();assert(key&&ctx);assert(EVP_DigestSignInit(ctx,nullptr,nullptr,nullptr,key)==1);
 size_t size=64;assert(EVP_DigestSign(ctx,sig,&size,data,length)==1);assert(size==64);
 EVP_MD_CTX_free(ctx);EVP_PKEY_free(key);
}
bool Identity::verify(const uint8_t* sig,const uint8_t* data,int length)const{
 ++verify_calls;EVP_PKEY* key=EVP_PKEY_new_raw_public_key(EVP_PKEY_ED25519,nullptr,pub_key,32);
 EVP_MD_CTX* ctx=EVP_MD_CTX_new();assert(key&&ctx);assert(EVP_DigestVerifyInit(ctx,nullptr,nullptr,nullptr,key)==1);
 bool valid=EVP_DigestVerify(ctx,sig,64,data,length)==1;EVP_MD_CTX_free(ctx);EVP_PKEY_free(key);return valid;
}
}
using namespace mesh;
static constexpr const char* Path="/fleet_channel";
static constexpr const char* Key="21212121212121212121212121212121";
static uint8_t channel_key[32]={0x21,0x21,0x21,0x21,0x21,0x21,0x21,0x21,
 0x21,0x21,0x21,0x21,0x21,0x21,0x21,0x21};
static std::string config(FleetChannel& fleet,const std::string& command){
 char reply[160]={};assert(fleet.handleConfig(command.c_str(),reply,sizeof(reply)));return reply;
}
static void enroll(FleetChannel& fleet,const LocalIdentity& publisher){
 assert(config(fleet,std::string("set fleet.channel ")+Key).rfind("OK",0)==0);
 char hex[65];Utils::toHex(hex,publisher.pub_key,32);
 assert(config(fleet,std::string("set fleet.controller ")+hex).rfind("OK",0)==0);
}
static Packet raw(const uint8_t* bytes,size_t length){
 Packet packet;Utils::sha256(packet.payload,1,channel_key,16);
 packet.payload_len=1+Utils::encryptThenMAC(channel_key,packet.payload+1,bytes,int(length));
 packet.radio_profile=1;packet.radio_generation=17;packet.path_hash_size=2;
 return packet;
}
static Packet command(const LocalIdentity& publisher,uint32_t sequence,const char* text="set radio2 off",
                      const char* target="all"){
 uint8_t payload[184]={},destination[16];assert(FleetCommand::parseTarget(target,destination));
 size_t length=FleetCommand::encode(publisher,channel_key,sequence,sequence+300,destination,text,payload+3,sizeof(payload)-3);
 assert(length);payload[0]=uint8_t(FleetCommand::DataType);payload[1]=uint8_t(FleetCommand::DataType>>8);payload[2]=uint8_t(length);
 return raw(payload,3+length);
}

static Packet targetsCommand(const LocalIdentity& publisher,uint32_t sequence,const std::string& list,
                             const char* text="get radio2"){
 FleetCommand::Targets targets;assert(FleetCommand::parseTargets(list.c_str(),list.size(),targets));
 uint8_t payload[184]={};
 const size_t length=FleetCommand::encode(publisher,channel_key,sequence,sequence+300,targets,text,
                                       payload+3,sizeof(payload)-3);
 assert(length);payload[0]=uint8_t(FleetCommand::DataType);payload[1]=uint8_t(FleetCommand::DataType>>8);
 payload[2]=uint8_t(length);
 return raw(payload,3+length);
}
static Packet shortNewAll(const LocalIdentity& publisher,uint32_t sequence){
 constexpr const char* text="get radio2";
 uint8_t data[184]={},message[160]={};uint8_t* payload=data+3;
 memcpy(payload,"FMC2",4);storage::writeLE32(payload+4,sequence);
 storage::writeLE32(payload+8,sequence+300);payload[12]=0;
 const size_t text_size=strlen(text);payload[13]=uint8_t(text_size);memcpy(payload+14,text,text_size);
 constexpr const char* domain="MeshCoreFleet1";const size_t domain_size=strlen(domain);
 memcpy(message,domain,domain_size);memcpy(message+domain_size,channel_key,16);
 memcpy(message+domain_size+16,payload,14+text_size);
 publisher.sign(payload+14+text_size,message,int(domain_size+16+14+text_size));
 data[0]=uint8_t(FleetCommand::DataType);data[1]=uint8_t(FleetCommand::DataType>>8);
 data[2]=uint8_t(14+text_size+64);return raw(data,3+data[2]);
}
static std::string publicHex(const Identity& identity){
 char text[65];Utils::toHex(text,identity.pub_key,32);return text;
}
static Packet signedUnchecked(const LocalIdentity& publisher,uint32_t sequence,uint32_t expires,
                              const char* text){
 uint8_t data[184]={},message[160]={};size_t text_size=strlen(text);
 assert(text_size<=FleetCommand::MaxCommandLength);
 uint8_t* payload=data+3;memcpy(payload,"FMC1",4);
 storage::writeLE32(payload+4,sequence);storage::writeLE32(payload+8,expires);
 payload[28]=uint8_t(text_size);memcpy(payload+29,text,text_size);
 constexpr const char* domain="MeshCoreFleet1";const size_t domain_size=strlen(domain);
 memcpy(message,domain,domain_size);memcpy(message+domain_size,channel_key,16);
 memcpy(message+domain_size+16,payload,29+text_size);
 publisher.sign(payload+29+text_size,message,int(domain_size+16+29+text_size));
 data[0]=uint8_t(FleetCommand::DataType);data[1]=uint8_t(FleetCommand::DataType>>8);
 data[2]=uint8_t(29+text_size+64);return raw(data,3+data[2]);
}

static void bind(FleetChannel& fleet,Mesh& mesh,RadioProfileCLI& profiles){
 mesh.copying=[&](Packet* second,const Packet* first){fleet.copy(second,first);};
 mesh.failure=[&](Packet* p){fleet.fail(p,profiles);};
}
static unsigned apply(FleetChannel& fleet,fs::FS& fs,Mesh& mesh,RadioProfileCLI& profiles,Packet& packet,
                      bool mutation=false,bool fail_handler=false,const TransportKey* scope=nullptr){
 unsigned calls=0;fleet.receive(&packet,mesh,scope);
 fleet.service(mesh,profiles,"test:node",[&](uint32_t seq,const char* text,char* reply){
  ++calls;assert(storage::readLE32(fs.files.at(Path).data()+56)==seq);
  assert(mesh.scoped_profile==1&&mesh.scoped_generation==17);assert(profiles.command);
  assert(!strcmp(text,"set radio2 off")||!strcmp(text,"get radio2.status")||!strcmp(text,"get radio2"));
  if(mutation)profiles.stage();strcpy(reply,fail_handler?"Err - rejected":"OK");
 });
 assert(mesh.scoped_profile==0&&mesh.scoped_generation==0);assert(!profiles.command);return calls;
}
static void reseal(std::vector<uint8_t>& image){
 storage::writeLE32(image.data()+60,storage::updateCRC32(0xffffffff,image.data(),60));
}
int main(int argc,char** argv){
 assert(argc==2);const std::string scenario=argv[1];fs::FS fs;Mesh mesh;RadioProfileCLI profiles;
 FleetChannel fleet(&fs);LocalIdentity publisher;bind(fleet,mesh,profiles);
 if(scenario=="enrollment"){
  Packet packet=command(publisher,mesh.clock.now);assert(apply(fleet,fs,mesh,profiles,packet)==0);
  assert(config(fleet,"get fleet.channel").find("off,controller=unset,store=ok")!=std::string::npos);
  assert(config(fleet,"set fleet.channel 00000000000000000000000000000000").rfind("Err",0)==0);
  assert(config(fleet,"set fleet.channel 8b3387e9c5cdea6ac9e5edbaa115cd72").rfind("Err",0)==0);
  assert(config(fleet,"set fleet.controller 0000000000000000000000000000000000000000000000000000000000000000").rfind("Err",0)==0);
  assert(config(fleet,std::string("set fleet.channel ")+Key).rfind("OK",0)==0);
  assert(apply(fleet,fs,mesh,profiles,packet)==0);enroll(fleet,publisher);
  const auto& image=fs.files.at(Path);assert(image.size()==64);
  assert(!memcmp(image.data()+24,publisher.pub_key,32));
  const std::vector<uint8_t> private_seed(32,0xAB);
  assert(std::search(image.begin(),image.end(),private_seed.begin(),private_seed.end())==image.end());
  assert(apply(fleet,fs,mesh,profiles,packet)==1);
  assert(config(fleet,"set fleet.controller off").rfind("OK",0)==0);
  ++mesh.clock.now;packet=command(publisher,mesh.clock.now);assert(apply(fleet,fs,mesh,profiles,packet)==0);
 }else if(scenario=="deferred_single_mailbox"){
  enroll(fleet,publisher);Packet a=command(publisher,mesh.clock.now),b=command(publisher,mesh.clock.now+1);
  unsigned decrypt_before=decrypt_calls,verify_before=verify_calls,writes_before=fs.writes;
  fleet.receive(&a,mesh);fleet.receive(&b,mesh);
  assert(decrypt_calls==decrypt_before&&verify_calls==verify_before&&fs.writes==writes_before);
  assert(config(fleet,"get fleet.stats").find("busy=1 pending=1")!=std::string::npos);
  assert(config(fleet,"set fleet.channel off").rfind("Err",0)==0);
  unsigned calls=0;fleet.service(mesh,profiles,"node",[&](uint32_t seq,const char*,char* reply){
   ++calls;assert(seq==mesh.clock.now);strcpy(reply,"OK");});assert(calls==1);
  assert(config(fleet,"get fleet.stats").find("accepted=1")!=std::string::npos);
 }else if(scenario=="durable_replay"){
  enroll(fleet,publisher);Packet packet=command(publisher,mesh.clock.now);
  assert(apply(fleet,fs,mesh,profiles,packet,false,true)==1);
  assert(apply(fleet,fs,mesh,profiles,packet)==0);FleetChannel rebooted(&fs);
  assert(apply(rebooted,fs,mesh,profiles,packet)==0);
  ++mesh.clock.now;packet=command(publisher,mesh.clock.now);assert(apply(rebooted,fs,mesh,profiles,packet)==1);
 }else if(scenario=="reserve_fault"){
  enroll(fleet,publisher);const auto previous=fs.files.at(Path);fs.fail_write=true;
  Packet packet=command(publisher,mesh.clock.now);assert(apply(fleet,fs,mesh,profiles,packet)==0);
  assert(fs.files.at(Path)==previous);fs.fail_write=false;
  assert(config(fleet,"get fleet.channel").find("store=error")!=std::string::npos);
  assert(apply(fleet,fs,mesh,profiles,packet)==0);FleetChannel rebooted(&fs);
  assert(apply(rebooted,fs,mesh,profiles,packet)==1);
 }else if(scenario=="config_fault"){
  enroll(fleet,publisher);const auto previous=fs.files.at(Path);fs.fail_write=true;
  assert(config(fleet,"set fleet.channel off").rfind("Err",0)==0);
  assert(fs.files.at(Path)==previous);fs.fail_write=false;
  assert(config(fleet,"get fleet.channel").find("on,controller=set")!=std::string::npos);
  FleetChannel rebooted(&fs);Packet packet=command(publisher,mesh.clock.now);
  assert(apply(rebooted,fs,mesh,profiles,packet)==1);
 }else if(scenario=="images"){
  enroll(fleet,publisher);const auto original=fs.files.at(Path);
  for(unsigned damage=0;damage<7;++damage){
   fs.files[Path]=original;auto& image=fs.files[Path];
   if(damage==0)image.resize(63);else if(damage==1)image.push_back(0);
   else if(damage==2)image[0]='X';else if(damage==3)image[4]=2;
   else if(damage==4)image[5]=1;else if(damage==5)image[60]^=1;
   else memset(image.data()+8,0,16);
   if(damage>=3&&damage!=5)reseal(image);
   FleetChannel bad(&fs);assert(config(bad,"get fleet.channel").find("store=error")!=std::string::npos);
   Packet packet=command(publisher,mesh.clock.now);assert(apply(bad,fs,mesh,profiles,packet)==0);
   assert(config(bad,std::string("set fleet.channel ")+Key).rfind("Err",0)==0);
  }
  fs.files[Path]=original;fs.fail_read=true;FleetChannel unreadable(&fs);fs.fail_read=false;
  assert(config(unreadable,"get fleet.channel").find("store=error")!=std::string::npos);
#if defined(RP2040_PLATFORM)
  fs.files.erase(Path);fs.files[std::string(Path)+".bak"]=original;
  FleetChannel recovered(&fs);assert(config(recovered,"get fleet.channel").find("store=ok")!=std::string::npos);
  assert(fs.files[Path]==original);Packet packet=command(publisher,mesh.clock.now);
  assert(apply(recovered,fs,mesh,profiles,packet)==1);
#endif
 }else if(scenario=="ack_both_copies"){
  enroll(fleet,publisher);mesh.fanout=true;Packet packet=command(publisher,mesh.clock.now);
  TransportKey scope;memset(scope.key,0x12,sizeof(scope.key));
  packet.scoped=true;packet.transport_codes[0]=scope.calcTransportCode(&packet);packet.transport_codes[1]=456;
  assert(apply(fleet,fs,mesh,profiles,packet,true,false,&scope)==1);assert(fleet.waiting());
  assert(mesh.manager.outbound.size()==2);assert(mesh.ack_scoped&&mesh.ack_codes[1]==0);
  assert(mesh.ack_codes[0]==scope.calcTransportCode(mesh.manager.outbound[0]));
  assert(mesh.ack_codes[0]!=packet.transport_codes[0]);
  assert(mesh.ack_delay==1000&&mesh.ack_path_size==2&&last_jitter_max==15500);
  assert(mesh.manager.outbound[0]->radio_reply&&!mesh.manager.outbound[0]->radio_bound);
  assert(mesh.manager.outbound[0]->flood_retry_policy==FLOOD_RETRY_POLICY_DENY);
  fleet.receive(&packet,mesh);assert(config(fleet,"get fleet.stats").find("busy=1")!=std::string::npos);
  fleet.complete(mesh.manager.removeOutboundByIdx(0),profiles);assert(fleet.waiting());assert(profiles.commits==0);
  fleet.fail(mesh.manager.removeOutboundByIdx(0),profiles);assert(!fleet.waiting());assert(profiles.commits==1&&profiles.rollbacks==0);
 }else if(scenario=="ack_all_fail"){
  enroll(fleet,publisher);mesh.fanout=true;Packet packet=command(publisher,mesh.clock.now);
  assert(apply(fleet,fs,mesh,profiles,packet,true)==1);
  fleet.fail(mesh.manager.removeOutboundByIdx(0),profiles);assert(fleet.waiting()&&profiles.rollbacks==0);
  fleet.fail(mesh.manager.removeOutboundByIdx(0),profiles);assert(!fleet.waiting());assert(profiles.rollbacks==1);
 }else if(scenario=="ack_queue_or_allocation"){
  enroll(fleet,publisher);Packet packet=command(publisher,mesh.clock.now);mesh.queue_ok=false;
  assert(apply(fleet,fs,mesh,profiles,packet,true)==1);assert(!fleet.waiting()&&profiles.rollbacks==1);
  assert(apply(fleet,fs,mesh,profiles,packet,true)==0);mesh.queue_ok=true;mesh.allocation_ok=false;
  ++mesh.clock.now;packet=command(publisher,mesh.clock.now);
  assert(apply(fleet,fs,mesh,profiles,packet,true)==1);assert(profiles.rollbacks==2);
 }else if(scenario=="ack_timeout"){
  enroll(fleet,publisher);mesh.fanout=true;Packet packet=command(publisher,mesh.clock.now);
  assert(apply(fleet,fs,mesh,profiles,packet,true)==1);fake_ms+=300000;
  fleet.service(mesh,profiles,"node",[](uint32_t,const char*,char*){assert(false);});
  assert(mesh.manager.outbound.empty()&&mesh.released==2);assert(profiles.rollbacks==1&&!fleet.waiting());
  ++mesh.clock.now;packet=command(publisher,mesh.clock.now);assert(apply(fleet,fs,mesh,profiles,packet,true)==1);
  mesh.in_flight=mesh.manager.removeOutboundByIdx(0);fake_ms+=300000;
  fleet.service(mesh,profiles,"node",[](uint32_t,const char*,char*){assert(false);});
  assert(mesh.cancelled==1&&fleet.waiting());fleet.fail(mesh.in_flight,profiles);mesh.in_flight=nullptr;
  assert(!fleet.waiting()&&profiles.rollbacks==2);
 }else if(scenario=="tamper_padding_target"){
  enroll(fleet,publisher);Packet good=command(publisher,mesh.clock.now);
  uint8_t data[184];int size=Utils::MACThenDecrypt(channel_key,data,good.payload+1,good.payload_len-1);
  data[3+29]^=1;Packet forged=raw(data,size);assert(apply(fleet,fs,mesh,profiles,forged)==0);
  size=Utils::MACThenDecrypt(channel_key,data,good.payload+1,good.payload_len-1);
  data[size-1]=1;Packet padded=raw(data,size);assert(apply(fleet,fs,mesh,profiles,padded)==0);
  Packet mac=good;mac.payload[1]^=1;assert(apply(fleet,fs,mesh,profiles,mac)==0);
  char target[65];uint8_t other[32];memset(other,0x23,sizeof(other));Utils::toHex(target,other,sizeof(other));
  Packet wrong=command(publisher,mesh.clock.now,"set radio2 off",target);assert(apply(fleet,fs,mesh,profiles,wrong)==0);
  fake_ms+=1000;Utils::toHex(target,mesh.self_id.pub_key,32);
  Packet mine=command(publisher,mesh.clock.now,"set radio2 off",target);assert(apply(fleet,fs,mesh,profiles,mine)==1);
  assert(last_jitter_max==1500);
 }else if(scenario=="verify_budget"){
  enroll(fleet,publisher);Packet packet=command(publisher,mesh.clock.now);
  packet.payload[1]^=1;unsigned before=decrypt_calls;
  for(unsigned i=0;i<20;++i)assert(apply(fleet,fs,mesh,profiles,packet)==0);
  assert(decrypt_calls-before==4);fake_ms+=1000;packet=command(publisher,mesh.clock.now);
  assert(apply(fleet,fs,mesh,profiles,packet)==1);
 }else if(scenario=="primary_denied"){
  assert(!FleetCommand::commandAllowed("set radio 915,500,5,5"));
  assert(!FleetCommand::commandAllowed("set tempradio 915,500,5,5,10"));
  assert(!FleetCommand::commandAllowed("set password pass"));
  assert(!FleetCommand::commandAllowed("reboot"));
  assert(!FleetCommand::commandAllowed("set fleet.controller off"));

 }else if(scenario=="signed_forbidden"){
  enroll(fleet,publisher);
  for(const char* text:{"set radio 915,500,5,5","set password pass","reboot",
      "set fleet.channel off","get prv.key","set flood.max 5;reboot"}){
   fake_ms+=1000;Packet packet=signedUnchecked(publisher,mesh.clock.now,mesh.clock.now+300,text);
   assert(apply(fleet,fs,mesh,profiles,packet)==0);
  }
  assert(storage::readLE32(fs.files[Path].data()+56)==0);assert(mesh.queued==0);
 }else if(scenario=="clock_limits"){
  enroll(fleet,publisher);const uint32_t now=mesh.clock.now;
  const std::vector<std::pair<uint32_t,uint32_t>> invalid={{now-301,now-1},{now+61,now+100},
    {FleetCommand::MinEpoch-1,now+100},{now,now+FleetCommand::MaxLifetime+1},{now,now-1}};
  for(const auto& times:invalid){fake_ms+=1000;
   Packet packet=signedUnchecked(publisher,times.first,times.second,"set radio2 off");
   assert(apply(fleet,fs,mesh,profiles,packet)==0);
  }
  mesh.clock.now=0;fake_ms+=1000;Packet packet=command(publisher,now);
  assert(apply(fleet,fs,mesh,profiles,packet)==0);mesh.clock.now=now;
  fake_ms+=1000;packet=signedUnchecked(publisher,now+60,now+100,"set radio2 off");
  assert(apply(fleet,fs,mesh,profiles,packet)==1);
 }else if(scenario=="transaction_faults"){
  for(unsigned fault=0;fault<4;++fault){
   fs::FS disk;Mesh transport;RadioProfileCLI radio;FleetChannel receiver(&disk);enroll(receiver,publisher);
   auto previous=disk.files.at(Path);Packet packet=command(publisher,transport.clock.now);
   if(fault==0)disk.fail_open=true;
   else if(fault==1)disk.fail_read=true;
   else if(fault==2)disk.fail_rename=true;
   else disk.fail_rename_from=std::string(Path)+".tmp";
   assert(apply(receiver,disk,transport,radio,packet)==0);assert(disk.files.at(Path)==previous);
   assert(config(receiver,"get fleet.channel").find("store=error")!=std::string::npos);
  }
 }else if(scenario=="backup_replay"){
  enroll(fleet,publisher);Packet packet=command(publisher,mesh.clock.now);
  assert(apply(fleet,fs,mesh,profiles,packet)==1);const auto persisted=fs.files.at(Path);
#if defined(RP2040_PLATFORM)
  fs.files.erase(Path);fs.files[std::string(Path)+".bak"]=persisted;
#else
  // Atomic LittleFS replacement never has a live-name gap. Abandoned .tmp
  // bytes are ignored; the committed live sequence remains authoritative.
  fs.files[std::string(Path)+".tmp"]=persisted;
#endif
  FleetChannel rebooted(&fs);assert(apply(rebooted,fs,mesh,profiles,packet)==0);
  assert(storage::readLE32(fs.files.at(Path).data()+56)==mesh.clock.now);

 }else if(scenario=="wrong_publisher"){
  enroll(fleet,publisher);LocalIdentity stranger;
  assert(memcmp(stranger.pub_key,publisher.pub_key,32));
  Packet packet=command(stranger,mesh.clock.now);assert(apply(fleet,fs,mesh,profiles,packet)==0);
  assert(verify_calls==1&&mesh.queued==0&&storage::readLE32(fs.files[Path].data()+56)==0);
 }else if(scenario=="bounded_ack"){
  enroll(fleet,publisher);Packet packet=command(publisher,mesh.clock.now);
  fleet.receive(&packet,mesh);fleet.service(mesh,profiles,"sixteen:character:very long name",
    [&](uint32_t,const char*,char* reply){memset(reply,'x',159);reply[159]=0;});
  assert(mesh.queued==1);uint8_t data[184]={};Packet* ack=mesh.manager.outbound[0];
  assert(ack->payload_len<=MAX_PACKET_PAYLOAD);
  assert(Utils::MACThenDecrypt(channel_key,data,ack->payload+1,ack->payload_len-1)==176);
  assert(!strncmp((char*)data+5,"sixteen;characte",15));
 }else if(scenario=="unknown_scope"){
  enroll(fleet,publisher);Packet packet=command(publisher,mesh.clock.now);packet.scoped=true;
  packet.transport_codes[0]=123;assert(apply(fleet,fs,mesh,profiles,packet,true)==1);
  assert(mesh.queued==0&&mesh.released==1&&profiles.rollbacks==1&&!fleet.waiting());
 }else if(scenario=="rotated_scope"){
  struct Region {bool wildcard=false;bool isWildcard()const{return wildcard;}};
  struct Regions {TransportKey keys[2];int getTransportKeysFor(const Region&,TransportKey* out,int max){
   assert(max==MAX_TKS_ENTRIES);memcpy(out,keys,sizeof(keys));return 2;}} regions;
  memset(regions.keys[0].key,0x11,16);memset(regions.keys[1].key,0x22,16);
  Region region;TransportKey captured{};Packet packet=command(publisher,mesh.clock.now);
  packet.scoped=true;packet.transport_codes[0]=regions.keys[1].calcTransportCode(&packet);
  assert(packet.transport_codes[0]!=regions.keys[0].calcTransportCode(&packet));
  assert(captureFleetReplyScope(regions,&region,&packet,captured));
  assert(!memcmp(captured.key,regions.keys[1].key,16));
  enroll(fleet,publisher);assert(apply(fleet,fs,mesh,profiles,packet,true,false,&captured)==1);
  assert(mesh.ack_codes[0]==regions.keys[1].calcTransportCode(mesh.manager.outbound[0]));
  assert(mesh.ack_codes[0]!=packet.transport_codes[0]);
  region.wildcard=true;assert(!captureFleetReplyScope(regions,&region,&packet,captured));
  assert(!captureFleetReplyScope(regions,static_cast<Region*>(nullptr),&packet,captured));
  region.wildcard=false;packet.transport_codes[0]^=1;
  assert(!captureFleetReplyScope(regions,&region,&packet,captured));

 }else if(scenario=="fmc2_short_all"){
  enroll(fleet,publisher);Packet packet=shortNewAll(publisher,mesh.clock.now);
  uint8_t plaintext[184]={};assert(Utils::MACThenDecrypt(channel_key,plaintext,packet.payload+1,packet.payload_len-1)>0);
  assert(plaintext[2]<FleetCommand::HeaderSize+FleetCommand::SignatureSize);
  assert(apply(fleet,fs,mesh,profiles,packet)==1);assert(last_jitter_max==60500);
  assert(apply(fleet,fs,mesh,profiles,packet)==0);
 }else if(scenario=="fmc2_short_prefix"){
  enroll(fleet,publisher);Packet packet=targetsCommand(publisher,mesh.clock.now,publicHex(mesh.self_id).substr(0,8));
  uint8_t plaintext[184]={};assert(Utils::MACThenDecrypt(channel_key,plaintext,packet.payload+1,packet.payload_len-1)>0);
  assert(plaintext[2]==FleetCommand::HeaderSize+FleetCommand::SignatureSize);
  assert(!memcmp(plaintext+3,"FMC2",4));
  assert(apply(fleet,fs,mesh,profiles,packet)==1);assert(last_jitter_max==60500);
  FleetChannel rebooted(&fs);assert(apply(rebooted,fs,mesh,profiles,packet)==0);
 }else if(scenario=="fmc2_overlap_matches_once"){
  enroll(fleet,publisher);const std::string mine=publicHex(mesh.self_id);
  Packet packet=targetsCommand(publisher,mesh.clock.now,mine.substr(0,8)+","+mine);
  assert(apply(fleet,fs,mesh,profiles,packet)==1);assert(mesh.queued==1&&last_jitter_max==60500);
  assert(config(fleet,"get fleet.stats").find("accepted=1")!=std::string::npos);
 }else if(scenario=="fmc2_nonmatching_no_reserve"){
  enroll(fleet,publisher);const auto previous=fs.files.at(Path);LocalIdentity stranger;
  char different_prefix[9];uint8_t changed[4];memcpy(changed,mesh.self_id.pub_key,4);changed[0]^=1;
  Utils::toHex(different_prefix,changed,4);
  Packet packet=targetsCommand(publisher,mesh.clock.now,std::string(different_prefix)+","+publicHex(stranger));
  assert(apply(fleet,fs,mesh,profiles,packet)==0);assert(mesh.queued==0&&fs.files.at(Path)==previous);
  packet=targetsCommand(publisher,mesh.clock.now,publicHex(stranger)+","+publicHex(mesh.self_id));
  assert(apply(fleet,fs,mesh,profiles,packet)==1);assert(last_jitter_max==60500);
 }else if(scenario=="fmc2_target_tamper"){
  enroll(fleet,publisher);const auto previous=fs.files.at(Path);
  Packet good=targetsCommand(publisher,mesh.clock.now,publicHex(mesh.self_id).substr(0,12));
  uint8_t plaintext[184]={};const int length=Utils::MACThenDecrypt(channel_key,plaintext,good.payload+1,good.payload_len-1);
  assert(length>0);plaintext[3+13]^=1;Packet changed=raw(plaintext,length);
  assert(apply(fleet,fs,mesh,profiles,changed)==0);assert(fs.files.at(Path)==previous);
  assert(apply(fleet,fs,mesh,profiles,good)==1);
 }else if(scenario=="existing_mutation"){
  enroll(fleet,publisher);Packet packet=command(publisher,mesh.clock.now);
  profiles.mutation=true;assert(apply(fleet,fs,mesh,profiles,packet)==0);
  assert(storage::readLE32(fs.files[Path].data()+56)==0);
  profiles.mutation=false;assert(apply(fleet,fs,mesh,profiles,packet)==1);
 }else assert(false);
 printf("fleet runtime %s passed\n",argv[1]);
}
'''


class FleetChannelRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("host C++ compiler required")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-fleet-runtime-")
        work = Path(cls.work.name)
        (work / "helpers").mkdir()
        for name, content in {"Utils.h": UTILS, "FS.h": FILESYSTEM, "Mesh.h": MESH,
                              "helpers/RadioProfileCLI.h": PROFILE,
                              "Packet.h": '#pragma once\n#include <Mesh.h>\n',
                              "Adafruit_LittleFS.h": '''
#pragma once
#include <FS.h>
using Adafruit_LittleFS = fs::FS;
namespace Adafruit_LittleFS_Namespace {}
#define FILE_O_READ 1
#define FILE_O_WRITE 2
#define LFS_ERR_NOENT -2
struct lfs_info {};
inline int lfs_stat(fs::FS* fs,const char* path,lfs_info*) {return fs->exists(path)?0:LFS_ERR_NOENT;}
''',
                              "InternalFileSystem.h": '#pragma once\n#include <FS.h>\ninline fs::FS InternalFS;\n',
                              "Arduino.h": '#pragma once\n#include <Mesh.h>\n'}.items():
            (work / name).write_text(content)
        fixture = work / "fixture.cpp"
        transport_source = (ROOT / "src/helpers/TransportKeyStore.cpp").read_text()
        methods = "\n".join(body(transport_source, signature) for signature in [
            "uint16_t TransportKey::calcTransportCode(", "bool TransportKey::isNull("])
        fixture.write_text(HARNESS.replace("@TRANSPORT_METHODS@", methods))
        sha_header = ROOT / "test/mocks/SHA256.h"
        (work / "SHA256.h").write_text(f'''
#pragma once
#define SHA256 SHA256Base
#include "{sha_header}"
#undef SHA256
class SHA256 : public SHA256Base {{ public:
 void finalize(void* out,size_t length) {{ SHA256Base::finalize(static_cast<uint8_t*>(out),length); }}
 void finalizeHMAC(const uint8_t* key,size_t size,void* out,size_t length) {{
  SHA256Base::finalizeHMAC(key,size,static_cast<uint8_t*>(out),length); }}
}};
''')
        cls.binaries = {}
        sanitizer_flags = (["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
                           if os.environ.get("MESHCORE_FLEET_SANITIZERS") == "1" else [])
        for backend in ["RP2040", "NRF52"]:
            binary = work / f"fleet-runtime-{backend.lower()}"
            sources = [str(ROOT / "src/helpers/FleetChannel.cpp"),
                       str(ROOT / "src/helpers/FleetCommand.cpp")]
            if backend == "NRF52":
                sources.append(str(ROOT / "src/helpers/AtomicFileWriter.cpp"))
            result = subprocess.run([
                compiler, *sanitizer_flags, "-std=c++17", f"-D{backend}_PLATFORM",
                "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                "-Wno-misleading-indentation", "-Wno-sign-compare", "-O2", "-g",
                "-I", str(work), "-isystem", str(ROOT / "test/mocks"),
                "-I", str(ROOT / "src"), str(fixture), *sources,
                "-lcrypto", "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            if result.returncode:
                cls.work.cleanup()
                raise AssertionError(f"{backend}:\n" + result.stdout + result.stderr)
            cls.binaries[backend] = binary

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def scenario(self, name):
        for backend, binary in self.binaries.items():
            with self.subTest(backend=backend):
                result = subprocess.run([str(binary), name], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_private_channel_and_public_only_publisher_enrollment(self):
        self.scenario("enrollment")

    def test_receive_defers_crypto_writes_and_accepts_one_pending_command(self):
        self.scenario("deferred_single_mailbox")

    def test_sequence_reserved_before_handler_including_error_survives_reboot(self):
        self.scenario("durable_replay")

    def test_failed_sequence_save_grants_no_control_and_disables_receiver(self):
        self.scenario("reserve_fault")

    def test_failed_configuration_write_preserves_durable_enrollment(self):
        self.scenario("config_fault")

    def test_corrupt_short_long_unreadable_images_fail_closed_and_backup_recovers(self):
        self.scenario("images")

    def test_ack_waits_for_both_profile_copies_with_one_success_and_retains_rx_scope(self):
        self.scenario("ack_both_copies")

    def test_both_ack_transmissions_failing_roll_back_radio_mutation(self):
        self.scenario("ack_all_fail")

    def test_ack_allocation_queue_failure_rolls_back_but_keeps_reserved_sequence(self):
        self.scenario("ack_queue_or_allocation")

    def test_ack_timeout_cancels_queued_and_inflight_packets_before_rollback(self):
        self.scenario("ack_timeout")

    def test_real_signature_mac_padding_and_full_identity_target_checks(self):
        self.scenario("tamper_padding_target")

    def test_forged_packets_have_bounded_verification_cost(self):
        self.scenario("verify_budget")

    def test_fleet_permission_never_allows_primary_radio_or_administrator_changes(self):
        self.scenario("primary_denied")

    def test_publisher_signed_commands_still_cannot_escape_fleet_capability(self):
        self.scenario("signed_forbidden")

    def test_expiry_future_clock_bounds_and_invalid_rtc_prevent_dispatch(self):
        self.scenario("clock_limits")

    def test_open_read_rename_and_publish_faults_preserve_state_and_fail_closed(self):
        self.scenario("transaction_faults")

    def test_recovered_backup_preserves_durable_anti_replay_sequence(self):
        self.scenario("backup_replay")

    def test_another_valid_ed25519_identity_cannot_authorize_fleet_commands(self):
        self.scenario("wrong_publisher")

    def test_long_cli_reply_and_sender_name_fit_one_encrypted_group_packet(self):
        self.scenario("bounded_ack")

    def test_unknown_scoped_route_refuses_ack_and_rolls_back_radio_mutation(self):
        self.scenario("unknown_scope")

    def test_scope_selects_matching_rotated_region_key_and_authenticates_new_ack_payload(self):
        self.scenario("rotated_scope")

    def test_new_short_broadcast_envelope_dispatches_and_preserves_replay_protection(self):
        self.scenario("fmc2_short_all")

    def test_new_public_key_prefix_envelope_dispatches_and_staggers_possible_multiple_acks(self):
        self.scenario("fmc2_short_prefix")

    def test_overlapping_prefix_and_full_key_matches_execute_once(self):
        self.scenario("fmc2_overlap_matches_once")

    def test_nonmatching_target_list_never_reserves_sequence_and_matching_list_can_reuse_it(self):
        self.scenario("fmc2_nonmatching_no_reserve")

    def test_modified_new_target_header_never_dispatches_without_valid_signature(self):
        self.scenario("fmc2_target_tamper")

    def test_existing_remote_radio_mutation_blocks_fleet_without_reserving_sequence(self):
        self.scenario("existing_mutation")


if __name__ == "__main__":
    unittest.main()
