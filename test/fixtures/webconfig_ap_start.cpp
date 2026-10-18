// Only peripheral endpoints are doubles. The readiness helper and startup
// method are extracted verbatim from production WebConfigServer.cpp.
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <string>
#define MESH_MANAGEMENT_PASSWORD_HOST_TEST
#include <helpers/esp32/ManagementPassword.h>
namespace password = mesh::esp32::managementPassword;
#ifndef TEST_ADMIN_PASSWORD
#define TEST_ADMIN_PASSWORD ""
#endif

using esp_err_t = int;
using wifi_mode_t = int;
using wifi_auth_mode_t = int;
constexpr esp_err_t ESP_OK = 0;
constexpr int WIFI_AUTH_OPEN = 0, WIFI_AUTH_WPA2_PSK = 3;
constexpr int WL_CONNECTED = 3, WL_DISCONNECTED = 6;
constexpr int WIFI_SCAN_FAILED = -2, WIFI_SCAN_RUNNING = -1;
constexpr int WIFI_OFF = 0, WIFI_STA = 1, WIFI_AP = 2, WIFI_AP_STA = 3;
constexpr int WIFI_MODE_NULL = WIFI_OFF, WIFI_MODE_STA = WIFI_STA;
constexpr int WIFI_MODE_AP = WIFI_AP, WIFI_MODE_APSTA = WIFI_AP_STA;
constexpr int WIFI_IF_STA = 0, WIFI_IF_AP = 1;
constexpr uint8_t WIFI_PROTOCOL_11B = 1, WIFI_PROTOCOL_11G = 2;
constexpr uint8_t WIFI_PROTOCOL_11N = 4, WIFI_PROTOCOL_LR = 8;
constexpr int WIFI_AP_STARTED_BIT = 1 << 0;
constexpr int AP_STARTED_BIT = WIFI_AP_STARTED_BIT;
constexpr int MALLOC_CAP_INTERNAL = 1;
#define WEBCONFIG_AP_PREFIX "MC"
#ifndef ESP_ARDUINO_VERSION_MAJOR
#define ESP_ARDUINO_VERSION_MAJOR 2
#endif

