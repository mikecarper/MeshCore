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
#include <helpers/RadioProfileCLI.h>
#include <helpers/CompanionNotificationPolicy.h>
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
#include <helpers/CompanionDelayedReplies.h>
#if MESH_ENABLE_LOST_REPLY
#include <helpers/CompanionLostReply.h>
#endif
#include <helpers/CompanionMotaControl.h>
#include <helpers/IdentityStore.h>
#include <helpers/LogicalMessageCache.h>
#include <helpers/SimpleMeshTables.h>
#include <helpers/StaticPoolPacketManager.h>
#include <helpers/TerminalCommandTracker.h>
#include <helpers/TerminalDisplayFilter.h>
#include <helpers/UsbLogging.h>
#include <target.h>
#if defined(OTA_SHARED_COMPANION_QUEUE)
#include <helpers/BorrowableFrameBuffer.h>
#include <helpers/ota/OtaContext.h>
#endif

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

// STM32WL Companion images have only 224 KiB of application flash. Keep their
// existing filesystem boundary; larger targets retain the one-key DM feature.
#ifndef MESH_ENABLE_ONE_KEY_DM
#if defined(STM32_PLATFORM)
#define MESH_ENABLE_ONE_KEY_DM 0
#else
#define MESH_ENABLE_ONE_KEY_DM 1
#endif
#endif

#if defined(NRF52_PLATFORM) && MAX_CONTACTS > 300 \
    && !defined(OTA_SHARED_COMPANION_QUEUE) && MESH_CONTACT_CACHE
#define ONE_KEY_DM_SHARED_OFFLINE_QUEUE 1
#else
#define ONE_KEY_DM_SHARED_OFFLINE_QUEUE 0
#endif

#ifndef OFFLINE_QUEUE_SIZE
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
#define OFFLINE_QUEUE_SIZE 512
#elif defined(NRF52_PLATFORM) && MAX_CONTACTS > 300 \
    && !defined(OTA_SHARED_COMPANION_QUEUE) && !ONE_KEY_DM_SHARED_OFFLINE_QUEUE
#define OFFLINE_QUEUE_SIZE 208
#elif defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM) \
    || defined(RP2040_PLATFORM)
#define OFFLINE_QUEUE_SIZE 256
#else
#define OFFLINE_QUEUE_SIZE 16
#endif
#endif

static_assert(OFFLINE_QUEUE_SIZE > 0, "OFFLINE_QUEUE_SIZE must be positive");
#if ONE_KEY_DM_SHARED_OFFLINE_QUEUE
static_assert(OFFLINE_QUEUE_SIZE >= 15, "Shared DM queue needs 15 frame slots");
#endif

#ifndef ROOM_MESSAGE_TIMESTAMP_CACHE_SIZE
#define ROOM_MESSAGE_TIMESTAMP_CACHE_SIZE 16
#endif

#ifndef BLE_NAME_PREFIX
#define BLE_NAME_PREFIX "MeshCore-"
#endif

#include <helpers/BaseChatMesh.h>
#include <helpers/TransportKeyStore.h>

#if ONE_KEY_DM_SHARED_OFFLINE_QUEUE
static_assert(MAX_TEXT_LEN + 12 <= MAX_FRAME_SIZE,
              "A held plain DM must fit in one offline frame");
#endif

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

#if defined(DISPLAY_CLASS) && !(defined(UI_NO_DISCOVER_SCREEN) && (UI_NO_DISCOVER_SCREEN + 0 != 0))
struct DiscoveredNode {
  uint8_t pubkey_prefix[9];
  float snr_in;
  float snr_out;
  char name[32];
  uint8_t type;
};
#endif

namespace mesh { namespace ui { class StartupScreen; } }

class MyMesh : public BaseChatMesh, public DataStoreHost, public UIShutdownGuard
#if COMPANION_FEATURE_NOTIFICATIONS
             , public mesh::notify::Sink, public mesh::notify::Store
#endif
#if COMPANION_FEATURE_TEXT_TERMINAL
             , public ContactVisitor
