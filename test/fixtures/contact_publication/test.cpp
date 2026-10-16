#include "FakeFilesystem.h"
#define FILESYSTEM FakeFilesystem
#define private public
#include "transaction_under_test.h"
#undef private
#include <cstdio>
using Writer=mesh::ContactFileTransaction;
using Progress=Writer::CommitProgress;
using Outcome=Writer::Outcome;
using Bytes=std::vector<uint8_t>;
#if defined(ESP32_PLATFORM)
#define DEFER_BEGIN ,true
#else
#define DEFER_BEGIN
#endif
Bytes image(size_t n,uint8_t salt=0){Bytes out(n);for(size_t i=0;i<n;++i)out[i]=uint8_t(i*29+i/3+salt);return out;}
bool presence(FakeFilesystem* fs,const char* name,bool& found){++fs->metadata_probes;if(fs->metadata_error||fs->metadata_probes==fs->fail_metadata_probe)return false;found=fs->exists(name);return true;}
void begin(Writer& w,bool coalesce=false){
#if defined(ESP32_PLATFORM)
 for(unsigned i=0;!w&&i<10;++i){const auto before=SPIFFS.startupOperations();assert(w.serviceBegin(coalesce)!=Writer::BeginProgress::Failed);assert(SPIFFS.startupOperations()-before<=1);}
#else
 (void)coalesce;
#endif
 assert(w);
}
void write(Writer& w,const Bytes& bytes){for(size_t i=0;i<bytes.size();i+=152){size_t n=std::min(size_t(152),bytes.size()-i);assert(w.write(bytes.data()+i,n)==n);}}
unsigned finish(Writer& w,bool defer,unsigned budget=8){Progress result=Progress::Pending;unsigned calls=0;while(result==Progress::Pending){assert(++calls<2000);result=w.serviceCommit(true,budget,defer);}assert(result==Progress::Succeeded);return calls;}
void reset(const Bytes& old){SPIFFS=FakeFilesystem();SPIFFS.emulate_stdio=true;SPIFFS.files["/contacts3"]=old;}
void contracts(){
 const auto old=image(304,1),next=image(608,2);
 for(bool deferred:{false,true}) for(bool coalesce:{false,true}) {
  reset(old);{
   Writer w(&SPIFFS,"/contacts3",presence DEFER_BEGIN);begin(w,coalesce);write(w,next);
   const unsigned removes=SPIFFS.removes;
   finish(w,deferred);
   assert(SPIFFS.files.at("/contacts3")==next);
#if defined(ESP32_PLATFORM)
   assert(SPIFFS.exists("/contacts3.bak")==deferred);
   assert(SPIFFS.removes-removes==unsigned(!deferred));
#else
   assert(!SPIFFS.exists("/contacts3.bak"));assert(SPIFFS.removes-removes==1);
#endif
  }
#if defined(ESP32_PLATFORM)
  if(deferred)assert(SPIFFS.files.at("/contacts3.bak")==old);
#endif
  assert(!SPIFFS.exists("/contacts3.tmp"));
 }
 // Default synchronous failures retain the pre-existing prior-generation backup.
#if defined(ESP32_PLATFORM)
 reset(old);const auto older=image(152,9);SPIFFS.files["/contacts3.bak"]=older;SPIFFS.fail_buffer_config=1;
 {Writer w(&SPIFFS,"/contacts3",presence);assert(!w);}
 assert(SPIFFS.files.at("/contacts3")==old&&SPIFFS.files.at("/contacts3.bak")==older);
 // RecoverFALSE skips retirement even when cleanupTRUE at Publish.
 reset(old);SPIFFS.files["/contacts3.bak"]=older;
 {Writer w(&SPIFFS,"/contacts3",presence,true);begin(w,false);assert(SPIFFS.files.at("/contacts3.bak")==older);write(w,next);finish(w,true);}
 assert(SPIFFS.files.at("/contacts3.bak")==old);
 // RecoverTRUE latches the retirement stage; ConfigureFALSE retains writer251.
 reset(old);SPIFFS.files["/contacts3.bak"]=older;
 {Writer w(&SPIFFS,"/contacts3",presence,true);
  for(unsigned i=0;i<3;++i)assert(w.serviceBegin(true)==Writer::BeginProgress::Pending);
  assert(w.serviceBegin(false)==Writer::BeginProgress::Pending);assert(!SPIFFS.exists("/contacts3.bak"));
  begin(w,false);write(w,next);finish(w,true);assert(SPIFFS.largest_backend_write==251);}
#endif
 // Default synchronous commit still cleans the old backup.
 reset(old);{Writer w(&SPIFFS,"/contacts3",presence);assert(w);write(w,next);assert(w.commit());}
 assert(!SPIFFS.exists("/contacts3.bak")&&SPIFFS.files.at("/contacts3")==next);
}
void retirement(){
#if defined(ESP32_PLATFORM)
 const auto old=image(304,1),older=image(152,2);reset(old);SPIFFS.files["/contacts3.bak"]=older;
 {
  Writer w(&SPIFFS,"/contacts3",presence,true);
  for(unsigned i=0;i<3;++i)assert(w.serviceBegin(true)==Writer::BeginProgress::Pending);
  assert(SPIFFS.files.at("/contacts3")==old&&SPIFFS.files.at("/contacts3.bak")==older);
  const auto removes=SPIFFS.removes,probes=SPIFFS.metadata_probes,opens=SPIFFS.opens;
  assert(w.serviceBegin(true)==Writer::BeginProgress::Pending);
  assert(SPIFFS.removes-removes==1&&SPIFFS.metadata_probes==probes&&SPIFFS.opens==opens);
  assert(!SPIFFS.exists("/contacts3.bak")&&SPIFFS.files.at("/contacts3")==old);
 }
 // Missing target recovers backup, updates flags and performs no removal.
 SPIFFS=FakeFilesystem();SPIFFS.files["/contacts3.bak"]=old;
 {
  Writer w(&SPIFFS,"/contacts3",presence,true);
  for(unsigned i=0;i<5;++i){const auto before=SPIFFS.startupOperations();assert(w.serviceBegin(true)==Writer::BeginProgress::Pending);assert(SPIFFS.startupOperations()-before<=1);}
  assert(w._begin_target_exists&&!w._begin_backup_exists);
  assert(SPIFFS.files.at("/contacts3")==old&&!SPIFFS.exists("/contacts3.bak"));
  const auto removes=SPIFFS.removes;
  assert(w.serviceBegin(true)==Writer::BeginProgress::Pending);
  assert(SPIFFS.removes==removes&&SPIFFS.missing_remove_logs==0);
 }
 // Failed retirement stops before new/temp ownership; prior temp is untouched.
 reset(old);SPIFFS.files["/contacts3.bak"]=older;SPIFFS.files["/contacts3.tmp"]={9,8,7};SPIFFS.fail_remove=true;
 {
  Writer w(&SPIFFS,"/contacts3",presence,true);
  for(unsigned i=0;i<3;++i)assert(w.serviceBegin(true)==Writer::BeginProgress::Pending);
  assert(w.serviceBegin(true)==Writer::BeginProgress::Failed&&!w&&!w._owns_temp);
  assert(!w.commit()&&w.write(old.data(),old.size())==0);
  assert(SPIFFS.opens==0&&SPIFFS.writes==0);
 }
 assert(SPIFFS.files.at("/contacts3")==old&&SPIFFS.files.at("/contacts3.bak")==older);
 assert(SPIFFS.files.at("/contacts3.tmp")==Bytes({9,8,7}));
#endif
}
void repeatedAndCancel(){
#if defined(ESP32_PLATFORM)
 const auto old=image(152,1),next=image(608,2),last=image(760,3);reset(old);
 {Writer w(&SPIFFS,"/contacts3",presence,true);begin(w,true);write(w,next);finish(w,true);}
 assert(SPIFFS.files.at("/contacts3.bak")==old);
 for(unsigned completed=0;completed<=8;++completed) {
  reset(next);SPIFFS.files["/contacts3.bak"]=old;
  {
   Writer w(&SPIFFS,"/contacts3",presence,true);
   for(unsigned i=0;i<completed;++i){const auto n=SPIFFS.startupOperations();assert(w.serviceBegin(true)!=Writer::BeginProgress::Failed);assert(SPIFFS.startupOperations()-n<=1);}
   if(w)write(w,last);
   assert(SPIFFS.files.at("/contacts3")==next);
  }
  assert(SPIFFS.files.at("/contacts3")==next);
  assert(SPIFFS.exists("/contacts3.bak")==bool(completed<4));
  FakeFilesystem boot;boot.files=SPIFFS.files;assert(Writer::recover(&boot,"/contacts3",presence));assert(boot.files.at("/contacts3")==next);
 }
 reset(next);SPIFFS.files["/contacts3.bak"]=old;
 {Writer w(&SPIFFS,"/contacts3",presence,true);begin(w,true);write(w,last);finish(w,true);}
 assert(SPIFFS.files.at("/contacts3")==last&&SPIFFS.files.at("/contacts3.bak")==next);
#endif
}
void faultsAndCrash(){
 const auto old=image(304,1),next=image(608,2);
 for(bool deferred:{false,true})for(unsigned fault:{0U,1U,2U}) {
  reset(old);
  {
   Writer w(&SPIFFS,"/contacts3",presence DEFER_BEGIN);begin(w,true);write(w,next);
   if(fault)SPIFFS.fail_rename=fault;
   Progress result=Progress::Pending;unsigned n=0;
   while(result==Progress::Pending){assert(++n<2000);result=w.serviceCommit(true,8,deferred);}
   assert(result==(fault?Progress::Failed:Progress::Succeeded));
  }
  assert(SPIFFS.files.at("/contacts3")== (fault?old:next));
  const auto snapshots=SPIFFS.rename_snapshots;
  for(const auto& snapshot:snapshots){FakeFilesystem boot;boot.files=snapshot;assert(Writer::recover(&boot,"/contacts3",presence));const auto& bytes=boot.files.at("/contacts3");assert(bytes==old||bytes==next);}
 }
}
void counts(){
#if defined(ESP32_PLATFORM)
 for(bool deferred:{false,true})for(bool coalesce:{false,true}) {
  reset(image(152,9));const auto next=image(347*152,3);
  Writer w(&SPIFFS,"/contacts3",presence,true);begin(w,coalesce);write(w,next);
  assert(finish(w,deferred)==108);assert(SPIFFS.files.at("/contacts3")==next);
  assert(SPIFFS.backend_writes==(coalesce?106U:211U));
  assert(SPIFFS.largest_backend_write==(coalesce?502U:251U));
  assert(SPIFFS.reads==825&&SPIFFS.backend_reads==104);
 }
 reset(image(152,9));Writer w(&SPIFFS,"/contacts3",presence);assert(w);write(w,image(347*152));
 assert(finish(w,false,1)==829&&SPIFFS.backend_writes==211&&SPIFFS.backend_reads==413);
#endif
}
void ready(Writer& writer,const Bytes& old,bool present=true){
 unsigned passes=0;
 while(!writer.readyToPublish()){
  const auto reads=SPIFFS.reads;
  assert(writer.serviceCommit(true,8,true)==Progress::Pending);
  assert(++passes<100&&SPIFFS.renames==0);
#if defined(ESP32_PLATFORM)
  assert(SPIFFS.reads-reads<=8);
  assert(SPIFFS.largest_backend_read<=512);
#else
  assert(SPIFFS.reads-reads<=1);
#endif
  assert(SPIFFS.largest_read<=64);
  assert(SPIFFS.exists("/contacts3")==present);
  if(present)assert(SPIFFS.files.at("/contacts3")==old);
 }
}
void terminal(Writer& writer,Outcome expected){
 assert(writer.outcome()==expected&&!writer.readyToPublish());
 const auto files=SPIFFS.files;
 const auto operations=SPIFFS.startupOperations(),reads=SPIFFS.reads,writes=SPIFFS.writes;
 const uint8_t byte=7;
 assert(!writer.commit()&&writer.serviceCommit()==Progress::Failed);
 assert(writer.write(&byte,1)==0&&writer.outcome()==expected);
 assert(SPIFFS.files==files&&SPIFFS.startupOperations()==operations
        &&SPIFFS.reads==reads&&SPIFFS.writes==writes);
}
void crashSnapshots(const Bytes& old,const Bytes& next){
 for(const auto& snapshot:SPIFFS.rename_snapshots){
  FakeFilesystem boot;boot.files=snapshot;
  assert(Writer::recover(&boot,"/contacts3",presence));
  const auto& bytes=boot.files.at("/contacts3");assert(bytes==old||bytes==next);
 }
}
void outcomes(){
 const auto old=image(304,1),next=image(608,2);
 for(bool deferred:{false,true})for(unsigned fault=0;fault<7;++fault){
  reset(old);FakeFilesystem::Files final;
  const bool committed=fault==1||fault==3;
  const bool uncertain=fault==4||fault==6;
  const Outcome expected=committed?Outcome::Committed:uncertain?Outcome::Indeterminate:Outcome::NotCommitted;
  {
   Writer writer(&SPIFFS,"/contacts3",presence DEFER_BEGIN);begin(writer);write(writer,next);ready(writer,old);
   if(fault==0)SPIFFS.false_success_rename=1; // Backup rename acknowledged without moving.
   if(fault==1)SPIFFS.late_rename_error=1;
   if(fault==2)SPIFFS.false_success_rename=2; // Publication did not happen; restore old.
   if(fault==3)SPIFFS.late_rename_error=2; // Candidate is public; never replace it with backup.
   if(fault==4||fault==5){SPIFFS.fail_rename=2;
    if(fault==4)SPIFFS.false_success_rename=3;else SPIFFS.late_rename_error=3;}
   if(fault==6){SPIFFS.false_success_rename=2;SPIFFS.fail_rename=3;}
   const auto reads=SPIFFS.reads;
   assert(writer.serviceCommit(true,8,deferred)==(committed?Progress::Succeeded:Progress::Failed));
   assert(SPIFFS.reads==reads); // No payload reads or yield inside publication.
   terminal(writer,expected);
   assert(SPIFFS.exists("/contacts3")==!uncertain);
   if(!uncertain)assert(SPIFFS.files.at("/contacts3")== (committed?next:old));
   if(uncertain){assert(SPIFFS.files.at("/contacts3.bak")==old);assert(SPIFFS.files.at("/contacts3.tmp")==next);}
   else {
    assert(!SPIFFS.exists("/contacts3.tmp"));
#if defined(ESP32_PLATFORM)
    assert(SPIFFS.exists("/contacts3.bak")== (committed&&deferred));
#else
    assert(!SPIFFS.exists("/contacts3.bak"));
#endif
   }
   if(fault==3)assert(SPIFFS.renames==2);
   final=SPIFFS.files;
  }
  assert(SPIFFS.files==final);crashSnapshots(old,next);
 }
}
void metadata(){
 const auto old=image(304,1),next=image(608,2);
 for(bool deferred:{false,true})for(unsigned moved=1;moved<=3;++moved)for(unsigned probe=1;probe<=2;++probe){
  reset(old);FakeFilesystem::Files final;
  {
   Writer writer(&SPIFFS,"/contacts3",presence DEFER_BEGIN);begin(writer);write(writer,next);ready(writer,old);
   if(moved==3)SPIFFS.fail_rename=2; // Force the rollback branch.
   SPIFFS.after_rename=[=](FakeFilesystem& fs,const char*){
    if(fs.renames==moved)fs.fail_metadata_probe=fs.metadata_probes+probe;
   };
   const auto reads=SPIFFS.reads;
   assert(writer.serviceCommit(true,8,deferred)==Progress::Failed&&SPIFFS.reads==reads);
   assert(SPIFFS.metadata_probes>=SPIFFS.fail_metadata_probe);
   terminal(writer,Outcome::Indeterminate);
   assert(SPIFFS.renames==moved); // Unknown publication cannot blindly enter rollback.
   assert(SPIFFS.exists("/contacts3")== (moved!=1));
   if(moved!=1)assert(SPIFFS.files.at("/contacts3")== (moved==2?next:old));
   assert(SPIFFS.exists("/contacts3.bak")== (moved!=3));
   assert(SPIFFS.exists("/contacts3.tmp")== (moved!=2));
   final=SPIFFS.files;
  }
  assert(SPIFFS.files==final);crashSnapshots(old,next);
 }
 // Probe failures before any move remain ordinary safe failures.
 for(unsigned probe=1;probe<=2;++probe){
  reset(old);Writer writer(&SPIFFS,"/contacts3",presence DEFER_BEGIN);begin(writer);write(writer,next);ready(writer,old);
  SPIFFS.fail_metadata_probe=SPIFFS.metadata_probes+probe;
  assert(writer.serviceCommit()==Progress::Failed);terminal(writer,Outcome::NotCommitted);
  assert(SPIFFS.renames==0&&SPIFFS.files.at("/contacts3")==old&&!SPIFFS.exists("/contacts3.tmp"));
 }
}
void fresh(){
 const auto next=image(608,2);
 for(unsigned fault=0;fault<5;++fault){
  SPIFFS=FakeFilesystem();FakeFilesystem::Files final;
  {
   Writer writer(&SPIFFS,"/contacts3",presence DEFER_BEGIN);begin(writer);write(writer,next);ready(writer,{},false);
   if(fault==1)SPIFFS.false_success_rename=1;
   if(fault==2)SPIFFS.late_rename_error=1;
   if(fault==3)SPIFFS.after_rename=[](FakeFilesystem& fs,const char*){fs.fail_metadata_probe=fs.metadata_probes+1;};
   if(fault==4)SPIFFS.fail_rename=1;
   const Outcome expected=fault==3?Outcome::Indeterminate:
       fault==1||fault==4?Outcome::NotCommitted:Outcome::Committed;
   const auto reads=SPIFFS.reads;
   assert(writer.serviceCommit(true,8,true)==(expected==Outcome::Committed?Progress::Succeeded:Progress::Failed));
   assert(SPIFFS.reads==reads);terminal(writer,expected);
   assert(SPIFFS.exists("/contacts3")== (expected!=Outcome::NotCommitted));
   if(SPIFFS.exists("/contacts3"))assert(SPIFFS.files.at("/contacts3")==next);
   assert(!SPIFFS.exists("/contacts3.bak")&&!SPIFFS.exists("/contacts3.tmp"));
   final=SPIFFS.files;
  }
  assert(SPIFFS.files==final);
 }
}
void ambiguous(){
 const auto old=image(304,1),next=image(608,2);
 for(bool deferred:{false,true})for(unsigned moved=1;moved<=2;++moved)for(bool duplicate:{false,true}){
  reset(old);FakeFilesystem::Files final;
  {
   Writer writer(&SPIFFS,"/contacts3",presence DEFER_BEGIN);begin(writer);write(writer,next);ready(writer,old);
   SPIFFS.after_rename=[=](FakeFilesystem& fs,const char*){
    if(fs.renames!=moved)return;
    if(moved==1){
     if(duplicate)fs.files["/contacts3"]=old;else fs.files.erase("/contacts3.bak");
    }else{
     if(duplicate)fs.files["/contacts3.tmp"]=next;else fs.files.erase("/contacts3");
    }
   };
   const auto reads=SPIFFS.reads,removes=SPIFFS.removes;
   assert(writer.serviceCommit(true,8,deferred)==Progress::Failed);
   terminal(writer,Outcome::Indeterminate);
   assert(SPIFFS.renames==moved&&SPIFFS.reads==reads&&SPIFFS.removes==removes);
   assert(SPIFFS.exists("/contacts3")==duplicate);
   if(duplicate)assert(SPIFFS.files.at("/contacts3")== (moved==1?old:next));
   assert(SPIFFS.exists("/contacts3.bak")== (moved==2||duplicate));
   if(SPIFFS.exists("/contacts3.bak"))assert(SPIFFS.files.at("/contacts3.bak")==old);
   assert(SPIFFS.exists("/contacts3.tmp")== (moved==1||duplicate));
   if(SPIFFS.exists("/contacts3.tmp"))assert(SPIFFS.files.at("/contacts3.tmp")==next);
   final=SPIFFS.files;
  }
  assert(SPIFFS.files==final);
 }
}
void recovery(){
 const auto old=image(304,1),next=image(608,2);
 for(unsigned fault=0;fault<8;++fault){
  FakeFilesystem fs;fs.files["/contacts3.bak"]=old;fs.files["/contacts3.tmp"]=next;
  if(fault==1)fs.false_success_rename=1;
  if(fault==2)fs.late_rename_error=1;
  if(fault==3)fs.fail_rename=1;
  if(fault>=4)fs.fail_metadata_probe=fault-3; // Before rename and each confirmation probe.
  assert(Writer::recover(&fs,"/contacts3",presence)==(fault==0||fault==2));
  const bool moved=fault==0||fault==2||fault>=6;
  assert(fs.exists("/contacts3")==moved&&fs.exists("/contacts3.bak")==!moved);
  if(moved)assert(fs.files.at("/contacts3")==old);
  assert(fs.files.at("/contacts3.tmp")==next);
  fs.false_success_rename=fs.late_rename_error=fs.fail_rename=fs.fail_metadata_probe=0;
  assert(Writer::recover(&fs,"/contacts3",presence)&&fs.files.at("/contacts3")==old);
 }
 // A present candidate wins over the retained old backup; scratch is never promoted.
 {FakeFilesystem fs;fs.files["/contacts3"]=next;fs.files["/contacts3.bak"]=old;
  assert(Writer::recover(&fs,"/contacts3",presence)&&fs.renames==0&&fs.files.at("/contacts3")==next&&fs.files.at("/contacts3.bak")==old);}
#if defined(ESP32_PLATFORM)
 for(bool background:{false,true})for(unsigned fault=0;fault<6;++fault){
  SPIFFS=FakeFilesystem();SPIFFS.files["/contacts3.bak"]=old;SPIFFS.files["/contacts3.tmp"]=next;
  FakeFilesystem::Files final;
  {
   Writer writer(&SPIFFS,"/contacts3",presence,true);
   if(fault==1)SPIFFS.false_success_rename=1;
   if(fault==2)SPIFFS.late_rename_error=1;
   if(fault==3)SPIFFS.fail_rename=1;
   if(fault>=4)SPIFFS.after_rename=[=](FakeFilesystem& fs,const char*){fs.fail_metadata_probe=fs.metadata_probes+fault-3;};
   auto result=Writer::BeginProgress::Pending;unsigned passes=0;
   while(result==Writer::BeginProgress::Pending){
    const auto before=SPIFFS.startupOperations();result=writer.serviceBegin(background);
    assert(++passes<=10&&SPIFFS.startupOperations()-before<=1);
   }
   const bool recovered=fault==0||fault==2;
   assert(result==(recovered?Writer::BeginProgress::Ready:Writer::BeginProgress::Failed));
   if(recovered){assert(writer&&passes==unsigned(background?10:9));assert(writer.outcome()==Outcome::Pending);}
   else {assert(!writer&&!writer._owns_temp);terminal(writer,Outcome::Indeterminate);
    assert(SPIFFS.files.at("/contacts3.tmp")==next);}
   final=SPIFFS.files;
  }
  if(fault==0||fault==2){final.erase("/contacts3.tmp");assert(SPIFFS.files==final&&SPIFFS.files.at("/contacts3")==old);}
  else assert(SPIFFS.files==final);
 }
 // Cancellation at every recovery/setup boundary retains old authority and
 // never deletes prior scratch until its explicit Remove stage.
 for(unsigned completed=0;completed<=10;++completed){
  SPIFFS=FakeFilesystem();SPIFFS.files["/contacts3.bak"]=old;SPIFFS.files["/contacts3.tmp"]=next;
  {Writer writer(&SPIFFS,"/contacts3",presence,true);
   for(unsigned i=0;i<completed;++i){const auto before=SPIFFS.startupOperations();assert(writer.serviceBegin(true)!=Writer::BeginProgress::Failed);assert(SPIFFS.startupOperations()-before<=1);}}
  assert(SPIFFS.exists("/contacts3.tmp")== (completed<8));
  if(completed<8)assert(SPIFFS.files.at("/contacts3.tmp")==next);
  assert(Writer::recover(&SPIFFS,"/contacts3",presence)&&SPIFFS.files.at("/contacts3")==old);
 }
#else
 // Constructor failure occurs before ownership of the prior scratch. Neither
 // a no-op recovery nor failed confirmation may delete that other attempt.
 for(unsigned fault=0;fault<6;++fault){
  SPIFFS=FakeFilesystem();SPIFFS.files["/contacts3.bak"]=old;SPIFFS.files["/contacts3.tmp"]=next;
  if(fault==0)SPIFFS.false_success_rename=1;
  if(fault==1)SPIFFS.fail_rename=1;
  if(fault>=2)SPIFFS.fail_metadata_probe=fault-1;
  FakeFilesystem::Files final;
  {Writer writer(&SPIFFS,"/contacts3",presence);assert(!writer);terminal(writer,Outcome::Indeterminate);
   assert(SPIFFS.files.at("/contacts3.tmp")==next);final=SPIFFS.files;}
  assert(SPIFFS.files==final);
 }
#endif
}
int main(int argc,char**argv){const std::string mode=argc>1?argv[1]:"all";if(mode=="contracts")contracts();else if(mode=="retire")retirement();else if(mode=="cancel")repeatedAndCancel();else if(mode=="faults")faultsAndCrash();else if(mode=="counts")counts();else if(mode=="outcomes")outcomes();else if(mode=="metadata")metadata();else if(mode=="fresh")fresh();else if(mode=="ambiguous")ambiguous();else if(mode=="recovery")recovery();else assert(false);}
