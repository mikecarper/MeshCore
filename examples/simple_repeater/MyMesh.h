#pragma once

#include <Arduino.h>
#include <Mesh.h>
#if defined(ENABLE_OTA)
  #include <helpers/ota/OtaContext.h>
#endif
#include <RTClib.h>
#include <CayenneLPP.h>
#include <target.h>

#ifndef MESH_ENABLE_TELEMETRY_HISTORY
  // LoRa-E5-class STM32 repeater images have less than 3 KB of spare flash.
  #if defined(STM32_PLATFORM)
    #define MESH_ENABLE_TELEMETRY_HISTORY 0
  #else
    #define MESH_ENABLE_TELEMETRY_HISTORY 1
  #endif
#endif

#ifndef MESH_ENABLE_TELEMETRY_GPS_HISTORY
  // Builds without a GPS provider only collect missing GPS positions. Omit
  // their unused decoder and resize CLI to preserve flash for useful history.
  #if ENV_INCLUDE_GPS == 1
    #define MESH_ENABLE_TELEMETRY_GPS_HISTORY 1
  #else
    #define MESH_ENABLE_TELEMETRY_GPS_HISTORY 0
  #endif
#endif

#ifndef MESH_ENABLE_RECENT_REPEATERS
  #define MESH_ENABLE_RECENT_REPEATERS  1
#endif
#ifndef MAX_RECENT_REPEATERS
  // Only repeater firmware supplies this RAM-heavy history storage.
  #if !MESH_ENABLE_RECENT_REPEATERS
    #define MAX_RECENT_REPEATERS  0
  #elif defined(ESP32) || defined(ESP32_PLATFORM)
    #define MAX_RECENT_REPEATERS  2048
  #elif defined(NRF52_PLATFORM)
    #define MAX_RECENT_REPEATERS  512
  #else
    #define MAX_RECENT_REPEATERS  64
  #endif
#endif

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  #include <InternalFileSystem.h>
#elif defined(RP2040_PLATFORM)
  #include <LittleFS.h>
#elif defined(ESP32)
  #include <SPIFFS.h>
  using File = fs::File;
#endif

#ifdef WITH_RS232_BRIDGE
#include "helpers/bridges/RS232Bridge.h"
#define WITH_BRIDGE
#endif

#ifdef WITH_ESPNOW_BRIDGE
#include "helpers/bridges/ESPNowBridge.h"
#define WITH_BRIDGE
#endif

#ifdef WITH_MQTT_BRIDGE
#include "helpers/bridges/MQTTBridge.h"
#define WITH_BRIDGE
#include "helpers/esp32/WebConfigServer.h"   // defines WITH_WEBCONFIG on ESP32
#endif

#if defined(ESP_PLATFORM) && defined(ADMIN_PASSWORD) && !defined(WEBCONFIG_DISABLED)
#include "helpers/esp32/WebConfigServer.h"
#endif

#ifdef WITH_SNMP
#include "helpers/SNMPAgent.h"
#endif

#include <helpers/AdvertDataHelpers.h>
#include <helpers/AlertReporter.h>
#include <helpers/ArduinoHelpers.h>
#include <helpers/ClientACL.h>
#include <helpers/CommonCLI.h>
#include <helpers/DeferredCliCommand.h>
#ifndef MESH_ENABLE_HOST_CLI
#define MESH_ENABLE_HOST_CLI 1
#endif
#if MESH_ENABLE_HOST_CLI
#include <helpers/HostCliBridge.h>
#endif
#include <helpers/RemoteCliReplyCache.h>
#include <helpers/RemoteCliRequest.h>
#if defined(ESP32_PLATFORM) || defined(USER_GPIO_CONTROL)
#include <helpers/UserGpioReplyTracker.h>
#endif
#include <helpers/IdentityStore.h>
#include <helpers/SimpleMeshTables.h>
#include <helpers/StaticPoolPacketManager.h>
#include <helpers/StatsFormatHelper.h>
#if MESH_ENABLE_TELEMETRY_HISTORY
#include <helpers/ExternalVoltageHistory.h>
#include <helpers/TelemetryHistory.h>
#endif
#include <helpers/TxtDataHelpers.h>
#include <helpers/RegionMap.h>
#include <helpers/RoutingPolicy.h>
#include "RateLimiter.h"


struct RepeaterStats {
  uint16_t batt_milli_volts;
  uint16_t curr_tx_queue_len;
  int16_t  noise_floor;
  int16_t  last_rssi;
  uint32_t n_packets_recv;
  uint32_t n_packets_sent;
  uint32_t total_air_time_secs;
  uint32_t total_up_time_secs;
  uint32_t n_sent_flood, n_sent_direct;
  uint32_t n_recv_flood, n_recv_direct;
  uint16_t err_events;                // was 'n_full_events'
  int16_t  last_snr;   // x 4
  uint16_t n_direct_dups, n_flood_dups;
  uint32_t total_rx_air_time_secs;
  uint32_t n_recv_errors;
};

struct NeighbourInfo {
  mesh::Identity id;
  uint32_t advert_timestamp;
  uint32_t heard_timestamp;
  int8_t snr; // multiplied by 4, user should divide to get float value
};

#ifndef FIRMWARE_BUILD_DATE
  #define FIRMWARE_BUILD_DATE   "14 Aug 2026"
#endif
#ifndef FIRMWARE_BUILD_EPOCH
  #define FIRMWARE_BUILD_EPOCH  0UL
#endif

#ifndef FIRMWARE_VERSION
  #define FIRMWARE_VERSION   "v1.17.1"
#endif

#define FIRMWARE_ROLE "repeater"

#define PACKET_LOG_FILE  "/packet_log"

#ifndef MAX_SCHEDULED_RADIO_SETTINGS_PER_TYPE
  #define MAX_SCHEDULED_RADIO_SETTINGS_PER_TYPE 3
#endif

#define MAX_SCHEDULED_RADIO_SETTINGS (MAX_SCHEDULED_RADIO_SETTINGS_PER_TYPE * 2)

#ifndef MESH_ENABLE_FLOOD_RULE_ENGINE
  // STM32WL repeater images have only 240 KB of application flash. They keep
  // the established dynamic flood.filter table unless a larger target profile
  // explicitly opts into the generalized rule parser and persistence format.
  #if defined(STM32_PLATFORM)
    #define MESH_ENABLE_FLOOD_RULE_ENGINE 0
  #else
    #define MESH_ENABLE_FLOOD_RULE_ENGINE 1
  #endif
#endif
#ifndef FLOOD_PACKET_FILTER_SLOTS
  #if MESH_ENABLE_FLOOD_RULE_ENGINE
    #define FLOOD_PACKET_FILTER_SLOTS 63
  #else
    #define FLOOD_PACKET_FILTER_SLOTS 16
  #endif
