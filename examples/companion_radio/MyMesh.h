#pragma once

#include <Arduino.h>
#include <Mesh.h>
#include "AbstractUITask.h"
#include "CompanionFeatures.h"

/*------------ Frame Protocol --------------*/
#define FIRMWARE_VER_CODE 14

#ifndef FIRMWARE_BUILD_DATE
#define FIRMWARE_BUILD_DATE "14 Aug 2026"
#endif

#ifndef FIRMWARE_VERSION
#define FIRMWARE_VERSION "v1.17.1"
#endif

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include <InternalFileSystem.h>
#elif defined(RP2040_PLATFORM)
#include <LittleFS.h>
#elif defined(ESP32)
#include <SPIFFS.h>
#endif

#include "DataStore.h"
#include "NodePrefs.h"

#if defined(ESP32_PLATFORM) && defined(WIFI_SSID) && !defined(WEBCONFIG_DISABLED)
#include <helpers/esp32/WebConfigServer.h>
#endif

#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
#include <helpers/CompanionMqttSetupPortal.h>
#include <helpers/MQTTPrefs.h>
#include <helpers/bridges/MQTTBridge.h>
#endif

#include <RTClib.h>
#include <helpers/ArduinoHelpers.h>
#include <helpers/BaseSerialInterface.h>
#include <helpers/CompanionMotaControl.h>
#include <helpers/IdentityStore.h>
#include <helpers/LogicalMessageCache.h>
#include <helpers/SimpleMeshTables.h>
#include <helpers/StaticPoolPacketManager.h>
#include <helpers/TerminalCommandTracker.h>
#include <helpers/TerminalDisplayFilter.h>
#include <target.h>

#ifdef COMPANION_MESH_CLOCK_SYNC
#include <helpers/ClientACL.h>
#include <helpers/MeshClockSync.h>
#endif

/* ---------------------------------- CONFIGURATION ------------------------------------- */

#ifndef LORA_FREQ
#define LORA_FREQ 915.0
#endif
#ifndef LORA_BW
#define LORA_BW 250
#endif
#ifndef LORA_SF
#define LORA_SF 10
#endif
#ifndef LORA_CR
#define LORA_CR 5
#endif
#ifndef LORA_TX_POWER
#define LORA_TX_POWER 20
#endif
#ifndef MAX_LORA_TX_POWER
#define MAX_LORA_TX_POWER LORA_TX_POWER
#endif

#ifndef MAX_CONTACTS
#define MAX_CONTACTS 100
#endif

#ifndef OFFLINE_QUEUE_SIZE
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
#define OFFLINE_QUEUE_SIZE 512
#elif defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM) \
    || defined(RP2040_PLATFORM)
#define OFFLINE_QUEUE_SIZE 256
#else
#define OFFLINE_QUEUE_SIZE 16
#endif
#endif

static_assert(OFFLINE_QUEUE_SIZE > 0, "OFFLINE_QUEUE_SIZE must be positive");

#ifndef ROOM_MESSAGE_TIMESTAMP_CACHE_SIZE
#define ROOM_MESSAGE_TIMESTAMP_CACHE_SIZE 16
#endif

#ifndef BLE_NAME_PREFIX
#define BLE_NAME_PREFIX "MeshCore-"
#endif

#include <helpers/BaseChatMesh.h>
#include <helpers/TransportKeyStore.h>

/* -------------------------------------------------------------------------------------- */

#define REQ_TYPE_GET_STATUS             0x01 // same as _GET_STATS
#define REQ_TYPE_KEEP_ALIVE             0x02
#define REQ_TYPE_GET_TELEMETRY_DATA     0x03

struct AdvertPath {
  uint8_t pubkey_prefix[7];
  uint8_t path_len;
  char    name[32];
  uint32_t recv_timestamp;
  uint8_t path[MAX_PATH_SIZE];
};

class MyMesh : public BaseChatMesh, public DataStoreHost, public UIShutdownGuard
#ifdef ENABLE_USB_INTERFACE
             , public ContactVisitor
#endif
#ifdef WITH_WEBCONFIG
             , public WebConfigServer::Callbacks
