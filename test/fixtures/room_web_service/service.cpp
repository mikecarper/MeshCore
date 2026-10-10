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
#include <helpers/ClientPathPersistence.h>
#include <helpers/ClientPathObservation.h>
#include <helpers/RoomClientPathCommand.h>
#include <helpers/LazyPersistence.h>
#include <helpers/RoomTopicStore.h>
#include <helpers/RoomCatchUp.h>
#define WITH_WEBCONFIG 1
#define PUB_KEY_SIZE 32
#define MAX_PATH_SIZE 64
#define MAX_HASH_SIZE 8
#define MESH_CLIENT_REPEATER_ONLY 0
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
struct Packet { static bool isValidPathLen(uint8_t length); };
struct Identity {
  uint8_t pub_key[32];
  Identity() = default;
  explicit Identity(const uint8_t* key) { memcpy(pub_key, key, PUB_KEY_SIZE); }
  bool matches(const Identity& other) const { return memcmp(pub_key, other.pub_key, 32) == 0; }
};
struct LocalIdentity : Identity {
  void calcSharedSecret(uint8_t* output, const uint8_t* key) const {
    for (size_t i = 0; i < PUB_KEY_SIZE; ++i) output[i] = key[i] ^ pub_key[i];
  }
};
struct Utils {
  static bool isHexChar(char);
  static bool fromHex(uint8_t*, int, const char*);
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
  static void sha256(uint8_t* output, size_t size, const uint8_t* bytes, size_t length) {
    memset(output, 0, size);
    for (size_t i = 0; i < length; ++i) output[i % size] ^= bytes[i];
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
#include "client.inc"
struct ACL {
  ClientInfo clients[8]{};
  ClientInfo durable[8]{};
  bool retained[8]{};
  ClientInfo& radio_admin = clients[0];
  unsigned lookups = 0, saves = 0;
  int count = 1;
  int& num_clients = count;
  static constexpr int capacity = 8;
  bool acl_load_complete = true, protect_explicit_permissions = true;
  bool save_ok = true;
  int getNumClients() const { return count; }
  ClientInfo* getClientByIdx(int i) { assert(i >= 0 && i < count); return &clients[i]; }
  ClientInfo* getClient(const uint8_t* key, int length) {
    ++lookups; assert(length == PUB_KEY_SIZE);
    for (int i = 0; i < count; ++i) {
      if (memcmp(clients[i].id.pub_key, key, PUB_KEY_SIZE) == 0) return &clients[i];
    }
    return nullptr;
  }
  bool save(MemoryFS* fs, bool (*filter)(ClientInfo*)) {
    assert(fs && filter); ++saves;
    if (!save_ok) return false;
    for (int i = 0; i < count; ++i) {
      retained[i] = (clients[i].permissions & PERM_ACL_ROLE_MASK) != PERM_ACL_GUEST && filter(&clients[i]);
      if (retained[i]) durable[i] = clients[i];
    }
    return true;
  }
  ClientInfo* putClient(const mesh::Identity& id, uint8_t permissions);
  bool applyPermissions(const mesh::LocalIdentity& self, const uint8_t* key, int length, uint8_t permissions);
};
struct Clock {
  uint32_t now = 1000;
  uint32_t getCurrentTime() const { return now; }
  uint32_t getCurrentTimeUnique() { return now++; }
};
#include "post.inc"
struct MyMesh {
  MemoryFS storage; MemoryFS* _fs = &storage;
  mesh::RoomAccessPolicy room_access;
  mesh::LocalIdentity self_id{};
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
  unsigned long dirty_contacts_expiry = 0;
  uint8_t contacts_save_failures = 0;
  uint32_t room_topic_revision = 0, room_topic_timestamp = 0, room_topic_ready_at = 0;
  bool room_topic_ready = false;
  struct { unsigned clears = 0; void clear() { ++clears; } } recent_room_posts;
  unsigned retry_cancellations = 0;
  uint32_t cancelled_retry_timestamp = 0;
  MyMesh() {
    self_id.pub_key[0] = 4; metadata_filesystem = &storage; assert(room_access.load(_fs));
    acl.radio_admin.permissions = PERM_ACL_ADMIN;
    for (auto& client : acl.clients) {
      client.out_path_len = client.alt_path_len = client.observed_path_len = OUT_PATH_UNKNOWN;
      client.out_path_is_persistable = true;
    }
  }
  Clock* getRTCClock() { return &clock; }
  bool handleRoomWebCommand(const char*, char*);
  void processRoomRequest(const uint8_t*, uint32_t, char*, char*, size_t);
  void serviceRoomQuotas();
  bool storePost(const mesh::Identity&, const char*);
  bool snapshotRoomHistory();
  bool executeClientPathCommand(ClientInfo*, mesh::RoomClientPathCommand, const char*, char*);
  bool setRoomClientPath(ClientInfo*, const char*, const char*, char*);
  bool applyRoomCatchUpCommand(ClientInfo*, const char*, uint32_t, char*);
  static bool saveFilter(ClientInfo*);
  uint8_t getUnsyncedCount(ClientInfo*);
  bool handleRoomHistoryCommand(const char*, char*);
  bool handleRoomTopicCommand(const char*, char*);
  void activateRoomTopic();
  void writeRoomClientJson(mesh::RoomJsonWriter&, ClientInfo*, bool);
  void cancelActiveMessageRetries(const uint8_t*, uint32_t timestamp) {
    ++retry_cancellations; cancelled_retry_timestamp = timestamp;
  }
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
static std::string clientKey(const ClientInfo& client) {
  char key[65]; mesh::Utils::toHex(key, client.id.pub_key, PUB_KEY_SIZE); return key;
}
static void seedClients(MyMesh& m, unsigned count = 5) {
  assert(count <= 8); m.acl.count = count;
  for (unsigned i = 0; i < count; ++i) {
    auto& client = m.acl.clients[i];
    memset(client.id.pub_key, i + 1, PUB_KEY_SIZE);
    client.permissions = i == 0 ? PERM_ACL_ADMIN : PERM_ACL_READ_WRITE;
    client.permissions_are_explicit = i < 2;
    client.last_activity = 900 + i;
    client.out_path_len = 1; client.out_path[0] = 0xA1 + i;
    client.out_path_is_persistable = true;
    client.alt_path_len = OUT_PATH_UNKNOWN;
    client.extra.room.sync_since = 100 + i;
  }
}
static void adminAuthorizationCases() {
  const char* operations[] = {"admin.users", "admin.user", "admin.route", "admin.catchup",
      "admin.access", "admin.ban", "admin.settings", "admin.topic", "admin.history", "admin.rates"};
  unsigned denied = 0;
  for (const char* password : {"", "wrong-password", "write-secret"}) {
    for (const char* operation : operations) {
      MyMesh m; enable(m); seedClients(m);
      const std::vector<ClientInfo> before(m.acl.clients, m.acl.clients + m.acl.count);
      const unsigned writes = m.storage.write_opens;
      const auto reply = result(m, request(operation, password,
          ",\"boot\":71,\"cursor\":0,\"key\":\"" + clientKey(m.acl.clients[1])
          + "\",\"which\":\"outpath\",\"value\":\"B1\",\"expected\":\"> A2\","
            "\"mode\":\"keep\",\"count\":1,\"expected_sync\":101,"
            "\"role\":3,\"expected_role\":2,\"banned\":true"), 999);
      assert(reply["error"].is<const char*>() && strstr(reply["error"].as<const char*>(), "admin"));
      assert(reply.as<JsonObjectConst>().size() == 1);
      assert(floors(m) == 0 && m.acl.lookups == 0 && m.acl.saves == 0);
      assert(m.storage.write_opens == writes && m.room_access.banCount() == 0);
      assert(memcmp(before.data(), m.acl.clients, before.size() * sizeof(ClientInfo)) == 0);
      assert(m._num_posted == 0 && m.last_room_timestamp == 0);
      ++denied;
    }
  }
  MyMesh closed; enable(closed); closed._prefs.allow_read_only = false;
  error(closed, request("admin.users", "wrong-password", ",\"cursor\":0"), 100, "incorrect");
  assert(floors(closed) == 0 && !closed.room_web_request_authenticated && closed.acl.lookups == 0);
  MyMesh empty; enable(empty); empty._prefs.password[0] = 0;
  error(empty, request("admin.users", "admin-secret", ",\"cursor\":0"), 100, "admin");
  assert(floors(empty) == 0 && empty.acl.lookups == 0);
  printf("ADMIN_AUTH: %u rejected low-trust operations leave state and identity data unchanged\n", denied + 2);
}
static std::string adminClientRequest(const char* operation, const ClientInfo& client,
                                       const std::string& fields = "", uint32_t boot = 71) {
  return request(operation, "admin-secret", ",\"boot\":" + std::to_string(boot)
      + ",\"key\":\"" + clientKey(client) + "\"" + fields);
}
static std::string routeFields(const char* which, const char* value, const char* expected) {
  if (strncmp(expected, "> ", 2) == 0) expected += 2;
  return std::string(",\"which\":\"") + which + "\",\"value\":\"" + value
      + "\",\"expected\":\"" + expected + "\"";
}
static void adminRouteCases() {
  MyMesh m; enable(m); seedClients(m);
  auto& retained = m.acl.clients[1];
  const ClientInfo other = m.acl.clients[0];
  uint32_t sequence = 1;
  error(m, adminClientRequest("admin.route", retained,
      routeFields("outpath", "B1", "> A2"), 70), sequence++, "restarted");
  assert(m.acl.saves == 0 && floors(m) == 0);
  assert(result(m, adminClientRequest("admin.route", retained,
      routeFields("outpath", "B1", "> A2")), sequence++)["ok"] == true);
  assert(retained.out_path_len == 1 && retained.out_path[0] == 0xB1 && m.acl.saves == 1);
  assert(m.acl.retained[1] && !m.acl.retained[2]);
  assert(memcmp(&other, &m.acl.clients[0], sizeof(other)) == 0);
  assert(retained.permissions == PERM_ACL_READ_WRITE && retained.permissions_are_explicit);
  assert(result(m, adminClientRequest("admin.route", retained,
      routeFields("outpath", "B1", "> B1")), sequence++)["ok"] == true);
  assert(m.acl.saves == 1);
  const ClientInfo selected = retained;
  const auto stale = result(m, adminClientRequest("admin.route", retained,
      routeFields("outpath", "C1", "> A2")), sequence++);
  assert(stale["error"].is<const char*>() && m.acl.saves == 1);
  assert(memcmp(&retained, &selected, sizeof(selected)) == 0);
  for (const char* value : {"B1,", "B1,,B2", "B1,1234", "direct;erase", "get prv.key", "path"}) {
    const auto invalid = result(m, adminClientRequest("admin.route", retained,
        routeFields("outpath", value, "> B1")), sequence++);
    assert(invalid["error"].is<const char*>());
    assert(memcmp(&retained, &selected, sizeof(selected)) == 0 && m.acl.saves == 1);
  }
  auto& transient = m.acl.clients[2];
  assert(result(m, adminClientRequest("admin.route", transient,
      routeFields("altpath", "C1,C2", "> unknown")), sequence++)["ok"] == true);
  assert(transient.alt_path_len == 2 && transient.alt_path[0] == 0xC1 && m.acl.saves == 1);
  assert(transient.permissions == PERM_ACL_READ_WRITE && !transient.permissions_are_explicit);
  m.acl.save_ok = false;
  m.dirty_contacts_expiry = 50000; m.contacts_save_failures = 3;
  error(m, adminClientRequest("admin.route", retained,
      routeFields("altpath", "D1", "> unknown")), sequence++, "save failed");
  assert(m.acl.saves == 2 && memcmp(&retained, &selected, sizeof(selected)) == 0);
  assert(m.dirty_contacts_expiry == 50000 && m.contacts_save_failures == 3);
  m.acl.save_ok = true;
  const std::string key = clientKey(retained);
  for (const std::string& invalid_key : {key.substr(0, 8), key.substr(0, 12), std::string(64, '0'), std::string(64, 'g')}) {
    const auto denied = result(m, request("admin.route", "admin-secret",
        ",\"boot\":71,\"key\":\"" + invalid_key + "\"" + routeFields("outpath", "D1", "> B1")), sequence++);
    assert(denied["error"].is<const char*>() && m.acl.saves == 2);
    assert(memcmp(&retained, &selected, sizeof(selected)) == 0);
  }
  puts("ADMIN_ROUTES: exact identities, stale guards, filtered saves, no-op writes, transient roles and rollback passed");
}
static void unreadPosts(MyMesh& m, const ClientInfo& client) {
  m.posts[0].post_timestamp = 150; m.posts[1].post_timestamp = 200;
  m.posts[2].post_timestamp = 250; m.posts[3].post_timestamp = 300;
  for (unsigned i = 0; i < 4; ++i) memset(m.posts[i].author.pub_key, 0x99, PUB_KEY_SIZE);
  m.posts[4].post_timestamp = 1000; m.posts[4].author = client.id;
  m.last_room_timestamp = 1000;
}
static void adminCatchUpCases() {
  MyMesh m; enable(m); seedClients(m);
  auto& client = m.acl.clients[1]; unreadPosts(m, client);
  client.extra.room.pending_ack = 0x12345678;
  client.extra.room.push_post_timestamp = 150;
  client.extra.room.ack_timeout = 9999;
  client.extra.room.push_failures = 2;
  const ClientInfo other = m.acl.clients[0];
  uint32_t sequence = 1;
  assert(result(m, adminClientRequest("admin.catchup", client,
      ",\"mode\":\"keep\",\"count\":2,\"expected_sync\":101"), sequence++)["ok"] == true);
  assert(client.extra.room.sync_since == 200 && client.extra.room.pending_ack == 0);
  assert(client.extra.room.push_post_timestamp == 0 && client.extra.room.ack_timeout == 0);
  assert(client.extra.room.push_failures == 0 && m.acl.saves == 1);
  assert(m.retry_cancellations == 1 && m.cancelled_retry_timestamp == 150);
  assert(memcmp(&other, &m.acl.clients[0], sizeof(other)) == 0);
  const ClientInfo advanced = client;
  const auto stale = result(m, adminClientRequest("admin.catchup", client,
      ",\"mode\":\"keep\",\"count\":0,\"expected_sync\":101"), sequence++);
  assert(stale["error"].is<const char*>() && m.acl.saves == 1);
  assert(memcmp(&client, &advanced, sizeof(advanced)) == 0);
  assert(m.retry_cancellations == 1);
  assert(result(m, adminClientRequest("admin.catchup", client,
      ",\"mode\":\"keep\",\"count\":99,\"expected_sync\":200"), sequence++)["ok"] == true);
  assert(memcmp(&client, &advanced, sizeof(advanced)) == 0 && m.acl.saves == 1);
  m.acl.save_ok = false;
  const auto failed = result(m, adminClientRequest("admin.catchup", client,
      ",\"mode\":\"keep\",\"count\":0,\"expected_sync\":200"), sequence++);
  assert(failed["error"].is<const char*>() && m.acl.saves == 2);
  assert(memcmp(&client, &advanced, sizeof(advanced)) == 0);
  m.acl.save_ok = true;
  assert(result(m, adminClientRequest("admin.catchup", client,
      ",\"mode\":\"before\",\"before\":4294967295,\"expected_sync\":200"), sequence++)["ok"] == true);
  assert(client.extra.room.sync_since == 300 && m.acl.saves == 3);
  // A retained own-author post must not create a future catch-up cursor.
  assert(client.extra.room.sync_since < m.posts[4].post_timestamp);
  auto& transient = m.acl.clients[2]; unreadPosts(m, transient);
  transient.extra.room.pending_ack = 0x87654321;
  transient.extra.room.pending_topic_revision = 7;
  transient.extra.room.push_post_timestamp = 150;
  assert(result(m, adminClientRequest("admin.catchup", transient,
      ",\"mode\":\"keep\",\"count\":0,\"expected_sync\":102"), sequence++)["ok"] == true);
  assert(transient.extra.room.sync_since == 300 && transient.extra.room.pending_ack == 0x87654321);
  assert(transient.extra.room.pending_topic_revision == 7 && m.acl.saves == 3);
  assert(!transient.permissions_are_explicit && transient.permissions == PERM_ACL_READ_WRITE);
  puts("ADMIN_CATCHUP: newest counts, stale cursors, future dates, own posts, topic ACKs, rollback and transient policy passed");
}
static void adminListingCases() {
  MyMesh m; enable(m); seedClients(m);
  auto& selected = m.acl.clients[1]; unreadPosts(m, selected);
  selected.extra.room.pending_ack = 1234;
  selected.extra.room.push_post_timestamp = 150;
  selected.extra.room.ack_timeout = ticks + 3000;
  selected.observed_path_len = 0x41;
  selected.observed_path[0] = 0x12; selected.observed_path[1] = 0x34;
  m.acl.clients[2].last_activity = 0;
  m.acl.clients[3].last_activity = m.clock.now + 1;
  m.acl.clients[3].extra.room.push_failures = 3;
  assert(m.room_access.addBan(m._fs, m.acl.clients[4].id.pub_key) == mesh::RoomAccessPolicy::BanResult::Saved);
  uint32_t sequence = 1;
  for (unsigned cursor : {0, 2, 4}) {
    const std::string output = execute(m, request("admin.users", "admin-secret",
        ",\"cursor\":" + std::to_string(cursor)), sequence++);
    assert(output.size() < 2048);
    JsonDocument page; assert(deserializeJson(page, output) == DeserializationError::Ok);
    assert(page["total"] == 5 && page["boot"] == 71);
    assert(page["active"] == 3 && page["backlog"] == 24);
    assert(page["pending"] == 1 && page["failed"] == 1);
    const unsigned expected = cursor == 4 ? 1 : 2;
    assert(page["users"].is<JsonArray>() && page["users"].size() == expected);
    for (unsigned i = 0; i < expected; ++i) {
      const auto user = page["users"][i];
      assert(user["key"].as<std::string>() == clientKey(m.acl.clients[cursor + i]));
      assert(user["shared_secret"].isUnbound() && user["last_timestamp"].isUnbound());
      assert(user["outpath"].isUnbound() && user["altpath"].isUnbound());
    }
    if (cursor < 4) assert(page["next"] == cursor + 2);
    else assert(page["next"].isNull());
  }
  const auto detail = result(m, adminClientRequest("admin.user", selected), sequence++);
  assert(detail["boot"] == 71 && detail["user"]["key"].as<std::string>() == clientKey(selected));
  assert(detail["user"]["outpath"] == "A2" && detail["user"]["altpath"] == "unknown");
  assert(detail["user"]["observed"] == "1234" && detail["user"]["sync_since"] == 101);
  assert(detail["user"]["delivery"] == "post" && detail["user"]["ack_wait_ms"] == 3000);
  assert(detail["user"]["shared_secret"].isUnbound() && m.acl.saves == 0);
  const auto inactive = result(m, adminClientRequest("admin.user", m.acl.clients[2]), sequence++);
  assert(inactive["user"]["active"] == false && inactive["user"]["heard_ago"].isNull());
  const auto future = result(m, adminClientRequest("admin.user", m.acl.clients[3]), sequence++);
  assert(future["user"]["heard_ago"].isNull());
  const auto banned = result(m, adminClientRequest("admin.user", m.acl.clients[4]), sequence++);
  assert(banned["user"]["banned"] == true && banned["user"]["active"] == false);
  selected.out_path_len = selected.alt_path_len = selected.observed_path_len = 0x60;
  memset(selected.out_path, 0xA1, MAX_PATH_SIZE);
  memset(selected.alt_path, 0xB2, MAX_PATH_SIZE);
  memset(selected.observed_path, 0xC3, MAX_PATH_SIZE);
  const std::string maximum = execute(m, adminClientRequest("admin.user", selected), sequence++);
  JsonDocument complete; assert(deserializeJson(complete, maximum) == DeserializationError::Ok);
  assert(complete["user"]["outpath"].as<std::string>().size() == 128 && maximum.size() < 2048);
  for (size_t capacity = 0; capacity < 750; ++capacity) {
    execute(m, adminClientRequest("admin.user", selected), sequence++, 1, capacity);
  }
  assert(m.acl.saves == 0);
  puts("ADMIN_USERS: two-row pages, delivery detail, full routes, inactive/future/banned states and bounded JSON passed");
}
static void adminAccessAndSettingsCases() {
  MyMesh m; enable(m); seedClients(m);
  uint32_t sequence = 1;
  auto& selected = m.acl.clients[1];
  const std::string target_key = clientKey(selected);
  const ClientInfo following = m.acl.clients[2];
  const auto changed_access = result(m, adminClientRequest("admin.access", selected,
      ",\"role\":1,\"expected_role\":2"), sequence++);
  assert(changed_access["ok"] == true && changed_access["pending_save"] == true);
  assert(selected.permissions == PERM_ACL_READ_ONLY && selected.permissions_are_explicit);
  assert(m.dirty_contacts_expiry != 0 && m.acl.saves == 0);
  const auto stale = result(m, adminClientRequest("admin.access", selected,
      ",\"role\":3,\"expected_role\":2"), sequence++);
  assert(stale["error"].is<const char*>() && selected.permissions == PERM_ACL_READ_ONLY);
  assert(result(m, adminClientRequest("admin.access", selected,
      ",\"role\":0,\"expected_role\":1"), sequence++)["ok"] == true);
  assert(m.acl.count == 4 && m.acl.getClient(following.id.pub_key, PUB_KEY_SIZE) != nullptr);
  assert(clientKey(m.acl.clients[1]) == clientKey(following));
  uint8_t removed[PUB_KEY_SIZE]; memset(removed, 2, sizeof(removed));
  assert(m.acl.getClient(removed, PUB_KEY_SIZE) == nullptr);
  const unsigned before_ban = m.storage.write_opens;
  assert(result(m, request("admin.ban", "admin-secret", ",\"boot\":71,\"key\":\""
      + target_key + "\",\"banned\":true"), sequence++)["ok"] == true);
  assert(m.room_access.isBanned(removed) && m.storage.write_opens > before_ban);
  const unsigned after_ban = m.storage.write_opens;
  assert(result(m, request("admin.ban", "admin-secret", ",\"boot\":71,\"key\":\""
      + target_key + "\",\"banned\":true"), sequence++)["ok"] == true);
  assert(m.storage.write_opens == after_ban);
  assert(result(m, request("admin.ban", "admin-secret", ",\"boot\":71,\"key\":\""
      + target_key + "\",\"banned\":false"), sequence++)["ok"] == true);
  assert(!m.room_access.isBanned(removed));
  const unsigned reads_only = m.storage.write_opens;
  const auto settings = result(m, request("admin.settings", "admin-secret"), sequence++);
  assert(settings["boot"] == 71 && settings["topic"] == m.room_topic);
  assert(settings["persistent_history"] == false && settings["post_rate"] == 0 && settings["poll_rate"] == 0);
  assert(m.storage.write_opens == reads_only);
  const std::string old_topic = m.room_topic;
  const auto topic = result(m, request("admin.topic", "admin-secret", ",\"boot\":71,\"value\":\"New topic\",\"expected\":\""
      + old_topic + "\""), sequence++);
  assert(topic["ok"] == true && strcmp(m.room_topic, "New topic") == 0);
  const uint32_t revision = m.room_topic_revision;
  const auto stale_topic = result(m, request("admin.topic", "admin-secret", ",\"boot\":71,\"value\":\"Stale\",\"expected\":\""
      + old_topic + "\""), sequence++);
  assert(stale_topic["error"].is<const char*>() && strcmp(m.room_topic, "New topic") == 0 && m.room_topic_revision == revision);
  assert(result(m, request("admin.history", "admin-secret", ",\"boot\":71,\"enabled\":true,\"expected\":false"), sequence++)["ok"] == true);
  assert(m.room_history_enabled);
  const auto stale_history = result(m, request("admin.history", "admin-secret", ",\"boot\":71,\"enabled\":false,\"expected\":false"), sequence++);
  assert(stale_history["error"].is<const char*>() && m.room_history_enabled);
  assert(result(m, request("admin.rates", "admin-secret", ",\"boot\":71,\"post_rate\":12,\"poll_rate\":60"), sequence++)["ok"] == true);
  assert(m.room_access.postRate() == 12 && m.room_access.pollRate() == 60);
  const unsigned rate_writes = m.storage.write_opens;
  assert(result(m, request("admin.rates", "admin-secret", ",\"boot\":71,\"post_rate\":12,\"poll_rate\":60"), sequence++)["ok"] == true);
  assert(m.storage.write_opens == rate_writes);
  ticks += 60000;
  m.storage.capacity = 0;
  const auto failed_topic = result(m, request("admin.topic", "admin-secret", ",\"boot\":71,\"value\":\"Not saved\",\"expected\":\"New topic\""), sequence++);
  assert(failed_topic["error"].is<const char*>() && strcmp(m.room_topic, "New topic") == 0);
  m.storage.reset(); m.storage.faults.insert("open:w:/room_hist_cfg.tmp");
  const auto failed_history = result(m, request("admin.history", "admin-secret", ",\"boot\":71,\"enabled\":false,\"expected\":true"), sequence++);
  assert(failed_history["error"].is<const char*>() && m.room_history_enabled);
  m.storage.reset(); m.storage.capacity = 0;
  const auto failed_rates = result(m, request("admin.rates", "admin-secret", ",\"boot\":71,\"post_rate\":13,\"poll_rate\":61"), sequence++);
  assert(failed_rates["error"].is<const char*>() && m.room_access.postRate() == 12 && m.room_access.pollRate() == 60);
  puts("ADMIN_SETTINGS: queued ACL semantics, role revocation, bans, topic/history/rates stale guards and storage failure passed");
}
static void adminInvalidAndSessionCases() {
  struct Invalid { const char* operation; const char* fields; };
  const Invalid invalid[] = {
    {"admin.users", ""}, {"admin.users", ",\"cursor\":-1"},
    {"admin.users", ",\"cursor\":\"0\""}, {"admin.users", ",\"cursor\":1.5"},
    {"admin.users", ",\"cursor\":false"}, {"admin.users", ",\"cursor\":6"},
    {"admin.user", ",\"key\":9"}, {"admin.user", ",\"key\":\"02020202\""},
    {"admin.user", ",\"key\":\"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff\""},
    {"admin.route", ",\"which\":\"secret\",\"value\":\"B1\",\"expected\":\"A2\""},
    {"admin.route", ",\"which\":false,\"value\":\"B1\",\"expected\":\"A2\""},
    {"admin.route", ",\"which\":\"outpath\",\"value\":[],\"expected\":\"A2\""},
    {"admin.route", ",\"which\":\"outpath\",\"value\":\"B1\",\"expected\":2"},
    {"admin.route", ",\"which\":\"outpath\",\"value\":\"B1\",\"expected\":\"A2\\u0000\""},
    {"admin.catchup", ",\"mode\":\"skip\",\"count\":0,\"expected_sync\":101"},
    {"admin.catchup", ",\"mode\":\"keep\",\"count\":-1,\"expected_sync\":101"},
    {"admin.catchup", ",\"mode\":\"keep\",\"count\":65536,\"expected_sync\":101"},
    {"admin.catchup", ",\"mode\":\"keep\",\"count\":\"0\",\"expected_sync\":101"},
    {"admin.catchup", ",\"mode\":\"keep\",\"count\":1.5,\"expected_sync\":101"},
    {"admin.catchup", ",\"mode\":\"keep\",\"count\":0,\"expected_sync\":\"101\""},
    {"admin.catchup", ",\"mode\":\"before\",\"before\":-1,\"expected_sync\":101"},
    {"admin.catchup", ",\"mode\":\"before\",\"before\":4294967296,\"expected_sync\":101"},
    {"admin.access", ",\"role\":4,\"expected_role\":2"},
    {"admin.access", ",\"role\":5,\"expected_role\":2"},
    {"admin.access", ",\"role\":256,\"expected_role\":2"},
    {"admin.access", ",\"role\":-1,\"expected_role\":2"},
    {"admin.access", ",\"role\":2.5,\"expected_role\":2"},
    {"admin.access", ",\"role\":\"3\",\"expected_role\":2"},
    {"admin.access", ",\"role\":3,\"expected_role\":\"2\""},
    {"admin.ban", ",\"banned\":\"true\""}, {"admin.ban", ",\"banned\":1"},
    {"admin.history", ",\"enabled\":1,\"expected\":false"},
    {"admin.history", ",\"enabled\":true,\"expected\":\"false\""},
    {"admin.topic", ",\"value\":7,\"expected\":\"unchanged\""},
    {"admin.topic", ",\"value\":\"New\\u0000topic\",\"expected\":\"unchanged\""},
    {"admin.rates", ",\"post_rate\":-1,\"poll_rate\":60"},
    {"admin.rates", ",\"post_rate\":65536,\"poll_rate\":60"},
    {"admin.rates", ",\"post_rate\":1.5,\"poll_rate\":60"},
    {"admin.rates", ",\"post_rate\":12,\"poll_rate\":\"60\""},
    {"admin.cli", ",\"value\":\"erase\""}, {"admin.userdetail", ""},
    {"admin.users\\u0000post", ",\"cursor\":0"},
  };
  for (const auto& entry : invalid) {
    MyMesh m; enable(m); seedClients(m); strcpy(m.room_topic, "unchanged");
    const std::vector<ClientInfo> clients(m.acl.clients, m.acl.clients + m.acl.count);
    const unsigned writes = m.storage.write_opens;
    const auto denied = result(m, adminClientRequest(entry.operation, m.acl.clients[1], entry.fields), 1);
    assert(denied["error"].is<const char*>() && denied.as<JsonObjectConst>().size() == 1);
    assert(m.acl.count == 5 && memcmp(clients.data(), m.acl.clients, clients.size() * sizeof(ClientInfo)) == 0);
    assert(m.acl.saves == 0 && m.dirty_contacts_expiry == 0 && m.retry_cancellations == 0);
    assert(m.storage.write_opens == writes && m.room_access.banCount() == 0);
    assert(!m.room_history_enabled && strcmp(m.room_topic, "unchanged") == 0);
    assert(m.room_access.postRate() == 0 && m.room_access.pollRate() == 0);
  }
  const char* writes[] = {"admin.route", "admin.catchup", "admin.access", "admin.ban",
      "admin.topic", "admin.history", "admin.rates"};
  for (const char* operation : writes) {
    MyMesh m; enable(m); seedClients(m);
    error(m, adminClientRequest(operation, m.acl.clients[1], "", 70), 99, "restarted");
    error(m, request(operation, "admin-secret", ",\"boot\":\"71\""), 99, "restarted");
    error(m, request(operation, "admin-secret"), 99, "restarted");
    assert(floors(m) == 0 && m.acl.lookups == 0 && m.acl.saves == 0);
  }
  MyMesh m; enable(m); seedClients(m);
  const auto mutation = adminClientRequest("admin.route", m.acl.clients[1], routeFields("outpath", "B1", "A2"));
  assert(result(m, mutation, 1)["ok"] == true && m.acl.saves == 1);
  error(m, mutation, 1, "completed"); assert(m.acl.saves == 1);
  error(m, request("admin.users", "write-secret", ",\"cursor\":0"), 100, "admin");
  assert(floors(m) == 1);
  assert(result(m, request("admin.users", "admin-secret", ",\"cursor\":0"), 2)["users"].size() == 2);
  MyMesh full; enable(full);
  for (uint8_t owner = 1; owner <= 8; ++owner) {
    assert(result(full, request("admin.settings", "admin-secret"), 1, owner)["boot"] == 71);
  }
  error(full, request("admin.settings", "admin-secret"), 1, "busy", 9);
  assert(floors(full) == 8 && full.acl.lookups == 0);
  MyMesh banned; enable(banned);
  uint8_t author[PUB_KEY_SIZE]; const auto identity = token();
  mesh::Utils::sha256(author, sizeof(author), identity.data(), identity.size(), banned.self_id.pub_key, PUB_KEY_SIZE);
  assert(banned.room_access.addBan(banned._fs, author) == mesh::RoomAccessPolicy::BanResult::Saved);
  error(banned, request("admin.settings", "admin-secret"), 1, "access denied");
  assert(floors(banned) == 0 && banned.acl.lookups == 0 && !banned.room_web_request_authenticated);
  printf("ADMIN_INVALID: %zu strict schemas, 21 boot guards, replay/auth floors, bounded sessions and banned browser passed\n", std::size(invalid));
}
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
  adminAuthorizationCases();
  adminRouteCases();
  adminCatchUpCases();
  adminListingCases();
  adminAccessAndSettingsCases();
  adminInvalidAndSessionCases();
  // Deliver concrete backend responses for the browser boundary fixture.
  MyMesh boundary; enable(boundary);
  printf("BOUNDARY_STATUS %s\n", execute(boundary, request("status"), 1).c_str());
  assert(result(boundary, saveRequest(1, 0, "Instructions", "Read only this article"), 2)["ok"] == true);
  printf("BOUNDARY_INDEX %s\n", execute(boundary, request("board.index", "write-secret", ",\"revision\":0,\"cursor\":0"), 3).c_str());
  printf("BOUNDARY_READ %s\n", execute(boundary, request("board.read", "write-secret", ",\"id\":1,\"version\":1,\"offset\":0"), 4).c_str());
  printf("PASS: %u base and 6 admin actual room web service authorization, storage, quota, JSON and boundary groups\n", scenarios);
}
