#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <initializer_list>
#define MQTT_DEBUG_PRINTLN(...) ((void)0)
static constexpr int ESP_OK=0;
using esp_err_t=int;
const char* esp_err_to_name(int) { return "mock"; }
uint32_t millis() { return 123; }
const uint8_t rootca_bytes[16]={};
const uint8_t* rootca_crt_bundle_start=nullptr;
const uint8_t* rootca_crt_bundle_end=nullptr;
static bool s_ca_bundle_loaded=false;
struct Client {
  int credentials=0, bundles=0, attaches=0; std::string server;
  void setServer(const char* uri) { server=uri; }
  void setCACertBundle(const uint8_t*, size_t) { ++bundles; }
  void attachArduinoCACertBundle(bool value) { assert(value); ++attaches; }
  void setCredentials(const char*, const char*) { ++credentials; }
};
struct MQTTBridge {
  struct MQTTSlot {
    char host[100]="broker.example", broker_uri[150]={};
    char username[40]="user", password[40]="secret", audience[40]={};
    char token[80]="signed-jwt"; char* auth_token=token;
    uint16_t port=8883; Client* client=nullptr;
    bool initial_connect_done=true; uint32_t last_reconnect_attempt=0;
  } _slots[2];
  Client clients[2]; int connects=0, tokens=0;
  const char* _jwt_username="jwt";
  MQTTBridge() { for (int i=0;i<2;++i) _slots[i].client=&clients[i]; }
  bool createSlotAuthToken(int) { ++tokens; return true; }
  int reconnectSlotClient(int) { ++connects; return ESP_OK; }
  bool setupCustom(int);
};
@METHOD@
int main() {
  for (const char* host : {"mqtts://broker", "wss://broker/path", "broker"}) {
    for (bool jwt : {false, true}) {
      MQTTBridge bridge; auto& slot=bridge._slots[0];
      strcpy(slot.host, host); if(jwt) strcpy(slot.audience, "fleet-audience");
      assert(!bridge.setupCustom(0));
      assert(!slot.initial_connect_done && slot.last_reconnect_attempt==123);
      assert(bridge.connects==0 && bridge.tokens==0 && bridge.clients[0].credentials==0);
    }
  }
  // Port 443 infers WSS and needs verification too.
  MQTTBridge wss; wss._slots[0].port=443; assert(!wss.setupCustom(0)); assert(wss.connects==0);
  // Explicit plaintext is an operator choice; never silently downgrade TLS.
  for (const char* host : {"mqtt://broker", "ws://broker/path", "broker"}) {
    MQTTBridge plain; strcpy(plain._slots[0].host, host); plain._slots[0].port=1883;
    assert(plain.setupCustom(0) && plain.connects==1 && plain.clients[0].credentials==1);
    assert(plain.clients[0].bundles==0);
  }
  rootca_crt_bundle_start=rootca_bytes; rootca_crt_bundle_end=rootca_bytes+sizeof(rootca_bytes);
  MQTTBridge trusted;
#ifdef PORTABLE_MQTT_OBSERVER
  assert(!trusted.setupCustom(0) && trusted.connects==0); // bundle deliberately unlinked
#else
  assert(trusted.setupCustom(0));
  assert(s_ca_bundle_loaded && trusted.clients[0].bundles==1 && trusted.clients[0].credentials==1);
  strcpy(trusted._slots[1].audience, "fleet-audience");
  assert(trusted.setupCustom(1));
  assert(trusted.clients[1].bundles==0 && trusted.clients[1].attaches==1);
  assert(trusted.tokens==1 && trusted.connects==2);
  assert(trusted.clients[0].server=="mqtts://broker.example:8883");
#endif
  puts("MQTT custom TLS security regression checks passed");
}
