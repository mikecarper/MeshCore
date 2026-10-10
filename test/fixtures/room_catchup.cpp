#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include <MeshCore.h>
#include <helpers/RoomCatchUp.h>

@CONSTANTS@
#define MESH_CLIENT_REPEATER_ONLY 0
namespace mesh {
struct Identity {
  uint8_t pub_key[PUB_KEY_SIZE]{};
  bool matches(const Identity& other) const {
    return memcmp(pub_key, other.pub_key, PUB_KEY_SIZE) == 0;
  }
};
}
@CLIENT_INFO@;
@POST_INFO@;

struct MyMesh {
  struct ACL {
    ClientInfo clients[2]{};
    int getNumClients() const { return 2; }
    ClientInfo* getClientByIdx(int index) { return &clients[index]; }
  } acl;
  struct Access {
    bool allowsIdentity(const uint8_t*) const { return true; }
  } room_access;
  bool processAck(const uint8_t*);
};
@PROCESS_ACK@

static ClientInfo client(uint8_t identity = 1, uint32_t cursor = 0) {
  ClientInfo value{};
  memset(value.id.pub_key, identity, PUB_KEY_SIZE);
  value.permissions = PERM_ACL_READ_WRITE;
  value.extra.room.sync_since = cursor;
  value.extra.room.last_post_timestamp = 550;
  value.extra.room.post_quota_used = 9;
  value.extra.room.poll_quota_used = 11;
  value.extra.room.topic_seen_revision = 7;
  value.extra.room.topic_failures = 2;
  return value;
}
static PostInfo post(uint32_t timestamp, uint8_t author = 2) {
  PostInfo value{}; value.post_timestamp = timestamp;
  memset(value.author.pub_key, author, PUB_KEY_SIZE);
  strcpy(value.text, "post");
  return value;
}
static void validatePlan(const ClientInfo& value, const std::vector<PostInfo>& posts,
                         mesh::RoomCatchUpMode mode, uint32_t argument,
                         uint32_t cursor, unsigned skipped, unsigned remaining) {
  const ClientInfo before = value;
  const auto plan = mesh::planRoomCatchUp(value, posts.data(), posts.size(), mode, argument);
  assert(memcmp(&value, &before, sizeof(value)) == 0);
  assert(plan.candidate_cursor == cursor && plan.skipped == skipped && plan.backlog_remaining == remaining);
  assert(plan.candidate_cursor >= value.extra.room.sync_since);
}

static void orderedCounts() {
  const auto value = client(1, 100);
  std::vector<PostInfo> posts = {post(400), post(150, 1), post(0), post(50), post(300), post(200)};
  validatePlan(value, posts, mesh::RoomCatchUpMode::SkipNext, 0, 100, 0, 3);
  validatePlan(value, posts, mesh::RoomCatchUpMode::SkipNext, 1, 200, 1, 2);
  validatePlan(value, posts, mesh::RoomCatchUpMode::SkipNext, 2, 300, 2, 1);
  validatePlan(value, posts, mesh::RoomCatchUpMode::SkipNext, UINT32_MAX, 400, 3, 0);
  validatePlan(value, posts, mesh::RoomCatchUpMode::KeepNewest, 0, 400, 3, 0);
  validatePlan(value, posts, mesh::RoomCatchUpMode::KeepNewest, 1, 300, 2, 1);
  validatePlan(value, posts, mesh::RoomCatchUpMode::KeepNewest, 2, 200, 1, 2);
  validatePlan(value, posts, mesh::RoomCatchUpMode::KeepNewest, UINT32_MAX, 100, 0, 3);
  validatePlan(value, posts, mesh::RoomCatchUpMode::Before, 0, 100, 0, 3);
  validatePlan(value, posts, mesh::RoomCatchUpMode::Before, 200, 100, 0, 3);
  validatePlan(value, posts, mesh::RoomCatchUpMode::Before, 300, 200, 1, 2);
  validatePlan(value, posts, mesh::RoomCatchUpMode::Before, UINT32_MAX, 400, 3, 0);
  for (size_t rotation = 0; rotation < posts.size(); ++rotation) {
    std::rotate(posts.begin(), posts.begin() + 1, posts.end());
    validatePlan(value, posts, mesh::RoomCatchUpMode::KeepNewest, 1, 300, 2, 1);
  }
  std::vector<PostInfo> own = {post(200, 1), post(400, 1), post(0)};
  validatePlan(value, own, mesh::RoomCatchUpMode::KeepNewest, 0, 100, 0, 0);
  puts("all modes order cyclic retention, exclude own posts, and bound future selections passed");
}