#endif
{
public:
  MyMesh(mesh::Radio &radio, mesh::RNG &rng, mesh::RTCClock &rtc, SimpleMeshTables &tables, DataStore& store, AbstractUITask* ui=NULL);

  void begin(bool has_display, bool radio_available = true);
  void activateRadio();
  bool isRadioReady() const { return _radio_available; }
  void startInterface(BaseSerialInterface &serial);
  void cancelSerialResponseStream();
  void cancelSerialOperationsForRoute(BaseSerialInterface* route);
  bool hasFiniteDelayedReplyForRoute(BaseSerialInterface* route) const;
  void resetUsbHostSessionInput();

  const char *getNodeName();
  CompanionNodePrefs *getNodePrefs();
  uint32_t getBLEPin();
  int getOfflineQueueCapacity() const;
  void noteInternetClockSet() {
#ifdef COMPANION_MESH_CLOCK_SYNC
    _clock_sync.onInternetClockSet();
#endif
  }
  void setMotaSourceControl(mesh::companion::MotaSourceControl* control) {
    _mota_source_control = control;
  }

#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
  void serviceMQTT(const char* wifi_ssid, const char* wifi_password);
  void stopMQTT();
  bool isMQTTConfigured() const { return _mqtt_configured; }
  bool isMQTTRunning() const {
    return _mqtt_started && _mqtt_bridge != nullptr
        && _mqtt_bridge->isRunning();
  }
  bool hasFreshMQTTNtpThisBoot() const {
    return _mqtt_bridge != nullptr
        && _mqtt_bridge->hasFreshNtpThisBoot();
  }
#endif

#ifdef WITH_WEBCONFIG
  bool startWebConfig(bool force_ap, char* reply);
  void stopWebConfig();
  void serviceWebConfig();
  bool isWebConfigActiveOrStopping() const;
  bool isWebConfigSetupActive() const;
  bool isWebConfigWiFiRecoveryActive() const;

  void getNodeSnapshot(WebConfigServer::NodeSnapshot& snapshot) override;
  void execCommand(char* cmd, char* reply) override;
  void rebootNow() override;
  void onConfigBatchStart() override;
  void onConfigBatchEnd() override;
  void buildStatsJson(char* buf, size_t buf_size) override;
#endif

  void loop();
  void handleCmdFrame(size_t len);
  bool advert();
  void enterCLIRescue();

#ifdef ENABLE_USB_INTERFACE
  void enterTerminalMode();
  void exitTerminalMode();
  // Clear state owned by the current text-terminal host without changing the
  // protocol owner or printing a new banner.
  void resetTerminalSession();
  bool isTerminalMode() const { return _terminal_mode; }
  void handleTerminalCommand(char* command);
#if COMPANION_FEATURE_NETWORK_TERMINAL
  bool enterNetworkTerminalMode(Stream& output);
  void exitNetworkTerminalMode(Stream& output);
  bool isNetworkTerminalMode(const Stream& output) const;
#endif
#endif

  // Local control shared by every USB terminal. ESP32 WiFi companions expose
  // setup/status commands here; Full builds add TempRadio and OTA commands.
  bool handleLocalControlCommand(const char* command, char* reply,
                                 size_t reply_size);

  int  getRecentlyHeard(AdvertPath dest[], int max_num);

protected:
#if COMPANION_FEATURE_TEMP_RADIO
  bool isTempRadioActive() const override {
    return _temp_radio_applied && _temp_radio_revert_at != 0
        && !millisHasNowPassed(_temp_radio_revert_at);
  }
#endif
  float getAirtimeBudgetFactor() const override;
  int getInterferenceThreshold() const override;
  bool getCADEnabled() const override;
  uint32_t getCADFailRetryDelay() const override;
  uint32_t getCADFailMaxDuration() const override;
#ifdef WITH_MQTT_BRIDGE
  uint32_t getRadioWatchdogMillis() const override { return 0; }
#endif
  int calcRxDelay(float score, uint32_t air_time) const override;
  uint32_t getRetransmitDelay(const mesh::Packet *packet) override;
  uint32_t getDirectRetransmitDelay(const mesh::Packet *packet) override;
  uint8_t getDefaultTxCodingRate() const override { return _prefs.cr; }
  uint8_t getExtraAckTransmitCount() const override;
  bool filterRecvFloodPacket(mesh::Packet* packet) override;
  bool allowPacketForward(const mesh::Packet* packet) override;
  bool allowFloodRetry(const mesh::Packet* packet) const override;
#ifdef COMPANION_MESH_CLOCK_SYNC
  void onAdvertRecv(mesh::Packet* packet, const mesh::Identity& id,
                    uint32_t timestamp, const uint8_t* app_data,
                    size_t app_data_len) override;
  void onGroupPacketRecv(mesh::Packet* packet) override;
#endif

  bool sendFloodScoped(const TransportKey& scope, mesh::Packet* pkt, uint32_t delay_millis);
  bool sendFloodScoped(const ContactInfo& recipient, mesh::Packet* pkt, uint32_t delay_millis=0) override;
  bool sendFloodScoped(const mesh::GroupChannel& channel, mesh::Packet* pkt, uint32_t delay_millis=0) override;

  void logRxRaw(float snr, float rssi, const uint8_t raw[], int len) override;
#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
  void logRx(mesh::Packet* packet, int len, float score) override;
  void logTx(mesh::Packet* packet, int len) override;
#endif
  bool isAutoAddEnabled() const override;
  bool shouldAutoAddContactType(uint8_t type) const override;
  bool shouldOverwriteWhenFull() const override;
  uint8_t getAutoAddMaxHops() const override;
  void onContactsFull() override;
  void onContactOverwrite(const uint8_t* pub_key) override;
  bool onContactPathRecv(ContactInfo& from, uint8_t* in_path, uint8_t in_path_len, uint8_t* out_path, uint8_t out_path_len, uint8_t extra_type, uint8_t* extra, uint8_t extra_len) override;
  void onDiscoveredContact(ContactInfo &contact, bool is_new, uint8_t path_len, const uint8_t* path) override;
  void onContactPathUpdated(const ContactInfo &contact) override;
#ifdef ENABLE_USB_INTERFACE
  void onContactVisit(const ContactInfo& contact) override;
#endif
  ContactInfo* processAck(const uint8_t *data) override;
  void queueMessage(const ContactInfo &from, uint8_t txt_type, mesh::Packet *pkt, uint32_t sender_timestamp,
                    const uint8_t *extra, int extra_len, const char *text,
                    bool terminal_command_reply=false,
                    uint32_t terminal_command_elapsed_millis=0);

  void onMessageRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                     const char *text) override;
  void onCommandDataRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                         const char *text) override;
  void onCLICommandRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                         const char *text, char* reply) override;
  void onSignedMessageRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                           const uint8_t *sender_prefix, const char *text) override;
  void onChannelMessageRecv(const mesh::GroupChannel &channel, mesh::Packet *pkt, uint32_t timestamp,
                            const char *text) override;
  void onChannelDataRecv(const mesh::GroupChannel &channel, mesh::Packet *pkt, uint16_t data_type,
                         const uint8_t *data, size_t data_len) override;

  uint8_t onContactRequest(const ContactInfo &contact, uint32_t sender_timestamp, const uint8_t *data,
                           uint8_t len, uint8_t *reply) override;
  void onContactResponse(const ContactInfo &contact, const uint8_t *data, uint8_t len) override;
  void onControlDataRecv(mesh::Packet *packet) override;
  void onRawDataRecv(mesh::Packet *packet) override;
  void onTraceRecv(mesh::Packet *packet, uint32_t tag, uint32_t auth_code, uint8_t flags,
                   const uint8_t *path_snrs, const uint8_t *path_hashes, uint8_t path_len) override;

  uint32_t calcFloodTimeoutMillisFor(uint32_t pkt_airtime_millis) const override;
  uint32_t calcDirectTimeoutMillisFor(uint32_t pkt_airtime_millis, uint8_t path_len) const override;
  void onSendTimeout() override;

  // DataStoreHost methods
  bool onContactLoaded(const ContactInfo& contact) override { return addContact(contact); }
  bool getContactForSave(uint32_t idx, ContactInfo& contact) override { return getContactByIdx(idx, contact); }
  ContactInfo* getContactForStore(uint32_t idx) override { return getContactPtrByIdx(idx); }
  bool onChannelLoaded(uint8_t channel_idx, const ChannelDetails& ch) override { return setChannel(channel_idx, ch); }
  bool getChannelForSave(uint8_t channel_idx, ChannelDetails& ch) override { return getChannel(channel_idx, ch); }

  void clearPendingReqs();
  bool hasPendingReqs() const;

