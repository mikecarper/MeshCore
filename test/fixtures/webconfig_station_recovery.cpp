#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <new>
#include <string>
#include <helpers/RoomWebMailbox.h>
#include <helpers/WiFiReconnectPolicy.h>
#include <helpers/WebConfigBatch.h>
#include <helpers/esp32/WiFiCoordinator.h>
#include <helpers/WiFiConnectionDiagnostics.h>
#define MESH_MANAGEMENT_PASSWORD_HOST_TEST
#include <helpers/esp32/ManagementPassword.h>
static uint32_t clock_ms = 1000;
uint32_t millis() { return clock_ms; }
void delay(unsigned) {}
enum { WL_DISCONNECTED = 6, WL_CONNECTED = 3, WIFI_STA = 1, WIFI_AP = 2, WIFI_AP_STA = 3,
       WIFI_IF_STA = 0, ESP_OK = 0, WIFI_PS_NONE = 0,
       WIFI_PS_MAX_MODEM = 2, WIFI_PS_MIN_MODEM = 1 };
using wifi_ps_type_t = int;
using wifi_power_t = int;
using esp_err_t = int;
constexpr int ESP_FAIL = -2, ESP_ERR_INVALID_STATE = -3, WIFI_POWER_11dBm = 44, WIFI_POWER_19_5dBm = 78;
static int applied_ps = -1;
static int applied_tx = -1;
int esp_wifi_set_ps(int mode) { applied_ps = mode; return ESP_OK; }
int esp_wifi_set_max_tx_power(int8_t value) { applied_tx = value; return ESP_OK; }
int esp_wifi_get_max_tx_power(int8_t* value) { *value = static_cast<int8_t>(applied_tx); return ESP_OK; }
void* xSemaphoreCreateMutex() { return nullptr; }
struct IP {
  struct Text { const char* c_str() const { return "192.168.0.2"; } };
  Text toString() const { return {}; }
};
struct FakeWiFi {
  int state = WL_DISCONNECTED, begins = 0, disconnects = 0;
  int current_mode = WIFI_STA, ap_clients = 0, ap_disconnects = 0;
  bool fail_mode = false, fail_ap_stop = false;
  bool next_begin_connects = false;
  int cached_ps = WIFI_PS_MIN_MODEM;
  bool sta_start_pending = false;
  std::string ssid, password;
  int status() const { return state; }
  bool mode(int value) {
    if (fail_mode) return false;
    if ((value & WIFI_STA) && !(current_mode & WIFI_STA)) sta_start_pending = true;
    current_mode = value; return true;
  }
  bool setSleep(int value) {
    if (cached_ps == value) return false;
    cached_ps = value;
    if (current_mode & WIFI_STA) applied_ps = value;
    return true;
  }
  void deliverStaStart() {
    assert(sta_start_pending); sta_start_pending = false;
    applied_ps = cached_ps; // Arduino 2.x's asynchronous STA_START handler.
  }
  int getMode() const { return current_mode; }
  bool setTxPower(int value) { applied_tx = value; return true; }
  int softAPgetStationNum() const { return ap_clients; }
  void disconnect(bool, bool) { ++disconnects; state = WL_DISCONNECTED; }
  void softAPdisconnect(bool) { ++ap_disconnects; if (!fail_ap_stop) current_mode &= ~WIFI_AP; }
  IP localIP() const { return {}; }
} WiFi;
@POWER_POLICY@
namespace mesh {
namespace wireless {
enum { WiFi = 1 };
struct Control { bool blocked(int) { return false; } };
Control& control() { static Control c; return c; }
}
namespace wifi {
ConnectionDiagnostics diagnostics;
void noteStationDiagnosticPhase(ConnectionPhase phase) { diagnostics.phase(phase, millis()); }
constexpr uint8_t kPowerSaveMax = 2, kDefaultPowerSave = 0, kPowerSaveNone = 1;
constexpr bool kPrimaryEspNowRadio = false;
bool fail_protocol = false;
int applyProtocolMask(int) { return fail_protocol ? -1 : ESP_OK; }
void setStationAutoReconnect(bool) {}
int beginStation(const char* ssid, const char* password) {
  diagnostics.begin(ssid, millis());
  ++WiFi.begins; WiFi.ssid = ssid; WiFi.password = password;
  if (WiFi.next_begin_connects) WiFi.state = WL_CONNECTED;
  return WiFi.state;
}
bool enforceStationChannel() { return true; }
}
struct Debug {
  template<class... T> void printf(const char*, T...) {}
  void println(const char*) {}
};
Debug& usbDebugPort() { static Debug port; return port; }
namespace cli {
bool standaloneWiFiPasswordValid(const char* pwd) {
  const size_t len = strlen(pwd);
  if (len < 64) return true;
  if (len != 64) return false;
  for (size_t i = 0; i < len; ++i) {
    const char c = pwd[i];
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')
          || (c >= 'A' && c <= 'F'))) return false;
  }
  return true;
}
}
}
struct MQTTPrefs {
  char wifi_ssid[32] = {};
  char wifi_password[64] = {};
  uint8_t wifi_power_save = 0;
};
uint8_t effectiveWiFiPowerSave(uint8_t value) { return value; }
static bool canonical_available = true;
static const char* canonical_ssid = "saved-station";
static std::string canonical_password(64, 'A');
struct DNS { void stop() {} };
struct WCLock { explicit WCLock(void*) {} };
class WebConfigServer {
public:
  enum Mode { MODE_OFF, MODE_SETUP, MODE_CONNECTING, MODE_LAN };
  struct Callbacks { bool supportsRoomService() const { return false; } };
  Callbacks* _cb;
  void* _mqtt_prefs;
  bool _owns_wifi;
  bool _runtime_wifi_ownership_managed = false;
  const bool _standalone_wifi;
  const uint8_t* _pub_key;
  const char *_fw_ver, *_build_date, *_role, *_board_name;
  void* _mux = nullptr;
  mesh::RoomWebMailbox* _room_mailbox = nullptr;
  bool _cli_enabled = false;
  char _wifi_ssid[32] = {}, _wifi_password[65] = {};
  uint8_t _wifi_power_save = 0;
  Mode _mode = MODE_OFF;
  bool _stopping = false, _retry_saved_wifi_in_setup = false, _start_retryable = false;
  bool _setup_reconnect_in_progress = false, _board_cmds_probed = false;
  bool _was_setup_ap = false, _initial_setup = false;
  bool foreground = false;
  bool _setup_provisioning_privilege = false, _setup_wifi_handoff_pending = false;
  enum { BATCH_IDLE, BATCH_PENDING };
  int _batch_state = BATCH_IDLE;
  unsigned _reboot_at = 0, handler_refs = 0;
  bool routes_attached = true, handler_arrives_on_detach = false;
  struct Terminal { bool running = false; struct { bool pending = false; } queue; };
  Terminal* _terminal = nullptr;
  uint32_t _setup_reconnect_deadline = 0, _connect_deadline = 0;
  uint32_t _setup_started_at = 0, _last_activity = 0;
  WiFiReconnectPolicy::Tracker _wifi_reconnect_tracker;
  DNS* _dns = nullptr;
  int setup_starts = 0, server_starts = 0;
  struct Entry { const char* key; char reply[160] = {}; };
  bool _standalone_wifi_dirty = false;
  WebConfigServer(Callbacks*, void*, bool, const uint8_t*, const char*,
                  const char*, const char*, const char*, bool);
  static bool loadCliEnabled(bool value) { return value; }
  static bool loadStandaloneWiFi(char* ssid, size_t, char* pwd, size_t, uint8_t* ps, const void* legacy = nullptr) {
    if (!canonical_available) {
#ifdef WITH_MQTT_BRIDGE
      if (legacy) {
        const auto* obs = static_cast<const MQTTPrefs*>(legacy);
        strcpy(ssid, obs->wifi_ssid); strcpy(pwd, obs->wifi_password);
        *ps = obs->wifi_power_save; return ssid[0] != 0;
      }
#endif
      return false;
    }
    strcpy(ssid, canonical_ssid); strcpy(pwd, canonical_password.c_str()); *ps = 0;
    return true;
  }
  bool startSetupMode(char* reply) {
    ++setup_starts; WiFi.disconnect(false, true); _mode = MODE_SETUP;
    WiFi.current_mode = WIFI_AP_STA;
    createServer();
    _was_setup_ap = true; _retry_saved_wifi_in_setup = false;
    strcpy(reply, "setup"); return true;
  }
  bool startLanMode(char* reply) { _mode = MODE_LAN; strcpy(reply, "LAN"); return true; }
  bool startAutoMode(char*);
  bool beginSavedStation(uint32_t);
  bool prepareStationOwner();
  void tick(uint32_t);
  void createServer() { ++server_starts; }
  void probeBoardCommands() { _board_cmds_probed = true; }
  void serviceTerminal(uint32_t) {}
  void serviceRoom(uint32_t) {}
  unsigned handlerRefCount() const { return handler_refs; }
  void detachRoutes() { routes_attached = false; if (handler_arrives_on_detach) ++handler_refs; }
  void attachRoutes() { routes_attached = true; }
  bool isNetworkForegroundActive(uint32_t) const { return foreground || _mode == MODE_SETUP; }
  bool bluetoothWiFiCoexistenceRequired() { return false; }
  bool acceptWiFi(Entry& e, const char* value) {
@CREDENTIAL_BATCH@
      return false;
    }
    return true;
  }
  bool rawPskCapability() const {
    struct Doc { bool psk = false; bool& operator[](const char*) { return psk; } } doc;
@PSK_CAPABILITY@
    return doc.psk;
  }
  @SETTER@
};
@CONSTRUCTOR@
@ATTEMPT@
@PREPARE@
@START@
@RECOVERY@