#endif
#define FLOOD_PACKET_FILTER_ANY_TYPE  0xFF
#define FLOOD_PACKET_FILTER_MAX_HOPS  63
#define FLOOD_PACKET_FILTER_SCOPE_NAME_LEN 32
#if defined(ESP32)
  #define FLOOD_PACKET_FILTER_BLACKLIST_MAX 255
#else
  #define FLOOD_PACKET_FILTER_BLACKLIST_MAX 18
#endif
#define FLOOD_PACKET_FILTER_BLACKLIST_REPLACE_MAX 18
#define FLOOD_PACKET_FILTER_PATH_ID_SIZE 3
#define FLOOD_PACKET_FILTER_PATH_PREFIX_HOPS_MAX 3
#define FLOOD_PACKET_FILTER_PATH_PREFIX_BYTES_MAX \
    (FLOOD_PACKET_FILTER_PATH_PREFIX_HOPS_MAX * 3)

#ifndef FLOOD_CHANNEL_SCOPE_SLOTS
  #if defined(ESP32)
    #define FLOOD_CHANNEL_SCOPE_SLOTS 255
  #elif defined(STM32_PLATFORM)
    #define FLOOD_CHANNEL_SCOPE_SLOTS 15
  #else
    #define FLOOD_CHANNEL_SCOPE_SLOTS 31
  #endif
#endif
#define FLOOD_CHANNEL_SCOPE_TXT_ANY    0
#define FLOOD_CHANNEL_SCOPE_LOGIN_ANY  1
#define FLOOD_CHANNEL_SCOPE_OTHER_ANY  2
#ifndef FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS
  // STM32WL repeater images and RAM are both exceptionally tight. Keep one
  // reusable regionless target there; other platforms scale with the rule
  // table up to the region-map capacity.
  #if defined(STM32_PLATFORM)
    #define FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS 1
  #elif FLOOD_CHANNEL_SCOPE_SLOTS < MAX_REGION_ENTRIES
    #define FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS FLOOD_CHANNEL_SCOPE_SLOTS
  #else
    #define FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS MAX_REGION_ENTRIES
  #endif
#endif
#ifndef FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS
  #define FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS FLOOD_CHANNEL_SCOPE_SLOTS
#endif

#ifndef MESH_ENABLE_FLOOD_GROUP_MODERATION
  #define MESH_ENABLE_FLOOD_GROUP_MODERATION 1
#endif
#ifndef FLOOD_GROUP_MODERATION_SLOTS
  #define FLOOD_GROUP_MODERATION_SLOTS 16
#endif
#define FLOOD_GROUP_MODERATION_NAME_LEN       32
#define FLOOD_GROUP_MODERATION_PATH_HOPS_MAX  3
#define FLOOD_GROUP_MODERATION_PATH_BYTES_MAX (FLOOD_GROUP_MODERATION_PATH_HOPS_MAX * 3)
#define FLOOD_GROUP_MODERATION_HOPS_ALL       0xFF
#define FLOOD_GROUP_MODERATION_RATE_UNLIMITED 0xFFFF

#ifndef MESH_ENABLE_CLOCK_SYNC
  #define MESH_ENABLE_CLOCK_SYNC 1
#endif

#ifndef CLOCK_SYNC_SAMPLE_SLOTS
  #define CLOCK_SYNC_SAMPLE_SLOTS 16
#endif
#ifndef CLOCK_SYNC_MESH_DEFAULT_ENABLED
  #define CLOCK_SYNC_MESH_DEFAULT_ENABLED 1
#endif
#ifndef CLOCK_SYNC_MESH_EDGE_DEFAULT_ENABLED
  #define CLOCK_SYNC_MESH_EDGE_DEFAULT_ENABLED 1
#endif
#define CLOCK_SYNC_REQUIRED_SAMPLES_MIN     3
#define CLOCK_SYNC_REQUIRED_SAMPLES_MAX     CLOCK_SYNC_SAMPLE_SLOTS
#define CLOCK_SYNC_REQUIRED_SAMPLES_DEFAULT 9
#define CLOCK_SYNC_PATH_ID_SIZE             8
#define CLOCK_SYNC_STARTUP_DELAY_MILLIS     (30ULL * 60ULL * 1000ULL)
#define CLOCK_SYNC_RETRY_INTERVAL_MILLIS    (30ULL * 60ULL * 1000ULL)
#define CLOCK_SYNC_RESYNC_INTERVAL_MILLIS   (7ULL * 24ULL * 60ULL * 60ULL * 1000ULL)
#define CLOCK_SYNC_SAMPLE_MAX_AGE_MILLIS    (2UL * 60UL * 60UL * 1000UL)
#define CLOCK_SYNC_CONSENSUS_WINDOW_SECONDS 600UL
#define CLOCK_SYNC_DRIFT_MIN_SECONDS        30UL
#define CLOCK_SYNC_DRIFT_MAX_SECONDS        86400UL
#define CLOCK_SYNC_MESH_SUPPRESS_NONE       0
#define CLOCK_SYNC_MESH_SUPPRESS_CLI        1
#define CLOCK_SYNC_MESH_SUPPRESS_GPS        2
#define CLOCK_SYNC_MESH_SUPPRESS_INTERNET   3

#define RECENT_REPEATER_MAX_AGE_MILLIS        (24UL * 60UL * 60UL * 1000UL)
#define RECENT_REPEATER_SWEEP_INTERVAL_MILLIS (3UL * 60UL * 60UL * 1000UL)
#define RADIO_APPLY_RETRY_INTERVAL_MILLIS     1000UL
#define SCHEDULED_RADIO_CLOCK_CHECKPOINT_SECS 60UL

class MyMesh : public mesh::Mesh, public CommonCLICallbacks
#ifdef WITH_WEBCONFIG
    , public WebConfigServer::Callbacks
