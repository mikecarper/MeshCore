#include "FakeFilesystem.h"
#include <array>
#include <helpers/LazyPersistence.h>
#include <helpers/PowerSavingDiagnostics.h>
#define FILESYSTEM FakeFilesystem
namespace mesh { struct Utils {
  static void toHex(char* dest, const uint8_t* src, int length) {
    for (int i=0; i<length; ++i) sprintf(dest+2*i, "%02X", src[i]);
    dest[2*length]=0;
  }
}; }
// Advert jobs are tested with Morse idle; actual Morse deferral is covered by
// test_morse_loop_policy.py and the production role-loop extraction fixtures.
namespace mesh { template<class Radio> bool morseTimingActive(const Radio*) { return false; } }
struct ContactInfo {};
class MyMesh;
using DataStoreHost = MyMesh;
class DataStore {
public:
  FakeFilesystem* _fs;
  bool _identity_creation_blocked=false, _prefs_load_incomplete=false;
  bool _channel_load_incomplete=false, _contact_write_requested=false;
  bool _uncached_contact_load_incomplete=false, _cache_load_incomplete=false;
  const char* _prefs_recovery_source=nullptr;
  const char* _channel_recovery_source=nullptr;
  File _contact_path_reader;
  bool contact_pending=false;
  unsigned contact_passes=0;
#include "advert_state.h"
#include "contact_recovery.h"
  explicit DataStore(FakeFilesystem& fs):_fs(&fs) {}
  File openRead(FakeFilesystem* fs,const char* path){return fs->open(path);}
  void cancelContactWrite(){}
  void begin();
  bool formatFileSystem();
  bool queueAdvertByKey(const uint8_t[],int,const uint8_t[],uint8_t);
  bool serviceAdvertWrites(uint32_t);
  bool flushAdvertWrites(uint32_t now=0);
  bool hasPendingAdvertWrites() const;
  bool isAdvertWriteDue(uint32_t) const;
  bool consumeSynchronousAdvertIO();
  uint8_t getBlobByKey(const uint8_t[],int,uint8_t[]);
  bool putBlobByKey(const uint8_t[],int,const uint8_t[],uint8_t);
  bool deleteBlobByKey(const uint8_t[],int);
  bool hasPendingContactWrites() const{return contact_pending;}
  bool serviceContactWrites(DataStoreHost*,bool(*)(const ContactInfo&)) {
    ++_fs->operations; ++contact_passes; return true;
  }
  bool flushContactWrites(DataStoreHost*,bool(*)(const ContactInfo&)) {
    ++_fs->operations; ++contact_passes; contact_pending=false; return true;
  }
};
#include "presence.h"
#include "store.h"
constexpr uint32_t CONTACT_PAGE_WRITE_GAP=1;
struct Clock { uint32_t now=10000; uint32_t getMillis() const{return now;} };
struct RadioDriver {
  bool isWatchdogObserving() const{return false;}
  bool isCalibratingNoiseFloor() const{return false;}
} radio_driver;
struct SerialInterface {
  bool hasPendingIO() const{return false;}
  bool isReplyRouteAvailable(const SerialInterface*) const{return false;}
};
struct DelayedReplies { bool hasBinaryTrace() const{return false;} };
class MyMesh {
public:
  DataStore* _store;
  Clock* _ms;
  RadioDriver* _radio=&radio_driver;
  bool _advert_write_next=true;
  unsigned long dirty_contacts_expiry=0;
  uint8_t dirty_contacts_failures=0;
  bool(*save_filter)(const ContactInfo&)=nullptr;
  bool _radio_available=false, _iter_started=false;
  bool command_radio_apply_pending=false, saved_radio_apply_pending=false;
  uint32_t _scheduled_reboot_at=0, radio_apply_retry_at=0;
  uint32_t emergency_client_repeat_send_at=0;
  void* sign_data=nullptr;
  void* emergency_client_repeat_packet=nullptr;
  SerialInterface* _serial=nullptr;
  SerialInterface* _iter_reply_route=nullptr;
  DelayedReplies _delayed_replies;
  bool putBlobByKey(const uint8_t[],int,const uint8_t[],int);
  void servicePersistence();
  void onContactCacheFlushed();
  bool flushContactsBeforeReboot();
  bool hasPendingWork() const;
  mesh::powerdiag::Mask pendingWorkReason() const;
  bool canRecoverUsbLogging() const;
  bool isDualRadioActive() const{return false;}
  bool hasOutbound() const{return false;}
  bool isAnyTempRadioActive() const{return false;}
  bool hasPendingOtaApply() const{return false;}
  bool hasPendingReqs() const{return false;}
  bool hasQueuedWorkDue() const{return false;}
  bool hasRetryWorkDue() const{return false;}
  bool millisHasNowPassed(uint32_t when) const{return _ms->now>=when;}
  bool isContactWriteDue() const{return _store->contact_pending;}
  void scheduleContactWriteRetry(){++dirty_contacts_failures;}
  unsigned long futureMillis(uint32_t delay) const{return _ms->now+delay;}
};
#include "mesh.h"
using Key=std::array<uint8_t,32>;
using Blob=std::vector<uint8_t>;
using Stage=DataStore::AdvertWriteState::Stage;
Key key(unsigned n){Key k{};k[0]=n;return k;}
Blob blob(unsigned n, unsigned len=255){return Blob(len,n);}
std::string path(const Key& key){char path[64];makeBlobPath(key.data(),32,path,sizeof(path));return path;}
Blob get(DataStore& store,const Key& k){uint8_t bytes[255];auto n=store.getBlobByKey(k.data(),32,bytes);return Blob(bytes,bytes+n);}
void enqueue(DataStore& store,const Key& k,const Blob& b){assert(store.queueAdvertByKey(k.data(),32,b.data(),b.size()));}
void step(DataStore& store,FakeFilesystem& fs,uint32_t now=10000) {
  auto before=fs.operations, backend=fs.backend_writes;
  fs.max_backend_write=0;
  store.serviceAdvertWrites(now);
  assert(fs.operations-before<=1 && "one storage operation per cooperative pass");
  assert(fs.backend_writes-backend<=1);
  assert(fs.max_backend_write<=64);
}
void drain(DataStore& store,FakeFilesystem& fs) {
  unsigned n=0;
  while(store.hasPendingAdvertWrites()){assert(++n<1000);step(store,fs,10000+n*1000);}
  assert(fs.handles==0);
}
void until(DataStore& store,FakeFilesystem& fs,Stage target) {
  unsigned n=0;
  do {assert(++n<1000);step(store,fs,10000+n*1000);}
  while(store._advert_write.stage!=target);
}