public:
  bool savePrefs() {
    const bool saved =
        _store->savePrefs(_prefs, sensors.node_lat, sensors.node_lon);
    if (saved) _prefs.clearDirty();
    return saved;
  }

#if ENV_INCLUDE_GPS == 1
  void applyGpsPrefs() {
    sensors.setSettingValue("gps", _prefs.gps_enabled ? "1" : "0");
    char interval_str[12];  // Max: 24 hours = 86400 seconds (5 digits + null)
    sprintf(interval_str, "%u", _prefs.gps_interval);
    sensors.setSettingValue("gps_interval", interval_str);
  }
#endif

  // To check if there is pending work
  bool hasPendingWork() const;

private:
  void writeOKFrame(BaseSerialInterface* route = nullptr);
  void writeErrFrame(uint8_t err_code,
                     BaseSerialInterface* route = nullptr);
  size_t writePendingSerialFrame(const uint8_t frame[], size_t len);
  void writeDisabledFrame();
  void writeContactRespFrame(uint8_t code, const ContactInfo &contact);
  void stopContactsIterator();
  void updateContactFromFrame(ContactInfo &contact, uint32_t& last_mod, const uint8_t *frame, int len);
  void addToOfflineQueue(const uint8_t frame[], int len);
  int getFromOfflineQueue(uint8_t frame[]);
  int getBlobByKey(const uint8_t key[], int key_len, uint8_t dest_buf[]) override { 
    return _store->getBlobByKey(key, key_len, dest_buf);
  }
  bool putBlobByKey(const uint8_t key[], int key_len, const uint8_t src_buf[], int len) override {
    return _store->putBlobByKey(key, key_len, src_buf, len);
  }

  void checkCLIRescueCmd();
  bool handleCommand(const char* text, uint32_t sender_timestamp, char* reply);
  void checkSerialInterface();
  bool applyAndSaveFemRxGain(bool enabled);
  bool applyAndSaveFemTxGain(bool enabled);
  bool applyAndSaveRxBoostedGain(bool enabled);
  bool handleCadCommand(const char* command, char* reply, size_t reply_size);
  bool saveBluetoothNameOverride(const char* name);
  bool applyAndSaveBluetoothName(const char* value, char* reply,
                                 size_t reply_size);
  void formatBluetoothNameStatus(char* reply, size_t reply_size) const;
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  bool applyAndSaveEspNowChannel(const char* value, char* reply,
                                 size_t reply_size);
  void formatEspNowChannel(char* reply, size_t reply_size) const;