#endif
#ifdef WITH_WEBCONFIG
             , public WebConfigServer::Callbacks
#endif
{
public:
  MyMesh(mesh::Radio &radio, mesh::RNG &rng, mesh::RTCClock &rtc, SimpleMeshTables &tables, DataStore& store, AbstractUITask* ui=NULL);

  bool handleLocalCommand(const char* command, char* reply) {
    return handleCommand(command, 0, reply);
  }

  void begin(bool has_display, bool radio_available = true,
             mesh::ui::StartupScreen* startup_screen = nullptr);
  void activateRadio();
  bool isRadioReady() const { return _radio_available; }
  void startInterface(BaseSerialInterface &serial);
  void cancelSerialResponseStream(BaseSerialInterface* route = NULL);
  void cancelSerialOperationsForRoute(BaseSerialInterface* route);
  bool hasFiniteDelayedReplyForRoute(BaseSerialInterface* route) const;
  void resetUsbHostSessionInput();

  const char *getNodeName();
  CompanionNodePrefs *getNodePrefs();
  uint32_t getBLEPin();
  bool setBluetoothEnabledPreference(bool enabled);
  bool isBluetoothEnabledPreference() const;
#if defined(BLE_PIN_CODE)
  bool prepareBluetoothMacForBoot(bool& address_rotated);
  bool armBluetoothMacRotationAfterConnection();
  bool saveBluetoothStealthPeer(
      const mesh::companion::BluetoothPeerIdentity& peer);
  bool resetBluetoothStealthPairing();
#endif
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
  bool supportsCliTerminal() const override { return true; }
  void execAdminCommand(char* cmd, char* reply) override;
#if COMPANION_FEATURE_TEXT_TERMINAL
  bool supportsStreamTerminal() const override { return true; }
  bool beginStreamTerminal(Stream& output) override;
  bool ownsStreamTerminal(const Stream& output) const override {
    return isNetworkTerminalMode(output);
  }
  void runStreamTerminal(char* command) override { handleTerminalCommand(command); }
  void endStreamTerminal(Stream& output) override;
#endif
  void rebootNow() override;
  void onConfigBatchStart() override;
  void onConfigBatchEnd() override;
  void buildStatsJson(char* buf, size_t buf_size) override;
#endif

  void loop();
  #if COMPANION_FEATURE_NOTIFICATIONS
  uint8_t capabilities() const override;
  bool gpioAvailable(uint8_t pin) const override;
  void pulse(mesh::notify::Output output, bool on, int8_t pin) override;
  void melody(const char* text) override;
  void screen(int8_t mode) override;
  bool save(const mesh::notify::Settings& settings) override;
  bool notificationButton() { return _notifications.button(); }
  void setNotificationOutputMute(mesh::notify::Output output, bool muted);
  #else
  bool notificationButton() { return false; }
  void setNotificationOutputMute(mesh::notify::Output, bool) {}
  #endif
  bool handleNotificationCommand(const char* command, char* reply, size_t size);
#if defined(STM32_PLATFORM)
  // Keep this large dispatcher separate from contact streaming on flash-
  // constrained STM32 targets; merging their branches increases code size.
  __attribute__((noinline))
#endif
  void handleCmdFrame(size_t len);
  bool advert();
  void enterCLIRescue();
  // Physical controls must flush deferred contact writes before entering a
  // board's irreversible power-off state.
  bool prepareForUserShutdown() { return prepareForUiShutdown(); }

#if COMPANION_FEATURE_TEXT_TERMINAL
  void enterTerminalMode(bool show_banner = true);
  void exitTerminalMode();
  // Clear state owned by the current text-terminal host without changing the
  // protocol owner or printing a new banner.
  void resetTerminalSession();
  bool isTerminalMode() const { return _terminal_mode; }
  bool isTerminalWaitingForInput() const {
    return _terminal_mode && _terminal_usb_silent;
  }
  void handleTerminalCommand(char* command);
#if COMPANION_FEATURE_NETWORK_TERMINAL || defined(WITH_WEBCONFIG)
  bool enterNetworkTerminalMode(Stream& output);
  void exitNetworkTerminalMode(Stream& output);
  bool isNetworkTerminalMode(const Stream& output) const;
  bool isAnyNetworkTerminalMode() const {
    return !_terminal_mode && _terminal_output != NULL;
  }
#endif
#endif

  // Local control shared by every USB terminal. ESP32 WiFi companions expose
  // setup/status commands here; Full builds add TempRadio and OTA commands.
  bool handleLocalControlCommand(const char* command, char* reply,
                                 size_t reply_size);
  void applyUsbLoggingState(bool enabled);
  // A framed USB logging change cannot abandon its requester before the
  // acknowledgement has entered the transport. Other transports stay live.
  void beginUsbLoggingReplyBarrier(BaseSerialInterface* route);
  void endUsbLoggingReplyBarrier(bool reply_queued);
  bool handleTxRoutingCommand(const char* command, char* reply, size_t reply_size);

  int  getRecentlyHeard(AdvertPath dest[], int max_num);

#if defined(DISPLAY_CLASS) && !(defined(UI_NO_DISCOVER_SCREEN) && (UI_NO_DISCOVER_SCREEN + 0 != 0))
  bool requestRepeatersDiscovery();
  int getDiscoveredNodes(DiscoveredNode nodes[], int max_num);
#endif

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
  int getAGCResetInterval() const override {
    return ((int)_prefs.agc_reset_interval) * 4000;   // milliseconds
  }
  int calcRxDelay(float score, uint32_t air_time) const override;
  uint32_t getRetransmitDelay(const mesh::Packet *packet) override;
  uint32_t getDirectRetransmitDelay(const mesh::Packet *packet) override;
  uint8_t getDefaultTxCodingRate() const override { return _prefs.cr; }
  uint8_t getExtraAckTransmitCount() const override;
  bool filterRecvFloodPacket(mesh::Packet* packet) override;
  bool allowPacketForward(const mesh::Packet* packet) override;
  bool allowFloodRetry(const mesh::Packet* packet) const override;
  uint8_t getFloodRetryMaxPathLength(const mesh::Packet* packet) const override;
  uint8_t getFloodRetryMaxAttempts(const mesh::Packet* packet) const override;
#ifdef COMPANION_MESH_CLOCK_SYNC
  void onAdvertRecv(mesh::Packet* packet, const mesh::Identity& id,
                    uint32_t timestamp, const uint8_t* app_data,
                    size_t app_data_len) override;
  void onGroupPacketRecv(mesh::Packet* packet) override;
#endif

  bool sendFloodScoped(const TransportKey& scope, mesh::Packet* pkt, uint32_t delay_millis);
  bool sendFloodScoped(const ContactInfo& recipient, mesh::Packet* pkt, uint32_t delay_millis=0) override;
  bool sendFloodScoped(const mesh::GroupChannel& channel, mesh::Packet* pkt, uint32_t delay_millis=0) override;
#if MESH_ENABLE_FLEET_CONTROL
  bool sendFleetCommandData(mesh::GroupChannel& channel, const uint8_t* envelope, size_t length);
  void onRadioProfileCopyQueued(mesh::Packet* packet, const mesh::Packet* original, uint8_t priority) override;
#endif

  void logRxRaw(float snr, float rssi, const uint8_t raw[], int len) override;
#if MESH_PACKET_LOGGING && !MESH_PACKET_LOGGING_COMPACT
  const char* getLogDateTime() override;
#endif
#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
  void logRx(mesh::Packet* packet, int len, float score) override;
  void logTx(mesh::Packet* packet, int len) override;
#endif
  bool isAutoAddEnabled() const override;
  bool shouldAutoAddContactType(uint8_t type) const override;
  bool shouldOverwriteWhenFull() const override;
  uint8_t getAutoAddMaxHops() const override;
  bool canMutateContacts() const override;
  void onContactsFull() override;
  bool onContactOverwrite(const ContactInfo& contact) override;
  void onContactReferenceChanged(const ContactInfo* previous, ContactInfo* replacement) override;
  bool onContactPathRecv(ContactInfo& from, uint8_t* in_path, uint8_t in_path_len, uint8_t* out_path, uint8_t out_path_len, uint8_t extra_type, uint8_t* extra, uint8_t extra_len) override;
  void onDiscoveredContact(ContactInfo &contact, bool is_new, uint8_t path_len, const uint8_t* path) override;
  void onContactPathUpdated(const ContactInfo &contact) override;
#if COMPANION_FEATURE_TEXT_TERMINAL
  void onContactVisit(const ContactInfo& contact) override;
#endif
  bool processAck(const uint8_t *data, ContactInfo*& peer) override;
#if MESH_ENABLE_ONE_KEY_DM
  void onAnonDataRecv(mesh::Packet* packet, const uint8_t* secret,
                      const mesh::Identity& sender, uint8_t* data,
                      size_t len) override;
  bool onAddressedTextPacket(mesh::Packet* packet, uint8_t src_hash,
                             const uint8_t* mac_and_data, size_t len) override;
#endif
  bool queueMessage(const ContactInfo &from, uint8_t txt_type, mesh::Packet *pkt, uint32_t sender_timestamp,
                    const uint8_t *extra, int extra_len, const char *text,
                    bool terminal_command_reply=false,
                    uint32_t terminal_command_elapsed_millis=0);

  void onMessageRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                     const char *text) override;
  void onCommandDataRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                         const char *text) override;
  void onCLICommandRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                         const char *text, char* reply) override;
  bool onSignedMessageRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
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
  bool allowRequestTag(uint32_t tag) override;
  bool allocateRequestTag(uint32_t& tag) override;

  // DataStoreHost methods
  bool onContactLoaded(const ContactInfo& contact) override { return addContact(contact); }
  bool getContactForSave(uint32_t idx, ContactInfo& contact) override { return getContactByIdx(idx, contact); }
  ContactInfo* getContactForStore(uint32_t idx) override { return getContactPtrByIdx(idx); }
  void onContactCacheFlushed() override;
  bool onChannelLoaded(uint8_t channel_idx, const ChannelDetails& ch) override { return setChannel(channel_idx, ch); }
  bool getChannelForSave(uint8_t channel_idx, ChannelDetails& ch) override { return getChannel(channel_idx, ch); }

  void clearPendingReqs();
  bool hasPendingReqs() const;

