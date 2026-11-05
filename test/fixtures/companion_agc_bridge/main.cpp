#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <string>
#include <vector>
#include <helpers/AGCMaintenanceCLI.h>
#include <helpers/radiolib/WarmRxRefresh.h>
#include <helpers/radiolib/AGCTimingDiagnostics.h>
#include "examples/companion_radio/NodePrefs.h"

struct Capture : Stream {
  std::string text;
  size_t write(uint8_t value) override { text+=char(value); return 1; }
  size_t signedNumber(long long value) { char data[32];snprintf(data,sizeof(data),"%lld",value);return Print::print(data); }
  size_t unsignedNumber(unsigned long long value) { char data[32];snprintf(data,sizeof(data),"%llu",value);return Print::print(data); }
  size_t print(unsigned char value,int) override {return unsignedNumber(value);}
  size_t print(int value,int) override {return signedNumber(value);}
  size_t print(unsigned value,int) override {return unsignedNumber(value);}
  size_t print(long value,int) override {return signedNumber(value);}
  size_t print(unsigned long value,int) override {return unsignedNumber(value);}
  size_t print(long long value,int) override {return signedNumber(value);}
  size_t print(unsigned long long value,int) override {return unsignedNumber(value);}
  size_t print(double value,int precision) override {char data[96];snprintf(data,sizeof(data),"%.*f",precision,value);return Print::print(data);}
};

struct Radio : mesh::Radio {
  bool supported=true, accept=true, diagnostic_supported=true;
  uint32_t old_seconds=0;
  unsigned sets=0;
  mutable unsigned warm_calls=0, old_calls=0;
  mutable uint32_t last_primary=0;
  mutable uint8_t last_page=255;
  WarmRxRefresh warm;
  AGCTimingDiagnostics legacy;
  bool supportsWarmRxRefresh() const override { return supported; }
  uint32_t getOldAGCInterval() const override { return old_seconds; }
  bool setOldAGCInterval(uint32_t seconds) override {
    ++sets;if(!accept)return false;old_seconds=seconds;return true;
  }
  bool formatWarmRxRefresh(uint8_t page,uint32_t primary,char* reply,size_t capacity) const override {
    ++warm_calls;if(!supported)return false;
    last_primary=primary;last_page=page;warm.format(page,primary,reply,capacity);return true;
  }
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
  bool formatAGCTimingDiagnostics(uint8_t page,char* reply,size_t capacity) const override {
    ++old_calls;if(!diagnostic_supported)return false;
    last_page=page;legacy.format(page,reply,capacity);return true;
  }
#endif
};

@ALLOWLIST@

struct MyMesh {
  CompanionNodePrefs _prefs;
  Radio radio;
  mesh::Radio* _radio=&radio;
  unsigned saves=0;
  bool save_ok=true;
  uint8_t stored=0;
  MyMesh() {_prefs.clearDirty();}
  bool savePrefs() {
    ++saves;if(!save_ok)return false;
    stored=_prefs.agc_reset_interval;_prefs.clearDirty();return true;
  }
  bool handleCommand(const char* command,uint32_t sender_timestamp,char* reply) {
    if(command==nullptr || reply==nullptr)return false;
    size_t reply_capacity=160;
    while(*command==' ' || *command=='\t')++command;
    @PREFIX@
    @MAINTENANCE@
    @TRANSACTION@
    return false;
  }
  std::string image() {Capture output;assert(_prefs.getRadioPrefs()->saveSerial(output));return output.text;}
  std::string command(const char* text,uint32_t sender=0) {
    unsigned char guarded[176];memset(guarded,0xA7,sizeof(guarded));
    char* reply=reinterpret_cast<char*>(guarded+8);strcpy(reply,"unchanged");
    assert(handleCommand(text,sender,reply));
    for(size_t n=0;n<8;++n)assert(guarded[n]==0xA7);
    for(size_t n=168;n<176;++n)assert(guarded[n]==0xA7);
    assert(memchr(reply,0,160));return reply;
  }
};

