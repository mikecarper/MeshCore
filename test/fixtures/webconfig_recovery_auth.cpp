#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <ArduinoJson.h>
#include <helpers/WebConfigLoginThrottle.h>
#include <helpers/WebConfigBatch.h>
#define MESH_MANAGEMENT_PASSWORD_HOST_TEST
#include <helpers/esp32/ManagementPassword.h>
namespace password = mesh::esp32::managementPassword;
static void resetCredential(const char* value) {
  {
    auto& state = password::detail::state();
    password::detail::Lock lock(state);
    state.current = password::Snapshot{};
    state.changing = state.uploading = state.rotating = false;
  }
  assert(password::initialize(value));
}
static void commitCredential(const char* value) {
  password::ChangeGuard change;
  assert(change.acquired() && change.publish(value));
}
struct String : std::string {
  using std::string::string;
  String(std::string value) : std::string(std::move(value)) {}
  int indexOf(const char* value) const { auto pos=find(value); return pos==npos ? -1 : int(pos); }
  String substring(int begin, int end) const { return substr(begin, end-begin); }
};
static uint32_t now_ms=10, random_word=0x12345678;
static bool rotate_during_login=false;
uint32_t millis() { return now_ms; }
uint32_t esp_random() {
  if (rotate_during_login) {
    rotate_during_login=false;
    commitCredential("rotated-secret");
  }
  return ++random_word;
}
static constexpr uint32_t WEBCONFIG_SESSION_TTL_MS=10000;
struct NodeSnapshot { char admin_password[16] = {}; };
struct Callback {
  NodeSnapshot node;
  void getNodeSnapshot(NodeSnapshot& result) { result=node; }
};
struct WCLock { std::unique_lock<std::mutex> lock; explicit WCLock(std::mutex& m) : lock(m) {} };
struct WiFiMock { int softAPIP() const { return 4; } } WiFi;
struct Client {
  int local_ip=4; uint32_t remote=0;
  int localIP() const { return local_ip; }
  uint32_t getRemoteAddress() const { return remote; }
};
struct Header { String cookies; const String& value() const { return cookies; } };
struct AsyncWebServerResponse {
  int status; std::map<std::string, std::string> headers;
  void addHeader(const char* key, const char* value) { headers[key]=value; }
};
struct AsyncWebServerRequest {
  Client socket; bool has_client=true;
  Header header; std::string body; void* _tempObject=nullptr;
  int status=0; AsyncWebServerResponse response{};
  Client* client() { return has_client ? &socket : nullptr; }
  bool hasHeader(const char*) const { return !header.cookies.empty(); }
  Header* getHeader(const char*) { return &header; }
  void send(int code, const char* =nullptr, const char* =nullptr) { status=code; }
  AsyncWebServerResponse* beginResponse(int code, const char*, const char*) { response.status=code; return &response; }
  void send(AsyncWebServerResponse* value) { status=value->status; }
  void password(const char* value) { body=std::string("{\"password\":\"")+value+"\"}"; _tempObject=body.data(); }
};
struct WebConfigServer {
  enum { MODE_OFF, MODE_SETUP, MODE_LAN };
  enum BatchKind { BATCH_CONFIG, BATCH_CLI };
  int _mode=MODE_SETUP;
  std::mutex _mux;
  char _wifi_ssid[33]="SavedNet", _session_token[33] = {};
  bool _initial_setup=false, _setup_provisioning_privilege=false, config_accepted=false;
  uint32_t _last_activity=0, _session_last_seen=0;
  uint32_t _session_generation=0, _ap_auth_generation=0;
  uint32_t _batch_auth_generation=0, _batch_reauth_until=0;
  bool _batch_reauth_required=false, _batch_reauth_delivered=false, _batch_started=false;
  BatchKind _batch_kind=BATCH_CONFIG;
  char _batch_owner_token[33]={}, _batch_reqid[24]="0123456789abcdef";
  WebConfigLoginThrottle::Tracker _login_throttle; Callback callback; Callback* _cb=&callback;
  WebConfigServer() { strcpy(callback.node.admin_password, "existing-secret"); resetCredential("existing-secret"); }
  unsigned network_activity = 0;
  void noteNetworkActivity() { ++network_activity; }
  bool checkAuth(AsyncWebServerRequest*);
  bool checkAuthLocked(AsyncWebServerRequest*, uint32_t* generation=nullptr);
  void clearSessionLocked();
  void beginBatchAuthorizationLocked(uint32_t);
  bool resultReceiptAllowedLocked(AsyncWebServerRequest*, const char*, BatchKind, uint32_t);
  void handleLogin(AsyncWebServerRequest*);
  void handleLogout(AsyncWebServerRequest*);
  void checkConfigPost(AsyncWebServerRequest*);
};
@METHODS@
int main() {
  WebConfigServer portal; AsyncWebServerRequest attacker;
  attacker.body="{\"set\":{\"password\":\"attacker\",\"wifi.ssid\":\"EvilNet\"}}";
  attacker._tempObject=attacker.body.data();
  assert(!portal.checkAuth(&attacker));
  assert(portal.network_activity==0);
  portal.checkConfigPost(&attacker);
  assert(attacker.status==401 && !portal.config_accepted);
  attacker.password("wrong"); portal.handleLogin(&attacker);
  assert(attacker.status==401 && portal._session_token[0]==0);
  assert(portal.network_activity==0);
  AsyncWebServerRequest admin; admin.password("existing-secret");
  portal.handleLogin(&admin); assert(admin.status==200);
  assert(portal.network_activity>0);
  assert(strlen(portal._session_token)==32);
  assert(admin.response.headers["Set-Cookie"].find("HttpOnly; SameSite=Lax")!=std::string::npos);
  admin.header.cookies=std::string("wcs=")+portal._session_token;
  assert(portal.checkAuth(&admin)); portal.checkConfigPost(&admin);
  assert(portal.config_accepted);
  std::string valid_token=portal._session_token;
  portal.handleLogout(&attacker);
  assert(attacker.status==401 && valid_token==portal._session_token);
  admin.socket.local_ip=9;
  assert(!portal.checkAuth(&admin));
  admin.password("existing-secret"); portal.handleLogin(&admin);
  assert(admin.status==401); // recovery must not authorize a recovered STA/LAN
  admin.socket.local_ip=4;
  admin.has_client=false; assert(!portal.checkAuth(&admin)); admin.has_client=true;
  now_ms+=WEBCONFIG_SESSION_TTL_MS+1; assert(!portal.checkAuth(&admin));
  admin.password("existing-secret"); portal.handleLogin(&admin); assert(admin.status==200);
  admin.header.cookies=std::string("wcs=")+portal._session_token;
  portal.handleLogout(&admin); assert(admin.status==200 && portal._session_token[0]==0);
  assert(!portal.checkAuth(&admin));
  portal._mode=WebConfigServer::MODE_LAN; admin.socket.local_ip=9;
  admin.password("existing-secret"); portal.handleLogin(&admin); assert(admin.status==200);
  admin.header.cookies=std::string("wcs=")+portal._session_token; assert(portal.checkAuth(&admin));
  portal._mode=WebConfigServer::MODE_OFF; assert(!portal.checkAuth(&admin));
  portal.handleLogin(&admin); assert(admin.status==503);
  // First provisioning remains possible and retains its AP-only privilege
  // through a save batch that changes SSID before the confirmation arrives.
  portal._mode=WebConfigServer::MODE_SETUP; portal._wifi_ssid[0]=0;
  portal._setup_provisioning_privilege=true;
  password::Snapshot credential; assert(password::snapshot(credential));
  portal._ap_auth_generation=credential.generation;
  portal._initial_setup=true; assert(portal.checkAuth(&attacker));
  strcpy(portal._wifi_ssid, "ProvisionedNet"); assert(portal.checkAuth(&attacker));
  portal.handleLogin(&attacker); assert(attacker.status==200);
  attacker.socket.local_ip=9; assert(!portal.checkAuth(&attacker));
  portal.handleLogin(&attacker); assert(attacker.status==401); attacker.socket.local_ip=4;
  // Explicit legacy passwordless deployments retain their no-password policy.
  portal._initial_setup=false; portal.callback.node.admin_password[0]=0;
  portal._setup_provisioning_privilege=false;
  resetCredential("");
  assert(portal.checkAuth(&attacker)); portal.handleLogin(&attacker); assert(attacker.status==200);
  // A spent login budget blocks new logins without revoking an existing
  // authenticated session, on either the saved-network AP or the LAN.
  for (int mode : {WebConfigServer::MODE_SETUP, WebConfigServer::MODE_LAN}) {
    WebConfigServer active; active._mode=mode;
    AsyncWebServerRequest owner; owner.socket.remote=100;
    owner.password("existing-secret"); active.handleLogin(&owner);
    assert(owner.status==200);
    const std::string token=active._session_token;
    owner.header.cookies=std::string("wcs=")+token;
    for (uint32_t address = 1; address <= 6; address++) {
      AsyncWebServerRequest wrong; wrong.socket.remote=address;
      wrong.password("wrong"); active.handleLogin(&wrong); assert(wrong.status==401);
    }
    AsyncWebServerRequest login; login.socket.remote=200;
    login.password("existing-secret"); active.handleLogin(&login);
    assert(login.status==429 && token==active._session_token);
    assert(active.checkAuth(&owner));
    active.checkConfigPost(&owner); assert(active.config_accepted);
    assert(token==active._session_token);
  }
  for (int mode : {WebConfigServer::MODE_SETUP, WebConfigServer::MODE_LAN}) {
    WebConfigServer active; active._mode=mode;
    AsyncWebServerRequest owner; owner.password("existing-secret");
    active.handleLogin(&owner); assert(owner.status==200);
    owner.header.cookies=std::string("wcs=")+active._session_token;
    const std::string original_cookie=owner.header.cookies;
    password::Snapshot before; assert(password::snapshot(before));
    active.beginBatchAuthorizationLocked(before.generation);
    // A setter's temporary RAM and failed-save rollback never reach auth.
    strcpy(active.callback.node.admin_password, "temporary");
    assert(active.checkAuth(&owner));
    AsyncWebServerRequest unsaved; unsaved.password("temporary");
    active.handleLogin(&unsaved); assert(unsaved.status==401);
    strcpy(active.callback.node.admin_password, "existing-secret");
    commitCredential("existing-secret");
    assert(active.checkAuth(&owner)); // same credential is not a rotation

    commitCredential("new-secret");
    password::Snapshot after; assert(password::snapshot(after));
    assert(after.generation!=before.generation);
    assert(!active.checkAuth(&owner));
    active.config_accepted=false; active.checkConfigPost(&owner);
    assert(owner.status==401 && !active.config_accepted);
    // Only an actual owning password batch grants this short-lived result
    // receipt; external rotation alone does not create one.
    assert(!active.resultReceiptAllowedLocked(&owner, active._batch_reqid,
                                               WebConfigServer::BATCH_CONFIG, now_ms));
    active._batch_auth_generation=after.generation;
    active._batch_reauth_required=true;
    active._batch_reauth_until=WebConfigBatch::scheduleAt(now_ms, WebConfigBatch::kReauthReceiptMs);
    assert(active.resultReceiptAllowedLocked(&owner, active._batch_reqid,
                                              WebConfigServer::BATCH_CONFIG, now_ms));
    assert(!active.resultReceiptAllowedLocked(&owner, "fedcba9876543210",
                                               WebConfigServer::BATCH_CONFIG, now_ms));
    assert(!active.resultReceiptAllowedLocked(&owner, active._batch_reqid,
                                               WebConfigServer::BATCH_CLI, now_ms));
    AsyncWebServerRequest other; other.header.cookies="wcs=00000000000000000000000000000000";
    assert(!active.resultReceiptAllowedLocked(&other, active._batch_reqid,
                                               WebConfigServer::BATCH_CONFIG, now_ms));
    assert(!active.resultReceiptAllowedLocked(&owner, active._batch_reqid,
        WebConfigServer::BATCH_CONFIG, active._batch_reauth_until));
    owner.password("existing-secret"); active.handleLogin(&owner); assert(owner.status==401);
    owner.password("new-secret"); active.handleLogin(&owner); assert(owner.status==200);
    owner.header.cookies=std::string("wcs=")+active._session_token;
    assert(active.checkAuth(&owner));
    owner.header.cookies=original_cookie; assert(!active.checkAuth(&owner));
    // Even changing back cannot revive the first generation's cookie/receipt.
    commitCredential("existing-secret");
    assert(!active.checkAuth(&owner));
    assert(!active.resultReceiptAllowedLocked(&owner, active._batch_reqid,
                                               WebConfigServer::BATCH_CONFIG, now_ms));
  }
  {
    WebConfigServer active;
    AsyncWebServerRequest first_setup;
    active._wifi_ssid[0]=0;
    active._initial_setup=active._setup_provisioning_privilege=true;
    password::Snapshot before; assert(password::snapshot(before));
    active._ap_auth_generation=before.generation;
    assert(active.checkAuth(&first_setup));
    active.beginBatchAuthorizationLocked(before.generation); // no initial cookie
    commitCredential("new-secret");
    assert(!active.checkAuth(&first_setup)); // old WPA association is not auth
    first_setup.password("new-secret"); active.handleLogin(&first_setup);
    assert(first_setup.status==200 && active._session_token[0]);
    first_setup.header.cookies=std::string("wcs=")+active._session_token;
    assert(active.checkAuth(&first_setup));
  }
  {
    WebConfigServer active;
    active._wifi_ssid[0]=0; active._initial_setup=true;
    commitCredential("short");
    AsyncWebServerRequest open_ap;
    assert(!active.checkAuth(&open_ap)); // short legacy password still needs HTTP login
    open_ap.password("short"); active.handleLogin(&open_ap); assert(open_ap.status==200);
  }
  {
    WebConfigServer active; AsyncWebServerRequest login;
    login.password("existing-secret"); rotate_during_login=true;
    active.handleLogin(&login);
    assert(login.status==401 && active._session_token[0]==0);
  }
  puts("WebConfig recovery authentication regression checks passed");
}
