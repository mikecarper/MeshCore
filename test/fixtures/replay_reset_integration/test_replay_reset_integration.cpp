#include <helpers/ReplayResetCommand.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

static constexpr size_t PUB_KEY_SIZE = 32;
static constexpr uint32_t NOW = 1760000000;
static constexpr int CLOCK_SYNC_MESH_SUPPRESS_NONE = 0;
static constexpr int CLOCK_SYNC_RESULT_WITHIN_DRIFT = 5;
static constexpr int CLOCK_SYNC_RESULT_CORRECTED_BACKWARD = 7;
static uint32_t fake_millis = 1000;
uint32_t millis() { return fake_millis; }
static uint32_t clockSyncMinimumValidEpoch() { return 1700000000; }
static uint32_t clockSyncMaximumValidEpoch() { return 1900000000; }

#define CHECK(expression) do { if (!(expression)) { \
  std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #expression); std::exit(1); \
} } while (0)

namespace mesh {
struct Utils {
  static void toHex(char* out, const uint8_t* data, size_t length) {
    const char digits[] = "0123456789ABCDEF";
    for (size_t i = 0; i < length; ++i) {
      out[i * 2] = digits[data[i] >> 4];
      out[i * 2 + 1] = digits[data[i] & 15];
    }
    out[length * 2] = 0;
  }
};
}

struct ClientInfo {
  struct { uint8_t pub_key[PUB_KEY_SIZE]; } id{};
  bool admin = false;
  bool observed_path_pending = true;
  uint32_t last_timestamp = NOW + 10000;
  bool isAdmin() const { return admin; }
};

struct ClientLoginReplayClampResult {
  uint16_t stored_matched, stored_changed, live_matched, live_changed;
};

struct FakeACL {
  std::vector<ClientInfo> clients;
  std::vector<ClientInfo> stored;
  bool fail_persist = false;
  int calls = 0;
  bool selected_all = false;
  std::array<uint8_t, 32> selected_key{};
  uint32_t selected_now = 0;

  int getNumClients() const { return static_cast<int>(clients.size()); }
  ClientInfo* getClientByIdx(int index) { return &clients.at(index); }
  bool clampLoginReplayTimestamps(const uint8_t* key, uint32_t now,
                                 ClientLoginReplayClampResult& result) {
    ++calls;
    selected_all = key == nullptr;
    if (key) std::copy(key, key + 32, selected_key.begin());
    selected_now = now;
    result = {};
    if (fail_persist) return false;
    for (auto& record : stored) {
      if (!key || std::memcmp(key, record.id.pub_key, 32) == 0) {
        ++result.stored_matched;
        if (record.last_timestamp > now) { record.last_timestamp = now; ++result.stored_changed; }
      }
    }
    for (auto& client : clients) {
      if (!key || std::memcmp(key, client.id.pub_key, 32) == 0) {
        ++result.live_matched;
        if (client.last_timestamp > now) { client.last_timestamp = now; ++result.live_changed; }
      }
    }
    return true;
  }
};

struct FakeClock { uint32_t now = NOW; uint32_t getCurrentTime() const { return now; } };
struct FakeRNG {
  uint8_t next = 1;
  int calls = 0;
  void random(uint8_t* output, size_t length) { ++calls; std::memset(output, next++, length); }
};

struct CountingReplyCache {
  bool hit;
  int& calls;
  bool lookup(const uint8_t*, uint32_t, uint32_t, const char**) {
    ++calls;
    return hit;
  }
};

class MyMesh {
public:
  FakeACL acl;
  FakeClock clock;
  FakeRNG rng;
  bool replay_clock_set = true;
  int clock_sync_mesh_suppressed_by = CLOCK_SYNC_MESH_SUPPRESS_NONE;
  int clock_sync_last_result = 0;
  mesh::ReplayResetNonce replay_reset_nonce;
  struct { bool pending = false; int client_index = -1; } deferred_cli_command;
  int mailbox_clears = 0;

