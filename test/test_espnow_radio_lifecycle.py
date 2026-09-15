"""Execute production ESP-NOW driver startup/shutdown with fault-injected SDK APIs."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
STUBS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <helpers/WirelessControl.h>
#include <helpers/ESPNowRawFragmentation.h>
#define ESP_IDF_VERSION_VAL(a,b,c) ((a)*10000+(b)*100+(c))
#define ESPNOW_DEBUG_PRINTLN(...) ((void)0)
#define portENTER_CRITICAL(x) ((void)0)
#define portEXIT_CRITICAL(x) ((void)0)
#define portMUX_INITIALIZER_UNLOCKED 0
using portMUX_TYPE=int;
using esp_err_t=int;
constexpr int ESP_OK=0,WIFI_IF_STA=0,WIFI_PROTOCOL_LR=8,WIFI_PHY_MODE_LR=1,WIFI_PHY_RATE_LORA_250K=2;
enum wifi_mode_t {WIFI_OFF=0,WIFI_STA=1,WIFI_AP=2,WIFI_AP_STA=3};
enum esp_now_send_status_t {ESP_NOW_SEND_SUCCESS=0,ESP_NOW_SEND_FAIL=1};
struct esp_now_send_info_t {};
struct esp_now_recv_info_t {const uint8_t* src_addr;};
struct esp_now_peer_info_t {uint8_t peer_addr[6];int channel,ifidx;bool encrypt;};
struct esp_now_rate_config_t {int phymode,rate;bool ersu,dcm;};
int failure=0,init_calls=0,deinit_calls=0,wake_refs=0,send_calls=0,power=0;
bool sdk_active=false;uint32_t millis(){return 1234;}
struct FakeWiFi {
 wifi_mode_t current=WIFI_OFF;bool autoreconnect=true;
 void persistent(bool){}
 wifi_mode_t getMode(){return current;}
 void setAutoReconnect(bool value){autoreconnect=value;}
 bool mode(wifi_mode_t value){if(failure==1)return false;current=value;return true;}
} WiFi;
namespace mesh {
 enum class RadioParamApplyResult {APPLIED};
 namespace wifi {
  int applyProtocolMask(int){return failure==2?-1:0;}
  int restoreEspNowChannel(){return failure==3?-1:0;}
 }
}
int esp_wifi_set_protocol(int,int){return failure==2?-1:0;}
int esp_now_init(){++init_calls;if(failure==4)return -1;sdk_active=true;return 0;}
int esp_now_deinit(){assert(sdk_active);sdk_active=false;++deinit_calls;return 0;}
int esp_wifi_force_wakeup_acquire(){if(failure==5)return -1;++wake_refs;return 0;}
int esp_wifi_force_wakeup_release(){assert(wake_refs==1);--wake_refs;return 0;}
int esp_wifi_set_max_tx_power(int value){if(failure==6)return -1;power=value;return 0;}
template<typename T> int esp_now_register_send_cb(T){return failure==7?-1:0;}
template<typename T> int esp_now_register_recv_cb(T){return failure==8?-1:0;}
int esp_now_unregister_send_cb(){return 0;}
int esp_now_unregister_recv_cb(){return 0;}
int esp_now_add_peer(esp_now_peer_info_t* peer){assert(peer->channel==0);return failure==9?-1:0;}
int esp_now_set_peer_rate_config(const uint8_t*,esp_now_rate_config_t*){return failure==10?-1:0;}
int esp_wifi_config_espnow_rate(int,int){return failure==10?-1:0;}
int esp_now_send(const uint8_t*,const uint8_t*,size_t){assert(sdk_active);++send_calls;return 0;}
void esp_efuse_mac_get_default(uint8_t* mac){memset(mac,1,6);}
'''

CHECKS = r'''
void deliver(const uint8_t* bytes,int len){
 static uint8_t mac[6]={1,2,3,4,5,6};
#if ESP_ARDUINO_VERSION_MAJOR >= 3
 esp_now_recv_info_t info{mac};OnDataRecv(&info,bytes,len);
#else
 OnDataRecv(mac,bytes,len);
#endif
}
int main(){
 ESPNOWRadio radio;
 assert(!radio.isEnabled());
 for(int stage=1;stage<=10;++stage){
   failure=stage;radio.init();assert(!radio.isEnabled());
   assert(!sdk_active&&wake_refs==0);
   assert(!radio.isInRecvMode()&&!radio.isSendComplete());
   radio.end();assert(wake_refs==0); // failed startup and repeated off are safe
 }
 failure=0;WiFi.current=WIFI_OFF;radio.init();
 assert(radio.isEnabled()&&sdk_active&&wake_refs==1&&!WiFi.autoreconnect);
 const int started=init_calls;radio.init();assert(init_calls==started&&wake_refs==1);
 assert(radio.setTxPower(13)&&power==52);
 uint8_t raw[255]={0x15,1,2,3},out[255];
 deliver(raw,4);assert(radio.recvRaw(out,sizeof(out))==4&&!memcmp(raw,out,4));
 // Stop in the middle of a fragmented TX: do not report success or send fragment 2.
 assert(radio.startSendRaw(raw,sizeof(raw))&&tx_second_pending);
 OnDataSent(nullptr,ESP_NOW_SEND_SUCCESS);
 radio.end();const int sent=send_calls;
 assert(!radio.isSendComplete()&&!radio.startSendRaw(raw,4)&&send_calls==sent);
 OnDataSent(nullptr,ESP_NOW_SEND_SUCCESS); // callback arriving while disabled
 deliver(raw,4);assert(radio.recvRaw(out,sizeof(out))==0);
 radio.end();assert(wake_refs==0&&!sdk_active);
 // Starting alongside a setup AP preserves it and restores the configured TX power.
 WiFi.current=WIFI_AP_STA;WiFi.autoreconnect=true;radio.init();
 assert(WiFi.current==WIFI_AP_STA&&WiFi.autoreconnect&&power==52);
 assert(!radio.isSendComplete()); // restarting before Dispatcher's timeout cannot fake TX success
 radio.onSendFinished(); // Dispatcher acknowledges the cancelled operation
 assert(radio.recvRaw(out,sizeof(out))==0&&radio.isInRecvMode());
 // Incomplete RX fragments cannot cross a stop/start boundary.
 mesh::espnow::ESPNowRawFrames frames;
 assert(mesh::espnow::encodeEspNowRawFrames(raw,sizeof(raw),frames));
 deliver(frames.data[0],frames.lengths[0]);assert(radio.recvRaw(out,sizeof(out))==0);
 radio.end();radio.init();
 deliver(frames.data[1],frames.lengths[1]);assert(radio.recvRaw(out,sizeof(out))==0);
 // A fresh two-fragment packet is received normally after restarting.
 deliver(frames.data[0],frames.lengths[0]);
 assert(radio.recvRaw(out,sizeof(out))==255&&!memcmp(raw,out,255));
 radio.end();assert(wake_refs==0&&!sdk_active);
}
'''

class EspNowLifecycleTests(unittest.TestCase):
    def test_sdk_failures_stop_and_restart(self):
        header=(ROOT/'src/helpers/esp32/ESPNOWRadio.h').read_text()
        source=(ROOT/'src/helpers/esp32/ESPNOWRadio.cpp').read_text()
        # Substitute only the abstract radio base and SDK includes; all driver
        # state, callbacks, queues, fragmentation, and method bodies are production.
        header=header[:header.index('#if ESPNOW_DEBUG_LOGGING')]
        header=re.sub(r'^\s*#(?:include|pragma).*$', '', header, flags=re.MULTILINE)
        header=header.replace(' : public mesh::Radio','').replace(' override','')
        source=re.sub(r'^\s*#include.*$', '', source, flags=re.MULTILINE)
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'driver.cpp').write_text(STUBS+header+source+CHECKS)
            for arduino,idf in [(2,40400),(3,50200),(3,50500)]:
                with self.subTest(arduino=arduino,idf=idf):
                    binary=folder/f'driver-{idf}'
                    subprocess.run([os.environ.get('CXX','g++'),'-std=c++17',
                                    '-I',str(ROOT/'src'),'-DMESH_PRIMARY_ESPNOW=1','-DMESH_ESPNOW_RADIO=1',
                                    f'-DESP_ARDUINO_VERSION_MAJOR={arduino}',f'-DESP_IDF_VERSION={idf}',
                                    str(folder/'driver.cpp'),'-o',str(binary)],check=True)
                    subprocess.run([str(binary)],check=True)

    def test_bridge_preserves_infrastructure_wifi(self):
        source=(ROOT/'src/helpers/bridges/ESPNowBridge.cpp').read_text()
        methods=method(source,'static void stopBridgeWiFiIfUnused(')+'\n'
        methods+='\n'.join(method(source,f'void ESPNowBridge::{name}(').replace('ESPNowBridge::','Bridge::')
                           for name in ('begin','end'))
        harness=STUBS.replace('assert(peer->channel==0)', 'assert(peer->channel==6)')+r'''
#include <helpers/bridges/ESPNowBridgeFormat.h>
#include <initializer_list>
#define BRIDGE_DEBUG_PRINTLN(...) ((void)0)
#define ESP_NOW_ETH_ALEN 6
#define WIFI_INIT_CONFIG_DEFAULT() 0
using wifi_init_config_t=int;
using wifi_second_chan_t=int;
constexpr int WIFI_STORAGE_RAM=0,WIFI_MODE_STA=1,WIFI_SECOND_CHAN_NONE=0;
constexpr int WIFI_PROTOCOL_11B=1,WIFI_PROTOCOL_11G=2,WIFI_PROTOCOL_11N=4;
int wifi_stops=0,wifi_starts=0;uint8_t wifi_channel=6;
int esp_wifi_init(wifi_init_config_t*){return 0;}
int esp_wifi_set_storage(int){return 0;}
int esp_wifi_set_mode(int){return 0;}
int esp_wifi_start(){++wifi_starts;return 0;}
int esp_wifi_stop(){++wifi_stops;return 0;}
int esp_wifi_deinit(){return 0;}
int esp_wifi_get_channel(uint8_t* channel,wifi_second_chan_t*){*channel=wifi_channel;return 0;}
int esp_wifi_set_channel(int channel,int){wifi_channel=channel;return 0;}
int esp_now_del_peer(const uint8_t*){return 0;}
namespace mesh {namespace wifi {
 std::atomic<uint8_t>& bridgeEspNowChannel(){static std::atomic<uint8_t> channel{0};return channel;}
}}
struct Backend : mesh::wireless::Backend {
 bool wifi=true;
 uint8_t available()const override{return mesh::wireless::WiFi|mesh::wireless::EspNow;}
 uint8_t enabled()const override{return wifi?mesh::wireless::WiFi:0;}
 uint8_t clients()const override{return mesh::wireless::Independent;}
 mesh::wireless::Result set(uint8_t,bool)override{return mesh::wireless::Result::Done;}
};
class Bridge {
public:
 struct Prefs {int bridge_format=mesh::bridge::ESPNOW_FORMAT_RAW;uint8_t bridge_channel=6;} prefs;
 Prefs* _prefs=&prefs;bool _initialized=false;
 int _active_format=0,_rx_mux=0,_rx_head=0,_rx_tail=0,_rx_count=0,_rx_dropped=0,_rx_dropped_reported=0;
 int _tx_mux=0,_tx_head=0,_tx_tail=0,_tx_count=0,_tx_waiting=0,_tx_callback_done=0;
 int _tx_callback_status=0,_tx_dropped=0,_tx_dropped_reported=0;
 mesh::espnow::ESPNowRawReassembler _raw_reassembler;
 struct QueuedTransmit {bool started=false;int packet=0;};
 static constexpr int TX_QUEUE_DEPTH=2;
 QueuedTransmit _tx_queue[TX_QUEUE_DEPTH];
 struct {void clear(const int*){}} _seen_packets;
 static void recv_cb(){}static void send_cb(){}
 void begin();void end();
};
'''+methods+r'''
int main(){
 Backend backend;mesh::wireless::control().begin(backend);Bridge bridge;
 // A channel conflict must leave the active WiFi connection untouched.
 wifi_channel=3;bridge.begin();assert(!bridge._initialized&&init_calls==0&&wifi_stops==0);
 wifi_channel=6;
 for(int stage:{2,4,7,8,9,10}){
   failure=stage;bridge.begin();assert(!bridge._initialized&&!sdk_active&&wifi_stops==0);
   bridge.end();assert(wifi_stops==0);
 }
 failure=0;bridge.begin();assert(bridge._initialized&&sdk_active&&wifi_starts==0);
 assert(mesh::wifi::bridgeEspNowChannel().load()==6);
 bridge.end();assert(!sdk_active&&wifi_stops==0&&mesh::wifi::bridgeEspNowChannel().load()==0);
 // When no infrastructure service owns WiFi, the bridge owns driver shutdown.
 backend.wifi=false;bridge.begin();assert(bridge._initialized&&wifi_starts==1);
 bridge.end();assert(!sdk_active&&wifi_stops==1);
 bridge.end();assert(wifi_stops==1);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'bridge.cpp').write_text(harness)
            binary=folder/'bridge'
            subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-I',str(ROOT/'src'),
                            '-DESP_IDF_VERSION=50200',str(folder/'bridge.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)

    def test_bridge_channel_constraint_tracks_runtime_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'esp_err.h').write_text('#pragma once\nusing esp_err_t=int;\nconstexpr int ESP_OK=0;\n')
            (folder/'esp_wifi.h').write_text(r'''
#pragma once
#include <cstdint>
#include <esp_err.h>
using wifi_interface_t=int;using wifi_second_chan_t=int;
constexpr int WIFI_IF_STA=0,WIFI_IF_AP=1,WIFI_SECOND_CHAN_NONE=0;
constexpr int WIFI_PROTOCOL_11B=1,WIFI_PROTOCOL_11G=2,WIFI_PROTOCOL_11N=4,WIFI_PROTOCOL_LR=8;
uint8_t current_channel=1,last_protocol=0;
int esp_wifi_set_protocol(int,uint8_t p){last_protocol=p;return 0;}
int esp_wifi_get_channel(uint8_t* p,int*){*p=current_channel;return 0;}
int esp_wifi_set_channel(uint8_t p,int){current_channel=p;return 0;}
''')
            (folder/'channel.cpp').write_text(r'''
#include <helpers/esp32/WiFiRadioPolicy.h>
#include <helpers/esp32/WiFiStationPolicy.h>
#include <cassert>
int main(){using namespace mesh::wifi;
 assert(!espNowChannelConstrained()&&stationChannelHint()==0&&stationScanChannel()==0);
 bridgeEspNowChannel().store(6);
 assert(espNowChannelConstrained()&&stationChannelHint()==6&&stationScanChannel()==6&&accessPointChannel()==6);
 assert(restoreEspNowChannel()==ESP_OK&&current_channel==6);
 applyProtocolMask(WIFI_IF_STA);assert(last_protocol==15);
 setStationAutoReconnect(true);assert(!WiFi.auto_reconnect);
 assert(beginStation("mesh","password")==WL_CONNECTED);
 assert(WiFi.scan_channel==6&&WiFi.join_channel==6);
 current_channel=11;assert(!enforceStationChannel()&&current_channel==6);
 bridgeEspNowChannel().store(0);current_channel=11;
 assert(!espNowChannelConstrained()&&stationChannelHint()==0&&stationScanChannel()==0);
 assert(restoreEspNowChannel()==ESP_OK&&current_channel==11);
 setStationAutoReconnect(true);assert(WiFi.auto_reconnect);
 assert(beginStation("mesh","password")==WL_CONNECTED&&WiFi.join_channel==0);
}
''')
            (folder/'WiFi.h').write_text(r'''
#pragma once
#include <esp_wifi.h>
#include <string>
enum wl_status_t {WL_IDLE_STATUS,WL_CONNECTED,WL_NO_SSID_AVAIL,WL_CONNECT_FAILED};
constexpr int WIFI_SCAN_RUNNING=-1;
struct {
 bool auto_reconnect=true;uint8_t scan_channel=0;int join_channel=0;
 wl_status_t state=WL_IDLE_STATUS;
 void setAutoReconnect(bool value){auto_reconnect=value;}
 wl_status_t begin(const char*,const char*,int channel=0,const uint8_t* =nullptr){
  join_channel=channel;state=WL_CONNECTED;return state;
 }
 int scanComplete(){return -2;}void scanDelete(){}
 int scanNetworks(bool,bool,bool,int,uint8_t channel,const char*,const uint8_t*){scan_channel=channel;return 2;}
 int channel(int index=-1){return index<0?current_channel:index==0?1:6;}
 std::string SSID(int){return "mesh";}int RSSI(int){return -50;}
 const uint8_t* BSSID(int){static uint8_t mac[6]={1};return mac;}
 wl_status_t status(){return state;}
 void disconnect(bool,bool){state=WL_IDLE_STATUS;}
} WiFi;
''')
            binary=folder/'channel'
            subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-DWITH_ESPNOW_BRIDGE=1',
                            '-I',str(folder),'-I',str(ROOT/'src'),str(folder/'channel.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)

if __name__=='__main__':
    unittest.main()
