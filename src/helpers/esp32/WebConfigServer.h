#pragma once

// Shared browser configuration portal for ESP32 infrastructure and WiFi
// companion builds. The UI removes MQTT controls when no bridge is present.
//
// Two modes:
//  - SETUP: open SoftAP + captive portal, raised automatically on first boot
//    when no WiFi is configured (wifi_ssid empty), or manually via
//    `start webconfig ap`. Save -> reboot; auto-stops after an idle timeout.
//  - LAN: bound to an existing or portal-owned STA connection. Infrastructure
//    roles require the node admin password; companions use their trusted LAN.
//
// Concurrency model: AsyncWebServer handlers run on the async_tcp task and
// must never touch the CLI, prefs persistence, or the radio. Config writes
// are marshaled into a single-slot command batch that tick() - called from
// MyMesh::loop() on the Arduino loop task - drains through the existing CLI
// `set` handlers. Prefs-struct reads and batch state are guarded by _mux.
//
// The master switch and standalone WiFi credentials use independent NVS
// namespaces, so role-specific prefs-file layouts remain untouched.

#if defined(ESP_PLATFORM) && !defined(WEBCONFIG_DISABLED)

#define WITH_WEBCONFIG 1

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <helpers/WebConfigBatch.h>

class AsyncWebServer;
class AsyncWebServerRequest;
class DNSServer;

#ifndef WEBCONFIG_AP_IDLE_TIMEOUT_MS
  #define WEBCONFIG_AP_IDLE_TIMEOUT_MS  (10UL * 60UL * 1000UL)
#endif
#ifndef WEBCONFIG_SESSION_TTL_MS
  #define WEBCONFIG_SESSION_TTL_MS      (20UL * 60UL * 1000UL)
#endif

class WebConfigServer {
public:
  enum Mode : uint8_t { MODE_OFF = 0, MODE_SETUP, MODE_CONNECTING, MODE_LAN };

  enum Capability : uint32_t {
    CAP_LOCATION = 1UL << 0,
    CAP_AIRTIME = 1UL << 1,
    CAP_RX_DELAY = 1UL << 2,
    CAP_CAD = 1UL << 3,
    CAP_RX_GAIN = 1UL << 4,
    CAP_REPEAT = 1UL << 5,
    CAP_ADVERT = 1UL << 6,
    CAP_FLOOD = 1UL << 7,
    CAP_LOOP = 1UL << 8,
    CAP_TX_DELAY = 1UL << 9,
    CAP_WIFI_POWER_SAVE = 1UL << 10,
    CAP_FEM_RX_GAIN = 1UL << 11,
    CAP_DELAYS = CAP_RX_DELAY | CAP_TX_DELAY,
  };

  // Role-neutral snapshot. Infrastructure and companion builds have different
  // persisted NodePrefs layouts, so the portal never casts either one or
  // assumes their member offsets match.
  struct NodeSnapshot {
    char name[32];
    char admin_password[32];       // empty = trusted LAN/no login prompt
    double lat;
    double lon;
    float freq;
    float bw;
    float airtime_factor;
    float rx_delay;
    float tx_delay;
    int8_t tx_power;
    uint8_t sf;
    uint8_t cr;
    uint8_t cad;
    uint8_t rx_gain;
    uint8_t fem_rx_gain;
    uint8_t repeat;
    uint16_t advert_interval;
    uint8_t flood_advert_interval;
    uint8_t flood_max;
    uint8_t flood_max_advert;
    uint8_t flood_max_unscoped;
    uint8_t loop_detect;
    uint32_t capabilities;
  };

  class Callbacks {
  public:
    virtual void getNodeSnapshot(NodeSnapshot& snapshot) = 0;
    // Run one CLI command. Called from tick() only (loop task); reply is 160 bytes.
    virtual void execCommand(char* cmd, char* reply) = 0;
    virtual void rebootNow() = 0;
    // Bracket a config batch so bridge restarts triggered by individual
    // `set` handlers can be coalesced into one.
    virtual void onConfigBatchStart() {}
    virtual void onConfigBatchEnd() {}
    // Fill buf with the stats JSON snapshot. Called from tick() (loop task).
    virtual void buildStatsJson(char* buf, size_t buf_size) = 0;
    // Teardown finished (server + DNS freed, WiFi mode restored).
    virtual void onWebConfigStopped() {}
  };

  // mqtt_prefs is an MQTTPrefs* on WITH_MQTT_BRIDGE builds and NULL otherwise.
  // owns_wifi is true when the portal itself brought up STA/AP (normal
  // repeater/room-server builds), false when the companion/MQTT runtime owns it.
  WebConfigServer(Callbacks* callbacks, void* mqtt_prefs, bool owns_wifi,
                  const uint8_t* pub_key, const char* fw_ver,
                  const char* role, const char* board_name);
  ~WebConfigServer();

  // For the device display: true while a setup-mode portal is active.
  // Fills the AP SSID and portal IP; either buffer may be NULL to just poll.
  // Call from the loop task only (same task that changes the mode).
  static bool getSetupInfo(char* ssid, size_t ssid_len, char* ip, size_t ip_len);

  // For the device display: true once a config save completed and the node is
  // about to reboot - ground truth for the user even if the browser lost its
  // connection before the confirmation arrived. Loop task only.
  static bool isRebootPending();

  // Persistent master switch and standalone WiFi credentials use separate NVS
  // namespaces. This avoids changing NodePrefs or MQTTPrefs file layouts.
  static bool loadEnabled(bool default_value = false);
  static bool saveEnabled(bool enabled);
  static bool loadStandaloneWiFi(char* ssid, size_t ssid_len,
                                 char* password, size_t password_len,
                                 uint8_t* power_save = NULL);
  static bool saveStandaloneWiFi(const char* ssid, const char* password,
                                 uint8_t power_save);