  FakeClock* getRTCClock() { return &clock; }
  FakeRNG* getRNG() { return &rng; }
  void clearDeferredCliCommand() { deferred_cli_command.pending = false; ++mailbox_clears; }
  bool handleReplayResetCommand(ClientInfo*, const char*, char*, bool);
};

#include "production_handler.inc"

static ClientInfo client(uint8_t key_byte, bool admin = false) {
  ClientInfo value;
  std::memset(value.id.pub_key, key_byte, sizeof(value.id.pub_key));
  value.admin = admin;
  return value;
}

static std::string key(const ClientInfo& value) {
  char output[65];
  mesh::Utils::toHex(output, value.id.pub_key, 32);
  return output;
}

static MyMesh populated() {
  fake_millis = 1000;
  MyMesh value;
  value.acl.clients = {client(0x12, true), client(0x77), client(0x99)};
  value.acl.stored = value.acl.clients;
  value.acl.stored.push_back(client(0x88));  // Historical identity, no live ACL entry.
  return value;
}

static std::string run(MyMesh& value, ClientInfo* sender, const std::string& command,
                       bool usb = false, bool expected_handled = true) {
  std::array<char, 168> guarded;
  guarded.fill('#');
  guarded[4] = 0;
  CHECK(value.handleReplayResetCommand(sender, command.c_str(), guarded.data() + 4, usb)
        == expected_handled);
  for (size_t i = 0; i < 4; ++i) CHECK(guarded[i] == '#');
  for (size_t i = 164; i < guarded.size(); ++i) CHECK(guarded[i] == '#');
  return std::string(guarded.data() + 4);
}

static std::string request(const ClientInfo& target) { return "replay reset " + key(target); }
static bool contains(const std::string& text, const char* part) { return text.find(part) != std::string::npos; }
static std::string challenge(MyMesh& value, ClientInfo* admin, const ClientInfo& target,
                              std::string* full_reply = nullptr) {
  const std::string response = run(value, admin, request(target));
  if (full_reply) *full_reply = response;
  const auto where = response.find("confirm: ");
  CHECK(where != std::string::npos);
  return response.substr(where + std::strlen("confirm: "));
}

static void authorization() {
  auto value = populated();
  const auto command = request(value.acl.clients[1]);
  CHECK(contains(run(value, nullptr, command), "requires USB or LoRa admin"));
  CHECK(contains(run(value, &value.acl.clients[1], command), "requires USB or LoRa admin"));
  CHECK(contains(run(value, &value.acl.clients[1], command, true), "requires USB or LoRa admin"));
  CHECK(value.acl.calls == 0 && value.rng.calls == 0);
  CHECK(run(value, nullptr, "get name", false, false).empty());
}

static void usb_exact_and_all() {
  auto value = populated();
  CHECK(contains(run(value, nullptr, request(value.acl.clients[1]), true), "OK - clamped"));
  CHECK(value.acl.calls == 1 && !value.acl.selected_all && value.acl.selected_now == NOW);
  CHECK(value.acl.clients[1].last_timestamp == NOW);
  CHECK(value.acl.clients[0].last_timestamp > NOW);
  CHECK(!value.acl.clients[1].observed_path_pending && value.acl.clients[0].observed_path_pending);
  CHECK(contains(run(value, nullptr, "replay reset all CONFIRM", true), "stored=3 live=2"));
  CHECK(value.acl.selected_all && value.acl.stored[3].last_timestamp == NOW);
  CHECK(value.acl.clients.size() == 3 && value.acl.stored.size() == 4);
}

static void all_usb_only() {
  auto value = populated();
  CHECK(contains(run(value, &value.acl.clients[0], "replay reset all CONFIRM"), "USB-only"));
  CHECK(contains(run(value, nullptr, "replay reset all CONFIRM"), "requires USB"));
  CHECK(contains(run(value, nullptr, "replay reset all", true), "64-hex"));
  CHECK(value.acl.calls == 0 && value.rng.calls == 0);
}