void rxRouteAndScheduling() {
  FakeFilesystem fs;DataStore store(fs);Clock clock;MyMesh mesh{&store,&clock};
  auto k=key(1);auto b=blob(1);
  const auto before=fs.operations;
  assert(mesh.putBlobByKey(k.data(),32,b.data(),b.size()));
  assert(fs.operations==before && "advert_rx_no_fs");
  assert(get(store,k)==b && fs.operations==before);
  store.contact_pending=true;
  unsigned blob_passes=0, serial_services=0;
  while(store.hasPendingAdvertWrites()) {
    ++serial_services;
    auto old=fs.operations, contacts=store.contact_passes;
    mesh.servicePersistence();
    assert(fs.operations-old<=1);
    if(store.contact_passes==contacts)++blob_passes;
    ++clock.now;
  }
  assert(serial_services>20 && blob_passes>10 && store.contact_passes>10);
  assert(fs.files.at(path(k))==b && !fs.exists("/advert.tmp"));
  // Explicit durability work must not be followed by another automatic job.
  enqueue(store,k,blob(2));assert(store.putBlobByKey(k.data(),32,b.data(),b.size()));
  auto old=fs.operations;mesh.servicePersistence();assert(old==fs.operations);
  // Disk share/export work consumes the same pass; a RAM share does not.
  store.consumeSynchronousAdvertIO();
  assert(get(store,k)==b);enqueue(store,key(2),blob(2));
  old=fs.operations;mesh.servicePersistence();assert(old==fs.operations);
  assert(get(store,key(2))==blob(2));
  old=fs.operations;mesh.servicePersistence();assert(fs.operations==old+1);
  store.contact_pending=false;drain(store,fs);
}

void coalesceOverflowAndDeleteEveryStage() {
  for(unsigned stage=0;stage<24;++stage) {
    FakeFilesystem fs;DataStore store(fs);auto k=key(1), other=key(2);auto old=blob(1), fresh=blob(2);
    fs.files[path(k)]=old;enqueue(store,k,fresh);
    for(unsigned i=0;i<stage && store.hasPendingAdvertWrites();++i)step(store,fs);
    enqueue(store,k,blob(3));assert(get(store,k)==blob(3));
    drain(store,fs);assert(fs.files.at(path(k))==blob(3));
    enqueue(store,k,old);
    for(unsigned i=0;i<stage && store.hasPendingAdvertWrites();++i)step(store,fs);
    assert(store.deleteBlobByKey(k.data(),32));
    // Reusing the active slot must not turn its old temp into another key.
    enqueue(store,other,fresh);drain(store,fs);
    assert(get(store,k).empty() && fs.files.at(path(other))==fresh);
  }
  FakeFilesystem fs;DataStore store(fs);
  for(unsigned i=1;i<=4;++i)enqueue(store,key(i),blob(i));
  auto before=fs.operations;
  auto fifth=key(5);auto b=blob(5);
  assert(!store.queueAdvertByKey(fifth.data(),32,b.data(),b.size()));
  assert(fs.operations==before);enqueue(store,key(2),blob(22));
  Key same_prefix=key(2);same_prefix[31]=99;
  assert(get(store,same_prefix)==blob(22));
  drain(store,fs);assert(fs.files.at(path(key(2)))==blob(22));
}