#endif
  bool applyAndSavePowerSaving(const char* value, char* reply);
  bool applyAndSaveRxPowerSaving(const char* value, char* reply);
  void appendRxPowerSavingAdjustmentNote(char* reply, size_t reply_size,
                                         uint8_t sf, float bw) const;
#if defined(ESP32) && defined(WIFI_SSID)
  bool applyAndSaveWiFiPowerSaving(const char* value, char* reply,
                                   size_t reply_size);
  void formatWiFiPowerSaving(char* reply, size_t reply_size) const;
  void syncWiFiPowerSaving();
#endif
#ifdef ENABLE_USB_INTERFACE
  Stream& terminalOutput();
  bool hasTerminalOutput() const { return _terminal_output != NULL; }
  void printTerminalBanner(bool show_binary_stop);
  ContactInfo* getTerminalRecipient();
  void printTerminalPath(const ContactInfo& recipient);
  void handleTerminalPath(ContactInfo& recipient, const char* path_spec);
  void importTerminalCard(char* command);
  void listTerminalChannels();
  void sendTerminalChannelMessage(ChannelDetails& channel, const char* text);
  void handleTerminalDisplayCommand(const char* arguments);
  void printTerminalSendStatus(const char* operation,
                               const ContactInfo& recipient, int result,
                               uint32_t timeout_millis);
  void clearTerminalLogin();
  void serviceTerminalLogin();
  void sendTerminalLogin(ContactInfo& recipient, const char* password);
  void clearTerminalCommand();
  void serviceTerminalCommand();
  void sendTerminalCommand(ContactInfo& recipient, const char* command);
  void clearTerminalTrace();
  void serviceTerminalTrace();
  void sendTerminalTraceRoute(const uint8_t* route, uint8_t hash_size,
                              uint8_t hop_count, const char* target);
  void sendTerminalTrace(ContactInfo& recipient);
  void sendTerminalRawTrace(const char* arguments);
