#include <gtest/gtest.h>
#include <Ed25519.h>
#include <helpers/StaticPoolPacketManager.h>

// Exercise the complete production import, receive and contact admission path.
// These helpers are not part of the default native source filter.
#include "../../src/helpers/AdvertDataHelpers.cpp"
#include "../../src/helpers/BaseChatMesh.cpp"

namespace {
class Clock : public mesh::MillisecondClock {
public:
  unsigned long getMillis() override { return 0; }
};
class RTC : public mesh::RTCClock {
public:
  uint32_t getCurrentTime() override { return 100; }
  void setCurrentTime(uint32_t) override {}
};
class RNG : public mesh::RNG {
public:
  void random(uint8_t* dest, size_t size) override { memset(dest, 1, size); }
};
class Radio : public mesh::Radio {
public:
  bool dual = false;
  mesh::RadioProfiles config;
  mesh::RadioProfiles* profiles() override { return dual ? &config : nullptr; }
  const mesh::RadioProfiles* profiles() const override { return dual ? &config : nullptr; }
  int recvRaw(uint8_t*, int) override { return 0; }
  uint32_t getEstAirtimeFor(int) override { return 10; }
  float packetScore(float, int) override { return 0; }
  bool startSendRaw(const uint8_t*, int) override { return false; }
  bool isSendComplete() override { return false; }
  void onSendFinished() override {}
  bool isInRecvMode() const override { return true; }
};
class Tables : public mesh::MeshTables {
public:
  // Existing admission tests do not deduplicate. Opt in for receive-order
  // regressions with a real payload/type hash, deliberately excluding profile.
  bool deduplicate = false, have_seen = false;
  unsigned seen_queries = 0, seen_marks = 0;
  uint8_t seen_hash[MAX_HASH_SIZE] = {};
  bool wasSeen(const mesh::Packet* packet) override {
    if (!deduplicate) return false;
    ++seen_queries;
    uint8_t hash[MAX_HASH_SIZE];
    packet->calculatePacketHash(hash);
    return have_seen && !memcmp(hash, seen_hash, sizeof(hash));
  }
  void markSeen(const mesh::Packet* packet) override {
    if (!deduplicate) return;
    ++seen_marks;
    packet->calculatePacketHash(seen_hash);
    have_seen = true;
  }
  void markSent(const mesh::Packet*) override {}
  void clear(const mesh::Packet*) override { have_seen = false; }
};
class Chat : public BaseChatMesh {
public:
  uint8_t getFloodRetryMaxAttempts(const mesh::Packet*) const override { return 0; }
  uint8_t getDirectRetryMaxAttempts(const mesh::Packet*) const override { return 0; }
  void receivedTextFromFirstContact(mesh::Packet& packet) {
    uint8_t hash[PATH_HASH_SIZE]; getContactPtrByIdx(MAX_ANON_CONTACTS)->id.copyHashTo(hash);
    ASSERT_GT(searchPeersByHash(hash), 0);
    uint8_t secret[32] = {}, data[32] = {};
    data[0] = 10; memcpy(data + 5, "hello", 5);
    onPeerDataRecv(&packet, PAYLOAD_TYPE_TXT_MSG, 0, secret, data, 10);
  }
  bool auto_add = false;
  bool writable = true;
  uint8_t max_hops = 1;
  unsigned discovered = 0, stored = 0, full = 0;
  bool forward_packets = false, reject_primary_groups = false;
  bool bind_secondary_groups = false, packet_group_search = false;
  uint32_t direct_forward_delay = 0;
  uint8_t extra_acks = 0;
  unsigned group_admissions = 0, group_searches = 0, received_groups = 0;
  uint8_t last_search_origin = 0xFF;
  mesh::GroupChannel primary_group, secondary_group, received_group;
  Chat(Radio& radio, Clock& clock, RNG& rng, RTC& rtc,
       StaticPoolPacketManager& pool, Tables& tables)
      : BaseChatMesh(radio, clock, rng, rtc, pool, tables) {}
  void receive(mesh::Packet& packet) { onRecvPacket(&packet); }
  bool shouldAutoAddContactType(uint8_t) const override { return auto_add; }
  uint8_t getAutoAddMaxHops() const override { return max_hops; }
  bool canMutateContacts() const override { return writable; }
  bool allowPacketForward(const mesh::Packet*) override { return forward_packets; }
  uint32_t getDirectRetransmitDelay(const mesh::Packet*) override { return direct_forward_delay; }
  uint8_t getExtraAckTransmitCount() const override { return extra_acks; }
  bool allowGroupPacketOnRadio(mesh::Packet* packet) override {
    ++group_admissions;
    // Stub only the application's policy, not the core admission/order path.
    if (reject_primary_groups && packet->radio_origin == 0) return false;
    if (bind_secondary_groups && packet->radio_origin == 1) {
      packet->tx_radio = mesh::RADIO_TX_SECONDARY;
    }
    return true;
  }
  int searchChannelsForPacket(const mesh::Packet* packet, const uint8_t* hash,
                             mesh::GroupChannel channels[], int max_matches) override {
    if (!packet_group_search) {
      return BaseChatMesh::searchChannelsForPacket(packet, hash, channels, max_matches);
    }
    ++group_searches;
    last_search_origin = packet->radio_origin;
    const auto& channel = packet->radio_origin == 1 ? secondary_group : primary_group;
    if (max_matches < 1 || channel.hash[0] != hash[0]) return 0;
    channels[0] = channel;
    return 1;
  }
  void onGroupDataRecv(mesh::Packet* packet, uint8_t type,
                       const mesh::GroupChannel& channel, uint8_t* data, size_t len) override {
    if (!packet_group_search) {
      BaseChatMesh::onGroupDataRecv(packet, type, channel, data, len);
      return;
    }
    ++received_groups;
    received_group = channel;
    // Native AES is a no-op mock. Production Utils/HMAC and the core channel
    // search/decrypt path run, but these tests make no plaintext/AES assertion.
  }
  void onContactsFull() override { ++full; }
  bool putBlobByKey(const uint8_t*, int, const uint8_t*, int) override {
    ++stored;
    return true;
  }
  void onDiscoveredContact(ContactInfo&, bool, uint8_t, const uint8_t*) override {
    ++discovered;
  }
  bool processAck(const uint8_t*, ContactInfo*& peer) override { peer = nullptr; return false; }
  void onContactPathUpdated(const ContactInfo&) override {}
  void onMessageRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*) override {}
  void onCommandDataRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*) override {}
  void onCLICommandRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*, char*) override {}
  bool onSignedMessageRecv(const ContactInfo&, mesh::Packet*, uint32_t,
                           const uint8_t*, const char*) override { return true; }
  uint32_t calcFloodTimeoutMillisFor(uint32_t) const override { return 100; }
  uint32_t calcDirectTimeoutMillisFor(uint32_t, uint8_t) const override { return 100; }
  void onSendTimeout() override {}
  void onChannelMessageRecv(const mesh::GroupChannel&, mesh::Packet*, uint32_t,
                            const char*) override {}
  uint8_t onContactRequest(const ContactInfo&, uint32_t, const uint8_t*, uint8_t,
                          uint8_t*) override { return 0; }
  void onContactResponse(const ContactInfo&, const uint8_t*, uint8_t) override {}
};

