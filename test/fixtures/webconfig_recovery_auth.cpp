#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <ArduinoJson.h>
struct String : std::string {
  using std::string::string;
  String(std::string value) : std::string(std::move(value)) {}
  int indexOf(const char* value) const { auto pos=find(value); return pos==npos ? -1 : int(pos); }
  String substring(int begin, int end) const { return substr(begin, end-begin); }
};
static uint32_t now_ms=10, random_word=0x12345678;
uint32_t millis() { return now_ms; }
uint32_t esp_random() { return ++random_word; }
static constexpr uint32_t WEBCONFIG_SESSION_TTL_MS=10000;
struct NodeSnapshot { char admin_password[16] = {}; };
struct Callback {
  NodeSnapshot node;
  void getNodeSnapshot(NodeSnapshot& result) { result=node; }
};
struct WCLock { explicit WCLock(int) {} };
struct WiFiMock { int softAPIP() const { return 4; } } WiFi;
struct Client { int local_ip=4; int localIP() const { return local_ip; } };
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
  int _mode=MODE_SETUP, _mux=0;
  char _wifi_ssid[33]="SavedNet", _session_token[33] = {};
  bool _initial_setup=false, config_accepted=false;
  uint32_t _last_activity=0, _session_last_seen=0, _login_lock_until=0;
  int _login_fails=0; Callback callback; Callback* _cb=&callback;
  WebConfigServer() { strcpy(callback.node.admin_password, "existing-secret"); }
  bool checkAuth(AsyncWebServerRequest*);
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
  portal.checkConfigPost(&attacker);
  assert(attacker.status==401 && !portal.config_accepted);
  attacker.password("wrong"); portal.handleLogin(&attacker);
  assert(attacker.status==401 && portal._session_token[0]==0);
  AsyncWebServerRequest admin; admin.password("existing-secret");
  portal.handleLogin(&admin); assert(admin.status==200);
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
  portal._initial_setup=true; assert(portal.checkAuth(&attacker));
  strcpy(portal._wifi_ssid, "ProvisionedNet"); assert(portal.checkAuth(&attacker));
  portal.handleLogin(&attacker); assert(attacker.status==200);
  attacker.socket.local_ip=9; assert(!portal.checkAuth(&attacker));
  portal.handleLogin(&attacker); assert(attacker.status==401); attacker.socket.local_ip=4;
  // Companion images deliberately have no administrator password or CLI
  // password management. Keep that explicit no-password deployment policy.
  portal._initial_setup=false; portal.callback.node.admin_password[0]=0;
  assert(portal.checkAuth(&attacker)); portal.handleLogin(&attacker); assert(attacker.status==200);
  puts("WebConfig recovery authentication regression checks passed");
}
