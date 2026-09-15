#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/CLICommandUtils.h>
#include <helpers/DeferredCliCommand.h>
#include <helpers/RemoteCliReplyCache.h>
#include <helpers/RemoteCliRequest.h>
#include <helpers/ReplayResetCommand.h>
#include <helpers/TempRadioReplyBarrier.h>
#include <helpers/HostCliBridge.h>
#include <array>

#undef MESH_DEBUG_PRINTLN
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#ifndef MESH_ENABLE_HOST_CLI
#define MESH_ENABLE_HOST_CLI 0
#endif
#define PAYLOAD_TYPE_REQ 0
#define PAYLOAD_TYPE_RESPONSE 1
#define PAYLOAD_TYPE_TXT_MSG 2
#define TXT_TYPE_PLAIN 0
#define TXT_TYPE_CLI_DATA 1
#define TXT_TYPE_CLI_COMMAND 2
#define SERVER_RESPONSE_DELAY 1
#define TXT_ACK_DELAY 1

namespace mesh {
struct Packet {
  uint8_t path[MAX_PATH_SIZE] = {};
  uint8_t path_len = 0, radio_profile = 0;
  uint32_t radio_generation = 1;
  bool isRouteFlood() const { return false; }
  uint8_t getPathHashSize() const { return 1; }
};
struct Utils {
  template <typename... Args>
  static void sha256(uint8_t* dest, size_t size, Args...) { memset(dest, 0, size); }
  static void toHex(char* dest, const uint8_t* bytes, size_t size) {
    for (size_t i = 0; i < size; ++i) snprintf(dest + i * 2, 3, "%02x", bytes[i]);
  }
};
struct Console {
  std::vector<std::string> records;
  void printf(const char*, const char* record) { records.emplace_back(record); }
};
static Console console;
inline Console& usbConsolePort() { return console; }
}
struct TransportKey { uint8_t key[32] = {}; };
struct Region { bool isWildcard() const { return true; } };
struct ClientInfo {
  struct { uint8_t pub_key[PUB_KEY_SIZE] = {}; } id;
  uint32_t last_timestamp = 0, last_activity = 0;
  bool admin = true;
  bool region_mgr = false;
  bool isAdmin() const { return admin; }
  bool isRegionMgr() const { return region_mgr; }
  bool isFilterMgr() const { return false; }
};
struct Clock { uint32_t getCurrentTime() const { return 1700000000; } };
struct ReceiveProfileScope {
  template <typename Mesh> ReceiveProfileScope(Mesh&, uint8_t, uint32_t) {}
};
static uint32_t now_millis = 100;
static uint32_t millis() { return now_millis; }

class MyMesh {
public:
  struct {
    ClientInfo clients[3];
    int num_clients = 3;
    int getNumClients() const { return num_clients; }
    ClientInfo* getClientByIdx(int i) { return &clients[i]; }
    const ClientInfo* getClientByIdx(int i) const { return &clients[i]; }
    void remove(int index) {
      ClientInfo* c = &clients[index];
      #include "acl_delete.inc"
    }
  } acl;
  struct {
    int getTransportKeysFor(const Region&, TransportKey*, int) { return 0; }
  } region_map;
  Region* recv_pkt_region = nullptr;
  mesh::DeferredCliCommand deferred_cli_command;
  mesh::RemoteCliReplyCache remote_cli_reply_cache;
  mesh::TempRadioReplyBarrier temp_radio_reply_barrier;
  struct {
    struct Profiles {
      bool pending = false;
      uint32_t generation = 0;
      uint32_t replyMutationGeneration() const { return generation; }
      bool hasReplyMutation() const { return pending; }
      void beginReplyCommand() {}
      void endReplyCommand() {}
    } profiles;
    Profiles& radioProfiles() { return profiles; }
  } _cli;
  uint32_t primary_radio_mutation_generation = 0, radio_reply_deadline = 0;
  bool radio_reply_secondary = false;
  bool deferred_cli_reply_scoped = false;
  TransportKey deferred_cli_reply_scope;
  int matching_peer_indexes[3] = {0, 1, 2};
  uint8_t reply_data[MAX_PACKET_PAYLOAD + 1] = {};
  Clock clock;
  int executions = 0, acknowledgements = 0;
  bool fail_command = false;
  bool host_cli_waiting = false;
  bool host_cli_claimed = false, host_cli_claim_emit = false;
  uint32_t host_cli_deadline = 0, host_cli_claim_emit_at = 0;
  uint64_t host_cli_nonce = 0, host_cli_claim_challenge = 0;
  struct {
    void sign(uint8_t* signature, const uint8_t*, size_t) {
      memset(signature, 0x5a, SIGNATURE_SIZE);
    }
  } self_id;
  struct RNG {
    void random(uint8_t* bytes, size_t count) { memset(bytes, 0x42, count); }
  } rng;
  int delete_during_command = -1;
  // The hardware-side parser callback is replaced by this accepted-operation
  // seam. Receive normalization, fingerprinting, dispatch, cache admission and
  // exact reply-barrier ownership below remain production code.
  enum class Mutation { None, Primary, Secondary } accepted_mutation = Mutation::None;
  bool queue_accepts = true;
  int cancellations = 0;
  mesh::Packet queued_ack;
  std::vector<std::string> replies;
  std::vector<std::array<uint8_t, PUB_KEY_SIZE>> recipients;
  std::vector<std::array<uint8_t, PUB_KEY_SIZE>> secrets;

