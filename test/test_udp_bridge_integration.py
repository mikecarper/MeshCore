#!/usr/bin/env python3
"""Execute production repeater UDP lifecycle with shared station owners."""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import unittest
from cpp_source import body
from test_wifi_station_power import power_policy

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <new>
#include <initializer_list>
#include <helpers/UDPBridgePrefs.h>
#include <helpers/esp32/WiFiCoordinator.h>
#include <helpers/esp32/InfrastructureBridgeCoordinator.h>
#include <helpers/WirelessControl.h>
uint32_t now_ms=1000;
uint32_t millis(){return now_ms;}
bool millisHasNowPassed(uint32_t value){return static_cast<int32_t>(now_ms-value)>=0;}
enum {WIFI_OFF=0,WIFI_STA=1,WIFI_AP=2,WIFI_AP_STA=3,WL_IDLE_STATUS=0,WL_CONNECTED=3};
enum {WIFI_IF_STA=0,ESP_OK=0,WIFI_PS_NONE=0,WIFI_PS_MIN_MODEM=1,WIFI_PS_MAX_MODEM=2};
using wifi_ps_type_t=int;
using wifi_power_t=int;
using esp_err_t=int;
constexpr int ESP_FAIL=-1,ESP_ERR_INVALID_STATE=-2,WIFI_POWER_11dBm=44,WIFI_POWER_19_5dBm=78;
int applied_ps=-1,applied_tx=-1;
struct {
 int current=WIFI_OFF,state=WL_IDLE_STATUS,begins=0,modes=0; uint32_t ip=0,mask=0x00ffffff;
 int getMode() const{return current;}
 int status()const{return state;}
 uint32_t localIP()const{return ip;}
 uint32_t subnetMask()const{return mask;}
 void persistent(bool){}
 bool mode(int value){++modes;current=value;return true;}
 bool setSleep(int){return true;}
 bool setTxPower(int value){applied_tx=value;return true;}
} WiFi;
using wifi_mode_t=int;
constexpr int WIFI_MODE_NULL=0,WIFI_MODE_STA=1;
int sdk_override=-1;
int esp_wifi_get_mode(wifi_mode_t*value){*value=sdk_override<0?WiFi.current:sdk_override;return ESP_OK;}
int esp_wifi_set_ps(int mode){applied_ps=mode;return ESP_OK;}
int esp_wifi_set_max_tx_power(int8_t value){applied_tx=value;return ESP_OK;}
int esp_wifi_get_max_tx_power(int8_t* value){*value=static_cast<int8_t>(applied_tx);return ESP_OK;}
@POWER_POLICY@
namespace mesh { namespace wifi {
 constexpr uint8_t kDefaultPowerSave=1,kPowerSaveNone=1,kPowerSaveMax=2;
 struct Credentials {char ssid[32]="test";char password[65]="";uint8_t power_save=1;};
 enum class CredentialState {Ready,Absent};
 bool have_credentials=true;
 CredentialState resolveCredentials(Credentials&,const char* =nullptr,const char* =nullptr,uint8_t=1){
   return have_credentials?CredentialState::Ready:CredentialState::Absent;
 }
 bool stationMutationAllowed(){return !(WiFi.current&WIFI_AP);}
 int applyProtocolMask(int){return ESP_OK;}
 void setStationAutoReconnect(bool){}
 void beginStation(const char*,const char*){++WiFi.begins;}
 uint8_t effectivePowerSave(uint8_t value,bool,bool){return value;}
 bool espNowChannelConstrained(){return false;}
}}
struct NodePrefs {char bridge_secret[16]="test-secret";};
struct FakeSettings {
 mesh::bridge::udp::Prefs data;
 bool health=true,save_ok=true;unsigned writes=0;
 bool healthy()const{return health;}
 const mesh::bridge::udp::Prefs& prefs()const{return data;}
 bool setPrefs(const mesh::bridge::udp::Prefs& value){++writes;if(!save_ok)return false;data=value;return true;}
};
struct Board {bool ota=false;bool isOTAUpdateRunning()const{return ota;}};
struct ObserverPrefs {const char*wifi_ssid="test";const char*wifi_password="";uint8_t wifi_power_save=1;};
struct CLI {Board board;ObserverPrefs observer;Board*getBoard(){return &board;}const ObserverPrefs*getObserverPrefs(){return &observer;}};
struct MQTT {bool running=false,stopping=false;bool isRunning()const{return running;}bool isStopping()const{return stopping;}};
struct WebConfig {bool allowed=true;int prepares=0;bool prepareStationOwner(){++prepares;return allowed;}};
struct UDPBridge {
 static int allocations;bool running=false;int loops=0,closes=0;
 uint32_t address=0,mask=0;
 UDPBridge(NodePrefs*,void*,void*,const mesh::bridge::udp::Prefs*){++allocations;}
 ~UDPBridge(){--allocations;}
 bool isRunning()const{return running;}
 void serviceNetwork(bool ready,uint32_t ip=0,uint32_t netmask=0){running=ready;address=ip;mask=netmask;if(!ready)++closes;}
 void loop(){assert(running);++loops;}
 void end(){running=false;++closes;}
 bool sendTest(uint8_t,uint32_t){return running;}
 void resetStats(){}
 void formatStatus(char*out,size_t cap){snprintf(out,cap,"> ready ip=10.0.0.1");}
 void formatStats(const char*,char*,size_t){}
}; int UDPBridge::allocations=0;
struct MyMesh {
 NodePrefs _prefs;void* _mgr=nullptr;CLI _cli;FakeSettings udp_bridge_settings;
 UDPBridge*udp_bridge=nullptr;uint32_t udp_station_retry_at=0;bool udp_network_paused=false;
 MQTT* mqtt_bridge=nullptr;bool web_active=false,web_stopping=false;
 WebConfig* _webconfig=nullptr;
 ~MyMesh(){delete udp_bridge;}
 bool isWebConfigActive(){return web_active;}
 bool isWebConfigStopping(){return web_stopping;}
 void*getRTCClock(){return nullptr;}
 void configureBridgeFilter(UDPBridge*){}
 bool ensureUDPBridge();bool isUDPBridgeNetworkEnabled()const;
 bool setUDPBridgeNetworkState(bool);bool setUDPBridgePrefs(const mesh::bridge::udp::Prefs&);
 void serviceUDPBridge();void formatUDPBridgeStatus(char*,size_t);
};
@METHODS@
struct Backend:mesh::wireless::Backend {
 uint8_t available()const override{return mesh::wireless::WiFi;}
 uint8_t enabled()const override{return mesh::wireless::WiFi;}
 uint8_t clients()const override{return mesh::wireless::Independent;}
 mesh::wireless::Result set(uint8_t,bool)override{return mesh::wireless::Result::Done;}
};
int main(){
 MyMesh node;
 char status[157];node.formatUDPBridgeStatus(status,sizeof(status));assert(strstr(status,"off ip=0.0.0.0"));
 node.serviceUDPBridge();
 assert(!node.udp_bridge&&!mesh::wifi::coordinator().hasStationLease(mesh::wifi::StationOwner::UdpBridge)&&WiFi.begins==0);
 auto prefs=node.udp_bridge_settings.data;prefs.enabled=1;
 for(unsigned i=0;i<8;++i){prefs.peers[i].ip[0]=10;prefs.peers[i].ip[3]=i+2;prefs.peers[i].port=4404;}
 assert(node.setUDPBridgePrefs(prefs));
 assert(node.udp_bridge&&UDPBridge::allocations==1&&WiFi.begins==1&&WiFi.current==WIFI_STA);
 assert(applied_ps==WIFI_PS_NONE&&applied_tx==78);
 assert(!node.udp_bridge->running&&mesh::wifi::coordinator().hasStationLease(mesh::wifi::StationOwner::UdpBridge));
 WiFi.state=WL_CONNECTED;WiFi.ip=0x0100000a;
 node.serviceUDPBridge();
 node.formatUDPBridgeStatus(status,sizeof(status));assert(strstr(status,"ready ip=10.0.0.1"));
 assert(node.udp_bridge->running&&node.udp_bridge->loops==1&&WiFi.begins==1);
 assert(node.udp_bridge->address==WiFi.ip&&node.udp_bridge->mask==WiFi.mask);
 for(int mode:{WIFI_AP,WIFI_AP_STA}){
   WiFi.current=mode;const int starts=WiFi.begins,modes=WiFi.modes;
   now_ms+=16000;node.serviceUDPBridge();
   assert(!node.udp_bridge->running&&WiFi.begins==starts&&WiFi.modes==modes);
 }
 WiFi.current=WIFI_STA;sdk_override=WIFI_AP_STA;node.serviceUDPBridge();assert(!node.udp_bridge->running);
 sdk_override=-1;WiFi.ip=0;now_ms+=16000;node.serviceUDPBridge();
 assert(!node.udp_bridge->running); // Association alone is not a usable IP.
 WiFi.ip=0x0100000a;node.serviceUDPBridge();assert(node.udp_bridge->running);
 node._cli.board.ota=true;node.serviceUDPBridge();
 assert(!node.udp_bridge->running&&!mesh::wifi::coordinator().hasStationLease(mesh::wifi::StationOwner::UdpBridge));
 node._cli.board.ota=false;node.serviceUDPBridge();assert(node.udp_bridge->running);
 assert(node.setUDPBridgeNetworkState(false));node.serviceUDPBridge();assert(!node.udp_bridge->running);
 assert(node.setUDPBridgeNetworkState(true)&&node.udp_bridge->running);
 WiFi.state=WL_IDLE_STATUS;now_ms+=16000;const int before_owner=WiFi.begins;
#ifdef WITH_WEBCONFIG
 WebConfig portal;node._webconfig=&portal;portal.allowed=false;
 node.serviceUDPBridge();assert(WiFi.begins==before_owner&&!node.udp_bridge->running);
 assert(portal.prepares==1);
 portal.allowed=true;node.serviceUDPBridge();assert(WiFi.begins==before_owner+1);
 node._webconfig=nullptr;now_ms+=16000;
#endif
 const int before_mqtt=WiFi.begins;
#ifdef WITH_MQTT_BRIDGE
 MQTT mqtt;node.mqtt_bridge=&mqtt;mqtt.running=true;node.serviceUDPBridge();
 assert(WiFi.begins==before_mqtt&&!node.udp_bridge->running);
 mqtt.running=false;mqtt.stopping=true;node.serviceUDPBridge();assert(WiFi.begins==before_mqtt);
 mqtt.stopping=false;
#endif
 node.serviceUDPBridge();assert(WiFi.begins==before_mqtt+1);
 WiFi.state=WL_CONNECTED;node.serviceUDPBridge();assert(node.udp_bridge->running);
 assert(mesh::wifi::coordinator().hasStationLease(mesh::wifi::StationOwner::UdpBridge));
 Backend backend;auto& control=mesh::wireless::control();control.begin(backend);char reply[160];
 assert(control.handle("set wifi off force",reply,sizeof(reply),now_ms,mesh::wireless::Independent));
 node.serviceUDPBridge();assert(!node.udp_bridge->running); // Honor off before reply-drain grace ends.
 node.formatUDPBridgeStatus(status,sizeof(status));assert(strstr(status,"wifi-paused"));
 now_ms+=250;control.service(now_ms);assert(control.blocked(mesh::wireless::WiFi));
 node.serviceUDPBridge();assert(!node.udp_bridge->running&&!mesh::wifi::coordinator().hasStationLease(mesh::wifi::StationOwner::UdpBridge));
 node.setUDPBridgeNetworkState(true);assert(!node.udp_bridge->running); // No silent master-off bypass.
 assert(control.handle("set wifi on",reply,sizeof(reply),now_ms,mesh::wireless::Independent));
 now_ms+=250;control.service(now_ms);node.serviceUDPBridge();assert(node.udp_bridge->running);
 assert(control.handle("set 2.4ghz off force",reply,sizeof(reply),now_ms,mesh::wireless::Independent));
 node.serviceUDPBridge();assert(!node.udp_bridge->running&&control.masterOff());
 now_ms+=250;control.service(now_ms);
 assert(control.handle("set 2.4ghz on",reply,sizeof(reply),now_ms,mesh::wireless::Independent));
 now_ms+=250;control.service(now_ms);node.serviceUDPBridge();assert(node.udp_bridge->running);
 node._prefs.bridge_secret[0]=0;node.formatUDPBridgeStatus(status,sizeof(status));assert(strstr(status,"no-secret"));
 node.serviceUDPBridge();assert(!node.udp_bridge->running);
 strcpy(node._prefs.bridge_secret,"test-secret");node.serviceUDPBridge();assert(node.udp_bridge->running);
 const auto before=node.udp_bridge_settings.data;node.udp_bridge_settings.save_ok=false;
 auto failed=before;failed.port=1234;assert(!node.setUDPBridgePrefs(failed));
 assert(node.udp_bridge_settings.data.port==before.port&&node.udp_bridge->running);
 node.udp_bridge_settings.save_ok=true;prefs.enabled=0;
 assert(node.setUDPBridgePrefs(prefs)&&!node.udp_bridge&&UDPBridge::allocations==0);
 assert(!mesh::wifi::coordinator().hasStationLease(mesh::wifi::StationOwner::UdpBridge));
}
'''

class UDPBridgeIntegrationTest(unittest.TestCase):
    def test_webconfig_station_ownership_uses_live_udp_and_mqtt_intent(self):
        method = body((ROOT / 'examples/simple_repeater/MyMesh.cpp').read_text(encoding='ascii'),
                      'bool MyMesh::webConfigOwnsWiFi(')
        fixture = r'''
