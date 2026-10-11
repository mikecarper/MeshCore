#include "CompanionFeatures.h"
#include "production_lost_include.inc"
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <stdint.h>
#include <string>
#include <vector>

#define PUB_KEY_SIZE 32
#define MAX_HASH_SIZE 8
#define MAX_TEXT_LEN 150
#define MAX_FRAME_SIZE 172
#define EXPECTED_ACK_TABLE_SIZE 8
#define TXT_TYPE_PLAIN 0
#define TXT_TYPE_CLI_COMMAND 1
#define TXT_TYPE_SIGNED_PLAIN 2
#define ADV_TYPE_CHAT 1
#define ADV_TYPE_ROOM 3
#define OUT_PATH_UNKNOWN 255
#define MSG_SEND_FAILED 0
#define MSG_SEND_SENT_FLOOD 1
#define MSG_SEND_SENT_DIRECT 2
#define PAYLOAD_TYPE_TXT_MSG 2
#define PAYLOAD_TYPE_ACK 3
#define PAYLOAD_TYPE_RESPONSE 1
#define ONE_KEY_DM_ID_SIZE 8
#include "production_constants.inc"

namespace mesh {
struct Identity { uint8_t pub_key[PUB_KEY_SIZE] = {}; };
struct GroupChannel { uint8_t secret[16] = {}; };
struct Packet {
  static bool isValidPathLen(uint8_t value) { return value != 254; }
  uint8_t path_len = 0;
  uint8_t path[8] = {};
  uint8_t payload[5 + MAX_TEXT_LEN + 1] = {};
  uint8_t recipient[PUB_KEY_SIZE] = {};
  bool flood = false, used = false, do_not_retransmit = false;
  uint8_t hash = 0;
  void calculatePacketHash(uint8_t* out) { memset(out, hash, MAX_HASH_SIZE); }
  bool isRouteFlood() const { return flood; }
  bool isRouteDirect() const { return !flood; }
  int getSNR() const { return 1; }
  int getPathHashCount() const { return 0; }
  void markDoNotRetransmit() { do_not_retransmit = true; }
};
namespace Utils {
static void sha256(uint8_t* out, size_t count, const uint8_t* a, size_t alen,
                   const uint8_t* b, size_t blen) {
  // Hashing/encryption are the transport boundary; production composes the
  // actual private payload and retains its independently matched ACK here.
  uint32_t value = 2166136261U;
  for (size_t i = 0; i < alen; ++i) value = (value ^ a[i]) * 16777619U;
  for (size_t i = 0; i < blen; ++i) value = (value ^ b[i]) * 16777619U;
  for (size_t i = 0; i < count; ++i) out[i] = uint8_t(value >> ((i % 4) * 8));
}
}
}

struct ContactInfo {
  mesh::Identity id;
  uint8_t type = ADV_TYPE_CHAT, out_path_len = 0, flags = 0;
  bool transient = false;
  uint32_t lastmod = 0;
  uint8_t path[8] = {};
  const uint8_t* getPath() const { return path; }
  const uint8_t* getSharedSecret(const mesh::Identity&) const { return id.pub_key; }
  bool isRemoteCLIAllowed() const { return flags & 0x10; }
  bool setPath(const uint8_t* value, uint8_t length) {
    if (!mesh::Packet::isValidPathLen(length) || length > sizeof(path)) return false;
    memcpy(path, value, length); out_path_len = length; return true;
  }
};
struct Clock {
  uint32_t now = 0, sequence = 100;
  uint32_t getMillis() const { return now; }
  uint32_t getCurrentTimeUnique() { return sequence++; }
  uint32_t getCurrentTime() const { return sequence; }
};
struct Manager { int free = 16; int getFreeCount() const { return free; } };
struct BaseSerialInterface {
  unsigned writes = 0;
  bool isConnected() const { return false; }
  bool isReplyRouteAvailable(BaseSerialInterface*) const { return true; }
  size_t writeFrameToRoute(BaseSerialInterface*, const uint8_t*, size_t n) { ++writes; return n; }
  size_t writeFrame(const uint8_t*, size_t n) { ++writes; return n; }
};
struct UI { void setMessageNotificationOverride(bool) {} };
struct Terminal {
  void print(const char*) {}
  template<typename... T> void printf(const char*, T...) {}
};
struct TerminalDisplay { bool shouldShowChannel(bool) const { return false; } };
struct ChannelDetails { char name[32] = {}; };
static const uint8_t EMERGENCY_CHANNEL_SECRET[16] = {1};
struct Random { int nextInt(int, int) { return 0; } };
struct LostSnapshot { uint8_t lost_reply = 0; uint32_t capabilities = 0; };
struct WebConfigServer { static constexpr uint32_t CAP_LOST_REPLY = 1UL << 18; };

