#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include "helpers/WiFiReconnectPolicy.h"
#include "helpers/esp32/WiFiCoordinator.h"
#define WIFI_DEBUG_PRINTLN(...) ((void)0)
using wifi_mode_t=int;using esp_err_t=int;
constexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_WIFI_NOT_INIT=-2;
constexpr int WIFI_OFF=0,WIFI_STA=1,WIFI_AP=2,WIFI_AP_STA=3,WIFI_MODE_NULL=0,WIFI_MODE_AP=2;
constexpr int WL_CONNECTED=3,WL_DISCONNECTED=0,WIFI_SETUP_FALLBACK_MS=120000;
constexpr const char* COMPANION_WIFI_SETUP_AP="mesh-test";
int sdk_mode=WIFI_STA,query_error=0,mode_writes=0,disconnects=0;
int joins=0,channel_checks=0,reconnect_writes=0,credential_loads=0;
int power_writes=0,socket_starts=0,console_starts=0;
uint32_t now=300001;
uint32_t millis(){return now;}
int esp_wifi_get_mode(wifi_mode_t* mode){if(query_error)return query_error;*mode=sdk_mode;return ESP_OK;}
struct WiFiClass{
 int state=WL_DISCONNECTED;
 bool mode(int value){++mode_writes;sdk_mode=value;return true;}
 int status(){return state;}
 void setAutoReconnect(bool){++reconnect_writes;}
 void disconnect(bool=false,bool=false){++disconnects;state=WL_DISCONNECTED;}
}WiFi;
namespace mesh{namespace wifi{
@STATION_GUARD@
 void setStationAutoReconnect(bool){++reconnect_writes;}
 bool enforceStationChannel(){++channel_checks;return true;}
 void applyProtocolMask(int){}
 void beginStation(const char*,const char*){assert(power_writes>0);++joins;}
}}
namespace mesh{namespace wireless{
 constexpr int WiFi=1;
 struct Control{bool blocked(int){return false;}};
 Control& control(){static Control instance;return instance;}
}}
constexpr int WIFI_IF_STA=0;
struct Board{
 bool ota=true,inhibit_sleep=false;
 bool isOTAUpdateRunning(){return ota;}
 void setInhibitSleep(bool enabled){inhibit_sleep=enabled;}
}board;
struct Mesh{
 bool setup=false,recovery=false,active=false,stopping=false;
 bool isWebConfigSetupActive(){return setup;}
 bool isWebConfigWiFiRecoveryActive(){return recovery;}
 bool isWebConfigWiFiTransitionActive(){return setup||stopping;}
 bool isWebConfigActiveOrStopping(){return active;}
 bool startWebConfig(bool,char*){return false;}
 void stopWebConfig(){setup=false;}
}the_mesh;
struct WebConfigServer{static bool loadEnabled(bool){return false;}};
struct Portal{
 bool active=false;bool isActive(){return active;}
 template<class T>bool begin(const char*,T,void*){return false;}
 void configureRecovery(const char*,const char*,uint32_t,uint32_t){}
}portal;
Portal& wifiSetupPortal(){return portal;}
void saveCompanionWiFi(){}
char configured_wifi_ssid[8]="mesh",configured_wifi_password[8]="secret";
bool companion_wifi_has_credentials=true,companion_wifi_requested=true,companion_wifi_active=true;
bool companion_wifi_interface_registered=true;
bool companion_wifi_services_stopped=true;
bool companion_wifi_background_stopped=false,companion_wifi_espnow_restore_pending=true;
bool companion_wifi_webconfig_start_pending=false;
bool companion_wifi_credential_reload_pending=true;
unsigned long companion_wifi_credential_reload_at=1;
bool wifi_setup_recovery_mode=false,wifi_setup_attempted=true;
unsigned long last_wifi_setup_attempt=1;
WiFiReconnectPolicy::Tracker wifi_reconnect_tracker;
void loadCompanionWiFiCredentials(){++credential_loads;}
void resetCompanionWiFiRecoveryState(){wifi_reconnect_tracker=WiFiReconnectPolicy::Tracker();}
void applyCompanionWiFiPowerSave(){++power_writes;}
constexpr int TCP_PORT=1234;
struct Interface{
 bool begin(int){++socket_starts;return true;}
 void enable(){}
 bool isListening()const{return socket_starts>0;}
}wifi_interface;
void ota_console_start(){++console_starts;}
@POWER_GUARDS@
#ifdef WITH_WEBCONFIG
@AUTO_WEBCONFIG@
#endif
@RELOAD@
@START_WIFI@
void serviceStation(){
@STATION_LOOP@
}
void assert_untouched(){
 assert(sdk_mode&WIFI_AP);assert(mode_writes==0&&disconnects==0&&joins==0);
 assert(channel_checks==0&&reconnect_writes==0);
}
int main(int argc,char** argv){
 assert(argc==2);const std::string scenario=argv[1];
 wifi_reconnect_tracker.noteDisconnected(1);wifi_reconnect_tracker.noteAttempt(1);
 if(scenario=="retry"){
  for(int mode:{WIFI_AP,WIFI_AP_STA}){
   sdk_mode=mode;serviceStation();assert_untouched();
   assert(wifi_reconnect_tracker.retryDue(now));
  }
  query_error=ESP_FAIL;serviceStation();assert_untouched();query_error=0;
  sdk_mode=WIFI_STA;board.ota=false;serviceStation();
  assert(sdk_mode==WIFI_STA&&mode_writes==1&&disconnects==1&&joins==1);
  assert(!wifi_reconnect_tracker.retryDue(now));
 }else if(scenario=="reload"){
  for(int mode:{WIFI_AP,WIFI_AP_STA}){
   sdk_mode=mode;serviceCompanionWiFiCredentialReload();assert_untouched();
   assert(companion_wifi_credential_reload_pending&&companion_wifi_credential_reload_at==1);
   assert(credential_loads==0);
  }
  query_error=ESP_FAIL;serviceCompanionWiFiCredentialReload();assert_untouched();query_error=0;
  sdk_mode=WIFI_STA;board.ota=false;serviceCompanionWiFiCredentialReload();
  assert(!companion_wifi_credential_reload_pending&&companion_wifi_credential_reload_at==0);
  assert(credential_loads==1&&mode_writes==1&&disconnects==1&&joins==1);
 }else if(scenario=="transition_reload"){
#ifdef WITH_WEBCONFIG
  sdk_mode=WIFI_STA;board.ota=false;the_mesh.stopping=true;
  serviceCompanionWiFiCredentialReload();
  assert(companion_wifi_credential_reload_pending&&companion_wifi_credential_reload_at==1);
  assert(credential_loads==0&&mode_writes==0&&disconnects==0&&joins==0);
  // The SDK AP may be gone while WebConfig still owns its teardown barrier.
  the_mesh.stopping=false;serviceCompanionWiFiCredentialReload();
  assert(!companion_wifi_credential_reload_pending&&companion_wifi_credential_reload_at==0);
  assert(credential_loads==1&&mode_writes==1&&disconnects==1&&joins==1);
#endif
 }else if(scenario=="setup_handoff"){
#ifdef WITH_WEBCONFIG
  sdk_mode=WIFI_AP_STA;board.ota=false;the_mesh.setup=the_mesh.recovery=true;
  serviceStation();assert(sdk_mode==WIFI_AP_STA&&mode_writes==0&&joins==1&&disconnects==1);
  // Even a stale WebConfig setup flag must not authorize changes to an OTA AP.
  board.ota=true;mode_writes=joins=disconnects=channel_checks=reconnect_writes=0;
  wifi_reconnect_tracker=WiFiReconnectPolicy::Tracker();
  wifi_reconnect_tracker.noteDisconnected(1);wifi_reconnect_tracker.noteAttempt(1);
  serviceStation();assert_untouched();
#else
  sdk_mode=WIFI_AP_STA;board.ota=false;portal.active=true;serviceStation();
  assert(sdk_mode==WIFI_AP_STA&&mode_writes==0&&joins==0&&disconnects==0);
#endif
 }else if(scenario=="unrelated_setup"){
  sdk_mode=WIFI_AP_STA;board.ota=false;the_mesh.setup=true;the_mesh.recovery=false;
  serviceStation();assert_untouched();
 }else if(scenario=="start_wifi"){
  companion_wifi_active=false;
  for(int mode:{WIFI_AP,WIFI_AP_STA}){
   sdk_mode=mode;startCompanionWiFi();assert_untouched();
   assert(!companion_wifi_active&&companion_wifi_requested&&companion_wifi_services_stopped);
   assert(socket_starts==0&&console_starts==0&&power_writes==0&&!board.inhibit_sleep);
  }
  query_error=ESP_FAIL;startCompanionWiFi();assert_untouched();query_error=0;
  sdk_mode=WIFI_STA;board.ota=false;startCompanionWiFi();
  assert(companion_wifi_active&&!companion_wifi_services_stopped&&board.inhibit_sleep);
  assert(mode_writes==1&&joins==1&&socket_starts==1&&console_starts==1&&power_writes==2);
  startCompanionWiFi();assert(mode_writes==1&&socket_starts==1);
 }else if(scenario=="registration"){
  companion_wifi_interface_registered=false;
  companion_wifi_active=false;board.ota=false;sdk_mode=WIFI_STA;
  startCompanionWiFi();
  assert(companion_wifi_active&&board.inhibit_sleep&&!companion_wifi_services_stopped);
  assert(socket_starts==0&&console_starts==1&&joins==1);
 }else assert(false);
}