#endif
{
  struct ScheduledRadioSetting {
    bool active;
    bool temporary;
    bool started;
    float freq;
    float bw;
    uint8_t sf;
    uint8_t cr;
    uint32_t start_time;
    uint32_t end_time;
  };

  FILESYSTEM* _fs;
  uint32_t last_millis;
  uint64_t uptime_millis;
  unsigned long next_local_advert, next_flood_advert;
  mesh::DeferredCliCommand deferred_cli_command;
  mesh::RemoteCliReplyCache remote_cli_reply_cache;
  TransportKey deferred_cli_reply_scope;
  bool deferred_cli_reply_scoped;
#if MESH_ENABLE_HOST_CLI
  bool host_cli_waiting;
  bool host_cli_claimed;
  bool host_cli_claim_emit;
  unsigned long host_cli_deadline;
  unsigned long host_cli_claim_emit_at;
  uint64_t host_cli_nonce;
  uint64_t host_cli_claim_challenge;
#endif
  uint32_t pending_self_advert_delay;
  bool pending_self_advert;
  bool pending_self_advert_flood;
  unsigned long next_battery_alert_check;
  unsigned long next_rx_watchdog_check;
  unsigned long next_recent_repeater_sweep;
  uint64_t last_battery_alert_sent;
  mesh::Packet* pending_battery_alert_packet;
  bool battery_alert_sent;
  bool _logging;
  NodePrefs _prefs;
  ClientACL  acl;
  CommonCLI _cli;
#if defined(ESP32_PLATFORM) || defined(USER_GPIO_CONTROL)
  UserGpioReplyTracker _gpio_reply_tracker;
#endif
  uint8_t reply_data[MAX_PACKET_PAYLOAD];
  uint8_t reply_path[MAX_PATH_SIZE];
  uint8_t reply_path_len;
  TransportKeyStore key_store;
  RegionMap region_map, temp_map;
  RegionEntry* load_stack[8];
  RegionEntry* recv_pkt_region;
  bool recv_pkt_regionless_scope_set;
  bool recv_pkt_channel_scope_bypass;
  bool recv_pkt_channel_scope_rejected;
  TransportKey default_scope;
  RateLimiter discover_limiter, anon_limiter;
  struct FloodRetryBridgeState {
    uint8_t key[MAX_HASH_SIZE];
    uint8_t source_mask;
    uint8_t target_mask;
    uint8_t heard_mask;
    uint8_t progress_marker;
    bool active;
  };
  struct FloodRetryBridgeReachability {
    uint8_t prefix[MAX_ROUTE_HASH_BYTES];
    uint8_t prefix_len;
    uint32_t last_heard_millis;
  };
  struct FloodPacketFilterEntry {
    bool active;
    uint8_t payload_type;
    uint8_t min_hops;
    uint8_t max_hops;
    bool suspend_on_temp_radio;
    char scope_name[FLOOD_PACKET_FILTER_SCOPE_NAME_LEN];
    bool match_blacklisted_path;
#if !MESH_ENABLE_FLOOD_RULE_ENGINE
    bool scope_requires_region_match;
#endif
    bool scope_uses_slow_timing;
#if MESH_ENABLE_FLOOD_RULE_ENGINE
    uint8_t incoming_scope_kind;
    char incoming_scope_name[FLOOD_PACKET_FILTER_SCOPE_NAME_LEN];
    uint8_t channel_key_len;
    uint8_t channel_hash;
    uint8_t channel_secret[PUB_KEY_SIZE];
    char channel_name[FLOOD_GROUP_MODERATION_NAME_LEN];
    uint8_t path_hash_size;
    uint8_t path_hops;
    uint8_t path[FLOOD_PACKET_FILTER_PATH_PREFIX_BYTES_MAX];
    char target_region_name[FLOOD_PACKET_FILTER_SCOPE_NAME_LEN];
    bool drop_on_match;
    bool rate_limit_enabled;
    uint16_t rate_per_minute;
    uint8_t priority;
    bool stop_on_match;
    bool retry_on_match;
    uint32_t rate_window_started;
    uint16_t rate_window_count;
    bool rate_window_active;
#endif
  };
  struct FloodChannelScopeEntry {
    // Zero means unused. Otherwise this is either a region ID or a one-based
    // flood_channel_direct_scopes index, as selected by selector.
    uint16_t target_id;
    // Encodes txt:*/login:*/other:* or an exact key length, target kind,
    // optional path selector, and fast/slow timing.
    uint8_t selector;
    uint8_t channel_hash;
    uint8_t secret[PUB_KEY_SIZE];
  };
  struct FloodChannelScopeRequireEntry {
    uint8_t key_len;  // zero means unused
    uint8_t channel_hash;
    uint8_t secret[PUB_KEY_SIZE];
  };
  struct FloodGroupModerationEntry {
    bool active;
    uint8_t key_len;
    uint8_t hash_prefix[FLOOD_CHANNEL_KEY_PREFIX_LEN];
    uint8_t secret[PUB_KEY_SIZE];
    char channel_name[FLOOD_GROUP_MODERATION_NAME_LEN];
    char sender[FLOOD_GROUP_MODERATION_NAME_LEN];
    uint8_t path_hash_size;
    uint8_t path_hops;
    uint8_t path[FLOOD_GROUP_MODERATION_PATH_BYTES_MAX];
    uint8_t max_hops;
    uint16_t rate_per_minute;
    uint32_t rate_window_started;
    uint16_t rate_window_count;
    bool rate_window_active;
  };
  struct ClockSyncSample {
    bool active;
    uint8_t source_kind;
    uint8_t source_id[4];
    uint8_t path_id[CLOCK_SYNC_PATH_ID_SIZE];
    uint32_t epoch;
    uint32_t received_millis;
  };
  mutable FloodRetryBridgeState flood_retry_bridge_states[MAX_FLOOD_RETRY_SLOTS];
  FloodRetryBridgeReachability flood_retry_bridge_reachability[FLOOD_RETRY_BRIDGE_BUCKETS + 1];
  FloodPacketFilterEntry flood_packet_filters[FLOOD_PACKET_FILTER_SLOTS];
  uint8_t flood_packet_filter_blacklist_count;
  uint8_t flood_packet_filter_blacklist[FLOOD_PACKET_FILTER_BLACKLIST_MAX]
                                       [FLOOD_PACKET_FILTER_PATH_ID_SIZE];
  FloodChannelScopeEntry flood_channel_scopes[FLOOD_CHANNEL_SCOPE_SLOTS];
  char flood_channel_direct_scopes[FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS]
                                  [FLOOD_PACKET_FILTER_SCOPE_NAME_LEN];
  FloodChannelScopeRequireEntry
      flood_channel_scope_requirements[FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS];
#if MESH_ENABLE_FLOOD_GROUP_MODERATION
  FloodGroupModerationEntry flood_group_moderation[FLOOD_GROUP_MODERATION_SLOTS];
#endif
  uint64_t recv_pkt_filter_match_mask;
#if MESH_ENABLE_FLOOD_RULE_ENGINE
  bool flood_policy_has_embedded_sections;
  // Zero-based forward-row slot owned by the flood.channel.data compatibility
  // facade. 0xFF means forwarding is enabled and no compatibility row exists.
  uint8_t flood_channel_data_rule_slot;
  uint8_t flood_channel_data_rule_max_hops;
#endif
  ClockSyncSample clock_sync_samples[CLOCK_SYNC_SAMPLE_SLOTS];
  bool clock_sync_mesh_enabled;
  bool clock_sync_mesh_edge_enabled;
  bool clock_sync_internet_enabled;
  bool clock_sync_complete;
  bool clock_sync_internet_pending;
  bool clock_sync_force_mesh_pending;
  uint8_t clock_sync_mesh_suppressed_by;
  uint8_t clock_sync_last_result;
  uint8_t clock_sync_last_source;
  uint8_t clock_sync_last_sample_count;
  uint8_t clock_sync_last_fresh_count;
  uint8_t clock_sync_last_required_count;
  uint8_t clock_sync_required_samples;
  uint32_t clock_sync_drift_seconds;
  uint32_t clock_sync_last_estimate;
  uint32_t clock_sync_last_abs_drift;
  uint32_t clock_sync_internet_requested_millis;
  uint64_t clock_sync_next_attempt_uptime;
  uint32_t pending_discover_tag;
  unsigned long pending_discover_until;
  bool region_load_active;
  unsigned long dirty_contacts_expiry;
#if MAX_NEIGHBOURS
  NeighbourInfo neighbours[MAX_NEIGHBOURS];
#endif
  CayenneLPP telemetry;
#if MESH_ENABLE_TELEMETRY_HISTORY
  mesh::TelemetryHistory telemetry_history;
  mesh::ExternalVoltageHistory external_voltage_history;
  bool telemetry_history_tx_enabled;
  uint8_t telemetry_history_tx_path[MAX_PATH_SIZE];
  uint8_t telemetry_history_tx_path_len;
  uint8_t telemetry_history_tx_interval_days;
  uint8_t telemetry_history_tx_pending;
  bool telemetry_history_tx_manual;
  uint8_t telemetry_history_tx_external_channel;
  uint8_t telemetry_history_tx_external_chunk;
  uint64_t telemetry_history_next_tx_uptime;
  uint64_t telemetry_history_tx_resume_uptime;
#endif
  unsigned long _ota_update_at = 0;  // deferred `ota update` fire time (0 = none scheduled)
  float active_bw;  // live BW, including temporary radio overrides
  uint8_t active_sf;  // live SF, including temporary radio overrides
  uint8_t active_cr;   // live CR, including temporary radio overrides
  bool saved_radio_apply_pending;
  bool temp_radio_handoff_pending;
  bool temp_radio_applied;
  bool scheduled_temp_radio_started;
  uint32_t next_scheduled_radio_time;
  unsigned long next_scheduled_radio_check_at;
  uint32_t scheduled_temp_radio_end_time;
  unsigned long scheduled_temp_radio_end_check_at;
  bool scheduled_temp_radio_end_check_final;
  unsigned long scheduled_radio_retry_at;
  uint8_t scheduled_radio_retry_failures;
  ScheduledRadioSetting scheduled_radio_settings[MAX_SCHEDULED_RADIO_SETTINGS];
  int  matching_peer_indexes[MAX_CLIENTS];
#if defined(WITH_MQTT_BRIDGE)
  MQTTBridge* mqtt_bridge;
#elif defined(WITH_RS232_BRIDGE)
  RS232Bridge bridge;
#elif defined(WITH_ESPNOW_BRIDGE)
  ESPNowBridge bridge;
#endif
#ifdef WITH_BRIDGE
  AbstractBridge* activeBridge() {
#ifdef WITH_MQTT_BRIDGE
    return mqtt_bridge;
#else
    return &bridge;
#endif
  }
  const AbstractBridge* activeBridge() const {
#ifdef WITH_MQTT_BRIDGE
    return mqtt_bridge;
#else
    return &bridge;
#endif
  }
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

#if defined(WITH_MQTT_NEIGHBORS)
  // Neighbor-scope discovery: a snapshot of the neighbor table overlaid with an
  // anon-regions query per neighbor, published to the MQTT neighbors topic once
  // every neighbor has responded or timed out.
  enum NeighborDiscoverStatus : uint8_t {
    ND_UNSENT = 0,
    ND_QUEUED = 1,
    ND_PENDING = 2,
    ND_RESPONDED = 3,
    ND_TIMEOUT = 4,
    ND_SEND_FAILED = 5,
  };
  struct NeighborDiscoverEntry {
    mesh::Identity id;       // immutable snapshot: neighbour table can change mid-pass
    uint32_t heard_timestamp;
    int8_t snr;              // multiplied by 4
    uint32_t tag;            // anon-regions request tag we're waiting on
    char scopes[96];         // scope names from the response
    uint8_t status;          // NeighborDiscoverStatus
  };
  NeighborDiscoverEntry neighbor_discover[MAX_NEIGHBOURS];
  uint8_t neighbor_discover_count;
  uint8_t neighbor_discover_next;            // newest-first entry currently being queried
  uint8_t neighbor_discover_publish_count;    // completed prefix that fits the JSON buffer
  uint8_t neighbor_discover_queried_count;    // requests confirmed transmitted
  size_t neighbor_discover_json_size;
  bool neighbor_discover_truncated;
  bool neighbor_discover_active;          // scope-query phase in flight
  bool neighbor_table_refresh_active;     // zero-hop table refresh (stage 1) in flight
  bool neighbor_table_refresh_periodic;   // that refresh was kicked by the periodic timer
  unsigned long neighbor_discover_until;  // current queue or response deadline
  mesh::Packet* neighbor_discover_request; // request awaiting TX completion
  unsigned long next_neighbors_publish;   // periodic publish deadline (0 = fire ASAP)
  char self_scopes_buf[96];
  char self_default_scope_buf[31];
  char neighbor_discover_origin[32];

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
  // Overlay peer indices are offset by this base so onPeerDataRecv can tell a
  // discovery response apart from a normal ACL-client index.
  static const int NEIGHBOR_DISCOVER_PEER_BASE = 1000;
  static const unsigned long NEIGHBOR_DISCOVER_QUEUE_TIMEOUT_MS = 29000;
  static const int NEIGHBOR_DISCOVER_MIN_FREE_PACKETS = 5;
#endif

  bool extractDirectRetryPrefix(const mesh::Packet* packet, uint8_t* prefix, uint8_t& prefix_len) const;
  int8_t getDirectRetryMinSNRX4() const;
  uint8_t getDirectRetryCodingRateForSNR(int8_t snr_x4) const;
  uint8_t getDirectRetryConfiguredMaxAttempts() const;
  uint32_t getDirectRetryAttemptStepMillis() const;
  bool hasFloodRetryPrefixes() const;
  bool floodRetryPrefixMatches(const mesh::Packet* packet) const;
  bool floodRetryLastHopMatches(const mesh::Packet* packet) const;
  bool floodRetryPrefixIgnored(const uint8_t* prefix, uint8_t prefix_len) const;
  uint8_t floodRetryEffectivePathLength(const mesh::Packet* packet, uint8_t max_hops = 0xFF) const;
  bool floodRetryPrefixFresh(const uint8_t* prefix, uint8_t prefix_len) const;
  uint8_t floodRetryBucketMaskForPrefix(const uint8_t* prefix, uint8_t prefix_len, bool require_fresh) const;
  uint8_t floodRetryBucketMaskForPathHop(const uint8_t* prefix, uint8_t prefix_len, uint8_t hop,
                                         uint8_t progress_marker) const;
  uint8_t floodRetrySourceMask(const mesh::Packet* packet) const;
  bool floodRetryBridgeBucketFresh(uint8_t bucket) const;
  void recordFloodRetryBridgeReachability(const uint8_t* prefix, uint8_t prefix_len, uint8_t bucket_mask);
  uint8_t floodRetryBridgeTargetMask(uint8_t source_mask) const;
  uint8_t floodRetryBridgeHeardMask(const mesh::Packet* packet, uint8_t source_mask,
                                    uint8_t progress_marker) const;
  bool floodRetryBridgeEligible(const mesh::Packet* packet) const;
  FloodRetryBridgeState* floodRetryBridgeStateFor(const mesh::Packet* packet, bool create) const;
  void clearFloodRetryBridgeStateByKey(const uint8_t* retry_key);
  void refreshFloodRetryReachability(const mesh::Packet* packet);
  void formatFloodRetryPath(char* dest, size_t dest_len, const mesh::Packet* packet) const;
  bool handleClientPathCommand(ClientInfo* sender, char* command, char* reply);
  bool formatFloodRetryHeard(char* dest, size_t dest_len, const mesh::Packet* packet) const;
  void putNeighbour(const mesh::Identity& id, uint32_t timestamp, float snr);
  uint8_t handleLoginReq(const mesh::Identity& sender, const uint8_t* secret, uint32_t sender_timestamp, const uint8_t* data, bool is_flood);
  uint8_t handleAnonRegionsReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data);
  uint8_t handleAnonOwnerReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data);
  uint8_t handleAnonClockReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data);
  int handleRequest(ClientInfo* sender, uint32_t sender_timestamp, uint8_t* payload, size_t payload_len);
  mesh::Packet* createSelfAdvert();
  void processDeferredCliCommand();
  void clearDeferredCliCommand();
