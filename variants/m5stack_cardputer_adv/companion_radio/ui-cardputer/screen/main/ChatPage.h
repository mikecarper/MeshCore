#pragma once

#include "../CardputerScreen.h"
#include "../MainScreen.h"
#include "ChatFocus.h"
#include "companion_radio/CardputerMesh.h"

class ChatPage : public CardputerScreen {
private:
  MainScreen *_p;
  String chat_text_box;
  String last_sent_message;

  ChannelDetails current_channel;
  uint8_t current_channel_history_id[PUB_KEY_SIZE] = {};
  int current_channel_idx = -1;

  ContactInfo current_contact;
  int current_contact_idx = -1;

  ChatHistory chat_history;
  int chat_history_offset = 0; // first drawn message, counted from the newest
  ChatFocus focus;             // text box / message / message menu focus

  void sendChatMessage();
  bool sendText(const char *text);
  const HistoryMessage *messageAt(int from_newest) const;
  size_t maxInputLength();
  void setTextBox(const char *prefix, const char *text);
  void onHistoryPushed();
  void pushOwnMessage(const char *text);
  void doMenuAction(ChatFocus::Action action, int msg_idx);
  void buildInfo(const HistoryMessage &m, char *out, size_t cap);
  void renderMenu(CardputerDisplay &lcd, int top, int bottom, bool own);
  void renderInfo(CardputerDisplay &lcd, int top, const HistoryMessage &m);
  bool refreshSelectedChannel();

public:
  ChatPage(MainScreen *parent) : _p(parent) { chat_text_box.reserve(MAX_MESSAGE_LENGTH); };
  int render(CardputerDisplay &lcd) override;
  bool handleInput(Keyboard::Event &e) override;
  void selectContact(const ContactInfo &contact, int idx);
  void selectChannel(const ChannelDetails &channel, int idx);

  bool onChannelMessageRecv(uint8_t channel_idx, const uint8_t history_id[32], const char *text);
  bool onContactMessageRecv(const ContactInfo &contact, const char *text);
  void onChannelMessageSent(uint8_t channel_idx, const uint8_t history_id[32], const char *text);
  void onContactMessageSent(const ContactInfo &contact, const char *text);
  void markRead(UnreadCounter &unread) const;

  void refreshSelectedContact();
};
