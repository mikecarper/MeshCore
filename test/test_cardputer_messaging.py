#!/usr/bin/env python3
"""Execute Cardputer admission, retry, accepted delivery and selection methods."""
from pathlib import Path
import unittest

from cpp_source import body
import test_cardputer_mesh_storage as storage_tests

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/m5stack_cardputer_adv"

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include "globals.h"
#define PUB_KEY_SIZE 32
#define OUT_PATH_UNKNOWN 255
#define MSG_SEND_FAILED 0
#define MESH_DEBUG_PRINTLN(...) ((void)0)
enum MessageSendState { MESSAGE_SENDING, MESSAGE_DELIVERED, MESSAGE_FAILED };
struct ContactInfo {
  struct { uint8_t pub_key[32] = {}; } id;
  uint8_t out_path_len = 0;
};
namespace mesh { struct GroupChannel { uint8_t secret[32] = {}; }; }
struct ChannelDetails { mesh::GroupChannel channel; char name[32] = "channel"; };
struct CustomNodePrefs { float battery_correction; uint8_t brightness, text_level; };
struct MessageMeta { bool has = false; };
#define OWN_ROUTE_FLOOD 0xFF
struct Tracker {
  uint32_t next = 0, failed_id = 0, routed_id = 0;
  unsigned adds = 0, fails = 0;
  uint8_t route = 0;
  uint32_t add(const uint8_t*, bool, uint32_t, uint8_t r = OWN_ROUTE_FLOOD) { ++adds; route = r; return ++next; }
  void setRoute(uint32_t id, uint8_t r) { routed_id = id; route = r; }
  void onFailed(uint32_t id) { ++fails; failed_id = id; }
};
struct Clock { uint32_t getMillis() const { return 7; } };
@LAST_MESSAGE@
struct Store {
  bool save_ok = true;
  unsigned writes = 0;
  uint8_t key[32] = {};
  std::string text;
  const MessageMeta* meta = nullptr;
  void storeMessage(const uint8_t* pubkey, const char* value, bool, bool, const MessageMeta* m = nullptr) {
    meta = m;
    ++writes; memcpy(key, pubkey, 32); text = value;
  }
  bool saveCustomPrefs(const CustomNodePrefs&) { return save_ok; }
  void loadCustomPrefs(CustomNodePrefs&) {}
};
struct UI {
  std::vector<MessageSendState> events;
  unsigned contacts = 0, channels = 0, sent_contacts = 0, sent_channels = 0;
  uint8_t key[32] = {};
  void onMessageSendAttempt(uint8_t, uint8_t, MessageSendState state) { events.push_back(state); }
  void onContactMessageRecv(const ContactInfo& contact, const char*) {
    ++contacts; memcpy(key, contact.id.pub_key, 32);
  }
  void onChannelMessageRecv(uint8_t, const uint8_t* id, const char*) {
    ++channels; memcpy(key, id, 32);
  }
  void onContactMessageSent(const ContactInfo&, const char*) { ++sent_contacts; }
  void onChannelMessageSent(uint8_t, const uint8_t*, const char*) { ++sent_channels; }
};
struct MyMesh {
  bool admit = true, present = true, persist = true, standard_save = true;
  uint32_t now = 100, timeout = 25000;
  unsigned sends = 0, standard_saves = 0, path_saves = 0;
  ContactInfo current;
  uint8_t sent_path = 0, sent_attempt = 0;
  virtual void onUiContactMessageSent(const ContactInfo&, const char*) {}
  int sendMessage(const ContactInfo& contact, uint32_t, uint8_t attempt, const char* text,
                  uint32_t& ack, uint32_t& estimate) {
    ++sends;
    if (!admit) { ack = 0; estimate = 0; return MSG_SEND_FAILED; }
    sent_path = contact.out_path_len; sent_attempt = attempt;
    ack = 1000 + attempt; estimate = timeout;
    if (attempt == 0) onUiContactMessageSent(contact, text);
    return 1;
  }
  ContactInfo* lookupContactByPubKey(const uint8_t* key, size_t count) {
    assert(count == 32);
    return present && !memcmp(key, current.id.pub_key, 32) ? &current : nullptr;
  }
  bool queueUiContactWrite(const ContactInfo&) { ++path_saves; return persist; }
  unsigned long futureMillis(uint32_t delay) const { return now + delay; }
  bool savePrefs() { ++standard_saves; return standard_save; }
};
struct CardputerMesh : MyMesh {
  Store store;
  Store* _store = &store;
  UI ui;
  UI* _ui = &ui;
  LastSentMessage last_msg;
  unsigned long resend_timeout = 0;
  Tracker own_tracker;
  Clock clock;
  Clock* _ms = &clock;
  uint32_t last_own_id = 0, last_dm_own_id = 0;
  bool ui_dm_sending = false;
  MessageMeta last_rx_meta;
  CustomNodePrefs _custom_prefs{1.0f, 128, 1}, _saved_custom_prefs = _custom_prefs;
  bool isMessageSending() const { return last_msg.state == MESSAGE_SENDING; }
  int sendDirectMessage(const ContactInfo&, uint32_t, const char*);
  void checkResend();
  void cancelResending();
  void onUiContactMessage(const ContactInfo&, const char*);
  void onUiChannelMessage(uint8_t, const char*);
  void onUiContactMessageSent(const ContactInfo&, const char*) override;
  bool getChannelHistoryId(int index, uint8_t* id) {
    assert(index == 7); memset(id, 0, 32); id[31] = 88; return true;
  }
  bool savePrefs();
  bool saveCustomPrefs();
  void beginCustom();
};
@METHODS@
int main() {
  CardputerMesh rejected;
  rejected.admit = false;
  assert(rejected.sendDirectMessage(rejected.current, 4, "test") == MSG_SEND_FAILED);
  assert(!rejected.isMessageSending() && rejected.resend_timeout == 0);
  assert(rejected.store.writes == 0 && rejected.ui.events.empty());

  CardputerMesh node;
  node.current.id.pub_key[31] = 42;
  node.current.out_path_len = 2;
  assert(node.sendDirectMessage(node.current, 5, "message") != MSG_SEND_FAILED);
  assert(node.resend_timeout == node.now + node.timeout && node.store.writes == 1);
  assert(node.ui.sent_contacts == 1);
  assert(node.last_msg.expected_ack == 1000 && node.last_msg.attempt == 1);
  assert(node.own_tracker.adds == 1 && node.last_own_id == 1 && node.last_dm_own_id == 1);
  assert(node.own_tracker.route == 2 && !node.ui_dm_sending);
  assert(node.sendDirectMessage(node.current, 6, "other") == MSG_SEND_FAILED && node.sends == 1);
  node.current.out_path_len = 3;
  node.now = 30000; node.timeout = 32000;
  node.checkResend();
  assert(node.sent_path == 3 && node.sent_attempt == 1 && node.last_msg.attempt == 2);
  assert(node.resend_timeout == node.now + 32000 && node.store.writes == 1);
  assert(node.ui.sent_contacts == 1);
  node.admit = false;
  node.checkResend();
  assert(!node.isMessageSending() && node.resend_timeout == 0 && node.last_msg.expected_ack == 0);
  assert(node.ui.events.back() == MESSAGE_FAILED && node.store.writes == 1);
  assert(node.own_tracker.fails == 1 && node.own_tracker.failed_id == 1);

  CardputerMesh removed;
  assert(removed.sendDirectMessage(removed.current, 1, "test") != MSG_SEND_FAILED);
  removed.present = false; removed.checkResend();
  assert(removed.sends == 1 && !removed.isMessageSending() && removed.resend_timeout == 0);

  CardputerMesh fallback;
  fallback.current.out_path_len = 4; fallback.timeout = 10;
  assert(fallback.sendDirectMessage(fallback.current, 1, "test") != MSG_SEND_FAILED);
  assert(fallback.resend_timeout == fallback.now + DIRECT_SEND_ROUTE_RESEND_RELAY);
  fallback.checkResend(); fallback.checkResend(); fallback.checkResend();
  assert(fallback.sent_path == OUT_PATH_UNKNOWN && fallback.path_saves == 1);
  assert(fallback.own_tracker.routed_id == 1 && fallback.own_tracker.route == OWN_ROUTE_FLOOD);
  assert(fallback.resend_timeout == fallback.now + DIRECT_SEND_FLOOD_RESEND_RELAY);
  fallback.checkResend(); fallback.checkResend(); fallback.checkResend();
  assert(!fallback.isMessageSending() && fallback.sends == 6);

  CardputerMesh blocked;
  blocked.current.out_path_len = 4;
  assert(blocked.sendDirectMessage(blocked.current, 1, "test") != MSG_SEND_FAILED);
  blocked.last_msg.attempt = DIRECT_SEND_ROUTE_ATTEMPTS;
  blocked.persist = false; blocked.checkResend();
  assert(blocked.current.out_path_len == 4 && !blocked.isMessageSending() && blocked.sends == 1);

  // App/terminal DMs reach history through the same hook but have no keyboard
  // ACK state, so they stay untracked.
  CardputerMesh app;
  uint32_t ack = 0, estimate = 0;
  assert(app.sendMessage(app.current, 9, 0, "app", ack, estimate) != MSG_SEND_FAILED);
  assert(app.ui.sent_contacts == 1 && app.own_tracker.adds == 0 && app.last_own_id == 0);

  CardputerMesh custom;
  custom._custom_prefs.brightness = 200;
  assert(custom.saveCustomPrefs() && custom.standard_saves == 0);
  custom._custom_prefs.brightness = 250; custom.store.save_ok = false;
  assert(!custom.saveCustomPrefs() && custom._custom_prefs.brightness == 200);
  assert(custom.savePrefs() && custom.standard_saves == 1 && custom._custom_prefs.brightness == 200);

  CardputerMesh accepted;
  accepted.current.id.pub_key[31] = 77;
  accepted.onUiContactMessage(accepted.current, "direct");
  assert(accepted.ui.contacts == 1 && accepted.store.writes == 1 && accepted.store.key[31] == 77);
  accepted.onUiChannelMessage(7, "group");
  assert(accepted.ui.channels == 1 && accepted.store.writes == 2 && accepted.store.key[31] == 88);
  assert(accepted.ui.key[31] == 88);
  assert(accepted.store.meta == &accepted.last_rx_meta);
}
'''

SELECTION = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#define PUB_KEY_SIZE 32
struct ContactInfo { struct { uint8_t pub_key[32] = {}; } id; int marker = 0; };
struct ChannelDetails { struct { uint8_t secret[32] = {}; } channel; char name[32] = "channel"; };
struct CardputerMesh {
  static bool channelHistoryId(const ChannelDetails& channel, uint8_t* id) {
    memset(id, 0, 32);
    for (size_t i=0; i<strlen(channel.name); ++i) id[i%32] ^= channel.name[i];
    for (size_t i=0; i<32; ++i) id[i] ^= channel.channel.secret[i];
    return channel.name[0] != 0;
  }
};
struct Mesh {
  std::vector<ContactInfo> contacts;
  ChannelDetails channel;
  bool channel_present = true;
  ContactInfo* lookupPersistentContactByPubKey(const uint8_t* key, size_t size) {
    assert(size == 32);
    for (auto& contact : contacts) if (!memcmp(key, contact.id.pub_key, 32)) return &contact;
    return nullptr;
  }
  bool getChannel(int index, ChannelDetails& value) {
    assert(index == 4);
    if (!channel_present) return false;
    value = channel; return true;
  }
} the_mesh_cp;
struct History { unsigned count = 1; void clear() { count = 0; } };
struct ChatFocus { int msg = 0; };
struct ChatPage {
  int current_contact_idx = 2, current_channel_idx = -1;
  ContactInfo current_contact;
  ChannelDetails current_channel;
  uint8_t current_channel_history_id[32] = {};
  History chat_history;
  int chat_history_offset = 2;
  std::string last_sent_message = "previous";
  ChatFocus focus{3};
  void refreshSelectedContact();
  bool refreshSelectedChannel();
};
@METHODS@
int main() {
  ContactInfo alice, bob, carol;
  alice.id.pub_key[31] = 1; bob.id.pub_key[31] = 2; carol.id.pub_key[31] = 3;
  bob.marker = 42; carol.marker = 99;
  the_mesh_cp.contacts = {alice, bob, carol};
  ChatPage page; page.current_contact = bob;
  the_mesh_cp.contacts.erase(the_mesh_cp.contacts.begin());
  page.refreshSelectedContact();
  assert(page.current_contact.id.pub_key[31] == 2 && page.current_contact.marker == 42);
  the_mesh_cp.contacts.erase(the_mesh_cp.contacts.begin());
  page.refreshSelectedContact();
  assert(page.current_contact_idx == -1 && page.chat_history.count == 0 && page.last_sent_message.empty());
  assert(page.focus.msg == 0);
  page.current_channel_idx = 4; page.current_channel = the_mesh_cp.channel;
  CardputerMesh::channelHistoryId(page.current_channel, page.current_channel_history_id);
  strcpy(the_mesh_cp.channel.name, "renamed");
  assert(!page.refreshSelectedChannel() && page.current_channel_idx == -1);
  page.current_channel_idx = 4; page.current_channel = the_mesh_cp.channel;
  CardputerMesh::channelHistoryId(page.current_channel, page.current_channel_history_id);
  the_mesh_cp.channel.channel.secret[31] = 1;
  assert(!page.refreshSelectedChannel() && page.current_channel_idx == -1);
  page.current_channel_idx = 4; page.current_channel = the_mesh_cp.channel;
  the_mesh_cp.channel.name[0] = 0;
  assert(!page.refreshSelectedChannel() && page.current_channel_idx == -1);
}
'''