#if MESH_ENABLE_HOST_CLI
  bool completeHostCliRequest(const char* service_reply);
#endif
  void sendRemoteCliReply(ClientInfo* client, const uint8_t* secret,
                          uint8_t path_hash_size, uint32_t sender_timestamp,
                          const char* reply, const TransportKey* fallback_scope);
  void sendClientReplyWithFallbackScope(ClientInfo* client, mesh::Packet* packet,
                                        unsigned long delay_millis, uint8_t path_hash_size,
                                        const TransportKey* fallback_scope);
  void servicePostMeshLoop();
#if MESH_ENABLE_TELEMETRY_HISTORY
  void sampleTelemetryHistory();
#if MESH_ENABLE_TELEMETRY_GPS_HISTORY
  uint8_t resizeTelemetryGpsDays(uint8_t requested_days);
#endif
  void loadTelemetryHistoryTxPrefs();
  bool saveTelemetryHistoryTxPrefs();
  void serviceTelemetryHistoryTx();
  bool sendTelemetryHistorySnapshot(mesh::TelemetryHistory::Series series);
  bool sendExternalVoltageHistorySnapshot(uint8_t channel_index,
                                          uint8_t chunk_index);
  void formatTelemetryHistoryTxStatus(char* reply, size_t reply_size) const;