class MyMesh {
public:
  struct Prefs { uint8_t lost_reply = 0; } _prefs;
  Clock clock, *_ms = &clock;
  Manager manager, *_mgr = &manager;
  BaseSerialInterface serial, *_serial = &serial;
  UI ui, *_ui = &ui;
  mesh::Identity self_id;
#include "production_lost_state.inc"
#include "production_entry.inc"
  AckTableEntry expected_ack_table[EXPECTED_ACK_TABLE_SIZE] = {};
  unsigned long txt_send_timeout = 0, next_ack_expiry = 0;
  bool has_next_ack_expiry = false;
  uint8_t next_ack_idx = 3;
  std::vector<ContactInfo> contacts;
  mesh::Packet packets[16];
  mesh::Packet incoming;
  unsigned queued = 0, dropped = 0, sends = 0, saves = 0, cancellations = 0;
  unsigned normal_marks = 0, contact_writes = 0, replacements = 0;
  unsigned path_updates = 0, path_retries = 0, contact_responses = 0;
  bool queue_available = true, radio_available = true, allocation_available = true;
  bool save_available = true, delivered = false;
  std::string last_answer;
  uint8_t last_recipient[PUB_KEY_SIZE] = {};
  uint8_t out_frame[MAX_FRAME_SIZE] = {};
  uint8_t app_target_ver = 3;
  uint16_t emergency_client_repeats[EMERGENCY_CLIENT_REPEAT_TABLE_SIZE] = {};
  uint8_t emergency_client_repeat_next = 0;
  mesh::Packet* emergency_client_repeat_packet = NULL;
  uint16_t emergency_client_repeat_key = 0;
  unsigned long emergency_client_repeat_send_at = 0;
  Random random;
  Terminal terminal;
  TerminalDisplay _terminal_display;
  uint8_t held_dm_count = 0;
  unsigned offline_queue_len = 0;
  struct Held { uint8_t sender_key[32] = {}, id[ONE_KEY_DM_ID_SIZE] = {}; } held_dms[1];