class ContactImport : public ::testing::Test {
protected:
  Radio radio;
  Clock clock;
  RNG rng;
  RTC rtc;
  Tables tables;
  StaticPoolPacketManager pool{8};
  Chat chat{radio, clock, rng, rtc, pool, tables};

  void SetUp() override {
    g_mock_ed25519_verify_result = true;
    g_mock_ed25519_verify_calls = 0;
  }
  void TearDown() override { g_mock_ed25519_verify_result = true; }

  mesh::Packet card(uint8_t key = 1, uint8_t hops = 3, uint32_t timestamp = 10) {
    mesh::Packet packet;
    packet.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_ADVERT << PH_TYPE_SHIFT);
    packet.setPathHashSizeAndCount(1, hops);
    memset(packet.path, 0x42, hops);
    memset(packet.payload, 0, sizeof(packet.payload));
    packet.payload[0] = key;
    memcpy(packet.payload + PUB_KEY_SIZE, &timestamp, sizeof(timestamp));
    const size_t offset = PUB_KEY_SIZE + sizeof(timestamp) + SIGNATURE_SIZE;
    AdvertDataBuilder builder(ADV_TYPE_CHAT, "Imported node");
    packet.payload_len = offset + builder.encodeTo(packet.payload + offset);
    return packet;
  }
  bool queue(mesh::Packet packet) {
    uint8_t raw[MAX_TRANS_UNIT];
    return chat.importContact(raw, packet.writeTo(raw));
  }
  void configureGroupSearch() {
    chat.packet_group_search = true;
    memset(chat.primary_group.secret, 0, sizeof(chat.primary_group.secret));
    memset(chat.secondary_group.secret, 0, sizeof(chat.secondary_group.secret));
    memset(chat.primary_group.secret, 1, 16);
    memset(chat.secondary_group.secret, 2, 16);
    // Simulate the supported one-byte channel-hash collision. Authentication
    // must distinguish these keys, not the packet's channel hash alone.
    chat.primary_group.hash[0] = chat.secondary_group.hash[0] = 17;
  }
  mesh::Packet groupPacket(const mesh::GroupChannel& channel, uint8_t route = ROUTE_TYPE_FLOOD) {
    mesh::Packet packet;
    packet.header = route | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
    packet.setPathHashSizeAndCount(1, 0);
    memset(packet.payload, 0, sizeof(packet.payload));
    uint8_t data[CIPHER_BLOCK_SIZE] = {};
    const uint32_t timestamp = 10;
    memcpy(data, &timestamp, sizeof(timestamp));
    memcpy(data + 5, "hello", 5);
    packet.payload[0] = channel.hash[0];
    packet.payload_len = 1 + mesh::Utils::encryptThenMAC(
        channel.secret, packet.payload + 1, data, sizeof(data));
    return packet;
  }
  bool queueReceived(mesh::Packet packet, uint8_t origin) {
    auto* received = chat.obtainNewPacket();
    if (received == nullptr) return false;
    packet.radio_profile = packet.radio_origin = origin;
    packet.radio_generation = packet.radio_origin_generation = radio.config.generation[origin];
    packet.radio_local = packet.radio_bound = false;
    packet.radio_reply = packet.radio_reply_force = false;
    packet.tx_radio = mesh::RADIO_TX_AUTO;
    *received = packet;
    pool.queueInbound(received, 0);
    chat.loop();
    return true;
  }
};