  MyMesh() { now_millis = 100;mesh::console.records.clear();
             acl.clients[0].id.pub_key[0] = 0x12; acl.clients[1].id.pub_key[0] = 0x77;
             acl.clients[2].id.pub_key[0] = 0x88; }
  Clock* getRTCClock() { return &clock; }
  RNG* getRNG() { return &rng; }
  uint32_t futureMillis(uint32_t delay) const { return millis() + delay; }
  bool millisHasNowPassed(uint32_t deadline) const { return int32_t(millis() - deadline) >= 0; }
  int handleRequest(ClientInfo*, uint32_t, uint8_t*, size_t) { return 0; }
  template <typename... Args> mesh::Packet* createPathReturn(Args...) { return nullptr; }
  template <typename... Args> mesh::Packet* createDatagram(Args...) { return nullptr; }
  template <typename... Args> void sendFloodReply(Args...) {}
  mesh::Packet* createAck(uint32_t) { return nullptr; }
  template <typename... Args> void sendClientReply(Args...) { ++acknowledgements; }
  bool sendRemoteCliReply(ClientInfo* client, const uint8_t* secret, uint8_t, uint32_t,
                         const char* reply, const TransportKey*, mesh::Packet** queued = nullptr) {
    if (!queue_accepts) return false;
    if (queued) *queued = &queued_ack;
    std::array<uint8_t, PUB_KEY_SIZE> key{}, shared{};
    memcpy(key.data(), client->id.pub_key, key.size());
    memcpy(shared.data(), secret, shared.size());
    recipients.push_back(key);secrets.push_back(shared);
    replies.emplace_back(reply); return true;
  }
  void scheduleNormalRadio() {}
  void finishRadioReply(bool delivered) {
    if (!delivered) ++cancellations;
    _cli.radioProfiles().pending = false;
    temp_radio_reply_barrier.clear();
    radio_reply_deadline = 0;
    radio_reply_secondary = false;
  }
  void clearDeferredCliCommand();
  bool completeHostCliRequest(const char*);
  bool handleHostCliSerialReply(const char*, char*);
  void handleCommand(uint32_t, ClientInfo*, char* command, char* reply, int, uint8_t) {
    ++executions;
    #include "normalization.inc"
    if (delete_during_command >= 0) acl.remove(delete_during_command);
    if (!fail_command) {
      if (accepted_mutation == Mutation::Primary) ++primary_radio_mutation_generation;
      else if (accepted_mutation == Mutation::Secondary) {
        ++_cli.radioProfiles().generation;
        _cli.radioProfiles().pending = true;
      }
    }
    strcpy(reply, fail_command ? "Err - simulated failure" : "OK");
  }
  void onPeerDataRecv(mesh::Packet*, uint8_t, int, const uint8_t*, uint8_t*, size_t);
  void processDeferredCliCommand();

