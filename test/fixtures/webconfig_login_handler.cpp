#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <ArduinoJson.h>
#include <helpers/WebConfigLoginThrottle.h>
static uint32_t now_ms=100000, random_word=0x12345678;
uint32_t millis() { return now_ms; }
uint32_t esp_random() { return ++random_word; }
struct NodeSnapshot { char admin_password[16] = {}; };
struct Callback {
  NodeSnapshot node;
  void getNodeSnapshot(NodeSnapshot& result) { result=node; }
};
struct WCLock { explicit WCLock(int) {} };
struct WiFiMock { int softAPIP() const { return 4; } } WiFi;
struct Client {
  int local_ip=4; uint32_t remote=0;
  int localIP() const { return local_ip; }
  uint32_t getRemoteAddress() const { return remote; }
};
struct AsyncWebServerResponse {
  int status; std::map<std::string, std::string> headers;
  void addHeader(const char* key, const char* value) { headers[key]=value; }
};
struct AsyncWebServerRequest {
  Client socket; bool has_client=true;
  std::string body, reply; void* _tempObject=nullptr;
  int status=0; AsyncWebServerResponse response{};
  Client* client() { return has_client ? &socket : nullptr; }
  void send(int code, const char* =nullptr, const char* content=nullptr) { status=code; reply=content ? content : ""; }
  AsyncWebServerResponse* beginResponse(int code, const char*, const char*) { response.status=code; return &response; }
  void send(AsyncWebServerResponse* value) { status=value->status; }
};
struct WebConfigServer {
  enum { MODE_OFF, MODE_SETUP, MODE_LAN };
  int _mode=MODE_SETUP, _mux=0;
  char _wifi_ssid[33]="SavedNet", _session_token[33] = {};
  bool _initial_setup=false;
  uint32_t _last_activity=0, _session_last_seen=0;
  WebConfigLoginThrottle::Tracker _login_throttle;
  Callback callback; Callback* _cb=&callback;
  WebConfigServer() { strcpy(callback.node.admin_password, "existing-secret"); }
  void noteNetworkActivity() {}
  void handleLogin(AsyncWebServerRequest*);
};
@METHODS@
static const char* kLocked = "{\"error\":\"locked, retry in 30s\"}";
static const char* kShared = "{\"error\":\"too many failed logins, retry shortly\"}";
static std::string last_reply;
static int login(WebConfigServer& portal, uint32_t address, const char* password, bool has_client=true) {
  AsyncWebServerRequest req; req.socket.remote=address; req.has_client=has_client;
  req.body=std::string("{\"password\":\"")+password+"\"}"; req._tempObject=req.body.data();
  portal.handleLogin(&req); last_reply=req.reply; return req.status;
}
int main() {
  const uint32_t attacker=0x0204A8C0, owner=0x0304A8C0;
  // Recovery AP on a configured node: failures from one address lock only
  // that address. The owner on another address still gets in.
  {
    WebConfigServer portal;
    for (int i = 0; i < 5; i++) assert(login(portal, attacker, "guess")==401);
    assert(login(portal, attacker, "existing-secret")==429 && last_reply==kLocked);
    assert(login(portal, owner, "existing-secret")==200);
    assert(strlen(portal._session_token)==32);
    assert(login(portal, attacker, "existing-secret")==429 && last_reply==kLocked);
    now_ms+=30000;
    assert(login(portal, attacker, "existing-secret")==200);
  }
  // A success clears the owner's own earlier typos.
  {
    WebConfigServer portal;
    for (int i = 0; i < 4; i++) assert(login(portal, owner, "typo")==401);
    assert(login(portal, owner, "existing-secret")==200);
    now_ms+=30000;
    assert(login(portal, owner, "typo")==401);
    assert(login(portal, owner, "existing-secret")==200);
  }
  // Rotating addresses does not add guesses: after the shared budget is
  // spent every address waits, then one more attempt per refill period.
  {
    WebConfigServer portal; uint32_t address=10;
    for (int i = 0; i < 6; i++) assert(login(portal, address++, "guess")==401);
    assert(login(portal, owner, "existing-secret")==429 && last_reply==kShared);
    now_ms+=5999;
    assert(login(portal, owner, "existing-secret")==429 && last_reply==kShared);
    now_ms+=1;
    assert(login(portal, owner, "existing-secret")==200);
  }
  // An old lockout does not return when millis() is half a wrap past it.
  {
    WebConfigServer portal;
    for (int i = 0; i < 5; i++) assert(login(portal, attacker, "guess")==401);
    now_ms+=0x80000000u+60000u;
    assert(login(portal, owner, "existing-secret")==200);
    assert(login(portal, attacker, "existing-secret")==200);
  }
  // LAN requests without a client object share one key and do not crash.
  {
    WebConfigServer portal; portal._mode=WebConfigServer::MODE_LAN;
    for (int i = 0; i < 5; i++) assert(login(portal, 0, "guess", false)==401);
    assert(login(portal, 0, "existing-secret", false)==429 && last_reply==kLocked);
    assert(login(portal, owner, "existing-secret")==200);
  }
  puts("WebConfig per-client login lockout checks passed");
}
