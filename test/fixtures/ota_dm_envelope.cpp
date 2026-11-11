#include <helpers/ota/OtaDmEnvelope.h>
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace mesh::ota::dm;
int main() {
  uint8_t key[32],sender[32],receiver[32],session[8],message[MAX_MESSAGE],frame[176],second[176],out[MAX_MESSAGE];
  memset(key,0x11,32);memset(sender,0x22,32);memset(receiver,0x33,32);memset(session,0x44,8);
  for(size_t i=0;i<sizeof(message);++i) message[i]=uint8_t(i);
  const size_t a=encode(frame,sizeof(frame),key,sender,receiver,session,1,1,message,sizeof(message),0);
  const size_t b=encode(second,sizeof(second),key,sender,receiver,session,2,1,message,sizeof(message),1);
  assert(a==167 && b==99);
  for(size_t i=0;i<a;++i) { printf("%02x",frame[i]); } puts("");
  FragmentView fa,fb;assert(decode(fa,frame,a,key,sender,receiver));assert(decode(fb,second,b,key,sender,receiver));
  assert(!decode(fa,frame,a,key,receiver,sender));
  for(size_t i=0;i<a;++i) { frame[i]^=1;assert(!decode(fa,frame,a,key,sender,receiver));frame[i]^=1; }
  for(size_t n=0;n<a;++n) assert(!decode(fa,frame,n,key,sender,receiver));
  frame[HEADER+5]^=1;assert(!decode(fa,frame,a,key,sender,receiver));frame[HEADER+5]^=1;
  assert(decode(fa,frame,a,key,sender,receiver));
  memset(frame+a,0,176-a);assert(decode(fa,frame,176,key,sender,receiver));frame[175]=1;
  assert(!decode(fa,frame,176,key,sender,receiver));frame[175]=0;
  assert(decode(fa,frame,a,key,sender,receiver));
  Reassembler r;size_t len=0;assert(r.begin(session));
  assert(r.receive(second,b,key,sender,receiver,1,out,sizeof(out),len)==Reassembler::Result::Pending);
  assert(r.receive(second,b,key,sender,receiver,2,out,sizeof(out),len)==Reassembler::Result::Replay);
  assert(r.receive(frame,a,key,sender,receiver,3,out,sizeof(out),len)==Reassembler::Result::Complete);
  assert(len==sizeof(message) && !memcmp(out,message,len));
  const size_t c=encode(frame,sizeof(frame),key,sender,receiver,session,3,1,message,sizeof(message),0);
  assert(decode(fa,frame,c,key,sender,receiver));assert(r.receive(frame,c,key,sender,receiver,4,out,sizeof(out),len)==Reassembler::Result::Replay);
  Reassembler dup;assert(dup.begin(session));
  const size_t d=encode(frame,sizeof(frame),key,sender,receiver,session,4,2,message,sizeof(message),0);
  assert(decode(fa,frame,d,key,sender,receiver));assert(dup.receive(frame,d,key,sender,receiver,5,out,sizeof(out),len)==Reassembler::Result::Pending);
  const size_t e=encode(frame,sizeof(frame),key,sender,receiver,session,5,2,message,sizeof(message),0);
  assert(decode(fa,frame,e,key,sender,receiver));assert(dup.receive(frame,e,key,sender,receiver,6,out,sizeof(out),len)==Reassembler::Result::Duplicate);
  message[3]^=1;const size_t bad=encode(frame,sizeof(frame),key,sender,receiver,session,6,2,message,sizeof(message),0);message[3]^=1;
  assert(decode(fa,frame,bad,key,sender,receiver));assert(dup.receive(frame,bad,key,sender,receiver,7,out,sizeof(out),len)==Reassembler::Result::Invalid);
  const size_t last=encode(second,sizeof(second),key,sender,receiver,session,7,2,message,sizeof(message),1);
  assert(decode(fb,second,last,key,sender,receiver));assert(dup.receive(second,last,key,sender,receiver,8,out,sizeof(out),len)==Reassembler::Result::Complete);
  assert(len==sizeof(message) && !memcmp(out,message,len));
  Reassembler bounded;assert(bounded.begin(session));
  for(uint32_t i=0;i<SLOTS;++i) {
    const size_t n=encode(frame,sizeof(frame),key,sender,receiver,session,10+i,10+i,message,sizeof(message),0);
    assert(decode(fa,frame,n,key,sender,receiver));assert(bounded.receive(frame,n,key,sender,receiver,9,out,sizeof(out),len)==Reassembler::Result::Pending);
  }
  const size_t busy=encode(frame,sizeof(frame),key,sender,receiver,session,20,20,message,sizeof(message),0);
  assert(decode(fa,frame,busy,key,sender,receiver));assert(bounded.receive(frame,busy,key,sender,receiver,10,out,sizeof(out),len)==Reassembler::Result::Busy);
  assert(bounded.receive(frame,busy,key,sender,receiver,EXPIRY_MS+10,out,sizeof(out),len)==Reassembler::Result::Pending);
  puts("PASS OTA DM envelope and bounded reassembly");
}
