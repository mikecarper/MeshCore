#include <Arduino.h>
#include <helpers/ArduinoSerialInterface.h>
#include <helpers/MultiSerialInterface.h>
#include <helpers/ContactInfo.h>
#include <helpers/ContactListOrder.h>
#include <helpers/AdvertDataHelpers.h>
#include <helpers/TxtDataHelpers.h>
#include <helpers/PersistentStoreFormat.h>
#include <algorithm>
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <new>
#include <set>
#include <string>
#include <vector>

#define MAX_ANON_CONTACTS 8
#define MAX_CONTACTS 20
#define MAX_CONNECTIONS 16
#define CONTACT_PSRAM_FALLBACK_TOTAL_SLOTS (MAX_ANON_CONTACTS + 16)
#define ADV_TYPE_NONE 0
#define ADV_TYPE_CHAT 1

#include "production_constants.inc"
#include "production_types.inc"
#include "production_stream.inc"
#include "production_constructors.inc"

#if MESH_CONTACT_CACHE
namespace mesh {
ContactPathStorage& contactPathStorage() {
  static ContactPathPool<128, 16> pool;
  return pool;
}
}
#endif

static unsigned allocations = 0, fail_allocation = 0;
static std::set<void*> outstanding_allocations;
static constexpr int MALLOC_CAP_SPIRAM = 1, MALLOC_CAP_8BIT = 2;
static void* heap_caps_calloc(size_t count, size_t size, int) {
  ++allocations;
  if (allocations == fail_allocation) return nullptr;
  void* result = calloc(count, size);
  if (result != nullptr) outstanding_allocations.insert(result);
  return result;
}
static void heap_caps_free(void* pointer) {
  assert(outstanding_allocations.erase(pointer) == 1);
  free(pointer);
}

struct Clock { unsigned long getMillis() const { return millis(); } };
struct RTC { uint32_t getCurrentTime() const { return 100; } };
struct Store {
  bool delete_ok = true, incomplete = false;
  unsigned deletions = 0;
  bool deleteBlobByKey(const uint8_t*, size_t) { ++deletions; return delete_ok; }
  bool hasIncompleteContactLoad() const { return incomplete; }
};
struct Terminal {
  std::string text;
  void print(const char* value) { text += value; }
  void printf(const char* format, ...) {
    char bytes[160];
    va_list args;
    va_start(args, format);
    vsnprintf(bytes, sizeof(bytes), format, args);
    va_end(args);
    text += bytes;
  }
};

static ContactInfo makeContact(uint8_t key, uint8_t type = ADV_TYPE_CHAT) {
  ContactInfo result;
  memset(result.id.pub_key, key, sizeof(result.id.pub_key));
  result.type = type;
  result.lastmod = key + 100;
  result.out_path_len = OUT_PATH_UNKNOWN;
  snprintf(result.name, sizeof(result.name), "peer-%u", key);
  if (key <= 3) {
    const uint8_t path[] = {key};
    assert(result.setPath(path, 1));
  }
  return result;
}

class BaseChatMesh {
public:
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
  ContactInfo contacts_fallback[MAX_ANON_CONTACTS + 16];
  ContactIndex sort_array_fallback[MAX_ANON_CONTACTS + 16] = {};
  ContactInfo* contacts = contacts_fallback;
  ContactIndex* sort_array = sort_array_fallback;
  int contact_capacity = MAX_ANON_CONTACTS + 16;
#else
  ContactInfo contacts[MAX_ANON_CONTACTS + MAX_CONTACTS];
#endif
  int num_contacts = 0;
  uint32_t contact_table_revision = 0;
  uint32_t txt_send_timeout = 0;
  Clock clock;
  Clock* _ms = &clock;
  RTC rtc;
  ConnectionInfo connections[MAX_CONNECTIONS] = {};
#ifdef MAX_GROUP_CHANNELS
  ChannelDetails channels[MAX_GROUP_CHANNELS];
  int num_channels;
#endif
  mesh::Packet* _pendingLoopback = nullptr;
  uint8_t temp_buf[MAX_TRANS_UNIT] = {};
  bool auto_add = true, overwrite = true, can_mutate = true;
  uint8_t max_hops = 0;
  unsigned discoveries = 0, full_notices = 0, path_updates = 0;
  std::vector<uint8_t> path_notices;
  std::vector<uint8_t> path_notice_lengths;
  std::vector<std::vector<uint8_t>> path_notice_bytes;