static void epochBoundaries() {
  std::vector<PostInfo> posts = {post(UINT32_MAX), post(UINT32_MAX - 1), post(1)};
  auto value = client(1, UINT32_MAX - 2);
  validatePlan(value, posts, mesh::RoomCatchUpMode::SkipNext, UINT32_MAX, UINT32_MAX, 2, 0);
  validatePlan(value, posts, mesh::RoomCatchUpMode::Before, UINT32_MAX, UINT32_MAX - 1, 1, 1);
  value.extra.room.sync_since = UINT32_MAX;
  validatePlan(value, posts, mesh::RoomCatchUpMode::KeepNewest, 0, UINT32_MAX, 0, 0);
  std::vector<PostInfo> empty;
  validatePlan(value, empty, mesh::RoomCatchUpMode::KeepNewest, 0, UINT32_MAX, 0, 0);
  value = client(1, 100);
  const auto invalid = mesh::planRoomCatchUp(value, posts.data(), 33, mesh::RoomCatchUpMode::SkipNext, 5);
  assert(invalid.candidate_cursor == 100 && invalid.skipped == 0 && !invalid.pending_post_cancelled);
  // Corrupt equal timestamps cannot under-report the actual cursor effect.
  std::vector<PostInfo> duplicate = {post(200), post(200), post(300)};
  validatePlan(value, duplicate, mesh::RoomCatchUpMode::SkipNext, 1, 200, 2, 1);
  puts("uint32 limits, empty retention, bounded input, and equal-timestamp accounting passed");
}

static void pendingDelivery() {
  std::vector<PostInfo> posts = {post(200), post(300), post(400)};
  for (bool topic : {false, true}) {
    MyMesh mesh; mesh.acl.clients[0] = client(1, 100); mesh.acl.clients[1] = client(3, 50);
    auto& own = mesh.acl.clients[0]; const ClientInfo other = mesh.acl.clients[1];
    own.extra.room.pending_ack = 0x55667788; own.extra.room.push_post_timestamp = 200;
    own.extra.room.pending_topic_revision = topic ? 8 : 0;
    own.extra.room.ack_timeout = 50000; own.extra.room.push_failures = 3;
    const auto plan = mesh::planRoomCatchUp(own, posts.data(), posts.size(), mesh::RoomCatchUpMode::KeepNewest, 1);
    assert(plan.pending_post_cancelled == !topic && plan.candidate_cursor == 300);
    assert(mesh::applyRoomCatchUpPlan(own, plan));
    assert(own.extra.room.sync_since == 300 && own.extra.room.push_failures == 0);
    assert(own.extra.room.last_post_timestamp == 550 && own.extra.room.post_quota_used == 9
           && own.extra.room.poll_quota_used == 11);
    assert(own.extra.room.topic_seen_revision == 7 && own.extra.room.topic_failures == 2);
    assert(memcmp(&other, &mesh.acl.clients[1], sizeof(other)) == 0);
    const uint32_t old_ack = 0x55667788;
    assert(mesh.processAck((const uint8_t*)&old_ack) == topic);
    assert(own.extra.room.sync_since == 300); // Actual processAck cannot rewind after skip.
    if (topic) assert(own.extra.room.topic_seen_revision == 8);
    else assert(own.extra.room.pending_ack == 0 && own.extra.room.push_post_timestamp == 0
                && own.extra.room.ack_timeout == 0);
  }
  auto own = client(1, 100); own.extra.room.pending_ack = 55; own.extra.room.push_post_timestamp = 400;
  own.extra.room.ack_timeout = 600;
  const auto later = mesh::planRoomCatchUp(own, posts.data(), posts.size(), mesh::RoomCatchUpMode::KeepNewest, 1);
  assert(!later.pending_post_cancelled && mesh::applyRoomCatchUpPlan(own, later));
  assert(own.extra.room.pending_ack == 55 && own.extra.room.push_post_timestamp == 400
         && own.extra.room.ack_timeout == 600);
  const ClientInfo unchanged = own;
  assert(!mesh::applyRoomCatchUpPlan(own, later) && memcmp(&own, &unchanged, sizeof(own)) == 0);
  auto replaced = client(1, 100); replaced.extra.room.pending_ack = 55;
  replaced.extra.room.push_post_timestamp = 200;
  const auto old_plan = mesh::planRoomCatchUp(replaced, posts.data(), posts.size(), mesh::RoomCatchUpMode::KeepNewest, 1);
  replaced.extra.room.pending_topic_revision = 10; replaced.extra.room.pending_ack = 77;
  assert(mesh::applyRoomCatchUpPlan(replaced, old_plan));
  assert(replaced.extra.room.pending_topic_revision == 10 && replaced.extra.room.pending_ack == 77);
  puts("actual client application clears skipped posts, preserves topics and quotas, and rejects stale ACKs passed");
}

