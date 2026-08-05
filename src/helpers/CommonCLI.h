#pragma once

#include "Mesh.h"
#include <helpers/IdentityStore.h>
#include <helpers/SensorManager.h>
#include <helpers/ClientACL.h>
#include <helpers/MQTTPresets.h>  // For MAX_MQTT_SLOTS (used in NodePrefs struct layout)
#include <helpers/MQTTPrefs.h>
#include <helpers/RegionMap.h>

#ifndef DEFAULT_CAD_ENABLED
  #define DEFAULT_CAD_ENABLED 0
#endif

#if defined(ESP32_PLATFORM) || defined(USER_GPIO_CONTROL)
#include <helpers/UserGpio.h>
#endif

#if defined(WITH_RS232_BRIDGE) || defined(WITH_ESPNOW_BRIDGE) || defined(WITH_MQTT_BRIDGE)
#define WITH_BRIDGE
#endif

#define ADVERT_LOC_NONE       0
#define ADVERT_LOC_SHARE      1
#define ADVERT_LOC_PREFS      2

#define LOOP_DETECT_OFF       0
#define LOOP_DETECT_MINIMAL   1
#define LOOP_DETECT_MODERATE  2
#define LOOP_DETECT_STRICT    3

#define RETRY_PRESET_INFRA    0
#define RETRY_PRESET_ROOFTOP  1
#define RETRY_PRESET_MOBILE   2
#define RETRY_PRESET_CUSTOM   0xFF

#define DIRECT_RETRY_INFRA_BASE_MS      275
#define DIRECT_RETRY_INFRA_COUNT          4
#define DIRECT_RETRY_INFRA_STEP_MS      150
#define DIRECT_RETRY_INFRA_MARGIN_X4     60

#define DIRECT_RETRY_ROOFTOP_BASE_MS    175
#define DIRECT_RETRY_ROOFTOP_COUNT       15
#define DIRECT_RETRY_ROOFTOP_STEP_MS    100
#define DIRECT_RETRY_ROOFTOP_MARGIN_X4   20

#define DIRECT_RETRY_MOBILE_BASE_MS     175
#define DIRECT_RETRY_MOBILE_COUNT        15
#define DIRECT_RETRY_MOBILE_STEP_MS      50
#define DIRECT_RETRY_MOBILE_MARGIN_X4     0
#define DIRECT_RETRY_RECENT_DEFAULT       1

#define FLOOD_RETRY_INFRA_COUNT           1
#define FLOOD_RETRY_INFRA_MAX_PATH        1

#define FLOOD_RETRY_ROOFTOP_COUNT         3
#define FLOOD_RETRY_ROOFTOP_MAX_PATH      2

#define FLOOD_RETRY_MOBILE_COUNT         15
#define FLOOD_RETRY_MOBILE_MAX_PATH       1
#define FLOOD_RETRY_ADVERT_DEFAULT        0
#define FLOOD_RETRY_GROUP_MAX_PATH_DEFAULT 1

#define BATTERY_ALERT_LOW_PERCENT_DEFAULT       20
#define BATTERY_ALERT_CRITICAL_PERCENT_DEFAULT  10

#ifndef FLOOD_RETRY_PREFIX_SLOTS
  #define FLOOD_RETRY_PREFIX_SLOTS        8
#endif
#ifndef FLOOD_RETRY_PREFIX_LEN
  #define FLOOD_RETRY_PREFIX_LEN          3
#endif
#ifndef FLOOD_RETRY_BRIDGE_BUCKETS
  #define FLOOD_RETRY_BRIDGE_BUCKETS      6
#endif
#ifndef FLOOD_RETRY_BUCKET_PREFIXES
  #define FLOOD_RETRY_BUCKET_PREFIXES     17
#endif
#ifndef FLOOD_RETRY_IGNORE_PREFIXES
  #define FLOOD_RETRY_IGNORE_PREFIXES     8
