#pragma once

// Fixed-RAM, runtime-opt-in observation for Full Companion lab probes only.
#ifndef MESH_ENABLE_RADIO_PROBE_OBSERVER
  #if (defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM)) \
      && defined(COMPANION_RADIO_FULL) && COMPANION_RADIO_FULL \
      && !(defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW)
    #define MESH_ENABLE_RADIO_PROBE_OBSERVER 1
  #else
    #define MESH_ENABLE_RADIO_PROBE_OBSERVER 0
  #endif
#endif
#if !(defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM)) \
    || !defined(COMPANION_RADIO_FULL) || !COMPANION_RADIO_FULL \
    || (defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW)
  #undef MESH_ENABLE_RADIO_PROBE_OBSERVER
  #define MESH_ENABLE_RADIO_PROBE_OBSERVER 0
#endif
#if MESH_ENABLE_RADIO_PROBE_OBSERVER != 0 && MESH_ENABLE_RADIO_PROBE_OBSERVER != 1
  #error "MESH_ENABLE_RADIO_PROBE_OBSERVER must be 0 or 1"
#endif

#if MESH_ENABLE_RADIO_PROBE_OBSERVER
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

namespace mesh {

class RadioProbeObserver {
 public:
  static constexpr unsigned Slots=128, PacketBytes=255, EntriesPerPage=8;

  // Called only after the radio supplied all bytes successfully, before the
  // application parser. The intentionally oversized application payload is
  // never credited here as ordinary application delivery.
  void observe(const uint8_t* raw, int len, float rssi, float snr, uint8_t profile) {
    if (!active_ || !raw || len<16 || memcmp(raw+8,nonce_,8)) return;
    if (len!=PacketBytes || raw[0]!=0x3e || raw[1]!=0
        || memcmp(raw+2,"SPLIT1",6) || raw[16]!='L') { increment(invalid_); return; }
    const uint32_t sequence=uint32_t(raw[17]) | uint32_t(raw[18])<<8
        | uint32_t(raw[19])<<16 | uint32_t(raw[20])<<24;
    if (sequence>=Slots) { increment(invalid_); return; }
    for (unsigned i=0;i<234;++i)
      if (raw[21+i]!=uint8_t(i)) { increment(invalid_); return; }
    if (!isfinite(rssi) || !isfinite(snr)) { increment(invalid_); return; }
    const uint8_t bit=uint8_t(1u<<(sequence&7));
    if (valid_[sequence>>3]&bit) { increment(duplicates_); return; }
    const int16_t dbm=rssi<=-32768.f ? INT16_MIN : rssi>=32767.f ? INT16_MAX : int16_t(rssi);
    const float quarters=snr*4.f;
    const int8_t quarter=quarters<=-128.f ? INT8_MIN : quarters>=127.f ? INT8_MAX : int8_t(quarters);
    uint8_t* record=records_+sequence*4;
    record[0]=uint8_t(uint16_t(dbm)); record[1]=uint8_t(uint16_t(dbm)>>8);
    record[2]=uint8_t(quarter); record[3]=profile<=1 ? profile : 255;
    valid_[sequence>>3]|=bit; ++unique_;
  }

  bool handle(const char* command, char* reply, size_t capacity, bool local) {
    constexpr char status[]="get radio.probe", page[]="get radio.probe.page ", set[]="set radio.probe ";
    if (strcmp(command,status) && strncmp(command,page,sizeof(page)-1)
        && strncmp(command,set,sizeof(set)-1)) return false;
    if (!local) { snprintf(reply,capacity,"Error: radio.probe is local only"); return true; }
    char nonce[17]; hex(nonce,nonce_,8);
    if (!strcmp(command,status)) {
      snprintf(reply,capacity,"> v=1,active=%u,nonce=%s,len=255,slots=128,unique=%lu,duplicates=%lu,invalid=%lu,scope=phy255,app=0",
          unsigned(active_),nonce,(unsigned long)unique_,(unsigned long)duplicates_,(unsigned long)invalid_);
    } else if (!strncmp(command,page,sizeof(page)-1)) {
      const char* value=command+sizeof(page)-1;
      unsigned index=0;
      if (!*value) { snprintf(reply,capacity,"Error: use radio.probe.page 0-15"); return true; }
      for (;*value;++value) {
        if (*value<'0' || *value>'9' || index>15) { snprintf(reply,capacity,"Error: use radio.probe.page 0-15"); return true; }
        index=index*10+unsigned(*value-'0');
      }
      if (index>=Slots/EntriesPerPage) { snprintf(reply,capacity,"Error: use radio.probe.page 0-15"); return true; }
      char records[EntriesPerPage*4*2+1]; hex(records,records_+index*EntriesPerPage*4,EntriesPerPage*4);
      snprintf(reply,capacity,"> v=1,nonce=%s,start=%u,count=8,valid=%02x,records=%s",
          nonce,index*EntriesPerPage,unsigned(valid_[index]),records);
    } else {
      const char* value=command+sizeof(set)-1;
      if (!strcmp(value,"off")) { active_=false; snprintf(reply,capacity,"OK - radio.probe off"); return true; }
      uint8_t next[8];
      if (strlen(value)!=16) { snprintf(reply,capacity,"Error: use radio.probe 16hex or off"); return true; }
      for (unsigned i=0;i<8;++i) {
        const int high=nibble(value[2*i]),low=nibble(value[2*i+1]);
        if (high<0 || low<0) { snprintf(reply,capacity,"Error: use radio.probe 16hex or off"); return true; }
        next[i]=uint8_t((high<<4)|low);
      }
      memcpy(nonce_,next,8); memset(valid_,0,sizeof(valid_)); memset(records_,0,sizeof(records_));
      unique_=duplicates_=invalid_=0; active_=true;
      snprintf(reply,capacity,"OK - radio.probe armed len=255 slots=128");
    }
    return true;
  }

 private:
  static void increment(uint32_t& value) { if (value!=UINT32_MAX) ++value; }
  static int nibble(char c) {
    return c>='0'&&c<='9' ? c-'0' : c>='a'&&c<='f' ? c-'a'+10 : c>='A'&&c<='F' ? c-'A'+10 : -1;
  }
  static void hex(char* out,const uint8_t* bytes,unsigned count) {
    constexpr char digits[]="0123456789abcdef";
    for (unsigned i=0;i<count;++i) { out[2*i]=digits[bytes[i]>>4]; out[2*i+1]=digits[bytes[i]&15]; }
    out[count*2]=0;
  }
  uint8_t records_[Slots*4]={};
  uint8_t valid_[Slots/8]={},nonce_[8]={};
  uint32_t unique_=0,duplicates_=0,invalid_=0;
  bool active_=false;
};
static_assert(sizeof(RadioProbeObserver)<=640,"Bounded probe observer RAM");

}  // namespace mesh
#endif