TEST_F(ContactImport, ExplicitImportBypassesDiscoveryFiltersOnlyForThatPacket) {
  auto imported = card();
  ASSERT_TRUE(queue(imported));
  EXPECT_EQ(chat.getNumContacts(), 0); // validation is deferred to the mesh loop
  auto over_air = card(2, 0);
  chat.receive(over_air); // a pending import must not grant admission to RF packets
  EXPECT_EQ(chat.getNumContacts(), 0);
  chat.loop();
  ASSERT_EQ(chat.getNumContacts(), 1);
  auto* contact = chat.lookupContactByPubKey(imported.payload, PUB_KEY_SIZE);
  ASSERT_NE(contact, nullptr);
  EXPECT_STREQ(contact->name, "Imported node");
  EXPECT_EQ(chat.stored, 1u);
  EXPECT_FALSE(chat.auto_add);
  EXPECT_EQ(chat.max_hops, 1);
  EXPECT_EQ(pool.getFreeCount(), 8);
  auto later = card(3, 0);
  chat.receive(later);
  EXPECT_EQ(chat.getNumContacts(), 1);
}

TEST_F(ContactImport, RadioDiscoveryStillHonorsHopLimit) {
  chat.auto_add = true;
  auto far = card(1, 3);
  chat.receive(far);
  EXPECT_EQ(chat.getNumContacts(), 0);
  auto direct = card(2, 0);
  chat.receive(direct);
  EXPECT_EQ(chat.getNumContacts(), 1);
}

