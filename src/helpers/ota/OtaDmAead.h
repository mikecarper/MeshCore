#pragma once

// Experimental, unwired per-direction OTA DM AEAD primitive. No radio,
// capability negotiation, reassembly or identity authentication is provided.
// Its caller MUST authenticate both peer identities and the session ID, use a
// fresh unpredictable 128-bit session ID with each shared secret, and prevent
// nonce reuse across independent instances and reboots.
#include <ChaChaPoly.h>
#include <HKDF.h>
#include <SHA256.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh { namespace ota { namespace dm_aead {
static constexpr size_t KEY=32, SESSION=16, HEADER=33, TAG=16, MAX_PLAINTEXT=167;
static constexpr size_t FRAGMENT=MAX_PLAINTEXT-HEADER-TAG, MAX_MESSAGE=184;
static_assert(FRAGMENT*2 >= MAX_MESSAGE, "OTA message needs more than two fragments");
inline uint16_t rd16(const uint8_t* p){return uint16_t(p[0])|(uint16_t(p[1])<<8);}
inline uint32_t rd32(const uint8_t* p){return uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24);}
inline void wr16(uint8_t* p,uint16_t n){p[0]=uint8_t(n);p[1]=uint8_t(n>>8);}
inline void wr32(uint8_t* p,uint32_t n){for(unsigned i=0;i<4;++i)p[i]=uint8_t(n>>(8*i));}
inline void wipe(uint8_t* p,size_t n){volatile uint8_t* v=p;while(n--)*v++=0;}
inline bool nonzero(const uint8_t* p,size_t n){uint8_t v=0;for(size_t i=0;i<n;++i)v|=p[i];return v!=0;}
inline bool overlap(const void* a,size_t an,const void* b,size_t bn){
  const uintptr_t x=reinterpret_cast<uintptr_t>(a),y=reinterpret_cast<uintptr_t>(b);
  return x<=y ? y-x<an : x-y<bn;
}
inline bool derive(uint8_t key[KEY],const uint8_t secret[KEY],const uint8_t session[SESSION],
                   const uint8_t sender[KEY],const uint8_t receiver[KEY]) {
  if(!key||!secret||!session||!sender||!receiver||!nonzero(secret,KEY)||!nonzero(session,SESSION)||
     !nonzero(sender,KEY)||!nonzero(receiver,KEY)||!memcmp(sender,receiver,KEY)) return false;
  static const uint8_t domain[]="MeshCore OTA DM AEAD key v1";
  uint8_t info[sizeof(domain)+2*KEY];memcpy(info,domain,sizeof(domain));
  memcpy(info+sizeof(domain),sender,KEY);memcpy(info+sizeof(domain)+KEY,receiver,KEY);
  HKDF<SHA256> kdf;kdf.setKey(secret,KEY,session,SESSION);kdf.extract(key,KEY,info,sizeof(info));kdf.clear();wipe(info,sizeof(info));
  return true;
}
inline void nonceFor(uint8_t nonce[12],uint32_t sequence){memset(nonce,0,12);wr32(nonce+8,sequence);}
inline void aad(ChaChaPoly& cipher,const uint8_t sender[KEY],const uint8_t receiver[KEY],const uint8_t header[HEADER]) {
  static const uint8_t domain[]="MeshCore OTA DM AEAD frame v1";
  cipher.addAuthData(domain,sizeof(domain));cipher.addAuthData(sender,KEY);cipher.addAuthData(receiver,KEY);cipher.addAuthData(header,HEADER);
}
struct FrameInfo { uint32_t sequence=0,message=0;uint16_t total=0;uint8_t index=0,count=0,length=0; };
class Sender {
public:
  Sender()=default;Sender(const Sender&)=delete;Sender& operator=(const Sender&)=delete;
  ~Sender(){wipe(_key,sizeof(_key));}
  bool begin(const uint8_t secret[KEY],const uint8_t session[SESSION],const uint8_t sender[KEY],const uint8_t receiver[KEY]) {
    if(_ready||!derive(_key,secret,session,sender,receiver))return false;
    memcpy(_session,session,SESSION);memcpy(_sender,sender,KEY);memcpy(_receiver,receiver,KEY);_next=1;_ready=true;return true;
  }
  size_t seal(uint8_t* out,size_t capacity,uint32_t message,const uint8_t* data,size_t total,uint8_t index){
    if(!_ready||!_next||!out||!data||!message||!total||total>MAX_MESSAGE)return 0;
    const size_t count=(total+FRAGMENT-1)/FRAGMENT;if(index>=count)return 0;
    const size_t offset=size_t(index)*FRAGMENT,length=(total-offset<FRAGMENT)?total-offset:FRAGMENT;
    if(capacity<HEADER+length+TAG||overlap(out,HEADER+length+TAG,data,total))return 0;
    memcpy(out,"ODA1",4);memcpy(out+4,_session,SESSION);wr32(out+20,_next);wr32(out+24,message);
    wr16(out+28,uint16_t(total));out[30]=index;out[31]=uint8_t(count);out[32]=uint8_t(length);
    uint8_t nonce[12];nonceFor(nonce,_next);ChaChaPoly cipher;
    if(!cipher.setKey(_key,KEY)||!cipher.setIV(nonce,sizeof(nonce))){wipe(nonce,sizeof(nonce));return 0;}
    aad(cipher,_sender,_receiver,out);cipher.encrypt(out+HEADER,data+offset,length);cipher.computeTag(out+HEADER+length,TAG);
    cipher.clear();wipe(nonce,sizeof(nonce));++_next;return HEADER+length+TAG;
  }
private:
  uint8_t _key[KEY]={},_session[SESSION]={},_sender[KEY]={},_receiver[KEY]={};uint32_t _next=0;bool _ready=false;
};
class Receiver {
public:
  Receiver()=default;Receiver(const Receiver&)=delete;Receiver& operator=(const Receiver&)=delete;
  ~Receiver(){wipe(_key,sizeof(_key));}
  bool begin(const uint8_t secret[KEY],const uint8_t session[SESSION],const uint8_t sender[KEY],const uint8_t receiver[KEY]) {
    if(_ready||!derive(_key,secret,session,sender,receiver))return false;
    memcpy(_session,session,SESSION);memcpy(_sender,sender,KEY);memcpy(_receiver,receiver,KEY);_ready=true;return true;
  }
  bool open(FrameInfo& info,uint8_t* out,size_t capacity,const uint8_t* frame,size_t size){
    info=FrameInfo();
    if(!_ready||!out||!frame||size<HEADER+TAG||size>176||memcmp(frame,"ODA1",4)||memcmp(frame+4,_session,SESSION))return false;
    const uint32_t seq=rd32(frame+20),message=rd32(frame+24);const uint16_t total=rd16(frame+28);
    const uint8_t index=frame[30],count=frame[31],length=frame[32];
    if(!seq||!message||!total||total>MAX_MESSAGE||count!=(total+FRAGMENT-1)/FRAGMENT||index>=count||seen(seq))return false;
    const size_t off=size_t(index)*FRAGMENT,want=(total-off<FRAGMENT)?total-off:FRAGMENT,used=HEADER+want+TAG;
    if(length!=want||capacity<want||size<used||(size!=used&&size!=((used+15)&~size_t(15))))return false;
    for(size_t i=used;i<size;++i)if(frame[i])return false;
    uint8_t nonce[12],plain[FRAGMENT];nonceFor(nonce,seq);ChaChaPoly cipher;
    if(!cipher.setKey(_key,KEY)||!cipher.setIV(nonce,sizeof(nonce))){wipe(nonce,sizeof(nonce));return false;}
    aad(cipher,_sender,_receiver,frame);cipher.decrypt(plain,frame+HEADER,want);
    const bool ok=cipher.checkTag(frame+HEADER+want,TAG);cipher.clear();wipe(nonce,sizeof(nonce));
    if(!ok){wipe(plain,sizeof(plain));return false;}
    memcpy(out,plain,want);wipe(plain,sizeof(plain));mark(seq);
    info.sequence=seq;info.message=message;info.total=total;info.index=index;info.count=count;info.length=length;return true;
  }
private:
  bool seen(uint32_t seq)const{if(!_high||seq>_high)return false;const uint32_t d=_high-seq;return d>=64||((_bits>>d)&1);}
  void mark(uint32_t seq){if(!_high){_high=seq;_bits=1;}else if(seq>_high){const uint32_t d=seq-_high;_bits=d>=64?1:(_bits<<d)|1;_high=seq;}else _bits|=uint64_t(1)<<(_high-seq);}
  uint8_t _key[KEY]={},_session[SESSION]={},_sender[KEY]={},_receiver[KEY]={};uint32_t _high=0;uint64_t _bits=0;bool _ready=false;
};
}}} // namespace mesh::ota::dm_aead
