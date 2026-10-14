#include <atomic>
#include <cassert>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>
#include "helpers/AlertFaultPolicy.h"
#include "helpers/MQTTConnectionPolicy.h"
#include "helpers/MQTTTransportHealth.h"
#include "helpers/WiFiPowerSave.h"
#include "helpers/WiFiNetworkPowerPolicy.h"
#define MQTT_DEBUG_PRINTLN(...) ((void)0)
#define pdMS_TO_TICKS(value) (value)
using TickType_t=uint32_t;
using wifi_mode_t=int;
using esp_err_t=int;
using wl_status_t=int;
using wifi_ps_type_t=int;
constexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_WIFI_NOT_INIT=-2;
constexpr int WIFI_MODE_NULL=0,WIFI_STA=1,WIFI_AP=2,WIFI_AP_STA=3,WIFI_MODE_AP=2;
constexpr int WL_CONNECTED=3,WL_DISCONNECTED=0,RUNTIME_MQTT_SLOTS=2;
constexpr int WIFI_PS_NONE=0,WIFI_PS_MIN_MODEM=1,WIFI_PS_MAX_MODEM=2,WIFI_POWER_11dBm=44;
int sdk_mode=WIFI_STA,mode_reads=0,enable_calls=0,mode_writes=0;
int reconnect_writes=0,autoconnect_writes=0,event_registers=0,station_begins=0;
int station_disconnects=0,channel_checks=0,power_writes=0,tx_power_writes=0;
int read_failure_at=0;
bool driver_initialized=true,facade_started=true,enable_failure=false,race_ap=false;
uint32_t now_ms=0;std::function<void()> tick;
int esp_wifi_get_mode(wifi_mode_t* mode){
 ++mode_reads;if(mode_reads==read_failure_at)return ESP_FAIL;
 if(!driver_initialized)return ESP_ERR_WIFI_NOT_INIT;
 *mode=sdk_mode;return ESP_OK;
}
uint32_t millis(){return now_ms;}
TickType_t xTaskGetTickCount(){return now_ms;}
void vTaskDelay(TickType_t value){now_ms+=value;if(tick)tick();}
int esp_wifi_set_ps(int){++power_writes;return ESP_OK;}
using WiFiEvent_t=int;
constexpr int ARDUINO_EVENT_WIFI_STA_GOT_IP=1,ARDUINO_EVENT_WIFI_STA_DISCONNECTED=2;
struct WiFiEventInfo_t{
 struct{struct{struct{uint32_t addr=1;}ip;}ip_info;}got_ip;
 struct{uint8_t reason=1;}wifi_sta_disconnected;
};
struct WiFiClass{
 int state=WL_CONNECTED;
 // Match Arduino-ESP32 2.0.17 enableSTA's getMode snapshot plus mode call.
 // This is not an atomic SDK OR. An AP observed before the snapshot survives;
 // stable AP deferral must use the SDK guard even with a stale OFF facade.
 int getMode(){if(race_ap)sdk_mode|=WIFI_AP;return facade_started?sdk_mode:WIFI_MODE_NULL;}
 bool enableSTA(bool enable){
  assert(enable);++enable_calls;if(enable_failure)return false;
  const wifi_mode_t currentMode=getMode();
  const bool isEnabled=(currentMode&WIFI_STA)!=0;
  if(isEnabled!=enable)return mode(enable?(currentMode|WIFI_STA):(currentMode&~WIFI_STA));
  return true;
 }
 bool mode(int value){++mode_writes;driver_initialized=facade_started=true;sdk_mode=value;return true;}
 void setAutoConnect(bool){++autoconnect_writes;}
 template<class T>void onEvent(T){++event_registers;}
 int status(){return state;}
 void disconnect(){++station_disconnects;state=WL_DISCONNECTED;}
 void setTxPower(int){++tx_power_writes;}
}WiFi;
namespace mesh{namespace wifi{
 @STATION_GUARD@
 constexpr bool kPrimaryEspNowRadio=false;
 void setStationAutoReconnect(bool){++reconnect_writes;}
 void beginStation(const char*,const char*){++station_begins;}
 bool enforceStationChannel(){++channel_checks;return true;}
}}
int s_wifi_disconnect_reason=0;uint32_t s_wifi_disconnect_time=0;
unsigned long s_wifi_connected_at=0;
struct MQTTBridge{
 mesh::wifi::NetworkPowerPolicy _network_power_policy;
 std::atomic<bool>_network_suspended{false};
 bool _network_espnow_restore_pending=false;
 bool _wifi_event_registered=false,_manage_wifi=true,_ntp_sync_pending=false;
 int _wifi_reconnect_backoff_attempt=0;
 std::atomic<bool>_ntp_synced{true},_stop_requested{false},_stop_acked{false};
 struct{void noteGotIp(){}}_ntp_reconnect_latch;
 AlertFaultPolicy::OutageSnapshot outage{true,1,0};
 AlertFaultPolicy::OutageSnapshot wifiOutage(){return outage;}
 void setWifiOutage(AlertFaultPolicy::OutageSnapshot value){outage=value;}
 char _wifi_ssid[8]="mesh",_wifi_password[8]="secret";
 void beginWiFiStation();
 void applyTimezoneRules() {}
 bool _wifi_status_initialized=true;
 int _last_wifi_status=WL_DISCONNECTED;
 unsigned long _last_wifi_check=0,_last_wifi_reconnect_attempt=0;
 uint8_t _wifi_power_save=1;
 struct{bool canonical_wifi=true;}_node_info;
 struct{uint8_t wifi_power_save=1;}prefs,*_obs=&prefs;
 struct Client{void disconnect(){}bool isStarted()const{return false;}};
 struct Slot{
  Client* client=nullptr;bool connected=false;
  uint32_t connected_at_ms=0,current_outage_started_ms=0;
  MQTTTransportHealth::State transport_health;
 }_slots[RUNTIME_MQTT_SLOTS];
 bool _slot_attempt_pending[RUNTIME_MQTT_SLOTS]={};
 std::atomic<bool> _slot_transport_fault_pending[RUNTIME_MQTT_SLOTS]={};
 std::atomic<bool> _slot_health_reset_pending[RUNTIME_MQTT_SLOTS]={};
 void updateSlotTransportDiag(int,uint32_t){}
 void updateCachedConnectionStatus(){}
 std::vector<std::string> cleanup;
 void cancelNtpRefresh(){cleanup.push_back("ntp");}
 void teardownSlot(int index){cleanup.push_back("slot"+std::to_string(index));}
 void destroySlotClients(){cleanup.push_back("clients");}
 int slot_work=0;
 bool initializeWiFiInTask();bool waitUnlessStopping(uint32_t delay_ms);
 bool handleWiFiConnection(unsigned long now);
 void mqttTaskLoop(){
@TASK_START@
  while(true){
@TASK_STOP@
    ++slot_work;
    break;
  }
 }
};
@INITIALIZE@
@WAIT@
void assert_ap_untouched(){
 assert(sdk_mode&WIFI_AP);
 assert(enable_calls==0&&mode_writes==0&&reconnect_writes==0&&autoconnect_writes==0);
 assert(event_registers==0&&station_begins==0);
}
int main(int argc,char** argv){
 assert(argc==2);const std::string scenario=argv[1];MQTTBridge bridge;
 if(scenario=="ap"){
  for(int mode:{WIFI_AP,WIFI_AP_STA})for(bool manage:{false,true}){
   sdk_mode=mode;bridge._manage_wifi=manage;facade_started=false;
   for(int attempt=0;attempt<3;++attempt)assert(!bridge.initializeWiFiInTask());
   assert_ap_untouched();assert(!bridge._wifi_event_registered&&!bridge._ntp_sync_pending);
  }
 }else if(scenario=="stop_waiting"){
  sdk_mode=WIFI_AP_STA;
  tick=[&](){if(now_ms>=65)bridge._stop_requested=true;};
  bridge.mqttTaskLoop();assert_ap_untouched();
  assert(now_ms==80&&bridge._stop_acked&&bridge.slot_work==0);
  assert((bridge.cleanup==std::vector<std::string>{"ntp","slot0","slot1","clients"}));
 }else if(scenario=="stop_before_start"){
  sdk_mode=WIFI_AP;bridge._stop_requested=true;
  bridge.mqttTaskLoop();assert_ap_untouched();
  assert(now_ms==0&&mode_reads==0&&bridge._stop_acked&&bridge.slot_work==0);
 }else if(scenario=="handoff"||scenario=="companion_handoff"){
  sdk_mode=WIFI_AP_STA;WiFi.state=WL_DISCONNECTED;
  bridge._manage_wifi=scenario!="companion_handoff";
  tick=[&](){if(now_ms<100){assert_ap_untouched();assert(!bridge._wifi_event_registered);}
             else sdk_mode=WIFI_STA;};
  bridge.mqttTaskLoop();
  assert(now_ms==1100&&sdk_mode==WIFI_STA&&bridge.slot_work==1&&!bridge._stop_acked);
  assert(enable_calls==1&&event_registers==1&&reconnect_writes==1);
  assert(station_begins==(bridge._manage_wifi?1:0));
 }else if(scenario=="cold_and_restart"){
  driver_initialized=false;sdk_mode=WIFI_MODE_NULL;bridge._ntp_synced=false;
  assert(bridge.initializeWiFiInTask());
  assert(driver_initialized&&sdk_mode==WIFI_STA&&event_registers==1&&bridge._ntp_sync_pending);
  sdk_mode=WIFI_AP_STA;const int writes=mode_writes;
  assert(!bridge.initializeWiFiInTask()&&mode_writes==writes&&event_registers==1);
  sdk_mode=WIFI_STA;assert(bridge.initializeWiFiInTask());assert(event_registers==1);
 }else if(scenario=="failures"){
  read_failure_at=1;assert(!bridge.initializeWiFiInTask());assert(enable_calls==0);
  read_failure_at=0;enable_failure=true;assert(!bridge.initializeWiFiInTask());
  assert(mode_writes==0&&event_registers==0&&reconnect_writes==0);
  enable_failure=false;read_failure_at=mode_reads+2;
  assert(!bridge.initializeWiFiInTask());assert(event_registers==0&&reconnect_writes==0);
  read_failure_at=0;assert(bridge.initializeWiFiInTask());assert(event_registers==1);
 }else if(scenario=="race"){
  race_ap=true;assert(!bridge.initializeWiFiInTask());
  assert(sdk_mode==WIFI_AP_STA&&event_registers==0&&reconnect_writes==0&&station_begins==0);
  race_ap=false;sdk_mode=WIFI_STA;assert(bridge.initializeWiFiInTask());assert(event_registers==1);
 }else if(scenario=="recovery"||scenario=="companion_recovery"){
  bridge._manage_wifi=scenario!="companion_recovery";
  for(int mode:{WIFI_AP,WIFI_AP_STA}){
   sdk_mode=mode;WiFi.state=WL_DISCONNECTED;
   bridge.beginWiFiStation();assert(station_begins==0);
   assert(!bridge.handleWiFiConnection(20000));
   assert(station_disconnects==0&&station_begins==0&&bridge._last_wifi_reconnect_attempt==0);
   assert(bridge._wifi_reconnect_backoff_attempt==0&&bridge._last_wifi_check==0);
   WiFi.state=WL_CONNECTED;assert(!bridge.handleWiFiConnection(40000));
   assert(channel_checks==0&&power_writes==0&&tx_power_writes==0);
  }
  // Unknown SDK mode also defers, without consuming the due reconnect rung.
  sdk_mode=WIFI_STA;WiFi.state=WL_DISCONNECTED;read_failure_at=mode_reads+1;
  assert(!bridge.handleWiFiConnection(20000)&&station_disconnects==0);
  read_failure_at=0;assert(!bridge.handleWiFiConnection(20000));
  assert(station_begins==(bridge._manage_wifi?1:0));
  assert(station_disconnects==(bridge._manage_wifi?1:0));
  WiFi.state=WL_CONNECTED;assert(bridge.handleWiFiConnection(40000));
  assert(power_writes==1&&tx_power_writes==1&&channel_checks==1);
 }else assert(false);
}
