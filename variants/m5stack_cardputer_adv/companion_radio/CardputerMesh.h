#pragma once

#include "CardputerDataStore.h"
#include "CardputerUITask.h"
#include "OwnMessageTracker.h"
#include "types.h"

#include <MyMesh.h>

#define CTL_TYPE_NODE_DISCOVER_REQ  0x80
#define CTL_TYPE_NODE_DISCOVER_RESP 0x90

class CardputerMesh : public MyMesh {
  CardputerUITask *_ui;
  CardputerDataStore *_store;
  uint64_t rx_packet_count = 0;
  uint32_t last_ping_tag = 0;
  uint32_t last_discover_tag = 0;
  uint8_t last_ping_key[PUB_KEY_SIZE] = { 0 };
  bool has_last_ping = false;
  uint8_t last_message_hash[MAX_HASH_SIZE] = { 0 };
  uint16_t last_message_heard_repeats = 0;
  OwnMessageTracker own_tracker;  // session-only delivery details of own messages
  uint32_t last_own_id = 0;       // own_id of the most recent own send (0 = none/failed)
  uint32_t last_dm_own_id = 0;    // own_id tracked by last_msg (DM resend state)
  uint32_t pending_channel_own_id = 0; // set by sendFloodScoped(), taken by onUiChannelMessageSent()
  bool ui_dm_sending = false;     // sendDirectMessage() is inside MyMesh::sendMessage()
  MessageMeta last_rx_meta;       // details of the most recently received message
  CustomNodePrefs _custom_prefs{ .battery_correction = 1.0f, .brightness = 128, .text_level = 1 };
  CustomNodePrefs _saved_custom_prefs = _custom_prefs;
  LastSentMessage last_msg;
  unsigned long resend_timeout = 0;

public:
  CardputerMesh(mesh::Radio &radio, mesh::RNG &rng, mesh::RTCClock &rtc, SimpleMeshTables &tables,
                CardputerDataStore &store, CardputerUITask *ui)
      : MyMesh(radio, rng, rtc, tables, store, ui), _ui(ui), _store(&store) {}

  void onTraceRecv(mesh::Packet *packet, uint32_t tag, uint32_t auth_code, uint8_t flags,
                   const uint8_t *path_snrs, const uint8_t *path_hashes, uint8_t path_len) override;
  void onControlDataRecv(mesh::Packet *packet) override;
  void captureRxMeta(const mesh::Packet *pkt, uint32_t sender_timestamp);
  mesh::DispatcherAction onRecvPacket(mesh::Packet *pkt) override;
  void onMessageRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                     const char *text) override;
  bool onSignedMessageRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                           const uint8_t *sender_prefix, const char *text) override;
  void onChannelMessageRecv(const mesh::GroupChannel &channel, mesh::Packet *pkt, uint32_t timestamp,
                            const char *text) override;
  void onUiChannelMessage(uint8_t channel_idx, const char *text) override;
  void onUiContactMessage(const ContactInfo &from, const char *text) override;
  void onUiContactMessageSent(const ContactInfo &recipient, const char *text) override;
  void onUiChannelMessageSent(uint8_t channel_idx, const char *text, size_t length) override;
  bool sendFloodScoped(const mesh::GroupChannel &channel, mesh::Packet *pkt, uint32_t delay_millis = 0) override;
  using MyMesh::sendFloodScoped;

  bool sendChannelMessage(int channel_idx, uint32_t timestamp, const char *text, size_t length);
  static bool channelHistoryId(const ChannelDetails &channel, uint8_t id[PUB_KEY_SIZE]);
  bool getChannelHistoryId(int channel_idx, uint8_t id[PUB_KEY_SIZE]);

  int sendDirectMessage(const ContactInfo &recipient, uint32_t timestamp, const char *text);

  void logRxRaw(float snr, float rssi, const uint8_t raw[], int len) override;

  void checkResend();

  bool processAck(const uint8_t *data, ContactInfo *&peer) override;

  inline uint64_t receivedPacketsCount() const { return rx_packet_count; }

  // Loads Cardputer UI prefs; call after MyMesh::begin().
  void beginCustom();
  bool savePrefs();
  bool saveCustomPrefs();
  bool sendPing(ContactInfo &contact);
  // Contact most recently passed to sendPing(); false if none or it was deleted.
  bool lastPingContact(ContactInfo &out) const;
  bool sendAdvert(bool flood);
  bool sendRepeatersDiscover();
  uint32_t getLastPingTag() const { return last_ping_tag; }
  inline CustomNodePrefs *getCustomNodePrefs() { return &_custom_prefs; }
  void loadMessageHistory(const uint8_t pkey[PUB_KEY_SIZE], bool is_channel, ChatHistory &history);
  void loop();
  void cancelResending();
  bool isMessageSending() { return last_msg.state == MESSAGE_SENDING; }
  // own_id of the message passed to the latest UI sent callback (0 if untracked).
  uint32_t lastOwnMessageId() const { return last_own_id; }
  const OwnMessageTracker &ownMessages() const { return own_tracker; }
  // Metadata of the message passed to the most recent UI receive callback.
  const MessageMeta &lastReceivedMeta() const { return last_rx_meta; }
};

extern CardputerMesh the_mesh_cp;