TEST_F(ContactImport, SignatureFailureCannotAddOrSaveAnImportedContact) {
  g_mock_ed25519_verify_result = false;
  ASSERT_TRUE(queue(card()));
  chat.loop();
  EXPECT_EQ(g_mock_ed25519_verify_calls, 1u);
  EXPECT_EQ(chat.getNumContacts(), 0);
  EXPECT_EQ(chat.discovered, 0u);
  EXPECT_EQ(chat.stored, 0u);
  EXPECT_EQ(pool.getFreeCount(), 8);
  g_mock_ed25519_verify_result = true;
  ASSERT_TRUE(queue(card()));
  chat.loop();
  EXPECT_EQ(chat.getNumContacts(), 1);
}

TEST_F(ContactImport, PendingImportCannotBeReplacedOrLeakItsPacket) {
  ASSERT_TRUE(queue(card()));
  EXPECT_EQ(pool.getFreeCount(), 7);
  EXPECT_FALSE(queue(card(2)));
  EXPECT_EQ(pool.getFreeCount(), 7);
  chat.loop();
  EXPECT_EQ(pool.getFreeCount(), 8);
  ASSERT_TRUE(queue(card(2)));
  chat.loop();
  EXPECT_EQ(chat.getNumContacts(), 2);
}

TEST_F(ContactImport, UnavailableStorageBlocksAdmissionAndDeferredMutation) {
  chat.writable = false;
  EXPECT_FALSE(queue(card()));
  EXPECT_EQ(pool.getFreeCount(), 8);
  chat.writable = true;
  ASSERT_TRUE(queue(card()));
  chat.writable = false;
  chat.loop();
  EXPECT_EQ(chat.getNumContacts(), 0);
  EXPECT_EQ(chat.stored, 0u);
  EXPECT_EQ(pool.getFreeCount(), 8);
}

TEST_F(ContactImport, FullContactTableIsNotOverwrittenByImport) {
  for (int i = 0; i < MAX_CONTACTS; ++i) {
    ContactInfo contact;
    contact.id.pub_key[0] = i + 2;
    contact.type = ADV_TYPE_CHAT;
    ASSERT_TRUE(chat.addContact(contact));
  }
  ASSERT_TRUE(queue(card()));
  chat.loop();
  EXPECT_EQ(chat.getNumContacts(), MAX_CONTACTS);
  EXPECT_EQ(chat.full, 1u);
  EXPECT_EQ(chat.stored, 0u);
}

TEST_F(ContactImport, ReimportDoesNotDuplicateContactOrRollBackAdvert) {
  ASSERT_TRUE(queue(card()));
  chat.loop();
  ASSERT_TRUE(queue(card(1, 3, 9)));
  chat.loop();
  EXPECT_EQ(chat.getNumContacts(), 1);
  EXPECT_EQ(chat.stored, 1u);
  EXPECT_EQ(chat.getContactPtrByIdx(MAX_ANON_CONTACTS)->last_advert_timestamp, 10u);
}

TEST_F(ContactImport, MalformedAndNonAdvertPacketsDoNotLeakAllocations) {
  const uint8_t malformed[] = {0};
  EXPECT_FALSE(chat.importContact(malformed, sizeof(malformed)));
  auto packet = card();
  packet.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT);
  EXPECT_FALSE(queue(packet));
  EXPECT_EQ(pool.getFreeCount(), 8);
}

