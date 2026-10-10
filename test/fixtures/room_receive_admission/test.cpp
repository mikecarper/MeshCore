// Actual receive dispatcher and Companion queue/callbacks are extracted into
// production.inc. Radio, crypto, UI and persistent-store boundaries are mocks.
#include <algorithm>
#include <cassert>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <utility>
#include <vector>
#include "helpers/CompanionFrameLimits.h"
#define PUB_KEY_SIZE 32
#define PRV_KEY_SIZE 64
#define MAX_CONTACTS 2
#define OFFLINE_QUEUE_SIZE 4
#define ADV_TYPE_NONE 0
#define ADV_TYPE_ROOM 3
#define OUT_PATH_UNKNOWN 255
#define PAYLOAD_TYPE_TXT_MSG 2
#define PAYLOAD_TYPE_REQ 0
#define PAYLOAD_TYPE_RESPONSE 1
#define PAYLOAD_TYPE_ACK 3
#define TXT_ACK_DELAY 200
#define CLI_REPLY_DELAY_MILLIS 200
#define SERVER_RESPONSE_DELAY 200
#define COMPANION_FEATURE_TEXT_TERMINAL 0
#define COMPANION_FEATURE_NOTIFICATIONS 1
#define MESH_ENABLE_ONE_KEY_DM 0
#define ONE_KEY_DM_SHARED_OFFLINE_QUEUE 0
#define DISPLAY_CLASS TestDisplay
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#include "constants.inc"
static uint32_t millis() { return 100; }
static bool millisHasNowPassed(unsigned long deadline) { return millis() > deadline; }
namespace mesh {
struct Identity { uint8_t pub_key[PUB_KEY_SIZE] = {}; };
struct Packet {
  bool flood = false;
  uint8_t path[2] = {7, 8}, path_len = 2;
  float getSNR() const { return 10.75f; }
  bool isRouteFlood() const { return flood; }
};
struct Utils {
  static void sha256(uint8_t* out, size_t size, const uint8_t*, size_t,
                     const uint8_t*, size_t) { memset(out, 0x55, size); }
};
}
struct ContactInfo {
  mesh::Identity id;
  uint8_t type = ADV_TYPE_ROOM, out_path_len = 0;
  uint32_t sync_since = 50, lastmod = 99;
  char name[16] = "room";
  const uint8_t* getPath() const { return id.pub_key; }
};
struct Clock {
  uint32_t now = 1200;
  uint32_t getCurrentTime() const { return now; }
  uint32_t getCurrentTimeUnique() { return now++; }
};
struct Rng { void random(uint8_t* out, size_t length) { memset(out, 0, length); } };
struct BaseChatMesh {
  ContactInfo contacts[MAX_CONTACTS];
  int matching_peer_indexes[1] = {0}, num_contacts = MAX_CONTACTS;
  bool mutable_contacts = true;
  Clock clock;
  Rng rng;
  mesh::Identity self_id;
  mesh::Packet response;
  unsigned direct_acks = 0, flood_acks = 0;
  std::vector<uint8_t> ack;
  uint8_t temp_buf[184] = {};
  bool canMutateContacts() const { return mutable_contacts; }
  Clock* getRTCClock() { return &clock; }
  Rng* getRNG() { return &rng; }
  virtual bool onSignedMessageRecv(const ContactInfo&, mesh::Packet*, uint32_t, const uint8_t*, const char*) = 0;
  virtual void onMessageRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*) = 0;
  void onCommandDataRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*) {}
  void onCLICommandRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*, char*) {}
  uint8_t onContactRequest(const ContactInfo&, uint32_t, const uint8_t*, size_t, uint8_t*) { return 0; }
  void onContactResponse(const ContactInfo&, const uint8_t*, size_t) {}
  void handleReturnPathRetry(const ContactInfo&, const uint8_t*, uint8_t) {}
  mesh::Packet* createDatagram(uint8_t, const mesh::Identity&, const uint8_t*, const uint8_t*, size_t) { return &response; }
  mesh::Packet* createPathReturn(const mesh::Identity&, const uint8_t*, const uint8_t*, uint8_t,
      uint8_t type, const uint8_t* bytes, size_t length) {
    if (type == PAYLOAD_TYPE_ACK) { ++flood_acks; ack.assign(bytes, bytes + length); }
    return &response;
  }
  void sendAckTo(const ContactInfo&, const uint8_t* bytes, size_t length = 4) {
    ++direct_acks; ack.assign(bytes, bytes + length);
  }
  void sendFloodScoped(const ContactInfo&, mesh::Packet*, unsigned long = 0) {}
  void sendDirect(mesh::Packet*, const uint8_t*, uint8_t, unsigned long = 0) {}
  void onPeerDataRecv(mesh::Packet*, uint8_t, int, const uint8_t*, uint8_t*, size_t);
};
enum class UIEventType { contactMessage };
struct UI {
  unsigned queue_changes = 0, messages = 0, notifications = 0, overrides = 0;
  void syncMessageQueue(int, int = -1) { ++queue_changes; }
  void setMessageNotificationOverride(bool, bool = false) { ++overrides; }
  template<typename... Args> void newMsg(Args...) { ++messages; }
  void notify(UIEventType) { ++notifications; }
};
struct Notifications {
  unsigned remote = 0, ordinary = 0;
  bool remoteMessage(const uint8_t*, const char*, uint32_t, uint32_t) { ++remote; return false; }
  bool message(const uint8_t*, const uint8_t*, bool, uint32_t) { ++ordinary; return false; }
  bool overridesScreen() const { return false; }
};
struct Serial {
  bool connected = false;
  unsigned writes = 0;
  bool isConnected() const { return connected; }
  bool isReplyRouteAvailable(void*) const { return true; }
  size_t writeFrameToRoute(void*, const uint8_t*, size_t length) { ++writes; return length; }
  size_t writeFrame(const uint8_t*, size_t length) { ++writes; return length; }
};
struct MyMesh : BaseChatMesh {
#include "frame.inc"
  Frame offline_queue[OFFLINE_QUEUE_SIZE] = {};
  int offline_queue_len = 0, offline_queue_head = 0;
  uint8_t out_frame[MAX_FRAME_SIZE + 1] = {};
  uint8_t app_target_ver = 3;
  UI ui, *_ui = &ui;
  Serial serial, *_serial = &serial;
  Notifications _notifications;
  void* private_key_backup_route = nullptr;
  unsigned long private_key_backup_deadline = 1000;
  uint8_t private_key_backup_sender[6] = {};
  char private_key_backup_nonce[17] = {};
  unsigned active = 0;
  std::vector<std::pair<uint32_t, uint32_t>> saved;
  MyMesh() { contacts[0].id.pub_key[0] = 77; contacts[1].id.pub_key[0] = 77; contacts[1].id.pub_key[31] = 1; }
  void markConnectionActive(const ContactInfo&) { ++active; }
  bool scheduleContactWrite(const ContactInfo& from) { saved.emplace_back(from.sync_since, from.lastmod); return true; }
  int getOfflineQueueCapacity() const;
  Frame& offlineQueueFrameAt(int);
  bool addToOfflineQueue(const uint8_t*, int);
  void popOfflineQueue();
  bool queueMessage(const ContactInfo&, uint8_t, mesh::Packet*, uint32_t, const uint8_t*, int, const char*, bool = false, uint32_t = 0);
  bool onSignedMessageRecv(const ContactInfo&, mesh::Packet*, uint32_t, const uint8_t*, const char*) override;
  void onMessageRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*) override;
  void receive(mesh::Packet& packet, uint32_t timestamp, const char* text, uint8_t type = TXT_TYPE_SIGNED_PLAIN, int sender = 0) {
    std::vector<uint8_t> payload(10 + strlen(text), 0);
    memcpy(payload.data(), &timestamp, 4); payload[4] = uint8_t(type << 2);
    const size_t offset = type == TXT_TYPE_SIGNED_PLAIN ? 9 : 5;
    if (offset == 9) { payload[5] = 1; payload[6] = 2; payload[7] = 3; payload[8] = 4; }
    memcpy(payload.data() + offset, text, strlen(text));
    matching_peer_indexes[0] = sender;
    const uint8_t secret[32] = {};
    onPeerDataRecv(&packet, PAYLOAD_TYPE_TXT_MSG, 0, secret, payload.data(), offset + strlen(text));
  }
  void fill(bool channel = false) {
    for (int at = 0; at < OFFLINE_QUEUE_SIZE; ++at) {
      const uint8_t frame[] = {uint8_t(channel && at == 1 ? RESP_CODE_CHANNEL_MSG_RECV : RESP_CODE_CONTACT_MSG_RECV), uint8_t(at)};
      assert(addToOfflineQueue(frame, sizeof(frame)));
    }
  }
};
#include "production.inc"