struct IPAddress {
  uint32_t value = 0;
  IPAddress() = default;
  IPAddress(uint8_t a, uint8_t b, uint8_t c, uint8_t d)
      : value((uint32_t(a) << 24) | (uint32_t(b) << 16) | (uint32_t(c) << 8) | d) {}
  explicit operator uint32_t() const { return value; }
  std::string toString() const {
    char text[16];
    snprintf(text, sizeof(text), "%u.%u.%u.%u", (value >> 24) & 255,
             (value >> 16) & 255, (value >> 8) & 255, value & 255);
    return text;
  }
};
struct wifi_config_t {
  struct {
    uint8_t ssid[32] = {};
    uint8_t password[64] = {};
    uint8_t ssid_len = 0, ssid_hidden = 0, channel = 1;
    wifi_auth_mode_t authmode = WIFI_AUTH_OPEN;
  } ap;
};
enum class Fault {
  None, StaleSsid, HiddenSsid, WrongChannel, WrongProtocol, MissingMode,
  NotStarted, ZeroIp, DelayedStart, StaleOnce, SetterFailure, WrongAuth, WrongPassword, StillConnected,
  ModeSetterFailure, IpConfigSetterFailure, StaProtocolSetterFailure,
  SdkModeReadFailure, SdkConfigReadFailure, SdkProtocolReadFailure
};
enum class ScanBehavior {
  Complete, SlowComplete, Failure, Stuck, StopDelayed, Unstoppable,
  FacadeFailedActive, FacadeFailedStopRejected
};
wifi_auth_mode_t expectedAuth() {
  return password::apPasswordEligible(TEST_ADMIN_PASSWORD) ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
}
void delay(unsigned amount);
struct WiFiFake {
  bool initialized = true, shared_espnow = false, auto_reconnect = true;
  bool configured_ip = false, ap_started = false;
  bool delayed_stop = false;
  unsigned pending_stop_ms = 0;
  unsigned stop_delay_ms = 160, config_delay_ms = 0;
  struct APStatus { bool started() const; } AP;
  int live_mode = WIFI_STA;
  uint8_t live_protocol = WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N;
  wifi_config_t live_config;
  IPAddress ap_ip{192, 168, 4, 1}, planned_ip{192, 168, 4, 1};
  Fault fault = Fault::None;
  ScanBehavior scan_behavior = ScanBehavior::Complete;
  unsigned ap_calls = 0, config_calls = 0, sta_only_calls = 0;
  unsigned ap_stops = 0, driver_stops = 0, driver_deinits = 0;
  unsigned polls = 0, scans = 0;
  unsigned scan_deletes = 0, scan_queries = 0;
  unsigned sdk_scan_stops = 0, scan_remaining_ms = 0;
  unsigned stale_picker_results = 2;
  int scan_result = 2;
  bool departed_home_channel = false, scan_stop_pending = false, sdk_scan_active = false;
  unsigned scans_during_ap = 0;
  bool mode(int mode) {
    assert(initialized);
    if (fault == Fault::ModeSetterFailure) return false;
    if (mode == WIFI_STA) ++sta_only_calls;
    const bool removing_ap = (live_mode & WIFI_AP) && !(mode & WIFI_AP);
    live_mode = mode;
    if (!(mode & WIFI_AP)) {
      if (delayed_stop && ap_started) {
        if (removing_ap) pending_stop_ms = stop_delay_ms;
      }
      else ap_started = false;
    }
    return true;
  }
  int getMode() const { return live_mode; }
  int status() const { return fault == Fault::StillConnected ? WL_CONNECTED : WL_DISCONNECTED; }
  bool setAutoReconnect(bool enabled) { auto_reconnect = enabled; return true; }
  bool disconnect(bool wifioff, bool erase) {
    assert(!wifioff && erase); return true;
  }
  bool softAPConfig(IPAddress ip, IPAddress gateway, IPAddress mask) {
    ++config_calls;
    assert(ip.value == IPAddress(192, 168, 4, 1).value && gateway.value == ip.value);
    assert(mask.value == IPAddress(255, 255, 255, 0).value);
    if (fault == Fault::IpConfigSetterFailure) return false;
    assert(scan_result != WIFI_SCAN_RUNNING && !sdk_scan_active);
    if (config_delay_ms) delay(config_delay_ms);
    configured_ip = true; planned_ip = ip;
    live_mode |= WIFI_AP;  // Arduino's configuration call enables the AP.
    return true;
  }
  bool softAP(const char* ssid, const char* password, int channel) {
    if (expectedAuth() == WIFI_AUTH_WPA2_PSK) {
      assert(password != nullptr && strcmp(password, TEST_ADMIN_PASSWORD) == 0);
    } else assert(password == nullptr);
    assert(channel == 6);
    ++ap_calls; polls = 0;
    if (fault == Fault::SetterFailure) return false;
    assert(pending_stop_ms == 0);  // A new start must not inherit old AP_START.
    live_mode |= WIFI_AP;
    live_config = {};
    const bool stale = fault == Fault::StaleSsid || (fault == Fault::StaleOnce && ap_calls == 1);
    const char* actual = stale ? "MeshCore-OTA" : ssid;
    live_config.ap.ssid_len = uint8_t(strlen(actual));
    memcpy(live_config.ap.ssid, actual, strlen(actual));
    live_config.ap.ssid_hidden = fault == Fault::HiddenSsid ? 1 : 0;
    live_config.ap.channel = fault == Fault::WrongChannel ? 11 : uint8_t(channel);
    live_config.ap.authmode = fault == Fault::WrongAuth
        ? (expectedAuth() == WIFI_AUTH_OPEN ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN)
        : expectedAuth();
    if (password) strcpy(reinterpret_cast<char*>(live_config.ap.password),
        fault == Fault::WrongPassword ? "obsolete-key" : password);
    ap_ip = fault == Fault::ZeroIp ? IPAddress() : planned_ip;
    ap_started = fault != Fault::NotStarted && fault != Fault::DelayedStart;
    // Every injected state error deliberately returns success here.
    return true;
  }
  bool softAPdisconnect(bool off) {
    assert(off); ++ap_stops;
    live_mode &= ~WIFI_AP;
    if (delayed_stop && ap_started) pending_stop_ms = stop_delay_ms;
    else ap_started = false;
    return true;
  }
  IPAddress softAPIP() const { return ap_ip; }
  int getStatusBits() const { return ap_started ? WIFI_AP_STARTED_BIT : 0; }
  int scanNetworks(bool async, bool hidden, bool passive, unsigned maximum, uint8_t channel) {
    assert(async && !hidden && !passive && (maximum == 200 || maximum == 300));
    assert(channel == (shared_espnow ? 6 : 0));
    if (live_mode & WIFI_AP) ++scans_during_ap;
    if (maximum == 200) assert(live_mode == WIFI_STA && !ap_started);
    ++scans; scan_result = WIFI_SCAN_RUNNING; sdk_scan_active = true;
    if (channel == 0) departed_home_channel = true;
    if (scan_behavior == ScanBehavior::Failure) {
      scan_result = WIFI_SCAN_FAILED;
      sdk_scan_active = false;
      return WIFI_SCAN_FAILED;
    }
    if (scan_behavior == ScanBehavior::Complete) scan_remaining_ms = 100;
    else if (scan_behavior == ScanBehavior::SlowComplete) scan_remaining_ms = 3500;
    else if (scan_behavior == ScanBehavior::FacadeFailedActive
             || scan_behavior == ScanBehavior::FacadeFailedStopRejected)
      scan_result = WIFI_SCAN_FAILED;  // Facade timeout hides the live SDK scanner.
    return WIFI_SCAN_RUNNING;
  }
  void scanDelete() {
    ++scan_deletes; stale_picker_results = 0;
    // Arduino deletes result storage; it does not cancel an SDK scan.
    if (!sdk_scan_active) scan_result = WIFI_SCAN_FAILED;
  }
  int scanComplete() { ++scan_queries; return scan_result; }
  const char* SSID(int) const { return "cached-network"; }
  int RSSI(int) const { return -50; }
  int encryptionType(int) const { return WIFI_AUTH_OPEN; }
  int channel(int) const { return 6; }
} WiFi;
bool WiFiFake::APStatus::started() const { return WiFi.ap_started; }

