#include <atomic>
#define MESH_MANAGEMENT_PASSWORD_HOST_TEST 1
#include <helpers/esp32/ManagementPassword.h>
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <string>
#include <vector>
using String = std::string;
constexpr int HTTP_GET=0, HTTP_POST=1, U_FLASH=0, U_SPIFFS=100;
constexpr unsigned UPDATE_SIZE_UNKNOWN=~0U;
constexpr int pdPASS=1;
constexpr int ERR_OK=0;
#define async_tcp_log_d(...) ((void)0)
#define pdMS_TO_TICKS(value) (value)
const char ELEGANT_HTML[] = "page";
const unsigned ELEGANT_HTML_SIZE=sizeof(ELEGANT_HTML);
int Serial=0;
bool in_network_callback=false;
struct Esp {
  int restarts=0;
  void restart() { assert(!in_network_callback); ++restarts; }
} ESP;
void yield() { assert(!in_network_callback); }
void delay(unsigned) { assert(!in_network_callback); }
void vTaskDelay(unsigned) { assert(!in_network_callback); }
void vTaskDelete(void*) {}
using Task = void (*)(void*);
Task pending_task=nullptr;
bool allow_task=true;
int xTaskCreate(Task task, const char*, unsigned, void*, unsigned, void*) {
  if (!allow_task) return 0;
  assert(!pending_task); pending_task=task; return pdPASS;
}
@REBOOT_STATE@
@UPLOAD_STATE@
// Independent MD5 vectors: eight zero bytes, and that payload with byte zero
// changed to one. The fake models Update's lifecycle rather than its digest
// algorithm; both vectors are fixed independently of the uploader callbacks.
const char fixture_md5[]="7dea362b3fac8e00956a4952a3d4f474";
const char fixture_corrupt_md5[]="33cdeccccebe80329f1fdbee7f5874cb";
struct Parameter { String text=fixture_md5; const String& value() { return text; } };
struct Response {
  int code;
  String body;
  std::vector<std::pair<String, String>> headers;
  void addHeader(const char* key, const char* value) { headers.emplace_back(key, value); }
};
unsigned fixture_now=0;
unsigned millis() { return fixture_now; }
struct Client {
  unsigned _rx_timeout=0, _rx_last_packet=fixture_now;
  bool closed=false;
  std::vector<unsigned> timeout_changes;
  std::function<void()> disconnected;
  void setRxTimeout(unsigned seconds) { _rx_timeout=seconds; timeout_changes.push_back(seconds); }
  void received() { _rx_last_packet=fixture_now; }
  void _close() { closed=true; if (disconnected) disconnected(); }
  int poll() {
    const unsigned now=fixture_now;
@RX_TIMEOUT@
    return ERR_OK;
  }
};
void accept(Client* c) {
@ACCEPT_TIMEOUT@
}
struct Request {
  void* _tempObject=nullptr;
  bool md5_present=true, authorized=true, sent=false;
  int code=0;
  String body;
  Response* queued_response=nullptr;
  std::function<void()> disconnected;
  Parameter md5;
  Parameter authorization;
  bool authorization_present = false;
  bool hasHeader(const char*) { return authorization_present; }
  Parameter* getHeader(const char*) { return &authorization; }
  Client connection;
  Request() { accept(&connection); }
  ~Request() { free(_tempObject); delete queued_response; }
  Client* client() { return &connection; }
  bool authenticate(const char*, const char*) { return authorized; }
  void requestAuthentication() { send(401,"text/plain","unauthorized"); }
  bool hasParam(const char*, bool) { return md5_present; }
  Parameter* getParam(const char*, bool) { return &md5; }
  Response* beginResponse(int code, const char*, const char* text) { return new Response{code,text}; }
  Response* beginResponse_P(int code, const char*, const char* text, unsigned) { return new Response{code,text}; }
  void send(Response* response) { delete queued_response; queued_response=response; code=response->code; body=response->body; }
  void send(int status, const char*, const char* text) { send(new Response{status,text}); }
  void send(int status, const char*, const String& text) { send(new Response{status,text}); }
  Response* getResponse() const { return queued_response; }
  void flush_response() { assert(queued_response); sent=true; connection.setRxTimeout(0); }
  bool isSent() const { return sent; }
  void onDisconnect(std::function<void()> fn) { disconnected=fn; connection.disconnected=fn; }
};
using AsyncWebServerRequest=Request;
static AsyncWebServerRequest* ota_upload_owner=nullptr;
using AsyncWebServerResponse=Response;
struct AsyncWebServer {
  std::vector<std::function<void(Request*)>> gets;
  std::function<void(Request*)> completion;
  std::function<void(Request*,String,size_t,uint8_t*,size_t,bool)> upload;
  template<class F> void on(const char*, int method, F callback) {
    assert(method == HTTP_GET); gets.push_back(callback);
  }
  template<class F,class U> void on(const char*, int method, F callback, U chunks) {
    assert(method==HTTP_POST); completion=callback; upload=chunks;
  }
};
struct UpdateFake {
  bool error=false, running=false, fail_begin=false, fail_end=false, fail_write=false;
  int aborts=0, writes=0, begins=0, md5_checks=0, ends=0, boot_commits=0;
  String target_md5;
  std::vector<uint8_t> received;
  bool hasError() const { return error; }
  bool setMD5(const char* md5) {
    // Arduino-ESP32 2.0.17 checks length here, and checks content at end().
    if (std::strlen(md5)!=32) return false;
    target_md5=md5;
    return true;
  }
  bool begin(unsigned, int) {
    ++begins;
    if (running) return false;
    // Pinned UpdateClass::begin() clears a digest set before initialization.
    target_md5.clear(); received.clear();
    error=fail_begin; running=!error;
    return running;
  }
  size_t write(uint8_t* data, size_t size) {
    ++writes;
    if (error || !running) return 0;
    if (fail_write) { error=true; running=false; return size-1; }
    received.insert(received.end(),data,data+size);
    return size;
  }
  bool end(bool) {
    ++ends;
    if (error || !running) return false;
    if (fail_end) { error=true; running=false; return false; }
    assert(received.size()==8);
    const std::vector<uint8_t> normal(8,0);
    auto corrupted=normal; corrupted[0]=1;
    assert(received==normal || received==corrupted);
    const String actual_md5=received==normal ? fixture_md5 : fixture_corrupt_md5;
    // Match Arduino: an empty target skips validation. Success assertions below
    // require a real comparison, catching the original pre-begin ordering.
    if (!target_md5.empty()) {
      ++md5_checks;
      if (target_md5!=actual_md5) { error=true; running=false; return false; }
    }
    running=false;
    // Pinned UpdateClass::_verifyEnd() activates the boot partition when end()
    // succeeds. Track that irreversible effect separately from file receipt.
    ++boot_commits;
    return true;
  }
  void abort() { error=true; running=false; ++aborts; }
  // A connected USB host which does not drain TX can block raw Serial. Every
  // upload error path must complete without attempting that raw print.
  void printError(int) { assert(false && "Raw USB printing can block OTA callbacks"); }
} Update;
struct AsyncElegantOtaClass {
  AsyncWebServer* _server=nullptr;
  bool _authRequired=false, _managementPassword=false;
  bool authenticateRequest(AsyncWebServerRequest*, uint32_t* generation=nullptr);
  String _username, _password, _id="fixture";
  void begin(AsyncWebServer*, const char*, const char*);
  void restart();
  bool setEnabled(bool);
};
@AUTH@
@BEGIN@
@RESTART@

