#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

enum { WL_DISCONNECTED, WL_CONNECTED };
size_t strlcpy(char* out, const char* value, size_t capacity) {
  if (capacity) snprintf(out, capacity, "%s", value);
  return strlen(value);
}
struct IP {
  std::string value;
  std::string toString() const { return value; }
};
struct {
  int state = WL_CONNECTED;
  std::string ip = "192.168.0.2";
  int status() const { return state; }
  IP localIP() const { return {ip}; }
} WiFi;

class WebConfigServer {
public:
  enum Mode : uint8_t { MODE_OFF, MODE_SETUP, MODE_CONNECTING, MODE_LAN };
  Mode _mode = MODE_OFF, _start_mode = MODE_OFF;
  bool _stopping = false;
  unsigned requests = 0;
  static bool saved_enabled;
  static bool has_saved_setting;
  static bool loadEnabled(bool default_value) {
    return has_saved_setting ? saved_enabled : default_value;
  }
  static bool getSetupInfo(char* ssid, size_t ssid_size, char* ip, size_t ip_size) {
    snprintf(ssid, ssid_size, "MC-D177");
    snprintf(ip, ip_size, "192.168.4.1");
    return true;
  }
  void requestNetworkAccess() { ++requests; }
  bool requestStart(bool force_ap, char*) {
    _mode = force_ap ? MODE_SETUP : MODE_CONNECTING;
    return true;
  }
  static bool formatWebUIStatus(char*, size_t, bool, const WebConfigServer*);
  @GETTERS@
};
bool WebConfigServer::saved_enabled = false;
bool WebConfigServer::has_saved_setting = true;
@METHOD@

struct { bool isOTAUpdateRunning() const { return false; } } board;
struct MyMesh {
  WebConfigServer* _webconfig = nullptr;
  bool getWebUIStatus(char*) const;
  bool startWebConfig(bool, char*);
};
@ROLE_STATUS@
@ROLE_START@

int main() {
  char reply[160] = {};
  MyMesh mesh;
  assert(mesh.getWebUIStatus(reply));
  assert(strcmp(reply, "> Web UI: stopped; auto-start: off") == 0);
  WebConfigServer::saved_enabled = true;
  assert(mesh.getWebUIStatus(reply));
  assert(strcmp(reply, "> Web UI: stopped; auto-start: on") == 0);
  WebConfigServer::has_saved_setting = false;
  assert(mesh.getWebUIStatus(reply));
  assert(strstr(reply, @DEFAULT_ENABLED@ ? "auto-start: on" : "auto-start: off"));
  WebConfigServer::has_saved_setting = true;
  WebConfigServer::saved_enabled = false;

  WebConfigServer portal;
  mesh._webconfig = &portal;
  portal._start_mode = WebConfigServer::MODE_CONNECTING;
  assert(mesh.getWebUIStatus(reply));
  assert(strcmp(reply, "> Web UI: starting; auto-start: off") == 0);
  portal._start_mode = WebConfigServer::MODE_OFF;
  portal._mode = WebConfigServer::MODE_CONNECTING;
  assert(mesh.getWebUIStatus(reply));
  assert(strcmp(reply, "> Web UI: connecting to WiFi; auto-start: off") == 0);
  char before[160];
  strcpy(before, reply);
  assert(mesh.startWebConfig(false, reply));
  assert(strcmp(reply, before) == 0);

  portal._mode = WebConfigServer::MODE_SETUP;
  assert(mesh.getWebUIStatus(reply));
  assert(strcmp(reply, "> Web UI: setup hotspot 'MC-D177' at http://192.168.4.1/; auto-start: off") == 0);
  assert(mesh.startWebConfig(false, reply));
  assert(strstr(reply, "setup hotspot 'MC-D177'"));

  portal._mode = WebConfigServer::MODE_LAN;
  assert(mesh.getWebUIStatus(reply));
  assert(strcmp(reply, "> Web UI: running at http://192.168.0.2/; auto-start: off") == 0);
  WebConfigServer::saved_enabled = true;
  assert(mesh.getWebUIStatus(reply));
  assert(strcmp(reply, "> Web UI: running at http://192.168.0.2/; auto-start: on") == 0);
  WiFi.state = WL_DISCONNECTED;
  assert(mesh.getWebUIStatus(reply));
  assert(strcmp(reply, "> Web UI: waiting for WiFi; auto-start: on") == 0);
  WiFi.state = WL_CONNECTED;
  WiFi.ip = "0.0.0.0";
  assert(mesh.getWebUIStatus(reply));
  assert(strcmp(reply, "> Web UI: waiting for WiFi; auto-start: on") == 0);
  assert(strstr(reply, "http://") == nullptr);

  // requestStop switches the mode off before its asynchronous cleanup finishes.
  // It may also be observed while the previous mode remains published.
  for (auto mode : {WebConfigServer::MODE_OFF, WebConfigServer::MODE_SETUP,
                    WebConfigServer::MODE_CONNECTING, WebConfigServer::MODE_LAN}) {
    portal._mode = mode;
    portal._stopping = true;
    assert(mesh.getWebUIStatus(reply));
    assert(strcmp(reply, "> Web UI: stopping; auto-start: on") == 0);
  }

  // The formatter must honor callers with less than the full CLI reply budget.
  assert(!WebConfigServer::formatWebUIStatus(nullptr, 160, true, &portal));
  assert(!WebConfigServer::formatWebUIStatus(reply, 0, true, &portal));
  portal._stopping = false;
  portal._mode = WebConfigServer::MODE_SETUP;
  for (size_t capacity = 1; capacity <= sizeof(reply); ++capacity) {
    memset(reply, '!', sizeof(reply));
    assert(WebConfigServer::formatWebUIStatus(reply, capacity, true, &portal));
    assert(reply[capacity - 1] == '\0' || strlen(reply) < capacity);
    if (capacity < sizeof(reply)) assert(reply[capacity] == '!');
  }
}