TEST_F(ContactImport, ContactMessagesUseFullIdentityAndPreservePolicyAcrossAdvertRefresh) {
  radio.dual = true; radio.config.secondary.mode = mesh::RadioProfileMode::RxTx;
  radio.config.cross = mesh::RadioCrossMode::Off;
  ContactInfo first, second;
  memset(first.id.pub_key, 0, PUB_KEY_SIZE); first.id.pub_key[0] = 17;
  first.type = ADV_TYPE_CHAT; first.tx_radio = mesh::RADIO_TX_SECONDARY;
  second = first; second.id.pub_key[31] = 9; second.tx_radio = mesh::RADIO_TX_PRIMARY;
  ASSERT_TRUE(chat.addContact(first)); ASSERT_TRUE(chat.addContact(second));
  uint32_t ack=0, timeout=0;
  EXPECT_EQ(MSG_SEND_SENT_DIRECT, chat.sendMessage(first, 1, 0, "one", ack, timeout));
  EXPECT_EQ(MSG_SEND_SENT_DIRECT, chat.sendMessage(second, 2, 0, "two", ack, timeout));
  ASSERT_EQ(2, pool.getOutboundTotal());
  EXPECT_EQ(1, pool.getOutboundByIdx(0)->radio_profile);
  EXPECT_EQ(0, pool.getOutboundByIdx(1)->radio_profile);
  auto refresh = card(17, 0, 20); chat.receive(refresh);
  EXPECT_EQ(mesh::RADIO_TX_SECONDARY, chat.getContactPtrByIdx(MAX_ANON_CONTACTS)->tx_radio);
}

TEST_F(ContactImport, OffContactAndChannelReportFailureWithoutLeakingPackets) {
  ContactInfo contact;
  memset(contact.id.pub_key, 1, PUB_KEY_SIZE); contact.type = ADV_TYPE_CHAT;
  contact.tx_radio = mesh::RADIO_TX_OFF;
  ASSERT_TRUE(chat.addContact(contact));
  uint32_t ack=0, timeout=0, tag=0;
  const uint8_t data[] = {1};
  for (uint8_t path : {uint8_t(0), uint8_t(OUT_PATH_UNKNOWN)}) {
    contact.out_path_len = path;
    EXPECT_EQ(MSG_SEND_FAILED, chat.sendMessage(contact, 1, 0, "test", ack, timeout));
    EXPECT_EQ(MSG_SEND_FAILED, chat.sendCommandData(contact, 1, 0, TXT_TYPE_CLI_COMMAND, "test", timeout));
    EXPECT_EQ(MSG_SEND_FAILED, chat.sendLogin(contact, "", timeout));
    EXPECT_EQ(MSG_SEND_FAILED, chat.sendAnonReq(contact, data, sizeof(data), tag, timeout));
    EXPECT_EQ(MSG_SEND_FAILED, chat.sendRequest(contact, data, sizeof(data), tag, timeout));
    EXPECT_EQ(MSG_SEND_FAILED, chat.sendRequest(contact, uint8_t(1), tag, timeout));
    mesh::GroupChannel channel; memset(channel.secret, 0, sizeof(channel.secret));
    channel.hash[0] = 17; channel.tx_radio = mesh::RADIO_TX_OFF;
    EXPECT_FALSE(chat.sendGroupMessage(1, channel, "me", "test", 4));
    EXPECT_FALSE(chat.sendGroupData(channel, nullptr, path, 1, data, sizeof(data)));
    EXPECT_EQ(0, pool.getOutboundTotal()); EXPECT_EQ(8, pool.getFreeCount());
  }
}