static void strict_full_key() {
  auto value = populated();
  const std::string full = key(value.acl.clients[1]);
  for (size_t size : {size_t(2), size_t(4), size_t(6), size_t(63)}) {
    CHECK(contains(run(value, nullptr, "replay reset " + full.substr(0, size), true), "64-hex"));
  }
  CHECK(contains(run(value, nullptr, "replay reset " + full + "00", true), "64-hex"));
  CHECK(contains(run(value, nullptr, "replay reset " + full + " trailing", true), "64-hex"));
  CHECK(value.acl.calls == 0);
}

static void clock_must_be_observed() {
  auto value = populated();
  value.replay_clock_set = false;
  CHECK(contains(run(value, nullptr, request(value.acl.clients[1]), true), "set/sync"));
  CHECK(value.acl.calls == 0);
  value.clock_sync_last_result = CLOCK_SYNC_RESULT_WITHIN_DRIFT;
  CHECK(contains(run(value, nullptr, request(value.acl.clients[1]), true), "OK"));
  value.clock_sync_last_result = 0;
  value.clock_sync_mesh_suppressed_by = 1;
  CHECK(contains(run(value, nullptr, request(value.acl.clients[2]), true), "OK"));
}

static void clock_must_be_sane() {
  auto value = populated();
  for (uint32_t now : {0U, clockSyncMinimumValidEpoch() - 1, clockSyncMaximumValidEpoch() + 1}) {
    value.clock.now = now;
    CHECK(contains(run(value, nullptr, request(value.acl.clients[1]), true), "set/sync"));
  }
  CHECK(value.acl.calls == 0 && value.rng.calls == 0);
}

static void remote_challenge_then_commit() {
  auto value = populated();
  auto* admin = &value.acl.clients[0];
  const auto confirm = challenge(value, admin, value.acl.clients[1]);
  CHECK(value.acl.calls == 0 && value.acl.clients[1].last_timestamp > NOW);
  CHECK(challenge(value, admin, value.acl.clients[1]) == confirm);
  CHECK(contains(run(value, admin, confirm), "OK - clamped"));
  CHECK(value.acl.calls == 1 && value.acl.clients[1].last_timestamp == NOW);
}

static void nonce_binds_sender_and_target() {
  auto value = populated();
  auto* admin = &value.acl.clients[0];
  value.acl.clients[2].admin = true;
  const auto confirm = challenge(value, admin, value.acl.clients[1]);
  CHECK(contains(run(value, &value.acl.clients[2], confirm), "expired/used"));
  std::string swapped = confirm;
  swapped.replace(std::strlen("replay reset "), 64, key(value.acl.clients[2]));
  CHECK(contains(run(value, admin, swapped), "expired/used"));
  CHECK(value.acl.calls == 0);
  CHECK(contains(run(value, admin, confirm), "OK"));
}

static void failure_is_not_live_and_token_is_consumed() {
  auto value = populated();
  auto* admin = &value.acl.clients[0];
  const auto confirm = challenge(value, admin, value.acl.clients[1]);
  value.acl.fail_persist = true;
  value.deferred_cli_command = {true, 1};
  CHECK(contains(run(value, admin, confirm), "no live timestamps changed"));
  CHECK(value.acl.calls == 1 && value.acl.clients[1].last_timestamp > NOW);
  CHECK(value.acl.clients[1].observed_path_pending && value.deferred_cli_command.pending);
  value.acl.fail_persist = false;
  CHECK(contains(run(value, admin, confirm), "expired/used"));
  CHECK(value.acl.calls == 1);
}

static void self_reset_replay_never_reexecutes_or_raises_floor() {
  auto value = populated();
  auto* admin = &value.acl.clients[0];
  const auto confirm = challenge(value, admin, *admin);
  CHECK(contains(run(value, admin, confirm), "OK"));
  CHECK(admin->last_timestamp == NOW && value.acl.calls == 1);
  apply_actual_receive_guard(admin, confirm.c_str(), NOW + 100000);
  CHECK(admin->last_timestamp == NOW);
  CHECK(contains(run(value, admin, confirm), "expired/used"));
  CHECK(value.acl.calls == 1 && admin->last_timestamp == NOW);
}