#endif
  void sendSelfAdvertisementNow(uint32_t delay_millis, bool flood);
  bool sendRepeatersFloodText(const char* text, const TransportKey* scope = nullptr,
                              mesh::Packet** queued_packet = nullptr);
  uint8_t getRegionDepth(const RegionEntry* region);
  const RegionEntry* findNarrowestBatteryAlertRegion(bool& ambiguous);
  bool getBatteryAlertScopeForRegion(const RegionEntry& region, TransportKey& scope);
  bool resolveBatteryAlertScope(TransportKey& scope);
  void checkBatteryAlert();
  void checkRxInactivityWatchdog();
  void expireRecentRepeatersIfDue();
  void printRecentRepeatersSerial();

  File openAppend(const char* fname);
  bool isLooped(const mesh::Packet* packet, const uint8_t max_counters[]);
  bool applyRadioParams(float freq, float bw, uint8_t sf, uint8_t cr);
  bool applySavedRadioParams();
  void queueSavedRadioApply();
  void refreshScheduledRadioState();
  void processScheduledRadioSettings();
  bool isMillisTimerDue(unsigned long timestamp) const;
  bool floodChannelDataHopApplies(const mesh::Packet* packet) const;
  bool loadFloodPacketFilters();
  bool saveFloodPacketFilters(bool empty_scope_phase = false,
                              bool empty_forward_phase = false);
#if MESH_ENABLE_FLOOD_RULE_ENGINE
  bool migrateLegacyFloodChannelBlocks();
  bool migrateLegacyFloodChannelData();
  bool importLegacyFloodPolicySections();
  int findFloodChannelDataRule() const;
  bool isFloodChannelDataRule(const FloodPacketFilterEntry& entry) const;
  void formatFloodChannelData(char* reply) const;
  void formatFloodChannelDataHops(char* reply) const;
  void setFloodChannelData(const char* value, char* reply);
  void setFloodChannelDataHops(const char* value, char* reply);