  BaseChatMesh() {
#include "production_base_constructor.inc"
  }
  virtual ~BaseChatMesh() {
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
    if (contacts != contacts_fallback) {
      for (int i = 0; i < contact_capacity; ++i) contacts[i].~ContactInfo();
      heap_caps_free(contacts);
      heap_caps_free(sort_array);
    }
#endif
  }
  virtual void onContactReferenceChanged(const ContactInfo*, ContactInfo*) {}
  virtual bool onContactOverwrite(const ContactInfo&) = 0;
  virtual bool processAck(const uint8_t*, ContactInfo*&) = 0;
  bool shouldOverwriteWhenFull() const { return overwrite; }
  bool shouldAutoAddContactType(uint8_t) const { return auto_add; }
  bool canMutateContacts() const { return can_mutate; }
  uint8_t getAutoAddMaxHops() const { return max_hops; }
  RTC* getRTCClock() { return &rtc; }
  bool putBlobByKey(const uint8_t*, int, const uint8_t*, int) { return true; }
  void onDiscoveredContact(ContactInfo&, bool, uint8_t, const uint8_t*) { ++discoveries; }
  void onContactsFull() { ++full_notices; }
  void onContactPathUpdated(const ContactInfo&) { ++path_updates; }
  void onContactResponse(const ContactInfo&, const uint8_t*, uint8_t) {}
  void handleReturnPathRetry(const ContactInfo& peer, const uint8_t* path, uint8_t path_len) {
    uint8_t bytes[MAX_PATH_SIZE];
    const size_t size = mesh::Packet::writePath(bytes, path, path_len);
    path_notices.push_back(peer.id.pub_key[0]);
    path_notice_lengths.push_back(path_len);
    path_notice_bytes.emplace_back(bytes, bytes + size);
  }
  void resetContacts();
  static void resetContactValue(ContactInfo&);
  bool initializeContactStorage();
  ContactInfo* allocateContactSlot(bool = false);
  void populateContactFromAdvert(ContactInfo&, const mesh::Identity&, const AdvertDataParser&, uint32_t);
  void onAdvertRecv(mesh::Packet*, const mesh::Identity&, uint32_t, const uint8_t*, size_t);
  ContactInfo* lookupContactByPubKey(const uint8_t*, int);
  ContactInfo* lookupTransientContactByPubKey(const uint8_t*, int);
  ContactInfo* lookupPersistentContactByPubKey(const uint8_t*, int);
  bool isTransientContact(const ContactInfo&) const;
  bool clearTransientContact(ContactInfo&, ContactInfo* = nullptr);
  bool addContact(const ContactInfo&);
  bool removeContact(ContactInfo&);
  bool checkConnectionsAck(const uint8_t*, ContactInfo*&);
  bool onContactPathRecv(ContactInfo&, uint8_t*, uint8_t, uint8_t*, uint8_t, uint8_t, uint8_t*, uint8_t);
  void onAckRecv(mesh::Packet*, uint32_t);
  bool millisHasNowPassed(unsigned long) const;
  unsigned long futureMillis(int) const;
};

class MyMesh : public BaseChatMesh {
public:
  BaseSerialInterface* _serial = nullptr;
  Store store;
  Store* _store = &store;
  Terminal terminal;
  uint8_t out_frame[MAX_FRAME_SIZE] = {}, cmd_frame[MAX_FRAME_SIZE] = {};
#include "production_entry.inc"
  AckTableEntry expected_ack_table[EXPECTED_ACK_TABLE_SIZE] = {};
  bool has_next_ack_expiry = false;
  unsigned long next_ack_expiry = 0;
  bool retry_active[256] = {};
  unsigned cancellations = 0, references_changed = 0;
  std::vector<uint8_t> learned;
  bool release_ok = true, restore_ok = true, schedule_ok = true;
  unsigned releases = 0, restores = 0, schedules = 0;
  unsigned ok_replies = 0, error_replies = 0;
  uint8_t last_error = 0;

