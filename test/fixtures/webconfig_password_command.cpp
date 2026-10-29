// Real JSON CLI parser, form command builder, drain dispatch and both real
// role password setters. Only auth entry and persistence/peripherals are fake.
#include <cassert>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <ArduinoJson.h>
#define MESH_MANAGEMENT_PASSWORD_HOST_TEST
#define ESP32_PLATFORM
#define ESP_PLATFORM
#define WIFI_SSID ""
struct HardwareSerial {};
HardwareSerial Serial;
#include <helpers/UsbMode.h>
#include <helpers/esp32/ManagementPassword.h>
#include <helpers/CLICommandUtils.h>
#include <helpers/ConfigCapabilities.h>
#include <helpers/WebConfigBatch.h>
#include <helpers/WebConfigKeys.h>
namespace password = mesh::esp32::managementPassword;
using String = std::string;
using WCLock = std::lock_guard<std::mutex>;
uint32_t millis() { return 1000; }
bool save_ok = true;
unsigned saves = 0;
bool durableSave() { ++saves; return save_ok; }
namespace mesh { namespace esp32 { namespace companionAdminPassword {
bool save(const char*) { return durableSave(); }
}} namespace wifi {
constexpr bool kPrimaryEspNowRadio = false;
bool parseEspNowChannel(const char*, uint8_t&) { return false; }
} struct Debug { template<typename... T> void printf(const char*, T...) {} };
Debug& usbDebugPort() { static Debug value; return value; }
}
struct StrHelper { static void strncpy(char* out, const char* in, size_t size) {
  ::strncpy(out, in, size); out[size - 1] = 0;
}};
// Preserve the full Companion command prefix without allowing password
// handling to consult unrelated hardware capability or profile providers.
struct Board {
  bool getSystemWatchdogRunning(bool&) { assert(false); return false; }
  bool canControlLoRaFemLna() { assert(false); return false; }
  bool canControlLoRaFemPaGain() { assert(false); return false; }
  bool isLoRaFemPaGainEnabled() { assert(false); return false; }
} board;
struct Radio {
  bool supportsRxPowerSavingRfRxDisable() const { assert(false); return false; }
} radio_driver;
struct UI {
  bool supportsDisplayRotation() { assert(false); return false; }
};
struct Profiles {
  uint16_t primaryPreamble() const { assert(false); return 0; }
};
struct CommonCLI {
  struct Prefs { char password[16] = "original"; } prefs;
  Prefs* _prefs = &prefs;
  bool trySavePrefs() { return durableSave(); }
  void command(char* command, char* reply) {
@SETTER@
  }
};
struct MyMesh {
  Profiles _radio_profiles;
  UI* _ui = nullptr;
  char _management_password[16] = "original";
  bool _management_password_ready = true;
  bool handleLocalControlCommand(const char*, char*, size_t);
  void command(char* cmd, char* reply) { handleLocalControlCommand(cmd, reply, 160); }
};
@COMPANION@
struct Callback {
  bool companion = false;
  unsigned starts = 0, ends = 0, calls = 0;
  CommonCLI infrastructure; MyMesh node;
  bool supportsCliTerminal() const { return true; }
  void execCommand(char* cmd, char* reply) {
    ++calls;
    if (companion) node.command(cmd, reply); else infrastructure.command(cmd, reply);
  }
  void execAdminCommand(char* cmd, char* reply) { execCommand(cmd, reply); }
  void onConfigBatchStart() { ++starts; }
  void onConfigBatchEnd() { ++ends; }
};
struct AsyncResponseStream : std::string {
  int code = 200;
  size_t available() const { return size(); }
  void setCode(int value) { code = value; }
  bool addHeader(const char*, const char*) { return true; }
  size_t write(uint8_t value) { push_back(char(value)); return 1; }
  size_t write(const uint8_t* bytes, size_t count) { append(reinterpret_cast<const char*>(bytes), count); return count; }
};
struct AsyncWebServerRequest {
  int status = 0;
  std::string body, reply;
  void* _tempObject = nullptr;
  size_t contentLength() const { return body.size(); }
  void send(int code, const char* = nullptr, const char* text = "") { status = code; reply = text; }
  void send(int code, const char*, const String& text) { status = code; reply = text; }
  AsyncResponseStream* beginResponseStream(const char*, size_t = 0) { return new AsyncResponseStream; }
  void send(AsyncResponseStream* response) { status = response->code; reply = *response; delete response; }
  void payload(const std::string& text) { body = text; _tempObject = &body[0]; }
};
static const char SECRET_SENTINEL[] = "********";
static bool isAllowedSetKey(const char*, bool);
static bool isSecretKey(const char*);
struct WebConfigServer {
  static constexpr unsigned MAX_BATCH = 24, MAX_BODY = 6144;
  struct BatchEntry { char key[33] = {}, cmd[160] = {}, reply[160] = {}; } _batch[MAX_BATCH];
  enum Mode { MODE_OFF, MODE_SETUP, MODE_LAN } _mode = MODE_LAN;
  enum BatchKind { BATCH_CONFIG, BATCH_CLI } _batch_kind = BATCH_CLI;
  enum State { BATCH_IDLE, BATCH_PENDING, BATCH_DONE } _batch_state = BATCH_IDLE;
  std::mutex _mux;
  Callback cb; Callback* _cb = &cb;
  bool _cli_enabled = true, _initial_setup = false, _admin_pwd_set = false;
  bool _batch_reboot = false, _batch_reboot_armed = false, _batch_all_ok = true;
  bool _batch_started = false, _batch_reauth_required = false, _batch_reauth_delivered = false;
  bool _standalone_wifi = false, _standalone_wifi_dirty = false;
  int _batch_count = 0, _batch_next = 0;
  uint32_t _batch_auth_generation = 0, _batch_reauth_until = 0, _batch_last_cmd = 0;
  uint32_t _session_generation = 0, _session_last_seen = 1;
  char _batch_owner_token[33] = {}, _session_token[33] = "0123456789abcdef0123456789abcdef";
  char _batch_reqid[24] = {}, _wifi_ssid[32] = {}, _wifi_password[65] = {};
  uint8_t _wifi_power_save = 0;
  void* _mqtt_prefs = nullptr;
  bool checkAuth(AsyncWebServerRequest*) { return true; }
  bool checkAuthLocked(AsyncWebServerRequest*, uint32_t* generation) {
    password::Snapshot value; assert(password::snapshot(value)); *generation = value.generation; return true;
  }
  bool bluetoothWiFiCoexistenceRequired() const { return false; }
  void reloadStandaloneWiFi() {}
  static WebConfigBatch::State toSpecState(State value) { return static_cast<WebConfigBatch::State>(value); }
  void clearSessionLocked();
  void beginBatchAuthorizationLocked(uint32_t);
  void cancelStaleBatchLocked(uint32_t);
  void handleCliPost(AsyncWebServerRequest*);
  void drainBatch(uint32_t);
  void configForTest(AsyncWebServerRequest* req) {
    DynamicJsonDocument doc(6144); assert(!deserializeJson(doc, req->body));
    JsonObject set = doc["set"];
    int count = 0;
@CONFIG_LOOP@
    _batch_count = count; _batch_next = 0; _batch_kind = BATCH_CONFIG;
    password::Snapshot value; assert(password::snapshot(value));
    beginBatchAuthorizationLocked(value.generation); _batch_state = BATCH_PENDING;
    req->send(202, "application/json", "{}");
  }
};
@METHODS@
void reset() {
  auto& state = password::detail::state();
  { password::detail::Lock lock(state); state.current = {}; state.changing = state.uploading = state.rotating = false; }
  assert(password::initialize("original")); save_ok = true; saves = 0;
}
std::string cliPayload(const std::string& command, bool prepend = false) {
  DynamicJsonDocument doc(6144); doc["reqid"] = "0123456789abcdef";
  JsonArray cmds = doc.createNestedArray("cmds"); if (prepend) cmds.add("get name"); cmds.add(command);
  std::string out; serializeJson(doc, out); return out;
}
int main() {
  for (bool companion : {false, true}) {
    for (bool config : {false, true}) {
      for (bool success : {false, true}) {
        reset(); WebConfigServer server; server.cb.companion = companion; save_ok = success;
        const char* value = " NewSecret  ";
        AsyncWebServerRequest req;
        if (config) {
          req.payload(std::string("{\"set\":{\"password\":\"") + value + "\"}}");
          server.configForTest(&req);
        } else {
          req.payload(cliPayload(std::string("  PaSsWoRd ") + value)); server.handleCliPost(&req);
        }
        assert(req.status == 202 && server._batch_count == 1);
        assert(!strcmp(server._batch[0].cmd, "password  NewSecret  "));
        server.drainBatch(1000);
        password::Snapshot current; assert(password::snapshot(current));
        assert(server.cb.calls == 1 && saves == 1);
        assert(!strcmp(current.password, success ? value : "original"));
        const char* runtime = companion ? server.cb.node._management_password
            : server.cb.infrastructure.prefs.password;
        assert(!strcmp(runtime, success ? value : "original"));
        assert(server._batch_reauth_required == success);
        assert(bool(server._session_token[0]) != success);
        assert(!strcmp(server._batch[0].reply, "OK") == success);
        assert(strstr(server._batch[0].reply, "NewSecret") == nullptr);
      }
    }
  }
  for (const char* command : {"PASSWORD bad\rvalue", "password bad\nvalue", "pass\nword rejected"}) {
    reset(); WebConfigServer server; AsyncWebServerRequest req;
    req.payload(cliPayload(command, true)); server.handleCliPost(&req);
    assert(req.status == 400 && server._batch_state == WebConfigServer::BATCH_IDLE);
    assert(server.cb.calls == 0 && saves == 0);
  }
  reset(); WebConfigServer ordinary; AsyncWebServerRequest req;
  req.payload(cliPayload("  get name\r\n \t")); ordinary.handleCliPost(&req);
  assert(req.status == 202 && !strcmp(ordinary._batch[0].cmd, "get name"));
}