public:
  bool savePrefs() {
    const uint8_t previous_usb_debug = _prefs.usb_debug_enabled;
    _prefs.usb_debug_enabled = _prefs.usb_debug_enabled == 1 ? 1 : 0;
    const bool saved =
        _store->savePrefs(_prefs, sensors.node_lat, sensors.node_lon);
    if (saved) {
      _prefs.clearDirty();
#if MESH_USB_LOGGING_AVAILABLE
      mesh::setUsbDebugEnabled(_prefs.usb_debug_enabled != 0);
#endif
    } else {
      _prefs.usb_debug_enabled = previous_usb_debug;
    }
    return saved;
  }
#if COMPANION_FEATURE_READER
  bool loadReaderBookmark(mesh::bible::Position& pos) { return _store->loadReaderBookmark(pos); }
  bool saveReaderBookmark(mesh::bible::Position pos) { return _store->saveReaderBookmark(pos); }
#endif

#if ENV_INCLUDE_GPS == 1
  bool setGpsEnabled(bool enabled);
  void applyGpsPrefs() {
    sensors.setSettingValue("gps", _prefs.gps_enabled ? "1" : "0");
    char interval_str[12];  // Max: 24 hours = 86400 seconds (5 digits + null)
    sprintf(interval_str, "%u", _prefs.gps_interval);
    sensors.setSettingValue("gps_interval", interval_str);
    sensors.applyGpsTimeSyncInterval(_prefs.gps_sync_interval_hours);
  }