static void dateCases() {
  struct Date { const char* text; uint32_t expected; };
  const Date valid[] = {
    @VALID_DATES@
  };
  for (const auto& date : valid) {
    uint32_t result = 999;
    assert(mesh::parseRoomCatchUpBefore(date.text, result) && result == date.expected);
  }
  const char* invalid[] = {"", "-1", "+1", "4294967296", "1969-12-31", "1970-00-01", "1970-13-01",
    "1970-01-00", "2026-02-29", "2100-02-29", "2000-02-30", "2026-04-31", "2026-1-01",
    "2026-01-01T24:00:00Z", "2026-01-01T00:60:00Z", "2026-01-01T00:00:60Z", "2026-01-01t00:00:00Z",
    "2026-01-01T00:00:00z", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00", "2026-01-01 ",
    "2106-02-07T06:28:16Z", "2106-02-08", "0000-01-01", "9999-01-01", "2026-01-01;erase",
    "18446744073709551616", " 200", "200 ", "1.5"};
  for (const char* text : invalid) {
    uint32_t result = 1234;
    assert(!mesh::parseRoomCatchUpBefore(text, result) && result == 1234);
  }
  uint32_t result = 1234;
  assert(!mesh::parseRoomCatchUpBefore(nullptr, result) && result == 1234);
  assert(mesh::parseRoomCatchUpCount("0", result) && result == 0);
  assert(mesh::parseRoomCatchUpCount("4294967295", result) && result == UINT32_MAX);
  for (const char* text : {"-1", "+1", "1;erase", "1 2", "1x", "1.0", "4294967296", ""}) {
    result = 1234; assert(!mesh::parseRoomCatchUpCount(text, result) && result == 1234);
  }
  puts("strict UTC Gregorian and uint32 parsing match independent epoch references passed");
}

static void randomizedPlans() {
  uint32_t random = 0xABCDEF01;
  for (unsigned iteration = 0; iteration < 5000; ++iteration) {
    auto next = [&random]() { random ^= random << 13; random ^= random >> 17; random ^= random << 5; return random; };
    const uint32_t floor = next() % 300;
    auto value = client(1, floor);
    std::vector<PostInfo> posts; std::vector<uint32_t> unread;
    const size_t count = next() % 33;
    for (size_t index = 0; index < count; ++index) {
      const uint32_t timestamp = (index + 1) * 20;
      const uint8_t author = next() % 3 == 0 ? 1 : 2;
      posts.push_back(post(timestamp, author));
      if (author != 1 && timestamp > floor) unread.push_back(timestamp);
    }
    for (size_t index = 0; index < posts.size(); ++index) std::swap(posts[index], posts[next() % posts.size()]);
    for (auto mode : {mesh::RoomCatchUpMode::Before, mesh::RoomCatchUpMode::SkipNext, mesh::RoomCatchUpMode::KeepNewest}) {
      const uint32_t argument = mode == mesh::RoomCatchUpMode::Before ? next() % 1000 : next() % 40;
      size_t skipped;
      if (mode == mesh::RoomCatchUpMode::Before) skipped = std::lower_bound(unread.begin(), unread.end(), argument) - unread.begin();
      else if (mode == mesh::RoomCatchUpMode::SkipNext) skipped = std::min<size_t>(argument, unread.size());
      else skipped = argument < unread.size() ? unread.size() - argument : 0;
      const uint32_t cursor = skipped ? unread[skipped - 1] : floor;
      validatePlan(value, posts, mode, argument, cursor, skipped, unread.size() - skipped);
    }
  }
  puts("15000 plans match independent ordered reference without mutating retention passed");
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "counts")) orderedCounts();
  else if (!strcmp(argv[1], "bounds")) epochBoundaries();
  else if (!strcmp(argv[1], "pending")) pendingDelivery();
  else if (!strcmp(argv[1], "dates")) dateCases();
  else if (!strcmp(argv[1], "random")) randomizedPlans();
  else assert(false);
}