void crashBoundaries() {
  for(unsigned stop=0;stop<26;++stop) {
    FakeFilesystem fs;auto k=key(1);auto old=blob(1), fresh=blob(2);
    fs.files[path(k)]=old;
    {
      DataStore store(fs);enqueue(store,k,fresh);
      for(unsigned i=0;i<stop && store.hasPendingAdvertWrites();++i)step(store,fs);
    }
    assert(fs.handles==0);
    DataStore boot(fs);auto value=get(boot,k);
    assert(value==old || value==fresh);
    assert(!boot.hasPendingAdvertWrites());
    enqueue(boot,k,blob(3));drain(boot,fs);
    assert(get(boot,k)==blob(3));
    assert(!fs.exists((path(k)+".bak").c_str()));
  }
}

void synchronousMutationAndRollback() {
  for(unsigned stage=0;stage<24;++stage) {
    FakeFilesystem fs;DataStore store(fs);auto k=key(1);auto old=blob(1), queued=blob(2), explicit_blob=blob(3);
    fs.files[path(k)]=old;enqueue(store,k,queued);
    for(unsigned i=0;i<stage && store.hasPendingAdvertWrites();++i)step(store,fs);
    assert(store.putBlobByKey(k.data(),32,explicit_blob.data(),explicit_blob.size()));
    drain(store,fs);assert(get(store,k)==explicit_blob);
    enqueue(store,k,queued);until(store,fs,Stage::Publish);
    fs.short_write=true;
    assert(!store.putBlobByKey(k.data(),32,explicit_blob.data(),explicit_blob.size()));
    assert(get(store,k)==queued);fs.short_write=false;drain(store,fs);assert(get(store,k)==queued);
    enqueue(store,k,old);until(store,fs,Stage::Publish);
    fs.fail_remove=fs.remove_calls+1;
    assert(!store.deleteBlobByKey(k.data(),32));
    assert(get(store,k)==old);fs.fail_remove=0;drain(store,fs);assert(get(store,k)==old);
  }
  FakeFilesystem fs;DataStore store(fs);auto k=key(1);auto old=blob(1), fresh=blob(2);
  fs.files[path(k)]=old;fs.files[path(k)+".bak"]=blob(8);enqueue(store,k,fresh);
  fs.fail_remove=fs.remove_calls+1;
  assert(!store.deleteBlobByKey(k.data(),32));assert(fs.files.at(path(k))==old);
  fs.fail_remove=0;fs.fail_metadata=true;
  auto removes=fs.remove_calls;assert(!store.deleteBlobByKey(k.data(),32));assert(fs.remove_calls==removes);
  fs.fail_metadata=false;drain(store,fs);assert(get(store,k)==fresh);
  fs.files[path(k)]=old;fs.files[path(k)+".bak"]=blob(8);enqueue(store,k,fresh);
  fs.fail_metadata_at=fs.metadata_calls+2;
  removes=fs.remove_calls;assert(!store.deleteBlobByKey(k.data(),32));
  assert(fs.remove_calls==removes && fs.files.at(path(k))==old);
  fs.fail_metadata_at=0;fs.fail_remove=fs.remove_calls+2;
  assert(!store.deleteBlobByKey(k.data(),32));
  assert(fs.files.at(path(k))==old && !fs.exists((path(k)+".bak").c_str()));
  fs.fail_remove=0;drain(store,fs);assert(get(store,k)==fresh);
}

