#!/usr/bin/env python3
"""Execute channel IDs, accepted sent history, and visible-chat unread policy."""
from pathlib import Path
import unittest

from cpp_source import body
import test_cardputer_mesh_storage as storage_tests

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/m5stack_cardputer_adv"

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <openssl/sha.h>
#include "RingBuffer.h"
#include "UnreadCounter.h"
#include "companion_radio/ui-cardputer/screen/main/ChatFocus.h"
#define PUB_KEY_SIZE 32
namespace mesh {
struct Utils {
  static void sha256(uint8_t* out, size_t count, const uint8_t* data, int length) {
    assert(count == 32); SHA256(data, length, out);
  }
};
}
struct ContactInfo { struct { uint8_t pub_key[32] = {}; } id; uint8_t out_path_len = 0xFF; };
struct ChannelDetails { struct { uint8_t secret[32] = {}; } channel; char name[32] = {}; };
struct MessageMeta { bool has = false; int tag = 0; };
struct HistoryMessage { char text[MAX_MESSAGE_LENGTH] = {}; bool out = false; MessageMeta meta; uint32_t own_id = 0; };
using ChatHistory = RingBuffer<HistoryMessage, CHAT_HISTORY_RINGBUF_SIZE>;
struct MainScreen;
struct UI {
  MainScreen* screen;
  bool visible = false;
  bool isChatVisible() const { return visible; }
  void onChannelMessageRecv(uint8_t, const uint8_t[32], const char*);
  void onChannelMessageSent(uint8_t, const uint8_t[32], const char*);
  void onContactMessageRecv(const ContactInfo&, const char*);
  void onContactMessageSent(const ContactInfo&, const char*);
};
struct ChatPage {
  MainScreen* _p;
  int current_channel_idx = -1, current_contact_idx = -1;
  uint8_t current_channel_history_id[32] = {};
  ContactInfo current_contact;
  ChatHistory chat_history;
  int chat_history_offset = 0;
  std::string last_sent_message;
  ChatFocus focus;
  void onHistoryPushed();
  void pushOwnMessage(const char*);
  bool onChannelMessageRecv(uint8_t, const uint8_t[32], const char*);
  bool onContactMessageRecv(const ContactInfo&, const char*);
  void onChannelMessageSent(uint8_t, const uint8_t[32], const char*);
  void onContactMessageSent(const ContactInfo&, const char*);
  void markRead(UnreadCounter&) const;
};
struct MainScreen {
  UI* _task;
  ChatPage* chat_page;
  UnreadCounter unread;
  UI* getUiTask() { return _task; }
  void onChannelMessageRecv(uint8_t, const uint8_t[32], const char*);
  void onContactMessageRecv(const ContactInfo&, const char*);
  void onChannelMessageSent(uint8_t, const uint8_t[32], const char*);
  void onContactMessageSent(const ContactInfo&, const char*);
};
void UI::onChannelMessageRecv(uint8_t index, const uint8_t* id, const char* text) { screen->onChannelMessageRecv(index,id,text); }
void UI::onChannelMessageSent(uint8_t index, const uint8_t* id, const char* text) { screen->onChannelMessageSent(index,id,text); }
void UI::onContactMessageRecv(const ContactInfo& contact, const char* text) { screen->onContactMessageRecv(contact,text); }
void UI::onContactMessageSent(const ContactInfo& contact, const char* text) { screen->onContactMessageSent(contact,text); }
struct Stored { std::vector<uint8_t> id; std::string text; bool out, channel; int meta_tag; };
struct Store {
  std::vector<Stored> messages;
  void storeMessage(const uint8_t* id, const char* text, bool out, bool channel,
                    const MessageMeta* meta = nullptr) {
    messages.push_back({std::vector<uint8_t>(id,id+32), text, out, channel, meta ? meta->tag : -1});
  }
};
struct Clock { uint32_t getMillis() const { return 5; } };
struct Tracker {
  uint32_t next = 0;
  uint32_t add(const uint8_t*, bool, uint32_t, uint8_t = 0xFF) { return ++next; }
};
struct CardputerMesh {
  Store* _store = nullptr;
  UI* _ui = nullptr;
  ChannelDetails channels[4];
  MessageMeta last_rx_meta;
  Tracker own_tracker;
  Clock clock;
  Clock* _ms = &clock;
  uint32_t last_own_id = 0, pending_channel_own_id = 0;
  bool ui_dm_sending = false;
  const MessageMeta& lastReceivedMeta() const { return last_rx_meta; }
  uint32_t lastOwnMessageId() const { return last_own_id; }
  bool getChannel(int index, ChannelDetails& channel) {
    if (index < 0 || index >= 4) return false;
    channel = channels[index]; return true;
  }
  static bool channelHistoryId(const ChannelDetails&, uint8_t[32]);
  bool getChannelHistoryId(int, uint8_t[32]);
  void onUiChannelMessage(uint8_t, const char*);
  void onUiContactMessage(const ContactInfo&, const char*);
  void onUiChannelMessageSent(uint8_t, const char*, size_t);
  void onUiContactMessageSent(const ContactInfo&, const char*);
};
CardputerMesh* mesh_instance;
#define the_mesh_cp (*mesh_instance)
@METHODS@
int main() {
  UI ui{}; ChatPage page{}; MainScreen main{&ui,&page,{}};
  ui.screen = &main; page._p = &main;
  Store store; CardputerMesh node;
  node._store = &store; node._ui = &ui;
  mesh_instance = &node;
  const char* labels[] = {"alpha", "r2-alpha", "mt-alpha", "mt-LongFast"};
  uint8_t ids[4][32];
  for (unsigned i=0; i<4; ++i) {
    strcpy(node.channels[i].name, labels[i]);
    memset(node.channels[i].channel.secret, 17, 16);
    assert(node.getChannelHistoryId(i, ids[i]));
    for (unsigned j=0; j<i; ++j) assert(memcmp(ids[i], ids[j], 32));
  }
  ChannelDetails tail = node.channels[2]; tail.channel.secret[31] = 9;
  uint8_t tail_id[32], padded_id[32];
  assert(CardputerMesh::channelHistoryId(tail, tail_id) && memcmp(tail_id, ids[2], 32));
  ChannelDetails padded = node.channels[2]; padded.name[31] = 'X';
  assert(CardputerMesh::channelHistoryId(padded, padded_id) && !memcmp(padded_id, ids[2], 32));
  ChannelDetails invalid{};
  assert(!CardputerMesh::channelHistoryId(invalid, padded_id));
  memset(invalid.name, 'x', sizeof(invalid.name));
  assert(!CardputerMesh::channelHistoryId(invalid, padded_id));

  page.current_channel_idx = 3; memcpy(page.current_channel_history_id, ids[3], 32);
  node.onUiChannelMessage(2, "other same-key channel");
  assert(page.chat_history.count() == 0 && main.unread.countChats() == 1);
  assert(store.messages.back().id == std::vector<uint8_t>(ids[2], ids[2]+32));
  node.last_rx_meta = MessageMeta{true, 42};
  node.onUiChannelMessage(3, "hidden selected chat");
  node.last_rx_meta = MessageMeta{};
  assert(page.chat_history.count() == 1 && main.unread.countChats() == 2);
  assert(store.messages.back().meta_tag == 42);
  const HistoryMessage* received = nullptr;
  assert(page.chat_history.get(0, received) && received->meta.has && received->meta.tag == 42);
  ui.visible = true;
  page.markRead(main.unread);
  assert(main.unread.countChats() == 1);
  node.onUiChannelMessage(3, "visible selected chat");
  assert(page.chat_history.count() == 2 && main.unread.countMessages() == 1);

  page.current_channel_idx = -1; page.current_contact_idx = 1;
  page.current_contact.id.pub_key[31] = 1;
  ContactInfo other = page.current_contact; other.id.pub_key[31] = 2;
  node.onUiContactMessage(other, "same six-byte prefix");
  assert(main.unread.countChats() == 2);
  ui.visible = false;
  node.onUiContactMessage(page.current_contact, "settings overlay");
  assert(main.unread.countChats() == 3);
  ui.visible = true; page.markRead(main.unread);
  assert(main.unread.countChats() == 2);

  page.chat_history.clear(); store.messages.clear();
  for (const char* origin : {"app", "USB terminal", "keyboard"}) {
    node.ui_dm_sending = !strcmp(origin, "keyboard");
    node.onUiContactMessageSent(page.current_contact, origin);
  }
  node.ui_dm_sending = false;
  assert(page.chat_history.count() == 3 && store.messages.size() == 3);
  const HistoryMessage* own = nullptr;
  assert(page.chat_history.get(0, own) && own->out && own->own_id == 0);
  assert(page.chat_history.get(2, own) && own->out && own->own_id == 1 && !own->meta.has);
  for (const auto& message : store.messages) assert(message.out && !message.channel);
  page.current_contact_idx = -1; page.current_channel_idx = 3;
  page.chat_history.clear(); store.messages.clear();
  for (const char* origin : {"app", "USB terminal", "keyboard"}) {
    node.pending_channel_own_id = strcmp(origin, "keyboard") ? 0 : 9; // Meshtext sends have no flood hash
    node.onUiChannelMessageSent(3, origin, strlen(origin));
    assert(node.pending_channel_own_id == 0);
  }
  assert(page.chat_history.count() == 3 && store.messages.size() == 3);
  assert(page.chat_history.get(0, own) && own->own_id == 0);
  assert(page.chat_history.get(2, own) && own->own_id == 9 && page.last_sent_message == "keyboard");
  for (const auto& message : store.messages) {
    assert(message.out && message.channel && message.id == std::vector<uint8_t>(ids[3],ids[3]+32));
  }
  assert(main.unread.countChats() == 2);
  char bounded[MAX_MESSAGE_LENGTH + 20]; memset(bounded, 'x', sizeof(bounded));
  node.onUiChannelMessageSent(3, bounded, sizeof(bounded));
  assert(store.messages.back().text.size() == MAX_MESSAGE_LENGTH);
}
'''


class CardputerChannelIdentityTests(unittest.TestCase):
    def test_identity_delivery_sent_history_and_visible_unread(self):
        mesh = (VARIANT / "companion_radio/CardputerMesh.cpp").read_text()
        main = (VARIANT / "companion_radio/ui-cardputer/screen/MainScreen.cpp").read_text()
        chat = (VARIANT / "companion_radio/ui-cardputer/screen/main/ChatPage.cpp").read_text()
        methods = []
        for source, signatures in (
            (mesh, ("bool CardputerMesh::channelHistoryId(", "bool CardputerMesh::getChannelHistoryId(",
                    "void CardputerMesh::onUiChannelMessage(", "void CardputerMesh::onUiContactMessage(",
                    "void CardputerMesh::onUiChannelMessageSent(", "void CardputerMesh::onUiContactMessageSent(")),
            (main, ("void MainScreen::onChannelMessageRecv(", "void MainScreen::onContactMessageRecv(",
                    "void MainScreen::onChannelMessageSent(", "void MainScreen::onContactMessageSent(")),
            (chat, ("bool ChatPage::onChannelMessageRecv(", "bool ChatPage::onContactMessageRecv(",
                    "void ChatPage::onChannelMessageSent(", "void ChatPage::onContactMessageSent(",
                    "void ChatPage::markRead(", "void ChatPage::onHistoryPushed()",
                    "void ChatPage::pushOwnMessage("))):
            methods.extend(body(source, signature) for signature in signatures)
        storage_tests.CardputerMeshStorageTests.run_cpp(
            self, HARNESS.replace("@METHODS@", "\n".join(methods)),
            flags=("-Wl,--no-as-needed", "-lcrypto"))


if __name__ == "__main__":
    unittest.main()