  MyMesh() { contacts.reserve(8); }
  ContactInfo& contact(uint8_t key, uint8_t suffix = 0) {
    contacts.emplace_back();
    contacts.back().id.pub_key[0] = key;
    contacts.back().id.pub_key[31] = suffix;
    return contacts.back();
  }
  void receive(ContactInfo& from, uint32_t timestamp, const char* question = "Am I lost?") {
    onMessageRecv(from, &incoming, timestamp, question);
  }
  bool isTransientContact(const ContactInfo& value) const { return value.transient; }
  ContactInfo* lookupPersistentContactByPubKey(const uint8_t* key, int n) {
    assert(n == PUB_KEY_SIZE);
    for (ContactInfo& c : contacts) if (!c.transient && memcmp(c.id.pub_key, key, n) == 0) return &c;
    return NULL;
  }
  void markConnectionActive(const ContactInfo&) { ++normal_marks; }
  void scheduleContactWrite(const ContactInfo&) { ++contact_writes; }
  bool queueMessage(const ContactInfo&, uint8_t, mesh::Packet*, uint32_t,
                    const uint8_t*, size_t, const char*) {
    if (queue_available) { ++queued; return true; } ++dropped; return false;
  }
  bool addToOfflineQueue(const uint8_t*, int) { ++queued; return true; }
  bool savePrefs() { ++saves; return save_available; }
  void makeOneKeyDMId(uint8_t* id, uint32_t timestamp, const char*) { memset(id, uint8_t(timestamp), ONE_KEY_DM_ID_SIZE); }
  bool wasDeliveredOneKeyDM(const uint8_t*, const uint8_t*) { return delivered; }
  void removeHeldOneKeyDM(uint8_t) { held_dm_count = 0; }
  unsigned getOfflineQueueCapacity() const { return 1; }
  void rememberDeliveredOneKeyDM(const uint8_t*, const uint8_t*) {}
  bool hasTerminalOutput() const { return false; }
  Terminal& terminalOutput() { return terminal; }
  void rememberOneKeyAck(ContactInfo&) {}
  uint8_t findChannelIdx(const mesh::GroupChannel&) { return 0; }
  bool getChannel(int, ChannelDetails&) { return false; }
  uint16_t emergencyClientRepeatKey(mesh::Packet*) { return 0; }
  bool hasEmergencyClientRepeat(uint16_t) { return false; }
  mesh::Packet* obtainNewPacket() { return NULL; }
  Random* getRNG() { return &random; }
  Clock* getRTCClock() { return &clock; }
  unsigned long futureMillis(int millis) const { return uint32_t(clock.now + millis); }
  uint32_t getTransmitAirtime(mesh::Packet*) { return 10; }
  uint32_t calcFloodTimeoutMillisFor(uint32_t) { return 6000; }
  uint32_t calcDirectTimeoutMillisFor(uint32_t, uint8_t) { return 3000; }
  bool checkConnectionsAck(const uint8_t*, ContactInfo*& peer) { peer = NULL; return false; }
  bool canMutateContacts() const { return true; }
  void onContactPathUpdated(const ContactInfo&) { ++path_updates; }
  void onContactResponse(const ContactInfo&, uint8_t*, uint8_t) { ++contact_responses; }
  void handleReturnPathRetry(const ContactInfo&, const uint8_t*, uint8_t) { ++path_retries; }
  bool hasActiveRetries(const uint8_t*) const { return false; }
  void cancelActiveRetries(const uint8_t*) { ++cancellations; }
  void replaceActiveRetries(mesh::Packet*, const uint8_t*) { ++replacements; }
  void replaceActiveMessageRetries(mesh::Packet*, const uint8_t*, uint32_t) { ++replacements; }
  mesh::Packet* createDatagram(uint8_t type, const mesh::Identity& to,
                              const uint8_t*, const uint8_t* data, int len) {
    assert(type == PAYLOAD_TYPE_TXT_MSG);
    if (!allocation_available) return NULL;
    for (mesh::Packet& p : packets) if (!p.used) {
      p.used = true; --manager.free;
      memset(p.payload, 0, sizeof(p.payload)); memcpy(p.payload, data, len);
      memcpy(p.recipient, to.pub_key, 32); p.hash = uint8_t(clock.sequence);
      return &p;
    }
    return NULL;
  }
  void releasePacket(mesh::Packet* p) { if (p->used) { p->used = false; ++manager.free; } }
  bool sendFloodScoped(const ContactInfo&, mesh::Packet* p, uint32_t) { return send(p); }
  bool sendDirect(mesh::Packet* p, const uint8_t*, uint8_t, uint32_t) { return send(p); }
  bool send(mesh::Packet* p) {
    if (radio_available) {
      ++sends; last_answer = reinterpret_cast<const char*>(p->payload + 5);
      memcpy(last_recipient, p->recipient, 32);
      assert(p->payload[4] == TXT_TYPE_PLAIN);
    }
    releasePacket(p); return radio_available;
  }
  void clearExpectedAck(AckTableEntry&, bool = true);
  void expireExpectedAcks();
  bool processAck(const uint8_t*, ContactInfo*&);
  void onAckRecv(mesh::Packet*, uint32_t);
  bool onContactPathRecv(ContactInfo&, uint8_t*, uint8_t, uint8_t*, uint8_t,
                         uint8_t, uint8_t*, uint8_t);
  void snapshot(LostSnapshot&);
  void onMessageRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*);
  void onCLICommandRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*, char*);
  bool onSignedMessageRecv(const ContactInfo&, mesh::Packet*, uint32_t, const uint8_t*, const char*);
  void onChannelMessageRecv(const mesh::GroupChannel&, mesh::Packet*, uint32_t, const char*);
  bool handleDirectCommand(const char*, char*, size_t);
  bool handleCommand(const char*, uint32_t, char*);
  mesh::Packet* composeMsgPacket(const ContactInfo&, uint32_t, uint8_t, const char*, uint32_t&);
  int sendMessage(const ContactInfo&, uint32_t, uint8_t, const char*, uint32_t&, uint32_t&,
                  uint8_t* = NULL, const uint8_t* = NULL, const uint8_t* = NULL,
                  uint32_t = 0);
  int sendMessageDetached(const ContactInfo&, uint32_t, const char*, uint32_t&,
                          uint32_t&, uint8_t*);
};