#include <cassert>
#include <helpers/esp32/InfrastructureBridgeCoordinator.h>
struct Worker {
  bool running=false,stopping=false;
  bool isRunning()const{return running;}
  bool isStopping()const{return stopping;}
};
struct MyMesh {
  Worker* mqtt_bridge=nullptr; bool udp=false;
  bool isUDPBridgeNetworkEnabled()const{return udp;}
  bool webConfigOwnsWiFi()const;
};
@METHOD@
int main(){
  MyMesh node;Worker mqtt;
  for(int udp=0;udp<2;++udp)for(int state=0;state<4;++state){
    node.udp=udp;node.mqtt_bridge=state?&mqtt:nullptr;
    mqtt.running=state==2;mqtt.stopping=state==3;
    bool expected=true;
#ifdef WITH_MQTT_BRIDGE
    expected=state<2;
#endif
#ifdef WITH_UDP_BRIDGE
    expected=expected&&!udp;
#endif
    assert(node.webConfigOwnsWiFi()==expected);
  }
}
'''.replace('@METHOD@', method)
        for flags in ((), ('WITH_MQTT_BRIDGE',), ('WITH_UDP_BRIDGE',),
                      ('WITH_MQTT_BRIDGE', 'WITH_UDP_BRIDGE')):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory(prefix='udp-web-owner-') as tmp:
                path = Path(tmp)
                (path / 'test.cpp').write_text(fixture, encoding='ascii')
                binary = path / 'test'
                subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    *(['-fno-pie', '-no-pie'] if sys.platform.startswith('linux') else []),
                    '-I', str(ROOT / 'src'),
                    *['-D' + flag + '=1' for flag in flags], str(path / 'test.cpp'), '-o', str(binary)], check=True)
                subprocess.run([str(binary)], check=True)

    def test_wireless_backend_can_query_public_espnow_state_in_every_udp_owner_mix(self):
        header = (ROOT / 'examples/simple_repeater/MyMesh.h').read_text(encoding='ascii')
        signature = 'bool isEspNowBridgeRunning() override'
        offset = header.index(signature)
        # Preserve the actual member access from the production class. A
        # private override compiles inside a role fixture but fails when the
        # external wireless backend calls it, as the Indicator build exposed.
        labels = re.findall(r'^\s*(public|protected|private):', header[:offset], re.M)
        access = labels[-1] if labels else 'private'
        method = body(header, signature)
        backend = body((ROOT / 'examples/InfrastructureWireless.h').read_text(encoding='ascii'),
                       'uint8_t enabled() const override')
        fixture = r'''