#endif

  // To check if there is pending work
  bool hasPendingWork() const;
  bool canRecoverUsbLogging() const;

private:
#if MESH_ENABLE_FLEET_CONTROL
  // Exact ownership of the optional radio copy during two-part admission.
  mesh::Packet* _fleet_admission_original = nullptr;
  mesh::Packet* _fleet_admission_copy = nullptr;
#endif
  // Only snapshot the changed value: CompanionNodePrefs owns self-referencing
  // runtime adapters and must not be copied as a transaction snapshot.
  template <typename T> bool savePreference(T& preference, T value) {
    const T previous = preference;
    preference = value;
    if (savePrefs()) return true;
    preference = previous;
    return false;
  }
  bool saveAdvertName(const char* name);
  bool saveAdvertLocation(double latitude, double longitude);
  void writeOKFrame(BaseSerialInterface* route = nullptr);
  void writeErrFrame(uint8_t err_code,
                     BaseSerialInterface* route = nullptr);
  size_t writePendingSerialFrame(const uint8_t frame[], size_t len, uint32_t now);
  void writeDisabledFrame();
  bool writeContactRespFrame(uint8_t code, const ContactInfo &contact,
                             BaseSerialInterface* route = NULL);
  void stopContactsIterator();
  bool updateContactFromFrame(ContactInfo &contact, uint32_t& last_mod, const uint8_t *frame, int len);
  bool addToOfflineQueue(const uint8_t frame[], int len);
  int peekOfflineQueue(uint8_t frame[]);
  void popOfflineQueue();
  int getFromOfflineQueue(uint8_t frame[]);
  void syncNextOfflineMessage();
  int getBlobByKey(const uint8_t key[], int key_len, uint8_t dest_buf[]) override { 
    return _store->getBlobByKey(key, key_len, dest_buf);
  }
  bool putBlobByKey(const uint8_t key[], int key_len, const uint8_t src_buf[], int len) override {
#if defined(ESP32_PLATFORM)
    return len > 0 && len <= 255
        && _store->queueAdvertByKey(key, key_len, src_buf, len);
#else
    return _store->putBlobByKey(key, key_len, src_buf, len);
#endif
  }

  void checkCLIRescueCmd();
  bool handleCommand(const char* text, uint32_t sender_timestamp, char* reply);
  bool handleDirectCommand(const char* command, char* reply, size_t reply_size);
  void checkSerialInterface();
  bool applyAndSaveFemRxGain(bool enabled);
  bool applyAndSaveFemTxGain(bool enabled);
  bool applyAndSaveRxBoostedGain(bool enabled);
  enum class RadioSettingResult { Saved, RadioRejected, SaveFailed };
  RadioSettingResult applyAndSaveTxPower(int8_t power);
  bool handleCadCommand(const char* command, char* reply, size_t reply_size);
  bool saveBluetoothNameOverride(const char* name);
  bool applyAndSaveBluetoothName(const char* value, char* reply,
                                 size_t reply_size);
  void formatBluetoothNameStatus(char* reply, size_t reply_size) const;