#endif
  bool loadFloodPacketFilterBlacklist();
  bool saveFloodPacketFilterBlacklist();
  void seedDefaultFloodPacketFilters();
  bool floodPacketFilterBlacklistMatches(const mesh::Packet* packet) const;
  bool floodPacketFilterFieldsMatch(const FloodPacketFilterEntry& entry,
                                    const mesh::Packet* packet,
                                    bool incoming_is_scoped,
                                    uint16_t incoming_transport_code,
                                    bool incoming_region_allowed,
                                    const RegionEntry* incoming_region) const;
  bool authenticateFloodPacketFilterChannel(
      const FloodPacketFilterEntry& entry,
      const mesh::Packet* packet) const;
  bool hasFloodPacketFilterRetryRules() const;
  bool floodPacketFilterAllowsRetry(uint64_t match_mask) const;
  int nextFloodPacketFilterMatch(uint64_t match_mask,
                                 uint64_t visited_mask) const;
  bool resolveFloodPacketFilterTargetRegion(
      const char* name, TransportKey& scope,
      const char*& canonical_name);
  uint64_t applyFloodPacketFilterStop(uint64_t match_mask);
  uint64_t evaluateFloodPacketFilterMatches(
      const mesh::Packet* packet, bool incoming_region_allowed,
      const RegionEntry* incoming_region);
  bool applyFloodPacketFilterScope(mesh::Packet* packet, uint64_t match_mask,
                                   bool& scope_set, bool& fast_track,
                                   bool log_change = true);
  bool shouldBlockFloodPacketForward(const mesh::Packet* packet) const;
  void commitFloodPacketFilterRates(const mesh::Packet* packet);
  void formatFloodPacketFilters(const char* args, char* reply) const;
  void formatFloodPacketFilterDetail(int index, char* reply, size_t reply_len) const;
  void setFloodPacketFilter(const char* args, char* reply,
                            bool require_explicit_action = false);
  void deleteFloodPacketFilter(const char* args, char* reply);
  void formatFloodPacketFilterBlacklist(const char* args, char* reply) const;
  void setFloodPacketFilterBlacklist(const char* args, char* reply);
  void deleteFloodPacketFilterBlacklist(const char* args, char* reply);
  bool loadFloodChannelScopes();
  bool saveFloodChannelScopes(bool empty_table = false);
  bool applyFloodChannelScopeTarget(mesh::Packet* packet, const FloodChannelScopeEntry& entry,
                                    bool& scope_changed, bool& fast_track,
                                    bool& regionless_scope_set,
                                    bool log_change = true);
  bool applyFloodChannelScope(mesh::Packet* packet, bool& fast_track,
                              bool& regionless_scope_set,
                              bool log_change = true);
  static uint8_t scoreFloodTransportScope(const mesh::Packet* packet, void* context);
  uint8_t getFloodTransportScopeDepth(const mesh::Packet* packet);
  void formatFloodChannelScopes(const char* args, char* reply);
  void formatFloodChannelScopeDetail(int index, char* reply, size_t reply_len);
  void setFloodChannelScope(const char* args, char* reply);
  void deleteFloodChannelScope(const char* args, char* reply);
  void loadFloodChannelScopeRequirements();
  bool saveFloodChannelScopeRequirements(bool empty_table = false);
  bool findFloodChannelScopeRequirementMatch(const mesh::Packet* packet,
                                             bool& table_active) const;
  void formatFloodChannelScopeRequirements(const char* args, char* reply);
  void formatFloodChannelScopeRequirementDetail(int index, char* reply,
                                                size_t reply_len) const;
  void setFloodChannelScopeRequirement(const char* args, char* reply);
  void deleteFloodChannelScopeRequirement(const char* args, char* reply);
  void loadFloodGroupModeration();
  bool saveFloodGroupModeration();
  bool shouldBlockFloodGroupTextForward(const mesh::Packet* packet);
  void formatFloodGroupModeration(const char* args, char* reply) const;
  void formatFloodGroupModerationDetail(int index, char* reply, size_t reply_len) const;
  void setFloodGroupModeration(const char* args, char* reply);
  void deleteFloodGroupModeration(const char* args, char* reply);
  bool decodeFloodGroupPlainText(const mesh::Packet* packet, const uint8_t* secret, uint8_t key_len,
                                 uint32_t& timestamp, char* sender, size_t sender_len) const;
  void loadClockSyncPrefs();
  bool saveClockSyncPrefs();
  void resetClockSyncAttempt();
  void suppressMeshClockSyncForBoot(uint8_t source);
  void checkGpsClockSyncOverride();
  bool isClockSyncCollectionActive() const;
  uint32_t estimateClockTransitMillis(const mesh::Packet* packet) const;
  void recordClockSyncSample(uint8_t source_kind, const uint8_t source_id[4], uint32_t epoch,
                             const mesh::Packet* packet);
  void recordAcceptedFloodClockSample(const mesh::Packet* packet);
  void recordPublicChannelClockSample(const mesh::Packet* packet);
  bool estimateMeshClock(uint32_t& estimate, uint8_t& fresh_count,
                         uint8_t& agreeing_count, uint8_t& required_count) const;
  bool applyClockEstimate(uint32_t estimate, uint8_t source, uint8_t sample_count);
  void checkClockSync();
  void formatClockSyncStatus(const char* args, char* reply, size_t reply_len) const;
  void formatClockSyncTable(char* reply, size_t reply_len) const;
  void formatClockSyncSampleDetail(int index, char* reply, size_t reply_len) const;
  bool hasScheduledRadioWorkDue() const;
  uint32_t limitSleepToMillisTimer(unsigned long timestamp, uint32_t sleep_secs) const;
  uint32_t limitSleepToScheduledRadioWork(uint32_t sleep_secs) const;
  bool hasStartedScheduledTempRadio() const;
  int findFreeScheduledRadioSlot() const;
  int countScheduledRadioSettings(bool temporary) const;
  int findScheduledRadioSettingByIndex(bool temporary, int wanted) const;
  int getScheduledRadioSettingIndex(bool temporary, int slot_idx) const;
  bool scheduledRadioConflicts(bool temporary, uint32_t start_time, uint32_t end_time) const;
  void clearScheduledRadioSetting(int idx, bool restore_if_started);
  void formatScheduledRadioDuration(char* dest, size_t dest_len, uint32_t target_time) const;
  void formatRadioParamTuple(char* dest, size_t dest_len, const ScheduledRadioSetting& setting) const;
  void formatScheduledRadioSetting(char* reply, int setting_idx, int display_idx) const;

protected:
  bool isTempRadioActive() const override;
  float getAirtimeBudgetFactor() const override {
    // A bounded TempRadio window is an explicitly coordinated private OTA
    // channel. Use its full TX budget without changing the saved normal-radio
    // duty factor; restoring the ordinary tuple restores ordinary pacing too.
    return isTempRadioActive() ? 0.0f : _prefs.airtime_factor;
  }
  bool getCADEnabled() const override {
    return _prefs.cad_enabled;
  }

  bool allowPacketForward(const mesh::Packet* packet) override;
  const char* getLogDateTime() override;
  void logRxRaw(float snr, float rssi, const uint8_t raw[], int len) override;

  void logRx(mesh::Packet* pkt, int len, float score) override;
  void logTx(mesh::Packet* pkt, int len) override;
  void logTxFail(mesh::Packet* pkt, int len) override;
  int calcRxDelay(float score, uint32_t air_time) const override;
  int calcRxDelayForPacket(const mesh::Packet* packet, float score,
                           uint32_t air_time) override;
  bool shouldBypassRxDelay(const mesh::Packet* packet) override;
  bool evaluateScopeRewriteTiming(const mesh::Packet* packet,
                                  bool& fast_track);

  uint32_t getRetransmitDelay(const mesh::Packet* packet) override;
  uint32_t getSlowScopeRetransmitDelay(const mesh::Packet* packet);
  uint32_t getDirectRetransmitDelay(const mesh::Packet* packet) override;
  bool supportsBasicRetryConfig() const override { return true; }
  bool supportsAdvancedRetryConfig() const override { return true; }
  void onRetryConfigChanged() override;
  uint8_t getDefaultTxCodingRate() const override { return active_cr; }
  bool allowDirectRetry(const mesh::Packet* packet, const uint8_t* next_hop_hash, uint8_t next_hop_hash_len) const override;
  bool maybeShortCircuitDirect(mesh::Packet* packet) override;
  void configureDirectRetryPacket(mesh::Packet* retry, const mesh::Packet* original, uint8_t retry_attempt) override;
  uint32_t getDirectRetryEchoDelay(const mesh::Packet* packet) const override;
  uint8_t getDirectRetryMaxAttempts(const mesh::Packet* packet) const override;
  uint32_t getDirectRetryAttemptDelay(const mesh::Packet* packet, uint8_t attempt_idx) override;
  void onDirectRetryEvent(const char* event, const mesh::Packet* packet, uint32_t delay_millis, uint8_t retry_attempt,
                          const uint8_t* target_hash = NULL, uint8_t target_hash_len = 0,
                          int16_t payload_type = -1) override;
  void onDirectRetryFailed(const uint8_t* next_hop_hash, uint8_t next_hop_hash_len) override;
  void onDirectRetrySucceeded(const uint8_t* next_hop_hash, uint8_t next_hop_hash_len, int8_t snr_x4) override;
  bool allowFloodRetry(const mesh::Packet* packet) const override;
  bool prepareFloodRetry(const mesh::Packet* packet) const override;
  void onFloodRetryEvent(const char* event, const mesh::Packet* packet, uint32_t delay_millis, uint8_t retry_attempt) override;
  void onFloodRetrySlotReleased(const uint8_t* retry_key) override;
  bool hasFloodRetryTargetPrefix(const mesh::Packet* packet) const override;
  uint8_t getFloodRetryMaxPathLength(const mesh::Packet* packet) const override;
  uint8_t getFloodRetryMaxAttempts(const mesh::Packet* packet) const override;
  bool isFloodRetryEchoTarget(const mesh::Packet* packet, uint8_t progress_marker) const override;

  int getInterferenceThreshold() const override {
    return _prefs.interference_threshold;
  }
  int getAGCResetInterval() const override {
    return ((int)_prefs.agc_reset_interval) * 4000;   // milliseconds
  }
