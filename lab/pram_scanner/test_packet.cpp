#include <Packet.h>
#include <cassert>
#include <cstring>
int main() {
  mesh::Packet p;
  p.header=ROUTE_TYPE_DIRECT;p.path_len=0;p.payload_len=3;
  p.payload[0]=1;p.payload[1]=2;p.payload[2]=3;
  uint8_t before[255]={},after[255]={};
  auto n=p.writeTo(before);
  p.radio_profile=p.radio_origin=1;p.radio_generation=p.radio_origin_generation=42;
  p.w12_rx={911150000u,911151250u,250000u,42u,32u,1u,7u,5u,true};
  auto m=p.writeTo(after);
  assert(n==m && memcmp(before,after,n)==0); // no on-air metadata
  mesh::Packet queued=p; // models packet pool / delayed queue copy
  p.w12_rx={};
  assert(queued.w12_rx.valid && queued.w12_rx.profile==1 && queued.w12_rx.generation==42);
  assert(queued.w12_rx.logical_freq_hz==911150000u && queued.w12_rx.actual_freq_hz==911151250u);
  assert(queued.w12_rx.bw_hz==250000u && queued.w12_rx.sf==7 && queued.w12_rx.cr==5 && queued.w12_rx.preamble==32);
  assert(queued.readFrom(before,n) && !queued.w12_rx.valid); // reuse can't inherit
}
