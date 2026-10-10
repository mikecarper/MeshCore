// Compile the production WebConfig stop/finalize methods with peripheral
// doubles. Delayed HTTP request destruction must never turn OTA WiFi off.
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <helpers/RoomWebMailbox.h>

static uint32_t millis() { return 123; }
struct WebConfigBatch {
  static uint32_t scheduleAt(uint32_t now, uint32_t delay) { return now + delay; }
};
constexpr int WIFI_STA = 1;
constexpr int WIFI_AP = 2;
struct WiFiFake {
  bool sta = true, ap = false, connected = true, allow_sta = true, allow_ap_stop = true;
  unsigned stops = 0, ap_stops = 0;
  bool enableSTA(bool on) { if (!allow_sta) return false; sta = on; return true; }
  bool softAPdisconnect(bool) { ++ap_stops; if (allow_ap_stop) ap = false; return false; }
  int getMode() const { return (sta ? WIFI_STA : 0) | (ap ? WIFI_AP : 0); }
  void mode(int value) { assert(value == WIFI_STA); sta = true; ap = false; }
} WiFi;
void stopOwnedWiFiRadio() {
  WiFi.sta = WiFi.ap = WiFi.connected = false;
  ++WiFi.stops;
}
struct Server { bool listening = true; void end() { listening = false; } };
struct DNS { void stop() {} };
struct Callbacks { unsigned stops = 0; void onWebConfigStopped() { ++stops; } };
struct WebConfigServer {
  enum Mode { MODE_OFF, MODE_LAN, MODE_SETUP };
  enum { STOP_WARN_MS = 1000, BATCH_IDLE = 0, BATCH_CONFIG = 0 };
  Server server;
  Server* _server = &server;
  DNS* _dns = nullptr;
  Callbacks callbacks;
  Callbacks* _cb = &callbacks;
  mesh::RoomWebMailbox* _room_mailbox = new mesh::RoomWebMailbox;
  Mode _mode = MODE_LAN;
  bool _stopping = false, _keep_wifi_on_stop = false, _was_setup_ap = false;
  bool _owns_wifi = true, _stop_warned = false, _initial_setup = false;
  bool _retry_saved_wifi_in_setup = false, _setup_reconnect_in_progress = false;
  bool _batch_reboot_armed = false, _setup_wifi_handoff_pending = false;
  bool _admin_pwd_set = false, _board_cmds_probed = false;
  uint32_t _stop_warn_at = 0, _setup_started_at = 0, _connect_deadline = 0;
  uint32_t _setup_reconnect_deadline = 0, _reboot_at = 0, _batch_state = 0;
  uint32_t _batch_next = 0, _setup_wifi_handoff_deadline = 0, _batch_kind = 0;
  uint32_t _board_cmds = 0;
  char _wifi_ssid[32] = {}, _setup_wifi_handoff_ip[16] = {};
  char _session_token[16] = {}, _stats_json[16] = {};
  bool routes = true;
  void detachRoutes() { routes = false; }
  void closeTerminal() {}
  bool isRunning() const { return _mode != MODE_OFF; }
  bool isStopping() const { return _stopping; }
  void requestStop();
  bool stopForOTA(char*);
  void finalizeTeardown();
  ~WebConfigServer() { delete _room_mailbox; }
};
@METHODS@

int main() {
  for (bool setup : {false, true}) {
    for (bool owns_wifi : {false, true}) {
      WebConfigServer portal;
      portal._owns_wifi = owns_wifi;
      portal._was_setup_ap = setup;
      portal._mode = setup ? WebConfigServer::MODE_SETUP : WebConfigServer::MODE_LAN;
      WiFi = WiFiFake{};
      WiFi.ap = setup;
      WiFi.sta = !setup;
      WiFi.connected = !setup;
      char reply[160] = {};
      assert(portal.stopForOTA(reply));
      assert(strcmp(reply, "WebConfig stopped") == 0);
      assert(!portal.routes && !portal.server.listening && portal.isStopping());
      assert(WiFi.sta && !WiFi.ap && WiFi.stops == 0);
      assert(WiFi.connected == !setup);
      // OTA can now bind port 80 and create its own AP, while HTTP request
      // objects on the old server still have outstanding references.
      WiFi.ap = true;
      portal.requestStop(); // A duplicate stop cannot revoke the handoff.
      portal.finalizeTeardown(); // Existing requests have finally drained.
      assert(portal._room_mailbox == nullptr);
      assert(WiFi.ap && WiFi.sta && WiFi.stops == 0);
      assert(WiFi.ap_stops == unsigned(setup));
      assert(portal.callbacks.stops == 1 && !portal._keep_wifi_on_stop);
    }
  }
  WebConfigServer ordinary;
  WiFi = WiFiFake{};
  ordinary.requestStop();
  ordinary.finalizeTeardown();
  assert(ordinary._room_mailbox == nullptr);
  assert(WiFi.stops == 1 && !WiFi.sta); // Normal stop keeps its old behavior.
  WebConfigServer failed;
  failed._was_setup_ap = true;
  WiFi = WiFiFake{};
  WiFi.allow_sta = false;
  char reply[160] = {};
  assert(!failed.stopForOTA(reply));
  assert(strstr(reply, "ERR:") && failed.server.listening && !failed.isStopping());
  WiFi.allow_sta = true;
  WiFi.ap = true;
  WiFi.allow_ap_stop = false;
  assert(!failed.stopForOTA(reply));
  assert(strstr(reply, "WebConfig stopped; WiFi AP handoff failed"));
  assert(!failed.server.listening);
}