static uint32_t clock_ms = 100;
uint32_t millis() { return clock_ms; }
void delay(unsigned amount) {
  clock_ms += amount;
  if (WiFi.pending_stop_ms) {
    WiFi.pending_stop_ms -= std::min(amount, WiFi.pending_stop_ms);
    if (!WiFi.pending_stop_ms) WiFi.ap_started = false;
  }
  if (amount == 20 && ++WiFi.polls >= 3 && WiFi.fault == Fault::DelayedStart
      && (WiFi.live_mode & WIFI_AP))
    WiFi.ap_started = true;
  if (WiFi.scan_remaining_ms) {
    WiFi.scan_remaining_ms -= std::min(amount, WiFi.scan_remaining_ms);
    if (!WiFi.scan_remaining_ms) {
      WiFi.scan_result = WiFi.scan_stop_pending ? WIFI_SCAN_FAILED : 2;
      WiFi.sdk_scan_active = false;
      WiFi.scan_stop_pending = false;
    }
  }
}
int esp_wifi_scan_stop() {
  ++WiFi.sdk_scan_stops;
  if (WiFi.scan_behavior == ScanBehavior::Unstoppable
      || WiFi.scan_behavior == ScanBehavior::FacadeFailedStopRejected) return -1;
  if (WiFi.scan_behavior == ScanBehavior::StopDelayed) {
    WiFi.scan_stop_pending = true;
    WiFi.scan_remaining_ms = 60;
  } else {
    WiFi.scan_result = WIFI_SCAN_FAILED; WiFi.sdk_scan_active = false;
  }
  return ESP_OK;
}
esp_err_t esp_wifi_get_mode(wifi_mode_t* mode) {
  if (WiFi.fault == Fault::SdkModeReadFailure) return -1;
  *mode = WiFi.fault == Fault::MissingMode ? WIFI_STA : WiFi.live_mode;
  return ESP_OK;
}
esp_err_t esp_wifi_get_config(int interface, wifi_config_t* config) {
  assert(interface == WIFI_IF_AP);
  if (WiFi.fault == Fault::SdkConfigReadFailure) return -1;
  *config = WiFi.live_config; return ESP_OK;
}
esp_err_t esp_wifi_get_protocol(int interface, uint8_t* protocol) {
  assert(interface == WIFI_IF_AP);
  if (WiFi.fault == Fault::SdkProtocolReadFailure) return -1;
  *protocol = WiFi.fault == Fault::WrongProtocol ? WIFI_PROTOCOL_LR : WiFi.live_protocol;
  return ESP_OK;
}
esp_err_t esp_wifi_set_protocol(int interface, uint8_t protocol) {
  if (interface == WIFI_IF_AP) WiFi.live_protocol = protocol;
  else {
    assert(interface == WIFI_IF_STA);
    if (WiFi.fault == Fault::StaProtocolSetterFailure) return -1;
  }
  return ESP_OK;
}
int esp_wifi_stop() { ++WiFi.driver_stops; return ESP_OK; }
int esp_wifi_deinit() { ++WiFi.driver_deinits; WiFi.initialized = false; return ESP_OK; }
unsigned heap_caps_get_largest_free_block(int) { return 100000; }
struct { unsigned getFreeHeap() const { return 100000; } } ESP;

