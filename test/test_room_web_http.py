#!/usr/bin/env python3
"""Execute actual room HTTP/loop handlers and mailbox under adversarial retries."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <mutex>
#include <new>
#include <string>
#include <thread>
#include <vector>
#include <helpers/RoomWebMailbox.h>
#include <helpers/CLICommandUtils.h>
#include <helpers/WebConfigBatch.h>
using String = std::string;
#define PROGMEM
@HTML@
static uint32_t ticks = 100;
static uint32_t millis() { return ticks; }
using SemaphoreHandle_t = std::mutex*;
static thread_local unsigned lock_depth = 0;
static constexpr unsigned portMAX_DELAY = 0xffffffffu;
static void xSemaphoreTake(SemaphoreHandle_t h, unsigned) { h->lock(); ++lock_depth; }
static void xSemaphoreGive(SemaphoreHandle_t h) { assert(lock_depth == 1); --lock_depth; h->unlock(); }
@LOCK@
static std::mutex s_wc_route_mux;
#define portENTER_CRITICAL(h) (h)->lock()
#define portEXIT_CRITICAL(h) (h)->unlock()
namespace mesh {
struct Debug { void printf(const char*, ...) {} };
static Debug& usbDebugPort() { static Debug value; return value; }
struct Utils {
  static void sha256(uint8_t* out, size_t count, const uint8_t* data, size_t length) {
    // Only callback ordering/ownership is under test; production still uses SHA256.
    uint32_t hash = 2166136261u;
    for (size_t i = 0; i < length; ++i) hash = (hash ^ data[i]) * 16777619u;
    for (size_t i = 0; i < count; ++i) out[i] = uint8_t(hash >> ((i & 3) * 8));
  }
};
}
struct Header { String text; const String& value() const { return text; } };
struct AsyncWebServerResponse {
  int status;
  String type, body;
  std::map<String, String> headers;
  AsyncWebServerResponse(int code, String content_type, String text) : status(code), type(content_type), body(text) {}
  virtual ~AsyncWebServerResponse() = default;
  void addHeader(const char* key, const char* value) { headers[key] = value; }
};
static bool fail_page_allocation = false;
struct WebConfigPacedProgmemResponse : AsyncWebServerResponse {
  WebConfigPacedProgmemResponse(const char* type, const uint8_t* bytes, size_t length)
      : AsyncWebServerResponse(200, type, String(reinterpret_cast<const char*>(bytes), length)) {}
  static void* operator new(size_t n, const std::nothrow_t&) noexcept {
    if (fail_page_allocation) return nullptr;
    try { return ::operator new(n); } catch (...) { return nullptr; }
  }
  static void operator delete(void* p) { ::operator delete(p); }
  static void operator delete(void* p, const std::nothrow_t&) { ::operator delete(p); }
};
struct AsyncWebServerRequest {
  std::map<String, Header> headers;
  void* _tempObject = nullptr;
  size_t length = 0;
  String content_type = "application/json";
  int status = 0;
  String reply, response_type;
  std::map<String, String> response_headers;
  bool fail_response = false;
  std::function<void()> on_send;
  ~AsyncWebServerRequest() { free(_tempObject); }
  bool hasHeader(const char* key) const { return headers.count(key) != 0; }
  Header* getHeader(const char* key) { return &headers.at(key); }
  const String& contentType() const { return content_type; }
  size_t contentLength() const { return length; }
  AsyncWebServerResponse* beginResponse(int code, const char* type, const String& text) {
    if (fail_response) return nullptr;
    return new AsyncWebServerResponse(code, type, text);
  }
  void send(int code, const char* type = "", const char* text = "") {
    status = code; response_type = type; reply = text;
    if (on_send) on_send();
  }
  void send(AsyncWebServerResponse* response) {
    assert(response);
    status = response->status; response_type = response->type;
    reply = response->body; response_headers = response->headers;
    delete response;
    if (on_send) on_send();
  }
};
static void wcLogReq(AsyncWebServerRequest*) {}
static constexpr int HTTP_GET = 1, HTTP_POST = 2;
struct AsyncWebServer {
  using Handler = std::function<void(AsyncWebServerRequest*)>;
  using Collector = void(*)(AsyncWebServerRequest*, uint8_t*, size_t, size_t, size_t);
  struct Route { String path; int method; Handler handler; Collector collector; };
  std::vector<Route> routes;
  Handler missing;
  bool ended = false;
  void on(const char* path, int method, Handler handler, void* = nullptr, Collector body = nullptr) {
    routes.push_back({path, method, handler, body});
  }
  void onNotFound(Handler handler) { missing = handler; }
  void end() { ended = true; }
  void invoke(const char* path, int method, AsyncWebServerRequest& request) {
    for (const auto& route : routes) {
      // Model AsyncWebServer's ordered prefix matching: /result must precede /room.
      if (route.method == method && String(path).compare(0, route.path.size(), route.path) == 0) {
        route.handler(&request); return;
      }
    }
    assert(missing); missing(&request);
  }
};
struct DNS { unsigned stops = 0; void stop() { ++stops; } };
struct Callbacks {
  bool supported = true, authenticated = true;
  bool roomRequestAuthenticated() const { return authenticated; }
  unsigned calls = 0, stops = 0;
  String last_body;
  uint8_t last_token[32] = {};
  uint32_t last_sequence = 0;
  size_t last_capacity = 0;
  std::function<void(char*, char*)> in_callback;
  bool supportsRoomService() const { return supported; }
  void processRoomRequest(const uint8_t* token, uint32_t sequence, char* body, char* result, size_t capacity) {
    assert(lock_depth == 0); // Mutating callback runs only on the loop, outside the HTTP mutex.
    ++calls; last_body = body; memcpy(last_token, token, 32);
    last_sequence = sequence; last_capacity = capacity;
    strcpy(result, "{\"owner_secret\":\"result-one\"}");
    if (in_callback) in_callback(body, result);
  }
  void onWebConfigStopped() { ++stops; }
};
struct WebConfigServer {
  static constexpr size_t MAX_BODY = @MAX_BODY@;
  static constexpr uint32_t STOP_WARN_MS = WebConfigBatch::kStopWarnMs;
  enum Mode { MODE_OFF, MODE_SETUP, MODE_LAN };
  Mode _mode = MODE_LAN;
  bool _stopping = false, _stop_warned = false, _board_cmds_probed = true;
  uint32_t _handler_refs = 0, _stop_warn_at = 0, _last_activity = 0;
  std::mutex mutex;
  SemaphoreHandle_t _mux = &mutex;
  mesh::RoomWebMailbox* _room_mailbox = nullptr;
  Callbacks* _cb;
  AsyncWebServer host;
  AsyncWebServer* _server = &host;
  DNS* _dns = nullptr;
  static WebConfigServer* _active;
  typedef void (WebConfigServer::*RequestHandler)(AsyncWebServerRequest*);
  unsigned finalizations = 0;
  explicit WebConfigServer(Callbacks& callbacks, bool allocate = true) : _cb(&callbacks) {
    if (allocate) _room_mailbox = new mesh::RoomWebMailbox;
    registerRoutes(); _active = this;
  }
  ~WebConfigServer() { if (_active == this) _active = nullptr; delete _room_mailbox; }
  void finalizeTeardown() { // Dependency stub: production tick decides when reclaim is safe.
    assert(_handler_refs == 0); ++finalizations;
    delete _room_mailbox; _room_mailbox = nullptr; _server = nullptr;
    _stopping = false; _cb->onWebConfigStopped();
  }
  void probeBoardCommands() { _board_cmds_probed = true; }
  void serviceTerminal(uint32_t) {}
  void serviceRoom(uint32_t);
  void tick(uint32_t);
  void requestStop();
  void detachRoutes();
  uint32_t handlerRefCount() const;
  void registerRoutes();
  static void dispatchRequest(AsyncWebServerRequest*, RequestHandler);
  static void collectBody(AsyncWebServerRequest*, uint8_t*, size_t, size_t, size_t);
  void handleRoomPage(AsyncWebServerRequest*);
  void handleRoomPost(AsyncWebServerRequest*);
  void handleRoomResult(AsyncWebServerRequest*);
  @UNRELATED_HANDLERS@
};
WebConfigServer* WebConfigServer::_active = nullptr;
@PRODUCTION@
static String token(unsigned value = 1) {
  static const char digits[] = "0123456789abcdef";
  String text(64, '0'); text[62] = digits[(value >> 4) & 15]; text[63] = digits[value & 15]; return text;
}
static void identity(AsyncWebServerRequest& request, unsigned owner = 1, const char* sequence = "1") {
  request.headers["X-Room-Token"].text = token(owner);
  request.headers["X-Room-Seq"].text = sequence;
  request.headers["Host"].text = "192.168.4.1";
  request.headers["Origin"].text = "http://192.168.4.1";
}
static void body(AsyncWebServerRequest& request, const String& text) {
  request.length = text.size();
  const size_t first = text.size() / 2;
  WebConfigServer::collectBody(&request, reinterpret_cast<uint8_t*>(const_cast<char*>(text.data())), first, 0, text.size());
  WebConfigServer::collectBody(&request, reinterpret_cast<uint8_t*>(const_cast<char*>(text.data() + first)), text.size() - first, first, text.size());
}
static void submit(WebConfigServer& server, AsyncWebServerRequest& request) { server.host.invoke("/api/room", HTTP_POST, request); }
static void result(WebConfigServer& server, AsyncWebServerRequest& request) { server.host.invoke("/api/room/result", HTTP_POST, request); }

static void validation() {
  for (unsigned fault = 0; fault < 19; ++fault) {
    Callbacks cb; WebConfigServer server(cb); AsyncWebServerRequest request;
    identity(request); body(request, "{\"op\":\"post\"}");
    switch (fault) {
      case 0: request.headers.erase("X-Room-Token"); break;
      case 1: request.headers.erase("X-Room-Seq"); break;
      case 2: request.headers["X-Room-Token"].text = String(64, '0'); break;
      case 3: request.headers["X-Room-Token"].text = token().substr(1); break;
      case 4: request.headers["X-Room-Token"].text[0] = 'g'; break;
      case 5: request.headers["X-Room-Seq"].text = "0"; break;
      case 6: request.headers["X-Room-Seq"].text = "-1"; break;
      case 7: request.headers["X-Room-Seq"].text = "4294967296"; break;
      case 8: request.headers["X-Room-Seq"].text = "1junk"; break;
      case 9: request.headers["X-Room-Seq"].text = "\n1"; break;
      case 10: request.headers["Origin"].text = "http://attacker.invalid"; break;
      case 11: request.headers.erase("Host"); break;
      case 12: request.headers["Origin"].text = "https://192.168.4.1"; break;
      case 13: request.content_type = "text/plain"; break;
      case 14: request.content_type = "application/json; charset=utf-8"; break;
      case 15: free(request._tempObject); request._tempObject = nullptr; break;
      case 16: request.length = 0; break;
      case 17: request.length = WebConfigServer::MAX_BODY + 1; break;
      case 18: static_cast<char*>(request._tempObject)[2] = 0; break;
    }
    submit(server, request);
    if (request.status != (fault == 18 ? 409 : 400)) fprintf(stderr, "validation fault=%u status=%d\n", fault, request.status);
    assert(request.status == (fault == 18 ? 409 : 400));
    assert(cb.calls == 0 && server._room_mailbox->state() == mesh::RoomWebMailbox::State::Idle);
    server.tick(ticks); assert(cb.calls == 0);
  }
  for (unsigned fault = 0; fault < 5; ++fault) {
    Callbacks cb; WebConfigServer server(cb); AsyncWebServerRequest request; identity(request);
    if (fault == 0) request.headers.erase("X-Room-Token");
    if (fault == 1) request.headers["X-Room-Seq"].text = "0";
    if (fault == 2) request.headers["Origin"].text = "null";
    if (fault == 3) request.headers["X-Room-Seq"].text = "1.0";
    if (fault == 4) request.headers["X-Room-Seq"].text = "+-1";
    result(server, request); assert(request.status == 400 && cb.calls == 0);
  }
  Callbacks cb; WebConfigServer server(cb); AsyncWebServerRequest allowed;
  identity(allowed); allowed.headers.erase("Origin"); body(allowed, "{}"); submit(server, allowed);
  assert(allowed.status == 202 && cb.calls == 0); server.tick(ticks); assert(cb.calls == 1);
  for (const char* sequence : {"+1", " \t1\t ", "0001"}) {
    Callbacks canonical_cb; WebConfigServer canonical(canonical_cb); AsyncWebServerRequest request;
    identity(request, 10, sequence); request.headers["X-Room-Token"].text[63] = 'A'; body(request, "{}");
    submit(canonical, request); assert(request.status == 202); canonical.tick(ticks);
    assert(canonical_cb.calls == 1 && canonical_cb.last_sequence == 1 && canonical_cb.last_token[31] == 10);
  }
  puts("actual HTTP identity, origin, media type, and body validation passed");
}
static void deferredAndOwnership() {
  Callbacks cb; WebConfigServer server(cb); AsyncWebServerRequest post;
  identity(post); body(post, "{\"op\":\"post\",\"text\":\"secret\"}"); submit(server, post);
  assert(post.status == 202 && cb.calls == 0 && server._room_mailbox->state() == mesh::RoomWebMailbox::State::Pending);
  AsyncWebServerRequest wrong, wrong_seq, pending;
  identity(wrong, 2); result(server, wrong); assert(wrong.status == 404 && wrong.reply.find("secret") == String::npos);
  identity(wrong_seq, 1, "2"); result(server, wrong_seq); assert(wrong_seq.status == 404);
  identity(pending); result(server, pending); assert(pending.status == 202 && pending.reply.find("secret") == String::npos);
  cb.in_callback = [&](char* input, char* output) {
    assert(server._room_mailbox->state() == mesh::RoomWebMailbox::State::Running);
    const String before = input;
    const auto async_actor = [&] {
      AsyncWebServerRequest competing, duplicate, changed, running, stranger;
      identity(competing, 2); body(competing, "{\"op\":\"other\"}"); submit(server, competing); assert(competing.status == 429);
      identity(duplicate); body(duplicate, before); submit(server, duplicate); assert(duplicate.status == 202);
      identity(changed); body(changed, before + " "); submit(server, changed); assert(changed.status == 409);
      identity(running); result(server, running); assert(running.status == 202);
      identity(stranger, 2); result(server, stranger); assert(stranger.status == 404);
    };
    std::thread first(async_actor), second(async_actor);
    first.join(); second.join(); // Real concurrent HTTP handlers share the actual extracted lock.
    assert(String(input) == before && String(output) == "{\"owner_secret\":\"result-one\"}");
  };
  server.tick(ticks);
  assert(cb.calls == 1 && cb.last_sequence == 1 && cb.last_token[31] == 1);
  assert(cb.last_capacity == mesh::RoomWebMailbox::OUTPUT_CAPACITY && cb.last_body == "{\"op\":\"post\",\"text\":\"secret\"}");
  assert(server._room_mailbox->state() == mesh::RoomWebMailbox::State::Done);
  for (size_t i = 0; i < mesh::RoomWebMailbox::INPUT_CAPACITY; ++i) assert(server._room_mailbox->input()[i] == 0);
  AsyncWebServerRequest competitor; identity(competitor, 2); body(competitor, "{}"); submit(server, competitor); assert(competitor.status == 429);
  AsyncWebServerRequest done; identity(done); result(server, done); assert(done.status == 200);
  assert(done.reply == "{\"owner_secret\":\"result-one\"}" && done.response_headers["Cache-Control"] == "no-store");
  AsyncWebServerRequest retry; identity(retry); body(retry, cb.last_body); submit(server, retry); assert(retry.status == 202);
  server.tick(ticks + 1); assert(cb.calls == 1);
  AsyncWebServerRequest mutated; identity(mutated); body(mutated, "{\"changed\":true}"); submit(server, mutated); assert(mutated.status == 409);
  cb.in_callback = {}; body(competitor, "{}"); submit(server, competitor); assert(competitor.status == 202); server.tick(ticks + 2); assert(cb.calls == 2);
  AsyncWebServerRequest old; identity(old); body(old, cb.last_body); submit(server, old); assert(old.status == 409);
  AsyncWebServerRequest no_leak; identity(no_leak); result(server, no_leak); assert(no_leak.status == 404);
  puts("actual deferred callback, running input, owner isolation, and duplicate sequence checks passed");
}
static void boundsAndFloors() {
  static_assert(sizeof(mesh::RoomWebMailbox) <= 6656, "bounded room heap");
  static_assert(WebConfigServer::MAX_BODY + 1 == mesh::RoomWebMailbox::INPUT_CAPACITY, "body cap agrees with mailbox");
  Callbacks cb; WebConfigServer server(cb); AsyncWebServerRequest full;
  identity(full, 1, "4294967295"); body(full, String(WebConfigServer::MAX_BODY, 'x'));
  assert(static_cast<char*>(full._tempObject)[WebConfigServer::MAX_BODY] == 0);
  submit(server, full); assert(full.status == 202 && cb.calls == 0 && full._tempObject == nullptr);
  server.tick(ticks); assert(cb.last_body.size() == WebConfigServer::MAX_BODY);
  AsyncWebServerRequest full_result; identity(full_result, 1, "4294967295"); result(server, full_result); assert(full_result.status == 200);
  AsyncWebServerRequest wrapped; identity(wrapped, 1, "1"); body(wrapped, "{}"); submit(server, wrapped); assert(wrapped.status == 409);
  for (unsigned owner = 2; owner <= 8; ++owner) {
    AsyncWebServerRequest post, reply; identity(post, owner); body(post, "{}"); submit(server, post); assert(post.status == 202);
    server.tick(ticks); identity(reply, owner); result(server, reply); assert(reply.status == 200);
  }
  AsyncWebServerRequest ninth; identity(ninth, 9); body(ninth, "{}"); submit(server, ninth); assert(ninth.status == 429 && cb.calls == 8);
  ticks += mesh::RoomWebMailbox::OWNER_TTL_MS; body(ninth, "{}"); submit(server, ninth); assert(ninth.status == 202); server.tick(ticks); assert(cb.calls == 9);
  Callbacks ttl_cb; WebConfigServer ttl(ttl_cb); AsyncWebServerRequest first; identity(first); body(first, "{}"); submit(ttl, first); ttl.tick(ticks);
  AsyncWebServerRequest second; identity(second, 2); body(second, "{}"); ticks += mesh::RoomWebMailbox::RESULT_TTL_MS - 1; submit(ttl, second); assert(second.status == 429);
  ++ticks; body(second, "{}"); submit(ttl, second); assert(second.status == 202); ttl.tick(ticks); assert(ttl_cb.calls == 2);
  {
    Callbacks output_cb; WebConfigServer output(output_cb);
    output_cb.in_callback = [](char*, char* bytes) { memset(bytes, 'x', mesh::RoomWebMailbox::OUTPUT_CAPACITY); };
    AsyncWebServerRequest post, response; identity(post); body(post, "{}"); submit(output, post); output.tick(ticks);
    identity(response); result(output, response); assert(response.status == 200);
    assert(response.reply.size() == mesh::RoomWebMailbox::OUTPUT_CAPACITY - 1);
  }
  {
    ticks = UINT32_MAX - 100;
    Callbacks wrap_cb; WebConfigServer wrap(wrap_cb);
    AsyncWebServerRequest first, second; identity(first); body(first, "{}"); submit(wrap, first); wrap.tick(ticks);
    identity(second, 2); body(second, "{}"); ticks += mesh::RoomWebMailbox::RESULT_TTL_MS - 1;
    submit(wrap, second); assert(second.status == 429);
    ++ticks; body(second, "{}"); submit(wrap, second); assert(second.status == 202); wrap.tick(ticks);
    assert(wrap_cb.calls == 2);
  }
  puts("actual full-size body, owner capacity, sequence overflow, and result expiry checks passed");
}
static void pagesAndAvailability() {
  Callbacks cb; WebConfigServer server(cb); AsyncWebServerRequest page;
  server.host.invoke("/room", HTTP_GET, page); assert(page.status == 200);
  assert(page.reply.find("MeshCore Room") != String::npos && page.response_type == "text/html; charset=utf-8");
  assert(page.response_headers["Cache-Control"] == "no-store");
  assert(page.response_headers["X-Content-Type-Options"] == "nosniff" && page.response_headers["Referrer-Policy"] == "no-referrer");
  assert(cb.calls == 0);
  fail_page_allocation = true; server.host.invoke("/room", HTTP_GET, page); assert(page.status == 503); fail_page_allocation = false;
  cb.supported = false; server.host.invoke("/room", HTTP_GET, page); assert(page.status == 404); cb.supported = true;
  server._mode = WebConfigServer::MODE_OFF; server.host.invoke("/room", HTTP_GET, page); assert(page.status == 404);
  server._mode = WebConfigServer::MODE_LAN;
  Callbacks lazy_cb; WebConfigServer lazy(lazy_cb, false); AsyncWebServerRequest before; identity(before); body(before, "{}"); submit(lazy, before); assert(before.status == 503);
  lazy.tick(ticks); assert(lazy._room_mailbox && lazy_cb.calls == 0); submit(lazy, before); assert(before.status == 202); lazy.tick(ticks); assert(lazy_cb.calls == 1);
  puts("actual page headers, bounded allocation failure, and service availability checks passed");
}
static void lifecycle() {
  Callbacks cb; WebConfigServer server(cb); AsyncWebServerRequest pending; identity(pending); body(pending, "{}");
  pending.on_send = [&] {
    assert(server.handlerRefCount() == 1);
    server.requestStop(); server.tick(ticks);
    assert(server._stopping && server._room_mailbox && server.finalizations == 0);
    assert(cb.calls == 0);
  };
  submit(server, pending); assert(pending.status == 202 && server.host.ended);
  assert(server.handlerRefCount() == 0 && server._stopping && cb.calls == 0);
  AsyncWebServerRequest unavailable; identity(unavailable); result(server, unavailable); assert(unavailable.status == 503);
  server.tick(ticks); assert(!server._room_mailbox && !server._stopping && cb.stops == 1 && server.finalizations == 1);
  assert(cb.calls == 0); // Pending work is canceled, never executed after stop.
  Callbacks off_cb; WebConfigServer off(off_cb); AsyncWebServerRequest post; identity(post); body(post, "{}"); submit(off, post);
  off._mode = WebConfigServer::MODE_OFF; off.tick(ticks); assert(off_cb.calls == 0);
  body(post, "{}"); off.handleRoomPost(&post); assert(post.status == 503);
  AsyncWebServerRequest poll; identity(poll); off.handleRoomResult(&poll); assert(poll.status == 503);
  puts("actual stop reference retention, route detach, pending cancellation, and OFF gating passed");
}
static void responseFailure() {
  Callbacks cb; WebConfigServer server(cb); AsyncWebServerRequest post; identity(post); body(post, "{}"); submit(server, post); server.tick(ticks);
  AsyncWebServerRequest result_failure; identity(result_failure); result_failure.fail_response = true;
  result(server, result_failure); assert(result_failure.status == 503 && cb.calls == 1);
  AsyncWebServerRequest competing; identity(competing, 2); body(competing, "{}");
  submit(server, competing); assert(competing.status == 429); // OOM is not delivery and cannot release the result.
  AsyncWebServerRequest retry; identity(retry); result(server, retry); assert(retry.status == 200 && cb.calls == 1);
  puts("actual room result allocation failure remains retryable without reexecution passed");
}
static void authenticationFloors() {
  Callbacks cb; WebConfigServer server(cb); cb.authenticated = false;
  for (unsigned owner = 1; owner <= 12; ++owner) {
    AsyncWebServerRequest post, response;
    identity(post, owner); body(post, "{\"password\":\"wrong\"}"); submit(server, post); assert(post.status == 202);
    server.tick(ticks); identity(response, owner); result(server, response); assert(response.status == 200);
  }
  assert(cb.calls == 12); // Failed authentication cannot reserve all eight identities for twenty minutes.
  cb.authenticated = true;
  AsyncWebServerRequest good, first_result; identity(good, 99); body(good, "{}"); submit(server, good); assert(good.status == 202);
  server.tick(ticks); identity(first_result, 99); result(server, first_result); assert(first_result.status == 200);
  cb.authenticated = false;
  AsyncWebServerRequest failed, failed_result; identity(failed, 99, "2"); body(failed, "{\"password\":\"wrong\"}"); submit(server, failed); assert(failed.status == 202);
  server.tick(ticks); identity(failed_result, 99, "2"); result(server, failed_result); assert(failed_result.status == 200);
  cb.authenticated = true;
  AsyncWebServerRequest different, different_result; identity(different, 100); body(different, "{}"); submit(server, different); server.tick(ticks);
  identity(different_result, 100); result(server, different_result); assert(different_result.status == 200);
  AsyncWebServerRequest replay; identity(replay, 99); body(replay, "{}"); submit(server, replay); assert(replay.status == 409);
  assert(cb.calls == 15);
  puts("actual unauthenticated identities release capacity while authenticated replay floors remain passed");
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "validation")) validation();
  else if (!strcmp(argv[1], "ownership")) deferredAndOwnership();
  else if (!strcmp(argv[1], "bounds")) boundsAndFloors();
  else if (!strcmp(argv[1], "pages")) pagesAndAvailability();
  else if (!strcmp(argv[1], "lifecycle")) lifecycle();
  else if (!strcmp(argv[1], "allocation")) responseFailure();
  else if (!strcmp(argv[1], "authentication")) authenticationFloors();
  else assert(false);
}
'''


class RoomWebHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise AssertionError("a host C++ compiler is required")
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        header = (ROOT / "src/helpers/esp32/WebConfigServer.h").read_text()
        html = (ROOT / "src/helpers/esp32/RoomWebHtml.h").read_text()
        html_constant = re.search(r'static const char ROOM_WEB_HTML\[\] PROGMEM = R"roomhtml\([\s\S]*?\)roomhtml";', html)
        if html_constant is None:
            raise AssertionError("production room HTML constant not found")
        methods = [extract_braced(source, signature) for signature in (
            "void WebConfigServer::serviceRoom(", "static bool roomRequestIdentity(",
            "void WebConfigServer::handleRoomPage(", "void WebConfigServer::handleRoomPost(",
            "void WebConfigServer::handleRoomResult(", "void WebConfigServer::collectBody(",
            "void WebConfigServer::dispatchRequest(", "void WebConfigServer::detachRoutes(",
            "uint32_t WebConfigServer::handlerRefCount(", "void WebConfigServer::requestStop(",
            "void WebConfigServer::registerRoutes(")]
        tick = extract_braced(source, "void WebConfigServer::tick(")
        # Execute the exact stop/OFF guards and loop service calls; SDK Wi-Fi,
        # display and later batch handling are outside this HTTP/mailbox fixture.
        endpoint = tick.index("  serviceRoom(now);") + len("  serviceRoom(now);")
        methods.append(tick[:endpoint] + "\n}")
        routes = methods[-2]
        unrelated = sorted(set(re.findall(r"&WebConfigServer::(handle\w+)", routes))
                           - {"handleRoomPage", "handleRoomPost", "handleRoomResult"})
        stubs = "\n".join("void " + name + "(AsyncWebServerRequest* req) { req->send(418); }" for name in unrelated)
        capacity = re.search(r"static const size_t MAX_BODY = ([0-9]+);", header)
        if capacity is None:
            raise AssertionError("production MAX_BODY not found")
        generated = HARNESS.replace("@HTML@", html_constant.group()).replace("@LOCK@", extract_braced(source, "struct WCLock") + ";")
        generated = generated.replace("@MAX_BODY@", capacity[1]).replace("@UNRELATED_HANDLERS@", stubs).replace("@PRODUCTION@", "\n".join(methods))
        cls.directory = tempfile.TemporaryDirectory(prefix="room-web-http-")
        cls.addClassCleanup(cls.directory.cleanup)
        work = Path(cls.directory.name)
        (work / "test.cpp").write_text(generated)
        cls.binary = work / "http"
        command = [compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                   "-pthread", "-I" + str(ROOT / "src"), str(work / "test.cpp"), "-o", str(cls.binary)]
        if sys.platform.startswith("linux"):
            command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if compiled.returncode != 0:
            raise AssertionError(compiled.stdout + compiled.stderr)

    def run_case(self, name, expected):
        checked = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=15)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn(expected, checked.stdout)

    def test_request_validation_never_runs_mutating_callback(self):
        self.run_case("validation", "actual HTTP identity, origin, media type, and body validation passed")

    def test_loop_only_mutation_running_ownership_and_duplicate_retries(self):
        self.run_case("ownership", "actual deferred callback, running input, owner isolation, and duplicate sequence checks passed")

    def test_full_body_bounds_owner_capacity_and_expiry(self):
        self.run_case("bounds", "actual full-size body, owner capacity, sequence overflow, and result expiry checks passed")

    def test_page_headers_allocation_and_lazy_service_start(self):
        self.run_case("pages", "actual page headers, bounded allocation failure, and service availability checks passed")

    def test_stop_lifecycle_retains_requests_and_never_executes_off(self):
        self.run_case("lifecycle", "actual stop reference retention, route detach, pending cancellation, and OFF gating passed")

    def test_result_allocation_failure_does_not_reexecute(self):
        self.run_case("allocation", "actual room result allocation failure remains retryable without reexecution passed")

    def test_failed_authentication_cannot_hold_owner_capacity_or_erase_good_floors(self):
        self.run_case("authentication", "actual unauthenticated identities release capacity while authenticated replay floors remain passed")

    def test_actual_route_registration_and_loop_order_are_not_shadowed(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        routes = extract_braced(source, "void WebConfigServer::registerRoutes(")
        self.assertLess(routes.index('_server->on("/api/room/result"'), routes.index('_server->on("/api/room"'))
        self.assertIn('dispatchRequest(r, &WebConfigServer::handleRoomResult)', routes)
        self.assertIn('dispatchRequest(r, &WebConfigServer::handleRoomPost)', routes)
        tick = extract_braced(source, "void WebConfigServer::tick(")
        self.assertLess(tick.index("if (_stopping)"), tick.index("serviceRoom(now)"))
        self.assertLess(tick.index("if (_mode == MODE_OFF)"), tick.index("serviceRoom(now)"))
        teardown = extract_braced(source, "void WebConfigServer::finalizeTeardown(")
        self.assertIn("delete _room_mailbox;", teardown)
        self.assertIn("_room_mailbox = nullptr;", teardown)


if __name__ == "__main__":
    unittest.main()
