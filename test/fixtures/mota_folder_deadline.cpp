#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>
#include <helpers/ota/FolderMotaStore.h>
#include <helpers/ota/MotaSeederProto.h>
#include <helpers/ota/OtaByteIO.h>
using namespace mesh::ota;
struct HostStream : Stream {
  bool request=false, noise=false, bad_checksum=false, error=false;
  bool stalled=false, short_write=false;
  uint32_t byte_delay=0; int stale_reads=0, stale_at_request=0;
  size_t response_pos=0; std::vector<uint8_t> response;
  int available() override { return request ? int(response.size()-response_pos) : (noise ? 3 : 0); }
  int read() override {
    if (!request) {
      if (noise && stale_reads<100) { ++stale_reads; return 0x5a; }
      return -1;
    }
    if (stalled || response_pos==response.size()) return -1;
    g_mock_millis+=byte_delay;
    return response[response_pos++];
  }
  int peek() override { return -1; }
  void flush() override { assert(false); }
  size_t write(uint8_t) override { assert(false); return 0; }
  size_t write(const uint8_t* data, size_t size) override {
    if(short_write) return size-1;
    assert(size>=4 && data[0]==MOTA_SEEDER_REQ_MAGIC0 && data[1]==MOTA_SEEDER_REQ_MAGIC1);
    uint8_t op=data[2], xor_sum=0;
    for(size_t i=2;i<size-1;++i)xor_sum^=data[i];
    assert(xor_sum==data[size-1]);
    stale_at_request=stale_reads; request=true; response_pos=0;
    response={MOTA_SEEDER_RSP_MAGIC0,MOTA_SEEDER_RSP_MAGIC1,op,uint8_t(error ? 1 : MS_STATUS_OK)};
    if(!error && op==MS_OP_SREAD) {
      uint16_t wanted=uint16_t(data[11]) | (uint16_t(data[12])<<8);
      for(uint16_t i=0;i<wanted;++i)response.push_back(uint8_t(i));
    } else if(!error && op==MS_OP_STAT) {
      response.push_back(1); uint8_t length[4]; wr_u32le(length,128);
      response.insert(response.end(),length,length+4);
    }
    xor_sum=0; for(uint8_t value:response)xor_sum^=value;
    response.push_back(bad_checksum ? uint8_t(xor_sum^1) : xor_sum);
    return size;
  }
};
int main() {
  uint8_t mid[4]={1,2,3,4}, data[32]={}, output[32]={};
  for(uint32_t initial : {0u,UINT32_MAX-10u}) {
    HostStream slow; slow.byte_delay=7; g_mock_millis=initial;
    FolderMotaStore store(slow,MotaStreamWritePolicy::NoFlush,20); store.set_mid(mid);
    assert(!store.reopen());
    assert(uint32_t(g_mock_millis-initial)<=27); // last read may cross deadline once
    assert(store.staged_size()==0);
  }
  HostStream stale; stale.noise=true; g_mock_millis=0;
  FolderMotaStore bounded(stale,MotaStreamWritePolicy::NoFlush,20); bounded.set_mid(mid);
  assert(bounded.begin(128)); assert(stale.stale_at_request==3);
  assert(bounded.staged_size()==128);
  HostStream good; FolderMotaStore valid(good,MotaStreamWritePolicy::NoFlush,3000); valid.set_mid(mid);
  assert(valid.begin(128)); assert(valid.write(4,data,sizeof(data)));
  assert(valid.read(0,output,sizeof(output)));
  for(size_t i=0;i<sizeof(output);++i)assert(output[i]==uint8_t(i));
  assert(valid.finalize() && valid.reopen() && valid.staged_size()==128);
  // Keep deliberate SlowFi tolerance: a response arriving within the overall
  // three-second budget is accepted without reducing the configured timeout.
  HostStream slow_valid; slow_valid.byte_delay=250; g_mock_millis=0;
  FolderMotaStore tolerant(slow_valid,MotaStreamWritePolicy::NoFlush,3000);
  assert(tolerant.reopen()); assert(g_mock_millis==2500);
  for(int scenario=0;scenario<4;++scenario) {
    HostStream bad;
    bad.stalled=scenario==0; bad.bad_checksum=scenario==1;
    bad.error=scenario==2; bad.short_write=scenario==3;
    g_mock_millis=0; FolderMotaStore fail(bad,MotaStreamWritePolicy::NoFlush,20);
    assert(!fail.begin(128) && fail.staged_size()==0);
    assert(g_mock_millis<=20);
  }
  puts("Folder mOTA deadline regression checks passed");
}
