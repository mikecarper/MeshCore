"""Compile and execute the production wireless command/state controller."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

POLICY_STORE_STUB = r'''
#pragma once
using FILESYSTEM = int;
namespace mesh { namespace wireless {
struct Policy { uint8_t mode=0,mask=0,primary_espnow=0; };
struct PolicyStore {
 Policy data; bool saves=true;
 bool begin(FILESYSTEM*){return true;}
 bool save(const Policy& p){if(!saves)return false;data=p;return true;}
 const Policy& value()const{return data;}
 bool healthy()const{return true;}
 bool present()const{return data.mode||data.primary_espnow;}
};
inline PolicyStore& policyStore(){static PolicyStore store;return store;}
}}
'''

def policy_store_stub(folder):
    (folder/'helpers').mkdir(exist_ok=True)
    (folder/'helpers/WirelessPolicyStore.h').write_text(POLICY_STORE_STUB)

HARNESS = r'''
#include <helpers/WirelessControl.h>
#include <cassert>
#include <vector>
using namespace mesh::wireless;
struct Fake : Backend {
 uint8_t supported=All, active=All, sessions=0, wait=0, fail=0;
 bool busy=false;
 std::vector<int> calls;
 uint8_t available()const override{return supported;}
 uint8_t enabled()const override{return active;}
 uint8_t clients()const override{return sessions;}
 bool replyBusy()const override{return busy;}
 Result set(uint8_t bit,bool on)override {
   calls.push_back(on?bit:-bit);
   if(wait==bit)return Result::Pending;
   if(fail==bit)return Result::Failed;
   if(on)active|=bit;else active&=~bit;
   return Result::Done;
 }
};
struct Fixture {
 Fake b; Control c; char reply[160]={}; uint32_t now=100;
 Fixture(){c.begin(b);}
 void run(const char* text,uint8_t requester=Independent){assert(c.handle(text,reply,sizeof(reply),now,requester));}
 void tick(unsigned ms=250){now+=ms;c.service(now);}
};
int main(int argc,char** argv) {
 const int test=argc>1?atoi(argv[1]):0;
 if(test==0){
   for(const char* text:{"set wifi","set espnow on force","set wifi off force extra",
       "set 2.4ghz on all force","set 2.4ghz on forced","get wifi junk","set 2.4ghz off all"})
     assert(parse(text).action==Action::Invalid);
   for(const char* text:{"set wifi.ssid x","get wifi.status","set espnow.channel 3","set bluetooth off","get 2.4ghzx"})
     assert(parse(text).action==Action::None);
   assert(parse(nullptr).action==Action::None);
   assert(parse(" set espnow\t off\tforce \t").action==Action::ForceOff);
   assert(parse("set 2.4ghz on all").action==Action::OnAll);
   assert(parse("set 2.4ghz on force").action==Action::OnAll);
   assert(parse("set wifi.enabled 0").action==Action::Off);
   assert(parse("set wifi.enabled on").action==Action::On);
   assert(parse("get wifi.enabled").action==Action::GetPreference);
   char unavailable[160];
   assert(handleNoServices("get wifi.enabled",unavailable,sizeof(unavailable)));
   assert(strstr(unavailable,"unavailable"));
 } else if(test==1){
   for(unsigned mask=0;mask<=All;++mask){
     Fixture f;f.b.active=mask;
     f.run("set 2.4ghz on");f.tick();assert(f.b.active==mask); // on before off is idempotent
     f.run("set 2.4ghz off");f.tick();assert(f.b.active==0);
     f.run("set 2.4ghz off");f.tick(); // never overwrite the saved state
     f.run("set wifi on");assert(strstr(f.reply,"Error:"));
     assert(!f.c.allowService(Bluetooth));
     f.run("set 2.4ghz on");f.tick();assert(f.b.active==mask);
     assert(f.c.allowService(Bluetooth));
     f.run("set 2.4ghz on all");f.tick();assert(f.b.active==All);
     f.run("set 2.4ghz off");f.tick();
     f.run("set 2.4ghz on force");f.tick();assert(f.b.active==All);
   }
 } else if(test==2){
   Fixture f;f.run("set wifi off",WiFi);assert(strstr(f.reply,"Error:"));
   f.b.sessions=Bluetooth;f.run("set wifi off",WiFi);assert(f.c.pending());
   f.tick(249);assert(f.b.active==All);f.b.sessions=0;f.tick(1);
   assert(f.b.active==All&&!f.c.pending());f.run("get wifi");assert(strstr(f.reply,"cancelled"));
   f.b.sessions=Bluetooth;f.run("set wifi off",WiFi);f.tick();assert(f.b.active==(Bluetooth|EspNow));
   f.run("set wifi on");f.tick();assert(f.b.active==All);
   f.b.sessions=Bluetooth|WiFi;f.run("set 2.4ghz off",Bluetooth);assert(strstr(f.reply,"Error:"));
   f.run("set espnow off",EspNow);f.tick();assert(f.b.active==(WiFi|Bluetooth));
 } else if(test==3){
   Fixture f;f.b.busy=true;f.run("set 2.4ghz off force",Bluetooth);
   f.tick();assert(f.b.active==All);f.tick(1749);assert(f.b.active==All);
   f.tick(1);assert(f.b.active==0);assert(f.c.blocked(All));
   assert((f.b.calls==std::vector<int>{-WiFi,-EspNow,-Bluetooth}));
   f.b.calls.clear();f.b.busy=false;f.run("set 2.4ghz on");f.tick();
   assert((f.b.calls==std::vector<int>{EspNow,WiFi,Bluetooth}));
 } else if(test==4){
   Fixture f;f.b.wait=WiFi;f.run("set 2.4ghz off");f.tick();
   assert(f.c.pending()&&f.b.calls.size()==1&&f.b.active==All);
   f.run("set espnow off");assert(strstr(f.reply,"pending"));
   f.b.wait=0;f.tick(1);assert(f.b.active==0&&!f.c.pending());
   f.b.fail=EspNow;f.run("set 2.4ghz on");f.tick();
   assert(f.b.active==0&&!f.c.pending());f.run("get 2.4ghz");assert(strstr(f.reply,"failed"));
   f.b.fail=0;f.run("set 2.4ghz on");f.tick();assert(f.b.active==All); // saved state survives failure
 } else if(test==5){
   Fixture f;f.run("set 2.4ghz off");f.tick(249);
   f.run("set 2.4ghz on");f.tick();assert(f.b.active==All&&!f.c.masterOff());
   f.b.sessions=Independent;f.run("set 2.4ghz off",Bluetooth);f.b.sessions=0;f.tick();
   assert(f.b.active==All&&!f.c.masterOff());
   f.now=UINT32_MAX-249;f.run("set 2.4ghz off");f.tick(249);assert(f.b.active==All);
   f.tick(1);assert(f.b.active==0);
 } else if(test==6){
   Fixture f;f.b.supported=Bluetooth;f.b.active=Bluetooth;
   f.run("set wifi on");assert(strstr(f.reply,"unavailable"));
   f.run("get 2.4ghz");assert(strstr(f.reply,"wifi=unavailable")&&strstr(f.reply,"bluetooth=on"));
   f.run("set 2.4ghz off");f.tick();assert(f.b.active==0);
   f.run("set 2.4ghz on all");f.tick();assert(f.b.active==Bluetooth);
   f.b.supported=0;f.run("set 2.4ghz on");assert(strstr(f.reply,"unavailable"));
 } else if(test==7){
   Fixture f;f.b.wait=WiFi;f.run("set 2.4ghz off");f.tick(30250);
   assert(!f.c.pending()&&f.b.active==All&&f.b.calls.size()==1);
   f.run("get 2.4ghz");assert(strstr(f.reply,"failed"));
   f.b.wait=0;f.run("set 2.4ghz off");f.tick();assert(f.b.active==0);
 } else if(test==8){
   Fixture f;f.b.active=WiFi|Bluetooth;f.b.sessions=WiFi|Bluetooth;
   f.run("set wifi off",Bluetooth);assert(f.c.pending());
   f.b.active=WiFi;f.b.sessions=WiFi; // the retained requester disconnects
   f.tick();assert(f.b.active==WiFi&&!f.c.pending());
   f.run("get wifi");assert(strstr(f.reply,"cancelled"));
   // Explicit force still permits shutting down the only live connection.
   f.run("set wifi off force",WiFi);f.tick();assert(f.b.active==0);
 } else if(test==9){
   Fixture f;f.b.active=WiFi;
   f.run("set 2.4ghz off");f.tick();
   f.b.fail=WiFi;f.run("set 2.4ghz on");f.tick();assert(!f.c.pending());
   f.b.fail=0;f.b.active=Bluetooth;f.b.sessions=Bluetooth;
   // The failed restore retains a WiFi-only saved mask. Enabling BLE to
   // recover access must not make the next ordinary restore cut that access.
   f.run("set 2.4ghz on",Bluetooth);
   assert(strstr(f.reply,"no remaining")&&!f.c.pending()&&f.b.active==Bluetooth);
   f.run("set 2.4ghz on all",Bluetooth);f.tick();assert(f.b.active==All);
 } else if(test==10){
   Fixture f;f.b.active=0;
   f.run("set wifi on");
#if defined(ESP_PLATFORM)
#if MESH_CLI_COMPACT_REPLIES
   assert(strcmp(f.reply,"OK - WiFi on (boot); get wifi.status")==0);
#else
   assert(strcmp(f.reply,"OK - WiFi enabled for this boot; use get wifi.status to check connection")==0);
#endif
#else
#if MESH_CLI_COMPACT_REPLIES
   assert(strcmp(f.reply,"OK - wifi on (boot); get wifi")==0);
#else
   assert(strcmp(f.reply,"OK - wifi on requested (this boot); get wifi for status")==0);
#endif
#endif
   assert(f.c.pending()&&f.b.active==0); // Enabling does not claim a connection.
   f.run("get wifi");assert(strcmp(f.reply,"wifi off (pending)")==0);
   f.tick();f.run("get wifi");assert(strcmp(f.reply,"wifi on")==0);
   f.run("set wifi off");
#if defined(ESP_PLATFORM)
#if MESH_CLI_COMPACT_REPLIES
   assert(strcmp(f.reply,"OK - WiFi off (boot)")==0);
#else
   assert(strcmp(f.reply,"OK - WiFi disabled for this boot")==0);
#endif
#else
#if MESH_CLI_COMPACT_REPLIES
   assert(strcmp(f.reply,"OK - wifi off (boot); get wifi")==0);
#else
   assert(strcmp(f.reply,"OK - wifi off requested (this boot); get wifi for status")==0);
#endif
#endif
   f.tick();assert(f.b.active==0);
   // Keep the independent ESP-NOW/master control responses unchanged.
   f.run("set espnow on");
#if MESH_CLI_COMPACT_REPLIES
   assert(strcmp(f.reply,"OK - espnow on (boot); get espnow")==0);
#else
   assert(strcmp(f.reply,"OK - espnow on requested (this boot); get espnow for status")==0);
#endif
   f.tick();f.run("set 2.4ghz off");
#if MESH_CLI_COMPACT_REPLIES
   assert(strcmp(f.reply,"OK - 2.4ghz off (boot); get 2.4ghz")==0);
#else
   assert(strcmp(f.reply,"OK - 2.4ghz off requested (this boot); get 2.4ghz for status")==0);
#endif
 }
}
'''


PERSISTENCE_HARNESS = r'''
#include <helpers/WirelessControl.h>
#include <cassert>
#include <cstdlib>
#include <vector>
using namespace mesh::wireless;
struct SavedBackend : Backend {
 bool saved=true, save_ok=true, requires_reboot=false;
 unsigned writes=0;
 uint8_t supported=All, active=WiFi|EspNow, sessions=0, failure=0;
 std::vector<int> calls;
 uint8_t available()const override{return supported;}
 uint8_t enabled()const override{return active;}
 uint8_t clients()const override{return sessions;}
 bool hasWiFiPreference()const override{return true;}
 bool wifiPreference()const override{return saved;}
 bool wifiRequiresReboot()const override{return requires_reboot;}
 bool saveWiFiPreference(bool on)override {
   ++writes;if(!save_ok)return false;saved=on;return true;
 }
 Result set(uint8_t bit,bool on)override {
   calls.push_back(on?bit:-bit);
   if(failure==bit)return Result::Failed;
   if(on)active|=bit;else active&=~bit;
   return Result::Done;
 }
};
struct SavedFixture {
 SavedBackend backend;Control control;uint32_t now=100;char reply[160]={};
 SavedFixture(){control.begin(backend);}
 void command(const char* text,uint8_t requester=Independent){
   assert(control.handle(text,reply,sizeof(reply),now,requester));
 }
 void tick(unsigned ms=250){now+=ms;control.service(now);}
};
int main(int argc,char** argv){
 const int scenario=argc>1?atoi(argv[1]):0;
 if(scenario==0){
   for(const char* off:{"set wifi off","set wifi 0","set wifi.enabled off","set wifi.enabled 0"}){
     SavedFixture f;f.command(off);assert(strncmp(f.reply,"OK",2)==0);
     assert(!f.backend.saved); // A reboot immediately after the reply retains Off.
     assert(f.backend.calls.empty()&&f.backend.active==(WiFi|EspNow));
     assert(!f.control.blocked(WiFi)); // Keep the reply transport alive until deferred teardown.
     Control reboot;reboot.begin(f.backend);
     assert(reboot.blocked(WiFi)&&!reboot.blocked(EspNow));
     assert(!reboot.allowService(WiFi));
     f.tick();assert(f.backend.active==EspNow);
   }
 }else if(scenario==1){
   for(const char* on:{"set wifi on","set wifi 1","set wifi.enabled on","set wifi.enabled 1"}){
     SavedFixture f;f.backend.saved=false;f.backend.active=EspNow;f.control.begin(f.backend);
     f.command(on);assert(strncmp(f.reply,"OK",2)==0&&f.backend.saved);
     f.tick();assert(f.backend.active==(WiFi|EspNow));
     Control reboot;reboot.begin(f.backend);assert(!reboot.blocked(WiFi));
   }
 }else if(scenario==2){
   SavedFixture f;f.backend.saved=false;f.backend.active=EspNow;f.control.begin(f.backend);
   f.command("get wifi.enabled");assert(!strcmp(f.reply,"> 0"));
   f.command("get wifi");assert(!strcmp(f.reply,"wifi off"));
   // Live state can be temporarily unavailable without rewriting saved intent.
   f.backend.saved=true;f.control.begin(f.backend);
   f.command("get wifi.enabled");assert(!strcmp(f.reply,"> 1"));
   f.command("get wifi");assert(!strcmp(f.reply,"wifi off"));
 }else if(scenario==3){
   SavedFixture f;f.backend.save_ok=false;f.command("set wifi off");
   assert(strstr(f.reply,"Error")||strstr(f.reply,"ERR"));
   assert(f.backend.saved&&!f.control.pending()&&f.backend.calls.empty());
   assert(!f.control.blocked(WiFi));f.tick();assert(f.backend.active==(WiFi|EspNow));
 }else if(scenario==4){
   SavedFixture f;f.command("set wifi.enabled 0",WiFi);
   assert(strstr(f.reply,"Error")&&f.backend.saved&&f.backend.writes==0&&!f.control.pending());
   f.backend.sessions=Bluetooth;f.command("set wifi off",WiFi);
   assert(!f.backend.saved&&f.control.pending());
   f.backend.sessions=0;f.tick();
   assert(f.backend.saved&&!f.control.pending()&&f.backend.active==(WiFi|EspNow));
   Control reboot;reboot.begin(f.backend);assert(!reboot.blocked(WiFi));
 }else if(scenario==5){
   SavedFixture f;f.command("set wifi off");assert(!f.backend.saved);
   f.tick(249);f.command("set wifi.enabled 1");
   assert(strstr(f.reply,"pending")&&!f.backend.saved); // No pending-policy overwrite.
   f.tick(1);assert(f.backend.active==EspNow);
   f.command("set wifi 1");f.tick();assert(f.backend.saved&&f.backend.active==(WiFi|EspNow));
   const auto writes=f.backend.writes;
   f.command("set 2.4ghz off");f.command("set wifi.enabled 0");
   assert(strstr(f.reply,"pending")&&f.backend.saved&&f.backend.writes==writes);
 }else if(scenario==6){
   SavedFixture f;f.command("set wifi.enabled off force",WiFi);assert(!f.backend.saved);
   f.tick();assert(f.backend.active==EspNow);
   Control reboot;reboot.begin(f.backend);assert(reboot.blocked(WiFi));
   // ESP-NOW is an independent owner of the shared 2.4 GHz hardware.
   assert(!reboot.blocked(EspNow)&&f.backend.active==EspNow);
 }else if(scenario==7){
   SavedFixture f;
   for(const char* text:{"set wifi.enabled", "set wifi.enabled 2", "get wifi.enabled extra",
                        "set wifi.enabled on force", "set wifi.enabled 0 force extra"}){
     const auto before=f.backend.writes;f.command(text);
     assert(strstr(f.reply,"Error")||strstr(f.reply,"ERR"));assert(f.backend.writes==before);
   }
   f.backend.supported=EspNow;f.control.begin(f.backend);
   f.command("set wifi.enabled off");assert(strstr(f.reply,"unavailable")&&f.backend.writes==0);
 }else if(scenario==8){
   SavedFixture f;f.command("set 2.4ghz off");f.tick();
   assert(f.backend.saved&&f.backend.writes==0);
   f.command("set 2.4ghz on");f.tick();assert(f.backend.saved&&f.backend.writes==0);
   f.command("set espnow off");f.tick();assert(f.backend.saved&&f.backend.writes==0);
 }else if(scenario==9){
   SavedFixture f;f.backend.failure=WiFi;f.command("set wifi off");
   assert(!f.backend.saved);f.tick();assert(!f.control.pending());
   f.command("get wifi");assert(strstr(f.reply,"failed"));
   // A failed teardown must not turn the saved Off policy back On at reboot.
   Control reboot;reboot.begin(f.backend);assert(reboot.blocked(WiFi));
 }else if(scenario==10){
   SavedFixture f;f.backend.requires_reboot=true;
   f.backend.supported=f.backend.active=Bluetooth;f.control.begin(f.backend);
   f.command("set wifi.enabled 0");
   assert(!f.backend.saved&&!f.control.pending()&&f.backend.calls.empty());
   assert(strstr(f.reply,"reboot")&&f.backend.active==Bluetooth);
   f.command("set wifi on");
   assert(f.backend.saved&&!f.control.pending()&&strstr(f.reply,"reboot"));
   assert(f.backend.calls.empty()&&f.backend.active==Bluetooth);
 }else if(scenario==11){
   SavedFixture f;f.backend.sessions=Bluetooth;f.command("set wifi off",WiFi);
   assert(!f.backend.saved);f.backend.save_ok=false;f.backend.sessions=0;f.tick();
   assert(!f.backend.saved&&!f.control.pending()&&f.backend.active==(WiFi|EspNow));
   f.command("get wifi");
   assert(strstr(f.reply,"saved")&&(strstr(f.reply,"failed")||strstr(f.reply,"restore")));
 }else if(scenario==12){
   for(bool ap_started:{false,true}){
     SavedFixture f;f.backend.saved=false;f.backend.active=EspNow;f.control.begin(f.backend);
     assert(f.control.blocked(WiFi)&&!f.control.allowService(WiFi));
     assert(f.control.allowWiFiForOTA()&&!f.control.blocked(WiFi));
     assert(f.control.allowService(WiFi));
     if(ap_started)f.backend.active|=WiFi;
     // Both failed setup and a normally stopped OTA session release the owner.
     f.backend.active&=~WiFi;f.control.finishWiFiForOTA();f.control.service(f.now);
     assert(f.control.pendingDisable(WiFi)&&!f.control.allowService(WiFi));
     f.tick();assert(f.control.blocked(WiFi)&&!f.control.allowService(WiFi));
     assert(!f.backend.saved&&f.backend.writes==0&&f.backend.active==EspNow);
   }
 }else if(scenario==13){
   for(const char* explicit_on:{"set wifi on","set 2.4ghz on all"}){
     SavedFixture f;f.backend.saved=false;f.backend.active=EspNow;f.control.begin(f.backend);
     assert(f.control.allowWiFiForOTA());f.command(explicit_on);f.tick();
     assert(!f.control.blocked(WiFi)&&(f.backend.active&WiFi));
     // A late OTA stop must not undo a more recent, accepted user command.
     f.control.finishWiFiForOTA();f.control.service(++f.now);
     assert(!f.control.blocked(WiFi)&&f.control.allowService(WiFi));
     assert(f.backend.active&WiFi);
     assert(f.backend.saved==(strcmp(explicit_on,"set wifi on")==0));
   }
 }else if(scenario==14){
   SavedFixture f;f.backend.saved=false;f.backend.active=EspNow;f.control.begin(f.backend);
   assert(f.control.allowWiFiForOTA());f.backend.save_ok=false;
   f.command("set wifi on");assert(strstr(f.reply,"Error")&&!f.backend.saved);
   // A rejected save grants no lasting override of the saved-Off policy.
   f.control.finishWiFiForOTA();f.control.service(++f.now);f.tick();
   assert(f.control.blocked(WiFi)&&!f.control.allowService(WiFi));
 }else if(scenario==17){
   SavedFixture f;f.backend.saved=false;f.backend.requires_reboot=true;
   f.backend.supported=f.backend.active=Bluetooth;f.control.begin(f.backend);
   assert(f.control.blocked(WiFi)&&!f.control.allowService(WiFi));
   assert(f.control.allowWiFiForOTA()&&!f.control.blocked(WiFi));
   // Exclusive BLE boots have no WiFi backend to stop after a failed borrow.
   // Cleanup must still revoke the temporary permission for future services.
   f.control.finishWiFiForOTA();f.control.service(++f.now);
   assert(!f.control.pending()&&f.control.blocked(WiFi));
   assert(!f.control.allowService(WiFi)&&f.control.blocked(WiFi));
   assert(!f.backend.saved&&f.backend.writes==0&&f.backend.calls.empty());
   assert(f.backend.active==Bluetooth);
#if MESH_ENABLE_EMERGENCY_POWER
 }else if(scenario==15){
   SavedFixture f;f.backend.saved=false;f.backend.active=EspNow;f.control.begin(f.backend);
   assert(f.control.allowWiFiForOTA());f.backend.active|=WiFi;
   assert(f.control.suspendForEmergency()&&f.backend.active==0);
   f.control.finishWiFiForOTA(); // The borrowed owner finishes while suspended.
   assert(f.control.resumeAfterEmergency());
   assert(f.backend.active==EspNow&&f.control.blocked(WiFi));
   assert(!f.control.allowService(WiFi));
   assert(!f.backend.saved&&f.backend.writes==0);
   f.control.service(++f.now);f.tick();
   assert(f.backend.active==EspNow&&f.control.blocked(WiFi)&&!f.backend.saved);
 }else if(scenario==16){
   SavedFixture f;f.backend.saved=false;f.backend.active=EspNow;f.control.begin(f.backend);
   f.command("set 2.4ghz on all");f.tick();assert(f.backend.active==All);
   assert(f.control.suspendForEmergency()&&f.backend.active==0);
   assert(f.control.resumeAfterEmergency()&&f.backend.active==All);
   assert(!f.control.blocked(WiFi)&&f.control.allowService(WiFi));
   assert(!f.backend.saved&&f.backend.writes==0);
   Control reboot;reboot.begin(f.backend);assert(reboot.blocked(WiFi));
#endif
 }
}
'''

class WirelessControlTests(unittest.TestCase):
    def test_ota_cleanup_with_wifi_unavailable_in_current_boot(self):
        with tempfile.TemporaryDirectory(prefix="wireless-persistence-no-wifi-") as tmp:
            source = Path(tmp) / 'persistence.cpp'
            source.write_text(PERSISTENCE_HARNESS)
            binary = Path(tmp) / 'persistence'
            subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', '-DESP_PLATFORM=1',
                            '-I', str(ROOT / 'src'), str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary), '17'], check=True)

    def test_saved_wifi_aliases_and_reboot_policy(self):
        with tempfile.TemporaryDirectory(prefix="wireless-persistence-") as tmp:
            source = Path(tmp) / 'persistence.cpp'
            source.write_text(PERSISTENCE_HARNESS)
            binary = Path(tmp) / 'persistence'
            subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', '-DESP_PLATFORM=1',
                            '-I', str(ROOT / 'src'), str(source), '-o', str(binary)], check=True)
            for scenario in range(15):
                with self.subTest(scenario=scenario):
                    subprocess.run([str(binary), str(scenario)], check=True)

    def test_saved_wifi_policy_across_emergency_sleep(self):
        with tempfile.TemporaryDirectory(prefix="wireless-persistence-emergency-") as tmp:
            source = Path(tmp) / 'persistence.cpp'
            source.write_text(PERSISTENCE_HARNESS)
            binary = Path(tmp) / 'persistence'
            subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', '-DESP_PLATFORM=1',
                            '-DMESH_ENABLE_EMERGENCY_POWER=1',
                            '-I', str(ROOT / 'src'), str(source), '-o', str(binary)], check=True)
            for scenario in (15, 16):
                with self.subTest(scenario=scenario):
                    subprocess.run([str(binary), str(scenario)], check=True)

    def test_compiled_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'wireless.cpp'
            source.write_text('#include <cstdlib>\n'+HARNESS)
            for platform, defines in (
                    ('other', []), ('esp32', ['-DESP_PLATFORM=1']),
                    ('nrf52-compact', ['-DNRF52_PLATFORM=1', '-DBLE_PIN_CODE=123456', '-DWITH_MQTT_BRIDGE=1', '-DWITH_ESPNOW_BRIDGE=1', '-DMESH_CLI_COMPACT_REPLIES=1']),
                    ('esp32-compact', ['-DESP_PLATFORM=1', '-DMESH_CLI_COMPACT_REPLIES=1'])):
                binary = Path(tmp)/('wireless-' + platform)
                subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++11', '-Wall', '-Wextra', '-Werror',
                                *defines, '-I', str(ROOT/'src'), str(source), '-o', str(binary)], check=True)
                # WiFi-only prose must not consume flash on constrained boards
                # which expose Bluetooth or UART through this shared controller.
                self.assertEqual(b'WiFi enabled for this boot' in binary.read_bytes(), platform == 'esp32')
                for scenario in range(11):
                    with self.subTest(platform=platform, scenario=scenario):
                        subprocess.run([str(binary), str(scenario)], check=True)

    def test_actual_companion_transport_adapter(self):
        from test_companion_bluetooth_control import HARNESS as BT_HARNESS
        main = (ROOT/'examples/companion_radio/main.cpp').read_text()
        prefix = BT_HARNESS.split('@METHODS@')[0].replace(
            ' bool isAnyNetworkTerminalMode()',
            ' bool isWebConfigActiveOrStopping(){return false;}\n bool isAnyNetworkTerminalMode()')
        prefix = prefix.replace('struct {\n bool bluetooth_preference=',
            'struct AdapterWiFiPrefs { uint8_t wifi_enabled=1; };\n'
            'struct {\n AdapterWiFiPrefs wifi_prefs;\n'
            ' AdapterWiFiPrefs* getNodePrefs(){return &wifi_prefs;}\n'
            ' bool bluetooth_preference=')
        adapter = method(main, 'class CompanionWirelessBackend')+';\n'
        adapter += method(main, 'static void disableCompanionBluetoothForCli(')+'\n'
        adapter += method(main, 'bool handleCompanionWirelessCommand(')
        adapter += '\n'+method(main, 'static bool hasCompanionNonBluetoothClient(')
        adapter += '\n'+method(main, 'static void serviceCompanionBluetoothControl(')
        adapter += '\n'+method(main, 'bool handleCompanionBluetoothCommand(')
        source_text = prefix+r'''
#include <helpers/WirelessPolicyBackend.h>
#include "examples/companion_radio/CompanionWireless.h"
bool companion_wifi_requested=true,companion_wifi_active=true;
bool companion_wifi_setup_requested=false,companion_wifi_disable_in_progress=false;
bool stop_pending=false;
static bool saveCompanionWiFiPreference(bool on){
 if(!the_mesh.storage_accepts)return false;
 the_mesh.getNodePrefs()->wifi_enabled=on;return true;
}
struct {int mode_value=0;void mode(int value){mode_value=value;}void setAutoReconnect(bool){}} WiFi;
#define WIFI_OFF 0
bool finishStoppingCompanionWiFi(){
 // Production tears down sockets before waiting for the web server, while
 // companion_wifi_active remains true until the whole teardown completes.
 wifi_interface.disable();
 if(stop_pending)return false;
 companion_wifi_active=false;return true;
}
void startCompanionWiFi(){
 if(companion_wifi_active)return;
 companion_wifi_active=true;wifi_interface.enable();
}
struct {
 bool active=true;
 bool isEnabled(){return active;}
 void end(){active=false;}
 void init(){active=true;}
} radio_driver;
static void disableCompanionBluetoothForCli();
'''+adapter+r'''
int main(){
 CompanionWirelessBackend backend;mesh::wireless::control().begin(backend);
 interface_manager.addInterface(InterfaceType::Bluetooth,&bluetooth_interface);
 interface_manager.addInterface(InterfaceType::USB,&usb_serial_interface);
 interface_manager.addInterface(InterfaceType::WiFi,&wifi_interface);
 interface_manager.addInterface(InterfaceType::Ethernet,&ethernet_interface);
 interface_manager.enable();bluetooth_interface.connected=true;
 assert(backend.available()==mesh::wireless::All);
 assert(backend.enabled()==mesh::wireless::All);
 // UART/HWCDC power cannot authorize cutting all wireless connections.
 assert(!(backend.clients()&mesh::wireless::Independent));
 char reply[160];
 handleCompanionWirelessCommand("set 2.4ghz off",reply,sizeof(reply),CompanionWirelessSource::Network);
 assert(strstr(reply,"Error:"));
 // A stale framed BLE route must not disguise an actual USB ASCII request.
 bluetooth_interface.frame=true;uint8_t frame[176];interface_manager.checkRecvFrame(frame);
 handleCompanionWirelessCommand("set 2.4ghz off",reply,sizeof(reply),CompanionWirelessSource::Usb);
 assert(strstr(reply,"saved"));stop_pending=true;now_ms+=250;
 mesh::wireless::control().service(now_ms);
 assert(radio_driver.active&&bluetooth_interface.enabled); // wait for WiFi tasks/sockets first
 stop_pending=false;mesh::wireless::control().service(++now_ms);
 assert(!radio_driver.active&&!bluetooth_interface.enabled&&!companion_wifi_active);
 assert(!companion_wifi_requested);
 handleCompanionWirelessCommand("set 2.4ghz on",reply,sizeof(reply),CompanionWirelessSource::Usb);
 now_ms+=250;mesh::wireless::control().service(now_ms);
 assert(radio_driver.active&&bluetooth_interface.enabled&&companion_wifi_active);
 assert(companion_wifi_requested);
 // A pending saved WiFi change cannot be overwritten by its other alias.
 handleCompanionWirelessCommand("set wifi off",reply,sizeof(reply),CompanionWirelessSource::Usb);
 stop_pending=true;now_ms+=250;mesh::wireless::control().service(now_ms);
 assert(mesh::wireless::control().pending()&&companion_wifi_active&&!wifi_interface.enabled);
 assert(!the_mesh.getNodePrefs()->wifi_enabled);
 handleCompanionWirelessCommand("set wifi.enabled 1",reply,sizeof(reply),CompanionWirelessSource::Usb);
 assert(strstr(reply,"pending")&&!the_mesh.getNodePrefs()->wifi_enabled);
 stop_pending=false;mesh::wireless::control().service(++now_ms);
 assert(!mesh::wireless::control().pending()&&!companion_wifi_active);
 handleCompanionWirelessCommand("set wifi.enabled 1",reply,sizeof(reply),CompanionWirelessSource::Usb);
 now_ms+=250;mesh::wireless::control().service(now_ms);
 assert(the_mesh.getNodePrefs()->wifi_enabled&&companion_wifi_active&&wifi_interface.enabled);
 // Reversing an asynchronous shutdown must finish teardown and restart the
 // sockets, rather than treating the still-active flag as a healthy WiFi stack.
 for(const char* family : {"2.4ghz"}) {
   char command[40];snprintf(command,sizeof(command),"set %s off",family);
   handleCompanionWirelessCommand(command,reply,sizeof(reply),CompanionWirelessSource::Usb);
   stop_pending=true;now_ms+=250;mesh::wireless::control().service(now_ms);
   assert(mesh::wireless::control().pending()&&companion_wifi_active&&!wifi_interface.enabled);
   snprintf(command,sizeof(command),"set %s on",family);
   handleCompanionWirelessCommand(command,reply,sizeof(reply),CompanionWirelessSource::Usb);
   now_ms+=250;mesh::wireless::control().service(now_ms);
   assert(mesh::wireless::control().pending()&&!wifi_interface.enabled);
   stop_pending=false;mesh::wireless::control().service(++now_ms);
   assert(!mesh::wireless::control().pending()&&companion_wifi_active&&wifi_interface.enabled);
   assert(companion_wifi_requested&&!companion_wifi_disable_in_progress);
 }
 // WiFi's framed client must not be mistaken for independent USB.
 wifi_interface.frame=true;wifi_interface.connected=true;bluetooth_interface.connected=false;
 interface_manager.checkRecvFrame(frame);
 handleCompanionWirelessCommand("set wifi off",reply,sizeof(reply),CompanionWirelessSource::Framed);
 assert(strstr(reply,"Error:"));
 // An actual framed USB request is proof even without DTR on a UART bridge.
 usb_serial_interface.frame=true;usb_serial_interface.connected=true;
 interface_manager.checkRecvFrame(frame);
 handleCompanionWirelessCommand("set espnow off",reply,sizeof(reply),CompanionWirelessSource::Framed);
 now_ms+=250;mesh::wireless::control().service(now_ms);
 assert(!radio_driver.active&&companion_wifi_active&&bluetooth_interface.enabled);
 // Each shutdown must not use the other pending shutdown's connection as
 // its safety net. Bluetooth's force override remains available explicitly.
 usb_serial_interface.connected=false;
 bluetooth_interface.connected=true;bluetooth_interface.frame=true;
 interface_manager.checkRecvFrame(frame);
 handleCompanionWirelessCommand("set wifi off",reply,sizeof(reply),CompanionWirelessSource::Framed);
 assert(mesh::wireless::control().pending());
 handleCompanionBluetoothCommand("set bluetooth off",reply,sizeof(reply),CompanionBluetoothCommandSource::Terminal);
 assert(strstr(reply,"pending")&&bluetooth_interface.enabled);
 handleCompanionBluetoothCommand("set bluetooth off force",reply,sizeof(reply),CompanionBluetoothCommandSource::Terminal);
 assert(!bluetooth_interface.enabled);
 now_ms+=250;mesh::wireless::control().service(now_ms);
 assert(companion_wifi_active&&!mesh::wireless::control().pending());
 handleCompanionWirelessCommand("get wifi",reply,sizeof(reply),CompanionWirelessSource::Network);
 assert(strstr(reply,"cancelled"));
 // The opposite command order is protected too, including asynchronous
 // WiFi teardown where its socket can stay visible during the first stop.
 handleCompanionBluetoothCommand("set bluetooth on",reply,sizeof(reply),CompanionBluetoothCommandSource::Terminal);
 bluetooth_interface.connected=true;bluetooth_interface.frame=true;
 interface_manager.checkRecvFrame(frame);
 handleCompanionBluetoothCommand("set bluetooth off",reply,sizeof(reply),CompanionBluetoothCommandSource::Framed);
 assert(companion_bluetooth_off_at!=0);
 handleCompanionWirelessCommand("set wifi off",reply,sizeof(reply),CompanionWirelessSource::Network);
 assert(strstr(reply,"Bluetooth change pending")&&!mesh::wireless::control().pending());
 now_ms+=250;serviceCompanionBluetoothControl();
 assert(!bluetooth_interface.enabled&&companion_wifi_active);
 // A restore can remove WiFi too. Keep a BLE-only saved snapshot after a
 // failed restore, then simulate both management services recovering.
 companion_wifi_requested=companion_wifi_active=false;wifi_interface.disable();
 the_mesh.bluetooth_preference=true;interface_manager.enableBluetooth();
 handleCompanionWirelessCommand("set 2.4ghz off",reply,sizeof(reply),CompanionWirelessSource::Usb);
 now_ms+=250;mesh::wireless::control().service(now_ms);
 bluetooth_interface.enable_fails=true;
 handleCompanionWirelessCommand("set 2.4ghz on",reply,sizeof(reply),CompanionWirelessSource::Usb);
 now_ms+=250;mesh::wireless::control().service(now_ms);
 assert(!mesh::wireless::control().pending()&&!bluetooth_interface.enabled);
 bluetooth_interface.enable_fails=false;interface_manager.enableBluetooth();
 companion_wifi_requested=companion_wifi_active=true;wifi_interface.enable();
 wifi_interface.connected=true;bluetooth_interface.connected=true;
 bluetooth_interface.frame=true;interface_manager.checkRecvFrame(frame);
 handleCompanionBluetoothCommand("set bluetooth off",reply,sizeof(reply),CompanionBluetoothCommandSource::Framed);
 assert(companion_bluetooth_off_at!=0);
 handleCompanionWirelessCommand("set 2.4ghz on",reply,sizeof(reply),CompanionWirelessSource::Network);
 assert(strstr(reply,"Bluetooth change pending")&&!mesh::wireless::control().pending());
 // Explicit all cannot remove a service and may replace the pending stop.
 handleCompanionWirelessCommand("set 2.4ghz on all",reply,sizeof(reply),CompanionWirelessSource::Network);
 assert(mesh::wireless::control().pending());
 now_ms+=250;mesh::wireless::control().service(now_ms);
 assert(!companion_bluetooth_off_at&&bluetooth_interface.enabled&&companion_wifi_active);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'Arduino.h').write_text('#pragma once\n#include <cstdint>\n#include <cstddef>\n#include <initializer_list>\n')
            policy_store_stub(folder)
            (folder/'adapter.cpp').write_text(source_text)
            binary=folder/'adapter'
            subprocess.run([os.environ.get('CXX','g++'), '-std=c++17',
                            '-I',str(folder),'-I',str(ROOT),'-I',str(ROOT/'src'),
                            '-DESP32=1','-DMESH_COMPANION_WIRELESS_POLICY=1','-DBLE_PIN_CODE=1','-DWIFI_SSID=""',
                            '-DWITH_WEBCONFIG=1','-DMESH_PRIMARY_ESPNOW=1',
                            '-DENABLE_USB_INTERFACE=1','-DCOMPANION_FEATURE_TEXT_TERMINAL=1',
                            '-DCOMPANION_FEATURE_NETWORK_TERMINAL=1',
                            str(folder/'adapter.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)

    def test_role_and_restart_wiring(self):
        for role, backend in [('companion_radio','companion_wireless'),
                              ('simple_repeater','infrastructure_wireless'),
                              ('simple_room_server','infrastructure_wireless')]:
            source = (ROOT/'examples'/role/'main.cpp').read_text()
            self.assertIn(f'control().begin({backend})', source)
            self.assertIn('control().service(millis())', source[source.index('\nvoid loop()'):])
            loop = method(source, '\nvoid loop()')
            if role == 'companion_radio':
                guard = method(loop, 'mesh::powerdiag::Mask sleep_reason = [&]()')
                self.assertIn('if (mesh::wireless::control().pending()) '
                              'return mask(Reason::Wireless);', guard)
                self.assertIn('const bool can_sleep = sleep_reason == 0;', loop)
                self.assertLess(loop.index(guard), loop.index(
                    'if (can_sleep && the_mesh.canIdleDualRadio())'))
            else:
                self.assertIn('&& sleep_guard(!mesh::wireless::control().pending(), '
                              'mesh::powerdiag::Reason::Wireless)', loop)
                self.assertIn('return allowed;', method(loop, 'const auto sleep_guard = []'))
        gates = {
            'src/helpers/bridges/MQTTBridge.cpp': 'WiFi',
            'src/helpers/bridges/ESPNowBridge.cpp': 'EspNow',
            'src/helpers/esp32/ESPNOWRadio.cpp': 'EspNow',
            'src/helpers/esp32/WebConfigServer.cpp': 'WiFi',
            'src/helpers/WiFiSetupPortal.cpp': 'WiFi',
            'src/helpers/MultiSerialInterface.h': 'Bluetooth',
        }
        for name, bit in gates.items():
            with self.subTest(file=name):
                self.assertIn(f'control().{"backgroundBlocked" if bit == "WiFi" else "blocked"}(mesh::wireless::{bit})', (ROOT/name).read_text())

    def test_actual_infrastructure_transport_adapter(self):
        source = r'''
#include <helpers/WirelessControl.h>
#include <cassert>
enum {WIFI_STA=1,WIFI_OFF=0,WIFI_IF_STA=0};
struct {
 bool usb=false, ota=false;
 bool isUsbDataConnected(){return usb;}
 bool isOTAUpdateRunning(){return ota;}
 bool stopOTAUpdate(char*){ota=false;return true;}
} board;
struct {
 int mode_value=WIFI_STA;bool autoreconnect=true;
 void setAutoReconnect(bool value){autoreconnect=value;}
 void disconnect(bool,bool){}
 void mode(int value){mode_value=value;}
} WiFi;
namespace mesh {namespace wifi {void applyProtocolMask(int){}void restoreEspNowChannel(){}}}
struct {
 bool active=true;bool isEnabled(){return active;}
 void init(){active=true;}void end(){active=false;}
} radio_driver;
struct AdapterNodePrefs { bool bridge_enabled=true; };
struct AdapterObserverPrefs { const char* wifi_ssid="test"; };
struct WebConfigServer {
 static bool saved_enabled;
 static bool loadEnabled(bool) {return saved_enabled;}
 static bool hasConfiguredWiFi(const AdapterObserverPrefs* p) {return p->wifi_ssid[0]!=0;}
};
bool WebConfigServer::saved_enabled=true;
struct {
 AdapterNodePrefs prefs;AdapterObserverPrefs observer;
 AdapterNodePrefs* getNodePrefs(){return &prefs;}
 AdapterObserverPrefs* getObserverPrefs(){return &observer;}
 bool web=true,stopping=false,bridge=true,network=false,mqtt_pending=false;
 bool isWebConfigActive(){return web||stopping;}
 bool isWebConfigStopping(){return stopping;}
 bool hasWirelessNetworkClient(){return network;}
 bool isBridgeRunning(){return bridge;}
 bool setBridgeState(bool on){bridge=on;return true;}
 bool isMqttBridgeRunning(){return bridge;}
 bool isMqttBridgeStartPending(){return mqtt_pending;}
 bool isEspNowBridgeRunning(){return bridge;}
 bool espnow_pref=true;
 bool isEspNowEnabledPreference()const{return espnow_pref;}
 bool setEspNowEnabledPreference(bool on){espnow_pref=on;return true;}
 bool setMqttBridgeState(bool on){bridge=on;mqtt_pending=false;return true;}
 bool setEspNowBridgeState(bool on){bridge=on;return true;}
 bool stopWebConfig(char*){web=false;stopping=true;return true;}
 bool startWebConfig(bool,char*){web=true;return true;}
} the_mesh;
#include "examples/InfrastructureWireless.h"
int main(){
 InfrastructureWirelessBackend b;mesh::wireless::Control c;c.begin(b);
 char reply[160];using namespace mesh::wireless;
#ifdef WITH_MQTT_BRIDGE
 assert(b.available()==(mesh::wireless::WiFi|EspNow));
 assert(b.enabled()==(mesh::wireless::WiFi|EspNow));
 assert(b.clients()==0);the_mesh.network=true;assert(b.clients()==mesh::wireless::WiFi);
 c.handle("set 2.4ghz off",reply,sizeof(reply),0,mesh::wireless::WiFi);assert(strstr(reply,"Error:"));
 c.handle("set 2.4ghz off",reply,sizeof(reply),0,Independent);c.service(250);
 assert(c.pending()&&!the_mesh.bridge&&the_mesh.stopping&&radio_driver.active);
 the_mesh.stopping=false;c.service(251);
 assert(!c.pending()&&!radio_driver.active&&::WiFi.mode_value==WIFI_OFF);
 c.handle("set 2.4ghz on",reply,sizeof(reply),300,Independent);c.service(550);
 assert(the_mesh.bridge&&the_mesh.web&&radio_driver.active);
 // OTA alone remains visible as an active WiFi owner after WebConfig drains.
 the_mesh.web=the_mesh.bridge=false;board.ota=true;
 WebConfigServer::saved_enabled=false;
 assert(b.enabled()&mesh::wireless::WiFi);
 InfrastructureWirelessBackend ota_backend;
 assert(ota_backend.set(mesh::wireless::WiFi,false)==Result::Done);
 assert(!board.ota);
 // Failed startup is still requested WiFi work and must participate in off/on.
 the_mesh.mqtt_pending=true;
 assert(b.enabled()&mesh::wireless::WiFi);
 InfrastructureWirelessBackend pending_backend;
 assert(pending_backend.set(mesh::wireless::WiFi,false)==Result::Done);
 assert(!the_mesh.mqtt_pending&&!the_mesh.bridge);
 assert(pending_backend.set(mesh::wireless::WiFi,true)==Result::Done);
 assert(the_mesh.bridge&&!the_mesh.web);
 the_mesh.web=the_mesh.bridge=true;WebConfigServer::saved_enabled=true;
 // Individual WiFi off retains the ESP-NOW driver and stops both its own services.
 c.handle("set wifi off",reply,sizeof(reply),600,Independent);c.service(850);
 the_mesh.stopping=false;c.service(851);
 assert(!the_mesh.bridge&&!the_mesh.web&&radio_driver.active&&::WiFi.mode_value==WIFI_STA);
 assert(!mesh::wifi::infrastructureWiFiEnabled());
 c.handle("set wifi on",reply,sizeof(reply),900,Independent);c.service(1150);
 assert(the_mesh.bridge&&the_mesh.web&&radio_driver.active);
 // Repeating "on" leaves the running services active and does not rely on
 // the removed background outage-suspension policy.
 c.handle("set wifi on",reply,sizeof(reply),1160,Independent);c.service(1410);
 assert(the_mesh.bridge&&the_mesh.web);
 c.handle("set wifi on",reply,sizeof(reply),1420,Independent);c.service(1670);
 assert(the_mesh.bridge&&the_mesh.web);
 c.handle("set espnow off",reply,sizeof(reply),1700,Independent);c.service(1950);
 assert(!radio_driver.active&&the_mesh.web&&the_mesh.bridge&&::WiFi.mode_value==WIFI_STA);
 // Master On restores permission, not an AP whose own saved switch is Off.
 the_mesh.web=the_mesh.bridge=false;the_mesh.prefs.bridge_enabled=false;
 WebConfigServer::saved_enabled=false;
 c.handle("set wifi.enabled 0",reply,sizeof(reply),2000,Independent);c.service(2250);
 assert(!mesh::wifi::infrastructureWiFiEnabled());
 c.handle("set wifi.enabled 1",reply,sizeof(reply),2300,Independent);c.service(2550);
 assert(mesh::wifi::infrastructureWiFiEnabled()&&!c.pending());
 assert(!the_mesh.web&&!the_mesh.bridge&&!(b.enabled()&mesh::wireless::WiFi));
 assert(!the_mesh.prefs.bridge_enabled&&!WebConfigServer::saved_enabled);
 c.handle("get wifi.enabled",reply,sizeof(reply),2600,Independent);assert(!strcmp(reply,"> 1"));
 c.handle("get wifi",reply,sizeof(reply),2600,Independent);assert(!strcmp(reply,"wifi off"));
 InfrastructureWirelessBackend after_reboot;
 assert(after_reboot.set(mesh::wireless::WiFi,true)==Result::Done);
 assert(!the_mesh.web&&!the_mesh.bridge); // No saved owner means no invented AP after reboot.
 // A transient group restore keeps an explicitly started web session even
 // when its automatic startup preference is Off.
 the_mesh.web=true;
 c.handle("set 2.4ghz off",reply,sizeof(reply),2700,Independent);c.service(2950);
 the_mesh.stopping=false;c.service(2951);assert(!the_mesh.web);
 c.handle("set 2.4ghz on",reply,sizeof(reply),3000,Independent);c.service(3250);
 assert(the_mesh.web&&!WebConfigServer::saved_enabled);
 assert(mesh::wifi::infrastructureWiFiEnabled());
 // A persistent On uses the latest service choices, not its older live snapshot.
 WebConfigServer::saved_enabled=true;the_mesh.web=true;
 c.handle("set wifi off",reply,sizeof(reply),3300,Independent);c.service(3550);
 the_mesh.stopping=false;c.service(3551);assert(!the_mesh.web);
 WebConfigServer::saved_enabled=false;
 c.handle("set wifi on",reply,sizeof(reply),3600,Independent);c.service(3850);
 assert(!the_mesh.web); // The newer saved WebUI Off wins.
 c.handle("set wifi off",reply,sizeof(reply),3900,Independent);c.service(4150);
 the_mesh.stopping=false;c.service(4151);assert(!the_mesh.web);
 WebConfigServer::saved_enabled=true;
 c.handle("set wifi on",reply,sizeof(reply),4200,Independent);c.service(4450);
 assert(the_mesh.web); // A newer saved WebUI On is applied without rebooting.
#else
 assert(b.available()==EspNow&&b.enabled()==EspNow);
 c.handle("set espnow off",reply,sizeof(reply),0,Independent);c.service(250);assert(!the_mesh.bridge);
 c.handle("set espnow on",reply,sizeof(reply),300,Independent);c.service(550);assert(the_mesh.bridge);
#endif
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'helpers/esp32').mkdir(parents=True)
            policy_store_stub(folder)
            (folder/'WiFi.h').write_text('#pragma once\n')
            (folder/'helpers/esp32/WiFiRadioPolicy.h').write_text('#pragma once\n')
            (folder/'helpers/esp32/InfrastructureWiFiPreference.h').write_text(r'''
#pragma once
namespace mesh {namespace wifi {
inline bool& adapterWiFiPreference(){static bool value=true;return value;}
inline bool infrastructureWiFiEnabled(){return adapterWiFiPreference();}
inline bool saveInfrastructureWiFiEnabled(bool on){adapterWiFiPreference()=on;return true;}
}}
''')
            (folder/'infrastructure.cpp').write_text(source)
            for defines in [('WITH_MQTT_BRIDGE','WITH_WEBCONFIG','MESH_PRIMARY_ESPNOW'),('WITH_ESPNOW_BRIDGE',)]:
                with self.subTest(features=defines):
                    binary=folder/'infrastructure'
                    subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-DESP32=1',
                                    '-I',str(folder),'-I',str(ROOT),'-I',str(ROOT/'src'),
                                    *['-D'+d+'=1' for d in defines],str(folder/'infrastructure.cpp'),
                                    '-o',str(binary)],check=True)
                    subprocess.run([str(binary)],check=True)

if __name__ == '__main__':
    unittest.main()
