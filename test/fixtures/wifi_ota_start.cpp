// WiFi and the HTTP server are peripheral fakes; both OTA lifecycle methods
// are inserted verbatim from ESP32Board.cpp by test_wifi_ota_start.py.
#include <cassert>
#include <atomic>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <string>
#include <helpers/WirelessControl.h>
#define MESH_MANAGEMENT_PASSWORD_HOST_TEST 1
#include <helpers/esp32/ManagementPassword.h>
static const bool published_password = mesh::esp32::managementPassword::publish("short");

#define MESH_DEBUG_PRINTLN(...) ((void)0)
constexpr int WL_CONNECTED = 3;
constexpr int HTTP_GET = 0;
using wifi_mode_t = int;
constexpr int WIFI_OFF = 0, WIFI_STA = 1, WIFI_AP = 2;
constexpr int WIFI_MODE_NULL = WIFI_OFF, WIFI_MODE_AP = WIFI_AP;
constexpr int ESP_OK = 0, WIFI_IF_AP = 1, WIFI_IF_STA = 0;
constexpr int WIFI_AUTH_OPEN = 0, WIFI_AUTH_WPA2_PSK = 3;
constexpr int WIFI_SCAN_RUNNING = -1, WIFI_SCAN_FAILED = -2, AP_STARTED_BIT = 1;
constexpr int LISTEN = 10;
bool async_bind_allowed = true;
constexpr uint8_t WIFI_PROTOCOL_11B = 1, WIFI_PROTOCOL_11G = 2;
constexpr uint8_t WIFI_PROTOCOL_11N = 4, WIFI_PROTOCOL_LR = 8;
using esp_err_t = int;
bool allow_protocol_reset = true;
bool constrain_espnow_channel = true;
struct wifi_config_t {
  struct {
    uint8_t ssid[32] = {};
    uint8_t password[64] = {};
    uint8_t ssid_len = 0, ssid_hidden = 0, channel = 1;
    int authmode = WIFI_AUTH_OPEN;
  } ap;
};
enum class Fault { None, Ssid, Hidden, Channel, Protocol, Mode, StartEvent, ZeroIp,
                   DelayedStart, ModeRead, ConfigRead, ProtocolRead };