#endif
#ifndef FLOOD_RETRY_LIST_PREFIXES
  #define FLOOD_RETRY_LIST_PREFIXES       ((FLOOD_RETRY_IGNORE_PREFIXES > FLOOD_RETRY_BUCKET_PREFIXES) ? FLOOD_RETRY_IGNORE_PREFIXES : FLOOD_RETRY_BUCKET_PREFIXES)
#endif
#ifndef FLOOD_RETRY_LIST_TEXT_MAX
  #define FLOOD_RETRY_LIST_TEXT_MAX       (FLOOD_RETRY_LIST_PREFIXES * FLOOD_RETRY_PREFIX_LEN * 2 + FLOOD_RETRY_LIST_PREFIXES)
#endif
#ifndef COMMON_CLI_TMP_LEN
  #define COMMON_CLI_TMP_LEN              ((FLOOD_RETRY_LIST_TEXT_MAX > (PRV_KEY_SIZE * 2 + 4)) ? FLOOD_RETRY_LIST_TEXT_MAX : (PRV_KEY_SIZE * 2 + 4))
#endif

#ifndef FLOOD_CHANNEL_BLOCK_SLOTS
  #define FLOOD_CHANNEL_BLOCK_SLOTS       15
#endif
#ifndef FLOOD_CHANNEL_BLOCK_NAME_LEN
  #define FLOOD_CHANNEL_BLOCK_NAME_LEN    32
#endif
#ifndef FLOOD_CHANNEL_BLOCK_PREFIX_LEN
  #define FLOOD_CHANNEL_BLOCK_PREFIX_LEN  4
#endif
#define FLOOD_CHANNEL_BLOCK_HOPS_ALL      0xFF
#define FLOOD_CHANNEL_BLOCK_HOPS_INHERIT  0xFE

#define DIRECT_RETRY_CR4_MIN_SNR_X4_DEFAULT  40
#define DIRECT_RETRY_CR5_MIN_SNR_X4_DEFAULT  30
#define DIRECT_RETRY_CR7_MIN_SNR_X4_DEFAULT  10
#define DIRECT_RETRY_CR8_MAX_SNR_X4_DEFAULT  10

#define DIRECT_RETRY_PREFS_MAGIC_0  0xD1
#define DIRECT_RETRY_PREFS_MAGIC_1  0x52

#define TELEMETRY_ACCESS_ALL  0
#define TELEMETRY_ACCESS_ACL  1