#include <cassert>
#include <cstdint>
#include <helpers/WirelessControl.h>
struct Base { virtual bool isEspNowBridgeRunning() { return false; } };
struct Bridge { bool running; bool isRunning() const { return running; } };
class MyMesh : public Base {
  Bridge bridge{false}, espnow_bridge{true};
public:
  bool isUDPBridgeNetworkEnabled() const { return true; }
  bool isMqttBridgeRunning() const { return false; }
  bool isMqttBridgeStartPending() const { return false; }
@ACCESS@:
@METHOD@
public:
  void flip() { bridge.running = true; espnow_bridge.running = false; }
} the_mesh;
struct BackendBase { virtual uint8_t enabled() const = 0; };
struct Backend : BackendBase {
@BACKEND@
};
int main() {
  Backend backend;
  assert(backend.enabled() & mesh::wireless::WiFi);
#if defined(WITH_MQTT_BRIDGE) || defined(WITH_RS232_BRIDGE)
  assert(backend.enabled() & mesh::wireless::EspNow);
  the_mesh.flip();
  assert(!(backend.enabled() & mesh::wireless::EspNow));
#else
  assert(!(backend.enabled() & mesh::wireless::EspNow));
  the_mesh.flip();
  assert(backend.enabled() & mesh::wireless::EspNow);
#endif
}
'''.replace('@ACCESS@', access).replace('@METHOD@', method).replace('@BACKEND@', backend)
        for flags in ((), ('WITH_MQTT_BRIDGE',), ('WITH_RS232_BRIDGE',),
                      ('WITH_MQTT_BRIDGE', 'WITH_RS232_BRIDGE')):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory(prefix='udp-public-api-') as tmp:
                path = Path(tmp)
                (path / 'test.cpp').write_text(fixture, encoding='ascii')
                binary = path / 'test'
                subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++11', '-Wall', '-Wextra', '-Werror',
                    '-DWITH_UDP_BRIDGE=1', '-DWITH_ESPNOW_BRIDGE=1',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    *(['-fno-pie', '-no-pie'] if sys.platform.startswith('linux') else []),
                    '-I', str(ROOT / 'src'), *['-D' + flag + '=1' for flag in flags],
                    str(path / 'test.cpp'), '-o', str(binary)], check=True)
                subprocess.run([str(binary)], check=True)

    def test_wireless_master_restores_udp_station_without_starting_setup_ap(self):
        backend=body((ROOT/'examples/InfrastructureWireless.h').read_text(),
                     'class InfrastructureWirelessBackend')+';'
        fixture=r'''
