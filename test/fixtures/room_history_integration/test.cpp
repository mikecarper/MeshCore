// The production methods, plain-post gate, RTCClock and PostInfo are extracted
// unchanged. Only filesystem faults, radio ACK observation and identity hashing
// are host boundaries; the actual history store, quota and replay cache run here.
#include "../room_history_store/filesystem.h"
#include <helpers/RoomHistoryStore.h>
#include <helpers/RoomAccessPolicy.h>
#include <helpers/LogicalMessageCache.h>
#define PUB_KEY_SIZE 32
#define MAX_POST_TEXT_LEN 151
#define MAX_UNSYNCED_POSTS 32
#define MAX_HASH_SIZE 8
#define POST_SYNC_DELAY_SECS 6
#define PUSH_NOTIFY_DELAY_MILLIS 2000
#define TXT_TYPE_PLAIN 0
#undef MESH_DEBUG_PRINTLN
#define MESH_DEBUG_PRINTLN(...) ((void)0)
static uint32_t ticks = 100;
static unsigned long futureMillis(unsigned long delay) { return uint32_t(ticks + delay); }
namespace mesh {
struct Identity { uint8_t pub_key[32] = {}; };
struct Utils {
  static void sha256(uint8_t* out, size_t size, const uint8_t* key, size_t key_size,
                     const uint8_t* text, size_t text_size) {
    uint64_t value = UINT64_C(14695981039346656037);
    for (size_t i = 0; i < key_size; ++i) value = (value ^ key[i]) * UINT64_C(1099511628211);
    for (size_t i = 0; i < text_size; ++i) value = (value ^ text[i]) * UINT64_C(1099511628211);
    for (size_t i = 0; i < size; ++i) out[i] = uint8_t(value >> (8 * (i % 8)));
  }
};
}
#include "state.inc"
struct StrHelper { static void strncpy(char*, const char*, size_t); };
struct Clock : mesh::RTCClock {
  uint32_t now = 1000;
  unsigned set_calls = 0;
  uint32_t getCurrentTime() override { return now; }
  void setCurrentTime(uint32_t value) override { now = value; ++set_calls; }
};
struct ClientInfo {
  mesh::Identity id;
  uint8_t permissions = PERM_ACL_READ_WRITE;
  struct { struct { uint32_t last_post_timestamp = 0; uint16_t post_quota_used = 0; } room; } extra;
  ClientInfo() { id.pub_key[0] = 1; }
};
struct MyMesh {
  MemoryFS* _fs;
  Clock clock;
  mesh::Identity self_id;
  mesh::RoomAccessPolicy room_access;
  mesh::LogicalMessageCache<8> recent_room_posts;
  ClientInfo* active = nullptr;
  PostInfo posts[MAX_UNSYNCED_POSTS] = {};
  uint32_t post_ready_at[MAX_UNSYNCED_POSTS] = {}, post_ready_mask = UINT32_MAX;
  uint32_t last_room_timestamp = 0;
  mesh::RoomHistoryState room_history_state;
  bool room_history_enabled = false, room_history_available = true;
  int next_post_idx = 0;
  unsigned long next_push = 0;
  uint16_t _num_posted = 0;
  unsigned acks = 0;
  explicit MyMesh(MemoryFS* fs) : _fs(fs) {
    metadata_filesystem = fs;
    self_id.pub_key[0] = 77;
    if (fs) assert(room_access.load(fs));
  }
  Clock* getRTCClock() { return &clock; }
  bool addPost(ClientInfo*, const char*);
  bool addSystemPost(const char*);
  bool storePost(const mesh::Identity&, const char*);
  bool snapshotRoomHistory();
  void loadRoomHistory();
  bool handleRoomHistoryCommand(const char*, char*);
  void serviceRoomQuotas() {
    room_access.serviceQuotaWindow(ticks, [this] { active->extra.room.post_quota_used = 0; });
  }
  void receive(ClientInfo* client, const char* text, uint32_t sender_timestamp) {
    active = client;
    const uint8_t flags = TXT_TYPE_PLAIN;
    const size_t text_len = strlen(text);
    bool send_ack = false;
#include "post_gate.inc"
    if (send_ack) ++acks;
  }
  std::string command(const char* command) {
    char reply[160] = {};
    assert(handleRoomHistoryCommand(command, reply));
    return reply;
  }
  std::vector<std::string> retained() const {
    std::vector<std::string> result;
    for (int k = 0, idx = next_post_idx; k < MAX_UNSYNCED_POSTS;
         ++k, idx = (idx + 1) % MAX_UNSYNCED_POSTS) {
      if (posts[idx].post_timestamp) result.emplace_back(posts[idx].text);
    }
    return result;
  }
};
#include "production.inc"
struct AdmissionState {
  std::vector<std::string> text;
  uint32_t floor, mask, ready[32], timestamps[32];
  int index;
  unsigned long next;
  uint16_t stats, quota;
  uint32_t replay;
  unsigned acks;
  AdmissionState(const MyMesh& m, const ClientInfo& c)
      : text(m.retained()), floor(m.last_room_timestamp), mask(m.post_ready_mask),
        index(m.next_post_idx), next(m.next_push), stats(m._num_posted),
        quota(c.extra.room.post_quota_used), replay(c.extra.room.last_post_timestamp), acks(m.acks) {
    memcpy(ready, m.post_ready_at, sizeof(ready));
    for (unsigned i = 0; i < 32; ++i) timestamps[i] = m.posts[i].post_timestamp;
  }
  void unchanged(const MyMesh& m, const ClientInfo& c) const {
    assert(text == m.retained() && floor == m.last_room_timestamp && mask == m.post_ready_mask);
    assert(index == m.next_post_idx && next == m.next_push && stats == m._num_posted);
    assert(quota == c.extra.room.post_quota_used && replay == c.extra.room.last_post_timestamp);
    assert(acks == m.acks && memcmp(ready, m.post_ready_at, sizeof(ready)) == 0);
    for (unsigned i = 0; i < 32; ++i) assert(timestamps[i] == m.posts[i].post_timestamp);
  }
};
static void compareBoot(MemoryFS& fs, const MyMesh& running) {
  const auto text = running.retained();
  const uint32_t floor = running.last_room_timestamp;
  MyMesh boot(&fs); boot.clock.now = 5; boot.loadRoomHistory();
  assert(boot.room_history_available && boot.room_history_enabled);
  assert(boot.retained() == text && boot.last_room_timestamp == floor);
  assert(boot.clock.now == 5 && boot.clock.set_calls == 0 && boot.post_ready_mask == UINT32_MAX);
  assert(boot._num_posted == 0);
}
int main() {
  unsigned cases = 0;
  {
    MemoryFS fs; MyMesh m(&fs); m.loadRoomHistory();
    assert(!m.room_history_enabled && m.room_history_available && m.retained().empty());
    for (unsigned i = 0; i < 40; ++i) assert(m.addSystemPost(("volatile-" + std::to_string(i)).c_str()));
    assert(!fs.files.count(mesh::ROOM_HISTORY_PATH) && fs.write_opens == 0);
    assert(m.retained().size() == 32 && m.retained().front() == "volatile-8");
    assert(m.command("set room.history on").find("enabled") != std::string::npos);
    assert(m.room_history_enabled && m.room_history_state.count == 32 && m.room_history_state.total_records == 32);
    compareBoot(fs, m);
    for (unsigned i = 0; i < 180; ++i) {
      assert(m.addSystemPost(("durable-" + std::to_string(i)).c_str()));
      assert(m.room_history_state.count == 32 && m.room_history_state.total_records <= 64);
      assert(fs.get(mesh::ROOM_HISTORY_PATH).size() <= mesh::ROOM_HISTORY_MAX_FILE_SIZE);
      if (i % 13 == 0) compareBoot(fs, m);
      ++cases;
    }
    compareBoot(fs, m);
    assert(m.retained().front() == "durable-148" && m.retained().back() == "durable-179");
    assert(m.command("get room.history") == "> on; storage ready; retained 32/32");
    ++cases;
  }
  for (const std::string& text : {std::string(151, 'A'), std::string("x") + std::string(50, 'z')}) {
    // Second text is replaced below with exactly fifty complete UTF-8 codepoints.
    std::string exact = text;
    if (exact.size() != 151) { exact = "x"; for (unsigned i = 0; i < 50; ++i) exact += "\xe2\x98\x83"; }
    assert(exact.size() == 151);
    MemoryFS fs; MyMesh m(&fs); assert(m.command("set room.history on").find("enabled") != std::string::npos);
    ClientInfo client; m.receive(&client, exact.c_str(), 123);
    assert(m.acks == 1 && m._num_posted == 1 && m.retained()[0] == exact);
    assert(m.snapshotRoomHistory()); compareBoot(fs, m);
    m.receive(&client, exact.c_str(), 123);
    assert(m.acks == 2 && m._num_posted == 1);
    AdmissionState before(m, client);
    m.receive(&client, (exact + "!").c_str(), 124); before.unchanged(m, client);
    assert(m.addSystemPost("short"));
    for (unsigned i = 0; i < 31; ++i) assert(m.addSystemPost("tiny"));
    assert(m.snapshotRoomHistory()); compareBoot(fs, m); // reused long slot has canonical padding
    ++cases;
  }
  for (const std::string failure : {"open:a:/room_history", "write:/room_history", "flush:/room_history",
                                    "close:w:/room_history", "read:/room_history", "seek:/room_history"}) {
    MemoryFS fs; MyMesh m(&fs); ClientInfo client;
    assert(m.command("set room.history on").find("enabled") != std::string::npos);
    m.receive(&client, "accepted", 100); assert(m.acks == 1 && m._num_posted == 1);
    ticks = 100; fs.reset(); fs.faults.insert(failure);
    AdmissionState before(m, client);
    m.receive(&client, "not-yet-retained", 101); before.unchanged(m, client);
    fs.reset(); m.receive(&client, "not-yet-retained", 101);
    assert(m.acks == 2 && m._num_posted == 2 && m.retained().back() == "not-yet-retained");
    assert(client.extra.room.last_post_timestamp == 101);
    compareBoot(fs, m); ++cases;
  }
  {
    MemoryFS fs; MyMesh m(&fs); ClientInfo client;
    assert(m.command("set room.history on").find("enabled") != std::string::npos);
    m.receive(&client, "trusted", 100); fs.short_write = true;
    AdmissionState before(m, client); m.receive(&client, "partial", 101); before.unchanged(m, client);
    assert(m.room_history_state.needs_repair);
    fs.reset();
    MyMesh reboot(&fs); reboot.loadRoomHistory();
    assert(reboot.room_history_state.needs_repair && reboot.retained() == m.retained());
    m.receive(&client, "partial", 101);
    assert(!m.room_history_state.needs_repair && m.acks == 2);
    assert(m.retained() == std::vector<std::string>({"trusted", "partial"}));
    compareBoot(fs, m); ++cases;
  }
  {
    MemoryFS fs; MyMesh m(&fs); ClientInfo client;
    assert(m.command("set room.history on").find("enabled") != std::string::npos);
    m.receive(&client, "durable-but-unconfirmed", 100); assert(m.acks == 1);
    // Fail readback only after header+existing record validation. A whole disk
    // append can survive without ACK, but trusted RAM repair must remove it.
    fs.reset(); fs.fail_read_at = 3;
    AdmissionState before(m, client); m.receive(&client, "readback-failed", 101); before.unchanged(m, client);
    assert(m.room_history_state.needs_repair);
    fs.reset(); m.receive(&client, "readback-failed", 101);
    assert(m.acks == 2 && m._num_posted == 2 && m.retained().size() == 2);
    compareBoot(fs, m); ++cases;
  }
  {
    MemoryFS fs; MyMesh m(&fs);
    assert(m.command("set room.history on").find("enabled") != std::string::npos);
    assert(m.addSystemPost("archived")); const auto archive = fs.get(mesh::ROOM_HISTORY_PATH);
    assert(m.command("set room.history off").find("archive retained") != std::string::npos);
    assert(fs.get(mesh::ROOM_HISTORY_PATH) == archive);
    assert(m.addSystemPost("volatile")); assert(fs.get(mesh::ROOM_HISTORY_PATH) == archive);
    MyMesh boot(&fs); boot.loadRoomHistory();
    assert(!boot.room_history_enabled && boot.retained().empty() && fs.get(mesh::ROOM_HISTORY_PATH) == archive);
    assert(m.command("set room.history 1").find("enabled") != std::string::npos);
    compareBoot(fs, m);
    const auto floor = m.last_room_timestamp;
    assert(m.command("room.history.clear").find("cleared") != std::string::npos);
    assert(m.retained().empty() && m.last_room_timestamp == floor);
    MyMesh cleared(&fs); cleared.loadRoomHistory();
    assert(cleared.room_history_available && cleared.retained().empty() && cleared.last_room_timestamp == 0);
    ++cases;
  }
  {
    MemoryFS fs; MyMesh m(&fs); ClientInfo client;
    assert(m.command("set room.history on").find("enabled") != std::string::npos);
    assert(m.addSystemPost("before-clear")); const auto archive = fs.get(mesh::ROOM_HISTORY_PATH);
    fs.faults.insert("rename:/room_history.tmp:/room_history"); AdmissionState before(m, client);
    assert(m.command("room.history.clear").find("failed") != std::string::npos);
    before.unchanged(m, client); fs.reset(); assert(fs.get(mesh::ROOM_HISTORY_PATH) == archive);
    ++cases;
  }
  {
    MemoryFS fs; MyMesh m(&fs); ClientInfo client; assert(m.addSystemPost("volatile-before-enable"));
    fs.faults.insert("rename:/room_hist_cfg.tmp:/room_hist_cfg");
    assert(m.command("set room.history on").find("failed") != std::string::npos && !m.room_history_enabled);
    fs.reset(); assert(m.command("set room.history on").find("enabled") != std::string::npos);
    const auto config = fs.get(mesh::ROOM_HISTORY_CONFIG_PATH);
    fs.faults.insert("rename:/room_hist_cfg.tmp:/room_hist_cfg");
    assert(m.command("set room.history 0").find("failed") != std::string::npos && m.room_history_enabled);
    fs.reset(); assert(fs.get(mesh::ROOM_HISTORY_CONFIG_PATH) == config);
    for (const char* invalid : {"set room.history yes", "set room.history on ", "set room.history 2"}) {
      assert(m.command(invalid).find("requires on/off") != std::string::npos);
    }
    char reply[160] = {}; assert(!m.handleRoomHistoryCommand("get room.history.extra", reply));
    ++cases;
  }
  {
    MemoryFS fs; MyMesh m(&fs); m.clock.now = 2000;
    assert(m.command("set room.history on").find("enabled") != std::string::npos);
    assert(m.addSystemPost("first")); const auto floor = m.last_room_timestamp;
    m.clock.now = 5; m.clock.resetUniqueTime(5);
    assert(m.addSystemPost("backward")); assert(m.last_room_timestamp == floor + 1 && m.clock.now == 5);
    assert(m.clock.set_calls == 0 && m.post_ready_at[1] == ticks + 6000);
    MyMesh boot(&fs); boot.clock.now = 1; boot.loadRoomHistory(); const auto boot_floor = boot.last_room_timestamp;
    assert(boot.addSystemPost("after-reboot") && boot.last_room_timestamp == boot_floor + 1);
    assert(boot.clock.now == 1 && boot.clock.set_calls == 0); ++cases;
  }
  {
    MemoryFS fs; MyMesh m(&fs);
    assert(m.command("set room.history on").find("enabled") != std::string::npos);
    m.clock.now = UINT32_MAX; assert(m.addSystemPost("last representable"));
    ClientInfo client; AdmissionState before(m, client); assert(!m.addSystemPost("overflow")); before.unchanged(m, client);
    MyMesh boot(&fs); boot.loadRoomHistory(); assert(boot.last_room_timestamp == UINT32_MAX);
    assert(!boot.addSystemPost("cannot wrap")); ++cases;
  }
  {
    MemoryFS fs; MyMesh m(&fs); ClientInfo client;
    assert(!m.addSystemPost(nullptr) && !m.addSystemPost("") && !m.storePost(client.id, nullptr));
    assert(!m.addSystemPost(std::string(152, 'x').c_str()) && m._num_posted == 0);
    MyMesh unavailable(nullptr); unavailable.loadRoomHistory();
    assert(!unavailable.room_history_available && !unavailable.addSystemPost("no fs"));
    assert(unavailable.command("get room.history").find("unavailable") != std::string::npos);
    assert(unavailable.command("set room.history on").find("failed") != std::string::npos);
    assert(unavailable.command("room.history.clear").find("failed") != std::string::npos); ++cases;
  }
  {
    MemoryFS fs; MyMesh m(&fs);
    assert(m.command("set room.history on").find("enabled") != std::string::npos);
    assert(m.addSystemPost("kept")); const auto original = fs.get(mesh::ROOM_HISTORY_PATH);
    fs.faults.insert("open:r:/room_history"); MyMesh boot(&fs); boot.loadRoomHistory();
    assert(!boot.room_history_available && boot.retained().empty() && fs.get(mesh::ROOM_HISTORY_PATH) == original);
    assert(!boot.addSystemPost("must not overwrite")); fs.reset();
    auto corrupt = original; corrupt[3] = 2; fs.put(mesh::ROOM_HISTORY_PATH, corrupt);
    MyMesh future(&fs); future.loadRoomHistory();
    assert(!future.room_history_available && !future.addSystemPost("must preserve future"));
    assert(fs.get(mesh::ROOM_HISTORY_PATH) == corrupt); ++cases;
  }
  {
    MemoryFS fs; mesh::RoomHistoryState state;
    metadata_filesystem = &fs;
    assert(mesh::saveRoomHistoryConfig(&fs, true));
    assert(mesh::saveRoomHistorySnapshot(&fs, state, 2, [](uint8_t ordinal, mesh::RoomHistoryRecord& record) {
      memset(&record, 0, sizeof(record)); record.author[0] = 1;
      record.timestamp = ordinal == 0 ? 100 : 99; strcpy(record.text, "out of order"); return true;
    }));
    const auto original = fs.get(mesh::ROOM_HISTORY_PATH); MyMesh boot(&fs); boot.loadRoomHistory();
    assert(!boot.room_history_available && boot.retained().empty() && boot.last_room_timestamp == 0);
    assert(fs.get(mesh::ROOM_HISTORY_PATH) == original && !boot.addSystemPost("no rewrite")); ++cases;
  }
  printf("%u production room history integration scenarios passed\n", cases);
}