  bool cancelActiveRetries(const uint8_t* key) {
    ++cancellations; retry_active[key[0]] = false; return true;
  }
  bool hasActiveRetries(const uint8_t* key) const { return retry_active[key[0]]; }
  void rememberOneKeyAck(ContactInfo& peer) { learned.push_back(peer.id.pub_key[0]); }
  bool hasTerminalOutput() const { return true; }
  Terminal& terminalOutput() { return terminal; }
  bool scheduleContactWriteAfterRelease(const ContactInfo&, uint16_t& slot) {
    ++releases; slot = 7; return release_ok;
  }
  bool restoreContactWriteAfterRelease(const ContactInfo&, uint16_t) { ++restores; return restore_ok; }
  bool scheduleContactWrite(const ContactInfo&) { ++schedules; return schedule_ok; }
  void updateGpsTelemetryPolicy() {}
  void writeOKFrame() { ++ok_replies; }
  void writeErrFrame(uint8_t error) { ++error_replies; last_error = error; }
  void onContactReferenceChanged(const ContactInfo* old, ContactInfo* replacement) override {
    ++references_changed;
    if (replacement != nullptr) assert(old->id.matches(replacement->id));
    applyContactReferenceChanged(old, replacement);
  }
  void applyContactReferenceChanged(const ContactInfo*, ContactInfo*);
  bool onContactOverwrite(const ContactInfo&) override;
  bool processAck(const uint8_t*, ContactInfo*&) override;
  void clearExpectedAck(AckTableEntry&, bool = true);
  void expireExpectedAcks();
  bool updateContactFromFrame(ContactInfo&, uint32_t&, const uint8_t*, int);
  void handleContactCommand(int);
  void acceptVerifiedOneKey(const mesh::Identity&);
  void service();
};

#include "production_functions.inc"

struct Fixture {
  BufferStream usb_stream, other_stream;
  ArduinoSerialInterface usb, other;
  MultiSerialInterface manager;
  MyMesh node;