struct NodePrefs { // persisted to file
  float airtime_factor;
  char node_name[32];
  double node_lat, node_lon;
  char password[16];
  float freq;
  int8_t tx_power_dbm;
  uint8_t disable_fwd;
  uint8_t advert_interval;       // minutes / 2
  uint8_t flood_advert_interval; // hours
  float rx_delay_base;
  float tx_delay_factor;
  char guest_password[16];
  float direct_tx_delay_factor;
  uint32_t guard;
  uint8_t sf;
  uint8_t cr;
  uint8_t allow_read_only;
  uint8_t multi_acks;
  float bw;
  uint8_t flood_max;
  uint8_t flood_max_unscoped;
  uint8_t flood_max_advert;
  uint8_t interference_threshold;
  uint8_t agc_reset_interval; // secs / 4
  // Bridge settings
  uint8_t bridge_enabled; // boolean
  uint16_t bridge_delay;  // milliseconds (default 500 ms)
  uint8_t bridge_pkt_src; // 0 = logTx, 1 = logRx (default logRx)
  uint32_t bridge_baud;   // 9600, 19200, 38400, 57600, 115200 (default 115200)
  uint8_t bridge_channel; // 1-14 (ESP-NOW only)
  char bridge_secret[16]; // for XOR encryption of bridge packets (ESP-NOW only)
  // Power setting
  uint8_t powersaving_enabled; // boolean
  uint8_t reboot_interval; // hours, 0-255 (default 0=disable)
  // Gps settings
  uint8_t gps_enabled;
  uint32_t gps_interval; // in seconds
  uint8_t advert_loc_policy;
  uint32_t discovery_mod_timestamp;
  float adc_multiplier;
  char owner_info[120];
  // NOTE: member order below matches mcarper/keymindCascade (upstream/dev order).
  // It is in-memory only - /com_prefs is read/written field-by-field in loadPrefsInt/
  // savePrefs, whose canonical file order (identical to the flex fleet's through
  // offset 294, keymind retry tail at 295+) is what devices actually persist.
  uint8_t rx_boosted_gain; // power settings
  uint8_t radio_fem_rxgain; // LoRa FEM RX gain setting
  uint8_t path_hash_mode;   // which path mode to use when sending
  uint8_t loop_detect;
  uint8_t cad_enabled;      // hardware Channel Activity Detection before TX (boolean)
  // LR2021 side-detector SFs are runtime-only in upstream/dev. Keep the field
  // here without shifting the established /com_prefs binary layout.
  uint8_t extra_sf[4] = {};
  uint8_t retry_preset;
  uint8_t direct_retry_attempts;
  uint16_t direct_retry_base_ms;
  uint16_t direct_retry_step_ms;
  uint16_t direct_retry_snr_margin_x4;
  int8_t direct_retry_cr4_snr_x4;
  int8_t direct_retry_cr5_snr_x4;
  int8_t direct_retry_cr7_snr_x4;
  int8_t direct_retry_cr8_snr_x4;
  uint8_t direct_retry_enabled;
  uint8_t direct_retry_cr_enabled;
  uint8_t direct_retry_prefs_magic[2];
  uint8_t flood_retry_attempts;
  uint8_t flood_retry_max_path;
  uint8_t flood_retry_prefixes[FLOOD_RETRY_PREFIX_SLOTS][FLOOD_RETRY_PREFIX_LEN];
  uint8_t flood_retry_bridge_enabled;
  uint8_t flood_retry_bridge_buckets[FLOOD_RETRY_BRIDGE_BUCKETS][FLOOD_RETRY_BUCKET_PREFIXES][FLOOD_RETRY_PREFIX_LEN];
  uint8_t flood_retry_ignore_prefixes[FLOOD_RETRY_IGNORE_PREFIXES][FLOOD_RETRY_PREFIX_LEN];
  uint8_t flood_retry_advert_enabled;
  uint8_t battery_alert_enabled;
  uint8_t battery_alert_low_percent;
  uint8_t battery_alert_critical_percent;
  uint8_t direct_retry_recent_enabled;
  uint8_t flood_channel_data_enabled;
  uint8_t flood_channel_block_max_hops;
  uint8_t flood_channel_data_max_hops;
  uint8_t telemetry_access;

  // NOTE: observer settings (MQTT/WiFi/timezone/SNMP/alert) were moved out of
  // NodePrefs into MQTTPrefs (persisted to /mqtt_prefs) so this struct stays
  // aligned with upstream (plus the keymind retry/flood/telemetry tail above). See
  // helpers/MQTTPrefs.h.

#if defined(ENABLE_OTA)
  // OTA config (persisted; synced to OtaContext on load, written on change). 0 = conservative defaults.
  uint8_t ota_autofetch;        // OtaManager AUTOFETCH_* (0=off, 1=any-compatible, 2=signed-only)
  uint8_t ota_autoinstall;      // OtaContext AUTOINSTALL_* (0=off, 1=trusted-only)
  uint8_t ota_signer_count;     // # of allowlisted signer pubkeys below
  uint8_t ota_signers[4][32];   // trusted Ed25519 signer pubkeys (== MAX_OTA_SIGNERS)
  uint16_t ota_checkpoint_blocks; // resume checkpoint cadence (blocks); 0=never. Default 4 (runtime-tunable)
  uint16_t ota_advert_interval;   // OTA beacon re-advertise cadence (mins); 0=off. Default 1440 (24h, tunable)
  uint8_t ota_max_hops;           // OTA flood reach in hops; 0=direct only. Default 3 (runtime-tunable)
#endif