int main() {
  MQTTPrefs obs;
  const uint8_t key[32] = {};
  char reply[160] = {};
  WebConfigServer portal(nullptr, &obs, true, key, "test", "test", "observer", "G2", true);
  assert(portal._standalone_wifi && portal.rawPskCapability());
  assert(strcmp(portal._wifi_ssid, canonical_ssid) == 0);
  assert(strlen(portal._wifi_password) == 64);
#if defined(TEST_UDP_STATION_OWNER)
  // UDP takes the station before the portal's first 15-second deadline.
  assert(portal.startAutoMode(reply) && WiFi.begins == 1);
  assert(portal.prepareStationOwner() && !portal._owns_wifi && !portal._connect_deadline);
  portal.tick(16000);
  assert(portal.setup_starts == 0 && WiFi.begins == 1);
  // With UDP off, ordinary WiFi-on owns its join and may expose recovery AP.
  portal.updateWiFiOwnership(true);
  portal.tick(16001);
  portal.tick(31001);
  assert(portal.setup_starts == 1 && portal._retry_saved_wifi_in_setup);
  const int begins = WiFi.begins, disconnects = WiFi.ap_disconnects;
  // A browser, queued command, handoff, reboot or active HTTP handler must
  // finish without its AP being taken away when UDP is enabled later.
  WiFi.ap_clients = 1; assert(!portal.prepareStationOwner()); WiFi.ap_clients = 0;
  portal._batch_state = WebConfigServer::BATCH_PENDING;
  assert(!portal.prepareStationOwner()); portal._batch_state = WebConfigServer::BATCH_IDLE;
  portal._setup_wifi_handoff_pending = true;
  assert(!portal.prepareStationOwner()); portal._setup_wifi_handoff_pending = false;
  portal._reboot_at = 1; assert(!portal.prepareStationOwner()); portal._reboot_at = 0;
  portal.handler_refs = 1; assert(!portal.prepareStationOwner()); portal.handler_refs = 0;
  portal.handler_arrives_on_detach = true;
  assert(!portal.prepareStationOwner()); portal.handler_arrives_on_detach = false; portal.handler_refs = 0;
  WebConfigServer::Terminal terminal;
  portal._terminal = &terminal; terminal.queue.pending = true;
  assert(!portal.prepareStationOwner()); terminal.queue.pending = false; terminal.running = true;
  assert(!portal.prepareStationOwner()); portal._terminal = nullptr;
  mesh::RoomWebMailbox room;
  uint8_t token[32] = {1}, digest[32] = {2};
  assert(room.submit(token, 1, digest, "test", 4, clock_ms) == mesh::RoomWebMailbox::Result::Accepted);
  portal._room_mailbox = &room;
  assert(!portal.prepareStationOwner());
  assert(room.begin() && !portal.prepareStationOwner()); portal._room_mailbox = nullptr;
  assert(portal.routes_attached && WiFi.ap_disconnects == disconnects);
  assert(mesh::esp32::managementPassword::initialize("admin-password"));
  mesh::esp32::managementPassword::Snapshot credential;
  assert(mesh::esp32::managementPassword::snapshot(credential));
  {
    mesh::esp32::managementPassword::UploadGuard upload(credential.generation);
    assert(upload.acquired() && !portal.prepareStationOwner());
    assert(WiFi.ap_disconnects == disconnects);
  }
  // An SDK failure must retain the recovery state and attached routes so a
  // later ordinary loop can complete the handoff, without a reboot or clear.
  WiFi.fail_ap_stop = true;
  assert(!portal.prepareStationOwner() && portal.routes_attached);
  assert(portal._mode == WebConfigServer::MODE_SETUP && portal._retry_saved_wifi_in_setup);
  WiFi.fail_ap_stop = false;
  mesh::wifi::fail_protocol = true;
  assert(!portal.prepareStationOwner() && portal.routes_attached);
  assert(portal._mode == WebConfigServer::MODE_SETUP && portal._retry_saved_wifi_in_setup);
  mesh::wifi::fail_protocol = false;
  assert(portal.prepareStationOwner() && portal._mode == WebConfigServer::MODE_CONNECTING);
  assert(!portal._retry_saved_wifi_in_setup && !portal._was_setup_ap && portal.routes_attached);
  assert(WiFi.current_mode == WIFI_STA && WiFi.begins == begins);
  // The external owner joins; the same portal follows the resulting LAN.
  WiFi.state = WL_CONNECTED; portal.tick(31002);
  assert(portal._mode == WebConfigServer::MODE_LAN);
  // An explicit setup AP is never reclassified as an automatic fallback.
  portal._mode = WebConfigServer::MODE_OFF;
  portal.startSetupMode(reply);
  assert(!portal._retry_saved_wifi_in_setup);
  const int explicit_disconnects = WiFi.ap_disconnects;
  assert(!portal.prepareStationOwner() && WiFi.current_mode == WIFI_AP_STA);
  assert(WiFi.ap_disconnects == explicit_disconnects);
  portal._stopping = true;
  assert(!portal.prepareStationOwner());
#elif defined(TEST_CREDENTIAL_PRIORITY)
  // Credential saves and async capability stay in canonical storage across
  // both ownership transitions; raw PSKs never enter MQTTPrefs[64].
  strcpy(obs.wifi_ssid, "legacy-station"); strcpy(obs.wifi_password, "legacy-secret");
  for (bool owner : {false, true}) {
    portal.updateWiFiOwnership(owner);
    assert(portal.rawPskCapability());
    WebConfigServer::Entry entry{"wifi.pwd"};
    assert(portal.acceptWiFi(entry, canonical_password.c_str()));
    assert(strcmp(entry.reply, "OK") == 0 && portal._standalone_wifi_dirty);
    assert(strlen(portal._wifi_password) == 64);
    assert(strcmp(obs.wifi_password, "legacy-secret") == 0);
    entry = WebConfigServer::Entry{"wifi.ssid"};
    assert(portal.acceptWiFi(entry, "replacement-station"));
    assert(strcmp(portal._wifi_ssid, "replacement-station") == 0);
    assert(strcmp(obs.wifi_ssid, "legacy-station") == 0);
    entry = WebConfigServer::Entry{"wifi.powersave"};
    assert(portal.acceptWiFi(entry, "none"));
    assert(portal._wifi_power_save == 1 && obs.wifi_power_save == 0);
  }
  WebConfigServer priority(nullptr, &obs, true, key, "t", "t", "observer", "G2", true);
  assert(strcmp(priority._wifi_ssid, canonical_ssid) == 0);
  canonical_available = false;
  WebConfigServer legacy(nullptr, &obs, true, key, "t", "t", "observer", "G2", true);
  assert(strcmp(legacy._wifi_ssid, "legacy-station") == 0);
  assert(strcmp(legacy._wifi_password, "legacy-secret") == 0);
#elif defined(TEST_LEGACY_COMPANION)
  WebConfigServer companion(nullptr, nullptr, false, key, "t", "t", "companion", "G2", false);
  assert(!companion._runtime_wifi_ownership_managed);
  assert(companion.startAutoMode(reply));
  assert(WiFi.begins == 1);
  companion.tick(15999);
  assert(companion.setup_starts == 0);
  companion.tick(16000);
  assert(companion.setup_starts == 1 && companion._retry_saved_wifi_in_setup);
  assert(!companion._runtime_wifi_ownership_managed);
#elif defined(TEST_EXTERNAL_OWNER)
  portal.updateWiFiOwnership(false);
  assert(portal._runtime_wifi_ownership_managed);
  assert(portal.startAutoMode(reply));
  assert(WiFi.begins == 0);
  portal.tick(301000);
  assert(portal.setup_starts == 0 && WiFi.disconnects == 0);
  // The worker stops: the waiting portal acquires a fresh 15s attempt.
  portal.updateWiFiOwnership(true);
  portal.tick(301001);
  assert(WiFi.begins == 1 && portal._connect_deadline == 316001);
  // A new worker takes over before the old deadline: do not disconnect it.
  portal.updateWiFiOwnership(false);
  portal.tick(400000);
  assert(portal.setup_starts == 0 && WiFi.disconnects == 0);
  // If that worker stops too, its expired predecessor deadline cannot cause
  // an immediate AP fallback instead of a fresh saved-credential attempt.
  portal.updateWiFiOwnership(true);
  portal.tick(400001);
  assert(WiFi.begins == 2 && portal._connect_deadline == 415001);
  portal.updateWiFiOwnership(false);
  WiFi.state = WL_CONNECTED;
  portal.tick(400002);
  assert(portal._mode == WebConfigServer::MODE_LAN && portal.server_starts == 1);
#else
  assert(portal.startAutoMode(reply) && WiFi.begins == 1);
  const auto* station_ssid = reinterpret_cast<const uint8_t*>(canonical_ssid);
  mesh::wifi::diagnostics.disconnected(station_ssid, strlen(canonical_ssid), 202, 2000);
  portal.tick(15999);
  assert(portal.setup_starts == 0);
  portal.tick(16000);
  assert(portal.setup_starts == 1 && portal._retry_saved_wifi_in_setup);
  auto diagnostic = mesh::wifi::diagnostics.snapshot();
  assert(diagnostic.phase == mesh::wifi::ConnectionPhase::TimedOut);
  assert(diagnostic.has_disconnect && diagnostic.last_reason == 202);
  // The deliberate AP fallback leave/stop cannot replace the original error.
  mesh::wifi::diagnostics.disconnected(station_ssid, strlen(canonical_ssid), 8, 16001);
  mesh::wifi::diagnostics.stopped(16002);
  assert(mesh::wifi::diagnostics.snapshot().phase == mesh::wifi::ConnectionPhase::TimedOut);
  assert(mesh::wifi::diagnostics.snapshot().last_reason == 202);
  assert(WiFi.begins == 1);
  portal.tick(300999);
  assert(WiFi.begins == 1);
  WiFi.next_begin_connects = true;
  portal.tick(301000);
  assert(WiFi.begins == 2);
  assert(mesh::wifi::diagnostics.snapshot().attempts == 2);
  assert(mesh::wifi::diagnostics.snapshot().phase == mesh::wifi::ConnectionPhase::Connecting);
  assert(mesh::wifi::diagnostics.snapshot().last_reason == 202);
  assert(WiFi.ssid == canonical_ssid && WiFi.password == canonical_password);
  portal.tick(301001);
  assert(portal._mode == WebConfigServer::MODE_LAN && portal.server_starts == 1);
  assert(!portal._retry_saved_wifi_in_setup);
#endif
}
