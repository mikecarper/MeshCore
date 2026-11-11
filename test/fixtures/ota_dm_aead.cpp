#include <helpers/ota/OtaDmAead.h>
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace mesh::ota::dm_aead;
int main() {
  uint8_t secret[32],sender[32],receiver[32],session[16],data[MAX_MESSAGE],frame[176],other[176],plain[FRAGMENT];
  memset(secret,0x11,32);memset(sender,0x22,32);memset(receiver,0x33,32);memset(session,0x44,16);
  for(size_t i=0;i<sizeof(data);++i)data[i]=uint8_t(i);
  Sender tx;Receiver rx;assert(tx.begin(secret,session,sender,receiver));assert(rx.begin(secret,session,sender,receiver));
  assert(!tx.begin(secret,session,sender,receiver));assert(!rx.begin(secret,session,sender,receiver));
  assert(!tx.seal(data,sizeof(data),1,data,sizeof(data),0));
  assert(!tx.seal(frame,20,1,data,sizeof(data),0));
  const size_t n=tx.seal(frame,sizeof(frame),1,data,sizeof(data),0);assert(n==167);
  for(size_t i=0;i<n;++i){printf("%02x",frame[i]);}puts("");
  for(size_t i=0;i<n;++i){Receiver trial;assert(trial.begin(secret,session,sender,receiver));
    memcpy(other,frame,n);other[i]^=1;FrameInfo f;memset(plain,0xaa,sizeof(plain));
    assert(!trial.open(f,plain,sizeof(plain),other,n));for(uint8_t x:plain)assert(x==0xaa);}
  for(size_t length=0;length<n;++length){Receiver trial;assert(trial.begin(secret,session,sender,receiver));FrameInfo f;assert(!trial.open(f,plain,sizeof(plain),frame,length));}
  Receiver wrong;assert(wrong.begin(secret,session,receiver,sender));FrameInfo f;
  assert(!wrong.open(f,plain,sizeof(plain),frame,n));
  assert(rx.open(f,plain,sizeof(plain),frame,n));assert(f.sequence==1 && f.message==1 && f.index==0 && f.count==2 && f.total==184 && f.length==FRAGMENT);
  assert(!memcmp(plain,data,FRAGMENT));assert(!rx.open(f,plain,sizeof(plain),frame,n));
  const size_t n2=tx.seal(other,sizeof(other),1,data,sizeof(data),1);assert(n2==115);
  assert(rx.open(f,plain,sizeof(plain),other,n2));assert(f.sequence==2 && f.index==1 && f.length==184-FRAGMENT);
  assert(!memcmp(plain,data+FRAGMENT,184-FRAGMENT));
  const size_t retry=tx.seal(other,sizeof(other),1,data,sizeof(data),0);assert(retry==n && memcmp(frame,other,n));
  assert(rx.open(f,plain,sizeof(plain),other,retry));assert(f.sequence==3);
  Receiver padded;assert(padded.begin(secret,session,sender,receiver));memcpy(other,frame,n);memset(other+n,0,176-n);
  assert(padded.open(f,plain,sizeof(plain),other,176));other[175]=1;Receiver padbad;assert(padbad.begin(secret,session,sender,receiver));
  assert(!padbad.open(f,plain,sizeof(plain),other,176));
  uint8_t zeros[32]={};Sender bad;assert(!bad.begin(zeros,session,sender,receiver));
  puts("PASS experimental OTA DM AEAD");
}