#include "production_methods.inc"

#if MESH_ENABLE_LOST_REPLY
static void matchingAndOrdinaryInbox() {
  for (const char* text : {"am i lost", "Am I lost?", "\t AM I LOST? \r\n", "\vam i lost\f"}) {
    MyMesh m; ContactInfo& peer = m.contact(1); m._prefs.lost_reply = 2;
    m.receive(peer, 7, text);
    assert(m.sends == 1 && m.last_answer == "Yes" && m.queued == 1);
    assert(m.last_recipient[0] == 1 && m.serial.writes == 0);
    assert(m.has_next_ack_expiry && m.expected_ack_table[0].reply_route == NULL);
  }
  for (const char* text : {"", "a", "am i los", "am i lost??", "am i lost ?", "am  i lost?",
                           "please am i lost?", "am i lost? yes", "am i lost\n?", "Yes", "No",
                           "!mail send 0123 Am I lost?", "set lost.reply yes", "am i lost\xc2\xa0"}) {
    MyMesh m; auto& peer = m.contact(1); m._prefs.lost_reply = 2;
    m.receive(peer, 7, text); assert(m.sends == 0 && m.queued == 1);
  }
  for (uint8_t setting : {uint8_t(0), uint8_t(255)}) {
    MyMesh m; auto& peer = m.contact(1); m._prefs.lost_reply = setting;
    m.receive(peer, 7); assert(m.sends == 0 && m.queued == 1);
  }
  MyMesh full; auto& peer = full.contact(1); full._prefs.lost_reply = 1;
  LostSnapshot saved; full.snapshot(saved);
  assert(saved.lost_reply == 1 && (saved.capabilities & WebConfigServer::CAP_LOST_REPLY));
  full.queue_available = false; full.offline_queue_len = full.getOfflineQueueCapacity();
  for (uint32_t question = 1; question <= 8; ++question) {
    full.clock.now = question * 60000; full.receive(peer, question);
  }
  assert(full.sends == 8 && full.last_answer == "No" && full.dropped == 8);
#if MESH_ENABLE_ONE_KEY_DM
  MyMesh held; auto& h = held.contact(2); held._prefs.lost_reply = 2;
  held.queue_available = false; held.offline_queue_len = 1; held.held_dm_count = 1;
  memcpy(held.held_dms[0].sender_key, h.id.pub_key, 32);
  held.makeOneKeyDMId(held.held_dms[0].id, 7, "Am I lost?");
  held.receive(h, 7); assert(held.sends == 1);
  held.delivered = true; held.clock.now = 60000; held.receive(h, 7); assert(held.sends == 1);
#endif
}

static void admissionAndLoops() {
  MyMesh m; auto& peer = m.contact(1); m._prefs.lost_reply = 2;
  for (uint8_t type : {uint8_t(0), uint8_t(2), uint8_t(ADV_TYPE_ROOM)}) {
    peer.type = type; m.receive(peer, 7); assert(m.sends == 0);
  }
  peer.type = ADV_TYPE_CHAT; peer.transient = true; m.receive(peer, 7); assert(m.sends == 0);
  peer.transient = false;
  ContactInfo unknown; unknown.id.pub_key[0] = 2; m.receive(unknown, 7); assert(m.sends == 0);
  ContactInfo collision = peer; collision.id.pub_key[31] = 1;
  m.receive(collision, 7); assert(m.sends == 0);
  uint8_t prefix[4] = {}; m.onSignedMessageRecv(peer, &m.incoming, 7, prefix, "Am I lost?");
  mesh::GroupChannel channel; m.onChannelMessageRecv(channel, &m.incoming, 7, "Am I lost?");
  char reply[160] = {}; m.onCLICommandRecv(peer, &m.incoming, 7, "Am I lost?", reply);
  peer.flags = 0x10; reply[0] = 0;
  m.onCLICommandRecv(peer, &m.incoming, 7, "Am I lost?", reply);
  assert(m.sends == 0);
  m.receive(peer, 7); assert(m.sends == 1);
  m.receive(peer, 8, "Yes"); m.receive(peer, 9, "No"); assert(m.sends == 1);
}