#if defined(BLE_PIN_CODE)
  bool saveBluetoothMac(uint8_t mode, const uint8_t* address);
  bool applyAndSaveBluetoothMac(const char* value, char* reply,
                                size_t reply_size);
  void formatBluetoothMacStatus(char* reply, size_t reply_size) const;
  bool applyAndSaveBluetoothStealth(const char* value, char* reply,
                                   size_t reply_size);
  void formatBluetoothStealthStatus(char* reply, size_t reply_size) const;
#endif
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
#if COMPANION_FEATURE_TEXT_TERMINAL
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
  void clearTerminalLogin(uint32_t now);
  void serviceTerminalLogin();
  void serviceTerminalLogin(uint32_t now);
  void sendTerminalLogin(ContactInfo& recipient, const char* password);
  void clearTerminalCommand();
  void serviceTerminalCommand();
  void sendTerminalCommand(ContactInfo& recipient, const char* command);
  void clearTerminalTrace();
  void clearTerminalTrace(uint32_t now);
  void serviceTerminalTrace();
  void serviceTerminalTrace(uint32_t now);
  void sendTerminalTraceRoute(const uint8_t* route, uint8_t hash_size,
                              uint8_t hop_count, const char* target);
  void sendTerminalTrace(ContactInfo& recipient);
  void sendTerminalRawTrace(const char* arguments);
