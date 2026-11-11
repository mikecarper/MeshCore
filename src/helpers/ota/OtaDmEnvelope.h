#pragma once

// Experimental, unwired codec for an authenticated direct-message OTA
// carrier. This does not negotiate sessions, derive keys, encrypt, route
// packets or enable an OTA mode. Callers must supply an independently
// authenticated per-session 32-byte key and a fresh nonzero session ID.
#include <SHA256.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh { namespace ota { namespace dm {
static constexpr size_t MAX_PLAINTEXT = 167; // Mesh::createDatagram admission, incl. padding allowance.
static constexpr size_t HEADER = 25, TAG = 16, FRAGMENT = MAX_PLAINTEXT - HEADER - TAG;
static constexpr size_t MAX_MESSAGE = 184, MAX_FRAGMENTS = 2, SLOTS = 4;
static constexpr uint32_t EXPIRY_MS = 300000;
static_assert(FRAGMENT * MAX_FRAGMENTS >= MAX_MESSAGE, "DM frame capacity");

inline uint16_t read16(const uint8_t* p) { return uint16_t(p[0]) | (uint16_t(p[1]) << 8); }
inline uint32_t read32(const uint8_t* p) { return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24); }
inline void write16(uint8_t* p, uint16_t n) { p[0]=uint8_t(n); p[1]=uint8_t(n>>8); }
inline void write32(uint8_t* p, uint32_t n) { for (unsigned i=0;i<4;++i) p[i]=uint8_t(n>>(8*i)); }
inline bool nonzero(const uint8_t* p, size_t n) { uint8_t v=0; for(size_t i=0;i<n;++i) v|=p[i]; return v!=0; }
inline void mac(uint8_t out[TAG], const uint8_t key[32], const uint8_t sender[32],
                const uint8_t receiver[32], const uint8_t* data, size_t len) {
  static const uint8_t domain[] = "MeshCore OTA DM v1";
  SHA256 h; h.resetHMAC(key,32); h.update(domain,sizeof(domain)); h.update(sender,32);
  h.update(receiver,32); h.update(data,len); h.finalizeHMAC(key,32,out,TAG);
}
struct FragmentView { uint8_t session[8]; uint32_t sequence, message; uint16_t total; uint8_t index, count, length; const uint8_t* data; };
inline size_t encode(uint8_t* out, size_t capacity, const uint8_t key[32], const uint8_t sender[32],
                     const uint8_t receiver[32], const uint8_t session[8], uint32_t sequence,
                     uint32_t message, const uint8_t* data, size_t total, uint8_t index) {
  if (!out || !key || !sender || !receiver || !session || !data || !nonzero(key,32) ||
      !nonzero(session,8) || !memcmp(sender,receiver,32) || !sequence || !message ||
      !total || total>MAX_MESSAGE) return 0;
  const size_t count=(total+FRAGMENT-1)/FRAGMENT;
  if (index>=count) return 0;
  const size_t offset=size_t(index)*FRAGMENT, length=(total-offset<FRAGMENT)?total-offset:FRAGMENT;
  if (capacity<HEADER+length+TAG) return 0;
  memcpy(out,"ODM1",4); memcpy(out+4,session,8);write32(out+12,sequence);write32(out+16,message);
  write16(out+20,uint16_t(total));out[22]=index;out[23]=uint8_t(count);out[24]=uint8_t(length);
  memcpy(out+HEADER,data+offset,length);mac(out+HEADER+length,key,sender,receiver,out,HEADER+length);
  return HEADER+length+TAG;
}
inline bool decode(FragmentView& f,const uint8_t* in,size_t length,const uint8_t key[32],
                   const uint8_t sender[32],const uint8_t receiver[32]) {
  if (!in || !key || !sender || !receiver || !nonzero(key,32) || !memcmp(sender,receiver,32) ||
      length<HEADER+TAG || length>176 || memcmp(in,"ODM1",4) || !nonzero(in+4,8)) return false;
  const uint32_t seq=read32(in+12), message=read32(in+16);const uint16_t total=read16(in+20);
  const uint8_t index=in[22],count=in[23],bytes=in[24];
  if (!seq || !message || !total || total>MAX_MESSAGE || count!=(total+FRAGMENT-1)/FRAGMENT || index>=count) return false;
  const size_t offset=size_t(index)*FRAGMENT, expected=(total-offset<FRAGMENT)?total-offset:FRAGMENT;
  const size_t used=HEADER+expected+TAG;
  if (bytes!=expected || length<used || (length!=used && length!=((used+15)&~size_t(15)))) return false;
  uint8_t diff=0, tag[TAG]; for(size_t i=used;i<length;++i) diff|=in[i];
  mac(tag,key,sender,receiver,in,HEADER+expected);for(size_t i=0;i<TAG;++i)diff|=tag[i]^in[HEADER+expected+i];
  if(diff) return false;
  memcpy(f.session,in+4,8);f.sequence=seq;f.message=message;f.total=total;f.index=index;f.count=count;
  f.length=bytes;f.data=in+HEADER;return true;
}
// Caller authenticates the negotiated session; every frame is authenticated
// again inside receive(). Sequences and message IDs are monotonic and may not
// wrap; a fresh negotiated session is then required.
class Reassembler {
public:
  enum class Result { Invalid, Replay, Busy, Pending, Duplicate, Complete };
  bool begin(const uint8_t session[8]) { if(!session || !nonzero(session,8)) return false;reset();memcpy(_session,session,8);return true; }
  void reset() { memset(_session,0,sizeof(_session));memset(_slots,0,sizeof(_slots));_seqHigh=_msgHigh=0;_seqBits=_msgBits=0; }
  Result receive(const uint8_t* in,size_t len,const uint8_t key[32],const uint8_t sender[32],
                 const uint8_t receiver[32],uint32_t now,uint8_t* out,size_t cap,size_t& outLen) {
    FragmentView f;outLen=0;
    if(!decode(f,in,len,key,sender,receiver)) return Result::Invalid;
    return accept(f,now,out,cap,outLen);
  }
private:
  Result accept(const FragmentView& f,uint32_t now,uint8_t* out,size_t cap,size_t& outLen) {
    outLen=0;
    if (!out || !f.data || !nonzero(_session,8) || memcmp(f.session,_session,8) || !f.sequence || !f.message ||
        !f.total || f.total>MAX_MESSAGE || f.count!=(f.total+FRAGMENT-1)/FRAGMENT || f.index>=f.count ||
        f.length!=((f.total-size_t(f.index)*FRAGMENT<FRAGMENT)?f.total-size_t(f.index)*FRAGMENT:FRAGMENT)) return Result::Invalid;
    if (seen(_seqHigh,_seqBits,f.sequence) || seen(_msgHigh,_msgBits,f.message)) return Result::Replay;
    Slot* slot=nullptr;Slot* freeSlot=nullptr;
    for(size_t i=0;i<SLOTS;++i) {
      if (_slots[i].used && uint32_t(now-_slots[i].last)>EXPIRY_MS) _slots[i].used=false;
      if (_slots[i].used && _slots[i].id==f.message) slot=&_slots[i];
      if (!_slots[i].used && !freeSlot) freeSlot=&_slots[i];
    }
    if (!slot && !freeSlot) return Result::Busy;
    if (!slot) { slot=freeSlot;memset(slot,0,sizeof(*slot));slot->used=true;slot->id=f.message;slot->total=f.total;slot->count=f.count; }
    if (slot->total!=f.total || slot->count!=f.count) return Result::Invalid;
    const size_t offset=size_t(f.index)*FRAGMENT;const uint8_t bit=uint8_t(1u<<f.index);
    if (slot->mask&bit) {
      if (memcmp(slot->data+offset,f.data,f.length)) return Result::Invalid;
      mark(_seqHigh,_seqBits,f.sequence);slot->last=now;return Result::Duplicate;
    }
    if (cap<f.total) return Result::Invalid;
    memcpy(slot->data+offset,f.data,f.length);slot->mask|=bit;slot->last=now;mark(_seqHigh,_seqBits,f.sequence);
    if (slot->mask!=uint8_t((1u<<slot->count)-1)) return Result::Pending;
    memcpy(out,slot->data,slot->total);outLen=slot->total;mark(_msgHigh,_msgBits,f.message);slot->used=false;return Result::Complete;
  }
  struct Slot { uint8_t data[MAX_MESSAGE];uint32_t id,last;uint16_t total;uint8_t mask,count;bool used; };
  static bool seen(uint32_t high,uint64_t bits,uint32_t n) { if(!high||n>high)return false;const uint32_t d=high-n;return d>=64 || ((bits>>d)&1); }
  static void mark(uint32_t& high,uint64_t& bits,uint32_t n) { if(!high){high=n;bits=1;return;}if(n>high){const uint32_t d=n-high;bits=d>=64?1:(bits<<d)|1;high=n;}else{const uint32_t d=high-n;if(d<64)bits|=uint64_t(1)<<d;} }
  uint8_t _session[8] = {};Slot _slots[SLOTS] = {};uint32_t _seqHigh=0,_msgHigh=0;uint64_t _seqBits=0,_msgBits=0;
};
}}} // namespace mesh::ota::dm
