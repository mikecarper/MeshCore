"""Compile and execute the production wireless command/state controller."""
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
 }
}
'''

class WirelessControlTests(unittest.TestCase):
    def test_compiled_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'wireless.cpp'
            source.write_text('#include <cstdlib>\n'+HARNESS)
            binary = Path(tmp)/'wireless'
            subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(ROOT/'src'), str(source), '-o', str(binary)], check=True)
            for scenario in range(8):
                with self.subTest(scenario=scenario):
                    subprocess.run([str(binary), str(scenario)], check=True)

    def test_actual_companion_transport_adapter(self):
        from test_companion_bluetooth_control import HARNESS as BT_HARNESS
        main = (ROOT/'examples/companion_radio/main.cpp').read_text()
        prefix = BT_HARNESS.split('@METHODS@')[0].replace(
            ' bool isAnyNetworkTerminalMode()',
            ' bool isWebConfigActiveOrStopping(){return false;}\n bool isAnyNetworkTerminalMode()')
        adapter = method(main, 'class CompanionWirelessBackend')+';\n'
        adapter += method(main, 'static void disableCompanionBluetoothForCli(')+'\n'
        adapter += method(main, 'bool handleCompanionWirelessCommand(')
        source_text = prefix+r'''
#include "examples/companion_radio/CompanionWireless.h"
bool companion_wifi_requested=true,companion_wifi_active=true;
bool companion_wifi_setup_requested=false,companion_wifi_disable_in_progress=false;
bool stop_pending=false;
struct {int mode_value=0;void mode(int value){mode_value=value;}void setAutoReconnect(bool){}} WiFi;
#define WIFI_OFF 0
bool finishStoppingCompanionWiFi(){
 if(stop_pending)return false;
 companion_wifi_active=false;wifi_interface.disable();return true;
}
void startCompanionWiFi(){companion_wifi_active=true;wifi_interface.enable();}
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
 assert(strstr(reply,"requested"));stop_pending=true;now_ms+=250;
 mesh::wireless::control().service(now_ms);
 assert(radio_driver.active&&bluetooth_interface.enabled); // wait for WiFi tasks/sockets first
 stop_pending=false;mesh::wireless::control().service(++now_ms);
 assert(!radio_driver.active&&!bluetooth_interface.enabled&&!companion_wifi_active);
 assert(!companion_wifi_requested);
 handleCompanionWirelessCommand("set 2.4ghz on",reply,sizeof(reply),CompanionWirelessSource::Usb);
 now_ms+=250;mesh::wireless::control().service(now_ms);
 assert(radio_driver.active&&bluetooth_interface.enabled&&companion_wifi_active);
 assert(companion_wifi_requested);
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
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'Arduino.h').write_text('#pragma once\n#include <cstdint>\n#include <cstddef>\n#include <initializer_list>\n')
            (folder/'adapter.cpp').write_text(source_text)
            binary=folder/'adapter'
            subprocess.run([os.environ.get('CXX','g++'), '-std=c++17',
                            '-I',str(folder),'-I',str(ROOT),'-I',str(ROOT/'src'),
                            '-DESP32=1','-DBLE_PIN_CODE=1','-DWIFI_SSID=""',
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
            self.assertIn('&& !mesh::wireless::control().pending()', source[source.index('\nvoid loop()'):])
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
                self.assertIn(f'control().blocked(mesh::wireless::{bit})', (ROOT/name).read_text())

    def test_actual_infrastructure_transport_adapter(self):
        source = r'''
#include <helpers/WirelessControl.h>
#include <cassert>
enum {WIFI_STA=1,WIFI_OFF=0,WIFI_IF_STA=0};
struct {bool usb=false;bool isUsbDataConnected(){return usb;}} board;
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
struct {
 bool web=true,stopping=false,bridge=true,network=false;
 bool isWebConfigActive(){return web||stopping;}
 bool isWebConfigStopping(){return stopping;}
 bool hasWirelessNetworkClient(){return network;}
 bool isBridgeRunning(){return bridge;}
 bool setBridgeState(bool on){bridge=on;return true;}
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
 // Individual WiFi off retains the ESP-NOW driver and stops both its own services.
 c.handle("set wifi off",reply,sizeof(reply),600,Independent);c.service(850);
 the_mesh.stopping=false;c.service(851);
 assert(!the_mesh.bridge&&!the_mesh.web&&radio_driver.active&&::WiFi.mode_value==WIFI_STA);
 c.handle("set wifi on",reply,sizeof(reply),900,Independent);c.service(1150);
 assert(the_mesh.bridge&&the_mesh.web&&radio_driver.active);
 c.handle("set espnow off",reply,sizeof(reply),1200,Independent);c.service(1450);
 assert(!radio_driver.active&&the_mesh.web&&the_mesh.bridge&&::WiFi.mode_value==WIFI_STA);
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
            (folder/'WiFi.h').write_text('#pragma once\n')
            (folder/'helpers/esp32/WiFiRadioPolicy.h').write_text('#pragma once\n')
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