  uint8_t rx_powersaving_enabled; // boolean
  uint32_t rx_ps_rx_us;
  uint32_t rx_ps_sleep_us;
  uint8_t rx_ps_level;      // 0 = manual/explicit us timings; 1..10 = level-derived (auto-retunes on SF/BW change)
  uint8_t rx_ps_preamble;   // 0 = auto (derive from SF); else 16 or 32 = explicit override for level calc
  char battery_alert_region[31]; // named scope for low-battery floods; empty = no alert scope
  uint8_t flood_retry_group_max_path; // PAYLOAD_TYPE_GRP_DATA retry path gate; 0xFF = use only the general gate
  uint8_t rx_watchdog_enabled; // repeater RX-inactivity reboot watchdog (boolean)
  uint8_t system_watchdog_enabled; // nRF52 main-loop hardware watchdog (boolean; default on)
};

#ifdef WITH_MQTT_BRIDGE
static_assert(MQTT_PREFS_SLOT_COUNT == MAX_MQTT_SLOTS,
              "MQTT prefs layout and slot count must change together");

// Observer settings captured from the trailing block of an old-format /com_prefs
// (fork firmware that predates the NodePrefs -> MQTTPrefs split). loadPrefsInt()
// fills this in when it detects the old file layout; loadMQTTPrefs() then applies
// the values one-time if the loaded /mqtt_prefs predates the appended observer
// fields, so SNMP/watchdog/alert config survives the firmware upgrade.
struct LegacyObserverTail {
  bool valid = false;
  uint8_t snmp_enabled;
  char snmp_community[24];
  uint8_t radio_watchdog_minutes;
  uint8_t alert_enabled;
  char alert_psk_hex[33];
  uint16_t alert_wifi_minutes;
  uint16_t alert_mqtt_minutes;
  uint16_t alert_min_interval_min;
  char alert_hashtag[24];
  char alert_region[31];
};
#endif

