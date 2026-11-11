#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <helpers/CompanionWiFiStatus.h>
#include <helpers/WebConfigBatch.h>
#include <helpers/esp32/WiFiCoordinator.h>
#include <helpers/WiFiConnectionDiagnostics.h>

enum { WL_IDLE_STATUS, WL_NO_SSID_AVAIL, WL_SCAN_COMPLETED, WL_CONNECTED,
       WL_CONNECT_FAILED, WL_CONNECTION_LOST, WL_DISCONNECTED };
using wl_status_t = int;
uint32_t millis() { return 1000; }
size_t strlcpy(char* out, const char* value, size_t capacity) {
  if (capacity) snprintf(out, capacity, "%s", value);
  return strlen(value);
}
struct IP {
  std::string toString() const { return "192.168.0.2"; }
};
struct {
  int state = WL_DISCONNECTED;
  int status() const { return state; }
  std::string SSID() const { return "iot"; }
  IP localIP() const { return {}; }
  int RSSI() const { return -48; }
} WiFi;
namespace mesh {
namespace wireless {
enum { WiFi = 1 };
struct Control { bool blocked(int) const { return false; } bool backgroundBlocked(int service) const {return blocked(service);} };
Control& control() { static Control value; return value; }
}
namespace wifi {
ConnectionDiagnosticsSnapshot diagnostic;
void appendStationDiagnosticsHint(char* reply, size_t capacity, const char* ssid) {
  if (WiFi.status() != WL_CONNECTED && strcmp(ssid, diagnostic.ssid) == 0)
    appendConnectionFailureHint(reply, capacity, diagnostic, millis());
}
constexpr uint8_t kDefaultPowerSave = 0;
enum class CredentialState { Absent };
struct Credentials {};
CredentialState readCredentials(Credentials&) { return CredentialState::Absent; }
}
}
#define portENTER_CRITICAL(mux) ((void)(mux))
#define portEXIT_CRITICAL(mux) ((void)(mux))
static int s_wc_route_mux;
struct Listener { void end() {} };
struct DNS { void stop() {} };
class WebConfigServer {
public:
  enum Mode { MODE_OFF, MODE_SETUP, MODE_CONNECTING, MODE_LAN };
  static WebConfigServer* _active;
  Mode _mode = MODE_OFF, _start_mode = MODE_OFF;
  bool _stopping = false, _start_retryable = false, _stop_warned = false;
  bool _owns_wifi = true, _runtime_wifi_ownership_managed = true;
  bool _retry_saved_wifi_in_setup = false;
  uint32_t _connect_deadline = 0, _start_retry_at = 0;
  uint32_t _start_retry_delay = 5000, _stop_warn_at = 0;
  static constexpr uint32_t STOP_WARN_MS = 1000;
  char _wifi_ssid[32] = "iot";
  Listener* _server = nullptr;
  DNS* _dns = nullptr;
  unsigned attempts = 0;
  static bool configured;
  static bool loadStandaloneWiFi(char* ssid, size_t size, char*, size_t,
                                 uint8_t*, const void*) {
    snprintf(ssid, size, "%s", configured ? "iot" : "");
    return configured;
  }
  static bool getSetupInfo(char* ssid, size_t ssid_size, char* ip, size_t ip_size) {
    if (!_active || _active->_mode != MODE_SETUP || _active->_stopping) return false;
    snprintf(ssid, ssid_size, "MC-D177");
    snprintf(ip, ip_size, "192.168.4.1");
    return true;
  }
  static bool formatWiFiStatus(char*, size_t,
      const mesh::wifi::CompanionWiFiRuntimeState* = nullptr,
      const void* = nullptr, const WebConfigServer* = nullptr);
  bool beginSavedStation(uint32_t) { ++attempts; return true; }
  bool startSetupMode(char*) { _mode = MODE_SETUP; attachRoutes(); return true; }
  bool startLanMode(char*) { _mode = MODE_LAN; attachRoutes(); return true; }
  bool startAutoMode(char*);
  void requestStop();
  void attachRoutes();
  void detachRoutes();
  @IS_RUNNING@
};
WebConfigServer* WebConfigServer::_active = nullptr;
bool WebConfigServer::configured = true;
@METHODS@

struct MyMesh {
  WebConfigServer* _webconfig = nullptr;
  const void* canonicalWiFiLegacyPrefs() const { return this; }
  bool getWiFiStatus(char*) const;
};
@ROLE_STATUS@