#endif
  bool isValidClientRepeatFreq(uint32_t f) const;
  bool hasLocationTelemetryRecipient();
  void updateGpsTelemetryPolicy();
  mesh::RadioParamApplyResult tryApplyRadioParams(float freq, float bw, uint8_t sf, uint8_t cr);
  bool applySavedRadioParams();
  void configureRadioFromPrefs();
  void finishRadioParamApply(float freq, float bw, uint8_t sf, uint8_t cr,
                             uint8_t repeat,
                             BaseSerialInterface* route = nullptr);
  void cancelPendingRadioParamApply();
  void servicePendingRadioParamApply();
  void servicePendingSerialReply();
  void clearBinaryTraceReply();
  void serviceBinaryTraceReply();
  void cancelSigningSession();
  void serviceSigningSession();
#if COMPANION_FEATURE_TEMP_RADIO
  bool scheduleTempRadio(float freq, float bw, uint8_t sf, uint8_t cr,
                         uint32_t timeout_mins, char* reply, size_t reply_size);
  void scheduleNormalRadio(char* reply, size_t reply_size);
  void serviceTempRadio();
#endif

  // helpers, short-cuts
  bool saveChannels() { return _store->saveChannels(this); }
  void saveContacts();
  void scheduleContactWriteRetry();
  bool isContactWriteDue() const;
  bool flushContactsBeforeReboot();
  bool prepareForOtaReboot() override;
  bool prepareForUiShutdown() override;
  void scheduleContactWrite(const ContactInfo& contact);
  void scheduleContactWriteAfterRelease(const ContactInfo& contact);
#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
  void repairInternalExtraFS(Stream& output);
#endif

  DataStore* _store;
  CompanionNodePrefs _prefs;
#ifdef COMPANION_MESH_CLOCK_SYNC
  ArduinoMillis _clock_sync_millis;
  ClientACL _clock_sync_acl;
  mesh::MeshClockSync _clock_sync;
#endif
#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
  MQTTPrefs _mqtt_prefs;
  MQTTBridge* _mqtt_bridge;
  bool _mqtt_configured;
  bool _mqtt_started;
#endif
#ifdef WITH_WEBCONFIG
  WebConfigServer* _webconfig;
  bool _wc_mqtt_dirty;
#endif
  uint32_t pending_login;
  uint32_t pending_status;
  uint32_t pending_telemetry, pending_discovery;   // pending _TELEMETRY_REQ
  uint32_t pending_req;   // pending _BINARY_REQ
  BaseSerialInterface* pending_serial_reply_route;
  unsigned long pending_serial_reply_deadline;
  BaseSerialInterface *_serial;
  mesh::companion::MotaSourceControl* _mota_source_control;
  AbstractUITask* _ui;

  ContactsIterator _iter;
  uint32_t _iter_filter_since;
  uint32_t _most_recent_lastmod;
  uint32_t _active_ble_pin;
  bool _iter_started;
  bool _cli_rescue;
#ifdef ENABLE_USB_INTERFACE
  bool _terminal_mode;
  Stream* _terminal_output;
  mesh::TerminalDisplayFilter _terminal_display;
  bool _terminal_recipient_set;
  uint8_t _terminal_recipient_key[PUB_KEY_SIZE];
  uint8_t _terminal_tmp_buf[MAX_TRANS_UNIT];
  bool _terminal_login_pending;
  uint8_t _terminal_login_key[4];
  unsigned long _terminal_login_expires_at;
  char _terminal_login_target[32];
  mesh::TerminalCommandTracker<PUB_KEY_SIZE> _terminal_command;
  char _terminal_command_target[32];
  bool _terminal_trace_pending;
  uint8_t _terminal_trace_hash_size;
  uint32_t _terminal_trace_tag;
  uint32_t _terminal_trace_auth;
  unsigned long _terminal_trace_sent_at;
  unsigned long _terminal_trace_expires_at;
  char _terminal_trace_target[32];
