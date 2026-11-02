#include <WiFi.h>
#include <algorithm>
#include "production_sta_begin.inc"
#include <helpers/esp32/WiFiStationPolicy.h>

static void clearCalls() {
  calls.clear(); connected_configs.clear();
  get_calls=set_calls=disconnect_calls=ip_calls=enable_calls=0;
}

static void reset() {
  clearCalls();
  fail_get=fail_set=0;
  fail_enable_n=fail_disconnect_n=0;
  fail_enable=fail_disconnect=fail_ip=fail_connect=false;
  sdk_associated=preserve_cached_status=false;
  constrained=blocked=false;
  sdk_status=WL_DISCONNECTED;
  WiFiSTAClass::_scanMethod=WIFI_FAST_SCAN;
  WiFiSTAClass::_sortMethod=WIFI_CONNECT_AP_BY_SIGNAL;
  sdk_config={};
  memcpy(sdk_config.sta.ssid,"stale-network",13);
  memcpy(sdk_config.sta.password,"stale-key",9);
  sdk_config.sta.sae_pwe_h2e=WPA3_SAE_PWE_HUNT_AND_PECK;
  WiFi.networks.clear();
}

static const wifi_config_t& joined(const char* ssid,const char* password) {
  assert(connected_configs.size()==1);
  const auto& config=connected_configs.front();
  const size_t ssid_size=strlen(ssid),key_size=password?strlen(password):0;
  assert(memcmp(config.sta.ssid,ssid,ssid_size)==0);
  if(ssid_size<sizeof(config.sta.ssid)) assert(config.sta.ssid[ssid_size]==0);
  if(key_size) assert(memcmp(config.sta.password,password,key_size)==0);
  if(key_size<sizeof(config.sta.password)) assert(config.sta.password[key_size]==0);
  assert(config.sta.sae_pwe_h2e==WPA3_SAE_PWE_BOTH);
  assert(config.sta.threshold.authmode==(key_size?WIFI_AUTH_WPA2_PSK:WIFI_AUTH_OPEN));
  assert(config.sta.threshold.rssi==-127);
#if defined(MESH_WIFI_ALL_CHANNEL_SCAN) && MESH_WIFI_ALL_CHANNEL_SCAN
  assert(config.sta.scan_method==WIFI_ALL_CHANNEL_SCAN);
#else
  assert(config.sta.scan_method==WIFI_FAST_SCAN);
#endif
  assert(config.sta.sort_method==WIFI_CONNECT_AP_BY_SIGNAL);
#if defined(MESH_WIFI_RECOVERY) && MESH_WIFI_RECOVERY \
    && defined(MESH_WIFI_ALL_CHANNEL_SCAN) && MESH_WIFI_ALL_CHANNEL_SCAN
  assert(config.sta.failure_retry_cnt==1);
#else
  assert(config.sta.failure_retry_cnt==0);
#endif
  assert(config.sta.pmf_cfg.capable&&!config.sta.pmf_cfg.required);
#if defined(SAE_H2E_IDENTIFIER_LEN)
  for(uint8_t byte:config.sta.sae_h2e_identifier)assert(byte==0);
#endif
  return config;
}

static void requestFixedChannel() {
  constrained=true;
  WiFi.networks={{"iot",1,-10,{1,2,3,4,5,6}},
                 {"iot",6,-75,{2,2,3,4,5,6}},
                 {"other",6,-5,{3,2,3,4,5,6}},
                 {"iot",6,-35,{4,2,3,4,5,6}}};
}