#endif
  bool isValidClientRepeatFreq(uint32_t f) const;
  bool hasLocationTelemetryRecipient();
  void updateGpsTelemetryPolicy();
  mesh::RadioParamApplyResult tryApplyRadioParams(float freq, float bw, uint8_t sf, uint8_t cr, bool temporary = false, uint16_t preamble = 0);
  bool applySavedRadioParams();
  void configureRadioFromPrefs();
  void finishRadioParamApply(float freq, float bw, uint8_t sf, uint8_t cr,
                             uint8_t repeat,
                             BaseSerialInterface* route = nullptr);
  void cancelPendingRadioParamApply();
  void servicePendingRadioParamApply();
  void servicePendingSerialReply();
  void servicePendingSerialReply(uint32_t now);
  bool beginPendingRequest(mesh::CompanionDelayedReplies::Kind kind,
                           const ContactInfo& contact, bool terminal = false);
  void armPendingRequest(uint32_t tag, uint32_t timeout, bool flood);
  void finishPendingRequest(int result, uint32_t tag, uint32_t timeout);
  void abandonPendingRequest();
  void clearBinaryTraceReply();
  void serviceBinaryTraceReply();
  void serviceBinaryTraceReply(uint32_t now);
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
#if defined(ESP32_PLATFORM)
  void servicePersistence();
#endif
  void scheduleContactWriteRetry();
  bool isContactWriteDue() const;
  bool flushContactsBeforeReboot();
  bool prepareForOtaReboot() override;
  bool prepareForUiShutdown() override;
  bool scheduleContactWrite(const ContactInfo& contact);
  bool scheduleContactWriteAfterRelease(const ContactInfo& contact,
                                        uint16_t& released_slot);
  bool restoreContactWriteAfterRelease(const ContactInfo& contact,
                                       uint16_t released_slot);
#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
  void repairInternalExtraFS(Stream& output);
  void scanInternalExtraFS(Stream& output);
#if defined(MESHCORE_EXTRAFS_HIL)
  bool handleExtraFsHilCommand(const char* command, char* reply,
                               size_t reply_size);
#endif
#endif

  DataStore* _store;
  mesh::RadioProfileCLI _radio_profiles;
  uint16_t _temp_radio_preamble = 0;
  CompanionNodePrefs _prefs;
  #if COMPANION_FEATURE_NOTIFICATIONS
  mesh::notify::Controller _notifications{*this, *this};
  bool _notification_button_down = false;
  #endif
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
  bool _mqtt_enabled = true;
#endif
#ifdef WITH_WEBCONFIG
  WebConfigServer* _webconfig;
  bool _wc_mqtt_dirty;
  bool _wc_batch_active = false;
#endif
  mesh::CompanionDelayedReplies _delayed_replies;
  bool _request_tag_rejected = false;
  BaseSerialInterface* private_key_backup_route = nullptr;
  unsigned long private_key_backup_deadline = 0;
  char private_key_backup_nonce[17] = {};
  uint8_t private_key_backup_sender[6] = {};
  BaseSerialInterface *_serial;
  mesh::companion::MotaSourceControl* _mota_source_control;
  AbstractUITask* _ui;

  ContactsIterator _iter;
  BaseSerialInterface* _iter_reply_route;
  ContactInfo _iter_pending_contact;
  uint32_t _iter_filter_since;
  uint32_t _iter_next_frame_at;
  uint32_t _iter_total_count;
  uint32_t _iter_table_revision;
  uint32_t _most_recent_lastmod;
  uint32_t _active_ble_pin;
  bool _iter_started;
  bool _iter_start_pending;
  bool _iter_contact_pending;
  bool _cli_rescue;