namespace mesh {
struct DebugPort { template<class... Args> void printf(const char*, Args...) {} };
DebugPort& usbDebugPort() { static DebugPort port; return port; }
namespace wireless {
constexpr uint8_t WiFi = 1;
struct Control { bool disabled = false; bool blocked(uint8_t) const { return disabled; } };
Control& control() { static Control control; return control; }
}
namespace wifi {
@AP_MASK@
constexpr uint8_t kProtocolMask = WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_LR;
int accessPointChannel() { return 6; }
uint8_t stationScanChannel() { return WiFi.shared_espnow ? 6 : 0; }
esp_err_t applyAccessPointProtocolMask() { return esp_wifi_set_protocol(WIFI_IF_AP, kAccessPointProtocolMask); }
esp_err_t applyProtocolMask(int interface) { return esp_wifi_set_protocol(interface, kProtocolMask); }
}
}

void stopOwnedWiFiRadio() {
  if (WiFi.shared_espnow) WiFi.mode(WIFI_STA);
  else WiFi.live_mode = WIFI_OFF;
}
struct DNSServer {
  static unsigned starts;
  void start(int port, const char* wildcard, IPAddress ip) {
    assert(port == 53 && strcmp(wildcard, "*") == 0 && ip.value != 0); ++starts;
  }
};
unsigned DNSServer::starts = 0;
struct NodeSnapshot { char admin_password[16] = {}; };
struct Callbacks { void getNodeSnapshot(NodeSnapshot& node) { strcpy(node.admin_password, TEST_ADMIN_PASSWORD); } };
struct AsyncResponseStream { std::string state; unsigned networks = 0; };
struct AsyncWebServerRequest {
  int status = 0;
  bool rescan = false;
  std::string payload;
  unsigned completed_networks = 0;
  AsyncResponseStream response;
  bool hasParam(const char* name) const { return rescan && strcmp(name, "rescan") == 0; }
  void send(int value) { status = value; }
  void send(int value, const char* type, const char* body) {
    assert(strcmp(type, "application/json") == 0); status = value; payload = body;
  }
  AsyncResponseStream* beginResponseStream(const char*) { return &response; }
  void send(AsyncResponseStream* value) {
    assert(value == &response); status = 200;
    payload = "{\"state\":\"" + value->state + "\"}";
    completed_networks = value->networks;
  }
};
// Serialization is an endpoint double; tests below exercise the complete
// production scan-request control flow rather than a copied branch.
struct JsonValue {
  std::string* text = nullptr;
  void operator=(const char* value) { if (text) *text = value; }
  template<class T> void operator=(const T&) {}
};
struct JsonObject { JsonValue operator[](const char*) { return {}; } };
struct JsonArray {
  unsigned* count;
  JsonObject createNestedObject() { ++*count; return {}; }
};
struct DynamicJsonDocument {
  std::string state;
  unsigned networks = 0;
  explicit DynamicJsonDocument(unsigned) {}
  JsonValue operator[](const char*) { return {&state}; }
  JsonArray createNestedArray(const char*) { return {&networks}; }
};
void serializeJson(DynamicJsonDocument& doc, AsyncResponseStream& response) {
  response.state = doc.state; response.networks = doc.networks;
}
struct WebConfigServer {
  enum Mode { MODE_OFF, MODE_LAN, MODE_SETUP };
  Mode _mode = MODE_OFF;
  bool _owns_wifi = true, _stopping = false;
  bool _retry_saved_wifi_in_setup = true, _setup_reconnect_in_progress = true;
  bool _was_setup_ap = false, _initial_setup = false;
  bool _setup_provisioning_privilege = false;
  uint32_t _ap_auth_generation = 0, _ap_rekey_generation = 0;
  uint32_t _ap_rekey_after = 0, _ap_rekey_deadline = 0;
  uint32_t _setup_reconnect_deadline = 5, _setup_started_at = 5, _last_activity = 0;
  const uint8_t _pub_key[2] = {0x62, 0xF2};
  char _ap_ssid[33] = {}, _wifi_ssid[32] = {};
  DNSServer* _dns = nullptr;
  Callbacks callbacks;
  Callbacks* _cb = &callbacks;
  unsigned servers = 0;
  bool auth_allowed = true;
  ~WebConfigServer() { delete _dns; }
  void createServer() { ++servers; }
  void requestNetworkAccess() {}
  void refreshNetworkPowerHold(uint32_t) {}
  void initializeManagementPassword();
  bool startSetupMode(char reply[]);
  bool checkAuth(AsyncWebServerRequest*) const { return auth_allowed; }
  void handleScan(AsyncWebServerRequest* req);
};
@METHODS@

