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
  bool wasSeen(const mesh::Packet*) override { return false; }
  void markSeen(const mesh::Packet*) override {}
  void markSent(const mesh::Packet*) override {}
  void clear(const mesh::Packet*) override {}
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
  Chat(Radio& radio, Clock& clock, RNG& rng, RTC& rtc,
       StaticPoolPacketManager& pool, Tables& tables)
      : BaseChatMesh(radio, clock, rng, rtc, pool, tables) {}
  void receive(mesh::Packet& packet) { onRecvPacket(&packet); }
  bool shouldAutoAddContactType(uint8_t) const override { return auto_add; }
  uint8_t getAutoAddMaxHops() const override { return max_hops; }
  bool canMutateContacts() const override { return writable; }
  bool allowPacketForward(const mesh::Packet*) override { return false; }
  void onContactsFull() override { ++full; }
  bool putBlobByKey(const uint8_t*, int, const uint8_t*, int) override {
    ++stored;
    return true;
  }
  void onDiscoveredContact(ContactInfo&, bool, uint8_t, const uint8_t*) override {
    ++discovered;
  }
  ContactInfo* processAck(const uint8_t*) override { return nullptr; }
  void onContactPathUpdated(const ContactInfo&) override {}
  void onMessageRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*) override {}
  void onCommandDataRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*) override {}
  void onCLICommandRecv(const ContactInfo&, mesh::Packet*, uint32_t, const char*, char*) override {}
  void onSignedMessageRecv(const ContactInfo&, mesh::Packet*, uint32_t,
                           const uint8_t*, const char*) override {}
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
} // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