  Fixture() {
    g_mock_millis = 100;
    allocations = fail_allocation = 0;
    assert(outstanding_allocations.empty());
    assert(node.references_changed == 0);
    assert(node.num_contacts == MAX_ANON_CONTACTS && node.contact_table_revision == 1);
    assert(node.txt_send_timeout == 0 && node._pendingLoopback == nullptr);
    for (const auto& connection : node.connections) {
      assert(connection.keep_alive_millis == 0 && connection.expected_ack == 0);
      assert(connection.next_ping == 0 && connection.last_activity == 0);
    }
#ifdef MAX_GROUP_CHANNELS
    assert(node.num_channels == 0);
    for (const auto& channel : node.channels) {
      for (uint8_t byte : channel.channel.secret) assert(byte == 0);
      for (char byte : channel.name) assert(byte == 0);
      assert(channel.channel.tx_radio == mesh::RADIO_TX_AUTO);
    }
#endif
    for (int i = 0; i < MAX_ANON_CONTACTS; ++i) {
      assert(node.contacts[i].type == ADV_TYPE_NONE && node.contacts[i].id.pub_key[0] == 0);
#if defined(NRF52_PLATFORM)
      assert(node.contacts[i].storage_slot == mesh::storage::CONTACT_SLOT_NONE);
#endif
    }
    usb.begin(usb_stream); usb.enableFlowControl(true);
    other.begin(other_stream); other.enableFlowControl(true);
    assert(manager.addInterface(InterfaceType::USB, &usb));
    assert(manager.addInterface(InterfaceType::HardwareSerial, &other));
    manager.enable();
    node._serial = &manager;
  }
  ContactInfo* add(uint8_t key) {
    const ContactInfo contact = makeContact(key);
    assert(node.addContact(contact));
    return node.lookupPersistentContactByPubKey(contact.id.pub_key, PUB_KEY_SIZE);
  }
  ContactInfo* transient(uint8_t key) {
    const ContactInfo contact = makeContact(key, ADV_TYPE_NONE);
    assert(node.addContact(contact));
    return node.lookupTransientContactByPubKey(contact.id.pub_key, PUB_KEY_SIZE);
  }
  void fill() {
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
    const int capacity = node.contact_capacity;
#else
    const int capacity = MAX_ANON_CONTACTS + MAX_CONTACTS;
#endif
    while (node.num_contacts < capacity) add(uint8_t(node.num_contacts + 10));
  }
  MyMesh::AckTableEntry& pending(ContactInfo* peer, unsigned slot = 0,
                                uint32_t ack = 0x11223344, BaseSerialInterface* route = nullptr) {
    auto& entry = node.expected_ack_table[slot];
    entry.ack = ack; entry.msg_sent = 50; entry.expires_at = 10000;
    entry.message_timestamp = 1; entry.contact = peer;
    entry.reply_route = route == nullptr ? &usb : route;
    entry.retry_key[0] = uint8_t(slot + 1); node.retry_active[slot + 1] = true;
    entry.text_fingerprint[0] = 0xaa;
    return entry;
  }
  void blockUsb() {
    usb_stream.write_capacity = 0;
    const uint8_t required = 0;
    for (unsigned i = 0; i < 4; ++i) assert(manager.writeFrameToRoute(&usb, &required, 1) == 1);
  }
  bool ack(uint32_t crc, ContactInfo*& peer) {
    return node.processAck(reinterpret_cast<const uint8_t*>(&crc), peer);
  }
  void received(uint32_t crc, uint8_t expected_peer = 0, uint8_t route = ROUTE_TYPE_FLOOD) {
    mesh::Packet packet;
    packet.header = route | (PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT);
    packet.setPathHashSizeAndCount(2, 2);
    const uint8_t path[] = {0xa1, 0xa2, 0xb1, 0xb2};
    memcpy(packet.path, path, sizeof(path));
    const auto notices = node.path_notices.size();
    node.txt_send_timeout = 9000;
    node.onAckRecv(&packet, crc);
    assert(packet.isMarkedDoNotRetransmit() && node.txt_send_timeout == 0);
    if (expected_peer != 0) {
      assert(node.path_notices.size() == notices + 1 && node.path_notices.back() == expected_peer);
      assert(node.path_notice_lengths.back() == packet.path_len);
      assert(node.path_notice_bytes.back() == std::vector<uint8_t>(path, path + sizeof(path)));
    } else assert(node.path_notices.size() == notices);
  }
  void drain() {
    usb_stream.write_capacity = other_stream.write_capacity = 4096;
    for (unsigned i = 0; i < 8; ++i) manager.loop();
  }
  void command(uint8_t key, uint8_t code = CMD_ADD_UPDATE_CONTACT) {
    memset(node.cmd_frame, 0, sizeof(node.cmd_frame));
    node.cmd_frame[0] = code;
    memset(node.cmd_frame + 1, key, PUB_KEY_SIZE);
    if (code == CMD_REMOVE_CONTACT) { node.handleContactCommand(1 + PUB_KEY_SIZE); return; }
    node.cmd_frame[1 + PUB_KEY_SIZE] = ADV_TYPE_CHAT;
    node.cmd_frame[1 + PUB_KEY_SIZE + 2] = OUT_PATH_UNKNOWN;
    memcpy(node.cmd_frame + 1 + PUB_KEY_SIZE + 3 + MAX_PATH_SIZE, "updated", 8);
    node.handleContactCommand(CONTACT_UPDATE_FRAME_MIN_LEN);
  }
  void advert(uint8_t key, uint8_t hops = 0) {
    ContactInfo contact = makeContact(key);
    uint8_t data[MAX_ADVERT_DATA_SIZE];
    const size_t size = AdvertDataBuilder(ADV_TYPE_CHAT, "new-advert").encodeTo(data);
    mesh::Packet packet;
    packet.header = ROUTE_TYPE_FLOOD; packet.path_len = hops;
    node.onAdvertRecv(&packet, contact.id, 100, data, size);
  }
};

static void unchanged(const MyMesh::AckTableEntry& current, const MyMesh::AckTableEntry& previous) {
  assert(current.ack == previous.ack && current.msg_sent == previous.msg_sent);
  assert(current.expires_at == previous.expires_at && current.confirmed == previous.confirmed);
  assert(current.message_timestamp == previous.message_timestamp && current.reply_route == previous.reply_route);
  assert(memcmp(current.retry_key, previous.retry_key, sizeof(current.retry_key)) == 0);
  assert(memcmp(current.text_fingerprint, previous.text_fingerprint, sizeof(current.text_fingerprint)) == 0);
#if COMPANION_FEATURE_TEXT_TERMINAL
  assert(current.terminal_origin == previous.terminal_origin);
#endif
}
static unsigned confirmations(const std::vector<uint8_t>& bytes, uint32_t ack = 0x11223344) {
  unsigned count = 0;
  size_t offset = 0;
  while (offset < bytes.size()) {
    assert(offset + 3 <= bytes.size() && bytes[offset] == '>');
    const size_t size = bytes[offset + 1] | (size_t(bytes[offset + 2]) << 8);
    offset += 3;
    assert(offset + size <= bytes.size());
    if (bytes[offset] == PUSH_CODE_SEND_CONFIRMED) {
      uint32_t crc, rtt;
      assert(size == 9);
      memcpy(&crc, bytes.data() + offset + 1, 4);
      memcpy(&rtt, bytes.data() + offset + 5, 4);
      if (crc == ack) { assert(rtt == 50); ++count; }
    }
    offset += size;
  }
  return count;
}