int main() {
  AsyncWebServer server;
  AsyncElegantOtaClass ota;
  ota.begin(&server,"","");
  auto complete=[&](Request& request) {
    in_network_callback=true;
    server.completion(&request); request.flush_response();
    in_network_callback=false;
  };
  auto upload=[&](Request& request) {
    uint8_t data[8]={};
    in_network_callback=true;
    server.upload(&request,"firmware",0,data,sizeof(data),true);
    in_network_callback=false;
  };
#ifdef TEST_OTA_MANAGEMENT_PASSWORD
  {
  using namespace mesh::esp32::managementPassword;
  ota._managementPassword = true;
  Request unpublished;
  server.gets[0](&unpublished); assert(unpublished.code == 503 && Update.begins == 0);
  assert(publish("password"));
  auto authorize = [](Request& request, const char* value = "Basic YWRtaW46cGFzc3dvcmQ=") {
    request.authorization_present = true; request.authorization.text = value;
  };
  assert(server.gets.size() == 2);
  for (auto& get : server.gets) {
    Request absent; get(&absent); assert(absent.code == 401);
    assert(absent.queued_response->headers.size() == 2);
    assert(absent.queued_response->headers[0].first == "WWW-Authenticate"
        && absent.queued_response->headers[0].second == "Basic realm=\"MeshCore (username: admin)\", charset=\"UTF-8\"");
    assert(absent.body == "Sign in with username admin and this node's admin password.");
    assert(absent.queued_response->headers[1].first == "Cache-Control"
        && absent.queued_response->headers[1].second == "no-store");
    Request wrong; authorize(wrong, "Basic wrong"); get(&wrong); assert(wrong.code == 401);
    Request good; authorize(good); get(&good); assert(good.code == 200);
  }
  Request wrong_post; authorize(wrong_post, "Basic wrong");
  upload(wrong_post); complete(wrong_post);
  assert(wrong_post.code == 401 && Update.begins == 0 && Update.writes == 0 && !uploadActive());
  assert(publish("different"));
  Request stale; authorize(stale); upload(stale); assert(stale.code == 401 && Update.begins == 0);
  const char* updated = "Basic YWRtaW46ZGlmZmVyZW50";
  {
    ChangeGuard saving; assert(saving.acquired());
    Request blocked; authorize(blocked, updated); upload(blocked);
    assert(blocked.code == 409 && Update.begins == 0 && !uploadActive());
  }
  uint8_t half[4] = {};
  Request partial; authorize(partial, updated);
  server.upload(&partial, "firmware", 0, half, sizeof(half), false);
  assert(Update.begins == 1 && Update.writes == 1 && uploadActive());
  {
    ChangeGuard changing; assert(!changing.acquired());
    assert(!publish("not-committed"));
  }
  // Authentication is rechecked on every chunk before touching Update.
  authorize(partial, "Basic wrong");
  server.upload(&partial, "firmware", sizeof(half), half, sizeof(half), true);
  assert(partial.code == 401 && Update.writes == 1 && Update.ends == 0);
  in_network_callback = true; partial.disconnected(); in_network_callback = false;
  assert(!uploadActive() && !ota_upload_busy.load() && Update.aborts == 1 && !pending_task);
  Request valid; authorize(valid, updated); upload(valid);
  assert(uploadActive() && Update.writes == 2);
  complete(valid);
  assert(valid.code == 200 && Update.boot_commits == 1 && uploadActive());
  in_network_callback = true; valid.disconnected(); in_network_callback = false;
  assert(!uploadActive() && pending_task);
  auto task = pending_task; pending_task = nullptr; task(nullptr);
  assert(ESP.restarts == 1);
  return 0;
  }
#endif
#if defined(TEST_OTA_SECOND_FILE) || defined(TEST_OTA_INCOMPLETE_POST)
  {
  Request rejected;
  upload(rejected);
  assert(ota_upload_owner==&rejected && ota_upload_busy.load()
      && !rejected.isSent() && !rejected.getResponse());
#ifdef TEST_OTA_SECOND_FILE
  // AsyncWebServer starts each multipart file at index zero. A second file
  // must keep its queued 409 instead of committing the already received file.
  upload(rejected);
  assert(!rejected.isSent() && rejected.getResponse() && rejected.code==409);
  complete(rejected);
  assert(rejected.code==409 && rejected.body=="OTA upload already active");
#else
  // The file-final boundary can arrive without the closing POST boundary.
  // Do not invoke the completion callback for this interrupted request.
  assert(rejected.code==0);
#endif
  in_network_callback=true; rejected.disconnected(); in_network_callback=false;
  // Execute any incorrectly scheduled restart so the negative control checks
  // both scheduling and the boot effect, rather than leaving work pending.
  if (pending_task) { auto task=pending_task; pending_task=nullptr; task(nullptr); }
  assert(Update.ends==0 && Update.boot_commits==0 && Update.md5_checks==0
      && Update.aborts==1 && !Update.running && !ota_upload_owner
      && !ota_upload_busy.load() && !ota_reboot_pending.load()
      && !pending_task && ESP.restarts==0);
  // Cleanup permits a valid upload on the same listener without restarting it.
  Request valid;
  upload(valid);
  assert(Update.ends==0 && Update.boot_commits==0 && Update.running);
  complete(valid);
  assert(valid.code==200 && valid.body=="OK" && Update.ends==1
      && Update.boot_commits==1 && Update.md5_checks==1 && !Update.running
      && ota_upload_owner==&valid && !pending_task && ESP.restarts==0);
  in_network_callback=true; valid.disconnected(); in_network_callback=false;
  assert(!ota_upload_owner && !ota_upload_busy.load() && pending_task && ESP.restarts==0);
  auto task=pending_task; pending_task=nullptr; task(nullptr);
  assert(ESP.restarts==1);
  return 0;
  }
#endif
#ifdef TEST_OTA_BODY_TIMEOUT
  {
  uint8_t chunk[4]={};
  auto receive=[&](Request& request,size_t index,bool final) {
    assert(!request.connection.closed);
    request.connection.received();
    in_network_callback=true;
    server.upload(&request,"firmware",index,chunk,sizeof(chunk),final);
    in_network_callback=false;
  };
  auto idle=[&](Request& request,unsigned ms) {
    fixture_now+=ms;
    in_network_callback=true; request.connection.poll(); in_network_callback=false;
  };
  Request ordinary;
  assert(ordinary.connection._rx_timeout==3);
  idle(ordinary,4000);
  assert(ordinary.connection.closed && !ota_upload_owner);
  Request admitted;
  receive(admitted,0,false);
  assert(ota_upload_owner==&admitted && !admitted.sent
      && admitted.connection.timeout_changes==std::vector<unsigned>({3,30}));
  Request concurrent;
  upload(concurrent);
  assert(!concurrent.isSent() && concurrent.getResponse()
      && concurrent.connection.timeout_changes==std::vector<unsigned>({3}));
  complete(concurrent);
  assert(concurrent.code==409 && ota_upload_owner==&admitted
      && concurrent.connection.timeout_changes==std::vector<unsigned>({3,0}));
  Request untouched;
  assert(untouched.connection._rx_timeout==3);
  idle(admitted,4000);
  assert(!admitted.connection.closed && ota_upload_owner==&admitted);
  idle(admitted,25999);
  assert(!admitted.connection.closed);
  idle(admitted,1);
  assert(admitted.connection.closed && Update.aborts==1 && !ota_upload_owner
      && !ota_upload_busy.load() && !pending_task && ESP.restarts==0);
  Request missing_md5;
  missing_md5.md5_present=false;
  upload(missing_md5);
  assert(!missing_md5.isSent() && missing_md5.getResponse()
      && missing_md5.connection.timeout_changes==std::vector<unsigned>({3}));
  complete(missing_md5);
  assert(missing_md5.code==400
      && missing_md5.connection.timeout_changes==std::vector<unsigned>({3,0})
      && !ota_upload_owner && !ota_upload_busy.load());
  Request bad_md5;
  bad_md5.md5.text="invalid";
  const int aborts_before_bad_md5=Update.aborts;
  upload(bad_md5); complete(bad_md5);
  assert(bad_md5.code==400 && !ota_upload_owner && !ota_upload_busy.load()
      && Update.aborts==aborts_before_bad_md5+1 && !Update.running
      && bad_md5.connection.timeout_changes==std::vector<unsigned>({3,0}));
  ota.begin(&server,"admin","password");
  Request unauthorized;
  unauthorized.authorized=false;
  upload(unauthorized); complete(unauthorized);
  assert(unauthorized.code==401 && !ota_upload_owner && !ota_upload_busy.load()
      && unauthorized.connection.timeout_changes==std::vector<unsigned>({3,0}));
  ota.begin(&server,"","");
  Request successful;
  receive(successful,0,false);
  idle(successful,4000);
  assert(!successful.connection.closed && ota_upload_owner==&successful);
  receive(successful,sizeof(chunk),true);
  complete(successful);
  assert(successful.code==200 && successful.body=="OK"
      && successful.connection._rx_timeout==0 && !pending_task
      && Update.md5_checks==1 && !Update.running);
  in_network_callback=true; successful.disconnected(); in_network_callback=false;
  assert(pending_task && !ota_upload_owner);
  auto task=pending_task; pending_task=nullptr; task(nullptr);
  assert(ESP.restarts==1);
  return 0;
  }
#endif
  // A POST without an actual completed upload cannot report OK or reboot.
  Request empty;
  complete(empty);
  assert(empty.code>=400 && !pending_task && ESP.restarts==0);
  Request invalid;
  invalid.md5_present=false;
  upload(invalid); complete(invalid);
  assert(invalid.code==400 && !pending_task && ESP.restarts==0);
  Request begin_failed;
  Update.fail_begin=true;
  upload(begin_failed); complete(begin_failed);
  assert(begin_failed.code==400 && begin_failed.body=="OTA could not begin"
      && !ota_upload_owner && !ota_upload_busy.load() && Update.aborts==0
      && !pending_task && ESP.restarts==0);
  Update.fail_begin=false;
  // An invalid MD5 must release initialization without creating an owner,
  // writing bytes, extending the receive timeout, or scheduling a reboot.
  Request bad_md5;
  bad_md5.md5.text="invalid";
  const int writes_before_bad_md5=Update.writes;
  const int begins_before_bad_md5=Update.begins;
  upload(bad_md5); complete(bad_md5);
  assert(bad_md5.code==400 && bad_md5.body=="MD5 parameter invalid"
      && !bad_md5._tempObject && !bad_md5.disconnected && !ota_upload_owner
      && !ota_upload_busy.load() && !Update.running && Update.aborts==1
      && Update.writes==writes_before_bad_md5 && Update.begins==begins_before_bad_md5+1
      && bad_md5.connection.timeout_changes==std::vector<unsigned>({3,0})
      && !pending_task && ESP.restarts==0);
  // A correctly sized but incorrect digest is admitted, then rejected only
  // after all file bytes arrive. Its owner stays reserved until disconnect.
  Request mismatched_md5;
  mismatched_md5.md5.text=fixture_corrupt_md5;
  upload(mismatched_md5); complete(mismatched_md5);
  assert(mismatched_md5.code==400 && mismatched_md5.body=="Could not end OTA"
      && ota_upload_owner==&mismatched_md5 && ota_upload_busy.load()
      && !static_cast<OtaUploadState*>(mismatched_md5._tempObject)->committed
      && Update.md5_checks==1 && !pending_task && ESP.restarts==0);
  in_network_callback=true; mismatched_md5.disconnected(); in_network_callback=false;
  assert(Update.aborts==2 && !ota_upload_owner && !ota_upload_busy.load()
      && !Update.running && !pending_task && ESP.restarts==0);
  // Corrupt the actual bytes with the original digest still supplied. This
  // independently rejects corruption instead of relying on a forced end error.
  Request corrupt_bytes;
  uint8_t corrupted_data[8]={1};
  in_network_callback=true;
  server.upload(&corrupt_bytes,"firmware",0,corrupted_data,sizeof(corrupted_data),true);
  in_network_callback=false;
  complete(corrupt_bytes);
  assert(corrupt_bytes.code==400 && corrupt_bytes.body=="Could not end OTA"
      && ota_upload_owner==&corrupt_bytes && ota_upload_busy.load()
      && !static_cast<OtaUploadState*>(corrupt_bytes._tempObject)->committed
      && Update.md5_checks==2 && !pending_task && ESP.restarts==0);
  in_network_callback=true; corrupt_bytes.disconnected(); in_network_callback=false;
  assert(Update.aborts==3 && !ota_upload_owner && !ota_upload_busy.load()
      && !Update.running && !pending_task && ESP.restarts==0);
  Request corrupt;
  Update.fail_end=true;
  upload(corrupt); complete(corrupt);
  assert(corrupt.code==400 && !pending_task && ESP.restarts==0);
  in_network_callback=true; corrupt.disconnected(); in_network_callback=false;
  assert(Update.aborts==4 && ota_upload_owner==nullptr);
  Update.fail_end=false;
  Request partial;
  uint8_t chunk[4]={};
  server.upload(&partial,"firmware",0,chunk,sizeof(chunk),false);
  assert(!ota.setEnabled(false)); // An admitted upload keeps its listener alive.
  Request concurrent;
  upload(concurrent); complete(concurrent);
  assert(concurrent.code==409 && ota_upload_owner==&partial);
  in_network_callback=true; partial.disconnected(); in_network_callback=false;
  assert(Update.aborts==5 && ota_upload_owner==nullptr && ESP.restarts==0);
  assert(ota.setEnabled(false));
  Request stopped;
  upload(stopped); complete(stopped);
  assert(stopped.code==503 && !ota_upload_owner && !pending_task);
  assert(ota.setEnabled(true));
  Request write_failed;
  Update.fail_write=true;
  server.upload(&write_failed,"firmware",0,chunk,sizeof(chunk),false);
  assert(!write_failed.isSent() && write_failed.getResponse()
      && write_failed.code==400 && write_failed.body=="OTA flash write failed");
  const int writes_after_error=Update.writes;
  server.upload(&write_failed,"firmware",sizeof(chunk),chunk,sizeof(chunk),true);
  assert(Update.writes==writes_after_error);
  server.upload(&write_failed,"firmware",0,chunk,sizeof(chunk),false);
  assert(Update.writes==writes_after_error && write_failed.code==400
      && write_failed.body=="OTA flash write failed");
  complete(write_failed);
  assert(write_failed.code==400 && write_failed.body=="OTA flash write failed"
      && !pending_task);
  in_network_callback=true; write_failed.disconnected(); in_network_callback=false;
  assert(Update.aborts==6 && !ota_upload_owner && !ota_upload_busy.load());
  Update.fail_write=false;
  Request valid;
  const int ends_before_valid=Update.ends, commits_before_valid=Update.boot_commits;
  upload(valid);
  assert(Update.ends==ends_before_valid && Update.boot_commits==commits_before_valid
      && Update.running && !static_cast<OtaUploadState*>(valid._tempObject)->committed);
  complete(valid);
  assert(valid.code==200 && valid.disconnected && !pending_task && ESP.restarts==0
      && Update.md5_checks==3 && !Update.running
      && Update.ends==ends_before_valid+1 && Update.boot_commits==commits_before_valid+1);
  // The response drains before disconnect; its callback only schedules work.
  in_network_callback=true; valid.disconnected(); in_network_callback=false;
  assert(pending_task && ESP.restarts==0);
  auto task=pending_task; pending_task=nullptr; task(nullptr);
  assert(ESP.restarts==1);
}