static void every_replay_family_preserves_receive_floor() {
  auto value = populated();
  auto* sender = &value.acl.clients[0];
  sender->last_timestamp = NOW;
  std::vector<std::string> commands = {"replay", "replay anything", "replay.reset", "REPLAY RESET 12",
      "replay reset all", "replay reset all CONFIRM", request(*sender),
      "aa| REPLAY RESET " + key(*sender) + " " + std::string(32, 'a'),
      "\tRePlAy\treset\tbad", "replay reset " + key(*sender) + " bad"};
  for (const auto& command : commands) {
    apply_actual_receive_guard(sender, command.c_str(), NOW + 10000);
    CHECK(sender->last_timestamp == NOW);
  }
  apply_actual_receive_guard(sender, "get name", NOW + 10);
  CHECK(sender->last_timestamp == NOW + 10);
  apply_actual_receive_guard(sender, "replayable", NOW + 20);
  CHECK(sender->last_timestamp == NOW + 20);
}

static void pending_usb_affected_and_unrelated_mailboxes() {
  auto value = populated();
  value.deferred_cli_command = {true, 2};
  run(value, nullptr, request(value.acl.clients[1]), true);
  CHECK(value.deferred_cli_command.pending && value.mailbox_clears == 0);
  value.deferred_cli_command = {true, 1};
  run(value, nullptr, request(value.acl.clients[1]), true);
  CHECK(!value.deferred_cli_command.pending && value.mailbox_clears == 1);
  value.deferred_cli_command = {true, -1};
  run(value, nullptr, "replay reset all CONFIRM", true);
  CHECK(!value.deferred_cli_command.pending && value.mailbox_clears == 2);
}

static void remote_keeps_its_executing_mailbox() {
  auto value = populated();
  auto* admin = &value.acl.clients[0];
  const auto confirm = challenge(value, admin, *admin);
  value.deferred_cli_command = {true, 0};
  CHECK(contains(run(value, admin, confirm), "OK"));
  CHECK(value.deferred_cli_command.pending && value.mailbox_clears == 0);
}

static void expiry_clock_change_and_usb_token_rejected() {
  auto value = populated();
  auto* admin = &value.acl.clients[0];
  auto confirm = challenge(value, admin, value.acl.clients[1]);
  CHECK(contains(run(value, nullptr, confirm, true), "no token"));
  fake_millis += mesh::ReplayResetNonce::LIFETIME_MILLIS;
  value.clock.now += 300;
  CHECK(contains(run(value, admin, confirm), "expired/used"));
  confirm = challenge(value, admin, value.acl.clients[1]);
  value.clock.now += 60;
  CHECK(contains(run(value, admin, confirm), "expired/used"));
  CHECK(value.acl.calls == 0);
}

static void missing_identity_does_not_create_records() {
  auto value = populated();
  const auto unknown = client(0xee);
  CHECK(contains(run(value, nullptr, request(unknown), true), "nothing created"));
  CHECK(value.acl.clients.size() == 3 && value.acl.stored.size() == 4);
  CHECK(value.acl.calls == 1);
  for (const auto& entry : value.acl.clients) CHECK(entry.last_timestamp > NOW);
}

