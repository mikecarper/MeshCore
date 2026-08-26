#pragma once

#include <Arduino.h>   // needed for PlatformIO
#include <Mesh.h>
#include "RoomServerFeatures.h"

#if defined(NRF52_PLATFORM)
  #include <InternalFileSystem.h>
#elif defined(RP2040_PLATFORM)
  #include <LittleFS.h>
#elif defined(ESP32)
  #include <SPIFFS.h>
#endif

#include <helpers/ArduinoHelpers.h>
#include <helpers/StaticPoolPacketManager.h>
#include <helpers/SimpleMeshTables.h>
#include <helpers/IdentityStore.h>
#include <helpers/AdvertDataHelpers.h>
#include <helpers/AlertReporter.h>
#include <helpers/TxtDataHelpers.h>
#include <helpers/CommonCLI.h>
#include <helpers/MeshClockSync.h>
#if defined(ESP32_PLATFORM) || defined(USER_GPIO_CONTROL)
#include <helpers/UserGpioReplyTracker.h>
#endif
#include <helpers/StatsFormatHelper.h>
#include <helpers/ClientACL.h>
#include <helpers/LogicalMessageCache.h>
#include <helpers/RemoteCliReplyCache.h>
#include <helpers/RemoteCliRequest.h>
#include <helpers/RegionMap.h>
#include "FloodRuleEngine.h"
#include <helpers/RoutingPolicy.h>
#include <RTClib.h>
#include <target.h>

#ifdef WITH_MQTT_BRIDGE
#include "helpers/bridges/MQTTBridge.h"
#define WITH_BRIDGE
#include "helpers/esp32/WebConfigServer.h"   // defines WITH_WEBCONFIG on ESP32
#endif

#ifdef WITH_SNMP
#include "helpers/SNMPAgent.h"
#endif


#if defined(ESP_PLATFORM) && defined(ADMIN_PASSWORD) && !defined(WEBCONFIG_DISABLED)
#include "helpers/esp32/WebConfigServer.h"
#endif

/* ------------------------------ Config -------------------------------- */

#ifndef FIRMWARE_BUILD_DATE
  #define FIRMWARE_BUILD_DATE   "14 Aug 2026"
#endif

#ifndef FIRMWARE_VERSION
  #define FIRMWARE_VERSION   "v1.17.1"
#endif

#ifndef LORA_FREQ
  #define LORA_FREQ   915.0
#endif
#ifndef LORA_BW
  #define LORA_BW     250
#endif
#ifndef LORA_SF
  #define LORA_SF     10
#endif
#ifndef LORA_CR
  #define LORA_CR      5
#endif
#ifndef LORA_TX_POWER
  #define LORA_TX_POWER  20
#endif

#ifndef ADVERT_NAME
  #define  ADVERT_NAME   "Test BBS"
#endif
#ifndef ADVERT_LAT
  #define  ADVERT_LAT  0.0
#endif
#ifndef ADVERT_LON
  #define  ADVERT_LON  0.0
#endif

#ifndef ADMIN_PASSWORD
  #define  ADMIN_PASSWORD  "password"
#endif

#ifndef MAX_UNSYNCED_POSTS
  #define MAX_UNSYNCED_POSTS    32
#endif

#ifndef ROOM_MESSAGE_CACHE_SIZE
  #define ROOM_MESSAGE_CACHE_SIZE 32
#endif

#ifndef SERVER_RESPONSE_DELAY
  #define SERVER_RESPONSE_DELAY   300
#endif

#ifndef TXT_ACK_DELAY
  #define TXT_ACK_DELAY     200
#endif

#define FIRMWARE_ROLE "room_server"

#define PACKET_LOG_FILE  "/packet_log"

#define MAX_POST_TEXT_LEN    (160-9)

struct PostInfo {
  mesh::Identity author;
  uint32_t post_timestamp;   // by OUR clock
  char text[MAX_POST_TEXT_LEN+1];
};

struct NeighbourInfo {
  mesh::Identity id;
  uint32_t advert_timestamp;
  uint32_t heard_timestamp;
  int8_t snr; // multiplied by 4, user should divide to get float value
};

class MyMesh : public mesh::Mesh, public CommonCLICallbacks,
               public mesh::MeshClockSyncCallbacks
#ifdef WITH_WEBCONFIG
    , public WebConfigServer::Callbacks