static void aliases() {
  // Exercise the real shared parser with the real Companion adapter. Its
  // canonical 0..255 SECOND acceptance and four-second storage are unchanged.
  for(uint32_t sender:{0U,1234U}) {
    MyMesh mesh;
    for(unsigned seconds=0;seconds<=255;++seconds) {
      for(const char* key:{"agc","agc.reset.interval"}) {
        char text[80];snprintf(text,sizeof(text),"set %s %u",key,seconds);
        const unsigned before=mesh.saves;
        assert(mesh.command(text,sender).find("OK - interval rounded to ")==0);
        assert(mesh._prefs.agc_reset_interval==seconds/4 && mesh.stored==seconds/4);
        assert(mesh.saves==before+1 && !mesh._prefs.isDirty());
        const std::string expected="> "+std::to_string((seconds/4)*4);
        assert(mesh.command("get agc",sender)==expected);
        assert(mesh.command("get agc.reset.interval",sender)==expected && mesh.saves==before+1);
      }
    }
    // Loaded stored bytes retain their historical meaning even above the
    // canonical setter range; an alias cannot reinterpret/mask saved values.
    for(uint8_t loaded:{uint8_t(0),uint8_t(28),uint8_t(255)}) {
      mesh._prefs.agc_reset_interval=loaded;
      assert(mesh.command("get agc")=="> "+std::to_string(unsigned(loaded)*4));
      assert(mesh.command("get agc.reset.interval")=="> "+std::to_string(unsigned(loaded)*4));
    }
    mesh._prefs.agc_reset_interval=7;
    assert(mesh.command("AA|get agc",sender)=="AA|> 28");
    assert(mesh.command("AA|set agc 19",sender)=="AA|OK - interval rounded to 16");
    assert(mesh._prefs.agc_reset_interval==4);
  }
}

static void invalidAndTransactions() {
  for(const char* key:{"agc","agc.reset.interval"}) {
    MyMesh mesh;mesh._prefs.agc_reset_interval=7;mesh.stored=7;
    const std::string image=mesh.image();
    for(const char* value:{"", "-1","256","1020","4294967296","2147483648","999999999999999999999999","1x","1 2","1.0","1e2","+1"," 1","1 ","1\n","1\r","0x10"}) {
      char text[160];snprintf(text,sizeof(text),"set %s %s",key,value);
      assert(mesh.command(text).find("Error")==0);
      assert(mesh._prefs.agc_reset_interval==7 && mesh.stored==7 && mesh.saves==0 && !mesh._prefs.isDirty());
      assert(mesh.image()==image);
    }
    mesh.save_ok=false;
    char text[80];snprintf(text,sizeof(text),"set %s 60",key);
    assert(mesh.command(text)=="Error: setting changed but save failed");
    assert(mesh._prefs.agc_reset_interval==7 && mesh.stored==7 && mesh.saves==1 && !mesh._prefs.isDirty());
    assert(mesh.image()==image);
    mesh.save_ok=true;assert(mesh.command(text)=="OK - interval rounded to 60");
    assert(mesh._prefs.agc_reset_interval==15 && mesh.stored==15 && mesh.saves==2 && !mesh._prefs.isDirty());
  }
  CompanionNodePrefs prefs;auto* shared=prefs.getRadioPrefs();char reply[160];
  assert(shared->handleCommand("set agc 19",0,reply,false));assert(prefs.agc_reset_interval==4 && shared->isDirty());
  shared->clearDirty();assert(shared->handleCommand("get agc",0,reply,false));assert(!strcmp(reply,"> 16") && !shared->isDirty());
  shared->markDirty();assert(shared->handleCommand("set agc invalid",0,reply,false));assert(shared->isDirty() && prefs.agc_reset_interval==4);
}

