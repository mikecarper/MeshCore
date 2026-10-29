// Model the pinned ESP-IDF 4.4.7 shared LR flag, not independent STA/AP masks.
#pragma once
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/WirelessControl.h>
#include <helpers/ESPNowRawFragmentation.h>
#include <helpers/bridges/ESPNowBridgeFormat.h>
#define ESP_IDF_VERSION_VAL(a,b,c) ((a)*10000+(b)*100+(c))
#define ESPNOW_DEBUG_PRINTLN(...) ((void)0)
#define BRIDGE_DEBUG_PRINTLN(...) ((void)0)
#define portENTER_CRITICAL(x) ((void)0)
#define portEXIT_CRITICAL(x) ((void)0)
#define portMUX_INITIALIZER_UNLOCKED 0
#define ESP_NOW_ETH_ALEN 6
#define WIFI_INIT_CONFIG_DEFAULT() 0
using portMUX_TYPE=int;
using esp_err_t=int;
using wifi_interface_t=int;
using wifi_second_chan_t=int;
using wifi_init_config_t=int;
enum wifi_mode_t {WIFI_OFF=0,WIFI_STA=1,WIFI_AP=2,WIFI_AP_STA=3};
constexpr wifi_mode_t WIFI_MODE_NULL=WIFI_OFF,WIFI_MODE_STA=WIFI_STA,WIFI_MODE_AP=WIFI_AP;
constexpr int ESP_OK=0,ESP_ERR_INVALID_STATE=-2,ESP_ERR_WIFI_NOT_INIT=-3;
constexpr int WIFI_IF_STA=0,WIFI_IF_AP=1,WIFI_STORAGE_RAM=0,WIFI_SECOND_CHAN_NONE=0;
constexpr int WIFI_PROTOCOL_11B=1,WIFI_PROTOCOL_11G=2,WIFI_PROTOCOL_11N=4,WIFI_PROTOCOL_LR=8;
constexpr int WIFI_PHY_MODE_LR=1,WIFI_PHY_RATE_LORA_250K=2,WIFI_SCAN_RUNNING=-1;
enum wl_status_t {WL_IDLE_STATUS,WL_CONNECTED,WL_NO_SSID_AVAIL,WL_CONNECT_FAILED,WL_DISCONNECTED};
enum esp_now_send_status_t {ESP_NOW_SEND_SUCCESS=0,ESP_NOW_SEND_FAIL=1};
enum wifi_ps_type_t {WIFI_PS_NONE=0,WIFI_PS_MIN_MODEM=1,WIFI_PS_MAX_MODEM=2};
int esp_wifi_set_ps(wifi_ps_type_t){return ESP_OK;}
namespace mesh {namespace bridge {
bool copyEspNowPeer(uint8_t* address){memset(address,255,6);return true;}
}}
struct esp_now_send_info_t {};
struct esp_now_recv_info_t {const uint8_t* src_addr;};
struct esp_now_peer_info_t {uint8_t peer_addr[6];int channel,ifidx;bool encrypt;};
struct esp_now_rate_config_t {int phymode,rate;bool ersu,dcm;};
namespace mesh {
@RADIO_APPLY_ENUM@
}
int failure=0,mode_reads=0,station_writes=0,ap_writes=0,storage_writes=0;
int init_calls=0,deinit_calls=0,wake_refs=0,send_calls=0,power=0,rate_calls=0;
bool driver_initialized=true,sdk_active=false,storage_ram=false,lr=false,nvs_lr=false;
bool ap_during_station_write=false;
wifi_mode_t sdk_mode=WIFI_STA;
uint8_t sta_phy=7,ap_phy=7,current_channel=1;
std::vector<int> protocol_order;
uint32_t millis(){return 1234;}
int esp_wifi_get_mode(wifi_mode_t* mode){
 ++mode_reads;if(failure==1 || (failure==2&&mode_reads==2))return -1;
 if(!driver_initialized)return ESP_ERR_WIFI_NOT_INIT;
 *mode=sdk_mode;return ESP_OK;
}
int esp_wifi_set_storage(int){++storage_writes;if(failure==3)return -1;storage_ram=true;return ESP_OK;}
int esp_wifi_set_protocol(int interface_id,uint8_t mask){
 if((interface_id==WIFI_IF_STA&&failure==4)||(interface_id==WIFI_IF_AP&&failure==5))return -1;
 if(interface_id==WIFI_IF_STA){++station_writes;sta_phy=mask&7;}else{++ap_writes;ap_phy=mask&7;}
 lr=(mask&WIFI_PROTOCOL_LR)!=0;
 if(!storage_ram)nvs_lr=lr;
 protocol_order.push_back(interface_id);
 if(interface_id==WIFI_IF_STA&&ap_during_station_write){sdk_mode=WIFI_AP_STA;ap_during_station_write=false;}
 return ESP_OK;
}
int esp_wifi_get_protocol(int interface_id,uint8_t* mask){
 if(failure==6)return -1;
 *mask=(interface_id==WIFI_IF_STA?sta_phy:ap_phy)|(lr?8:0);
 if(failure==7)*mask|=8;
 return ESP_OK;
}
int esp_wifi_get_channel(uint8_t* c,wifi_second_chan_t*){*c=current_channel;return ESP_OK;}
int esp_wifi_set_channel(uint8_t c,int){current_channel=c;return ESP_OK;}
int esp_wifi_init(wifi_init_config_t*){driver_initialized=true;return ESP_OK;}
int esp_wifi_set_mode(wifi_mode_t mode){sdk_mode=mode;return ESP_OK;}
int esp_wifi_start(){return ESP_OK;}
int esp_wifi_stop(){return ESP_OK;}
int esp_wifi_deinit(){driver_initialized=false;return ESP_OK;}
struct FakeWiFi {
 bool facade_started=true,autoreconnect=true;
 wifi_ps_type_t cached_sleep=WIFI_PS_MIN_MODEM;
 wifi_ps_type_t getSleep(){return cached_sleep;}
 bool setSleep(wifi_ps_type_t value){if(value==cached_sleep)return false;cached_sleep=value;return true;}
 wl_status_t state=WL_DISCONNECTED;int join_channel=0,scan_channel=0;
 void persistent(bool){} // A late Arduino preference does not switch SDK storage.
 wifi_mode_t getMode(){return facade_started?sdk_mode:WIFI_OFF;}
 int channel(int index=-1){return index<0?current_channel:1;}
 void setAutoReconnect(bool value){autoreconnect=value;}
 bool mode(wifi_mode_t value){if(failure==8)return false;driver_initialized=true;facade_started=true;sdk_mode=value;return true;}
 bool softAPdisconnect(bool){sdk_mode=WIFI_STA;return true;}
 struct IP {struct Text {const char* c_str(){return "192.0.2.1";}};Text toString(){return {};}};
 IP localIP(){return {};}
 wl_status_t status(){return state;}
 wl_status_t begin(const char*,const char*,int channel=0,const uint8_t* =nullptr){join_channel=channel;state=WL_CONNECTED;return state;}
 int scanComplete(){return -2;}void scanDelete(){}
 int scanNetworks(bool,bool,bool,int,int channel,const char*,const uint8_t*){scan_channel=channel;return 1;}
 std::string SSID(int){return "mesh";}int RSSI(int){return -50;}
 const uint8_t* BSSID(int){static uint8_t mac[6]={1};return mac;}
 void disconnect(bool,bool){state=WL_DISCONNECTED;}
} WiFi;
int esp_now_init(){++init_calls;sdk_active=true;return ESP_OK;}
int esp_now_deinit(){assert(sdk_active);sdk_active=false;++deinit_calls;return ESP_OK;}
int esp_wifi_force_wakeup_acquire(){++wake_refs;return ESP_OK;}
int esp_wifi_force_wakeup_release(){assert(wake_refs==1);--wake_refs;return ESP_OK;}
int esp_wifi_set_max_tx_power(int value){power=value;return ESP_OK;}
template<typename T> int esp_now_register_send_cb(T){return ESP_OK;}
template<typename T> int esp_now_register_recv_cb(T){return ESP_OK;}
int esp_now_unregister_send_cb(){return ESP_OK;}
int esp_now_unregister_recv_cb(){return ESP_OK;}
int esp_now_add_peer(esp_now_peer_info_t*){return ESP_OK;}
int esp_now_del_peer(const uint8_t*){return ESP_OK;}
int esp_now_set_peer_rate_config(const uint8_t*,esp_now_rate_config_t*){++rate_calls;return ESP_OK;}
int esp_wifi_config_espnow_rate(int,int){++rate_calls;return ESP_OK;}
int esp_now_send(const uint8_t*,const uint8_t*,size_t){assert(sdk_active);++send_calls;return ESP_OK;}
void esp_efuse_mac_get_default(uint8_t* mac){memset(mac,1,6);}
class Preferences {
public:
 bool begin(const char*,bool){return true;}bool isKey(const char*){return false;}
 uint8_t getUChar(const char*,uint8_t fallback){return fallback;}
 size_t putUChar(const char*,uint8_t){return 1;}void end(){}
};
struct Backend : mesh::wireless::Backend {
 bool wifi=true;
 uint8_t available()const override{return mesh::wireless::WiFi|mesh::wireless::EspNow;}
 uint8_t enabled()const override{return wifi?mesh::wireless::WiFi:0;}
 uint8_t clients()const override{return mesh::wireless::Independent;}
 mesh::wireless::Result set(uint8_t,bool)override{return mesh::wireless::Result::Done;}
};