TEST_F(ContactImport, SameHashChannelsKeepIndependentTransmitChoices) {
  radio.dual = true; radio.config.secondary.mode = mesh::RadioProfileMode::RxTx;
  radio.config.cross = mesh::RadioCrossMode::Off;
  mesh::GroupChannel a, b;
  memset(a.secret, 1, sizeof(a.secret)); memset(b.secret, 2, sizeof(b.secret));
  a.hash[0] = b.hash[0] = 17;
  a.tx_radio = mesh::RADIO_TX_PRIMARY; b.tx_radio = mesh::RADIO_TX_SECONDARY;
  ASSERT_TRUE(chat.sendGroupMessage(1, a, "me", "one", 3));
  ASSERT_TRUE(chat.sendGroupMessage(2, b, "me", "two", 3));
  const uint8_t data[] = {1};
  ASSERT_TRUE(chat.sendGroupData(b, nullptr, 0, 1, data, sizeof(data)));
  ASSERT_EQ(3, pool.getOutboundTotal());
  EXPECT_EQ(0, pool.getOutboundByIdx(0)->radio_profile);
  EXPECT_EQ(1, pool.getOutboundByIdx(1)->radio_profile);
  EXPECT_EQ(1, pool.getOutboundByIdx(2)->radio_profile);
}

TEST_F(ContactImport, ContactRoutingAlsoControlsAcknowledgementsAndReturnedPaths) {
  radio.dual = true; radio.config.secondary.mode = mesh::RadioProfileMode::RxTx;
  radio.config.cross = mesh::RadioCrossMode::Off;
  ContactInfo contact; memset(contact.id.pub_key, 1, PUB_KEY_SIZE);
  contact.type = ADV_TYPE_CHAT; contact.tx_radio = mesh::RADIO_TX_SECONDARY;
  ASSERT_TRUE(chat.addContact(contact));
  // A same-key anonymous slot must not bypass the saved contact's choice.
  *chat.getContactPtrByIdx(0) = contact;
  chat.getContactPtrByIdx(0)->type = ADV_TYPE_NONE;
  chat.getContactPtrByIdx(0)->tx_radio = mesh::RADIO_TX_AUTO;
  mesh::Packet incoming;
  incoming.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT);
  chat.receivedTextFromFirstContact(incoming);
  incoming.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT);
  chat.receivedTextFromFirstContact(incoming);
  ASSERT_EQ(2, pool.getOutboundTotal());
  EXPECT_EQ(1, pool.getOutboundByIdx(0)->radio_profile);
  EXPECT_EQ(1, pool.getOutboundByIdx(1)->radio_profile);
}

TEST_F(ContactImport, GroupAdmissionRejectsWrongProfileBeforeGlobalDeduplication) {
  radio.dual = true;
  radio.config.secondary.mode = mesh::RadioProfileMode::RxTx;
  radio.config.cross = mesh::RadioCrossMode::Off;
  tables.deduplicate = true;
  chat.reject_primary_groups = true;
  configureGroupSearch();
  const auto packet = groupPacket(chat.secondary_group);

  ASSERT_TRUE(queueReceived(packet, 0));
  EXPECT_EQ(chat.group_admissions, 1u);
  EXPECT_EQ(chat.group_searches, 0u);
  EXPECT_EQ(chat.received_groups, 0u);
  EXPECT_EQ(tables.seen_queries, 0u);
  EXPECT_EQ(tables.seen_marks, 0u);
  EXPECT_FALSE(tables.wasSeen(&packet));
  EXPECT_EQ(pool.getFreeCount(), 8);

  // Exactly the same wire payload must remain eligible on its allowed radio.
  ASSERT_TRUE(queueReceived(packet, 1));
  EXPECT_EQ(chat.group_admissions, 2u);
  EXPECT_EQ(chat.group_searches, 1u);
  EXPECT_EQ(chat.last_search_origin, 1);
  EXPECT_EQ(chat.received_groups, 1u);
  EXPECT_EQ(tables.seen_marks, 1u);
  EXPECT_TRUE(tables.wasSeen(&packet));
  EXPECT_EQ(pool.getFreeCount(), 8);

  ASSERT_TRUE(queueReceived(packet, 1));
  EXPECT_EQ(chat.group_admissions, 3u);
  EXPECT_EQ(chat.group_searches, 1u);
  EXPECT_EQ(chat.received_groups, 1u);
  EXPECT_EQ(tables.seen_marks, 1u);
}