  // UI tasks use an otherwise-unused multi-click gesture without reaching into
  // MyMesh directly. The mesh loop consumes the request and performs the NVS /
  // WiFi transition on its own task.
  static void requestToggleFromButton();
  static bool takeButtonToggleRequest();

  bool startSetupMode(char reply[]);   // open SoftAP + DNS captive portal
  bool startLanMode(char reply[]);     // bind to existing STA connection
  bool startAutoMode(char reply[]);    // use saved WiFi, or setup AP when absent
  void requestStop();                  // stop listening and detach this session
  void tick(uint32_t now);             // call every loop iteration

  Mode mode() const { return _mode; }
  bool isRunning() const { return _mode != MODE_OFF; }
  bool isStopping() const { return _stopping; }

private:
  static const int MAX_BATCH = WebConfigBatch::kMaxBatch;
  static const size_t MAX_BODY = 4096;
  static const uint32_t STOP_WARN_MS = WebConfigBatch::kStopWarnMs;
  enum BatchState : uint8_t { BATCH_IDLE = 0, BATCH_PENDING, BATCH_DONE };
  static WebConfigBatch::State toSpecState(BatchState state) {
    switch (state) {
      case BATCH_PENDING: return WebConfigBatch::State::Pending;
      case BATCH_DONE: return WebConfigBatch::State::Done;
      default: return WebConfigBatch::State::Idle;
    }
  }
  struct BatchEntry {
    char key[24];     // allowlisted config key (echoed back to the UI)
    char cmd[160];    // full CLI command (may contain secrets - never echoed)
    char reply[160];
  };

  Callbacks* _cb;
  void* _mqtt_prefs;
  bool _owns_wifi;
  const uint8_t* _pub_key;
  const char* _fw_ver;
  const char* _role;
  const char* _board_name;

  AsyncWebServer* _server = NULL;
  DNSServer* _dns = NULL;
  SemaphoreHandle_t _mux;
  Mode _mode = MODE_OFF;
  bool _stopping = false;
  bool _was_setup_ap = false;
  bool _initial_setup = false;
  uint32_t _connect_deadline = 0;
  char _wifi_ssid[32] = {0};
  char _wifi_password[64] = {0};
  uint8_t _wifi_power_save = 1;
  char _ap_ssid[33] = {0};

  // Currently attached session, also used by the display's setup-info poll.
  static WebConfigServer* _active;
  // Process-lifetime listener and route table. Async requests retain their
  // server pointer until disconnect, so this object is not deleted on stop.
  static AsyncWebServer* _host;

  // Command batch: filled by async_tcp under _mux, drained by tick().
  volatile BatchState _batch_state = BATCH_IDLE;
  uint8_t _batch_count = 0;
  uint8_t _batch_next = 0;        // drain progress (one command per tick)
  uint32_t _batch_last_cmd = 0;
  bool _batch_reboot = false;
  bool _batch_reboot_armed = false;
  bool _batch_all_ok = true;
  char _batch_reqid[24] = {0};
  bool _standalone_wifi_dirty = false;
  BatchEntry _batch[MAX_BATCH];

  // LAN-mode session (single slot; new login evicts the old session)
  char _session_token[33] = {0};
  uint32_t _session_last_seen = 0;
  uint8_t _login_fails = 0;
  uint32_t _login_lock_until = 0;

  // Once set (via /api/portal/exit), OS captive probes get native "success"
  // replies so the phone's sign-in sheet can be dismissed without dropping the
  // WiFi, letting the user continue in their real browser. async_tcp-task only.
  volatile bool _captive_release = false;

  // Save-path diagnostics: 1 Hz serial trace for 60 s after each config POST
  // (AP station count, heap, batch state) to pinpoint client drops on hardware.
  volatile uint32_t _diag_until = 0;
  uint32_t _diag_last = 0;

  volatile uint32_t _last_activity = 0;
  uint32_t _reboot_at = 0;         // 0 = none scheduled
  uint32_t _stop_warn_at = 0;
  bool _stop_warned = false;
  uint32_t _handler_refs = 0;
  volatile uint32_t _stats_wanted_until = 0;
  uint32_t _stats_built_at = 0;
  char _stats_json[1024] = {0};

  void createServer();
  void registerRoutes();
  typedef void (WebConfigServer::*RequestHandler)(AsyncWebServerRequest*);
  static void dispatchRequest(AsyncWebServerRequest* req, RequestHandler handler);
  void attachRoutes();
  void detachRoutes();
  uint32_t handlerRefCount() const;
  void drainBatch(uint32_t now);
  void finalizeTeardown();
  bool checkAuth(AsyncWebServerRequest* req);
  static void collectBody(AsyncWebServerRequest* req, uint8_t* data, size_t len,
                          size_t index, size_t total);

  void handleRoot(AsyncWebServerRequest* req);
  void handleStatus(AsyncWebServerRequest* req);
  void handleLogin(AsyncWebServerRequest* req);
  void handleLogout(AsyncWebServerRequest* req);
  void handleConfigGet(AsyncWebServerRequest* req);
  void handleConfigPost(AsyncWebServerRequest* req);
  void handleConfigResult(AsyncWebServerRequest* req);
  void handleStats(AsyncWebServerRequest* req);
  void handleScan(AsyncWebServerRequest* req);
  void handlePresets(AsyncWebServerRequest* req);
  void handleReboot(AsyncWebServerRequest* req);
  void handlePortalExit(AsyncWebServerRequest* req);
  void handleNotFound(AsyncWebServerRequest* req);
};

#endif  // ESP_PLATFORM && !WEBCONFIG_DISABLED