#ifdef WITH_MQTT_BRIDGE
  uint32_t getRadioWatchdogMillis() const override {
    return ((uint32_t)_cli.getObserverPrefs()->radio_watchdog_minutes) * 60000UL;
  }
#endif
  uint8_t getExtraAckTransmitCount() const override {
    return _prefs.multi_acks;
  }

#if ENV_INCLUDE_GPS == 1
  void applyGpsPrefs() {
    sensors.setPowerSavingEnabled(_prefs.powersaving_enabled != 0);
    sensors.setSettingValue("gps", _prefs.gps_enabled?"1":"0");
    char interval_str[12];
    sprintf(interval_str, "%u", _prefs.gps_interval);
    sensors.setSettingValue("gps_interval", interval_str);
  }
#endif

  mesh::DispatcherAction onRecvPacket(mesh::Packet* pkt) override;
  void onSendComplete(mesh::Packet* packet) override;
  void onSendFail(mesh::Packet* packet) override;

  void onAnonDataRecv(mesh::Packet* packet, const uint8_t* secret, const mesh::Identity& sender, uint8_t* data, size_t len) override;
  int searchPeersByHash(const uint8_t* hash) override;
  void getPeerSharedSecret(uint8_t* dest_secret, int peer_idx) override;
  void onAdvertRecv(mesh::Packet* packet, const mesh::Identity& id, uint32_t timestamp, const uint8_t* app_data, size_t app_data_len) override;
  void onGroupPacketRecv(mesh::Packet* packet) override;
  void onPeerDataRecv(mesh::Packet* packet, uint8_t type, int sender_idx, const uint8_t* secret, uint8_t* data, size_t len) override;
  bool onPeerPathRecv(mesh::Packet* packet, int sender_idx, const uint8_t* secret, uint8_t* path, uint8_t path_len, uint8_t extra_type, uint8_t* extra, uint8_t extra_len) override;
  void onControlDataRecv(mesh::Packet* packet) override;
  // OTA mesh-integration is centralized in mesh::Mesh (no per-example onOtaRecv / send adapter / tick).

  void sendFloodReply(mesh::Packet* packet, unsigned long delay_millis, uint8_t path_hash_size);
  void sendClientReply(ClientInfo* client, mesh::Packet* packet, unsigned long delay_millis, uint8_t path_hash_size);

public:
  MyMesh(mesh::MainBoard& board, mesh::Radio& radio, mesh::MillisecondClock& ms, mesh::RNG& rng, mesh::RTCClock& rtc, mesh::MeshTables& tables);

  void begin(FILESYSTEM* fs);
  void sendNodeDiscoverReq();
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

  void onManualClockSet() override;

  bool sendFloodScoped(const TransportKey& scope, mesh::Packet* pkt, uint32_t delay_millis, uint8_t path_hash_size);

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
  void addScheduledRadioParams(bool temporary, float freq, float bw, uint8_t sf, uint8_t cr,
                               uint32_t start_time, uint32_t end_time, char* reply) override;
  void formatScheduledRadioParams(bool temporary, const char* selector, char* reply) override;
  void deleteScheduledRadioParams(bool temporary, const char* selector, char* reply) override;
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
  bool supportsRxPowerSavingRfRxDisable() const override;
  bool setRxPowerSavingRfRxDisabled(bool disabled) override;
  bool isRxPowerSavingRfRxDisabled() const override;
  void getRxPsWatchdogCounts(uint32_t* soft, uint32_t* hard) override;
  void formatNeighborsReply(char *reply) override;
  void removeNeighbor(const uint8_t* pubkey, int key_len) override;
  void formatStatsReply(char *reply) override;
  void formatRadioStatsReply(char *reply) override;
  void formatRadioDiagReply(char *reply) override;
  void formatPacketStatsReply(char *reply) override;
  void formatRecentRepeatersReply(char *reply, int page,
                                  const uint8_t* search_prefix,
                                  uint8_t search_prefix_len) override;
  bool setRecentRepeater(const uint8_t* prefix, uint8_t prefix_len, int8_t snr_x4) override;
  void clearRecentRepeaters() override;
  void startRegionsLoad() override;
  bool saveRegions() override;
  void onDefaultRegionChanged(const RegionEntry* r) override;
  mesh::LocalIdentity& getSelfId() override { return self_id; }

  void saveIdentity(const mesh::LocalIdentity& new_id) override;
  void clearStats() override;

  void handleCommand(uint32_t sender_timestamp, ClientInfo* sender, char* command,
                     char* reply, int gpio_client_index = -1,
                     uint8_t gpio_path_hash_size = 1);
  void handleCommand(uint32_t sender_timestamp, char* command, char* reply) {
    handleCommand(sender_timestamp, NULL, command, reply);
  }
#if MESH_ENABLE_HOST_CLI
  bool handleHostCliSerialReply(const char* command, char* reply);
#endif
  void loop();
  uint32_t getPowerSaveSleepSeconds(uint32_t max_secs) const;