int main() {
  char reply[160] = {};
  MyMesh mesh;
  assert(mesh.getWiFiStatus(reply));
  assert(strstr(reply, "> WiFi: idle; saved network: 'iot'"));

  // The first saved-network join has no HTTP routes to publish. The live
  // session still owns WiFi and must report connecting until its join finishes.
  WebConfigServer portal;
  mesh._webconfig = &portal;
  assert(portal.startAutoMode(reply) && portal.isRunning());
  assert(portal.attempts == 1 && WebConfigServer::_active == nullptr);
  assert(mesh.getWiFiStatus(reply));
  assert(strcmp(reply, "> WiFi: connecting to 'iot'") == 0);
  assert(WebConfigServer::_active == nullptr);

  // An MQTT/UDP owner may perform the join while WebConfig waits for it.
  portal.requestStop();
  assert(!portal.isRunning() && WebConfigServer::_active == nullptr);
  assert(mesh.getWiFiStatus(reply) && strstr(reply, "> WiFi: idle; saved network: 'iot'"));
  portal._stopping = false;
  portal._owns_wifi = false;
  assert(portal.startAutoMode(reply) && portal.isRunning() && portal.attempts == 1);
  assert(strstr(reply, "waiting for WiFi owner"));
  assert(mesh.getWiFiStatus(reply) && strcmp(reply, "> WiFi: connecting to 'iot'") == 0);
  assert(WebConfigServer::_active == nullptr);

  // HTTP admission appears only once routes attach; teardown removes it even
  // though the role retains its object for a subsequent start.
  portal._mode = WebConfigServer::MODE_LAN;
  portal.attachRoutes();
  WiFi.state = WL_CONNECTED;
  assert(mesh.getWiFiStatus(reply));
  assert(strcmp(reply, "> WiFi: connected to 'iot'; IP: 192.168.0.2; signal: -48 dBm") == 0);
  WiFi.state = WL_DISCONNECTED;
  assert(mesh.getWiFiStatus(reply) && strcmp(reply, "> WiFi: disconnected from 'iot'") == 0);
  portal._mode = WebConfigServer::MODE_SETUP;
  assert(mesh.getWiFiStatus(reply));
  assert(strcmp(reply, "> WiFi: setup hotspot 'MC-D177'; open http://192.168.4.1/ to configure WiFi") == 0);
  portal._retry_saved_wifi_in_setup = true;
  assert(mesh.getWiFiStatus(reply));
  assert(strcmp(reply, "> WiFi: could not connect to 'iot'; setup hotspot 'MC-D177' at http://192.168.4.1/") == 0);
  // A recovery hotspot can remain active after the station joins. Do not
  // continue telling the operator that WiFi failed in this transitional state.
  WiFi.state = WL_CONNECTED;
  assert(mesh.getWiFiStatus(reply));
  assert(strcmp(reply, "> WiFi: connected to 'iot'; setup hotspot 'MC-D177' at http://192.168.4.1/") == 0);
  WiFi.state = WL_DISCONNECTED;
  portal._retry_saved_wifi_in_setup = false;
  portal.requestStop();
  assert(!portal.isRunning() && portal._stopping && WebConfigServer::_active == nullptr);
  assert(mesh.getWiFiStatus(reply) && strstr(reply, "> WiFi: idle; saved network: 'iot'"));

  // Callers without an explicit session retain active-route and Companion
  // fallback behavior, including a station owned outside WebConfig.
  portal._stopping = false;
  portal._mode = WebConfigServer::MODE_CONNECTING;
  portal.attachRoutes();
  assert(WebConfigServer::formatWiFiStatus(reply, sizeof(reply)));
  assert(strcmp(reply, "> WiFi: connecting to 'iot'") == 0);
  portal.requestStop();
  for (const auto state : {mesh::wifi::CompanionWiFiRuntimeState{false, false, false},
                           {true, false, false}, {true, true, true}, {true, true, false}}) {
    assert(WebConfigServer::formatWiFiStatus(reply, sizeof(reply), &state));
    const char* wanted = !state.requested ? "> WiFi: off; saved network: 'iot'"
        : !state.active ? "> WiFi: enabled, not connecting; saved network: 'iot'"
        : state.credential_reload_pending ? "> WiFi: reconnect scheduled for 'iot'"
                                         : "> WiFi: connecting or retrying 'iot'";
    assert(strncmp(reply, wanted, strlen(wanted)) == 0);
  }
  const mesh::wifi::CompanionWiFiRuntimeState companion{true, true, false};
  WiFi.state = WL_CONNECTED;
  assert(WebConfigServer::formatWiFiStatus(reply, sizeof(reply), &companion));
  assert(strstr(reply, "> WiFi: connected to 'iot'"));
  WiFi.state = WL_DISCONNECTED;
  for (const int state : {WL_NO_SSID_AVAIL, WL_CONNECT_FAILED, WL_CONNECTION_LOST}) {
    WiFi.state = state;
    assert(mesh.getWiFiStatus(reply) && strstr(reply, "iot"));
    assert(!strstr(reply, "> WiFi: off;"));
  }
  WebConfigServer::configured = false;
  assert(mesh.getWiFiStatus(reply) && strstr(reply, "> WiFi: no saved network;"));
  for (size_t capacity = 1; capacity <= 160; ++capacity) {
    char guarded[164];
    memset(guarded, 'X', sizeof(guarded));
    assert(WebConfigServer::formatWiFiStatus(guarded, capacity));
    assert(memchr(guarded, 0, capacity));
    for (size_t i = capacity; i < sizeof(guarded); ++i) assert(guarded[i] == 'X');
  }
  assert(!WebConfigServer::formatWiFiStatus(nullptr, sizeof(reply)));
  assert(!WebConfigServer::formatWiFiStatus(reply, 0));

  // Real role formatter and suffix helper retain the hotspot address while
  // forwarding the saved-network context into the shared diagnostic adapter.
  WebConfigServer::configured = true;
  portal._stopping = false;
  portal._mode = WebConfigServer::MODE_SETUP;
  portal._retry_saved_wifi_in_setup = true;
  portal.attachRoutes();
  WiFi.state = WL_DISCONNECTED;
  auto& diagnostic = mesh::wifi::diagnostic;
  strcpy(diagnostic.ssid, "iot");
  diagnostic.has_disconnect = true;
  diagnostic.last_reason = 202;
  diagnostic.last_disconnect_ms = 0;
  assert(mesh.getWiFiStatus(reply));
  assert(strstr(reply, "http://192.168.4.1/"));
  assert(strstr(reply, "; last drop: 202 auth failed; 1s ago"));
  strcpy(diagnostic.ssid, "old network");
  assert(mesh.getWiFiStatus(reply));
  assert(!strstr(reply, "last drop"));
}