int main() {
  unsigned checks = 0;
  {
    Fixture f;
    // Execute the real constructor body without a live reference callback,
    // then prove a later runtime reset uses the hook and advances revision.
    assert(f.node.references_changed == 0 && f.node.contact_table_revision == 1);
    f.node.resetContacts();
    assert(f.node.references_changed == MAX_ANON_CONTACTS);
    assert(f.node.num_contacts == MAX_ANON_CONTACTS && f.node.contact_table_revision == 2);
    ++checks;
  }
  {
    Fixture f;
    auto* a = f.add(1); auto* b = f.add(2); auto* c = f.add(3);
    auto& first = f.pending(b); auto& second = f.pending(c, 1, 0x55667788);
    auto& same_peer = f.pending(b, 2, 0x88776655);
    const auto prior = first;
    assert(f.node.removeContact(*a));
    assert(first.contact == &f.node.contacts[MAX_ANON_CONTACTS]);
    assert(second.contact == &f.node.contacts[MAX_ANON_CONTACTS + 1]);
    assert(same_peer.contact == first.contact);
    unchanged(first, prior);
    assert(first.contact->id.pub_key[0] == 2 && second.contact->id.pub_key[0] == 3);
    uint8_t path[MAX_PATH_SIZE];
    assert(first.contact->copyPathTo(path) && path[0] == 2);
    assert(second.contact->copyPathTo(path) && path[0] == 3);
    f.received(first.ack, 2); f.received(second.ack, 3); f.received(same_peer.ack, 2);
    if (MESH_ENABLE_ONE_KEY_DM) assert((f.node.learned == std::vector<uint8_t>{2, 3, 2}));
    else assert(f.node.learned.empty());
    ++checks;
  }
  {
    Fixture f; auto* peer = f.add(1); f.blockUsb();
    auto& entry = f.pending(peer); const auto prior = entry;
    assert(f.node.removeContact(*peer) && entry.contact == nullptr);
    unchanged(entry, prior); assert(f.node.retry_active[1]);
    f.received(entry.ack);
    assert(entry.confirmed && entry.reply_route == &f.usb && entry.msg_sent == 50);
    assert(entry.expires_at == 10100 && f.node.cancellations == 1 && f.node.learned.empty());
    f.drain(); g_mock_millis = 110; f.node.service(); f.drain();
    assert(entry.ack == 0 && confirmations(f.usb_stream.output) == 1);
    assert(f.other_stream.output.empty());
    ++checks;
  }
  {
    Fixture f; f.add(1); auto* tail = f.add(2);
    auto& entry = f.pending(tail); const auto prior = entry;
    assert(f.node.removeContact(*tail) && entry.contact == nullptr);
    unchanged(entry, prior);
    f.add(3); assert(entry.contact == nullptr);
    f.received(entry.ack); assert(f.node.learned.empty());
    ++checks;
  }
  {
    Fixture f; auto* peer = f.add(1); auto* anon = f.transient(9);
    auto& entry = f.pending(peer); auto& transient_entry = f.pending(anon, 1, 0x55667788);
    const auto prior = entry;
    f.node.resetContacts();
    assert(entry.contact == nullptr && transient_entry.contact == nullptr);
    unchanged(entry, prior); f.add(2); assert(entry.contact == nullptr);
    f.received(entry.ack); assert(f.node.learned.empty());
    ++checks;
  }
  {
    Fixture f; auto* anon = f.transient(9); auto& entry = f.pending(anon);
    const auto prior = entry;
    ContactInfo external = makeContact(10);
    assert(!f.node.clearTransientContact(external) && entry.contact == anon);
    assert(f.node.clearTransientContact(*anon) && entry.contact == nullptr);
    unchanged(entry, prior); f.received(entry.ack);
    assert(f.node.learned.empty());
    ++checks;
  }
  {
    Fixture f; auto* anon = f.transient(9); auto& entry = f.pending(anon);
    const auto prior = entry;
    // Every unused transient has lastmod zero; force this exact slot to be the
    // oldest reusable slot so stale references cannot attach to another key.
    for (int i = 0; i < MAX_ANON_CONTACTS; ++i) f.node.contacts[i].lastmod = 1000;
    anon->lastmod = 1;
    assert(f.transient(10) == anon && entry.contact == nullptr);
    unchanged(entry, prior); f.received(entry.ack); assert(f.node.learned.empty());
    ++checks;
  }
  for (unsigned failure = 0; failure < 2; ++failure) {
    Fixture f; auto* peer = f.add(1); f.fill();
    auto& entry = f.pending(peer); const auto prior = entry;
    const auto notifications = f.node.references_changed;
    if (failure == 0) f.node.release_ok = false;
    else f.node.store.delete_ok = false;
    assert(!f.node.addContact(makeContact(99)));
    assert(entry.contact == peer && peer->id.pub_key[0] == 1);
    unchanged(entry, prior);
    assert(f.node.references_changed == notifications && f.node.cancellations == 0);
    assert(f.node.restores == (failure == 1 ? 1u : 0u));
    ++checks;
  }
  {
    Fixture f; auto* peer = f.add(1); f.fill();
    auto& entry = f.pending(peer); const auto prior = entry;
    assert(f.node.addContact(makeContact(99)) && entry.contact == nullptr);
    assert(peer->id.pub_key[0] == 99); unchanged(entry, prior);
    f.received(entry.ack); assert(f.node.learned.empty());
    ++checks;
  }
  for (unsigned failure = 0; failure < 2; ++failure) {
    Fixture f; auto* anon = f.transient(9); auto& entry = f.pending(anon);
    const auto prior = entry;
    const auto prior_contact = *anon;
    if (failure == 0) { f.fill(); f.node.overwrite = false; }
    else f.node.schedule_ok = false;
    f.command(9);
    assert(f.node.error_replies == 1 && f.node.ok_replies == 0);
    assert(entry.contact == anon && anon->id.matches(prior_contact.id));
    assert(anon->lastmod == prior_contact.lastmod && anon->type == prior_contact.type);
    unchanged(entry, prior); assert(f.node.cancellations == 0);
    ++checks;
  }
  {
    Fixture f; auto* anon = f.transient(9); auto& entry = f.pending(anon);
    const auto prior = entry;
    f.command(9);
    const ContactInfo expected = makeContact(9);
    auto* accepted = f.node.lookupPersistentContactByPubKey(expected.id.pub_key, PUB_KEY_SIZE);
    assert(accepted != nullptr && entry.contact == accepted && anon->id.pub_key[0] == 0);
    assert(f.node.ok_replies == 1 && f.node.error_replies == 0);
    unchanged(entry, prior); f.received(entry.ack);
    if (MESH_ENABLE_ONE_KEY_DM) assert((f.node.learned == std::vector<uint8_t>{9}));
    ++checks;
  }
  for (unsigned failure = 0; failure < 2; ++failure) {
    Fixture f; auto* persistent = f.add(9); auto* anon = f.transient(9);
    auto& entry = f.pending(anon); const auto prior = entry;
    f.node.schedule_ok = failure == 0;
    f.command(9);
    assert(entry.contact == (failure == 0 ? persistent : anon));
    assert(f.node.ok_replies == (failure == 0 ? 1u : 0u));
    assert(f.node.error_replies == (failure == 1 ? 1u : 0u));
    unchanged(entry, prior);
    ++checks;
  }
  {
    Fixture f; auto* old = f.add(1); auto* next = f.add(2); auto* anon = f.transient(9); f.fill();
    auto& old_entry = f.pending(old);
    auto& next_entry = f.pending(next, 1, 0x55667788);
    auto& anon_entry = f.pending(anon, 2, 0x88776655);
    const auto old_state = old_entry, next_state = next_entry, anon_state = anon_entry;
    f.node.schedule_ok = false; f.command(9);
    assert(f.node.error_replies == 1 && old_entry.contact == nullptr);
    assert(next_entry.contact == &f.node.contacts[MAX_ANON_CONTACTS]);
    assert(next_entry.contact->id.pub_key[0] == 2 && anon_entry.contact == anon);
    unchanged(old_entry, old_state); unchanged(next_entry, next_state); unchanged(anon_entry, anon_state);
    assert(f.node.cancellations == 0);
    f.received(old_entry.ack); assert(f.node.learned.empty());
    ++checks;
  }
  for (unsigned refusal = 0; refusal < 3; ++refusal) {
    Fixture f; auto* anon = f.transient(9); auto& entry = f.pending(anon);
    const auto prior = entry;
    if (refusal == 0) f.node.auto_add = false;
    if (refusal == 1) f.node.max_hops = 1;
    if (refusal == 2) { f.fill(); f.node.overwrite = false; }
    f.advert(9, refusal == 1 ? 1 : 0);
    assert(entry.contact == anon && anon->id.pub_key[0] == 9);
    unchanged(entry, prior); assert(f.node.cancellations == 0);
    ++checks;
  }
  {
    Fixture f; auto* anon = f.transient(9); auto& entry = f.pending(anon);
    const auto prior = entry;
    f.advert(9);
    const ContactInfo identity = makeContact(9);
    auto* accepted = f.node.lookupPersistentContactByPubKey(identity.id.pub_key, PUB_KEY_SIZE);
    assert(accepted != nullptr && entry.contact == accepted && anon->id.pub_key[0] == 0);
    unchanged(entry, prior); f.received(entry.ack);
    ++checks;
  }
  for (unsigned failure = 0; failure < 2; ++failure) {
    Fixture f; auto* anon = f.transient(9); auto& entry = f.pending(anon);
    const auto prior = entry;
    f.node.schedule_ok = failure == 0;
    const auto identity = makeContact(9).id;
    f.node.acceptVerifiedOneKey(identity);
    if (failure == 0) {
      assert(entry.contact == f.node.lookupPersistentContactByPubKey(identity.pub_key, PUB_KEY_SIZE));
      assert(anon->id.pub_key[0] == 0);
    } else assert(entry.contact == anon && anon->id.pub_key[0] == 9);
    unchanged(entry, prior);
    ++checks;
  }
  for (unsigned failure = 0; failure < 2; ++failure) {
    Fixture f; auto* peer = f.add(1); auto& entry = f.pending(peer);
    const auto prior = entry;
    if (failure == 0) f.node.release_ok = false;
    else f.node.store.delete_ok = false;
    f.command(1, CMD_REMOVE_CONTACT);
    assert(f.node.error_replies == 1 && entry.contact == peer && peer->id.pub_key[0] == 1);
    unchanged(entry, prior); assert(f.node.cancellations == 0);
    ++checks;
  }
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
  for (unsigned failure = 1; failure <= 2; ++failure) {
    Fixture f; auto* peer = f.add(1); auto& entry = f.pending(peer);
    const auto prior = entry;
    const auto notifications = f.node.references_changed;
    fail_allocation = failure;
    assert(!f.node.initializeContactStorage());
    assert(entry.contact == peer && f.node.contacts == f.node.contacts_fallback);
    assert(outstanding_allocations.empty() && f.node.references_changed == notifications);
    unchanged(entry, prior);
    ++checks;
  }
  {
    Fixture f; auto* peer = f.add(1); auto& entry = f.pending(peer);
    entry.confirmed = true; entry.msg_sent = 50; entry.expires_at = 10100;
    const auto prior = entry;
    assert(f.node.initializeContactStorage());
    assert(entry.contact != peer && entry.contact == &f.node.contacts[MAX_ANON_CONTACTS]);
    assert(entry.contact->id.pub_key[0] == 1);
    uint8_t path[MAX_PATH_SIZE]; assert(entry.contact->copyPathTo(path) && path[0] == 1);
    unchanged(entry, prior); assert(f.node.cancellations == 0);
    ++checks;
  }
#endif
  {
    Fixture f; auto* peer = f.add(1);
    auto& connection = f.node.connections[0];
    connection.server_id = peer->id; connection.keep_alive_millis = 1000;
    connection.last_activity = 1; connection.next_ping = 2;
    ContactInfo* result = peer;
    assert(!f.ack(0, result) && result == nullptr);
    assert(connection.last_activity == 1 && connection.next_ping == 2);
    connection.expected_ack = 0x11223344;
    assert(f.ack(connection.expected_ack, result) && result == peer);
    assert(connection.expected_ack == 0 && connection.last_activity == 100);
    result = peer; assert(!f.ack(0x11223344, result) && result == nullptr);
    connection.expected_ack = 0x11223344;
    assert(f.node.removeContact(*peer));
    f.received(connection.expected_ack);
    assert(connection.expected_ack == 0 && f.node.learned.empty());
    connection.expected_ack = 0x11223344; connection.keep_alive_millis = 0;
    assert(!f.ack(0x11223344, result) && result == nullptr);
    ++checks;
  }
  {
    Fixture f; auto* peer = f.add(1); auto& entry = f.pending(peer);
    assert(f.node.removeContact(*peer));
    ContactInfo sender = makeContact(2); uint8_t path[] = {3};
    uint32_t crc = entry.ack;
    f.node.can_mutate = false; f.node.txt_send_timeout = 9000;
    assert(f.node.onContactPathRecv(sender, path, 1, path, 1, PAYLOAD_TYPE_ACK,
        reinterpret_cast<uint8_t*>(&crc), sizeof(crc)));
    assert(f.node.txt_send_timeout == 0 && f.node.cancellations == 1 && f.node.learned.empty());
    ++checks;
  }
  {
    Fixture f; auto* a = f.add(1); auto* b = f.add(2); f.blockUsb();
    auto& entry = f.pending(b);
    ContactInfo* result = nullptr;
    assert(f.ack(entry.ack, result) && result == b && entry.confirmed);
    const auto prior = entry;
    const auto learned = f.node.learned;
    assert(f.node.removeContact(*a));
    assert(entry.contact == &f.node.contacts[MAX_ANON_CONTACTS]); unchanged(entry, prior);
    assert(!f.ack(entry.ack, result) && result == nullptr);
    assert(f.node.removeContact(*entry.contact) && entry.contact == nullptr);
    unchanged(entry, prior); assert(f.node.learned == learned && f.node.cancellations == 1);
    f.drain(); g_mock_millis = 110; f.node.service(); f.drain();
    assert(entry.ack == 0 && confirmations(f.usb_stream.output) == 1);
    ++checks;
  }
  {
    Fixture f; auto* old = f.add(1); auto* live = f.add(2); f.blockUsb();
    auto& first = f.pending(old);
    auto& second = f.pending(live, 1, first.ack, &f.other);
    assert(f.node.removeContact(*old) && first.contact == nullptr && second.contact->id.pub_key[0] == 2);
    ContactInfo* result = live;
    assert(f.ack(first.ack, result) && result == nullptr && first.confirmed && !second.confirmed);
    assert(f.ack(first.ack, result) && result == &f.node.contacts[MAX_ANON_CONTACTS]);
    assert(f.node.cancellations == 2);
    if (MESH_ENABLE_ONE_KEY_DM) assert((f.node.learned == std::vector<uint8_t>{2}));
    else assert(f.node.learned.empty());
    ++checks;
  }
  const struct { uint8_t route, expected_peer; } routes[] = {
    {ROUTE_TYPE_FLOOD, 1}, {ROUTE_TYPE_TRANSPORT_FLOOD, 1},
    {ROUTE_TYPE_DIRECT, 0}, {ROUTE_TYPE_TRANSPORT_DIRECT, 0},
  };
  for (const auto& route : routes) {
    Fixture f; auto* peer = f.add(1); auto& entry = f.pending(peer);
    f.received(entry.ack, route.expected_peer, route.route);
    assert(entry.ack == 0 && f.node.cancellations == 1);
    ++checks;
  }
  {
    Fixture f; auto* peer = f.add(9); auto& entry = f.pending(peer);
    assert(peer->out_path_len == OUT_PATH_UNKNOWN);
    f.received(entry.ack);
    assert(entry.ack == 0 && f.node.cancellations == 1);
    ++checks;
  }
  {
    Fixture f; auto* peer = f.add(1); auto& entry = f.pending(peer); f.blockUsb();
    f.received(entry.ack, 1);
    assert(entry.confirmed && f.node.path_notices.size() == 1);
    const auto prior = entry;
    for (uint32_t crc : {entry.ack, uint32_t(0xdeadbeef)}) {
      mesh::Packet packet;
      packet.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT);
      f.node.txt_send_timeout = 9000;
      f.node.onAckRecv(&packet, crc);
      assert(!packet.isMarkedDoNotRetransmit() && packet.isRouteFlood());
      assert(f.node.txt_send_timeout == 9000 && f.node.path_notices.size() == 1);
      assert(f.node.cancellations == 1);
      unchanged(entry, prior);
    }
    ++checks;
  }
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
  assert(checks == 37);
#else
  assert(checks == 34);
#endif
  assert(outstanding_allocations.empty());
  printf("PASS: %u production contact lifetime checks\n", checks);
}