class CommonCLICallbacks {
public:
  virtual void savePrefs() = 0;
  // Called only after a CLI command successfully changes the RTC. Roles that
  // derive time from untrusted radio traffic can use this to prefer the manual
  // value for the remainder of the boot.
  virtual void onManualClockSet() { }
  // GPIO timers use the active authenticated CLI client as a deduplication
  // source and to route the asynchronous final-transition report. Local CLI
  // commands keep the default source of zero and do not get a network report.
  virtual uint32_t getUserGpioRequestSource() const { return 0; }
  virtual void onUserGpioTimerScheduled(uint8_t /*pin*/, uint32_t /*request_id*/) { }
  virtual void onUserGpioTimerCancelled(uint8_t /*pin*/) { }
  virtual void onUserGpioTimerCompleted(uint8_t /*pin*/, uint8_t /*state*/,
                                        uint32_t /*request_id*/) { }
  virtual const char* getFirmwareVer() = 0;
  virtual const char* getBuildDate() = 0;
  virtual const char* getRole() = 0;
  virtual bool formatFileSystem() = 0;
  virtual void sendSelfAdvertisement(int delay_millis, bool flood) = 0;
  virtual void updateAdvertTimer() = 0;
  virtual void updateFloodAdvertTimer() = 0;
  virtual void setLoggingOn(bool enable) = 0;
  virtual void eraseLogFile() = 0;
  virtual void dumpLogFile() = 0;
  virtual void setTxPower(int8_t power_dbm) = 0;
  virtual void formatNeighborsReply(char *reply) = 0;
  virtual void removeNeighbor(const uint8_t* pubkey, int key_len) {
    // no op by default
  };
  virtual void formatStatsReply(char *reply) = 0;
  virtual void formatRadioStatsReply(char *reply) = 0;
  virtual void formatRadioDiagReply(char *reply) { strcpy(reply, "Not supported"); }
  virtual void formatPacketStatsReply(char *reply) = 0;
  virtual void formatRecentRepeatersReply(char *reply, int page) {
    (void)page;
    if (reply != NULL) reply[0] = 0;
  }
  virtual bool setRecentRepeater(const uint8_t* prefix, uint8_t prefix_len, int8_t snr_x4) {
    (void)prefix;
    (void)prefix_len;
    (void)snr_x4;
    return false;
  }
  virtual void clearRecentRepeaters() {
  }
  // Basic retry controls are portable across roles (enable/count/timing,
  // flood count/path/advert, and the current-CR retry progression). Advanced
  // controls require the repeater's recent-SNR and bridge-routing tables.
  virtual bool supportsBasicRetryConfig() const { return false; }
  virtual bool supportsAdvancedRetryConfig() const { return false; }
  virtual void onRetryConfigChanged() { }
  virtual mesh::LocalIdentity& getSelfId() = 0;
  virtual void saveIdentity(const mesh::LocalIdentity& new_id) = 0;
  virtual void clearStats() = 0;
  virtual void applyTempRadioParams(float freq, float bw, uint8_t sf, uint8_t cr, int timeout_mins) = 0;
#if defined(ENABLE_OTA)
  virtual bool isTempRadioActive() const { return false; }
#endif
  virtual void addScheduledRadioParams(bool temporary, float freq, float bw, uint8_t sf, uint8_t cr,
                                       uint32_t start_time, uint32_t end_time, char* reply) {
    (void)temporary;
    (void)freq;
    (void)bw;
    (void)sf;
    (void)cr;
    (void)start_time;
    (void)end_time;
    strcpy(reply, "Error: unsupported");
  }
  virtual void formatScheduledRadioParams(bool temporary, const char* selector, char* reply) {
    (void)temporary;
    (void)selector;
    strcpy(reply, "Error: unsupported");
  }
  virtual void deleteScheduledRadioParams(bool temporary, const char* selector, char* reply) {
    (void)temporary;
    (void)selector;
    strcpy(reply, "Error: unsupported");
  }
  virtual void setFloodChannelBlock(int index, const uint8_t* secret, uint8_t key_len,
                                    const char* name, uint8_t max_hops, char* reply) {
    (void)index;
    (void)secret;
    (void)key_len;
    (void)name;
    (void)max_hops;
    strcpy(reply, "Error: unsupported");
  }
  virtual void formatFloodChannelBlocks(const char* selector, char* reply) {
    (void)selector;
    strcpy(reply, "Error: unsupported");
  }
  virtual void deleteFloodChannelBlock(const char* selector, char* reply) {
    (void)selector;
    strcpy(reply, "Error: unsupported");
  }

  virtual void startRegionsLoad() {
    // no op by default
  }
  virtual bool saveRegions() {
    return false;
  }
  virtual void onDefaultRegionChanged(const RegionEntry* r) {
    // no op by default
  }

  virtual void setBridgeState(bool enable) {
    // no op by default
  };

  virtual void restartBridge() {
    // no op by default
  };

  virtual void restartBridgeSlot(int slot) {
    // Default: fall back to full restart
    restartBridge();
  };

  // Schedule a pull-OTA firmware update to run shortly (from the app loop), after
  // the "Beginning update..." CLI reply has been transmitted. Deferred because the
  // flash blocks the loop and then reboots, so it can't run inline with the reply.
  // Returns true if scheduled. Default: not supported.
  virtual bool beginDeferredOtaUpdate() {
    return false;
  };