#if defined(WITH_BRIDGE)
  void setBridgeState(bool enable) override {
#ifdef WITH_MQTT_BRIDGE
    if (!mqtt_bridge) {
      MQTTNodeInfo node_info;
      node_info.node_name = _prefs.node_name;
      node_info.freq = &_prefs.freq;
      node_info.bw = &_prefs.bw;
      node_info.sf = &_prefs.sf;
      node_info.cr = &_prefs.cr;
      node_info.repeat_flag = &_prefs.disable_fwd;
      node_info.repeat_when_nonzero = false;
      mqtt_bridge = new MQTTBridge(node_info, _cli.getObserverPrefs(),
                                   getRTCClock(), &self_id);
      if (!mqtt_bridge) return;
    }
#endif
    AbstractBridge* active_bridge = activeBridge();
    if (!active_bridge || enable == active_bridge->isRunning()) return;
    if (enable)
    {
#ifdef WITH_MQTT_BRIDGE
      // Set device metadata before starting bridge (same as in begin())
      char device_id[65];
      mesh::LocalIdentity self_id = getSelfId();
      mesh::Utils::toHex(device_id, self_id.pub_key, PUB_KEY_SIZE);
      mqtt_bridge->setDeviceID(device_id);
      mqtt_bridge->setFirmwareVersion(getFirmwareVer());
      mqtt_bridge->setBoardModel(_cli.getBoard()->getManufacturerName());
      mqtt_bridge->setBuildDate(getBuildDate());
      mqtt_bridge->setStatsSources(this, _radio, _cli.getBoard(), _ms);
#endif
      active_bridge->begin();
#ifdef WITH_MQTT_BRIDGE
      _alerter.setBridge(mqtt_bridge);
#endif
    }
    else
    {
      active_bridge->end();
#ifdef WITH_MQTT_BRIDGE
      _alerter.setBridge(nullptr);
#endif
    }
  }

  void restartBridge() override {
    AbstractBridge* active_bridge = activeBridge();
    if (!active_bridge || !active_bridge->isRunning()) return;
#ifdef WITH_WEBCONFIG
    if (_wc_batch_active) {   // coalesced: applied once in onConfigBatchEnd()
      _wc_restart_pending = true;
      return;
    }
#endif
    active_bridge->end();
#ifdef WITH_MQTT_BRIDGE
    // Set device metadata before restarting bridge (same as in begin())
    char device_id[65];
    mesh::LocalIdentity self_id = getSelfId();
    mesh::Utils::toHex(device_id, self_id.pub_key, PUB_KEY_SIZE);
    mqtt_bridge->setDeviceID(device_id);
    mqtt_bridge->setFirmwareVersion(getFirmwareVer());
    mqtt_bridge->setBoardModel(_cli.getBoard()->getManufacturerName());
    mqtt_bridge->setBuildDate(getBuildDate());
    mqtt_bridge->setStatsSources(this, _radio, _cli.getBoard(), _ms);
#endif
    active_bridge->begin();
  }

  void restartBridgeSlot(int slot) override {
#ifdef WITH_MQTT_BRIDGE
    if (!mqtt_bridge || !mqtt_bridge->isRunning()) return;
#ifdef WITH_WEBCONFIG
    if (_wc_batch_active && slot >= 0 && slot < MAX_MQTT_SLOTS) {
      _wc_slot_restart_mask |= static_cast<uint8_t>(1U << slot);
      return;
    }
#endif
    mqtt_bridge->setSlotPreset(slot, _cli.getObserverPrefs()->mqtt_slot_preset[slot]);
#else
    (void)slot;
#endif
  }

#if defined(WITH_MQTT_BRIDGE)
  // Broadcast a key OTA milestone (start/fail only) on the configured alert
  // channel, in addition to the Serial log -- so an operator who triggered
  // `ota update` via remote management still gets feedback that lands well after
  // the command's reply window. Respects the `alert on/off` master switch and
  // rides the configured alert scope (sendChannel -> resolveAlertScope); a no-op
  // when alerts are off or no channel is set. Deliberately NOT wired to routine
  // slot connect/disconnect -- those remain in AlertReporter's fault logic.
  void otaAlert(const char* msg) {
    auto* obs = _cli.getObserverPrefs();
    if (obs && obs->alert_enabled) _alerter.sendText(msg);
  }

  // Best-effort flush of the outbound packet queue before an OTA teardown that
  // blocks the loop until reboot. The START alert (otaAlert) and the CLI reply
  // are queued fire-and-forget (delay 0 / CLI_REPLY_DELAY_MILLIS); once
  // setBridgeState(false) + otaFromManifest() run they spin the loop task until
  // the chip reboots, so anything still in the send queue at that point is
  // silently lost -- the observed "OTA update starting never arrives" case on a
  // busy / duty-limited channel where the packet can't win a TX slot inside the
  // 2.5 s window. Pump the mesh loop so already-queued packets get their airtime,
  // bounded by timeout_ms so a jammed or budget-exhausted channel can't stall the
  // update. Respects duty cycle / CAD: it only drains what is queued, it does not
  // force a transmit. Returns instantly on a healthy node (queue already empty).
  void drainOutbound(uint32_t timeout_ms) {
    unsigned long start = millis();
    while (hasOutbound() || _mgr->getOutboundCount(millis()) > 0) {
      if (millis() - start >= timeout_ms) break;
      mesh::Mesh::loop();  // base dispatcher only -- drives RX + checkSend()/TX
      delay(1);            // yield to the radio ISR / other FreeRTOS tasks
    }
  }
#endif

  // Schedule the pull-OTA flash to run from loop() in ~2.5 s, leaving time for the
  // "Beginning update..." CLI reply (CLI_REPLY_DELAY_MILLIS = 600 ms) to transmit
  // before the flash blocks the loop and reboots.
  bool beginDeferredOtaUpdate() override {
    _ota_update_at = millis() + 2500;
    if (_ota_update_at == 0) _ota_update_at = 1;  // 0 means "none"
#if defined(WITH_MQTT_BRIDGE)
    // Broadcast START now, while the loop still runs (the 2.5 s reply window):
    // the deferred flash blocks the loop and, on success, reboots -- so a start
    // alert queued at fire time could never transmit. See otaAlert().
    otaAlert("OTA update starting");
#endif
    return true;
  }

  int getQueueSize() override {
#ifdef WITH_MQTT_BRIDGE
    return mqtt_bridge ? mqtt_bridge->getQueueSize() : 0;
#else
    return 0;
#endif
  }

  bool isMqttBridgeRunning() override {
#ifdef WITH_MQTT_BRIDGE
    return mqtt_bridge && mqtt_bridge->isRunning();
#else
    return false;
#endif
  }

  bool syncMqttNtp() override {
#ifdef WITH_MQTT_BRIDGE
    if (!mqtt_bridge || !mqtt_bridge->isRunning()) return false;
    // Marshal onto the MQTT task (Core 0); this runs on the CLI thread (Core 1).
    return mqtt_bridge->requestForcedNtpSync(0);
#else
    return false;
#endif
  }

  bool runMqttNtpDiag(char* reply, size_t reply_size, bool verbose) override {
#ifdef WITH_MQTT_BRIDGE
    if (!mqtt_bridge || !mqtt_bridge->isRunning()) return false;
    return mqtt_bridge->ntpDiag(reply, reply_size, verbose);
#else
    (void)reply;
    (void)reply_size;
    (void)verbose;
    return false;
#endif
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

  // To check if there is pending work
  bool hasPendingWork() const;

  bool setRxBoostedGain(bool enable) override;
  void recalibrateNoiseFloor() override { _radio->recalibrateNoiseFloor(); }

  #if defined(USE_LR2021)
  virtual bool configSideDetectors(const uint8_t sideDetSFs[], uint8_t num, float bw) override;
  #endif

};