#include <cassert>
#include <cstdint>
#include <vector>
#include <helpers/WirelessControl.h>
enum {WIFI_OFF=0,WIFI_STA=1,WIFI_IF_STA=0};
std::vector<int> events;
struct {bool ota=false;bool isOTAUpdateRunning()const{return ota;}
 bool stopOTAUpdate(char*){ota=false;return true;}} board;
struct {int mode_=WIFI_STA;void setAutoReconnect(bool){}
 void disconnect(bool,bool){events.push_back(2);}
 void mode(int value){mode_=value;events.push_back(value==WIFI_OFF?3:4);}} WiFi;
namespace mesh {namespace wifi {
void applyProtocolMask(int){} void restoreEspNowChannel(){}
bool infrastructureWiFiEnabled(){return true;}
bool saveInfrastructureWiFiEnabled(bool){return true;}
}}
struct AdapterNode {bool bridge_enabled=false;};
struct AdapterObserver {const char* wifi_ssid="";};
struct WebConfigServer {
 static bool hasConfiguredWiFi(const AdapterObserver* p){return p->wifi_ssid[0]!=0;}
 static bool loadEnabled(bool){return false;}
};
struct {
 AdapterNode node;AdapterObserver observer;
 AdapterNode* getNodePrefs(){return &node;}
 AdapterObserver* getObserverPrefs(){return &observer;}
 bool paused=false,configured=true,mqtt=false,web=false;unsigned web_starts=0;
 bool isUDPBridgeNetworkEnabled()const{return configured&&!paused;}
 bool isUDPBridgeConfigured()const{return configured;}
 bool setUDPBridgeNetworkState(bool enabled){paused=!enabled;events.push_back(enabled?5:1);if(enabled)WiFi.mode(WIFI_STA);return true;}
 bool isMqttBridgeRunning()const{return mqtt;}
 bool isMqttBridgeStartPending()const{return false;}
 bool setMqttBridgeState(bool value){mqtt=value;return true;}
 bool isWebConfigActive()const{return web;}
 bool isWebConfigStopping()const{return false;}
 bool hasWirelessNetworkClient()const{return false;}
 bool stopWebConfig(char*){web=false;return true;}
 bool startWebConfig(bool,char*){++web_starts;web=true;return true;}
} the_mesh;
@BACKEND@
int main(){
 InfrastructureWirelessBackend backend;
 assert(backend.enabled()&mesh::wireless::WiFi);
 assert(backend.set(mesh::wireless::WiFi,false)==mesh::wireless::Result::Done);
 assert(the_mesh.paused&&WiFi.mode_==WIFI_OFF&&events.front()==1);
 assert(backend.set(mesh::wireless::WiFi,true)==mesh::wireless::Result::Done);
 assert(!the_mesh.paused&&WiFi.mode_==WIFI_STA&&the_mesh.web_starts==0);
 // An explicit first WiFi-on with configured UDP must not launch a setup AP.
 InfrastructureWirelessBackend initial;
 assert(initial.set(mesh::wireless::WiFi,true)==mesh::wireless::Result::Done);
 assert(the_mesh.web_starts==0);
}
'''.replace('@BACKEND@',backend)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);(path/'backend.cpp').write_text(fixture)
            binary=path/'backend'
            subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-DESP32=1','-DWITH_UDP_BRIDGE=1',
                '-DWITH_WEBCONFIG=1','-DWITH_MQTT_BRIDGE=1','-fsanitize=address,undefined',
                '-fno-omit-frame-pointer','-fno-pie','-no-pie','-I',str(ROOT/'src'),
                str(path/'backend.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)

    def test_station_owners_master_off_reconnect_and_broker_independence(self):
        source=(ROOT/'examples/simple_repeater/MyMesh.cpp').read_text()
        methods='\n'.join(body(source,signature) for signature in (
            'bool MyMesh::ensureUDPBridge(', 'bool MyMesh::isUDPBridgeNetworkEnabled(',
            'bool MyMesh::setUDPBridgeNetworkState(', 'bool MyMesh::setUDPBridgePrefs(',
            'void MyMesh::serviceUDPBridge(', 'void MyMesh::formatUDPBridgeStatus('))
        for flags in ((),('WITH_MQTT_BRIDGE',),('WITH_WEBCONFIG',),('WITH_MQTT_BRIDGE','WITH_WEBCONFIG')):
            with self.subTest(flags=flags),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp);(path/'test.cpp').write_text(HARNESS.replace('@METHODS@',methods)
                    .replace('@POWER_POLICY@',power_policy()))
                binary=path/'test'
                subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer','-fno-pie','-no-pie','-I',str(ROOT/'src'),
                    *['-D'+flag+'=1' for flag in flags],str(path/'test.cpp'),'-o',str(binary)],check=True)
                subprocess.run([str(binary)],check=True)

if __name__=='__main__':unittest.main()
