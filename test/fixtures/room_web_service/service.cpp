#include "filesystem.h"
#include <cstdlib>
#include <new>
#include <array>
#include <ArduinoJson.h>
static bool deny_json_allocation = false;
static unsigned json_allocations = 0;
static void* trackedMalloc(size_t size) {
  if (deny_json_allocation) return nullptr;
  void* result = std::malloc(size); if (result) ++json_allocations; return result;
}
static void trackedFree(void* ptr) {
  if (ptr) { assert(json_allocations != 0); --json_allocations; } std::free(ptr);
}
static void* trackedRealloc(void* ptr, size_t size) {
  if (deny_json_allocation) return nullptr;
  return std::realloc(ptr, size);
}
#define malloc trackedMalloc
#define free trackedFree
#define realloc trackedRealloc
#include <helpers/RoomWebJson.h>
#undef malloc
#undef free
#undef realloc
#include <helpers/RoomBoardStore.h>
#include <helpers/RoomHistoryStore.h>
#include <helpers/RoomAccessPolicy.h>
#include <helpers/RoomLoginAuthorization.h>
#define WITH_WEBCONFIG 1
#define PUB_KEY_SIZE 32
#define MAX_POST_TEXT_LEN 151
#define MAX_UNSYNCED_POSTS 32
#define POST_SYNC_DELAY_SECS 2
#define PUSH_NOTIFY_DELAY_MILLIS 200
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#include "roles.inc"
static uint32_t ticks = 100;
static uint32_t millis() { return ticks; }
static uint32_t futureMillis(uint32_t duration) { return millis() + duration; }
namespace mesh {
struct Identity {
  uint8_t pub_key[32] = {};
  bool matches(const Identity& other) const { return memcmp(pub_key, other.pub_key, 32) == 0; }
};
struct Utils {
  // Cryptography is a boundary mock: every fixture token has a distinct key.
  static void sha256(uint8_t* out, size_t length, const uint8_t* token, size_t token_length,
                     const uint8_t* server, size_t server_length) {
    assert(length == 32 && token_length == 32 && server_length == 32);
    for (size_t i = 0; i < length; ++i) out[i] = token[i] ^ server[i] ^ 0x5a;
  }
  static void toHex(char* output, const uint8_t* key, size_t length) {
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < length; ++i) { output[2 * i] = digits[key[i] >> 4]; output[2 * i + 1] = digits[key[i] & 15]; }
    output[length * 2] = 0;
  }
};
}
struct StrHelper {
  static void strncpy(char* out, const char* text, size_t capacity) { std::snprintf(out, capacity, "%s", text); }
};
struct Preferences {
  static bool value, fail_open, fail_write;
  bool begin(const char* name, bool readonly) { assert(strcmp(name, "mesh-room") == 0 && !readonly); return !fail_open; }
  size_t putBool(const char* key, bool next) { assert(strcmp(key, "web") == 0); if (fail_write) return 0; value = next; return sizeof(bool); }
  bool getBool(const char* key, bool fallback) { (void)fallback; assert(strcmp(key, "web") == 0); return value; }
  void end() {}
};
bool Preferences::value = false, Preferences::fail_open = false, Preferences::fail_write = false;
struct ClientInfo {
  mesh::Identity id;
  uint8_t permissions = PERM_ACL_ADMIN;
  struct { struct { uint16_t post_quota_used = 0, poll_quota_used = 0; uint32_t last_post_timestamp = 0; } room; } extra;
};
struct ACL {
  ClientInfo radio_admin;
  unsigned lookups = 0;
  int getNumClients() const { return 1; }
  ClientInfo* getClientByIdx(int i) { assert(i == 0); return &radio_admin; }
  ClientInfo* getClient(const mesh::Identity&) { ++lookups; return &radio_admin; }
};
struct Clock { uint32_t now = 1000; uint32_t getCurrentTimeUnique() { return now++; } };
#include "post.inc"
struct MyMesh {
  MemoryFS storage; MemoryFS* _fs = &storage;
  mesh::RoomAccessPolicy room_access;
  mesh::Identity self_id;
  struct { char password[65] = "admin-secret", guest_password[65] = "write-secret", node_name[64] = "Test room"; bool allow_read_only = true; } _prefs;
  bool room_web_enabled = false;
  bool room_web_request_authenticated = false;
  uint32_t room_web_boot_id = 71;
#include "web_user.inc"
  ACL acl;
  Clock clock;
  PostInfo posts[MAX_UNSYNCED_POSTS] = {};
  int next_post_idx = 0;
  uint32_t last_room_timestamp = 0, post_ready_at[MAX_UNSYNCED_POSTS] = {}, post_ready_mask = UINT32_MAX, next_push = 0;
  uint16_t _num_posted = 0;
  mesh::RoomHistoryState room_history_state;
  bool room_history_enabled = false, room_history_available = true;
  char room_topic[152] = "Topic <img src=x onerror=attack()>";
  MyMesh() { self_id.pub_key[0] = 4; metadata_filesystem = &storage; assert(room_access.load(_fs)); }
  Clock* getRTCClock() { return &clock; }
  bool handleRoomWebCommand(const char*, char*);
  void processRoomRequest(const uint8_t*, uint32_t, char*, char*, size_t);
  void serviceRoomQuotas();
  bool storePost(const mesh::Identity&, const char*);
  bool snapshotRoomHistory();
};
#include "production.inc"

