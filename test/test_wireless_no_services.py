"""Keep LoRa/wired ARM builds free of unused wireless controller state."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
#include <helpers/WirelessControl.h>
#include <cassert>
#include <cstring>
#include <initializer_list>
using namespace mesh::wireless;
struct Observed : Backend {
 mutable unsigned reads=0;unsigned writes=0;
 uint8_t available()const override{++reads;return 0;}
 uint8_t enabled()const override{++reads;return 0;}
 uint8_t clients()const override{++reads;return 0;}
 bool hasWiFiPreference()const override{++reads;return true;}
 bool wifiPreference()const override{++reads;return false;}
 bool saveWiFiPreference(bool)override{++writes;return false;}
 bool groupPreference(bool&,uint8_t&)const override{++reads;return false;}
 Result set(uint8_t,bool)override{++writes;return Result::Failed;}
};
int main(){
#if EXPECT_TINY
 static_assert(MESH_WIRELESS_NO_SERVICES==1,"genuine no-wireless ARM path");
 static_assert(sizeof(Control)==1,"no unused wireless state in constrained ARM RAM");
 Control c;Observed b;c.begin(b);
 c.loadGroupPolicy(true,All);c.loadEspNowPolicy(false);
 assert(!c.hasWiFiPreference()&&c.wifiPreference());
 for(uint8_t bit:{uint8_t(WiFi),uint8_t(Bluetooth),uint8_t(EspNow),uint8_t(All)}){
  assert(!c.blocked(bit)&&!c.backgroundBlocked(bit)&&!c.pendingDisable(bit));
  assert(!c.allowService(bit));
 }
 assert(!c.allowWiFiForOTA());c.finishWiFiForOTA();
 for(uint32_t now:{0u,250u,30000u,0xffffffffu})c.service(now);
 assert(!c.pending()&&!c.masterOff()&&c.enabled()==0);
#if MESH_ENABLE_EMERGENCY_POWER
 assert(c.suspendForEmergency()&&c.resumeAfterEmergency());
 assert(c.suspendForEmergency()&&c.suspendForEmergency()&&c.resumeAfterEmergency());
#endif
 for(const char* text:{"", "unknown", "get wifi", "get wifi.enabled", "get espnow", "get espnow.enabled", "get 2.4ghz",
     "set wifi on", "set wifi.enabled 0", "set wifi off force", "set espnow on", "set espnow.enabled off",
     "set 2.4ghz on all", "set 2.4ghz off force", "get wifi junk", "set wifi on force", "set espnow.channel 7"}){
  for(size_t size=0;size<=160;++size){
   char actual[160],expected[160];memset(actual,0x5a,sizeof(actual));memset(expected,0x5a,sizeof(expected));
   assert(c.handle(text,actual,size,100,Independent)==handleNoServices(text,expected,size));
   assert(memcmp(actual,expected,sizeof(actual))==0);
  }
  assert(c.handle(text,nullptr,160,100,Independent)==handleNoServices(text,nullptr,160));
 }
 assert(b.reads==0&&b.writes==0);
#else
 static_assert(MESH_WIRELESS_NO_SERVICES==0,"active/native builds retain controller");
 static_assert(sizeof(Control)>1,"full controller retained");
 // Native/header consumers remain safe before any backend is registered.
 Control c;assert(c.enabled()==0&&!c.pending()&&!c.masterOff()&&!c.blocked(All));
 c.service(100);
#if MESH_ENABLE_EMERGENCY_POWER
 assert(c.suspendForEmergency()&&c.resumeAfterEmergency());
#endif
#endif
}
'''

class WirelessNoServicesTest(unittest.TestCase):
    def test_unavailable_arm_services_have_no_state_or_backend_access(self):
        with tempfile.TemporaryDirectory(prefix='wireless-no-services-') as tmp:
            folder=Path(tmp);(folder/'test.cpp').write_text(HARNESS)
            for platform in ('NRF52_PLATFORM','STM32_PLATFORM'):
                for emergency in (0,1):
                    for wired in ([],['-DWITH_BRIDGE=1'],['-DWITH_RS232_BRIDGE=1'],
                                  ['-DWITH_BRIDGE=1','-DWITH_RS232_BRIDGE=1'],['-DETHERNET_ENABLED=1'],
                                  ['-DMESH_PRIMARY_ESPNOW=0']):
                        with self.subTest(platform=platform,emergency=emergency,wired=wired):
                            subprocess.run([os.environ.get('CXX','g++'),'-std=c++11','-Wall','-Wextra','-Werror',
                                '-DEXPECT_TINY=1','-D'+platform+'=1',f'-DMESH_ENABLE_EMERGENCY_POWER={emergency}',
                                *wired,'-I',str(ROOT/'src'),str(folder/'test.cpp'),'-o',str(folder/'test')],check=True)
                            subprocess.run([str(folder/'test')],check=True)

    def test_active_wireless_and_native_builds_keep_full_controller(self):
        with tempfile.TemporaryDirectory(prefix='wireless-available-') as tmp:
            folder=Path(tmp);(folder/'test.cpp').write_text(HARNESS)
            profiles=[[],['-DMESH_ENABLE_EMERGENCY_POWER=1']]
            for platform in ('NRF52_PLATFORM','STM32_PLATFORM'):
                profiles.extend([['-D'+platform+'=1','-D'+feature+'=1','-DMESH_ENABLE_EMERGENCY_POWER=1']
                    for feature in ('BLE_PIN_CODE','WIFI_SSID','ENABLE_WIFI_INTERFACE','WITH_WEBCONFIG',
                                    'WITH_MQTT_BRIDGE','WITH_UDP_BRIDGE','WITH_ESPNOW_BRIDGE','MESH_PRIMARY_ESPNOW')])
            for flags in profiles:
                with self.subTest(flags=flags):
                    subprocess.run([os.environ.get('CXX','g++'),'-std=c++11','-Wall','-Wextra','-Werror',
                        '-DEXPECT_TINY=0',*flags,'-I',str(ROOT/'src'),str(folder/'test.cpp'),'-o',str(folder/'test')],check=True)
                    subprocess.run([str(folder/'test')],check=True)

    def test_derived_bridge_and_wifi_aliases_do_not_change_definition_between_units(self):
        with tempfile.TemporaryDirectory(prefix='wireless-tus-') as tmp:
            folder=Path(tmp)
            (folder/'before.cpp').write_text('#define WITH_BRIDGE 1\n#include <helpers/WirelessControl.h>\n'
                'unsigned before(){return sizeof(mesh::wireless::Control);}\n')
            (folder/'after.cpp').write_text('#include <helpers/WirelessControl.h>\n#define WITH_BRIDGE 1\n'
                'unsigned after(){return sizeof(mesh::wireless::Control);}\n')
            (folder/'main.cpp').write_text('#include <cassert>\nunsigned before();unsigned after();\n'
                'int main(){assert(before()==after());}\n')
            for flags in (['-DNRF52_PLATFORM=1','-DWITH_RS232_BRIDGE=1'],
                          ['-DSTM32_PLATFORM=1'],['-DNRF52_PLATFORM=1','-DENABLE_WIFI_INTERFACE=1']):
                subprocess.run([os.environ.get('CXX','g++'),'-std=c++11',*flags,'-I',str(ROOT/'src'),
                    str(folder/'before.cpp'),str(folder/'after.cpp'),str(folder/'main.cpp'),'-o',str(folder/'test')],check=True)
                subprocess.run([str(folder/'test')],check=True)

if __name__=='__main__':unittest.main()