class CardputerMessagingTests(unittest.TestCase):
    def run_cpp(self, source):
        storage_tests.CardputerMeshStorageTests.run_cpp(self, source)

    def test_admission_retry_accepted_delivery_and_preference_rollback(self):
        source = (VARIANT / "companion_radio/CardputerMesh.cpp").read_text()
        methods = "\n".join(body(source, signature) for signature in (
            "int CardputerMesh::sendDirectMessage(", "void CardputerMesh::checkResend()",
            "void CardputerMesh::cancelResending()", "void CardputerMesh::onUiContactMessage(",
            "void CardputerMesh::onUiContactMessageSent(",
            "void CardputerMesh::onUiChannelMessage(", "bool CardputerMesh::saveCustomPrefs()",
            "bool CardputerMesh::savePrefs()", "void CardputerMesh::beginCustom()"))
        last_message = body((VARIANT / "types.h").read_text(), "struct LastSentMessage {") + ";"
        self.run_cpp(HARNESS.replace("@METHODS@", methods).replace("@LAST_MESSAGE@", last_message))

    def test_selection_survives_contact_compaction_and_invalidates_replaced_keys(self):
        source = (VARIANT / "companion_radio/ui-cardputer/screen/main/ChatPage.cpp").read_text()
        methods = "\n".join(body(source, signature) for signature in (
            "void ChatPage::refreshSelectedContact()", "bool ChatPage::refreshSelectedChannel()"))
        self.run_cpp(SELECTION.replace("@METHODS@", methods))


if __name__ == "__main__":
    unittest.main()