void failuresAndLifecycle() {
  for(unsigned fault=0;fault<9;++fault) {
    FakeFilesystem fs;DataStore store(fs);auto k=key(1);auto old=blob(1), fresh=blob(2);
    fs.files[path(k)]=old;enqueue(store,k,fresh);
    switch(fault) {
      case 0:fs.fail_metadata=true;break;
      case 1:fs.fail_open=true;fs.partial_open=true;break;
      case 2:fs.fail_config=1;break;
      case 3:fs.short_write=true;break;
      case 4:fs.fail_close=true;break;
      case 5:fs.fail_read=true;break;
      case 6:fs.fail_rename=1;break;
      case 7:fs.fail_rename=2;break;
      case 8:fs.fail_config=2;break;
    }
    assert(!store.flushAdvertWrites(10000));
    assert(fs.handles==0 && get(store,k)==fresh);
    assert(!store.isAdvertWriteDue(10999));
    fs.fail_metadata=fs.fail_open=fs.partial_open=fs.short_write=fs.fail_close=fs.fail_read=false;
    fs.fail_config=fs.fail_rename=0;drain(store,fs);
    assert(get(store,k)==fresh && !fs.exists("/advert.tmp"));
  }
  {
    FakeFilesystem fs;DataStore store(fs);auto k=key(1);auto old=blob(1), fresh=blob(2);
    fs.files[path(k)]=old;enqueue(store,k,fresh);until(store,fs,Stage::OpenVerify);
    fs.files.at("/advert.tmp")[73]^=1;
    assert(!store.flushAdvertWrites(10000));
    assert(fs.files.at(path(k))==old && get(store,k)==fresh && fs.handles==0);
    drain(store,fs);assert(get(store,k)==fresh);
  }
  FakeFilesystem fs;DataStore store(fs);auto k=key(1);auto b=blob(1);
  enqueue(store,k,b);until(store,fs,Stage::Write);assert(fs.handles==1);
  store.begin();assert(fs.handles==0 && !store.hasPendingAdvertWrites());
  enqueue(store,k,b);until(store,fs,Stage::Write);fs.fail_format=true;
  store._contact_recovery=DataStore::ContactStorageUnavailable;
  assert(!store.formatFileSystem());assert(fs.handles==0 && get(store,k)==b);
  assert(store.contactsAreVolatile());
  fs.fail_format=false;drain(store,fs);enqueue(store,k,blob(2));
  assert(store.formatFileSystem());assert(!store.hasPendingAdvertWrites() && get(store,k).empty());
  assert(!store.contactsAreVolatile() && !store.contactRecoveryPending());
  // Reboot and identity-import paths use the real MyMesh durability gate.
  Clock clock;MyMesh mesh{&store,&clock};enqueue(store,k,b);
  assert(mesh.flushContactsBeforeReboot());assert(get(store,k)==b && fs.handles==0);
  enqueue(store,k,blob(2));fs.fail_open=true;
  assert(!mesh.flushContactsBeforeReboot());assert(fs.handles==0);
  auto before=fs.operations;assert(!mesh.flushContactsBeforeReboot());assert(fs.operations==before);
  // Persistent errors use the shared bounded exponential retry policy;
  // received coalescing adverts cannot postpone or cancel the backoff.
  FakeFilesystem broken_fs;DataStore broken(broken_fs);enqueue(broken,k,b);
  broken_fs.fail_metadata=true;uint32_t now=10000;
  for(unsigned failure=0;failure<12;++failure) {
    while(broken.serviceAdvertWrites(now)) ++now;
    uint32_t delay=broken._advert_write.retry_at-now;
    assert(delay==std::min(1000u<<failure,300000u));
    enqueue(broken,k,blob(2));
    auto ops=broken_fs.operations;broken.serviceAdvertWrites(now+delay-1);
    assert(ops==broken_fs.operations && get(broken,k)==blob(2));
    now+=delay;
  }
}

void pendingWorkAndRecovery() {
  FakeFilesystem fs;DataStore store(fs);Clock clock;MyMesh mesh{&store,&clock};
  const auto k=key(1);const auto b=blob(1);
  assert(!mesh.hasPendingWork() && mesh.canRecoverUsbLogging());
  store._contact_recovery=DataStore::ContactRecoveryPending;
  assert(mesh.hasPendingWork());
  store._contact_recovery=0;
  enqueue(store,k,b);
  assert(mesh.hasPendingWork() && !mesh.canRecoverUsbLogging());
  fs.fail_open=true;
  assert(!store.flushAdvertWrites(clock.now));
  assert(!store.isAdvertWriteDue(clock.now));
  // An accepted RAM packet awaiting retry must not be abandoned by sleep or
  // discarded when the USB logger's recovery path resets the transport.
  assert(mesh.hasPendingWork() && !mesh.canRecoverUsbLogging());
  fs.fail_open=false;clock.now+=1000;until(store,fs,Stage::Write);
  assert(store.deleteBlobByKey(k.data(),32));
  assert(get(store,k).empty());
  // Successful deletion retires RAM immediately, while the old file handle
  // and reserved temp still need cooperative cancellation and cleanup.
  assert(mesh.hasPendingWork() && !mesh.canRecoverUsbLogging());
  drain(store,fs);
  assert(!mesh.hasPendingWork() && mesh.canRecoverUsbLogging());
}

int main() {
  rxRouteAndScheduling();coalesceOverflowAndDeleteEveryStage();crashBoundaries();
  synchronousMutationAndRollback();failuresAndLifecycle();pendingWorkAndRecovery();
  puts("PASS: actual ESP queue/route/scheduler, latest RAM, crash/rollback, faults and lifecycle");
}