int main(int argc,char** argv) {
  assert(argc==2);
  const std::string scenario=argv[1];
  if(scenario=="security") {
    for(const char* password:{"test-password","",static_cast<const char*>(nullptr)}) {
      reset();
      assert(mesh::wifi::beginStation("iot",password)!=WL_CONNECT_FAILED);
      const auto& config=joined("iot",password);
      assert(!config.sta.bssid_set&&config.sta.channel==0);
    }
    reset();
    const std::string full_ssid(32,'s'),full_key(64,'k');
    assert(mesh::wifi::beginStation(full_ssid.c_str(),full_key.c_str())!=WL_CONNECT_FAILED);
    joined(full_ssid.c_str(),full_key.c_str());
  } else if(scenario=="fixed-channel") {
    reset(); requestFixedChannel();
    assert(mesh::wifi::beginStation("iot","test-password")!=WL_CONNECT_FAILED);
    const auto& config=joined("iot","test-password");
    assert(config.sta.bssid_set&&config.sta.channel==6&&config.sta.bssid[0]==4);
  } else if(scenario=="scan-policy") {
    for(bool fixed:{false,true}) {
      reset(); if(fixed)requestFixedChannel();
#if defined(MESH_WIFI_ALL_CHANNEL_SCAN) && MESH_WIFI_ALL_CHANNEL_SCAN
      WiFiSTAClass::_sortMethod=WIFI_CONNECT_AP_BY_SECURITY;
#endif
      const std::string raw_key(64,'a');
      assert(mesh::wifi::beginStation("iot",raw_key.c_str())!=WL_CONNECT_FAILED);
      const auto& config=joined("iot",raw_key.c_str());
      assert(config.sta.bssid_set==fixed&&config.sta.channel==(fixed?6:0));
      if(fixed)assert(config.sta.bssid[0]==4);
#if defined(MESH_WIFI_ALL_CHANNEL_SCAN) && MESH_WIFI_ALL_CHANNEL_SCAN
      const auto enable=std::find(calls.begin(),calls.end(),"enable");
      assert(std::find(calls.begin(),enable,"scan-method")!=enable);
      assert(std::find(calls.begin(),enable,"sort-method")!=enable);
#else
      assert(std::find(calls.begin(),calls.end(),"scan-method")==calls.end());
      assert(std::find(calls.begin(),calls.end(),"sort-method")==calls.end());
#endif
      // Driver-managed retries use no-argument begin() and must keep the
      // selected scan strategy, fixed-channel guard and authentication fields.
      sdk_status=WL_DISCONNECTED;clearCalls();
      assert(WiFi.begin()!=WL_CONNECT_FAILED);
      const auto& retry=joined("iot",raw_key.c_str());
      assert(retry.sta.bssid_set==fixed&&retry.sta.channel==(fixed?6:0));
      if(fixed)assert(retry.sta.bssid[0]==4);
    }
  } else if(scenario=="failures") {
    reset();
    assert(mesh::wifi::beginStation("iot","test-password")!=WL_CONNECT_FAILED);
    const int maximum_get=get_calls,maximum_set=set_calls;
    assert(maximum_get>0&&maximum_set>0);
    for(int nth=1;nth<=maximum_get;++nth) {
      reset(); fail_get=nth;
      assert(mesh::wifi::beginStation("iot","test-password")==WL_CONNECT_FAILED);
      assert(connected_configs.empty());
    }
    for(int nth=1;nth<=maximum_set;++nth) {
      reset(); fail_set=nth;
      assert(mesh::wifi::beginStation("iot","test-password")==WL_CONNECT_FAILED);
      assert(connected_configs.empty());
    }
    for(int stage=0;stage<4;++stage) {
      reset();
      if(stage==0)fail_enable=true;
      if(stage==1)fail_disconnect=true;
      if(stage==2)fail_ip=true;
      if(stage==3)fail_connect=true;
      assert(mesh::wifi::beginStation("iot","test-password")==WL_CONNECT_FAILED);
      if(stage==3) joined("iot","test-password");
      else assert(connected_configs.empty());
    }
  } else if(scenario=="idempotent") {
    for(bool fixed:{false,true}) {
      reset(); if(fixed)requestFixedChannel();
      assert(mesh::wifi::beginStation("iot","test-password")!=WL_CONNECT_FAILED);
      joined("iot","test-password");
      sdk_status=WL_CONNECTED;sdk_associated=true;clearCalls();
      assert(mesh::wifi::beginStation("iot","test-password")==WL_CONNECTED);
      assert(connected_configs.empty()&&disconnect_calls==0&&set_calls==0);
      assert(sdk_config.sta.sae_pwe_h2e==WPA3_SAE_PWE_BOTH);
      if(!fixed) {
        // IDF can remember the live AP channel even when the request had no
        // channel hint. Keep a healthy unrestricted link instead of churning it.
        sdk_config.sta.channel=6;
        assert(mesh::wifi::beginStation("iot","test-password")==WL_CONNECTED);
        assert(connected_configs.empty()&&disconnect_calls==0&&set_calls==0);
      }
      assert(mesh::wifi::beginStation("iot","new-password")!=WL_CONNECT_FAILED);
      joined("iot","new-password");
      assert(disconnect_calls>0);
    }
    reset();
    assert(mesh::wifi::beginStation("iot","test-password")!=WL_CONNECT_FAILED);
    sdk_status=WL_CONNECTED;sdk_associated=true;clearCalls();
    assert(mesh::wifi::beginStation("new-network","test-password")!=WL_CONNECT_FAILED);
    joined("new-network","test-password");
  } else if(scenario=="preflight") {
    for(const char* ssid:{static_cast<const char*>(nullptr),"","123456789012345678901234567890123"}) {
      reset();
      assert(mesh::wifi::beginStation(ssid,"test-password")==WL_CONNECT_FAILED);
      assert(connected_configs.empty());
    }
    reset();blocked=true;
    assert(mesh::wifi::beginStation("iot","test-password")==WL_CONNECT_FAILED);
    assert(connected_configs.empty()&&enable_calls==0);
    reset();
    const std::string key(65,'k');
    assert(mesh::wifi::beginStation("iot",key.c_str())==WL_CONNECT_FAILED);
    assert(connected_configs.empty());
  } else if(scenario=="reconnect") {
    reset();
    assert(mesh::wifi::beginStation("iot","test-password")!=WL_CONNECT_FAILED);
    joined("iot","test-password");
    sdk_status=WL_DISCONNECTED;clearCalls();
    assert(WiFi.begin()!=WL_CONNECT_FAILED);
    joined("iot","test-password");
  } else if(scenario=="cached-status") {
    for(wl_status_t cached:{WL_CONNECTED,WL_CONNECT_FAILED}) {
      reset();sdk_status=cached;preserve_cached_status=true;
      // Driver is not associated, although Arduino has not handled its queued
      // disconnect event yet. A cached status must never authorize old keys.
      assert(!sdk_associated);
      const auto result=mesh::wifi::beginStation("iot","test-password");
      assert(result==(cached==WL_CONNECTED?WL_IDLE_STATUS:cached));
      joined("iot","test-password");
      assert(mesh::wifi::last_phase!=mesh::wifi::ConnectionPhase::PolicyFailed);
    }
    reset();
    assert(mesh::wifi::beginStation("iot","test-password")!=WL_CONNECT_FAILED);
    sdk_status=WL_CONNECT_FAILED;preserve_cached_status=true;clearCalls();
    fail_get=1; // Stage cannot get its current config; readback still proves it.
    assert(mesh::wifi::beginStation("iot","test-password")==WL_CONNECT_FAILED);
    joined("iot","test-password");
    reset();sdk_status=WL_CONNECTED;preserve_cached_status=true;
    fail_disconnect_n=2; // Only the explicit post-stage reconnect fails.
    assert(mesh::wifi::beginStation("iot","test-password")==WL_CONNECT_FAILED);
    assert(connected_configs.empty());
    assert(mesh::wifi::last_phase==mesh::wifi::ConnectionPhase::PolicyFailed);
    reset();sdk_status=WL_CONNECTED;preserve_cached_status=true;fail_connect=true;
    assert(mesh::wifi::beginStation("iot","test-password")==WL_CONNECT_FAILED);
    joined("iot","test-password");
    assert(mesh::wifi::last_phase==mesh::wifi::ConnectionPhase::PolicyFailed);
  } else if(scenario=="identifier") {
#if defined(SAE_H2E_IDENTIFIER_LEN)
    reset();
    assert(mesh::wifi::beginStation("iot","test-password")!=WL_CONNECT_FAILED);
    sdk_config.sta.sae_h2e_identifier[0]=42;
    sdk_status=WL_CONNECTED;sdk_associated=true;preserve_cached_status=true;
    clearCalls();fail_enable_n=1;
    // Safe retry after an early stage failure: current credentials are proven,
    // but the pre-existing identifier must be cleared before authentication.
    assert(mesh::wifi::beginStation("iot","test-password")==WL_IDLE_STATUS);
    joined("iot","test-password");
#else
    assert(false);
#endif
  } else {
    assert(false);
  }
}
