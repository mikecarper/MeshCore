// Production bridge methods are injected by test_espnow_radio_lifecycle.py.
// Keep Arduino's private caches distinct from the underlying SDK driver.
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <array>
#include <vector>
#include <helpers/WirelessControl.h>
#include <helpers/ESPNowRawFragmentation.h>
#include <helpers/bridges/ESPNowBridgeFormat.h>
#include <helpers/bridges/ESPNowBridgeDiagnostics.h>

#define ESP_IDF_VERSION_VAL(a,b,c) ((a)*10000+(b)*100+(c))
#define BRIDGE_DEBUG_PRINTLN(...) ((void)0)
#define log_e(...) ((void)0)
#define portENTER_CRITICAL(x) ((void)0)
#define portEXIT_CRITICAL(x) ((void)0)
#define ESP_NOW_ETH_ALEN 6
#define WIFI_INIT_CONFIG_DEFAULT() 0
using wifi_init_config_t = int;
using wifi_second_chan_t = int;
using esp_err_t = int;
enum wifi_mode_t { WIFI_OFF=0, WIFI_STA=1, WIFI_AP=2, WIFI_AP_STA=3 };
constexpr wifi_mode_t WIFI_MODE_NULL = WIFI_OFF, WIFI_MODE_STA = WIFI_STA;
constexpr int ESP_ERR_WIFI_NOT_INIT=-91;
constexpr int ESP_OK=0, WIFI_STORAGE_RAM=0, WIFI_IF_STA=0, WIFI_SECOND_CHAN_NONE=0;
constexpr int WIFI_PROTOCOL_11B=1, WIFI_PROTOCOL_11G=2, WIFI_PROTOCOL_11N=4, WIFI_PROTOCOL_LR=8;
constexpr int WIFI_PHY_MODE_LR=1, WIFI_PHY_RATE_LORA_250K=2;
enum esp_now_send_status_t { ESP_NOW_SEND_SUCCESS=0, ESP_NOW_SEND_FAIL=1 };
enum wifi_ps_type_t { WIFI_PS_NONE=0, WIFI_PS_MIN_MODEM=1, WIFI_PS_MAX_MODEM=2 };
struct esp_now_peer_info_t { uint8_t peer_addr[6]; int channel, ifidx; bool encrypt; };
struct esp_now_rate_config_t { int phymode, rate; bool ersu, dcm; };