enum class ScanFault { None, FailedFacade, StopRejected, Timeout, DelayedStop };
static unsigned clock_ms = 100;
unsigned millis() { return clock_ms; }
void delay(unsigned);
struct IPAddress {
  std::string value;
  IPAddress() = default;
  IPAddress(unsigned a, unsigned b, unsigned c, unsigned d) {
    value = std::to_string(a) + "." + std::to_string(b) + "."
        + std::to_string(c) + "." + std::to_string(d);
  }
  std::string toString() const { return value; }
  explicit operator uint32_t() const { return value.empty() || value == "0.0.0.0" ? 0 : 1; }
};
struct WiFiFake {
  bool connected = true, ap = false, allow_ap = true, radio = true, ap_started = false;
  bool mode_allowed = true, sdk_scan_active = false;
  unsigned starts = 0, sta_preparations = 0, radio_stops = 0, scans = 0, scan_stops = 0;
  unsigned pending_start = 0, pending_stop = 0, scan_remaining = 0;
  int scan_result = WIFI_SCAN_FAILED;
  Fault fault = Fault::None;
  ScanFault scan_fault = ScanFault::None;
  wifi_config_t live_config;
  struct APStatus { bool started() const; } AP;
  int ap_channel = 0;
  uint8_t ap_protocol = 15; // Valid driver state, but LR beacons hide the AP from ordinary clients.
  int status() const { return connected ? WL_CONNECTED : 0; }
  int getMode() const { return (ap ? WIFI_AP : 0) | (radio ? WIFI_STA : 0); }
  int getStatusBits() const { return ap_started ? AP_STARTED_BIT : 0; }
  bool mode(int requested) {
    if (!mode_allowed) return false;
    radio = requested != WIFI_OFF;
    if (!radio) { ++radio_stops; connected = false; }
    if (requested == WIFI_STA) ++sta_preparations;
    if (!(requested & WIFI_AP)) {
      ap = false;
      if (ap_started) pending_stop = 60;
    }
    return true;
  }
  IPAddress localIP() const { return IPAddress(10, 20, 30, 40); }
  // A configured netif IP is independent from the live SDK AP/event state.
  IPAddress softAPIP() const { return fault != Fault::ZeroIp && ap
      ? IPAddress(192, 168, 4, 1) : IPAddress(0, 0, 0, 0); }
  bool softAPConfig(IPAddress ip, IPAddress gateway, IPAddress mask) {
    assert(ip.value == "192.168.4.1" && gateway.value == ip.value);
    assert(mask.value == "255.255.255.0");
    assert(!sdk_scan_active);
    return allow_ap;
  }
  bool softAP(const char* ssid, const char* password, int channel = 1) {
    assert(ssid && strlen(ssid) <= 32);
    ap_channel = channel;
    ++starts; ap = allow_ap; radio = true;
    if (ap) {
      live_config = {};
      live_config.ap.authmode = password && password[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
      if (password) strcpy(reinterpret_cast<char*>(live_config.ap.password), password);
      const char* live_ssid = fault == Fault::Ssid ? "old-hotspot" : ssid;
      live_config.ap.ssid_len = uint8_t(strlen(live_ssid));
      memcpy(live_config.ap.ssid, live_ssid, strlen(live_ssid));
      live_config.ap.ssid_hidden = fault == Fault::Hidden;
      live_config.ap.channel = fault == Fault::Channel ? 11 : channel;
      ap_started = fault != Fault::StartEvent && fault != Fault::DelayedStart;
      if (fault == Fault::DelayedStart) pending_start = 60;
    }
    return ap;
  }
  void softAPdisconnect(bool) { ap = false; ap_started = false; }
  void scanDelete() { if (!sdk_scan_active) scan_result = WIFI_SCAN_FAILED; }
  int scanComplete() const { return scan_result; }
  int scanNetworks(bool async, bool hidden, bool passive, unsigned maximum, uint8_t channel) {
    assert(async && !hidden && !passive && maximum == 200);
    assert(channel == (constrain_espnow_channel ? 6 : 0));
    assert(radio && !ap && !ap_started);
    ++scans; sdk_scan_active = true; scan_result = WIFI_SCAN_RUNNING;
    if (scan_fault == ScanFault::None) scan_remaining = 100;
    else if (scan_fault == ScanFault::FailedFacade || scan_fault == ScanFault::StopRejected)
      scan_result = WIFI_SCAN_FAILED;
    return scan_result;
  }
} WiFi;
bool WiFiFake::APStatus::started() const { return WiFi.ap_started; }
void delay(unsigned amount) {
  clock_ms += amount;
  auto consume = [amount](unsigned& pending) {
    if (!pending) return false;
    pending -= std::min(pending, amount); return pending == 0;
  };
  if (consume(WiFi.pending_stop)) WiFi.ap_started = false;
  if (consume(WiFi.pending_start)) WiFi.ap_started = true;
  if (consume(WiFi.scan_remaining)) {
    WiFi.sdk_scan_active = false;
    WiFi.scan_result = WiFi.scan_fault == ScanFault::None ? 2 : WIFI_SCAN_FAILED;
  }
}
int esp_wifi_scan_stop() {
  ++WiFi.scan_stops;
  if (WiFi.scan_fault == ScanFault::StopRejected) return -1;
  if (WiFi.scan_fault == ScanFault::DelayedStop) WiFi.scan_remaining = 60;
  else { WiFi.sdk_scan_active = false; WiFi.scan_result = WIFI_SCAN_FAILED; }
  return ESP_OK;
}
int esp_wifi_get_mode(wifi_mode_t* value) {
  if (WiFi.fault == Fault::ModeRead) return -1;
  *value = WiFi.fault == Fault::Mode ? WIFI_STA : WiFi.getMode(); return ESP_OK;
}
int esp_wifi_get_config(int interface_id, wifi_config_t* value) {
  assert(interface_id == WIFI_IF_AP);
  if (WiFi.fault == Fault::ConfigRead) return -1;
  *value = WiFi.live_config; return ESP_OK;
}
int esp_wifi_get_protocol(int interface_id, uint8_t* value) {
  assert(interface_id == WIFI_IF_AP);
  if (WiFi.fault == Fault::ProtocolRead) return -1;
  *value = WiFi.fault == Fault::Protocol ? WIFI_PROTOCOL_LR : WiFi.ap_protocol;
  return ESP_OK;
}
esp_err_t esp_wifi_set_protocol(int interface_id, uint8_t protocols) {
  assert(interface_id == WIFI_IF_AP || interface_id == WIFI_IF_STA);
  if (!allow_protocol_reset) return -1;
  if (interface_id == WIFI_IF_AP) WiFi.ap_protocol = protocols;
  return ESP_OK;
}
namespace mesh { namespace wifi {
bool espNowChannelConstrained() { return constrain_espnow_channel; }
uint8_t activeEspNowChannel() { return 6; }
uint8_t stationScanChannel() { return constrain_espnow_channel ? 6 : 0; }
esp_err_t applyProtocolMask(int interface_id) { return esp_wifi_set_protocol(interface_id, 15); }
@AP_PROTOCOL_POLICY@
} }
@AP_LIFECYCLE_POLICY@
static unsigned server_starts = 0;
struct AsyncWebServerRequest {
  void send(int, const char*, const char*) {}
  void send(int, const char*, const char*, const char*) {}
};
struct AsyncWebServer {
  bool listening = false;
  explicit AsyncWebServer(int) {}
  template<class F> void on(const char*, int, F) {}
  void begin() { ++server_starts; listening = async_bind_allowed; }
  void end() { listening = false; }
  int state() const { return listening ? LISTEN : 0; }
};
struct Elegant {
  bool busy = false;
  void setID(const char*) {}
  void useManagementPassword() {}
  bool authenticateRequest(AsyncWebServerRequest*) { return true; }
  void begin(AsyncWebServer*) {}
  bool setEnabled(bool enabled) { return enabled || !busy; }
} AsyncElegantOTA;
static AsyncWebServer* async_ota_host = nullptr;
static int SPIFFS;
struct ESP32Board {
  bool inhibit_sleep = false;
  bool ota_started_ap = false;
  bool ota_started_radio = false;
#ifdef LIGHTWEIGHT_WIFI_OTA
  void* ota_server = nullptr;
#else
  AsyncWebServer* ota_server = nullptr;
#endif
  const char* getManufacturerName() const { return "test"; }
  bool startOTAUpdate(const char*, char*, bool);
  bool stopOTAUpdate(char*);
  static bool rotateOTAAccessPoint(void*) { return true; }
};
struct LightweightServer {
  bool allow = true, running = false, allow_stop = true;
  bool begin(ESP32Board*) { ++server_starts; running = allow; return allow; }
  bool isRunning() const { return running; }
  bool end() { running = false; return allow_stop; }
} lightweight_ota_server;
#ifdef COMPANION_RADIO_FULL
static constexpr uint16_t LIGHTWEIGHT_OTA_PORT = 8080;
#else
static constexpr uint16_t LIGHTWEIGHT_OTA_PORT = 80;
#endif
struct esp_partition_t { unsigned address; };
static bool valid_partitions = true;
const esp_partition_t* esp_ota_get_running_partition() {
  static esp_partition_t value{0x10000}; return &value;
}
const esp_partition_t* esp_ota_get_next_update_partition(void*) {
  static esp_partition_t value{0x340000}; return valid_partitions ? &value : nullptr;
}
@METHODS@

int main() {
  ESP32Board board;
  struct { char text[160] = {}; uint32_t guard = 0x12345678; } reply;
  for (uint8_t owner : {mesh::wifi::kLongRangeRadioOwner, mesh::wifi::kLongRangeBridgeOwner}) {
    mesh::wifi::setLongRangeOwner(owner, true);
    assert(!board.startOTAUpdate("test node", reply.text, true));
    assert(strstr(reply.text, "stop ESP-NOW") && WiFi.starts == 0 && !WiFi.ap);
    assert(!board.inhibit_sleep && !board.ota_server);
    assert(!mesh::wifi::startOpenAccessPoint("MeshCore-OTA", false));
    assert(WiFi.starts == 0 && !WiFi.ap);
    mesh::wifi::setLongRangeOwner(owner, false);
  }
  auto start = [&](bool ap, const char* ip, const char* instruction) {
    assert(board.startOTAUpdate("test node", reply.text, ap));
    assert(board.inhibit_sleep && board.ota_server);
    const std::string url = std::string("Started: http://") + ip
#ifdef COMPANION_RADIO_FULL
        + ":8080"
#endif
        + "/update";
    assert(strncmp(reply.text, url.c_str(), url.size()) == 0);
    assert(strstr(reply.text, instruction));
    if (ap || !WiFi.connected) {
      assert(WiFi.ap_protocol == 7);
      assert(WiFi.ap_channel == (constrain_espnow_channel ? 6 : 1));
    }
    assert(strlen(reply.text) < 160 && reply.guard == 0x12345678);
  };
  // Normal start reports the actual LAN address and does not disconnect it.
  start(false, "10.20.30.40", "same WiFi/LAN");
  assert(WiFi.connected && !WiFi.ap && WiFi.starts == 0 && server_starts == 1);
  start(false, "10.20.30.40", "same WiFi/LAN");
  assert(server_starts == 1);

  // A failed switch must leave the existing uploader protected from sleep.
  WiFi.allow_ap = false;
  assert(!board.startOTAUpdate("test node", reply.text, true));
  assert(strcmp(reply.text, "ERR: OTA WiFi failed") == 0);
  assert(board.inhibit_sleep && board.ota_server && WiFi.connected);
  WiFi.allow_ap = true;
  // In particular, an already-running LAN server must honor a later `ap`.
  start(true, "192.168.4.1", "Join WiFi MeshCore-OTA");
  assert(WiFi.ap && WiFi.connected && server_starts == 1);
  const auto ap_starts = WiFi.starts;
  WiFi.ap_protocol = 15; // Repair an existing AP without dropping its clients.
  start(true, "192.168.4.1", "Join WiFi MeshCore-OTA");
  assert(WiFi.starts == ap_starts); // Healthy repeats retain associated clients.
  WiFi.ap = false; // An external driver stop invalidates cached AP ownership.
  start(true, "192.168.4.1", "Join WiFi MeshCore-OTA");
  assert(WiFi.starts == ap_starts + 1);
#ifndef LIGHTWEIGHT_WIFI_OTA
  auto* existing_server = board.ota_server;
  existing_server->listening = false;
  async_bind_allowed = false;
  assert(!board.startOTAUpdate("test node", reply.text, true));
  assert(strstr(reply.text, "OTA server failed") && board.inhibit_sleep);
  async_bind_allowed = true;
  start(true, "192.168.4.1", "Join WiFi MeshCore-OTA");
  assert(board.ota_server == existing_server && existing_server->listening);
  AsyncElegantOTA.busy = true;
  assert(!board.stopOTAUpdate(reply.text));
  assert(strstr(reply.text, "OTA upload active") && board.inhibit_sleep);
  assert(board.ota_server == existing_server && existing_server->listening);
  AsyncElegantOTA.busy = false;
#endif
#ifdef LIGHTWEIGHT_WIFI_OTA
  lightweight_ota_server.allow_stop = false;
  assert(!board.stopOTAUpdate(reply.text));
  assert(strstr(reply.text, "OTA stopping") && board.inhibit_sleep && board.ota_server);
  assert(!board.startOTAUpdate("test node", reply.text, true));
  assert(strstr(reply.text, "retry stop ota first"));
  lightweight_ota_server.allow_stop = true;
#endif
  assert(board.stopOTAUpdate(reply.text));
  assert(WiFi.scans == 0); // Raising an AP preserves the live LAN without scanning.
  assert(!board.inhibit_sleep && !board.ota_server && !WiFi.ap && WiFi.connected);

  constrain_espnow_channel = false;
  WiFi.connected = false;
  allow_protocol_reset = false;
  assert(!board.startOTAUpdate("test node", reply.text, false));
  assert(strcmp(reply.text, "ERR: OTA WiFi failed") == 0);
  assert(!board.inhibit_sleep && !board.ota_server && !WiFi.ap);
  allow_protocol_reset = true;
  start(false, "192.168.4.1", "Join WiFi MeshCore-OTA");
  assert(WiFi.ap && !WiFi.connected);
  assert(board.stopOTAUpdate(reply.text));
  WiFi.allow_ap = false;
  assert(!board.startOTAUpdate("test node", reply.text, false));
  assert(strcmp(reply.text, "ERR: OTA WiFi failed") == 0);
  assert(!board.inhibit_sleep && !board.ota_server);
#ifndef LIGHTWEIGHT_WIFI_OTA
  WiFi.allow_ap = true;
  async_bind_allowed = false;
  assert(!board.startOTAUpdate("test node", reply.text, false));
  assert(strstr(reply.text, "OTA server failed"));
  assert(!board.inhibit_sleep && !board.ota_server && !WiFi.ap);
  async_bind_allowed = true;
#endif
  // Cold start/stop must restore OFF without leaking the temporary STA radio.
  WiFi.allow_ap = true;
  WiFi.radio = false;
  WiFi.connected = false;
  start(true, "192.168.4.1", "Join WiFi MeshCore-OTA");
  assert(WiFi.scans > 0); // Offline OTA uses STA-only scan-first, including minimal.
  assert(board.ota_started_radio && WiFi.radio && WiFi.ap_started);
  assert(board.stopOTAUpdate(reply.text));
  assert(!WiFi.radio && !board.ota_started_radio);

  // A facade-successful AP cannot report success with any unusable live state.
  for (Fault fault : {Fault::Ssid, Fault::Hidden, Fault::Channel, Fault::Protocol,
                     Fault::Mode, Fault::StartEvent, Fault::ZeroIp,
                     Fault::ModeRead, Fault::ConfigRead, Fault::ProtocolRead}) {
    WiFi.fault = fault;
    const unsigned started = millis();
    assert(!board.startOTAUpdate("test", reply.text, true));
    assert(strcmp(reply.text, "ERR: OTA WiFi failed") == 0);
    assert(!WiFi.ap && !WiFi.radio && !board.inhibit_sleep && !board.ota_server);
    assert(millis() - started < 5000);
  }
  WiFi.fault = Fault::DelayedStart;
  start(true, "192.168.4.1", "Join WiFi MeshCore-OTA");
  assert(WiFi.ap_started);
  assert(board.stopOTAUpdate(reply.text));
  WiFi.fault = Fault::None;
  // Failed/timeout scans must stop in the SDK before a hotspot can start.
  for (ScanFault fault : {ScanFault::FailedFacade, ScanFault::Timeout, ScanFault::DelayedStop}) {
    WiFi.scan_fault = fault;
    const unsigned started = millis();
    start(true, "192.168.4.1", "Join WiFi MeshCore-OTA");
    assert(!WiFi.sdk_scan_active && millis() - started < 5000);
    assert(board.stopOTAUpdate(reply.text));
  }
  WiFi.scan_fault = ScanFault::StopRejected;
  const unsigned ap_before_scan_failure = WiFi.starts;
  assert(!board.startOTAUpdate("test", reply.text, true));
  assert(WiFi.starts == ap_before_scan_failure && !WiFi.ap && !WiFi.radio);
  WiFi.sdk_scan_active = false;
  WiFi.scan_fault = ScanFault::None;
  // A later ESP-NOW owner or associated station protects its radio on stop.
  start(true, "192.168.4.1", "Join WiFi MeshCore-OTA");
  constrain_espnow_channel = true;
  assert(board.stopOTAUpdate(reply.text));
  assert(WiFi.radio && !WiFi.ap);
  constrain_espnow_channel = false;
  WiFi.radio = false;
  start(true, "192.168.4.1", "Join WiFi MeshCore-OTA");
  WiFi.connected = true;
  assert(board.stopOTAUpdate(reply.text));
  assert(WiFi.radio && WiFi.connected);
  WiFi.connected = false;
  // A pending wireless shutdown must not race a newly started uploader.
  struct Backend : mesh::wireless::Backend {
    uint8_t active = mesh::wireless::All;
    uint8_t available() const override { return mesh::wireless::All; }
    uint8_t enabled() const override { return active; }
    uint8_t clients() const override { return 0; }
    mesh::wireless::Result set(uint8_t service, bool on) override {
      if (on) active |= service; else active &= ~service;
      return mesh::wireless::Result::Done;
    }
  } backend;
  auto& control = mesh::wireless::control();
  control.begin(backend);
  assert(control.handle("set 2.4ghz off", reply.text, 160, 100,
                        mesh::wireless::Independent));
  assert(!board.startOTAUpdate("test node", reply.text, false));
  assert(strstr(reply.text, "wireless change pending"));
  control.service(350);
  assert(control.masterOff() && control.blocked(mesh::wireless::WiFi));
  WiFi.allow_ap = true;
  start(false, "192.168.4.1", "Join WiFi MeshCore-OTA");
  assert(!control.masterOff() && !control.blocked(mesh::wireless::WiFi));
  assert(control.blocked(mesh::wireless::Bluetooth) && control.blocked(mesh::wireless::EspNow));
  assert(board.stopOTAUpdate(reply.text));
#ifdef LIGHTWEIGHT_WIFI_OTA
  WiFi.allow_ap = true;
  lightweight_ota_server.allow = false;
  assert(!board.startOTAUpdate("test node", reply.text, true));
  assert(strcmp(reply.text, "ERR: OTA server failed") == 0);
  assert(!board.inhibit_sleep && !board.ota_server && !WiFi.ap);
#ifdef COMPANION_RADIO_FULL
  valid_partitions = false;
  assert(!board.startOTAUpdate("test node", reply.text, false));
  assert(strstr(reply.text, "partition layout requires a USB firmware update"));
#endif
#endif
}