  // Browser-based configuration portal. ESP32 infrastructure roles override
  // these; force_ap=true asks for the captive SoftAP even when WiFi is set.
  virtual bool startWebConfig(bool force_ap, char* reply) {
    (void)force_ap;
    (void)reply;
    return false;
  };
  virtual bool stopWebConfig(char* reply) {
    (void)reply;
    return false;
  };
  virtual bool isWebConfigActive() const {
    return false;
  };
  virtual bool setWebUIEnabled(bool enabled, char* reply) {
    (void)enabled;
    (void)reply;
    return false;
  };
  virtual bool getWebUIStatus(char* reply) const {
    (void)reply;
    return false;
  };
  virtual bool getWiFiSSID(char* reply) const {
    (void)reply;
    return false;
  };
  virtual bool getWiFiStatus(char* reply) const {
    (void)reply;
    return false;
  };
  virtual bool getWiFiPowerSave(char* reply) const {
    (void)reply;
    return false;
  };
  virtual bool getWiFiCLI(char* reply) const {
    (void)reply;
    return false;
  };
  virtual bool setWiFiSSID(const char* value, char* reply) {
    (void)value;
    (void)reply;
    return false;
  };
  virtual bool setWiFiPassword(const char* value, char* reply) {
    (void)value;
    (void)reply;
    return false;
  };
  virtual bool setWiFiPowerSave(const char* value, char* reply) {
    (void)value;
    (void)reply;
    return false;
  };
  virtual bool setWiFiCLI(const char* value, char* reply) {
    (void)value;
    (void)reply;
    return false;
  };

  virtual int getQueueSize() {
    return 0; // no op by default
  };

  virtual bool syncMqttNtp() {
    return false; // WITH_MQTT_BRIDGE builds override
  };

  virtual bool isMqttBridgeRunning() {
    return false;
  };

  // Probe all configured NTP servers for connectivity (verbose=serial console gets a
  // detailed table; otherwise reply gets a compact "<server> ok|fail" list).
  virtual bool runMqttNtpDiag(char* reply, size_t reply_size, bool verbose) {
    return false; // WITH_MQTT_BRIDGE builds override
  };

  virtual bool setRxBoostedGain(bool enable) {
    return false; // CommonCLI reports unsupported if not overridden by wrapper
  };

  #if defined(USE_LR2021)
  virtual bool configSideDetectors(const uint8_t sideDetSFs[], uint8_t num, float bw) {
    return false; // Override in wrapper
  }
  #endif

  virtual void recalibrateNoiseFloor() {
    // no op by default for radios without a noise-floor estimator
  };

  // Fault-alert channel hooks (see NodePrefs::alert_*). The default no-op
  // implementations keep CLI commands harmless on builds that don't wire up
  // an AlertReporter.
  virtual void onAlertConfigChanged() {
    // no op by default
  }
  virtual bool sendAlertText(const char* /*text*/) {
    return false; // no op by default
  }
  // Resolve the TransportKey scope to use for outgoing fault-alert floods.
  // Implementations should consult NodePrefs::alert_region first (look up via
  // RegionMap), then fall back to the repeater's default_scope, then return
  // false if neither yields a usable key. AlertReporter falls back to an
  // unscoped flood when this returns false.
  virtual bool resolveAlertScope(TransportKey& /*dest*/) {
    return false; // no op by default
  }

  virtual bool setRxPowerSaving(bool enable, uint32_t rx_us, uint32_t sleep_us) {
    return !enable;
  };

  virtual void getRxPsWatchdogCounts(uint32_t* soft, uint32_t* hard) {
    *soft = 0; *hard = 0;
  };
};

#ifdef WITH_MQTT_BRIDGE
namespace MQTTPrefsAtomicStore {
class LegacyUpgradeGate;
}
#endif

class CommonCLI {
  mesh::RTCClock* _rtc;
  NodePrefs* _prefs;
  CommonCLICallbacks* _callbacks;
  mesh::MainBoard* _board;
#if defined(ESP32_PLATFORM) || defined(USER_GPIO_CONTROL)
  UserGpio _user_gpio;
#endif
  SensorManager* _sensors;
  RegionMap* _region_map;
  ClientACL* _acl;
  char tmp[COMMON_CLI_TMP_LEN];
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
  bool _sdcard_format_recorded = false;
  bool _sdcard_erase_recorded = false;
  uint32_t _sdcard_format_at = 0;
  uint32_t _sdcard_erase_at = 0;
#endif
#ifdef WITH_MQTT_BRIDGE
  MQTTPrefs _mqtt_prefs;
  LegacyObserverTail _legacy_tail;
  // /mqtt_prefs is newer, corrupt, or temporarily unreadable. The in-memory prefs
  // run on defaults and saveMQTTPrefs() must not overwrite the source file.
  bool _mqtt_prefs_hold = false;
#endif
  bool _com_prefs_needs_upgrade = false;  // old-format /com_prefs detected; rewrite once after load