int failure=0, wifi_starts=0, wifi_stops=0, wifi_deinits=0, facade_initializations=0;
bool sdk_initialized=false, sdk_started=false, espnow_active=false;
uint8_t wifi_channel=6;
wifi_mode_t sdk_mode=WIFI_OFF;
wifi_ps_type_t wifi_ps=WIFI_PS_MIN_MODEM;
unsigned ps_writes=0,pending_sta_events=0;
bool deliver_sta_during_start=false;
void deliverArduinoStaStart();
int esp_wifi_set_ps(wifi_ps_type_t value) {
  ++ps_writes;
  if (!sdk_started || failure==19) return -1;
  wifi_ps=value; return ESP_OK;
}
namespace mesh { namespace bridge {
bool peer_settings_ready=true;
uint8_t configured_peer[6]={255,255,255,255,255,255};
bool copyEspNowPeer(uint8_t* out) {
  if (!peer_settings_ready) return false;
  memcpy(out,configured_peer,6); return true;
}
}}
std::vector<std::array<uint8_t,6>> added_peers,removed_peers;
int esp_wifi_init(wifi_init_config_t*) {
  if (sdk_initialized) return ESP_OK; // esp-idf init is idempotent.
  if (failure==11) return -1;
  sdk_initialized=true;
  return ESP_OK;
}
int esp_wifi_set_storage(int) { return !sdk_initialized || failure==12 ? -1 : ESP_OK; }
int esp_wifi_set_mode(wifi_mode_t mode) {
  if (!sdk_initialized || failure==13) return -1;
  sdk_mode=mode;
  return ESP_OK;
}
int esp_wifi_get_mode(wifi_mode_t* mode) {
  if (!sdk_initialized) return ESP_ERR_WIFI_NOT_INIT;
  *mode=sdk_mode;
  return ESP_OK;
}
int esp_wifi_start() {
  if (!sdk_initialized || failure==14) return -1;
  sdk_started=true;
  ++wifi_starts;
  ++pending_sta_events;
  if(deliver_sta_during_start)deliverArduinoStaStart();
  return ESP_OK;
}
int esp_wifi_stop() {
  if (!sdk_initialized || failure==16) return -1;
  assert(!espnow_active);
  sdk_started=false;
  ++wifi_stops;
  return ESP_OK;
}
int esp_wifi_deinit() {
  if (!sdk_initialized || sdk_started || failure==17) return -1;
  assert(!espnow_active);
  sdk_initialized=false;
  sdk_mode=WIFI_OFF;
  ++wifi_deinits;
  return ESP_OK;
}
int esp_wifi_get_channel(uint8_t* channel, wifi_second_chan_t*) {
  if (!sdk_initialized) return -1;
  *channel=wifi_channel;
  return ESP_OK;
}
int esp_wifi_set_channel(int channel, int) {
  if (!sdk_started || failure==15) return -1;
  wifi_channel=channel;
  return ESP_OK;
}
int esp_wifi_set_protocol(int, int) { return !sdk_started || failure==2 ? -1 : ESP_OK; }
int esp_now_init() {
  if (!sdk_started || failure==4) return -1;
  assert(!espnow_active);
  espnow_active=true;
  return ESP_OK;
}
int esp_now_deinit() { assert(espnow_active); espnow_active=false; return ESP_OK; }
template<typename T> int esp_now_register_send_cb(T) { return failure==7 ? -1 : ESP_OK; }
template<typename T> int esp_now_register_recv_cb(T) { return failure==8 ? -1 : ESP_OK; }
int esp_now_add_peer(esp_now_peer_info_t* peer) {
  assert(peer->channel==6);
  std::array<uint8_t,6> address;memcpy(address.data(),peer->peer_addr,6);
  added_peers.push_back(address);
  return failure==9 ? -1 : ESP_OK;
}
int esp_now_del_peer(const uint8_t* peer) {
  std::array<uint8_t,6> address;memcpy(address.data(),peer,6);
  removed_peers.push_back(address);return ESP_OK;
}
int esp_now_set_peer_rate_config(const uint8_t*, esp_now_rate_config_t*) { return failure==10 ? -1 : ESP_OK; }
int esp_wifi_config_espnow_rate(int, int) { return failure==10 ? -1 : ESP_OK; }

constexpr int WL_DISCONNECTED=6,STA_STARTED_BIT=8,ARDUINO_EVENT_WIFI_STA_START=42;
struct WiFiSTAClass {static void _setStatus(int){}};
struct arduino_event_t {int event_id;};
struct Facade {
  bool initialized=false, started=false;
  static wifi_ps_type_t _sleepEnabled;
  bool setSleep(wifi_ps_type_t sleepType);
  wifi_ps_type_t getSleep();
  void setStatusBits(int) {}
  void deliverStart();
  wifi_mode_t getMode() const {
    // Pinned Arduino 2.x and 3.x both gate this getter on both caches.
    if (!initialized || !started) return WIFI_OFF;
    wifi_mode_t mode=WIFI_OFF;
    return esp_wifi_get_mode(&mode)==ESP_OK ? mode : WIFI_OFF;
  }
  int channel() const {
    // channel() only checks initialized, not started, in both pinned cores.
    if (!initialized) return 0;
    uint8_t channel=0;
    wifi_second_chan_t secondary=0;
    return esp_wifi_get_channel(&channel, &secondary)==ESP_OK ? channel : 0;
  }
  bool mode(wifi_mode_t requested) {
    const wifi_mode_t current=getMode();
    if (current==requested) return true;
    if (!current && requested && !initialized) {
      wifi_init_config_t config=0;
      if (esp_wifi_init(&config)!=ESP_OK) return false;
      initialized=true;
      ++facade_initializations;
      if (esp_wifi_set_storage(WIFI_STORAGE_RAM)!=ESP_OK) {
        initialized=false;
        return false;
      }
    }
    if (current && !requested) {
      if (!started) return true;
      started=false;
      if (esp_wifi_stop()!=ESP_OK) { started=true; return false; }
      if (initialized && esp_wifi_deinit()!=ESP_OK) return false;
      initialized=false;
      return true;
    }
    if (esp_wifi_set_mode(requested)!=ESP_OK) return false;
    if (started) return true;
    started=true;
    if (esp_wifi_start()!=ESP_OK) { started=false; return false; }
    return true;
  }
} WiFi;