#if COMPANION_FEATURE_TEXT_TERMINAL
  bool _terminal_mode;
  bool _terminal_usb_silent = false;
  Stream* _terminal_output;
  mesh::TerminalDisplayFilter _terminal_display;
  bool _terminal_recipient_set;
  uint8_t _terminal_recipient_key[PUB_KEY_SIZE];
  uint8_t _terminal_tmp_buf[MAX_TRANS_UNIT];
  bool _terminal_login_pending;
  uint8_t _terminal_login_key[PUB_KEY_SIZE];
  unsigned long _terminal_login_expires_at;
  char _terminal_login_target[32];
  mesh::TerminalCommandTracker<PUB_KEY_SIZE> _terminal_command;
  char _terminal_command_target[32];
  bool _terminal_trace_pending;
  uint8_t _terminal_trace_history = mesh::CompanionDelayedReplies::NO_HISTORY;
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
  bool _cli_line_overflow = false;
  char reply_buf[166];
  uint8_t app_target_ver;
  uint8_t *sign_data;
  uint32_t sign_data_len;
  BaseSerialInterface* sign_data_reply_route;
  unsigned long sign_data_deadline;
  unsigned long dirty_contacts_expiry;
  uint8_t dirty_contacts_failures;
#if defined(ESP32_PLATFORM)
  bool _advert_write_next = true;
#endif

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
#if ONE_KEY_DM_SHARED_OFFLINE_QUEUE
  Frame& heldDMFrameAt(uint8_t index);
  void removeHeldOneKeyDM(uint8_t index);
#endif
  void initializeOfflineQueue();
  int offline_queue_len;
  int offline_queue_head;
#if defined(OTA_SHARED_COMPANION_QUEUE)
  static_assert(OFFLINE_QUEUE_SIZE == 256, "Shared mOTA queue requires 256 normal slots");
  mesh::BorrowableFrameBuffer<Frame, OFFLINE_QUEUE_SIZE, 128, mesh::ota::OtaContext> offline_queue;
  static mesh::ota::OtaContext* acquireOfflineQueueForOta(void* owner);
  static uint16_t drainOfflineQueueForOta(void* owner);
  static void releaseOfflineQueueFromOta(void* owner);
#elif defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
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
    uint8_t retry_key[MAX_HASH_SIZE];
    bool confirmed; // msg_sent holds the frozen RTT while host admission is pending
#if COMPANION_FEATURE_TEXT_TERMINAL
    bool terminal_origin;
#endif
    unsigned long msg_sent;
    unsigned long expires_at;
    uint32_t ack;
    uint32_t message_timestamp;
    ContactInfo* contact;
    uint8_t text_fingerprint[MAX_HASH_SIZE];
    BaseSerialInterface* reply_route;
  };
  #define EXPECTED_ACK_TABLE_SIZE 8
  AckTableEntry expected_ack_table[EXPECTED_ACK_TABLE_SIZE]; // circular table
#if MESH_ENABLE_LOST_REPLY
  mesh::companion::LostReplyLimiter lost_reply_limiter;
  void maybeReplyToLostQuestion(const ContactInfo& from, uint32_t sender_timestamp,
                               const char* text);