TEST_F(ContactImport, PacketAwareGroupSearchUsesCapturedOriginAndAuthenticatedKey) {
  radio.dual = true;
  radio.config.secondary.mode = mesh::RadioProfileMode::RxTx;
  configureGroupSearch();
  const auto secondary_packet = groupPacket(chat.secondary_group);
  uint8_t plaintext[MAX_PACKET_PAYLOAD];
  ASSERT_GT(mesh::Utils::MACThenDecrypt(chat.secondary_group.secret, plaintext,
      secondary_packet.payload + 1, secondary_packet.payload_len - 1), 0);
  ASSERT_EQ(mesh::Utils::MACThenDecrypt(chat.primary_group.secret, plaintext,
      secondary_packet.payload + 1, secondary_packet.payload_len - 1), 0);

  // The transceiver reports primary while a delayed secondary packet is
  // processed. The packet-aware hook must receive the captured RX origin.
  ASSERT_EQ(radio.receiveProfile(), 0);
  ASSERT_TRUE(queueReceived(secondary_packet, 1));
  EXPECT_EQ(chat.last_search_origin, 1);
  EXPECT_EQ(chat.received_groups, 1u);
  EXPECT_EQ(memcmp(chat.received_group.secret, chat.secondary_group.secret, PUB_KEY_SIZE), 0);
  ASSERT_TRUE(queueReceived(secondary_packet, 0));
  EXPECT_EQ(chat.last_search_origin, 0);
  EXPECT_EQ(chat.received_groups, 1u); // same hash, wrong authenticated key
  ASSERT_TRUE(queueReceived(groupPacket(chat.primary_group), 0));
  EXPECT_EQ(chat.received_groups, 2u);
  EXPECT_EQ(memcmp(chat.received_group.secret, chat.primary_group.secret, PUB_KEY_SIZE), 0);
  EXPECT_EQ(chat.group_searches, 3u);
  EXPECT_EQ(pool.getFreeCount(), 8);
}

TEST_F(ContactImport, GroupAdmissionBindsDirectRelayBeforeCrossProfileFanout) {
  radio.dual = true;
  radio.config.secondary.mode = mesh::RadioProfileMode::RxTx;
  radio.config.cross = mesh::RadioCrossMode::On;
  tables.deduplicate = true;
  chat.forward_packets = chat.reject_primary_groups = chat.bind_secondary_groups = true;
  chat.direct_forward_delay = 100;
  configureGroupSearch();
  auto packet = groupPacket(chat.secondary_group, ROUTE_TYPE_DIRECT);
  chat.self_id.pub_key[0] = 0x42;
  packet.setPathHashSizeAndCount(1, 2);
  packet.path[0] = chat.self_id.pub_key[0];
  packet.path[1] = 0x93;

  ASSERT_TRUE(queueReceived(packet, 0));
  EXPECT_EQ(tables.seen_queries, 0u);
  EXPECT_EQ(tables.seen_marks, 0u);
  EXPECT_EQ(pool.getOutboundTotal(), 0);
  EXPECT_EQ(pool.getFreeCount(), 8);

  ASSERT_TRUE(queueReceived(packet, 1));
  EXPECT_EQ(chat.group_admissions, 2u);
  EXPECT_EQ(chat.group_searches, 0u); // direct relay returns before local search
  EXPECT_EQ(chat.received_groups, 0u);
  EXPECT_EQ(tables.seen_marks, 1u);
  ASSERT_EQ(pool.getOutboundTotal(), 1); // cross=on must not add a primary copy
  const auto* relayed = pool.getOutboundByIdx(0);
  ASSERT_NE(relayed, nullptr);
  EXPECT_EQ(relayed->radio_profile, 1);
  EXPECT_EQ(relayed->radio_origin, 1);
  EXPECT_EQ(relayed->tx_radio, mesh::RADIO_TX_SECONDARY);
  EXPECT_TRUE(relayed->radio_bound);
  EXPECT_FALSE(relayed->radio_reply);
  EXPECT_FALSE(relayed->radio_reply_force);
  EXPECT_EQ(relayed->getPathHashCount(), 1);
  EXPECT_EQ(relayed->path[0], 0x93);
  EXPECT_EQ(pool.getFreeCount(), 7);
}
TEST_F(ContactImport, RelayedAckHonorsInitialDirectForwardDelay) {
  chat.forward_packets = true;
  chat.direct_forward_delay = 175;
  mesh::Packet packet;
  packet.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT);
  packet.setPathHashSizeAndCount(1, 2);
  packet.path[0] = chat.self_id.pub_key[0];
  packet.path[1] = 0x93;
  packet.payload_len = 4;
  memset(packet.payload, 0x37, packet.payload_len);
  chat.receive(packet);
  ASSERT_EQ(pool.getOutboundTotal(), 1);
  EXPECT_EQ(pool.getOutboundCount(174), 0);
  EXPECT_EQ(pool.getOutboundCount(175), 1);
  EXPECT_EQ(pool.getOutboundByIdx(0)->getPathHashCount(), 1);
  EXPECT_EQ(pool.getOutboundByIdx(0)->path[0], 0x93);
  EXPECT_FALSE(pool.getOutboundByIdx(0)->radio_reply);
}