// Arduino-ESP32 2.0.17 WiFiGeneric.cpp: actual cached sleep methods and STA_START branch.
// Source SHA256 e37ce5edb3e0ab2f9d6a2879b2c32162c78c7b76249bc0ff114b3283bcaa2e51.
wifi_ps_type_t Facade::_sleepEnabled=WIFI_PS_MIN_MODEM;
bool Facade::setSleep(wifi_ps_type_t sleepType)
{
    if(sleepType != _sleepEnabled){
        _sleepEnabled = sleepType;
        if((getMode() & WIFI_MODE_STA) != 0){
            if(esp_wifi_set_ps(_sleepEnabled) != ESP_OK){
                log_e("esp_wifi_set_ps failed!");
                return false;
            }
        }
        return true;
    }
    return false;
}
wifi_ps_type_t Facade::getSleep()
{
    return _sleepEnabled;
}
void Facade::deliverStart() {arduino_event_t actual{ARDUINO_EVENT_WIFI_STA_START};auto* event=&actual;
if(event->event_id == ARDUINO_EVENT_WIFI_STA_START) {
        WiFiSTAClass::_setStatus(WL_DISCONNECTED);
        setStatusBits(STA_STARTED_BIT);
        if(esp_wifi_set_ps(_sleepEnabled) != ESP_OK){
            log_e("esp_wifi_set_ps failed");
        }
    }
}
void deliverArduinoStaStart(){assert(pending_sta_events);--pending_sta_events;WiFi.deliverStart();}

namespace mesh { namespace wifi {
constexpr uint8_t kLongRangeBridgeOwner=2;
void setLongRangeOwner(uint8_t,bool){}
int checkLongRangeRadioStart() { return sdk_initialized && (sdk_mode & WIFI_AP) ? -1 : ESP_OK; }
int applyStationProtocolMask(uint8_t mask, bool) { return esp_wifi_set_protocol(WIFI_IF_STA, mask); }
std::atomic<uint8_t>& bridgeEspNowChannel() { static std::atomic<uint8_t> channel{0}; return channel; }
} }
struct Backend : mesh::wireless::Backend {
  bool wifi=false;
  uint8_t available() const override { return mesh::wireless::WiFi|mesh::wireless::EspNow; }
  uint8_t enabled() const override { return wifi ? mesh::wireless::WiFi : 0; }
  uint8_t clients() const override { return mesh::wireless::Independent; }
  mesh::wireless::Result set(uint8_t, bool) override { return mesh::wireless::Result::Done; }
};
class Bridge {
public:
  static constexpr int ESP_ERR_INVALID_STATE=-78,ESP_ERR_INVALID_ARG=-79;
  std::atomic<bool> _debug_dirty{false};
  void recordError(mesh::bridge::diag::Stage, int32_t) {}
  void emitDebug() {}
  struct Prefs { int bridge_format=mesh::bridge::ESPNOW_FORMAT_RAW; uint8_t bridge_channel=6; } prefs;
  Prefs* _prefs=&prefs;
  bool _initialized=false;
  int _active_format=0, _rx_mux=0, _rx_head=0, _rx_tail=0, _rx_count=0, _rx_dropped=0, _rx_dropped_reported=0;
  uint8_t _active_peer[6]={255,255,255,255,255,255};
  int _tx_mux=0, _tx_head=0, _tx_tail=0, _tx_count=0, _tx_waiting=0, _tx_callback_done=0;
  int _tx_callback_status=0, _tx_dropped=0, _tx_dropped_reported=0;
  mesh::espnow::ESPNowRawReassembler _raw_reassembler;
  struct QueuedTransmit { bool started=false, diagnostic=false; int seen_key=0; };
  static constexpr int TX_QUEUE_DEPTH=2;
  QueuedTransmit _tx_queue[TX_QUEUE_DEPTH];
  struct { void clearSeenKey(const int&) {} } _seen_packets;
  static void recv_cb() {}
  static void send_cb() {}
  uint32_t _tx_started_at = 0, _tx_retry_at = 0;
  bool _sdk_teardown_failed = false;
  uint8_t _previous_wifi_sleep=0;bool _wifi_sleep_preference_owned=false;
  void begin();
  void end();
};

@METHODS@