  void receive(const char* text, uint32_t timestamp, uint32_t logical_id = 99,
               int sender = 0, bool process = true) {
    uint8_t wire[MAX_PACKET_PAYLOAD + 1] = {};
    uint8_t secret[PUB_KEY_SIZE] = {};
    memcpy(secret, acl.clients[sender].id.pub_key, sizeof(secret));
    memcpy(wire, &timestamp, sizeof(timestamp));
    wire[4] = TXT_TYPE_CLI_COMMAND << 2;
    memcpy(wire + 5, text, strlen(text));
    const size_t size = mesh::RemoteCliRequest::append(wire, sizeof(wire) - 1,
                                                      5, strlen(text), logical_id);
    assert(size != 0);
    mesh::Packet packet;
    onPeerDataRecv(&packet, PAYLOAD_TYPE_TXT_MSG, sender, secret, wire, size);
    if (process) processDeferredCliCommand();
  }
};

#include "production.inc"

#if MESH_ENABLE_HOST_CLI
static void changeHostRequester(MyMesh& value, int change) {
  if (change == 0) value.acl.remove(0);
  if (change == 1) value.acl.remove(1);
  if (change == 2) value.acl.clients[1].id.pub_key[31] = 1;
  if (change == 3) {
    value.acl.clients[1].admin = false;
    value.acl.clients[1].region_mgr = true;
  }
}
static void hostReply(MyMesh& value, const char* text, char* reply) {
  char command[160];
  snprintf(command, sizeof(command), "host.reply %08X %016llX %s",
      unsigned(value.deferred_cli_command.request_id),
      static_cast<unsigned long long>(value.host_cli_nonce), text);
  assert(value.handleHostCliSerialReply(command, reply));
}
static void hostIdentityLifecycle() {
  for (int change : {0, 1, 2, 3}) {
    // Authorization must still be live when an admitted command reaches USB.
    MyMesh value;value.receive("ab|host test", 101, 99, 1, false);
    changeHostRequester(value, change);value.processDeferredCliCommand();
    assert(value.executions == 0);
    assert(mesh::console.records.size() == (change == 0 ? 1U : 0U));
    assert(value.host_cli_waiting == (change == 0));
    if (change == 3) assert(value.replies[0] == "ab|Err - not permitted");
  }
  for (bool change_after_claim_request : {false, true}) for (int change : {0, 1, 2, 3}) {
    MyMesh value;value.receive("ab|host test", 101, 99, 1);
    assert(value.executions == 0 && value.host_cli_waiting);
    assert(mesh::console.records.size() == 1 && value.host_cli_nonce != 0);
    if (!change_after_claim_request) changeHostRequester(value, change);
    char reply[160] = {};
    hostReply(value, "@claim=0000000000000123", reply);
    assert(!strcmp(reply, "OK - host claim accepted"));
    if (change_after_claim_request) changeHostRequester(value, change);
    now_millis += mesh::HostCliBridge::SERVICE_CLAIM_EMIT_DELAY_MILLIS;
    value.processDeferredCliCommand();
    // Provisional serial acceptance is not permission to execute: no signed
    // CLAIMED record may escape after removal or admin-to-manager downgrade.
    assert(mesh::console.records.size() == (change == 0 ? 2U : 1U));
    assert(value.host_cli_claimed == (change == 0));
    if (change != 0) {
      assert(!value.deferred_cli_command.pending && !value.host_cli_waiting);
      assert(!value.host_cli_claim_emit && value.host_cli_nonce == 0);
      assert(value.replies.empty());
    } else {
      assert(mesh::console.records.back().find("HOSTCLI/1 CLAIMED") != std::string::npos);
      hostReply(value, "OK - service reply", reply);
      assert(!strcmp(reply, "OK - host reply accepted"));
      assert(value.replies.size() == 1 && value.replies[0] == "ab|OK - service reply");
      assert(value.recipients[0][0] == 0x77 && value.secrets[0][0] == 0x77);
      assert(!value.deferred_cli_command.pending && !value.host_cli_waiting);
      value.receive("ab|host test", 102, 99, 0);
      assert(mesh::console.records.size() == 2 && value.replies.size() == 2);
    }
  }
  for (int change : {0, 1, 2, 3}) {
    MyMesh value;value.receive("host test", 101, 99, 1);
    char reply[160] = {};
    hostReply(value, "@claim=0000000000000123", reply);
    now_millis += mesh::HostCliBridge::SERVICE_CLAIM_EMIT_DELAY_MILLIS;
    value.processDeferredCliCommand();assert(value.host_cli_claimed);
    changeHostRequester(value, change);
    hostReply(value, "OK - service reply", reply);
    assert(!strcmp(reply, change == 0 ? "OK - host reply accepted" : "Err - host requester is unavailable"));
    assert(value.replies.size() == (change == 0 ? 1U : 0U));
    if (change == 0) assert(value.recipients[0][0] == 0x77 && value.secrets[0][0] == 0x77);
    assert(!value.deferred_cli_command.pending && !value.host_cli_waiting);
    assert(value.host_cli_nonce == 0);
  }
}
#endif