static void runtimeOld() {
  MyMesh mesh;mesh._prefs.agc_reset_interval=7;
  const std::string before=mesh.image();
  assert(mesh.command("get agc.old")=="> 0");
  for(uint32_t seconds:{0U,1U,600U,86400U}) {
    char command[80];snprintf(command,sizeof(command),"set agc.old %lu",(unsigned long)seconds);
    const std::string reply=mesh.command(command);
    assert(reply.find("OK - old full-reset interval ")==0);
    assert(reply.find("disabled after reboot")!=std::string::npos);
    assert(mesh.command("get agc.old")=="> "+std::to_string(seconds));
    assert(mesh.radio.old_seconds==seconds && mesh.image()==before && mesh.saves==0 && !mesh._prefs.isDirty());
  }
  const unsigned calls=mesh.radio.sets;
  for(const char* value:{"", "-1","-0","86401","4294967295","4294967296","999999999999999999999999","1x","1 2","1.5","1e2","on","off","1\n","1\r","0x10"}) {
    char command[160];snprintf(command,sizeof(command),"set agc.old %s",value);
    assert(mesh.command(command).find("Error:")==0);
    assert(mesh.radio.old_seconds==86400 && mesh.radio.sets==calls && mesh.saves==0 && mesh.image()==before);
  }
  mesh.radio.accept=false;assert(mesh.command("set agc.old 600")=="Error: failed to set old AGC interval");
  assert(mesh.radio.old_seconds==86400 && mesh.radio.sets==calls+1 && mesh.saves==0 && mesh.image()==before);
  mesh.radio.supported=false;const unsigned unsupported=mesh.radio.sets;
  assert(mesh.command("set agc.old 0")=="Error: unsupported");assert(mesh.command("get agc.old")=="Error: unsupported");
  assert(mesh.radio.sets==unsupported && mesh.radio.old_seconds==86400);
  mesh._radio=nullptr;assert(mesh.command("set agc.old 0")=="Error: unsupported");assert(mesh.command("get agc.status")=="Error: unsupported");
  mesh::Radio unsupported_radio;char reply[160]="unchanged";
  assert(!unsupported_radio.supportsWarmRxRefresh() && !unsupported_radio.setOldAGCInterval(600) && unsupported_radio.getOldAGCInterval()==0);
  assert(!unsupported_radio.formatWarmRxRefresh(0,28,reply,sizeof(reply)) && !strcmp(reply,"unchanged"));
  assert(mesh::handleAGCMaintenanceCommand(&unsupported_radio,"set agc.old 600",28,reply,sizeof(reply)) && !strcmp(reply,"Error: unsupported"));
}