void coherent() {
  // SDK ownership may exist without Arduino, but never the reverse.
  assert(!WiFi.initialized || sdk_initialized);
  assert(!WiFi.started || (sdk_initialized && sdk_started));
}
void off() {
  coherent();
  assert(!sdk_initialized && !sdk_started && !espnow_active);
  assert(!WiFi.initialized && !WiFi.started);
  assert(mesh::wifi::bridgeEspNowChannel().load()==0);
}
int main() {
  Backend backend;
  mesh::wireless::control().begin(backend);
  Bridge bridge;
  // Stopping a bridge that only used IDF must not allocate the Arduino facade.
  for (int cycle=0; cycle<100; ++cycle) {
    bridge.begin();
    assert(bridge._initialized && sdk_started && !WiFi.initialized);
    bridge.end();
    bridge.end();
    off();
    assert(facade_initializations==0);
  }
  // Every driver startup failure releases pure-IDF resources without a facade.
  for (int stage : {11,12,13,14,15,19,2,4,7,8,9,10}) {
    failure=stage;
    bridge.begin();
    assert(!bridge._initialized);
    bridge.end();
    off();
    assert(facade_initializations==0);
  }
  failure=0;
  for (int cycle=0; cycle<100; ++cycle) {
    // ESP-NOW -> WebConfig -> stop WebConfig -> stop ESP-NOW -> WebConfig.
    bridge.begin();
    assert(bridge._initialized);
    assert(WiFi.mode(WIFI_AP_STA));
    backend.wifi=true;
    coherent();
    assert(WiFi.mode(WIFI_STA)); // WebConfig keeps STA for the running bridge.
    backend.wifi=false;
    bridge.end();
    off();
    assert(WiFi.mode(WIFI_AP_STA)); // Must initialize again, rather than use stale caches.
    coherent();
    assert(WiFi.mode(WIFI_OFF));
    off();

    // Browser OTA pauses ESP-NOW, initializes Arduino, then leaves STA alive.
    bridge.begin();
    bridge.end();
    off();
    assert(WiFi.mode(WIFI_AP_STA));
    backend.wifi=true;
    coherent();
    assert(WiFi.mode(WIFI_STA));
    backend.wifi=false;
    bridge.begin();
    assert(bridge._initialized && WiFi.started);
    bridge.end();
    off();
  }
  // An active infrastructure owner survives all bridge failures and stops.
  assert(WiFi.mode(WIFI_AP_STA));
  backend.wifi=true;
  const int stops=wifi_stops, deinits=wifi_deinits;
  for (int stage : {2,4,7,8,9,10}) {
    failure=stage;
    bridge.begin();
    assert(!bridge._initialized && !espnow_active);
    bridge.end();
    coherent();
    assert(sdk_started && WiFi.started && WiFi.getMode()==WIFI_AP_STA);
    assert(wifi_stops==stops && wifi_deinits==deinits);
  }
  failure=0;
  bridge.begin();
  bridge.end();
  assert(wifi_stops==stops && wifi_deinits==deinits);
  backend.wifi=false;
  assert(WiFi.mode(WIFI_OFF));
  off();

  // A failed Arduino start retains initialized=true and started=false.
  // Driver teardown must repair this partial cache, including its own failures.
  for (int stage : {0,12,13,14,15,2,4,7,8,9,10}) {
    failure=14;
    assert(!WiFi.mode(WIFI_STA));
    assert(WiFi.initialized && !WiFi.started && sdk_initialized);
    assert(WiFi.getMode()==WIFI_OFF && WiFi.channel()==6);
    failure=stage;
    bridge.begin();
    if (!stage) assert(bridge._initialized);
    else assert(!bridge._initialized);
    bridge.end();
    coherent(); // A failed repair must not destroy the SDK under a cached facade.
    failure=0;
    stopBridgeWiFiIfUnused();
    off();
    assert(WiFi.mode(WIFI_AP_STA));
    coherent();
    assert(WiFi.mode(WIFI_OFF));
    off();
  }
  // SDK shutdown errors also leave facade caches coherent for a safe retry.
  for (int stage : {16,17}) {
    assert(WiFi.mode(WIFI_STA));
    bridge.begin();
    assert(bridge._initialized);
    failure=stage;
    bridge.end();
    assert(!espnow_active);
    coherent();
    failure=0;
    stopBridgeWiFiIfUnused();
    off();
  }
}