int main() {
#if MESH_ENABLE_HOST_CLI
  hostIdentityLifecycle();
#endif
  // Dispatch must follow full identity across ACL movement before execution,
  // and snapshot the reply destination before a handler compacts the ACL.
  for (const int deleted : {0, 1}) {
    MyMesh value;
    value.delete_during_command = deleted;
    const char* command = "setperm 12 0";
    value.receive(command, 101, 99, 1);
    assert(value.executions == 1 && value.replies.size() == 1);
    assert(value.recipients[0][0] == 0x77 && value.secrets[0][0] == 0x77);
    const auto fp = mesh::RemoteCliReplyCache::fingerprint(command, strlen(command));
    assert(value.remote_cli_reply_cache.matches(value.recipients[0].data(), 99, fp));
    assert(!value.remote_cli_reply_cache.matches(value.acl.clients[1].id.pub_key, 99, fp));
    if (deleted == 0) {
      value.receive(command, 102, 99, 0);
      assert(value.executions == 1 && value.recipients.back()[0] == 0x77);
    }
  }
  for (const bool host_completion : {false, true}) {
    for (const int change : {0, 1, 2, 3}) {
      MyMesh value;
      value.receive("get name", 101, 99, 1, false);
      if (change == 0) value.acl.remove(0);  // same key, different index
      if (change == 1) value.acl.remove(1);  // sender deleted, slot reused
      if (change == 2) value.acl.clients[1].id.pub_key[31] = 1; // prefix collision
      if (change == 3) value.acl.clients[1].admin = false; // role revoked
      if (host_completion) {
        value.host_cli_waiting = true;
        assert(value.completeHostCliRequest("OK - service reply") == (change == 0));
        assert(value.executions == 0);
      } else {
        value.processDeferredCliCommand();
        assert(value.executions == (change == 0 ? 1 : 0));
      }
      assert(value.replies.size() == (change == 0 ? 1U : 0U));
      if (change == 0) {
        assert(value.recipients[0][0] == 0x77 && value.secrets[0][0] == 0x77);
      }
      assert(!value.deferred_cli_command.pending);
    }
  }
  const char* commands[] = {
      "advert", "ADVERT", "advert\r\n", "  ab|ADVERT \t\r\n",
      "setperm 1212121212121212121212121212121212121212121212121212121212121212 3",
      "Ab|Setperm 1212121212121212121212121212121212121212121212121212121212121212 3\n",
  };
  for (const auto command : commands) {
    for (const bool fail : {false, true}) {
      MyMesh value;
      value.fail_command = fail;
      value.receive(command, 101);
      assert(value.executions == 1 && value.replies.size() == 1);
      const auto expected = value.replies.front();
      value.receive(command, 101);  // exact same-timestamp retry
      value.receive(command, 102);  // fresh timestamp, same logical request
      value.receive(command, 100);  // delayed retry may replay result only
      assert(value.executions == 1 && value.replies.size() == 4);
      for (const auto& reply : value.replies) assert(reply == expected);
      assert(value.acl.clients[0].last_timestamp == 102);
      value.receive("reboot", 100);  // uncached stale text remains blocked
      value.receive("reboot", 102);  // equal timestamp cannot run new text
      assert(value.executions == 1 && value.replies.size() == 4);
      value.receive(command, 103, 100);  // genuinely new request
      assert(value.executions == 2 && value.replies.size() == 5);
    }
  }
  {
    MyMesh value;
    value.receive("ADVERT\n", 101, 99, 0, false);
    value.receive("ADVERT\n", 102, 99, 0, false);
    assert(value.executions == 0 && value.replies.empty());
    value.processDeferredCliCommand();
    assert(value.executions == 1 && value.replies.size() == 1);
    value.receive("ADVERT\n", 103);
    assert(value.executions == 1 && value.replies.size() == 2);
  }
  {
    MyMesh value;
    value.receive("ADVERT", 101);
    value.receive("ADVERT", 101, 99, 1); // another authenticated identity
    assert(value.executions == 2 && value.replies.size() == 2);
    value.acl.clients[0].admin = false;
    value.receive("ADVERT", 102); // revoked role cannot recover cached replies
    assert(value.executions == 2 && value.replies.size() == 2);
  }
  // Every accepted primary operation owns its ACK irrespective of the user's
  // spelling. These used to be detected by a literal "tempradio " prefix.
  for (const char* command : {"tempradio 910.525,62.5,7,5,10",
                             "set tempradio 910.525,62.5,7,5,10",
                             "A7|set tempradio 910.525,62.5,7,5,10",
                             "  A7|TEMPRADIO 910.525,62.5,7,5,10\r\n"}) {
    for (const auto mutation : {MyMesh::Mutation::Primary, MyMesh::Mutation::Secondary}) {
      MyMesh value;value.accepted_mutation = mutation;
      value.receive(command, 101);
      assert(value.executions == 1 && value.replies.size() == 1);
      assert(value.temp_radio_reply_barrier.waiting());
      assert(value.temp_radio_reply_barrier.contains(&value.queued_ack));
      assert(value.radio_reply_deadline == millis() + 300000UL);
      assert(value.radio_reply_secondary == (mutation == MyMesh::Mutation::Secondary));
      value.receive(command, 101);value.receive(command, 102);
      assert(value.executions == 1 && value.replies.size() == 1); // no untracked success copy
      value.receive("advert", 103, 100);
      assert(value.executions == 1 && value.replies.size() == 2);
      assert(value.replies.back().find("pending") != std::string::npos);
      assert(value.temp_radio_reply_barrier.contains(&value.queued_ack));
      value.finishRadioReply(true);
      value.receive(command, 104);
      assert(value.executions == 1 && value.replies.size() == 2); // no post-completion replay
    }
  }
  for (const auto mutation : {MyMesh::Mutation::Primary, MyMesh::Mutation::Secondary}) {
    MyMesh value;value.accepted_mutation = mutation;value.queue_accepts = false;
    const char* command = "A7|set tempradio 910.525,62.5,7,5,10";
    value.receive(command, 101);
    assert(value.executions == 1 && value.replies.empty() && value.cancellations == 1);
    assert(!value.temp_radio_reply_barrier.waiting() && !value._cli.radioProfiles().pending);
    value.queue_accepts = true;
    value.receive(command, 102);
    assert(value.executions == 1 && value.replies.empty()); // canceled success is not replayable
  }
  {
    MyMesh value;value._cli.radioProfiles().pending = true;
    value.receive("set tempradio 910.525,62.5,7,5,10", 101);
    assert(value.executions == 0 && value.replies.size() == 1);
    assert(value.replies.back().find("pending") != std::string::npos);
    assert(value._cli.radioProfiles().pending); // do not replace the old accepted operation
  }
  {
    MyMesh value;value.accepted_mutation = MyMesh::Mutation::Primary;value.fail_command = true;
    value.receive("set tempradio invalid", 101);
    value.receive("set tempradio invalid", 102);
    assert(value.executions == 1 && value.replies.size() == 2);
    assert(!value.temp_radio_reply_barrier.waiting()); // validation errors remain recoverable
  }
  puts("CLI wire identity checks passed");
}