void requireEndpoint(const WebConfigServer& portal, const char* reply, bool cached_scan = true) {
  assert(portal._mode == WebConfigServer::MODE_SETUP && portal._dns != nullptr);
  assert(WiFi.live_mode == WIFI_AP_STA && WiFi.ap_started);
  assert(WiFi.live_config.ap.ssid_len == 7);
  assert(memcmp(WiFi.live_config.ap.ssid, "MC-62F2", 7) == 0);
  assert(!WiFi.live_config.ap.ssid_hidden && WiFi.live_config.ap.channel == 6);
  assert(WiFi.live_config.ap.authmode == expectedAuth());
  assert(WiFi.live_protocol == mesh::wifi::kAccessPointProtocolMask);
  assert(WiFi.ap_ip.value != 0 && WiFi.config_calls > 0 && WiFi.sta_only_calls > 0);
  assert(WiFi.scans == 1 && WiFi.scans_during_ap == 0 && DNSServer::starts == 1);
  const bool stopped_scan = WiFi.scan_behavior != ScanBehavior::Complete
      && WiFi.scan_behavior != ScanBehavior::SlowComplete;
  assert(WiFi.scan_deletes == (stopped_scan ? 2u : 1u) && WiFi.stale_picker_results == 0);
  assert(WiFi.scan_result == (cached_scan ? 2 : WIFI_SCAN_FAILED));
  assert(!WiFi.sdk_scan_active && WiFi.sdk_scan_stops == (stopped_scan ? 1u : 0u));
  assert(!WiFi.auto_reconnect && !portal._retry_saved_wifi_in_setup);
  assert(strstr(reply, "join 'MC-62F2', http://192.168.4.1/"));
  assert(strstr(reply, expectedAuth() == WIFI_AUTH_WPA2_PSK
      ? "WiFi: admin password" : TEST_ADMIN_PASSWORD[0]
        ? "open WiFi; admin login required" : "open WiFi; no admin password"));
  assert(portal._setup_provisioning_privilege == (expectedAuth() == WIFI_AUTH_WPA2_PSK));
  if (expectedAuth() == WIFI_AUTH_WPA2_PSK)
    assert(strcmp(reinterpret_cast<const char*>(WiFi.live_config.ap.password), TEST_ADMIN_PASSWORD) == 0);
  assert(portal.servers == 1);
  assert(WiFi.initialized && WiFi.driver_stops == 0 && WiFi.driver_deinits == 0);
}
void reset(Fault fault = Fault::None) {
  auto& state = password::detail::state();
  { password::detail::Lock lock(state); state.current = {};
    state.changing = state.uploading = state.rotating = false; }
  WiFi = WiFiFake{}; WiFi.fault = fault;
  clock_ms = 100;
  DNSServer::starts = 0; mesh::wireless::control().disabled = false;
}
int main() {
  // A live LR transport must remain untouched instead of being silently
  // downgraded when a conventional setup access point is requested.
  for (uint8_t owner : {mesh::wifi::kLongRangeRadioOwner, mesh::wifi::kLongRangeBridgeOwner}) {
    reset(); mesh::wifi::setLongRangeOwner(owner, true);
    WebConfigServer portal; char reply[160] = {};
    assert(!portal.startSetupMode(reply));
    assert(strstr(reply, "stop ESP-NOW") && WiFi.live_mode == WIFI_STA);
    assert(WiFi.sta_only_calls == 0 && WiFi.ap_calls == 0 && WiFi.scans == 0);
    mesh::wifi::setLongRangeOwner(owner, false);
  }
  for (Fault fault : {Fault::StaleSsid, Fault::HiddenSsid, Fault::WrongChannel,
                     Fault::WrongProtocol, Fault::MissingMode, Fault::NotStarted,
                     Fault::ZeroIp, Fault::SetterFailure, Fault::WrongAuth, Fault::WrongPassword, Fault::StillConnected,
                     Fault::ModeSetterFailure, Fault::IpConfigSetterFailure,
                     Fault::StaProtocolSetterFailure, Fault::SdkModeReadFailure,
                     Fault::SdkConfigReadFailure, Fault::SdkProtocolReadFailure}) {
    if (fault == Fault::WrongPassword && expectedAuth() == WIFI_AUTH_OPEN) continue;
    reset(fault); WiFi.shared_espnow = true;
    WebConfigServer portal;
    char reply[160] = {};
    const uint32_t started = millis();
    assert(!portal.startSetupMode(reply));
    assert(millis() - started < 4500);
    assert(portal._mode == WebConfigServer::MODE_OFF && !portal._dns && portal.servers == 0);
    const unsigned prepared = fault != Fault::ModeSetterFailure
        && fault != Fault::StaProtocolSetterFailure ? 1u : 0u;
    assert(!strstr(reply, "AP started") && WiFi.scans == prepared
        && WiFi.scan_deletes == prepared && WiFi.scans_during_ap == 0 && DNSServer::starts == 0);
    assert(WiFi.ap_calls <= 6 && WiFi.initialized && (WiFi.live_mode & WIFI_STA));
    assert(WiFi.driver_stops == 0 && WiFi.driver_deinits == 0);
  }
  // Slow asynchronous stops and successful-but-stale setters must remain a
  // bounded CLI request, rather than exhausting every per-attempt timeout.
  reset(Fault::StaleSsid); WiFi.shared_espnow = true;
  WiFi.delayed_stop = true; WiFi.stop_delay_ms = 280; WiFi.config_delay_ms = 100;
  WiFi.live_mode = WIFI_AP_STA; WiFi.ap_started = true;
  WebConfigServer slow_failure;
  char slow_reply[160] = {};
  const uint32_t slow_started = millis();
  assert(!slow_failure.startSetupMode(slow_reply));
  assert(millis() - slow_started < 4500);
  assert(WiFi.ap_calls < 6 && !slow_failure._dns && slow_failure.servers == 0);
  assert(WiFi.initialized && (WiFi.live_mode & WIFI_STA));
  assert(!strstr(slow_reply, "AP started"));
  for (bool shared_espnow : {false, true}) {
    for (Fault fault : {Fault::None, Fault::DelayedStart, Fault::StaleOnce}) {
      reset(fault); WiFi.shared_espnow = shared_espnow;
      WebConfigServer portal;
      char reply[160] = {};
      assert(portal.startSetupMode(reply));
      requireEndpoint(portal, reply);
      if (fault == Fault::StaleOnce) assert(WiFi.ap_calls >= 2);
      if (fault == Fault::DelayedStart) assert(WiFi.polls >= 3);
    }
  }
  reset(); WiFi.scan_behavior = ScanBehavior::SlowComplete;
  WebConfigServer slow_success; char slow_success_reply[160] = {};
  const uint32_t long_scan_started = millis();
  assert(slow_success.startSetupMode(slow_success_reply));
  assert(millis() - long_scan_started < 4500);
  requireEndpoint(slow_success, slow_success_reply);
  // Completing just before the pre-scan deadline must still leave enough
  // time to bring up a delayed AP after an asynchronous old AP shutdown.
  reset(Fault::DelayedStart); WiFi.scan_behavior = ScanBehavior::SlowComplete;
  WiFi.delayed_stop = true; WiFi.stop_delay_ms = 280; WiFi.config_delay_ms = 100;
  WiFi.live_mode = WIFI_AP_STA; WiFi.ap_started = true;
  WebConfigServer slow_handoff; char slow_handoff_reply[160] = {};
  const uint32_t slow_handoff_started = millis();
  assert(slow_handoff.startSetupMode(slow_handoff_reply));
  assert(millis() - slow_handoff_started < 4500);
  requireEndpoint(slow_handoff, slow_handoff_reply);
  // A failed first AP attempt after a long successful scan must report
  // failure truthfully and return within the console reply deadline.
  reset(Fault::StaleSsid); WiFi.scan_behavior = ScanBehavior::SlowComplete;
  WiFi.shared_espnow = true; WiFi.delayed_stop = true;
  WiFi.stop_delay_ms = 280; WiFi.config_delay_ms = 100;
  WiFi.live_mode = WIFI_AP_STA; WiFi.ap_started = true;
  WebConfigServer slow_scan_failure; char slow_scan_failure_reply[160] = {};
  const uint32_t slow_scan_failure_started = millis();
  assert(!slow_scan_failure.startSetupMode(slow_scan_failure_reply));
  assert(millis() - slow_scan_failure_started < 4500);
  assert(!slow_scan_failure._dns && slow_scan_failure.servers == 0 && WiFi.scans == 1);
  assert(!strstr(slow_scan_failure_reply, "AP started"));
  // Maximum scan time, delayed SDK cancellation, old AP shutdown, and
  // unsuccessful AP readiness may exceed 4.5 seconds together. They must
  // still fit the actual five-second console reply deadline.
  reset(Fault::StaleSsid); WiFi.scan_behavior = ScanBehavior::StopDelayed;
  WiFi.shared_espnow = true; WiFi.delayed_stop = true;
  WiFi.stop_delay_ms = 280; WiFi.config_delay_ms = 100;
  WiFi.live_mode = WIFI_AP_STA; WiFi.ap_started = true;
  WebConfigServer exhausted_scan; char exhausted_scan_reply[160] = {};
  const uint32_t exhausted_scan_started = millis();
  assert(!exhausted_scan.startSetupMode(exhausted_scan_reply));
  assert(millis() - exhausted_scan_started < 5000);
  assert(!exhausted_scan._dns && exhausted_scan.servers == 0 && WiFi.scans == 1);
  assert(WiFi.sdk_scan_stops == 1 && !WiFi.sdk_scan_active);
  assert(!strstr(exhausted_scan_reply, "AP started"));
  for (ScanBehavior scan_behavior : {ScanBehavior::Failure, ScanBehavior::Stuck,
                                    ScanBehavior::StopDelayed, ScanBehavior::FacadeFailedActive}) {
    reset(); WiFi.scan_behavior = scan_behavior;
    WebConfigServer portal; char reply[160] = {};
    const uint32_t started = millis();
    assert(portal.startSetupMode(reply));
    assert(millis() - started < 4500);
    requireEndpoint(portal, reply, false);
    // An ordinary picker request must preserve the stable AP after a failed
    // pre-scan. A user-requested rescan can retry without an automatic loop.
    const unsigned scan_stops = WiFi.sdk_scan_stops;
    const unsigned scan_deletes = WiFi.scan_deletes;
    AsyncWebServerRequest scan;
    for (unsigned request = 0; request < 2; ++request) {
      portal.handleScan(&scan);
      assert(scan.status == 200);
      assert(scan.payload.find("\"state\":\"done\"") != std::string::npos);
      assert(scan.payload.find("\"networks\":[]") != std::string::npos);
      assert(scan.payload.find("\"scan_failed\":true") != std::string::npos);
      assert(WiFi.scans == 1 && WiFi.scans_during_ap == 0);
      assert(WiFi.sdk_scan_stops == scan_stops && WiFi.scan_deletes == scan_deletes);
      assert(!WiFi.sdk_scan_active && WiFi.scan_result == WIFI_SCAN_FAILED);
    }
    WiFi.scan_behavior = ScanBehavior::Complete;
    scan.rescan = true;
    portal.handleScan(&scan);
    assert(scan.status == 200 && scan.payload == "{\"state\":\"scanning\"}");
    assert(WiFi.scans == 2 && WiFi.scans_during_ap == 1 && WiFi.sdk_scan_active);
    portal.handleScan(&scan);
    assert(WiFi.scans == 2);
    delay(100); scan.rescan = false;
    portal.handleScan(&scan);
    assert(scan.payload == "{\"state\":\"done\"}" && scan.completed_networks == 2);
    assert(WiFi.scans == 2 && WiFi.scan_deletes == scan_deletes);
    assert(WiFi.sdk_scan_stops == scan_stops && !WiFi.sdk_scan_active);
  }
  for (ScanBehavior scan_behavior : {ScanBehavior::Unstoppable, ScanBehavior::FacadeFailedStopRejected}) {
    reset(); WiFi.scan_behavior = scan_behavior; WiFi.shared_espnow = true;
    WebConfigServer stuck_scanner; char stuck_reply[160] = {};
    const uint32_t stuck_started = millis();
    assert(!stuck_scanner.startSetupMode(stuck_reply));
    assert(millis() - stuck_started < 4500 && WiFi.scans == 1 && WiFi.sdk_scan_stops == 1);
    assert(WiFi.sdk_scan_active && WiFi.ap_calls == 0 && WiFi.config_calls == 0);
    assert(!stuck_scanner._dns && stuck_scanner.servers == 0 && !strstr(stuck_reply, "AP started"));
  }
  // Reuse after an OTA AP: the existing SSID/protocol must be replaced, while
  // the shared STA driver remains alive. Repeat after the setup AP is stopped.
  reset(); WiFi.shared_espnow = true; WiFi.delayed_stop = true;
  WiFi.live_mode = WIFI_AP_STA; WiFi.ap_started = true;
  memcpy(WiFi.live_config.ap.ssid, "MeshCore-OTA", 12);
  WiFi.live_config.ap.ssid_len = 12;
  WiFi.live_protocol = WIFI_PROTOCOL_LR;
  for (int iteration = 0; iteration < 2; ++iteration) {
    WebConfigServer portal; char reply[160] = {};
    assert(portal.startSetupMode(reply));
    requireEndpoint(portal, reply);
    WiFi.softAPdisconnect(true);
    assert(WiFi.live_mode == WIFI_STA && WiFi.initialized);
    WiFi.scans = WiFi.scan_deletes = DNSServer::starts = 0;
    WiFi.stale_picker_results = 2;
  }
  reset(); mesh::wireless::control().disabled = true;
  WebConfigServer disabled; char reply[160] = {};
  assert(!disabled.startSetupMode(reply) && WiFi.ap_calls == 0 && !disabled._dns);
  // Promoting an existing LAN listener changes its network, not its lifetime.
  reset(); WiFi.shared_espnow = true;
  WebConfigServer promoted;
  promoted._mode = WebConfigServer::MODE_LAN;
  promoted._owns_wifi = false;
  promoted.servers = 1;
  assert(promoted.startSetupMode(reply));
  requireEndpoint(promoted, reply);
  reset(Fault::StaleSsid); WiFi.shared_espnow = true;
  WebConfigServer failed_promotion;
  failed_promotion._mode = WebConfigServer::MODE_LAN;
  failed_promotion._owns_wifi = false;
  failed_promotion.servers = 1;
  assert(!failed_promotion.startSetupMode(reply));
  assert(failed_promotion._mode == WebConfigServer::MODE_LAN);
  assert(failed_promotion.servers == 1 && !failed_promotion._dns);
  assert(!strstr(reply, "AP started") && DNSServer::starts == 0 && WiFi.scans == 1);
  assert(WiFi.initialized && WiFi.live_mode == WIFI_STA);
  // Existing setup, owned LAN, and pending teardown must reject a new start.
  for (int busy_case = 0; busy_case < 3; ++busy_case) {
    reset(); WebConfigServer busy;
    if (busy_case == 0) busy._mode = WebConfigServer::MODE_SETUP;
    else if (busy_case == 1) busy._mode = WebConfigServer::MODE_LAN;
    else busy._stopping = true;
    assert(!busy.startSetupMode(reply));
    assert(strstr(reply, "busy") && WiFi.ap_calls == 0 && !busy._dns && busy.servers == 0);
  }
  // The first browser request reuses the completed pre-AP scan. Only an
  // explicit rescan starts scanning while the AP is serving clients.
  for (bool shared_espnow : {false, true}) {
    reset(); WiFi.shared_espnow = shared_espnow;
    WebConfigServer portal;
    assert(portal.startSetupMode(reply));
    requireEndpoint(portal, reply);
    AsyncWebServerRequest scan;
    portal.handleScan(&scan);
    assert(scan.status == 200 && scan.payload == "{\"state\":\"done\"}" && scan.completed_networks == 2);
    assert(WiFi.scans == 1 && WiFi.scan_deletes == 1);
    assert(WiFi.departed_home_channel == !shared_espnow);
    assert(WiFi.scans_during_ap == 0);
    scan.rescan = true;
    portal.handleScan(&scan);
    assert(scan.status == 200 && scan.payload == "{\"state\":\"scanning\"}");
    assert(WiFi.scans == 2 && WiFi.scan_deletes == 2 && WiFi.scans_during_ap == 1);
    portal.handleScan(&scan);
    assert(WiFi.scans == 2);  // Existing request still owns the scan.
    delay(100); scan.rescan = false;
    portal.handleScan(&scan);
    assert(scan.payload == "{\"state\":\"done\"}" && scan.completed_networks == 2);
    assert(WiFi.scans == 2 && WiFi.scan_deletes == 2 && !WiFi.stale_picker_results);
  }
  // LAN mode has no AP startup pre-scan. Its first picker request must still
  // initiate scanning automatically when no results are available.
  for (bool shared_espnow : {false, true}) {
    reset(); WiFi.shared_espnow = shared_espnow;
    WiFi.scan_result = WIFI_SCAN_FAILED;
    WebConfigServer lan;
    lan._mode = WebConfigServer::MODE_LAN; lan._owns_wifi = false; lan.servers = 1;
    AsyncWebServerRequest scan;
    lan.handleScan(&scan);
    assert(scan.status == 200 && scan.payload == "{\"state\":\"scanning\"}");
    assert(WiFi.scans == 1 && WiFi.sdk_scan_active && WiFi.scans_during_ap == 0);
    assert(WiFi.scan_deletes == 0 && WiFi.sdk_scan_stops == 0);
    lan.handleScan(&scan);
    assert(WiFi.scans == 1);
    delay(100);
    lan.handleScan(&scan);
    assert(scan.payload == "{\"state\":\"done\"}" && scan.completed_networks == 2);
    assert(WiFi.scans == 1 && !WiFi.sdk_scan_active);
    assert(lan._mode == WebConfigServer::MODE_LAN && lan.servers == 1 && !lan._dns);
  }
  reset(); WebConfigServer inactive;
  AsyncWebServerRequest inactive_request;
  inactive.handleScan(&inactive_request);
  assert(inactive_request.status == 503 && WiFi.scans == 0 && WiFi.scan_queries == 0);
  inactive._mode = WebConfigServer::MODE_SETUP; inactive.auth_allowed = false;
  inactive.handleScan(&inactive_request);
  assert(inactive_request.status == 401 && WiFi.scans == 0 && WiFi.scan_queries == 0);
  puts("WebConfig AP startup regression checks passed");
}
