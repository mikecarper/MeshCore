#include "MyMesh.h"
#include <helpers/radiolib/RxBoostedGainDefaults.h>
#include <algorithm>
#include <stdlib.h>  // for qsort()
#include <helpers/CLICommandUtils.h>
#include <helpers/ClockSyncUtils.h>
#include <helpers/FloodFilterPolicy.h>
#include <helpers/RegionNameUtils.h>
#if defined(USE_LR2021)
#include <helpers/radiolib/LR2021SideDetectorConfig.h>
#endif
#include <helpers/radiolib/RXPowerSaving.h>
#include <helpers/RxReservePacketManager.h>
#ifdef WITH_WEBCONFIG
#include <WiFi.h>
#endif
#if MESH_ENABLE_TELEMETRY_HISTORY && defined(STM32_PLATFORM)
#include <sys/types.h>
extern "C" caddr_t _sbrk(int increment);
#endif
#if defined(WITH_MQTT_NEIGHBORS)
#include <helpers/MQTTConnectionPolicy.h>  // kSyncedClockEpoch
#endif

/* ------------------------------ Config -------------------------------- */

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

#ifndef DEFAULT_ADVERT_INTERVAL_MINUTES
  #define DEFAULT_ADVERT_INTERVAL_MINUTES 2
#endif
#ifndef DEFAULT_FLOOD_ADVERT_INTERVAL_HOURS
  #define DEFAULT_FLOOD_ADVERT_INTERVAL_HOURS 47
#endif
#ifndef DEFAULT_AGC_RESET_INTERVAL_SECONDS
  #define DEFAULT_AGC_RESET_INTERVAL_SECONDS 0
#endif
#ifndef DEFAULT_RX_DELAY_BASE
  #define DEFAULT_RX_DELAY_BASE 0.0f
#endif
#ifndef DEFAULT_MULTI_ACKS
  #define DEFAULT_MULTI_ACKS 0
#endif
#ifndef DEFAULT_PATH_HASH_MODE
  #define DEFAULT_PATH_HASH_MODE 0
#endif
#ifndef DEFAULT_LOOP_DETECT
  #define DEFAULT_LOOP_DETECT LOOP_DETECT_MINIMAL
#endif

#ifndef ADVERT_NAME
  #define ADVERT_NAME "repeater"
#endif
#ifndef ADVERT_LAT
  #define ADVERT_LAT 0.0
#endif
#ifndef ADVERT_LON
  #define ADVERT_LON 0.0
#endif

#ifndef ADMIN_PASSWORD
  #define ADMIN_PASSWORD "password"
#endif

#ifndef SERVER_RESPONSE_DELAY
  #define SERVER_RESPONSE_DELAY 300
#endif

#ifndef TXT_ACK_DELAY
  #define TXT_ACK_DELAY 200
#endif

#define FIRMWARE_VER_LEVEL       2

#define REQ_TYPE_GET_STATUS         0x01 // same as _GET_STATS
#define REQ_TYPE_KEEP_ALIVE         0x02
#define REQ_TYPE_GET_TELEMETRY_DATA 0x03
#define REQ_TYPE_GET_ACCESS_LIST    0x05
#define REQ_TYPE_GET_NEIGHBOURS     0x06
#define REQ_TYPE_GET_OWNER_INFO     0x07     // FIRMWARE_VER_LEVEL >= 2

#define RESP_SERVER_LOGIN_OK        0 // response to ANON_REQ

#define ANON_REQ_TYPE_REGIONS      0x01
#define ANON_REQ_TYPE_OWNER        0x02
#define ANON_REQ_TYPE_BASIC        0x03   // just remote clock

#define CLI_REPLY_DELAY_MILLIS      600

// Max time to flush the outbound queue (START alert + CLI reply) before the OTA
// teardown blocks the loop until reboot. Best-effort: exits early once the queue
// drains (immediate on a healthy node), caps the wait on a jammed/duty-limited
// channel so an update is never stalled indefinitely.
#define OTA_TX_DRAIN_TIMEOUT_MS     5000

#define LAZY_CONTACTS_WRITE_DELAY    5000

#define LEGACY_FLOOD_CHANNEL_BLOCK_FILE "/flood_ch_block"
#define LEGACY_FLOOD_CHANNEL_BLOCK_SLOTS 15
#define LEGACY_FLOOD_CHANNEL_BLOCK_NAME_LEN 32
#define LEGACY_FLOOD_CHANNEL_BLOCK_HOPS_INHERIT 0xFE
#define FLOOD_PACKET_FILTER_FILE      "/flood_filter"
#define FLOOD_PACKET_FILTER_TEMP_FILE "/flood_filter.tmp"
#define FLOOD_PACKET_FILTER_BACKUP_FILE "/flood_filter.bak"
#define FLOOD_PACKET_FILTER_BLACKLIST_FILE "/flood_filter_bl"
#define FLOOD_CHANNEL_SCOPE_FILE      "/flood_ch_scope"
#define FLOOD_CHANNEL_SCOPE_TEMP_FILE "/flood_ch_scope.tmp"
#define FLOOD_POLICY_SECTION_MAGIC    "FPS1"
#define FLOOD_CHANNEL_SCOPE_REQUIRE_FILE "/flood_ch_req"
#define FLOOD_CHANNEL_SCOPE_REQUIRE_TEMP_FILE "/flood_ch_req.tmp"
#define FLOOD_GROUP_MODERATION_FILE   "/flood_grp_mod"
#define CLOCK_SYNC_PREFS_FILE         "/clock_sync"
#define DEFAULT_WARDRIVING_CHANNEL       "#wardriving"
static const char FLOOD_PACKET_FILTER_USAGE[] =
#if MESH_ENABLE_FLOOD_RULE_ENGINE
    "Err - use: set flood.rule[.n] type=<type> [hops=<range>] [...]";
#else
    "Err - use: set flood.filter[.n] <type> [hops] [path=blacklist] [scope=<name>] [require=region] [tx=slow] [suspend=tempradio]";
#endif
static const char FLOOD_PACKET_FILTER_DUPLICATE[] =
    "Err - duplicate filter option";
static const char FLOOD_CHANNEL_SCOPE_USAGE[] =
#if defined(STM32_PLATFORM)
    "Err - bad scope matcher";
#else
    "Err - use: set flood.channel.scope[.n] <match> <region|scope=name> [...]";
#endif
#ifndef DEFAULT_WARDRIVING_MAX_HOPS
  #define DEFAULT_WARDRIVING_MAX_HOPS 4
#endif

#ifndef REPEATERS_CHANNEL_KEY_HEX
  #define REPEATERS_CHANNEL_KEY_HEX "89db441e2814dccf0dbd2e8cc5f501a3"
#endif
#ifndef BATT_MIN_MILLIVOLTS
  #define BATT_MIN_MILLIVOLTS 3000
#endif
#ifndef BATT_MAX_MILLIVOLTS
  #define BATT_MAX_MILLIVOLTS 4200
#endif

#define LOW_BATTERY_MIN_VALID_MV       1000
#define LOW_BATTERY_STARTUP_DELAY      (30ULL * 60ULL * 1000ULL)
#define LOW_BATTERY_CHECK_INTERVAL     (30UL * 60UL * 1000UL)
#define LOW_BATTERY_ALERT_INTERVAL     (12UL * 60UL * 60UL * 1000UL)
#define RX_INACTIVITY_WATCHDOG_INTERVAL (12UL * 60UL * 60UL * 1000UL)
#if MESH_ENABLE_TELEMETRY_HISTORY
#define TELEMETRY_GPS_HEAP_RESERVE_BYTES 2048U
#define TELEMETRY_HISTORY_TX_PREFS_FILE "/telemetry_tx"
static const uint64_t TELEMETRY_HISTORY_TX_RETRY_MILLIS =
    30ULL * 60ULL * 1000ULL;
static const uint8_t TELEMETRY_HISTORY_TX_DEFAULT_DAYS = 2U;
static const uint8_t TELEMETRY_HISTORY_TX_MAX_DAYS = 30U;
static const uint8_t TELEMETRY_HISTORY_TX_TEMPERATURE = 1U;
static const uint8_t TELEMETRY_HISTORY_TX_VOLTAGE = 2U;
#endif

#define CLOCK_SYNC_VALID_YEARS 10

#if MESH_ENABLE_TELEMETRY_HISTORY
static size_t telemetryFreeHeapBytes() {
#if defined(ESP_PLATFORM)
  return (size_t)ESP.getFreeHeap();
#elif defined(NRF52_PLATFORM)
  const int free_bytes = dbgHeapFree();
  return free_bytes > 0 ? (size_t)free_bytes : 0;
#elif defined(RP2040_PLATFORM)
  const int free_bytes = rp2040.getFreeHeap();
  return free_bytes > 0 ? (size_t)free_bytes : 0;
#elif defined(STM32_PLATFORM)
  uint8_t stack_marker;
  const caddr_t heap_end = _sbrk(0);
  if (heap_end == (caddr_t)-1) return 0;
  const uintptr_t stack_address = (uintptr_t)&stack_marker;
  const uintptr_t heap_address = (uintptr_t)heap_end;
  return stack_address > heap_address ? stack_address - heap_address : 0;
#else
  return 0;
#endif
}

static bool parseTelemetryGpsDays(const char* args, uint8_t& days) {
  while (*args == ' ') args++;
  if (*args < '0' || *args > '9') return false;

  unsigned parsed = 0;
  while (*args >= '0' && *args <= '9') {
    parsed = parsed * 10U + (unsigned)(*args++ - '0');
    if (parsed > mesh::TelemetryHistory::GPS_MAX_RETENTION_DAYS) return false;
  }
  while (*args == ' ') args++;
  if (*args != 0 || parsed < 1U) return false;
  days = (uint8_t)parsed;
  return true;
}
#endif

enum ClockSyncSource : uint8_t {
  CLOCK_SYNC_SOURCE_NONE = 0,
  CLOCK_SYNC_SOURCE_MESH = 1,
  CLOCK_SYNC_SOURCE_INTERNET = 2
};

enum ClockSyncResult : uint8_t {
  CLOCK_SYNC_RESULT_WAITING = 0,
  CLOCK_SYNC_RESULT_COLLECTING = 1,
  CLOCK_SYNC_RESULT_INTERNET_PENDING = 2,
  CLOCK_SYNC_RESULT_NO_CONSENSUS = 3,
  CLOCK_SYNC_RESULT_INTERNET_UNAVAILABLE = 4,
  CLOCK_SYNC_RESULT_WITHIN_DRIFT = 5,
  CLOCK_SYNC_RESULT_CORRECTED_FORWARD = 6,
  CLOCK_SYNC_RESULT_CORRECTED_BACKWARD = 7
};

static bool clockSyncLeapYear(uint16_t year) {
  return (year % 4U == 0 && year % 100U != 0) || year % 400U == 0;
}

static uint32_t clockSyncMinimumValidEpoch() {
#if FIRMWARE_BUILD_EPOCH > 0
  return (uint32_t)FIRMWARE_BUILD_EPOCH;
#else
  static uint32_t minimum = 0;
  if (minimum == 0) minimum = DateTime(__DATE__, __TIME__).unixtime();
  return minimum;
#endif
}

static uint32_t clockSyncMaximumValidEpoch() {
  static uint32_t maximum = 0;
  if (maximum == 0) {
    DateTime built(clockSyncMinimumValidEpoch());
    uint16_t upper_year = built.year() + CLOCK_SYNC_VALID_YEARS;
    uint8_t upper_day = built.day();
    if (built.month() == 2 && upper_day == 29 && !clockSyncLeapYear(upper_year)) upper_day = 28;
    maximum = DateTime(upper_year, built.month(), upper_day,
                       built.hour(), built.minute(), built.second()).unixtime();
  }
  return maximum;
}

static bool clockSyncEpochIsValid(uint32_t epoch) {
  return epoch >= clockSyncMinimumValidEpoch() && epoch <= clockSyncMaximumValidEpoch();
}

// Channel encryption uses a 128-bit key, while MACThenDecrypt's shared-secret
// buffer is PUB_KEY_SIZE bytes. Keep the unused half zero-padded like GroupChannel.
static const uint8_t FLOOD_PUBLIC_CHANNEL_SECRET[PUB_KEY_SIZE] = {
  0x8b, 0x33, 0x87, 0xe9, 0xc5, 0xcd, 0xea, 0x6a,
  0xc9, 0xe5, 0xed, 0xba, 0xa1, 0x15, 0xcd, 0x72
};

static uint32_t nextRadioApplyRetryDelay(uint8_t& failure_count) {
  uint8_t shift = failure_count < 5 ? failure_count : 5;
  if (failure_count < 6) failure_count++;
  uint32_t delay_ms = 1000UL << shift;
  return delay_ms > 30000UL ? 30000UL : delay_ms;
}

static const char* skipLocalSpaces(const char* text) {
  while (text != NULL && *text == ' ') text++;
  return text;
}

static bool selectorIsEmpty(const char* text) {
  text = skipLocalSpaces(text);
  return text == NULL || *text == 0;
}

static bool selectorIsAll(const char* text) {
  text = skipLocalSpaces(text);
  if (text == NULL || memcmp(text, "all", 3) != 0) {
    return false;
  }
  text += 3;
  while (*text == ' ') text++;
  return *text == 0;
}

static bool parsePositiveSelector(const char* text, int& value) {
  text = skipLocalSpaces(text);
  if (text == NULL || *text == 0) {
    return false;
  }

  uint32_t n = 0;
  bool saw_digit = false;
  while (*text >= '0' && *text <= '9') {
    saw_digit = true;
    n = (n * 10) + (uint32_t)(*text - '0');
    if (n > 32767) {
      return false;
    }
    text++;
  }
  while (*text == ' ') text++;
  if (!saw_digit || n == 0 || *text != 0) {
    return false;
  }
  value = (int)n;
  return true;
}

static bool bwMatches(float bw, float allowed) {
  float diff = bw - allowed;
  if (diff < 0.0f) diff = -diff;
  return diff <= 0.001f;
}

static bool isValidLoRaBandwidth(float bw) {
#if defined(USE_LR1110)
  return bwMatches(bw, 62.5f)
      || bwMatches(bw, 125.0f)
      || bwMatches(bw, 250.0f)
      || bwMatches(bw, 500.0f);
#elif defined(USE_LLCC68) || defined(USE_SX1272)
  return bwMatches(bw, 125.0f)
      || bwMatches(bw, 250.0f)
      || bwMatches(bw, 500.0f);
#else
  return bwMatches(bw, 7.8f)
      || bwMatches(bw, 10.4f)
      || bwMatches(bw, 15.6f)
      || bwMatches(bw, 20.8f)
      || bwMatches(bw, 31.25f)
      || bwMatches(bw, 41.7f)
      || bwMatches(bw, 62.5f)
      || bwMatches(bw, 125.0f)
      || bwMatches(bw, 250.0f)
      || bwMatches(bw, 500.0f);
#endif
}

static bool isValidScheduledRadioParams(float freq, float bw, uint8_t sf, uint8_t cr) {
  return freq >= 150.0f && freq <= 2500.0f
      && isValidLoRaBandwidth(bw)
      && sf >= 5 && sf <= 12
      && cr >= 5 && cr <= 8;
}

static bool buildRepeatersChannel(mesh::GroupChannel& channel) {
  const char* hex = REPEATERS_CHANNEL_KEY_HEX;
  size_t hex_len = strlen(hex);
  if (!(hex_len == 32 || hex_len == 64)) return false;
  for (size_t i = 0; i < hex_len; i++) {
    if (!mesh::Utils::isHexChar(hex[i])) return false;
  }

  memset(channel.secret, 0, sizeof(channel.secret));
  size_t key_len = hex_len / 2;
  if (!mesh::Utils::fromHex(channel.secret, key_len, hex)) return false;

  mesh::Utils::sha256(channel.hash, sizeof(channel.hash), channel.secret, key_len);
  return true;
}

static File openFloodSettingsRead(FILESYSTEM* fs, const char* filename) {
#if defined(RP2040_PLATFORM)
  return fs->open(filename, "r");
#else
  return fs->open(filename);
#endif
}

static File openFloodSettingsWrite(FILESYSTEM* fs, const char* filename) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  fs->remove(filename);
  return fs->open(filename, FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
  return fs->open(filename, "w");
#else
  return fs->open(filename, "w", true);
#endif
}

static uint32_t updateFloodSettingsHash(uint32_t hash,
                                        const uint8_t* data, size_t len) {
  while (len-- > 0) {
    hash ^= *data++;
    hash *= 16777619UL;
  }
  return hash;
}

static bool verifyFloodSettingsWrite(FILESYSTEM* fs, const char* filename,
                                     size_t expected_size,
                                     uint32_t expected_hash) {
  File file = openFloodSettingsRead(fs, filename);
  if (!file || file.size() != expected_size) {
    if (file) file.close();
    return false;
  }
  uint32_t hash = 2166136261UL;
  uint8_t buffer[64];
  size_t remaining = expected_size;
  while (remaining > 0) {
    size_t amount = remaining < sizeof(buffer) ? remaining : sizeof(buffer);
    if (file.read(buffer, amount) != amount) {
      file.close();
      return false;
    }
    hash = updateFloodSettingsHash(hash, buffer, amount);
    remaining -= amount;
  }
  file.close();
  return hash == expected_hash;
}

static uint8_t batteryPercentFromMilliVolts(uint16_t batt_mv) {
  const int min_mv = BATT_MIN_MILLIVOLTS;
  const int max_mv = BATT_MAX_MILLIVOLTS;
  if (max_mv <= min_mv) return 100;

  int pct = (((int)batt_mv - min_mv) * 100) / (max_mv - min_mv);
  if (pct < 0) return 0;
  if (pct > 100) return 100;
  return (uint8_t)pct;
}

static bool parseBatteryAlertPercent(const char* value, uint8_t min_value, uint8_t max_value, uint8_t& result) {
  if (value == NULL || *value == 0) {
    return false;
  }

  uint16_t parsed = 0;
  while (*value) {
    if (*value < '0' || *value > '9') {
      return false;
    }
    parsed = (uint16_t)(parsed * 10 + (*value - '0'));
    if (parsed > max_value) {
      return false;
    }
    value++;
  }
  if (parsed < min_value) {
    return false;
  }

  result = (uint8_t)parsed;
  return true;
}

static void formatFixed3(char* dest, size_t dest_len, float value) {
  long scaled = (long)(value * 1000.0f + (value >= 0.0f ? 0.5f : -0.5f));
  long whole = scaled / 1000;
  long decimals = scaled % 1000;
  if (decimals < 0) decimals = -decimals;
  snprintf(dest, dest_len, "%ld.%03ld", whole, decimals);
}

void MyMesh::putNeighbour(const mesh::Identity &id, uint32_t timestamp, float snr) {
#if MAX_NEIGHBOURS // check if neighbours enabled
  // find existing neighbour, else use least recently updated
  uint32_t oldest_timestamp = 0xFFFFFFFF;
  NeighbourInfo *neighbour = &neighbours[0];
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    // if neighbour already known, we should update it
    if (id.matches(neighbours[i].id)) {
      neighbour = &neighbours[i];
      break;
    }

    // otherwise we should update the least recently updated neighbour
    if (neighbours[i].heard_timestamp < oldest_timestamp) {
      neighbour = &neighbours[i];
      oldest_timestamp = neighbour->heard_timestamp;
    }
  }

  // update neighbour info
  neighbour->id = id;
  neighbour->advert_timestamp = timestamp;
  neighbour->heard_timestamp = getRTCClock()->getCurrentTime();
  neighbour->snr = (int8_t)(snr * 4);
#endif
}

uint8_t MyMesh::handleLoginReq(const mesh::Identity& sender, const uint8_t* secret, uint32_t sender_timestamp, const uint8_t* data, bool is_flood) {
  ClientInfo* client = NULL;
  if (strcmp((char *)data, _prefs.password) != 0) {  // admin pw bypasses ACL (allows upgrade)
    client = acl.getClient(sender.pub_key, PUB_KEY_SIZE);
    if (client == NULL) {
    #if MESH_DEBUG
      MESH_DEBUG_PRINTLN("Login, sender not in ACL");
    #endif
    }
  }
  if (client == NULL) {
    uint8_t perms;
    if (strcmp((char *)data, _prefs.password) == 0) { // check for valid admin password
      perms = PERM_ACL_ADMIN;
    } else if (strcmp((char *)data, _prefs.guest_password) == 0) { // check guest password
      perms = PERM_ACL_GUEST;
    } else {
#if MESH_DEBUG
      MESH_DEBUG_PRINTLN("Invalid password: %s", data);
#endif
      return 0;
    }

    client = acl.putClient(sender, 0);  // add to contacts (if not already known)
    if (client == NULL) {
      MESH_DEBUG_PRINTLN("Login rejected: ACL is full of protected contacts");
      return 0;
    }
    if (sender_timestamp <= client->last_timestamp) {
      MESH_DEBUG_PRINTLN("Possible login replay attack!");
      return 0;  // FATAL: client table is full -OR- replay attack
    }

    MESH_DEBUG_PRINTLN("Login success!");
    client->last_timestamp = sender_timestamp;
    client->last_activity = getRTCClock()->getCurrentTime();
    client->permissions &= ~PERM_ACL_ROLE_MASK;
    client->permissions |= perms;
    memcpy(client->shared_secret, secret, PUB_KEY_SIZE);

    if (perms != PERM_ACL_GUEST) {   // keep number of FS writes to a minimum
      dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
    }
  }

  if (is_flood) {
    client->out_path_len = OUT_PATH_UNKNOWN;  // need to rediscover out_path
  }

  uint32_t now = getRTCClock()->getCurrentTimeUnique();
  memcpy(reply_data, &now, 4);   // response packets always prefixed with timestamp
  reply_data[4] = RESP_SERVER_LOGIN_OK;
  reply_data[5] = 0;  // Legacy: was recommended keep-alive interval (secs / 16)
  reply_data[6] = (client->isAdmin() || client->isRegionMgr() || client->isFilterMgr()) ? 1 : 0;
  reply_data[7] = client->permissions;
  getRNG()->random(&reply_data[8], 4);   // random blob to help packet-hash uniqueness
  reply_data[12] = FIRMWARE_VER_LEVEL;  // New field

  return 13;  // reply length
}

// Comparison functions for qsort() - defined at file scope to avoid heap allocations
static int cmp_neighbours_newest_to_oldest(const void* a, const void* b) {
  const NeighbourInfo* na = *(const NeighbourInfo**)a;
  const NeighbourInfo* nb = *(const NeighbourInfo**)b;
  if (nb->heard_timestamp > na->heard_timestamp) return 1;
  if (nb->heard_timestamp < na->heard_timestamp) return -1;
  return 0;
}

static int cmp_neighbours_oldest_to_newest(const void* a, const void* b) {
  const NeighbourInfo* na = *(const NeighbourInfo**)a;
  const NeighbourInfo* nb = *(const NeighbourInfo**)b;
  if (na->heard_timestamp > nb->heard_timestamp) return 1;
  if (na->heard_timestamp < nb->heard_timestamp) return -1;
  return 0;
}

static int cmp_neighbours_strongest_to_weakest(const void* a, const void* b) {
  const NeighbourInfo* na = *(const NeighbourInfo**)a;
  const NeighbourInfo* nb = *(const NeighbourInfo**)b;
  if (nb->snr > na->snr) return 1;
  if (nb->snr < na->snr) return -1;
  return 0;
}

static int cmp_neighbours_weakest_to_strongest(const void* a, const void* b) {
  const NeighbourInfo* na = *(const NeighbourInfo**)a;
  const NeighbourInfo* nb = *(const NeighbourInfo**)b;
  if (na->snr > nb->snr) return 1;
  if (na->snr < nb->snr) return -1;
  return 0;
}

uint8_t MyMesh::handleAnonRegionsReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data) {
  if (anon_limiter.allow(rtc_clock.getCurrentTime())) {
    // request data has: {reply-path-len}{reply-path}
    reply_path_len = *data++;
    if (!mesh::Packet::isValidPathLen(reply_path_len)) return 0;  // reject - bad encoding

    mesh::Packet::writePath(reply_path, data, reply_path_len);
    // data += (uint8_t)reply_path_len * reply_path_hash_size;

    memcpy(reply_data, &sender_timestamp, 4);   // prefix with sender_timestamp, like a tag
    uint32_t now = getRTCClock()->getCurrentTime();
    memcpy(&reply_data[4], &now, 4);     // include our clock (for easy clock sync, and packet hash uniqueness)

    return 8 + region_map.exportNamesTo((char *) &reply_data[8], sizeof(reply_data) - 12, REGION_DENY_FLOOD);   // reply length
  }
  return 0;
}

uint8_t MyMesh::handleAnonOwnerReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data) {
  if (anon_limiter.allow(rtc_clock.getCurrentTime())) {
    // request data has: {reply-path-len}{reply-path}
    reply_path_len = *data++;
    if (!mesh::Packet::isValidPathLen(reply_path_len)) return 0;  // reject - bad encoding

    mesh::Packet::writePath(reply_path, data, reply_path_len);
    // data += (uint8_t)reply_path_len * reply_path_hash_size;

    memcpy(reply_data, &sender_timestamp, 4);   // prefix with sender_timestamp, like a tag
    uint32_t now = getRTCClock()->getCurrentTime();
    memcpy(&reply_data[4], &now, 4);     // include our clock (for easy clock sync, and packet hash uniqueness)
    sprintf((char *) &reply_data[8], "%s\n%s", _prefs.node_name, _prefs.owner_info);

    return 8 + strlen((char *) &reply_data[8]);   // reply length
  }
  return 0;
}

uint8_t MyMesh::handleAnonClockReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data) {
  if (anon_limiter.allow(rtc_clock.getCurrentTime())) {
    // request data has: {reply-path-len}{reply-path}
    reply_path_len = *data++;
    if (!mesh::Packet::isValidPathLen(reply_path_len)) return 0;  // reject - bad encoding

    mesh::Packet::writePath(reply_path, data, reply_path_len);
    // data += (uint8_t)reply_path_len * reply_path_hash_size;

    memcpy(reply_data, &sender_timestamp, 4);   // prefix with sender_timestamp, like a tag
    uint32_t now = getRTCClock()->getCurrentTime();
    memcpy(&reply_data[4], &now, 4);     // include our clock (for easy clock sync, and packet hash uniqueness)
    reply_data[8] = 0;  // features
#ifdef WITH_RS232_BRIDGE
    reply_data[8] |= 0x01;  // is bridge, type UART
#elif WITH_ESPNOW_BRIDGE
    reply_data[8] |= 0x03;  // is bridge, type ESP-NOW
#endif
    if (_prefs.disable_fwd) {   // is this repeater currently disabled
      reply_data[8] |= 0x80;  // is disabled
    }
    // TODO:  add some kind of moving-window utilisation metric, so can query 'how busy' is this repeater
    return 9;   // reply length
  }
  return 0;
}

int MyMesh::handleRequest(ClientInfo *sender, uint32_t sender_timestamp, uint8_t *payload, size_t payload_len) {
  // uint32_t now = getRTCClock()->getCurrentTimeUnique();
  // memcpy(reply_data, &now, 4);   // response packets always prefixed with timestamp
  memcpy(reply_data, &sender_timestamp, 4); // reflect sender_timestamp back in response packet (kind of like a 'tag')

  if (payload[0] == REQ_TYPE_GET_STATUS) {  // guests can also access this now
    RepeaterStats stats;
    stats.batt_milli_volts = board.getBattMilliVolts();
    stats.curr_tx_queue_len = _mgr->getOutboundCount(0xFFFFFFFF);
    stats.noise_floor = (int16_t)_radio->getNoiseFloor();
    stats.last_rssi = (int16_t)radio_driver.getLastRSSI();
    stats.n_packets_recv = radio_driver.getPacketsRecv();
    stats.n_packets_sent = radio_driver.getPacketsSent();
    stats.total_air_time_secs = getTotalAirTime() / 1000;
    stats.total_up_time_secs = uptime_millis / 1000;
    stats.n_sent_flood = getNumSentFlood();
    stats.n_sent_direct = getNumSentDirect();
    stats.n_recv_flood = getNumRecvFlood();
    stats.n_recv_direct = getNumRecvDirect();
    stats.err_events = _err_flags;
    stats.last_snr = (int16_t)(radio_driver.getLastSNR() * 4);
    stats.n_direct_dups = ((SimpleMeshTables *)getTables())->getNumDirectDups();
    stats.n_flood_dups = ((SimpleMeshTables *)getTables())->getNumFloodDups();
    stats.total_rx_air_time_secs = getReceiveAirTime() / 1000;
    stats.n_recv_errors = radio_driver.getPacketsRecvErrors();
    memcpy(&reply_data[4], &stats, sizeof(stats));

    return 4 + sizeof(stats); //  reply_len
  }
  if (payload[0] == REQ_TYPE_GET_TELEMETRY_DATA) {
    uint8_t perm_mask = ~(payload[1]); // NEW: first reserved byte (of 4), is now inverse mask to apply to permissions

    telemetry.reset();
    telemetry.addVoltage(TELEM_CHANNEL_SELF, (float)board.getBattMilliVolts() / 1000.0f);

    // query other sensors -- target specific
    if ((sender->permissions & PERM_ACL_ROLE_MASK) == PERM_ACL_GUEST) {
      perm_mask = 0x00;  // just base telemetry allowed
    }
    sensors.querySensors(perm_mask, telemetry);

	// This default temperature will be overridden by external sensors (if any)
    float temperature = board.getMCUTemperature();
    if(!isnan(temperature)) { // Supported boards with built-in temperature sensor. ESP32-C3 may return NAN
      telemetry.addTemperature(TELEM_CHANNEL_SELF, temperature); // Built-in MCU Temperature
    }

    uint8_t tlen = telemetry.getSize();
    memcpy(&reply_data[4], telemetry.getBuffer(), tlen);
    return 4 + tlen; // reply_len
  }
  if (payload[0] == REQ_TYPE_GET_ACCESS_LIST && sender->isAdmin()) {
    uint8_t res1 = payload[1];   // reserved for future  (extra query params)
    uint8_t res2 = payload[2];
    if (res1 == 0 && res2 == 0) {
      uint8_t ofs = 4;
      for (int i = 0; i < acl.getNumClients() && ofs + 7 <= sizeof(reply_data) - 4; i++) {
        auto c = acl.getClientByIdx(i);
        if (c->permissions == 0) continue;  // skip deleted entries
        memcpy(&reply_data[ofs], c->id.pub_key, 6); ofs += 6;  // just 6-byte pub_key prefix
        reply_data[ofs++] = c->permissions;
      }
      return ofs;
    }
  }
  if (payload[0] == REQ_TYPE_GET_NEIGHBOURS) {
    uint8_t request_version = payload[1];
    if (request_version == 0) {

      // reply data offset (after response sender_timestamp/tag)
      int reply_offset = 4;

      // get request params
      uint8_t count = payload[2]; // how many neighbours to fetch (0-255)
      uint16_t offset;
      memcpy(&offset, &payload[3], 2); // offset from start of neighbours list (0-65535)
      uint8_t order_by = payload[5]; // how to order neighbours. 0=newest_to_oldest, 1=oldest_to_newest, 2=strongest_to_weakest, 3=weakest_to_strongest
      uint8_t pubkey_prefix_length = payload[6]; // how many bytes of neighbour pub key we want
      // we also send a 4 byte random blob in payload[7...10] to help packet uniqueness

      MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS count=%d, offset=%d, order_by=%d, pubkey_prefix_length=%d", count, offset, order_by, pubkey_prefix_length);

      // clamp pub key prefix length to max pub key length
      if(pubkey_prefix_length > PUB_KEY_SIZE){
        pubkey_prefix_length = PUB_KEY_SIZE;
        MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS invalid pubkey_prefix_length=%d clamping to %d", pubkey_prefix_length, PUB_KEY_SIZE);
      }

      // Early exit if no neighbours to avoid unnecessary processing
      int16_t neighbours_count = 0;
#if MAX_NEIGHBOURS
      NeighbourInfo* sorted_neighbours[MAX_NEIGHBOURS];
#endif
      for (int i = 0; i < MAX_NEIGHBOURS; i++) {
        if (neighbours[i].heard_timestamp > 0) {
          neighbours_count++;
        }
      }
      
      if (neighbours_count == 0) {
        // No neighbours - return minimal response
        memcpy(&reply_data[reply_offset], &neighbours_count, 2); reply_offset += 2;
        uint16_t zero = 0;
        memcpy(&reply_data[reply_offset], &zero, 2); reply_offset += 2; // results_count = 0
        return reply_offset;
      }

      // create copy of neighbours list, skipping empty entries so we can sort it separately from main list
      int16_t sorted_idx = 0;
      for (int i = 0; i < MAX_NEIGHBOURS; i++) {
        auto neighbour = &neighbours[i];
        if (neighbour->heard_timestamp > 0) {
          sorted_neighbours[sorted_idx++] = neighbour;
        }
      }

      // Sort neighbours based on order using qsort() - standard C library function
      // qsort() doesn't allocate heap memory (uses stack-based recursion) and is O(n log n)
      // This matches the pattern used elsewhere in the codebase (e.g., BaseChatMesh)
      if (order_by == 0) {
        // sort by newest to oldest
        qsort(sorted_neighbours, neighbours_count, sizeof(NeighbourInfo*), cmp_neighbours_newest_to_oldest);
      } else if (order_by == 1) {
        // sort by oldest to newest
        qsort(sorted_neighbours, neighbours_count, sizeof(NeighbourInfo*), cmp_neighbours_oldest_to_newest);
      } else if (order_by == 2) {
        // sort by strongest to weakest
        qsort(sorted_neighbours, neighbours_count, sizeof(NeighbourInfo*), cmp_neighbours_strongest_to_weakest);
      } else if (order_by == 3) {
        // sort by weakest to strongest
        qsort(sorted_neighbours, neighbours_count, sizeof(NeighbourInfo*), cmp_neighbours_weakest_to_strongest);
      }

      // build results buffer
      int results_count = 0;
      int results_offset = 0;
      uint8_t results_buffer[130];
      for(int index = 0; index < count && index + offset < neighbours_count; index++){

        // stop if we can't fit another entry in results
        int entry_size = pubkey_prefix_length + 4 + 1;
        if(results_offset + entry_size > sizeof(results_buffer)){
          MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS no more entries can fit in results buffer");
          break;
        }

#if MAX_NEIGHBOURS
        // add next neighbour to results
        auto neighbour = sorted_neighbours[index + offset];
        uint32_t heard_seconds_ago = getRTCClock()->getCurrentTime() - neighbour->heard_timestamp;
        memcpy(&results_buffer[results_offset], neighbour->id.pub_key, pubkey_prefix_length); results_offset += pubkey_prefix_length;
        memcpy(&results_buffer[results_offset], &heard_seconds_ago, 4); results_offset += 4;
        memcpy(&results_buffer[results_offset], &neighbour->snr, 1); results_offset += 1;
        results_count++;
#endif

      }

      // build reply
      MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS neighbours_count=%d results_count=%d", neighbours_count, results_count);
      memcpy(&reply_data[reply_offset], &neighbours_count, 2); reply_offset += 2;
      memcpy(&reply_data[reply_offset], &results_count, 2); reply_offset += 2;
      memcpy(&reply_data[reply_offset], &results_buffer, results_offset); reply_offset += results_offset;

      return reply_offset;
    }
  } else if (payload[0] == REQ_TYPE_GET_OWNER_INFO) {
    sprintf((char *) &reply_data[4], "%s\n%s\n%s", FIRMWARE_VERSION, _prefs.node_name, _prefs.owner_info);
    return 4 + strlen((char *) &reply_data[4]);
  }
  return 0; // unknown command
}

mesh::Packet *MyMesh::createSelfAdvert() {
  uint8_t app_data[MAX_ADVERT_DATA_SIZE];
  uint8_t app_data_len = _cli.buildAdvertData(ADV_TYPE_REPEATER, app_data);

  return createAdvert(self_id, app_data, app_data_len);
}

File MyMesh::openAppend(const char *fname) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  return _fs->open(fname, FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
  return _fs->open(fname, "a");
#else
  return _fs->open(fname, "a", true);
#endif
}

static uint8_t max_loop_minimal[] =  { 0, /* 1-byte */  4, /* 2-byte */  2, /* 3-byte */  1 };
static uint8_t max_loop_moderate[] = { 0, /* 1-byte */  2, /* 2-byte */  1, /* 3-byte */  1 };
static uint8_t max_loop_strict[] =   { 0, /* 1-byte */  1, /* 2-byte */  1, /* 3-byte */  1 };

bool MyMesh::isLooped(const mesh::Packet* packet, const uint8_t max_counters[]) {
  uint8_t hash_size = packet->getPathHashSize();
  uint8_t hash_count = packet->getPathHashCount();
  uint8_t n = 0;
  const uint8_t* path = packet->path;
  while (hash_count > 0) {      // count how many times this node is already in the path
    if (self_id.isHashMatch(path, hash_size)) n++;
    hash_count--;
    path += hash_size;
  }
  return n >= max_counters[hash_size];
}

void MyMesh::sendFloodReply(mesh::Packet* packet, unsigned long delay_millis, uint8_t path_hash_size) {
  TransportKey req_scope;
  bool is_wildcard = recv_pkt_region != NULL && recv_pkt_region->isWildcard();
  bool req_scope_known = recv_pkt_region != NULL && !is_wildcard
                      && region_map.getTransportKeysFor(*recv_pkt_region, &req_scope, 1) > 0;

  switch (mesh::chooseReplyScope(req_scope_known, is_wildcard, !default_scope.isNull())) {
    case mesh::REPLY_SCOPE_REQUEST:
      sendFloodScoped(req_scope, packet, delay_millis, path_hash_size);   // reply with same scope as request
      break;
    case mesh::REPLY_SCOPE_DEFAULT:
      // requester's scope is unknown: DIRECT request (no transport codes), or code matched no Region.
      // un-scoped would be dropped at hop 0 by repeaters running flood.max.unscoped=0
      sendFloodScoped(default_scope, packet, delay_millis, path_hash_size);
      break;
    case mesh::REPLY_SCOPE_NONE:
      sendFlood(packet, delay_millis, path_hash_size);  // send un-scoped
      break;
  }
}

static bool directPathsEqual(const uint8_t* a_path, uint8_t a_len, const uint8_t* b_path, uint8_t b_len) {
  if (!mesh::Packet::isValidPathLen(a_len) || !mesh::Packet::isValidPathLen(b_len) || a_len != b_len) {
    return false;
  }
  uint8_t hash_count = a_len & 63;
  uint8_t hash_size = (a_len >> 6) + 1;
  uint8_t byte_len = hash_count * hash_size;
  return byte_len == 0 || memcmp(a_path, b_path, byte_len) == 0;
}

void MyMesh::sendClientReply(ClientInfo* client, mesh::Packet* packet, unsigned long delay_millis, uint8_t path_hash_size) {
  TransportKey fallback_scope;
  const TransportKey* fallback_scope_ptr = NULL;
  if (recv_pkt_region != NULL && !recv_pkt_region->isWildcard()
      && region_map.getTransportKeysFor(*recv_pkt_region, &fallback_scope, 1) > 0) {
    fallback_scope_ptr = &fallback_scope;
  }
  sendClientReplyWithFallbackScope(client, packet, delay_millis, path_hash_size,
                                   fallback_scope_ptr);
}

void MyMesh::sendClientReplyWithFallbackScope(ClientInfo* client, mesh::Packet* packet,
                                              unsigned long delay_millis, uint8_t path_hash_size,
                                              const TransportKey* fallback_scope) {
  if (packet == NULL) {
    return;
  }
  if (client == NULL || !mesh::Packet::isValidPathLen(client->out_path_len)) {
    if (fallback_scope != NULL) {
      sendFloodScoped(*fallback_scope, packet, delay_millis, path_hash_size);
    } else {
      sendFlood(packet, delay_millis, path_hash_size);
    }
    return;
  }

  mesh::Packet* alt = NULL;
  if (mesh::Packet::isValidPathLen(client->alt_path_len)
      && !directPathsEqual(client->out_path, client->out_path_len, client->alt_path, client->alt_path_len)) {
    alt = obtainNewPacket();
    if (alt != NULL) {
      *alt = *packet;
    } else {
      MESH_DEBUG_PRINTLN("sendClientReply: altpath packet pool empty");
    }
  }

  sendDirect(packet, client->out_path, client->out_path_len, delay_millis);
  if (alt != NULL) {
    uint8_t direct_retry_enabled = _prefs.direct_retry_enabled;
    _prefs.direct_retry_enabled = 0;
    sendDirect(alt, client->alt_path, client->alt_path_len, delay_millis);
    _prefs.direct_retry_enabled = direct_retry_enabled;
  }
}

bool MyMesh::floodChannelDataHopApplies(const mesh::Packet* packet) const {
  if (packet == NULL) {
    return false;
  }
  uint8_t max_hops = _prefs.flood_channel_data_max_hops;
  return max_hops == FLOOD_CHANNEL_HOPS_ALL || packet->getPathHashCount() > max_hops;
}

bool MyMesh::allowPacketForward(const mesh::Packet *packet) {
  if (_prefs.disable_fwd) return false;
  if (packet->isRouteFlood()) {
    if (mesh::isFloodHopLimitExceeded(packet, _prefs.flood_max,
                                      _prefs.flood_max_unscoped,
                                      _prefs.flood_max_advert)) {
      return false;
    }
#if !defined(PORTABLE_MQTT_OBSERVER) && !MESH_ENABLE_FLOOD_RULE_ENGINE
    if (!_prefs.flood_channel_data_enabled
        && packet->getPayloadType() == PAYLOAD_TYPE_GRP_DATA
        && floodChannelDataHopApplies(packet)) {
      MESH_DEBUG_PRINTLN("allowPacketForward: flood.channel.data off, blocking GRP_DATA hops=%d",
                         packet->getPathHashCount());
      return false;
    }
#endif
#if !defined(PORTABLE_MQTT_OBSERVER)
    if (shouldBlockFloodPacketForward(packet)) return false;
#endif
  }
  if (packet->isRouteFlood() && recv_pkt_channel_scope_rejected) {
    MESH_DEBUG_PRINTLN(
        "allowPacketForward: flood.channel.scope.require rejected incoming scope");
    return false;
  }
  if (packet->isRouteFlood() && recv_pkt_region == NULL
      && !recv_pkt_regionless_scope_set
      && !recv_pkt_channel_scope_bypass) {
    MESH_DEBUG_PRINTLN("allowPacketForward: unknown transport code, or wildcard not allowed for FLOOD packet");
    return false;
  }
  if (packet->isRouteFlood() && _prefs.loop_detect != LOOP_DETECT_OFF) {
    const uint8_t* maximums;
    if (_prefs.loop_detect == LOOP_DETECT_MINIMAL) {
      maximums = max_loop_minimal;
    } else if (_prefs.loop_detect == LOOP_DETECT_MODERATE) {
      maximums = max_loop_moderate;
    } else {
      maximums = max_loop_strict;
    }
    if (isLooped(packet, maximums)) {
      MESH_DEBUG_PRINTLN("allowPacketForward: FLOOD packet loop detected!");
      return false;
    }
  }
  // Moderation has rate-counter side effects, so evaluate it only after every
  // other forwarding gate has accepted the packet. Quota is then spent only
  // for a message this repeater will actually retransmit.
#if !defined(PORTABLE_MQTT_OBSERVER)
#if MESH_ENABLE_FLOOD_GROUP_MODERATION
  if (packet->isRouteFlood() && shouldBlockFloodGroupTextForward(packet)) return false;
#endif
  if (packet->isRouteFlood()) commitFloodPacketFilterRates(packet);
  // Normal path mode accepts clock evidence only from packets this node would
  // forward. Edge mode observes verified evidence on the receive path instead,
  // so repeat off and other forwarding filters do not hide a single upstream
  // path from a node at the edge of the network.
#if !defined(PORTABLE_MQTT_OBSERVER) && MESH_ENABLE_CLOCK_SYNC
  if (!clock_sync_mesh_edge_enabled) recordAcceptedFloodClockSample(packet);
#endif
#endif
  return true;
}

const char *MyMesh::getLogDateTime() {
  static char tmp[32];
  uint32_t now = getRTCClock()->getCurrentTime();
  DateTime dt = DateTime(now);
  sprintf(tmp, "%02d:%02d:%02d - %d/%d/%d U", dt.hour(), dt.minute(), dt.second(), dt.day(), dt.month(),
          dt.year());
  return tmp;
}

void MyMesh::logRxRaw(float snr, float rssi, const uint8_t raw[], int len) {
#if MESH_PACKET_LOGGING
  if (mesh::isUsbLoggingEnabled()) {
    // Logging builds prefer backpressure over silently losing a packet record.
    Serial.print(getLogDateTime());
    Serial.print(" RAW: ");
    mesh::Utils::printHex(Serial, raw, len);
    Serial.println();
  }
#endif

#ifdef WITH_MQTT_BRIDGE
  if (_prefs.bridge_enabled) {
    // Store raw radio data for MQTT messages
    if (mqtt_bridge) mqtt_bridge->storeRawRadioData(raw, len, snr, rssi);
  }
#endif
}

void MyMesh::logRx(mesh::Packet *pkt, int len, float score) {
#ifdef WITH_MQTT_BRIDGE
  // MQTT bridge: always feed RX packets - bridge decides based on mqtt.rx setting
  if (mqtt_bridge) mqtt_bridge->onPacketReceived(pkt);
#elif defined(WITH_BRIDGE)
  // Non-MQTT bridge: use bridge.source setting
  if (_prefs.bridge_pkt_src == 1) {
    activeBridge()->sendPacket(pkt);
  }
#endif

  if (_logging) {
    File f = openAppend(PACKET_LOG_FILE);
    if (f) {
      f.print(getLogDateTime());
      f.printf(": RX, len=%d (type=%d, route=%s, payload_len=%d) SNR=%d RSSI=%d score=%d", len,
               pkt->getPayloadType(), pkt->isRouteDirect() ? "D" : "F", pkt->payload_len,
               (int)_radio->getLastSNR(), (int)_radio->getLastRSSI(), (int)(score * 1000));

      if (pkt->getPayloadType() == PAYLOAD_TYPE_PATH || pkt->getPayloadType() == PAYLOAD_TYPE_REQ ||
          pkt->getPayloadType() == PAYLOAD_TYPE_RESPONSE || pkt->getPayloadType() == PAYLOAD_TYPE_TXT_MSG) {
        f.printf(" [%02X -> %02X]\n", (uint32_t)pkt->payload[1], (uint32_t)pkt->payload[0]);
      } else {
        f.printf("\n");
      }
      f.close();
    }
  }
}

void MyMesh::logTx(mesh::Packet *pkt, int len) {
#if defined(WITH_MQTT_NEIGHBORS)
  if (neighbor_discover_active && pkt == neighbor_discover_request
      && neighbor_discover_next < neighbor_discover_count) {
    NeighborDiscoverEntry& entry = neighbor_discover[neighbor_discover_next];
    if (entry.status == ND_QUEUED) {
      entry.status = ND_PENDING;
      neighbor_discover_queried_count++;
      neighbor_discover_request = NULL;
      neighbor_discover_until = futureMillis(neighborDiscoverQueryTimeoutMs());
    }
  }
#endif

#ifdef WITH_MQTT_BRIDGE
  // MQTT bridge: always feed TX packets - bridge decides based on mqtt.tx setting
  if (mqtt_bridge) mqtt_bridge->sendPacket(pkt);
#elif defined(WITH_BRIDGE)
  // Non-MQTT bridge: use bridge.source setting
  if (_prefs.bridge_pkt_src == 0) {
    activeBridge()->sendPacket(pkt);
  }
#endif

  if (_logging) {
    File f = openAppend(PACKET_LOG_FILE);
    if (f) {
      f.print(getLogDateTime());
      f.printf(": TX, len=%d (type=%d, route=%s, payload_len=%d)", len, pkt->getPayloadType(),
               pkt->isRouteDirect() ? "D" : "F", pkt->payload_len);

      if (pkt->getPayloadType() == PAYLOAD_TYPE_PATH || pkt->getPayloadType() == PAYLOAD_TYPE_REQ ||
          pkt->getPayloadType() == PAYLOAD_TYPE_RESPONSE || pkt->getPayloadType() == PAYLOAD_TYPE_TXT_MSG) {
        f.printf(" [%02X -> %02X]\n", (uint32_t)pkt->payload[1], (uint32_t)pkt->payload[0]);
      } else {
        f.printf("\n");
      }
      f.close();
    }
  }
}

void MyMesh::logTxFail(mesh::Packet *pkt, int len) {
#if defined(WITH_MQTT_NEIGHBORS)
  if (neighbor_discover_active && pkt == neighbor_discover_request
      && neighbor_discover_next < neighbor_discover_count) {
    NeighborDiscoverEntry& entry = neighbor_discover[neighbor_discover_next];
    if (entry.status == ND_QUEUED) {
      entry.status = ND_SEND_FAILED;
      neighbor_discover_request = NULL;
      neighbor_discover_until = 0;
    }
  }
#endif

  if (_logging) {
    File f = openAppend(PACKET_LOG_FILE);
    if (f) {
      f.print(getLogDateTime());
      f.printf(": TX FAIL!, len=%d (type=%d, route=%s, payload_len=%d)\n", len, pkt->getPayloadType(),
               pkt->isRouteDirect() ? "D" : "F", pkt->payload_len);
      f.close();
    }
  }
}

int MyMesh::calcRxDelay(float score, uint32_t air_time) const {
  if (_prefs.rx_delay_base <= 0.0f) return 0;
  return (int)((powf(_prefs.rx_delay_base, 0.85f - score) - 1.0f) * air_time);
}

bool MyMesh::evaluateScopeRewriteTiming(const mesh::Packet* packet,
                                        bool& fast_track) {
  fast_track = false;
#if defined(PORTABLE_MQTT_OBSERVER)
  (void)packet;
  return false;
#else
  if (packet == NULL || !packet->isRouteFlood()) return false;

  bool incoming_region_allowed = false;
  RegionEntry* incoming_region = NULL;
  if (packet->getRouteType() == ROUTE_TYPE_TRANSPORT_FLOOD) {
    incoming_region = region_map.findMatch(packet, REGION_DENY_FLOOD);
    incoming_region_allowed = incoming_region != NULL;
  } else if (packet->getRouteType() == ROUTE_TYPE_FLOOD) {
    incoming_region_allowed =
        (region_map.getWildcard().flags & REGION_DENY_FLOOD) == 0;
  }

  bool requirement_table_active = false;
  bool channel_requires_scope = findFloodChannelScopeRequirementMatch(
      packet, requirement_table_active);
  bool is_group_channel_packet =
      packet->getPayloadType() == PAYLOAD_TYPE_GRP_TXT
      || packet->getPayloadType() == PAYLOAD_TYPE_GRP_DATA;
  FloodFilterPolicy::ChannelScopeGate channel_scope_gate =
      FloodFilterPolicy::channelScopeGate(
          requirement_table_active, is_group_channel_packet,
          channel_requires_scope,
          packet->getRouteType() == ROUTE_TYPE_TRANSPORT_FLOOD,
          incoming_region_allowed);
  if (channel_scope_gate
      == FloodFilterPolicy::CHANNEL_SCOPE_REQUIRED_REJECTED) {
    return false;
  }

  mesh::Packet candidate = *packet;
  uint64_t filter_match_mask = evaluateFloodPacketFilterMatches(
      packet, incoming_region_allowed, incoming_region);
  bool channel_regionless_scope_set = false;
  bool scope_changed = applyFloodChannelScope(
      &candidate, fast_track, channel_regionless_scope_set, false);
  bool filter_scope_set = false;
  bool filter_fast_track = false;
  if (applyFloodPacketFilterScope(&candidate, filter_match_mask,
                                  filter_scope_set, filter_fast_track, false)) {
    scope_changed = true;
    fast_track = filter_fast_track;
  }
  return scope_changed;
#endif
}

bool MyMesh::shouldBypassRxDelay(const mesh::Packet* packet) {
  if (packet != NULL && packet->getPayloadType() == PAYLOAD_TYPE_OTA
      && isTempRadioActive()) return true;
  bool fast_track = false;
  return evaluateScopeRewriteTiming(packet, fast_track) && fast_track;
}

int MyMesh::calcRxDelayForPacket(const mesh::Packet* packet, float score,
                                 uint32_t air_time) {
  if (packet != NULL && packet->getPayloadType() == PAYLOAD_TYPE_OTA
      && isTempRadioActive()) return 0;
  bool fast_track = false;
  if (!evaluateScopeRewriteTiming(packet, fast_track)) {
    return calcRxDelay(score, air_time);
  }
  if (fast_track) return 0;

  float slow_base =
      FloodFilterPolicy::slowScopeRxDelayBase(_prefs.rx_delay_base);
  return (int)((powf(slow_base, 0.85f - score) - 1.0f) * air_time);
}

uint32_t MyMesh::getRetransmitDelay(const mesh::Packet *packet) {
  uint32_t t = (_radio->getEstAirtimeFor(packet->getPathByteLen() + packet->payload_len + 2) * _prefs.tx_delay_factor);
  return getRNG()->nextInt(0, 5*t + 1);
}
uint32_t MyMesh::getSlowScopeRetransmitDelay(const mesh::Packet* packet) {
  uint32_t airtime =
      _radio->getEstAirtimeFor(packet->getPathByteLen() + packet->payload_len + 2);
  uint32_t max_delay = FloodFilterPolicy::slowScopeMaxDelay(airtime);
  return getRNG()->nextInt(0, max_delay + 1);
}
uint32_t MyMesh::getDirectRetransmitDelay(const mesh::Packet *packet) {
  uint32_t t = (_radio->getEstAirtimeFor(packet->getPathByteLen() + packet->payload_len + 2) * _prefs.direct_tx_delay_factor);
  return getRNG()->nextInt(0, 5*t + 1);
}

void MyMesh::onRetryConfigChanged() {
  if (!_prefs.direct_retry_enabled) cancelAllDirectRetries();
  if (_prefs.disable_fwd || _prefs.flood_retry_attempts == 0) cancelAllFloodRetries();
}

bool MyMesh::extractDirectRetryPrefix(const mesh::Packet* packet, uint8_t* prefix, uint8_t& prefix_len) const {
  if (packet == NULL || !packet->isRouteDirect() || packet->getPathHashCount() == 0) {
    return false;
  }
  prefix_len = packet->getPathHashSize();
  memcpy(prefix, packet->path, prefix_len);
  return true;
}

static bool isDirectShortcutPayload(const mesh::Packet* packet) {
  if (packet == NULL || !packet->isRouteDirect()) {
    return false;
  }

  switch (packet->getPayloadType()) {
    case PAYLOAD_TYPE_PATH:
    case PAYLOAD_TYPE_REQ:
    case PAYLOAD_TYPE_RESPONSE:
    case PAYLOAD_TYPE_TXT_MSG:
    case PAYLOAD_TYPE_ANON_REQ:
      return true;
    default:
      return false;
  }
}

bool MyMesh::maybeShortCircuitDirect(mesh::Packet* packet) {
  if (!isDirectShortcutPayload(packet)) {
    return false;
  }

  uint8_t hash_size = packet->getPathHashSize();
  uint8_t hash_count = packet->getPathHashCount();
  if (hash_size == 0 || hash_size > MAX_ROUTE_HASH_BYTES || hash_count < 3) {
    return false;
  }

  int self_idx = -1;
  for (uint8_t i = 1; i + 1 < hash_count; i++) {
    if (self_id.isHashMatch(&packet->path[i * hash_size], hash_size)) {
      self_idx = i;
      break;
    }
  }
  if (self_idx < 1) {
    return false;
  }

  const SimpleMeshTables* tables = static_cast<const SimpleMeshTables*>(getTables());
  if (tables == NULL) {
    return false;
  }

  const uint8_t* previous_hop = &packet->path[(self_idx - 1) * hash_size];
  const uint8_t* next_hop = &packet->path[(self_idx + 1) * hash_size];
  if (tables->findRecentRepeaterByHash(previous_hop, hash_size) == NULL
      || tables->findRecentRepeaterByHash(next_hop, hash_size) == NULL) {
    return false;
  }

  uint8_t remaining_count = hash_count - (uint8_t)self_idx;
  memmove(packet->path, &packet->path[self_idx * hash_size], remaining_count * hash_size);
  packet->setPathHashCount(remaining_count);
  MESH_DEBUG_PRINTLN("direct shortcut: skipped %u planned hop(s), remaining=%u",
                     (uint32_t)self_idx,
                     (uint32_t)remaining_count);
  return true;
}

int8_t MyMesh::getDirectRetryMinSNRX4() const {
  switch (active_sf) {
    case 7: return -30;
    case 8: return -40;
    case 9: return -50;
    case 10: return -60;
    case 11: return -70;
    case 12: return -80;
    default: return -60;
  }
}

uint8_t MyMesh::getDirectRetryCodingRateForSNR(int8_t snr_x4) const {
  if (!_prefs.direct_retry_cr_enabled) return 0;
  if (snr_x4 >= _prefs.direct_retry_cr4_snr_x4) return 4;
  if (snr_x4 >= _prefs.direct_retry_cr5_snr_x4) return 5;
  if (snr_x4 <= _prefs.direct_retry_cr8_snr_x4) return 8;
  if (snr_x4 >= _prefs.direct_retry_cr7_snr_x4) return 7;
  return 7;
}

uint8_t MyMesh::getDirectRetryConfiguredMaxAttempts() const {
  return constrain(_prefs.direct_retry_attempts, 1, 15);
}

uint32_t MyMesh::getDirectRetryAttemptStepMillis() const {
  return _prefs.direct_retry_step_ms;
}

bool MyMesh::allowDirectRetry(const mesh::Packet* packet, const uint8_t* next_hop_hash, uint8_t next_hop_hash_len) const {
  (void)packet;
  if (!_prefs.direct_retry_enabled) {
    return false;
  }
  if (!_prefs.direct_retry_recent_enabled) {
    return true;
  }
  if (next_hop_hash == NULL || next_hop_hash_len == 0) {
    return true;
  }
  const SimpleMeshTables* tables = static_cast<const SimpleMeshTables*>(getTables());
  const SimpleMeshTables::RecentRepeaterInfo* repeater = tables != NULL
      ? tables->findRecentRepeaterByHash(next_hop_hash, next_hop_hash_len)
      : NULL;

  if (repeater == NULL) {
    // Retry unknown repeaters too. If they fail, onDirectRetryFailed() seeds the
    // recent-repeater table below the +3.00 dB starting point.
    return true;
  }
  int16_t retry_floor_x4 = (int16_t)getDirectRetryMinSNRX4() + (int16_t)_prefs.direct_retry_snr_margin_x4;
  return (int16_t)repeater->snr_x4 >= retry_floor_x4;
}

void MyMesh::configureDirectRetryPacket(mesh::Packet* retry, const mesh::Packet* original, uint8_t retry_attempt) {
  int8_t snr_x4 = 12;  // unknown repeaters start at +3.00 dB
  const SimpleMeshTables* tables = static_cast<const SimpleMeshTables*>(getTables());
  if (tables != NULL) {
    uint8_t prefix[MAX_HASH_SIZE];
    uint8_t prefix_len = 0;
    if (extractDirectRetryPrefix(original, prefix, prefix_len)) {
      const SimpleMeshTables::RecentRepeaterInfo* repeater = tables->findRecentRepeaterByHash(prefix, prefix_len);
      if (repeater != NULL) {
        snr_x4 = repeater->snr_x4;
      }
    }
  }

  retry->tx_cr = getDirectRetryCodingRateForAttempt(getDirectRetryCodingRateForSNR(snr_x4), retry_attempt);
}

uint32_t MyMesh::getDirectRetryEchoDelay(const mesh::Packet* packet) const {
  uint32_t base_wait_millis = constrain((uint32_t)_prefs.direct_retry_base_ms, (uint32_t)10, (uint32_t)5000);
  if (packet == NULL) {
    return base_wait_millis;
  }

  // Use the driver's LoRa airtime calculation for the echo window.
  return base_wait_millis + getDirectRetryPacketAirtimeDelay(packet);
}

static uint8_t decodeDirectRetryTraceHashSize(uint8_t flags, uint8_t route_bytes) {
  uint8_t code = flags & 0x03;
  uint8_t size_pow2 = (uint8_t)(1U << code);
  uint8_t size_linear = (uint8_t)(code + 1U);

  bool pow2_ok = size_pow2 > 0 && (route_bytes % size_pow2) == 0;
  bool linear_ok = size_linear > 0 && (route_bytes % size_linear) == 0;

  if (pow2_ok && !linear_ok) return size_pow2;
  if (linear_ok && !pow2_ok) return size_linear;
  if (pow2_ok) return size_pow2;
  return size_linear;
}

uint8_t MyMesh::getDirectRetryMaxAttempts(const mesh::Packet* packet) const {
  if (packet != NULL && packet->getPayloadType() == PAYLOAD_TYPE_TXT_MSG) {
    return 21;
  }

  uint8_t configured_attempts = getDirectRetryConfiguredMaxAttempts();
  uint8_t total_hops = 0;

  if (packet != NULL) {
    if (packet->isRouteDirect() && packet->getPayloadType() == PAYLOAD_TYPE_TRACE && packet->payload_len >= 9) {
      uint8_t route_bytes = packet->payload_len - 9;
      uint8_t hash_size = decodeDirectRetryTraceHashSize(packet->payload[8], route_bytes);
      if (hash_size > 0) {
        total_hops = (uint8_t)(route_bytes / hash_size);
      }
    } else {
      total_hops = packet->getPathHashCount();
    }
  }

  uint8_t path_cap = 15;
  if (total_hops <= 3) {
    path_cap = 8;
  } else if (total_hops == 4) {
    path_cap = 12;
  }

  return configured_attempts < path_cap ? configured_attempts : path_cap;
}

uint32_t MyMesh::getDirectRetryAttemptDelay(const mesh::Packet* packet, uint8_t attempt_idx) {
  uint32_t retry_delay = getDirectRetryEchoDelay(packet) + ((uint32_t)attempt_idx * getDirectRetryAttemptStepMillis());
  if (packet == NULL) {
    return retry_delay;
  }
  return getDirectRetransmitDelay(packet) + retry_delay;
}

static void formatDirectRetryTarget(char* dest, size_t dest_len, const uint8_t* target_hash, uint8_t target_hash_len) {
  if (dest == NULL || dest_len == 0) {
    return;
  }
  if (target_hash == NULL || target_hash_len == 0 || target_hash_len > MAX_HASH_SIZE) {
    StrHelper::strncpy(dest, "-", dest_len);
    return;
  }

  size_t hex_len = (size_t)target_hash_len * 2;
  if (dest_len <= hex_len) {
    StrHelper::strncpy(dest, "-", dest_len);
    return;
  }

  mesh::Utils::toHex(dest, target_hash, target_hash_len);
  dest[hex_len] = 0;
}

static uint8_t getRetryLogCodingRate(const mesh::Packet* packet, uint8_t default_cr) {
  if (packet != NULL && packet->tx_cr >= 4 && packet->tx_cr <= 8) {
    return packet->tx_cr;
  }
  return default_cr;
}

static uint16_t getRetryLogPreambleLength(const mesh::Packet* packet, uint16_t default_preamble_len) {
  if (packet != NULL && packet->tx_cr >= 4 && packet->tx_cr <= 8) {
    return 32;
  }
  return default_preamble_len;
}

void MyMesh::onDirectRetryEvent(const char* event, const mesh::Packet* packet, uint32_t delay_millis, uint8_t retry_attempt,
                                const uint8_t* target_hash, uint8_t target_hash_len, int16_t payload_type) {
#if defined(PORTABLE_MQTT_OBSERVER)
  (void)event; (void)packet; (void)delay_millis; (void)retry_attempt;
  (void)target_hash; (void)target_hash_len; (void)payload_type;
  return;
#endif
  char type_label[8];
  char target_label[(MAX_HASH_SIZE * 2) + 1];
  const char* route_label = packet != NULL ? (packet->isRouteDirect() ? "D" : "F") : "D";
  if (packet != NULL) {
    snprintf(type_label, sizeof(type_label), "%u", (uint32_t)packet->getPayloadType());
  } else if (payload_type >= 0) {
    snprintf(type_label, sizeof(type_label), "%u", (uint32_t)payload_type);
  } else {
    strcpy(type_label, "?");
  }
  formatDirectRetryTarget(target_label, sizeof(target_label), target_hash, target_hash_len);
  uint8_t log_cr = getRetryLogCodingRate(packet, getDefaultTxCodingRate());
  uint16_t log_preamble_len = getRetryLogPreambleLength(packet, radio_driver.getDefaultPreambleLength());

#if MESH_DEBUG
  MESH_DEBUG_PRINTLN("direct retry %s attempt=%u delay=%lu type=%s route=%s target=%s cr=%u preamble_len=%u",
                     event ? event : "?",
                     (uint32_t)retry_attempt,
                     (unsigned long)delay_millis,
                     type_label,
                     route_label,
                     target_label,
                     (uint32_t)log_cr,
                     (uint32_t)log_preamble_len);
#endif
  if (_logging) {
    File f = openAppend(PACKET_LOG_FILE);
    if (f) {
      f.print(getLogDateTime());
      f.printf(": direct retry %s attempt=%u delay=%lu type=%s route=%s target=%s cr=%u preamble_len=%u\n",
               event ? event : "?",
               (uint32_t)retry_attempt,
               (unsigned long)delay_millis,
               type_label,
               route_label,
               target_label,
               (uint32_t)log_cr,
               (uint32_t)log_preamble_len);
      f.close();
    }
  }
}

void MyMesh::onDirectRetryFailed(const uint8_t* next_hop_hash, uint8_t next_hop_hash_len) {
  if (next_hop_hash == NULL || next_hop_hash_len == 0) {
    return;
  }

  SimpleMeshTables* tables = static_cast<SimpleMeshTables*>(getTables());
  if (tables != NULL) {
    if (!tables->decrementRecentRepeaterSnrX4(next_hop_hash, next_hop_hash_len, 1)) {
      tables->setRecentRepeater(next_hop_hash, next_hop_hash_len, 11);
    }
  }
}

void MyMesh::onDirectRetrySucceeded(const uint8_t* next_hop_hash, uint8_t next_hop_hash_len, int8_t snr_x4) {
  if (next_hop_hash == NULL || next_hop_hash_len == 0) {
    return;
  }

  SimpleMeshTables* tables = static_cast<SimpleMeshTables*>(getTables());
  if (tables != NULL) {
    tables->setRecentRepeater(next_hop_hash, next_hop_hash_len, snr_x4);
  }
}

bool MyMesh::hasFloodRetryPrefixes() const {
  for (int i = 0; i < FLOOD_RETRY_PREFIX_SLOTS; i++) {
    const uint8_t* configured = _prefs.flood_retry_prefixes[i];
    if (configured[0] != 0 || configured[1] != 0 || configured[2] != 0) {
      return true;
    }
  }
  return false;
}

static bool configuredFloodRetryPrefixMatches(const uint8_t* configured,
                                              const uint8_t* observed,
                                              uint8_t observed_len) {
  return (configured[0] != 0 || configured[1] != 0 || configured[2] != 0)
      && routeHashPrefixesOverlap(configured, FLOOD_RETRY_PREFIX_LEN, observed, observed_len);
}

bool MyMesh::floodRetryLastHopMatches(const mesh::Packet* packet) const {
  if (packet == NULL || packet->getPathHashCount() == 0) {
    return false;
  }

  uint8_t hash_size = packet->getPathHashSize();
  if (hash_size == 0 || hash_size > MAX_ROUTE_HASH_BYTES) {
    return false;
  }

  const uint8_t* heard_prefix = &packet->path[(packet->getPathHashCount() - 1) * hash_size];
  for (int i = 0; i < FLOOD_RETRY_PREFIX_SLOTS; i++) {
    const uint8_t* configured = _prefs.flood_retry_prefixes[i];
    if (configuredFloodRetryPrefixMatches(configured, heard_prefix, hash_size)) {
      return true;
    }
  }

  return false;
}

bool MyMesh::floodRetryPrefixMatches(const mesh::Packet* packet) const {
  if (packet == NULL || packet->getPathHashCount() == 0) {
    return false;
  }

  uint8_t hash_size = packet->getPathHashSize();
  if (hash_size == 0 || hash_size > MAX_ROUTE_HASH_BYTES) {
    return false;
  }

  const uint8_t* path = packet->path;
  for (int hop = 0; hop < packet->getPathHashCount(); hop++) {
    for (int i = 0; i < FLOOD_RETRY_PREFIX_SLOTS; i++) {
      const uint8_t* configured = _prefs.flood_retry_prefixes[i];
      if (configuredFloodRetryPrefixMatches(configured, path, hash_size)) {
        return true;
      }
    }
    path += hash_size;
  }

  return false;
}

bool MyMesh::floodRetryPrefixIgnored(const uint8_t* prefix, uint8_t prefix_len) const {
  if (prefix == NULL || prefix_len == 0 || prefix_len > MAX_ROUTE_HASH_BYTES) {
    return false;
  }
  for (int i = 0; i < FLOOD_RETRY_IGNORE_PREFIXES; i++) {
    const uint8_t* ignored = _prefs.flood_retry_ignore_prefixes[i];
    if (configuredFloodRetryPrefixMatches(ignored, prefix, prefix_len)) {
      return true;
    }
  }
  return false;
}

uint8_t MyMesh::floodRetryEffectivePathLength(const mesh::Packet* packet, uint8_t max_hops) const {
  if (packet == NULL || !packet->isRouteFlood() || packet->getPathHashCount() == 0) {
    return 0;
  }

  uint8_t hash_size = packet->getPathHashSize();
  if (hash_size == 0 || hash_size > MAX_ROUTE_HASH_BYTES) {
    return packet->getPathHashCount();
  }

  uint8_t hop_count = packet->getPathHashCount();
  if (max_hops < hop_count) {
    hop_count = max_hops;
  }

  uint8_t effective_len = 0;
  const uint8_t* path = packet->path;
  for (uint8_t hop = 0; hop < hop_count; hop++) {
    if (!floodRetryPrefixIgnored(path, hash_size)) {
      effective_len++;
    }
    path += hash_size;
  }
  return effective_len;
}

bool MyMesh::floodRetryPrefixFresh(const uint8_t* prefix, uint8_t prefix_len) const {
  const SimpleMeshTables* tables = static_cast<const SimpleMeshTables*>(getTables());
  if (tables == NULL) {
    return false;
  }
  const auto* recent = tables->findRecentRepeaterByHash(prefix, prefix_len);
  if (recent == NULL || recent->last_heard_millis == 0) {
    return false;
  }
  return (uint32_t)(millis() - recent->last_heard_millis) <= 3600000UL;
}

static const uint8_t FLOOD_RETRY_BRIDGE_OTHER_BUCKET = FLOOD_RETRY_BRIDGE_BUCKETS;

static uint8_t floodRetryBucketMask(uint8_t bucket) {
  if (bucket >= 8) {
    return 0;
  }
  return (uint8_t)(1U << bucket);
}

uint8_t MyMesh::floodRetryBucketMaskForPrefix(const uint8_t* prefix, uint8_t prefix_len, bool require_fresh) const {
  if (prefix == NULL || prefix_len == 0 || prefix_len > MAX_ROUTE_HASH_BYTES) {
    return 0;
  }
  if (floodRetryPrefixIgnored(prefix, prefix_len)) {
    return 0;
  }
  if (require_fresh && !floodRetryPrefixFresh(prefix, prefix_len)) {
    return 0;
  }
  uint8_t mask = 0;
  for (int bucket = 0; bucket < FLOOD_RETRY_BRIDGE_BUCKETS; bucket++) {
    for (int i = 0; i < FLOOD_RETRY_BUCKET_PREFIXES; i++) {
      const uint8_t* configured = _prefs.flood_retry_bridge_buckets[bucket][i];
      if (configuredFloodRetryPrefixMatches(configured, prefix, prefix_len)) {
        mask |= floodRetryBucketMask((uint8_t)bucket);
        break;
      }
    }
  }
  for (int i = 0; i < FLOOD_RETRY_PREFIX_SLOTS; i++) {
    if (configuredFloodRetryPrefixMatches(_prefs.flood_retry_prefixes[i], prefix, prefix_len)) {
      mask |= floodRetryBucketMask(FLOOD_RETRY_BRIDGE_OTHER_BUCKET);
      break;
    }
  }
  return mask;
}

uint8_t MyMesh::floodRetryBucketMaskForPathHop(const uint8_t* prefix, uint8_t prefix_len, uint8_t hop,
                                               uint8_t progress_marker) const {
  return floodRetryBucketMaskForPrefix(prefix, prefix_len, hop < progress_marker);
}

uint8_t MyMesh::floodRetrySourceMask(const mesh::Packet* packet) const {
  if (packet == NULL) {
    return 0;
  }
  uint8_t hash_size = packet->getPathHashSize();
  if (hash_size == 0 || hash_size > MAX_ROUTE_HASH_BYTES) {
    return 0;
  }
  if (packet->getPathHashCount() < 2) {
    return floodRetryBucketMask(FLOOD_RETRY_BRIDGE_OTHER_BUCKET);
  }
  const uint8_t* source_prefix = &packet->path[(packet->getPathHashCount() - 2) * hash_size];
  return floodRetryBucketMaskForPrefix(source_prefix, hash_size, true);
}

bool MyMesh::floodRetryBridgeBucketFresh(uint8_t bucket) const {
  if (bucket > FLOOD_RETRY_BRIDGE_OTHER_BUCKET) {
    return false;
  }

  const uint8_t (*prefixes)[FLOOD_RETRY_PREFIX_LEN];
  uint8_t prefix_count;
  if (bucket == FLOOD_RETRY_BRIDGE_OTHER_BUCKET) {
    prefixes = _prefs.flood_retry_prefixes;
    prefix_count = FLOOD_RETRY_PREFIX_SLOTS;
  } else {
    prefixes = _prefs.flood_retry_bridge_buckets[bucket];
    prefix_count = FLOOD_RETRY_BUCKET_PREFIXES;
  }

  for (uint8_t i = 0; i < prefix_count; i++) {
    const uint8_t* configured = prefixes[i];
    if ((configured[0] != 0 || configured[1] != 0 || configured[2] != 0)
        && !floodRetryPrefixIgnored(configured, FLOOD_RETRY_PREFIX_LEN)
        && floodRetryPrefixFresh(configured, FLOOD_RETRY_PREFIX_LEN)) {
      return true;
    }
  }

  const FloodRetryBridgeReachability& reachable = flood_retry_bridge_reachability[bucket];
  if (reachable.prefix_len == 0 || reachable.last_heard_millis == 0
      || (uint32_t)(millis() - reachable.last_heard_millis) > 3600000UL) {
    return false;
  }
  return (floodRetryBucketMaskForPrefix(reachable.prefix, reachable.prefix_len, false)
          & floodRetryBucketMask(bucket)) != 0;
}

void MyMesh::recordFloodRetryBridgeReachability(const uint8_t* prefix, uint8_t prefix_len,
                                                uint8_t bucket_mask) {
  if (prefix == NULL || prefix_len == 0 || prefix_len > MAX_ROUTE_HASH_BYTES) {
    return;
  }

  uint32_t now = millis();
  if (now == 0) {
    now = 1;  // zero means unused
  }
  for (uint8_t bucket = 0; bucket <= FLOOD_RETRY_BRIDGE_OTHER_BUCKET; bucket++) {
    if ((bucket_mask & floodRetryBucketMask(bucket)) == 0) {
      continue;
    }
    FloodRetryBridgeReachability& reachable = flood_retry_bridge_reachability[bucket];
    memset(reachable.prefix, 0, sizeof(reachable.prefix));
    memcpy(reachable.prefix, prefix, prefix_len);
    reachable.prefix_len = prefix_len;
    reachable.last_heard_millis = now;
  }
}

uint8_t MyMesh::floodRetryBridgeTargetMask(uint8_t source_mask) const {
  uint8_t mask = 0;
  for (int bucket = 0; bucket < FLOOD_RETRY_BRIDGE_BUCKETS; bucket++) {
    uint8_t bucket_mask = floodRetryBucketMask((uint8_t)bucket);
    if ((source_mask & bucket_mask) != 0) {
      continue;
    }
    if (floodRetryBridgeBucketFresh((uint8_t)bucket)) {
      mask |= bucket_mask;
    }
  }
  uint8_t other_mask = floodRetryBucketMask(FLOOD_RETRY_BRIDGE_OTHER_BUCKET);
  if ((source_mask & other_mask) == 0
      && floodRetryBridgeBucketFresh(FLOOD_RETRY_BRIDGE_OTHER_BUCKET)) {
    mask |= other_mask;
  }
  return mask;
}

uint8_t MyMesh::floodRetryBridgeHeardMask(const mesh::Packet* packet, uint8_t source_mask,
                                          uint8_t progress_marker) const {
  if (packet == NULL || packet->getPathHashCount() == 0) {
    return 0;
  }
  uint8_t hash_size = packet->getPathHashSize();
  if (hash_size == 0 || hash_size > MAX_ROUTE_HASH_BYTES) {
    return 0;
  }

  uint8_t mask = 0;
  const uint8_t* path = packet->path;
  for (int hop = 0; hop < packet->getPathHashCount(); hop++) {
    if (progress_marker > 0 && hop == progress_marker - 1) {
      path += hash_size;
      continue;
    }
    uint8_t bucket_mask = floodRetryBucketMaskForPathHop(path, hash_size, (uint8_t)hop, progress_marker);
    mask |= bucket_mask & (uint8_t)~source_mask;
    path += hash_size;
  }
  return mask;
}

bool MyMesh::floodRetryBridgeEligible(const mesh::Packet* packet) const {
  FloodRetryBridgeState* state = floodRetryBridgeStateFor(packet, false);
  if (state != NULL) {
    return (state->heard_mask & state->target_mask) != state->target_mask;
  }

  uint8_t source_mask = floodRetrySourceMask(packet);
  if (source_mask == 0) {
    return false;
  }
  uint8_t target_mask = floodRetryBridgeTargetMask(source_mask);
  if (target_mask == 0) {
    return false;
  }
  uint8_t progress_marker = packet->getPathHashCount();
  uint8_t heard_mask = floodRetryBridgeHeardMask(packet, source_mask, progress_marker) & target_mask;
  return (heard_mask & target_mask) != target_mask;
}

MyMesh::FloodRetryBridgeState* MyMesh::floodRetryBridgeStateFor(const mesh::Packet* packet, bool create) const {
  if (packet == NULL) {
    return NULL;
  }

  uint8_t key[MAX_HASH_SIZE];
  packet->calculatePacketHash(key);
  FloodRetryBridgeState* free_slot = NULL;
  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (flood_retry_bridge_states[i].active
        && memcmp(flood_retry_bridge_states[i].key, key, MAX_HASH_SIZE) == 0) {
      return &flood_retry_bridge_states[i];
    }
    if (!flood_retry_bridge_states[i].active && free_slot == NULL) {
      free_slot = &flood_retry_bridge_states[i];
    }
  }
  if (!create || free_slot == NULL) {
    return NULL;
  }

  uint8_t source_mask = floodRetrySourceMask(packet);
  if (source_mask == 0) {
    return NULL;
  }

  uint8_t target_mask = floodRetryBridgeTargetMask(source_mask);
  if (target_mask == 0) {
    return NULL;
  }

  uint8_t progress_marker = packet->getPathHashCount();
  uint8_t heard_mask = floodRetryBridgeHeardMask(packet, source_mask, progress_marker) & target_mask;
  if ((heard_mask & target_mask) == target_mask) {
    return NULL;
  }

  memset(free_slot, 0, sizeof(*free_slot));
  memcpy(free_slot->key, key, sizeof(free_slot->key));
  free_slot->source_mask = source_mask;
  free_slot->target_mask = target_mask;
  free_slot->heard_mask = heard_mask;
  free_slot->progress_marker = progress_marker;
  free_slot->active = true;
  return free_slot;
}

bool MyMesh::allowFloodRetry(const mesh::Packet* packet) const {
  if (_prefs.disable_fwd || constrain(_prefs.flood_retry_attempts, 0, 15) == 0) {
    return false;
  }
  if (packet != NULL && packet->getPayloadType() == PAYLOAD_TYPE_ADVERT && !_prefs.flood_retry_advert_enabled) {
    return false;
  }
  if (!_prefs.flood_retry_bridge_enabled) {
    return true;
  }
  return floodRetryBridgeEligible(packet);
}

bool MyMesh::prepareFloodRetry(const mesh::Packet* packet) const {
  if (!_prefs.flood_retry_bridge_enabled) {
    return true;
  }
  return floodRetryBridgeStateFor(packet, true) != NULL;
}

void MyMesh::clearFloodRetryBridgeStateByKey(const uint8_t* retry_key) {
  if (retry_key == NULL) {
    return;
  }
  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (flood_retry_bridge_states[i].active
        && memcmp(flood_retry_bridge_states[i].key, retry_key, MAX_HASH_SIZE) == 0) {
      flood_retry_bridge_states[i].active = false;
      return;
    }
  }
}

void MyMesh::refreshFloodRetryReachability(const mesh::Packet* packet) {
  if (packet == NULL || !packet->isRouteFlood() || packet->getPathHashCount() == 0) {
    return;
  }

  uint8_t hash_size = packet->getPathHashSize();
  if (hash_size == 0 || hash_size > MAX_ROUTE_HASH_BYTES) {
    return;
  }

  SimpleMeshTables* tables = static_cast<SimpleMeshTables*>(getTables());
  if (tables == NULL) {
    return;
  }

  uint8_t path_count = packet->getPathHashCount();
  const uint8_t* last_hop = &packet->path[(path_count - 1) * hash_size];
  tables->setRecentRepeater(last_hop, hash_size, packet->_snr, false, true);

  const uint8_t* path = packet->path;
  if (_prefs.flood_retry_bridge_enabled) {
    FloodRetryBridgeState* state = floodRetryBridgeStateFor(packet, false);
    if (state != NULL) {
      for (uint8_t hop = 0; hop < path_count; hop++) {
        if (state->progress_marker > 0 && hop == state->progress_marker - 1) {
          path += hash_size;
          continue;
        }
        uint8_t bucket_mask = floodRetryBucketMaskForPathHop(path, hash_size, (uint8_t)hop,
                                                             state->progress_marker);
        bucket_mask &= state->target_mask & (uint8_t)~state->source_mask;
        if (bucket_mask != 0 && hop != path_count - 1) {
          recordFloodRetryBridgeReachability(path, hash_size, bucket_mask);
        }
        path += hash_size;
      }
    }
  }
}

void MyMesh::formatFloodRetryPath(char* dest, size_t dest_len, const mesh::Packet* packet) const {
  if (dest == NULL || dest_len == 0) {
    return;
  }
  dest[0] = 0;

  if (packet == NULL || packet->getPathHashCount() == 0) {
    StrHelper::strncpy(dest, "-", dest_len);
    return;
  }

  uint8_t hash_size = packet->getPathHashSize();
  if (hash_size == 0 || hash_size > MAX_ROUTE_HASH_BYTES) {
    StrHelper::strncpy(dest, "invalid", dest_len);
    return;
  }

  char* out = dest;
  size_t remaining = dest_len;
  const uint8_t* path = packet->path;
  for (int hop = 0; hop < packet->getPathHashCount(); hop++) {
    size_t needed = (hop > 0 ? 1 : 0) + ((size_t)hash_size * 2) + 1;
    if (remaining < needed) {
      if (remaining > 4) {
        strcpy(out, "...");
      }
      return;
    }
    if (hop > 0) {
      *out++ = '>';
      remaining--;
    }
    mesh::Utils::toHex(out, path, hash_size);
    out += (size_t)hash_size * 2;
    remaining -= (size_t)hash_size * 2;
    path += hash_size;
  }
}

bool MyMesh::formatFloodRetryHeard(char* dest, size_t dest_len, const mesh::Packet* packet) const {
  if (dest == NULL || dest_len == 0 || packet == NULL || packet->getPathHashCount() == 0) {
    return false;
  }
  dest[0] = 0;

  uint8_t hash_size = packet->getPathHashSize();
  if (hash_size == 0 || hash_size > MAX_ROUTE_HASH_BYTES) {
    return false;
  }

  char* out = dest;
  size_t remaining = dest_len;
  bool first = true;

  if (_prefs.flood_retry_bridge_enabled) {
    FloodRetryBridgeState* state = floodRetryBridgeStateFor(packet, false);
    if (state == NULL) {
      return false;
    }
    const uint8_t* path = packet->path;
    for (int hop = 0; hop < packet->getPathHashCount(); hop++) {
      if (state->progress_marker > 0 && hop == state->progress_marker - 1) {
        path += hash_size;
        continue;
      }
      uint8_t matching_mask = floodRetryBucketMaskForPathHop(path, hash_size, (uint8_t)hop,
                                                              state->progress_marker);
      matching_mask &= state->target_mask & (uint8_t)~state->source_mask;
      for (uint8_t bucket = 0; bucket <= FLOOD_RETRY_BRIDGE_OTHER_BUCKET; bucket++) {
        uint8_t bucket_mask = floodRetryBucketMask(bucket);
        if ((matching_mask & bucket_mask) == 0) {
          continue;
        }
        char bucket_label[8];
        if (bucket == FLOOD_RETRY_BRIDGE_OTHER_BUCKET) {
          strcpy(bucket_label, "other");
        } else {
          snprintf(bucket_label, sizeof(bucket_label), "b%d", bucket + 1);
        }
        size_t needed = (first ? 0 : 1) + strlen(bucket_label) + 1 + ((size_t)hash_size * 2) + 1;
        if (remaining < needed) {
          if (remaining > 4) {
            strcpy(out, "...");
          }
          return dest[0] != 0;
        }
        if (!first) {
          *out++ = ',';
          remaining--;
        }
        int n = snprintf(out, remaining, "%s:", bucket_label);
        if (n < 0 || (size_t)n >= remaining) {
          return dest[0] != 0;
        }
        out += n;
        remaining -= n;
        mesh::Utils::toHex(out, path, hash_size);
        out += (size_t)hash_size * 2;
        remaining -= (size_t)hash_size * 2;
        first = false;
      }
      path += hash_size;
    }
    return dest[0] != 0;
  }

  const uint8_t* heard_prefix = &packet->path[(packet->getPathHashCount() - 1) * hash_size];
  if (remaining < ((size_t)hash_size * 2) + 1) {
    return false;
  }
  mesh::Utils::toHex(out, heard_prefix, hash_size);
  return true;
}

void MyMesh::onFloodRetryEvent(const char* event, const mesh::Packet* packet, uint32_t delay_millis, uint8_t retry_attempt) {
#if defined(PORTABLE_MQTT_OBSERVER)
  (void)event; (void)packet; (void)delay_millis; (void)retry_attempt;
  return;
#endif
  if (event == NULL) {
    return;
  }

  if (strcmp(event, "failure") == 0) {
    return;
  }

  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("flood retry %s (retry=%u, elapsed_ms=%lu, packet=released)",
                       event, (unsigned int)retry_attempt, (unsigned long)delay_millis);
    if (_logging) {
      File f = openAppend(PACKET_LOG_FILE);
      if (f) {
        f.print(getLogDateTime());
        f.printf(": FLOOD RETRY %s (retry=%u, elapsed_ms=%lu, packet=released)\n",
                 event, (unsigned int)retry_attempt, (unsigned long)delay_millis);
        f.close();
      }
    }
    return;
  }

  const char* time_label = "time_ms";
  if (strcmp(event, "queued") == 0 || strcmp(event, "dropped_queue_full") == 0) {
    time_label = "wait_ms";
  } else if (strcmp(event, "resent") == 0 || strcmp(event, "failed_all_tries") == 0
      || strcmp(event, "failure") == 0 || strncmp(event, "dropped_", 8) == 0) {
    time_label = "elapsed_ms";
  } else if (strcmp(event, "good") == 0) {
    time_label = "echo_ms";
  }

  char path_log[208];
  char heard_log[96];
  char heard_suffix[112];
  formatFloodRetryPath(path_log, sizeof(path_log), packet);
  heard_suffix[0] = 0;
  if (strcmp(event, "good") == 0 && formatFloodRetryHeard(heard_log, sizeof(heard_log), packet)) {
    refreshFloodRetryReachability(packet);
    snprintf(heard_suffix, sizeof(heard_suffix), ", heard=%s", heard_log);
  }
  uint8_t log_cr = getRetryLogCodingRate(packet, getDefaultTxCodingRate());
  uint16_t log_preamble_len = getRetryLogPreambleLength(packet, radio_driver.getDefaultPreambleLength());

  MESH_DEBUG_PRINTLN("flood retry %s (retry=%u, type=%d, route=%s, payload_len=%d, hop=%u, path=%s%s, %s=%lu, cr=%u, preamble_len=%u)",
                     event,
                     (unsigned int)retry_attempt,
                     (uint32_t)packet->getPayloadType(),
                     packet->isRouteDirect() ? "D" : "F",
                     (uint32_t)packet->payload_len,
                     (unsigned int)packet->getPathHashCount(),
                     path_log,
                     heard_suffix,
                     time_label,
                     (unsigned long)delay_millis,
                     (uint32_t)log_cr,
                     (uint32_t)log_preamble_len);

  if (_logging) {
    File f = openAppend(PACKET_LOG_FILE);
    if (f) {
      f.print(getLogDateTime());
      f.printf(": FLOOD RETRY %s (retry=%u, type=%d, route=%s, payload_len=%d, hop=%u, path=%s%s, %s=%lu, cr=%u, preamble_len=%u)\n",
               event,
               (unsigned int)retry_attempt,
               (uint32_t)packet->getPayloadType(),
               packet->isRouteDirect() ? "D" : "F",
               (uint32_t)packet->payload_len,
               (unsigned int)packet->getPathHashCount(),
               path_log,
               heard_suffix,
               time_label,
               (unsigned long)delay_millis,
               (uint32_t)log_cr,
               (uint32_t)log_preamble_len);
      f.close();
    }
  }

}

void MyMesh::onFloodRetrySlotReleased(const uint8_t* retry_key) {
  clearFloodRetryBridgeStateByKey(retry_key);
}

bool MyMesh::hasFloodRetryTargetPrefix(const mesh::Packet* packet) const {
  if (_prefs.flood_retry_bridge_enabled) {
    return false;
  }
  return floodRetryPrefixMatches(packet);
}

uint8_t MyMesh::getFloodRetryMaxPathLength(const mesh::Packet* packet) const {
  uint8_t gate = _prefs.flood_retry_max_path;
  if (gate != FLOOD_RETRY_PATH_GATE_DISABLED) {
    if (gate > 63) {
      gate = FLOOD_RETRY_ROOFTOP_MAX_PATH;
    }

    uint8_t raw_hops = packet != NULL ? packet->getPathHashCount() : 0;
    uint8_t effective_hops = floodRetryEffectivePathLength(packet);
    uint8_t ignored_hops = raw_hops > effective_hops ? raw_hops - effective_hops : 0;
    uint16_t adjusted_gate = (uint16_t)gate + ignored_hops;
    gate = adjusted_gate > 63 ? 63 : (uint8_t)adjusted_gate;
  }

  uint8_t group_data_gate = _prefs.flood_retry_group_max_path == FLOOD_RETRY_PATH_GATE_DISABLED
      ? FLOOD_RETRY_PATH_GATE_DISABLED
      : constrain(_prefs.flood_retry_group_max_path, 0, 63);
  return applyGroupDataFloodRetryPathGate(packet, gate, group_data_gate);
}

uint8_t MyMesh::getFloodRetryMaxAttempts(const mesh::Packet* packet) const {
  if (_prefs.disable_fwd) {
    return 0;
  }

  uint8_t attempts = constrain(_prefs.flood_retry_attempts, 0, 15);
  uint16_t scaled_attempts = attempts;
  uint8_t hops = packet != NULL ? packet->getPathHashCount() : 0;
  if (hops == 0) {
    scaled_attempts = (uint16_t)attempts * 2U;
  } else if (hops == 1) {
    scaled_attempts = (((uint16_t)attempts * 3U) + 1U) / 2U;
  }
  return scaled_attempts > 15 ? 15 : (uint8_t)scaled_attempts;
}

bool MyMesh::isFloodRetryEchoTarget(const mesh::Packet* packet, uint8_t progress_marker) const {
  if (packet == NULL || !packet->isRouteFlood()) {
    return false;
  }
  if (packet->getPathHashCount() == 0) {
    return false;
  }
  uint8_t hash_size = packet->getPathHashSize();
  if (hash_size == 0 || hash_size > MAX_ROUTE_HASH_BYTES) {
    return false;
  }
  const uint8_t* heard_prefix = &packet->path[(packet->getPathHashCount() - 1) * hash_size];
  if (floodRetryPrefixIgnored(heard_prefix, hash_size)) {
    return false;
  }
  if (_prefs.flood_retry_bridge_enabled) {
    FloodRetryBridgeState* state = floodRetryBridgeStateFor(packet, false);
    if (state == NULL) {
      return false;
    }
    state->heard_mask |= floodRetryBridgeHeardMask(packet, state->source_mask, state->progress_marker) & state->target_mask;
    return (state->heard_mask & state->target_mask) == state->target_mask;
  }
  if (hasFloodRetryPrefixes()) {
    return floodRetryLastHopMatches(packet);
  }
  return true;
}

static void formatLocalSnrX4(char* dest, size_t dest_len, int16_t snr_x4) {
  int16_t v = snr_x4;
  const char* sign = "";
  if (v < 0) {
    sign = "-";
    v = -v;
  }
  snprintf(dest, dest_len, "%s%d.%02d", sign, v / 4, (v % 4) * 25);
  size_t len = strlen(dest);
  if (len > 3 && dest[len - 1] == '0') {
    dest[len - 1] = 0;
  }
}

void MyMesh::formatRecentRepeatersReply(char *reply, int page,
                                        const uint8_t* search_prefix,
                                        uint8_t search_prefix_len) {
  const SimpleMeshTables* tables = static_cast<const SimpleMeshTables*>(getTables());
  if (tables == NULL) {
    strcpy(reply, "Error: unsupported");
    return;
  }
  const bool is_search = search_prefix != NULL && search_prefix_len > 0;
  int count = is_search
      ? tables->getRecentRepeaterMatchingCount(search_prefix, search_prefix_len)
      : tables->getRecentRepeaterCount();
  if (count <= 0) {
    if (is_search) {
      char search_hex[MAX_ROUTE_HASH_BYTES * 2 + 1];
      mesh::Utils::toHex(search_hex, search_prefix, search_prefix_len);
      search_hex[search_prefix_len * 2] = 0;
      snprintf(reply, 160, "> %s -none-", search_hex);
    } else {
      strcpy(reply, "> -none-");
    }
    return;
  }

  // Search rows include their compact recorded age. Six worst-case rows plus the
  // page header still fit the 160-byte remote CLI reply buffer.
  const int page_size = is_search ? 6 : 10;
  int pages = (count + page_size - 1) / page_size;
  if (page < 1) page = 1;
  if (page > pages) page = pages;

  int len;
  if (is_search) {
    char search_hex[MAX_ROUTE_HASH_BYTES * 2 + 1];
    mesh::Utils::toHex(search_hex, search_prefix, search_prefix_len);
    search_hex[search_prefix_len * 2] = 0;
    len = snprintf(reply, 160, "> %s %d/%d (%d matches)",
                   search_hex, page, pages, count);
  } else {
    len = snprintf(reply, 160, "> %d/%d", page, pages);
  }
  int start = (page - 1) * page_size;
  for (int i = 0; i < page_size && len < 150; i++) {
    const SimpleMeshTables::RecentRepeaterInfo* info = is_search
        ? tables->getRecentRepeaterMatchingBySortedIdx(
              search_prefix, search_prefix_len, start + i)
        : tables->getRecentRepeaterBySortedIdx(start + i);
    if (info == NULL) break;
    char prefix[MAX_ROUTE_HASH_BYTES * 2 + 1];
    char snr[12];
    mesh::Utils::toHex(prefix, info->prefix, info->prefix_len);
    prefix[info->prefix_len * 2] = 0;
    formatLocalSnrX4(snr, sizeof(snr), info->snr_x4);
    if (is_search) {
      const uint32_t age_seconds =
          (uint32_t)(millis() - info->last_heard_millis) / 1000UL;
      char age[12];
      mesh::cli::formatRecentRepeaterAge(age, sizeof(age), age_seconds);
      len += snprintf(&reply[len], 160 - len, "\n%s,%s%s,%s",
                      prefix,
                      snr[0] == '-' ? "" : " ",
                      snr,
                      age);
    } else {
      len += snprintf(&reply[len], 160 - len, "\n%s,%s%s",
                      prefix,
                      snr[0] == '-' ? "" : " ",
                      snr);
    }
  }
}

void MyMesh::printRecentRepeatersSerial() {
  const SimpleMeshTables* tables = static_cast<const SimpleMeshTables*>(getTables());
  if (tables == NULL) {
    Serial.println("Error: unsupported");
    return;
  }

  int count = tables->getRecentRepeaterCount();
  Serial.printf("Recent repeaters (%d):\n", count);
  if (count <= 0) {
    Serial.println("-none-");
    return;
  }

  for (int i = 0; i < count; i++) {
    const SimpleMeshTables::RecentRepeaterInfo* info = tables->getRecentRepeaterBySortedIdx(i);
    if (info == NULL) break;
    char prefix[MAX_ROUTE_HASH_BYTES * 2 + 1];
    char snr[12];
    mesh::Utils::toHex(prefix, info->prefix, info->prefix_len);
    prefix[info->prefix_len * 2] = 0;
    formatLocalSnrX4(snr, sizeof(snr), info->snr_x4);
    Serial.printf("%s,%s%s\n", prefix, snr[0] == '-' ? "" : " ", snr);
  }
}

bool MyMesh::setRecentRepeater(const uint8_t* prefix, uint8_t prefix_len, int8_t snr_x4) {
  SimpleMeshTables* tables = static_cast<SimpleMeshTables*>(getTables());
  return tables != NULL && tables->setRecentRepeater(prefix, prefix_len, snr_x4);
}

void MyMesh::clearRecentRepeaters() {
  SimpleMeshTables* tables = static_cast<SimpleMeshTables*>(getTables());
  if (tables != NULL) {
    tables->clearRecentRepeaters();
  }
}

void MyMesh::expireRecentRepeatersIfDue() {
  if (!next_recent_repeater_sweep || !millisHasNowPassed(next_recent_repeater_sweep)) {
    return;
  }

  SimpleMeshTables* tables = static_cast<SimpleMeshTables*>(getTables());
  if (tables != NULL) {
    int expired = tables->expireRecentRepeaters(_ms->getMillis(), RECENT_REPEATER_MAX_AGE_MILLIS);
    if (expired > 0) {
      MESH_DEBUG_PRINTLN("Recent repeaters: expired %d entries", expired);
    }
  }
  next_recent_repeater_sweep = futureMillis(RECENT_REPEATER_SWEEP_INTERVAL_MILLIS);
}

mesh::DispatcherAction MyMesh::onRecvPacket(mesh::Packet* pkt) {
  bool scope_changed = false;
  bool fast_track_scope_change = false;
  recv_pkt_regionless_scope_set = false;
  recv_pkt_channel_scope_bypass = false;
  recv_pkt_channel_scope_rejected = false;
  recv_pkt_filter_match_mask = 0;
#if !defined(PORTABLE_MQTT_OBSERVER)
  if (pkt->isRouteFlood()) {
    bool incoming_region_allowed = false;
    RegionEntry* incoming_region = NULL;
    if (pkt->getRouteType() == ROUTE_TYPE_TRANSPORT_FLOOD) {
      incoming_region = region_map.findMatch(pkt, REGION_DENY_FLOOD);
      incoming_region_allowed = incoming_region != NULL;
    } else if (pkt->getRouteType() == ROUTE_TYPE_FLOOD) {
      incoming_region_allowed =
          (region_map.getWildcard().flags & REGION_DENY_FLOOD) == 0;
    }

    bool requirement_table_active = false;
    bool channel_requires_scope = findFloodChannelScopeRequirementMatch(
        pkt, requirement_table_active);
    bool is_group_channel_packet =
        pkt->getPayloadType() == PAYLOAD_TYPE_GRP_TXT
        || pkt->getPayloadType() == PAYLOAD_TYPE_GRP_DATA;
    FloodFilterPolicy::ChannelScopeGate channel_scope_gate =
        FloodFilterPolicy::channelScopeGate(
            requirement_table_active, is_group_channel_packet,
            channel_requires_scope,
            pkt->getRouteType() == ROUTE_TYPE_TRANSPORT_FLOOD,
            incoming_region_allowed);
    recv_pkt_channel_scope_bypass =
        channel_scope_gate == FloodFilterPolicy::CHANNEL_SCOPE_BYPASS;
    recv_pkt_channel_scope_rejected =
        channel_scope_gate
        == FloodFilterPolicy::CHANNEL_SCOPE_REQUIRED_REJECTED;

    if (!recv_pkt_channel_scope_rejected) {
      recv_pkt_filter_match_mask = evaluateFloodPacketFilterMatches(
          pkt, incoming_region_allowed, incoming_region);
      bool channel_regionless_scope_set = false;
      scope_changed =
          applyFloodChannelScope(pkt, fast_track_scope_change,
                                 channel_regionless_scope_set);
      bool filter_scope_set = false;
      bool filter_fast_track = false;
      if (applyFloodPacketFilterScope(pkt, recv_pkt_filter_match_mask,
                                      filter_scope_set, filter_fast_track)) {
        scope_changed = true;
        fast_track_scope_change = filter_fast_track;
      }
      recv_pkt_regionless_scope_set =
          channel_regionless_scope_set || filter_scope_set;
    }
  }
#endif
  if (pkt->getRouteType() == ROUTE_TYPE_TRANSPORT_FLOOD) {
    recv_pkt_region = region_map.findMatch(pkt, REGION_DENY_FLOOD);
  } else if (pkt->getRouteType() == ROUTE_TYPE_FLOOD) {
    if (region_map.getWildcard().flags & REGION_DENY_FLOOD) {
      recv_pkt_region = NULL;
    } else {
      recv_pkt_region =  &region_map.getWildcard();
    }
  } else {
    recv_pkt_region = NULL;
  }
  mesh::DispatcherAction action = Mesh::onRecvPacket(pkt);
  if (scope_changed && action != ACTION_RELEASE && action != ACTION_MANUAL_HOLD) {
    if (fast_track_scope_change) {
      // This repeater changed the scope, so forward it at the highest queue
      // priority with no txdelay and let the selected scope win downstream.
      action = ACTION_RETRANSMIT(0);
    } else {
      uint8_t priority = (action >> 24) - 1;
      action = ACTION_RETRANSMIT_DELAYED(
          priority, getSlowScopeRetransmitDelay(pkt));
    }
  }
  return action;
}

void MyMesh::onAnonDataRecv(mesh::Packet *packet, const uint8_t *secret, const mesh::Identity &sender,
                            uint8_t *data, size_t len) {
  if (packet->getPayloadType() == PAYLOAD_TYPE_ANON_REQ) { // received an initial request by a possible admin
                                                           // client (unknown at this stage)
    uint32_t timestamp;
    memcpy(&timestamp, data, 4);

    data[len] = 0;  // ensure null terminator
    uint8_t reply_len;

    reply_path_len = 0xFF;
    if (data[4] == 0 || data[4] >= ' ') {   // is password, ie. a login request
      reply_len = handleLoginReq(sender, secret, timestamp, &data[4], packet->isRouteFlood());
    } else if (data[4] == ANON_REQ_TYPE_REGIONS && packet->isRouteDirect()) {
      reply_len = handleAnonRegionsReq(sender, timestamp, &data[5]);
    } else if (data[4] == ANON_REQ_TYPE_OWNER && packet->isRouteDirect()) {
      reply_len = handleAnonOwnerReq(sender, timestamp, &data[5]);
    } else if (data[4] == ANON_REQ_TYPE_BASIC && packet->isRouteDirect()) {
      reply_len = handleAnonClockReq(sender, timestamp, &data[5]);
    } else {
      reply_len = 0;  // unknown/invalid request type
    }

    if (reply_len == 0) return;   // invalid request

    // a DIRECT login can reply via the stored out_path, as onPeerDataRecv() does for REQ
    ClientInfo* client = acl.getClient(sender.pub_key, PUB_KEY_SIZE);
    bool have_out_path = client != NULL && client->out_path_len != OUT_PATH_UNKNOWN;

    auto route = mesh::chooseReplyRoute(packet->isRouteFlood(), reply_path_len != 0xFF, have_out_path);

    if (route == mesh::REPLY_ROUTE_PATH_RETURN) {
      // let this sender know path TO here, so they can use sendDirect(), and ALSO encode the response
      mesh::Packet* path = createPathReturn(sender, secret, packet->path, packet->path_len,
                                            PAYLOAD_TYPE_RESPONSE, reply_data, reply_len);
      if (path) sendFloodReply(path, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
      return;
    }

    mesh::Packet* reply = createDatagram(PAYLOAD_TYPE_RESPONSE, sender, secret, reply_data, reply_len);
    if (reply == NULL) return;

    if (route == mesh::REPLY_ROUTE_DIRECT_SUPPLIED) {
      sendDirect(reply, reply_path, reply_path_len, SERVER_RESPONSE_DELAY);
    } else if (route == mesh::REPLY_ROUTE_DIRECT_OUT_PATH) {
      sendDirect(reply, client->out_path, client->out_path_len, SERVER_RESPONSE_DELAY);
    } else {
      sendFloodReply(reply, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
    }
  }
}

int MyMesh::searchPeersByHash(const uint8_t *hash) {
  int n = 0;
#if defined(WITH_MQTT_NEIGHBORS)
  // While a neighbor-scope discovery is active, overlay the heard neighbours
  // that are NOT already ACL clients so their RESPONSE packets can be decoded.
  // Overlay indices are offset by NEIGHBOR_DISCOVER_PEER_BASE to keep them
  // distinct from real ACL indices.
  if (neighbor_discover_active) {
    for (int i = 0; i < neighbor_discover_count && n < MAX_CLIENTS; i++) {
      auto& entry = neighbor_discover[i];
      if (acl.getClient(entry.id.pub_key, PUB_KEY_SIZE) != nullptr) continue;
      if (entry.heard_timestamp > 0 && entry.id.isHashMatch(hash)) {
        matching_peer_indexes[n++] = NEIGHBOR_DISCOVER_PEER_BASE + i;
      }
    }
  }
#endif
  for (int i = 0; i < acl.getNumClients() && n < MAX_CLIENTS; i++) {
    if (acl.getClientByIdx(i)->id.isHashMatch(hash)) {
      matching_peer_indexes[n++] = i; // store the INDEXES of matching contacts (for subsequent 'peer' methods)
    }
  }
  return n;
}

void MyMesh::getPeerSharedSecret(uint8_t *dest_secret, int peer_idx) {
  int i = matching_peer_indexes[peer_idx];
#if defined(WITH_MQTT_NEIGHBORS)
  // Overlay entries have no precomputed shared secret; derive it on the fly.
  if (neighbor_discover_active && i >= NEIGHBOR_DISCOVER_PEER_BASE) {
    int oi = i - NEIGHBOR_DISCOVER_PEER_BASE;
    if (oi >= 0 && oi < neighbor_discover_count) {
      self_id.calcSharedSecret(dest_secret, neighbor_discover[oi].id);
      return;
    }
  }
#endif
  if (i >= 0 && i < acl.getNumClients()) {
    // lookup pre-calculated shared_secret
    memcpy(dest_secret, acl.getClientByIdx(i)->shared_secret, PUB_KEY_SIZE);
  } else {
    MESH_DEBUG_PRINTLN("getPeerSharedSecret: Invalid peer idx: %d", i);
  }
}

static bool isShare(const mesh::Packet *packet) {
  if (packet->hasTransportCodes()) {
    return packet->transport_codes[0] == 0 && packet->transport_codes[1] == 0;  // codes { 0, 0 } means 'send to nowhere'
  }
  return false;
}

void MyMesh::onAdvertRecv(mesh::Packet *packet, const mesh::Identity &id, uint32_t timestamp,
                          const uint8_t *app_data, size_t app_data_len) {
  mesh::Mesh::onAdvertRecv(packet, id, timestamp, app_data, app_data_len); // chain to super impl

  // Mesh calls this hook only after verifying the advert's Ed25519 signature.
  // Edge mode observes it here rather than in allowPacketForward(), because
  // forwarding can be disabled on a receive-only edge node.
#if !defined(PORTABLE_MQTT_OBSERVER) && MESH_ENABLE_CLOCK_SYNC
  if (clock_sync_mesh_edge_enabled && isClockSyncCollectionActive()) {
    uint8_t source_id[4];
    mesh::Utils::sha256(source_id, sizeof(source_id), id.pub_key, PUB_KEY_SIZE);
    recordClockSyncSample(mesh::CLOCK_SYNC_SAMPLE_SOURCE_SIGNED_ADVERT,
                          source_id, timestamp, packet);
  }
#endif

  // if this a zero hop advert (and not via 'Share'), add it to neighbours
  if (packet->getPathHashCount() == 0 && !isShare(packet)) {
    AdvertDataParser parser(app_data, app_data_len);
    if (parser.isValid() && parser.getType() == ADV_TYPE_REPEATER) { // just keep neigbouring Repeaters
      putNeighbour(id, timestamp, packet->getSNR());
    }
  }
}

void MyMesh::onGroupPacketRecv(mesh::Packet* packet) {
#if !defined(PORTABLE_MQTT_OBSERVER) && MESH_ENABLE_CLOCK_SYNC
  // The base Mesh calls this for every unseen, structurally valid group packet
  // before the forwarding decision. Public-channel decryption below also
  // verifies its MAC, so unrelated or forged channel packets are ignored.
  if (clock_sync_mesh_edge_enabled && packet != NULL
      && packet->getPayloadType() == PAYLOAD_TYPE_GRP_TXT) {
    recordPublicChannelClockSample(packet);
  }
#else
  (void)packet;
#endif
}

void MyMesh::onPeerDataRecv(mesh::Packet *packet, uint8_t type, int sender_idx, const uint8_t *secret,
                            uint8_t *data, size_t len) {
  int i = matching_peer_indexes[sender_idx];
#if defined(WITH_MQTT_NEIGHBORS)
  // Overlay response: a heard neighbour (not an ACL client) answering our
  // anon-regions scope query. Consume it and stop -- it is not a client packet.
  if (neighbor_discover_active && i >= NEIGHBOR_DISCOVER_PEER_BASE) {
    int oi = i - NEIGHBOR_DISCOVER_PEER_BASE;
    if (type == PAYLOAD_TYPE_RESPONSE && oi >= 0 && oi < neighbor_discover_count) {
      handleNeighborDiscoverResponse(oi, data, len);
    }
    return;
  }
#endif
  if (i < 0 || i >= acl.getNumClients()) { // get from our known_clients table (sender SHOULD already be known in this context)
    MESH_DEBUG_PRINTLN("onPeerDataRecv: invalid peer idx: %d", i);
    return;
  }
  ClientInfo* client = acl.getClientByIdx(i);
#if defined(WITH_MQTT_NEIGHBORS)
  // A neighbour that IS an ACL client resolves to a normal index above, so a
  // scope-query response from it lands here -- match it against the overlay.
  if (neighbor_discover_active && type == PAYLOAD_TYPE_RESPONSE) {
    for (int oi = 0; oi < neighbor_discover_count; oi++) {
      if (client->id.matches(neighbor_discover[oi].id)
          && handleNeighborDiscoverResponse(oi, data, len)) {
        return;
      }
    }
  }
#endif

  if (type == PAYLOAD_TYPE_REQ) { // request (from a Known admin client!)
    uint32_t timestamp;
    memcpy(&timestamp, data, 4);

    if (timestamp > client->last_timestamp) { // prevent replay attacks
      int reply_len = handleRequest(client, timestamp, &data[4], len - 4);
      if (reply_len == 0) return; // invalid command

      client->last_timestamp = timestamp;
      client->last_activity = getRTCClock()->getCurrentTime();

      if (packet->isRouteFlood()) {
        // let this sender know path TO here, so they can use sendDirect(), and ALSO encode the response
        mesh::Packet *path = createPathReturn(client->id, secret, packet->path, packet->path_len,
                                              PAYLOAD_TYPE_RESPONSE, reply_data, reply_len);
        if (path) sendFloodReply(path, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
      } else {
        mesh::Packet *reply =
            createDatagram(PAYLOAD_TYPE_RESPONSE, client->id, secret, reply_data, reply_len);
        sendClientReply(client, reply, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
      }
    } else {
      MESH_DEBUG_PRINTLN("onPeerDataRecv: possible replay attack detected");
    }
  } else if (type == PAYLOAD_TYPE_TXT_MSG && len > 5
      && (client->isAdmin() || client->isRegionMgr() || client->isFilterMgr())) { // a CLI command
    uint32_t sender_timestamp;
    memcpy(&sender_timestamp, data, 4); // timestamp (by sender's RTC clock - which could be wrong)
    uint8_t flags = (data[4] >> 2);        // message attempt number, and other flags

    if (!(flags == TXT_TYPE_PLAIN || flags == TXT_TYPE_CLI_DATA)) {
      MESH_DEBUG_PRINTLN("onPeerDataRecv: unsupported text type received: flags=%02x", (uint32_t)flags);
    } else {
      // len can be > original length, but 'text' will be padded with zeroes
      data[len] = 0; // need to make a C string again, with null terminator

      char *command = (char *)&data[5];
      size_t command_len = strlen(command);
      uint32_t request_id = sender_timestamp;
      mesh::RemoteCliRequest::parse(data, len, 5, request_id);
      uint32_t command_fingerprint =
          mesh::RemoteCliReplyCache::fingerprint(command, command_len);
      const char* cached_response = NULL;
      const bool cached_retry = remote_cli_reply_cache.lookup(
          client->id.pub_key, request_id, command_fingerprint,
          &cached_response);

      // An old exact match may only replay its stored response. Any stale
      // mismatch remains blocked by the normal timestamp replay guard.
      if (sender_timestamp < client->last_timestamp && !cached_retry) {
        MESH_DEBUG_PRINTLN("onPeerDataRecv: possible replay attack detected");
      } else {
        const bool repeated_timestamp = sender_timestamp == client->last_timestamp;
        if (sender_timestamp > client->last_timestamp) {
          client->last_timestamp = sender_timestamp;
        }
        client->last_activity = getRTCClock()->getCurrentTime();

        if (flags == TXT_TYPE_PLAIN) { // for legacy CLI, send Acks
          uint32_t ack_hash; // calc truncated hash of the message timestamp + text + sender pub_key, to prove
                             // to sender that we got it
          mesh::Utils::sha256((uint8_t *)&ack_hash, 4, data, 5 + command_len,
                              client->id.pub_key, PUB_KEY_SIZE);

          mesh::Packet *ack = createAck(ack_hash);
          sendClientReply(client, ack, TXT_ACK_DELAY, packet->getPathHashSize());
        }

        TransportKey reply_scope;
        const bool reply_scoped =
            recv_pkt_region != NULL && !recv_pkt_region->isWildcard()
            && region_map.getTransportKeysFor(*recv_pkt_region, &reply_scope, 1) > 0;

        if (cached_retry) {
          MESH_DEBUG_PRINTLN("onPeerDataRecv: replaying cached remote CLI reply");
          sendRemoteCliReply(client, secret, packet->getPathHashSize(),
                             sender_timestamp, cached_response,
                             reply_scoped ? &reply_scope : NULL);
        } else if (deferred_cli_command.matches(i, request_id, command,
                                                command_len)) {
          // The original request is already queued. Let it produce the one
          // authoritative result instead of turning an in-flight retry into a
          // spurious busy error.
          MESH_DEBUG_PRINTLN("onPeerDataRecv: remote CLI request is already pending");
        } else if (repeated_timestamp) {
          MESH_DEBUG_PRINTLN("onPeerDataRecv: duplicate remote CLI request has no cached reply");
        } else if (!deferred_cli_command.enqueue(i, sender_timestamp,
                                                 packet->getPathHashSize(), secret,
                                                 command, command_len,
                                                 request_id)) {
          const char* error = deferred_cli_command.pending
              ? "Err - another remote command is still running"
              : "Err - remote command is too long";
          remote_cli_reply_cache.remember(client->id.pub_key, request_id,
                                          command_fingerprint, error);
          sendRemoteCliReply(client, secret, packet->getPathHashSize(),
                             sender_timestamp, error,
                             reply_scoped ? &reply_scope : NULL);
        } else {
          deferred_cli_reply_scoped = reply_scoped;
          if (reply_scoped) {
            deferred_cli_reply_scope = reply_scope;
          } else {
            memset(deferred_cli_reply_scope.key, 0, sizeof(deferred_cli_reply_scope.key));
          }
        }
      }
    }
  }
}

void MyMesh::sendRemoteCliReply(ClientInfo* client, const uint8_t* secret,
                                uint8_t path_hash_size, uint32_t sender_timestamp,
                                const char* reply, const TransportKey* fallback_scope) {
  if (client == NULL || secret == NULL || reply == NULL) return;
  if (reply[0] == 0) reply = "OK";

  size_t text_len = strlen(reply);
  const size_t max_text_len =
      MAX_PACKET_PAYLOAD - CIPHER_MAC_SIZE - (CIPHER_BLOCK_SIZE - 1) - 5;
  if (text_len > max_text_len) text_len = max_text_len;

  uint32_t timestamp = getRTCClock()->getCurrentTimeUnique();
  if (timestamp == sender_timestamp) {
    // The two timestamps need to differ in the remote CLI view.
    timestamp++;
  }
  memcpy(reply_data, &timestamp, sizeof(timestamp));
  reply_data[4] = (TXT_TYPE_CLI_DATA << 2);
  if (reply != (const char*)&reply_data[5]) {
    memmove(&reply_data[5], reply, text_len);
  }

  mesh::Packet* packet = createDatagram(PAYLOAD_TYPE_TXT_MSG, client->id, secret,
                                        reply_data, 5 + text_len);
  sendClientReplyWithFallbackScope(client, packet, CLI_REPLY_DELAY_MILLIS,
                                   path_hash_size, fallback_scope);
}

#if defined(ESP32_PLATFORM) || defined(USER_GPIO_CONTROL)
void MyMesh::onUserGpioTimerCompleted(uint8_t pin, uint8_t state,
                                      uint32_t request_id) {
  int client_index;
  uint8_t path_hash_size;
  uint8_t client_tag[UserGpioReplyTracker::CLIENT_TAG_SIZE];
  if (!_gpio_reply_tracker.takeRoute(pin, request_id, client_index,
                                     path_hash_size, client_tag)) {
    MESH_DEBUG_PRINTLN("GPIO %u timer complete: %s", pin,
                       UserGpio::stateName((UserGpio::State)state));
    return;
  }
  ClientInfo* client = NULL;
  if (client_index >= 0 && client_index < acl.getNumClients()) {
    ClientInfo* indexed = acl.getClientByIdx(client_index);
    if (UserGpioReplyTracker::matchesClient(indexed->id.pub_key, client_tag)) {
      client = indexed;
    }
  }
  if (client == NULL) {
    for (int i = 0; i < acl.getNumClients(); i++) {
      ClientInfo* candidate = acl.getClientByIdx(i);
      if (UserGpioReplyTracker::matchesClient(candidate->id.pub_key, client_tag)) {
        client = candidate;
        break;
      }
    }
  }
  if (client == NULL) return;

  char reply[64];
  snprintf(reply, sizeof(reply), "> GPIO %u timer complete: %s", pin,
           UserGpio::stateName((UserGpio::State)state));
  sendRemoteCliReply(client, client->shared_secret, path_hash_size, request_id,
                     reply, NULL);
}
#endif

void __attribute__((noinline)) MyMesh::processDeferredCliCommand() {
  if (!deferred_cli_command.pending) return;

  const int client_index = deferred_cli_command.client_index;
  if (client_index < 0 || client_index >= acl.getNumClients()) {
    MESH_DEBUG_PRINTLN("processDeferredCliCommand: invalid client idx: %d", client_index);
    deferred_cli_command.clear();
    deferred_cli_reply_scoped = false;
    memset(deferred_cli_reply_scope.key, 0, sizeof(deferred_cli_reply_scope.key));
    return;
  }

  ClientInfo* client = acl.getClientByIdx(client_index);
  char* reply = (char*)&reply_data[5];
  reply[0] = 0;
  const uint32_t command_fingerprint =
      mesh::RemoteCliReplyCache::fingerprint(
          deferred_cli_command.command,
          strlen(deferred_cli_command.command));
  handleCommand(deferred_cli_command.sender_timestamp, client,
                deferred_cli_command.command, reply, client_index,
                deferred_cli_command.path_hash_size);
  remote_cli_reply_cache.remember(client->id.pub_key,
                                  deferred_cli_command.request_id,
                                  command_fingerprint, reply);
  sendRemoteCliReply(client, deferred_cli_command.secret,
                     deferred_cli_command.path_hash_size,
                     deferred_cli_command.sender_timestamp, reply,
                     deferred_cli_reply_scoped ? &deferred_cli_reply_scope : NULL);
  deferred_cli_command.clear();
  deferred_cli_reply_scoped = false;
  memset(deferred_cli_reply_scope.key, 0, sizeof(deferred_cli_reply_scope.key));
}

bool MyMesh::onPeerPathRecv(mesh::Packet *packet, int sender_idx, const uint8_t *secret, uint8_t *path,
                            uint8_t path_len, uint8_t extra_type, uint8_t *extra, uint8_t extra_len) {
  // TODO: prevent replay attacks
  int i = matching_peer_indexes[sender_idx];

  if (i >= 0 && i < acl.getNumClients()) { // get from our known_clients table (sender SHOULD already be known in this context)
    MESH_DEBUG_PRINTLN("PATH to client, path_len=%d", (uint32_t)path_len);
    auto client = acl.getClientByIdx(i);

    // store a copy of path, for sendDirect()
    if (client->out_path_len != OUT_PATH_FORCE_FLOOD) {
      client->out_path_len = mesh::Packet::copyPath(client->out_path, path, path_len);
    }
    client->last_activity = getRTCClock()->getCurrentTime();
  } else {
    MESH_DEBUG_PRINTLN("onPeerPathRecv: invalid peer idx: %d", i);
  }

  // NOTE: no reciprocal path send!!
  return false;
}

#define CTL_TYPE_NODE_DISCOVER_REQ   0x80
#define CTL_TYPE_NODE_DISCOVER_RESP  0x90

void MyMesh::onControlDataRecv(mesh::Packet* packet) {
  uint8_t type = packet->payload[0] & 0xF0;    // just test upper 4 bits
  if (type == CTL_TYPE_NODE_DISCOVER_REQ && packet->payload_len >= 6 && discover_limiter.allow(rtc_clock.getCurrentTime())) {
    int i = 1;
    uint8_t  filter = packet->payload[i++];
    uint32_t tag;
    memcpy(&tag, &packet->payload[i], 4); i += 4;
    uint32_t since;
    if (packet->payload_len >= i+4) {   // optional since field
      memcpy(&since, &packet->payload[i], 4); i += 4;
    } else {
      since = 0;
    }

    if ((filter & (1 << ADV_TYPE_REPEATER)) != 0 && _prefs.discovery_mod_timestamp >= since) {
      bool prefix_only = packet->payload[0] & 1;
      uint8_t data[6 + PUB_KEY_SIZE];
      data[0] = CTL_TYPE_NODE_DISCOVER_RESP | ADV_TYPE_REPEATER;   // low 4-bits for node type
      data[1] = packet->_snr;   // let sender know the inbound SNR ( x 4)
      memcpy(&data[2], &tag, 4);     // include tag from request, for client to match to
      memcpy(&data[6], self_id.pub_key, PUB_KEY_SIZE);
      auto resp = createControlData(data, prefix_only ? 6 + 8 : 6 + PUB_KEY_SIZE);
      if (resp) {
        sendZeroHop(resp, getRetransmitDelay(resp)*4);  // apply random delay (widened x4), as multiple nodes can respond to this
      }
    }
  } else if (type == CTL_TYPE_NODE_DISCOVER_RESP && packet->payload_len >= 6) {
    uint8_t node_type = packet->payload[0] & 0x0F;
    if (node_type != ADV_TYPE_REPEATER) {
      return;
    }
    if (packet->payload_len < 6 + PUB_KEY_SIZE) {
      MESH_DEBUG_PRINTLN("onControlDataRecv: DISCOVER_RESP pubkey too short: %d", (uint32_t)packet->payload_len);
      return;
    }

    if (pending_discover_tag == 0 || millisHasNowPassed(pending_discover_until)) {
      pending_discover_tag = 0;
      return;
    }
    uint32_t tag;
    memcpy(&tag, &packet->payload[2], 4);
    if (tag != pending_discover_tag) {
      return;
    }

    mesh::Identity id(&packet->payload[6]);
    if (id.matches(self_id)) {
      return;
    }
    putNeighbour(id, rtc_clock.getCurrentTime(), packet->getSNR());
  }
}

void MyMesh::sendNodeDiscoverReq() {
  uint8_t data[10];
  data[0] = CTL_TYPE_NODE_DISCOVER_REQ; // prefix_only=0
  data[1] = (1 << ADV_TYPE_REPEATER);
  getRNG()->random(&data[2], 4); // tag
  memcpy(&pending_discover_tag, &data[2], 4);
  pending_discover_until = futureMillis(60000);
  uint32_t since = 0;
  memcpy(&data[6], &since, 4);

  auto pkt = createControlData(data, sizeof(data));
  if (pkt) {
    sendZeroHop(pkt);
  }
}

MyMesh::MyMesh(mesh::MainBoard &board, mesh::Radio &radio, mesh::MillisecondClock &ms, mesh::RNG &rng,
               mesh::RTCClock &rtc, mesh::MeshTables &tables)
    : mesh::Mesh(radio, ms, rng, rtc, *createObserverPacketManager(32), tables),
      region_map(key_store), temp_map(key_store),
      _cli(board, rtc, sensors, region_map, acl, &_prefs, this),
      telemetry(MAX_PACKET_PAYLOAD - 4),
      discover_limiter(4, 120),  // max 4 every 2 minutes
      anon_limiter(4, 180)   // max 4 every 3 minutes
#if defined(WITH_MQTT_BRIDGE)
      , mqtt_bridge(nullptr)
#elif defined(WITH_RS232_BRIDGE)
      , bridge(&_prefs, WITH_RS232_BRIDGE, _mgr, &rtc)
#elif defined(WITH_ESPNOW_BRIDGE)
      , bridge(&_prefs, _mgr, &rtc)
#endif
{
  static_cast<StaticPoolPacketManager*>(_mgr)->setFloodScopePreference(
      scoreFloodTransportScope, this);
  last_millis = 0;
  uptime_millis = 0;
  next_local_advert = next_flood_advert = 0;
  deferred_cli_reply_scoped = false;
  memset(deferred_cli_reply_scope.key, 0, sizeof(deferred_cli_reply_scope.key));
  pending_self_advert_delay = 0;
  pending_self_advert = false;
  pending_self_advert_flood = false;
  next_battery_alert_check = 0;
  next_rx_watchdog_check = 0;
  next_recent_repeater_sweep = 0;
  last_battery_alert_sent = 0;
  pending_battery_alert_packet = NULL;
  battery_alert_sent = false;
  dirty_contacts_expiry = 0;
  active_bw = 0.0f;
  active_sf = 0;
  active_cr = 0;
  saved_radio_apply_pending = false;
  temp_radio_handoff_pending = false;
  temp_radio_applied = false;
  scheduled_temp_radio_started = false;
  next_scheduled_radio_time = 0;
  next_scheduled_radio_check_at = 0;
  scheduled_temp_radio_end_time = 0;
  scheduled_temp_radio_end_check_at = 0;
  scheduled_temp_radio_end_check_final = false;
  scheduled_radio_retry_at = 0;
  scheduled_radio_retry_failures = 0;
  memset(scheduled_radio_settings, 0, sizeof(scheduled_radio_settings));
  _logging = false;
  region_load_active = false;
  memset(flood_retry_bridge_states, 0, sizeof(flood_retry_bridge_states));
  memset(flood_retry_bridge_reachability, 0, sizeof(flood_retry_bridge_reachability));
  recv_pkt_region = NULL;
  recv_pkt_regionless_scope_set = false;
  recv_pkt_channel_scope_bypass = false;
  recv_pkt_channel_scope_rejected = false;
  recv_pkt_filter_match_mask = 0;
  memset(flood_packet_filters, 0, sizeof(flood_packet_filters));
  flood_packet_filter_blacklist_count = 0;
  memset(flood_packet_filter_blacklist, 0, sizeof(flood_packet_filter_blacklist));
  memset(flood_channel_scopes, 0, sizeof(flood_channel_scopes));
  memset(flood_channel_direct_scopes, 0,
         sizeof(flood_channel_direct_scopes));
  memset(flood_channel_scope_requirements, 0,
         sizeof(flood_channel_scope_requirements));
#if MESH_ENABLE_FLOOD_GROUP_MODERATION
  memset(flood_group_moderation, 0, sizeof(flood_group_moderation));
#endif
#if MESH_ENABLE_FLOOD_RULE_ENGINE
  flood_policy_has_embedded_sections = false;
  flood_channel_data_rule_slot = 0xFF;
  flood_channel_data_rule_max_hops = FLOOD_CHANNEL_HOPS_ALL;
#endif
  memset(clock_sync_samples, 0, sizeof(clock_sync_samples));
  clock_sync_mesh_enabled = CLOCK_SYNC_MESH_DEFAULT_ENABLED != 0;
  clock_sync_mesh_edge_enabled = CLOCK_SYNC_MESH_EDGE_DEFAULT_ENABLED != 0;
  clock_sync_internet_enabled = false;
  clock_sync_complete = false;
  clock_sync_internet_pending = false;
  clock_sync_force_mesh_pending = false;
  clock_sync_mesh_suppressed_by = CLOCK_SYNC_MESH_SUPPRESS_NONE;
  clock_sync_last_result = CLOCK_SYNC_RESULT_WAITING;
  clock_sync_last_source = CLOCK_SYNC_SOURCE_NONE;
  clock_sync_last_sample_count = 0;
  clock_sync_last_fresh_count = 0;
  clock_sync_last_required_count = CLOCK_SYNC_REQUIRED_SAMPLES_DEFAULT;
  clock_sync_required_samples = CLOCK_SYNC_REQUIRED_SAMPLES_DEFAULT;
  clock_sync_drift_seconds = CLOCK_SYNC_DRIFT_DEFAULT_SECONDS;
  clock_sync_last_estimate = 0;
  clock_sync_last_abs_drift = 0;
  clock_sync_internet_requested_millis = 0;
  clock_sync_next_attempt_uptime = CLOCK_SYNC_STARTUP_DELAY_MILLIS;

#if MESH_ENABLE_TELEMETRY_HISTORY
  telemetry_history_tx_enabled = false;
  memset(telemetry_history_tx_path, 0, sizeof(telemetry_history_tx_path));
  telemetry_history_tx_path_len = OUT_PATH_UNKNOWN;
  telemetry_history_tx_interval_days = TELEMETRY_HISTORY_TX_DEFAULT_DAYS;
  telemetry_history_tx_pending = 0;
  telemetry_history_next_tx_uptime = 0;
#endif

#if MAX_NEIGHBOURS
  memset(neighbours, 0, sizeof(neighbours));
#endif

  // defaults
  _prefs.airtime_factor = 1.0;
  _prefs.rx_delay_base = DEFAULT_RX_DELAY_BASE;   // fork kept this off by default (macro defaults 0.0f)
  _prefs.tx_delay_factor = 0.5f; // was 0.25f
  _prefs.direct_tx_delay_factor = 0.3f; // was 0.2
  StrHelper::strncpy(_prefs.node_name, ADVERT_NAME, sizeof(_prefs.node_name));
  _prefs.node_lat = ADVERT_LAT;
  _prefs.node_lon = ADVERT_LON;
  StrHelper::strncpy(_prefs.password, ADMIN_PASSWORD, sizeof(_prefs.password));
  _prefs.freq = LORA_FREQ;
  _prefs.sf = LORA_SF;
  _prefs.bw = LORA_BW;
  _prefs.cr = LORA_CR;
  _prefs.tx_power_dbm = LORA_TX_POWER;
  _prefs.advert_interval = DEFAULT_ADVERT_INTERVAL_MINUTES / 2;
  _prefs.flood_advert_interval = DEFAULT_FLOOD_ADVERT_INTERVAL_HOURS;
  _prefs.flood_max = 64;
  _prefs.flood_max_unscoped = 64;
  _prefs.flood_max_advert = 8;
  _prefs.interference_threshold = 0; // disabled
  _prefs.cad_enabled = DEFAULT_CAD_ENABLED; // Cascade defaults CAD on; target default remains off
  _prefs.agc_reset_interval = DEFAULT_AGC_RESET_INTERVAL_SECONDS / 4;
  _prefs.multi_acks = DEFAULT_MULTI_ACKS;
  _prefs.path_hash_mode = DEFAULT_PATH_HASH_MODE;
  _prefs.loop_detect = DEFAULT_LOOP_DETECT;
  _prefs.retry_preset = RETRY_PRESET_ROOFTOP;
  _prefs.direct_retry_attempts = DIRECT_RETRY_ROOFTOP_COUNT;
  _prefs.direct_retry_base_ms = DIRECT_RETRY_ROOFTOP_BASE_MS;
  _prefs.direct_retry_step_ms = DIRECT_RETRY_ROOFTOP_STEP_MS;
  _prefs.direct_retry_snr_margin_x4 = DIRECT_RETRY_ROOFTOP_MARGIN_X4;
  _prefs.direct_retry_cr4_snr_x4 = DIRECT_RETRY_CR4_MIN_SNR_X4_DEFAULT;
  _prefs.direct_retry_cr5_snr_x4 = DIRECT_RETRY_CR5_MIN_SNR_X4_DEFAULT;
  _prefs.direct_retry_cr7_snr_x4 = DIRECT_RETRY_CR7_MIN_SNR_X4_DEFAULT;
  _prefs.direct_retry_cr8_snr_x4 = DIRECT_RETRY_CR8_MAX_SNR_X4_DEFAULT;
  _prefs.direct_retry_enabled = 1;
  _prefs.direct_retry_cr_enabled = 1;
  _prefs.direct_retry_prefs_magic[0] = DIRECT_RETRY_PREFS_MAGIC_0;
  _prefs.direct_retry_prefs_magic[1] = DIRECT_RETRY_PREFS_MAGIC_1;
  _prefs.direct_retry_recent_enabled = DIRECT_RETRY_RECENT_DEFAULT;
  _prefs.flood_retry_attempts = FLOOD_RETRY_ROOFTOP_COUNT;
  _prefs.flood_retry_max_path = FLOOD_RETRY_ROOFTOP_MAX_PATH;
  _prefs.flood_retry_group_max_path = FLOOD_RETRY_GROUP_MAX_PATH_DEFAULT;
  _prefs.flood_retry_bridge_enabled = 0;
  _prefs.flood_retry_advert_enabled = FLOOD_RETRY_ADVERT_DEFAULT;
  _prefs.flood_channel_data_enabled = 1;
  _prefs.legacy_flood_channel_block_max_hops = FLOOD_CHANNEL_HOPS_ALL;
  _prefs.flood_channel_data_max_hops = FLOOD_CHANNEL_HOPS_ALL;
  _prefs.battery_alert_enabled = 0;
  _prefs.battery_alert_low_percent = BATTERY_ALERT_LOW_PERCENT_DEFAULT;
  _prefs.battery_alert_critical_percent = BATTERY_ALERT_CRITICAL_PERCENT_DEFAULT;
  _prefs.powersaving_enabled = DEFAULT_POWERSAVING_ENABLED ? 1 : 0;
#ifdef WITH_MQTT_BRIDGE
  _prefs.agc_reset_interval = 7;    // 28 seconds (secs/4) - prevents AGC drift on long-running observers
#endif
  // Observer defaults (radio_watchdog, alert.*, snmp.*) moved to applyMQTTDefaults()
  // in MQTTDefaults.h - they live in /mqtt_prefs now, not NodePrefs.
  _prefs.rx_powersaving_enabled = DEFAULT_RXPS_ENABLED ? 1 : 0;
  _prefs.rx_ps_level = DEFAULT_RXPS_LEVEL;
  _prefs.rx_ps_preamble = DEFAULT_RXPS_PREAMBLE;
  _prefs.rx_ps_rx_us = RX_POWERSAVING_DEFAULT_RX_US;
  _prefs.rx_ps_sleep_us = RX_POWERSAVING_DEFAULT_SLEEP_US;
  recalcRxPowerSavingFromLevel(_prefs.rx_ps_level, _prefs.sf, _prefs.bw,
                               _prefs.rx_ps_preamble, &_prefs.rx_ps_rx_us,
                               &_prefs.rx_ps_sleep_us);

  // bridge defaults
  _prefs.bridge_enabled = 1;    // enabled
  _prefs.bridge_delay   = 500;  // milliseconds
  _prefs.bridge_pkt_src = 1;    // logRx (RX packets)
  _prefs.bridge_baud = 115200;  // baud rate
  _prefs.bridge_channel = 1;    // channel 1

  StrHelper::strncpy(_prefs.bridge_secret, "LVSITANOS", sizeof(_prefs.bridge_secret));

  // GPS defaults
  _prefs.gps_enabled = 0;
  _prefs.gps_interval = 0;
  _prefs.advert_loc_policy = ADVERT_LOC_PREFS;

  // MQTT/WiFi/timezone/radio_watchdog defaults live in /mqtt_prefs now (see applyMQTTDefaults).

  _prefs.adc_multiplier = 0.0f; // 0.0f means use default board multiplier

#if defined(USE_SX1262) || defined(USE_SX1268) || defined(USE_LR1110) \
    || defined(USE_LR2021) || defined(SX126X_RX_BOOSTED_GAIN) \
    || defined(RX_BOOSTED_GAIN)
  _prefs.rx_boosted_gain = mesh::radio::configuredRxBoostedGainDefault();
#endif
  _prefs.radio_fem_rxgain = 1;      // LoRa FEM RX gain on by default (FEM boards)
  _prefs.radio_fem_txgain = 0;      // LoRa FEM TX gain off by default (FEM boards)

  pending_discover_tag = 0;
  pending_discover_until = 0;

#if defined(WITH_MQTT_NEIGHBORS)
  neighbor_discover_count = 0;
  neighbor_discover_next = 0;
  neighbor_discover_publish_count = 0;
  neighbor_discover_queried_count = 0;
  neighbor_discover_json_size = 0;
  neighbor_discover_truncated = false;
  neighbor_discover_active = false;
  neighbor_table_refresh_active = false;
  neighbor_table_refresh_periodic = false;
  neighbor_discover_until = 0;
  neighbor_discover_request = NULL;
  next_neighbors_publish = 0;
  self_scopes_buf[0] = 0;
  self_default_scope_buf[0] = 0;
  neighbor_discover_origin[0] = 0;
#endif

  memset(default_scope.key, 0, sizeof(default_scope.key));
}

// OTA mesh-integration (receive/begin/loop) is centralized in mesh::Mesh - no per-example wiring.

void MyMesh::begin(FILESYSTEM *fs) {
  mesh::Mesh::begin();   // also starts OTA (ota_ctx().begin) for all roles
  _fs = fs;
  // load persisted prefs
  _cli.loadPrefs(_fs);
#if MESH_ENABLE_TELEMETRY_HISTORY
  loadTelemetryHistoryTxPrefs();
#endif

#ifdef SIM_WIFI_SSID
  // Emulator builds (Wokwi) boot with fresh NVS every run. Seed WiFi so the
  // observer auto-joins the simulator's network and brings the MQTT bridge up
  // (WiFi is driven by the bridge task), instead of raising the setup AP that
  // the emulator can't model. No-op for real firmware (flag never defined).
  {
    MQTTPrefs* obs = _cli.getObserverPrefs();
    if (obs->wifi_ssid[0] == 0) {
      strncpy(obs->wifi_ssid, SIM_WIFI_SSID, sizeof(obs->wifi_ssid) - 1);
      obs->wifi_ssid[sizeof(obs->wifi_ssid) - 1] = 0;
  #ifdef SIM_WIFI_PWD
      strncpy(obs->wifi_password, SIM_WIFI_PWD, sizeof(obs->wifi_password) - 1);
      obs->wifi_password[sizeof(obs->wifi_password) - 1] = 0;
  #endif
      _prefs.bridge_enabled = 1;   // WiFi comes up via the MQTT bridge task
    }
  }
#endif

  acl.load(_fs, self_id);
  // TODO: key_store.begin();
  region_map.load(_fs);
#if !defined(PORTABLE_MQTT_OBSERVER)
#if MESH_ENABLE_FLOOD_RULE_ENGINE
  bool flood_filters_loaded = loadFloodPacketFilters();
  if (flood_filters_loaded) importLegacyFloodPolicySections();
#else
  loadFloodPacketFilterBlacklist();
  bool flood_filters_loaded = loadFloodPacketFilters();
  (void)flood_filters_loaded;
  loadFloodChannelScopes();
#endif
  loadFloodChannelScopeRequirements();
#if MESH_ENABLE_FLOOD_GROUP_MODERATION
  loadFloodGroupModeration();
#endif
#if MESH_ENABLE_CLOCK_SYNC
  loadClockSyncPrefs();
#endif
#endif

  // establish default-scope
  {
    RegionEntry* r = region_map.getDefaultRegion();
    if (r) {
      region_map.getTransportKeysFor(*r, &default_scope, 1);
    } else {
#ifdef DEFAULT_FLOOD_SCOPE_NAME
      r = region_map.findByName(DEFAULT_FLOOD_SCOPE_NAME);
      if (r == NULL) {
        r = region_map.putRegion(DEFAULT_FLOOD_SCOPE_NAME, 0);  // auto-create the default scope region
        if (r) { r->flags = 0; }   // Allow-flood
      }
      if (r) {
        region_map.setDefaultRegion(r);
        region_map.getTransportKeysFor(*r, &default_scope, 1);
      }
#endif
    }
  }

#if defined(WITH_BRIDGE)
  if (_prefs.bridge_enabled) {
#ifdef WITH_MQTT_BRIDGE
    // Defer construction to avoid static init crashes on ESP32 classic
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
#endif
    AbstractBridge* active_bridge = activeBridge();
    if (active_bridge) {
#ifdef WITH_MQTT_BRIDGE
      // Set device public key for MQTT topics
      char device_id[65];
      mesh::LocalIdentity self_id = getSelfId();
      mesh::Utils::toHex(device_id, self_id.pub_key, PUB_KEY_SIZE);
      MESH_DEBUG_PRINTLN("Setting device ID: %s", device_id);
      mqtt_bridge->setDeviceID(device_id);

      // Set firmware version
      mqtt_bridge->setFirmwareVersion(getFirmwareVer());

      // Set board model
      mqtt_bridge->setBoardModel(_cli.getBoard()->getManufacturerName());

      // Set build date
      mqtt_bridge->setBuildDate(getBuildDate());

      // Set stats sources for automatic stats collection
      mqtt_bridge->setStatsSources(this, _radio, _cli.getBoard(), _ms);
#ifdef WITH_SNMP
      if (_cli.getObserverPrefs()->snmp_enabled) {
        _snmp_agent.setNodeName(_prefs.node_name);
        _snmp_agent.setFirmwareVersion(getFirmwareVer());
        mqtt_bridge->setSNMPAgent(&_snmp_agent);
      }
#endif
#endif

      active_bridge->begin();
    }
  }
#endif

  // Wire fault-alert reporter. begin() is safe regardless of bridge state.
  // Passing `this` as the callbacks lets the reporter resolve a TransportKey
  // scope (alert.region override, falling back to default_scope) so alert
  // floods ride the same scope as adverts/channel messages.
#ifdef WITH_MQTT_BRIDGE
  _alerter.begin(&_prefs, _cli.getObserverPrefs(), this, this);
  _alerter.setBridge(mqtt_bridge);
#endif

#if defined(WITH_WEBCONFIG) && !defined(WEBCONFIG_NO_AUTO_AP)
  bool start_webui = WebConfigServer::loadEnabled(false);
#ifdef WITH_MQTT_BRIDGE
  // Preserve the MQTT observer's first-boot setup experience even though the
  // persistent WebUI master switch defaults off on infrastructure roles.
  start_webui = start_webui || _cli.getObserverPrefs()->wifi_ssid[0] == 0;
  if (start_webui && _cli.getObserverPrefs()->wifi_ssid[0] == 0) {
    if (mqtt_bridge && mqtt_bridge->isRunning()) mqtt_bridge->end();
  }
#endif
  if (start_webui) {
    char wc_reply[160];
    startWebConfig(false, wc_reply);
    Serial.println(wc_reply);
  }
#endif

  saved_radio_apply_pending = !applySavedRadioParams();
  if (!saved_radio_apply_pending) {
    radio_driver.setTxPower(_prefs.tx_power_dbm);
    radio_driver.setRxBoostedGainMode(_prefs.rx_boosted_gain);
  }
  MESH_DEBUG_PRINTLN("RX Boosted Gain Mode: %s",
                     radio_driver.getRxBoostedGainMode() ? "Enabled" : "Disabled");
  const bool fem_gain_changed = board.canControlLoRaFemLna()
      && board.isLoRaFemLnaEnabled() != (_prefs.radio_fem_rxgain != 0);
  if (board.setLoRaFemLnaEnabled(_prefs.radio_fem_rxgain) && fem_gain_changed) {
    _radio->recalibrateNoiseFloor();
  }
  board.setLoRaFemPaGainEnabled(_prefs.radio_fem_txgain);
  setRxPowerSaving(_prefs.rx_powersaving_enabled, _prefs.rx_ps_rx_us, _prefs.rx_ps_sleep_us);

  updateAdvertTimer();
  updateFloodAdvertTimer();
  next_recent_repeater_sweep = futureMillis(RECENT_REPEATER_SWEEP_INTERVAL_MILLIS);

#if ENV_INCLUDE_GPS == 1
  applyGpsPrefs();
#if MESH_ENABLE_TELEMETRY_HISTORY
  if (sensors.getLocationProvider() != NULL) {
    const uint8_t gps_days = resizeTelemetryGpsDays(7);
    MESH_DEBUG_PRINTLN("Telemetry GPS retention: %u days", (unsigned)gps_days);
  }
#endif
#endif
}

bool MyMesh::sendFloodScoped(const TransportKey& scope, mesh::Packet* pkt, uint32_t delay_millis, uint8_t path_hash_size) {
  if (scope.isNull()) {
    return sendFlood(pkt, delay_millis, path_hash_size);
  } else {
    uint16_t codes[2];
    codes[0] = scope.calcTransportCode(pkt);
    codes[1] = 0;  // REVISIT: set to 'home' Region, for sender/return region?
    return sendFlood(pkt, codes, delay_millis, path_hash_size);
  }
}

bool MyMesh::resolveAlertScope(TransportKey& dest) {
  // Prefer an explicit alert.region override; look it up lazily via
  // RegionMap so the operator can name a region that doesn't exist yet
  // without polluting region_map state - we just silently fall through
  // to default_scope on miss.
#ifdef WITH_MQTT_BRIDGE
  const char* alert_region = _cli.getObserverPrefs()->alert_region;
  if (alert_region[0]) {
    auto r = region_map.findByNamePrefix(alert_region);
    if (r && region_map.getTransportKeysFor(*r, &dest, 1) > 0 && !dest.isNull()) {
      return true;
    }
  }
#endif
  if (!default_scope.isNull()) {
    dest = default_scope;
    return true;
  }
  return false;
}

uint8_t MyMesh::getRegionDepth(const RegionEntry* region) {
  if (region == NULL || region->isWildcard()) return 0;
  uint8_t depth = 1;
  uint16_t parent_id = region->parent;
  const int region_count = region_map.getCount();
  for (int hops = 0; parent_id != 0; hops++) {
    if (hops >= region_count) return 0;
    const RegionEntry* parent = region_map.findById(parent_id);
    if (parent == NULL || parent->isWildcard()) return 0;
    depth++;
    parent_id = parent->parent;
  }
  return depth;
}

const RegionEntry* MyMesh::findNarrowestBatteryAlertRegion(bool& ambiguous) {
  ambiguous = false;
  const RegionEntry* narrowest = NULL;
  uint8_t narrowest_depth = 0;
  const int region_count = region_map.getCount();

  for (int i = 0; i < region_count; i++) {
    const RegionEntry* candidate = region_map.getByIdx(i);
    // Bound the shared hierarchy walk so an unsaved temporary cycle cannot
    // hang either alert selection or rxdelay scope arbitration.
    uint8_t depth = getRegionDepth(candidate);
    if (depth == 0) continue;

    // A named region is not necessarily a usable transport scope. In
    // particular, private regions need a stored key; do not select one merely
    // because it happens to be the deepest entry in the hierarchy.
    TransportKey candidate_scope;
    if (!getBatteryAlertScopeForRegion(*candidate, candidate_scope)) continue;

    if (depth > narrowest_depth) {
      narrowest = candidate;
      narrowest_depth = depth;
      ambiguous = false;
    } else if (depth == narrowest_depth) {
      ambiguous = true;
    }
  }
  return narrowest;
}

bool MyMesh::getBatteryAlertScopeForRegion(const RegionEntry& region, TransportKey& scope) {
  if (region.isWildcard()) return false;
  return region_map.getTransportKeysFor(region, &scope, 1) > 0 && !scope.isNull();
}

bool MyMesh::resolveBatteryAlertScope(TransportKey& scope) {
  if (_prefs.battery_alert_region[0] == 0) return false;

  const RegionEntry* region = region_map.findByName(_prefs.battery_alert_region);
  return region != NULL && getBatteryAlertScopeForRegion(*region, scope);
}

bool MyMesh::sendRepeatersFloodText(const char* text, const TransportKey* scope,
                                    mesh::Packet** queued_packet) {
  if (queued_packet != NULL) *queued_packet = NULL;
  if (text == NULL || *text == 0) return false;

  mesh::GroupChannel channel;
  if (!buildRepeatersChannel(channel)) {
    return false;
  }

  uint8_t temp[MAX_PACKET_PAYLOAD];
  uint32_t timestamp = getRTCClock()->getCurrentTimeUnique();
  memcpy(temp, &timestamp, 4);
  temp[4] = (TXT_TYPE_PLAIN << 2);

  const size_t max_data_len = MAX_PACKET_PAYLOAD - CIPHER_BLOCK_SIZE;
  const size_t prefix_cap = max_data_len > 5 ? max_data_len - 5 + 1 : 0;
  char node_name[sizeof(_prefs.node_name)];
  StrHelper::strncpy(node_name, _prefs.node_name, sizeof(node_name));
  for (char* p = node_name; *p; p++) {
    if (*p == ':') *p = ';';
  }
  int prefix_written = prefix_cap > 0
      ? snprintf((char*)&temp[5], prefix_cap, "%s: ", node_name)
      : -1;
  if (prefix_written < 0) {
    return false;
  }

  size_t prefix_len = (size_t)prefix_written;
  if (prefix_len >= prefix_cap) {
    prefix_len = prefix_cap - 1;
  }

  size_t text_len = strlen(text);
  size_t max_text_len = max_data_len - 5 - prefix_len;
  if (text_len > max_text_len) {
    text_len = max_text_len;
  }
  memcpy(&temp[5 + prefix_len], text, text_len);

  auto pkt = createGroupDatagram(PAYLOAD_TYPE_GRP_TXT, channel, temp, 5 + prefix_len + text_len);
  if (pkt == NULL) {
    return false;
  }

  const TransportKey& send_scope = scope == NULL ? default_scope : *scope;
  if (!sendFloodScoped(send_scope, pkt, 0, _prefs.path_hash_mode + 1)) {
    return false;
  }
  if (queued_packet != NULL) *queued_packet = pkt;
  return true;
}

void MyMesh::onSendComplete(mesh::Packet* packet) {
  mesh::Mesh::onSendComplete(packet);
  if (packet == pending_battery_alert_packet) {
    pending_battery_alert_packet = NULL;
    battery_alert_sent = true;
    last_battery_alert_sent = uptime_millis + (uint32_t)(millis() - last_millis);
  }
}

void MyMesh::onSendFail(mesh::Packet* packet) {
  mesh::Mesh::onSendFail(packet);
  if (packet == pending_battery_alert_packet) {
    pending_battery_alert_packet = NULL;
  }
}

void MyMesh::checkBatteryAlert() {
  if (!_prefs.battery_alert_enabled) {
    return;
  }

  if (next_battery_alert_check && !millisHasNowPassed(next_battery_alert_check)) {
    return;
  }

  const uint64_t current_uptime_millis =
      uptime_millis + (uint32_t)(millis() - last_millis);

  // Ignore startup voltage sag and give solar/charger hardware time to settle.
  // uptime_millis is 64-bit and includes time spent in the platform's light or
  // event sleep, so this guard remains reliable across millis() wraparound.
  // Arm the remaining startup delay once so subsequent loops use the cheaper
  // 32-bit deadline check above and powersaving can include it as a wake limit.
  if (current_uptime_millis < LOW_BATTERY_STARTUP_DELAY) {
    next_battery_alert_check = futureMillis(
        (unsigned long)(LOW_BATTERY_STARTUP_DELAY - current_uptime_millis));
    return;
  }

  next_battery_alert_check = futureMillis(LOW_BATTERY_CHECK_INTERVAL);

  // Only a completed over-the-air transmission starts the fixed cooldown.
  // A queued packet remains tracked until Dispatcher reports success/failure.
  if (pending_battery_alert_packet != NULL) {
    return;
  }

  // Do this before region resolution and ADC sampling so repeat checks during
  // the cooldown stay cheap. Battery recovery and alert toggles must not bypass
  // the cooldown within this boot.
  if (battery_alert_sent) {
    if (current_uptime_millis - last_battery_alert_sent < LOW_BATTERY_ALERT_INTERVAL) {
      return;
    }
    battery_alert_sent = false;
  }

  // Check the cheap configuration path first. This avoids powering the ADC or
  // battery-divider circuitry when the selected region has been removed or no
  // longer has a usable transport key.
  TransportKey alert_scope;
  if (!resolveBatteryAlertScope(alert_scope)) {
    return;  // low-battery alerts are never sent as an unscoped flood
  }

  uint16_t batt_mv = board.getBattMilliVolts();
  uint8_t batt_pct = batteryPercentFromMilliVolts(batt_mv);
  if (batt_mv <= LOW_BATTERY_MIN_VALID_MV || batt_pct >= _prefs.battery_alert_low_percent) {
    return;
  }

  char text[96];
  const char* severity = batt_pct <= _prefs.battery_alert_critical_percent
    ? "CRITICAL BATTERY"
    : "LOW BATTERY";
  snprintf(text, sizeof(text), "%s %u%% (%u mV)", severity, (uint32_t)batt_pct, (uint32_t)batt_mv);
  mesh::Packet* queued_packet = NULL;
  if (sendRepeatersFloodText(text, &alert_scope, &queued_packet)) {
    pending_battery_alert_packet = queued_packet;
  }
}

void MyMesh::checkRxInactivityWatchdog() {
  if (!_prefs.rx_watchdog_enabled) {
    next_rx_watchdog_check = 0;
    return;
  }

  if (next_rx_watchdog_check == 0) {
    next_rx_watchdog_check = futureMillis(RX_INACTIVITY_WATCHDOG_INTERVAL);
    if (next_rx_watchdog_check == 0) next_rx_watchdog_check = 1;
    return;
  }
  if (!millisHasNowPassed(next_rx_watchdog_check)) {
    return;
  }

  next_rx_watchdog_check = futureMillis(RX_INACTIVITY_WATCHDOG_INTERVAL);
  if (next_rx_watchdog_check == 0) next_rx_watchdog_check = 1;

  const unsigned long now = millis();
  const unsigned long last_rx = _radio->getLastRecvMillis();
  if (last_rx == 0 || (uint32_t)(now - last_rx) >= RX_INACTIVITY_WATCHDOG_INTERVAL) {
    MESH_DEBUG_PRINTLN("RX watchdog: no packet received in 12 hours, rebooting");
    _cli.getBoard()->reboot();
  }
}

bool MyMesh::applyRadioParams(float freq, float bw, uint8_t sf, uint8_t cr) {
  uint32_t rx_us = _prefs.rx_ps_rx_us;
  uint32_t sleep_us = _prefs.rx_ps_sleep_us;
  if (_prefs.rx_powersaving_enabled && _prefs.rx_ps_level != 0) {
    uint32_t preamble = _prefs.rx_ps_preamble ? _prefs.rx_ps_preamble : (sf <= 8 ? 32UL : 16UL);
    if (!CommonCLI::calculateRxPowerSavingLevel(
          _prefs.rx_ps_level, sf, bw, preamble, &rx_us, &sleep_us)) return false;
  }
  uint32_t timings[2] = {rx_us, sleep_us};
  const uint32_t* applied_timings = _prefs.rx_powersaving_enabled
      && radio_driver.supportsRxPowerSaving() ? timings : NULL;
  if (!radio_driver.setParams(freq, bw, sf, cr,
                              applied_timings)) {
    MESH_DEBUG_PRINTLN("Radio schedule: radio busy or parameter apply failed");
    return false;
  }
  active_bw = bw;
  active_sf = sf;
  active_cr = cr;
  return true;
}

bool MyMesh::applySavedRadioParams() {
#if defined(USE_LR2021)
  uint8_t extra_sf_count = 0;
  const bool extra_sf_valid = mesh::lr2021::storedSideDetectorCount(
      _prefs.extra_sf, extra_sf_count)
      && mesh::lr2021::validateSideDetectorSFs(
          _prefs.extra_sf, extra_sf_count, _prefs.sf, _prefs.bw);
  if (!extra_sf_valid) {
    // A permanent radio-profile change can make the old detector list invalid.
    // Clear the live/cache state before applying the new primary modulation so
    // the radio is not trapped retrying an impossible combination forever.
    if (!radio_driver.configSideDetectors(nullptr, 0, _prefs.bw)) return false;
    memset(_prefs.extra_sf, 0, sizeof(_prefs.extra_sf));
    extra_sf_count = 0;
    savePrefs();
  }
#endif

  if (!applyRadioParams(_prefs.freq, _prefs.bw, _prefs.sf, _prefs.cr)) return false;

#if defined(USE_LR2021)
  return radio_driver.configSideDetectors(_prefs.extra_sf, extra_sf_count, _prefs.bw);
#else
  return true;
#endif
}

void MyMesh::queueSavedRadioApply() {
  saved_radio_apply_pending = true;
  scheduled_radio_retry_at = 0;
  scheduled_radio_retry_failures = 0;
}

void MyMesh::refreshScheduledRadioState() {
  next_scheduled_radio_time = 0;
  scheduled_temp_radio_started = false;
  scheduled_temp_radio_end_time = 0;
  for (int i = 0; i < MAX_SCHEDULED_RADIO_SETTINGS; i++) {
    const ScheduledRadioSetting& setting = scheduled_radio_settings[i];
    if (!setting.active) continue;

    uint32_t deadline = setting.start_time;
    if (setting.temporary && setting.started) {
      scheduled_temp_radio_started = true;
      if (scheduled_temp_radio_end_time == 0 || setting.end_time < scheduled_temp_radio_end_time) {
        scheduled_temp_radio_end_time = setting.end_time;
      }
      deadline = setting.end_time;
    }
    if (next_scheduled_radio_time == 0 || deadline < next_scheduled_radio_time) {
      next_scheduled_radio_time = deadline;
    }
  }
  const uint32_t now = (next_scheduled_radio_time != 0 || scheduled_temp_radio_end_time != 0)
      ? getRTCClock()->getCurrentTime()
      : 0;
  if (next_scheduled_radio_time != 0) {
    uint32_t delay_ms = 0;
    if (next_scheduled_radio_time > now) {
      uint32_t delay_secs = next_scheduled_radio_time - now;
      // millis timers are only unambiguous for half of their rollover range.
      // A minute checkpoint handles RTC corrections without per-loop RTC reads
      // or table scans, while bounding a forward clock-sync delay to one minute.
      if (delay_secs > SCHEDULED_RADIO_CLOCK_CHECKPOINT_SECS) {
        delay_secs = SCHEDULED_RADIO_CLOCK_CHECKPOINT_SECS;
      }
      delay_ms = delay_secs * 1000UL;
    }
    next_scheduled_radio_check_at = futureMillis(delay_ms);
  } else {
    next_scheduled_radio_check_at = 0;
  }
  if (scheduled_temp_radio_end_time != 0) {
    uint32_t delay_ms = 0;
    scheduled_temp_radio_end_check_final = true;
    if (scheduled_temp_radio_end_time > now) {
      uint32_t delay_secs = scheduled_temp_radio_end_time - now;
      if (delay_secs > SCHEDULED_RADIO_CLOCK_CHECKPOINT_SECS) {
        delay_secs = SCHEDULED_RADIO_CLOCK_CHECKPOINT_SECS;
        scheduled_temp_radio_end_check_final = false;
      }
      delay_ms = delay_secs * 1000UL;
    }
    scheduled_temp_radio_end_check_at = futureMillis(delay_ms);
  } else {
    scheduled_temp_radio_end_check_at = 0;
    scheduled_temp_radio_end_check_final = false;
  }
  if (next_scheduled_radio_time == 0 && !saved_radio_apply_pending) {
    scheduled_radio_retry_at = 0;
    scheduled_radio_retry_failures = 0;
  }
}

bool MyMesh::hasStartedScheduledTempRadio() const {
  return scheduled_temp_radio_started;
}

bool MyMesh::isTempRadioActive() const {
  return temp_radio_applied;
}

int MyMesh::findFreeScheduledRadioSlot() const {
  for (int i = 0; i < MAX_SCHEDULED_RADIO_SETTINGS; i++) {
    if (!scheduled_radio_settings[i].active) {
      return i;
    }
  }
  return -1;
}

int MyMesh::countScheduledRadioSettings(bool temporary) const {
  int count = 0;
  for (int i = 0; i < MAX_SCHEDULED_RADIO_SETTINGS; i++) {
    const ScheduledRadioSetting& setting = scheduled_radio_settings[i];
    if (setting.active && setting.temporary == temporary) {
      count++;
    }
  }
  return count;
}

int MyMesh::findScheduledRadioSettingByIndex(bool temporary, int wanted) const {
  bool used[MAX_SCHEDULED_RADIO_SETTINGS] = {};
  for (int rank = 1; rank <= wanted; rank++) {
    int best = -1;
    for (int i = 0; i < MAX_SCHEDULED_RADIO_SETTINGS; i++) {
      const ScheduledRadioSetting& setting = scheduled_radio_settings[i];
      if (!setting.active || setting.temporary != temporary || used[i]) {
        continue;
      }
      if (best < 0 || setting.start_time < scheduled_radio_settings[best].start_time
          || (setting.start_time == scheduled_radio_settings[best].start_time && i < best)) {
        best = i;
      }
    }
    if (best < 0) {
      return -1;
    }
    used[best] = true;
    if (rank == wanted) {
      return best;
    }
  }
  return -1;
}

int MyMesh::getScheduledRadioSettingIndex(bool temporary, int slot_idx) const {
  int count = countScheduledRadioSettings(temporary);
  for (int i = 1; i <= count; i++) {
    if (findScheduledRadioSettingByIndex(temporary, i) == slot_idx) {
      return i;
    }
  }
  return -1;
}

bool MyMesh::scheduledRadioConflicts(bool temporary, uint32_t start_time, uint32_t end_time) const {
  for (int i = 0; i < MAX_SCHEDULED_RADIO_SETTINGS; i++) {
    const ScheduledRadioSetting& setting = scheduled_radio_settings[i];
    if (!setting.active) {
      continue;
    }
    if (temporary) {
      if (setting.temporary && start_time < setting.end_time && end_time > setting.start_time) {
        return true;
      }
      if (!setting.temporary && setting.start_time >= start_time && setting.start_time < end_time) {
        return true;
      }
    } else {
      if (!setting.temporary && setting.start_time == start_time) {
        return true;
      }
      if (setting.temporary && start_time >= setting.start_time && start_time < setting.end_time) {
        return true;
      }
    }
  }
  return false;
}

void MyMesh::clearScheduledRadioSetting(int idx, bool restore_if_started) {
  if (idx < 0 || idx >= MAX_SCHEDULED_RADIO_SETTINGS) {
    return;
  }
  bool restore_radio = restore_if_started
      && scheduled_radio_settings[idx].active
      && scheduled_radio_settings[idx].temporary
      && scheduled_radio_settings[idx].started;
  scheduled_radio_settings[idx].active = false;
  scheduled_radio_settings[idx].started = false;
  refreshScheduledRadioState();
  if (scheduled_radio_settings[idx].temporary && temp_radio_handoff_pending
      && countScheduledRadioSettings(true) == 0) {
    temp_radio_handoff_pending = false;
    queueSavedRadioApply();
  }
  if ((restore_radio || saved_radio_apply_pending) && !hasStartedScheduledTempRadio()) {
    queueSavedRadioApply();
  }
}

void MyMesh::formatScheduledRadioDuration(char* dest, size_t dest_len, uint32_t target_time) const {
  uint32_t now = getRTCClock()->getCurrentTime();
  if (target_time <= now) {
    StrHelper::strncpy(dest, "now", dest_len);
    return;
  }

  uint32_t seconds = target_time - now;
  uint32_t days = seconds / 86400;
  seconds %= 86400;
  uint32_t hours = seconds / 3600;
  seconds %= 3600;
  uint32_t minutes = seconds / 60;
  seconds %= 60;

  if (days > 0) {
    snprintf(dest, dest_len, "%lud%luh", (unsigned long)days, (unsigned long)hours);
  } else if (hours > 0) {
    snprintf(dest, dest_len, "%luh%lum", (unsigned long)hours, (unsigned long)minutes);
  } else if (minutes > 0) {
    snprintf(dest, dest_len, "%lum%lus", (unsigned long)minutes, (unsigned long)seconds);
  } else {
    snprintf(dest, dest_len, "%lus", (unsigned long)seconds);
  }
}

void MyMesh::formatRadioParamTuple(char* dest, size_t dest_len, const ScheduledRadioSetting& setting) const {
  char freq[16];
  char bw[16];
  formatFixed3(freq, sizeof(freq), setting.freq);
  StrHelper::strncpy(bw, StrHelper::ftoa3(setting.bw), sizeof(bw));
  snprintf(dest, dest_len, "%s,%s,%u,%u", freq, bw, (uint32_t)setting.sf, (uint32_t)setting.cr);
}

void MyMesh::formatScheduledRadioSetting(char* reply, int setting_idx, int display_idx) const {
  const ScheduledRadioSetting& setting = scheduled_radio_settings[setting_idx];
  char params[40];
  char delay[16];
  formatRadioParamTuple(params, sizeof(params), setting);

  if (setting.temporary) {
    if (setting.started) {
      formatScheduledRadioDuration(delay, sizeof(delay), setting.end_time);
      snprintf(reply, 160, "> %d:%s@%lu-%lu active ends in %s",
               display_idx,
               params,
               (unsigned long)setting.start_time,
               (unsigned long)setting.end_time,
               delay);
    } else {
      formatScheduledRadioDuration(delay, sizeof(delay), setting.start_time);
      snprintf(reply, 160, "> %d:%s@%lu-%lu starts in %s",
               display_idx,
               params,
               (unsigned long)setting.start_time,
               (unsigned long)setting.end_time,
               delay);
    }
  } else {
    formatScheduledRadioDuration(delay, sizeof(delay), setting.start_time);
    snprintf(reply, 160, "> %d:%s@%lu in %s",
             display_idx,
             params,
             (unsigned long)setting.start_time,
             delay);
  }
}

void MyMesh::addScheduledRadioParams(bool temporary, float freq, float bw, uint8_t sf, uint8_t cr,
                                     uint32_t start_time, uint32_t end_time, char* reply) {
  uint32_t now = getRTCClock()->getCurrentTime();
  if (!isValidScheduledRadioParams(freq, bw, sf, cr)) {
    strcpy(reply, "Error, invalid radio params");
    return;
  }
  if (start_time <= now) {
    strcpy(reply, "Error: start is in the past");
    return;
  }
  if (temporary && end_time <= now) {
    strcpy(reply, "Error: end is in the past");
    return;
  }
  if (temporary && end_time <= start_time) {
    strcpy(reply, "Error: end must be after start");
    return;
  }
  if (countScheduledRadioSettings(temporary) >= MAX_SCHEDULED_RADIO_SETTINGS_PER_TYPE) {
    snprintf(reply, 160, "Error: max %d queued", MAX_SCHEDULED_RADIO_SETTINGS_PER_TYPE);
    return;
  }
  if (scheduledRadioConflicts(temporary, start_time, end_time)) {
    strcpy(reply, "Error: schedule conflict");
    return;
  }

  int slot = findFreeScheduledRadioSlot();
  if (slot < 0) {
    strcpy(reply, "Error: queue full");
    return;
  }

  scheduled_radio_settings[slot].active = true;
  scheduled_radio_settings[slot].temporary = temporary;
  scheduled_radio_settings[slot].started = false;
  scheduled_radio_settings[slot].freq = freq;
  scheduled_radio_settings[slot].bw = bw;
  scheduled_radio_settings[slot].sf = sf;
  scheduled_radio_settings[slot].cr = cr;
  scheduled_radio_settings[slot].start_time = start_time;
  scheduled_radio_settings[slot].end_time = temporary ? end_time : 0;
  // A newly requested schedule must not inherit the backoff of an older radio
  // apply failure, especially when its deadline is sooner than that retry.
  scheduled_radio_retry_at = 0;
  scheduled_radio_retry_failures = 0;
  refreshScheduledRadioState();

  char delay[16];
  formatScheduledRadioDuration(delay, sizeof(delay), start_time);
  snprintf(reply, 160, "OK - %s %d in %s",
           temporary ? "tempradioat" : "radioat",
           getScheduledRadioSettingIndex(temporary, slot),
           delay);
}

void MyMesh::formatScheduledRadioParams(bool temporary, const char* selector, char* reply) {
  if (selectorIsEmpty(selector) || selectorIsAll(selector)) {
    int count = countScheduledRadioSettings(temporary);
    if (count == 0) {
      strcpy(reply, "> -none-");
      return;
    }

    int len = snprintf(reply, 160, "> ");
    for (int display_idx = 1; display_idx <= count && len < 159; display_idx++) {
      int idx = findScheduledRadioSettingByIndex(temporary, display_idx);
      if (idx < 0) {
        break;
      }
      char params[40];
      formatRadioParamTuple(params, sizeof(params), scheduled_radio_settings[idx]);
      int written;
      if (temporary) {
        written = snprintf(&reply[len], 160 - len, "%s%d:%s@%lu-%lu",
                           display_idx == 1 ? "" : " ",
                           display_idx,
                           params,
                           (unsigned long)scheduled_radio_settings[idx].start_time,
                           (unsigned long)scheduled_radio_settings[idx].end_time);
      } else {
        written = snprintf(&reply[len], 160 - len, "%s%d:%s@%lu",
                           display_idx == 1 ? "" : " ",
                           display_idx,
                           params,
                           (unsigned long)scheduled_radio_settings[idx].start_time);
      }
      if (written < 0 || written >= 160 - len) {
        reply[159] = 0;
        break;
      }
      len += written;
    }
    return;
  }

  int wanted = 0;
  if (!parsePositiveSelector(selector, wanted)) {
    strcpy(reply, temporary ? "Error, use: get tempradioat [n]" : "Error, use: get radioat [n]");
    return;
  }

  int idx = findScheduledRadioSettingByIndex(temporary, wanted);
  if (idx < 0) {
    strcpy(reply, "Error: not found");
    return;
  }
  formatScheduledRadioSetting(reply, idx, wanted);
}

void MyMesh::deleteScheduledRadioParams(bool temporary, const char* selector, char* reply) {
  if (selectorIsEmpty(selector) || selectorIsAll(selector)) {
    int deleted = 0;
    bool restore_radio = false;
    for (int i = 0; i < MAX_SCHEDULED_RADIO_SETTINGS; i++) {
      ScheduledRadioSetting& setting = scheduled_radio_settings[i];
      if (setting.active && setting.temporary == temporary) {
        restore_radio = restore_radio || (setting.temporary && setting.started);
        setting.active = false;
        setting.started = false;
        deleted++;
      }
    }
    refreshScheduledRadioState();
    if ((restore_radio || saved_radio_apply_pending) && !hasStartedScheduledTempRadio()) {
      queueSavedRadioApply();
    }
    if (temporary && temp_radio_handoff_pending) {
      temp_radio_handoff_pending = false;
      queueSavedRadioApply();
    }
    snprintf(reply, 160, "OK - deleted %d", deleted);
    return;
  }

  int wanted = 0;
  if (!parsePositiveSelector(selector, wanted)) {
    strcpy(reply, temporary ? "Error, use: del tempradioat [n]" : "Error, use: del radioat [n]");
    return;
  }

  int idx = findScheduledRadioSettingByIndex(temporary, wanted);
  if (idx < 0) {
    strcpy(reply, "Error: not found");
    return;
  }
  clearScheduledRadioSetting(idx, true);
  strcpy(reply, "OK");
}

void MyMesh::processScheduledRadioSettings() {
  if (scheduled_radio_retry_at && !millisHasNowPassed(scheduled_radio_retry_at)) return;

  const bool schedule_check_due = next_scheduled_radio_time != 0
      && (next_scheduled_radio_check_at == 0
          || millisHasNowPassed(next_scheduled_radio_check_at));
  uint32_t now = 0;
  bool schedule_due = false;
  if (schedule_check_due) {
    now = getRTCClock()->getCurrentTime();
    schedule_due = now >= next_scheduled_radio_time;
    if (!schedule_due) refreshScheduledRadioState();
  }
  bool saved_apply_due = saved_radio_apply_pending && !temp_radio_handoff_pending
      && !scheduled_temp_radio_started;
  if (!schedule_due && !saved_apply_due) return;

  // Never touch modulation registers while a packet is still on air. Back off
  // this check too; a long packet should not make the scheduler poll every loop.
  if (hasOutbound()) {
    scheduled_radio_retry_at = futureMillis(RADIO_APPLY_RETRY_INTERVAL_MILLIS);
    return;
  }

  bool apply_failed = false;
  bool saved_params_changed = false;

  while (schedule_due) {
    int due_idx = -1;
    for (int i = 0; i < MAX_SCHEDULED_RADIO_SETTINGS; i++) {
      const ScheduledRadioSetting& setting = scheduled_radio_settings[i];
      if (!setting.active || setting.temporary || now < setting.start_time) {
        continue;
      }
      if (due_idx < 0 || setting.start_time < scheduled_radio_settings[due_idx].start_time
          || (setting.start_time == scheduled_radio_settings[due_idx].start_time && i < due_idx)) {
        due_idx = i;
      }
    }
    if (due_idx < 0) {
      break;
    }

    ScheduledRadioSetting& setting = scheduled_radio_settings[due_idx];
    _prefs.freq = setting.freq;
    _prefs.bw = setting.bw;
    _prefs.sf = setting.sf;
    _prefs.cr = setting.cr;
    setting.active = false;
    setting.started = false;
    saved_params_changed = true;
  }

  if (saved_params_changed) {
    // Keep level-derived RX duty-cycle windows synchronized with the newly
    // persisted SF/BW. Manual RX/sleep timings intentionally remain fixed.
    CommonCLI::recalculateRxPowerSavingFromLevel(&_prefs);
    savePrefs();
    queueSavedRadioApply();
  }

  if (schedule_due) {
    for (int i = 0; i < MAX_SCHEDULED_RADIO_SETTINGS; i++) {
      ScheduledRadioSetting& setting = scheduled_radio_settings[i];
      if (setting.active && setting.temporary && setting.started && now >= setting.end_time) {
        setting.active = false;
        setting.started = false;
        queueSavedRadioApply();
      }
    }

    for (int i = 0; i < MAX_SCHEDULED_RADIO_SETTINGS; i++) {
      ScheduledRadioSetting& setting = scheduled_radio_settings[i];
      if (setting.active && setting.temporary && !setting.started && now >= setting.start_time) {
        if (now >= setting.end_time) {
          setting.active = false;
          if (temp_radio_handoff_pending) {
            temp_radio_handoff_pending = false;
            queueSavedRadioApply();
          }
        } else if (applyRadioParams(setting.freq, setting.bw, setting.sf, setting.cr)) {
          setting.started = true;
          temp_radio_applied = true;
          temp_radio_handoff_pending = false;
        } else {
          // setParams() can fail after changing only part of the modulation
          // tuple. Restore the saved tuple if this temporary window expires
          // before a later retry succeeds.
          saved_radio_apply_pending = true;
          apply_failed = true;
          break;
        }
      }
    }
  }

  refreshScheduledRadioState();
  if (saved_radio_apply_pending && !temp_radio_handoff_pending
      && !scheduled_temp_radio_started && !apply_failed) {
    // If begin() deferred the saved params to preserve a wake packet, its gain
    // update was deferred for the same reason. Retry both at the first safe
    // handoff; unsupported boosted-gain modes remain harmless here.
    radio_driver.setRxBoostedGainMode(_prefs.rx_boosted_gain);
    if (applySavedRadioParams()) {
      radio_driver.setTxPower(_prefs.tx_power_dbm);
      saved_radio_apply_pending = false;
      temp_radio_applied = false;
    } else {
      apply_failed = true;
    }
  }
  if (apply_failed) {
    scheduled_radio_retry_at = futureMillis(nextRadioApplyRetryDelay(scheduled_radio_retry_failures));
  } else {
    scheduled_radio_retry_at = 0;
    scheduled_radio_retry_failures = 0;
  }
}

bool MyMesh::isMillisTimerDue(unsigned long timestamp) const {
  return timestamp && millisHasNowPassed(timestamp);
}

bool MyMesh::hasScheduledRadioWorkDue() const {
  if (scheduled_radio_retry_at && !millisHasNowPassed(scheduled_radio_retry_at)) return false;
  if (saved_radio_apply_pending && !temp_radio_handoff_pending
      && !scheduled_temp_radio_started) return true;
  return next_scheduled_radio_time != 0
      && (next_scheduled_radio_check_at == 0
          || millisHasNowPassed(next_scheduled_radio_check_at));
}

uint32_t MyMesh::limitSleepToMillisTimer(unsigned long timestamp, uint32_t sleep_secs) const {
  if (!timestamp || sleep_secs == 0) {
    return sleep_secs;
  }
  unsigned long now = millis();
  if ((long)(now - timestamp) >= 0) {
    return 0;
  }
  unsigned long remaining_ms = timestamp - now;
  uint32_t remaining_secs = (remaining_ms + 999UL) / 1000UL;
  return remaining_secs < sleep_secs ? remaining_secs : sleep_secs;
}

uint32_t MyMesh::limitSleepToScheduledRadioWork(uint32_t sleep_secs) const {
  if (scheduled_radio_retry_at && !millisHasNowPassed(scheduled_radio_retry_at)) {
    return limitSleepToMillisTimer(scheduled_radio_retry_at, sleep_secs);
  }
  return limitSleepToMillisTimer(next_scheduled_radio_check_at, sleep_secs);
}

uint32_t MyMesh::getPowerSaveSleepSeconds(uint32_t max_secs) const {
  if (max_secs == 0 || hasPendingWork()) {
    return 0;
  }

  uint32_t sleep_secs = max_secs;
  uint32_t queue_delay_ms;
  if (getNextQueueWakeDelay(queue_delay_ms)) {
    uint32_t queue_delay_secs = (queue_delay_ms + 999UL) / 1000UL;
    if (queue_delay_secs < sleep_secs) sleep_secs = queue_delay_secs;
  }
  uint32_t retry_delay_ms;
  if (getNextRetryWakeDelay(retry_delay_ms)) {
    uint32_t retry_delay_secs = (retry_delay_ms + 999UL) / 1000UL;
    if (retry_delay_secs < sleep_secs) sleep_secs = retry_delay_secs;
  }
  sleep_secs = limitSleepToMillisTimer(next_flood_advert, sleep_secs);
  sleep_secs = limitSleepToMillisTimer(next_local_advert, sleep_secs);
  sleep_secs = limitSleepToMillisTimer(dirty_contacts_expiry, sleep_secs);
  sleep_secs = limitSleepToMillisTimer(next_recent_repeater_sweep, sleep_secs);
  if (_prefs.battery_alert_enabled) {
    sleep_secs = limitSleepToMillisTimer(next_battery_alert_check, sleep_secs);
  }
  sleep_secs = limitSleepToScheduledRadioWork(sleep_secs);
  return sleep_secs;
}

void MyMesh::applyTempRadioParams(float freq, float bw, uint8_t sf, uint8_t cr, int timeout_mins) {
  scheduled_radio_retry_at = 0;
  scheduled_radio_retry_failures = 0;
  bool cancelled_started_temp = false;
  for (int i = 0; i < MAX_SCHEDULED_RADIO_SETTINGS; i++) {
    if (scheduled_radio_settings[i].active && scheduled_radio_settings[i].temporary) {
      cancelled_started_temp = cancelled_started_temp || scheduled_radio_settings[i].started;
      scheduled_radio_settings[i].active = false;
      scheduled_radio_settings[i].started = false;
    }
  }
  if (cancelled_started_temp) {
    // Keep the currently-active channel long enough for the CLI reply and use
    // the new temporary entry as an explicit handoff. If that entry expires or
    // is deleted before applying, the scheduler restores saved parameters.
    temp_radio_handoff_pending = true;
  }

  int slot = findFreeScheduledRadioSlot();
  if (slot < 0) {
    if (temp_radio_handoff_pending) {
      temp_radio_handoff_pending = false;
      queueSavedRadioApply();
    }
    refreshScheduledRadioState();
    return;
  }

  uint32_t start_time = getRTCClock()->getCurrentTime() + 2; // give CLI reply time to be sent first
  scheduled_radio_settings[slot].active = true;
  scheduled_radio_settings[slot].temporary = true;
  scheduled_radio_settings[slot].started = false;
  scheduled_radio_settings[slot].freq = freq;
  scheduled_radio_settings[slot].bw = bw;
  scheduled_radio_settings[slot].sf = sf;
  scheduled_radio_settings[slot].cr = cr;
  scheduled_radio_settings[slot].start_time = start_time;
  scheduled_radio_settings[slot].end_time = start_time + ((uint32_t)timeout_mins * 60);
  refreshScheduledRadioState();
}

bool MyMesh::scheduleNormalRadio() {
  // Cancel every pending/active temporary entry, but leave permanent radioat
  // changes intact. The saved apply waits for the CLI reply to leave the
  // outbound queue before changing modulation parameters.
  for (int i = 0; i < MAX_SCHEDULED_RADIO_SETTINGS; i++) {
    if (!scheduled_radio_settings[i].temporary) continue;
    scheduled_radio_settings[i].active = false;
    scheduled_radio_settings[i].started = false;
  }
  temp_radio_handoff_pending = false;
  refreshScheduledRadioState();
  queueSavedRadioApply();
  return true;
}

bool MyMesh::formatFileSystem() {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  return InternalFS.format();
#elif defined(RP2040_PLATFORM)
  return LittleFS.format();
#elif defined(ESP32)
  return SPIFFS.format();
#else
#error "need to implement file system erase"
  return false;
#endif
}

void MyMesh::sendSelfAdvertisement(int delay_millis, bool flood) {
  // Keep signing outside command handlers and other callers. This is a second
  // stack boundary in addition to deferred remote-command dispatch.
  pending_self_advert_delay = delay_millis > 0 ? (uint32_t)delay_millis : 0;
  pending_self_advert_flood = flood;
  pending_self_advert = true;
}

void MyMesh::sendSelfAdvertisementNow(uint32_t delay_millis, bool flood) {
  mesh::Packet *pkt = createSelfAdvert();
  if (pkt) {
    if (flood) {
      sendFloodScoped(default_scope, pkt, delay_millis, _prefs.path_hash_mode + 1);
    } else {
      sendZeroHop(pkt, delay_millis);
    }
  } else {
    MESH_DEBUG_PRINTLN("ERROR: unable to create advertisement packet!");
  }
}

void MyMesh::updateAdvertTimer() {
  if (_prefs.advert_interval > 0) { // schedule local advert timer
    next_local_advert = futureMillis((int)((uint32_t)_prefs.advert_interval * 2 * 60 * 1000));
  } else {
    next_local_advert = 0; // stop the timer
  }
}

void MyMesh::updateFloodAdvertTimer() {
  if (_prefs.flood_advert_interval > 0) { // schedule flood advert timer
    next_flood_advert = futureMillis(((uint32_t)_prefs.flood_advert_interval) * 60 * 60 * 1000);
  } else {
    next_flood_advert = 0; // stop the timer
  }
}

void MyMesh::dumpLogFile() {
#if defined(RP2040_PLATFORM)
  File f = _fs->open(PACKET_LOG_FILE, "r");
#else
  File f = _fs->open(PACKET_LOG_FILE);
#endif
  if (f) {
    while (f.available()) {
      int c = f.read();
      if (c < 0) break;
      Serial.print((char)c);
    }
    f.close();
  }
}

void MyMesh::setTxPower(int8_t power_dbm) {
  radio_driver.setTxPower(power_dbm);
}

bool MyMesh::setRxPowerSaving(bool enable, uint32_t rx_us, uint32_t sleep_us) {
  bool ok = radio_driver.setRxPowerSaving(enable, rx_us, sleep_us);
  MESH_DEBUG_PRINTLN("RX Power Saving: %s (%lu/%lu us)%s",
                     enable ? "Enabled" : "Disabled",
                     (unsigned long)rx_us,
                     (unsigned long)sleep_us,
                     ok ? "" : " unsupported");
  return ok;
}

bool MyMesh::supportsRxPowerSavingRfRxDisable() const {
  return radio_driver.supportsRxPowerSavingRfRxDisable();
}

bool MyMesh::setRxPowerSavingRfRxDisabled(bool disabled) {
  bool ok = radio_driver.setRxPowerSavingRfRxDisabled(disabled);
  MESH_DEBUG_PRINTLN("RX Power Saving RF_RX control: %s, %s",
                     disabled ? "disabled" : "enabled",
                     ok ? "accepted" : "unsupported");
  return ok;
}

bool MyMesh::isRxPowerSavingRfRxDisabled() const {
  return radio_driver.isRxPowerSavingRfRxDisabled();
}

void MyMesh::getRxPsWatchdogCounts(uint32_t* soft, uint32_t* hard) {
  *soft = radio_driver.getRxPsWatchdogSoftCount();
  *hard = radio_driver.getRxPsWatchdogHardCount();
}

bool MyMesh::setRxBoostedGain(bool enable) {
  return radio_driver.setRxBoostedGainMode(enable);
}

#if defined(USE_LR2021)
bool MyMesh::configSideDetectors(const uint8_t sideDetSFs[], uint8_t num, float bw) {
  return radio_driver.configSideDetectors(sideDetSFs, num, bw);
}
#endif

void MyMesh::formatNeighborsReply(char *reply) {
  char *dp = reply;

#if MAX_NEIGHBOURS
  // create copy of neighbours list, skipping empty entries so we can sort it separately from main list
  int16_t neighbours_count = 0;
  NeighbourInfo* sorted_neighbours[MAX_NEIGHBOURS];
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    auto neighbour = &neighbours[i];
    if (neighbour->heard_timestamp > 0) {
      sorted_neighbours[neighbours_count] = neighbour;
      neighbours_count++;
    }
  }

  // sort neighbours newest to oldest
  std::sort(sorted_neighbours, sorted_neighbours + neighbours_count, [](const NeighbourInfo* a, const NeighbourInfo* b) {
    return a->heard_timestamp > b->heard_timestamp; // desc
  });

  for (int i = 0; i < neighbours_count && dp - reply < 134; i++) {
    NeighbourInfo *neighbour = sorted_neighbours[i];

    // add new line if not first item
    if (i > 0) *dp++ = '\n';

    char hex[10];
    // get 4 bytes of neighbour id as hex
    mesh::Utils::toHex(hex, neighbour->id.pub_key, 4);

    // add next neighbour
    uint32_t secs_ago = getRTCClock()->getCurrentTime() - neighbour->heard_timestamp;
    sprintf(dp, "%s:%d:%d", hex, secs_ago, neighbour->snr);
    while (*dp)
      dp++; // find end of string
  }
#endif
  if (dp == reply) { // no neighbours, need empty response
    strcpy(dp, "-none-");
    dp += 6;
  }
  *dp = 0; // null terminator
}

void MyMesh::removeNeighbor(const uint8_t *pubkey, int key_len) {
#if MAX_NEIGHBOURS
  if (pubkey == NULL || key_len <= 0 || key_len > PUB_KEY_SIZE) return;
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    NeighbourInfo *neighbour = &neighbours[i];
    if (memcmp(neighbour->id.pub_key, pubkey, key_len) == 0) {
      neighbours[i] = NeighbourInfo(); // clear neighbour entry
    }
  }
#endif
}

void MyMesh::startRegionsLoad() {
  temp_map.resetFrom(region_map);   // rebuild regions in a temp instance
  memset(load_stack, 0, sizeof(load_stack));
  load_stack[0] = &temp_map.getWildcard();
  region_load_active = true;
}

bool MyMesh::saveRegions() {
  return region_map.save(_fs);
}

void MyMesh::onDefaultRegionChanged(const RegionEntry* r) {
  if (r) {
    region_map.getTransportKeysFor(*r, &default_scope, 1);
  } else {
    memset(default_scope.key, 0, sizeof(default_scope.key));
  }
}

static const char* skipFloodFilterSpaces(const char* text) {
  while (text != NULL && *text == ' ') text++;
  return text == NULL ? "" : text;
}

static bool floodFilterAsciiEqual(const char* left, const char* right) {
  if (left == NULL || right == NULL) return false;
  while (*left && *right) {
    char a = *left++;
    char b = *right++;
    if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
    if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
    if (a != b) return false;
  }
  return *left == 0 && *right == 0;
}

static bool floodFilterAsciiStartsWith(const char* text, const char* prefix) {
  if (text == NULL || prefix == NULL) return false;
  while (*prefix) {
    char a = *text++;
    char b = *prefix++;
    if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
    if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
    if (a != b) return false;
  }
  return true;
}

static bool parseFloodFilterUnsigned(const char* text, uint8_t maximum, uint8_t& value) {
  if (text == NULL || *text == 0) return false;
  uint16_t parsed = 0;
  for (const char* p = text; *p; p++) {
    if (*p < '0' || *p > '9') return false;
    uint16_t digit = (uint16_t)(*p - '0');
    if (digit > maximum || parsed > (uint16_t)(maximum - digit) / 10U) return false;
    parsed = (uint16_t)(parsed * 10U + digit);
  }
  value = (uint8_t)parsed;
  return true;
}

static const char* floodFilterPayloadTypeName(uint8_t type) {
  switch (type) {
    case PAYLOAD_TYPE_REQ: return "req";
    case PAYLOAD_TYPE_RESPONSE: return "response";
    case PAYLOAD_TYPE_TXT_MSG: return "txt_msg";
    case PAYLOAD_TYPE_ACK: return "ack";
    case PAYLOAD_TYPE_ADVERT: return "advert";
    case PAYLOAD_TYPE_GRP_TXT: return "grp_txt";
    case PAYLOAD_TYPE_GRP_DATA: return "grp_data";
    case PAYLOAD_TYPE_ANON_REQ: return "anon_req";
    case PAYLOAD_TYPE_PATH: return "path";
    case PAYLOAD_TYPE_TRACE: return "trace";
    case PAYLOAD_TYPE_MULTIPART: return "multipart";
    case PAYLOAD_TYPE_CONTROL: return "control";
    case PAYLOAD_TYPE_OTA: return "ota";
    case 0x0D: return "reserved13";
    case 0x0E: return "reserved14";
    case PAYLOAD_TYPE_RAW_CUSTOM: return "raw_custom";
    case FLOOD_PACKET_FILTER_ANY_TYPE: return "any";
    default: return "invalid";
  }
}

static bool parseFloodFilterPayloadType(const char* text, uint8_t& type) {
  uint8_t numeric;
  if (parseFloodFilterUnsigned(text, PH_TYPE_MASK, numeric)) {
    type = numeric;
    return true;
  }
  if (text != NULL && text[0] == '0' && (text[1] == 'x' || text[1] == 'X') && text[2] != 0) {
    uint8_t parsed = 0;
    for (const char* p = text + 2; *p; p++) {
      uint8_t digit;
      if (*p >= '0' && *p <= '9') digit = (uint8_t)(*p - '0');
      else if (*p >= 'a' && *p <= 'f') digit = (uint8_t)(*p - 'a' + 10);
      else if (*p >= 'A' && *p <= 'F') digit = (uint8_t)(*p - 'A' + 10);
      else return false;
      if (parsed > (PH_TYPE_MASK - digit) / 16U) return false;
      parsed = (uint8_t)(parsed * 16U + digit);
    }
    type = parsed;
    return true;
  }
  if (floodFilterAsciiStartsWith(text, "payload_type_")) text += strlen("payload_type_");
  if (floodFilterAsciiEqual(text, "any")) type = FLOOD_PACKET_FILTER_ANY_TYPE;
  else if (floodFilterAsciiEqual(text, "req")) type = PAYLOAD_TYPE_REQ;
  else if (floodFilterAsciiEqual(text, "response") || floodFilterAsciiEqual(text, "resp")) type = PAYLOAD_TYPE_RESPONSE;
  else if (floodFilterAsciiEqual(text, "txt_msg") || floodFilterAsciiEqual(text, "txt")) type = PAYLOAD_TYPE_TXT_MSG;
  else if (floodFilterAsciiEqual(text, "ack")) type = PAYLOAD_TYPE_ACK;
  else if (floodFilterAsciiEqual(text, "advert")) type = PAYLOAD_TYPE_ADVERT;
  else if (floodFilterAsciiEqual(text, "grp_txt") || floodFilterAsciiEqual(text, "group_text")) type = PAYLOAD_TYPE_GRP_TXT;
  else if (floodFilterAsciiEqual(text, "grp_data") || floodFilterAsciiEqual(text, "group_data")) type = PAYLOAD_TYPE_GRP_DATA;
  else if (floodFilterAsciiEqual(text, "anon_req")) type = PAYLOAD_TYPE_ANON_REQ;
  else if (floodFilterAsciiEqual(text, "path")) type = PAYLOAD_TYPE_PATH;
  else if (floodFilterAsciiEqual(text, "trace")) type = PAYLOAD_TYPE_TRACE;
  else if (floodFilterAsciiEqual(text, "multipart")) type = PAYLOAD_TYPE_MULTIPART;
  else if (floodFilterAsciiEqual(text, "control")) type = PAYLOAD_TYPE_CONTROL;
  else if (floodFilterAsciiEqual(text, "ota")) type = PAYLOAD_TYPE_OTA;
  else if (floodFilterAsciiEqual(text, "raw") || floodFilterAsciiEqual(text, "raw_custom")) type = PAYLOAD_TYPE_RAW_CUSTOM;
  else return false;
  return true;
}

static bool parseFloodFilterHopSpec(const char* text, uint8_t& min_hops, uint8_t& max_hops) {
  if (text == NULL || *text == 0) return false;
  if (floodFilterAsciiEqual(text, "all")) {
    min_hops = 0;
    max_hops = FLOOD_PACKET_FILTER_MAX_HOPS;
    return true;
  }

  char spec[12];
  if (strlen(text) >= sizeof(spec)) return false;
  strcpy(spec, text);
  size_t len = strlen(spec);
  if (len > 1 && spec[len - 1] == '+') {
    spec[len - 1] = 0;
    if (!parseFloodFilterUnsigned(spec, FLOOD_PACKET_FILTER_MAX_HOPS, min_hops)) return false;
    max_hops = FLOOD_PACKET_FILTER_MAX_HOPS;
    return true;
  }

  char* dash = strchr(spec, '-');
  if (dash != NULL) {
    *dash++ = 0;
    if (!parseFloodFilterUnsigned(spec, FLOOD_PACKET_FILTER_MAX_HOPS, min_hops)
        || !parseFloodFilterUnsigned(dash, FLOOD_PACKET_FILTER_MAX_HOPS, max_hops)) {
      return false;
    }
    return min_hops <= max_hops;
  }

  if (!parseFloodFilterUnsigned(spec, FLOOD_PACKET_FILTER_MAX_HOPS, min_hops)) return false;
  max_hops = min_hops;
  return true;
}

static void formatFloodFilterHopSpec(char* dest, size_t dest_len, uint8_t min_hops, uint8_t max_hops) {
  if (min_hops == 0 && max_hops == FLOOD_PACKET_FILTER_MAX_HOPS) {
    snprintf(dest, dest_len, "all");
  } else if (max_hops == FLOOD_PACKET_FILTER_MAX_HOPS) {
    snprintf(dest, dest_len, "%u+", (uint32_t)min_hops);
  } else if (min_hops == max_hops) {
    snprintf(dest, dest_len, "%u", (uint32_t)min_hops);
  } else {
    snprintf(dest, dest_len, "%u-%u", (uint32_t)min_hops, (uint32_t)max_hops);
  }
}

static bool normalizeFloodFilterScopeName(const char* text, char* dest, size_t dest_len) {
  if (text == NULL || dest == NULL || dest_len < 3) return false;
  if (*text == '#') text++;
  if (*text == 0 || *text == '$') return false;

  size_t name_len = 0;
  while (text[name_len]) {
    if (!RegionMap::is_name_char((uint8_t)text[name_len])
        || text[name_len] == '#' || text[name_len] == '$') {
      return false;
    }
    name_len++;
  }
  if (name_len + 2 > dest_len) return false;

  dest[0] = '#';
  memcpy(&dest[1], text, name_len + 1);
  return true;
}

static bool isValidStoredFloodFilterScopeName(const char* text) {
  if (text[0] != '#' || text[1] == 0) return false;
  for (size_t i = 1; i < FLOOD_PACKET_FILTER_SCOPE_NAME_LEN; i++) {
    uint8_t c = (uint8_t)text[i];
    if (c == 0) return true;
    if (!RegionMap::is_name_char(c) || c == '#' || c == '$') return false;
  }
  return false;
}

static bool isValidStoredFloodRuleRegionName(const char* text) {
  if (text == NULL || text[0] == 0 || strcmp(text, "*") == 0) return false;
  for (size_t i = 0; i < FLOOD_PACKET_FILTER_SCOPE_NAME_LEN; i++) {
    uint8_t c = (uint8_t)text[i];
    if (c == 0) return true;
    if (!RegionMap::is_name_char(c)) return false;
  }
  return false;
}

static void deriveFloodFilterScopeKey(const char* scope_name, TransportKey& scope) {
  mesh::Utils::sha256(scope.key, sizeof(scope.key),
                      (const uint8_t*)scope_name, strlen(scope_name));
}

static bool parseFloodPacketFilterBlacklist(
    const char* text,
    uint8_t ids[FLOOD_PACKET_FILTER_BLACKLIST_REPLACE_MAX]
               [FLOOD_PACKET_FILTER_PATH_ID_SIZE],
    uint8_t& count) {
  count = 0;
  memset(ids, 0, FLOOD_PACKET_FILTER_BLACKLIST_REPLACE_MAX
      * FLOOD_PACKET_FILTER_PATH_ID_SIZE);
  if (text == NULL || *text == 0
      || strlen(text) >= FLOOD_PACKET_FILTER_BLACKLIST_REPLACE_MAX * 7U) {
    return false;
  }

  char input[FLOOD_PACKET_FILTER_BLACKLIST_REPLACE_MAX * 7];
  strcpy(input, text);
  char* token = input;
  while (token != NULL) {
    char* comma = strchr(token, ',');
    if (comma != NULL) *comma = 0;
    if (strlen(token) != FLOOD_PACKET_FILTER_PATH_ID_SIZE * 2U
        || count >= FLOOD_PACKET_FILTER_BLACKLIST_REPLACE_MAX) {
      return false;
    }
    for (const char* p = token; *p; p++) {
      if (!mesh::Utils::isHexChar(*p)) return false;
    }
    if (!mesh::Utils::fromHex(ids[count], FLOOD_PACKET_FILTER_PATH_ID_SIZE, token)) {
      return false;
    }
    for (uint8_t i = 0; i < count; i++) {
      if (memcmp(ids[i], ids[count], FLOOD_PACKET_FILTER_PATH_ID_SIZE) == 0) {
        return false;
      }
    }
    count++;
    token = comma != NULL ? comma + 1 : NULL;
    if (token != NULL && *token == 0) return false;
  }
  return count > 0;
}

bool MyMesh::loadFloodPacketFilterBlacklist() {
  flood_packet_filter_blacklist_count = 0;
  memset(flood_packet_filter_blacklist, 0, sizeof(flood_packet_filter_blacklist));
  if (_fs == NULL || !_fs->exists(FLOOD_PACKET_FILTER_BLACKLIST_FILE)) return true;

  File file = openFloodSettingsRead(_fs, FLOOD_PACKET_FILTER_BLACKLIST_FILE);
  if (!file) return false;

  uint8_t magic[4];
  uint8_t count = 0;
  uint8_t loaded[FLOOD_PACKET_FILTER_BLACKLIST_MAX][FLOOD_PACKET_FILTER_PATH_ID_SIZE];
  memset(loaded, 0, sizeof(loaded));
  bool success = file.read(magic, sizeof(magic)) == sizeof(magic)
      && memcmp(magic, "FBL1", sizeof(magic)) == 0
      && file.read(&count, sizeof(count)) == sizeof(count)
      && count <= FLOOD_PACKET_FILTER_BLACKLIST_MAX
      && file.read((uint8_t*)loaded, count * FLOOD_PACKET_FILTER_PATH_ID_SIZE)
          == count * FLOOD_PACKET_FILTER_PATH_ID_SIZE;
  for (uint8_t i = 0; success && i < count; i++) {
    for (uint8_t j = 0; j < i; j++) {
      if (memcmp(loaded[i], loaded[j], FLOOD_PACKET_FILTER_PATH_ID_SIZE) == 0) {
        success = false;
        break;
      }
    }
  }
  file.close();

  if (success) {
    flood_packet_filter_blacklist_count = count;
    memcpy(flood_packet_filter_blacklist, loaded, sizeof(loaded));
  }
  return success;
}

bool MyMesh::saveFloodPacketFilterBlacklist() {
#if MESH_ENABLE_FLOOD_RULE_ENGINE
  return saveFloodPacketFilters();
#else
  if (_fs == NULL) return false;
  File file = openFloodSettingsWrite(_fs, FLOOD_PACKET_FILTER_BLACKLIST_FILE);
  if (!file) return false;

  const uint8_t magic[4] = {'F', 'B', 'L', '1'};
  bool success = file.write(magic, sizeof(magic)) == sizeof(magic)
      && file.write(&flood_packet_filter_blacklist_count,
                    sizeof(flood_packet_filter_blacklist_count))
          == sizeof(flood_packet_filter_blacklist_count)
      && file.write((const uint8_t*)flood_packet_filter_blacklist,
                    flood_packet_filter_blacklist_count * FLOOD_PACKET_FILTER_PATH_ID_SIZE)
          == flood_packet_filter_blacklist_count * FLOOD_PACKET_FILTER_PATH_ID_SIZE;
  file.close();
  return success;
#endif
}

void MyMesh::formatFloodPacketFilterBlacklist(const char* args, char* reply) const {
  const char* selector = skipFloodFilterSpaces(args);
  if (*selector == '.') selector = skipFloodFilterSpaces(selector + 1);
  if (*selector != 0) {
    uint8_t slot = 0;
    if (!parseFloodFilterUnsigned(selector, FLOOD_PACKET_FILTER_BLACKLIST_MAX, slot)
        || slot == 0 || slot > flood_packet_filter_blacklist_count) {
      snprintf(reply, 160, "Err - blacklist slot must be 1-%u",
               (uint32_t)flood_packet_filter_blacklist_count);
      return;
    }
    char id[FLOOD_PACKET_FILTER_PATH_ID_SIZE * 2 + 1];
    mesh::Utils::toHex(id, flood_packet_filter_blacklist[slot - 1],
                       FLOOD_PACKET_FILTER_PATH_ID_SIZE);
    snprintf(reply, 160, "> %u=%s", (uint32_t)slot, id);
    return;
  }
  if (flood_packet_filter_blacklist_count == 0) {
    strcpy(reply, "> off");
    return;
  }

  size_t used = (size_t)snprintf(reply, 160, "> count=%u ",
                                 (uint32_t)flood_packet_filter_blacklist_count);
  bool truncated = false;
  for (uint8_t i = 0; i < flood_packet_filter_blacklist_count; i++) {
    char id[FLOOD_PACKET_FILTER_PATH_ID_SIZE * 2 + 1];
    mesh::Utils::toHex(id, flood_packet_filter_blacklist[i],
                       FLOOD_PACKET_FILTER_PATH_ID_SIZE);
    size_t needed = strlen(id) + (i == 0 ? 0 : 1);
    if (used + needed >= 155) {
      truncated = true;
      break;
    }
    int written = snprintf(&reply[used], 160 - used, "%s%s", i == 0 ? "" : ",", id);
    if (written < 0) break;
    used += (size_t)written;
  }
  if (truncated) {
    StrHelper::strncpy(&reply[used], " ...", 160 - used);
  }
}

void MyMesh::setFloodPacketFilterBlacklist(const char* args, char* reply) {
  const char* cursor = skipFloodFilterSpaces(args);
  int requested_slot = -1;
  if (*cursor == '.') {
    cursor++;
    const char* slot_start = cursor;
    while (*cursor >= '0' && *cursor <= '9') cursor++;
    size_t slot_len = (size_t)(cursor - slot_start);
    char slot_text[8];
    if (slot_len == 0 || slot_len >= sizeof(slot_text)) {
      snprintf(reply, 160, "Err - blacklist slot must be 1-%u",
               (uint32_t)FLOOD_PACKET_FILTER_BLACKLIST_MAX);
      return;
    }
    memcpy(slot_text, slot_start, slot_len);
    slot_text[slot_len] = 0;
    uint8_t slot = 0;
    if (!parseFloodFilterUnsigned(slot_text, FLOOD_PACKET_FILTER_BLACKLIST_MAX, slot)
        || slot == 0) {
      snprintf(reply, 160, "Err - blacklist slot must be 1-%u",
               (uint32_t)FLOOD_PACKET_FILTER_BLACKLIST_MAX);
      return;
    }
    requested_slot = slot - 1;
    if (*cursor != ' ') {
      strcpy(reply, "Err - expected six-digit repeater ID");
      return;
    }
  }
  cursor = skipFloodFilterSpaces(cursor);

  uint8_t parsed[FLOOD_PACKET_FILTER_BLACKLIST_REPLACE_MAX]
                [FLOOD_PACKET_FILTER_PATH_ID_SIZE];
  uint8_t count = 0;
  if (!parseFloodPacketFilterBlacklist(cursor, parsed, count)) {
    if (requested_slot >= 0) {
      snprintf(reply, 160, "Err - use 1-%d unique six-digit hex IDs separated by commas",
               FLOOD_PACKET_FILTER_BLACKLIST_REPLACE_MAX);
    } else {
      snprintf(reply, 160, "Err - use 1-%d unique six-digit hex IDs separated by commas",
               FLOOD_PACKET_FILTER_BLACKLIST_REPLACE_MAX);
    }
    return;
  }

  uint8_t previous_count = flood_packet_filter_blacklist_count;
  uint8_t previous[FLOOD_PACKET_FILTER_BLACKLIST_MAX][FLOOD_PACKET_FILTER_PATH_ID_SIZE];
  memcpy(previous, flood_packet_filter_blacklist, sizeof(previous));

  if (requested_slot >= 0) {
    if (requested_slot > flood_packet_filter_blacklist_count) {
      snprintf(reply, 160, "Err - next blacklist slot is %u",
               (uint32_t)flood_packet_filter_blacklist_count + 1U);
      return;
    }
    if ((uint16_t)requested_slot + count > FLOOD_PACKET_FILTER_BLACKLIST_MAX) {
      snprintf(reply, 160, "Err - blacklist range exceeds slot %u",
               (uint32_t)FLOOD_PACKET_FILTER_BLACKLIST_MAX);
      return;
    }
    for (uint8_t i = 0; i < flood_packet_filter_blacklist_count; i++) {
      if (i >= requested_slot && i < requested_slot + count) continue;
      for (uint8_t j = 0; j < count; j++) {
        if (memcmp(flood_packet_filter_blacklist[i], parsed[j],
                   FLOOD_PACKET_FILTER_PATH_ID_SIZE) == 0) {
          strcpy(reply, "Err - duplicate blacklist ID");
          return;
        }
      }
    }
    memcpy(flood_packet_filter_blacklist[requested_slot], parsed,
           count * FLOOD_PACKET_FILTER_PATH_ID_SIZE);
    uint16_t new_count = (uint16_t)requested_slot + count;
    if (new_count > flood_packet_filter_blacklist_count) {
      flood_packet_filter_blacklist_count = (uint8_t)new_count;
    }
  } else {
    flood_packet_filter_blacklist_count = count;
    memset(flood_packet_filter_blacklist, 0, sizeof(flood_packet_filter_blacklist));
    memcpy(flood_packet_filter_blacklist, parsed,
           count * FLOOD_PACKET_FILTER_PATH_ID_SIZE);
  }

  if (!saveFloodPacketFilterBlacklist()) {
    flood_packet_filter_blacklist_count = previous_count;
    memcpy(flood_packet_filter_blacklist, previous, sizeof(previous));
    strcpy(reply, "Err - unable to save flood filter blacklist");
    return;
  }
  if (requested_slot >= 0) {
    if (count == 1) {
      snprintf(reply, 160, "OK - blacklist slot %u",
               (uint32_t)requested_slot + 1U);
    } else {
      snprintf(reply, 160, "OK - blacklist slots %u-%u",
               (uint32_t)requested_slot + 1U,
               (uint32_t)requested_slot + count);
    }
  } else {
    snprintf(reply, 160, "OK - %u blacklist IDs", (uint32_t)count);
  }
}

void MyMesh::deleteFloodPacketFilterBlacklist(const char* args, char* reply) {
  const char* selector = skipFloodFilterSpaces(args);
  if (*selector == '.') selector = skipFloodFilterSpaces(selector + 1);
  bool delete_all = *selector == 0 || floodFilterAsciiEqual(selector, "all");
  uint8_t slot = 0;
  if (!delete_all
      && (!parseFloodFilterUnsigned(selector, FLOOD_PACKET_FILTER_BLACKLIST_MAX, slot)
          || slot == 0 || slot > flood_packet_filter_blacklist_count)) {
    snprintf(reply, 160, "Err - use: del flood.filter.blacklist[.<1-%u>| all]",
             (uint32_t)FLOOD_PACKET_FILTER_BLACKLIST_MAX);
    return;
  }

  uint8_t previous_count = flood_packet_filter_blacklist_count;
  uint8_t previous[FLOOD_PACKET_FILTER_BLACKLIST_MAX][FLOOD_PACKET_FILTER_PATH_ID_SIZE];
  memcpy(previous, flood_packet_filter_blacklist, sizeof(previous));
  if (delete_all) {
    flood_packet_filter_blacklist_count = 0;
    memset(flood_packet_filter_blacklist, 0, sizeof(flood_packet_filter_blacklist));
  } else {
    uint8_t index = slot - 1;
    if (index + 1 < flood_packet_filter_blacklist_count) {
      memmove(flood_packet_filter_blacklist[index],
              flood_packet_filter_blacklist[index + 1],
              (flood_packet_filter_blacklist_count - index - 1)
                  * FLOOD_PACKET_FILTER_PATH_ID_SIZE);
    }
    flood_packet_filter_blacklist_count--;
    memset(flood_packet_filter_blacklist[flood_packet_filter_blacklist_count], 0,
           FLOOD_PACKET_FILTER_PATH_ID_SIZE);
  }
  if (!saveFloodPacketFilterBlacklist()) {
    flood_packet_filter_blacklist_count = previous_count;
    memcpy(flood_packet_filter_blacklist, previous, sizeof(previous));
    strcpy(reply, "Err - unable to save flood filter blacklist");
    return;
  }
  if (delete_all) {
    strcpy(reply, "OK - flood filter blacklist removed");
  } else {
    snprintf(reply, 160, "OK - blacklist slot %u removed", (uint32_t)slot);
  }
}

static bool parseFloodModerationUnsigned(const char* text, uint32_t maximum,
                                         uint32_t& value);
static bool parseFloodModerationChannel(
    const char* text, uint8_t secret[PUB_KEY_SIZE], uint8_t& key_len,
    uint8_t hash_prefix[FLOOD_CHANNEL_KEY_PREFIX_LEN], char* name,
    size_t name_len);
static bool parseFloodModerationPath(
    const char* text, uint8_t& hash_size, uint8_t& path_hops,
    uint8_t path[FLOOD_GROUP_MODERATION_PATH_BYTES_MAX]);
static void formatFloodModerationPath(
    char* dest, size_t dest_len, uint8_t hash_size, uint8_t path_hops,
    const uint8_t path[FLOOD_GROUP_MODERATION_PATH_BYTES_MAX]);

void MyMesh::seedDefaultFloodPacketFilters() {
  auto& entry = flood_packet_filters[0];
  memset(&entry, 0, sizeof(entry));
  entry.active = true;
  entry.payload_type = PAYLOAD_TYPE_OTA;
  entry.min_hops = 0;
  entry.max_hops = FLOOD_PACKET_FILTER_MAX_HOPS;
  entry.suspend_on_temp_radio = true;
#if MESH_ENABLE_FLOOD_RULE_ENGINE
  entry.drop_on_match = true;

  // Preserve the former channel-block default as a normal FPF7 rule. Channel
  // authentication limits this any-type row to GRP_TXT and GRP_DATA packets.
  auto& wardriving = flood_packet_filters[1];
  memset(&wardriving, 0, sizeof(wardriving));
  wardriving.active = true;
  wardriving.payload_type = FLOOD_PACKET_FILTER_ANY_TYPE;
  wardriving.min_hops = DEFAULT_WARDRIVING_MAX_HOPS + 1;
  wardriving.max_hops = FLOOD_PACKET_FILTER_MAX_HOPS;
  wardriving.channel_key_len = CIPHER_KEY_SIZE;
  mesh::Utils::sha256(
      wardriving.channel_secret, CIPHER_KEY_SIZE,
      (const uint8_t*)DEFAULT_WARDRIVING_CHANNEL,
      strlen(DEFAULT_WARDRIVING_CHANNEL));
  mesh::Utils::sha256(
      &wardriving.channel_hash, sizeof(wardriving.channel_hash),
      wardriving.channel_secret, wardriving.channel_key_len);
  StrHelper::strncpy(wardriving.channel_name, DEFAULT_WARDRIVING_CHANNEL,
                     sizeof(wardriving.channel_name));
  wardriving.drop_on_match = true;
#endif
}

#if MESH_ENABLE_FLOOD_RULE_ENGINE
bool MyMesh::loadFloodPacketFilters() {
  if (_fs == NULL) {
    seedDefaultFloodPacketFilters();
    return true;
  }

  enum class FileState : uint8_t { Missing, Valid, Invalid, Unreadable };
  auto loadFile = [this](const char* filename) -> FileState {
    memset(flood_packet_filters, 0, sizeof(flood_packet_filters));
    flood_channel_data_rule_slot = 0xFF;
    flood_channel_data_rule_max_hops = FLOOD_CHANNEL_HOPS_ALL;
    memset(flood_channel_scopes, 0, sizeof(flood_channel_scopes));
    memset(flood_channel_direct_scopes, 0,
           sizeof(flood_channel_direct_scopes));
    flood_packet_filter_blacklist_count = 0;
    memset(flood_packet_filter_blacklist, 0,
           sizeof(flood_packet_filter_blacklist));
    flood_policy_has_embedded_sections = false;
    if (!_fs->exists(filename)) return FileState::Missing;

    File file = openFloodSettingsRead(_fs, filename);
    if (!file) return FileState::Unreadable;

    FloodPacketFilterEntry* loaded = flood_packet_filters;
  uint8_t magic[4];
  uint8_t count = 0;
  bool success = file.read(magic, sizeof(magic)) == sizeof(magic);
  bool version_6 = success && memcmp(magic, "FPF6", sizeof(magic)) == 0;
  bool version_7 = success && memcmp(magic, "FPF7", sizeof(magic)) == 0;
  success = (version_6 || version_7)
      && file.read(&count, sizeof(count)) == sizeof(count)
      && FloodFilterPolicy::forwardPersistenceCountSupported(
          count, FLOOD_PACKET_FILTER_SLOTS);

  for (int i = 0; success && i < count; i++) {
    uint8_t active = 0;
    uint8_t suspend_on_temp_radio = 0;
    uint8_t match_blacklisted_path = 0;
    uint8_t scope_requires_region_match = 0;
    uint8_t scope_uses_slow_timing = 0;
    uint8_t drop_on_match = 0;
    uint8_t rate_limit_enabled = 0;
    uint8_t stop_on_match = 0;
    success = file.read(&active, sizeof(active)) == sizeof(active);
    success = success && file.read(&loaded[i].payload_type, sizeof(loaded[i].payload_type)) == sizeof(loaded[i].payload_type);
    success = success && file.read(&loaded[i].min_hops, sizeof(loaded[i].min_hops)) == sizeof(loaded[i].min_hops);
    success = success && file.read(&loaded[i].max_hops, sizeof(loaded[i].max_hops)) == sizeof(loaded[i].max_hops);
    success = success && file.read(&suspend_on_temp_radio,
                                    sizeof(suspend_on_temp_radio))
        == sizeof(suspend_on_temp_radio);
    success = success && file.read((uint8_t*)loaded[i].scope_name,
                                    sizeof(loaded[i].scope_name))
        == sizeof(loaded[i].scope_name);
    success = success
        && memchr(loaded[i].scope_name, 0, sizeof(loaded[i].scope_name)) != NULL;
    success = success && file.read(&match_blacklisted_path,
                                    sizeof(match_blacklisted_path))
        == sizeof(match_blacklisted_path);
    success = success && file.read(&scope_requires_region_match,
                                    sizeof(scope_requires_region_match))
        == sizeof(scope_requires_region_match);
    success = success && file.read(&scope_uses_slow_timing,
                                    sizeof(scope_uses_slow_timing))
        == sizeof(scope_uses_slow_timing);
    if (success && version_7) {
      success = file.read(&loaded[i].incoming_scope_kind,
                          sizeof(loaded[i].incoming_scope_kind))
          == sizeof(loaded[i].incoming_scope_kind);
      success = success
          && file.read((uint8_t*)loaded[i].incoming_scope_name,
                       sizeof(loaded[i].incoming_scope_name))
              == sizeof(loaded[i].incoming_scope_name);
      success = success
          && file.read(&loaded[i].channel_key_len,
                       sizeof(loaded[i].channel_key_len))
              == sizeof(loaded[i].channel_key_len);
      success = success
          && file.read(loaded[i].channel_secret,
                       sizeof(loaded[i].channel_secret))
              == sizeof(loaded[i].channel_secret);
      success = success
          && file.read((uint8_t*)loaded[i].channel_name,
                       sizeof(loaded[i].channel_name))
              == sizeof(loaded[i].channel_name);
      success = success
          && file.read(&loaded[i].path_hash_size,
                       sizeof(loaded[i].path_hash_size))
              == sizeof(loaded[i].path_hash_size);
      success = success
          && file.read(&loaded[i].path_hops,
                       sizeof(loaded[i].path_hops))
              == sizeof(loaded[i].path_hops);
      success = success
          && file.read(loaded[i].path, sizeof(loaded[i].path))
              == sizeof(loaded[i].path);
      success = success
          && file.read(&drop_on_match, sizeof(drop_on_match))
              == sizeof(drop_on_match);
      success = success
          && file.read(&rate_limit_enabled, sizeof(rate_limit_enabled))
              == sizeof(rate_limit_enabled);
      success = success
          && file.read((uint8_t*)&loaded[i].rate_per_minute,
                       sizeof(loaded[i].rate_per_minute))
              == sizeof(loaded[i].rate_per_minute);
      success = success
          && file.read((uint8_t*)loaded[i].target_region_name,
                       sizeof(loaded[i].target_region_name))
              == sizeof(loaded[i].target_region_name);
      success = success
          && file.read(&loaded[i].priority, sizeof(loaded[i].priority))
              == sizeof(loaded[i].priority);
      success = success
          && file.read(&stop_on_match, sizeof(stop_on_match))
              == sizeof(stop_on_match);
    } else {
      loaded[i].incoming_scope_kind = scope_requires_region_match
          ? FloodFilterPolicy::RULE_IN_ALLOWED
          : FloodFilterPolicy::RULE_IN_ANY;
      drop_on_match = loaded[i].scope_name[0] == 0 ? 1 : 0;
    }
    loaded[i].active = active != 0;
    loaded[i].suspend_on_temp_radio = suspend_on_temp_radio != 0;
    loaded[i].match_blacklisted_path = match_blacklisted_path != 0;
    loaded[i].scope_uses_slow_timing = scope_uses_slow_timing != 0;
    loaded[i].drop_on_match = drop_on_match != 0;
    loaded[i].rate_limit_enabled = rate_limit_enabled != 0;
    loaded[i].stop_on_match = stop_on_match != 0;
    if (success && (active > 1 || suspend_on_temp_radio > 1
        || match_blacklisted_path > 1 || scope_requires_region_match > 1
        || scope_uses_slow_timing > 1 || drop_on_match > 1
        || rate_limit_enabled > 1 || stop_on_match > 1)) {
      success = false;
    }
    if (!success) break;
    if (!loaded[i].active) {
      memset(&loaded[i], 0, sizeof(loaded[i]));
      continue;
    }

    bool direct_target = loaded[i].scope_name[0] != 0;
    bool target_name_terminated = memchr(
        loaded[i].target_region_name, 0,
        sizeof(loaded[i].target_region_name)) != NULL;
    bool region_target = target_name_terminated
        && loaded[i].target_region_name[0] != 0;
    bool input_name_terminated = memchr(
        loaded[i].incoming_scope_name, 0,
        sizeof(loaded[i].incoming_scope_name)) != NULL;
    bool channel_name_terminated = memchr(
        loaded[i].channel_name, 0, sizeof(loaded[i].channel_name)) != NULL;
    bool incoming_valid = loaded[i].incoming_scope_kind
        <= FloodFilterPolicy::RULE_IN_REGION;
    if (incoming_valid
        && loaded[i].incoming_scope_kind == FloodFilterPolicy::RULE_IN_SCOPE) {
      incoming_valid = input_name_terminated
          && isValidStoredFloodFilterScopeName(
              loaded[i].incoming_scope_name);
    } else if (incoming_valid
        && loaded[i].incoming_scope_kind == FloodFilterPolicy::RULE_IN_REGION) {
      incoming_valid = input_name_terminated
          && isValidStoredFloodRuleRegionName(
              loaded[i].incoming_scope_name);
    } else if (incoming_valid) {
      incoming_valid = input_name_terminated
          && loaded[i].incoming_scope_name[0] == 0;
    }

    bool channel_valid = loaded[i].channel_key_len == 0
        || loaded[i].channel_key_len == CIPHER_KEY_SIZE
        || loaded[i].channel_key_len == PUB_KEY_SIZE;
    if (channel_valid && loaded[i].channel_key_len == 0) {
      channel_valid = channel_name_terminated
          && loaded[i].channel_name[0] == 0;
    } else if (channel_valid) {
      channel_valid = channel_name_terminated
          && loaded[i].channel_name[0] != 0;
      if (channel_valid) {
        mesh::Utils::sha256(&loaded[i].channel_hash,
                            sizeof(loaded[i].channel_hash),
                            loaded[i].channel_secret,
                            loaded[i].channel_key_len);
      }
    }
    if (channel_valid && loaded[i].channel_key_len != 0) {
      channel_valid = loaded[i].payload_type == FLOOD_PACKET_FILTER_ANY_TYPE
          || loaded[i].payload_type == PAYLOAD_TYPE_GRP_TXT
          || loaded[i].payload_type == PAYLOAD_TYPE_GRP_DATA;
    }

    bool path_valid = (loaded[i].path_hash_size == 0
            && loaded[i].path_hops == 0)
        || (!loaded[i].match_blacklisted_path
            && loaded[i].path_hash_size >= 1
            && loaded[i].path_hash_size <= 3
            && loaded[i].path_hops >= 1
            && loaded[i].path_hops
                <= FLOOD_PACKET_FILTER_PATH_PREFIX_HOPS_MAX);
    bool action_valid = loaded[i].drop_on_match || direct_target
        || region_target || loaded[i].rate_limit_enabled
        || loaded[i].stop_on_match;
    if (!((loaded[i].payload_type <= PH_TYPE_MASK
              || loaded[i].payload_type == FLOOD_PACKET_FILTER_ANY_TYPE)
          && loaded[i].min_hops <= loaded[i].max_hops
          && loaded[i].max_hops <= FLOOD_PACKET_FILTER_MAX_HOPS
          && (!direct_target
              || isValidStoredFloodFilterScopeName(loaded[i].scope_name))
          && target_name_terminated
          && (!region_target
              || isValidStoredFloodRuleRegionName(
                  loaded[i].target_region_name))
          && !(direct_target && region_target)
          && !(loaded[i].drop_on_match && (direct_target || region_target))
          && !(loaded[i].drop_on_match && loaded[i].rate_limit_enabled)
          && (!loaded[i].rate_limit_enabled
              || loaded[i].rate_per_minute
                  < FLOOD_GROUP_MODERATION_RATE_UNLIMITED)
          && (!loaded[i].scope_uses_slow_timing
              || direct_target || region_target)
          && incoming_valid && channel_valid && path_valid
          && action_valid)) {
      success = false;
    }
  }

  // FPF7 keeps the forwarding rows byte-compatible with the room-server
  // engine. Repeater-only policy phases follow in a tagged extension so an
  // older FPF7 image can still be upgraded in place.
  if (success && version_7 && file.available() > 0) {
    static_assert(sizeof(FloodChannelScopeEntry) == 36,
                  "channel scope persistence requires 36-byte entries");
    uint8_t section_magic[4];
    uint8_t compatibility_slot = 0xFF;
    uint8_t compatibility_max_hops = FLOOD_CHANNEL_HOPS_ALL;
    uint8_t scope_count = 0;
    success = file.read(section_magic, sizeof(section_magic))
            == sizeof(section_magic)
        && memcmp(section_magic, FLOOD_POLICY_SECTION_MAGIC,
                  sizeof(section_magic)) == 0
        && file.read(&compatibility_slot, sizeof(compatibility_slot))
            == sizeof(compatibility_slot)
        && file.read(&compatibility_max_hops,
                     sizeof(compatibility_max_hops))
            == sizeof(compatibility_max_hops)
        && (compatibility_max_hops == FLOOD_CHANNEL_HOPS_ALL
            || (compatibility_max_hops >= 1
                && compatibility_max_hops <= 7))
        && file.read(&scope_count, sizeof(scope_count))
            == sizeof(scope_count);

    uint8_t retained_scope = scope_count < FLOOD_CHANNEL_SCOPE_SLOTS
        ? scope_count : FLOOD_CHANNEL_SCOPE_SLOTS;
    size_t retained_scope_bytes = (size_t)retained_scope
        * sizeof(FloodChannelScopeEntry);
    success = success
        && file.read((uint8_t*)flood_channel_scopes, retained_scope_bytes)
            == retained_scope_bytes;
    FloodChannelScopeEntry discarded_scope;
    for (uint16_t i = retained_scope; success && i < scope_count; i++) {
      success = file.read((uint8_t*)&discarded_scope,
                          sizeof(discarded_scope))
          == sizeof(discarded_scope);
    }

    uint8_t direct_count = 0;
    success = success
        && file.read(&direct_count, sizeof(direct_count))
            == sizeof(direct_count);
    uint8_t retained_direct = direct_count < FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS
        ? direct_count : FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS;
    for (uint16_t i = 0; success && i < direct_count; i++) {
      char direct_name[FLOOD_PACKET_FILTER_SCOPE_NAME_LEN];
      success = file.read((uint8_t*)direct_name, sizeof(direct_name))
          == sizeof(direct_name);
      if (success && direct_name[0] != 0
          && !isValidStoredFloodFilterScopeName(direct_name)) {
        success = false;
      }
      if (success && i < retained_direct) {
        memcpy(flood_channel_direct_scopes[i], direct_name,
               sizeof(direct_name));
      }
    }

    for (uint16_t i = 0; success && i < retained_scope; i++) {
      auto& entry = flood_channel_scopes[i];
      uint8_t selector =
          FloodFilterPolicy::channelScopeMatchSelectorValue(entry.selector);
      bool direct_target =
          FloodFilterPolicy::channelScopeUsesDirectTarget(entry.selector);
      if (entry.target_id == 0) {
        memset(&entry, 0, sizeof(entry));
      } else if (direct_target
          && (entry.target_id > retained_direct
              || flood_channel_direct_scopes[entry.target_id - 1][0] == 0)) {
        success = false;
      } else if (selector <= FLOOD_CHANNEL_SCOPE_OTHER_ANY) {
        entry.channel_hash = 0;
        memset(entry.secret, 0, sizeof(entry.secret));
      } else if (selector == CIPHER_KEY_SIZE || selector == PUB_KEY_SIZE) {
        mesh::Utils::sha256(&entry.channel_hash,
                            sizeof(entry.channel_hash), entry.secret,
                            selector);
        if (selector == CIPHER_KEY_SIZE) {
          memset(&entry.secret[CIPHER_KEY_SIZE], 0,
                 PUB_KEY_SIZE - CIPHER_KEY_SIZE);
        }
      } else {
        success = false;
      }
    }

    uint8_t blacklist_count = 0;
    success = success
        && file.read(&blacklist_count, sizeof(blacklist_count))
            == sizeof(blacklist_count)
        && blacklist_count <= FLOOD_PACKET_FILTER_BLACKLIST_MAX
        && file.read((uint8_t*)flood_packet_filter_blacklist,
                     blacklist_count * FLOOD_PACKET_FILTER_PATH_ID_SIZE)
            == blacklist_count * FLOOD_PACKET_FILTER_PATH_ID_SIZE;
    for (uint16_t i = 0; success && i < blacklist_count; i++) {
      for (uint16_t j = 0; j < i; j++) {
        if (memcmp(flood_packet_filter_blacklist[i],
                   flood_packet_filter_blacklist[j],
                   FLOOD_PACKET_FILTER_PATH_ID_SIZE) == 0) {
          success = false;
          break;
        }
      }
    }
    if (success && file.available() != 0) success = false;
    if (success && compatibility_slot != 0xFF) {
      success = compatibility_slot < count
          && isFloodChannelDataRule(loaded[compatibility_slot])
          && loaded[compatibility_slot].min_hops
              == (compatibility_max_hops == FLOOD_CHANNEL_HOPS_ALL
                  ? 0 : compatibility_max_hops + 1);
    }
    if (success) {
      flood_packet_filter_blacklist_count = blacklist_count;
      flood_channel_data_rule_slot = compatibility_slot;
      flood_channel_data_rule_max_hops = compatibility_max_hops;
      flood_policy_has_embedded_sections = true;
    }
  }
    file.close();
    if (!success) {
      memset(flood_packet_filters, 0, sizeof(flood_packet_filters));
      memset(flood_channel_scopes, 0, sizeof(flood_channel_scopes));
      memset(flood_channel_direct_scopes, 0,
             sizeof(flood_channel_direct_scopes));
      flood_packet_filter_blacklist_count = 0;
      memset(flood_packet_filter_blacklist, 0,
             sizeof(flood_packet_filter_blacklist));
      flood_channel_data_rule_slot = 0xFF;
      flood_channel_data_rule_max_hops = FLOOD_CHANNEL_HOPS_ALL;
      flood_policy_has_embedded_sections = false;
    }
    return success ? FileState::Valid : FileState::Invalid;
  };

  FileState primary = loadFile(FLOOD_PACKET_FILTER_FILE);
  if (primary == FileState::Valid) {
    // A valid primary is already committed. Transaction remnants are stale.
    if (_fs->exists(FLOOD_PACKET_FILTER_TEMP_FILE))
      _fs->remove(FLOOD_PACKET_FILTER_TEMP_FILE);
    if (_fs->exists(FLOOD_PACKET_FILTER_BACKUP_FILE))
      _fs->remove(FLOOD_PACKET_FILTER_BACKUP_FILE);
    return true;
  }
  if (primary == FileState::Unreadable) {
    // The primary name is authoritative. Do not replace a file that the
    // filesystem reported but could not open.
    return false;
  }

  FileState temp = loadFile(FLOOD_PACKET_FILTER_TEMP_FILE);
  if (temp == FileState::Valid) {
    // A complete temp is the newest transaction image. Never destroy an
    // unreadable primary, but still use the verified temp in RAM this boot.
    if (primary != FileState::Unreadable) {
      if (primary == FileState::Invalid)
        _fs->remove(FLOOD_PACKET_FILTER_FILE);
      if (!_fs->exists(FLOOD_PACKET_FILTER_FILE)
          && _fs->rename(FLOOD_PACKET_FILTER_TEMP_FILE,
                         FLOOD_PACKET_FILTER_FILE)) {
        if (_fs->exists(FLOOD_PACKET_FILTER_BACKUP_FILE))
          _fs->remove(FLOOD_PACKET_FILTER_BACKUP_FILE);
      }
    }
    return true;
  }

  FileState backup = loadFile(FLOOD_PACKET_FILTER_BACKUP_FILE);
  if (backup == FileState::Valid) {
    if (primary != FileState::Unreadable) {
      if (primary == FileState::Invalid)
        _fs->remove(FLOOD_PACKET_FILTER_FILE);
      if (!_fs->exists(FLOOD_PACKET_FILTER_FILE)
          && _fs->rename(FLOOD_PACKET_FILTER_BACKUP_FILE,
                         FLOOD_PACKET_FILTER_FILE)) {
        if (temp != FileState::Unreadable
            && _fs->exists(FLOOD_PACKET_FILTER_TEMP_FILE))
          _fs->remove(FLOOD_PACKET_FILTER_TEMP_FILE);
      }
    }
    return true;
  }

  // No complete image survived. Install the normal safe defaults in RAM and
  // replace only files proven malformed; unreadable files are preserved.
  seedDefaultFloodPacketFilters();
  if (primary == FileState::Invalid) _fs->remove(FLOOD_PACKET_FILTER_FILE);
  if (temp == FileState::Invalid) _fs->remove(FLOOD_PACKET_FILTER_TEMP_FILE);
  if (backup == FileState::Invalid)
    _fs->remove(FLOOD_PACKET_FILTER_BACKUP_FILE);
  if (primary == FileState::Unreadable || temp == FileState::Unreadable
      || backup == FileState::Unreadable) {
    return false;
  }
  // Defer the first FPF7 write until legacy scope, blacklist, channel-data,
  // and channel-block settings have been imported into the same transaction.
  return true;
}

bool MyMesh::isFloodChannelDataRule(
    const FloodPacketFilterEntry& entry) const {
  bool valid_hops = entry.min_hops == 0
      || (entry.min_hops >= 2 && entry.min_hops <= 8);
  return entry.active
      && entry.payload_type == PAYLOAD_TYPE_GRP_DATA
      && valid_hops
      && entry.max_hops == FLOOD_PACKET_FILTER_MAX_HOPS
      && !entry.suspend_on_temp_radio
      && entry.scope_name[0] == 0
      && !entry.match_blacklisted_path
      && !entry.scope_uses_slow_timing
      && entry.incoming_scope_kind == FloodFilterPolicy::RULE_IN_ANY
      && entry.incoming_scope_name[0] == 0
      && entry.channel_key_len == 0
      && entry.channel_name[0] == 0
      && entry.path_hash_size == 0
      && entry.path_hops == 0
      && entry.target_region_name[0] == 0
      && entry.drop_on_match
      && !entry.rate_limit_enabled
      && entry.priority == 0
      && !entry.stop_on_match;
}

int MyMesh::findFloodChannelDataRule() const {
  if (flood_channel_data_rule_slot >= FLOOD_PACKET_FILTER_SLOTS) return -1;
  return isFloodChannelDataRule(
      flood_packet_filters[flood_channel_data_rule_slot])
      ? flood_channel_data_rule_slot : -1;
}

static bool parseFloodChannelDataHopsValue(const char* value,
                                           uint8_t& max_hops) {
  value = skipFloodFilterSpaces(value);
  if (strcmp(value, "all") == 0) {
    max_hops = FLOOD_CHANNEL_HOPS_ALL;
    return true;
  }
  uint8_t parsed = 0;
  if (!parseFloodFilterUnsigned(value, 7, parsed)
      || parsed < 1 || parsed > 7) {
    return false;
  }
  max_hops = parsed;
  return true;
}

void MyMesh::formatFloodChannelDataHops(char* reply) const {
  if (flood_channel_data_rule_max_hops == FLOOD_CHANNEL_HOPS_ALL) {
    strcpy(reply, "> h=all");
  } else {
    snprintf(reply, 160, "> h>%u",
             (uint32_t)flood_channel_data_rule_max_hops);
  }
}

void MyMesh::formatFloodChannelData(char* reply) const {
  const char* state = findFloodChannelDataRule() >= 0 ? "off" : "on";
  if (flood_channel_data_rule_max_hops == FLOOD_CHANNEL_HOPS_ALL) {
    snprintf(reply, 160, "> %s h=all", state);
  } else {
    snprintf(reply, 160, "> %s h>%u", state,
             (uint32_t)flood_channel_data_rule_max_hops);
  }
}

void MyMesh::setFloodChannelData(const char* value, char* reply) {
  value = skipFloodFilterSpaces(value);
  bool enable = strcmp(value, "on") == 0;
  if (!enable && strcmp(value, "off") != 0) {
    strcpy(reply, "Error, must be on or off");
    return;
  }

  uint8_t previous_slot = flood_channel_data_rule_slot;
  int current = findFloodChannelDataRule();
  int slot = current;
  if (!enable && slot < 0) {
    for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
      if (!flood_packet_filters[i].active) {
        slot = i;
        break;
      }
    }
    if (slot < 0) {
      strcpy(reply, "Err - filter table full");
      return;
    }
  }

  FloodPacketFilterEntry previous;
  memset(&previous, 0, sizeof(previous));
  if (slot >= 0) previous = flood_packet_filters[slot];
  if (enable) {
    if (current >= 0) {
      memset(&flood_packet_filters[current], 0,
             sizeof(flood_packet_filters[current]));
    }
    flood_channel_data_rule_slot = 0xFF;
  } else {
    auto& entry = flood_packet_filters[slot];
    memset(&entry, 0, sizeof(entry));
    entry.active = true;
    entry.payload_type = PAYLOAD_TYPE_GRP_DATA;
    entry.min_hops = flood_channel_data_rule_max_hops
            == FLOOD_CHANNEL_HOPS_ALL
        ? 0 : flood_channel_data_rule_max_hops + 1;
    entry.max_hops = FLOOD_PACKET_FILTER_MAX_HOPS;
    entry.incoming_scope_kind = FloodFilterPolicy::RULE_IN_ANY;
    entry.drop_on_match = true;
    flood_channel_data_rule_slot = (uint8_t)slot;
  }

  if (!saveFloodPacketFilters()) {
    flood_channel_data_rule_slot = previous_slot;
    if (slot >= 0) flood_packet_filters[slot] = previous;
    strcpy(reply, "Err - unable to save flood.channel.data rule");
    return;
  }
  _prefs.flood_channel_data_enabled = enable ? 1 : 0;
  _prefs.flood_channel_data_max_hops = flood_channel_data_rule_max_hops;
  _cli.savePrefs(_fs, PrefsSaveRouting::Scope::Common);
  strcpy(reply, "OK");
}

void MyMesh::setFloodChannelDataHops(const char* value, char* reply) {
  uint8_t max_hops = FLOOD_CHANNEL_HOPS_ALL;
  if (!parseFloodChannelDataHopsValue(value, max_hops)) {
    strcpy(reply, "Error, must be all or 1-7");
    return;
  }

  uint8_t previous_max_hops = flood_channel_data_rule_max_hops;
  int slot = findFloodChannelDataRule();
  uint8_t previous_min_hops = slot >= 0
      ? flood_packet_filters[slot].min_hops : 0;
  flood_channel_data_rule_max_hops = max_hops;
  if (slot >= 0) {
    flood_packet_filters[slot].min_hops = max_hops
            == FLOOD_CHANNEL_HOPS_ALL
        ? 0 : max_hops + 1;
  }
  if (!saveFloodPacketFilters()) {
    flood_channel_data_rule_max_hops = previous_max_hops;
    if (slot >= 0) flood_packet_filters[slot].min_hops = previous_min_hops;
    strcpy(reply, "Err - unable to save flood.channel.data rule");
    return;
  }
  _prefs.flood_channel_data_max_hops = max_hops;
  _cli.savePrefs(_fs, PrefsSaveRouting::Scope::Common);
  strcpy(reply, "OK");
}

bool MyMesh::migrateLegacyFloodChannelData() {
  flood_channel_data_rule_slot = 0xFF;
  flood_channel_data_rule_max_hops = _prefs.flood_channel_data_max_hops;
  if (_prefs.flood_channel_data_enabled) return true;

  int slot = -1;
  for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
    if (!flood_packet_filters[i].active) {
      slot = i;
      break;
    }
  }
  if (slot < 0) {
    MESH_DEBUG_PRINTLN(
        "legacy flood.channel.data migration skipped: FPF7 table full");
    return false;
  }

  auto& entry = flood_packet_filters[slot];
  memset(&entry, 0, sizeof(entry));
  entry.active = true;
  entry.payload_type = PAYLOAD_TYPE_GRP_DATA;
  entry.min_hops = flood_channel_data_rule_max_hops
          == FLOOD_CHANNEL_HOPS_ALL
      ? 0 : flood_channel_data_rule_max_hops + 1;
  entry.max_hops = FLOOD_PACKET_FILTER_MAX_HOPS;
  entry.incoming_scope_kind = FloodFilterPolicy::RULE_IN_ANY;
  entry.drop_on_match = true;
  flood_channel_data_rule_slot = (uint8_t)slot;
  return true;
}

bool MyMesh::importLegacyFloodPolicySections() {
  if (flood_policy_has_embedded_sections) return true;
  if (_fs == NULL
      || !loadFloodPacketFilterBlacklist()
      || !loadFloodChannelScopes()
      || !migrateLegacyFloodChannelData()
      || !migrateLegacyFloodChannelBlocks()) {
    MESH_DEBUG_PRINTLN("FPF7 legacy policy import deferred");
    return false;
  }
  if (!saveFloodPacketFilters()) {
    MESH_DEBUG_PRINTLN("FPF7 legacy policy import save failed");
    return false;
  }

  // The FPF7 image is now authoritative. Failed removals are harmless: the
  // embedded-section marker prevents importing stale files again.
  if (_fs->exists(FLOOD_PACKET_FILTER_BLACKLIST_FILE))
    _fs->remove(FLOOD_PACKET_FILTER_BLACKLIST_FILE);
  if (_fs->exists(FLOOD_CHANNEL_SCOPE_FILE))
    _fs->remove(FLOOD_CHANNEL_SCOPE_FILE);
  if (_fs->exists(FLOOD_CHANNEL_SCOPE_TEMP_FILE))
    _fs->remove(FLOOD_CHANNEL_SCOPE_TEMP_FILE);
  if (_fs->exists(LEGACY_FLOOD_CHANNEL_BLOCK_FILE))
    _fs->remove(LEGACY_FLOOD_CHANNEL_BLOCK_FILE);
  return true;
}

bool MyMesh::migrateLegacyFloodChannelBlocks() {
  if (_fs == NULL || !_fs->exists(LEGACY_FLOOD_CHANNEL_BLOCK_FILE)) {
    return true;
  }

  struct LegacyRow {
    uint8_t active;
    uint8_t key_len;
    uint8_t max_hops;
    uint8_t secret[PUB_KEY_SIZE];
    char name[LEGACY_FLOOD_CHANNEL_BLOCK_NAME_LEN];
  };
  LegacyRow rows[LEGACY_FLOOD_CHANNEL_BLOCK_SLOTS];
  memset(rows, 0, sizeof(rows));

  File file = openFloodSettingsRead(_fs, LEGACY_FLOOD_CHANNEL_BLOCK_FILE);
  if (!file) return false;

  uint8_t magic[4];
  uint8_t count = 0;
  bool success = file.read(magic, sizeof(magic)) == sizeof(magic)
      && memcmp(magic, "FCB2", sizeof(magic)) == 0
      && file.read(&count, sizeof(count)) == sizeof(count)
      && count <= LEGACY_FLOOD_CHANNEL_BLOCK_SLOTS;
  for (int i = 0; success && i < count; i++) {
    uint8_t ignored_hash_prefix[FLOOD_CHANNEL_KEY_PREFIX_LEN];
    success = file.read(&rows[i].active, sizeof(rows[i].active))
            == sizeof(rows[i].active)
        && file.read(&rows[i].key_len, sizeof(rows[i].key_len))
            == sizeof(rows[i].key_len)
        && file.read(&rows[i].max_hops, sizeof(rows[i].max_hops))
            == sizeof(rows[i].max_hops)
        && file.read(ignored_hash_prefix, sizeof(ignored_hash_prefix))
            == sizeof(ignored_hash_prefix)
        && file.read(rows[i].secret, sizeof(rows[i].secret))
            == sizeof(rows[i].secret)
        && file.read((uint8_t*)rows[i].name, sizeof(rows[i].name))
            == sizeof(rows[i].name);
    if (!success || rows[i].active > 1) break;
    if (rows[i].active
        && !((rows[i].key_len == CIPHER_KEY_SIZE
                || rows[i].key_len == PUB_KEY_SIZE)
            && memchr(rows[i].name, 0, sizeof(rows[i].name)) != NULL
            && rows[i].name[0] != 0
            && (rows[i].max_hops == FLOOD_CHANNEL_HOPS_ALL
                || rows[i].max_hops
                    == LEGACY_FLOOD_CHANNEL_BLOCK_HOPS_INHERIT
                || (rows[i].max_hops >= 1 && rows[i].max_hops <= 7)))) {
      success = false;
    }
  }
  file.close();
  if (!success) {
    MESH_DEBUG_PRINTLN("legacy channel-block migration skipped: invalid file");
    return false;
  }

  uint8_t append_slots[LEGACY_FLOOD_CHANNEL_BLOCK_SLOTS];
  uint8_t append_count = 0;
  for (int row_index = 0; row_index < count; row_index++) {
    const LegacyRow& row = rows[row_index];
    if (!row.active) continue;

    uint8_t effective_max_hops = row.max_hops;
    if (effective_max_hops == LEGACY_FLOOD_CHANNEL_BLOCK_HOPS_INHERIT) {
      effective_max_hops = _prefs.legacy_flood_channel_block_max_hops;
    }
    uint8_t min_hops = effective_max_hops == FLOOD_CHANNEL_HOPS_ALL
        ? 0 : effective_max_hops + 1;

    bool duplicate = false;
    int free_slot = -1;
    for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
      const auto& entry = flood_packet_filters[i];
      if (!entry.active) {
        if (free_slot < 0) free_slot = i;
        continue;
      }
      duplicate = entry.payload_type == FLOOD_PACKET_FILTER_ANY_TYPE
          && entry.min_hops == min_hops
          && entry.max_hops == FLOOD_PACKET_FILTER_MAX_HOPS
          && !entry.suspend_on_temp_radio
          && entry.scope_name[0] == 0
          && !entry.match_blacklisted_path
          && !entry.scope_uses_slow_timing
          && entry.incoming_scope_kind == FloodFilterPolicy::RULE_IN_ANY
          && entry.incoming_scope_name[0] == 0
          && entry.channel_key_len == row.key_len
          && memcmp(entry.channel_secret, row.secret, row.key_len) == 0
          && entry.path_hash_size == 0 && entry.path_hops == 0
          && entry.target_region_name[0] == 0
          && entry.drop_on_match && !entry.rate_limit_enabled
          && !entry.stop_on_match;
      if (duplicate) break;
    }
    if (duplicate) continue;
    if (free_slot < 0) {
      for (int i = 0; i < append_count; i++) {
        memset(&flood_packet_filters[append_slots[i]], 0,
               sizeof(flood_packet_filters[append_slots[i]]));
      }
      MESH_DEBUG_PRINTLN(
          "legacy channel-block migration skipped: FPF7 table full");
      return false;
    }

    auto& entry = flood_packet_filters[free_slot];
    memset(&entry, 0, sizeof(entry));
    entry.active = true;
    entry.payload_type = FLOOD_PACKET_FILTER_ANY_TYPE;
    entry.min_hops = min_hops;
    entry.max_hops = FLOOD_PACKET_FILTER_MAX_HOPS;
    entry.incoming_scope_kind = FloodFilterPolicy::RULE_IN_ANY;
    entry.channel_key_len = row.key_len;
    memcpy(entry.channel_secret, row.secret, sizeof(entry.channel_secret));
    if (entry.channel_key_len == CIPHER_KEY_SIZE) {
      memset(&entry.channel_secret[CIPHER_KEY_SIZE], 0,
             PUB_KEY_SIZE - CIPHER_KEY_SIZE);
    }
    mesh::Utils::sha256(&entry.channel_hash, sizeof(entry.channel_hash),
                        entry.channel_secret, entry.channel_key_len);
    StrHelper::strncpy(entry.channel_name, row.name,
                       sizeof(entry.channel_name));
    entry.drop_on_match = true;
    append_slots[append_count++] = (uint8_t)free_slot;
  }

  return true;
}

bool MyMesh::saveFloodPacketFilters(bool empty_scope_phase,
                                    bool empty_forward_phase) {
  if (_fs == NULL) return false;
  // Recovery owns transaction remnants; overwriting one could erase the only
  // complete image after a failed publish boundary.
  if (_fs->exists(FLOOD_PACKET_FILTER_TEMP_FILE)
      || _fs->exists(FLOOD_PACKET_FILTER_BACKUP_FILE)) {
    return false;
  }
  File file = openFloodSettingsWrite(_fs, FLOOD_PACKET_FILTER_TEMP_FILE);
  if (!file) return false;

  size_t bytes_written = 0;
  uint32_t write_hash = 2166136261UL;
  auto writeExact = [&file, &bytes_written, &write_hash](
      const void* source, size_t len) {
    const uint8_t* data = (const uint8_t*)source;
    if (file.write(data, len) != len) return false;
    bytes_written += len;
    write_hash = updateFloodSettingsHash(write_hash, data, len);
    return true;
  };

  const uint8_t magic[4] = {'F', 'P', 'F', '7'};
  const uint8_t count = FloodFilterPolicy::forwardPersistenceCount(
      flood_packet_filters, FLOOD_PACKET_FILTER_SLOTS,
      empty_forward_phase);
  bool success = writeExact(magic, sizeof(magic))
      && writeExact(&count, sizeof(count));
  for (int i = 0; success && i < count; i++) {
    const auto& entry = flood_packet_filters[i];
    uint8_t active = entry.active ? 1 : 0;
    uint8_t suspend_on_temp_radio = entry.suspend_on_temp_radio ? 1 : 0;
    uint8_t match_blacklisted_path = entry.match_blacklisted_path ? 1 : 0;
    uint8_t scope_requires_region_match =
        entry.incoming_scope_kind == FloodFilterPolicy::RULE_IN_ALLOWED
            ? 1 : 0;
    uint8_t scope_uses_slow_timing =
        entry.scope_uses_slow_timing ? 1 : 0;
    uint8_t drop_on_match = entry.drop_on_match ? 1 : 0;
    uint8_t rate_limit_enabled = entry.rate_limit_enabled ? 1 : 0;
    uint8_t stop_on_match = entry.stop_on_match ? 1 : 0;
    success = writeExact(&active, sizeof(active))
        && writeExact(&entry.payload_type, sizeof(entry.payload_type))
        && writeExact(&entry.min_hops, sizeof(entry.min_hops))
        && writeExact(&entry.max_hops, sizeof(entry.max_hops))
        && writeExact(&suspend_on_temp_radio, sizeof(suspend_on_temp_radio))
        && writeExact(entry.scope_name, sizeof(entry.scope_name))
        && writeExact(&match_blacklisted_path,
                      sizeof(match_blacklisted_path))
        && writeExact(&scope_requires_region_match,
                      sizeof(scope_requires_region_match))
        && writeExact(&scope_uses_slow_timing,
                      sizeof(scope_uses_slow_timing))
        && writeExact(&entry.incoming_scope_kind,
                      sizeof(entry.incoming_scope_kind))
        && writeExact(entry.incoming_scope_name,
                      sizeof(entry.incoming_scope_name))
        && writeExact(&entry.channel_key_len, sizeof(entry.channel_key_len))
        && writeExact(entry.channel_secret, sizeof(entry.channel_secret))
        && writeExact(entry.channel_name, sizeof(entry.channel_name))
        && writeExact(&entry.path_hash_size, sizeof(entry.path_hash_size))
        && writeExact(&entry.path_hops, sizeof(entry.path_hops))
        && writeExact(entry.path, sizeof(entry.path))
        && writeExact(&drop_on_match, sizeof(drop_on_match))
        && writeExact(&rate_limit_enabled, sizeof(rate_limit_enabled))
        && writeExact(&entry.rate_per_minute, sizeof(entry.rate_per_minute))
        && writeExact(entry.target_region_name,
                      sizeof(entry.target_region_name))
        && writeExact(&entry.priority, sizeof(entry.priority))
        && writeExact(&stop_on_match, sizeof(stop_on_match));
  }
  static_assert(FLOOD_CHANNEL_SCOPE_SLOTS <= 255,
                "FPF7 scope phase count is one byte");
  static_assert(FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS <= 255,
                "FPF7 direct-scope count is one byte");
  static_assert(sizeof(FloodChannelScopeEntry) == 36,
                "channel scope persistence requires 36-byte entries");
  const uint8_t section_magic[4] = {'F', 'P', 'S', '1'};
  const uint8_t channel_data_slot = empty_forward_phase
      ? 0xFF : flood_channel_data_rule_slot;
  uint8_t scope_count = 0;
  uint8_t direct_count = 0;
  if (!empty_scope_phase) {
    for (uint16_t i = 0; i < FLOOD_CHANNEL_SCOPE_SLOTS; i++) {
      if (flood_channel_scopes[i].target_id != 0) {
        scope_count = (uint8_t)(i + 1U);
      }
    }
    for (uint16_t i = 0; i < FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS; i++) {
      if (flood_channel_direct_scopes[i][0] != 0) {
        direct_count = (uint8_t)(i + 1U);
      }
    }
  }
  success = success
      && writeExact(section_magic, sizeof(section_magic))
      && writeExact(&channel_data_slot, sizeof(channel_data_slot))
      && writeExact(&flood_channel_data_rule_max_hops,
                    sizeof(flood_channel_data_rule_max_hops))
      && writeExact(&scope_count, sizeof(scope_count));
  if (success && scope_count != 0) {
    success = writeExact(flood_channel_scopes,
                         scope_count * sizeof(FloodChannelScopeEntry));
  }
  success = success && writeExact(&direct_count, sizeof(direct_count));
  if (success && direct_count != 0) {
    success = writeExact(flood_channel_direct_scopes,
                         direct_count
                             * FLOOD_PACKET_FILTER_SCOPE_NAME_LEN);
  }
  success = success
      && writeExact(&flood_packet_filter_blacklist_count,
                    sizeof(flood_packet_filter_blacklist_count));
  if (success && flood_packet_filter_blacklist_count != 0) {
    success = writeExact(
        flood_packet_filter_blacklist,
        flood_packet_filter_blacklist_count
            * FLOOD_PACKET_FILTER_PATH_ID_SIZE);
  }
  file.close();
  if (!success || !verifyFloodSettingsWrite(
          _fs, FLOOD_PACKET_FILTER_TEMP_FILE, bytes_written, write_hash)) {
    _fs->remove(FLOOD_PACKET_FILTER_TEMP_FILE);
    return false;
  }

  if (_fs->exists(FLOOD_PACKET_FILTER_FILE)
      && !_fs->rename(FLOOD_PACKET_FILTER_FILE,
                      FLOOD_PACKET_FILTER_BACKUP_FILE)) {
    _fs->remove(FLOOD_PACKET_FILTER_TEMP_FILE);
    return false;
  }
  if (!_fs->rename(FLOOD_PACKET_FILTER_TEMP_FILE,
                   FLOOD_PACKET_FILTER_FILE)) {
    // Keep both the verified new image and the old backup for boot recovery.
    return false;
  }
  if (_fs->exists(FLOOD_PACKET_FILTER_BACKUP_FILE))
    _fs->remove(FLOOD_PACKET_FILTER_BACKUP_FILE);
  flood_policy_has_embedded_sections = true;
  return true;
}
#else
bool MyMesh::loadFloodPacketFilters() {
  memset(flood_packet_filters, 0, sizeof(flood_packet_filters));
  if (_fs == NULL) {
    seedDefaultFloodPacketFilters();
    return true;
  }
  if (!_fs->exists(FLOOD_PACKET_FILTER_FILE)) {
    seedDefaultFloodPacketFilters();
    return saveFloodPacketFilters();
  }

  File file = openFloodSettingsRead(_fs, FLOOD_PACKET_FILTER_FILE);
  if (!file) return false;

  FloodPacketFilterEntry* loaded = flood_packet_filters;
  uint8_t magic[4];
  uint8_t count = 0;
  bool success = file.read(magic, sizeof(magic)) == sizeof(magic);
  bool version_6 = success && memcmp(magic, "FPF6", sizeof(magic)) == 0;
  success = version_6
      && file.read(&count, sizeof(count)) == sizeof(count)
      && count <= FLOOD_PACKET_FILTER_SLOTS;

  for (int i = 0; success && i < count; i++) {
    uint8_t active = 0;
    uint8_t suspend_on_temp_radio = 0;
    uint8_t match_blacklisted_path = 0;
    uint8_t scope_requires_region_match = 0;
    uint8_t scope_uses_slow_timing = 0;
    success = file.read(&active, sizeof(active)) == sizeof(active);
    success = success && file.read(&loaded[i].payload_type, sizeof(loaded[i].payload_type)) == sizeof(loaded[i].payload_type);
    success = success && file.read(&loaded[i].min_hops, sizeof(loaded[i].min_hops)) == sizeof(loaded[i].min_hops);
    success = success && file.read(&loaded[i].max_hops, sizeof(loaded[i].max_hops)) == sizeof(loaded[i].max_hops);
    success = success && file.read(&suspend_on_temp_radio,
                                    sizeof(suspend_on_temp_radio))
        == sizeof(suspend_on_temp_radio);
    success = success && file.read((uint8_t*)loaded[i].scope_name,
                                    sizeof(loaded[i].scope_name))
        == sizeof(loaded[i].scope_name);
    success = success
        && memchr(loaded[i].scope_name, 0, sizeof(loaded[i].scope_name)) != NULL;
    success = success && file.read(&match_blacklisted_path,
                                    sizeof(match_blacklisted_path))
        == sizeof(match_blacklisted_path);
    success = success && file.read(&scope_requires_region_match,
                                    sizeof(scope_requires_region_match))
        == sizeof(scope_requires_region_match);
    success = success && file.read(&scope_uses_slow_timing,
                                    sizeof(scope_uses_slow_timing))
        == sizeof(scope_uses_slow_timing);
    loaded[i].active = active != 0;
    loaded[i].suspend_on_temp_radio = suspend_on_temp_radio != 0;
    loaded[i].match_blacklisted_path = match_blacklisted_path != 0;
    loaded[i].scope_requires_region_match = scope_requires_region_match != 0;
    loaded[i].scope_uses_slow_timing = scope_uses_slow_timing != 0;
    if (success && (active > 1 || suspend_on_temp_radio > 1
        || match_blacklisted_path > 1 || scope_requires_region_match > 1
        || scope_uses_slow_timing > 1
        || (!loaded[i].active
            && (loaded[i].suspend_on_temp_radio || loaded[i].scope_name[0] != 0
                || loaded[i].match_blacklisted_path
                || loaded[i].scope_requires_region_match
                || loaded[i].scope_uses_slow_timing)))) {
      success = false;
    }
    if (success && loaded[i].active
        && !((loaded[i].payload_type <= PH_TYPE_MASK
                || loaded[i].payload_type == FLOOD_PACKET_FILTER_ANY_TYPE)
            && loaded[i].min_hops <= loaded[i].max_hops
            && loaded[i].max_hops <= FLOOD_PACKET_FILTER_MAX_HOPS
            && (loaded[i].scope_name[0] == 0
                || isValidStoredFloodFilterScopeName(loaded[i].scope_name))
            && (!loaded[i].scope_requires_region_match
                || loaded[i].scope_name[0] != 0)
            && (!loaded[i].scope_uses_slow_timing
                || loaded[i].scope_name[0] != 0))) {
      success = false;
    }
    if (success && loaded[i].scope_name[0] != 0) {
      StrHelper::strzcpy(loaded[i].scope_name, loaded[i].scope_name,
                         sizeof(loaded[i].scope_name));
    }
  }
  file.close();

  // A truncated or invalid file fails open; filtering must never be enabled by corrupt bytes.
  if (!success) memset(flood_packet_filters, 0, sizeof(flood_packet_filters));
  return success;
}

bool MyMesh::saveFloodPacketFilters(bool empty_scope_phase,
                                    bool empty_forward_phase) {
  (void)empty_scope_phase;
  if (_fs == NULL) return false;
  File file = openFloodSettingsWrite(_fs, FLOOD_PACKET_FILTER_FILE);
  if (!file) return false;

  const uint8_t magic[4] = {'F', 'P', 'F', '6'};
  uint8_t count = FLOOD_PACKET_FILTER_SLOTS;
  bool success = file.write(magic, sizeof(magic)) == sizeof(magic)
      && file.write(&count, sizeof(count)) == sizeof(count);
  FloodPacketFilterEntry empty_entry;
  memset(&empty_entry, 0, sizeof(empty_entry));
  for (int i = 0; success && i < FLOOD_PACKET_FILTER_SLOTS; i++) {
    const auto& entry = empty_forward_phase
        ? empty_entry : flood_packet_filters[i];
    uint8_t active = entry.active ? 1 : 0;
    uint8_t suspend_on_temp_radio = entry.suspend_on_temp_radio ? 1 : 0;
    uint8_t match_blacklisted_path = entry.match_blacklisted_path ? 1 : 0;
    uint8_t scope_requires_region_match =
        entry.scope_requires_region_match ? 1 : 0;
    uint8_t scope_uses_slow_timing =
        entry.scope_uses_slow_timing ? 1 : 0;
    success = file.write(&active, sizeof(active)) == sizeof(active);
    success = success && file.write(&entry.payload_type, sizeof(entry.payload_type)) == sizeof(entry.payload_type);
    success = success && file.write(&entry.min_hops, sizeof(entry.min_hops)) == sizeof(entry.min_hops);
    success = success && file.write(&entry.max_hops, sizeof(entry.max_hops)) == sizeof(entry.max_hops);
    success = success && file.write(&suspend_on_temp_radio, sizeof(suspend_on_temp_radio)) == sizeof(suspend_on_temp_radio);
    success = success && file.write((const uint8_t*)entry.scope_name,
                                    sizeof(entry.scope_name)) == sizeof(entry.scope_name);
    success = success && file.write(&match_blacklisted_path,
                                    sizeof(match_blacklisted_path))
        == sizeof(match_blacklisted_path);
    success = success && file.write(&scope_requires_region_match,
                                    sizeof(scope_requires_region_match))
        == sizeof(scope_requires_region_match);
    success = success && file.write(&scope_uses_slow_timing,
                                    sizeof(scope_uses_slow_timing))
        == sizeof(scope_uses_slow_timing);
  }
  file.close();
  return success;
}
#endif

bool MyMesh::floodPacketFilterBlacklistMatches(const mesh::Packet* packet) const {
  static_assert(FLOOD_PACKET_FILTER_PATH_ID_SIZE
                    == FloodFilterPolicy::BLACKLIST_ID_SIZE,
                "flood filter blacklist ID sizes must agree");
  return FloodFilterPolicy::pathMatchesBlacklist(
      packet, (const uint8_t*)flood_packet_filter_blacklist,
      flood_packet_filter_blacklist_count);
}

#if MESH_ENABLE_FLOOD_RULE_ENGINE
bool MyMesh::floodPacketFilterFieldsMatch(
    const FloodPacketFilterEntry& entry, const mesh::Packet* packet,
    bool incoming_is_scoped, uint16_t incoming_transport_code,
    bool incoming_region_allowed,
    const RegionEntry* incoming_region) const {
  if (!entry.active || packet == NULL || !packet->isRouteFlood()) return false;
  if (entry.suspend_on_temp_radio && isTempRadioActive()) return false;
  if (entry.match_blacklisted_path && !floodPacketFilterBlacklistMatches(packet)) {
    return false;
  }
  if (!FloodFilterPolicy::pathStartsWith(
          packet, entry.path_hash_size, entry.path_hops, entry.path)) {
    return false;
  }

  uint8_t type = packet->getPayloadType();
  uint8_t hops = packet->getPathHashCount();
  if ((entry.payload_type != FLOOD_PACKET_FILTER_ANY_TYPE && entry.payload_type != type)
      || hops < entry.min_hops || hops > entry.max_hops) {
    return false;
  }

  if (entry.incoming_scope_kind == FloodFilterPolicy::RULE_IN_REGION) {
    if (incoming_region == NULL
        || !RegionNameUtils::equivalent(
            entry.incoming_scope_name, incoming_region->name)) {
      return false;
    }
  } else {
    uint16_t wanted_transport_code = 0;
    if (entry.incoming_scope_kind == FloodFilterPolicy::RULE_IN_SCOPE) {
      TransportKey incoming_scope;
      deriveFloodFilterScopeKey(entry.incoming_scope_name, incoming_scope);
      wanted_transport_code = incoming_scope.calcTransportCode(packet);
    }
    if (!FloodFilterPolicy::ruleIncomingScopeMatches(
            entry.incoming_scope_kind, incoming_is_scoped,
            incoming_transport_code, incoming_region_allowed,
            wanted_transport_code)) {
      return false;
    }
  }

  if (entry.channel_key_len != 0) {
    if ((type != PAYLOAD_TYPE_GRP_TXT && type != PAYLOAD_TYPE_GRP_DATA)
        || packet->payload_len
            < PATH_HASH_SIZE + CIPHER_MAC_SIZE + CIPHER_BLOCK_SIZE
        || ((packet->payload_len - PATH_HASH_SIZE - CIPHER_MAC_SIZE)
            % CIPHER_BLOCK_SIZE) != 0
        || packet->payload[0] != entry.channel_hash) {
      return false;
    }
  }

  return true;
}

bool MyMesh::authenticateFloodPacketFilterChannel(
    const FloodPacketFilterEntry& entry,
    const mesh::Packet* packet) const {
  if (entry.channel_key_len == 0) return true;
  uint8_t data[MAX_PACKET_PAYLOAD];
  return mesh::Utils::MACThenDecrypt(
      entry.channel_secret, data, &packet->payload[PATH_HASH_SIZE],
      packet->payload_len - PATH_HASH_SIZE) > 0;
}

int MyMesh::nextFloodPacketFilterMatch(uint64_t match_mask,
                                       uint64_t visited_mask) const {
  uint8_t priorities[FLOOD_PACKET_FILTER_SLOTS];
  for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
    priorities[i] = flood_packet_filters[i].priority;
  }
  return FloodFilterPolicy::nextOrderedRule(
      match_mask, visited_mask, priorities, FLOOD_PACKET_FILTER_SLOTS);
}

bool MyMesh::resolveFloodPacketFilterTargetRegion(
    const char* name, TransportKey& scope,
    const char*& canonical_name) {
  if (name == NULL || name[0] == 0) return false;
  RegionEntry* region = region_map.findByName(name);
  if (region == NULL || region->isWildcard()
      || (region->flags & REGION_DENY_FLOOD) != 0
      || region_map.getTransportKeysFor(*region, &scope, 1) <= 0
      || scope.isNull()) {
    return false;
  }
  canonical_name = region->name;
  return true;
}

uint64_t MyMesh::applyFloodPacketFilterStop(uint64_t match_mask) {
  uint8_t priorities[FLOOD_PACKET_FILTER_SLOTS];
  uint8_t stop_flags[FLOOD_PACKET_FILTER_SLOTS];
  for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
    priorities[i] = flood_packet_filters[i].priority;
    const auto& entry = flood_packet_filters[i];
    bool region_usable = true;
    if (entry.target_region_name[0] != 0) {
      TransportKey scope;
      const char* canonical_name = NULL;
      region_usable = resolveFloodPacketFilterTargetRegion(
          entry.target_region_name, scope, canonical_name);
    }
    stop_flags[i] = FloodFilterPolicy::stopActionApplies(
        entry.stop_on_match, entry.target_region_name[0] != 0,
        region_usable) ? 1 : 0;
  }
  return FloodFilterPolicy::truncateRulesAtStop(
      match_mask, priorities, stop_flags, FLOOD_PACKET_FILTER_SLOTS);
}

uint64_t MyMesh::evaluateFloodPacketFilterMatches(
    const mesh::Packet* packet, bool incoming_region_allowed,
    const RegionEntry* incoming_region) {
  static_assert(FLOOD_PACKET_FILTER_SLOTS <= 64,
                "flood filter match mask supports at most 64 slots");
  if (packet == NULL || !packet->isRouteFlood()) return 0;
  bool incoming_is_scoped =
      packet->getRouteType() == ROUTE_TYPE_TRANSPORT_FLOOD;
  uint16_t incoming_transport_code = incoming_is_scoped
      ? packet->transport_codes[0] : 0;
  bool channel_auth_checked[FLOOD_PACKET_FILTER_SLOTS] = { false };
  bool channel_auth_valid[FLOOD_PACKET_FILTER_SLOTS] = { false };
  uint64_t matches = 0;
  for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
    const auto& entry = flood_packet_filters[i];
    if (!floodPacketFilterFieldsMatch(
            entry, packet, incoming_is_scoped, incoming_transport_code,
            incoming_region_allowed, incoming_region)) {
      continue;
    }
    bool authenticated = true;
    if (entry.channel_key_len != 0) {
      int cached = -1;
      for (int j = 0; j < i; j++) {
        if (channel_auth_checked[j]
            && FloodFilterPolicy::sameChannelKey(
                flood_packet_filters[j].channel_key_len,
                flood_packet_filters[j].channel_secret,
                entry.channel_key_len, entry.channel_secret)) {
          cached = j;
          break;
        }
      }
      authenticated = cached >= 0
          ? channel_auth_valid[cached]
          : authenticateFloodPacketFilterChannel(entry, packet);
      channel_auth_checked[i] = true;
      channel_auth_valid[i] = authenticated;
    }
    if (authenticated) matches |= (uint64_t)1U << i;
  }
  return applyFloodPacketFilterStop(matches);
}

bool MyMesh::applyFloodPacketFilterScope(mesh::Packet* packet,
                                         uint64_t match_mask,
                                         bool& scope_set,
                                         bool& fast_track,
                                         bool log_change) {
  scope_set = false;
  fast_track = false;
  if (packet == NULL || !packet->isRouteFlood()) return false;

  uint64_t visited = 0;
  while (true) {
    int i = nextFloodPacketFilterMatch(match_mask, visited);
    if (i < 0) break;
    visited |= (uint64_t)1U << i;
    const auto& entry = flood_packet_filters[i];
    if (entry.scope_name[0] == 0
        && entry.target_region_name[0] == 0) {
      continue;
    }

    TransportKey scope;
    const char* target_name = entry.scope_name;
    if (entry.scope_name[0] != 0) {
      deriveFloodFilterScopeKey(entry.scope_name, scope);
    } else {
      if (!resolveFloodPacketFilterTargetRegion(
              entry.target_region_name, scope, target_name)) {
        continue;
      }
    }
    uint16_t transport_code = scope.calcTransportCode(packet);
    bool scope_changed =
        FloodFilterPolicy::setTransportScope(packet, transport_code);

    scope_set = true;
    fast_track = FloodFilterPolicy::fastTrackScopeChange(
        scope_changed, entry.scope_uses_slow_timing);
    if (scope_changed && log_change) {
      MESH_DEBUG_PRINTLN("flood.filter set scope slot=%d type=%d hops=%d scope=%s tx=%s",
                         i + 1, packet->getPayloadType(), packet->getPathHashCount(),
                         target_name,
                         entry.scope_uses_slow_timing ? "slow" : "fast");
    }
    return scope_changed;
  }
  return false;
}

bool MyMesh::shouldBlockFloodPacketForward(const mesh::Packet* packet) const {
  if (packet == NULL || !packet->isRouteFlood()) return false;
  uint8_t type = packet->getPayloadType();
  uint8_t hops = packet->getPathHashCount();

  uint64_t visited = 0;
  while (true) {
    int i = nextFloodPacketFilterMatch(recv_pkt_filter_match_mask, visited);
    if (i < 0) break;
    visited |= (uint64_t)1U << i;
    const auto& entry = flood_packet_filters[i];
    bool blocked = entry.drop_on_match;
    if (entry.rate_limit_enabled) {
      uint32_t now = _ms->getMillis();
      if (FloodFilterPolicy::rateLimitReached(
              entry.rate_window_active, now, entry.rate_window_started,
              entry.rate_window_count, entry.rate_per_minute)) {
        blocked = true;
      }
    }
    if (blocked) {
      MESH_DEBUG_PRINTLN("allowPacketForward: flood.filter matched slot=%d type=%d hops=%d",
                         i + 1, type, hops);
      return true;
    }
  }
  return false;
}

void MyMesh::commitFloodPacketFilterRates(const mesh::Packet* packet) {
  if (packet == NULL || !packet->isRouteFlood()) return;
  uint32_t now = _ms->getMillis();
  for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
    auto& entry = flood_packet_filters[i];
    if ((recv_pkt_filter_match_mask & ((uint64_t)1U << i)) == 0
        || !entry.rate_limit_enabled) {
      continue;
    }
    if (!entry.rate_window_active
        || now - entry.rate_window_started >= 60000UL) {
      entry.rate_window_active = true;
      entry.rate_window_started = now;
      entry.rate_window_count = 0;
    }
    if (entry.rate_window_count < 0xFFFF) entry.rate_window_count++;
  }
}
#else
bool MyMesh::floodPacketFilterFieldsMatch(
    const FloodPacketFilterEntry& entry, const mesh::Packet* packet,
    bool incoming_is_scoped, uint16_t incoming_transport_code,
    bool incoming_region_allowed,
    const RegionEntry* incoming_region) const {
  (void)incoming_is_scoped;
  (void)incoming_transport_code;
  (void)incoming_region_allowed;
  (void)incoming_region;
  if (!entry.active || packet == NULL || !packet->isRouteFlood()) return false;
  if (entry.suspend_on_temp_radio && isTempRadioActive()) return false;
  if (entry.match_blacklisted_path
      && !floodPacketFilterBlacklistMatches(packet)) return false;

  uint8_t type = packet->getPayloadType();
  uint8_t hops = packet->getPathHashCount();
  return (entry.payload_type == FLOOD_PACKET_FILTER_ANY_TYPE
          || entry.payload_type == type)
      && hops >= entry.min_hops && hops <= entry.max_hops;
}

uint64_t MyMesh::evaluateFloodPacketFilterMatches(
    const mesh::Packet* packet, bool incoming_region_allowed,
    const RegionEntry* incoming_region) {
  (void)incoming_region;
  uint64_t matches = 0;
  for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
    const auto& entry = flood_packet_filters[i];
    if (floodPacketFilterFieldsMatch(entry, packet, false, 0,
                                     incoming_region_allowed, NULL)
        && (!entry.scope_requires_region_match
            || incoming_region_allowed)) {
      matches |= (uint64_t)1U << i;
    }
  }
  return matches;
}

bool MyMesh::applyFloodPacketFilterScope(mesh::Packet* packet,
                                         uint64_t match_mask,
                                         bool& scope_set,
                                         bool& fast_track,
                                         bool log_change) {
  scope_set = false;
  fast_track = false;
  if (packet == NULL || !packet->isRouteFlood()) return false;
  for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
    const auto& entry = flood_packet_filters[i];
    if ((match_mask & ((uint64_t)1U << i)) == 0
        || entry.scope_name[0] == 0) continue;

    TransportKey scope;
    deriveFloodFilterScopeKey(entry.scope_name, scope);
    bool scope_changed = FloodFilterPolicy::setTransportScope(
        packet, scope.calcTransportCode(packet));
    scope_set = true;
    fast_track = FloodFilterPolicy::fastTrackScopeChange(
        scope_changed, entry.scope_uses_slow_timing);
    if (scope_changed && log_change) {
      MESH_DEBUG_PRINTLN("flood.filter set scope slot=%d type=%d hops=%d scope=%s tx=%s",
                         i + 1, packet->getPayloadType(),
                         packet->getPathHashCount(), entry.scope_name,
                         entry.scope_uses_slow_timing ? "slow" : "fast");
    }
    return scope_changed;
  }
  return false;
}

bool MyMesh::shouldBlockFloodPacketForward(const mesh::Packet* packet) const {
  if (packet == NULL || !packet->isRouteFlood()) return false;
  uint8_t type = packet->getPayloadType();
  uint8_t hops = packet->getPathHashCount();
  for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
    const auto& entry = flood_packet_filters[i];
    if ((recv_pkt_filter_match_mask & ((uint64_t)1U << i)) != 0
        && entry.scope_name[0] == 0) {
      MESH_DEBUG_PRINTLN("allowPacketForward: flood.filter matched slot=%d type=%d hops=%d range=%d-%d",
                         i + 1, type, hops, entry.min_hops,
                         entry.max_hops);
      return true;
    }
  }
  return false;
}

void MyMesh::commitFloodPacketFilterRates(const mesh::Packet* packet) {
  (void)packet;
}
#endif

#if MESH_ENABLE_FLOOD_RULE_ENGINE
static bool floodRuleRegionNamePresent(const RegionMap& map,
                                       const char* name) {
  for (int i = 0; i < map.getCount(); i++) {
    const RegionEntry* region = map.getByIdx(i);
    if (region != NULL
        && RegionNameUtils::equivalent(region->name, name)) return true;
  }
  return false;
}

void MyMesh::formatFloodPacketFilterDetail(int index, char* reply, size_t reply_len) const {
  if (index < 0 || index >= FLOOD_PACKET_FILTER_SLOTS || !flood_packet_filters[index].active) {
    snprintf(reply, reply_len, "Err - empty filter slot");
    return;
  }
  const auto& entry = flood_packet_filters[index];
  char hops[12];
  char prefix[32];
  char incoming[48];
  char action[64];
  char rate[24];
  formatFloodFilterHopSpec(hops, sizeof(hops), entry.min_hops, entry.max_hops);
  if (entry.match_blacklisted_path) {
    strcpy(prefix, "blacklist");
  } else {
    formatFloodModerationPath(prefix, sizeof(prefix), entry.path_hash_size,
                              entry.path_hops, entry.path);
  }

  switch (entry.incoming_scope_kind) {
    case FloodFilterPolicy::RULE_IN_NONE:
      strcpy(incoming, "none");
      break;
    case FloodFilterPolicy::RULE_IN_SCOPED:
      strcpy(incoming, "scoped");
      break;
    case FloodFilterPolicy::RULE_IN_ALLOWED:
      strcpy(incoming, "allowed");
      break;
    case FloodFilterPolicy::RULE_IN_UNKNOWN:
      strcpy(incoming, "unknown");
      break;
    case FloodFilterPolicy::RULE_IN_SCOPE:
      snprintf(incoming, sizeof(incoming), "scope:%s",
               entry.incoming_scope_name);
      break;
    case FloodFilterPolicy::RULE_IN_REGION: {
      snprintf(incoming, sizeof(incoming), "region:%s%s",
               entry.incoming_scope_name,
               floodRuleRegionNamePresent(
                   region_map, entry.incoming_scope_name) ? "" : "?");
      break;
    }
    default:
      strcpy(incoming, "any");
      break;
  }

  action[0] = 0;
  if (entry.drop_on_match) {
    strcpy(action, "drop");
  } else if (entry.scope_name[0] != 0) {
    snprintf(action, sizeof(action), "scope=%s", entry.scope_name);
  } else if (entry.target_region_name[0] != 0) {
    snprintf(action, sizeof(action), "region=%s%s",
             entry.target_region_name,
             floodRuleRegionNamePresent(
                 region_map, entry.target_region_name) ? "" : "?");
  }
  rate[0] = 0;
  if (entry.rate_limit_enabled) {
    snprintf(rate, sizeof(rate), "%srate=%u/min",
             action[0] == 0 ? "" : " ",
             (unsigned int)entry.rate_per_minute);
  }
  int written = snprintf(
      reply, reply_len,
      "> %d type=%s hops=%s channel=%s prefix=%s in=%s %s%s priority=%u%s%s%s",
      index + 1, floodFilterPayloadTypeName(entry.payload_type), hops,
      entry.channel_key_len == 0 ? "*" : entry.channel_name,
      prefix, incoming, action, rate,
      (unsigned int)entry.priority,
      entry.stop_on_match ? " stop" : "",
      entry.scope_uses_slow_timing ? " tx=slow" : "",
      entry.suspend_on_temp_radio ? " suspend=tempradio" : "");
  if (written >= 0 && (size_t)written < reply_len) return;

  // A fully populated row can exceed the 160-byte CLI reply. Fall back to a
  // compact, accepted-by-set spelling instead of silently hiding tail fields.
  char compact_type[4];
  if (entry.payload_type == FLOOD_PACKET_FILTER_ANY_TYPE) {
    strcpy(compact_type, "any");
  } else {
    snprintf(compact_type, sizeof(compact_type), "%u",
             (unsigned int)entry.payload_type);
  }
  char compact_incoming[40];
  switch (entry.incoming_scope_kind) {
    case FloodFilterPolicy::RULE_IN_NONE:
      strcpy(compact_incoming, "n");
      break;
    case FloodFilterPolicy::RULE_IN_SCOPED:
      strcpy(compact_incoming, "s");
      break;
    case FloodFilterPolicy::RULE_IN_ALLOWED:
      strcpy(compact_incoming, "a");
      break;
    case FloodFilterPolicy::RULE_IN_UNKNOWN:
      strcpy(compact_incoming, "u");
      break;
    case FloodFilterPolicy::RULE_IN_SCOPE:
      snprintf(compact_incoming, sizeof(compact_incoming), "s:%s",
               entry.incoming_scope_name);
      break;
    case FloodFilterPolicy::RULE_IN_REGION: {
      snprintf(compact_incoming, sizeof(compact_incoming), "r:%s",
               entry.incoming_scope_name);
      break;
    }
    default:
      strcpy(compact_incoming, "*");
      break;
  }

  char compact_action[40];
  if (entry.drop_on_match) {
    strcpy(compact_action, " drop");
  } else if (entry.scope_name[0] != 0) {
    snprintf(compact_action, sizeof(compact_action), " scope=%s",
             entry.scope_name);
  } else if (entry.target_region_name[0] != 0) {
    snprintf(compact_action, sizeof(compact_action), " region=%s",
             entry.target_region_name);
  } else {
    compact_action[0] = 0;
  }
  char compact_rate[12];
  compact_rate[0] = 0;
  if (entry.rate_limit_enabled) {
    snprintf(compact_rate, sizeof(compact_rate), " q=%u",
             (unsigned int)entry.rate_per_minute);
  }
  char compact_flags[8];
  compact_flags[0] = 0;
  if (entry.scope_uses_slow_timing || entry.suspend_on_temp_radio) {
    snprintf(compact_flags, sizeof(compact_flags), " f=%s%s",
             entry.scope_uses_slow_timing ? "s" : "",
             entry.suspend_on_temp_radio ? "t" : "");
  }
  snprintf(reply, reply_len,
           ">%d %s %s c=%s p=%s i=%s%s%s pri=%u%s%s",
           index + 1, compact_type, hops,
           entry.channel_key_len == 0 ? "*" : entry.channel_name,
           prefix, compact_incoming, compact_action, compact_rate,
           (unsigned int)entry.priority,
           entry.stop_on_match ? " stop" : "",
           compact_flags);
}

void MyMesh::formatFloodPacketFilters(const char* args, char* reply) const {
  const char* selector = skipFloodFilterSpaces(args);
  if (*selector == '.') selector = skipFloodFilterSpaces(selector + 1);
  if (*selector != 0) {
    uint8_t slot;
    if (!parseFloodFilterUnsigned(selector, FLOOD_PACKET_FILTER_SLOTS, slot) || slot == 0) {
      snprintf(reply, 160, "Err - filter slot must be 1-%d", FLOOD_PACKET_FILTER_SLOTS);
      return;
    }
    formatFloodPacketFilterDetail(slot - 1, reply, 160);
    return;
  }

  size_t used = (size_t)snprintf(reply, 160, ">");
  int active_count = 0;
  bool truncated = false;
  for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
    const auto& entry = flood_packet_filters[i];
    if (!entry.active) continue;
    active_count++;
    char hops[12];
    char item[120];
    char target[44];
    char priority[8];
    formatFloodFilterHopSpec(hops, sizeof(hops), entry.min_hops, entry.max_hops);
    if (entry.drop_on_match) {
      strcpy(target, "!drop");
    } else if (entry.scope_name[0] != 0) {
      snprintf(target, sizeof(target), ">%s", entry.scope_name);
    } else if (entry.target_region_name[0] != 0) {
      snprintf(target, sizeof(target), ">r:%s",
               entry.target_region_name);
    } else {
      target[0] = 0;
    }
    priority[0] = 0;
    if (entry.priority != 0) {
      snprintf(priority, sizeof(priority), "^%u",
               (unsigned int)entry.priority);
    }
    snprintf(item, sizeof(item), " %d=%s@%s%s%s%s%s%s%s%s%s",
             i + 1, floodFilterPayloadTypeName(entry.payload_type), hops,
             entry.match_blacklisted_path ? "?blacklist" : "",
             target, priority,
             entry.stop_on_match ? "~stop" : "",
             entry.rate_limit_enabled ? "~rate" : "",
             entry.scope_uses_slow_timing ? "~slow" : "",
             entry.suspend_on_temp_radio ? "~tempradio" : "",
             i == findFloodChannelDataRule() ? "~data" : "");
    size_t item_len = strlen(item);
    if (used + item_len >= 156) {
      truncated = true;
      break;
    }
    memcpy(&reply[used], item, item_len + 1);
    used += item_len;
  }
  if (active_count == 0) {
    strcpy(reply, "> off");
  } else if (truncated) {
    StrHelper::strncpy(&reply[used], " ...", 160 - used);
  }
}

void MyMesh::setFloodPacketFilter(const char* args, char* reply,
                                  bool require_explicit_action) {
  const char* cursor = skipFloodFilterSpaces(args);
  int requested_slot = -1;
  if (*cursor == '.') {
    cursor++;
    const char* slot_start = cursor;
    while (*cursor >= '0' && *cursor <= '9') cursor++;
    size_t slot_len = (size_t)(cursor - slot_start);
    char slot_text[8];
    if (slot_len == 0 || slot_len >= sizeof(slot_text)) {
      snprintf(reply, 160, "Err - filter slot must be 1-%d", FLOOD_PACKET_FILTER_SLOTS);
      return;
    }
    memcpy(slot_text, slot_start, slot_len);
    slot_text[slot_len] = 0;
    uint8_t slot;
    if (!parseFloodFilterUnsigned(slot_text, FLOOD_PACKET_FILTER_SLOTS, slot) || slot == 0) {
      snprintf(reply, 160, "Err - filter slot must be 1-%d", FLOOD_PACKET_FILTER_SLOTS);
      return;
    }
    requested_slot = slot - 1;
    if (*cursor != ' ') {
      strcpy(reply, FLOOD_PACKET_FILTER_USAGE);
      return;
    }
  }
  cursor = skipFloodFilterSpaces(cursor);
  if (strlen(cursor) >= 192) {
    strcpy(reply, "Err - rule parameters too long");
    return;
  }

  char params[192];
  strcpy(params, cursor);
  char* tokens[18];
  int token_count = 0;
  char* token = params;
  while (*token != 0) {
    if (token_count >= 18) {
      strcpy(reply, "Err - too many rule parameters");
      return;
    }
    tokens[token_count++] = token;
    char* separator = strchr(token, ' ');
    if (separator == NULL) break;
    *separator++ = 0;
    while (*separator == ' ') separator++;
    token = separator;
  }
  if (token_count == 0) {
    strcpy(reply, FLOOD_PACKET_FILTER_USAGE);
    return;
  }

  uint8_t payload_type;
  const char* type_text = floodFilterAsciiStartsWith(tokens[0], "type=")
      ? tokens[0] + strlen("type=") : tokens[0];
  if (!parseFloodFilterPayloadType(type_text, payload_type)) {
    strcpy(reply, "Err - packet type must be name, any, 0-15, or 0x00-0x0F");
    return;
  }

  uint8_t min_hops = 0;
  uint8_t max_hops = FLOOD_PACKET_FILTER_MAX_HOPS;
  bool hops_set = false;
  bool suspend_on_temp_radio = false;
  bool match_blacklisted_path = false;
  bool path_set = false;
  uint8_t path_hash_size = 0;
  uint8_t path_hops = 0;
  uint8_t path[FLOOD_PACKET_FILTER_PATH_PREFIX_BYTES_MAX];
  memset(path, 0, sizeof(path));
  uint8_t incoming_scope_kind = FloodFilterPolicy::RULE_IN_ANY;
  char incoming_scope_name[FLOOD_PACKET_FILTER_SCOPE_NAME_LEN];
  memset(incoming_scope_name, 0, sizeof(incoming_scope_name));
  bool incoming_set = false;
  uint8_t channel_key_len = 0;
  uint8_t channel_hash = 0;
  uint8_t channel_secret[PUB_KEY_SIZE];
  memset(channel_secret, 0, sizeof(channel_secret));
  char channel_name[FLOOD_GROUP_MODERATION_NAME_LEN];
  memset(channel_name, 0, sizeof(channel_name));
  bool channel_set = false;
  bool scope_timing_set = false;
  bool scope_uses_slow_timing = false;
  char scope_name[FLOOD_PACKET_FILTER_SCOPE_NAME_LEN];
  memset(scope_name, 0, sizeof(scope_name));
  char target_region_name[FLOOD_PACKET_FILTER_SCOPE_NAME_LEN];
  memset(target_region_name, 0, sizeof(target_region_name));
  bool target_set = false;
  bool drop_on_match = false;
  bool drop_set = false;
  bool rate_limit_enabled = false;
  uint16_t rate_per_minute = 0;
  uint8_t priority = 0;
  bool priority_set = false;
  bool stop_on_match = false;
  for (int i = 1; i < token_count; i++) {
    if (floodFilterAsciiEqual(tokens[i], "suspend=tempradio")) {
      if (suspend_on_temp_radio) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      suspend_on_temp_radio = true;
    } else if (floodFilterAsciiStartsWith(tokens[i], "f=")) {
      const char* flags = tokens[i] + 2;
      if (*flags == 0) {
        strcpy(reply, "Err - compact flags are s and/or t");
        return;
      }
      while (*flags != 0) {
        if (*flags == 's' || *flags == 'S') {
          if (scope_timing_set) {
            strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
            return;
          }
          scope_timing_set = true;
          scope_uses_slow_timing = true;
        } else if (*flags == 't' || *flags == 'T') {
          if (suspend_on_temp_radio) {
            strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
            return;
          }
          suspend_on_temp_radio = true;
        } else {
          strcpy(reply, "Err - compact flags are s and/or t");
          return;
        }
        flags++;
      }
    } else if (floodFilterAsciiEqual(tokens[i], "path=blacklist")) {
      if (path_set) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      path_set = true;
      match_blacklisted_path = true;
    } else if (floodFilterAsciiEqual(tokens[i], "require=region")) {
      if (incoming_set) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      incoming_set = true;
      incoming_scope_kind = FloodFilterPolicy::RULE_IN_ALLOWED;
    } else if (floodFilterAsciiStartsWith(tokens[i], "in=")
        || floodFilterAsciiStartsWith(tokens[i], "i=")) {
      if (incoming_set) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      incoming_set = true;
      const bool compact = floodFilterAsciiStartsWith(tokens[i], "i=");
      const char* value = tokens[i] + (compact ? 2 : strlen("in="));
      if (floodFilterAsciiEqual(value, "any")
          || (compact && floodFilterAsciiEqual(value, "*"))) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_ANY;
      } else if (floodFilterAsciiEqual(value, "none")
          || floodFilterAsciiEqual(value, "unscoped")
          || (compact && floodFilterAsciiEqual(value, "n"))) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_NONE;
      } else if (floodFilterAsciiEqual(value, "scoped")) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_SCOPED;
      } else if (compact && floodFilterAsciiEqual(value, "s")) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_SCOPED;
      } else if (floodFilterAsciiEqual(value, "allowed")
          || floodFilterAsciiEqual(value, "known")
          || (compact && floodFilterAsciiEqual(value, "a"))) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_ALLOWED;
      } else if (floodFilterAsciiEqual(value, "unknown")
          || (compact && floodFilterAsciiEqual(value, "u"))) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_UNKNOWN;
      } else if (floodFilterAsciiStartsWith(value, "scope:")
          || (compact && floodFilterAsciiStartsWith(value, "s:"))) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_SCOPE;
        if (!normalizeFloodFilterScopeName(
                value + (floodFilterAsciiStartsWith(value, "scope:")
                    ? strlen("scope:") : 2), incoming_scope_name,
                sizeof(incoming_scope_name))) {
          strcpy(reply, "Err - bad incoming scope name");
          return;
        }
      } else if (floodFilterAsciiStartsWith(value, "region:")
          || (compact && floodFilterAsciiStartsWith(value, "r:"))) {
        RegionEntry* region = region_map.findByNamePrefix(
            value + (floodFilterAsciiStartsWith(value, "region:")
                ? strlen("region:") : 2));
        if (region == NULL || region->isWildcard()) {
          strcpy(reply, "Err - bad incoming region");
          return;
        }
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_REGION;
        StrHelper::strzcpy(incoming_scope_name, region->name,
                           sizeof(incoming_scope_name));
      } else {
        strcpy(reply, "Err - in=any|none|scoped|allowed|unknown|scope:name|region:name");
        return;
      }
    } else if (floodFilterAsciiStartsWith(tokens[i], "priority=")
        || floodFilterAsciiStartsWith(tokens[i], "pri=")) {
      if (priority_set) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      const char* value = strchr(tokens[i], '=') + 1;
      uint32_t parsed = 0;
      if (!parseFloodModerationUnsigned(value, 255, parsed)) {
        strcpy(reply, "Err - priority must be 0-255");
        return;
      }
      priority = (uint8_t)parsed;
      priority_set = true;
    } else if (floodFilterAsciiEqual(tokens[i], "stop")
        || floodFilterAsciiEqual(tokens[i], "action=stop")) {
      if (stop_on_match) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      stop_on_match = true;
    } else if (floodFilterAsciiStartsWith(tokens[i], "tx=")) {
      if (scope_timing_set) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      if (floodFilterAsciiEqual(tokens[i], "tx=slow")) {
        scope_uses_slow_timing = true;
      } else if (!floodFilterAsciiEqual(tokens[i], "tx=fast")) {
        strcpy(reply, "Err - tx timing must be fast or slow");
        return;
      }
      scope_timing_set = true;
    } else if (floodFilterAsciiStartsWith(tokens[i], "scope=")) {
      if (target_set || drop_set) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      if (!normalizeFloodFilterScopeName(tokens[i] + strlen("scope="),
                                         scope_name, sizeof(scope_name))) {
        strcpy(reply, "Err - scope must be a public name of at most 30 characters");
        return;
      }
      target_set = true;
    } else if (floodFilterAsciiStartsWith(tokens[i], "region=")) {
      if (target_set || drop_set) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      RegionEntry* region = region_map.findByNamePrefix(
          tokens[i] + strlen("region="));
      TransportKey target_scope;
      if (region == NULL || region->isWildcard()
          || (region->flags & REGION_DENY_FLOOD) != 0
          || region_map.getTransportKeysFor(*region, &target_scope, 1) <= 0
          || target_scope.isNull()) {
        strcpy(reply, "Err - bad target region");
        return;
      }
      StrHelper::strzcpy(target_region_name, region->name,
                         sizeof(target_region_name));
      target_set = true;
    } else if (floodFilterAsciiEqual(tokens[i], "drop")
        || floodFilterAsciiEqual(tokens[i], "action=drop")) {
      if (drop_set || target_set) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      drop_on_match = true;
      drop_set = true;
    } else if (floodFilterAsciiStartsWith(tokens[i], "rate=")
        || floodFilterAsciiStartsWith(tokens[i], "q=")) {
      if (rate_limit_enabled) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      char rate_text[24];
      const bool compact = floodFilterAsciiStartsWith(tokens[i], "q=");
      StrHelper::strncpy(rate_text,
                         tokens[i] + (compact ? 2 : strlen("rate=")),
                         sizeof(rate_text));
      char* slash = strchr(rate_text, '/');
      if ((!compact && slash == NULL)
          || (slash != NULL && !(strcmp(slash, "/min") == 0
              || strcmp(slash, "/m") == 0))) {
        strcpy(reply, "Err - rate format is X/min");
        return;
      }
      if (slash != NULL) *slash = 0;
      uint32_t parsed = 0;
      if (!parseFloodModerationUnsigned(
              rate_text, FLOOD_GROUP_MODERATION_RATE_UNLIMITED - 1,
              parsed)) {
        strcpy(reply, "Err - rate must be 0-65534/min");
        return;
      }
      rate_per_minute = (uint16_t)parsed;
      rate_limit_enabled = true;
    } else if (floodFilterAsciiStartsWith(tokens[i], "channel=")
        || floodFilterAsciiStartsWith(tokens[i], "c=")) {
      if (channel_set) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      channel_set = true;
      const char* value = tokens[i]
          + (floodFilterAsciiStartsWith(tokens[i], "c=")
              ? 2 : strlen("channel="));
      if (!floodFilterAsciiEqual(value, "*")) {
        if (value[0] == '#'
            && strlen(value) >= sizeof(channel_name)) {
          strcpy(reply, "Err - channel name is too long");
          return;
        }
        uint8_t hash_prefix[FLOOD_CHANNEL_KEY_PREFIX_LEN];
        if (!parseFloodModerationChannel(
                value, channel_secret, channel_key_len, hash_prefix,
                channel_name, sizeof(channel_name))) {
          strcpy(reply, "Err - channel must be *, public, #channel, or a key");
          return;
        }
        channel_hash = hash_prefix[0];
      }
    } else if (floodFilterAsciiStartsWith(tokens[i], "prefix=")
        || floodFilterAsciiStartsWith(tokens[i], "p=")
        || (floodFilterAsciiStartsWith(tokens[i], "path=")
            && !floodFilterAsciiEqual(tokens[i], "path=blacklist"))) {
      if (path_set) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      const char* value = strchr(tokens[i], '=') + 1;
      static_assert(FLOOD_PACKET_FILTER_PATH_PREFIX_BYTES_MAX
                        == FLOOD_GROUP_MODERATION_PATH_BYTES_MAX,
                    "rule and moderation path buffers must agree");
      if (!parseFloodModerationPath(value, path_hash_size, path_hops,
                                    path)) {
        strcpy(reply, "Err - prefix is * or 1-3 comma-separated 1/2/3-byte IDs");
        return;
      }
      path_set = true;
    } else if (floodFilterAsciiStartsWith(tokens[i], "hops=")) {
      if (hops_set || !parseFloodFilterHopSpec(
              tokens[i] + strlen("hops="), min_hops, max_hops)) {
        strcpy(reply, "Err - hops must be all, N, N+, or N-M (0-63)");
        return;
      }
      hops_set = true;
    } else if (!hops_set
        && parseFloodFilterHopSpec(tokens[i], min_hops, max_hops)) {
      hops_set = true;
    } else {
      strcpy(reply, FLOOD_PACKET_FILTER_USAGE);
      return;
    }
  }
  if (scope_timing_set && !target_set) {
    strcpy(reply, "Err - tx timing requires scope= or region=");
    return;
  }
  if (drop_on_match && rate_limit_enabled) {
    strcpy(reply, "Err - drop cannot be combined with rate");
    return;
  }
  if (channel_key_len != 0 && payload_type != FLOOD_PACKET_FILTER_ANY_TYPE
      && payload_type != PAYLOAD_TYPE_GRP_TXT
      && payload_type != PAYLOAD_TYPE_GRP_DATA) {
    strcpy(reply, "Err - channel matcher requires type=any|grp_txt|grp_data");
    return;
  }
  bool action_set = drop_set || target_set || rate_limit_enabled
      || stop_on_match;
  if (require_explicit_action && !action_set) {
    strcpy(reply,
           "Err - flood.rule requires drop, scope=, region=, rate=, or stop");
    return;
  }
  // Preserve the positional flood.filter behavior. The flood.rule alias is
  // strict so an omitted action cannot silently install a deny rule.
  if (!action_set) drop_on_match = true;

  FloodPacketFilterEntry candidate;
  memset(&candidate, 0, sizeof(candidate));
  candidate.active = true;
  candidate.payload_type = payload_type;
  candidate.min_hops = min_hops;
  candidate.max_hops = max_hops;
  candidate.suspend_on_temp_radio = suspend_on_temp_radio;
  candidate.match_blacklisted_path = match_blacklisted_path;
  candidate.scope_uses_slow_timing = scope_uses_slow_timing;
  candidate.incoming_scope_kind = incoming_scope_kind;
  StrHelper::strzcpy(candidate.incoming_scope_name, incoming_scope_name,
                     sizeof(candidate.incoming_scope_name));
  candidate.channel_key_len = channel_key_len;
  candidate.channel_hash = channel_hash;
  memcpy(candidate.channel_secret, channel_secret,
         sizeof(candidate.channel_secret));
  StrHelper::strzcpy(candidate.channel_name, channel_name,
                     sizeof(candidate.channel_name));
  candidate.path_hash_size = path_hash_size;
  candidate.path_hops = path_hops;
  memcpy(candidate.path, path, sizeof(candidate.path));
  StrHelper::strzcpy(candidate.target_region_name, target_region_name,
                     sizeof(candidate.target_region_name));
  candidate.drop_on_match = drop_on_match;
  candidate.rate_limit_enabled = rate_limit_enabled;
  candidate.rate_per_minute = rate_per_minute;
  candidate.priority = priority;
  candidate.stop_on_match = stop_on_match;
  StrHelper::strzcpy(candidate.scope_name, scope_name,
                     sizeof(candidate.scope_name));

  int slot = requested_slot;
  if (slot < 0) {
    for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
      // Keep the compatibility-owned row distinct from an ordinary rule with
      // identical match/action fields. Otherwise a generic, unnumbered set
      // would silently detach flood.channel.data from its own row.
      if (i == flood_channel_data_rule_slot) continue;
      const auto& entry = flood_packet_filters[i];
      if (entry.active
          && memcmp(&entry, &candidate,
                    offsetof(FloodPacketFilterEntry,
                             rate_window_started)) == 0) {
        slot = i;
        break;
      }
    }
  }
  if (slot < 0) {
    for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
      if (!flood_packet_filters[i].active) {
        slot = i;
        break;
      }
    }
  }
  if (slot < 0) {
    strcpy(reply, "Err - filter table full");
    return;
  }

  FloodPacketFilterEntry previous = flood_packet_filters[slot];
  uint8_t previous_channel_data_slot = flood_channel_data_rule_slot;
  if (slot == flood_channel_data_rule_slot) {
    flood_channel_data_rule_slot = 0xFF;
  }
  flood_packet_filters[slot] = candidate;
  if (!saveFloodPacketFilters()) {
    flood_packet_filters[slot] = previous;
    flood_channel_data_rule_slot = previous_channel_data_slot;
    strcpy(reply, "Err - unable to save flood filter");
    return;
  }

  char detail[160];
  formatFloodPacketFilterDetail(slot, detail, sizeof(detail));
  snprintf(reply, 160, "OK - %s", detail[0] == '>' ? skipFloodFilterSpaces(detail + 1) : detail);
}
#else
void MyMesh::formatFloodPacketFilterDetail(int index, char* reply, size_t reply_len) const {
  if (index < 0 || index >= FLOOD_PACKET_FILTER_SLOTS || !flood_packet_filters[index].active) {
    snprintf(reply, reply_len, "Err - empty filter slot");
    return;
  }
  const auto& entry = flood_packet_filters[index];
  char hops[12];
  formatFloodFilterHopSpec(hops, sizeof(hops), entry.min_hops, entry.max_hops);
  const char* suspension = entry.suspend_on_temp_radio ? " suspend=tempradio" : "";
  const char* scope_prefix = entry.scope_name[0] ? " scope=" : "";
  const char* scope_name = entry.scope_name[0] ? entry.scope_name : "";
  const char* path_match = entry.match_blacklisted_path ? " path=blacklist" : "";
  const char* region_requirement =
      entry.scope_requires_region_match ? " require=region" : "";
  const char* scope_timing =
      entry.scope_uses_slow_timing ? " tx=slow" : "";
  if (entry.payload_type == FLOOD_PACKET_FILTER_ANY_TYPE) {
    snprintf(reply, reply_len, "> %d type=any hops=%s route=flood%s%s%s%s%s%s",
             index + 1, hops, path_match, scope_prefix, scope_name,
             region_requirement, scope_timing, suspension);
  } else {
    snprintf(reply, reply_len, "> %d type=%s(%u) hops=%s route=flood%s%s%s%s%s%s",
             index + 1, floodFilterPayloadTypeName(entry.payload_type),
             (uint32_t)entry.payload_type, hops, path_match,
             scope_prefix, scope_name, region_requirement, scope_timing,
             suspension);
  }
}

void MyMesh::formatFloodPacketFilters(const char* args, char* reply) const {
  const char* selector = skipFloodFilterSpaces(args);
  if (*selector == '.') selector = skipFloodFilterSpaces(selector + 1);
  if (*selector != 0) {
    uint8_t slot;
    if (!parseFloodFilterUnsigned(selector, FLOOD_PACKET_FILTER_SLOTS, slot) || slot == 0) {
      snprintf(reply, 160, "Err - filter slot must be 1-%d", FLOOD_PACKET_FILTER_SLOTS);
      return;
    }
    formatFloodPacketFilterDetail(slot - 1, reply, 160);
    return;
  }

  size_t used = (size_t)snprintf(reply, 160, ">");
  int active_count = 0;
  bool truncated = false;
  for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
    const auto& entry = flood_packet_filters[i];
    if (!entry.active) continue;
    active_count++;
    char hops[12];
    char item[112];
    formatFloodFilterHopSpec(hops, sizeof(hops), entry.min_hops, entry.max_hops);
    snprintf(item, sizeof(item), " %d=%s@%s%s%s%s%s%s%s",
             i + 1, floodFilterPayloadTypeName(entry.payload_type), hops,
             entry.match_blacklisted_path ? "?blacklist" : "",
             entry.scope_name[0] ? ">" : "",
             entry.scope_name,
             entry.scope_requires_region_match ? "!region" : "",
             entry.scope_uses_slow_timing ? "~slow" : "",
             entry.suspend_on_temp_radio ? "~tempradio" : "");
    size_t item_len = strlen(item);
    if (used + item_len >= 156) {
      truncated = true;
      break;
    }
    memcpy(&reply[used], item, item_len + 1);
    used += item_len;
  }
  if (active_count == 0) {
    strcpy(reply, "> off");
  } else if (truncated) {
    StrHelper::strncpy(&reply[used], " ...", 160 - used);
  }
}

void MyMesh::setFloodPacketFilter(const char* args, char* reply,
                                  bool require_explicit_action) {
  (void)require_explicit_action;
  const char* cursor = skipFloodFilterSpaces(args);
  int requested_slot = -1;
  if (*cursor == '.') {
    cursor++;
    const char* slot_start = cursor;
    while (*cursor >= '0' && *cursor <= '9') cursor++;
    size_t slot_len = (size_t)(cursor - slot_start);
    char slot_text[8];
    if (slot_len == 0 || slot_len >= sizeof(slot_text)) {
      snprintf(reply, 160, "Err - filter slot must be 1-%d", FLOOD_PACKET_FILTER_SLOTS);
      return;
    }
    memcpy(slot_text, slot_start, slot_len);
    slot_text[slot_len] = 0;
    uint8_t slot;
    if (!parseFloodFilterUnsigned(slot_text, FLOOD_PACKET_FILTER_SLOTS, slot) || slot == 0) {
      snprintf(reply, 160, "Err - filter slot must be 1-%d", FLOOD_PACKET_FILTER_SLOTS);
      return;
    }
    requested_slot = slot - 1;
    if (*cursor != ' ') {
      strcpy(reply, "Err - packet type must be name, any, 0-15, or 0x00-0x0F");
      return;
    }
  }
  cursor = skipFloodFilterSpaces(cursor);
  if (strlen(cursor) >= 140) {
    strcpy(reply, "Err - filter parameters too long");
    return;
  }

  char params[140];
  strcpy(params, cursor);
  char* tokens[7];
  int token_count = 0;
  char* token = params;
  while (*token != 0) {
    if (token_count >= 7) {
      strcpy(reply, "Err - too many flood filter parameters");
      return;
    }
    tokens[token_count++] = token;
    char* separator = strchr(token, ' ');
    if (separator == NULL) break;
    *separator++ = 0;
    while (*separator == ' ') separator++;
    token = separator;
  }
  if (token_count == 0) {
    strcpy(reply, FLOOD_PACKET_FILTER_USAGE);
    return;
  }

  uint8_t payload_type;
  if (!parseFloodFilterPayloadType(tokens[0], payload_type)) {
    strcpy(reply, "Err - packet type must be name, any, 0-15, or 0x00-0x0F");
    return;
  }

  uint8_t min_hops = 0;
  uint8_t max_hops = FLOOD_PACKET_FILTER_MAX_HOPS;
  bool hops_set = false;
  bool suspend_on_temp_radio = false;
  bool match_blacklisted_path = false;
  bool scope_requires_region_match = false;
  bool scope_timing_set = false;
  bool scope_uses_slow_timing = false;
  char scope_name[FLOOD_PACKET_FILTER_SCOPE_NAME_LEN];
  memset(scope_name, 0, sizeof(scope_name));
  for (int i = 1; i < token_count; i++) {
    if (floodFilterAsciiEqual(tokens[i], "suspend=tempradio")) {
      if (suspend_on_temp_radio) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      suspend_on_temp_radio = true;
    } else if (floodFilterAsciiEqual(tokens[i], "path=blacklist")) {
      if (match_blacklisted_path) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      match_blacklisted_path = true;
    } else if (floodFilterAsciiEqual(tokens[i], "require=region")) {
      if (scope_requires_region_match) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      scope_requires_region_match = true;
    } else if (floodFilterAsciiStartsWith(tokens[i], "tx=")) {
      if (scope_timing_set) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      if (floodFilterAsciiEqual(tokens[i], "tx=slow")) {
        scope_uses_slow_timing = true;
      } else if (!floodFilterAsciiEqual(tokens[i], "tx=fast")) {
        strcpy(reply, "Err - tx timing must be fast or slow");
        return;
      }
      scope_timing_set = true;
    } else if (floodFilterAsciiStartsWith(tokens[i], "scope=")) {
      if (scope_name[0] != 0) {
        strcpy(reply, FLOOD_PACKET_FILTER_DUPLICATE);
        return;
      }
      if (!normalizeFloodFilterScopeName(tokens[i] + strlen("scope="),
                                         scope_name, sizeof(scope_name))) {
        strcpy(reply, "Err - scope must be a public name of at most 30 characters");
        return;
      }
    } else if (!hops_set && parseFloodFilterHopSpec(tokens[i], min_hops, max_hops)) {
      hops_set = true;
    } else {
      strcpy(reply, FLOOD_PACKET_FILTER_USAGE);
      return;
    }
  }
  if (scope_requires_region_match && scope_name[0] == 0) {
    strcpy(reply, "Err - require=region requires scope=<name>");
    return;
  }
  if (scope_timing_set && scope_name[0] == 0) {
    strcpy(reply, "Err - tx timing requires scope=<name>");
    return;
  }

  int slot = requested_slot;
  if (slot < 0) {
    for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
      const auto& entry = flood_packet_filters[i];
      if (entry.active && entry.payload_type == payload_type
          && entry.min_hops == min_hops && entry.max_hops == max_hops
          && entry.suspend_on_temp_radio == suspend_on_temp_radio
          && entry.match_blacklisted_path == match_blacklisted_path
          && entry.scope_requires_region_match == scope_requires_region_match
          && strcmp(entry.scope_name, scope_name) == 0) {
        slot = i;
        break;
      }
    }
  }
  if (slot < 0) {
    for (int i = 0; i < FLOOD_PACKET_FILTER_SLOTS; i++) {
      if (!flood_packet_filters[i].active) {
        slot = i;
        break;
      }
    }
  }
  if (slot < 0) {
    strcpy(reply, "Err - filter table full");
    return;
  }

  FloodPacketFilterEntry previous = flood_packet_filters[slot];
  auto& entry = flood_packet_filters[slot];
  entry.active = true;
  entry.payload_type = payload_type;
  entry.min_hops = min_hops;
  entry.max_hops = max_hops;
  entry.suspend_on_temp_radio = suspend_on_temp_radio;
  entry.match_blacklisted_path = match_blacklisted_path;
  entry.scope_requires_region_match = scope_requires_region_match;
  entry.scope_uses_slow_timing = scope_uses_slow_timing;
  StrHelper::strzcpy(entry.scope_name, scope_name, sizeof(entry.scope_name));
  if (!saveFloodPacketFilters()) {
    entry = previous;
    strcpy(reply, "Err - unable to save flood filter");
    return;
  }

  char detail[160];
  formatFloodPacketFilterDetail(slot, detail, sizeof(detail));
  snprintf(reply, 160, "OK - %s", detail[0] == '>' ? skipFloodFilterSpaces(detail + 1) : detail);
}
#endif

void MyMesh::deleteFloodPacketFilter(const char* args, char* reply) {
  const char* selector = skipFloodFilterSpaces(args);
  if (*selector == '.') selector = skipFloodFilterSpaces(selector + 1);
  if (floodFilterAsciiEqual(selector, "all")) {
    if (!saveFloodPacketFilters(false, true)) {
      strcpy(reply, "Err - unable to save flood filter");
    } else {
      memset(flood_packet_filters, 0, sizeof(flood_packet_filters));
#if MESH_ENABLE_FLOOD_RULE_ENGINE
      flood_channel_data_rule_slot = 0xFF;
#endif
      strcpy(reply, "OK - all flood filters removed");
    }
    return;
  }

  uint8_t slot;
  if (!parseFloodFilterUnsigned(selector, FLOOD_PACKET_FILTER_SLOTS, slot) || slot == 0) {
    snprintf(reply, 160, "Err - use: del flood.filter.<1-%d>|all", FLOOD_PACKET_FILTER_SLOTS);
    return;
  }
  int index = slot - 1;
  if (!flood_packet_filters[index].active) {
    strcpy(reply, "Err - empty filter slot");
    return;
  }
  FloodPacketFilterEntry previous = flood_packet_filters[index];
#if MESH_ENABLE_FLOOD_RULE_ENGINE
  uint8_t previous_channel_data_slot = flood_channel_data_rule_slot;
  if (index == flood_channel_data_rule_slot) {
    flood_channel_data_rule_slot = 0xFF;
  }
#endif
  memset(&flood_packet_filters[index], 0, sizeof(flood_packet_filters[index]));
  if (!saveFloodPacketFilters()) {
    flood_packet_filters[index] = previous;
#if MESH_ENABLE_FLOOD_RULE_ENGINE
    flood_channel_data_rule_slot = previous_channel_data_slot;
#endif
    strcpy(reply, "Err - unable to save flood filter");
  } else {
    strcpy(reply, "OK");
  }
}

static bool isExactFloodChannelScopeSelector(uint8_t selector) {
  selector = FloodFilterPolicy::channelScopeMatchSelectorValue(selector);
  return selector == CIPHER_KEY_SIZE || selector == PUB_KEY_SIZE;
}

static bool isLoginFloodPayloadType(uint8_t type) {
  switch (type) {
    case PAYLOAD_TYPE_REQ:
    case PAYLOAD_TYPE_RESPONSE:
    case PAYLOAD_TYPE_TXT_MSG:
    case PAYLOAD_TYPE_ANON_REQ:
    case PAYLOAD_TYPE_PATH:
      return true;
    default:
      return false;
  }
}

static uint8_t wildcardFloodChannelScopeSelector(uint8_t type) {
  if (type == PAYLOAD_TYPE_GRP_TXT || type == PAYLOAD_TYPE_GRP_DATA) {
    return FLOOD_CHANNEL_SCOPE_TXT_ANY;
  }
  if (isLoginFloodPayloadType(type)) return FLOOD_CHANNEL_SCOPE_LOGIN_ANY;
  return FLOOD_CHANNEL_SCOPE_OTHER_ANY;
}

static const char* wildcardFloodChannelScopeName(uint8_t selector) {
  selector = FloodFilterPolicy::channelScopeMatchSelectorValue(selector);
  if (selector == FLOOD_CHANNEL_SCOPE_TXT_ANY) return "txt:*";
  if (selector == FLOOD_CHANNEL_SCOPE_LOGIN_ANY) return "login:*";
  if (selector == FLOOD_CHANNEL_SCOPE_OTHER_ANY) return "other:*";
  return NULL;
}

bool MyMesh::loadFloodChannelScopes() {
  static_assert(sizeof(FloodChannelScopeEntry) == 36,
                "channel scope persistence requires 36-byte entries");
  memset(flood_channel_scopes, 0, sizeof(flood_channel_scopes));
  memset(flood_channel_direct_scopes, 0,
         sizeof(flood_channel_direct_scopes));
  if (_fs == NULL || !_fs->exists(FLOOD_CHANNEL_SCOPE_FILE)) return true;
  File file = openFloodSettingsRead(_fs, FLOOD_CHANNEL_SCOPE_FILE);
  if (!file) return false;

  uint8_t magic[4];
  uint8_t count = 0;
  bool success = file.read(magic, sizeof(magic)) == sizeof(magic);
  bool version_1 = success && memcmp(magic, "FCS1", sizeof(magic)) == 0;
  bool version_2 = success && memcmp(magic, "FCS2", sizeof(magic)) == 0;
  bool version_3 = success && memcmp(magic, "FCS3", sizeof(magic)) == 0;
  bool version_4 = success && memcmp(magic, "FCS4", sizeof(magic)) == 0;
  bool version_5 = success && memcmp(magic, "FCS5", sizeof(magic)) == 0;
  success = (version_1 || version_2 || version_3 || version_4 || version_5)
      && file.read(&count, sizeof(count)) == sizeof(count);
  uint8_t retained = count < FLOOD_CHANNEL_SCOPE_SLOTS ? count : FLOOD_CHANNEL_SCOPE_SLOTS;
  size_t retained_bytes = (size_t)retained * sizeof(FloodChannelScopeEntry);
  success = success && file.read((uint8_t*)flood_channel_scopes, retained_bytes) == retained_bytes;
  FloodChannelScopeEntry discarded;
  for (int i = retained; success && i < count; i++) {
    success = file.read((uint8_t*)&discarded, sizeof(discarded)) == sizeof(discarded);
  }

  if (success && version_5) {
    uint8_t direct_count = 0;
    success = file.read(&direct_count, sizeof(direct_count))
        == sizeof(direct_count);
    uint8_t retained_direct = direct_count < FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS
        ? direct_count : FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS;
    size_t retained_direct_bytes = (size_t)retained_direct
        * FLOOD_PACKET_FILTER_SCOPE_NAME_LEN;
    success = success
        && file.read((uint8_t*)flood_channel_direct_scopes,
                     retained_direct_bytes) == retained_direct_bytes;
    for (int i = 0; success && i < retained_direct; i++) {
      char* name = flood_channel_direct_scopes[i];
      if (name[0] != 0 && !isValidStoredFloodFilterScopeName(name)) {
        success = false;
      }
    }
  }

  for (int i = 0; success && i < retained; i++) {
    auto& entry = flood_channel_scopes[i];
    if ((version_1
         && (FloodFilterPolicy::scopeUsesSlowTiming(entry.selector)
             || FloodFilterPolicy::scopeRequiresPath(entry.selector)))
        || (version_2
            && FloodFilterPolicy::scopeRequiresPath(entry.selector))
        || (version_3
            && FloodFilterPolicy::scopePathSelectorValue(entry.selector)
                > FloodFilterPolicy::SCOPE_PATH_BLACKLIST)) {
      success = false;
      break;
    }
    bool direct_target =
        FloodFilterPolicy::channelScopeUsesDirectTarget(entry.selector);
    uint8_t selector =
        FloodFilterPolicy::channelScopeMatchSelectorValue(entry.selector);
    if (entry.target_id == 0) {
      memset(&entry, 0, sizeof(entry));
    } else if (direct_target
        && (!version_5
            || entry.target_id > FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS
            || flood_channel_direct_scopes[entry.target_id - 1][0] == 0)) {
      success = false;
      break;
    } else if (selector <= FLOOD_CHANNEL_SCOPE_OTHER_ANY) {
      entry.channel_hash = 0;
      memset(entry.secret, 0, sizeof(entry.secret));
    } else if (isExactFloodChannelScopeSelector(selector)) {
      mesh::Utils::sha256(&entry.channel_hash, sizeof(entry.channel_hash),
                          entry.secret, selector);
      if (selector == CIPHER_KEY_SIZE) {
        memset(&entry.secret[CIPHER_KEY_SIZE], 0, PUB_KEY_SIZE - CIPHER_KEY_SIZE);
      }
    } else {
      success = false;
    }
  }
  file.close();

  // A malformed table is inert; corrupt storage must never assign a scope.
  if (!success) {
    memset(flood_channel_scopes, 0, sizeof(flood_channel_scopes));
    memset(flood_channel_direct_scopes, 0,
           sizeof(flood_channel_direct_scopes));
  }
  return success;
}

bool MyMesh::saveFloodChannelScopes(bool empty_table) {
#if MESH_ENABLE_FLOOD_RULE_ENGINE
  return saveFloodPacketFilters(empty_table);
#else
  if (_fs == NULL) return false;
  File file = openFloodSettingsWrite(_fs, FLOOD_CHANNEL_SCOPE_TEMP_FILE);
  if (!file) return false;

  const uint8_t magic[4] = {'F', 'C', 'S', '5'};
  uint8_t count = empty_table ? 0 : FLOOD_CHANNEL_SCOPE_SLOTS;
  bool success = file.write(magic, sizeof(magic)) == sizeof(magic)
      && file.write(&count, sizeof(count)) == sizeof(count);
  if (success && count != 0) {
    success = file.write((const uint8_t*)flood_channel_scopes,
                         sizeof(flood_channel_scopes)) == sizeof(flood_channel_scopes);
  }
  uint8_t direct_count = empty_table ? 0 : FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS;
  success = success
      && file.write(&direct_count, sizeof(direct_count))
          == sizeof(direct_count);
  if (success && direct_count != 0) {
    success = file.write((const uint8_t*)flood_channel_direct_scopes,
                         sizeof(flood_channel_direct_scopes))
        == sizeof(flood_channel_direct_scopes);
  }
  file.close();
  if (!success || !_fs->rename(FLOOD_CHANNEL_SCOPE_TEMP_FILE, FLOOD_CHANNEL_SCOPE_FILE)) {
    _fs->remove(FLOOD_CHANNEL_SCOPE_TEMP_FILE);
    return false;
  }
  return true;
#endif
}

void MyMesh::loadFloodChannelScopeRequirements() {
  memset(flood_channel_scope_requirements, 0,
         sizeof(flood_channel_scope_requirements));
  if (_fs == NULL || !_fs->exists(FLOOD_CHANNEL_SCOPE_REQUIRE_FILE)) return;

  File file =
      openFloodSettingsRead(_fs, FLOOD_CHANNEL_SCOPE_REQUIRE_FILE);
  if (!file) return;

  uint8_t magic[4];
  uint8_t count = 0;
  bool success = file.read(magic, sizeof(magic)) == sizeof(magic)
      && memcmp(magic, "FCR1", sizeof(magic)) == 0
      && file.read(&count, sizeof(count)) == sizeof(count);
  uint8_t retained = count < FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS
      ? count : FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS;
  size_t retained_bytes =
      (size_t)retained * sizeof(FloodChannelScopeRequireEntry);
  success = success
      && file.read((uint8_t*)flood_channel_scope_requirements, retained_bytes)
          == retained_bytes;
  FloodChannelScopeRequireEntry discarded;
  for (int i = retained; success && i < count; i++) {
    success = file.read((uint8_t*)&discarded, sizeof(discarded))
        == sizeof(discarded);
  }
  for (int i = 0; success && i < retained; i++) {
    auto& entry = flood_channel_scope_requirements[i];
    if (entry.key_len == 0) {
      memset(&entry, 0, sizeof(entry));
    } else if (entry.key_len == CIPHER_KEY_SIZE
        || entry.key_len == PUB_KEY_SIZE) {
      mesh::Utils::sha256(&entry.channel_hash, sizeof(entry.channel_hash),
                          entry.secret, entry.key_len);
      if (entry.key_len == CIPHER_KEY_SIZE) {
        memset(&entry.secret[CIPHER_KEY_SIZE], 0,
               PUB_KEY_SIZE - CIPHER_KEY_SIZE);
      }
    } else {
      success = false;
    }
  }
  file.close();

  if (!success) {
    memset(flood_channel_scope_requirements, 0,
           sizeof(flood_channel_scope_requirements));
  }
}

bool MyMesh::saveFloodChannelScopeRequirements(bool empty_table) {
  if (_fs == NULL) return false;
  File file = openFloodSettingsWrite(
      _fs, FLOOD_CHANNEL_SCOPE_REQUIRE_TEMP_FILE);
  if (!file) return false;

  const uint8_t magic[4] = {'F', 'C', 'R', '1'};
  uint8_t count = FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS;
  bool success = file.write(magic, sizeof(magic)) == sizeof(magic)
      && file.write(&count, sizeof(count)) == sizeof(count);
  if (success && empty_table) {
    FloodChannelScopeRequireEntry empty;
    memset(&empty, 0, sizeof(empty));
    for (int i = 0; success && i < FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS; i++) {
      success = file.write((const uint8_t*)&empty, sizeof(empty))
          == sizeof(empty);
    }
  } else if (success) {
    success = file.write(
        (const uint8_t*)flood_channel_scope_requirements,
        sizeof(flood_channel_scope_requirements))
        == sizeof(flood_channel_scope_requirements);
  }
  file.close();
  if (!success || !_fs->rename(FLOOD_CHANNEL_SCOPE_REQUIRE_TEMP_FILE,
                               FLOOD_CHANNEL_SCOPE_REQUIRE_FILE)) {
    _fs->remove(FLOOD_CHANNEL_SCOPE_REQUIRE_TEMP_FILE);
    return false;
  }
  return true;
}

bool MyMesh::findFloodChannelScopeRequirementMatch(
    const mesh::Packet* packet, bool& table_active) const {
  table_active = false;
  if (packet == NULL || !packet->isRouteFlood()) return false;

  uint8_t type = packet->getPayloadType();
  if (type != PAYLOAD_TYPE_GRP_TXT && type != PAYLOAD_TYPE_GRP_DATA) {
    return false;
  }
  bool valid_layout =
      packet->payload_len >= PATH_HASH_SIZE + CIPHER_MAC_SIZE + CIPHER_BLOCK_SIZE
      && ((packet->payload_len - PATH_HASH_SIZE - CIPHER_MAC_SIZE)
          % CIPHER_BLOCK_SIZE) == 0;

  for (int i = 0; i < FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS; i++) {
    const auto& entry = flood_channel_scope_requirements[i];
    if (entry.key_len == 0) continue;
    table_active = true;
    if (!valid_layout || packet->payload[0] != entry.channel_hash) continue;

    uint8_t data[MAX_PACKET_PAYLOAD];
    if (mesh::Utils::MACThenDecrypt(
            entry.secret, data, &packet->payload[PATH_HASH_SIZE],
            packet->payload_len - PATH_HASH_SIZE) > 0) {
      return true;
    }
  }
  return false;
}

bool MyMesh::applyFloodChannelScopeTarget(mesh::Packet* packet,
                                          const FloodChannelScopeEntry& entry,
                                          bool& scope_changed,
                                          bool& fast_track,
                                          bool& regionless_scope_set,
                                          bool log_change) {
  scope_changed = false;
  fast_track = false;
  regionless_scope_set = false;
  TransportKey scope;
  RegionEntry* region = NULL;
  const char* target_name = NULL;
  bool direct_target =
      FloodFilterPolicy::channelScopeUsesDirectTarget(entry.selector);
  if (direct_target) {
    target_name = flood_channel_direct_scopes[entry.target_id - 1];
    deriveFloodFilterScopeKey(target_name, scope);
    regionless_scope_set = true;
  } else {
    region = region_map.findById(entry.target_id);
    if (region == NULL || region->isWildcard()
        || (region->flags & REGION_DENY_FLOOD) != 0
        || region_map.getTransportKeysFor(*region, &scope, 1) <= 0
        || scope.isNull()) {
      return false;
    }
    target_name = region->name;
  }

  uint16_t transport_code = scope.calcTransportCode(packet);
  scope_changed =
      FloodFilterPolicy::setTransportScope(packet, transport_code);
  if (!scope_changed) return true;
  fast_track = FloodFilterPolicy::fastTrackScopeChange(
      scope_changed, FloodFilterPolicy::scopeUsesSlowTiming(entry.selector));

  if (log_change) {
#if defined(STM32_PLATFORM)
    MESH_DEBUG_PRINTLN("s %u %s %s",
#else
    MESH_DEBUG_PRINTLN("force-scoped flood type=%u scope=%s tx=%s",
#endif
                       (unsigned int)packet->getPayloadType(), target_name,
                       FloodFilterPolicy::scopeUsesSlowTiming(entry.selector)
                           ? "slow" : "fast");
  }
  return true;
}

bool MyMesh::applyFloodChannelScope(mesh::Packet* packet, bool& fast_track,
                                    bool& regionless_scope_set,
                                    bool log_change) {
  static_assert(FLOOD_RETRY_BRIDGE_BUCKETS
                    == FloodFilterPolicy::SCOPE_PATH_BRIDGE_BUCKET_COUNT,
                "channel scope and bridge bucket counts must agree");
  static_assert(FLOOD_RETRY_PREFIX_LEN
                    == FloodFilterPolicy::BLACKLIST_ID_SIZE,
                "channel scope and bridge bucket ID sizes must agree");
  fast_track = false;
  regionless_scope_set = false;
  if (packet == NULL || !packet->isRouteFlood()) return false;
  uint8_t type = packet->getPayloadType();
  bool valid_channel_layout = (type == PAYLOAD_TYPE_GRP_TXT || type == PAYLOAD_TYPE_GRP_DATA)
      && packet->payload_len >= PATH_HASH_SIZE + CIPHER_MAC_SIZE + CIPHER_BLOCK_SIZE
      && ((packet->payload_len - PATH_HASH_SIZE - CIPHER_MAC_SIZE) % CIPHER_BLOCK_SIZE) == 0;
  uint8_t path_match_checked = 0;
  uint8_t path_match_results = 0;
  auto path_selector_matches = [&](uint8_t path_selector) {
    if (path_selector == FloodFilterPolicy::SCOPE_PATH_NONE) return true;
    uint8_t match_bit = (uint8_t)(1U << (path_selector - 1U));
    if ((path_match_checked & match_bit) == 0) {
      bool matches = false;
      if (path_selector == FloodFilterPolicy::SCOPE_PATH_BLACKLIST) {
        matches = floodPacketFilterBlacklistMatches(packet);
      } else {
        uint8_t bucket = (uint8_t)(
            path_selector - FloodFilterPolicy::SCOPE_PATH_BRIDGE_BUCKET_BASE);
        if (bucket < FLOOD_RETRY_BRIDGE_BUCKETS) {
          matches = FloodFilterPolicy::pathMatchesConfiguredIds(
              packet,
              (const uint8_t*)_prefs.flood_retry_bridge_buckets[bucket],
              FLOOD_RETRY_BUCKET_PREFIXES);
        }
      }
      path_match_checked |= match_bit;
      if (matches) path_match_results |= match_bit;
    }
    return (path_match_results & match_bit) != 0;
  };
  auto matches_path_pass = [&](uint8_t selector, bool qualified_pass) {
    uint8_t path_selector =
        FloodFilterPolicy::scopePathSelectorValue(selector);
    bool path_matches = path_selector_matches(path_selector);
    return FloodFilterPolicy::scopePathPassMatches(
        selector, qualified_pass, path_matches);
  };

  if (valid_channel_layout) {
    uint8_t data[MAX_PACKET_PAYLOAD];
    for (int path_pass = 1; path_pass >= 0; path_pass--) {
      for (int i = 0; i < FLOOD_CHANNEL_SCOPE_SLOTS; i++) {
        const auto& entry = flood_channel_scopes[i];
        if (entry.target_id == 0 || !isExactFloodChannelScopeSelector(entry.selector)
            || packet->payload[0] != entry.channel_hash
            || !matches_path_pass(entry.selector, path_pass != 0)) {
          continue;
        }
        if (mesh::Utils::MACThenDecrypt(entry.secret, data, &packet->payload[PATH_HASH_SIZE],
                                        packet->payload_len - PATH_HASH_SIZE) > 0) {
          bool scope_changed;
          if (applyFloodChannelScopeTarget(packet, entry, scope_changed,
                                           fast_track, regionless_scope_set,
                                           log_change)) {
            return scope_changed;
          }
        }
      }
    }
  }
  uint8_t wildcard = wildcardFloodChannelScopeSelector(type);
  for (int path_pass = 1; path_pass >= 0; path_pass--) {
    for (int i = 0; i < FLOOD_CHANNEL_SCOPE_SLOTS; i++) {
      const auto& entry = flood_channel_scopes[i];
      if (entry.target_id != 0
          && FloodFilterPolicy::channelScopeMatchSelectorValue(entry.selector)
              == wildcard
          && matches_path_pass(entry.selector, path_pass != 0)) {
        bool scope_changed;
        if (applyFloodChannelScopeTarget(packet, entry, scope_changed,
                                         fast_track, regionless_scope_set,
                                         log_change)) {
          return scope_changed;
        }
      }
    }
  }
  return false;
}

uint8_t MyMesh::scoreFloodTransportScope(const mesh::Packet* packet, void* context) {
  return static_cast<MyMesh*>(context)->getFloodTransportScopeDepth(packet);
}

uint8_t MyMesh::getFloodTransportScopeDepth(const mesh::Packet* packet) {
  if (packet == NULL || packet->getRouteType() != ROUTE_TYPE_TRANSPORT_FLOOD) return 0;
  uint8_t best_depth = 0;
  const int region_count = region_map.getCount();
  for (int i = 0; i < region_count; i++) {
    const RegionEntry* region = region_map.getByIdx(i);
    if ((region->flags & REGION_DENY_FLOOD) != 0) continue;
    uint8_t depth = getRegionDepth(region);
    if (depth <= best_depth) continue;

    TransportKey keys[4];
    int key_count = region_map.getTransportKeysFor(*region, keys, 4);
    for (int key_idx = 0; key_idx < key_count; key_idx++) {
      if (keys[key_idx].calcTransportCode(packet) == packet->transport_codes[0]) {
        best_depth = depth;
        break;
      }
    }
  }
  return best_depth;
}

static bool parseFloodModerationUnsigned(const char* text, uint32_t maximum, uint32_t& value) {
  if (text == NULL || *text == 0) return false;
  uint32_t parsed = 0;
  for (const char* p = text; *p; p++) {
    if (*p < '0' || *p > '9') return false;
    uint32_t digit = (uint32_t)(*p - '0');
    if (digit > maximum || parsed > (maximum - digit) / 10U) return false;
    parsed = parsed * 10U + digit;
  }
  value = parsed;
  return true;
}

// Returns 1 for a token, 0 at end of input, and -1 for malformed/oversize input.
static int takeFloodModerationToken(const char*& cursor, char* dest, size_t dest_len) {
  cursor = skipFloodFilterSpaces(cursor);
  if (*cursor == 0) return 0;
  char quote = 0;
  if (*cursor == '\'' || *cursor == '"') quote = *cursor++;
  const char* start = cursor;
  if (quote) {
    while (*cursor && *cursor != quote) cursor++;
    if (*cursor != quote) return -1;
  } else {
    while (*cursor && *cursor != ' ') cursor++;
  }
  size_t len = (size_t)(cursor - start);
  if (len >= dest_len) return -1;
  memcpy(dest, start, len);
  dest[len] = 0;
  if (quote) {
    cursor++;
    if (*cursor != 0 && *cursor != ' ') return -1;
  }
  return 1;
}

static bool parseFloodModerationChannel(const char* text, uint8_t secret[PUB_KEY_SIZE],
                                        uint8_t& key_len, uint8_t hash_prefix[FLOOD_CHANNEL_KEY_PREFIX_LEN],
                                        char* name, size_t name_len) {
  if (text == NULL || *text == 0) return false;
  memset(secret, 0, PUB_KEY_SIZE);
  if (floodFilterAsciiEqual(text, "public")) {
    key_len = CIPHER_KEY_SIZE;
    memcpy(secret, FLOOD_PUBLIC_CHANNEL_SECRET, sizeof(FLOOD_PUBLIC_CHANNEL_SECRET));
    StrHelper::strncpy(name, "public", name_len);
  } else if (text[0] == '#' && text[1] != 0) {
    key_len = CIPHER_KEY_SIZE;
    mesh::Utils::sha256(secret, key_len, (const uint8_t*)text, strlen(text));
    StrHelper::strncpy(name, text, name_len);
  } else {
    size_t hex_len = strlen(text);
    if (hex_len != CIPHER_KEY_SIZE * 2 && hex_len != PUB_KEY_SIZE * 2) return false;
    for (size_t i = 0; i < hex_len; i++) {
      if (!mesh::Utils::isHexChar(text[i])) return false;
    }
    key_len = (uint8_t)(hex_len / 2);
    if (!mesh::Utils::fromHex(secret, key_len, text)) return false;
    mesh::Utils::sha256(hash_prefix, FLOOD_CHANNEL_KEY_PREFIX_LEN, secret, key_len);
    char prefix_hex[FLOOD_CHANNEL_KEY_PREFIX_LEN * 2 + 1];
    mesh::Utils::toHex(prefix_hex, hash_prefix, FLOOD_CHANNEL_KEY_PREFIX_LEN);
    snprintf(name, name_len, "key:%s", prefix_hex);
  }
  mesh::Utils::sha256(hash_prefix, FLOOD_CHANNEL_KEY_PREFIX_LEN, secret, key_len);
  return true;
}

static bool parseFloodChannelScopeIndex(const char*& cursor, int& index) {
  cursor = skipFloodFilterSpaces(cursor);
  index = -1;
  if (*cursor != '.') return true;
  cursor++;
  const char* start = cursor;
  while (*cursor >= '0' && *cursor <= '9') cursor++;
  size_t len = (size_t)(cursor - start);
  char text[4];
  if (len == 0 || len >= sizeof(text)) return false;
  memcpy(text, start, len);
  text[len] = 0;
  uint8_t slot = 0;
  if (!parseFloodFilterUnsigned(text, FLOOD_CHANNEL_SCOPE_SLOTS, slot) || slot == 0) return false;
  if (*cursor != 0 && *cursor != ' ') return false;
  cursor = skipFloodFilterSpaces(cursor);
  index = slot - 1;
  return true;
}

void MyMesh::formatFloodChannelScopeDetail(int index, char* reply, size_t reply_len) {
  if (index < 0 || index >= FLOOD_CHANNEL_SCOPE_SLOTS) {
    snprintf(reply, reply_len, "Err - scope slot must be 1-%d", FLOOD_CHANNEL_SCOPE_SLOTS);
    return;
  }
  const auto& entry = flood_channel_scopes[index];
  if (entry.target_id == 0) {
    snprintf(reply, reply_len, "> %d empty", index + 1);
    return;
  }

  char channel[12];
  uint8_t selector =
      FloodFilterPolicy::channelScopeMatchSelectorValue(entry.selector);
  const char* wildcard = wildcardFloodChannelScopeName(selector);
  if (wildcard != NULL) {
    strcpy(channel, wildcard);
  } else {
    uint8_t prefix[FLOOD_CHANNEL_KEY_PREFIX_LEN];
    mesh::Utils::sha256(prefix, sizeof(prefix), entry.secret, selector);
    mesh::Utils::toHex(channel, prefix, sizeof(prefix));
  }
  char path[24];
  uint8_t path_selector =
      FloodFilterPolicy::scopePathSelectorValue(entry.selector);
  if (path_selector == FloodFilterPolicy::SCOPE_PATH_BLACKLIST) {
    strcpy(path, " path=blacklist");
  } else {
    uint8_t bucket =
        FloodFilterPolicy::scopeBridgeBucketIndex(entry.selector);
    if (bucket != FloodFilterPolicy::SCOPE_PATH_INVALID_BUCKET) {
      snprintf(path, sizeof(path), " path=bucket:%u",
               (unsigned int)bucket + 1U);
    } else {
      path[0] = 0;
    }
  }
  const char* timing =
      FloodFilterPolicy::scopeUsesSlowTiming(entry.selector) ? " tx=slow" : "";
  if (FloodFilterPolicy::channelScopeUsesDirectTarget(entry.selector)) {
    snprintf(reply, reply_len, "> %d match=%s%s scope=%s%s",
             index + 1, channel, path,
             flood_channel_direct_scopes[entry.target_id - 1], timing);
    return;
  }

  RegionEntry* region = region_map.findById(entry.target_id);
  if (region != NULL) {
    snprintf(reply, reply_len, "> %d match=%s%s scope=%s%s",
             index + 1, channel, path, region->name, timing);
  } else {
    snprintf(reply, reply_len, "> %d match=%s%s scope=id:%u?%s", index + 1,
             channel, path, (unsigned int)entry.target_id, timing);
  }
}

void MyMesh::formatFloodChannelScopes(const char* args, char* reply) {
  const char* cursor = args;
  int index = -1;
  if (!parseFloodChannelScopeIndex(cursor, index)) {
    snprintf(reply, 160, "Err - scope slot must be 1-%d", FLOOD_CHANNEL_SCOPE_SLOTS);
    return;
  }
  if (*cursor != 0) {
    strcpy(reply, "Err - use get flood.channel.scope[.n]");
    return;
  }
  if (index >= 0) {
    formatFloodChannelScopeDetail(index, reply, 160);
    return;
  }

  int active = 0;
  for (int i = 0; i < FLOOD_CHANNEL_SCOPE_SLOTS; i++) {
    if (flood_channel_scopes[i].target_id != 0) active++;
  }
#if defined(STM32_PLATFORM)
  snprintf(reply, 160, "> %d/%d",
#else
  snprintf(reply, 160, "> %d/%d active; use get flood.channel.scope.<n>",
#endif
           active, FLOOD_CHANNEL_SCOPE_SLOTS);
}

void MyMesh::setFloodChannelScope(const char* args, char* reply) {
  const char* cursor = args;
  int requested_slot = -1;
  if (!parseFloodChannelScopeIndex(cursor, requested_slot)) {
    strcpy(reply, "Err - bad scope slot");
    return;
  }

  char channel_text[80];
  char target_text[40];
  if (takeFloodModerationToken(cursor, channel_text, sizeof(channel_text)) != 1
      || takeFloodModerationToken(cursor, target_text, sizeof(target_text)) != 1) {
    strcpy(reply, FLOOD_CHANNEL_SCOPE_USAGE);
    return;
  }
  bool slow_timing = false;
  uint8_t path_selector = FloodFilterPolicy::SCOPE_PATH_NONE;
  bool timing_set = false;
  bool path_set = false;
  char option[20];
  int option_result;
  while ((option_result =
              takeFloodModerationToken(cursor, option, sizeof(option))) == 1) {
    if (floodFilterAsciiEqual(option, "tx=slow")
        || floodFilterAsciiEqual(option, "tx=fast")) {
      if (timing_set) {
        strcpy(reply, "Err - duplicate tx timing");
        return;
      }
      timing_set = true;
      slow_timing = floodFilterAsciiEqual(option, "tx=slow");
    } else if (floodFilterAsciiEqual(option, "path=blacklist")) {
      if (path_set) {
        strcpy(reply, "Err - duplicate path matcher");
        return;
      }
      path_set = true;
      path_selector = FloodFilterPolicy::SCOPE_PATH_BLACKLIST;
    } else if (strncmp(option, "path=bucket:", 12) == 0) {
      if (path_set) {
        strcpy(reply, "Err - duplicate path matcher");
        return;
      }
      uint8_t bucket = 0;
      if (!parseFloodFilterUnsigned(
              option + 12, FLOOD_RETRY_BRIDGE_BUCKETS, bucket)
          || bucket == 0) {
        strcpy(reply, "Err - path bucket must be 1-6");
        return;
      }
      path_set = true;
      path_selector = (uint8_t)(
          FloodFilterPolicy::SCOPE_PATH_BRIDGE_BUCKET_BASE + bucket - 1U);
    } else {
      strcpy(reply, FLOOD_CHANNEL_SCOPE_USAGE);
      return;
    }
  }
  if (option_result < 0) {
    strcpy(reply, FLOOD_CHANNEL_SCOPE_USAGE);
    return;
  }

  bool direct_target = floodFilterAsciiStartsWith(target_text, "scope=");
  char direct_scope_name[FLOOD_PACKET_FILTER_SCOPE_NAME_LEN];
  memset(direct_scope_name, 0, sizeof(direct_scope_name));
  RegionEntry* region = NULL;
  if (direct_target) {
    if (!normalizeFloodFilterScopeName(
            target_text + strlen("scope="), direct_scope_name,
            sizeof(direct_scope_name))) {
      strcpy(reply,
             "Err - scope must be a public name of at most 30 characters");
      return;
    }
  } else {
    region = region_map.findByNamePrefix(target_text);
    TransportKey scope;
    if (region == NULL || region->isWildcard()
        || (region->flags & REGION_DENY_FLOOD) != 0
        || region_map.getTransportKeysFor(*region, &scope, 1) <= 0
        || scope.isNull()) {
      strcpy(reply, "Err - bad scope region");
      return;
    }
  }

  uint8_t secret[PUB_KEY_SIZE];
  memset(secret, 0, sizeof(secret));
  uint8_t selector = FLOOD_CHANNEL_SCOPE_TXT_ANY;
  uint8_t key_len = 0;
  uint8_t hash_prefix[FLOOD_CHANNEL_KEY_PREFIX_LEN];
  memset(hash_prefix, 0, sizeof(hash_prefix));
  if (strcmp(channel_text, "*") == 0 || floodFilterAsciiEqual(channel_text, "txt:*")) {
    selector = FLOOD_CHANNEL_SCOPE_TXT_ANY;
  } else if (floodFilterAsciiEqual(channel_text, "login:*")) {
    selector = FLOOD_CHANNEL_SCOPE_LOGIN_ANY;
  } else if (floodFilterAsciiEqual(channel_text, "other:*")) {
    selector = FLOOD_CHANNEL_SCOPE_OTHER_ANY;
  } else {
    char ignored_name[FLOOD_GROUP_MODERATION_NAME_LEN];
    if (!parseFloodModerationChannel(channel_text, secret, key_len, hash_prefix,
                                     ignored_name, sizeof(ignored_name))) {
      strcpy(reply, "Err - bad scope matcher");
      return;
    }
    selector = key_len;
  }

  int matching_slot = -1;
  int free_slot = -1;
  for (int i = 0; i < FLOOD_CHANNEL_SCOPE_SLOTS; i++) {
    const auto& entry = flood_channel_scopes[i];
    if (entry.target_id == 0) {
      if (free_slot < 0) free_slot = i;
    } else if (FloodFilterPolicy::channelScopeMatchSelectorValue(
                   entry.selector) == selector
        && FloodFilterPolicy::scopePathSelectorValue(entry.selector)
            == path_selector
        && (!isExactFloodChannelScopeSelector(selector)
            || memcmp(entry.secret, secret, PUB_KEY_SIZE) == 0)) {
      matching_slot = i;
      break;
    }
  }
  int slot = requested_slot >= 0 ? requested_slot : (matching_slot >= 0 ? matching_slot : free_slot);
  if (slot < 0) {
    strcpy(reply, "Err - scope table full");
    return;
  }

  int direct_scope_slot = -1;
  int changed_direct_scope_slot = -1;
  char previous_direct_scope[FLOOD_PACKET_FILTER_SCOPE_NAME_LEN];
  memset(previous_direct_scope, 0, sizeof(previous_direct_scope));
  if (direct_target) {
    int reusable_direct_scope_slot = -1;
    for (int i = 0; i < FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS; i++) {
      if (strcmp(flood_channel_direct_scopes[i], direct_scope_name) == 0) {
        direct_scope_slot = i;
        break;
      }
      if (reusable_direct_scope_slot < 0) {
        bool referenced = false;
        for (int row = 0; row < FLOOD_CHANNEL_SCOPE_SLOTS; row++) {
          if (row == slot) continue;
          const auto& entry = flood_channel_scopes[row];
          if (entry.target_id == i + 1
              && FloodFilterPolicy::channelScopeUsesDirectTarget(
                  entry.selector)) {
            referenced = true;
            break;
          }
        }
        if (!referenced) {
          reusable_direct_scope_slot = i;
        }
      }
    }
    if (direct_scope_slot < 0) {
      direct_scope_slot = reusable_direct_scope_slot;
    }
    if (direct_scope_slot < 0) {
      strcpy(reply, "Err - scope table full");
      return;
    }
    if (strcmp(flood_channel_direct_scopes[direct_scope_slot],
               direct_scope_name) != 0) {
      changed_direct_scope_slot = direct_scope_slot;
      memcpy(previous_direct_scope,
             flood_channel_direct_scopes[direct_scope_slot],
             sizeof(previous_direct_scope));
      StrHelper::strzcpy(flood_channel_direct_scopes[direct_scope_slot],
                         direct_scope_name,
                         sizeof(flood_channel_direct_scopes[direct_scope_slot]));
    }
  }

  uint8_t target_selector =
      FloodFilterPolicy::encodeChannelScopeTargetSelector(selector,
                                                          direct_target);

  FloodChannelScopeEntry previous = flood_channel_scopes[slot];
  auto& entry = flood_channel_scopes[slot];
  memset(&entry, 0, sizeof(entry));
  entry.target_id = direct_target ? direct_scope_slot + 1 : region->id;
  entry.selector =
      FloodFilterPolicy::encodeScopeSelector(
          target_selector, slow_timing, path_selector);
  if (isExactFloodChannelScopeSelector(selector)) {
    entry.channel_hash = hash_prefix[0];
    memcpy(entry.secret, secret, sizeof(entry.secret));
  }
  if (!saveFloodChannelScopes()) {
    entry = previous;
    if (changed_direct_scope_slot >= 0) {
      memcpy(flood_channel_direct_scopes[changed_direct_scope_slot],
             previous_direct_scope, sizeof(previous_direct_scope));
    }
    strcpy(reply, "Err - save failed");
    return;
  }
  formatFloodChannelScopeDetail(slot, reply, 160);
}

void MyMesh::deleteFloodChannelScope(const char* args, char* reply) {
  const char* cursor = skipFloodFilterSpaces(args);
  if (floodFilterAsciiEqual(cursor, "all")) {
    if (!saveFloodChannelScopes(true)) {
      strcpy(reply, "Err - save failed");
    } else {
      memset(flood_channel_scopes, 0, sizeof(flood_channel_scopes));
      memset(flood_channel_direct_scopes, 0,
             sizeof(flood_channel_direct_scopes));
      strcpy(reply, "OK");
    }
    return;
  }

  int index = -1;
  if (!parseFloodChannelScopeIndex(cursor, index)) index = -1;
  if (index < 0 || *cursor != 0) {
    strcpy(reply, "Err - use: del flood.channel.scope.<n>|all");
    return;
  }
  if (flood_channel_scopes[index].target_id == 0) {
    strcpy(reply, "Err - empty scope slot");
    return;
  }
  FloodChannelScopeEntry previous = flood_channel_scopes[index];
  memset(&flood_channel_scopes[index], 0, sizeof(flood_channel_scopes[index]));
  if (!saveFloodChannelScopes()) {
    flood_channel_scopes[index] = previous;
    strcpy(reply, "Err - save failed");
  } else {
    strcpy(reply, "OK");
  }
}

void MyMesh::formatFloodChannelScopeRequirementDetail(
    int index, char* reply, size_t reply_len) const {
  if (index < 0 || index >= FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS) {
    snprintf(reply, reply_len, "Err - require slot must be 1-%d",
             FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS);
    return;
  }
  const auto& entry = flood_channel_scope_requirements[index];
  if (entry.key_len == 0) {
    snprintf(reply, reply_len, "> %d empty", index + 1);
    return;
  }

  uint8_t prefix[FLOOD_CHANNEL_KEY_PREFIX_LEN];
  char prefix_text[FLOOD_CHANNEL_KEY_PREFIX_LEN * 2 + 1];
  mesh::Utils::sha256(prefix, sizeof(prefix), entry.secret, entry.key_len);
  mesh::Utils::toHex(prefix_text, prefix, sizeof(prefix));
  snprintf(reply, reply_len, "> %d match=%s key=%u require=scope",
           index + 1, prefix_text, (unsigned int)entry.key_len * 8U);
}

void MyMesh::formatFloodChannelScopeRequirements(const char* args,
                                                 char* reply) {
  const char* cursor = args;
  int index = -1;
  if (!parseFloodChannelScopeIndex(cursor, index)) {
    snprintf(reply, 160, "Err - require slot must be 1-%d",
             FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS);
    return;
  }
  if (*cursor != 0) {
    strcpy(reply, "Err - use get flood.channel.scope.require[.n]");
    return;
  }
  if (index >= 0) {
    formatFloodChannelScopeRequirementDetail(index, reply, 160);
    return;
  }

  int active = 0;
  for (int i = 0; i < FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS; i++) {
    if (flood_channel_scope_requirements[i].key_len != 0) active++;
  }
  snprintf(reply, 160,
           "> %d/%d active; listed=require allowed scope, others=bypass",
           active, FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS);
}

void MyMesh::setFloodChannelScopeRequirement(const char* args, char* reply) {
  const char* cursor = args;
  int requested_slot = -1;
  if (!parseFloodChannelScopeIndex(cursor, requested_slot)) {
    strcpy(reply, "Err - bad require slot");
    return;
  }

  char channel_text[80];
  char extra[2];
  if (takeFloodModerationToken(cursor, channel_text, sizeof(channel_text)) != 1
      || takeFloodModerationToken(cursor, extra, sizeof(extra)) != 0) {
    strcpy(reply,
           "Err - use: set flood.channel.scope.require[.n] <channel>");
    return;
  }

  uint8_t secret[PUB_KEY_SIZE];
  uint8_t key_len = 0;
  uint8_t hash_prefix[FLOOD_CHANNEL_KEY_PREFIX_LEN];
  char ignored_name[FLOOD_GROUP_MODERATION_NAME_LEN];
  if (!parseFloodModerationChannel(channel_text, secret, key_len, hash_prefix,
                                   ignored_name, sizeof(ignored_name))) {
    strcpy(reply, "Err - channel must be public, #name, or 128/256-bit key");
    return;
  }

  int matching_slot = -1;
  int free_slot = -1;
  for (int i = 0; i < FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS; i++) {
    const auto& entry = flood_channel_scope_requirements[i];
    if (entry.key_len == 0) {
      if (free_slot < 0) free_slot = i;
    } else if (entry.key_len == key_len
        && memcmp(entry.secret, secret, PUB_KEY_SIZE) == 0) {
      matching_slot = i;
      break;
    }
  }
  int slot = requested_slot >= 0
      ? requested_slot
      : (matching_slot >= 0 ? matching_slot : free_slot);
  if (slot < 0) {
    strcpy(reply, "Err - scope require table full");
    return;
  }

  FloodChannelScopeRequireEntry previous =
      flood_channel_scope_requirements[slot];
  auto& entry = flood_channel_scope_requirements[slot];
  memset(&entry, 0, sizeof(entry));
  entry.key_len = key_len;
  entry.channel_hash = hash_prefix[0];
  memcpy(entry.secret, secret, sizeof(entry.secret));
  if (entry.key_len == CIPHER_KEY_SIZE) {
    memset(&entry.secret[CIPHER_KEY_SIZE], 0,
           PUB_KEY_SIZE - CIPHER_KEY_SIZE);
  }

  if (!saveFloodChannelScopeRequirements()) {
    entry = previous;
    strcpy(reply, "Err - save failed");
    return;
  }
  formatFloodChannelScopeRequirementDetail(slot, reply, 160);
}

void MyMesh::deleteFloodChannelScopeRequirement(const char* args,
                                                char* reply) {
  const char* cursor = skipFloodFilterSpaces(args);
  if (floodFilterAsciiEqual(cursor, "all")) {
    if (!saveFloodChannelScopeRequirements(true)) {
      strcpy(reply, "Err - save failed");
    } else {
      memset(flood_channel_scope_requirements, 0,
             sizeof(flood_channel_scope_requirements));
      strcpy(reply, "OK");
    }
    return;
  }

  int index = -1;
  if (!parseFloodChannelScopeIndex(cursor, index)) index = -1;
  if (index < 0 || *cursor != 0) {
    strcpy(reply, "Err - use: del flood.channel.scope.require.<n>|all");
    return;
  }
  if (flood_channel_scope_requirements[index].key_len == 0) {
    strcpy(reply, "Err - empty require slot");
    return;
  }

  FloodChannelScopeRequireEntry previous =
      flood_channel_scope_requirements[index];
  memset(&flood_channel_scope_requirements[index], 0,
         sizeof(flood_channel_scope_requirements[index]));
  if (!saveFloodChannelScopeRequirements()) {
    flood_channel_scope_requirements[index] = previous;
    strcpy(reply, "Err - save failed");
  } else {
    strcpy(reply, "OK");
  }
}

#if MESH_ENABLE_FLOOD_GROUP_MODERATION

static bool parseFloodModerationPath(const char* text, uint8_t& hash_size, uint8_t& path_hops,
                                     uint8_t path[FLOOD_GROUP_MODERATION_PATH_BYTES_MAX]) {
  memset(path, 0, FLOOD_GROUP_MODERATION_PATH_BYTES_MAX);
  if (text == NULL || *text == 0 || strcmp(text, "*") == 0) {
    hash_size = 0;
    path_hops = 0;
    return text != NULL && *text != 0;
  }
  if (strlen(text) >= 32) return false;
  char input[32];
  strcpy(input, text);
  char* token = input;
  uint8_t parsed_size = 0;
  uint8_t count = 0;
  while (token != NULL) {
    char* comma = strchr(token, ',');
    if (comma) *comma = 0;
    size_t hex_len = strlen(token);
    if (hex_len != 2 && hex_len != 4 && hex_len != 6) return false;
    uint8_t token_size = (uint8_t)(hex_len / 2);
    if ((parsed_size != 0 && parsed_size != token_size)
        || count >= FLOOD_GROUP_MODERATION_PATH_HOPS_MAX) return false;
    for (size_t i = 0; i < hex_len; i++) {
      if (!mesh::Utils::isHexChar(token[i])) return false;
    }
    parsed_size = token_size;
    if (!mesh::Utils::fromHex(&path[count * parsed_size], parsed_size, token)) return false;
    count++;
    token = comma ? comma + 1 : NULL;
  }
  if (count == 0) return false;
  hash_size = parsed_size;
  path_hops = count;
  return true;
}

static void formatFloodModerationPath(char* dest, size_t dest_len, uint8_t hash_size, uint8_t path_hops,
                                      const uint8_t path[FLOOD_GROUP_MODERATION_PATH_BYTES_MAX]) {
  if (hash_size == 0 || path_hops == 0) {
    StrHelper::strncpy(dest, "*", dest_len);
    return;
  }
  size_t used = 0;
  dest[0] = 0;
  for (uint8_t i = 0; i < path_hops; i++) {
    char hop[7];
    mesh::Utils::toHex(hop, &path[i * hash_size], hash_size);
    int written = snprintf(&dest[used], dest_len - used, "%s%s", i == 0 ? "" : ",", hop);
    if (written < 0 || (size_t)written >= dest_len - used) {
      dest[dest_len - 1] = 0;
      return;
    }
    used += (size_t)written;
  }
}

static bool floodModerationSenderNameValid(const char* sender) {
  if (sender == NULL || *sender == 0 || strlen(sender) >= FLOOD_GROUP_MODERATION_NAME_LEN) return false;
  if (strcmp(sender, "*") == 0) return true;
  for (const char* p = sender; *p; p++) {
    if (*p == ':' || *p == '\r' || *p == '\n' || (uint8_t)*p < 0x20) return false;
  }
  return true;
}

static bool floodModerationPathMatches(const mesh::Packet* packet, uint8_t hash_size, uint8_t path_hops,
                                       const uint8_t path[FLOOD_GROUP_MODERATION_PATH_BYTES_MAX]) {
  if (path_hops == 0) return true;
  if (packet == NULL || packet->getPathHashSize() != hash_size
      || packet->getPathHashCount() < path_hops) return false;
  return memcmp(packet->path, path, path_hops * hash_size) == 0;
}

void MyMesh::loadFloodGroupModeration() {
  memset(flood_group_moderation, 0, sizeof(flood_group_moderation));
  if (_fs == NULL || !_fs->exists(FLOOD_GROUP_MODERATION_FILE)) return;
  File file = openFloodSettingsRead(_fs, FLOOD_GROUP_MODERATION_FILE);
  if (!file) return;

  FloodGroupModerationEntry loaded[FLOOD_GROUP_MODERATION_SLOTS];
  memset(loaded, 0, sizeof(loaded));
  uint8_t magic[4];
  uint8_t count = 0;
  bool success = file.read(magic, sizeof(magic)) == sizeof(magic)
      && memcmp(magic, "FGM1", sizeof(magic)) == 0
      && file.read(&count, sizeof(count)) == sizeof(count)
      && count <= FLOOD_GROUP_MODERATION_SLOTS;
  for (int i = 0; success && i < count; i++) {
    auto& entry = loaded[i];
    uint8_t active = 0;
    success = file.read(&active, sizeof(active)) == sizeof(active);
    success = success && file.read(&entry.key_len, sizeof(entry.key_len)) == sizeof(entry.key_len);
    success = success && file.read(entry.secret, sizeof(entry.secret)) == sizeof(entry.secret);
    success = success && file.read((uint8_t*)entry.channel_name, sizeof(entry.channel_name)) == sizeof(entry.channel_name);
    success = success && file.read((uint8_t*)entry.sender, sizeof(entry.sender)) == sizeof(entry.sender);
    success = success && file.read(&entry.path_hash_size, sizeof(entry.path_hash_size)) == sizeof(entry.path_hash_size);
    success = success && file.read(&entry.path_hops, sizeof(entry.path_hops)) == sizeof(entry.path_hops);
    success = success && file.read(entry.path, sizeof(entry.path)) == sizeof(entry.path);
    success = success && file.read(&entry.max_hops, sizeof(entry.max_hops)) == sizeof(entry.max_hops);
    success = success && file.read((uint8_t*)&entry.rate_per_minute, sizeof(entry.rate_per_minute)) == sizeof(entry.rate_per_minute);
    entry.channel_name[sizeof(entry.channel_name) - 1] = 0;
    entry.sender[sizeof(entry.sender) - 1] = 0;
    entry.active = active != 0;
    if (success && entry.active) {
      bool path_valid = (entry.path_hops == 0 && entry.path_hash_size == 0)
          || (entry.path_hops >= 1 && entry.path_hops <= FLOOD_GROUP_MODERATION_PATH_HOPS_MAX
              && entry.path_hash_size >= 1 && entry.path_hash_size <= 3);
      bool hops_valid = entry.max_hops == FLOOD_GROUP_MODERATION_HOPS_ALL
          || entry.max_hops <= FLOOD_PACKET_FILTER_MAX_HOPS;
      success = (entry.key_len == CIPHER_KEY_SIZE || entry.key_len == PUB_KEY_SIZE)
          && entry.channel_name[0] != 0 && floodModerationSenderNameValid(entry.sender)
          && path_valid && hops_valid;
      if (success) {
        mesh::Utils::sha256(entry.hash_prefix, sizeof(entry.hash_prefix), entry.secret, entry.key_len);
      }
    }
  }
  file.close();
  // As with the general table, malformed persistence fails open.
  if (success) memcpy(flood_group_moderation, loaded, sizeof(flood_group_moderation));
}

bool MyMesh::saveFloodGroupModeration() {
  if (_fs == NULL) return false;
  File file = openFloodSettingsWrite(_fs, FLOOD_GROUP_MODERATION_FILE);
  if (!file) return false;
  const uint8_t magic[4] = {'F', 'G', 'M', '1'};
  uint8_t count = FLOOD_GROUP_MODERATION_SLOTS;
  bool success = file.write(magic, sizeof(magic)) == sizeof(magic)
      && file.write(&count, sizeof(count)) == sizeof(count);
  for (int i = 0; success && i < FLOOD_GROUP_MODERATION_SLOTS; i++) {
    const auto& entry = flood_group_moderation[i];
    uint8_t active = entry.active ? 1 : 0;
    success = file.write(&active, sizeof(active)) == sizeof(active);
    success = success && file.write(&entry.key_len, sizeof(entry.key_len)) == sizeof(entry.key_len);
    success = success && file.write(entry.secret, sizeof(entry.secret)) == sizeof(entry.secret);
    success = success && file.write((const uint8_t*)entry.channel_name, sizeof(entry.channel_name)) == sizeof(entry.channel_name);
    success = success && file.write((const uint8_t*)entry.sender, sizeof(entry.sender)) == sizeof(entry.sender);
    success = success && file.write(&entry.path_hash_size, sizeof(entry.path_hash_size)) == sizeof(entry.path_hash_size);
    success = success && file.write(&entry.path_hops, sizeof(entry.path_hops)) == sizeof(entry.path_hops);
    success = success && file.write(entry.path, sizeof(entry.path)) == sizeof(entry.path);
    success = success && file.write(&entry.max_hops, sizeof(entry.max_hops)) == sizeof(entry.max_hops);
    success = success && file.write((const uint8_t*)&entry.rate_per_minute, sizeof(entry.rate_per_minute)) == sizeof(entry.rate_per_minute);
  }
  file.close();
  return success;
}

#endif

bool MyMesh::decodeFloodGroupPlainText(const mesh::Packet* packet, const uint8_t* secret, uint8_t key_len,
                                       uint32_t& timestamp, char* sender, size_t sender_len) const {
  if (sender != NULL && sender_len > 0) sender[0] = 0;
  if (packet == NULL || secret == NULL || sender == NULL || sender_len < 2
      || !packet->isRouteFlood() || packet->getPayloadType() != PAYLOAD_TYPE_GRP_TXT
      || (key_len != CIPHER_KEY_SIZE && key_len != PUB_KEY_SIZE)
      || packet->payload_len <= PATH_HASH_SIZE + CIPHER_MAC_SIZE) return false;

  uint8_t channel_hash = 0;
  mesh::Utils::sha256(&channel_hash, sizeof(channel_hash), secret, key_len);
  if (packet->payload[0] != channel_hash) return false;

  uint8_t data[MAX_PACKET_PAYLOAD];
  int len = mesh::Utils::MACThenDecrypt(secret, data, &packet->payload[PATH_HASH_SIZE],
                                        packet->payload_len - PATH_HASH_SIZE);
  if (len <= 5 || (data[4] >> 2) != TXT_TYPE_PLAIN) return false;

  memcpy(&timestamp, data, sizeof(timestamp));
  const uint8_t* text = &data[5];
  size_t text_len = (size_t)len - 5;
  const uint8_t* colon = (const uint8_t*)memchr(text, ':', text_len);
  if (colon == NULL) return false;

  size_t parsed_len = (size_t)(colon - text);
  while (parsed_len > 0 && text[parsed_len - 1] == ' ') parsed_len--;
  if (parsed_len == 0 || parsed_len >= sender_len) return false;
  for (size_t i = 0; i < parsed_len; i++) {
    if (text[i] == '\r' || text[i] == '\n' || text[i] < 0x20) return false;
  }
  memcpy(sender, text, parsed_len);
  sender[parsed_len] = 0;
  return true;
}

#if MESH_ENABLE_FLOOD_GROUP_MODERATION

bool MyMesh::shouldBlockFloodGroupTextForward(const mesh::Packet* packet) {
  if (packet == NULL || !packet->isRouteFlood() || packet->getPayloadType() != PAYLOAD_TYPE_GRP_TXT
      || packet->payload_len <= PATH_HASH_SIZE + CIPHER_MAC_SIZE) return false;

  uint8_t cached_secret[PUB_KEY_SIZE];
  uint8_t cached_key_len = 0;
  bool cache_present = false;
  bool cache_valid = false;
  char decoded_sender[FLOOD_GROUP_MODERATION_NAME_LEN];
  decoded_sender[0] = 0;
  uint8_t hops = packet->getPathHashCount();
  uint8_t rate_slots[FLOOD_GROUP_MODERATION_SLOTS];
  uint8_t rate_slot_count = 0;
  uint32_t now = _ms->getMillis();

  for (int i = 0; i < FLOOD_GROUP_MODERATION_SLOTS; i++) {
    auto& entry = flood_group_moderation[i];
    if (!entry.active || packet->payload[0] != entry.hash_prefix[0]
        || !floodModerationPathMatches(packet, entry.path_hash_size, entry.path_hops, entry.path)) continue;

    bool same_cached_key = cache_present && cached_key_len == entry.key_len
        && memcmp(cached_secret, entry.secret, entry.key_len) == 0;
    if (!same_cached_key) {
      cache_present = true;
      cached_key_len = entry.key_len;
      memcpy(cached_secret, entry.secret, entry.key_len);
      cache_valid = false;
      decoded_sender[0] = 0;

      uint32_t ignored_timestamp = 0;
      cache_valid = decodeFloodGroupPlainText(packet, entry.secret, entry.key_len,
                                              ignored_timestamp, decoded_sender,
                                              sizeof(decoded_sender));
    }
    if (!cache_valid
        || (strcmp(entry.sender, "*") != 0 && !floodFilterAsciiEqual(entry.sender, decoded_sender))) continue;

    bool rule_blocked = entry.max_hops != FLOOD_GROUP_MODERATION_HOPS_ALL && hops >= entry.max_hops;
    if (entry.rate_per_minute != FLOOD_GROUP_MODERATION_RATE_UNLIMITED) {
      uint16_t effective_count = (!entry.rate_window_active
              || now - entry.rate_window_started >= 60000UL)
          ? 0 : entry.rate_window_count;
      if (entry.rate_per_minute == 0 || effective_count >= entry.rate_per_minute) {
        rule_blocked = true;
      }
    }
    if (rule_blocked) {
      MESH_DEBUG_PRINTLN("allowPacketForward: flood.moderation slot=%d channel=%s sender=%s hops=%d rate=%d",
                         i + 1, entry.channel_name, decoded_sender, hops, entry.rate_per_minute);
      return true;
    }
    if (entry.rate_per_minute != FLOOD_GROUP_MODERATION_RATE_UNLIMITED) {
      rate_slots[rate_slot_count++] = (uint8_t)i;
    }
  }

  // All matching deny rules accepted the packet. Only now spend the quota for
  // each applicable rate rule, so hop-blocked or otherwise denied packets do
  // not reduce the number of messages this repeater may actually forward.
  for (uint8_t i = 0; i < rate_slot_count; i++) {
    auto& entry = flood_group_moderation[rate_slots[i]];
    if (!entry.rate_window_active || now - entry.rate_window_started >= 60000UL) {
      entry.rate_window_active = true;
      entry.rate_window_started = now;
      entry.rate_window_count = 0;
    }
    entry.rate_window_count++;
  }
  return false;
}

void MyMesh::formatFloodGroupModerationDetail(int index, char* reply, size_t reply_len) const {
  if (index < 0 || index >= FLOOD_GROUP_MODERATION_SLOTS || !flood_group_moderation[index].active) {
    snprintf(reply, reply_len, "Err - empty moderation slot");
    return;
  }
  const auto& entry = flood_group_moderation[index];
  char path[32];
  char rate[24];
  char hops[12];
  formatFloodModerationPath(path, sizeof(path), entry.path_hash_size, entry.path_hops, entry.path);
  if (entry.rate_per_minute == FLOOD_GROUP_MODERATION_RATE_UNLIMITED) strcpy(rate, "unlimited");
  else snprintf(rate, sizeof(rate), "%u/min", (uint32_t)entry.rate_per_minute);
  if (entry.max_hops == FLOOD_GROUP_MODERATION_HOPS_ALL) strcpy(hops, "all");
  else snprintf(hops, sizeof(hops), "%u", (uint32_t)entry.max_hops);
  snprintf(reply, reply_len, "> %d channel=%s sender=\"%s\" path=%s rate=%s hops=%s",
           index + 1, entry.channel_name, entry.sender, path, rate, hops);
}

void MyMesh::formatFloodGroupModeration(const char* args, char* reply) const {
  const char* selector = skipFloodFilterSpaces(args);
  if (*selector == '.') selector = skipFloodFilterSpaces(selector + 1);
  if (*selector != 0) {
    uint8_t slot;
    if (!parseFloodFilterUnsigned(selector, FLOOD_GROUP_MODERATION_SLOTS, slot) || slot == 0) {
      snprintf(reply, 160, "Err - moderation slot must be 1-%d", FLOOD_GROUP_MODERATION_SLOTS);
      return;
    }
    formatFloodGroupModerationDetail(slot - 1, reply, 160);
    return;
  }

  size_t used = (size_t)snprintf(reply, 160, ">");
  int active_count = 0;
  bool truncated = false;
  for (int i = 0; i < FLOOD_GROUP_MODERATION_SLOTS; i++) {
    const auto& entry = flood_group_moderation[i];
    if (!entry.active) continue;
    active_count++;
    char action[20];
    if (entry.rate_per_minute == 0) strcpy(action, "drop");
    else if (entry.rate_per_minute != FLOOD_GROUP_MODERATION_RATE_UNLIMITED) {
      snprintf(action, sizeof(action), "r%u", (uint32_t)entry.rate_per_minute);
    } else if (entry.max_hops != FLOOD_GROUP_MODERATION_HOPS_ALL) {
      snprintf(action, sizeof(action), "h%u", (uint32_t)entry.max_hops);
    } else {
      strcpy(action, "off");
    }
    char item[72];
    snprintf(item, sizeof(item), " %d=%s/%s@%s", i + 1, entry.channel_name, entry.sender, action);
    size_t item_len = strlen(item);
    if (used + item_len >= 156) {
      truncated = true;
      break;
    }
    memcpy(&reply[used], item, item_len + 1);
    used += item_len;
  }
  if (active_count == 0) strcpy(reply, "> off");
  else if (truncated) StrHelper::strncpy(&reply[used], " ...", 160 - used);
}

void MyMesh::setFloodGroupModeration(const char* args, char* reply) {
  const char* cursor = skipFloodFilterSpaces(args);
  int requested_slot = -1;
  if (*cursor == '.') {
    cursor++;
    const char* slot_start = cursor;
    while (*cursor >= '0' && *cursor <= '9') cursor++;
    size_t slot_len = (size_t)(cursor - slot_start);
    char slot_text[8];
    if (slot_len == 0 || slot_len >= sizeof(slot_text)) {
      snprintf(reply, 160, "Err - moderation slot must be 1-%d", FLOOD_GROUP_MODERATION_SLOTS);
      return;
    }
    memcpy(slot_text, slot_start, slot_len);
    slot_text[slot_len] = 0;
    uint8_t slot;
    if (!parseFloodFilterUnsigned(slot_text, FLOOD_GROUP_MODERATION_SLOTS, slot) || slot == 0) {
      snprintf(reply, 160, "Err - moderation slot must be 1-%d", FLOOD_GROUP_MODERATION_SLOTS);
      return;
    }
    requested_slot = slot - 1;
    if (*cursor != ' ') {
      strcpy(reply, "Err - expected channel, sender, and action");
      return;
    }
  }

  char channel_text[80];
  char sender[FLOOD_GROUP_MODERATION_NAME_LEN];
  int token_result = takeFloodModerationToken(cursor, channel_text, sizeof(channel_text));
  if (token_result != 1 || takeFloodModerationToken(cursor, sender, sizeof(sender)) != 1) {
    strcpy(reply, "Err - use: set flood.moderation[.n] <channel> <sender> <action>");
    return;
  }
  if (!floodModerationSenderNameValid(sender)) {
    strcpy(reply, "Err - sender must be 1-31 chars; quote names with spaces");
    return;
  }

  uint8_t path_hash_size = 0;
  uint8_t path_hops = 0;
  uint8_t path[FLOOD_GROUP_MODERATION_PATH_BYTES_MAX];
  memset(path, 0, sizeof(path));
  uint8_t max_hops = FLOOD_GROUP_MODERATION_HOPS_ALL;
  uint16_t rate_per_minute = FLOOD_GROUP_MODERATION_RATE_UNLIMITED;
  bool action_set = false;
  bool rate_set = false;
  char option[48];
  while ((token_result = takeFloodModerationToken(cursor, option, sizeof(option))) == 1) {
    if (strcmp(option, "drop") == 0) {
      if (rate_set) {
        strcpy(reply, "Err - use only one rate/drop option");
        return;
      }
      rate_per_minute = 0;
      rate_set = true;
      action_set = true;
    } else if (strncmp(option, "rate=", 5) == 0) {
      if (rate_set) {
        strcpy(reply, "Err - use only one rate/drop option");
        return;
      }
      char rate_text[24];
      StrHelper::strncpy(rate_text, option + 5, sizeof(rate_text));
      char* slash = strchr(rate_text, '/');
      if (slash == NULL || !(strcmp(slash, "/min") == 0 || strcmp(slash, "/m") == 0)) {
        strcpy(reply, "Err - rate format is X/min");
        return;
      }
      *slash = 0;
      uint32_t parsed;
      if (!parseFloodModerationUnsigned(rate_text, FLOOD_GROUP_MODERATION_RATE_UNLIMITED - 1, parsed)) {
        strcpy(reply, "Err - rate must be 0-65534/min");
        return;
      }
      rate_per_minute = (uint16_t)parsed;
      rate_set = true;
      action_set = true;
    } else if (strncmp(option, "hops=", 5) == 0) {
      if (strcmp(option + 5, "all") == 0) {
        max_hops = FLOOD_GROUP_MODERATION_HOPS_ALL;
      } else {
        uint32_t parsed;
        if (!parseFloodModerationUnsigned(option + 5, FLOOD_PACKET_FILTER_MAX_HOPS, parsed)) {
          strcpy(reply, "Err - hops must be all or 0-63");
          return;
        }
        max_hops = (uint8_t)parsed;
        action_set = true;
      }
    } else if (strncmp(option, "path=", 5) == 0) {
      if (!parseFloodModerationPath(option + 5, path_hash_size, path_hops, path)) {
        strcpy(reply, "Err - path is * or 1-3 comma-separated 1/2/3-byte hashes");
        return;
      }
    } else {
      strcpy(reply, "Err - options: drop rate=X/min hops=N|all path=H1[,H2,H3]");
      return;
    }
  }
  if (token_result < 0) {
    strcpy(reply, "Err - malformed or overlong moderation option");
    return;
  }
  if (!action_set) {
    strcpy(reply, "Err - set drop, rate=X/min, or hops=N");
    return;
  }
  if (strcmp(sender, "*") == 0 && rate_per_minute != FLOOD_GROUP_MODERATION_RATE_UNLIMITED) {
    strcpy(reply, "Err - rate limiting requires an exact sender name");
    return;
  }

  uint8_t secret[PUB_KEY_SIZE];
  uint8_t key_len = 0;
  uint8_t hash_prefix[FLOOD_CHANNEL_KEY_PREFIX_LEN];
  char channel_name[FLOOD_GROUP_MODERATION_NAME_LEN];
  if (!parseFloodModerationChannel(channel_text, secret, key_len, hash_prefix,
                                   channel_name, sizeof(channel_name))) {
    strcpy(reply, "Err - channel must be public, #channel, or 128/256-bit hex key");
    return;
  }

  int slot = requested_slot;
  if (slot < 0) {
    for (int i = 0; i < FLOOD_GROUP_MODERATION_SLOTS; i++) {
      const auto& entry = flood_group_moderation[i];
      if (entry.active && entry.key_len == key_len && memcmp(entry.secret, secret, key_len) == 0
          && floodFilterAsciiEqual(entry.sender, sender)
          && entry.path_hash_size == path_hash_size && entry.path_hops == path_hops
          && memcmp(entry.path, path, sizeof(path)) == 0) {
        slot = i;
        break;
      }
    }
  }
  if (slot < 0) {
    for (int i = 0; i < FLOOD_GROUP_MODERATION_SLOTS; i++) {
      if (!flood_group_moderation[i].active) {
        slot = i;
        break;
      }
    }
  }
  if (slot < 0) {
    strcpy(reply, "Err - moderation table full");
    return;
  }

  FloodGroupModerationEntry previous = flood_group_moderation[slot];
  auto& entry = flood_group_moderation[slot];
  memset(&entry, 0, sizeof(entry));
  entry.active = true;
  entry.key_len = key_len;
  memcpy(entry.hash_prefix, hash_prefix, sizeof(entry.hash_prefix));
  memcpy(entry.secret, secret, sizeof(entry.secret));
  StrHelper::strncpy(entry.channel_name, channel_name, sizeof(entry.channel_name));
  StrHelper::strncpy(entry.sender, sender, sizeof(entry.sender));
  entry.path_hash_size = path_hash_size;
  entry.path_hops = path_hops;
  memcpy(entry.path, path, sizeof(entry.path));
  entry.max_hops = max_hops;
  entry.rate_per_minute = rate_per_minute;
  if (!saveFloodGroupModeration()) {
    entry = previous;
    strcpy(reply, "Err - unable to save moderation rule");
    return;
  }
  char detail[160];
  formatFloodGroupModerationDetail(slot, detail, sizeof(detail));
  snprintf(reply, 160, "OK - %s", detail[0] == '>' ? skipFloodFilterSpaces(detail + 1) : detail);
}

void MyMesh::deleteFloodGroupModeration(const char* args, char* reply) {
  const char* selector = skipFloodFilterSpaces(args);
  if (*selector == '.') selector = skipFloodFilterSpaces(selector + 1);
  if (floodFilterAsciiEqual(selector, "all")) {
    FloodGroupModerationEntry previous[FLOOD_GROUP_MODERATION_SLOTS];
    memcpy(previous, flood_group_moderation, sizeof(previous));
    memset(flood_group_moderation, 0, sizeof(flood_group_moderation));
    if (!saveFloodGroupModeration()) {
      memcpy(flood_group_moderation, previous, sizeof(flood_group_moderation));
      strcpy(reply, "Err - unable to save moderation rules");
    } else {
      strcpy(reply, "OK - all moderation rules removed");
    }
    return;
  }
  uint8_t slot;
  if (!parseFloodFilterUnsigned(selector, FLOOD_GROUP_MODERATION_SLOTS, slot) || slot == 0) {
    snprintf(reply, 160, "Err - use: del flood.moderation.<1-%d>|all", FLOOD_GROUP_MODERATION_SLOTS);
    return;
  }
  int index = slot - 1;
  if (!flood_group_moderation[index].active) {
    strcpy(reply, "Err - empty moderation slot");
    return;
  }
  FloodGroupModerationEntry previous = flood_group_moderation[index];
  memset(&flood_group_moderation[index], 0, sizeof(flood_group_moderation[index]));
  if (!saveFloodGroupModeration()) {
    flood_group_moderation[index] = previous;
    strcpy(reply, "Err - unable to save moderation rules");
  } else {
    strcpy(reply, "OK");
  }
}

#endif

#if !defined(PORTABLE_MQTT_OBSERVER) && MESH_ENABLE_CLOCK_SYNC

void MyMesh::loadClockSyncPrefs() {
  clock_sync_mesh_enabled = CLOCK_SYNC_MESH_DEFAULT_ENABLED != 0;
  clock_sync_mesh_edge_enabled = CLOCK_SYNC_MESH_EDGE_DEFAULT_ENABLED != 0;
  clock_sync_internet_enabled = false;
  clock_sync_drift_seconds = CLOCK_SYNC_DRIFT_DEFAULT_SECONDS;
  clock_sync_required_samples = CLOCK_SYNC_REQUIRED_SAMPLES_DEFAULT;

  if (_fs != NULL && _fs->exists(CLOCK_SYNC_PREFS_FILE)) {
    File file = openFloodSettingsRead(_fs, CLOCK_SYNC_PREFS_FILE);
    if (file) {
      uint8_t magic[4];
      uint8_t mesh_enabled = 0;
      uint8_t mesh_edge_enabled = CLOCK_SYNC_MESH_EDGE_DEFAULT_ENABLED != 0 ? 1 : 0;
      uint8_t internet_enabled = 0;
      uint8_t required_samples = CLOCK_SYNC_REQUIRED_SAMPLES_DEFAULT;
      uint32_t drift_seconds = 0;
      bool valid = file.read(magic, sizeof(magic)) == sizeof(magic);
      bool version3 = valid && memcmp(magic, "CTS3", sizeof(magic)) == 0;
      bool version4 = valid && memcmp(magic, "CTS4", sizeof(magic)) == 0;
      valid = valid && (version3 || version4)
          && file.read(&mesh_enabled, sizeof(mesh_enabled)) == sizeof(mesh_enabled)
          && file.read(&internet_enabled, sizeof(internet_enabled)) == sizeof(internet_enabled)
          && file.read((uint8_t*)&drift_seconds, sizeof(drift_seconds)) == sizeof(drift_seconds)
          && file.read(&required_samples, sizeof(required_samples)) == sizeof(required_samples);
      if (valid && version4) {
        valid = file.read(&mesh_edge_enabled, sizeof(mesh_edge_enabled)) == sizeof(mesh_edge_enabled);
      }
      valid = valid && mesh_enabled <= 1 && mesh_edge_enabled <= 1 && internet_enabled <= 1
          && drift_seconds >= CLOCK_SYNC_DRIFT_MIN_SECONDS
          && drift_seconds <= CLOCK_SYNC_DRIFT_MAX_SECONDS
          && required_samples >= CLOCK_SYNC_REQUIRED_SAMPLES_MIN
          && required_samples <= CLOCK_SYNC_REQUIRED_SAMPLES_MAX;
      file.close();
      if (valid) {
        clock_sync_mesh_enabled = mesh_enabled != 0;
        clock_sync_mesh_edge_enabled = mesh_edge_enabled != 0;
        clock_sync_internet_enabled = internet_enabled != 0;
        clock_sync_drift_seconds = drift_seconds;
        clock_sync_required_samples = required_samples;
      }
    }
  }
  resetClockSyncAttempt();
}

bool MyMesh::saveClockSyncPrefs() {
  if (_fs == NULL) return false;
  File file = openFloodSettingsWrite(_fs, CLOCK_SYNC_PREFS_FILE);
  if (!file) return false;
  const uint8_t magic[4] = {'C', 'T', 'S', '4'};
  const uint8_t mesh_enabled = clock_sync_mesh_enabled ? 1 : 0;
  const uint8_t mesh_edge_enabled = clock_sync_mesh_edge_enabled ? 1 : 0;
  const uint8_t internet_enabled = clock_sync_internet_enabled ? 1 : 0;
  bool success = file.write(magic, sizeof(magic)) == sizeof(magic)
      && file.write(&mesh_enabled, sizeof(mesh_enabled)) == sizeof(mesh_enabled)
      && file.write(&internet_enabled, sizeof(internet_enabled)) == sizeof(internet_enabled)
      && file.write((const uint8_t*)&clock_sync_drift_seconds,
                    sizeof(clock_sync_drift_seconds)) == sizeof(clock_sync_drift_seconds)
      && file.write(&clock_sync_required_samples,
                    sizeof(clock_sync_required_samples)) == sizeof(clock_sync_required_samples)
      && file.write(&mesh_edge_enabled, sizeof(mesh_edge_enabled)) == sizeof(mesh_edge_enabled);
  file.close();
  return success;
}

void MyMesh::resetClockSyncAttempt() {
  clock_sync_complete = false;
  clock_sync_internet_pending = false;
  clock_sync_force_mesh_pending = false;
  clock_sync_last_result = CLOCK_SYNC_RESULT_WAITING;
  clock_sync_last_source = CLOCK_SYNC_SOURCE_NONE;
  clock_sync_last_sample_count = 0;
  clock_sync_last_fresh_count = 0;
  clock_sync_last_required_count = clock_sync_required_samples;
  clock_sync_last_estimate = 0;
  clock_sync_last_abs_drift = 0;
  clock_sync_internet_requested_millis = 0;
  clock_sync_next_attempt_uptime = uptime_millis < CLOCK_SYNC_STARTUP_DELAY_MILLIS
      ? CLOCK_SYNC_STARTUP_DELAY_MILLIS : uptime_millis;
}

static const char* clockSyncMeshSuppressionName(uint8_t source) {
  switch (source) {
    case CLOCK_SYNC_MESH_SUPPRESS_CLI: return "cli";
    case CLOCK_SYNC_MESH_SUPPRESS_GPS: return "gps";
    case CLOCK_SYNC_MESH_SUPPRESS_INTERNET: return "internet";
    default: return "none";
  }
}

void MyMesh::suppressMeshClockSyncForBoot(uint8_t source) {
  if (source == CLOCK_SYNC_MESH_SUPPRESS_NONE
      || clock_sync_mesh_suppressed_by != CLOCK_SYNC_MESH_SUPPRESS_NONE) return;

  clock_sync_mesh_suppressed_by = source;
  clock_sync_force_mesh_pending = false;
  memset(clock_sync_samples, 0, sizeof(clock_sync_samples));
  MESH_DEBUG_PRINTLN("Clock sync: LoRa estimate suppressed by %s until reboot",
                     clockSyncMeshSuppressionName(source));
}

void MyMesh::onManualClockSet() {
  suppressMeshClockSyncForBoot(CLOCK_SYNC_MESH_SUPPRESS_CLI);
}

void MyMesh::checkGpsClockSyncOverride() {
  LocationProvider* location = sensors.getLocationProvider();
  if (location != NULL && location->consumeTimeSyncApplied()) {
    suppressMeshClockSyncForBoot(CLOCK_SYNC_MESH_SUPPRESS_GPS);
  }
}

bool MyMesh::isClockSyncCollectionActive() const {
  if (!clock_sync_mesh_enabled
      || clock_sync_mesh_suppressed_by != CLOCK_SYNC_MESH_SUPPRESS_NONE) return false;
  if (!clock_sync_complete) return true;
  if (clock_sync_next_attempt_uptime == 0) return false;
  if (uptime_millis >= clock_sync_next_attempt_uptime) return true;

  // Keep only the evidence that could still be fresh at the next scheduled
  // evaluation. This opens a two-hour rolling collection window before each
  // seven-day re-sync without decrypting Public traffic for the entire week.
  return clock_sync_next_attempt_uptime - uptime_millis
      <= (uint64_t)CLOCK_SYNC_SAMPLE_MAX_AGE_MILLIS;
}

static void deriveClockSyncPathId(const mesh::Packet* packet,
                                  uint8_t path_id[CLOCK_SYNC_PATH_ID_SIZE]) {
  uint8_t material[2 + MAX_PATH_SIZE];
  uint8_t count = packet->getPathHashCount();
  uint8_t path_bytes = packet->getPathByteLen();
  // All zero-hop receptions represent the same empty route, regardless of the
  // otherwise-unused hash-size bits in path_len.
  material[0] = count == 0 ? 0 : packet->getPathHashSize();
  material[1] = count;
  if (path_bytes > 0) memcpy(&material[2], packet->path, path_bytes);
  mesh::Utils::sha256(path_id, CLOCK_SYNC_PATH_ID_SIZE, material, 2 + path_bytes);
}

uint32_t MyMesh::estimateClockTransitMillis(const mesh::Packet* packet) const {
  if (packet == NULL || _radio == NULL) return 0;

  uint8_t hops = packet->getPathHashCount();
  uint8_t hash_size = packet->getPathHashSize();
  int base_length = packet->getRawLength() - packet->getPathByteLen();
  if (base_length < 2) return 0;

  // The origin sends the packet without a path entry. Each relay then appends
  // one entry, waits for its flood jitter, and transmits the longer packet.
  uint64_t total = _radio->getEstAirtimeFor(base_length);
  const uint64_t maximum = (uint64_t)CLOCK_SYNC_CONSENSUS_WINDOW_SECONDS * 1000ULL;
  for (uint8_t relay = 1; relay <= hops; relay++) {
    uint32_t airtime = _radio->getEstAirtimeFor(base_length + relay * hash_size);
    if (_prefs.tx_delay_factor > 0.0f) {
      // Flood forwarding selects uniformly from 0..5*t, where
      // t=airtime*tx_delay_factor. Use its midpoint as the best expectation.
      float expected_delay = (float)airtime * _prefs.tx_delay_factor * 2.5f;
      if (expected_delay > 0.0f) total += (uint32_t)(expected_delay + 0.5f);
    }
    total += airtime;
    if (total >= maximum) return (uint32_t)maximum;
  }
  return (uint32_t)total;
}

void MyMesh::recordClockSyncSample(uint8_t source_kind, const uint8_t source_id[4], uint32_t epoch,
                                   const mesh::Packet* packet) {
  if (!isClockSyncCollectionActive() || source_id == NULL
      || packet == NULL || !packet->isRouteFlood() || !clockSyncEpochIsValid(epoch)) return;

  uint32_t transit_millis = estimateClockTransitMillis(packet);
  uint32_t transit_seconds = (transit_millis + 500UL) / 1000UL;
  uint32_t maximum = clockSyncMaximumValidEpoch();
  if (epoch > maximum - transit_seconds) return;
  epoch += transit_seconds;

  uint8_t path_id[CLOCK_SYNC_PATH_ID_SIZE];
  deriveClockSyncPathId(packet, path_id);
  uint32_t now = _ms->getMillis();
  uint32_t received_millis = _radio == NULL ? 0 : _radio->getLastRecvMillis();
  // RadioLib records this immediately after reading the packet. Starting age
  // there also accounts for signature/decryption/filter processing before this
  // function runs. Fall back for radio backends that do not expose RX time.
  if (received_millis == 0 || now - received_millis > 60000UL) received_millis = now;

  int source_slot = -1;
  for (int i = 0; i < CLOCK_SYNC_SAMPLE_SLOTS; i++) {
    const ClockSyncSample& sample = clock_sync_samples[i];
    if (sample.active && sample.source_kind == source_kind
        && memcmp(sample.source_id, source_id, sizeof(sample.source_id)) == 0) {
      source_slot = i;
      break;
    }
  }

  if (source_slot >= 0) {
    const ClockSyncSample& prior = clock_sync_samples[source_slot];
    // An identical reception must not make old evidence look fresh.
    if (prior.epoch == epoch
        && memcmp(prior.path_id, path_id, sizeof(prior.path_id)) == 0) return;
  }

  if (mesh::clockSyncRequiresUniquePath(clock_sync_mesh_edge_enabled)) {
    // In normal mode every fresh vote must arrive through a distinct full
    // received path. This also means only one zero-hop vote can be present.
    for (int i = 0; i < CLOCK_SYNC_SAMPLE_SLOTS; i++) {
      const ClockSyncSample& sample = clock_sync_samples[i];
      if (i == source_slot || !sample.active
          || now - sample.received_millis > CLOCK_SYNC_SAMPLE_MAX_AGE_MILLIS) continue;
      if (memcmp(sample.path_id, path_id, sizeof(sample.path_id)) == 0) return;
    }
  }

  int slot = source_slot;
  int reusable = -1;
  int oldest = 0;
  uint32_t oldest_age = 0;
  for (int i = 0; slot < 0 && i < CLOCK_SYNC_SAMPLE_SLOTS; i++) {
    const ClockSyncSample& sample = clock_sync_samples[i];
    uint32_t age = sample.active ? now - sample.received_millis : 0;
    if ((!sample.active || age > CLOCK_SYNC_SAMPLE_MAX_AGE_MILLIS) && reusable < 0) reusable = i;
    if (sample.active && age >= oldest_age) {
      oldest_age = age;
      oldest = i;
    }
  }
  if (slot < 0) slot = reusable >= 0 ? reusable : oldest;

  ClockSyncSample& sample = clock_sync_samples[slot];
  sample.active = true;
  sample.source_kind = source_kind;
  memcpy(sample.source_id, source_id, sizeof(sample.source_id));
  memcpy(sample.path_id, path_id, sizeof(sample.path_id));
  sample.epoch = epoch;
  sample.received_millis = received_millis;

  // Do not leave a complete quorum waiting behind the startup or retry timer.
  // Evaluate on the next normal loop once enough fresh evidence exists. This
  // also refreshes a prior no-consensus result whenever a source changes.
  if (!clock_sync_complete) {
    uint32_t estimate = 0;
    uint8_t fresh = 0;
    uint8_t agreeing = 0;
    uint8_t required = clock_sync_required_samples;
    estimateMeshClock(estimate, fresh, agreeing, required);
    if (fresh >= clock_sync_required_samples) {
      clock_sync_last_fresh_count = fresh;
      clock_sync_last_sample_count = agreeing;
      clock_sync_last_required_count = required;
      clock_sync_next_attempt_uptime = uptime_millis;
    }
  }
}

void MyMesh::recordAcceptedFloodClockSample(const mesh::Packet* packet) {
  if (!isClockSyncCollectionActive() || packet == NULL) return;

  if (packet->getPayloadType() == PAYLOAD_TYPE_GRP_TXT) {
    recordPublicChannelClockSample(packet);
  } else if (packet->getPayloadType() == PAYLOAD_TYPE_ADVERT) {
    // Mesh::onRecvPacket reaches allowPacketForward() for adverts only after
    // verifying the Ed25519 signature over this identity and timestamp.
    const size_t minimum = PUB_KEY_SIZE + sizeof(uint32_t) + SIGNATURE_SIZE;
    if (packet->payload_len < minimum) return;
    uint8_t source_id[4];
    mesh::Utils::sha256(source_id, sizeof(source_id), packet->payload, PUB_KEY_SIZE);
    uint32_t timestamp = 0;
    memcpy(&timestamp, &packet->payload[PUB_KEY_SIZE], sizeof(timestamp));
    recordClockSyncSample(mesh::CLOCK_SYNC_SAMPLE_SOURCE_SIGNED_ADVERT,
                          source_id, timestamp, packet);
  }
}

void MyMesh::recordPublicChannelClockSample(const mesh::Packet* packet) {
  if (!isClockSyncCollectionActive()) return;
  uint32_t timestamp = 0;
  char sender[FLOOD_GROUP_MODERATION_NAME_LEN];
  if (!decodeFloodGroupPlainText(packet, FLOOD_PUBLIC_CHANNEL_SECRET, CIPHER_KEY_SIZE,
                                 timestamp, sender, sizeof(sender))) return;

  // Treat ASCII case variants of the same display name as one (unverified) source.
  for (char* p = sender; *p; p++) {
    if (*p >= 'A' && *p <= 'Z') *p = (char)(*p - 'A' + 'a');
  }
  uint8_t source_id[4];
  mesh::Utils::sha256(source_id, sizeof(source_id), (const uint8_t*)sender, strlen(sender));
  recordClockSyncSample(mesh::CLOCK_SYNC_SAMPLE_SOURCE_PUBLIC_CHANNEL,
                        source_id, timestamp, packet);
}

bool MyMesh::estimateMeshClock(uint32_t& estimate, uint8_t& fresh_count,
                               uint8_t& agreeing_count, uint8_t& required_count) const {
  uint32_t values[CLOCK_SYNC_SAMPLE_SLOTS];
  uint8_t count = 0;
  uint32_t now = _ms->getMillis();
  uint32_t maximum = clockSyncMaximumValidEpoch();
  for (int i = 0; i < CLOCK_SYNC_SAMPLE_SLOTS; i++) {
    const ClockSyncSample& sample = clock_sync_samples[i];
    if (!sample.active) continue;
    uint32_t age_millis = now - sample.received_millis;
    if (age_millis > CLOCK_SYNC_SAMPLE_MAX_AGE_MILLIS) continue;
    uint32_t age_seconds = age_millis / 1000UL;
    if (sample.epoch > maximum - age_seconds) continue;
    values[count++] = sample.epoch + age_seconds;
  }
  mesh::ClockSyncConsensusResult result = mesh::evaluateClockSyncConsensus(
      values, count, clock_sync_required_samples, CLOCK_SYNC_CONSENSUS_WINDOW_SECONDS);
  fresh_count = result.fresh_count;
  agreeing_count = result.agreeing_count;
  required_count = result.required_count;
  estimate = result.estimate;
  return result.consensus;
}

static uint32_t rebaseClockTimestamp(uint32_t timestamp, uint32_t old_now, uint32_t new_now) {
  if (timestamp == 0) return 0;
  uint32_t age = old_now >= timestamp ? old_now - timestamp : 0;
  return new_now > age ? new_now - age : 1;
}

bool MyMesh::applyClockEstimate(uint32_t estimate, uint8_t source, uint8_t sample_count) {
  if (!clockSyncEpochIsValid(estimate)) return false;
#ifdef WITH_MQTT_BRIDGE
  // Close the normal cross-core race where NTP can finish after checkClockSync()
  // selected mesh fallback but before that estimate reaches the RTC.
  if (source == CLOCK_SYNC_SOURCE_MESH && mqtt_bridge != NULL && mqtt_bridge->hasNtpTime()) {
    suppressMeshClockSyncForBoot(CLOCK_SYNC_MESH_SUPPRESS_INTERNET);
    return true;
  }
#endif
  uint32_t old_now = getRTCClock()->getCurrentTime();
  int64_t delta = (int64_t)estimate - (int64_t)old_now;
  uint64_t magnitude = delta < 0 ? (uint64_t)(-delta) : (uint64_t)delta;

  clock_sync_last_source = source;
  clock_sync_last_sample_count = sample_count;
  clock_sync_last_estimate = estimate;
  clock_sync_last_abs_drift = magnitude > UINT32_MAX ? UINT32_MAX : (uint32_t)magnitude;
  clock_sync_complete = true;
  clock_sync_next_attempt_uptime = uptime_millis + CLOCK_SYNC_RESYNC_INTERVAL_MILLIS;

  if (magnitude <= clock_sync_drift_seconds) {
    clock_sync_last_result = CLOCK_SYNC_RESULT_WITHIN_DRIFT;
    MESH_DEBUG_PRINTLN("Clock sync: within drift (%lu seconds, source=%u)",
                       (unsigned long)clock_sync_last_abs_drift, (unsigned int)source);
    return true;
  }

  getRTCClock()->setCurrentTime(estimate);
  getRTCClock()->resetUniqueTime(estimate);
  clock_sync_last_result = delta > 0
      ? CLOCK_SYNC_RESULT_CORRECTED_FORWARD : CLOCK_SYNC_RESULT_CORRECTED_BACKWARD;

#if MAX_NEIGHBOURS
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    neighbours[i].heard_timestamp = rebaseClockTimestamp(neighbours[i].heard_timestamp,
                                                         old_now, estimate);
  }
#endif
  for (int i = 0; i < acl.getNumClients(); i++) {
    ClientInfo* client = acl.getClientByIdx(i);
    client->last_activity = rebaseClockTimestamp(client->last_activity, old_now, estimate);
  }
  discover_limiter.reset();
  anon_limiter.reset();
  refreshScheduledRadioState();

  MESH_DEBUG_PRINTLN("Clock sync: corrected %s by %lu seconds (source=%u samples=%u)",
                     delta > 0 ? "forward" : "backward",
                     (unsigned long)clock_sync_last_abs_drift,
                     (unsigned int)source, (unsigned int)sample_count);
  return true;
}

void MyMesh::checkClockSync() {
#ifdef WITH_MQTT_BRIDGE
  // MQTT/WiFi builds already set the RTC as soon as an NTP server answers.
  // Once that authoritative source succeeds, LoRa remains only a next-boot
  // fallback and must not replace internet time later in this boot.
  if (mqtt_bridge != NULL && mqtt_bridge->hasNtpTime()) {
    suppressMeshClockSyncForBoot(CLOCK_SYNC_MESH_SUPPRESS_INTERNET);
  }
#endif

  bool mesh_available = clock_sync_mesh_enabled
      && clock_sync_mesh_suppressed_by == CLOCK_SYNC_MESH_SUPPRESS_NONE;
  bool force_mesh = clock_sync_force_mesh_pending && mesh_available;
  if ((!mesh_available && !clock_sync_internet_enabled)
      || (!force_mesh && uptime_millis < clock_sync_next_attempt_uptime)) return;

  // A manual mesh request bypasses both the normal deadline and the optional
  // internet-first probe once. It never bypasses the source safety latch.
  if (force_mesh) clock_sync_force_mesh_pending = false;

  // A successful estimate closes the current attempt but leaves a seven-day
  // deadline behind. Reopen the attempt when that deadline arrives; failures
  // below retain the existing 30-minute retry cadence until the next success.
  if (clock_sync_complete) clock_sync_complete = false;

  if (!force_mesh) {
#ifdef WITH_MQTT_BRIDGE
    if (clock_sync_internet_enabled && mqtt_bridge != NULL && mqtt_bridge->isRunning()) {
      if (clock_sync_internet_pending) {
        uint32_t estimate = 0;
        bool finished = false;
        bool success = mqtt_bridge->takeNtpTimeEstimate(estimate, finished);
        if (!finished && _ms->getMillis() - clock_sync_internet_requested_millis < 30000UL) return;
        clock_sync_internet_pending = false;
        if (success && applyClockEstimate(estimate, CLOCK_SYNC_SOURCE_INTERNET, 1)) return;
        clock_sync_last_result = CLOCK_SYNC_RESULT_INTERNET_UNAVAILABLE;
      } else if (mqtt_bridge->requestNtpTimeEstimate()) {
        clock_sync_internet_pending = true;
        clock_sync_internet_requested_millis = _ms->getMillis();
        clock_sync_last_result = CLOCK_SYNC_RESULT_INTERNET_PENDING;
        return;
      } else {
        clock_sync_last_result = CLOCK_SYNC_RESULT_INTERNET_UNAVAILABLE;
      }
    } else if (clock_sync_internet_enabled) {
      clock_sync_internet_pending = false;
      clock_sync_last_result = CLOCK_SYNC_RESULT_INTERNET_UNAVAILABLE;
    }
#else
    if (clock_sync_internet_enabled) {
      clock_sync_internet_pending = false;
      clock_sync_last_result = CLOCK_SYNC_RESULT_INTERNET_UNAVAILABLE;
    }
#endif
  }

  if (mesh_available) {
    uint32_t estimate = 0;
    uint8_t fresh = 0;
    uint8_t agreeing = 0;
    uint8_t required = clock_sync_required_samples;
    bool consensus = estimateMeshClock(estimate, fresh, agreeing, required);
    clock_sync_last_fresh_count = fresh;
    clock_sync_last_sample_count = agreeing;
    clock_sync_last_required_count = required;
    if (consensus && applyClockEstimate(estimate, CLOCK_SYNC_SOURCE_MESH, agreeing)) return;
    clock_sync_last_result = fresh < clock_sync_required_samples
        ? CLOCK_SYNC_RESULT_COLLECTING : CLOCK_SYNC_RESULT_NO_CONSENSUS;
  }
  clock_sync_next_attempt_uptime = uptime_millis + CLOCK_SYNC_RETRY_INTERVAL_MILLIS;
}

static const char* clockSyncSampleKindName(uint8_t source_kind) {
  if (source_kind == mesh::CLOCK_SYNC_SAMPLE_SOURCE_SIGNED_ADVERT) return "advert";
  if (source_kind == mesh::CLOCK_SYNC_SAMPLE_SOURCE_PUBLIC_CHANNEL) return "public";
  return "unknown";
}

static char clockSyncSampleKindCode(uint8_t source_kind) {
  if (source_kind == mesh::CLOCK_SYNC_SAMPLE_SOURCE_SIGNED_ADVERT) return 'A';
  if (source_kind == mesh::CLOCK_SYNC_SAMPLE_SOURCE_PUBLIC_CHANNEL) return 'P';
  return '?';
}

void MyMesh::formatClockSyncSampleDetail(int index, char* reply, size_t reply_len) const {
  if (index < 0 || index >= CLOCK_SYNC_SAMPLE_SLOTS) {
    snprintf(reply, reply_len, "Err - clock sample slot must be 1-%d", CLOCK_SYNC_SAMPLE_SLOTS);
    return;
  }
  const ClockSyncSample& sample = clock_sync_samples[index];
  if (!sample.active) {
    snprintf(reply, reply_len, "> %d empty", index + 1);
    return;
  }

  uint32_t age_seconds = (_ms->getMillis() - sample.received_millis) / 1000UL;
  bool fresh = age_seconds <= CLOCK_SYNC_SAMPLE_MAX_AGE_MILLIS / 1000UL;
  uint32_t current_epoch = sample.epoch;
  if (age_seconds <= UINT32_MAX - current_epoch) current_epoch += age_seconds;
  uint32_t local_epoch = getRTCClock()->getCurrentTime();
  char delta_sign = current_epoch >= local_epoch ? '+' : '-';
  uint32_t delta = current_epoch >= local_epoch
      ? current_epoch - local_epoch : local_epoch - current_epoch;
  char source_id[sizeof(sample.source_id) * 2 + 1];
  char path_id[sizeof(sample.path_id) * 2 + 1];
  mesh::Utils::toHex(source_id, sample.source_id, sizeof(sample.source_id));
  mesh::Utils::toHex(path_id, sample.path_id, sizeof(sample.path_id));
  snprintf(reply, reply_len,
           "> %d %s id=%s path=%s age=%lus epoch=%lu delta=%c%lus fresh=%s",
           index + 1, clockSyncSampleKindName(sample.source_kind), source_id, path_id,
           (unsigned long)age_seconds, (unsigned long)current_epoch, delta_sign,
           (unsigned long)delta, fresh ? "yes" : "no");
}

void MyMesh::formatClockSyncTable(char* reply, size_t reply_len) const {
  uint32_t now = _ms->getMillis();
  uint8_t fresh = 0;
  uint8_t active = 0;
  for (int i = 0; i < CLOCK_SYNC_SAMPLE_SLOTS; i++) {
    if (!clock_sync_samples[i].active) continue;
    active++;
    if (now - clock_sync_samples[i].received_millis <= CLOCK_SYNC_SAMPLE_MAX_AGE_MILLIS) fresh++;
  }

  const char* mode = clock_sync_mesh_edge_enabled ? "edge" : "paths";
  size_t used = (size_t)snprintf(reply, reply_len, "> %s collect=%s fresh=%u/%u",
                                 mode, isClockSyncCollectionActive() ? "active" : "inactive",
                                 (unsigned int)fresh,
                                 (unsigned int)clock_sync_required_samples);
  if (active == 0 || used >= reply_len) {
    if (active == 0 && used + 5 < reply_len) StrHelper::strncpy(&reply[used], " none", reply_len - used);
    return;
  }

  for (int i = 0; i < CLOCK_SYNC_SAMPLE_SLOTS && used + 1 < reply_len; i++) {
    const ClockSyncSample& sample = clock_sync_samples[i];
    if (!sample.active) continue;
    uint32_t age_seconds = (now - sample.received_millis) / 1000UL;
    unsigned long age_value = age_seconds < 120UL
        ? (unsigned long)age_seconds : (unsigned long)(age_seconds / 60UL);
    char age_unit = age_seconds < 120UL ? 's' : 'm';
    bool sample_fresh = age_seconds <= CLOCK_SYNC_SAMPLE_MAX_AGE_MILLIS / 1000UL;
    char item[24];
    snprintf(item, sizeof(item), " %d:%c:%02X%02X:%lu%c%s", i + 1,
             clockSyncSampleKindCode(sample.source_kind), sample.source_id[0],
             sample.source_id[1], age_value, age_unit, sample_fresh ? "" : "!");
    size_t item_len = strlen(item);
    if (used + item_len >= reply_len - 4) {
      StrHelper::strncpy(&reply[used], " ...", reply_len - used);
      return;
    }
    memcpy(&reply[used], item, item_len + 1);
    used += item_len;
  }
}

void MyMesh::formatClockSyncStatus(const char* args, char* reply, size_t reply_len) const {
  if (reply == NULL || reply_len == 0) return;
  const char* selector = skipLocalSpaces(args);
  if (selector != NULL && *selector == '.') selector = skipLocalSpaces(selector + 1);
  if (selector != NULL && *selector != 0) {
    if (strcmp(selector, "table") == 0) {
      formatClockSyncTable(reply, reply_len);
      return;
    }
    int slot = 0;
    if (parsePositiveSelector(selector, slot) && slot <= CLOCK_SYNC_SAMPLE_SLOTS) {
      formatClockSyncSampleDetail(slot - 1, reply, reply_len);
      return;
    }
    snprintf(reply, reply_len, "Err - use get clock.sync.status[.table|.1-.%d]",
             CLOCK_SYNC_SAMPLE_SLOTS);
    return;
  }

  uint8_t active = 0;
  for (int i = 0; i < CLOCK_SYNC_SAMPLE_SLOTS; i++) {
    if (!clock_sync_samples[i].active) continue;
    active++;
  }
  uint32_t live_estimate = 0;
  uint8_t fresh = 0;
  uint8_t live_agreeing = 0;
  uint8_t live_required = clock_sync_required_samples;
  bool live_consensus = estimateMeshClock(live_estimate, fresh, live_agreeing, live_required);
  bool mesh_available = clock_sync_mesh_enabled
      && clock_sync_mesh_suppressed_by == CLOCK_SYNC_MESH_SUPPRESS_NONE;
  const char* mesh_state = !clock_sync_mesh_enabled ? "off"
      : (mesh_available ? "on"
          : (clock_sync_mesh_suppressed_by == CLOCK_SYNC_MESH_SUPPRESS_CLI
              ? "suppressed-cli"
              : (clock_sync_mesh_suppressed_by == CLOCK_SYNC_MESH_SUPPRESS_GPS
                  ? "suppressed-gps" : "suppressed-internet")));
  const char* mesh_mode = clock_sync_mesh_edge_enabled ? "edge" : "paths";
  const char* evidence_name = clock_sync_mesh_edge_enabled ? "sources" : "paths";
  bool collection_active = isClockSyncCollectionActive();
  uint64_t remaining_ms = clock_sync_next_attempt_uptime > uptime_millis
      ? clock_sync_next_attempt_uptime - uptime_millis : 0;
  unsigned long next_seconds = (unsigned long)((remaining_ms + 999ULL) / 1000ULL);
  if (!mesh_available && !clock_sync_internet_enabled) {
    const char* reason = clock_sync_mesh_enabled ? mesh_state : "mesh-off";
    snprintf(reply, reply_len,
             "> not-set reason=%s collect=inactive mode=%s %s=%u/%u table=%u",
             reason, mesh_mode, evidence_name, (unsigned int)fresh,
             (unsigned int)clock_sync_required_samples, (unsigned int)active);
    return;
  }

  const char* result = "waiting";
  switch (clock_sync_last_result) {
    case CLOCK_SYNC_RESULT_COLLECTING: result = "collecting"; break;
    case CLOCK_SYNC_RESULT_INTERNET_PENDING: result = "internet-pending"; break;
    case CLOCK_SYNC_RESULT_NO_CONSENSUS: result = "no-consensus"; break;
    case CLOCK_SYNC_RESULT_INTERNET_UNAVAILABLE: result = "internet-unavailable"; break;
    case CLOCK_SYNC_RESULT_WITHIN_DRIFT: result = "within-drift"; break;
    case CLOCK_SYNC_RESULT_CORRECTED_FORWARD: result = "corrected-forward"; break;
    case CLOCK_SYNC_RESULT_CORRECTED_BACKWARD: result = "corrected-backward"; break;
    default: break;
  }
  const char* source = clock_sync_last_source == CLOCK_SYNC_SOURCE_INTERNET ? "internet"
      : (clock_sync_last_source == CLOCK_SYNC_SOURCE_MESH ? "mesh" : "none");
  if (clock_sync_complete) {
    bool clock_was_set = clock_sync_last_result == CLOCK_SYNC_RESULT_CORRECTED_FORWARD
        || clock_sync_last_result == CLOCK_SYNC_RESULT_CORRECTED_BACKWARD;
    snprintf(reply, reply_len,
             "> %s reason=%s difference=%lus threshold=%lus via=%s collect=%s mode=%s table=%u next=%lus",
             clock_was_set ? "set" : "not-set", result,
             (unsigned long)clock_sync_last_abs_drift,
             (unsigned long)clock_sync_drift_seconds, source,
             collection_active ? "active" : "inactive", mesh_mode,
             (unsigned int)active, next_seconds);
  } else {
    if (clock_sync_last_result == CLOCK_SYNC_RESULT_NO_CONSENSUS
        || (fresh >= clock_sync_required_samples && !live_consensus)) {
      snprintf(reply, reply_len,
               "> not-set reason=no-consensus collect=%s mode=%s %s=%u agree=%u/%u table=%u next=%lus",
               collection_active ? "active" : "inactive", mesh_mode, evidence_name,
               (unsigned int)fresh,
               (unsigned int)live_agreeing,
               (unsigned int)live_required,
               (unsigned int)active, next_seconds);
    } else if (live_consensus) {
      snprintf(reply, reply_len,
               "> not-set reason=ready collect=%s mode=%s %s=%u agree=%u/%u table=%u next=%lus",
               collection_active ? "active" : "inactive", mesh_mode, evidence_name,
               (unsigned int)fresh, (unsigned int)live_agreeing,
               (unsigned int)live_required, (unsigned int)active, next_seconds);
    } else {
      const char* reason = result;
      if (clock_sync_last_result == CLOCK_SYNC_RESULT_WAITING) reason = "waiting-deadline";
      else if (clock_sync_last_result == CLOCK_SYNC_RESULT_COLLECTING) {
        reason = clock_sync_mesh_edge_enabled ? "need-more-sources" : "need-more-paths";
      }
      snprintf(reply, reply_len,
               "> not-set reason=%s collect=%s mesh=%s mode=%s %s=%u/%u table=%u next=%lus",
               reason, collection_active ? "active" : "inactive", mesh_state, mesh_mode,
               evidence_name, (unsigned int)fresh,
               (unsigned int)clock_sync_required_samples, (unsigned int)active, next_seconds);
    }
  }
}

#else

// Builds without mesh consensus retain manual clock setting and scheduled
// radio changes. MQTT observers use their bridge's NTP source.
static const char* clockSyncMeshSuppressionName(uint8_t source) {
  (void)source;
  return "unavailable";
}

void MyMesh::onManualClockSet() {}

#endif

void MyMesh::formatStatsReply(char *reply) {
  StatsFormatHelper::formatCoreStats(reply, board, *_ms, _err_flags, _mgr);
}

void MyMesh::formatRadioStatsReply(char *reply) {
  StatsFormatHelper::formatRadioStats(reply, _radio, radio_driver, getTotalAirTime(), getReceiveAirTime());
}

void MyMesh::formatRadioDiagReply(char *reply) {
  StatsFormatHelper::formatRadioDiag(reply, _radio, radio_driver, *_ms, _err_flags, hasOutbound());
}

void MyMesh::formatPacketStatsReply(char *reply) {
  StatsFormatHelper::formatPacketStats(reply, radio_driver, getNumSentFlood(), getNumSentDirect(),
                                       getNumRecvFlood(), getNumRecvDirect());
}

void MyMesh::saveIdentity(const mesh::LocalIdentity &new_id) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  IdentityStore store(*_fs, "");
#elif defined(ESP32)
  IdentityStore store(*_fs, "/identity");
#elif defined(RP2040_PLATFORM)
  IdentityStore store(*_fs, "/identity");
#else
#error "need to define saveIdentity()"
#endif
  store.save("_main", new_id);
}

void MyMesh::clearStats() {
  radio_driver.resetStats();
  resetStats();
  ((SimpleMeshTables *)getTables())->resetStats();
}

#ifdef WITH_WEBCONFIG
void MyMesh::getNodeSnapshot(WebConfigServer::NodeSnapshot& s) {
  memset(&s, 0, sizeof(s));
  StrHelper::strncpy(s.name, _prefs.node_name, sizeof(s.name));
  StrHelper::strncpy(s.admin_password, _prefs.password, sizeof(s.admin_password));
  s.lat = _prefs.node_lat;
  s.lon = _prefs.node_lon;
  s.freq = _prefs.freq;
  s.bw = _prefs.bw;
  s.sf = _prefs.sf;
  s.cr = _prefs.cr;
  s.tx_power = _prefs.tx_power_dbm;
  s.airtime_factor = _prefs.airtime_factor;
  s.rx_delay = _prefs.rx_delay_base;
  s.tx_delay = _prefs.tx_delay_factor;
  s.cad = _prefs.cad_enabled;
  s.rx_gain = _prefs.rx_boosted_gain;
  s.fem_rx_gain = board.isLoRaFemLnaEnabled();
  s.rx_ps_enabled = _prefs.rx_powersaving_enabled;
  s.rx_ps_level = _prefs.rx_ps_level;
  s.rx_ps_preamble = _prefs.rx_ps_preamble;
  s.rx_ps_rx_us = _prefs.rx_ps_rx_us;
  s.rx_ps_sleep_us = _prefs.rx_ps_sleep_us;
  s.power_saving = _prefs.powersaving_enabled;
  s.repeat = !_prefs.disable_fwd;
  s.advert_interval = _prefs.advert_interval * 2;
  s.flood_advert_interval = _prefs.flood_advert_interval;
  s.flood_max = _prefs.flood_max;
  s.flood_max_advert = _prefs.flood_max_advert;
  s.flood_max_unscoped = _prefs.flood_max_unscoped;
  s.loop_detect = _prefs.loop_detect;
  s.capabilities = WebConfigServer::CAP_LOCATION | WebConfigServer::CAP_AIRTIME
      | WebConfigServer::CAP_DELAYS | WebConfigServer::CAP_CAD
      | WebConfigServer::CAP_RX_GAIN | WebConfigServer::CAP_REPEAT
      | WebConfigServer::CAP_ADVERT | WebConfigServer::CAP_FLOOD
      | WebConfigServer::CAP_LOOP | WebConfigServer::CAP_WIFI_POWER_SAVE
      | WebConfigServer::CAP_POWER_SAVING;
  if (board.canControlLoRaFemLna()) {
    s.capabilities |= WebConfigServer::CAP_FEM_RX_GAIN;
  }
  if (radio_driver.supportsRxPowerSaving()) {
    s.capabilities |= WebConfigServer::CAP_RX_POWER_SAVING;
  }
}

bool MyMesh::startWebConfig(bool force_ap, char* reply) {
  if (_cli.getBoard()->isOTAUpdateRunning()) {
    strcpy(reply, "Err: OTA server is running - 'stop ota' first");
    return true;
  }
  if (_webconfig && (_webconfig->isRunning() || _webconfig->isStopping())) {
    strcpy(reply, _webconfig->isStopping() ? "Err: webconfig still stopping, retry shortly"
                                           : "Err: webconfig already running");
    return true;
  }
  if (!_webconfig) {
    void* mqtt_prefs = nullptr;
    bool owns_wifi = true;
#ifdef WITH_MQTT_BRIDGE
    mqtt_prefs = _cli.getObserverPrefs();
    owns_wifi = false;
#endif
    _webconfig = new WebConfigServer(this, mqtt_prefs, owns_wifi,
                                     self_id.pub_key, getFirmwareVer(), getBuildDate(), getRole(),
                                     _cli.getBoard()->getManufacturerName());
    if (!_webconfig) {
      strcpy(reply, "Err: not enough memory for webconfig");
      return true;
    }
  }

  if (force_ap) {
#ifdef WITH_MQTT_BRIDGE
    if (mqtt_bridge && mqtt_bridge->isRunning()) {
      strcpy(reply, "Err: MQTT bridge is running - 'set bridge off' first");
      return true;
    }
#endif
    _webconfig->startSetupMode(reply);
  } else {
    _webconfig->startAutoMode(reply);
  }
  return true;
}

bool MyMesh::stopWebConfig(char* reply) {
  if (!_webconfig || !_webconfig->isRunning()) {
    strcpy(reply, "Err: webconfig not running");
    return true;
  }
  _webconfig->requestStop();
  strcpy(reply, "OK - webconfig stopping");
  return true;
}

bool MyMesh::setWebUIEnabled(bool enabled, char* reply) {
  if (!WebConfigServer::saveEnabled(enabled)) {
    strcpy(reply, "Error: failed to save webui setting");
    return true;
  }
  if (enabled) {
    if (_webconfig && (_webconfig->isRunning() || _webconfig->isStopping())) {
      strcpy(reply, "OK - webui on (already active)");
    } else {
      startWebConfig(false, reply);
      if (strncmp(reply, "WebConfig", 9) == 0) {
        char tmp[160];
        StrHelper::strncpy(tmp, reply, sizeof(tmp));
        snprintf(reply, 160, "OK - webui on; %s", tmp);
      }
    }
  } else {
    if (_webconfig && _webconfig->isRunning()) _webconfig->requestStop();
    strcpy(reply, "OK - webui off");
  }
  return true;
}

bool MyMesh::getWebUIStatus(char* reply) const {
  const bool enabled = WebConfigServer::loadEnabled(false);
  if (!_webconfig || (!_webconfig->isRunning() && !_webconfig->isStopping())) {
    snprintf(reply, 160, "> %s, inactive", enabled ? "on" : "off");
  } else if (_webconfig->mode() == WebConfigServer::MODE_SETUP) {
    char ssid[33], ip[16];
    WebConfigServer::getSetupInfo(ssid, sizeof(ssid), ip, sizeof(ip));
    snprintf(reply, 160, "> %s, setup AP %s http://%s/", enabled ? "on" : "off", ssid, ip);
  } else if (_webconfig->mode() == WebConfigServer::MODE_CONNECTING) {
    snprintf(reply, 160, "> %s, connecting to WiFi", enabled ? "on" : "off");
  } else {
    snprintf(reply, 160, "> %s, http://%s/", enabled ? "on" : "off",
             WiFi.localIP().toString().c_str());
  }
  return true;
}

bool MyMesh::getWiFiSSID(char* reply) const {
  return WebConfigServer::formatWiFiSSID(reply, 160);
}

bool MyMesh::getWiFiStatus(char* reply) const {
  return WebConfigServer::formatWiFiStatus(reply, 160);
}

bool MyMesh::getWiFiPowerSave(char* reply) const {
  return WebConfigServer::formatWiFiPowerSave(reply, 160);
}

bool MyMesh::getWiFiCLI(char* reply) const {
  return WebConfigServer::formatWiFiCliStatus(reply, 160);
}

bool MyMesh::setWiFiSSID(const char* value, char* reply) {
  if (WebConfigServer::setStandaloneWiFiSSID(value, reply, 160)) {
    const bool was_running = _webconfig && _webconfig->isRunning();
    if (was_running) _webconfig->requestStop();
    if (_webconfig) _webconfig->reloadStandaloneWiFi();
    if (was_running) {
      strcpy(reply, "OK - WiFi SSID saved; WebConfig stopping, start again to apply");
    }
  }
  return true;
}

bool MyMesh::setWiFiPassword(const char* value, char* reply) {
  if (WebConfigServer::setStandaloneWiFiPassword(value, reply, 160)) {
    const bool was_running = _webconfig && _webconfig->isRunning();
    if (was_running) _webconfig->requestStop();
    if (_webconfig) _webconfig->reloadStandaloneWiFi();
    if (was_running) {
      strcpy(reply, "OK - WiFi password saved; WebConfig stopping, start again to apply");
    }
  }
  return true;
}

bool MyMesh::setWiFiPowerSave(const char* value, char* reply) {
  if (WebConfigServer::setStandaloneWiFiPowerSave(value, reply, 160)
      && _webconfig) {
    _webconfig->reloadStandaloneWiFi();
  }
  return true;
}

bool MyMesh::setWiFiCLI(const char* value, char* reply) {
  WebConfigServer::setWiFiCliEnabled(value, reply, 160);
  return true;
}

void MyMesh::onConfigBatchEnd() {
  _wc_batch_active = false;
#ifdef WITH_MQTT_BRIDGE
  if (_wc_restart_pending) {
    _wc_restart_pending = false;
    _wc_slot_restart_mask = 0;
    restartBridge();
    return;
  }

  const uint8_t mask = _wc_slot_restart_mask;
  _wc_slot_restart_mask = 0;
  for (int i = 0; i < RUNTIME_MQTT_SLOTS; i++) {
    if (mask & (1U << i)) restartBridgeSlot(i);
  }
#else
  _wc_restart_pending = false;
  _wc_slot_restart_mask = 0;
#endif
}

void MyMesh::buildStatsJson(char* buf, size_t buf_size) {
  char ip[20] = "";
  int wifi_rssi = 0;
  if (WiFi.status() == WL_CONNECTED) {
    strncpy(ip, WiFi.localIP().toString().c_str(), sizeof(ip) - 1);
    wifi_rssi = WiFi.RSSI();
  } else if (_webconfig && _webconfig->mode() == WebConfigServer::MODE_SETUP) {
    strncpy(ip, WiFi.softAPIP().toString().c_str(), sizeof(ip) - 1);
  }
  char snr_text[16];
  StrHelper::ftoaFixed(snr_text, sizeof(snr_text), radio_driver.getLastSNR(), 1);
  int pos = snprintf(buf, buf_size,
      "{\"uptime_s\":%lu,\"batt_mv\":%u,"
      "\"heap_free\":%lu,\"heap_min\":%lu,\"heap_max_alloc\":%lu,"
      "\"noise\":%d,\"rssi\":%d,\"snr\":%s,"
      "\"airtime_s\":%lu,\"rx_airtime_s\":%lu,"
      "\"recv\":%lu,\"sent\":%lu,\"rx_err\":%lu,"
      "\"sent_flood\":%lu,\"sent_direct\":%lu,\"recv_flood\":%lu,\"recv_direct\":%lu,"
      "\"tx_queue\":%d,\"wifi_rssi\":%d,\"ip\":\"%s\",\"mqtt_queue\":%d,\"slots\":[",
      (unsigned long)(uptime_millis / 1000), (unsigned)board.getBattMilliVolts(),
      (unsigned long)ESP.getFreeHeap(), (unsigned long)ESP.getMinFreeHeap(),
      (unsigned long)ESP.getMaxAllocHeap(),
      (int)_radio->getNoiseFloor(), (int)radio_driver.getLastRSSI(),
      snr_text,
      (unsigned long)(getTotalAirTime() / 1000), (unsigned long)(getReceiveAirTime() / 1000),
      (unsigned long)radio_driver.getPacketsRecv(), (unsigned long)radio_driver.getPacketsSent(),
      (unsigned long)radio_driver.getPacketsRecvErrors(),
      (unsigned long)getNumSentFlood(), (unsigned long)getNumSentDirect(),
      (unsigned long)getNumRecvFlood(), (unsigned long)getNumRecvDirect(),
      (int)_mgr->getOutboundCount(0xFFFFFFFF), wifi_rssi, ip,
#ifdef WITH_MQTT_BRIDGE
      mqtt_bridge ? mqtt_bridge->getQueueSize() : 0);
#else
      0);
#endif
  if (pos < 0 || pos >= static_cast<int>(buf_size) - 3) return;

  bool first = true;
#ifdef WITH_MQTT_BRIDGE
  for (int i = 0; i < RUNTIME_MQTT_SLOTS; i++) {
    MQTTBridge::SlotStatusSnapshot status;
    if (!MQTTBridge::getSlotStatusSnapshot(i, &status)) continue;
    char filter[24] = "";
    if (status.filter_mask != MQTTPacketFilter::kAllPacketTypes) {
      snprintf(filter, sizeof(filter), ",\"filt\":%u",
               (unsigned)status.filter_mask);
    }
    int written;
    if (status.has_publish_counts) {
      written = snprintf(buf + pos, buf_size - pos,
          "%s{\"n\":%d,\"name\":\"%s\",\"state\":\"%s\",\"ok\":%lu,\"err\":%lu%s}",
          first ? "" : ",", i + 1, status.name, status.state,
          status.publish_ok, status.publish_err, filter);
    } else {
      written = snprintf(buf + pos, buf_size - pos,
          "%s{\"n\":%d,\"name\":\"%s\",\"state\":\"%s\"%s}",
          first ? "" : ",", i + 1, status.name, status.state, filter);
    }
    if (written < 0 || written >= static_cast<int>(buf_size - pos)) break;
    pos += written;
    first = false;
  }
#else
  (void)first;
#endif
  snprintf(buf + pos, buf_size - pos, "]}");
}
#endif

static char* trimSpaces(char* s) {
  while (*s == ' ') s++;
  char* end = s + strlen(s);
  while (end > s && end[-1] == ' ') end--;
  *end = 0;
  return s;
}

static bool parsePathCommand(char* raw, uint8_t* out_path, uint8_t& out_path_len, const char*& err) {
  if (raw == NULL || out_path == NULL) {
    err = "Err - bad params";
    return false;
  }

  char* spec = trimSpaces(raw);
  if (*spec == 0) {
    err = "Err - missing path";
    return false;
  }
  if (strcmp(spec, "clear") == 0 || strcmp(spec, "-") == 0 || strcmp(spec, "none") == 0) {
    out_path_len = OUT_PATH_UNKNOWN;
    return true;
  }
  if (strcmp(spec, "flood") == 0) {
    out_path_len = OUT_PATH_FORCE_FLOOD;
    return true;
  }
  if (strcmp(spec, "direct") == 0) {
    out_path_len = 0;
    return true;
  }

  uint8_t hash_size = 0;
  uint8_t hop_count = 0;
  char* token = spec;
  while (token && *token) {
    char* comma = strchr(token, ',');
    if (comma) *comma = 0;
    token = trimSpaces(token);

    int hex_len = strlen(token);
    if (!(hex_len == 2 || hex_len == 4 || hex_len == 6)) {
      err = "Err - bad params";
      return false;
    }

    uint8_t hop_hash_size = (uint8_t)(hex_len / 2);
    if (hash_size == 0) {
      hash_size = hop_hash_size;
    } else if (hash_size != hop_hash_size) {
      err = "Err - bad params";
      return false;
    }

    if (hop_count >= 63 || (hop_count + 1) * hash_size > MAX_PATH_SIZE) {
      err = "Err - bad params";
      return false;
    }
    if (!mesh::Utils::fromHex(&out_path[hop_count * hash_size], hash_size, token)) {
      err = "Err - bad hex";
      return false;
    }

    hop_count++;
    token = comma ? comma + 1 : NULL;
  }

  if (hash_size == 0 || hop_count == 0) {
    err = "Err - missing path";
    return false;
  }
  out_path_len = ((hash_size - 1) << 6) | (hop_count & 63);
  return true;
}

static void formatPathReply(const uint8_t* path, uint8_t path_len, char* out, size_t out_len) {
  if (path_len == OUT_PATH_FORCE_FLOOD) {
    snprintf(out, out_len, "> flood");
    return;
  }
  if (path_len == OUT_PATH_UNKNOWN) {
    snprintf(out, out_len, "> unknown");
    return;
  }
  if (!mesh::Packet::isValidPathLen(path_len)) {
    snprintf(out, out_len, "> invalid");
    return;
  }
  if ((path_len & 63) == 0) {
    snprintf(out, out_len, "> direct");
    return;
  }

  uint8_t hash_size = (path_len >> 6) + 1;
  uint8_t hop_count = path_len & 63;
  size_t path_text_len = (size_t)hop_count * hash_size * 2 + (hop_count - 1);

  // Every path that fits in the 160-byte CLI command buffer also fits this
  // shorter reply form. Retain a compact, complete fallback for a path learned
  // from an unusually long over-the-air route rather than truncating it.
  if (path_text_len + 3 > out_len) {
    out[0] = '>';
    out[1] = ' ';
    mesh::Utils::toHex(out + 2, path, (size_t)hop_count * hash_size);
    return;
  }

  size_t pos = 0;
  out[pos++] = '>';
  out[pos++] = ' ';
  for (uint8_t hop = 0; hop < hop_count; hop++) {
    mesh::Utils::toHex(out + pos, path + ((size_t)hop * hash_size), hash_size);
    pos += hash_size * 2;
    if (hop + 1 < hop_count) out[pos++] = ',';
  }
  out[pos] = 0;
}

enum ClientPathCommand : uint8_t {
  CLIENT_PATH_NONE = 0,
  CLIENT_PATH_VALID = 1,
  CLIENT_PATH_SET = 2,
  CLIENT_PATH_ALT = 4,
};

static __attribute__((always_inline)) ClientPathCommand classifyClientPathCommand(const char* command) {
  uint8_t result = CLIENT_PATH_VALID;
  bool is_get = strncmp(command, "get ", 4) == 0;
  if (!is_get) {
    if (strncmp(command, "set ", 4) != 0) return CLIENT_PATH_NONE;
    result |= CLIENT_PATH_SET;
  }

  const char* path = command + 4;
  if (strncmp(path, "outpath", 7) != 0) {
    if (strncmp(path, "altpath", 7) != 0) return CLIENT_PATH_NONE;
    result |= CLIENT_PATH_ALT;
  }

  if (is_get) {
    if (path[7] != 0) return CLIENT_PATH_NONE;
  } else if (path[7] != 0 && path[7] != ' ') {
    return CLIENT_PATH_NONE;
  }
  return (ClientPathCommand)result;
}

bool MyMesh::handleClientPathCommand(ClientInfo* sender, char* command, char* reply) {
  const ClientPathCommand path_command = classifyClientPathCommand(command);
  if (path_command == CLIENT_PATH_NONE) return false;

  bool is_get = (path_command & CLIENT_PATH_SET) == 0;
  bool is_alt = (path_command & CLIENT_PATH_ALT) != 0;
  if (sender == NULL) {
    strcpy(reply, "Err - command needs remote client context");
    return true;
  }

  uint8_t* stored_path = is_alt ? sender->alt_path : sender->out_path;
  uint8_t* stored_path_len = is_alt ? &sender->alt_path_len : &sender->out_path_len;
  if (is_get) {
    formatPathReply(stored_path, *stored_path_len, reply, 160);
    return true;
  }

  char* spec = command + 11;  // length of "set outpath" or "set altpath"
  if (*spec == ' ') spec++;

  uint8_t path[MAX_PATH_SIZE];
  uint8_t path_len = OUT_PATH_UNKNOWN;
  const char* err = NULL;
  if (!parsePathCommand(spec, path, path_len, err)) {
    strcpy(reply, err ? err : "Err - invalid path");
    return true;
  }

  if (path_len == OUT_PATH_UNKNOWN || path_len == OUT_PATH_FORCE_FLOOD) {
    memset(stored_path, 0, MAX_PATH_SIZE);
    *stored_path_len = path_len;
  } else {
    *stored_path_len = mesh::Packet::copyPath(stored_path, path, path_len);
  }
  dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
  formatPathReply(stored_path, *stored_path_len, reply, 160);
  return true;
}

static bool commandFamilyMatches(const char* command, const char* family) {
  size_t len = strlen(family);
  if (strncmp(command, family, len) != 0) return false;
  return command[len] == 0 || command[len] == ' ' || command[len] == '.';
}

static bool isCommonManagerReadOnlyAllowed(const char* cmd) {
  while (*cmd == ' ') cmd++; // skip leading spaces
  if (commandFamilyMatches(cmd, "ver") || commandFamilyMatches(cmd, "board")
      || commandFamilyMatches(cmd, "neighbors") || strcmp(cmd, "clock") == 0) return true;
  // sensor reads only
  if (commandFamilyMatches(cmd, "sensor get") || commandFamilyMatches(cmd, "sensor list")) return true;

  // Keep delegated reads explicit. CommonCLI's generic `get` namespace also
  // contains credentials (guest.password, WiFi/MQTT secrets, bridge.secret,
  // and similar settings), so it must never be granted wholesale.
  return commandFamilyMatches(cmd, "get role")
      || commandFamilyMatches(cmd, "get public.key")
      || commandFamilyMatches(cmd, "get clock.sync")
      || commandFamilyMatches(cmd, "get battery.alert")
      || commandFamilyMatches(cmd, "get rx.watchdog")
      || commandFamilyMatches(cmd, "get recent.repeater")
      || commandFamilyMatches(cmd, "get recent.repeaters")
      || commandFamilyMatches(cmd, "get outpath")
      || commandFamilyMatches(cmd, "get altpath");
}

// Whitelist helpers for delegated manager roles. Admins bypass both lists.
static bool isRegionMgrAllowed(const char* cmd) {
  while (*cmd == ' ') cmd++;
  return commandFamilyMatches(cmd, "region")
      || commandFamilyMatches(cmd, "get flood.channel.scope")
      || commandFamilyMatches(cmd, "set flood.channel.scope")
      || commandFamilyMatches(cmd, "del flood.channel.scope")
      || isCommonManagerReadOnlyAllowed(cmd);
}

static bool isFilterMgrAllowed(const char* cmd) {
  while (*cmd == ' ') cmd++;
  if (isCommonManagerReadOnlyAllowed(cmd)) return true;
  if (commandFamilyMatches(cmd, "get repeat")
      || commandFamilyMatches(cmd, "get loop.detect")
      || commandFamilyMatches(cmd, "get flood.max")
      || commandFamilyMatches(cmd, "get flood.channel.data")
      || commandFamilyMatches(cmd, "get flood.filter")
#if MESH_ENABLE_FLOOD_RULE_ENGINE
      || commandFamilyMatches(cmd, "get flood.rule")
#endif
      || commandFamilyMatches(cmd, "get flood.moderation")) return true;
  // General payload/hop filters plus the remaining flood-hop gates.
  return commandFamilyMatches(cmd, "set flood.filter")
      || commandFamilyMatches(cmd, "del flood.filter")
#if MESH_ENABLE_FLOOD_RULE_ENGINE
      || commandFamilyMatches(cmd, "set flood.rule")
      || commandFamilyMatches(cmd, "del flood.rule")
#endif
      || commandFamilyMatches(cmd, "set flood.moderation")
      || commandFamilyMatches(cmd, "del flood.moderation")
      || commandFamilyMatches(cmd, "set flood.channel.data")
      || commandFamilyMatches(cmd, "set flood.max")
      || commandFamilyMatches(cmd, "set loop.detect")
      || commandFamilyMatches(cmd, "set repeat");
}

void MyMesh::handleCommand(uint32_t sender_timestamp, ClientInfo* sender, char *command,
                           char *reply, int gpio_client_index,
                           uint8_t gpio_path_hash_size) {
#if defined(ESP32_PLATFORM) || defined(USER_GPIO_CONTROL)
  _gpio_reply_tracker.beginCommand(gpio_client_index, gpio_path_hash_size,
                                   sender == NULL ? NULL : sender->id.pub_key);
#endif
  char* reply_start = reply;

  // Remote admin clients may include a line ending in the command payload.
  // Normalize it here so exact-match commands such as `get outpath` behave the
  // same over LoRa and serial. Keep leading whitespace intact until after the
  // region-load handler because it encodes region hierarchy indentation.
  char* command_end = command + strlen(command);
  while (command_end > command
      && (command_end[-1] == ' ' || command_end[-1] == '\t'
          || command_end[-1] == '\r' || command_end[-1] == '\n')) {
    *--command_end = 0;
  }

  if (region_load_active && sender && !sender->isAdmin() && !sender->isRegionMgr()) {
    strcpy(reply, "Err - region load owned by admin/region manager");
    return;
  }

  if (region_load_active) {
    if (StrHelper::isBlank(command)) {  // empty/blank line, signal to terminate 'load' operation
      region_load_active = false;
      // resetFrom() preserves the selected IDs. Reject a replacement that
      // omitted either selected region instead of leaving a dangling ID.
      bool missing_default = region_map.getDefaultRegion() != NULL
          && temp_map.getDefaultRegion() == NULL;
      bool missing_home = region_map.getHomeRegion() != NULL
          && temp_map.getHomeRegion() == NULL;
      if (!missing_default && !missing_home) {
        region_map = temp_map;
        sprintf(reply, "OK - loaded %d regions", region_map.getCount());
      } else {
        strcpy(reply, "Err - invalid region map; previous map retained");
      }
    } else {
      char *np = command;
      while (*np == ' ') np++;   // skip indent
      int indent = np - command;

      char *ep = np;
      while (RegionMap::is_name_char(*ep)) ep++;
      if (*ep) { *ep++ = 0; }  // set null terminator for end of name

      while (*ep && *ep != 'F') ep++;  // look for (optional) flags

      if (indent > 0 && indent < 8 && strlen(np) > 0) {
        auto parent = load_stack[indent - 1];
        if (parent) {
          auto old = region_map.findByName(np);
          auto nw = temp_map.putRegion(np, parent->id, old ? old->id : 0);  // carry-over the current ID (if name already exists)
          if (nw) {
            nw->flags = old ? old->flags : (*ep == 'F' ? 0 : REGION_DENY_FLOOD);   // carry-over flags from curr

            load_stack[indent] = nw;  // keep pointers to parent regions, to resolve parent_id's
          }
        }
      }
      reply[0] = 0;
    }
    return;
  }

  while (*command == ' ') command++; // skip leading spaces

  if (strlen(command) > 4 && command[2] == '|') { // optional prefix (for companion radio CLI)
    memcpy(reply, command, 3);                    // reflect the prefix back
    reply += 3;
    command += 3;
  }

  mesh::cli::normalizeCommandVerb(command);
  const mesh::cli::NoArgCommandMatch discover_neighbors_match =
      mesh::cli::matchNoArgCommand(command, "discover.neighbors");

#if MESH_ENABLE_TELEMETRY_HISTORY
  const char* telemetry_args = NULL;
  mesh::TelemetryHistory::Series telemetry_series =
      mesh::TelemetryHistory::SERIES_TEMPERATURE;
  static const char telemetry_temp_command[] = "get telemetry.temp";
  static const char telemetry_volt_command[] = "get telemetry.volt";
#if MESH_ENABLE_TELEMETRY_GPS_HISTORY
  static const char telemetry_gps_command[] = "get telemetry.gps";
  static const char telemetry_gps_set_command[] = "set telemetry.gps";
#endif
  static const char telemetry_tx_get_command[] = "get telemetry.tx";
  static const char telemetry_tx_set_command[] = "set telemetry.tx";
  static const char telemetry_tx_schedule_command[] =
      "set telemetry.tx schedule";
  static const char telemetry_tx_send_now_command[] = "send telemetry.tx now";

  if (strcmp(command, telemetry_tx_send_now_command) == 0) {
    if (sender != NULL && !sender->isAdmin()) {
      strcpy(reply, "Err - not permitted");
      return;
    }
    if (telemetry_history_tx_path_len == OUT_PATH_UNKNOWN
        || telemetry_history_tx_path_len == OUT_PATH_FORCE_FLOOD
        || !mesh::Packet::isValidPathLen(telemetry_history_tx_path_len)) {
      strcpy(reply, "Err - configure telemetry.tx direct or path first");
      return;
    }
    if (telemetry_history.voltageSampleCount() == 0) {
      strcpy(reply, "Err - telemetry history is empty");
      return;
    }
    const bool temperature_queued = sendTelemetryHistorySnapshot(
        mesh::TelemetryHistory::SERIES_TEMPERATURE);
    const bool voltage_queued = sendTelemetryHistorySnapshot(
        mesh::TelemetryHistory::SERIES_VOLTAGE);
    if (temperature_queued && voltage_queued) {
      const uint16_t available = telemetry_history.voltageSampleCount();
      const unsigned sent = available < mesh::TelemetryHistory::BINARY_MAX_SAMPLES
          ? (unsigned)available
          : (unsigned)mesh::TelemetryHistory::BINARY_MAX_SAMPLES;
      snprintf(reply, 160, "OK - telemetry.tx queued temp=%u volt=%u",
               sent, sent);
    } else {
      snprintf(reply, 160, "Err - telemetry.tx queue temp=%s volt=%s",
               temperature_queued ? "yes" : "no",
               voltage_queued ? "yes" : "no");
    }
    return;
  }

  if (strcmp(command, telemetry_tx_get_command) == 0) {
    if (sender != NULL && !sender->isAdmin()) {
      strcpy(reply, "Err - not permitted");
    } else {
      formatTelemetryHistoryTxStatus(reply, 160);
    }
    return;
  }

  if (strncmp(command, telemetry_tx_schedule_command,
              sizeof(telemetry_tx_schedule_command) - 1U) == 0
      && (command[sizeof(telemetry_tx_schedule_command) - 1U] == 0
          || command[sizeof(telemetry_tx_schedule_command) - 1U] == ' ')) {
    if (sender != NULL && !sender->isAdmin()) {
      strcpy(reply, "Err - not permitted");
      return;
    }
    char* spec = trimSpaces(
        command + sizeof(telemetry_tx_schedule_command) - 1U);
    bool enable = false;
    unsigned long days = telemetry_history_tx_interval_days;
    if (strcmp(spec, "off") == 0) {
      enable = false;
    } else {
      char* end = NULL;
      days = strtoul(spec, &end, 10);
      if (end == spec) days = 0;
      if (*end == 'd') end++;
      end = trimSpaces(end);
      if (*end != 0 || days < 1U
          || days > TELEMETRY_HISTORY_TX_MAX_DAYS) {
        strcpy(reply, "Err - use: set telemetry.tx schedule <off|1-30d>");
        return;
      }
      if (telemetry_history_tx_path_len == OUT_PATH_UNKNOWN
          || telemetry_history_tx_path_len == OUT_PATH_FORCE_FLOOD
          || !mesh::Packet::isValidPathLen(telemetry_history_tx_path_len)) {
        strcpy(reply, "Err - configure telemetry.tx direct or path first");
        return;
      }
      enable = true;
    }

    const bool previous_enabled = telemetry_history_tx_enabled;
    const uint8_t previous_days = telemetry_history_tx_interval_days;
    const uint8_t previous_pending = telemetry_history_tx_pending;
    const uint64_t previous_next_tx = telemetry_history_next_tx_uptime;
    telemetry_history_tx_enabled = enable;
    if (enable) telemetry_history_tx_interval_days = (uint8_t)days;
    telemetry_history_tx_pending = 0;
    telemetry_history_next_tx_uptime = 0;
    if (!saveTelemetryHistoryTxPrefs()) {
      telemetry_history_tx_enabled = previous_enabled;
      telemetry_history_tx_interval_days = previous_days;
      telemetry_history_tx_pending = previous_pending;
      telemetry_history_next_tx_uptime = previous_next_tx;
      strcpy(reply, "Err - unable to save telemetry.tx schedule");
    } else if (enable) {
      snprintf(reply, 160, "OK - telemetry.tx schedule=%ud", (unsigned)days);
    } else {
      strcpy(reply, "OK - telemetry.tx schedule=off");
    }
    return;
  }

  if (strncmp(command, telemetry_tx_set_command,
              sizeof(telemetry_tx_set_command) - 1U) == 0
      && (command[sizeof(telemetry_tx_set_command) - 1U] == 0
          || command[sizeof(telemetry_tx_set_command) - 1U] == ' ')) {
    if (sender != NULL && !sender->isAdmin()) {
      strcpy(reply, "Err - not permitted");
      return;
    }

    char* spec = trimSpaces(
        command + sizeof(telemetry_tx_set_command) - 1U);
    if (*spec == 0) {
      strcpy(reply,
             "Err - use: set telemetry.tx <off|direct|path>");
      return;
    }

    const bool previous_enabled = telemetry_history_tx_enabled;
    const uint8_t previous_path_len = telemetry_history_tx_path_len;
    const uint8_t previous_pending = telemetry_history_tx_pending;
    const uint64_t previous_next_tx = telemetry_history_next_tx_uptime;
    uint8_t previous_path[MAX_PATH_SIZE];
    memcpy(previous_path, telemetry_history_tx_path, sizeof(previous_path));

    if (strcmp(spec, "off") == 0) {
      telemetry_history_tx_enabled = false;
      telemetry_history_tx_pending = 0;
      telemetry_history_next_tx_uptime = 0;
    } else {
      uint8_t path[MAX_PATH_SIZE];
      uint8_t path_len = OUT_PATH_UNKNOWN;
      const char* err = NULL;
      if (!parsePathCommand(spec, path, path_len, err)
          || path_len == OUT_PATH_UNKNOWN
          || path_len == OUT_PATH_FORCE_FLOOD) {
        strcpy(reply, err != NULL ? err
                                 : "Err - telemetry.tx needs a direct path");
        return;
      }
      telemetry_history_tx_enabled = true;
      telemetry_history_tx_path_len = path_len;
      telemetry_history_tx_pending = 0;
      telemetry_history_next_tx_uptime = 0;
      memset(telemetry_history_tx_path, 0,
             sizeof(telemetry_history_tx_path));
      if ((path_len & 63U) != 0) {
        mesh::Packet::copyPath(telemetry_history_tx_path, path, path_len);
      }
    }

    if (!saveTelemetryHistoryTxPrefs()) {
      telemetry_history_tx_enabled = previous_enabled;
      telemetry_history_tx_path_len = previous_path_len;
      telemetry_history_tx_pending = previous_pending;
      telemetry_history_next_tx_uptime = previous_next_tx;
      memcpy(telemetry_history_tx_path, previous_path,
             sizeof(telemetry_history_tx_path));
      strcpy(reply, "Err - unable to save telemetry.tx");
    } else if (telemetry_history_tx_enabled) {
      snprintf(reply, 160, "OK - telemetry.tx schedule=%ud temp=165 volt=165",
               (unsigned)telemetry_history_tx_interval_days);
    } else {
      strcpy(reply, "OK - telemetry.tx off");
    }
    return;
  }

#if MESH_ENABLE_TELEMETRY_GPS_HISTORY
  if (strncmp(command, telemetry_gps_set_command,
              sizeof(telemetry_gps_set_command) - 1U) == 0
      && (command[sizeof(telemetry_gps_set_command) - 1U] == 0
          || command[sizeof(telemetry_gps_set_command) - 1U] == ' ')) {
    if (sender != NULL && !sender->isAdmin()) {
      strcpy(reply, "Err - not permitted");
      return;
    }
    uint8_t requested_days = 0;
    if (!parseTelemetryGpsDays(
            command + sizeof(telemetry_gps_set_command) - 1U,
            requested_days)) {
      strcpy(reply, "Err - use: set telemetry.gps <1-30>");
      return;
    }
    const uint8_t actual_days = resizeTelemetryGpsDays(requested_days);
    snprintf(reply, 160,
             "OK - telemetry.gps days=%u pages=%u requested=%u",
             (unsigned)actual_days,
             (unsigned)telemetry_history.gpsPageCount(),
             (unsigned)requested_days);
    return;
  }
#endif

  if (strncmp(command, telemetry_temp_command,
              sizeof(telemetry_temp_command) - 1U) == 0
      && (command[sizeof(telemetry_temp_command) - 1U] == 0
          || command[sizeof(telemetry_temp_command) - 1U] == ' ')) {
    telemetry_args = command + sizeof(telemetry_temp_command) - 1U;
  } else if (strncmp(command, telemetry_volt_command,
                     sizeof(telemetry_volt_command) - 1U) == 0
      && (command[sizeof(telemetry_volt_command) - 1U] == 0
          || command[sizeof(telemetry_volt_command) - 1U] == ' ')) {
    telemetry_series = mesh::TelemetryHistory::SERIES_VOLTAGE;
    telemetry_args = command + sizeof(telemetry_volt_command) - 1U;
#if MESH_ENABLE_TELEMETRY_GPS_HISTORY
  } else if (strncmp(command, telemetry_gps_command,
                     sizeof(telemetry_gps_command) - 1U) == 0
      && (command[sizeof(telemetry_gps_command) - 1U] == 0
          || command[sizeof(telemetry_gps_command) - 1U] == ' ')) {
    telemetry_series = mesh::TelemetryHistory::SERIES_GPS;
    telemetry_args = command + sizeof(telemetry_gps_command) - 1U;
#endif
  }

  if (telemetry_args != NULL) {
    if (sender != NULL && !sender->isAdmin()) {
      strcpy(reply, "Err - not permitted");
    } else {
      telemetry_history.formatPageReply(telemetry_series, telemetry_args,
                                        reply, 160);
    }
    return;
  }
#endif

#if defined(PORTABLE_MQTT_OBSERVER)
  // Legacy PORTABLE_MQTT_OBSERVER builds kept neighbor refresh ahead of their
  // reduced role handoff. Current build.sh profiles never define this macro.
  if (discover_neighbors_match != mesh::cli::NoArgCommandMatch::NoMatch) {
    if (discover_neighbors_match ==
        mesh::cli::NoArgCommandMatch::HasArguments) {
      strcpy(reply, "Err - discover.neighbors has no options");
    } else {
      sendNodeDiscoverReq();
      strcpy(reply, "OK - Discover sent");
    }
    return;
  }

  // Legacy PORTABLE_MQTT_OBSERVER builds kept reply-path overrides ahead of
  // their reduced role handoff. Current build.sh profiles use FULL instead.
  if (classifyClientPathCommand(command) != CLIENT_PATH_NONE) {
    if (sender && !sender->isAdmin()) {
      bool allowed = (sender->isRegionMgr() || sender->isFilterMgr())
          && isCommonManagerReadOnlyAllowed(command);
      if (!allowed) {
        strcpy(reply, "Err - not permitted");
        return;
      }
    }
    handleClientPathCommand(sender, command, reply);
    return;
  }

  // Compatibility path for manually defined legacy portable builds. Current
  // release builds use FULL and do not enter this branch.
  _cli.handleCommand(sender_timestamp, command, reply);
  return;
#endif

  if (sender && !sender->isAdmin()) {
    bool allowed = (sender->isRegionMgr() && isRegionMgrAllowed(command))
        || (sender->isFilterMgr() && isFilterMgrAllowed(command));
    if (!allowed) {
      strcpy(reply, "Err - not permitted");
      return;
    }
  }

  // handle ACL related commands
#if MESH_ENABLE_FLOOD_RULE_ENGINE
  if (strcmp(command, "get flood.channel.data.hops") == 0) {
    formatFloodChannelDataHops(reply);
  } else if (strcmp(command, "get flood.channel.data") == 0) {
    formatFloodChannelData(reply);
  } else if (commandFamilyMatches(command, "set flood.channel.data.hops")) {
    setFloodChannelDataHops(
        command + strlen("set flood.channel.data.hops"), reply);
  } else if (commandFamilyMatches(command, "set flood.channel.data")) {
    setFloodChannelData(command + strlen("set flood.channel.data"), reply);
  } else
#endif
  if (commandFamilyMatches(command, "get flood.channel.scope.require")) {
    formatFloodChannelScopeRequirements(
        command + strlen("get flood.channel.scope.require"), reply);
  } else if (commandFamilyMatches(command, "set flood.channel.scope.require")) {
    setFloodChannelScopeRequirement(
        command + strlen("set flood.channel.scope.require"), reply);
  } else if (commandFamilyMatches(command, "del flood.channel.scope.require")) {
    deleteFloodChannelScopeRequirement(
        command + strlen("del flood.channel.scope.require"), reply);
  } else if (commandFamilyMatches(command, "get flood.channel.scope")) {
    formatFloodChannelScopes(command + strlen("get flood.channel.scope"), reply);
  } else if (commandFamilyMatches(command, "set flood.channel.scope")) {
    setFloodChannelScope(command + strlen("set flood.channel.scope"), reply);
  } else if (commandFamilyMatches(command, "del flood.channel.scope")) {
    deleteFloodChannelScope(command + strlen("del flood.channel.scope"), reply);
  } else if (commandFamilyMatches(command, "get flood.filter.blacklist")) {
    formatFloodPacketFilterBlacklist(
        command + strlen("get flood.filter.blacklist"), reply);
  } else if (commandFamilyMatches(command, "set flood.filter.blacklist")) {
    setFloodPacketFilterBlacklist(
        command + strlen("set flood.filter.blacklist"), reply);
  } else if (commandFamilyMatches(command, "del flood.filter.blacklist")) {
    deleteFloodPacketFilterBlacklist(
        command + strlen("del flood.filter.blacklist"), reply);
  } else if (commandFamilyMatches(command, "get flood.filter")) {
    formatFloodPacketFilters(command + strlen("get flood.filter"), reply);
  } else if (commandFamilyMatches(command, "set flood.filter")) {
    setFloodPacketFilter(command + strlen("set flood.filter"), reply, false);
  } else if (commandFamilyMatches(command, "del flood.filter")) {
    deleteFloodPacketFilter(command + strlen("del flood.filter"), reply);
#if MESH_ENABLE_FLOOD_RULE_ENGINE
  } else if (commandFamilyMatches(command, "get flood.rule")) {
    formatFloodPacketFilters(command + strlen("get flood.rule"), reply);
  } else if (commandFamilyMatches(command, "set flood.rule")) {
    setFloodPacketFilter(command + strlen("set flood.rule"), reply, true);
  } else if (commandFamilyMatches(command, "del flood.rule")) {
    deleteFloodPacketFilter(command + strlen("del flood.rule"), reply);
#endif
  }
#if MESH_ENABLE_FLOOD_GROUP_MODERATION
  else if (commandFamilyMatches(command, "get flood.moderation")) {
    formatFloodGroupModeration(command + strlen("get flood.moderation"), reply);
  } else if (commandFamilyMatches(command, "set flood.moderation")) {
    setFloodGroupModeration(command + strlen("set flood.moderation"), reply);
  } else if (commandFamilyMatches(command, "del flood.moderation")) {
    deleteFloodGroupModeration(command + strlen("del flood.moderation"), reply);
  }
#endif
#if MESH_ENABLE_CLOCK_SYNC
  else if (commandFamilyMatches(command, "get clock.sync.status")) {
    formatClockSyncStatus(command + strlen("get clock.sync.status"), reply, 160);
  } else if (strcmp(command, "get clock.sync") == 0) {
    formatClockSyncStatus("", reply, 160);
  } else if (strcmp(command, "get clock.sync.mesh") == 0) {
    if (clock_sync_mesh_enabled
        && clock_sync_mesh_suppressed_by != CLOCK_SYNC_MESH_SUPPRESS_NONE) {
      sprintf(reply, "> on (suppressed by %s until reboot)",
              clockSyncMeshSuppressionName(clock_sync_mesh_suppressed_by));
    } else {
      sprintf(reply, "> %s", clock_sync_mesh_enabled ? "on" : "off");
    }
  } else if (strcmp(command, "get clock.sync.mesh.edge") == 0) {
    sprintf(reply, "> %s", clock_sync_mesh_edge_enabled ? "on" : "off");
  } else if (strcmp(command, "get clock.sync.internet") == 0) {
#ifdef WITH_MQTT_BRIDGE
    sprintf(reply, "> %s", clock_sync_internet_enabled ? "on" : "off");
#else
    sprintf(reply, "> %s (unavailable on this build)", clock_sync_internet_enabled ? "on" : "off");
#endif
  } else if (strcmp(command, "get clock.sync.drift") == 0) {
    sprintf(reply, "> %lu", (unsigned long)clock_sync_drift_seconds);
  } else if (strcmp(command, "get clock.sync.samples") == 0) {
    sprintf(reply, "> %u", (unsigned int)clock_sync_required_samples);
  } else if (strcmp(command, "clock.sync.mesh now") == 0) {
    if (!clock_sync_mesh_enabled) {
      strcpy(reply, "Err - mesh clock sync is off");
    } else if (clock_sync_mesh_suppressed_by != CLOCK_SYNC_MESH_SUPPRESS_NONE) {
      sprintf(reply, "Err - mesh sync suppressed by %s until reboot",
              clockSyncMeshSuppressionName(clock_sync_mesh_suppressed_by));
    } else {
      resetClockSyncAttempt();
      clock_sync_force_mesh_pending = true;
      clock_sync_next_attempt_uptime = uptime_millis;
      strcpy(reply, "OK - mesh clock sync queued");
    }
  } else if (strncmp(command, "set clock.sync.mesh ", 20) == 0) {
    const char* value = command + 20;
    bool enabled;
    if (strcmp(value, "on") == 0) enabled = true;
    else if (strcmp(value, "off") == 0) enabled = false;
    else {
      strcpy(reply, "Err - usage: set clock.sync.mesh <on|off>");
      return;
    }
    bool previous = clock_sync_mesh_enabled;
    clock_sync_mesh_enabled = enabled;
    if (!saveClockSyncPrefs()) {
      clock_sync_mesh_enabled = previous;
      strcpy(reply, "Err - unable to save clock sync settings");
    } else {
      if (enabled && !previous) memset(clock_sync_samples, 0, sizeof(clock_sync_samples));
      resetClockSyncAttempt();
      if (enabled && clock_sync_mesh_suppressed_by != CLOCK_SYNC_MESH_SUPPRESS_NONE) {
        sprintf(reply, "OK - enabled; suppressed by %s until reboot",
                clockSyncMeshSuppressionName(clock_sync_mesh_suppressed_by));
      } else {
        strcpy(reply, enabled ? "OK - mesh clock sync enabled" : "OK - mesh clock sync disabled");
      }
    }
  } else if (strncmp(command, "set clock.sync.mesh.edge ", 25) == 0) {
    const char* value = command + 25;
    bool enabled;
    if (strcmp(value, "on") == 0) enabled = true;
    else if (strcmp(value, "off") == 0) enabled = false;
    else {
      strcpy(reply, "Err - usage: set clock.sync.mesh.edge <on|off>");
      return;
    }
    bool previous = clock_sync_mesh_edge_enabled;
    clock_sync_mesh_edge_enabled = enabled;
    if (!saveClockSyncPrefs()) {
      clock_sync_mesh_edge_enabled = previous;
      strcpy(reply, "Err - unable to save clock sync settings");
    } else {
      if (enabled != previous) memset(clock_sync_samples, 0, sizeof(clock_sync_samples));
      resetClockSyncAttempt();
      strcpy(reply, enabled ? "OK - edge clock sync enabled" : "OK - edge clock sync disabled");
    }
  } else if (strncmp(command, "set clock.sync.internet ", 24) == 0) {
    const char* value = command + 24;
    bool enabled;
    if (strcmp(value, "on") == 0) enabled = true;
    else if (strcmp(value, "off") == 0) enabled = false;
    else {
      strcpy(reply, "Err - usage: set clock.sync.internet <on|off>");
      return;
    }
    bool previous = clock_sync_internet_enabled;
    clock_sync_internet_enabled = enabled;
    if (!saveClockSyncPrefs()) {
      clock_sync_internet_enabled = previous;
      strcpy(reply, "Err - unable to save clock sync settings");
    } else {
      resetClockSyncAttempt();
#ifdef WITH_MQTT_BRIDGE
      strcpy(reply, enabled ? "OK - internet clock sync enabled" : "OK - internet clock sync disabled");
#else
      strcpy(reply, enabled ? "OK - enabled (internet unavailable on this build)" : "OK - internet clock sync disabled");
#endif
    }
  } else if (strncmp(command, "set clock.sync.drift ", 21) == 0) {
    uint32_t drift = 0;
    if (!parseFloodModerationUnsigned(command + 21, CLOCK_SYNC_DRIFT_MAX_SECONDS, drift)
        || drift < CLOCK_SYNC_DRIFT_MIN_SECONDS) {
      strcpy(reply, "Err - drift must be 30-86400 seconds");
    } else {
      uint32_t previous = clock_sync_drift_seconds;
      clock_sync_drift_seconds = drift;
      if (!saveClockSyncPrefs()) {
        clock_sync_drift_seconds = previous;
        strcpy(reply, "Err - unable to save clock sync settings");
      } else {
        resetClockSyncAttempt();
        sprintf(reply, "OK - clock drift threshold %lu seconds", (unsigned long)drift);
      }
    }
  } else if (strncmp(command, "set clock.sync.samples ", 23) == 0) {
    uint32_t required = 0;
    if (!parseFloodModerationUnsigned(command + 23, CLOCK_SYNC_REQUIRED_SAMPLES_MAX, required)
        || required < CLOCK_SYNC_REQUIRED_SAMPLES_MIN) {
      strcpy(reply, "Err - samples must be 3-16");
    } else {
      uint8_t previous = clock_sync_required_samples;
      clock_sync_required_samples = (uint8_t)required;
      if (!saveClockSyncPrefs()) {
        clock_sync_required_samples = previous;
        strcpy(reply, "Err - unable to save clock sync settings");
      } else {
        resetClockSyncAttempt();
        sprintf(reply, "OK - clock sync requires %u samples", (unsigned int)required);
      }
    }
  }
#endif
  else if (memcmp(command, "setperm ", 8) == 0) {   // format:  setperm {pubkey-hex} {permissions-int8}
    char* hex = &command[8];
    char* sp = strchr(hex, ' ');   // look for separator char
    if (sp == NULL) {
      strcpy(reply, "Err - bad params");
    } else {
      size_t hex_len = (size_t)(sp - hex);
      *sp++ = 0;   // replace space with null terminator

      uint8_t pubkey[PUB_KEY_SIZE];
      if (hex_len > 0 && hex_len <= PUB_KEY_SIZE * 2 && (hex_len & 1) == 0
          && mesh::Utils::fromHex(pubkey, (int)(hex_len / 2), hex)) {
        uint8_t perms = atoi(sp);
        if (acl.applyPermissions(self_id, pubkey, (int)(hex_len / 2), perms)) {
          dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);   // trigger acl.save()
          strcpy(reply, "OK");
        } else {
          strcpy(reply, "Err - invalid params");
        }
      } else {
        strcpy(reply, "Err - bad pubkey");
      }
    }
  } else if (sender_timestamp == 0 && sender == NULL
      && (strcmp(command, "get recent.repeater") == 0 || strcmp(command, "get recent.repeaters") == 0)) {
    printRecentRepeatersSerial();
    reply_start[0] = 0;
  } else if (sender_timestamp == 0 && strcmp(command, "get acl") == 0) {
    Serial.println("ACL:");
    for (int i = 0; i < acl.getNumClients(); i++) {
      auto c = acl.getClientByIdx(i);
      if (c->permissions == 0) continue;  // skip deleted (or guest) entries

      Serial.printf("%02X ", c->permissions);
      mesh::Utils::printHex(Serial, c->id.pub_key, PUB_KEY_SIZE);
      Serial.printf("\n");
    }
    reply[0] = 0;
  } else if (handleClientPathCommand(sender, command, reply)) {
    return;
  } else if (strncmp(command, "send text.flood ", 16) == 0) {
    char* text = trimSpaces(command + 16);
    if (*text == 0) {
      strcpy(reply, "Err - usage: send text.flood <message>");
    } else if (sendRepeatersFloodText(text)) {
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Err - unable to create packet");
    }
  } else if (strcmp(command, "get battery.alert") == 0) {
    sprintf(reply, "> %s", _prefs.battery_alert_enabled ? "on" : "off");
  } else if (strcmp(command, "get battery.alert.region") == 0) {
    sprintf(reply, "> %s", _prefs.battery_alert_region[0]
            ? _prefs.battery_alert_region : "<unset>");
  } else if (strcmp(command, "get battery.alert.low") == 0) {
    sprintf(reply, "> %u", (uint32_t)_prefs.battery_alert_low_percent);
  } else if (strcmp(command, "get battery.alert.critical") == 0) {
    sprintf(reply, "> %u", (uint32_t)_prefs.battery_alert_critical_percent);
  } else if (strcmp(command, "get rx.watchdog") == 0) {
    sprintf(reply, "> %s", _prefs.rx_watchdog_enabled ? "on" : "off");
  } else if (strncmp(command, "set rx.watchdog ", 16) == 0) {
    const char* value = command + 16;
    if (strcmp(value, "on") == 0) {
      _prefs.rx_watchdog_enabled = 1;
      next_rx_watchdog_check = 0;
      savePrefs();
      strcpy(reply, "OK - RX watchdog enabled; first check in 12 hours");
    } else if (strcmp(value, "off") == 0) {
      _prefs.rx_watchdog_enabled = 0;
      next_rx_watchdog_check = 0;
      savePrefs();
      strcpy(reply, "OK - RX watchdog disabled");
    } else {
      strcpy(reply, "Err - usage: set rx.watchdog <on|off>");
    }
  } else if (strncmp(command, "set battery.alert ", 18) == 0) {
    const char* value = command + 18;
    if (strncmp(value, "on", 2) == 0 && (value[2] == 0 || value[2] == ' ')) {
      const char* region_name = skipLocalSpaces(value + 2);
      const RegionEntry* region = NULL;
      bool ambiguous = false;

      if (*region_name) {
        if (strchr(region_name, ' ') != NULL) {
          strcpy(reply, "Err - region names cannot contain spaces");
          return;
        }
        region = region_map.findByName(region_name);
        if (region == NULL || region->isWildcard()) {
          strcpy(reply, "Err - unknown or invalid alert region");
          return;
        }
      } else {
        region = findNarrowestBatteryAlertRegion(ambiguous);
        if (region == NULL) {
          strcpy(reply, "Err - define a usable region before enabling battery alerts");
          return;
        }
        if (ambiguous) {
          strcpy(reply, "Err - multiple narrowest regions; specify one");
          return;
        }
      }

      TransportKey region_scope;
      if (!getBatteryAlertScopeForRegion(*region, region_scope)) {
        strcpy(reply, "Err - alert region has no usable transport key");
        return;
      }

      StrHelper::strncpy(_prefs.battery_alert_region, region->name,
                         sizeof(_prefs.battery_alert_region));
      _prefs.battery_alert_enabled = 1;
      next_battery_alert_check = 0;
      savePrefs();
      sprintf(reply, "OK - battery alerts scoped to %s", _prefs.battery_alert_region);
    } else if (strcmp(value, "off") == 0) {
      _prefs.battery_alert_enabled = 0;
      next_battery_alert_check = 0;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Err - usage: set battery.alert <on [region]|off>");
    }
  } else if (strncmp(command, "set battery.alert.low ", 22) == 0) {
    uint8_t percent;
    if (!parseBatteryAlertPercent(command + 22, 1, 100, percent)) {
      strcpy(reply, "Err - usage: set battery.alert.low <1-100>");
    } else if (percent <= _prefs.battery_alert_critical_percent) {
      strcpy(reply, "Err - low must be greater than critical");
    } else {
      _prefs.battery_alert_low_percent = percent;
      next_battery_alert_check = 0;
      savePrefs();
      strcpy(reply, "OK");
    }
  } else if (strncmp(command, "set battery.alert.critical ", 27) == 0) {
    uint8_t percent;
    if (!parseBatteryAlertPercent(command + 27, 0, 99, percent)) {
      strcpy(reply, "Err - usage: set battery.alert.critical <0-99>");
    } else if (percent >= _prefs.battery_alert_low_percent) {
      strcpy(reply, "Err - critical must be less than low");
    } else {
      _prefs.battery_alert_critical_percent = percent;
      next_battery_alert_check = 0;
      savePrefs();
      strcpy(reply, "OK");
    }
  } else if (discover_neighbors_match !=
             mesh::cli::NoArgCommandMatch::NoMatch) {
    if (discover_neighbors_match ==
        mesh::cli::NoArgCommandMatch::HasArguments) {
      strcpy(reply, "Err - discover.neighbors has no options");
    } else {
      sendNodeDiscoverReq();
      strcpy(reply, "OK - Discover sent");
    }
#if defined(WITH_MQTT_NEIGHBORS)
  } else if (memcmp(command, "discover.scopes", 15) == 0) {
    const char* sub = command + 15;
    while (*sub == ' ') sub++;
    if (*sub != 0) {
      strcpy(reply, "Err - discover.scopes has no options");
    } else if (pending_discover_tag != 0 &&
               !millisHasNowPassed(pending_discover_until) &&
               !neighbor_discover_active) {
      // A zero-hop table refresh is already collecting; queue the scope pass
      // behind it (as a manual, non-periodic request) rather than starting a
      // second refresh.
      if (!neighborDiscoverReady(reply)) {
        // reply already set by neighborDiscoverReady
      } else {
        neighbor_table_refresh_active = true;
        neighbor_table_refresh_periodic = false;
        long remaining_ms = (long)(pending_discover_until - futureMillis(0));
        unsigned remaining_secs = remaining_ms > 0
          ? (unsigned)(((unsigned long)remaining_ms + 999UL) / 1000UL) : 0;
        sprintf(reply, "OK - scopes queued (%us discovery remaining)", remaining_secs);
        MESH_DEBUG_PRINTLN("Neighbor scopes queued behind active discovery (%us remaining)", remaining_secs);
      }
    } else if (!startNeighborDiscover(reply)) {
      // reply already set by startNeighborDiscover
    }
#elif defined(WITH_MQTT_BRIDGE)
  } else if (memcmp(command, "discover.scopes", 15) == 0) {
    strcpy(reply, "Err - neighbors not enabled in this build");
#endif
  } else{
    _cli.handleCommand(sender_timestamp, command, reply);  // common CLI commands
  }
}

void MyMesh::loop() {
  // Check radio FIRST to ensure we don't miss incoming packets
  // MQTT processing runs in a separate FreeRTOS task on Core 0, so we don't call bridge.loop() here
  mesh::Mesh::loop();
  _cli.loop();
  processDeferredCliCommand();
  servicePostMeshLoop();
}

#if MESH_ENABLE_TELEMETRY_HISTORY
#if MESH_ENABLE_TELEMETRY_GPS_HISTORY
uint8_t MyMesh::resizeTelemetryGpsDays(uint8_t requested_days) {
  size_t free_bytes = telemetryFreeHeapBytes();
  const size_t allocation_budget = free_bytes > TELEMETRY_GPS_HEAP_RESERVE_BYTES
      ? free_bytes - TELEMETRY_GPS_HEAP_RESERVE_BYTES : 0;
  uint8_t actual_days = telemetry_history.resizeGpsDays(
      requested_days, allocation_budget);

  free_bytes = telemetryFreeHeapBytes();
  while (actual_days > mesh::TelemetryHistory::GPS_DEFAULT_RETENTION_DAYS
         && free_bytes < TELEMETRY_GPS_HEAP_RESERVE_BYTES) {
    const size_t deficit = TELEMETRY_GPS_HEAP_RESERVE_BYTES - free_bytes;
    size_t days_to_release =
        (deficit + mesh::TelemetryHistory::GPS_HEAP_BYTES_PER_DAY - 1U)
        / mesh::TelemetryHistory::GPS_HEAP_BYTES_PER_DAY;
    if (days_to_release == 0) days_to_release = 1;
    const uint8_t target_days = days_to_release
            < actual_days - mesh::TelemetryHistory::GPS_DEFAULT_RETENTION_DAYS
        ? (uint8_t)(actual_days - days_to_release)
        : mesh::TelemetryHistory::GPS_DEFAULT_RETENTION_DAYS;
    const uint8_t reduced_days = telemetry_history.resizeGpsDays(target_days, 0);
    if (reduced_days >= actual_days) {
      actual_days = telemetry_history.resizeGpsDays(
          mesh::TelemetryHistory::GPS_DEFAULT_RETENTION_DAYS, 0);
    } else {
      actual_days = reduced_days;
    }
    free_bytes = telemetryFreeHeapBytes();
  }
  return actual_days;
}
#endif

void MyMesh::loadTelemetryHistoryTxPrefs() {
  telemetry_history_tx_enabled = false;
  memset(telemetry_history_tx_path, 0, sizeof(telemetry_history_tx_path));
  telemetry_history_tx_path_len = OUT_PATH_UNKNOWN;
  telemetry_history_tx_interval_days = TELEMETRY_HISTORY_TX_DEFAULT_DAYS;
  telemetry_history_tx_pending = 0;
  telemetry_history_next_tx_uptime = 0;

  if (_fs == NULL || !_fs->exists(TELEMETRY_HISTORY_TX_PREFS_FILE)) return;
  File file = openFloodSettingsRead(_fs, TELEMETRY_HISTORY_TX_PREFS_FILE);
  if (!file) return;

  uint8_t magic[4];
  uint8_t enabled = 0;
  uint8_t path_len = OUT_PATH_UNKNOWN;
  uint8_t interval_days = TELEMETRY_HISTORY_TX_DEFAULT_DAYS;
  uint8_t path[MAX_PATH_SIZE];
  bool valid = file.read(magic, sizeof(magic)) == sizeof(magic)
      && memcmp(magic, "THT2", sizeof(magic)) == 0
      && file.read(&enabled, sizeof(enabled)) == sizeof(enabled)
      && file.read(&path_len, sizeof(path_len)) == sizeof(path_len)
      && file.read(&interval_days, sizeof(interval_days))
             == sizeof(interval_days)
      && file.read(path, sizeof(path)) == sizeof(path);
  file.close();

  valid = valid && enabled <= 1 && interval_days >= 1
      && interval_days <= TELEMETRY_HISTORY_TX_MAX_DAYS;
  if (enabled != 0) {
    valid = valid && path_len != OUT_PATH_UNKNOWN
        && path_len != OUT_PATH_FORCE_FLOOD
        && mesh::Packet::isValidPathLen(path_len);
  }
  if (!valid) return;

  telemetry_history_tx_enabled = enabled != 0;
  telemetry_history_tx_path_len = path_len;
  telemetry_history_tx_interval_days = interval_days;
  memcpy(telemetry_history_tx_path, path, sizeof(telemetry_history_tx_path));
}

bool MyMesh::saveTelemetryHistoryTxPrefs() {
  if (_fs == NULL) return false;
  File file = openFloodSettingsWrite(_fs, TELEMETRY_HISTORY_TX_PREFS_FILE);
  if (!file) return false;

  const uint8_t magic[4] = {'T', 'H', 'T', '2'};
  const uint8_t enabled = telemetry_history_tx_enabled ? 1 : 0;
  bool success = file.write(magic, sizeof(magic)) == sizeof(magic)
      && file.write(&enabled, sizeof(enabled)) == sizeof(enabled)
      && file.write(&telemetry_history_tx_path_len,
                    sizeof(telemetry_history_tx_path_len))
             == sizeof(telemetry_history_tx_path_len)
      && file.write(&telemetry_history_tx_interval_days,
                    sizeof(telemetry_history_tx_interval_days))
             == sizeof(telemetry_history_tx_interval_days)
      && file.write(telemetry_history_tx_path,
                    sizeof(telemetry_history_tx_path))
             == sizeof(telemetry_history_tx_path);
  file.close();
  return success;
}

void MyMesh::formatTelemetryHistoryTxStatus(char* reply,
                                            size_t reply_size) const {
  char source_id[mesh::TelemetryHistory::BINARY_SOURCE_ID_SIZE * 2U + 1U];
  mesh::Utils::toHex(source_id, self_id.pub_key,
                     mesh::TelemetryHistory::BINARY_SOURCE_ID_SIZE);
  char path_reply[132];
  formatPathReply(telemetry_history_tx_path,
                  telemetry_history_tx_path_len,
                  path_reply, sizeof(path_reply));
  snprintf(reply, reply_size, "> %s%ud id=%s p=%s",
           telemetry_history_tx_enabled ? "on " : "off ",
           (unsigned)telemetry_history_tx_interval_days,
           source_id, path_reply[0] == '>' && path_reply[1] == ' '
               ? path_reply + 2 : path_reply);
}

bool MyMesh::sendTelemetryHistorySnapshot(
    mesh::TelemetryHistory::Series series) {
  uint8_t payload[mesh::TelemetryHistory::BINARY_PAYLOAD_SIZE];
  const size_t payload_len = series == mesh::TelemetryHistory::SERIES_VOLTAGE
      ? telemetry_history.formatVoltageBinarySnapshot(
            self_id.pub_key, payload, sizeof(payload))
      : telemetry_history.formatTemperatureBinarySnapshot(
            self_id.pub_key, payload, sizeof(payload));
  if (payload_len <= mesh::TelemetryHistory::BINARY_HEADER_SIZE) return false;

  mesh::Packet* packet = createRawData(payload, payload_len);
  if (packet == NULL) return false;
  return sendDirect(packet, telemetry_history_tx_path,
                    telemetry_history_tx_path_len);
}

void MyMesh::serviceTelemetryHistoryTx() {
  if (!telemetry_history_tx_enabled
      || telemetry_history.voltageSampleCount()
             < mesh::TelemetryHistory::BINARY_MAX_SAMPLES) {
    return;
  }

  const uint64_t current_uptime_millis =
      uptime_millis + (uint32_t)(millis() - last_millis);
  if (telemetry_history_next_tx_uptime != 0
      && current_uptime_millis < telemetry_history_next_tx_uptime) {
    return;
  }

  if (telemetry_history_tx_pending == 0) {
    telemetry_history_tx_pending = TELEMETRY_HISTORY_TX_TEMPERATURE
        | TELEMETRY_HISTORY_TX_VOLTAGE;
  }
  if ((telemetry_history_tx_pending & TELEMETRY_HISTORY_TX_TEMPERATURE) != 0
      && sendTelemetryHistorySnapshot(
          mesh::TelemetryHistory::SERIES_TEMPERATURE)) {
    telemetry_history_tx_pending &=
        (uint8_t)~TELEMETRY_HISTORY_TX_TEMPERATURE;
  }
  if ((telemetry_history_tx_pending & TELEMETRY_HISTORY_TX_VOLTAGE) != 0
      && sendTelemetryHistorySnapshot(mesh::TelemetryHistory::SERIES_VOLTAGE)) {
    telemetry_history_tx_pending &= (uint8_t)~TELEMETRY_HISTORY_TX_VOLTAGE;
  }

  if (telemetry_history_tx_pending == 0) {
    telemetry_history_next_tx_uptime =
        current_uptime_millis
        + (uint64_t)telemetry_history_tx_interval_days
            * 24ULL * 60ULL * 60ULL * 1000ULL;
  } else {
    telemetry_history_next_tx_uptime =
        current_uptime_millis + TELEMETRY_HISTORY_TX_RETRY_MILLIS;
  }
}

void MyMesh::sampleTelemetryHistory() {
  const uint32_t now = rtc_clock.getCurrentTime();
  if (!telemetry_history.sampleDue(now)) return;

  int32_t latitude_e7 = 0;
  int32_t longitude_e7 = 0;
  bool gps_valid = false;
#if ENV_INCLUDE_GPS == 1
  LocationProvider* location = sensors.getLocationProvider();
  if (location != NULL && location->isEnabled() && location->isValid()) {
    const long latitude_e6 = location->getLatitude();
    const long longitude_e6 = location->getLongitude();
    if (latitude_e6 >= -90000000L && latitude_e6 <= 90000000L
        && longitude_e6 >= -180000000L && longitude_e6 <= 180000000L) {
      latitude_e7 = (int32_t)((int64_t)latitude_e6 * 10);
      longitude_e7 = (int32_t)((int64_t)longitude_e6 * 10);
      gps_valid = latitude_e7 != 0 || longitude_e7 != 0;
    }
  }
#endif

  const float measured_temperature = _cli.getBoard()->getMCUTemperature();
  const bool temperature_valid = isfinite(measured_temperature);
  int16_t temperature_c = 0;
  if (temperature_valid) {
    if (measured_temperature < -32768.0f) temperature_c = INT16_MIN;
    else if (measured_temperature > 32767.0f) temperature_c = INT16_MAX;
    else temperature_c = (int16_t)lroundf(measured_temperature);
  }

  telemetry_history.record(now, temperature_c, temperature_valid,
                           _cli.getBoard()->getBattMilliVolts(),
                           latitude_e7, longitude_e7, gps_valid);
}
#endif

void __attribute__((noinline)) MyMesh::servicePostMeshLoop() {
  if (pending_self_advert) {
    const uint32_t delay_millis = pending_self_advert_delay;
    const bool flood = pending_self_advert_flood;
    pending_self_advert = false;
    sendSelfAdvertisementNow(delay_millis, flood);
  }
  checkRxInactivityWatchdog();
#if MESH_ENABLE_TELEMETRY_HISTORY
  sampleTelemetryHistory();
  serviceTelemetryHistoryTx();
#endif
#if !defined(PORTABLE_MQTT_OBSERVER)
  checkBatteryAlert();
  expireRecentRepeatersIfDue();
#endif

#if defined(WITH_BRIDGE) && !defined(WITH_MQTT_BRIDGE)
  // MQTT runs its own task; serial and ESP-NOW bridges remain cooperative.
  AbstractBridge* active_bridge = activeBridge();
  if (active_bridge && active_bridge->isRunning()) active_bridge->loop();
#endif

  if (next_flood_advert && millisHasNowPassed(next_flood_advert)) {
    mesh::Packet *pkt = createSelfAdvert();
    uint32_t delay_millis = 0;
    if (pkt) sendFloodScoped(default_scope, pkt, delay_millis, _prefs.path_hash_mode + 1);

    updateFloodAdvertTimer(); // schedule next flood advert
    updateAdvertTimer();      // also schedule local advert (so they don't overlap)
  } else if (next_local_advert && millisHasNowPassed(next_local_advert)) {
    mesh::Packet *pkt = createSelfAdvert();
    if (pkt) sendZeroHop(pkt);

    updateAdvertTimer(); // schedule next local advert
  }

#if !defined(PORTABLE_MQTT_OBSERVER)
  processScheduledRadioSettings();
#endif

#if defined(WITH_MQTT_BRIDGE) && defined(OTA_MANIFEST_BASE)
  if (_ota_update_at && millisHasNowPassed(_ota_update_at)) { // deferred `ota update`
    _ota_update_at = 0;                                       // clear timer
    // The "Beginning update..." reply has now gone out. Free the bridge for heap
    // headroom, then flash: otaFromManifest reboots into the new image on success
    // (so this never returns); on any abort (already up to date, partition change,
    // download error) it returns and we resume the bridge.
    Serial.println("OTA: starting update");
    // Flush the START alert (and CLI reply) out the radio BEFORE teardown blocks
    // the loop until reboot - otherwise a packet still queued here (busy /
    // duty-limited channel) is lost when the flash spins the loop and reboots.
    drainOutbound(OTA_TX_DRAIN_TIMEOUT_MS);
    setBridgeState(false);
    char ota_reply[160];
    // OTA teardown barrier (Phase 5): only flash after a CLEAN MQTT shutdown.
    // A timed-out/forced stop leaves mbedTLS/heap ownership uncertain - writing
    // firmware then is the observed teardown heap-panic path - so abort and
    // resume the bridge instead of flashing under uncertain ownership.
    if (mqtt_bridge && !mqtt_bridge->canFlashAfterStop()) {
      Serial.println("OTA: aborted, MQTT stop did not complete cleanly - resuming bridge");
      otaAlert("OTA aborted: MQTT stop unclean, bridge resumed");
      setBridgeState(true);
    } else if (!_cli.getBoard()->otaFromManifest(getFirmwareVer(), false, ota_reply)) {
      Serial.print("OTA: aborted, resuming bridge - "); Serial.println(ota_reply);
      char ota_alert_msg[160];
      snprintf(ota_alert_msg, sizeof(ota_alert_msg), "OTA aborted: %s", ota_reply);
      otaAlert(ota_alert_msg);
      setBridgeState(true);
    }
    // Success path: otaFromManifest() flashes and reboots into the new image
    // (never returns), so there is no in-boot "success" alert - the START alert
    // plus the node returning on the new version is the success signal.
  }
#endif

#ifdef WITH_WEBCONFIG
  if (WebConfigServer::takeButtonToggleRequest()) {
    char wc_reply[160];
    setWebUIEnabled(!WebConfigServer::loadEnabled(false), wc_reply);
    Serial.println(wc_reply);
  }
  if (_webconfig) {
    _webconfig->tick(millis());
    if (!_webconfig->isRunning() && !_webconfig->isStopping()) {
      delete _webconfig;
      _webconfig = nullptr;
    }
  }
#endif

  // is pending dirty contacts write needed?
  if (dirty_contacts_expiry && millisHasNowPassed(dirty_contacts_expiry)) {
    acl.save(_fs);
    dirty_contacts_expiry = 0;
  }

  // update uptime
  uint32_t now = millis();
  uptime_millis += now - last_millis;
  last_millis = now;
#if !defined(PORTABLE_MQTT_OBSERVER) && MESH_ENABLE_CLOCK_SYNC
  checkGpsClockSyncOverride();
  checkClockSync();
#endif

#ifdef WITH_MQTT_BRIDGE
  _alerter.onLoop(now);
#endif

#if defined(WITH_MQTT_NEIGHBORS)
  // Two-stage periodic neighbors publication:
  //   stage 1 - zero-hop node-discover refreshes the neighbour table (60s window)
  //   stage 2 - anon-regions scope query per neighbour (startNeighborDiscover)
  // then the table JSON is published and the next cycle is rescheduled.
  bool periodic_neighbors_enabled = _cli.getObserverPrefs()->mqtt_neighbors_enabled;
  if (neighbor_discover_active) {
    loopNeighborDiscover();
  } else if (neighbor_table_refresh_active) {
    if (neighbor_table_refresh_periodic && !periodic_neighbors_enabled) {
      // periodic switched off mid-refresh -> cancel (leave pending_discover_tag alone)
      neighbor_table_refresh_active = false;
      neighbor_table_refresh_periodic = false;
      next_neighbors_publish = 0;
    } else if (pending_discover_tag == 0 || millisHasNowPassed(pending_discover_until)) {
      // 60s zero-hop window done -> begin the per-neighbour scope queries
      bool was_periodic = neighbor_table_refresh_periodic;
      pending_discover_tag = 0;
      neighbor_table_refresh_active = false;
      neighbor_table_refresh_periodic = false;
      char tmp_reply[80];
      const char* origin_str = was_periodic ? "periodic" : "manual";
      if (startNeighborDiscover(tmp_reply)) {
        MESH_DEBUG_PRINTLN("MQTT %s %s", origin_str, tmp_reply);
      } else {
        if (periodic_neighbors_enabled) {
          next_neighbors_publish = futureMillis(_cli.getObserverPrefs()->mqtt_neighbors_interval);
        }
        MESH_DEBUG_PRINTLN("MQTT %s neighbor scope discovery failed: %s", origin_str, tmp_reply);
      }
    }
  } else if (periodic_neighbors_enabled && mqtt_bridge && mqtt_bridge->isRunning()) {
    if (next_neighbors_publish == 0 ||
        (next_neighbors_publish != 0 && millisHasNowPassed(next_neighbors_publish))) {
      if (pending_discover_tag == 0 || millisHasNowPassed(pending_discover_until)) {
        pending_discover_tag = 0;
        sendNodeDiscoverReq();
        MESH_DEBUG_PRINTLN("MQTT periodic neighbor table refresh started");
      } else {
        MESH_DEBUG_PRINTLN("MQTT periodic refresh joined active neighbor discovery");
      }
      neighbor_table_refresh_active = true;
      neighbor_table_refresh_periodic = true;
    }
  }

  // Report the schedule state back to the bridge for `get mqtt.status`.
  if (mqtt_bridge) {
    if (neighbor_discover_active || neighbor_table_refresh_active) {
      mqtt_bridge->setNeighborsSchedule(MQTTBridge::NBR_ACTIVE, 0);
    } else if (next_neighbors_publish == 0 || millisHasNowPassed(next_neighbors_publish)) {
      mqtt_bridge->setNeighborsSchedule(MQTTBridge::NBR_DUE, 0);
    } else {
      long remaining_ms = (long)(next_neighbors_publish - futureMillis(0));
      uint32_t remaining_secs = remaining_ms > 0 ? (uint32_t)(remaining_ms / 1000) : 0;
      mqtt_bridge->setNeighborsSchedule(MQTTBridge::NBR_SCHEDULED, remaining_secs);
    }
  }
#endif

#ifdef WITH_SNMP
  // Push radio stats to SNMP agent every 2 seconds
  if (_snmp_agent.isRunning()) {
    static unsigned long last_snmp_stats = 0;
    if (now - last_snmp_stats >= 2000) {
      last_snmp_stats = now;
      _snmp_agent.updateRadioStats(
        radio_driver.getPacketsRecv(), radio_driver.getPacketsSent(),
        radio_driver.getPacketsRecvErrors(),
        (int16_t)_radio->getNoiseFloor(),
        (int16_t)radio_driver.getLastRSSI(),
        (int16_t)(radio_driver.getLastSNR() * 4),
        getNumSentFlood(), getNumSentDirect(),
        getNumRecvFlood(), getNumRecvDirect(),
        getTotalAirTime() / 1000, uptime_millis / 1000);
    }
  }
#endif
}

#if defined(WITH_MQTT_NEIGHBORS)
#include "helpers/MQTTMessageBuilder.h"
#if defined(ESP_PLATFORM)
#include <esp_heap_caps.h>
#endif

// This node's own non-flood scope names, same source the anon-regions server
// reply uses. Empty string when the node has no scoped regions.
void MyMesh::getLocalScopes(char* buf, size_t len) {
  if (!buf || len == 0) return;
  buf[0] = 0;
  region_map.exportNamesTo(buf, (int)len, REGION_DENY_FLOOD);
}

// Client side of the anon-regions request (the server side is handleAnonRegionsReq).
// Inner payload: {tag(4)}{ANON_REQ_TYPE_REGIONS}{0x00 = zero-hop reply path}.
mesh::Packet* MyMesh::sendAnonRegionsReq(const mesh::Identity& target, uint32_t& tag) {
  // RxReservePacketManager keeps a four-packet emergency floor. Preflight one
  // extra free packet so its void queue API cannot silently shed this request.
  if (_mgr->getFreeCount() < NEIGHBOR_DISCOVER_MIN_FREE_PACKETS) return NULL;

  uint8_t secret[PUB_KEY_SIZE];
  self_id.calcSharedSecret(secret, target);

  tag = getRTCClock()->getCurrentTimeUnique();
  uint8_t inner[6];
  memcpy(inner, &tag, 4);
  inner[4] = ANON_REQ_TYPE_REGIONS;
  inner[5] = 0x00; // request a zero-hop reply path

  mesh::Packet* pkt = createAnonDatagram(PAYLOAD_TYPE_ANON_REQ, self_id, target, secret, inner, sizeof(inner));
  if (!pkt) return NULL;
  sendDirect(pkt, NULL, 0, 0);
  return pkt;
}

bool MyMesh::cancelNeighborDiscoverRequest() {
  if (!neighbor_discover_request) return false;
  for (int i = _mgr->getOutboundTotal() - 1; i >= 0; i--) {
    if (_mgr->getOutboundByIdx(i) == neighbor_discover_request) {
      mesh::Packet* pkt = _mgr->removeOutboundByIdx(i);
      if (pkt) releasePacket(pkt);
      neighbor_discover_request = NULL;
      return true;
    }
  }
  return false;
}

// This timer starts after the request finishes transmitting. Allow the server
// delay, the responder's full CAD deferral window plus one maximum retry
// overshoot, and airtime for one priority-0 packet ahead of the response plus
// the response itself. The radio estimate scales with SF, bandwidth, coding
// rate, and preamble.
uint32_t MyMesh::neighborDiscoverQueryTimeoutMs() const {
  uint32_t response_airtime = _radio->getEstAirtimeFor(MAX_PACKET_PAYLOAD + 2);
  return SERVER_RESPONSE_DELAY + getCADFailMaxDuration() + 360UL
    + response_airtime * 2UL;
}

void MyMesh::resetNeighborDiscoverJsonBudget() {
  getLocalScopes(self_scopes_buf, sizeof(self_scopes_buf));
  {
    // No default region means this node floods unscoped, i.e. the wildcard.
    RegionEntry* def = region_map.getDefaultRegion();
    const char* def_name = (def && def->name[0]) ? def->name : "*";
    if (*def_name == '#') def_name++;  // match how self.scopes renders names
    strncpy(self_default_scope_buf, def_name, sizeof(self_default_scope_buf) - 1);
    self_default_scope_buf[sizeof(self_default_scope_buf) - 1] = 0;
  }
  MQTTBridge::getEffectiveMqttOrigin(
    _prefs.node_name, _cli.getObserverPrefs(),
    neighbor_discover_origin, sizeof(neighbor_discover_origin));

  char self_pubkey_hex[65];
  mesh::Utils::toHex(self_pubkey_hex, self_id.pub_key, PUB_KEY_SIZE);
  char timestamp[40];
  MQTTMessageBuilder::formatIsoTimestampForMqtt(
    getRTCClock()->getCurrentTime(), 0, nullptr, timestamp, sizeof(timestamp));

  neighbor_discover_publish_count = 0;
  neighbor_discover_queried_count = 0;
  neighbor_discover_truncated = false;
  neighbor_discover_json_size = MQTTMessageBuilder::measureNeighborsMessageBase(
    neighbor_discover_origin, self_pubkey_hex, timestamp, self_scopes_buf,
    self_default_scope_buf, neighbor_discover_count);
}

// Account for one terminal result. The base measurement reserves maximum-width
// progress metadata; UINT32_MAX likewise reserves the widest heard-age value.
// If this result cannot fit, stop before transmitting another scope request.
bool MyMesh::completeNeighborDiscoverEntry() {
  NeighborDiscoverEntry& entry = neighbor_discover[neighbor_discover_next];
  char pubkey_hex[65];
  mesh::Utils::toHex(pubkey_hex, entry.id.pub_key, PUB_KEY_SIZE);

  MQTTMessageBuilder::NeighborsMessageEntry measured = {
    pubkey_hex,
    entry.snr / 4.0f,
    UINT32_MAX,
    entry.scopes,
    entry.status == ND_RESPONDED ? "responded"
      : (entry.status == ND_SEND_FAILED ? "send_failed" : "timeout")
  };
  size_t added = MQTTMessageBuilder::measureNeighborsMessageEntry(measured);
  if (neighbor_discover_publish_count > 0) added++;  // array comma

  if (neighbor_discover_json_size + added >= MQTTBridge::NEIGHBORS_JSON_BUFFER_SIZE ||
      neighbor_discover_publish_count >= MQTTBridge::NEIGHBORS_MAX_PUBLISH_ENTRIES) {
    neighbor_discover_truncated = true;
    finishNeighborDiscover();
    return false;
  }

  neighbor_discover_json_size += added;
  neighbor_discover_publish_count++;
  neighbor_discover_next++;
  return true;
}

// Match a RESPONSE against the pending overlay entry by tag; copy its scope
// string (payload after the 8-byte {tag}{clock} header) into the entry.
bool MyMesh::handleNeighborDiscoverResponse(int overlay_idx, const uint8_t* data, size_t len) {
  if (overlay_idx < 0 || overlay_idx >= neighbor_discover_count) return false;
  NeighborDiscoverEntry& entry = neighbor_discover[overlay_idx];
  if (entry.status != ND_PENDING || len < 8) return false;

  uint32_t tag;
  memcpy(&tag, data, 4);
  if (tag != entry.tag) return false;

  size_t scope_len = len - 8;
  if (scope_len >= sizeof(entry.scopes)) {
    scope_len = sizeof(entry.scopes) - 1;
  }
  memcpy(entry.scopes, &data[8], scope_len);
  entry.scopes[scope_len] = 0;
  entry.status = ND_RESPONDED;
  // A zero-hop reply is proof we heard this neighbour now, so re-stamp both the
  // snapshot and the live table; a stamp taken before time sync heals here.
  entry.heard_timestamp = getRTCClock()->getCurrentTime();
  touchNeighbourHeard(entry.id, entry.heard_timestamp);
  return true;
}

// Refresh a live neighbour's heard time only: a scope reply carries no advert
// timestamp or SNR to update.
void MyMesh::touchNeighbourHeard(const mesh::Identity& id, uint32_t heard_timestamp) {
#if MAX_NEIGHBOURS
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    if (id.matches(neighbours[i].id)) {
      neighbours[i].heard_timestamp = heard_timestamp;
      return;
    }
  }
#endif
}

// A heard age is a wall-clock delta, so it only means something when both stamps
// share a clock epoch. An entry heard before the clock was set holds the unset
// default, which a synced clock turns into a ~2-year age; report those as
// unknown instead. See UPSTREAM_BUGS.md for the monotonic fix.
static bool neighborHeardAgeUsable(uint32_t heard_timestamp, uint32_t now_secs) {
  if (heard_timestamp == 0 || now_secs < heard_timestamp) return false;
  // Never synced: the stamp shares this clock's boot epoch, so the delta holds.
  if (now_secs < MQTTConnectionPolicy::kSyncedClockEpoch) return true;
  return heard_timestamp >= MQTTConnectionPolicy::kSyncedClockEpoch;
}

// Publish-ordering: usable ages first, then most recently heard, then stronger
// SNR, then pubkey. The JSON builder drops the tail if the buffer fills, so the
// head must be the most useful entries.
static bool neighborPublishEntryComesBefore(
    const MQTTMessageBuilder::NeighborsMessageEntry& lhs,
    const MQTTMessageBuilder::NeighborsMessageEntry& rhs) {
  if (lhs.heard_unknown != rhs.heard_unknown) {
    return !lhs.heard_unknown;
  }
  if (lhs.heard_secs_ago != rhs.heard_secs_ago) {
    return lhs.heard_secs_ago < rhs.heard_secs_ago;  // newer first
  }
  if (lhs.snr != rhs.snr) {
    return lhs.snr > rhs.snr;  // stronger first when equally recent
  }
  return strcmp(lhs.pubkey_hex, rhs.pubkey_hex) < 0;
}

#if defined(ESP_PLATFORM)
// Neighbors allocations prefer PSRAM where it exists and otherwise come from
// internal DRAM, so MQTT_NEIGHBORS_WITHOUT_PSRAM boards can build the table too.
#if defined(BOARD_HAS_PSRAM)
static const uint32_t kNeighborsAllocCaps = MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
#else
static const uint32_t kNeighborsAllocCaps = MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;
#endif

static void* neighborsAlloc(size_t size) {
  if (size == 0) return nullptr;
  void* p = heap_caps_malloc(size, kNeighborsAllocCaps);
#if defined(BOARD_HAS_PSRAM)
  if (!p) p = heap_caps_malloc(size, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
#endif
  return p;
}

static void neighborsFree(void* ptr) {
  if (ptr) heap_caps_free(ptr);
}

// ArduinoJson v7 JsonDocument has no real capacity cap (DynamicJsonDocument(N)
// is a no-op shim), so soft-cap peak pool growth to NEIGHBORS_DOC_POOL_BUDGET.
// used only rises on allocate - conservative for this single-shot doc (overflow
// path removes+breaks, so no further growth after free).
struct NeighborsDocAllocator : ArduinoJson::Allocator {
  size_t used = 0;
  static const size_t kBudget = MQTTBridge::NEIGHBORS_DOC_POOL_BUDGET;

  void* allocate(size_t size) override {
    if (used >= kBudget || size > kBudget - used) return nullptr;
    void* p = neighborsAlloc(size);
    if (p) used += size;
    return p;
  }

  void deallocate(void* ptr) override {
    neighborsFree(ptr);
  }

  void* reallocate(void* ptr, size_t new_size) override {
    size_t old_size = ptr ? heap_caps_get_allocated_size(ptr) : 0;
    size_t next_used = (used >= old_size) ? (used - old_size) : 0;
    if (next_used >= kBudget || new_size > kBudget - next_used) return nullptr;
    void* p = heap_caps_realloc(ptr, new_size, kNeighborsAllocCaps);
#if defined(BOARD_HAS_PSRAM)
    if (!p) p = heap_caps_realloc(ptr, new_size, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
#endif
    if (p) used = next_used + new_size;
    return p;
  }
};
#else
static void* neighborsAlloc(size_t size) { return size ? malloc(size) : nullptr; }
static void neighborsFree(void* ptr) { free(ptr); }
#endif

// Build the neighbors-table JSON and hand it to the bridge, then reschedule.
void MyMesh::finishNeighborDiscover() {
  char self_pubkey_hex[65];
  mesh::Utils::toHex(self_pubkey_hex, self_id.pub_key, PUB_KEY_SIZE);

  char timestamp[40];
  MQTTMessageBuilder::formatIsoTimestampForMqtt(getRTCClock()->getCurrentTime(), 0, nullptr, timestamp, sizeof(timestamp));

  // The entry table plus one hex string each reaches ~4.5 KB at MAX_NEIGHBOURS,
  // which does not fit the mesh loop task's 8 KB stack, so both share a single
  // heap block sized to this pass. Publishing is skipped if either alloc fails.
  const int publish_count = neighbor_discover_publish_count;
  const size_t hex_size = PUB_KEY_SIZE * 2 + 1;
  const size_t entries_bytes =
    sizeof(MQTTMessageBuilder::NeighborsMessageEntry) * publish_count;
  void* scratch = neighborsAlloc(entries_bytes + hex_size * publish_count);
  char* json_buf = (char*)neighborsAlloc(MQTTBridge::NEIGHBORS_JSON_BUFFER_SIZE);

  if (json_buf && (scratch || publish_count == 0)) {
    auto* entries = (MQTTMessageBuilder::NeighborsMessageEntry*)scratch;
    char* pubkey_hex = (char*)scratch + entries_bytes;
    uint32_t now_secs = getRTCClock()->getCurrentTime();

    for (int i = 0; i < publish_count; i++) {
      auto& entry = neighbor_discover[i];
      char* hex = &pubkey_hex[i * hex_size];
      mesh::Utils::toHex(hex, entry.id.pub_key, PUB_KEY_SIZE);
      entries[i].pubkey_hex = hex;
      entries[i].snr = entry.snr / 4.0f;
      bool heard_known = neighborHeardAgeUsable(entry.heard_timestamp, now_secs);
      entries[i].heard_unknown = !heard_known;
      entries[i].heard_secs_ago = heard_known ? (now_secs - entry.heard_timestamp) : 0;
      entries[i].scopes = entry.scopes;
      switch (entry.status) {
        case ND_RESPONDED:   entries[i].status = "responded"; break;
        case ND_SEND_FAILED: entries[i].status = "send_failed"; break;
        default:             entries[i].status = "timeout"; break;
      }
    }

    // insertion sort: most useful first (JSON builder drops the tail on overflow)
    for (int i = 1; i < publish_count; i++) {
      MQTTMessageBuilder::NeighborsMessageEntry entry = entries[i];
      int j = i;
      while (j > 0 && neighborPublishEntryComesBefore(entry, entries[j - 1])) {
        entries[j] = entries[j - 1];
        j--;
      }
      entries[j] = entry;
    }

#if defined(ESP_PLATFORM)
    NeighborsDocAllocator doc_alloc;
    JsonDocument doc(&doc_alloc);
#else
    JsonDocument doc;
#endif
    int json_len = MQTTMessageBuilder::buildNeighborsMessage(
      doc, neighbor_discover_origin, self_pubkey_hex, timestamp, self_scopes_buf,
      self_default_scope_buf, entries, publish_count,
      json_buf, MQTTBridge::NEIGHBORS_JSON_BUFFER_SIZE,
      neighbor_discover_count, neighbor_discover_queried_count,
      neighbor_discover_truncated);

    if (json_len > 0 && mqtt_bridge) {
      mqtt_bridge->requestPublishNeighbors(json_buf, (size_t)json_len);
    }
  }

  neighborsFree(scratch);
  neighborsFree(json_buf);

  neighbor_discover_active = false;
  neighbor_discover_count = 0;
  neighbor_discover_next = 0;
  neighbor_discover_publish_count = 0;
  neighbor_discover_queried_count = 0;
  neighbor_discover_json_size = 0;
  neighbor_discover_truncated = false;
  neighbor_discover_until = 0;
  neighbor_discover_request = NULL;
  if (_cli.getObserverPrefs()->mqtt_neighbors_enabled) {
    next_neighbors_publish = futureMillis(_cli.getObserverPrefs()->mqtt_neighbors_interval);
  }
}

// Advance the newest-first scope-query phase. Keep only one request in flight so
// its responder gets a clear reply opportunity and the packet pool stays free.
void MyMesh::loopNeighborDiscover() {
  if (!neighbor_discover_active) return;

  if (neighbor_discover_next >= neighbor_discover_count) {
    finishNeighborDiscover();
    return;
  }

  NeighborDiscoverEntry& entry = neighbor_discover[neighbor_discover_next];
  if (entry.status == ND_QUEUED) {
    if (!millisHasNowPassed(neighbor_discover_until)) return;
    if (cancelNeighborDiscoverRequest()) {
      entry.status = ND_SEND_FAILED;
      completeNeighborDiscoverEntry();
      return;
    }
    if (isCurrentOutbound(neighbor_discover_request)) {
      neighbor_discover_until = futureMillis(neighborDiscoverQueryTimeoutMs());
      return;
    }
    neighbor_discover_request = NULL;  // packet manager already shed it
    entry.status = ND_SEND_FAILED;
    completeNeighborDiscoverEntry();
    return;
  }
  if (entry.status == ND_PENDING) {
    if (!millisHasNowPassed(neighbor_discover_until)) return;
    entry.status = ND_TIMEOUT;
    completeNeighborDiscoverEntry();
    return;
  }
  if (entry.status == ND_RESPONDED || entry.status == ND_SEND_FAILED
      || entry.status == ND_TIMEOUT) {
    completeNeighborDiscoverEntry();
    return;
  }
  if (entry.status != ND_UNSENT) {
    neighbor_discover_next++;
    return;
  }

  uint32_t tag;
  mesh::Packet* request = sendAnonRegionsReq(entry.id, tag);
  if (request) {
    entry.tag = tag;
    entry.status = ND_QUEUED;
    neighbor_discover_request = request;
    neighbor_discover_until = futureMillis(NEIGHBOR_DISCOVER_QUEUE_TIMEOUT_MS);
  } else {
    entry.status = ND_SEND_FAILED;
    completeNeighborDiscoverEntry();
  }
}

// Shared precondition for starting a discovery: usable buffers + bridge running.
// PSRAM builds size their neighbors buffers for PSRAM, so a board whose PSRAM
// failed to init must not silently spend that much internal DRAM here.
// MQTT_NEIGHBORS_WITHOUT_PSRAM builds are already sized for internal DRAM.
bool MyMesh::neighborDiscoverReady(char* reply) {
#if defined(ESP_PLATFORM) && defined(BOARD_HAS_PSRAM)
  if (!psramFound()) { strcpy(reply, "Err - PSRAM not available"); return false; }
#endif
  if (!mqtt_bridge || !mqtt_bridge->isRunning()) { strcpy(reply, "Err - MQTT bridge not running"); return false; }
  return true;
}

// Snapshot the neighbor table newest-first. loopNeighborDiscover() emits one
// anon-regions query at a time so hidden responders do not reply as a burst.
bool MyMesh::startNeighborDiscover(char* reply) {
  if (neighbor_discover_active) {
    strcpy(reply, "Err - neighbor discover already active");
    return false;
  }
  if (!neighborDiscoverReady(reply)) {
    return false;  // reply already set
  }

  neighbor_discover_count = 0;
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    if (neighbours[i].heard_timestamp > 0) {
      NeighborDiscoverEntry& entry = neighbor_discover[neighbor_discover_count];
      entry.id = neighbours[i].id;
      entry.heard_timestamp = neighbours[i].heard_timestamp;
      entry.snr = neighbours[i].snr;
      entry.scopes[0] = 0;
      entry.tag = 0;
      entry.status = ND_UNSENT;
      neighbor_discover_count++;
    }
  }

  // Query the freshest/strongest entries first; pubkey makes ties deterministic.
  for (int i = 1; i < neighbor_discover_count; i++) {
    NeighborDiscoverEntry entry = neighbor_discover[i];
    int j = i;
    while (j > 0) {
      auto& rhs = neighbor_discover[j - 1];
      bool before = entry.heard_timestamp > rhs.heard_timestamp
        || (entry.heard_timestamp == rhs.heard_timestamp && entry.snr > rhs.snr)
        || (entry.heard_timestamp == rhs.heard_timestamp && entry.snr == rhs.snr
            && memcmp(entry.id.pub_key, rhs.id.pub_key, PUB_KEY_SIZE) < 0);
      if (!before) break;
      neighbor_discover[j] = neighbor_discover[j - 1];
      j--;
    }
    neighbor_discover[j] = entry;
  }

  neighbor_discover_next = 0;
  resetNeighborDiscoverJsonBudget();
  neighbor_discover_active = true;
  neighbor_discover_until = 0;
  neighbor_discover_request = NULL;

  if (neighbor_discover_count == 0) {
    finishNeighborDiscover();
    strcpy(reply, "OK - neighbor discover started (0 neighbors, self only)");
  } else {
    loopNeighborDiscover();  // queue the first request now
    sprintf(reply, "OK - neighbor discover started (%u neighbors)", (unsigned)neighbor_discover_count);
  }
  return true;
}
#endif // WITH_MQTT_NEIGHBORS

// To check if there is pending work
bool MyMesh::hasPendingWork() const {
  if (deferred_cli_command.pending || pending_self_advert || _cli.hasActiveUserGpioTimer()) return true;
#if defined(WITH_BRIDGE)
  const AbstractBridge* active_bridge = activeBridge();
  if (active_bridge && active_bridge->isRunning()) return true;
#endif
  if (radio_driver.isWatchdogObserving()) return true;  // keep MCU awake for one radio duty cycle
  if (radio_driver.isCalibratingNoiseFloor()) return true;  // keep MCU awake for the noise-floor window
  if (hasQueuedWorkDue() || hasRetryWorkDue()) return true;
  if (isMillisTimerDue(next_flood_advert) || isMillisTimerDue(next_local_advert)) return true;
  if (isMillisTimerDue(dirty_contacts_expiry)) return true;
  if (isMillisTimerDue(next_recent_repeater_sweep)) return true;
  if (_prefs.battery_alert_enabled && isMillisTimerDue(next_battery_alert_check)) return true;
  return hasScheduledRadioWorkDue();
}