static void retriesCooldownAndFullKeys() {
  MyMesh m; auto& one = m.contact(1); auto& same_prefix = m.contact(1, 1);
  m._prefs.lost_reply = 2;
  m.receive(one, 7); assert(m.sends == 1);
  for (uint32_t time : {uint32_t(1), uint32_t(59999), uint32_t(60000), uint32_t(3600000)}) {
    m.clock.now = time; m.receive(one, 7); assert(m.sends == 1);
  }
  m.clock.now = 59999; m.receive(one, 8); assert(m.sends == 1);
  m.clock.now = 60000; m.receive(one, 8); assert(m.sends == 2);
  m.clock.now = 69999; m.receive(same_prefix, 8); assert(m.sends == 2);
  m.clock.now = 70000; m.receive(same_prefix, 8); assert(m.sends == 3 && m.last_recipient[31] == 1);
  m.clock.now = 120000; m.receive(one, 9); assert(m.sends == 4);
  using mesh::companion::LostReplyLimiter;
  LostReplyLimiter limiter;
  uint8_t keys[5][32] = {};
  for (size_t i = 0; i < 5; ++i) keys[i][31] = uint8_t(i);
  for (size_t i = 0; i < 4; ++i) {
    uint32_t now = uint32_t(i * 10000);
    assert(limiter.canReply(keys[i], 1, now)); limiter.rememberReply(keys[i], 1, now);
  }
  assert(!limiter.canReply(keys[4], 1, 40000));
  assert(!limiter.canReply(keys[0], 2, 59999));
  assert(limiter.canReply(keys[4], 1, 60000));
  LostReplyLimiter wrap;
  const uint32_t start = 0xfffffff0U;
  wrap.rememberReply(keys[0], 1, start);
  assert(!wrap.canReply(keys[0], 2, uint32_t(start + 59999U)));
  assert(wrap.canReply(keys[0], 2, uint32_t(start + 60000U)));
  assert(!wrap.canReply(keys[1], 1, uint32_t(start + 9999U)));
  assert(wrap.canReply(keys[1], 1, uint32_t(start + 10000U)));
}

static void queueReserveUserOwnershipAndAck() {
  MyMesh normal; auto& ordinary = normal.contact(3);
  normal.clock.now = 100; normal.txt_send_timeout = 42;
  uint32_t expected = 0, timeout = 0;
  assert(normal.sendMessage(ordinary, 1, 0, "ordinary", expected, timeout) != MSG_SEND_FAILED);
  assert(expected != 0 && normal.txt_send_timeout == normal.futureMillis(timeout));
  MyMesh m; auto& peer = m.contact(1); m._prefs.lost_reply = 2;
  auto& user = m.expected_ack_table[0];
  user.ack = 10; user.expires_at = 123456; user.msg_sent = 20;
  user.contact = &peer; user.reply_route = &m.serial;
  user.retry_key[0] = 31; user.text_fingerprint[0] = 47;
  const auto snapshot = user;
  m.txt_send_timeout = 654321;
  m.receive(peer, 7);
  assert(m.sends == 1 && m.txt_send_timeout == 654321 && m.next_ack_idx == 3);
  assert(memcmp(&user, &snapshot, sizeof(user)) == 0 && m.replacements == 0);
  auto& automatic = m.expected_ack_table[1];
  assert(automatic.ack != 0 && automatic.contact == &peer && automatic.reply_route == NULL);
  uint32_t ack = automatic.ack; ContactInfo* matched = NULL;
  assert(m.processAck(reinterpret_cast<const uint8_t*>(&ack), matched));
  assert(matched == &peer && automatic.ack == 0 && m.cancellations == 1);
  assert(memcmp(&user, &snapshot, sizeof(user)) == 0 && m.serial.writes == 0);
  assert(!m.processAck(reinterpret_cast<const uint8_t*>(&ack), matched));
  for (auto& entry : m.expected_ack_table) { entry.ack = 1; entry.expires_at = 123456; }
  m.clock.now = 60000; m.receive(peer, 8); assert(m.sends == 1);
  m.expected_ack_table[1].ack = 0;
  for (int available : {0, 1, 2}) {
    m.manager.free = available; m.receive(peer, 8); assert(m.sends == 1);
  }
  m.manager.free = 3; m.receive(peer, 8); assert(m.sends == 2);
  assert(m.txt_send_timeout == 654321 && m.replacements == 0);
  for (int failure = 0; failure < 3; ++failure) {
    MyMesh f; auto& p = f.contact(1); f._prefs.lost_reply = 1; f.txt_send_timeout = 1234;
    if (failure == 0) f.allocation_available = false;
    if (failure == 1) f.radio_available = false;
    if (failure == 2) p.out_path_len = 254;
    f.receive(p, 7); assert(f.sends == 0 && f.txt_send_timeout == 1234);
    assert(f.manager.free == 16 && f.expected_ack_table[0].ack == 0);
    f.allocation_available = true; f.radio_available = true; p.out_path_len = OUT_PATH_UNKNOWN;
    f.receive(p, 7); assert(f.sends == 1 && f.last_answer == "No");
  }
}