  mesh::RTCClock* getRTCClock() { return _rtc; }
  void savePrefs();
  void loadPrefsInt(FILESYSTEM* _fs, const char* filename);
#ifdef WITH_MQTT_BRIDGE
  bool recoverCommonPrefsFiles(FILESYSTEM* fs);
  bool saveCommonPrefsImageAtomically(FILESYSTEM* fs);
  void loadMQTTPrefs(FILESYSTEM* fs, MQTTPrefsAtomicStore::LegacyUpgradeGate* legacy_upgrade);
  bool saveMQTTPrefs(FILESYSTEM* fs);
#endif
#if defined(ENABLE_OTA)
  void syncOtaConfigFromPrefs();   // persisted OTA policy + signer allowlist -> running OtaContext
#endif

  void handleRegionCmd(char* command, char* reply);
  void handleGetCmd(uint32_t sender_timestamp, char* command, char* reply);
  void handleSetCmd(uint32_t sender_timestamp, char* command, char* reply);
  void handleDelCmd(char* command, char* reply);
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
  bool handleSdCardGetCmd(const char* config, char* reply);
  bool handleSdCardSetCmd(const char* config, char* reply);
#endif

  // Observer/MQTT/WiFi/timezone/alert/SNMP CLI handling lives in the fork-owned
  // CommonCLI_Observer.cpp to keep these branches out of the upstream-tracked
  // CommonCLI.cpp. Each returns true if it recognized (handled) the command, or
  // false to fall through to the base get/set parsing.
  bool handleObserverSetCmd(uint32_t sender_timestamp, const char* config, char* reply);
  bool handleObserverGetCmd(uint32_t sender_timestamp, const char* config, char* reply);
  // Observer-only top-level commands (ota check/update, tls.bundletest, alert test)
  // also live in CommonCLI_Observer.cpp; returns true if it handled the command.
  bool handleObserverCommand(uint32_t sender_timestamp, char* command, char* reply);

public:
  static bool calculateRxPowerSavingLevel(uint32_t level, uint8_t sf, float bw, uint32_t preamble,
                                          uint32_t* rx_us, uint32_t* sleep_us);
  static bool recalculateRxPowerSavingFromLevel(NodePrefs* prefs);

  CommonCLI(mesh::MainBoard& board, mesh::RTCClock& rtc, SensorManager& sensors, RegionMap& region_map, ClientACL& acl, NodePrefs* prefs, CommonCLICallbacks* callbacks)
      : _rtc(&rtc), _prefs(prefs), _callbacks(callbacks), _board(&board),
#if defined(ESP32_PLATFORM) || defined(USER_GPIO_CONTROL)
        _user_gpio(board),
#endif
        _sensors(&sensors), _region_map(&region_map), _acl(&acl) { }

  void loadPrefs(FILESYSTEM* _fs);
  void savePrefs(FILESYSTEM* _fs, bool save_mqtt = true);
  void loop();
  bool hasActiveUserGpioTimer() const;
  void handleCommand(uint32_t sender_timestamp, char* command, char* reply);
  mesh::MainBoard* getBoard() { return _board; }
  uint8_t buildAdvertData(uint8_t node_type, uint8_t* app_data);
#ifdef WITH_MQTT_BRIDGE
  // Observer config (MQTT/WiFi/timezone/SNMP/alert), persisted to /mqtt_prefs.
  // Exposed so the app can hand it to MQTTBridge/AlertReporter, which read these
  // fields directly (they no longer live in NodePrefs).
  MQTTPrefs* getObserverPrefs() const { return const_cast<MQTTPrefs*>(&_mqtt_prefs); }
#endif
};