#endif
  bool saved_radio_apply_pending;
  bool _radio_available;
  unsigned long radio_apply_retry_at;
  uint8_t radio_apply_failures;
  bool command_radio_apply_pending;
  float command_radio_freq;
  float command_radio_bw;
  uint8_t command_radio_sf;
  uint8_t command_radio_cr;
  uint8_t command_radio_repeat;
  unsigned long command_radio_apply_deadline;
  BaseSerialInterface* command_radio_reply_route;
  bool binary_trace_pending;
  uint32_t binary_trace_tag;
  uint32_t binary_trace_auth;
  unsigned long binary_trace_deadline;
  BaseSerialInterface* binary_trace_reply_route;
  // Deferred so USB/TCP terminals can transmit the acknowledgement before
  // the transport disappears. Also used by USB interface changes.
  unsigned long _scheduled_reboot_at;
#if COMPANION_FEATURE_TEMP_RADIO
  unsigned long _temp_radio_set_at;
  unsigned long _temp_radio_revert_at;
  unsigned long _temp_radio_retry_at;
  float _temp_radio_freq;
  float _temp_radio_bw;
  uint8_t _temp_radio_sf;
  uint8_t _temp_radio_cr;
  uint8_t _temp_radio_failures;
  bool _temp_radio_applied;
#endif
  bool send_unscoped;   // force un-scoped flood (instead of using send_scope)
  char cli_command[80];
  char reply_buf[166];
  uint8_t app_target_ver;
  uint8_t *sign_data;
  uint32_t sign_data_len;
  BaseSerialInterface* sign_data_reply_route;
  unsigned long sign_data_deadline;
  unsigned long dirty_contacts_expiry;
  uint8_t dirty_contacts_failures;

  TransportKey send_scope;

  uint8_t cmd_frame[MAX_FRAME_SIZE + 1];
  uint8_t out_frame[MAX_FRAME_SIZE + 1];
  CayenneLPP telemetry;

  struct Frame {
    uint8_t len;
    uint8_t buf[MAX_FRAME_SIZE];

    bool isChannelMsg() const;
  };
  Frame& offlineQueueFrameAt(int logical_index);
  void initializeOfflineQueue();
  int offline_queue_len;
  int offline_queue_head;
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
  enum {
    OFFLINE_QUEUE_PSRAM_FALLBACK_SIZE =
        OFFLINE_QUEUE_SIZE < 16 ? OFFLINE_QUEUE_SIZE : 16
  };
  Frame* offline_queue;
  Frame offline_queue_fallback[OFFLINE_QUEUE_PSRAM_FALLBACK_SIZE];
  int offline_queue_capacity;
#else
  Frame offline_queue[OFFLINE_QUEUE_SIZE];
#endif

  struct AckTableEntry {
    unsigned long msg_sent;
    unsigned long expires_at;
    uint32_t ack;
    uint32_t message_timestamp;
    ContactInfo* contact;
    uint8_t text_fingerprint[MAX_HASH_SIZE];
    uint8_t retry_key[MAX_HASH_SIZE];
    BaseSerialInterface* reply_route;
#ifdef ENABLE_USB_INTERFACE
    bool terminal_origin;
#endif
  };
  #define EXPECTED_ACK_TABLE_SIZE 8
  AckTableEntry expected_ack_table[EXPECTED_ACK_TABLE_SIZE]; // circular table
  mesh::LogicalMessageCache<ROOM_MESSAGE_TIMESTAMP_CACHE_SIZE> room_message_timestamps;
  int next_ack_idx;
  unsigned long next_ack_expiry;
  bool has_next_ack_expiry;

  void clearExpectedAck(AckTableEntry& entry, bool cancel_retries = true);
  void expireExpectedAcks();
  AckTableEntry* findPendingTextMessage(
      const uint8_t text_fingerprint[MAX_HASH_SIZE], uint32_t message_timestamp);
#ifdef ENABLE_USB_INTERFACE
  void rememberTerminalAck(ContactInfo& recipient, const char* text,
                           uint32_t message_timestamp, uint32_t expected_ack,
                           uint32_t est_timeout,
                           const uint8_t packet_retry_key[MAX_HASH_SIZE],
                           AckTableEntry* replacement_entry);
#endif

  #define ADVERT_PATH_TABLE_SIZE   16
  AdvertPath advert_paths[ADVERT_PATH_TABLE_SIZE]; // circular table
};

extern MyMesh the_mesh;
