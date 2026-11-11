"""Exercise durable wireless admission, reboot gates, and pause/resume intent."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <helpers/WirelessControl.h>
#include <cassert>
#include <cstdlib>
#include <vector>
using namespace mesh::wireless;
struct Disk { bool wifi=true,espnow=true,bluetooth=true,off=false;uint8_t mask=0; };
struct Adapter : Backend {
 Disk& disk; uint8_t active=All,sessions=0,fail_runtime=0,fail_save=0;
 bool group_save=true;unsigned writes=0;
 Adapter(Disk& d):disk(d){}
 uint8_t available()const override{return All;}
 uint8_t enabled()const override{return active;}
 uint8_t clients()const override{return sessions;}
 bool hasWiFiPreference()const override{return true;}
 bool wifiPreference()const override{return disk.wifi;}
 bool saveWiFiPreference(bool on)override{++writes;if(fail_save==WiFi)return false;disk.wifi=on;return true;}
 bool hasEspNowPreference()const override{return true;}
 bool espNowPreference()const override{return disk.espnow;}
 bool saveEspNowPreference(bool on)override{++writes;if(fail_save==EspNow)return false;disk.espnow=on;return true;}
 bool hasBluetoothPreference()const override{return true;}
 bool bluetoothPreference()const override{return disk.bluetooth;}
 bool saveBluetoothPreference(bool on)override{++writes;if(fail_save==Bluetooth)return false;disk.bluetooth=on;return true;}
 bool hasGroupPreference()const override{return true;}
 bool groupPreference(bool& off,uint8_t& mask)const override{off=disk.off;mask=disk.mask;return true;}
 bool saveGroupPreference(bool off,uint8_t mask)override{++writes;if(!group_save)return false;disk.off=off;disk.mask=mask;return true;}
 Result set(uint8_t bit,bool on)override{if(fail_runtime==bit)return Result::Failed;if(on)active|=bit;else active&=~bit;return Result::Done;}
};
struct Fixture {
 Disk disk;Adapter backend;Control control;char reply[160]={};uint32_t now=10;
 Fixture():backend(disk){control.begin(backend);}
 void command(const char* text,uint8_t requester=Independent){assert(control.handle(text,reply,sizeof(reply),now,requester));}
 void tick(){now+=250;control.service(now);}
};
int main(int argc,char** argv){
 const int scenario=argc>1?atoi(argv[1]):0;
 if(scenario==0){
  for(unsigned mask=0;mask<=All;++mask){
   Fixture f;f.backend.active=mask;f.disk.wifi=mask&WiFi;f.disk.espnow=mask&EspNow;f.disk.bluetooth=mask&Bluetooth;
   f.control.begin(f.backend);const Disk before=f.disk;
   f.command("set 2.4ghz off");assert(strstr(f.reply,"saved")&&f.disk.off&&f.disk.mask==mask);f.tick();
   assert(f.backend.active==0&&f.disk.wifi==before.wifi&&f.disk.espnow==before.espnow&&f.disk.bluetooth==before.bluetooth);
   f.command("set 2.4ghz off");f.tick();assert(f.disk.mask==mask);
   Adapter after(f.disk);after.active=0;Control reboot;reboot.begin(after);
   assert(reboot.masterOff()&&reboot.blocked(All)&&!reboot.allowService(EspNow));
   assert(reboot.handle("set 2.4ghz on",f.reply,sizeof(f.reply),100,Independent));reboot.service(350);
   assert(!reboot.masterOff()&&!f.disk.off&&after.active==mask);
   assert(f.disk.wifi==before.wifi&&f.disk.espnow==before.espnow&&f.disk.bluetooth==before.bluetooth);
   assert(reboot.handle("set 2.4ghz on all",f.reply,sizeof(f.reply),400,Independent));reboot.service(650);
   assert(after.active==All&&f.disk.wifi&&f.disk.espnow&&f.disk.bluetooth&&!f.disk.off);
   Control again;again.begin(after);assert(!again.blocked(All));
  }
 }else if(scenario==1){
  Fixture f;f.backend.group_save=false;f.command("set 2.4ghz off");
  assert(strstr(f.reply,"Error")&&!f.control.pending()&&!f.disk.off&&f.disk.mask==0&&f.backend.active==All);
  f.backend.group_save=true;f.command("set 2.4ghz off");f.tick();
  f.command("set espnow.enabled off");f.tick();f.command("set wifi off");f.tick();
  f.disk.bluetooth=false; // An individual BLE setter uses its existing saved preference.
  f.command("set 2.4ghz on");f.tick();assert(f.backend.active==0&&!f.disk.off);
  for(uint8_t failed:{uint8_t(WiFi),uint8_t(EspNow),uint8_t(Bluetooth)}){
   f.backend.fail_save=failed;f.command("set 2.4ghz on all");
   assert(strstr(f.reply,"Error")&&!f.control.pending());
   assert(!f.disk.wifi&&!f.disk.espnow&&!f.disk.bluetooth&&!f.disk.off&&f.backend.active==0);
  }
  f.backend.fail_save=0;f.backend.group_save=false;f.command("set 2.4ghz on all");
  assert(strstr(f.reply,"Error")&&!f.disk.wifi&&!f.disk.espnow&&!f.disk.bluetooth&&!f.disk.off);
 }else if(scenario==2){
  Fixture f;f.backend.sessions=Independent;f.command("set 2.4ghz off",WiFi);
  assert(f.control.pending()&&f.disk.off);f.backend.sessions=0;f.tick();
  assert(!f.disk.off&&!f.control.masterOff()&&f.backend.active==All&&!f.control.pending());
  f.command("get 2.4ghz");assert(strstr(f.reply,"cancelled"));
  f.backend.sessions=WiFi;f.command("set espnow off",EspNow);assert(!f.disk.espnow);
  f.backend.sessions=0;f.tick();assert(f.disk.espnow&&f.backend.active==All&&!f.control.pending());
  // Failed replacement admission must retain the original Off rollback.
  f.backend.sessions=Independent;f.command("set 2.4ghz off",WiFi);assert(f.disk.off);
  f.backend.fail_save=EspNow;f.command("set 2.4ghz on all",WiFi);
  assert(strstr(f.reply,"Error")&&f.disk.off&&f.control.pending());
  f.backend.fail_save=0;f.backend.sessions=0;f.tick();
  assert(!f.disk.off&&!f.control.masterOff()&&f.backend.active==All);
  // An idempotent duplicate before teardown also keeps the first baseline.
  f.backend.sessions=Independent;f.command("set 2.4ghz off",WiFi);
  f.command("set 2.4ghz off",WiFi);f.backend.sessions=0;f.tick();
  assert(!f.disk.off&&!f.control.masterOff()&&f.backend.active==All);
 }else if(scenario==3){
  Fixture f;f.backend.fail_runtime=EspNow;f.command("set espnow.enabled 0");f.tick();
  assert(!f.disk.espnow&&f.backend.active==All&&!f.control.pending());
  f.command("get espnow.enabled");assert(strcmp(f.reply,"> off")==0);
  f.command("get espnow");assert(strstr(f.reply,"on")&&strstr(f.reply,"failed")&&strstr(f.reply,"saved"));
  Control reboot;reboot.begin(f.backend);assert(reboot.blocked(EspNow)&&!reboot.allowService(EspNow));
  f.backend.fail_runtime=WiFi;f.command("set 2.4ghz off");f.tick();assert(f.disk.off);
  Control group_reboot;group_reboot.begin(f.backend);assert(group_reboot.masterOff()&&group_reboot.blocked(All));
 }else if(scenario==4){
  Fixture f;f.command("set 2.4ghz off");f.tick();assert(f.disk.wifi&&f.disk.espnow&&f.disk.bluetooth);
  assert(f.control.allowWiFiForOTA()&&f.control.masterOff()&&!f.control.blocked(WiFi));
  assert(f.control.blocked(EspNow|Bluetooth)&&!f.control.allowService(EspNow));
  assert(f.control.backgroundBlocked(WiFi));
  f.backend.active|=WiFi;f.control.finishWiFiForOTA();assert(f.control.blocked(WiFi));
  f.control.service(f.now);f.tick();assert(f.backend.active==0&&f.disk.off&&f.disk.mask==All);
  f.command("set 2.4ghz on all");f.tick();assert(f.backend.active==All&&!f.disk.off);
  f.control.finishWiFiForOTA();f.tick();assert(f.backend.active==All);
 }else if(scenario==5){
  Fixture f;f.command("set espnow off");f.tick();assert(!f.disk.espnow);
  assert(!f.control.allowService(EspNow));
  assert(f.control.suspendForEmergency());assert(f.control.resumeAfterEmergency());
  assert(!(f.backend.active&EspNow)&&!f.disk.espnow);
  f.command("set espnow.enabled on");f.tick();assert(f.disk.espnow&&(f.backend.active&EspNow));
  f.command("set 2.4ghz off");f.tick();assert(f.disk.off);
  f.command("set espnow on");assert(strstr(f.reply,"2.4ghz")&&strstr(f.reply,"Error"));
  assert(f.control.suspendForEmergency()&&f.control.resumeAfterEmergency()&&f.backend.active==0);
 }
}
'''

class SavedGroupTest(unittest.TestCase):
    def test_saved_group_and_espnow(self):
        with tempfile.TemporaryDirectory(prefix='wireless-saved-group-') as tmp:
            folder=Path(tmp)
            (folder/'test.cpp').write_text(HARNESS)
            for platform in ([], ['-DESP_PLATFORM=1'],
                             ['-DNRF52_PLATFORM=1','-DBLE_PIN_CODE=123456','-DWITH_MQTT_BRIDGE=1','-DWITH_ESPNOW_BRIDGE=1','-DMESH_CLI_COMPACT_REPLIES=1'],
                             ['-DESP_PLATFORM=1','-DMESH_CLI_COMPACT_REPLIES=1']):
                binary=folder/'test'
                subprocess.run([os.environ.get('CXX','g++'),'-std=c++11','-DMESH_ENABLE_EMERGENCY_POWER=1',
                                *platform,'-I',str(ROOT/'src'),str(folder/'test.cpp'),'-o',str(binary)],check=True)
                for scenario in range(6):
                    with self.subTest(platform=platform,scenario=scenario):
                        subprocess.run([str(binary),str(scenario)],check=True)

    def test_bridge_preference_save_is_checked_and_failed_publication_is_restored(self):
        for role in ('simple_repeater','simple_room_server'):
            header=(ROOT/'examples'/role/'MyMesh.h').read_text()
            methods='\n'.join(method(header,signature) for signature in (
                'bool isEspNowEnabledPreference()', 'bool setEspNowEnabledPreference('))
            source=r'''
#include <cstdint>
#include <cassert>
struct Prefs { uint8_t bridge_enabled=1,espnow_bridge_enabled=1; };
struct CLI {
 Prefs* prefs;Prefs disk;unsigned writes=0;bool fail_next=false;
 bool saveCommonPrefs(){++writes;disk=*prefs;if(fail_next){fail_next=false;return false;}return true;}
};
struct Role {
 Prefs _prefs;CLI _cli;
 Role(){_cli.prefs=&_prefs;}
 @METHODS@
};
int main(){
 Role r;assert(r.isEspNowEnabledPreference());
 assert(r.setEspNowEnabledPreference(false));assert(!r.isEspNowEnabledPreference());
#if defined(WITH_MQTT_BRIDGE) || defined(WITH_RS232_BRIDGE)
 assert(r._prefs.bridge_enabled); // Other bridge intent is independent.
#else
 assert(!r._prefs.bridge_enabled);
#endif
 const Prefs before=r._prefs;
 r._cli.fail_next=true;assert(!r.setEspNowEnabledPreference(true));
 assert(r._prefs.bridge_enabled==before.bridge_enabled&&r._prefs.espnow_bridge_enabled==before.espnow_bridge_enabled);
 assert(r._cli.disk.bridge_enabled==before.bridge_enabled&&r._cli.disk.espnow_bridge_enabled==before.espnow_bridge_enabled);
 assert(r._cli.writes==3); // Failed published image followed by confirmed restoration.
 assert(r.setEspNowEnabledPreference(true)&&r.isEspNowEnabledPreference());
}
'''.replace('@METHODS@',methods)
            with tempfile.TemporaryDirectory(prefix='espnow-role-save-') as tmp:
                folder=Path(tmp);(folder/'test.cpp').write_text(source)
                for flags in ([],['-DESPNOW_BRIDGE_MERGED=1'],['-DWITH_MQTT_BRIDGE=1'],['-DWITH_RS232_BRIDGE=1']):
                    with self.subTest(role=role,flags=flags):
                        subprocess.run([os.environ.get('CXX','g++'),'-std=c++11',*flags,
                            str(folder/'test.cpp'),'-o',str(folder/'test')],check=True)
                        subprocess.run([str(folder/'test')],check=True)

if __name__=='__main__':unittest.main()