static void diagnosticsAndBounds() {
  MyMesh mesh;mesh._prefs.agc_reset_interval=28;
  const std::string before=mesh.image();
  mesh.radio.warm.requests=mesh.radio.warm.admissions=mesh.radio.warm.deferrals=mesh.radio.warm.failures=mesh.radio.warm.successes=UINT32_MAX;
  mesh.radio.warm.rearm_failures=mesh.radio.warm.request_us=UINT32_MAX;
  mesh.radio.warm.last_error=INT16_MIN;mesh.radio.warm.pending=mesh.radio.warm.method_seen=true;
  mesh.radio.warm.reason=WarmRxRefresh::Reason::Command;mesh.radio.warm.last_failed_step=WarmRxRefresh::Step::Standby;
  mesh.radio.warm.warm_gap={UINT32_MAX,UINT32_MAX,UINT32_MAX,UINT32_MAX};
  mesh.radio.warm.recovery_gap=mesh.radio.warm.warm_gap;
  mesh.radio.legacy.periodic_requests=mesh.radio.legacy.periodic_admitted=mesh.radio.legacy.periodic_skipped=UINT32_MAX;
  mesh.radio.legacy.shared_requests=mesh.radio.legacy.shared_admitted=mesh.radio.legacy.shared_skipped=UINT32_MAX;
  mesh.radio.legacy.rearm_failures=mesh.radio.legacy.command_failures=mesh.radio.legacy.failed_sequences=UINT32_MAX;
  mesh.radio.legacy.poll_timeouts=mesh.radio.legacy.superseded_pending=UINT32_MAX;
  mesh.radio.legacy.helper_us={UINT32_MAX,UINT32_MAX,UINT32_MAX,UINT32_MAX};mesh.radio.legacy.rx_gap_us=mesh.radio.legacy.helper_us;
  mesh.radio.legacy.last_error=INT16_MIN;mesh.radio.legacy.last_error_step=AGCTimingDiagnostics::Step::Standby;
  const char* warm[]={"get agc.status","get agc.timing","get agc.recovery"};
  const char* complete[]={"err=-32768","request_us=4294967295","step=standby"};
  for(uint8_t page=0;page<3;++page) {
    const std::string reply=mesh.command(warm[page]);
    assert(mesh.radio.last_page==page && mesh.radio.last_primary==112 && reply.size()<=156 && reply.find(complete[page])!=std::string::npos);
    const std::string prefixed=mesh.command(("AA|"+std::string(warm[page])).c_str());
    assert(prefixed=="AA|"+reply && prefixed.size()<=159);
  }
  const char* names[]={"status","timing","errors"};
  const char* legacy_ends[]={"rf=4294967295","g_max=4294967295","skip=none"};
#if !defined(MESH_AGC_TIMING_DIAGNOSTICS) || !MESH_AGC_TIMING_DIAGNOSTICS
  (void)legacy_ends;
#endif
  for(uint8_t page=0;page<3;++page) {
    const std::string old="get agc.old."+std::string(names[page]);
    const std::string canonical="get agc.reset."+std::string(names[page]);
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
    assert(mesh.command(old.c_str())==mesh.command(canonical.c_str()));assert(mesh.radio.last_page==page);
    const std::string reply=mesh.command(old.c_str());
    assert(reply.size()<=156 && reply.find(legacy_ends[page])!=std::string::npos);
    assert(mesh.command(("AA|"+old).c_str())=="AA|"+reply);
#else
    assert(mesh.command(old.c_str())=="Error: unsupported" && mesh.command(canonical.c_str())=="Error: unsupported");
    assert(mesh.radio.old_calls==0);
#endif
  }
  mesh.radio.diagnostic_supported=false;
  for(const char* name:names) {
    assert(mesh.command(("get agc.old."+std::string(name)).c_str())=="Error: unsupported");
    assert(mesh.command(("get agc.reset."+std::string(name)).c_str())=="Error: unsupported");
  }
  // The shared helper must honor even smaller capacities and preserve caller
  // guard bytes. Both unsupported and formatter replies exercise this loop.
  for(size_t capacity=0;capacity<=180;++capacity) {
    for(bool supported:{false,true}) {
      mesh.radio.supported=supported;
      std::vector<unsigned char> bytes(capacity+16,0xA7);char* reply=reinterpret_cast<char*>(bytes.data()+8);
      const bool handled=mesh::handleAGCMaintenanceCommand(&mesh.radio,"get agc.timing",112,reply,capacity);
      assert(handled==(capacity!=0));if(capacity)assert(memchr(reply,0,capacity));
      for(size_t n=0;n<8;++n)assert(bytes[n]==0xA7);
      for(size_t n=capacity+8;n<bytes.size();++n)assert(bytes[n]==0xA7);
    }
  }
  char reply[160]="unchanged";
  for(const char* command:{"get agc.status junk","get agc.statusx","get agc.oldx","set agc.oldx 1","get agc.old.statusx","set agcx 1"}) {
    strcpy(reply,"unchanged");assert(!mesh.handleCommand(command,0,reply));assert(!strcmp(reply,"unchanged"));
  }
  assert(!mesh::handleAGCMaintenanceCommand(nullptr,nullptr,0,reply,sizeof(reply)));
  assert(!mesh::handleAGCMaintenanceCommand(nullptr,"get agc.old",0,nullptr,sizeof(reply)));
  assert(mesh.image()==before && mesh.saves==0 && !mesh._prefs.isDirty());
}

int main(int argc,char** argv) {
  assert(argc==2);
  if(!strcmp(argv[1],"aliases"))aliases();
  else if(!strcmp(argv[1],"transactions"))invalidAndTransactions();
  else if(!strcmp(argv[1],"runtime"))runtimeOld();
  else if(!strcmp(argv[1],"diagnostics"))diagnosticsAndBounds();
  else assert(false);
}