TEST_F(ContactImport, ExtraForwardedAcksKeepInitialDelayAndThreeHundredMsSpacing) {
  chat.forward_packets = true;
  chat.direct_forward_delay = 175;
  chat.extra_acks = 3;
  mesh::Packet packet;
  packet.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT);
  packet.setPathHashSizeAndCount(1, 1);
  packet.path[0] = chat.self_id.pub_key[0];
  packet.payload_len = 4;
  memset(packet.payload, 0x47, packet.payload_len);
  chat.receive(packet);
  ASSERT_EQ(pool.getOutboundTotal(), 4);
  for (uint32_t index = 0; index < 4; ++index) {
    const uint32_t due = 175 + 300 * index;
    EXPECT_EQ(pool.getNextOutbound(due - 1), nullptr);
    auto* ack = pool.getNextOutbound(due);
    ASSERT_NE(ack, nullptr);
    EXPECT_EQ(ack->getPayloadType(), index < 3 ? PAYLOAD_TYPE_MULTIPART : PAYLOAD_TYPE_ACK);
    EXPECT_EQ(ack->getPathHashCount(), 0);
    EXPECT_FALSE(ack->radio_reply);
    pool.free(ack);
  }
  EXPECT_EQ(pool.getFreeCount(), 8);
}

TEST_F(ContactImport, MultipartAckAddsInitialDelayWithoutLosingDuplicateProtection) {
  chat.forward_packets = true;
  chat.direct_forward_delay = 175;
  tables.deduplicate = true;
  mesh::Packet packet;
  packet.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_MULTIPART << PH_TYPE_SHIFT);
  packet.setPathHashSizeAndCount(1, 2);
  packet.path[0] = chat.self_id.pub_key[0];
  packet.path[1] = 0x93;
  packet.payload_len = 5;
  packet.payload[0] = (2 << 4) | PAYLOAD_TYPE_ACK;
  memset(packet.payload + 1, 0x57, packet.payload_len - 1);
  const auto duplicate = packet;
  chat.receive(packet);
  ASSERT_EQ(pool.getOutboundTotal(), 1);
  EXPECT_EQ(pool.getOutboundCount(1074), 0);
  EXPECT_EQ(pool.getOutboundCount(1075), 1);
  auto second = duplicate;
  chat.receive(second);
  EXPECT_EQ(pool.getOutboundTotal(), 1);
  EXPECT_EQ(tables.seen_marks, 1u);
}
} // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