static std::array<uint8_t, 32> token(uint8_t id = 1) { std::array<uint8_t, 32> value = {}; value[0] = id; return value; }
static std::string execute(MyMesh& m, const std::string& input, uint32_t sequence,
                           uint8_t owner = 1, size_t capacity = 2048) {
  metadata_filesystem = &m.storage;
  std::vector<char> request(input.begin(), input.end()); request.push_back(0);
  std::vector<char> reply(capacity + 2, 0x7f);
  const auto key = token(owner);
  m.processRoomRequest(key.data(), sequence, request.data(), reply.data() + 1, capacity);
  assert(reply.front() == 0x7f && reply.back() == 0x7f && json_allocations == 0);
  if (!capacity) return "";
  assert(memchr(reply.data() + 1, 0, capacity)); return reply.data() + 1;
}
static JsonDocument result(MyMesh& m, const std::string& input, uint32_t sequence, uint8_t owner = 1) {
  const auto text = execute(m, input, sequence, owner);
  JsonDocument json; assert(deserializeJson(json, text) == DeserializationError::Ok); return json;
}
static std::string request(const char* op, const char* password = "write-secret", const std::string& fields = "") {
  return std::string("{\"op\":\"") + op + "\",\"password\":\"" + password + "\"" + fields + "}";
}
static void error(MyMesh& m, const std::string& input, uint32_t sequence, const char* fragment, uint8_t owner = 1) {
  const auto json = result(m, input, sequence, owner); const char* text = json["error"];
  if (!text || !strstr(text, fragment)) fprintf(stderr, "Expected %s, got %s for %.120s\n", fragment, text ? text : "no error", input.c_str());
  assert(text && strstr(text, fragment));
}
static std::string encoded(const std::string& body) {
  std::vector<char> out(4 * ((body.size() + 2) / 3) + 1);
  mesh::roomEncodeBase64(reinterpret_cast<const uint8_t*>(body.data()), body.size(), out.data()); return out.data();
}
static std::string saveRequest(uint8_t id, uint32_t version, const std::string& title, const std::string& body, const char* password = "admin-secret") {
  return request("board.save", password, ",\"boot\":71,\"id\":" + std::to_string(id) + ",\"version\":" + std::to_string(version)
      + ",\"title\":\"" + title + "\",\"body64\":\"" + encoded(body) + "\"");
}
static void enable(MyMesh& m) { char reply[160]; assert(m.handleRoomWebCommand("set room.web on", reply)); assert(m.room_web_enabled); }
static unsigned floors(const MyMesh& m) { unsigned total = 0; for (const auto& user : m.room_web_users) total += user.last_sequence; return total; }
int main() {
  unsigned scenarios = 0;
  {
    MyMesh m; assert(!m.room_web_enabled);
    error(m, request("status"), 1, "disabled"); assert(floors(m) == 0);
    char reply[160]; assert(m.handleRoomWebCommand("get room.web", reply) && strstr(reply, "off"));
    Preferences::fail_write = true; assert(m.handleRoomWebCommand("set room.web on", reply) && !m.room_web_enabled);
    Preferences::fail_write = false; enable(m);
    assert(!m.handleRoomWebCommand("get room.webx", reply));
    auto status = result(m, request("status"), 1); assert(status["role"] == PERM_ACL_READ_WRITE && status["boot"] == 71);
    assert(strcmp(status["topic"], m.room_topic) == 0 && status["persistent_history"] == false);
    status = result(m, request("status", "bad-password", ",\"key\":\"radio-admin\",\"permissions\":3"), 2);
    assert(status["role"] == PERM_ACL_GUEST && m.acl.lookups == 0);
    const std::string web_key = status["web_key"].as<std::string>(); assert(web_key.size() == 64);
    error(m, request("post", "bad-password", ",\"boot\":71,\"name\":\"A\",\"text\":\"denied\""), 500, "read-only");
    assert(floors(m) == 2 && m.last_room_timestamp == 0 && m._num_posted == 0);
    assert(result(m, request("status", "admin-secret"), 3)["role"] == PERM_ACL_ADMIN); ++scenarios;
  }
  {
    MyMesh m; enable(m); m._prefs.allow_read_only = false;
    error(m, request("status", "wrong"), 100, "incorrect"); assert(floors(m) == 0 && !m.room_web_request_authenticated);
    error(m, request("status"), 100, "invalid browser", 0); assert(floors(m) == 0 && !m.room_web_request_authenticated);
    assert(result(m, request("status"), 1)["role"] == PERM_ACL_READ_WRITE);
    error(m, request("post", "write-secret", ",\"boot\":70,\"name\":\"A\",\"text\":\"no\""), 200, "restarted");
    assert(floors(m) == 1 && m._num_posted == 0);
    error(m, saveRequest(1, 0, "Title", "body", "write-secret"), 300, "admin"); assert(floors(m) == 1);
    const auto okay = result(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A\",\"text\":\"one\""), 2);
    assert(okay["ok"] == true && m.last_room_timestamp == 1000 && m._num_posted == 1);
    error(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A\",\"text\":\"one\""), 2, "completed");
    assert(m._num_posted == 1 && m.acl.radio_admin.extra.room.last_post_timestamp == 0);
    auto post = result(m, request("posts", "write-secret", ",\"after\":0"), 3);
    assert(strcmp(post["post"]["text"], "A: one") == 0 && post["post"]["timestamp"] == 1000);
    post = result(m, request("posts", "write-secret", ",\"after\":1000"), 4); assert(post["post"].isNull()); ++scenarios;
  }
  {
    MyMesh m; enable(m); m.room_history_enabled = true;
    const std::string text(148, 'x');
    assert(result(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A\",\"text\":\"" + text + "\""), 1)["ok"] == true);
    assert(strlen(m.posts[0].text) == 151 && m.room_history_state.count == 1);
    error(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A\",\"text\":\"" + text + "x\""), 2, "151");
    assert(m._num_posted == 1 && m.last_room_timestamp == 1000);
    error(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A:B\",\"text\":\"x\""), 3, "display name");
    m.storage.capacity = 0;
    error(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A\",\"text\":\"retain\""), 4, "not retained");
    assert(m._num_posted == 1 && m.last_room_timestamp == 1000 && m.room_history_state.count == 1);
    m.storage.reset(); m.clock.now = 2;
    assert(result(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A\",\"text\":\"retry with new sequence\""), 5)["timestamp"] == 1001);
    assert(m._num_posted == 2 && m.room_history_state.count == 2); ++scenarios;
  }
  {
    MyMesh m; enable(m); assert(m.room_access.setRates(m._fs, 1, 2));
    assert(result(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A\",\"text\":\"one\""), 1)["ok"] == true);
    error(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A\",\"text\":\"two\""), 2, "rate limit");
    assert(m._num_posted == 1 && m.last_room_timestamp == 1000);
    result(m, request("status"), 3); result(m, request("status"), 4); error(m, request("status"), 5, "rate limit");
    ticks += 60000; result(m, request("status"), 6);
    const auto key = token(); uint8_t author[32]; mesh::Utils::sha256(author, 32, key.data(), 32, m.self_id.pub_key, 32);
    assert(m.room_access.addBan(m._fs, author) == mesh::RoomAccessPolicy::BanResult::Saved);
    const unsigned floor = floors(m); error(m, request("status"), 1000, "denied"); assert(floors(m) == floor);
    assert(m.room_access.removeBan(m._fs, author) == mesh::RoomAccessPolicy::BanResult::Saved);
    result(m, request("status"), 7); ++scenarios;
  }
  {
    MyMesh m; enable(m); const std::string title = u8"Café 地図 📡"; const std::string body(2048, 'b');
    const auto saved = result(m, saveRequest(1, 0, title, body), 1);
    if (saved["ok"] != true) fprintf(stderr, "Full board save error: %s\n", saved["error"].as<const char*>());
    assert(saved["ok"] == true);
    assert(saved["version"] == 1);
    auto page = result(m, request("board.index", "", ",\"revision\":0,\"cursor\":0"), 2);
    assert(page["revision"] == 1 && page["articles"].size() == 1 && page["next"] == 255);
    assert(page["articles"][0]["length"] == 2048 && strcmp(page["articles"][0]["title"], title.c_str()) == 0);
    std::string assembled;
    for (unsigned offset = 0, sequence = 3; offset < 2048; ++sequence) {
      auto chunk = result(m, request("board.read", "", ",\"id\":1,\"version\":1,\"offset\":" + std::to_string(offset)), sequence);
      assert(chunk["version"] == 1 && chunk["offset"] == offset && chunk["total"] == 2048);
      uint8_t bytes[128]; size_t length; assert(mesh::roomDecodeBase64(chunk["data64"], bytes, sizeof(bytes), length) && length > 0 && length <= 128);
      assembled.append(reinterpret_cast<const char*>(bytes), length); offset += length; assert(chunk["next"] == offset);
    }
    assert(assembled == body);
    const auto updated = result(m, saveRequest(1, 1, "Updated", "new body"), 30);
    assert(updated["ok"] == true && updated["version"] == 2);
    error(m, request("board.index", "", ",\"revision\":1,\"cursor\":0"), 31, "board changed");
    error(m, request("board.read", "", ",\"id\":1,\"version\":1,\"offset\":128"), 32, "article changed");
    error(m, saveRequest(1, 1, "Stale", "old"), 33, "article changed");
    error(m, request("board.delete", "admin-secret", ",\"boot\":71,\"id\":1,\"version\":0"), 34, "current article version");
    assert(result(m, request("board.delete", "admin-secret", ",\"boot\":71,\"id\":1,\"version\":2"), 35)["ok"] == true);
    assert(result(m, saveRequest(1, 0, "Reused", "new"), 36)["ok"] == true);
    error(m, request("board.read", "", ",\"id\":1,\"version\":2,\"offset\":0"), 37, "article changed"); ++scenarios;
  }
  {
    MyMesh m; enable(m); uint32_t sequence = 1;
    for (const std::string& input : {std::string("{"), std::string("[]"), std::string("null"), std::string("{\"op\":{\"a\":{\"b\":{\"c\":{\"d\":1}}}}}"),
        std::string("{\"op\":\"status\",\"password\":\"write-secret\\u0000ignored\"}"), std::string("{\"op\":\"status\\u0000ignored\",\"password\":\"write-secret\"}"),
        std::string("{\"op\":3,\"password\":[]}")}) {
      const auto json = result(m, input, sequence++); assert(json["error"].is<const char*>() && floors(m) == 0);
    }
    error(m, std::string(4097, ' '), sequence++, "invalid request");
    for (const std::string text : {"A===", "====", "Zg=A", "Zg==Zg==", "Zh==", "Zm9=", "*m9v", "Zg", "Zg== "}) {
      error(m, request("board.save", "admin-secret", ",\"boot\":71,\"id\":1,\"version\":0,\"title\":\"T\",\"body64\":\"" + text + "\""), sequence++, "encoding");
    }
    error(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A\",\"text\":\"before\\u0000after\""), sequence++, "name and message");
    error(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A\\u0000B\",\"text\":\"body\""), sequence++, "name and message");
    error(m, request("board.save", "admin-secret", ",\"boot\":71,\"id\":1,\"version\":0,\"title\":\"T\\u0000X\",\"body64\":\"Zg==\""), sequence++, "article changed");
    error(m, request("board.save", "admin-secret", ",\"boot\":71,\"id\":1,\"version\":0,\"title\":\"T\",\"body64\":\"Zg==\\u0000Zg==\""), sequence++, "article changed");
    assert(m._num_posted == 0 && m.last_room_timestamp == 0);
    error(m, saveRequest(1, 0, std::string(64, 't'), "body"), sequence++, "article changed");
    error(m, saveRequest(1, 0, "Title", std::string(2049, 'b')), sequence++, "encoding");
    assert(!m.storage.files.count(mesh::ROOM_BOARD_PRIMARY_PATH));
    deny_json_allocation = true; error(m, request("status"), sequence++, "invalid JSON"); deny_json_allocation = false;
    result(m, request("status"), sequence++);
    // Excess unknown array members exhaust the actual bounded allocator.
    std::string huge = "{\"op\":\"status\",\"password\":\"write-secret\",\"extra\":[";
    for (unsigned i = 0; i < 1000; ++i) { if (i) huge += ','; huge += '0'; } huge += "]}";
    error(m, huge, sequence++, "invalid JSON"); assert(json_allocations == 0); ++scenarios;
  }
  {
    MyMesh m; enable(m);
    for (uint8_t owner = 1; owner <= 8; ++owner) result(m, request("status"), 1, owner);
    error(m, request("status"), 1, "busy", 9); ticks += 1200000;
    result(m, request("status"), 1, 9);
    // Every small response capacity stays within its provided buffer.
    for (size_t capacity = 0; capacity < 200; ++capacity) execute(m, request("status"), uint32_t(capacity + 2), 9, capacity);
    for (unsigned i = 0; i < 40; ++i) assert(result(m, request("post", "write-secret", ",\"boot\":71,\"name\":\"A\",\"text\":\"bounded ring\""), 300 + i, 9)["ok"] == true);
    assert(m._num_posted == 40 && m.next_post_idx == 8 && m.last_room_timestamp == 1039); ++scenarios;
  }
  // Deliver concrete backend responses for the browser boundary fixture.
  MyMesh boundary; enable(boundary);
  printf("BOUNDARY_STATUS %s\n", execute(boundary, request("status"), 1).c_str());
  assert(result(boundary, saveRequest(1, 0, "Instructions", "Read only this article"), 2)["ok"] == true);
  printf("BOUNDARY_INDEX %s\n", execute(boundary, request("board.index", "write-secret", ",\"revision\":0,\"cursor\":0"), 3).c_str());
  printf("BOUNDARY_READ %s\n", execute(boundary, request("board.read", "write-secret", ",\"id\":1,\"version\":1,\"offset\":0"), 4).c_str());
  printf("PASS: %u actual room web service authorization, storage, quota, JSON and boundary scenarios\n", scenarios);
}