#endif
#if MESH_ENABLE_ONE_KEY_DM
  // A recent ACK proves that peer can decrypt our normal text packets. Retry
  // attempts still send the introduction in case its contact was later erased.
  static constexpr uint8_t ONE_KEY_PEERS = 8;
  struct OneKeyPeerState {
    uint8_t pub_key[PUB_KEY_SIZE];
    uint32_t intro_tag;
    uint8_t status;  // 0: pending, 1: acknowledged, 2: rejected
  };
  OneKeyPeerState one_key_peers[ONE_KEY_PEERS] = {};
  uint8_t one_key_peer_count = 0;
  uint8_t one_key_peer_next = 0;
  bool hasOneKeyAck(const ContactInfo& contact) const;
  bool hasOneKeyReject(const ContactInfo& contact) const;
  void rememberOneKeyIntro(const ContactInfo& contact, uint32_t tag);
  void rememberOneKeyAck(const ContactInfo& contact);
  bool rememberOneKeyReject(const ContactInfo& contact, uint32_t tag);
  uint32_t sendOneKeyIntroduction(const ContactInfo& contact);

  static constexpr uint8_t MAX_HELD_ONE_KEY_DMS = 15;
  static constexpr uint8_t ONE_KEY_DM_ID_SIZE = 8;
  uint8_t verified_pending_keys[MAX_HELD_ONE_KEY_DMS][PUB_KEY_SIZE] = {};
  uint8_t verified_pending_count = 0;
  struct HeldOneKeyDM {
    uint8_t sender_key[PUB_KEY_SIZE];
    uint8_t id[ONE_KEY_DM_ID_SIZE];
#if !ONE_KEY_DM_SHARED_OFFLINE_QUEUE
    mesh::Packet packet;
#endif
  };
  HeldOneKeyDM held_dms[MAX_HELD_ONE_KEY_DMS];
  uint8_t held_dm_count = 0;
  struct DeliveredOneKeyDM {
    uint8_t sender_key[PUB_KEY_SIZE];
    uint8_t id[ONE_KEY_DM_ID_SIZE];
  };
  DeliveredOneKeyDM delivered_dms[MAX_HELD_ONE_KEY_DMS] = {};
  uint8_t delivered_dm_count = 0;
  uint8_t delivered_dm_next = 0;
  void rememberVerifiedPendingSender(const uint8_t* pub_key);
  void forgetVerifiedPendingSender(const uint8_t* pub_key);
  static void makeOneKeyDMId(uint8_t id[ONE_KEY_DM_ID_SIZE], uint32_t timestamp,
                             const char* text);
  bool wasDeliveredOneKeyDM(const uint8_t* pub_key,
                            const uint8_t id[ONE_KEY_DM_ID_SIZE]) const;
  void rememberDeliveredOneKeyDM(const uint8_t* pub_key,
                                 const uint8_t id[ONE_KEY_DM_ID_SIZE]);
  void releaseHeldOneKeyDMs();
#endif
  mesh::LogicalMessageCache<ROOM_MESSAGE_TIMESTAMP_CACHE_SIZE> room_message_timestamps;
  int next_ack_idx;
  unsigned long next_ack_expiry;
  bool has_next_ack_expiry;

  void clearExpectedAck(AckTableEntry& entry, bool cancel_retries = true);
  void expireExpectedAcks();
  AckTableEntry* findPendingTextMessage(
      const uint8_t text_fingerprint[MAX_HASH_SIZE], uint32_t message_timestamp);
#if COMPANION_FEATURE_TEXT_TERMINAL
  void rememberTerminalAck(ContactInfo& recipient, const char* text,
                           uint32_t message_timestamp, uint32_t expected_ack,
                           uint32_t est_timeout,
                           const uint8_t packet_retry_key[MAX_HASH_SIZE],
                           AckTableEntry* replacement_entry);
#endif

  #define ADVERT_PATH_TABLE_SIZE   16
  AdvertPath advert_paths[ADVERT_PATH_TABLE_SIZE]; // circular table

#if defined(DISPLAY_CLASS) && !(defined(UI_NO_DISCOVER_SCREEN) && (UI_NO_DISCOVER_SCREEN + 0 != 0))
  #ifdef UI_RECENT_LIST_SIZE
    #define DISCOVERED_NODES_TABLE_SIZE UI_RECENT_LIST_SIZE
  #else
    #define DISCOVERED_NODES_TABLE_SIZE 4
  #endif
  DiscoveredNode discovered_nodes[DISCOVERED_NODES_TABLE_SIZE]; // not circular, latest discovered nodes are not kept
  uint32_t disc_node_req_tag = 0;
  uint32_t disc_node_req_at = 0;
  bool disc_node_req_active = false;
  uint32_t disc_nodes_count = 0;

  void checkControlDataForPendingDiscovery(uint8_t payload[], size_t p_len);
#endif
};

extern MyMesh the_mesh;