static void confirmation_only_window_preserves_last_resend() {
  auto value = populated();
  auto* admin = &value.acl.clients[0];
  const auto prepare = request(value.acl.clients[1]);
  std::string first_reply;
  const auto original = challenge(value, admin, value.acl.clients[1], &first_reply);
  CHECK(contains(first_reply, "ttl=300s; confirm:"));
  const auto token = original.substr(original.rfind(' ') + 1);
  fake_millis = 1000 + 119999;
  value.clock.now = NOW + 119;
  std::string last_resend_reply;
  CHECK(challenge(value, admin, value.acl.clients[1], &last_resend_reply) == original);
  CHECK(contains(last_resend_reply, "ttl=180s; confirm:"));

  for (uint32_t age : {120000U, 180000U, 299999U}) {
    fake_millis = 1000 + age;
    value.clock.now = NOW + age / 1000;
    const auto response = run(value, admin, prepare);
    CHECK(contains(response, "confirmation-only"));
    CHECK(!contains(response, "confirm: "));
    CHECK(response.find(token) == std::string::npos);
    CHECK(response.find(key(value.acl.clients[1])) == std::string::npos);
    CHECK(value.acl.calls == 0);
    CHECK(contains(run(value, admin, request(value.acl.clients[2])), "another replay confirmation"));
    CHECK(contains(run(value, &value.acl.clients[1], original), "requires USB or LoRa admin"));
  }
  // This is exactly 180000ms after the last allowed token resend at119999ms.
  CHECK(contains(run(value, admin, original), "OK - clamped"));
  CHECK(value.acl.calls == 1 && value.acl.clients[1].last_timestamp == NOW + 299);
  CHECK(contains(run(value, admin, original), "expired/used"));
  CHECK(value.acl.calls == 1);
}

static void repeated_requests_cannot_extend_original_deadline() {
  auto value = populated();
  auto* admin = &value.acl.clients[0];
  const auto original = challenge(value, admin, value.acl.clients[1]);
  for (uint32_t age : {119999U, 120000U, 200000U, 299999U}) {
    fake_millis = 1000 + age;
    value.clock.now = NOW + age / 1000;
    run(value, admin, request(value.acl.clients[1]));
  }
  fake_millis = 1000 + 300000;
  value.clock.now = NOW + 300;
  CHECK(contains(run(value, admin, original), "expired/used"));
  CHECK(value.acl.calls == 0);
  const auto next = challenge(value, admin, value.acl.clients[1]);
  CHECK(next != original);
  CHECK(contains(run(value, admin, original), "expired/used"));
  CHECK(contains(run(value, admin, next), "OK"));
  CHECK(value.acl.calls == 1);
}

static void preparation_never_uses_cached_token_reply() {
  auto value = populated();
  const auto prepare = request(value.acl.clients[1]);
  const auto confirm = prepare + " " + std::string(32, 'a');
  for (const auto& command : {prepare, "aa| " + prepare, "\tREPLAY RESET " + key(value.acl.clients[1])}) {
    int calls = 0;
    CHECK(!apply_actual_receive_cache_gate(command.c_str(), true, calls));
    CHECK(calls == 0);
  }
  for (const auto& command : {confirm, std::string("get name"), std::string("replay reset 12")}) {
    int calls = 0;
    CHECK(apply_actual_receive_cache_gate(command.c_str(), true, calls));
    CHECK(calls == 1);
    CHECK(!apply_actual_receive_cache_gate(command.c_str(), false, calls));
    CHECK(calls == 2);
  }
}

int main() {
#define RUN(test) do { test(); std::printf("PASS: %s\n", #test); } while (0)
  RUN(authorization);
  RUN(usb_exact_and_all);
  RUN(all_usb_only);
  RUN(strict_full_key);
  RUN(clock_must_be_observed);
  RUN(clock_must_be_sane);
  RUN(remote_challenge_then_commit);
  RUN(nonce_binds_sender_and_target);
  RUN(failure_is_not_live_and_token_is_consumed);
  RUN(self_reset_replay_never_reexecutes_or_raises_floor);
  RUN(every_replay_family_preserves_receive_floor);
  RUN(pending_usb_affected_and_unrelated_mailboxes);
  RUN(remote_keeps_its_executing_mailbox);
  RUN(expiry_clock_change_and_usb_token_rejected);
  RUN(missing_identity_does_not_create_records);
  RUN(confirmation_only_window_preserves_last_resend);
  RUN(repeated_requests_cannot_extend_original_deadline);
  RUN(preparation_never_uses_cached_token_reply);
  std::puts("18 replay-reset integration checks passed");
}