static void automaticAckReceivePreservesUserExpiry() {
  for (bool encoded_path : {false, true}) {
    for (uint32_t user_deadline : {uint32_t(1000), uint32_t(9000)}) {
      MyMesh m; auto& owner = m.contact(1); auto& user_peer = m.contact(2);
      m.clock.now = 100; m._prefs.lost_reply = 2;
      auto& user = m.expected_ack_table[0];
      user.ack = 10; user.msg_sent = 20; user.expires_at = user_deadline;
      user.contact = &user_peer; user.reply_route = &m.serial;
      user.retry_key[0] = 31; user.text_fingerprint[0] = 47;
      const auto snapshot = user;
      m.txt_send_timeout = user_deadline;
      m.receive(owner, 7);
      auto& automatic = m.expected_ack_table[1];
      const uint32_t automatic_ack = automatic.ack;
      assert(automatic_ack != 0 && automatic_ack != user.ack);
      assert(m.txt_send_timeout == user_deadline);
      assert(m.next_ack_expiry == std::min<uint32_t>(user_deadline, automatic.expires_at));

      mesh::Packet ack_packet; ack_packet.flood = true;
      uint8_t ack_bytes[4], path[2] = {3, 4};
      memcpy(ack_bytes, &automatic_ack, sizeof(ack_bytes));
      if (encoded_path) {
        assert(m.onContactPathRecv(owner, path, 2, path, 2,
                                    PAYLOAD_TYPE_ACK, ack_bytes, sizeof(ack_bytes)));
        assert(m.path_updates == 1 && owner.out_path_len == 2);
      } else {
        m.onAckRecv(&ack_packet, automatic_ack);
        assert(ack_packet.do_not_retransmit && m.path_retries == 1);
      }
      // BaseChatMesh keeps its existing shared-timer ACK behavior. Companion
      // owns each delivery's deadline separately and services it from loop().
      assert(m.txt_send_timeout == 0 && automatic.ack == 0);
      assert(memcmp(&user, &snapshot, sizeof(user)) == 0);
      assert(m.has_next_ack_expiry && m.next_ack_expiry == user_deadline);
      assert(m.cancellations == 1 && m.serial.writes == 0 && m.next_ack_idx == 3);

      // A late duplicate cannot clear a newer shared timer or the user entry.
      m.txt_send_timeout = 99999;
      if (encoded_path) {
        assert(m.onContactPathRecv(owner, path, 2, path, 2,
                                    PAYLOAD_TYPE_ACK, ack_bytes, sizeof(ack_bytes)));
      } else {
        ack_packet.do_not_retransmit = false;
        m.onAckRecv(&ack_packet, automatic_ack);
        assert(!ack_packet.do_not_retransmit && m.path_retries == 1);
      }
      assert(m.txt_send_timeout == 99999 && m.cancellations == 1);
      assert(memcmp(&user, &snapshot, sizeof(user)) == 0);
      assert(m.has_next_ack_expiry && m.next_ack_expiry == user_deadline);

      // Expiry still works through the actual Companion service without the
      // compatibility timer being involved, including after the automatic ACK.
      m.txt_send_timeout = 0;
      m.clock.now = user_deadline - 1; m.expireExpectedAcks();
      assert(memcmp(&user, &snapshot, sizeof(user)) == 0);
      assert(m.has_next_ack_expiry && m.next_ack_expiry == user_deadline);
      m.clock.now = user_deadline; m.expireExpectedAcks();
      assert(user.ack == 0 && !m.has_next_ack_expiry && m.next_ack_expiry == 0);
      assert(m.serial.writes == 0 && m.cancellations == 1);
    }
  }
}