#endif
{
  FILESYSTEM* _fs;
  uint32_t last_millis;
  uint64_t uptime_millis;
  unsigned long next_local_advert, next_flood_advert;
  bool _logging;
  bool region_load_active;
  NodePrefs _prefs;
  TransportKeyStore key_store;
  RegionMap region_map, temp_map;
#if MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE
  FloodRuleEngine flood_rules;
  uint32_t recv_pkt_rule_match_mask;
  bool recv_pkt_regionless_scope_set;
#endif
  ClientACL acl;
  mesh::LogicalMessageCache<ROOM_MESSAGE_CACHE_SIZE> recent_room_posts;
  mesh::RemoteCliReplyCache remote_cli_reply_cache;
  CommonCLI _cli;
  mesh::MeshClockSync _clock_sync;
#if defined(ESP32_PLATFORM) || defined(USER_GPIO_CONTROL)
  UserGpioReplyTracker _gpio_reply_tracker;
#endif
  unsigned long dirty_contacts_expiry;
  uint8_t reply_data[MAX_PACKET_PAYLOAD];
  unsigned long next_push;
  uint16_t _num_posted, _num_post_pushes;
  int next_client_idx;  // for round-robin polling
  int next_post_idx;
  PostInfo posts[MAX_UNSYNCED_POSTS];   // cyclic queue
  CayenneLPP telemetry;
  RegionEntry* load_stack[8];
  RegionEntry* recv_pkt_region;
  TransportKey default_scope;
  unsigned long set_radio_at, revert_radio_at;
  unsigned long _ota_update_at = 0;  // deferred `ota update` fire time (0 = none scheduled)
  float pending_freq;
  float pending_bw;
  uint8_t pending_sf;
  uint8_t pending_cr;
  uint8_t active_cr;
  bool temp_radio_applied;
  bool saved_radio_apply_pending;
  unsigned long radio_apply_retry_at;
  uint8_t radio_apply_failures;
  int  matching_peer_indexes[MAX_CLIENTS];
#if defined(WITH_MQTT_NEIGHBORS)
  NeighbourInfo neighbours[MAX_NEIGHBOURS];
  uint32_t pending_discover_tag;
  unsigned long pending_discover_until;
  enum NeighborDiscoverStatus : uint8_t {
    ND_UNSENT = 0,
    ND_QUEUED = 1,
    ND_PENDING = 2,
    ND_RESPONDED = 3,
    ND_TIMEOUT = 4,
    ND_SEND_FAILED = 5,
  };
  struct NeighborDiscoverEntry {
    mesh::Identity id;
    uint32_t heard_timestamp;
    int8_t snr;
    uint32_t tag;
    char scopes[96];
    uint8_t status;
  };
  NeighborDiscoverEntry neighbor_discover[MAX_NEIGHBOURS];
  uint8_t neighbor_discover_count;
  uint8_t neighbor_discover_next;
  uint8_t neighbor_discover_publish_count;
  uint8_t neighbor_discover_queried_count;    // requests confirmed transmitted
  size_t neighbor_discover_json_size;
  bool neighbor_discover_truncated;
  bool neighbor_discover_active;
  bool neighbor_table_refresh_active;
  bool neighbor_table_refresh_periodic;
  unsigned long neighbor_discover_until;
  mesh::Packet* neighbor_discover_request;
  unsigned long next_neighbors_publish;
  char self_scopes_buf[96];
  char self_default_scope_buf[31];
  char neighbor_discover_origin[32];

  void putNeighbour(const mesh::Identity& id, uint32_t timestamp, float snr);
  void sendNodeDiscoverReq();
  mesh::Packet* sendAnonRegionsReq(const mesh::Identity& target, uint32_t& tag);
  bool cancelNeighborDiscoverRequest();
  uint32_t neighborDiscoverQueryTimeoutMs() const;
  bool completeNeighborDiscoverEntry();
  void resetNeighborDiscoverJsonBudget();
  bool neighborDiscoverReady(char* reply);
  bool startNeighborDiscover(char* reply);
  void loopNeighborDiscover();
  void finishNeighborDiscover();
  bool handleNeighborDiscoverResponse(int overlay_idx, const uint8_t* data, size_t len);
  void touchNeighbourHeard(const mesh::Identity& id, uint32_t heard_timestamp);
  void getLocalScopes(char* buf, size_t len);
  static const int NEIGHBOR_DISCOVER_PEER_BASE = 1000;
  static const unsigned long NEIGHBOR_DISCOVER_QUEUE_TIMEOUT_MS = 29000;
  static const int NEIGHBOR_DISCOVER_MIN_FREE_PACKETS = 5;
#endif
#ifdef WITH_MQTT_BRIDGE
  MQTTBridge* bridge;
#endif
#ifdef WITH_SNMP
  MeshSNMPAgent _snmp_agent;
#endif
#ifdef WITH_MQTT_BRIDGE
  AlertReporter _alerter;
#endif
#ifdef WITH_WEBCONFIG
  WebConfigServer* _webconfig = nullptr;
  bool _wc_batch_active = false;
  bool _wc_restart_pending = false;
  uint8_t _wc_slot_restart_mask = 0;
#endif

  void addPost(ClientInfo* client, const char* postData);
  bool applySavedRadioParams();
  void storePost(const mesh::Identity& author, const char* postData);
  void pushPostToClient(ClientInfo* client, PostInfo& post);
  uint8_t getUnsyncedCount(ClientInfo* client);
  bool processAck(const uint8_t *data);
  mesh::Packet* createSelfAdvert();
  File openAppend(const char* fname);
  int handleRequest(ClientInfo* sender, uint32_t sender_timestamp, uint8_t* payload, size_t payload_len);
#if MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE
  bool evaluateFloodRuleTiming(const mesh::Packet* packet,
                               bool& fast_track);
  bool isLooped(const mesh::Packet* packet,
                const uint8_t max_counters[]) const;
  uint32_t getSlowFloodRuleRetransmitDelay(
      const mesh::Packet* packet);
#endif

protected:
  bool isTempRadioActive() const override {
    return temp_radio_applied && revert_radio_at != 0 && !millisHasNowPassed(revert_radio_at);
  }
  float getAirtimeBudgetFactor() const override {
    return _prefs.airtime_factor;
  }

  void logRxRaw(float snr, float rssi, const uint8_t raw[], int len) override;
  void logRx(mesh::Packet* pkt, int len, float score) override;
  void logTx(mesh::Packet* pkt, int len) override;
  void logTxFail(mesh::Packet* pkt, int len) override;

  int calcRxDelay(float score, uint32_t air_time) const override;
#if MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE
  int calcRxDelayForPacket(const mesh::Packet* packet, float score,
                           uint32_t air_time) override;
#endif
  const char* getLogDateTime() override;
  uint32_t getRetransmitDelay(const mesh::Packet* packet) override;
  uint32_t getDirectRetransmitDelay(const mesh::Packet* packet) override;
  bool allowDirectRetry(const mesh::Packet* packet, const uint8_t* next_hop_hash,
                        uint8_t next_hop_hash_len) const override;
  void configureDirectRetryPacket(mesh::Packet* retry, const mesh::Packet* original,
                                  uint8_t retry_attempt) override;
  uint32_t getDirectRetryEchoDelay(const mesh::Packet* packet) const override;
  uint8_t getDirectRetryMaxAttempts(const mesh::Packet* packet) const override;
  uint32_t getDirectRetryAttemptDelay(const mesh::Packet* packet, uint8_t attempt_idx) override;
  bool allowFloodRetry(const mesh::Packet* packet) const override;
  uint8_t getFloodRetryMaxPathLength(const mesh::Packet* packet) const override;
  uint8_t getFloodRetryMaxAttempts(const mesh::Packet* packet) const override;

  bool supportsBasicRetryConfig() const override { return true; }
  void onRetryConfigChanged() override;

  int getInterferenceThreshold() const override {
    return _prefs.interference_threshold;
  }
  int getAGCResetInterval() const override {
    return ((int)_prefs.agc_reset_interval) * 4000;   // milliseconds
  }
  uint8_t getExtraAckTransmitCount() const override {
    return _prefs.multi_acks;
  }
  uint8_t getDefaultTxCodingRate() const override {
    return active_cr;
  }

  mesh::DispatcherAction onRecvPacket(mesh::Packet* pkt) override;

  bool allowPacketForward(const mesh::Packet* packet) override;
  void onAnonDataRecv(mesh::Packet* packet, const uint8_t* secret, const mesh::Identity& sender, uint8_t* data, size_t len) override;
  int searchPeersByHash(const uint8_t* hash) override ;
  void getPeerSharedSecret(uint8_t* dest_secret, int peer_idx) override;
  void onPeerDataRecv(mesh::Packet* packet, uint8_t type, int sender_idx, const uint8_t* secret, uint8_t* data, size_t len) override;
  bool onPeerPathRecv(mesh::Packet* packet, int sender_idx, const uint8_t* secret, uint8_t* path, uint8_t path_len, uint8_t extra_type, uint8_t* extra, uint8_t extra_len) override;
  void onAckRecv(mesh::Packet* packet, uint32_t ack_crc) override;
  void onAdvertRecv(mesh::Packet* packet, const mesh::Identity& id, uint32_t timestamp, const uint8_t* app_data, size_t app_data_len) override;
  void onGroupPacketRecv(mesh::Packet* packet) override;
#if defined(WITH_MQTT_NEIGHBORS)
  void onControlDataRecv(mesh::Packet* packet) override;
#endif

#if ENV_INCLUDE_GPS == 1
  void applyGpsPrefs() {
    sensors.setPowerSavingEnabled(_prefs.powersaving_enabled != 0);
    sensors.setSettingValue("gps", _prefs.gps_enabled?"1":"0");
    char interval_str[12];
    sprintf(interval_str, "%u", _prefs.gps_interval);
    sensors.setSettingValue("gps_interval", interval_str);
  }
#endif

  void sendFloodReply(mesh::Packet* packet, unsigned long delay_millis, uint8_t path_hash_size);

public:
  MyMesh(mesh::MainBoard& board, mesh::Radio& radio, mesh::MillisecondClock& ms, mesh::RNG& rng, mesh::RTCClock& rtc, mesh::MeshTables& tables);

  void begin(FILESYSTEM* fs);
  void addSystemPost(const char* postData);

  const char* getFirmwareVer() override { return FIRMWARE_VERSION; }
  const char* getBuildDate() override { return FIRMWARE_BUILD_DATE; }
  const char* getRole() override { return FIRMWARE_ROLE; }
  const char* getNodeName() { return _prefs.node_name; }
  NodePrefs* getNodePrefs() {
    return &_prefs;
  }

  void savePrefs(
      PrefsSaveRouting::Scope scope = PrefsSaveRouting::Scope::Common) override {
    _cli.savePrefs(_fs, scope);
  }
  void onManualClockSet() override { _clock_sync.onManualClockSet(); }
  bool hasAuthoritativeClock() const override {
#ifdef WITH_MQTT_BRIDGE
    return bridge != nullptr && bridge->hasNtpTime();
#else
    return false;
#endif
  }

  bool sendFloodScoped(const TransportKey& scope, mesh::Packet* pkt,
                       uint32_t delay_millis, uint8_t path_hash_size);

  // CommonCLICallbacks
  void applyTempRadioParams(float freq, float bw, uint8_t sf, uint8_t cr, int timeout_mins) override;
  bool scheduleNormalRadio() override;
#if defined(ESP32_PLATFORM) || defined(USER_GPIO_CONTROL)
  uint32_t getUserGpioRequestSource() const override {
    return _gpio_reply_tracker.requestSource();
  }
  void onUserGpioTimerScheduled(uint8_t pin, uint32_t request_id) override {
    _gpio_reply_tracker.timerScheduled(pin, request_id);
  }
  void onUserGpioTimerCancelled(uint8_t pin) override {
    _gpio_reply_tracker.timerCancelled(pin);
  }
  void onUserGpioTimerCompleted(uint8_t pin, uint8_t state,
                                uint32_t request_id) override;
#endif

#ifdef WITH_MQTT_BRIDGE
  void onAlertConfigChanged() override { _alerter.onConfigChanged(); }
  bool sendAlertText(const char* text) override { return _alerter.sendText(text); }
#endif
  bool resolveAlertScope(TransportKey& dest) override;
  bool formatFileSystem() override;
  void sendSelfAdvertisement(int delay_millis, bool flood) override;
  void updateAdvertTimer() override;
  void updateFloodAdvertTimer() override;

  void setLoggingOn(bool enable) override { _logging = enable; }

  void eraseLogFile() override {
    _fs->remove(PACKET_LOG_FILE);
  }

  void dumpLogFile() override;
  bool setTxPower(int8_t power_dbm) override;
  bool setRxPowerSaving(bool enable, uint32_t rx_us, uint32_t sleep_us) override;
  void recalibrateNoiseFloor() override { _radio->recalibrateNoiseFloor(); }
  void getRxPsWatchdogCounts(uint32_t* soft, uint32_t* hard) override;
  bool setRxBoostedGain(bool enable) override;

  void formatNeighborsReply(char *reply) override;
  void removeNeighbor(const uint8_t* pubkey, int key_len) override;
  void formatStatsReply(char *reply) override;
  void formatRadioStatsReply(char *reply) override;
  void formatRadioDiagReply(char *reply) override;
  void formatPacketStatsReply(char *reply) override;
  void startRegionsLoad() override;
  bool saveRegions() override;
  void onDefaultRegionChanged(const RegionEntry* r) override;

  mesh::LocalIdentity& getSelfId() override { return self_id; }

  static bool saveFilter(ClientInfo* client);

  void saveIdentity(const mesh::LocalIdentity& new_id) override;
  void clearStats() override;
  void handleCommand(uint32_t sender_timestamp, char* command, char* reply,
                     int gpio_client_index = -1,
                     uint8_t gpio_path_hash_size = 1);
  void loop();

#if defined(WITH_BRIDGE)
  void setBridgeState(bool enable) override {
    if (!bridge) {
#ifdef WITH_MQTT_BRIDGE
      MQTTNodeInfo node_info;
      node_info.node_name = _prefs.node_name;
      node_info.freq = &_prefs.freq;
      node_info.bw = &_prefs.bw;
      node_info.sf = &_prefs.sf;
      node_info.cr = &_prefs.cr;
      node_info.repeat_flag = &_prefs.disable_fwd;
      node_info.repeat_when_nonzero = false;
      bridge = new MQTTBridge(node_info, _cli.getObserverPrefs(),
                              getRTCClock(), &self_id);
#endif
      if (!bridge) return;
    }
    if (enable == bridge->isRunning()) return;
    if (enable)
    {
      char device_id[65];
      mesh::LocalIdentity self_id = getSelfId();
      mesh::Utils::toHex(device_id, self_id.pub_key, PUB_KEY_SIZE);
      bridge->setDeviceID(device_id);
      bridge->setFirmwareVersion(getFirmwareVer());
      bridge->setBoardModel(_cli.getBoard()->getManufacturerName());
      bridge->setBuildDate(getBuildDate());
#ifdef WITH_MQTT_BRIDGE
      bridge->setStatsSources(this, _radio, _cli.getBoard(), _ms);
#ifdef WITH_SNMP
      if (_cli.getObserverPrefs()->snmp_enabled) {
        _snmp_agent.setNodeName(_prefs.node_name);
        _snmp_agent.setFirmwareVersion(getFirmwareVer());
        bridge->setSNMPAgent(&_snmp_agent);
      }
#endif
#endif
      bridge->begin();
#ifdef WITH_MQTT_BRIDGE
      _alerter.setBridge(bridge);
#endif
    }
    else
    {
      bridge->end();
#ifdef WITH_MQTT_BRIDGE
      _alerter.setBridge(nullptr);
#endif
    }
  }

  void restartBridge() override {
    if (!bridge || !bridge->isRunning()) return;
#ifdef WITH_WEBCONFIG
    if (_wc_batch_active) {   // coalesced: applied once in onConfigBatchEnd()
      _wc_restart_pending = true;
      return;
    }
#endif
    bridge->end();
    char device_id[65];
    mesh::LocalIdentity self_id = getSelfId();
    mesh::Utils::toHex(device_id, self_id.pub_key, PUB_KEY_SIZE);
    bridge->setDeviceID(device_id);
    bridge->setFirmwareVersion(getFirmwareVer());
    bridge->setBoardModel(_cli.getBoard()->getManufacturerName());
    bridge->setBuildDate(getBuildDate());
#ifdef WITH_MQTT_BRIDGE
    bridge->setStatsSources(this, _radio, _cli.getBoard(), _ms);
#endif
    bridge->begin();
  }

  void restartBridgeSlot(int slot) override {
#ifdef WITH_MQTT_BRIDGE
    if (!bridge || !bridge->isRunning()) return;
#ifdef WITH_WEBCONFIG
    if (_wc_batch_active && slot >= 0 && slot < MAX_MQTT_SLOTS) {
      _wc_slot_restart_mask |= static_cast<uint8_t>(1U << slot);
      return;
    }
#endif
    bridge->setSlotPreset(slot, _cli.getObserverPrefs()->mqtt_slot_preset[slot]);
#else
    (void)slot;
#endif
  }

#if defined(WITH_MQTT_BRIDGE)
  // Pump already-queued mesh traffic before OTA blocks the loop and reboots.
  // This is deliberately bounded: a jammed or duty-limited channel must not
  // prevent an update, and Mesh::loop() continues to respect TX constraints.
  void drainOutbound(uint32_t timeout_ms) {
    unsigned long start = millis();
    while (hasOutbound() || _mgr->getOutboundCount(millis()) > 0) {
      if (millis() - start >= timeout_ms) break;
      mesh::Mesh::loop();
      delay(1);
    }
  }
#endif

  // Schedule the pull-OTA flash to run from loop() in ~2.5 s, leaving time for the
  // "Beginning update..." CLI reply (CLI_REPLY_DELAY_MILLIS = 600 ms) to transmit
  // before the flash blocks the loop and reboots.
  bool beginDeferredOtaUpdate() override {
    _ota_update_at = millis() + 2500;
    if (_ota_update_at == 0) _ota_update_at = 1;  // 0 means "none"
    return true;
  }

  int getQueueSize() override {
    return bridge ? bridge->getQueueSize() : 0;
  }

  bool isMqttBridgeRunning() override {
    return bridge && bridge->isRunning();
  }

  bool syncMqttNtp() override {
    if (!bridge || !bridge->isRunning()) return false;
    // Queue the sync onto the MQTT task (Core 0) without blocking: this runs on
    // the Arduino loop task (serial CLI and the web config batch both drain
    // here), and blocking up to 30 s would stall mesh/radio forwarding. Returns
    // true once queued; verify with `get mqtt.ntp.diag`.
    return bridge->requestForcedNtpSync(0);
  }

  bool runMqttNtpDiag(char* reply, size_t reply_size, bool verbose) override {
    if (!bridge || !bridge->isRunning()) return false;
    return bridge->ntpDiag(reply, reply_size, verbose);
  }
#endif

#ifdef WITH_WEBCONFIG
  bool startWebConfig(bool force_ap, char* reply) override;
  bool stopWebConfig(char* reply) override;
  bool setWebUIEnabled(bool enabled, char* reply) override;
  bool getWebUIStatus(char* reply) const override;
  bool getWiFiSSID(char* reply) const override;
  bool getWiFiStatus(char* reply) const override;
  bool getWiFiPowerSave(char* reply) const override;
  bool getWiFiCLI(char* reply) const override;
  bool setWiFiSSID(const char* value, char* reply) override;
  bool setWiFiPassword(const char* value, char* reply) override;
  bool setWiFiPowerSave(const char* value, char* reply) override;
  bool setWiFiCLI(const char* value, char* reply) override;
  bool isWebConfigActive() const override {
    return _webconfig && (_webconfig->isRunning() || _webconfig->isStopping());
  }
  void getNodeSnapshot(WebConfigServer::NodeSnapshot& snapshot) override;
  void execCommand(char* cmd, char* reply) override { handleCommand(0, cmd, reply); }
  bool supportsCliTerminal() const override { return true; }
  void execAdminCommand(char* cmd, char* reply) override {
    handleCommand(1, cmd, reply);
  }
  void rebootNow() override { _cli.getBoard()->reboot(); }
  void onConfigBatchStart() override {
    _wc_batch_active = true;
    _wc_restart_pending = false;
    _wc_slot_restart_mask = 0;
  }
  void onConfigBatchEnd() override;
  void buildStatsJson(char* buf, size_t buf_size) override;
#endif
  uint32_t getPowerSaveSleepSeconds(uint32_t max_secs) const;

  // To check if there is pending work
  bool hasPendingWork() const;

private:
  bool isMillisTimerDue(unsigned long timestamp) const;
  uint32_t limitSleepToMillisTimer(unsigned long timestamp, uint32_t sleep_secs) const;
};