static unsigned effects(const MyMesh& m) {
  return m.ui.messages + m.ui.notifications + m.ui.overrides + m.serial.writes
      + m._notifications.remote + m._notifications.ordinary + m.active + unsigned(m.saved.size());
}
int main() {
  unsigned cases = 0;
  for (int version : {2, 3}) for (bool flood : {false, true}) for (bool connected : {false, true}) {
    MyMesh m; m.app_target_ver = version; m.serial.connected = connected; m.fill();
    mesh::Packet packet; packet.flood = flood;
    const unsigned before = effects(m);
    m.receive(packet, 100, "retained-before-ack");
    assert(m.contacts[0].sync_since == 50 && m.contacts[0].lastmod == 99);
    assert(m.direct_acks == 0 && m.flood_acks == 0 && m.saved.empty());
    assert(effects(m) == before && m.offline_queue_len == OFFLINE_QUEUE_SIZE);
    m.popOfflineQueue(); m.receive(packet, 100, "retained-before-ack");
    assert(m.contacts[0].sync_since == 100 && m.contacts[0].lastmod == 1200);
    assert(m.saved.size() == 1 && m.saved[0] == std::make_pair(100U, 1200U));
    assert(m.direct_acks == unsigned(!flood) && m.flood_acks == unsigned(flood));
    assert(m.ack.size() == 4 && m.ui.messages == 1 && m._notifications.ordinary == 1);
    const auto& frame = m.offlineQueueFrameAt(OFFLINE_QUEUE_SIZE - 1);
    const size_t timestamp_offset = version >= 3 ? 12 : 9;
    uint32_t stored; memcpy(&stored, frame.buf + timestamp_offset, 4); assert(stored == 100);
    assert(frame.buf[timestamp_offset - 1] == TXT_TYPE_SIGNED_PLAIN);
    assert(memcmp(frame.buf + timestamp_offset + 4, "\x01\x02\x03\x04", 4) == 0);
    // Lost ACK with a now-full queue must not claim retention of another copy.
    const unsigned accepted_effects = effects(m);
    m.receive(packet, 100, "retained-before-ack");
    assert(effects(m) == accepted_effects && m.saved.size() == 1);
    assert(m.direct_acks + m.flood_acks == 1 && m.contacts[0].sync_since == 100);
    // Once the app drains capacity, normal retry admission and ACK resume.
    m.popOfflineQueue(); m.receive(packet, 100, "retained-before-ack");
    assert(m.direct_acks + m.flood_acks == 2 && m.saved.size() == 2);
    assert(m.contacts[0].sync_since == 100 && m.offline_queue_len == OFFLINE_QUEUE_SIZE);
    ++cases;
  }
  {
    MyMesh m; m.fill(true); mesh::Packet packet;
    m.receive(packet, 100, "replace-channel");
    assert(m.offline_queue_len == OFFLINE_QUEUE_SIZE && m.direct_acks == 1);
    assert(m.contacts[0].sync_since == 100 && m.saved.size() == 1); ++cases;
  }
  {
    MyMesh m; mesh::Packet packet; m.mutable_contacts = false;
    m.receive(packet, 100, "immutable-contact");
    assert(m.contacts[0].sync_since == 50 && m.contacts[0].lastmod == 99);
    assert(m.direct_acks == 1 && m.offline_queue_len == 1); ++cases;
  }
  {
    MyMesh m; mesh::Packet packet;
    m.receive(packet, 40, "earlier-topic");
    assert(m.contacts[0].sync_since == 50 && m.direct_acks == 1);
    m.receive(packet, 100, "same-text", TXT_TYPE_SIGNED_PLAIN, 0);
    m.receive(packet, 100, "same-text", TXT_TYPE_SIGNED_PLAIN, 1);
    assert(m.contacts[1].sync_since == 100 && m.offline_queue_len == 3);
    // Distinct full room keys sharing the serialized prefix are both retained.
    ++cases;
  }
  {
    MyMesh m; m.fill(); mesh::Packet packet;
    m.receive(packet, 100, "direct-message", TXT_TYPE_PLAIN);
    assert(m.direct_acks == 1 && m.ack.size() == 6);
    assert(m.contacts[0].sync_since == 50 && m.saved.size() == 1);
    assert(m.ui.messages == 1 && m.offline_queue_len == OFFLINE_QUEUE_SIZE); ++cases;
  }
  for (size_t length : {6U, 7U, 8U}) {
    MyMesh m; mesh::Packet packet; std::vector<uint8_t> malformed(length + 1, 0);
    malformed[4] = TXT_TYPE_SIGNED_PLAIN << 2;
    const uint8_t secret[32] = {};
    m.onPeerDataRecv(&packet, PAYLOAD_TYPE_TXT_MSG, 0, secret, malformed.data(), length);
    assert(m.direct_acks == 0 && m.offline_queue_len == 0 && effects(m) == 0);
    assert(m.contacts[0].sync_since == 50 && m.contacts[0].lastmod == 99); ++cases;
  }
  assert(cases == 15);
  printf("PASS: production room receive admission (%u cases, direct/flood, legacy/V3, both app states)\n", cases);
  return 0;
}