static void localPolicyAndRemoteBoundary() {
  MyMesh m; char reply[160] = {};
  assert(m.handleCommand("get lost.reply", 0, reply) && std::string(reply) == "> off");
  for (const char* value : {"no", "yes", "off"}) {
    const std::string command = std::string("set lost.reply ") + value;
    assert(m.handleCommand(command.c_str(), 0, reply));
    assert(std::string(reply) == std::string("> lost.reply ") + value);
  }
  assert(m.saves == 3);
  for (const char* invalid : {"YES", "on", "yes now", "", "1"}) {
    const std::string command = std::string("set lost.reply ") + invalid;
    assert(m.handleCommand(command.c_str(), 0, reply));
    assert(std::string(reply).find("Error:") == 0 && m._prefs.lost_reply == 0 && m.saves == 3);
  }
  m.save_available = false;
  assert(m.handleCommand("set lost.reply yes", 0, reply));
  assert(m._prefs.lost_reply == 0 && std::string(reply).find("Error:") == 0);
  m.save_available = true;
  auto& peer = m.contact(1); peer.flags = 0x10;
  for (uint32_t timestamp : {uint32_t(0), uint32_t(1), uint32_t(0xffffffffU)}) {
    reply[0] = 0;
    m.onCLICommandRecv(peer, &m.incoming, timestamp, "set lost.reply yes", reply);
    assert(m._prefs.lost_reply == 0 && m.saves == 4);
    reply[0] = 0;
    m.onCLICommandRecv(peer, &m.incoming, timestamp, "get lost.reply", reply);
    assert(std::string(reply) == "Unknown command");
  }
  assert(m.handleCommand("set lost.reply yes", 0, reply) && m._prefs.lost_reply == 2);
  m.receive(peer, 7, "set lost.reply no"); assert(m._prefs.lost_reply == 2 && m.saves == 5 && m.sends == 0);
}
#else
// The actual gated MyMesh header fragment supplies neither runtime state nor
// a responder method in this build. The callbacks still admit normal text.
static void excludedFeature() {
  MyMesh m; auto& peer = m.contact(1); m._prefs.lost_reply = 2;
  for (uint32_t poll = 0; poll < 8; ++poll) {
    m.clock.now = poll * 60000; m.receive(peer, poll);
  }
  assert(m.sends == 0 && m.queued == 8 && m.normal_marks == 8);
  char reply[160] = {};
  for (const char* command : {"get lost.reply", "set lost.reply yes", "set lost.reply no", "set lost.reply off"}) {
    assert(!m.handleCommand(command, 0, reply));
    assert(m.saves == 0 && m._prefs.lost_reply == 2 && reply[0] == 0);
  }
  LostSnapshot hidden; m.snapshot(hidden);
  assert(hidden.lost_reply == 0 && !(hidden.capabilities & WebConfigServer::CAP_LOST_REPLY));
}
#endif

int main() {
#if MESH_ENABLE_LOST_REPLY
  matchingAndOrdinaryInbox(); admissionAndLoops(); retriesCooldownAndFullKeys();
  queueReserveUserOwnershipAndAck(); automaticAckReceivePreservesUserExpiry();
  localPolicyAndRemoteBoundary();
#else
  excludedFeature();
#endif
  std::puts("PASS: Companion lost reply production paths");
}
