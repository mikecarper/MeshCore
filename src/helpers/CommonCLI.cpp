#include <Arduino.h>
#include "CommonCLI.h"
#include "CLICommandUtils.h"
#include "TxtDataHelpers.h"
#include "AdvertDataHelpers.h"
#include "AlertReporter.h"  // for alertReporterBannedChannelMatch()
#if defined(NRF52_PLATFORM)
#include "AtomicFileWriter.h"
#endif
#include <RTClib.h>
#include <Utils.h>
#include <stddef.h>

#if defined(NRF52_PLATFORM)
#include <nrf.h>
#include <nrf_soc.h>

#ifndef DFU_MAGIC_UF2_RESET
#define DFU_MAGIC_UF2_RESET 0x57
#endif

static void resetToUf2Bootloader() {
  uint8_t sd_enabled = 0;
  sd_softdevice_is_enabled(&sd_enabled);

  if (sd_enabled) {
    sd_power_gpregret_clr(0, 0xFF);
    sd_power_gpregret_set(0, DFU_MAGIC_UF2_RESET);
  } else {
    NRF_POWER->GPREGRET = DFU_MAGIC_UF2_RESET;
  }

  NVIC_SystemReset();
}
#endif

#define STR_HELPER(x) #x
#define STR(x) STR_HELPER(x)
#if defined(ENABLE_OTA)
  #include "ota/OtaCli.h"
  #include "ota/OtaContext.h"   // persist/sync OTA policy + signer allowlist with NodePrefs
#endif

#ifndef BRIDGE_MAX_BAUD
#define BRIDGE_MAX_BAUD 115200
#endif
#ifdef ESP_PLATFORM
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_wifi.h>
#include <esp_heap_caps.h>
#endif
#ifdef WITH_MQTT_BRIDGE
#include "bridges/MQTTBridge.h"
#include "MQTTDefaults.h"
#endif

#define RECENT_REPEATER_PREFIX_MAX_BYTES  3

// Believe it or not, this std C function is busted on some platforms!
static uint32_t _atoi(const char* sp) {
  uint32_t n = 0;
  while (*sp && *sp >= '0' && *sp <= '9') {
    n *= 10;
    n += (*sp++ - '0');
  }
  return n;
}

static bool parseRecentRepeaterGet(const char* config, int& page) {
  if (strncmp(config, "recent.repeater", 15) != 0) {
    return false;
  }

  const char* cursor = &config[15];
  if (*cursor == 's') {
    cursor++;
  }
  if (*cursor != 0 && *cursor != ' ') {
    return false;
  }

  while (*cursor == ' ') cursor++;
  if (strncmp(cursor, "page", 4) == 0 && (cursor[4] == 0 || cursor[4] == ' ')) {
    cursor += 4;
    while (*cursor == ' ') cursor++;
  }

  page = 1;
  if (*cursor) page = _atoi(cursor);
  if (page < 1) page = 1;
  return true;
}

static bool isValidName(const char *n) {
  while (*n) {
    if (*n == '[' || *n == ']' || *n == '/' || *n == '\\' || *n == ':' || *n == ',' || *n == '?' || *n == '*') return false;
    n++;
  }
  return true;
}

// Old fork firmware persisted the (since removed) NodePrefs MQTT fields to /com_prefs
// as a zero-filled gap between owner_info (which ends at offset 290) and a trailing
// observer block (rx_boosted_gain, flood_max_*, snmp/watchdog/alert settings).
// The gap size depended on MAX_MQTT_SLOTS at the time: 306 bytes of non-slot fields
// plus 186 bytes per slot (preset 24 + host 64 + port 2 + username 32 + password 64).
// loadPrefsInt() uses the file size to tell the eras apart and recover the tail.
static const size_t LEGACY_MQTT_GAP_6SLOT = 306 + 6 * 186;  // 1422
static const size_t LEGACY_MQTT_GAP_3SLOT = 306 + 3 * 186;  // 864
static const size_t LEGACY_OBS_TAIL_MAX = 124;  // rx_boosted(1) + flood(2) + snmp(25) + watchdog(1) + alert block(95)

static bool looksNumeric(const char* s) {
  if (s == NULL) return false;
  while (*s == ' ') s++;
  if (*s == '-' || *s == '+') s++;
  bool saw_digit = false;
  bool saw_dot = false;
  while (*s) {
    if (*s >= '0' && *s <= '9') {
      saw_digit = true;
    } else if (*s == '.' && !saw_dot) {
      saw_dot = true;
    } else if (*s == ' ') {
      while (*s == ' ') s++;
      return saw_digit && *s == 0;
    } else {
      break;
    }
    s++;
  }
  return saw_digit && *s == 0;
}

static bool looksUnsignedInteger(const char* s) {
  if (s == NULL) return false;
  while (*s == ' ') s++;
  bool saw_digit = false;
  while (*s) {
    if (*s >= '0' && *s <= '9') {
      saw_digit = true;
    } else if (*s == ' ') {
      while (*s == ' ') s++;
      return saw_digit && *s == 0;
    } else {
      return false;
    }
    s++;
  }
  return saw_digit;
}

static bool configKeyMatches(const char* config, const char* key) {
  if (config == NULL || key == NULL) return false;
  size_t key_len = strlen(key);
  return strncmp(config, key, key_len) == 0
      && (config[key_len] == 0 || config[key_len] == ' ' || config[key_len] == '.');
}

static bool isAdvancedRetryConfig(const char* config) {
  return configKeyMatches(config, "direct.retry.heard")
      || configKeyMatches(config, "direct.retry.margin")
      || configKeyMatches(config, "flood.retry.prefixes")
      || configKeyMatches(config, "flood.retry.ignore")
      || configKeyMatches(config, "flood.retry.bridge")
      || configKeyMatches(config, "flood.retry.bucket")
      || configKeyMatches(config, "recent.repeater")
      || configKeyMatches(config, "recent.repeaters");
}

static bool isBasicRetryConfig(const char* config) {
  return configKeyMatches(config, "retry.preset")
      || configKeyMatches(config, "direct.retry")
      || configKeyMatches(config, "flood.retry.count")
      || configKeyMatches(config, "flood.retry.path")
      || configKeyMatches(config, "flood.retry.group.path")
      || configKeyMatches(config, "flood.retry.advert");
}

static bool parseUint8Strict(const char* value, uint8_t min_value, uint8_t max_value, uint8_t& result) {
  if (value == NULL || *value == 0) {
    return false;
  }

  uint16_t parsed = 0;
  const char* sp = value;
  while (*sp) {
    if (*sp < '0' || *sp > '9') {
      return false;
    }
    parsed = (uint16_t)((parsed * 10) + (*sp - '0'));
    if (parsed > max_value) {
      return false;
    }
    sp++;
  }
  if (parsed < min_value) {
    return false;
  }
  result = (uint8_t)parsed;
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

static float defaultLoRaBandwidth() {
#ifdef LORA_BW
  if (isValidLoRaBandwidth((float)LORA_BW)) {
    return (float)LORA_BW;
  }
#endif
  return 125.0f;
}

static const char* skipSpacesConst(const char* s) {
  while (s != NULL && *s == ' ') s++;
  return s;
}

static bool parseUint32Strict(const char* s, uint32_t& out) {
  if (!looksUnsignedInteger(s)) {
    return false;
  }

  uint64_t n = 0;
  s = skipSpacesConst(s);
  while (*s >= '0' && *s <= '9') {
    n = (n * 10) + (uint32_t)(*s - '0');
    if (n > 0xFFFFFFFFULL) {
      return false;
    }
    s++;
  }
  out = (uint32_t)n;
  return true;
}

static int countSeparatedParts(const char* s, char separator) {
  if (s == NULL || *s == 0) {
    return 0;
  }

  int count = 1;
  while (*s) {
    if (*s++ == separator) {
      count++;
    }
  }
  return count;
}

static bool parseScheduledRadioArgs(const char* args, bool temporary, float& freq, float& bw,
                                    uint8_t& sf, uint8_t& cr, uint32_t& start_time,
                                    uint32_t& end_time) {
  const int expected_parts = temporary ? 6 : 5;
  args = skipSpacesConst(args);
  if (countSeparatedParts(args, ',') != expected_parts) {
    return false;
  }
  char local[96];
  if (strlen(args) >= sizeof(local)) {
    return false;
  }
  StrHelper::strncpy(local, args, sizeof(local));
  const char* parts[6];
  int num = mesh::Utils::parseTextParts(local, parts, expected_parts, ',');
  if (num != expected_parts) {
    return false;
  }

  uint32_t sf_u32 = 0;
  uint32_t cr_u32 = 0;
  if (!looksNumeric(parts[0]) || !looksNumeric(parts[1])
      || !parseUint32Strict(parts[2], sf_u32)
      || !parseUint32Strict(parts[3], cr_u32)
      || !parseUint32Strict(parts[4], start_time)) {
    return false;
  }
  if (sf_u32 > 255 || cr_u32 > 255) {
    return false;
  }

  freq = atof(parts[0]);
  bw = atof(parts[1]);
  sf = (uint8_t)sf_u32;
  cr = (uint8_t)cr_u32;
  if (temporary && !parseUint32Strict(parts[5], end_time)) {
    return false;
  }
  if (!temporary) {
    end_time = 0;
  }
  return true;
}

static int16_t parseSnrDbX4(const char* s) {
  float db = atof(s);
  return (int16_t)(db * 4.0f + (db >= 0.0f ? 0.5f : -0.5f));
}

static void formatSnrDbX4(char* dest, size_t dest_len, int16_t snr_x4) {
  int16_t v = snr_x4;
  const char* sign = "";
  if (v < 0) {
    sign = "-";
    v = -v;
  }
  snprintf(dest, dest_len, "%s%d.%02d", sign, v / 4, (v % 4) * 25);
}

static const char* retryPresetName(uint8_t preset) {
  switch (preset) {
    case RETRY_PRESET_INFRA: return "infra";
    case RETRY_PRESET_ROOFTOP: return "rooftop";
    case RETRY_PRESET_MOBILE: return "mobile";
    default: return "custom";
  }
}

static bool isValidRxPowerSavingPeriod(uint32_t us) {
  return us >= RX_POWERSAVING_MIN_PERIOD_US && us <= RX_POWERSAVING_MAX_PERIOD_US;
}

// MeshCore preamble convention used for the RX powersaving timing calculation.
// Must stay in sync with RadioLibWrapper::preambleLengthForSF() (the value the
// radio actually transmits); kept local here because CommonCLI is radio-agnostic.
static uint16_t rxPowerSavingPreambleForSF(uint8_t sf) {
  return sf <= 8 ? 32 : 16;
}

static bool isNumeric(const char* sp) {
  if (!sp || !*sp) return false;
  while (*sp) {
    if (*sp < '0' || *sp > '9') return false;
    sp++;
  }
  return true;
}

static uint32_t ceilPositiveFloat(float value) {
  uint32_t rounded = (uint32_t)value;
  return value > (float)rounded ? rounded + 1 : rounded;
}

bool CommonCLI::calculateRxPowerSavingLevel(uint32_t level, uint8_t sf, float bw, uint32_t preamble,
                                            uint32_t* rx_us, uint32_t* sleep_us) {
  if (level < 1 || level > 10 || sf < 5 || sf > 12 || bw <= 0.0f || (preamble != 16 && preamble != 32)) {
    return false;
  }

  const float symbol_us = (1000.0f * (float)(1UL << sf)) / bw;
  const float amount = (float)(level - 1) / 9.0f;
  const float rx_start_symbols = preamble == 16 ? 12.0f : 16.0f;
  const float sleep_start_symbols = preamble == 16 ? 2.0f : 15.0f;
  const float rx_edge_symbols = 8.0f;
  const float sleep_edge_symbols = (float)preamble + 4.25f - 8.0f;

  const float rx_symbols = rx_start_symbols + amount * (rx_edge_symbols - rx_start_symbols);
  const float sleep_symbols = sleep_start_symbols + amount * (sleep_edge_symbols - sleep_start_symbols);

  *rx_us = ceilPositiveFloat(rx_symbols * symbol_us);
  *sleep_us = (uint32_t)(sleep_symbols * symbol_us);
  return true;
}

static void ensureRxPowerSavingDefaults(NodePrefs* prefs) {
  if (!isValidRxPowerSavingPeriod(prefs->rx_ps_rx_us)) {
    prefs->rx_ps_rx_us = RX_POWERSAVING_DEFAULT_RX_US;
  }
  if (!isValidRxPowerSavingPeriod(prefs->rx_ps_sleep_us)) {
    prefs->rx_ps_sleep_us = RX_POWERSAVING_DEFAULT_SLEEP_US;
  }
}

// Recomputes rx_ps_rx_us/rx_ps_sleep_us from the stored level and the current
// radio SF/BW. No-op (returns false) for manual timings (rx_ps_level == 0).
// Lets level-based RX powersaving auto-retune when SF/BW change.
bool CommonCLI::recalculateRxPowerSavingFromLevel(NodePrefs* prefs) {
  if (prefs->rx_ps_level < 1 || prefs->rx_ps_level > 10) return false;  // manual: nothing to recompute
  uint32_t preamble = prefs->rx_ps_preamble ? prefs->rx_ps_preamble
                                            : rxPowerSavingPreambleForSF(prefs->sf);
  uint32_t rx_us, sleep_us;
  if (!calculateRxPowerSavingLevel(prefs->rx_ps_level, prefs->sf, prefs->bw, preamble, &rx_us, &sleep_us)) {
    return false;
  }
  if (!isValidRxPowerSavingPeriod(rx_us) || !isValidRxPowerSavingPeriod(sleep_us)) {
    return false;
  }
  prefs->rx_ps_rx_us = rx_us;
  prefs->rx_ps_sleep_us = sleep_us;
  return true;
}

static void markDirectRetryPrefsValid(NodePrefs* prefs) {
  prefs->direct_retry_prefs_magic[0] = DIRECT_RETRY_PREFS_MAGIC_0;
  prefs->direct_retry_prefs_magic[1] = DIRECT_RETRY_PREFS_MAGIC_1;
}

static void applyFloodRetryPreset(NodePrefs* prefs, uint8_t preset) {
  if (preset == RETRY_PRESET_INFRA) {
    prefs->flood_retry_attempts = FLOOD_RETRY_INFRA_COUNT;
    prefs->flood_retry_max_path = FLOOD_RETRY_INFRA_MAX_PATH;
  } else if (preset == RETRY_PRESET_MOBILE) {
    prefs->flood_retry_attempts = FLOOD_RETRY_MOBILE_COUNT;
    prefs->flood_retry_max_path = FLOOD_RETRY_MOBILE_MAX_PATH;
  } else {
    prefs->flood_retry_attempts = FLOOD_RETRY_ROOFTOP_COUNT;
    prefs->flood_retry_max_path = FLOOD_RETRY_ROOFTOP_MAX_PATH;
  }
  prefs->flood_retry_group_max_path = prefs->flood_retry_max_path == 0
      ? FLOOD_RETRY_PATH_GATE_DISABLED
      : FLOOD_RETRY_GROUP_MAX_PATH_DEFAULT;
}

static bool parseFloodRetryPathGate(const char* value, uint8_t& path_gate) {
  if (value == NULL) {
    return false;
  }
  if (strcmp(value, "off") == 0 || strcmp(value, "disabled") == 0 || strcmp(value, "disable") == 0) {
    path_gate = FLOOD_RETRY_PATH_GATE_DISABLED;
    return true;
  }
  return parseUint8Strict(value, 0, 63, path_gate);
}

static void formatFloodRetryPathGate(char* dest, uint8_t path_gate) {
  if (path_gate == FLOOD_RETRY_PATH_GATE_DISABLED) {
    strcpy(dest, "off");
  } else {
    sprintf(dest, "%u", (unsigned int)path_gate);
  }
}

static bool parseFloodChannelBlockHops(const char* value, uint8_t& max_hops) {
  if (value == NULL) {
    return false;
  }
  value = skipSpacesConst(value);
  if (strcmp(value, "all") == 0) {
    max_hops = FLOOD_CHANNEL_BLOCK_HOPS_ALL;
    return true;
  }
  return parseUint8Strict(value, 1, 7, max_hops);
}

static bool parseFloodChannelBlockRowHops(const char* value, uint8_t& max_hops) {
  if (value == NULL) {
    return false;
  }
  value = skipSpacesConst(value);
  if (strcmp(value, "default") == 0 || strcmp(value, "def") == 0 || strcmp(value, "inherit") == 0) {
    max_hops = FLOOD_CHANNEL_BLOCK_HOPS_INHERIT;
    return true;
  }
  return parseFloodChannelBlockHops(value, max_hops);
}

static bool parseFloodChannelBlockHopAssignment(const char* text, bool allow_bare, uint8_t& max_hops) {
  char token[16];
  text = skipSpacesConst(text);
  if (text == NULL || *text == 0) {
    return false;
  }

  size_t len = 0;
  while (text[len] && text[len] != ' ' && len + 1 < sizeof(token)) {
    token[len] = text[len];
    len++;
  }
  token[len] = 0;

  const char* value = NULL;
  if (strncmp(token, "h=", 2) == 0) {
    value = token + 2;
  } else if (strncmp(token, "hops=", 5) == 0) {
    value = token + 5;
  } else if (allow_bare) {
    value = token;
  } else {
    return false;
  }
  return parseFloodChannelBlockRowHops(value, max_hops);
}

static bool looksFloodChannelBlockHopAssignment(const char* text) {
  text = skipSpacesConst(text);
  if (text == NULL || *text == 0) {
    return false;
  }
  return (*text >= '0' && *text <= '9')
      || strncmp(text, "h=", 2) == 0
      || strncmp(text, "hops=", 5) == 0
      || strncmp(text, "all", 3) == 0
      || strncmp(text, "def", 3) == 0
      || strncmp(text, "default", 7) == 0
      || strncmp(text, "inherit", 7) == 0;
}

static bool trimFloodChannelBlockHopSuffix(char* name, uint8_t& max_hops) {
  size_t len = strlen(name);
  while (len > 0 && name[len - 1] == ' ') {
    name[--len] = 0;
  }
  char* token = strrchr(name, ' ');
  if (token == NULL) {
    return true;
  }
  if (strncmp(token + 1, "h=", 2) != 0 && strncmp(token + 1, "hops=", 5) != 0) {
    return true;
  }
  if (!parseFloodChannelBlockHopAssignment(token + 1, false, max_hops)) {
    return false;
  }
  *token = 0;
  return strlen(name) > 0;
}

static void formatFloodChannelBlockHops(char* dest, uint8_t max_hops) {
  if (max_hops == FLOOD_CHANNEL_BLOCK_HOPS_ALL) {
    strcpy(dest, "h=all");
  } else {
    sprintf(dest, "h>%u", (unsigned int)max_hops);
  }
}

static void formatFloodRetryPrefixList(char* dest, const uint8_t prefixes[][FLOOD_RETRY_PREFIX_LEN],
                                       uint8_t max_prefixes) {
  char* out = dest;
  bool first = true;
  for (int i = 0; i < max_prefixes; i++) {
    const uint8_t* prefix = prefixes[i];
    if (prefix[0] == 0 && prefix[1] == 0 && prefix[2] == 0) {
      continue;
    }
    if (!first) {
      *out++ = ',';
    }
    mesh::Utils::toHex(out, prefix, FLOOD_RETRY_PREFIX_LEN);
    out += FLOOD_RETRY_PREFIX_LEN * 2;
    first = false;
  }
  *out = 0;
}

static bool parseFloodRetryPrefixList(uint8_t dest[][FLOOD_RETRY_PREFIX_LEN], uint8_t max_prefixes, const char* value) {
  if (max_prefixes > FLOOD_RETRY_LIST_PREFIXES) {
    return false;
  }
  uint8_t parsed[FLOOD_RETRY_LIST_PREFIXES][FLOOD_RETRY_PREFIX_LEN];
  memset(parsed, 0, sizeof(parsed));
  if (value == NULL || value[0] == 0 || strcmp(value, "none") == 0 || strcmp(value, "off") == 0) {
    memcpy(dest, parsed, max_prefixes * FLOOD_RETRY_PREFIX_LEN);
    return true;
  }

  char local[FLOOD_RETRY_LIST_TEXT_MAX];
  StrHelper::strncpy(local, value, sizeof(local));
  const char* parts[FLOOD_RETRY_LIST_PREFIXES + 1];
  int num = mesh::Utils::parseTextParts(local, parts, FLOOD_RETRY_LIST_PREFIXES + 1);
  if (num > max_prefixes) {
    return false;
  }
  for (int i = 0; i < num; i++) {
    if (strlen(parts[i]) != FLOOD_RETRY_PREFIX_LEN * 2) {
      return false;
    }
    for (int j = 0; j < FLOOD_RETRY_PREFIX_LEN * 2; j++) {
      if (!mesh::Utils::isHexChar(parts[i][j])) {
        return false;
      }
    }
    if (!mesh::Utils::fromHex(parsed[i], FLOOD_RETRY_PREFIX_LEN, parts[i])
        || (parsed[i][0] == 0 && parsed[i][1] == 0 && parsed[i][2] == 0)) {
      return false;
    }
  }
  memcpy(dest, parsed, max_prefixes * FLOOD_RETRY_PREFIX_LEN);
  return true;
}

static const uint8_t* floodRetryBucketPrefixAt(const NodePrefs* prefs, uint8_t bucket, uint8_t index) {
  if (bucket < FLOOD_RETRY_BRIDGE_BUCKETS && index < FLOOD_RETRY_BUCKET_PREFIXES) {
    return prefs->flood_retry_bridge_buckets[bucket][index];
  }
  if (bucket == FLOOD_RETRY_BRIDGE_BUCKETS && index < FLOOD_RETRY_PREFIX_SLOTS) {
    return prefs->flood_retry_prefixes[index];
  }
  return NULL;
}

static uint8_t floodRetryBucketPrefixCount(uint8_t bucket) {
  return bucket < FLOOD_RETRY_BRIDGE_BUCKETS ? FLOOD_RETRY_BUCKET_PREFIXES : FLOOD_RETRY_PREFIX_SLOTS;
}

static bool findFloodRetryFirstByteCollision(const NodePrefs* prefs, uint8_t& first_byte,
                                             uint8_t& first_bucket, uint8_t& second_bucket) {
  for (uint8_t bucket_a = 0; bucket_a <= FLOOD_RETRY_BRIDGE_BUCKETS; bucket_a++) {
    for (uint8_t bucket_b = bucket_a + 1; bucket_b <= FLOOD_RETRY_BRIDGE_BUCKETS; bucket_b++) {
      for (uint8_t a = 0; a < floodRetryBucketPrefixCount(bucket_a); a++) {
        const uint8_t* prefix_a = floodRetryBucketPrefixAt(prefs, bucket_a, a);
        if (prefix_a == NULL || (prefix_a[0] == 0 && prefix_a[1] == 0 && prefix_a[2] == 0)) {
          continue;
        }
        for (uint8_t b = 0; b < floodRetryBucketPrefixCount(bucket_b); b++) {
          const uint8_t* prefix_b = floodRetryBucketPrefixAt(prefs, bucket_b, b);
          if (prefix_b != NULL && (prefix_b[0] != 0 || prefix_b[1] != 0 || prefix_b[2] != 0)
              && prefix_a[0] == prefix_b[0]) {
            first_byte = prefix_a[0];
            first_bucket = bucket_a;
            second_bucket = bucket_b;
            return true;
          }
        }
      }
    }
  }
  return false;
}

static bool formatFloodRetryBucketCollisionWarning(char* reply, const NodePrefs* prefs, const char* prefix) {
  uint8_t first_byte, first_bucket, second_bucket;
  if (!findFloodRetryFirstByteCollision(prefs, first_byte, first_bucket, second_bucket)) {
    return false;
  }
  if (second_bucket == FLOOD_RETRY_BRIDGE_BUCKETS) {
    snprintf(reply, 160, "%sWARNING: 1-byte %02X matches buckets %u and 7 (other)", prefix,
             (unsigned int)first_byte, (unsigned int)first_bucket + 1U);
  } else {
    snprintf(reply, 160, "%sWARNING: 1-byte %02X matches buckets %u and %u", prefix,
             (unsigned int)first_byte, (unsigned int)first_bucket + 1U,
             (unsigned int)second_bucket + 1U);
  }
  return true;
}

static bool parseFloodChannelBlockKey(const char* text, uint8_t secret[PUB_KEY_SIZE], uint8_t& key_len) {
  if (text == NULL || text[0] == 0) {
    return false;
  }

  memset(secret, 0, PUB_KEY_SIZE);
  if (text[0] == '#') {
    if (!isValidName(text)) {
      return false;
    }
    mesh::Utils::sha256(secret, CIPHER_KEY_SIZE, (const uint8_t*)text, strlen(text));
    key_len = CIPHER_KEY_SIZE;
    return true;
  }

  size_t hex_len = strlen(text);
  if (!(hex_len == CIPHER_KEY_SIZE * 2 || hex_len == PUB_KEY_SIZE * 2)) {
    return false;
  }
  for (size_t i = 0; i < hex_len; i++) {
    if (!mesh::Utils::isHexChar(text[i])) {
      return false;
    }
  }

  key_len = (uint8_t)(hex_len / 2);
  return mesh::Utils::fromHex(secret, key_len, text);
}

static bool parseFloodChannelBlockDotIndex(const char*& cursor, int& index) {
  if (*cursor != '.') {
    index = 0;
    return true;
  }

  cursor++;
  if (*cursor < '0' || *cursor > '9') {
    return false;
  }
  int value = 0;
  while (*cursor >= '0' && *cursor <= '9') {
    value = (value * 10) + (*cursor - '0');
    if (value > FLOOD_CHANNEL_BLOCK_SLOTS) {
      return false;
    }
    cursor++;
  }
  if (value < 1) {
    return false;
  }
  index = value;
  return true;
}

static void copyTrimmedFloodChannelBlockName(char* dest, size_t dest_len, const char* src) {
  src = skipSpacesConst(src);
  StrHelper::strncpy(dest, src, dest_len);
  size_t len = strlen(dest);
  while (len > 0 && dest[len - 1] == ' ') {
    dest[--len] = 0;
  }
}

static void applyDirectRetryPreset(NodePrefs* prefs, uint8_t preset) {
  prefs->retry_preset = preset;
  if (preset == RETRY_PRESET_INFRA) {
    prefs->direct_retry_attempts = DIRECT_RETRY_INFRA_COUNT;
    prefs->direct_retry_base_ms = DIRECT_RETRY_INFRA_BASE_MS;
    prefs->direct_retry_step_ms = DIRECT_RETRY_INFRA_STEP_MS;
    prefs->direct_retry_snr_margin_x4 = DIRECT_RETRY_INFRA_MARGIN_X4;
  } else if (preset == RETRY_PRESET_MOBILE) {
    prefs->direct_retry_attempts = DIRECT_RETRY_MOBILE_COUNT;
    prefs->direct_retry_base_ms = DIRECT_RETRY_MOBILE_BASE_MS;
    prefs->direct_retry_step_ms = DIRECT_RETRY_MOBILE_STEP_MS;
    prefs->direct_retry_snr_margin_x4 = DIRECT_RETRY_MOBILE_MARGIN_X4;
  } else {
    prefs->retry_preset = RETRY_PRESET_ROOFTOP;
    prefs->direct_retry_attempts = DIRECT_RETRY_ROOFTOP_COUNT;
    prefs->direct_retry_base_ms = DIRECT_RETRY_ROOFTOP_BASE_MS;
    prefs->direct_retry_step_ms = DIRECT_RETRY_ROOFTOP_STEP_MS;
    prefs->direct_retry_snr_margin_x4 = DIRECT_RETRY_ROOFTOP_MARGIN_X4;
  }
  applyFloodRetryPreset(prefs, prefs->retry_preset);
  markDirectRetryPrefsValid(prefs);
}

static void setDefaultDirectRetryPrefs(NodePrefs* prefs) {
  applyDirectRetryPreset(prefs, RETRY_PRESET_ROOFTOP);
  prefs->direct_retry_cr_enabled = 1;
  prefs->direct_retry_cr4_snr_x4 = DIRECT_RETRY_CR4_MIN_SNR_X4_DEFAULT;
  prefs->direct_retry_cr5_snr_x4 = DIRECT_RETRY_CR5_MIN_SNR_X4_DEFAULT;
  prefs->direct_retry_cr7_snr_x4 = DIRECT_RETRY_CR7_MIN_SNR_X4_DEFAULT;
  prefs->direct_retry_cr8_snr_x4 = DIRECT_RETRY_CR8_MAX_SNR_X4_DEFAULT;
  prefs->direct_retry_enabled = 1;
  prefs->direct_retry_recent_enabled = DIRECT_RETRY_RECENT_DEFAULT;
  markDirectRetryPrefsValid(prefs);
}

static bool directRetryPrefsValid(const NodePrefs* prefs) {
  return prefs->direct_retry_prefs_magic[0] == DIRECT_RETRY_PREFS_MAGIC_0
      && prefs->direct_retry_prefs_magic[1] == DIRECT_RETRY_PREFS_MAGIC_1;
}

static bool parseRetryPreset(const char* s, uint8_t& preset) {
  if (strcmp(s, "infra") == 0 || strcmp(s, "0") == 0) {
    preset = RETRY_PRESET_INFRA;
    return true;
  }
  if (strcmp(s, "rooftop") == 0 || strcmp(s, "1") == 0) {
    preset = RETRY_PRESET_ROOFTOP;
    return true;
  }
  if (strcmp(s, "mobile") == 0 || strcmp(s, "2") == 0) {
    preset = RETRY_PRESET_MOBILE;
    return true;
  }
  return false;
}

static bool parseHashPrefix(const char* text, uint8_t* prefix, uint8_t& prefix_len) {
  size_t hex_len = strlen(text);
  if (hex_len == 0 || (hex_len & 1) || hex_len > RECENT_REPEATER_PREFIX_MAX_BYTES * 2) {
    return false;
  }
  for (size_t i = 0; i < hex_len; i++) {
    if (!mesh::Utils::isHexChar(text[i])) {
      return false;
    }
  }
  prefix_len = hex_len / 2;
  return mesh::Utils::fromHex(prefix, prefix_len, text);
}

static void formatSnrDbX4Short(char* dest, size_t dest_len, int16_t snr_x4) {
  formatSnrDbX4(dest, dest_len, snr_x4);
  size_t len = strlen(dest);
  if (len > 3 && dest[len - 1] == '0') {
    dest[len - 1] = 0;
  }
}

void CommonCLI::loadPrefs(FILESYSTEM* fs) {
  bool is_fresh_install = false;
  bool is_upgrade = false;
  bool loaded = false;

  // The hardware main-loop watchdog is opt-out. Older preference files do not
  // contain its appended byte, so they safely inherit the enabled default.
  _prefs->system_watchdog_enabled = 1;

  if (fs->exists("/com_prefs")) {
    loadPrefsInt(fs, "/com_prefs"); loaded = true;   // new filename
  } else if (fs->exists("/node_prefs")) {
    loadPrefsInt(fs, "/node_prefs");
    loaded = true;
    is_upgrade = true;  // Migrating from old filename
    savePrefs(fs);  // save to new filename
    fs->remove("/node_prefs");  // remove old
  } else {
    // File doesn't exist - set default bridge settings for fresh installs
    is_fresh_install = true;
    _prefs->bridge_pkt_src = 1;  // Default to RX (logRx) for new installs
  }
#ifdef WITH_MQTT_BRIDGE
  // Load observer preferences (MQTT/WiFi/timezone/SNMP/alert) from /mqtt_prefs.
  // Readers (MQTTBridge, AlertReporter, observer CLI) use _mqtt_prefs directly —
  // these fields no longer exist in NodePrefs, so there is nothing to sync.
  loadMQTTPrefs(fs);

  // For MQTT bridge, migrate bridge.source to RX (logRx) only on fresh installs or upgrades
  // so legacy "tx" is not the default. mqtt.rx / mqtt.tx are separate (fresh default: advert for TX)
  if ((is_fresh_install || is_upgrade) && _prefs->bridge_pkt_src == 0) {
    MESH_DEBUG_PRINTLN("MQTT Bridge: Migrating bridge.source from tx to rx (MQTT bridge default)");
    _prefs->bridge_pkt_src = 1;  // Set to RX (logRx)
    savePrefs(fs);  // Save the updated preference
  }
  // mqtt_rx_enabled: new field appended to end of MQTTPrefs. On upgrade from older firmware,
  // the shorter /mqtt_prefs file won't contain it, so it keeps the default value (1 = on)
  // set by setMQTTPrefsDefaults(). No explicit migration needed.
#endif

  if (_com_prefs_needs_upgrade) {
    // Old-format /com_prefs (legacy MQTT gap + trailing observer block) was detected:
    // rewrite the prefs files in the current layout, one time. This persists the
    // recovered rx_boosted_gain/flood_max_* values and (on MQTT builds) the observer
    // settings that loadMQTTPrefs carried over into /mqtt_prefs.
    savePrefs(fs);
    _com_prefs_needs_upgrade = false;
  }
#if defined(ENABLE_OTA)
  if (loaded) syncOtaConfigFromPrefs();   // persisted OTA policy/keys -> OtaContext (else keep safe defaults)
#endif
}

#if defined(ENABLE_OTA)
// Push the persisted OTA policy + signer allowlist into the running OtaContext (called after load).
void CommonCLI::syncOtaConfigFromPrefs() {
  mesh::ota::OtaContext& c = mesh::ota::ota_ctx();
  c.manager.set_autofetch(_prefs->ota_autofetch);
  c.manager.set_checkpoint_blocks(_prefs->ota_checkpoint_blocks);
  c.manager.set_advert_mins(_prefs->ota_advert_interval);
  c.manager.set_max_hops(_prefs->ota_max_hops);
  c.autoinstall = _prefs->ota_autoinstall;
  c.allow.clear();
  for (uint8_t i = 0; i < _prefs->ota_signer_count && i < MAX_OTA_SIGNERS; i++)
    c.allow.add(_prefs->ota_signers[i]);
}
#endif

void CommonCLI::loadPrefsInt(FILESYSTEM* fs, const char* filename) {
#if defined(RP2040_PLATFORM)
  File file = fs->open(filename, "r");
#else
  File file = fs->open(filename);
#endif
  if (file) {
    uint8_t pad[8];

    file.read((uint8_t *)&_prefs->airtime_factor, sizeof(_prefs->airtime_factor));    // 0
    file.read((uint8_t *)&_prefs->node_name, sizeof(_prefs->node_name));              // 4
    file.read(pad, 4);                                                                // 36
    file.read((uint8_t *)&_prefs->node_lat, sizeof(_prefs->node_lat));                // 40
    file.read((uint8_t *)&_prefs->node_lon, sizeof(_prefs->node_lon));                // 48
    file.read((uint8_t *)&_prefs->password[0], sizeof(_prefs->password));             // 56
    file.read((uint8_t *)&_prefs->freq, sizeof(_prefs->freq));                        // 72
    file.read((uint8_t *)&_prefs->tx_power_dbm, sizeof(_prefs->tx_power_dbm));        // 76
    file.read((uint8_t *)&_prefs->disable_fwd, sizeof(_prefs->disable_fwd));          // 77
    file.read((uint8_t *)&_prefs->advert_interval, sizeof(_prefs->advert_interval));  // 78
    file.read(pad, 1);                                                                // 79 : 1 byte unused (was rx_boosted_gain in v1.14.1, moved to end for upgrade compat)
    file.read((uint8_t *)&_prefs->rx_delay_base, sizeof(_prefs->rx_delay_base));      // 80
    file.read((uint8_t *)&_prefs->tx_delay_factor, sizeof(_prefs->tx_delay_factor));  // 84
    file.read((uint8_t *)&_prefs->guest_password[0], sizeof(_prefs->guest_password)); // 88
    file.read((uint8_t *)&_prefs->direct_tx_delay_factor, sizeof(_prefs->direct_tx_delay_factor)); // 104
    file.read(pad, 4);                                                                             // 108
    file.read((uint8_t *)&_prefs->sf, sizeof(_prefs->sf));                                         // 112
    file.read((uint8_t *)&_prefs->cr, sizeof(_prefs->cr));                                         // 113
    file.read((uint8_t *)&_prefs->allow_read_only, sizeof(_prefs->allow_read_only));               // 114
    file.read((uint8_t *)&_prefs->multi_acks, sizeof(_prefs->multi_acks));                         // 115
    file.read((uint8_t *)&_prefs->bw, sizeof(_prefs->bw));                                         // 116
    file.read((uint8_t *)&_prefs->agc_reset_interval, sizeof(_prefs->agc_reset_interval));         // 120
    file.read((uint8_t *)&_prefs->path_hash_mode, sizeof(_prefs->path_hash_mode));                 // 121
    file.read((uint8_t *)&_prefs->loop_detect, sizeof(_prefs->loop_detect));                       // 122
    file.read(pad, 1);                                                                             // 123
    file.read((uint8_t *)&_prefs->flood_max, sizeof(_prefs->flood_max));                           // 124
    file.read((uint8_t *)&_prefs->flood_advert_interval, sizeof(_prefs->flood_advert_interval));   // 125
    file.read((uint8_t *)&_prefs->interference_threshold, sizeof(_prefs->interference_threshold)); // 126
    file.read((uint8_t *)&_prefs->bridge_enabled, sizeof(_prefs->bridge_enabled));                 // 127
    file.read((uint8_t *)&_prefs->bridge_delay, sizeof(_prefs->bridge_delay));                     // 128
    file.read((uint8_t *)&_prefs->bridge_pkt_src, sizeof(_prefs->bridge_pkt_src));                 // 130
    file.read((uint8_t *)&_prefs->bridge_baud, sizeof(_prefs->bridge_baud));                       // 131
    file.read((uint8_t *)&_prefs->bridge_channel, sizeof(_prefs->bridge_channel));                 // 135
    file.read((uint8_t *)&_prefs->bridge_secret, sizeof(_prefs->bridge_secret));                   // 136
    file.read((uint8_t *)&_prefs->powersaving_enabled, sizeof(_prefs->powersaving_enabled));       // 152
    file.read((uint8_t *)&_prefs->reboot_interval, sizeof(_prefs->reboot_interval));               // 153
    file.read(pad, 2);                                                                             // 154
    file.read((uint8_t *)&_prefs->gps_enabled, sizeof(_prefs->gps_enabled));                       // 156
    file.read((uint8_t *)&_prefs->gps_interval, sizeof(_prefs->gps_interval));                     // 157
    file.read((uint8_t *)&_prefs->advert_loc_policy, sizeof (_prefs->advert_loc_policy));          // 161
    file.read((uint8_t *)&_prefs->discovery_mod_timestamp, sizeof(_prefs->discovery_mod_timestamp)); // 162
    file.read((uint8_t *)&_prefs->adc_multiplier, sizeof(_prefs->adc_multiplier));                 // 166
    file.read((uint8_t *)_prefs->owner_info, sizeof(_prefs->owner_info));                          // 170
    // MQTT/observer settings are no longer stored in /com_prefs — they live in
    // /mqtt_prefs (loaded by loadMQTTPrefs). Old fork firmware wrote a zero-filled
    // MQTT gap here followed by a trailing observer block; detect that layout by the
    // extra length, skip the gap, and recover the tail so those settings survive
    // the upgrade (the file is rewritten in the new layout by loadPrefs afterwards).
    // Defaults for the trailing fields that older/shorter files may not contain.
    // (upstream defaults: FEM RX gain on, CAD off) — overwritten below if present.
    _prefs->radio_fem_rxgain = 1;
    _prefs->cad_enabled = 0;
    _prefs->rx_powersaving_enabled = 0;
    _prefs->rx_ps_rx_us = RX_POWERSAVING_DEFAULT_RX_US;
    _prefs->rx_ps_sleep_us = RX_POWERSAVING_DEFAULT_SLEEP_US;
    _prefs->rx_ps_level = 0;
    _prefs->rx_ps_preamble = 0;
#if defined(ENABLE_OTA)
    // OTA settings were appended after Keymind's retry/flood tail. Initialize them
    // before reading so older and legacy preference files remain conservative.
    _prefs->ota_autofetch = 0;
    _prefs->ota_autoinstall = 0;
    _prefs->ota_signer_count = 0;
    memset(_prefs->ota_signers, 0, sizeof(_prefs->ota_signers));
    _prefs->ota_checkpoint_blocks = 4;
    _prefs->ota_advert_interval = 0;
    _prefs->ota_max_hops = 3;
#endif
    _prefs->telemetry_access = TELEMETRY_ACCESS_ALL;
    _prefs->flood_retry_group_max_path = FLOOD_RETRY_GROUP_MAX_PATH_DEFAULT;
    _prefs->rx_watchdog_enabled = 0;
    _prefs->system_watchdog_enabled = 1;
    // A remainder larger than the smallest legacy MQTT gap (864) means an old fork
    // file with the zero-filled gap; detect and recover it below. Anything smaller
    // (upstream/flex 5-byte tails, or the ~384-byte keymind retry tail) takes the
    // normal path: guarded 290-294 reads, then the keymind retry tail whose absence
    // in shorter files is detected via direct_retry_prefs_magic.
    size_t extra = file.available();
    bool has_flood_retry_prefs = false;  // set when the keymind flood-retry tail is read below
    if (extra > LEGACY_MQTT_GAP_3SLOT) {
      _com_prefs_needs_upgrade = true;
      size_t gap = 0;
      if (extra > LEGACY_MQTT_GAP_6SLOT && extra <= LEGACY_MQTT_GAP_6SLOT + LEGACY_OBS_TAIL_MAX) {
        gap = LEGACY_MQTT_GAP_6SLOT;
      } else if (extra > LEGACY_MQTT_GAP_3SLOT && extra <= LEGACY_MQTT_GAP_3SLOT + LEGACY_OBS_TAIL_MAX) {
        gap = LEGACY_MQTT_GAP_3SLOT;
      }
      // Unrecognized legacy sizes (e.g. pre-slot-era files) leave gap == 0: the tail
      // is not read and everything past owner_info degrades to defaults.
      if (gap > 0) {
        uint8_t skip_buf[64];
        size_t remaining = gap;
        while (remaining > 0) {
          size_t n = remaining > sizeof(skip_buf) ? sizeof(skip_buf) : remaining;
          file.read(skip_buf, n);
          remaining -= n;
        }
        file.read((uint8_t *)&_prefs->rx_boosted_gain, sizeof(_prefs->rx_boosted_gain));
        // Tail layout: flood_max_unscoped, flood_max_advert, then the snmp fields —
        // except legacy flex-branch files where snmp starts right after
        // rx_boosted_gain (no flood_max_*). Same heuristic the old firmware used:
        // snmp_enabled is 0/1 and the first community char is printable (> 64).
        uint8_t b1 = 0, b2 = 0;
        bool have_flood_bytes = file.available() >= 2;
        if (have_flood_bytes) {
          file.read(&b1, 1);
          file.read(&b2, 1);
        }
#ifdef WITH_MQTT_BRIDGE
        // Pre-fill with the same defaults applyMQTTDefaults() uses, so fields a
        // shorter (older) tail doesn't contain degrade to defaults when applied.
        memset(&_legacy_tail, 0, sizeof(_legacy_tail));
        strncpy(_legacy_tail.snmp_community, "public", sizeof(_legacy_tail.snmp_community) - 1);
        _legacy_tail.radio_watchdog_minutes = 5;
        _legacy_tail.alert_wifi_minutes = 30;
        _legacy_tail.alert_mqtt_minutes = 240;
        _legacy_tail.alert_min_interval_min = 60;
#endif
        if (have_flood_bytes && b1 <= 1 && b2 > 64) {
          // Legacy variant: no flood_max_* — b1/b2 are snmp_enabled + community[0]
#ifdef WITH_MQTT_BRIDGE
          _legacy_tail.snmp_enabled = b1;
          _legacy_tail.snmp_community[0] = (char) b2;
          if (file.available() >= (int)(sizeof(_legacy_tail.snmp_community) - 1)) {
            file.read((uint8_t *)&_legacy_tail.snmp_community[1], sizeof(_legacy_tail.snmp_community) - 1);
          }
#endif
        } else if (have_flood_bytes) {
          _prefs->flood_max_unscoped = b1;
          _prefs->flood_max_advert = b2;
#ifdef WITH_MQTT_BRIDGE
          if (file.available() >= (int)sizeof(_legacy_tail.snmp_enabled)) {
            file.read((uint8_t *)&_legacy_tail.snmp_enabled, sizeof(_legacy_tail.snmp_enabled));
          }
          if (file.available() >= (int)sizeof(_legacy_tail.snmp_community)) {
            file.read((uint8_t *)&_legacy_tail.snmp_community, sizeof(_legacy_tail.snmp_community));
          }
#endif
        }
#ifdef WITH_MQTT_BRIDGE
        if (file.available() >= (int)sizeof(_legacy_tail.radio_watchdog_minutes)) {
          file.read((uint8_t *)&_legacy_tail.radio_watchdog_minutes, sizeof(_legacy_tail.radio_watchdog_minutes));
        }
        if (file.available() >= (int)sizeof(_legacy_tail.alert_enabled)) {
          file.read((uint8_t *)&_legacy_tail.alert_enabled, sizeof(_legacy_tail.alert_enabled));
        }
        if (file.available() >= (int)sizeof(_legacy_tail.alert_psk_hex)) {
          file.read((uint8_t *)&_legacy_tail.alert_psk_hex, sizeof(_legacy_tail.alert_psk_hex));
        }
        if (file.available() >= (int)sizeof(_legacy_tail.alert_wifi_minutes)) {
          file.read((uint8_t *)&_legacy_tail.alert_wifi_minutes, sizeof(_legacy_tail.alert_wifi_minutes));
        }
        if (file.available() >= (int)sizeof(_legacy_tail.alert_mqtt_minutes)) {
          file.read((uint8_t *)&_legacy_tail.alert_mqtt_minutes, sizeof(_legacy_tail.alert_mqtt_minutes));
        }
        if (file.available() >= (int)sizeof(_legacy_tail.alert_min_interval_min)) {
          file.read((uint8_t *)&_legacy_tail.alert_min_interval_min, sizeof(_legacy_tail.alert_min_interval_min));
        }
        if (file.available() >= (int)sizeof(_legacy_tail.alert_hashtag)) {
          file.read((uint8_t *)&_legacy_tail.alert_hashtag, sizeof(_legacy_tail.alert_hashtag));
        }
        if (file.available() >= (int)sizeof(_legacy_tail.alert_region)) {
          file.read((uint8_t *)&_legacy_tail.alert_region, sizeof(_legacy_tail.alert_region));
        }
        _legacy_tail.snmp_enabled = constrain(_legacy_tail.snmp_enabled, 0, 1);
        _legacy_tail.radio_watchdog_minutes = constrain(_legacy_tail.radio_watchdog_minutes, 0, 120);
        _legacy_tail.alert_enabled = constrain(_legacy_tail.alert_enabled, 0, 1);
        _legacy_tail.snmp_community[sizeof(_legacy_tail.snmp_community) - 1] = '\0';
        _legacy_tail.alert_psk_hex[sizeof(_legacy_tail.alert_psk_hex) - 1] = '\0';
        _legacy_tail.alert_hashtag[sizeof(_legacy_tail.alert_hashtag) - 1] = '\0';
        _legacy_tail.alert_region[sizeof(_legacy_tail.alert_region) - 1] = '\0';
        _legacy_tail.valid = true;
#endif
      }
    } else {
      if (file.available() >= (int)sizeof(_prefs->rx_boosted_gain)) {
        file.read((uint8_t *)&_prefs->rx_boosted_gain, sizeof(_prefs->rx_boosted_gain));
      }
      if (file.available() >= (int)sizeof(_prefs->flood_max_unscoped)) {
        file.read((uint8_t *)&_prefs->flood_max_unscoped, sizeof(_prefs->flood_max_unscoped));
      }
      if (file.available() >= (int)sizeof(_prefs->flood_max_advert)) {
        file.read((uint8_t *)&_prefs->flood_max_advert, sizeof(_prefs->flood_max_advert));
      }
      if (file.available() >= (int)sizeof(_prefs->radio_fem_rxgain)) {   // 293
        file.read((uint8_t *)&_prefs->radio_fem_rxgain, sizeof(_prefs->radio_fem_rxgain));
      }
      if (file.available() >= (int)sizeof(_prefs->cad_enabled)) {        // 294
        file.read((uint8_t *)&_prefs->cad_enabled, sizeof(_prefs->cad_enabled));
      }
    // next: 295
    file.read((uint8_t *)&_prefs->retry_preset, sizeof(_prefs->retry_preset));                     // 295
    file.read((uint8_t *)&_prefs->direct_retry_attempts, sizeof(_prefs->direct_retry_attempts));   // 296
    file.read((uint8_t *)&_prefs->direct_retry_base_ms, sizeof(_prefs->direct_retry_base_ms));     // 297
    file.read((uint8_t *)&_prefs->direct_retry_step_ms, sizeof(_prefs->direct_retry_step_ms));     // 299
    file.read((uint8_t *)&_prefs->direct_retry_snr_margin_x4, sizeof(_prefs->direct_retry_snr_margin_x4)); // 301
    file.read((uint8_t *)&_prefs->direct_retry_cr4_snr_x4, sizeof(_prefs->direct_retry_cr4_snr_x4)); // 303
    file.read((uint8_t *)&_prefs->direct_retry_cr5_snr_x4, sizeof(_prefs->direct_retry_cr5_snr_x4)); // 304
    file.read((uint8_t *)&_prefs->direct_retry_cr7_snr_x4, sizeof(_prefs->direct_retry_cr7_snr_x4)); // 305
    file.read((uint8_t *)&_prefs->direct_retry_cr8_snr_x4, sizeof(_prefs->direct_retry_cr8_snr_x4)); // 306
    file.read((uint8_t *)&_prefs->direct_retry_enabled, sizeof(_prefs->direct_retry_enabled));       // 307
    file.read((uint8_t *)&_prefs->direct_retry_cr_enabled, sizeof(_prefs->direct_retry_cr_enabled)); // 308
    file.read((uint8_t *)&_prefs->direct_retry_prefs_magic, sizeof(_prefs->direct_retry_prefs_magic)); // 309
    memset(_prefs->flood_retry_prefixes, 0, sizeof(_prefs->flood_retry_prefixes));
    _prefs->flood_retry_bridge_enabled = 0;
    memset(_prefs->flood_retry_bridge_buckets, 0, sizeof(_prefs->flood_retry_bridge_buckets));
    memset(_prefs->flood_retry_ignore_prefixes, 0, sizeof(_prefs->flood_retry_ignore_prefixes));
    _prefs->flood_retry_advert_enabled = FLOOD_RETRY_ADVERT_DEFAULT;
    _prefs->battery_alert_enabled = 0;
    _prefs->battery_alert_low_percent = BATTERY_ALERT_LOW_PERCENT_DEFAULT;
    _prefs->battery_alert_critical_percent = BATTERY_ALERT_CRITICAL_PERCENT_DEFAULT;
    memset(_prefs->battery_alert_region, 0, sizeof(_prefs->battery_alert_region));
    _prefs->direct_retry_recent_enabled = DIRECT_RETRY_RECENT_DEFAULT;
    _prefs->flood_channel_data_enabled = 1;
    _prefs->flood_channel_block_max_hops = FLOOD_CHANNEL_BLOCK_HOPS_ALL;
    _prefs->flood_channel_data_max_hops = FLOOD_CHANNEL_BLOCK_HOPS_ALL;
    has_flood_retry_prefs = file.available() >= 2;
    if (has_flood_retry_prefs) {
      file.read((uint8_t *)&_prefs->flood_retry_attempts, sizeof(_prefs->flood_retry_attempts));     // 311
      file.read((uint8_t *)&_prefs->flood_retry_max_path, sizeof(_prefs->flood_retry_max_path));     // 312
      if (file.available() >= (int)sizeof(_prefs->flood_retry_prefixes)) {
        file.read((uint8_t *)&_prefs->flood_retry_prefixes[0][0], sizeof(_prefs->flood_retry_prefixes));
      }
      if (file.available() >= (int)sizeof(_prefs->flood_retry_bridge_enabled)) {
        file.read((uint8_t *)&_prefs->flood_retry_bridge_enabled, sizeof(_prefs->flood_retry_bridge_enabled));
      }
      if (file.available() >= (int)sizeof(_prefs->flood_retry_bridge_buckets)) {
        file.read((uint8_t *)&_prefs->flood_retry_bridge_buckets[0][0][0], sizeof(_prefs->flood_retry_bridge_buckets));
      }
      if (file.available() >= (int)sizeof(_prefs->flood_retry_ignore_prefixes)) {
        file.read((uint8_t *)&_prefs->flood_retry_ignore_prefixes[0][0], sizeof(_prefs->flood_retry_ignore_prefixes));
      }
      if (file.available() >= (int)sizeof(_prefs->flood_retry_advert_enabled)) {
        file.read((uint8_t *)&_prefs->flood_retry_advert_enabled, sizeof(_prefs->flood_retry_advert_enabled));
      }
      if (file.available() >= (int)sizeof(_prefs->battery_alert_enabled)) {
        file.read((uint8_t *)&_prefs->battery_alert_enabled, sizeof(_prefs->battery_alert_enabled));
      }
      if (file.available() >= (int)sizeof(_prefs->battery_alert_low_percent)) {
        file.read((uint8_t *)&_prefs->battery_alert_low_percent, sizeof(_prefs->battery_alert_low_percent));
      }
      if (file.available() >= (int)sizeof(_prefs->battery_alert_critical_percent)) {
        file.read((uint8_t *)&_prefs->battery_alert_critical_percent, sizeof(_prefs->battery_alert_critical_percent));
      }
      if (file.available() >= (int)sizeof(_prefs->direct_retry_recent_enabled)) {
        file.read((uint8_t *)&_prefs->direct_retry_recent_enabled, sizeof(_prefs->direct_retry_recent_enabled));
      }
      if (file.available() >= (int)sizeof(_prefs->flood_channel_data_enabled)) {
        file.read((uint8_t *)&_prefs->flood_channel_data_enabled, sizeof(_prefs->flood_channel_data_enabled));
      }
      if (file.available() >= (int)sizeof(_prefs->flood_channel_block_max_hops)) {
        file.read((uint8_t *)&_prefs->flood_channel_block_max_hops, sizeof(_prefs->flood_channel_block_max_hops));
      }
      if (file.available() >= (int)sizeof(_prefs->flood_channel_data_max_hops)) {
        file.read((uint8_t *)&_prefs->flood_channel_data_max_hops, sizeof(_prefs->flood_channel_data_max_hops));
      }
      if (file.available() >= (int)sizeof(_prefs->telemetry_access)) {
        file.read((uint8_t *)&_prefs->telemetry_access, sizeof(_prefs->telemetry_access));
      }
    }
#if defined(ENABLE_OTA)
    // OTA config starts at 675, after telemetry_access. Guard every appended field so older files keep defaults.
    if (file.available() >= (int)sizeof(_prefs->ota_autofetch)) {
      file.read((uint8_t *)&_prefs->ota_autofetch, sizeof(_prefs->ota_autofetch));
    }
    if (file.available() >= (int)sizeof(_prefs->ota_autoinstall)) {
      file.read((uint8_t *)&_prefs->ota_autoinstall, sizeof(_prefs->ota_autoinstall));
    }
    if (file.available() >= (int)sizeof(_prefs->ota_signer_count)) {
      file.read((uint8_t *)&_prefs->ota_signer_count, sizeof(_prefs->ota_signer_count));
    }
    if (file.available() >= (int)sizeof(_prefs->ota_signers)) {
      file.read((uint8_t *)_prefs->ota_signers, sizeof(_prefs->ota_signers));
    }
    if (file.available() >= (int)sizeof(_prefs->ota_checkpoint_blocks)) {
      file.read((uint8_t *)&_prefs->ota_checkpoint_blocks, sizeof(_prefs->ota_checkpoint_blocks));
    }
    if (file.available() >= (int)sizeof(_prefs->ota_advert_interval)) {
      file.read((uint8_t *)&_prefs->ota_advert_interval, sizeof(_prefs->ota_advert_interval));
    }
    if (file.available() >= (int)sizeof(_prefs->ota_max_hops)) {
      file.read((uint8_t *)&_prefs->ota_max_hops, sizeof(_prefs->ota_max_hops));
    }
    // next: 811
#endif
    // RXPS is stored at a fixed offset after the reserved OTA tail. This keeps
    // both old OTA and non-OTA /com_prefs files unambiguous.
    bool has_rxps_tail = file.available() >= 11;
#if !defined(ENABLE_OTA)
    const size_t ota_tail_size = 136;
    const size_t padded_rxps_tail_size = sizeof(_prefs->rx_powersaving_enabled)
        + sizeof(_prefs->rx_ps_rx_us) + sizeof(_prefs->rx_ps_sleep_us)
        + sizeof(_prefs->rx_ps_level) + sizeof(_prefs->rx_ps_preamble);
    if (file.available() >= (int)(ota_tail_size + padded_rxps_tail_size)) {
      size_t remaining = ota_tail_size;
      while (remaining > 0) {
        size_t n = remaining > sizeof(pad) ? sizeof(pad) : remaining;
        file.read(pad, n);
        remaining -= n;
      }
      has_rxps_tail = true;
    } else {
      has_rxps_tail = false;
    }
#endif
    const size_t rxps_tail_size = sizeof(_prefs->rx_powersaving_enabled)
        + sizeof(_prefs->rx_ps_rx_us) + sizeof(_prefs->rx_ps_sleep_us)
        + sizeof(_prefs->rx_ps_level) + sizeof(_prefs->rx_ps_preamble);
    if (has_rxps_tail && file.available() >= (int)rxps_tail_size) {
      file.read((uint8_t *)&_prefs->rx_powersaving_enabled, sizeof(_prefs->rx_powersaving_enabled));
      file.read((uint8_t *)&_prefs->rx_ps_rx_us, sizeof(_prefs->rx_ps_rx_us));
      file.read((uint8_t *)&_prefs->rx_ps_sleep_us, sizeof(_prefs->rx_ps_sleep_us));
      file.read((uint8_t *)&_prefs->rx_ps_level, sizeof(_prefs->rx_ps_level));
      file.read((uint8_t *)&_prefs->rx_ps_preamble, sizeof(_prefs->rx_ps_preamble));
    }
    if (file.available() >= (int)sizeof(_prefs->battery_alert_region)) {
      file.read((uint8_t *)_prefs->battery_alert_region, sizeof(_prefs->battery_alert_region));
      _prefs->battery_alert_region[sizeof(_prefs->battery_alert_region) - 1] = '\0';
    }
    if (file.available() >= (int)sizeof(_prefs->flood_retry_group_max_path)) {
      file.read((uint8_t *)&_prefs->flood_retry_group_max_path,
                sizeof(_prefs->flood_retry_group_max_path));
    }
    if (file.available() >= (int)sizeof(_prefs->rx_watchdog_enabled)) {
      file.read((uint8_t *)&_prefs->rx_watchdog_enabled,
                sizeof(_prefs->rx_watchdog_enabled));
    }
    if (file.available() >= (int)sizeof(_prefs->system_watchdog_enabled)) {
      file.read((uint8_t *)&_prefs->system_watchdog_enabled,
                sizeof(_prefs->system_watchdog_enabled));
    }
    }

    // sanitise bad pref values
    _prefs->rx_delay_base = constrain(_prefs->rx_delay_base, 0, 20.0f);
    _prefs->tx_delay_factor = constrain(_prefs->tx_delay_factor, 0, 2.0f);
    _prefs->direct_tx_delay_factor = constrain(_prefs->direct_tx_delay_factor, 0, 2.0f);
    _prefs->airtime_factor = constrain(_prefs->airtime_factor, 0, 9.0f);
    _prefs->freq = constrain(_prefs->freq, 150.0f, 2500.0f);
    _prefs->bw = isValidLoRaBandwidth(_prefs->bw) ? _prefs->bw : defaultLoRaBandwidth();
    _prefs->sf = constrain(_prefs->sf, 5, 12);
    _prefs->cr = constrain(_prefs->cr, 5, 8);
    _prefs->tx_power_dbm = constrain(_prefs->tx_power_dbm, -9, 30);
    _prefs->multi_acks = constrain(_prefs->multi_acks, 0, 1);
    _prefs->adc_multiplier = constrain(_prefs->adc_multiplier, 0.0f, 10.0f);
    _prefs->path_hash_mode = constrain(_prefs->path_hash_mode, 0, 2);   // NOTE: mode 3 reserved for future
    _prefs->loop_detect = constrain(_prefs->loop_detect, 0, 3);          // LOOP_DETECT_OFF..LOOP_DETECT_STRICT

    // sanitise bad bridge pref values
    _prefs->bridge_enabled = constrain(_prefs->bridge_enabled, 0, 1);
    _prefs->bridge_delay = constrain(_prefs->bridge_delay, 0, 10000);
    _prefs->bridge_pkt_src = constrain(_prefs->bridge_pkt_src, 0, 1);
    _prefs->bridge_baud = constrain(_prefs->bridge_baud, 9600, BRIDGE_MAX_BAUD);
    _prefs->bridge_channel = constrain(_prefs->bridge_channel, 0, 14);

    _prefs->powersaving_enabled = constrain(_prefs->powersaving_enabled, 0, 1);
    _prefs->reboot_interval = constrain(_prefs->reboot_interval, 0, 255);

    _prefs->gps_enabled = constrain(_prefs->gps_enabled, 0, 1);
    _prefs->advert_loc_policy = constrain(_prefs->advert_loc_policy, 0, 2);

    _prefs->rx_boosted_gain = constrain(_prefs->rx_boosted_gain, 0, 1); // boolean
    _prefs->radio_fem_rxgain = constrain(_prefs->radio_fem_rxgain, 0, 1); // boolean
    _prefs->cad_enabled = constrain(_prefs->cad_enabled, 0, 1); // boolean
    if (!directRetryPrefsValid(_prefs)) {
      setDefaultDirectRetryPrefs(_prefs);
      memset(_prefs->flood_retry_prefixes, 0, sizeof(_prefs->flood_retry_prefixes));
      _prefs->flood_retry_bridge_enabled = 0;
      memset(_prefs->flood_retry_bridge_buckets, 0, sizeof(_prefs->flood_retry_bridge_buckets));
      memset(_prefs->flood_retry_ignore_prefixes, 0, sizeof(_prefs->flood_retry_ignore_prefixes));
      _prefs->flood_retry_advert_enabled = FLOOD_RETRY_ADVERT_DEFAULT;
    } else if (!has_flood_retry_prefs) {
      applyFloodRetryPreset(_prefs, _prefs->retry_preset);
    }
    if (_prefs->retry_preset > RETRY_PRESET_MOBILE && _prefs->retry_preset != RETRY_PRESET_CUSTOM) {
      _prefs->retry_preset = RETRY_PRESET_CUSTOM;
    }
    _prefs->direct_retry_attempts = constrain(_prefs->direct_retry_attempts, 1, 15);
    _prefs->direct_retry_base_ms = constrain(_prefs->direct_retry_base_ms, 10, 5000);
    _prefs->direct_retry_step_ms = constrain(_prefs->direct_retry_step_ms, 0, 5000);
    _prefs->direct_retry_snr_margin_x4 = constrain(_prefs->direct_retry_snr_margin_x4, 0, 160);
    _prefs->direct_retry_enabled = constrain(_prefs->direct_retry_enabled, 0, 1);
    _prefs->direct_retry_cr_enabled = constrain(_prefs->direct_retry_cr_enabled, 0, 1);
    _prefs->flood_retry_attempts = constrain(_prefs->flood_retry_attempts, 0, 15);
    if (_prefs->flood_retry_max_path != FLOOD_RETRY_PATH_GATE_DISABLED) {
      _prefs->flood_retry_max_path = constrain(_prefs->flood_retry_max_path, 0, 63);
    }
    if (_prefs->flood_retry_group_max_path != FLOOD_RETRY_PATH_GATE_DISABLED) {
      _prefs->flood_retry_group_max_path = constrain(_prefs->flood_retry_group_max_path, 0, 63);
    }
    if (_prefs->flood_retry_max_path == 0) {
      _prefs->flood_retry_group_max_path = FLOOD_RETRY_PATH_GATE_DISABLED;
    }
    _prefs->flood_retry_bridge_enabled = constrain(_prefs->flood_retry_bridge_enabled, 0, 1);
    _prefs->flood_retry_advert_enabled = constrain(_prefs->flood_retry_advert_enabled, 0, 1);
    _prefs->battery_alert_enabled = constrain(_prefs->battery_alert_enabled, 0, 1);
    _prefs->direct_retry_recent_enabled = constrain(_prefs->direct_retry_recent_enabled, 0, 1);
    _prefs->flood_channel_data_enabled = constrain(_prefs->flood_channel_data_enabled, 0, 1);
    _prefs->telemetry_access = constrain(_prefs->telemetry_access, 0, 1);
    if (_prefs->flood_channel_block_max_hops != FLOOD_CHANNEL_BLOCK_HOPS_ALL
        && (_prefs->flood_channel_block_max_hops < 1 || _prefs->flood_channel_block_max_hops > 7)) {
      _prefs->flood_channel_block_max_hops = FLOOD_CHANNEL_BLOCK_HOPS_ALL;
    }
    if (_prefs->flood_channel_data_max_hops != FLOOD_CHANNEL_BLOCK_HOPS_ALL
        && (_prefs->flood_channel_data_max_hops < 1 || _prefs->flood_channel_data_max_hops > 7)) {
      _prefs->flood_channel_data_max_hops = FLOOD_CHANNEL_BLOCK_HOPS_ALL;
    }
    if (_prefs->battery_alert_low_percent < 1
        || _prefs->battery_alert_low_percent > 100
        || _prefs->battery_alert_critical_percent >= _prefs->battery_alert_low_percent) {
      _prefs->battery_alert_low_percent = BATTERY_ALERT_LOW_PERCENT_DEFAULT;
      _prefs->battery_alert_critical_percent = BATTERY_ALERT_CRITICAL_PERCENT_DEFAULT;
    }
#if defined(ENABLE_OTA)
    _prefs->ota_autofetch = constrain(_prefs->ota_autofetch, 0, 2);
    _prefs->ota_autoinstall = constrain(_prefs->ota_autoinstall, 0, 1);
    if (_prefs->ota_checkpoint_blocks > 4096) _prefs->ota_checkpoint_blocks = 4;   // 0=never; cap absurd
    if (_prefs->ota_advert_interval > 10080) _prefs->ota_advert_interval = 1440;   // 0=off; cap at 7 days
    if (_prefs->ota_max_hops > 8) _prefs->ota_max_hops = 3;   // 0=direct only; cap absurd reach
    if (_prefs->ota_signer_count > 4) _prefs->ota_signer_count = 0;     // corrupt count -> drop keys
#endif
    _prefs->rx_powersaving_enabled = constrain(_prefs->rx_powersaving_enabled, 0, 1);
    _prefs->rx_watchdog_enabled = constrain(_prefs->rx_watchdog_enabled, 0, 1);
    _prefs->system_watchdog_enabled = constrain(_prefs->system_watchdog_enabled, 0, 1);
    _prefs->rx_ps_level = constrain(_prefs->rx_ps_level, 0, 10);
    if (_prefs->rx_ps_preamble != 16 && _prefs->rx_ps_preamble != 32) {
      _prefs->rx_ps_preamble = 0;   // 0 = auto (derive from SF)
    }
    ensureRxPowerSavingDefaults(_prefs);
    recalculateRxPowerSavingFromLevel(_prefs);   // retune level-based timings to the loaded SF/BW

    file.close();
  }
}

void CommonCLI::savePrefs(FILESYSTEM* fs) {
#if defined(NRF52_PLATFORM)
  mesh::AtomicFileWriter file(fs, "/com_prefs");
#elif defined(STM32_PLATFORM)
  fs->remove("/com_prefs");
  File file = fs->open("/com_prefs", FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
  File file = fs->open("/com_prefs", "w");
#else
  File file = fs->open("/com_prefs", "w", true);
#endif
  if (file) {
    uint8_t pad[8];
    memset(pad, 0, sizeof(pad));

    file.write((uint8_t *)&_prefs->airtime_factor, sizeof(_prefs->airtime_factor));    // 0
    file.write((uint8_t *)&_prefs->node_name, sizeof(_prefs->node_name));              // 4
    file.write(pad, 4);                                                                // 36
    file.write((uint8_t *)&_prefs->node_lat, sizeof(_prefs->node_lat));                // 40
    file.write((uint8_t *)&_prefs->node_lon, sizeof(_prefs->node_lon));                // 48
    file.write((uint8_t *)&_prefs->password[0], sizeof(_prefs->password));             // 56
    file.write((uint8_t *)&_prefs->freq, sizeof(_prefs->freq));                        // 72
    file.write((uint8_t *)&_prefs->tx_power_dbm, sizeof(_prefs->tx_power_dbm));        // 76
    file.write((uint8_t *)&_prefs->disable_fwd, sizeof(_prefs->disable_fwd));          // 77
    file.write((uint8_t *)&_prefs->advert_interval, sizeof(_prefs->advert_interval));  // 78
    file.write(pad, 1);                                                                // 79 : 1 byte unused (rx_boosted_gain moved to end)
    file.write((uint8_t *)&_prefs->rx_delay_base, sizeof(_prefs->rx_delay_base));      // 80
    file.write((uint8_t *)&_prefs->tx_delay_factor, sizeof(_prefs->tx_delay_factor));  // 84
    file.write((uint8_t *)&_prefs->guest_password[0], sizeof(_prefs->guest_password)); // 88
    file.write((uint8_t *)&_prefs->direct_tx_delay_factor, sizeof(_prefs->direct_tx_delay_factor)); // 104
    file.write(pad, 4);                                                                             // 108
    file.write((uint8_t *)&_prefs->sf, sizeof(_prefs->sf));                                         // 112
    file.write((uint8_t *)&_prefs->cr, sizeof(_prefs->cr));                                         // 113
    file.write((uint8_t *)&_prefs->allow_read_only, sizeof(_prefs->allow_read_only));               // 114
    file.write((uint8_t *)&_prefs->multi_acks, sizeof(_prefs->multi_acks));                         // 115
    file.write((uint8_t *)&_prefs->bw, sizeof(_prefs->bw));                                         // 116
    file.write((uint8_t *)&_prefs->agc_reset_interval, sizeof(_prefs->agc_reset_interval));         // 120
    file.write((uint8_t *)&_prefs->path_hash_mode, sizeof(_prefs->path_hash_mode));                 // 121
    file.write((uint8_t *)&_prefs->loop_detect, sizeof(_prefs->loop_detect));                       // 122
    file.write(pad, 1);                                                                             // 123
    file.write((uint8_t *)&_prefs->flood_max, sizeof(_prefs->flood_max));                           // 124
    file.write((uint8_t *)&_prefs->flood_advert_interval, sizeof(_prefs->flood_advert_interval));   // 125
    file.write((uint8_t *)&_prefs->interference_threshold, sizeof(_prefs->interference_threshold)); // 126
    file.write((uint8_t *)&_prefs->bridge_enabled, sizeof(_prefs->bridge_enabled));                 // 127
    file.write((uint8_t *)&_prefs->bridge_delay, sizeof(_prefs->bridge_delay));                     // 128
    file.write((uint8_t *)&_prefs->bridge_pkt_src, sizeof(_prefs->bridge_pkt_src));                 // 130
    file.write((uint8_t *)&_prefs->bridge_baud, sizeof(_prefs->bridge_baud));                       // 131
    file.write((uint8_t *)&_prefs->bridge_channel, sizeof(_prefs->bridge_channel));                 // 135
    file.write((uint8_t *)&_prefs->bridge_secret, sizeof(_prefs->bridge_secret));                   // 136
    file.write((uint8_t *)&_prefs->powersaving_enabled, sizeof(_prefs->powersaving_enabled));       // 152
    file.write((uint8_t *)&_prefs->reboot_interval, sizeof(_prefs->reboot_interval));               // 153
    file.write(pad, 2);                                                                             // 154
    file.write((uint8_t *)&_prefs->gps_enabled, sizeof(_prefs->gps_enabled));                       // 156
    file.write((uint8_t *)&_prefs->gps_interval, sizeof(_prefs->gps_interval));                     // 157
    file.write((uint8_t *)&_prefs->advert_loc_policy, sizeof(_prefs->advert_loc_policy));           // 161
    file.write((uint8_t *)&_prefs->discovery_mod_timestamp, sizeof(_prefs->discovery_mod_timestamp)); // 162
    file.write((uint8_t *)&_prefs->adc_multiplier, sizeof(_prefs->adc_multiplier));                 // 166
    file.write((uint8_t *)_prefs->owner_info, sizeof(_prefs->owner_info));                          // 170
    // MQTT/observer settings are stored in /mqtt_prefs, not here. /com_prefs
    // holds the upstream fields plus the keymind retry tail below.
    file.write((uint8_t *)&_prefs->rx_boosted_gain, sizeof(_prefs->rx_boosted_gain));               // 290
    file.write((uint8_t *)&_prefs->flood_max_unscoped, sizeof(_prefs->flood_max_unscoped));         // 291
    file.write((uint8_t *)&_prefs->flood_max_advert, sizeof(_prefs->flood_max_advert));             // 292
    file.write((uint8_t *)&_prefs->radio_fem_rxgain, sizeof(_prefs->radio_fem_rxgain));             // 293
    file.write((uint8_t *)&_prefs->cad_enabled, sizeof(_prefs->cad_enabled));                       // 294
    // next: 295
    markDirectRetryPrefsValid(_prefs);
    file.write((uint8_t *)&_prefs->retry_preset, sizeof(_prefs->retry_preset));                     // 295
    file.write((uint8_t *)&_prefs->direct_retry_attempts, sizeof(_prefs->direct_retry_attempts));   // 296
    file.write((uint8_t *)&_prefs->direct_retry_base_ms, sizeof(_prefs->direct_retry_base_ms));     // 297
    file.write((uint8_t *)&_prefs->direct_retry_step_ms, sizeof(_prefs->direct_retry_step_ms));     // 299
    file.write((uint8_t *)&_prefs->direct_retry_snr_margin_x4, sizeof(_prefs->direct_retry_snr_margin_x4)); // 301
    file.write((uint8_t *)&_prefs->direct_retry_cr4_snr_x4, sizeof(_prefs->direct_retry_cr4_snr_x4)); // 303
    file.write((uint8_t *)&_prefs->direct_retry_cr5_snr_x4, sizeof(_prefs->direct_retry_cr5_snr_x4)); // 304
    file.write((uint8_t *)&_prefs->direct_retry_cr7_snr_x4, sizeof(_prefs->direct_retry_cr7_snr_x4)); // 305
    file.write((uint8_t *)&_prefs->direct_retry_cr8_snr_x4, sizeof(_prefs->direct_retry_cr8_snr_x4)); // 306
    file.write((uint8_t *)&_prefs->direct_retry_enabled, sizeof(_prefs->direct_retry_enabled));       // 307
    file.write((uint8_t *)&_prefs->direct_retry_cr_enabled, sizeof(_prefs->direct_retry_cr_enabled)); // 308
    file.write((uint8_t *)&_prefs->direct_retry_prefs_magic, sizeof(_prefs->direct_retry_prefs_magic)); // 309
    file.write((uint8_t *)&_prefs->flood_retry_attempts, sizeof(_prefs->flood_retry_attempts));       // 311
    file.write((uint8_t *)&_prefs->flood_retry_max_path, sizeof(_prefs->flood_retry_max_path));       // 312
    file.write((uint8_t *)&_prefs->flood_retry_prefixes[0][0], sizeof(_prefs->flood_retry_prefixes)); // 313
    file.write((uint8_t *)&_prefs->flood_retry_bridge_enabled, sizeof(_prefs->flood_retry_bridge_enabled));
    file.write((uint8_t *)&_prefs->flood_retry_bridge_buckets[0][0][0], sizeof(_prefs->flood_retry_bridge_buckets));
    file.write((uint8_t *)&_prefs->flood_retry_ignore_prefixes[0][0], sizeof(_prefs->flood_retry_ignore_prefixes));
    file.write((uint8_t *)&_prefs->flood_retry_advert_enabled, sizeof(_prefs->flood_retry_advert_enabled));
    file.write((uint8_t *)&_prefs->battery_alert_enabled, sizeof(_prefs->battery_alert_enabled));
    file.write((uint8_t *)&_prefs->battery_alert_low_percent, sizeof(_prefs->battery_alert_low_percent));
    file.write((uint8_t *)&_prefs->battery_alert_critical_percent, sizeof(_prefs->battery_alert_critical_percent));
    file.write((uint8_t *)&_prefs->direct_retry_recent_enabled, sizeof(_prefs->direct_retry_recent_enabled));
    file.write((uint8_t *)&_prefs->flood_channel_data_enabled, sizeof(_prefs->flood_channel_data_enabled));
    file.write((uint8_t *)&_prefs->flood_channel_block_max_hops, sizeof(_prefs->flood_channel_block_max_hops));
    file.write((uint8_t *)&_prefs->flood_channel_data_max_hops, sizeof(_prefs->flood_channel_data_max_hops));
    file.write((uint8_t *)&_prefs->telemetry_access, sizeof(_prefs->telemetry_access));             // 674
#if defined(ENABLE_OTA)
    file.write((uint8_t *)&_prefs->ota_autofetch, sizeof(_prefs->ota_autofetch));                   // 675
    file.write((uint8_t *)&_prefs->ota_autoinstall, sizeof(_prefs->ota_autoinstall));               // 676
    file.write((uint8_t *)&_prefs->ota_signer_count, sizeof(_prefs->ota_signer_count));             // 677
    file.write((uint8_t *)_prefs->ota_signers, sizeof(_prefs->ota_signers));                        // 678
    file.write((uint8_t *)&_prefs->ota_checkpoint_blocks, sizeof(_prefs->ota_checkpoint_blocks));   // 806
    file.write((uint8_t *)&_prefs->ota_advert_interval, sizeof(_prefs->ota_advert_interval));       // 808
    file.write((uint8_t *)&_prefs->ota_max_hops, sizeof(_prefs->ota_max_hops));                     // 810
    // next: 811
#else
    // Reserve the OTA tail so RXPS has the same offset in every build. Write
    // OTA's normal defaults so a later OTA-enabled firmware reads sane values.
    file.write(pad, 3);   // autofetch, autoinstall, signer_count
    for (size_t remaining = 128; remaining > 0; ) {
      size_t n = remaining > sizeof(pad) ? sizeof(pad) : remaining;
      file.write(pad, n);
      remaining -= n;
    }
    const uint16_t ota_checkpoint_blocks = 4;
    const uint16_t ota_advert_interval = 0;
    const uint8_t ota_max_hops = 3;
    file.write((uint8_t *)&ota_checkpoint_blocks, sizeof(ota_checkpoint_blocks));
    file.write((uint8_t *)&ota_advert_interval, sizeof(ota_advert_interval));
    file.write((uint8_t *)&ota_max_hops, sizeof(ota_max_hops));
#endif
    file.write((uint8_t *)&_prefs->rx_powersaving_enabled, sizeof(_prefs->rx_powersaving_enabled)); // 811
    file.write((uint8_t *)&_prefs->rx_ps_rx_us, sizeof(_prefs->rx_ps_rx_us));                       // 812
    file.write((uint8_t *)&_prefs->rx_ps_sleep_us, sizeof(_prefs->rx_ps_sleep_us));                 // 816
    file.write((uint8_t *)&_prefs->rx_ps_level, sizeof(_prefs->rx_ps_level));                       // 820
    file.write((uint8_t *)&_prefs->rx_ps_preamble, sizeof(_prefs->rx_ps_preamble));                 // 821
    file.write((uint8_t *)_prefs->battery_alert_region, sizeof(_prefs->battery_alert_region));      // 822
    file.write((uint8_t *)&_prefs->flood_retry_group_max_path,
               sizeof(_prefs->flood_retry_group_max_path));                                        // 853
    file.write((uint8_t *)&_prefs->rx_watchdog_enabled,
               sizeof(_prefs->rx_watchdog_enabled));                                               // 854
    file.write((uint8_t *)&_prefs->system_watchdog_enabled,
               sizeof(_prefs->system_watchdog_enabled));                                           // 855
    // next: 856

#if defined(NRF52_PLATFORM)
    if (!file.commit()) {
      MESH_DEBUG_PRINTLN("ERROR: savePrefs atomic commit failed");
    }
#else
    file.close();
#endif
  }
#ifdef WITH_MQTT_BRIDGE
  // Observer config (MQTT/WiFi/timezone/SNMP/alert) is persisted separately. The
  // observer CLI writes _mqtt_prefs directly, so no NodePrefs->MQTTPrefs sync runs.
  saveMQTTPrefs(fs);
#endif
}

#ifdef WITH_MQTT_BRIDGE
// Set default values for MQTT preferences (used when file doesn't exist or is corrupted)
static void setMQTTPrefsDefaults(MQTTPrefs* prefs) {
  applyMQTTDefaults(prefs);
}

static File openMqttPrefsRead(FILESYSTEM* fs) {
#if defined(RP2040_PLATFORM)
  return fs->open("/mqtt_prefs", "r");
#else
  return fs->open("/mqtt_prefs");
#endif
}

void CommonCLI::loadMQTTPrefs(FILESYSTEM* fs) {
  // Initialize with defaults first
  setMQTTPrefsDefaults(&_mqtt_prefs);
  _mqtt_prefs_hold = false;

  // Whether the loaded /mqtt_prefs already contained the observer fields (snmp/
  // watchdog/alert) appended in Phase 2 — if not, they may be carried over from an
  // old-format /com_prefs trailing block below.
  bool has_observer_fields = false;

  bool file_existed = fs->exists("/mqtt_prefs");
  if (file_existed) {
    // First, peek the header to see if this is a versioned file.
    File file = openMqttPrefsRead(fs);
    bool versioned = false;
    if (file) {
      size_t file_size = file.size();
      if (file_size >= sizeof(MQTTPrefsHeader)) {
        MQTTPrefsHeader hdr;
        if (file.read((uint8_t *)&hdr, sizeof(hdr)) == sizeof(hdr)
            && memcmp(hdr.magic, MQTT_PREFS_MAGIC, sizeof(hdr.magic)) == 0) {
          versioned = true;
          if (hdr.version == MQTT_PREFS_VERSION) {
            // Current version: the payload follows the header. Read up to
            // sizeof(MQTTPrefs); a shorter payload (an earlier v1 firmware that
            // hadn't appended a tail field) leaves the trailing fields at their
            // defaults, and a longer one (a future append) is truncated harmlessly.
            size_t payload_avail = file_size - sizeof(hdr);
            if (hdr.payload_len < payload_avail) payload_avail = hdr.payload_len;
            size_t to_read = payload_avail < sizeof(_mqtt_prefs) ? payload_avail : sizeof(_mqtt_prefs);
            size_t got = file.read((uint8_t *)&_mqtt_prefs, to_read);
            if (got != to_read) {
              setMQTTPrefsDefaults(&_mqtt_prefs);
            } else {
              const size_t observer_tail_end = offsetof(MQTTPrefs, alert_region)
                  + sizeof(_mqtt_prefs.alert_region);
              has_observer_fields = to_read >= observer_tail_end;
            }
          } else {
            // Unknown (newer) version: don't risk misreading a layout we don't know.
            // Keep defaults for this boot and hold the file so later savePrefs()
            // calls can't overwrite the newer config (no downgrade).
            _mqtt_prefs_hold = true;
            MESH_DEBUG_PRINTLN("MQTT: /mqtt_prefs version unsupported, using defaults (file preserved)");
          }
        }
      }
      file.close();
    }

    if (!versioned) {
      // Headerless (legacy) file. Detect the historical on-disk layout by size,
      // migrate it into the compact versioned struct, and re-save — which adds the
      // header and drops the vestigial `_legacy_*` fields. Reopen because the peek
      // above advanced the read cursor past the (non-matching) leading bytes.
      File file = openMqttPrefsRead(fs);
      if (file) {
        size_t file_size = file.size();

        // Detect old (pre-slot) format by file size.
        // Old MQTTPrefs was ~472 bytes (no slot fields).
        // If the file is smaller than the new struct but close to OldMQTTPrefs size,
        // read it with the old layout and migrate.
        if (file_size > 0 && file_size <= sizeof(OldMQTTPrefs)) {
          OldMQTTPrefs old_prefs;
          memset(&old_prefs, 0, sizeof(old_prefs));
          size_t bytes_read = file.read((uint8_t *)&old_prefs, file_size < sizeof(old_prefs) ? file_size : sizeof(old_prefs));
          file.close();

          if (bytes_read > 0) {
            MESH_DEBUG_PRINTLN("MQTT: Migrating old-format prefs to versioned layout");

            // Copy common fields (identical layout at start of both structs)
            memcpy(_mqtt_prefs.mqtt_origin, old_prefs.mqtt_origin, sizeof(_mqtt_prefs.mqtt_origin));
            memcpy(_mqtt_prefs.mqtt_iata, old_prefs.mqtt_iata, sizeof(_mqtt_prefs.mqtt_iata));
            _mqtt_prefs.mqtt_status_enabled = old_prefs.mqtt_status_enabled;
            _mqtt_prefs.mqtt_packets_enabled = old_prefs.mqtt_packets_enabled;
            _mqtt_prefs.mqtt_raw_enabled = old_prefs.mqtt_raw_enabled;
            _mqtt_prefs.mqtt_tx_enabled = old_prefs.mqtt_tx_enabled;
            _mqtt_prefs.mqtt_status_interval = old_prefs.mqtt_status_interval;
            memcpy(_mqtt_prefs.wifi_ssid, old_prefs.wifi_ssid, sizeof(_mqtt_prefs.wifi_ssid));
            memcpy(_mqtt_prefs.wifi_password, old_prefs.wifi_password, sizeof(_mqtt_prefs.wifi_password));
            _mqtt_prefs.wifi_power_save = old_prefs.wifi_power_save;
            memcpy(_mqtt_prefs.timezone_string, old_prefs.timezone_string, sizeof(_mqtt_prefs.timezone_string));
            _mqtt_prefs.timezone_offset = old_prefs.timezone_offset;

            // Migrate shared auth fields
            memcpy(_mqtt_prefs.mqtt_owner_public_key, old_prefs.mqtt_owner_public_key, sizeof(_mqtt_prefs.mqtt_owner_public_key));
            memcpy(_mqtt_prefs.mqtt_email, old_prefs.mqtt_email, sizeof(_mqtt_prefs.mqtt_email));

            // Migrate analyzer presets to slots
            if (old_prefs.mqtt_analyzer_us_enabled == 1) {
              strncpy(_mqtt_prefs.mqtt_slot_preset[0], "analyzer-us", sizeof(_mqtt_prefs.mqtt_slot_preset[0]) - 1);
            } else {
              strncpy(_mqtt_prefs.mqtt_slot_preset[0], "none", sizeof(_mqtt_prefs.mqtt_slot_preset[0]) - 1);
            }
            if (old_prefs.mqtt_analyzer_eu_enabled == 1) {
              strncpy(_mqtt_prefs.mqtt_slot_preset[1], "analyzer-eu", sizeof(_mqtt_prefs.mqtt_slot_preset[1]) - 1);
            } else {
              strncpy(_mqtt_prefs.mqtt_slot_preset[1], "none", sizeof(_mqtt_prefs.mqtt_slot_preset[1]) - 1);
            }

            // Migrate custom server to slot 3
            if (old_prefs.mqtt_server[0] != '\0' && old_prefs.mqtt_port > 0) {
              strncpy(_mqtt_prefs.mqtt_slot_preset[2], "custom", sizeof(_mqtt_prefs.mqtt_slot_preset[2]) - 1);
              strncpy(_mqtt_prefs.mqtt_slot_host[2], old_prefs.mqtt_server, sizeof(_mqtt_prefs.mqtt_slot_host[2]) - 1);
              _mqtt_prefs.mqtt_slot_port[2] = old_prefs.mqtt_port;
              strncpy(_mqtt_prefs.mqtt_slot_username[2], old_prefs.mqtt_username, sizeof(_mqtt_prefs.mqtt_slot_username[2]) - 1);
              strncpy(_mqtt_prefs.mqtt_slot_password[2], old_prefs.mqtt_password, sizeof(_mqtt_prefs.mqtt_slot_password[2]) - 1);
            } else {
              strncpy(_mqtt_prefs.mqtt_slot_preset[2], "none", sizeof(_mqtt_prefs.mqtt_slot_preset[2]) - 1);
            }

            // Save migrated prefs in the versioned format
            saveMQTTPrefs(fs);
          }
        } else if (file_size > 0 && file_size <= sizeof(ThreeSlotMQTTPrefs)) {
          // 3-slot format → compact 6-slot migration
          // Array sizes changed from [3] to [6], shifting all field offsets.
          // Read into old layout struct and field-copy to new layout.
          ThreeSlotMQTTPrefs old3;
          memset(&old3, 0, sizeof(old3));
          size_t bytes_to_read = file_size < sizeof(old3) ? file_size : sizeof(old3);
          size_t bytes_read = file.read((uint8_t *)&old3, bytes_to_read);
          file.close();

          if (bytes_read > 0) {
            MESH_DEBUG_PRINTLN("MQTT: Migrating 3-slot prefs to versioned layout");

            // Copy non-slot fields (identical layout)
            memcpy(_mqtt_prefs.mqtt_origin, old3.mqtt_origin, sizeof(_mqtt_prefs.mqtt_origin));
            memcpy(_mqtt_prefs.mqtt_iata, old3.mqtt_iata, sizeof(_mqtt_prefs.mqtt_iata));
            _mqtt_prefs.mqtt_status_enabled = old3.mqtt_status_enabled;
            _mqtt_prefs.mqtt_packets_enabled = old3.mqtt_packets_enabled;
            _mqtt_prefs.mqtt_raw_enabled = old3.mqtt_raw_enabled;
            _mqtt_prefs.mqtt_tx_enabled = old3.mqtt_tx_enabled;
            _mqtt_prefs.mqtt_status_interval = old3.mqtt_status_interval;
            memcpy(_mqtt_prefs.wifi_ssid, old3.wifi_ssid, sizeof(_mqtt_prefs.wifi_ssid));
            memcpy(_mqtt_prefs.wifi_password, old3.wifi_password, sizeof(_mqtt_prefs.wifi_password));
            _mqtt_prefs.wifi_power_save = old3.wifi_power_save;
            memcpy(_mqtt_prefs.timezone_string, old3.timezone_string, sizeof(_mqtt_prefs.timezone_string));
            _mqtt_prefs.timezone_offset = old3.timezone_offset;

            // Copy slot fields for indices 0-2 from old layout
            for (int i = 0; i < 3; i++) {
              memcpy(_mqtt_prefs.mqtt_slot_preset[i], old3.mqtt_slot_preset[i], sizeof(_mqtt_prefs.mqtt_slot_preset[i]));
              memcpy(_mqtt_prefs.mqtt_slot_host[i], old3.mqtt_slot_host[i], sizeof(_mqtt_prefs.mqtt_slot_host[i]));
              _mqtt_prefs.mqtt_slot_port[i] = old3.mqtt_slot_port[i];
              memcpy(_mqtt_prefs.mqtt_slot_username[i], old3.mqtt_slot_username[i], sizeof(_mqtt_prefs.mqtt_slot_username[i]));
              memcpy(_mqtt_prefs.mqtt_slot_password[i], old3.mqtt_slot_password[i], sizeof(_mqtt_prefs.mqtt_slot_password[i]));
              memcpy(_mqtt_prefs.mqtt_slot_token[i], old3.mqtt_slot_token[i], sizeof(_mqtt_prefs.mqtt_slot_token[i]));
              memcpy(_mqtt_prefs.mqtt_slot_topic[i], old3.mqtt_slot_topic[i], sizeof(_mqtt_prefs.mqtt_slot_topic[i]));
            }
            // Slots 3-5 keep defaults ("none") from setMQTTPrefsDefaults()

            // Copy shared auth fields
            memcpy(_mqtt_prefs.mqtt_owner_public_key, old3.mqtt_owner_public_key, sizeof(_mqtt_prefs.mqtt_owner_public_key));
            memcpy(_mqtt_prefs.mqtt_email, old3.mqtt_email, sizeof(_mqtt_prefs.mqtt_email));

            // Save migrated prefs in the versioned format
            saveMQTTPrefs(fs);
          }
        } else if (file_size > 0) {
          // Headerless 6-slot layout as shipped on mqtt-bridge-implementation-flex
          // (the deployed fleet). Same field order as the compact struct but with the
          // vestigial `_legacy_*` block mid-struct and no observer tail — so read it
          // into Legacy6SlotMQTTPrefs and field-copy across, dropping `_legacy_*`.
          Legacy6SlotMQTTPrefs old6;
          memset(&old6, 0, sizeof(old6));
          size_t bytes_to_read = file_size < sizeof(old6) ? file_size : sizeof(old6);
          size_t bytes_read = file.read((uint8_t *)&old6, bytes_to_read);
          file.close();

          if (bytes_read > 0) {
            MESH_DEBUG_PRINTLN("MQTT: Migrating headerless 6-slot prefs to versioned layout");

            memcpy(_mqtt_prefs.mqtt_origin, old6.mqtt_origin, sizeof(_mqtt_prefs.mqtt_origin));
            memcpy(_mqtt_prefs.mqtt_iata, old6.mqtt_iata, sizeof(_mqtt_prefs.mqtt_iata));
            _mqtt_prefs.mqtt_status_enabled = old6.mqtt_status_enabled;
            _mqtt_prefs.mqtt_packets_enabled = old6.mqtt_packets_enabled;
            _mqtt_prefs.mqtt_raw_enabled = old6.mqtt_raw_enabled;
            _mqtt_prefs.mqtt_tx_enabled = old6.mqtt_tx_enabled;
            _mqtt_prefs.mqtt_status_interval = old6.mqtt_status_interval;
            memcpy(_mqtt_prefs.wifi_ssid, old6.wifi_ssid, sizeof(_mqtt_prefs.wifi_ssid));
            memcpy(_mqtt_prefs.wifi_password, old6.wifi_password, sizeof(_mqtt_prefs.wifi_password));
            _mqtt_prefs.wifi_power_save = old6.wifi_power_save;
            memcpy(_mqtt_prefs.timezone_string, old6.timezone_string, sizeof(_mqtt_prefs.timezone_string));
            _mqtt_prefs.timezone_offset = old6.timezone_offset;
            memcpy(_mqtt_prefs.mqtt_slot_preset, old6.mqtt_slot_preset, sizeof(_mqtt_prefs.mqtt_slot_preset));
            memcpy(_mqtt_prefs.mqtt_slot_host, old6.mqtt_slot_host, sizeof(_mqtt_prefs.mqtt_slot_host));
            memcpy(_mqtt_prefs.mqtt_slot_port, old6.mqtt_slot_port, sizeof(_mqtt_prefs.mqtt_slot_port));
            memcpy(_mqtt_prefs.mqtt_slot_username, old6.mqtt_slot_username, sizeof(_mqtt_prefs.mqtt_slot_username));
            memcpy(_mqtt_prefs.mqtt_slot_password, old6.mqtt_slot_password, sizeof(_mqtt_prefs.mqtt_slot_password));
            memcpy(_mqtt_prefs.mqtt_owner_public_key, old6.mqtt_owner_public_key, sizeof(_mqtt_prefs.mqtt_owner_public_key));
            memcpy(_mqtt_prefs.mqtt_email, old6.mqtt_email, sizeof(_mqtt_prefs.mqtt_email));
            // `_legacy_*` fields are intentionally dropped here.
            memcpy(_mqtt_prefs.mqtt_slot_token, old6.mqtt_slot_token, sizeof(_mqtt_prefs.mqtt_slot_token));
            memcpy(_mqtt_prefs.mqtt_slot_topic, old6.mqtt_slot_topic, sizeof(_mqtt_prefs.mqtt_slot_topic));
            memcpy(_mqtt_prefs.mqtt_slot_audience, old6.mqtt_slot_audience, sizeof(_mqtt_prefs.mqtt_slot_audience));
            _mqtt_prefs.mqtt_rx_enabled = old6.mqtt_rx_enabled;
            memcpy(_mqtt_prefs.mqtt_ntp_server, old6.mqtt_ntp_server, sizeof(_mqtt_prefs.mqtt_ntp_server));
            // Observer tail (snmp/watchdog/alert) keeps defaults; if this device is
            // also upgrading across the NodePrefs split, loadPrefsInt captured those
            // values from /com_prefs and they are applied below.

            saveMQTTPrefs(fs);
          }
        } else {
          file.close();
          setMQTTPrefsDefaults(&_mqtt_prefs);
        }
      }
    }
  } else {
    // No /mqtt_prefs file — defaults already set. (MQTT slot/WiFi settings from
    // pre-/mqtt_prefs-split fork firmware are NOT recovered from /com_prefs — that
    // offset-based migration was fragile and was removed; those users re-enter
    // their MQTT config. The observer trailing block IS recovered, below.)
  }

  // One-time upgrade path: if loadPrefsInt captured the trailing observer block of
  // an old-format /com_prefs and this /mqtt_prefs predates the appended observer
  // fields (or doesn't exist), carry the settings over so SNMP, radio-watchdog and
  // fault-alert config survive the firmware upgrade. loadPrefs() persists both
  // files in the new layout right after this.
  if (_legacy_tail.valid && !has_observer_fields) {
    _mqtt_prefs.snmp_enabled = _legacy_tail.snmp_enabled;
    memcpy(_mqtt_prefs.snmp_community, _legacy_tail.snmp_community, sizeof(_mqtt_prefs.snmp_community));
    _mqtt_prefs.radio_watchdog_minutes = _legacy_tail.radio_watchdog_minutes;
    _mqtt_prefs.alert_enabled = _legacy_tail.alert_enabled;
    memcpy(_mqtt_prefs.alert_psk_hex, _legacy_tail.alert_psk_hex, sizeof(_mqtt_prefs.alert_psk_hex));
    _mqtt_prefs.alert_wifi_minutes = _legacy_tail.alert_wifi_minutes;
    _mqtt_prefs.alert_mqtt_minutes = _legacy_tail.alert_mqtt_minutes;
    _mqtt_prefs.alert_min_interval_min = _legacy_tail.alert_min_interval_min;
    memcpy(_mqtt_prefs.alert_hashtag, _legacy_tail.alert_hashtag, sizeof(_mqtt_prefs.alert_hashtag));
    memcpy(_mqtt_prefs.alert_region, _legacy_tail.alert_region, sizeof(_mqtt_prefs.alert_region));
    MESH_DEBUG_PRINTLN("MQTT: Migrated observer settings from legacy /com_prefs trailing block");
  }
  _legacy_tail.valid = false;
}

void CommonCLI::saveMQTTPrefs(FILESYSTEM* fs) {
  if (_mqtt_prefs_hold) {
    // /mqtt_prefs was written by newer firmware; overwriting it here (v1 header +
    // this boot's defaults) would destroy that config. Observer settings changed
    // this boot are not persisted until current-or-older firmware is flashed.
    MESH_DEBUG_PRINTLN("MQTT: /mqtt_prefs from newer firmware, not overwriting");
    return;
  }
#if defined(NRF52_PLATFORM)
  mesh::AtomicFileWriter file(fs, "/mqtt_prefs");
#elif defined(STM32_PLATFORM)
  fs->remove("/mqtt_prefs");
  File file = fs->open("/mqtt_prefs", FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
  File file = fs->open("/mqtt_prefs", "w");
#else
  File file = fs->open("/mqtt_prefs", "w", true);
#endif
  if (file) {
    // Versioned format: 8-byte header followed by the raw MQTTPrefs payload.
    MQTTPrefsHeader hdr;
    memcpy(hdr.magic, MQTT_PREFS_MAGIC, sizeof(hdr.magic));
    hdr.version = MQTT_PREFS_VERSION;
    hdr.payload_len = (uint16_t)sizeof(_mqtt_prefs);
    file.write((uint8_t *)&hdr, sizeof(hdr));
    file.write((uint8_t *)&_mqtt_prefs, sizeof(_mqtt_prefs));
#if defined(NRF52_PLATFORM)
    if (!file.commit()) {
      MESH_DEBUG_PRINTLN("ERROR: saveMQTTPrefs atomic commit failed");
    }
#else
    file.close();
#endif
  }
}

#endif

#define MIN_LOCAL_ADVERT_INTERVAL   60

void CommonCLI::savePrefs() {
  uint8_t old_advert_interval = _prefs->advert_interval;
  if (_prefs->advert_interval * 2 < MIN_LOCAL_ADVERT_INTERVAL) {
    _prefs->advert_interval = 0;  // turn it off, now that device has been manually configured
  }
  // If advert_interval was changed, update the timer to reflect the change
  if (old_advert_interval != _prefs->advert_interval) {
    _callbacks->updateAdvertTimer();
  }
  _callbacks->savePrefs();
}

uint8_t CommonCLI::buildAdvertData(uint8_t node_type, uint8_t* app_data) {
  if (_prefs->advert_loc_policy == ADVERT_LOC_NONE) {
    AdvertDataBuilder builder(node_type, _prefs->node_name);
    return builder.encodeTo(app_data);
  } else if (_prefs->advert_loc_policy == ADVERT_LOC_SHARE) {
    AdvertDataBuilder builder(node_type, _prefs->node_name, _sensors->node_lat, _sensors->node_lon);
    return builder.encodeTo(app_data);
  } else {
    AdvertDataBuilder builder(node_type, _prefs->node_name, _prefs->node_lat, _prefs->node_lon);
    return builder.encodeTo(app_data);
  }
}

void CommonCLI::handleCommand(uint32_t sender_timestamp, char* command, char* reply) {
    mesh::cli::normalizeCommandVerb(command);

    // Observer-only top-level commands (ota check/update, tls.bundletest, alert test)
    // live in CommonCLI_Observer.cpp.
    if (handleObserverCommand(sender_timestamp, command, reply)) return;
    if (memcmp(command, "poweroff", 8) == 0 || memcmp(command, "shutdown", 8) == 0) {
      _board->powerOff();  // doesn't return
    } else if (memcmp(command, "reboot", 6) == 0) {
      _board->reboot();  // doesn't return
    } else if (sender_timestamp == 0 && memcmp(command, "uf2reset", 8) == 0 && (command[8] == 0 || command[8] == ' ')) {
#if defined(NRF52_PLATFORM)
      resetToUf2Bootloader();  // doesn't return
#else
      strcpy(reply, "ERR: unsupported");
#endif
    } else if (memcmp(command, "clkreboot", 9) == 0) {
      // Reset clock
      getRTCClock()->setCurrentTime(1715770351);  // 15 May 2024, 8:50pm
      _board->reboot();  // doesn't return
    } else if (memcmp(command, "advert.zerohop", 14) == 0 && (command[14] == 0 || command[14] == ' ')) {
      // send zerohop advert
      _callbacks->sendSelfAdvertisement(1500, false);  // longer delay, give CLI response time to be sent first
      strcpy(reply, "OK - zerohop advert sent");
    } else if (memcmp(command, "advert", 6) == 0) {
      // send flood advert
      _callbacks->sendSelfAdvertisement(1500, true);  // longer delay, give CLI response time to be sent first
      strcpy(reply, "OK - Advert sent");
    } else if (memcmp(command, "clock sync", 10) == 0) {
      uint32_t curr = getRTCClock()->getCurrentTime();
      if (sender_timestamp > curr) {
        getRTCClock()->setCurrentTime(sender_timestamp + 1);
        _callbacks->onManualClockSet();
        uint32_t now = getRTCClock()->getCurrentTime();
        DateTime dt = DateTime(now);
        sprintf(reply, "OK - clock set: %02d:%02d - %d/%d/%d UTC", dt.hour(), dt.minute(), dt.day(), dt.month(), dt.year());
      } else {
        strcpy(reply, "ERR: clock cannot go backwards");
      }
#if defined(ESP_PLATFORM) && defined(ADMIN_PASSWORD) && !defined(WEBCONFIG_DISABLED)
    } else if (memcmp(command, "start webconfig", 15) == 0
               && (command[15] == 0 || command[15] == ' ')) {
      const bool force_ap = command[15] == ' ' && strcmp(&command[16], "ap") == 0;
      if (command[15] == ' ' && !force_ap) {
        strcpy(reply, "ERR: usage start webconfig [ap]");
      } else if (!_callbacks->startWebConfig(force_ap, reply)) {
        strcpy(reply, "ERR: webconfig not supported on this build");
      }
    } else if (strcmp(command, "stop webconfig") == 0) {
      if (!_callbacks->stopWebConfig(reply)) {
        strcpy(reply, "ERR: webconfig not supported on this build");
      }
#endif
#ifdef ESP_PLATFORM
    } else if (memcmp(command, "memory", 6) == 0) {
      sprintf(reply, "Free: %d, Min: %d, Max: %d, Queue: %d, IntFree: %d, IntMax: %d, PSRAM: %d/%d",
              ESP.getFreeHeap(), ESP.getMinFreeHeap(), ESP.getMaxAllocHeap(),
              _callbacks->getQueueSize(),
              (int)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
              (int)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
              (int)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
              (int)heap_caps_get_total_size(MALLOC_CAP_SPIRAM));
#endif
    } else if (memcmp(command, "start ota", 9) == 0 && (command[9] == 0 || command[9] == ' ')) {
      // Manual OTA: bring up the board's browser server for a hand-uploaded binary.
#if defined(ESP_PLATFORM) && defined(ADMIN_PASSWORD) && !defined(WEBCONFIG_DISABLED)
      if (_callbacks->isWebConfigActive()) {
        strcpy(reply, "ERR: stop webconfig first");
      } else
#endif
      if (!_board->startOTAUpdate(_prefs->node_name, reply)) {
        strcpy(reply, "Error");
      }
#if defined(WITH_MQTT_BRIDGE) && defined(LIGHTWEIGHT_WIFI_OTA)
      else {
        // Keep WiFi up, but release MQTT/TLS heap while the browser uploader runs.
        _callbacks->setBridgeState(false);
      }
#endif
    } else if (memcmp(command, "stop ota", 8) == 0 && (command[8] == 0 || command[8] == ' ')) {
      if (!_board->stopOTAUpdate(reply)) {
        strcpy(reply, "Error");
      }
#if defined(WITH_MQTT_BRIDGE) && defined(LIGHTWEIGHT_WIFI_OTA)
      else if (_prefs->bridge_enabled) {
        _callbacks->setBridgeState(true);
      }
#endif
    } else if (memcmp(command, "clock", 5) == 0) {
      uint32_t now = getRTCClock()->getCurrentTime();
      DateTime dt = DateTime(now);
      sprintf(reply, "%02d:%02d - %d/%d/%d UTC", dt.hour(), dt.minute(), dt.day(), dt.month(), dt.year());
    } else if (memcmp(command, "time ", 5) == 0) {  // set time (to epoch seconds)
      uint32_t secs = _atoi(&command[5]);
      uint32_t curr = getRTCClock()->getCurrentTime();
      if (secs > curr) {
        getRTCClock()->setCurrentTime(secs);
        _callbacks->onManualClockSet();
        uint32_t now = getRTCClock()->getCurrentTime();
        DateTime dt = DateTime(now);
        sprintf(reply, "OK - clock set: %02d:%02d - %d/%d/%d UTC", dt.hour(), dt.minute(), dt.day(), dt.month(), dt.year());
      } else {
        strcpy(reply, "(ERR: clock cannot go backwards)");
      }
    } else if (memcmp(command, "neighbors", 9) == 0) {
      _callbacks->formatNeighborsReply(reply);
    } else if (memcmp(command, "neighbor.remove ", 16) == 0) {
      const char* hex = &command[16];
      uint8_t pubkey[PUB_KEY_SIZE];
      size_t hex_len = strlen(hex);
      int pubkey_len = (int)(hex_len / 2);
      if (hex_len > 0 && hex_len <= PUB_KEY_SIZE * 2 && (hex_len & 1) == 0
          && mesh::Utils::fromHex(pubkey, pubkey_len, hex)) {
        _callbacks->removeNeighbor(pubkey, pubkey_len);
        strcpy(reply, "OK");
      } else {
        strcpy(reply, "ERR: bad pubkey");
      }
    } else if (memcmp(command, "tempradio ", 10) == 0) {
      strcpy(tmp, &command[10]);
      const char *parts[5];
      int num = mesh::Utils::parseTextParts(tmp, parts, 5);
      float freq  = num > 0 ? strtof(parts[0], nullptr) : 0.0f;
      float bw    = num > 1 ? strtof(parts[1], nullptr) : 0.0f;
      uint8_t sf  = num > 2 ? atoi(parts[2]) : 0;
      uint8_t cr  = num > 3 ? atoi(parts[3]) : 0;
      int temp_timeout_mins  = num > 4 ? atoi(parts[4]) : 0;
      if (freq >= 150.0f && freq <= 2500.0f && sf >= 5 && sf <= 12 && cr >= 5 && cr <= 8 && isValidLoRaBandwidth(bw) && temp_timeout_mins > 0) {
        _callbacks->applyTempRadioParams(freq, bw, sf, cr, temp_timeout_mins);
        sprintf(reply, "OK - temp params for %d mins", temp_timeout_mins);
      } else {
        strcpy(reply, "Error, invalid params");
      }
    } else if (memcmp(command, "password ", 9) == 0) {
      // change admin password
      StrHelper::strncpy(_prefs->password, &command[9], sizeof(_prefs->password));
      savePrefs();
      sprintf(reply, "password now: %s", _prefs->password);   // echo back just to let admin know for sure!!
    } else if (memcmp(command, "clear stats", 11) == 0) {
      _callbacks->clearStats();
      strcpy(reply, "(OK - stats reset)");
    } else if (memcmp(command, "clear recent.repeater", 21) == 0 && (command[21] == 0 || command[21] == ' ')) {
      if (_callbacks->supportsAdvancedRetryConfig()) {
        _callbacks->clearRecentRepeaters();
        strcpy(reply, "OK");
      } else {
        strcpy(reply, "Error, unsupported on this role");
      }
    } else if (memcmp(command, "get ", 4) == 0) {
      handleGetCmd(sender_timestamp, command, reply);
    } else if (memcmp(command, "set ", 4) == 0) {
      handleSetCmd(sender_timestamp, command, reply);
    } else if (memcmp(command, "del ", 4) == 0) {
      handleDelCmd(command, reply);
    } else if (sender_timestamp == 0 && strcmp(command, "erase") == 0) {
      bool s = _callbacks->formatFileSystem();
      sprintf(reply, "File system erase: %s", s ? "OK" : "Err");
    } else if (memcmp(command, "ver", 3) == 0) {
      sprintf(reply, "%s (Build: %s)", _callbacks->getFirmwareVer(), _callbacks->getBuildDate());
    } else if (memcmp(command, "board", 5) == 0) {
      sprintf(reply, "%s", _board->getManufacturerName());
#if defined(ENABLE_OTA)
    } else if (memcmp(command, "ota", 3) == 0 && (command[3] == 0 || command[3] == ' ')) {
      mesh::ota::handle_ota_command(command, reply, *_board);
      if (mesh::ota::ota_ctx().config_dirty) {        // a policy/key changed via the CLI -> persist it
        mesh::ota::OtaContext& c = mesh::ota::ota_ctx();
        _prefs->ota_autofetch = c.manager.autofetch();
        _prefs->ota_checkpoint_blocks = c.manager.checkpoint_blocks();
        _prefs->ota_advert_interval = c.manager.advert_mins();
        _prefs->ota_max_hops = c.manager.max_hops();
        _prefs->ota_autoinstall = c.autoinstall;
        _prefs->ota_signer_count = c.allow.count();
        for (uint8_t i = 0; i < c.allow.count() && i < MAX_OTA_SIGNERS; i++)
          memcpy(_prefs->ota_signers[i], c.allow.get(i), 32);
        _callbacks->savePrefs();
        c.config_dirty = false;
      }
#endif
    } else if (memcmp(command, "sensor get ", 11) == 0) {
      const char* key = command + 11;
      const char* val = _sensors->getSettingByKey(key);
      if (val != NULL) {
        sprintf(reply, "> %s", val);
      } else {
        strcpy(reply, "null");
      }
    } else if (memcmp(command, "sensor set ", 11) == 0) {
      strcpy(tmp, &command[11]);
      const char *parts[2];
      int num = mesh::Utils::parseTextParts(tmp, parts, 2, ' ');
      const char *key = (num > 0) ? parts[0] : "";
      const char *value = (num > 1) ? parts[1] : "null";
      if (_sensors->setSettingValue(key, value)) {
        strcpy(reply, "ok");
      } else {
        strcpy(reply, "can't find custom var");
      }
    } else if (memcmp(command, "sensor list", 11) == 0) {
      char* dp = reply;
      int start = 0;
      int end = _sensors->getNumSettings();
      if (strlen(command) > 11) {
        start = _atoi(command+12);
      }
      if (start >= end) {
        strcpy(reply, "no custom var");
      } else {
        sprintf(dp, "%d vars\n", end);
        dp = strchr(dp, 0);
        int i;
        for (i = start; i < end && (dp-reply < 134); i++) {
          sprintf(dp, "%s=%s\n",
            _sensors->getSettingName(i),
            _sensors->getSettingValue(i));
          dp = strchr(dp, 0);
        }
        if (i < end) {
          sprintf(dp, "... next:%d", i);
        } else {
          *(dp-1) = 0; // remove last CR
        }
      }
    } else if (memcmp(command, "region", 6) == 0) {
      handleRegionCmd(command, reply);
#if ENV_INCLUDE_GPS == 1
    } else if (memcmp(command, "gps on", 6) == 0) {
      if (_sensors->setSettingValue("gps", "1")) {
        _prefs->gps_enabled = 1;
        savePrefs();

        if (_prefs->powersaving_enabled) { // Power Saving
          strcpy(reply, "on (powersaving)");
        } else { // Normal mode
          strcpy(reply, "ok");
        }
      } else {
        strcpy(reply, "gps toggle not found");
      }
    } else if (memcmp(command, "gps off", 7) == 0) {
      if (_sensors->setSettingValue("gps", "0")) {
        _prefs->gps_enabled = 0;
        savePrefs();
        strcpy(reply, "ok");
      } else {
        strcpy(reply, "gps toggle not found");
      }
    } else if (memcmp(command, "gps sync", 8) == 0) {
      LocationProvider * l = _sensors->getLocationProvider();
      if (!_prefs->gps_enabled) {
        strcpy(reply, "gps is off");
      } else if (l != NULL) {
        l->syncTime();
        strcpy(reply, "scheduled");
      } else {
        strcpy(reply, "gps provider not found");
      }
    } else if (memcmp(command, "gps setloc", 10) == 0) {
      _prefs->node_lat = _sensors->node_lat;
      _prefs->node_lon = _sensors->node_lon;
      savePrefs();
      strcpy(reply, "ok");
    } else if (memcmp(command, "gps advert", 10) == 0) {
      if (strlen(command) == 10) {
        switch (_prefs->advert_loc_policy) {
          case ADVERT_LOC_NONE:
            strcpy(reply, "> none");
            break;
          case ADVERT_LOC_PREFS:
            strcpy(reply, "> prefs");
            break;
          case ADVERT_LOC_SHARE:
            strcpy(reply, "> share");
            break;
          default:
            strcpy(reply, "error");
        }
      } else if (memcmp(command+11, "none", 4) == 0) {
        _prefs->advert_loc_policy = ADVERT_LOC_NONE;
        savePrefs();
        strcpy(reply, "ok");
      } else if (memcmp(command+11, "share", 5) == 0) {
        _prefs->advert_loc_policy = ADVERT_LOC_SHARE;
        savePrefs();
        strcpy(reply, "ok");
      } else if (memcmp(command+11, "prefs", 5) == 0) {
        _prefs->advert_loc_policy = ADVERT_LOC_PREFS;
        savePrefs();
        strcpy(reply, "ok");
      } else {
        strcpy(reply, "error");
      }
    } else if (memcmp(command, "gps", 3) == 0) {
      LocationProvider * l = _sensors->getLocationProvider();
      if (l != NULL) {
        bool enabled = l->isEnabled(); // is EN pin on ?
        bool fix = l->isValid();       // has fix ?
        int sats = l->satellitesCount();
        const char* gps_setting = _sensors->getSettingByKey("gps");
        bool active = gps_setting != NULL && strcmp(gps_setting, "1") == 0;

        if (_prefs->powersaving_enabled && l->getGPSPowerSaving()) { // GPS Power Saving
          unsigned long now = millis();
          unsigned long next_off = l->getNextGPSOff();
          unsigned long deadline = next_off != 0 ? next_off : l->getNextGPSOn();
          long remaining_ms = deadline == 0 ? 0 : (long)(deadline - now);
          unsigned long mins = remaining_ms > 0 ? (unsigned long)remaining_ms / 60000UL : 0;
          if (next_off != 0) {
            snprintf(reply, 160, "on (powersaving, sleep in %luh %lum), %s, %s, %d sats",
                     mins / 60UL, mins % 60UL, active ? "active" : "deactivated",
                     fix ? "fix" : "no fix", sats);
          } else {
            snprintf(reply, 160, "off (powersaving, wake in %luh %lum)", mins / 60UL, mins % 60UL);
          }

          // "last sync" from GPS
          unsigned long last_sync = l->getLastValidTimeSync();
          size_t used = strlen(reply);
          if (last_sync == 0) {
            snprintf(reply + used, 160 - used, ", last sync: none");
          } else {
            DateTime dt = DateTime(last_sync);
            snprintf(reply + used, 160 - used, ", last sync: %02d:%02d - %d/%d/%d UTC",
                     dt.hour(), dt.minute(), dt.day(), dt.month(), dt.year());
          }
        } else { // Normal mode
          if (enabled) {
            sprintf(reply, "on, %s, %s, %d sats",
              active?"active":"deactivated",
              fix?"fix":"no fix",
              sats);
          } else {
            strcpy(reply, "off");
          }
        }
      } else {
        strcpy(reply, "Can't find GPS");
      }
#endif
    } else if (memcmp(command, "powersaving on", 14) == 0) {
#if defined(NRF52_PLATFORM)
      if (sender_timestamp == 0 || _board->isUsbDataConnected()) {
        strcpy(reply, "Error: USB serial connected");
      } else {
        _prefs->powersaving_enabled = 1;
        _sensors->setPowerSavingEnabled(true);
        savePrefs();
        strcpy(reply, "on - Immediate effect");
      }
#elif defined(ESP32) && !defined(WITH_BRIDGE)
      if (sender_timestamp == 0 || _board->isUsbDataConnected()) {
        strcpy(reply, "Error: USB serial connected");
      } else {
        _prefs->powersaving_enabled = 1;
        _sensors->setPowerSavingEnabled(true);
        savePrefs();
        strcpy(reply, "on - After 2 minutes");
      }
#elif defined(WITH_BRIDGE)
      strcpy(reply, "Bridge not supported");
#else
      strcpy(reply, "Board not supported");
#endif
    } else if (memcmp(command, "powersaving off", 15) == 0) {
      _prefs->powersaving_enabled = 0;
      _sensors->setPowerSavingEnabled(false);
      savePrefs();
      strcpy(reply, "off");
    } else if (memcmp(command, "powersaving", 11) == 0) {
      if (_prefs->powersaving_enabled) {
        strcpy(reply, "on");
      } else {
        strcpy(reply, "off");
      }
    } else if (memcmp(command, "sensor", 6) == 0) {
      // I2C
#if defined(ENV_PIN_SDA) && defined(ENV_PIN_SCL)
      sprintf(reply, "I2C Wire1: SDA=%s,SCL=%s\r\n", STR(ENV_PIN_SDA), STR(ENV_PIN_SCL));
#elif defined(PIN_BOARD_SDA) && defined(PIN_BOARD_SCL)
      sprintf(reply, "I2C Wire: SDA=%s, SCL=%s\r\n", STR(PIN_BOARD_SDA), STR(PIN_BOARD_SCL));
#elif defined(PIN_WIRE_SDA) && defined(PIN_WIRE_SCL)
      sprintf(reply, "I2C Wire: SDA=%s, SCL=%s\r\n", STR(PIN_WIRE_SDA), STR(PIN_WIRE_SCL));
#else
      sprintf(reply, "I2C GPIOs not defined\r\n");
#endif

      // GPS
#if defined(PIN_GPS_RX) && defined(PIN_GPS_TX)
      sprintf(reply + strlen(reply), "GPS Serial: RX=%s, TX=%s", STR(PIN_GPS_RX), STR(PIN_GPS_TX));
#if defined(ENV_INCLUDE_GPS) && ENV_INCLUDE_GPS > 0
      sprintf(reply + strlen(reply), ". Configured");
#else
      sprintf(reply + strlen(reply), ". Not configured");
#endif
#else
      sprintf(reply + strlen(reply), "GPS Serial not defined");
#endif
    } else if (memcmp(command, "powerlog", 8) == 0) {
      sprintf(reply, "Last reset reason: %s", _board->getResetReasonString(_board->getResetReason()));
#if defined(NRF52_PLATFORM)
      sprintf(reply + strlen(reply), "\r\nLast shutdown reason: %s",
              _board->getShutdownReasonString(_board->getShutdownReason()));
      sprintf(reply + strlen(reply), "\r\nLast boot voltage: %u mV", _board->getBootVoltage());
#endif
    } else if (memcmp(command, "log start", 9) == 0) {
      _callbacks->setLoggingOn(true);
      strcpy(reply, "   logging on");
    } else if (memcmp(command, "log stop", 8) == 0) {
      _callbacks->setLoggingOn(false);
      strcpy(reply, "   logging off");
    } else if (memcmp(command, "log erase", 9) == 0) {
      _callbacks->eraseLogFile();
      strcpy(reply, "   log erased");
    } else if (sender_timestamp == 0 && memcmp(command, "log", 3) == 0) {
      _callbacks->dumpLogFile();
      strcpy(reply, "   EOF");
    } else if (sender_timestamp == 0 && memcmp(command, "stats-packets", 13) == 0 && (command[13] == 0 || command[13] == ' ')) {
      _callbacks->formatPacketStatsReply(reply);
    } else if (sender_timestamp == 0 && memcmp(command, "stats-radio-diag", 16) == 0 && (command[16] == 0 || command[16] == ' ')) {
      _callbacks->formatRadioDiagReply(reply);
    } else if (sender_timestamp == 0 && memcmp(command, "stats-radio", 11) == 0 && (command[11] == 0 || command[11] == ' ')) {
      _callbacks->formatRadioStatsReply(reply);
    } else if (sender_timestamp == 0 && memcmp(command, "stats-core", 10) == 0 && (command[10] == 0 || command[10] == ' ')) {
      _callbacks->formatStatsReply(reply);
    } else {
      strcpy(reply, "Unknown command");
    }
}

void CommonCLI::handleSetCmd(uint32_t sender_timestamp, char* command, char* reply) {
  const char* config = &command[4];
#if defined(ESP_PLATFORM) && defined(ADMIN_PASSWORD) && !defined(WEBCONFIG_DISABLED)
  if (memcmp(config, "webui ", 6) == 0) {
    const char* value = &config[6];
    bool enabled;
    if (strcmp(value, "on") == 0) enabled = true;
    else if (strcmp(value, "off") == 0) enabled = false;
    else {
      strcpy(reply, "Error: usage set webui on|off");
      return;
    }
    if (!_callbacks->setWebUIEnabled(enabled, reply)) {
      strcpy(reply, "Error: webui not supported on this build");
    }
    return;
  }
#endif
  // Observer/MQTT/WiFi/timezone/alert/SNMP commands live in CommonCLI_Observer.cpp.
  if (handleObserverSetCmd(sender_timestamp, config, reply)) return;
  if (isAdvancedRetryConfig(config) && !_callbacks->supportsAdvancedRetryConfig()) {
    strcpy(reply, "Error, unsupported on this role");
    return;
  }
  if (isBasicRetryConfig(config) && !_callbacks->supportsBasicRetryConfig()) {
    strcpy(reply, "Error, retry configuration unsupported on this role");
    return;
  }
  if (memcmp(config, "dutycycle ", 10) == 0) {
    float dc = atof(&config[10]);
    if (dc < 1 || dc > 100) {
      strcpy(reply, "ERROR: dutycycle must be 1-100");
    } else {
      _prefs->airtime_factor = (100.0f / dc) - 1.0f;
      savePrefs();
      float actual = 100.0f / (_prefs->airtime_factor + 1.0f);
      int a_int = (int)actual;
      int a_frac = (int)((actual - a_int) * 10.0f + 0.5f);
      sprintf(reply, "OK - %d.%d%%", a_int, a_frac);
    }
  } else if (memcmp(config, "system.watchdog ", 16) == 0) {
#if defined(NRF52_PLATFORM)
    const char* value = &config[16];
    if (strcmp(value, "on") == 0) {
      _prefs->system_watchdog_enabled = 1;
      savePrefs();
      strcpy(reply, "OK - system watchdog enabled");
    } else if (strcmp(value, "off") == 0) {
      _prefs->system_watchdog_enabled = 0;
      savePrefs();
      strcpy(reply, "OK - disabled; restarting within 60s");
    } else {
      strcpy(reply, "Error: use set system.watchdog on|off");
    }
#else
    strcpy(reply, "Error: unsupported on this platform");
#endif
  } else if (memcmp(config, "af ", 3) == 0) {
    _prefs->airtime_factor = atof(&config[3]);
    savePrefs();
    strcpy(reply, "OK");
  } else if (memcmp(config, "int.thresh ", 11) == 0) {
    _prefs->interference_threshold = atoi(&config[11]);
    savePrefs();
    strcpy(reply, "OK");
  } else if (memcmp(config, "cad ", 4) == 0) {
    _prefs->cad_enabled = memcmp(&config[4], "on", 2) == 0;
    savePrefs();
    strcpy(reply, "OK");
  } else if (memcmp(config, "agc.reset.interval ", 19) == 0) {
    _prefs->agc_reset_interval = atoi(&config[19]) / 4;
    savePrefs();
    sprintf(reply, "OK - interval rounded to %d", ((uint32_t) _prefs->agc_reset_interval) * 4);
  } else if (memcmp(config, "multi.acks ", 11) == 0) {
    _prefs->multi_acks = atoi(&config[11]);
    savePrefs();
    strcpy(reply, "OK");
  } else if (memcmp(config, "allow.read.only ", 16) == 0) {
    _prefs->allow_read_only = memcmp(&config[16], "on", 2) == 0;
    savePrefs();
    strcpy(reply, "OK");
  } else if (memcmp(config, "telemetry.access ", 17) == 0) {
    if (strcmp(&config[17], "all") == 0) {
      _prefs->telemetry_access = TELEMETRY_ACCESS_ALL;
      savePrefs();
      strcpy(reply, "OK");
    } else if (strcmp(&config[17], "acl") == 0) {
      _prefs->telemetry_access = TELEMETRY_ACCESS_ACL;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "ERROR: telemetry.access must be all or acl");
    }
  } else if (memcmp(config, "flood.advert.interval ", 22) == 0) {
    int hours = _atoi(&config[22]);
    if ((hours > 0 && hours < 3) || (hours > 168)) {
      strcpy(reply, "Error: interval range is 3-168 hours");
    } else {
      _prefs->flood_advert_interval = (uint8_t)(hours);
      _callbacks->updateFloodAdvertTimer();
      savePrefs();
      strcpy(reply, "OK");
    }
  } else if (memcmp(config, "advert.interval ", 16) == 0) {
    int mins = _atoi(&config[16]);
    if ((mins > 0 && mins < MIN_LOCAL_ADVERT_INTERVAL) || (mins > 240)) {
      sprintf(reply, "Error: interval range is %d-240 minutes", MIN_LOCAL_ADVERT_INTERVAL);
    } else {
      _prefs->advert_interval = (uint8_t)(mins / 2);
      _callbacks->updateAdvertTimer();
      savePrefs();
      strcpy(reply, "OK");
    }
  } else if (memcmp(config, "guest.password ", 15) == 0) {
    StrHelper::strncpy(_prefs->guest_password, &config[15], sizeof(_prefs->guest_password));
    savePrefs();
    strcpy(reply, "OK");
  } else if (memcmp(config, "prv.key ", 8) == 0) {
    uint8_t prv_key[PRV_KEY_SIZE];
    bool success = mesh::Utils::fromHex(prv_key, PRV_KEY_SIZE, &config[8]);
    // only allow rekey if key is valid
    if (success && mesh::LocalIdentity::validatePrivateKey(prv_key)) {
      mesh::LocalIdentity new_id;
      new_id.readFrom(prv_key, PRV_KEY_SIZE);
      _callbacks->saveIdentity(new_id);
      strcpy(reply, "OK, reboot to apply! New pubkey: ");
      mesh::Utils::toHex(&reply[33], new_id.pub_key, PUB_KEY_SIZE);
    } else {
      strcpy(reply, "Error, bad key");
    }
  } else if (memcmp(config, "name ", 5) == 0) {
    if (isValidName(&config[5])) {
      StrHelper::strncpy(_prefs->node_name, &config[5], sizeof(_prefs->node_name));
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, bad chars");
    }
  } else if (memcmp(config, "repeat ", 7) == 0) {
    if (strcmp(&config[7], "on") == 0 || strcmp(&config[7], "off") == 0) {
      _prefs->disable_fwd = strcmp(&config[7], "off") == 0;
      savePrefs();
      _callbacks->onRetryConfigChanged();
      strcpy(reply, _prefs->disable_fwd ? "OK - repeat is now OFF" : "OK - repeat is now ON");
    } else {
      strcpy(reply, "Error, must be on or off");
    }
  } else if (memcmp(config, "radio.rxgain ", 13) == 0) {
    bool enabled = memcmp(&config[13], "on", 2) == 0;
    if (_callbacks->setRxBoostedGain(enabled)) {
      _prefs->rx_boosted_gain = enabled;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error: unsupported");
    }
  } else if (memcmp(config, "radio.fem.rxgain ", 17) == 0) {
    if (!_board->canControlLoRaFemLna()) {
      strcpy(reply, "Error: unsupported");
    } else if (memcmp(&config[17], "on", 2) == 0) {
      bool changed = !_board->isLoRaFemLnaEnabled();
      if (_board->setLoRaFemLnaEnabled(true)) {
        if (changed) _callbacks->recalibrateNoiseFloor();
        _prefs->radio_fem_rxgain = 1;
        savePrefs();
        strcpy(reply, "OK - LoRa FEM RX gain on");
      } else {
        strcpy(reply, "Error: failed to apply LoRa FEM RX gain");
      }
    } else if (memcmp(&config[17], "off", 3) == 0) {
      bool changed = _board->isLoRaFemLnaEnabled();
      if (_board->setLoRaFemLnaEnabled(false)) {
        if (changed) _callbacks->recalibrateNoiseFloor();
        _prefs->radio_fem_rxgain = 0;
        savePrefs();
        strcpy(reply, "OK - LoRa FEM RX gain off");
      } else {
        strcpy(reply, "Error: failed to apply LoRa FEM RX gain");
      }
    } else {
      strcpy(reply, "Error: state must be on or off");
    }
  } else if (memcmp(config, "radio.rxps ", 11) == 0) {
    const char* value = &config[11];
    uint8_t enable = _prefs->rx_powersaving_enabled;
    uint32_t rx_us = _prefs->rx_ps_rx_us;
    uint32_t sleep_us = _prefs->rx_ps_sleep_us;
    uint32_t level = 0;
    uint32_t preamble = rxPowerSavingPreambleForSF(_prefs->sf);
    bool level_requested = false;
    bool preamble_overridden = false;

    ensureRxPowerSavingDefaults(_prefs);
    rx_us = _prefs->rx_ps_rx_us;
    sleep_us = _prefs->rx_ps_sleep_us;

    if (strcmp(value, "off") == 0) {
      enable = 0;
    } else if (strcmp(value, "on") == 0 || strcmp(value, "conservative") == 0) {
      enable = 1;
      level = RX_POWERSAVING_CONSERVATIVE_LEVEL;
      preamble = RX_POWERSAVING_PROFILE_PREAMBLE;
      level_requested = true;
      preamble_overridden = true;
    } else if (strcmp(value, "balanced") == 0) {
      enable = 1;
      level = RX_POWERSAVING_BALANCED_LEVEL;
      preamble = RX_POWERSAVING_PROFILE_PREAMBLE;
      level_requested = true;
      preamble_overridden = true;
    } else {
      StrHelper::strncpy(tmp, value, sizeof(tmp));
      const char *parts[4];
      int num = mesh::Utils::parseTextParts(tmp, parts, 4, ' ');
      if (num == 1 && isNumeric(parts[0])) {
        level = _atoi(parts[0]);
        level_requested = true;
        enable = 1;
      } else if (num == 2 && strcmp(parts[0], "level") == 0 && isNumeric(parts[1])) {
        level = _atoi(parts[1]);
        level_requested = true;
        enable = 1;
      } else if (num == 4 && strcmp(parts[0], "level") == 0 && isNumeric(parts[1]) &&
                 strcmp(parts[2], "preamble") == 0 && isNumeric(parts[3])) {
        level = _atoi(parts[1]);
        preamble = _atoi(parts[3]);
        level_requested = true;
        preamble_overridden = true;
        enable = 1;
      } else if (num == 2 && isNumeric(parts[0]) && isNumeric(parts[1])) {
        rx_us = _atoi(parts[0]);
        sleep_us = _atoi(parts[1]);
        enable = 1;
      } else {
        strcpy(reply, "ERROR: use off|on|conservative|balanced|level <1-10>|<rx_us> <sleep_us>");
        return;
      }
    }

    if (level_requested && !calculateRxPowerSavingLevel(level, _prefs->sf, _prefs->bw, preamble, &rx_us, &sleep_us)) {
      strcpy(reply, "ERROR: level range is 1-10; preamble is 16 or 32");
      return;
    }

    if (!isValidRxPowerSavingPeriod(rx_us) || !isValidRxPowerSavingPeriod(sleep_us)) {
      sprintf(reply, "ERROR: range is %lu-%lu us",
              (unsigned long)RX_POWERSAVING_MIN_PERIOD_US,
              (unsigned long)RX_POWERSAVING_MAX_PERIOD_US);
      return;
    }

    if (!_callbacks->setRxPowerSaving(enable, rx_us, sleep_us)) {
      strcpy(reply, "ERROR: RX powersaving unsupported");
      return;
    }

    _prefs->rx_powersaving_enabled = enable;
    _prefs->rx_ps_rx_us = rx_us;
    _prefs->rx_ps_sleep_us = sleep_us;
    if (level_requested) {
      // Remember the intent so the timings can auto-retune when SF/BW change.
      _prefs->rx_ps_level = level;
      _prefs->rx_ps_preamble = preamble_overridden ? preamble : 0;   // 0 = auto (derive from SF)
    } else if (strcmp(value, "off") != 0) {
      // manual <rx_us> <sleep_us> timings are fixed, not level-derived
      // (the named profiles set level_requested and are handled above)
      _prefs->rx_ps_level = 0;
      _prefs->rx_ps_preamble = 0;
    }
    savePrefs();
    if (level_requested) {
      sprintf(reply, "OK - level %lu,%s,%lu,%lu,preamble=%lu",
              (unsigned long)level,
              enable ? "on" : "off",
              (unsigned long)rx_us,
              (unsigned long)sleep_us,
              (unsigned long)preamble);
    } else {
      sprintf(reply, "OK - %s,%lu,%lu", enable ? "on" : "off",
              (unsigned long)rx_us, (unsigned long)sleep_us);
    }
  } else if (memcmp(config, "radio ", 6) == 0) {
    strcpy(tmp, &config[6]);
    const char *parts[4];
    int num = mesh::Utils::parseTextParts(tmp, parts, 4);
    float freq  = num > 0 ? strtof(parts[0], nullptr) : 0.0f;
    float bw    = num > 1 ? strtof(parts[1], nullptr) : 0.0f;
    uint8_t sf  = num > 2 ? atoi(parts[2]) : 0;
    uint8_t cr  = num > 3 ? atoi(parts[3]) : 0;
    if (freq >= 150.0f && freq <= 2500.0f && sf >= 5 && sf <= 12 && cr >= 5 && cr <= 8 && isValidLoRaBandwidth(bw)) {
      _prefs->sf = sf;
      _prefs->cr = cr;
      _prefs->freq = freq;
      _prefs->bw = bw;
      // Retune level-based RX powersaving to the new SF/BW. Persist only; the
      // radio itself is "reboot to apply", and begin() re-arms the timings then.
      bool rxps_retuned = recalculateRxPowerSavingFromLevel(_prefs);
      _callbacks->savePrefs();
      strcpy(reply, rxps_retuned ? "OK - reboot to apply (rxps retuned)" : "OK - reboot to apply");
    } else {
      strcpy(reply, "Error, invalid radio params");
    }
  } else if (memcmp(config, "radioat ", 8) == 0) {
    float freq, bw;
    uint8_t sf, cr;
    uint32_t start_time, end_time;
    if (!parseScheduledRadioArgs(&config[8], false, freq, bw, sf, cr, start_time, end_time)) {
      strcpy(reply, "Error, use: set radioat f,bw,sf,cr,start");
    } else if (freq < 150.0f || freq > 2500.0f || sf < 5 || sf > 12 || cr < 5 || cr > 8 || !isValidLoRaBandwidth(bw)) {
      strcpy(reply, "Error, invalid radio params");
    } else {
      _callbacks->addScheduledRadioParams(false, freq, bw, sf, cr, start_time, end_time, reply);
    }
  } else if (memcmp(config, "tempradioat ", 12) == 0) {
    float freq, bw;
    uint8_t sf, cr;
    uint32_t start_time, end_time;
    if (!parseScheduledRadioArgs(&config[12], true, freq, bw, sf, cr, start_time, end_time)) {
      strcpy(reply, "Error, use: set tempradioat f,bw,sf,cr,start,end");
    } else if (freq < 150.0f || freq > 2500.0f || sf < 5 || sf > 12 || cr < 5 || cr > 8 || !isValidLoRaBandwidth(bw)) {
      strcpy(reply, "Error, invalid radio params");
    } else {
      _callbacks->addScheduledRadioParams(true, freq, bw, sf, cr, start_time, end_time, reply);
    }
  } else if (memcmp(config, "lat ", 4) == 0) {
    _prefs->node_lat = atof(&config[4]);
    savePrefs();
    strcpy(reply, "OK");
  } else if (memcmp(config, "lon ", 4) == 0) {
    _prefs->node_lon = atof(&config[4]);
    savePrefs();
    strcpy(reply, "OK");
  } else if (memcmp(config, "rxdelay ", 8) == 0) {
    float db = atof(&config[8]);
    if (db >= 0 && db <= 20.0f) {
      _prefs->rx_delay_base = db;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0-20");
    }
  } else if (memcmp(config, "txdelay ", 8) == 0) {
    float f = atof(&config[8]);
    if (f >= 0 && f <= 2.0f) {
      _prefs->tx_delay_factor = f;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0-2");
    }
  } else if (memcmp(config, "flood.max.unscoped ", 19) == 0) {
    uint8_t m = atoi(&config[19]);
    if (m <= 64) {
      _prefs->flood_max_unscoped = m;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, max 64");
    } 
  } else if (memcmp(config, "flood.max.advert ", 17) == 0) {
    uint8_t m = atoi(&config[17]);
    if (m <= 64) {
      _prefs->flood_max_advert = m;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, max 64");
    }
  } else if (memcmp(config, "flood.max ", 10) == 0) {
    uint8_t m = atoi(&config[10]);
    if (m <= 64) {
      _prefs->flood_max = m;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, max 64");
    }
  } else if (memcmp(config, "direct.txdelay ", 15) == 0) {
    float f = atof(&config[15]);
    if (f >= 0 && f <= 2.0f) {
      _prefs->direct_tx_delay_factor = f;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0-2");
    }
  } else if (memcmp(config, "retry.preset ", 13) == 0) {
    uint8_t preset;
    if (parseRetryPreset(&config[13], preset)) {
      applyDirectRetryPreset(_prefs, preset);
      savePrefs();
      sprintf(reply, "OK - %s", retryPresetName(_prefs->retry_preset));
    } else {
      strcpy(reply, "Error, must be infra, rooftop, or mobile");
    }
  } else if (memcmp(config, "direct.retry ", 13) == 0) {
    if (strcmp(&config[13], "on") == 0) {
      _prefs->direct_retry_enabled = 1;
      savePrefs();
      _callbacks->onRetryConfigChanged();
      strcpy(reply, "OK");
    } else if (strcmp(&config[13], "off") == 0) {
      _prefs->direct_retry_enabled = 0;
      savePrefs();
      _callbacks->onRetryConfigChanged();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be on or off");
    }
  } else if (memcmp(config, "direct.retry.heard ", 19) == 0) {
    if (strcmp(&config[19], "on") == 0) {
      _prefs->direct_retry_recent_enabled = 1;
      savePrefs();
      strcpy(reply, "OK");
    } else if (strcmp(&config[19], "off") == 0) {
      _prefs->direct_retry_recent_enabled = 0;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be on or off");
    }
  } else if (memcmp(config, "direct.retry.margin ", 20) == 0) {
    if (!looksNumeric(&config[20])) {
      strcpy(reply, "Error, must be 0-40 dB");
    } else {
      int16_t margin_x4 = parseSnrDbX4(&config[20]);
      if (margin_x4 >= 0 && margin_x4 <= 160) {
        _prefs->direct_retry_snr_margin_x4 = (uint16_t)margin_x4;
        _prefs->retry_preset = RETRY_PRESET_CUSTOM;
        savePrefs();
        strcpy(reply, "OK");
      } else {
        strcpy(reply, "Error, must be 0-40 dB");
      }
    }
  } else if (memcmp(config, "direct.retry.count ", 19) == 0) {
    int attempts = looksUnsignedInteger(&config[19]) ? _atoi(&config[19]) : -1;
    if (attempts >= 1 && attempts <= 15) {
      _prefs->direct_retry_attempts = (uint8_t)attempts;
      _prefs->retry_preset = RETRY_PRESET_CUSTOM;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 1-15");
    }
  } else if (memcmp(config, "direct.retry.base ", 18) == 0) {
    int base_ms = looksUnsignedInteger(&config[18]) ? _atoi(&config[18]) : -1;
    if (base_ms >= 10 && base_ms <= 5000) {
      _prefs->direct_retry_base_ms = (uint16_t)base_ms;
      _prefs->retry_preset = RETRY_PRESET_CUSTOM;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 10-5000 ms");
    }
  } else if (memcmp(config, "direct.retry.step ", 18) == 0) {
    int step_ms = looksUnsignedInteger(&config[18]) ? _atoi(&config[18]) : -1;
    if (step_ms >= 0 && step_ms <= 5000) {
      _prefs->direct_retry_step_ms = (uint16_t)step_ms;
      _prefs->retry_preset = RETRY_PRESET_CUSTOM;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0-5000 ms");
    }
  } else if (memcmp(config, "flood.channel.data.hops ", 24) == 0) {
    uint8_t max_hops;
    if (parseFloodChannelBlockHops(&config[24], max_hops)) {
      _prefs->flood_channel_data_max_hops = max_hops;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be all or 1-7");
    }
  } else if (memcmp(config, "flood.channel.data ", 19) == 0) {
    if (strcmp(&config[19], "on") == 0) {
      _prefs->flood_channel_data_enabled = 1;
      savePrefs();
      strcpy(reply, "OK");
    } else if (strcmp(&config[19], "off") == 0) {
      _prefs->flood_channel_data_enabled = 0;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be on or off");
    }
  } else if (memcmp(config, "flood.channel.block.hops ", 25) == 0) {
    uint8_t max_hops;
    if (parseFloodChannelBlockHops(&config[25], max_hops)) {
      _prefs->flood_channel_block_max_hops = max_hops;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be all or 1-7");
    }
  } else if (memcmp(config, "flood.channel.block", 19) == 0
      && (config[19] == ' ' || config[19] == '.')) {
    const char* cursor = &config[19];
    int index = 0;
    if (!parseFloodChannelBlockDotIndex(cursor, index)) {
      sprintf(reply, "Error, index 1-%d", FLOOD_CHANNEL_BLOCK_SLOTS);
      return;
    }
    cursor = skipSpacesConst(cursor);

    const char* key_start = cursor;
    while (*cursor && *cursor != ' ') cursor++;
    size_t key_len_text = cursor - key_start;
    char key_text[PUB_KEY_SIZE * 2 + 1];
    if (key_len_text == 0 || key_len_text >= sizeof(key_text)) {
      strcpy(reply, "Error, use: set flood.channel.block[.n] <hex-key> <name>|#channel");
      return;
    }
    memcpy(key_text, key_start, key_len_text);
    key_text[key_len_text] = 0;

    char name[FLOOD_CHANNEL_BLOCK_NAME_LEN];
    uint8_t block_hops = FLOOD_CHANNEL_BLOCK_HOPS_INHERIT;
    if (key_text[0] == '#') {
      StrHelper::strncpy(name, key_text, sizeof(name));
      const char* extra = skipSpacesConst(cursor);
      if (*extra && looksFloodChannelBlockHopAssignment(extra)
          && !parseFloodChannelBlockHopAssignment(extra, true, block_hops)) {
        strcpy(reply, "Error, hops must be all, default, or 1-7");
        return;
      }
    } else {
      copyTrimmedFloodChannelBlockName(name, sizeof(name), cursor);
      if (!trimFloodChannelBlockHopSuffix(name, block_hops)) {
        strcpy(reply, "Error, bad name or hops");
        return;
      }
    }
    uint8_t secret[PUB_KEY_SIZE];
    uint8_t decoded_key_len = 0;
    if (!parseFloodChannelBlockKey(key_text, secret, decoded_key_len)) {
      strcpy(reply, "Error, key must be 128/256-bit hex or #channel");
    } else if (name[0] == 0 || !isValidName(name)) {
      strcpy(reply, "Error, bad name");
    } else {
      _callbacks->setFloodChannelBlock(index, secret, decoded_key_len, name, block_hops, reply);
    }
  } else if (memcmp(config, "flood.retry.count ", 18) == 0) {
    int attempts = looksUnsignedInteger(&config[18]) ? _atoi(&config[18]) : -1;
    if (attempts >= 0 && attempts <= 15) {
      _prefs->flood_retry_attempts = (uint8_t)attempts;
      _prefs->retry_preset = RETRY_PRESET_CUSTOM;
      savePrefs();
      _callbacks->onRetryConfigChanged();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0-15");
    }
  } else if (memcmp(config, "flood.retry.path ", 17) == 0) {
    uint8_t path_gate;
    if (parseFloodRetryPathGate(&config[17], path_gate)) {
      _prefs->flood_retry_max_path = path_gate;
      if (path_gate == 0) {
        _prefs->flood_retry_group_max_path = FLOOD_RETRY_PATH_GATE_DISABLED;
      }
      _prefs->retry_preset = RETRY_PRESET_CUSTOM;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0-63 or off");
    }
  } else if (memcmp(config, "flood.retry.group.path ", 23) == 0) {
    uint8_t path_gate;
    if (parseFloodRetryPathGate(&config[23], path_gate)) {
      _prefs->flood_retry_group_max_path = _prefs->flood_retry_max_path == 0
          ? FLOOD_RETRY_PATH_GATE_DISABLED
          : path_gate;
      _prefs->retry_preset = RETRY_PRESET_CUSTOM;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0-63 or off");
    }
  } else if (memcmp(config, "flood.retry.prefixes ", 21) == 0) {
    if (parseFloodRetryPrefixList(_prefs->flood_retry_prefixes, FLOOD_RETRY_PREFIX_SLOTS, &config[21])) {
      savePrefs();
      if (!formatFloodRetryBucketCollisionWarning(reply, _prefs, "OK - ")) {
        strcpy(reply, "OK");
      }
    } else {
      sprintf(reply, "Error, use up to %u comma-separated 3-byte hex prefixes",
              (unsigned int)FLOOD_RETRY_PREFIX_SLOTS);
    }
  } else if (memcmp(config, "flood.retry.ignore ", 19) == 0) {
    if (parseFloodRetryPrefixList(_prefs->flood_retry_ignore_prefixes,
                                  FLOOD_RETRY_IGNORE_PREFIXES, &config[19])) {
      savePrefs();
      strcpy(reply, "OK");
    } else {
      sprintf(reply, "Error, use up to %u comma-separated 3-byte hex prefixes",
              (unsigned int)FLOOD_RETRY_IGNORE_PREFIXES);
    }
  } else if (memcmp(config, "flood.retry.advert ", 19) == 0) {
    if (strcmp(&config[19], "on") == 0) {
      _prefs->flood_retry_advert_enabled = 1;
      savePrefs();
      strcpy(reply, "OK");
    } else if (strcmp(&config[19], "off") == 0) {
      _prefs->flood_retry_advert_enabled = 0;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be on or off");
    }
  } else if (memcmp(config, "flood.retry.bridge ", 19) == 0) {
    if (strcmp(&config[19], "on") == 0) {
      _prefs->flood_retry_bridge_enabled = 1;
      savePrefs();
      if (!formatFloodRetryBucketCollisionWarning(reply, _prefs, "OK - bucket 7=prefixes; ")) {
        strcpy(reply, "OK - flood.retry.prefixes acts as bucket 7 (other); multi-try bridge routing enabled");
      }
    } else if (strcmp(&config[19], "off") == 0) {
      _prefs->flood_retry_bridge_enabled = 0;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be on or off");
    }
  } else if (memcmp(config, "flood.retry.bucket ", 19) == 0) {
    const char* params = &config[19];
    uint8_t bucket = atoi(params);
    const char* list = strchr(params, ' ');
    if (bucket < 1 || bucket > FLOOD_RETRY_BRIDGE_BUCKETS || list == NULL || *(list + 1) == 0) {
      sprintf(reply, "Error, usage: set flood.retry.bucket <1-%d> <prefixes|none>", FLOOD_RETRY_BRIDGE_BUCKETS);
    } else if (parseFloodRetryPrefixList(_prefs->flood_retry_bridge_buckets[bucket - 1],
                                         FLOOD_RETRY_BUCKET_PREFIXES, list + 1)) {
      savePrefs();
      if (!formatFloodRetryBucketCollisionWarning(reply, _prefs, "OK - ")) {
        strcpy(reply, "OK");
      }
    } else {
      sprintf(reply, "Error, use up to %u comma-separated 3-byte hex prefixes",
              (unsigned int)FLOOD_RETRY_BUCKET_PREFIXES);
    }
  } else if (memcmp(config, "direct.retry.cr ", 16) == 0) {
    if (strcmp(&config[16], "off") == 0) {
      _prefs->direct_retry_cr_enabled = 0;
      savePrefs();
      strcpy(reply, "OK");
    } else if (!_callbacks->supportsAdvancedRetryConfig()) {
      if (strcmp(&config[16], "on") == 0) {
        _prefs->direct_retry_cr_enabled = 1;
        savePrefs();
        strcpy(reply, "OK");
      } else {
        strcpy(reply, "Error, use on or off on this role");
      }
    } else {
      strcpy(tmp, &config[16]);
      const char *parts[4];
      int num = mesh::Utils::parseTextParts(tmp, parts, 4, ',');
      if (num == 4 && looksNumeric(parts[0]) && looksNumeric(parts[1]) && looksNumeric(parts[2]) && looksNumeric(parts[3])) {
        int16_t cr4 = parseSnrDbX4(parts[0]);
        int16_t cr5 = parseSnrDbX4(parts[1]);
        int16_t cr7 = parseSnrDbX4(parts[2]);
        int16_t cr8 = parseSnrDbX4(parts[3]);
        if (cr4 >= -128 && cr4 <= 127 && cr5 >= -128 && cr5 <= 127 && cr7 >= -128 && cr7 <= 127 && cr8 >= -128 && cr8 <= 127) {
          _prefs->direct_retry_cr4_snr_x4 = (int8_t)cr4;
          _prefs->direct_retry_cr5_snr_x4 = (int8_t)cr5;
          _prefs->direct_retry_cr7_snr_x4 = (int8_t)cr7;
          _prefs->direct_retry_cr8_snr_x4 = (int8_t)cr8;
          _prefs->direct_retry_cr_enabled = 1;
          savePrefs();
          strcpy(reply, "OK");
        } else {
          strcpy(reply, "Error, SNR must fit -32.00..31.75 dB");
        }
      } else {
        strcpy(reply, "Error, use CR4,CR5,CR7,CR8 SNRs or off");
      }
    }
  } else if (memcmp(config, "recent.repeater ", 16) == 0) {
    strcpy(tmp, &config[16]);
    const char *parts[2];
    int num = mesh::Utils::parseTextParts(tmp, parts, 2, ' ');
    uint8_t prefix[MAX_HASH_SIZE];
    uint8_t prefix_len = 0;
    int16_t snr_x4 = 12;  // default to +3.0 dB when omitted or invalid
    if (num < 1 || !parseHashPrefix(parts[0], prefix, prefix_len)) {
      strcpy(reply, "Error, use: set recent.repeater <hex> [snr_db]");
    } else if (num > 1 && !looksNumeric(parts[1])) {
      strcpy(reply, "Error, SNR must be numeric");
    } else {
      if (num > 1) {
        snr_x4 = parseSnrDbX4(parts[1]);
      }
      if (snr_x4 < -128 || snr_x4 > 127) {
        strcpy(reply, "Error, SNR must fit -32.00..31.75 dB");
      } else if (_callbacks->setRecentRepeater(prefix, prefix_len, (int8_t)snr_x4)) {
        char prefix_hex[RECENT_REPEATER_PREFIX_MAX_BYTES * 2 + 1];
        char snr[12];
        mesh::Utils::toHex(prefix_hex, prefix, prefix_len);
        prefix_hex[prefix_len * 2] = 0;
        formatSnrDbX4Short(snr, sizeof(snr), snr_x4);
        sprintf(reply, "OK - set %s at %s SNR", prefix_hex, snr);
      } else {
        strcpy(reply, "Error, table rejected prefix");
      }
    }
  } else if (memcmp(config, "owner.info ", 11) == 0) {
    config += 11;
    char *dp = _prefs->owner_info;
    while (*config && dp - _prefs->owner_info < sizeof(_prefs->owner_info)-1) {
      *dp++ = (*config == '|') ? '\n' : *config;    // translate '|' to newline chars
      config++;
    }
    *dp = 0;
    savePrefs();
    strcpy(reply, "OK");
  } else if (memcmp(config, "path.hash.mode ", 15) == 0) {
    config += 15;
    uint8_t mode = atoi(config);
    if (mode < 3) {
      _prefs->path_hash_mode = mode;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0,1, or 2");
    }
  } else if (memcmp(config, "loop.detect ", 12) == 0) {
    config += 12;
    uint8_t mode;
    if (memcmp(config, "off", 3) == 0) {
      mode = LOOP_DETECT_OFF;
    } else if (memcmp(config, "minimal", 7) == 0) {
      mode = LOOP_DETECT_MINIMAL;
    } else if (memcmp(config, "moderate", 8) == 0) {
      mode = LOOP_DETECT_MODERATE;
    } else if (memcmp(config, "strict", 6) == 0) {
      mode = LOOP_DETECT_STRICT;
    } else {
      mode = 0xFF;
      strcpy(reply, "Error, must be: off, minimal, moderate, or strict");
    }
    if (mode != 0xFF) {
      _prefs->loop_detect = mode;
      savePrefs();
      strcpy(reply, "OK");
    }
  } else if (memcmp(config, "tx ", 3) == 0) {
    _prefs->tx_power_dbm = atoi(&config[3]);
    savePrefs();
    _callbacks->setTxPower(_prefs->tx_power_dbm);
    strcpy(reply, "OK");
  } else if (sender_timestamp == 0 && memcmp(config, "freq ", 5) == 0) {
    _prefs->freq = atof(&config[5]);
    savePrefs();
    strcpy(reply, "OK - reboot to apply");
#ifdef WITH_BRIDGE
  } else if (memcmp(config, "bridge.enabled ", 15) == 0) {
    _prefs->bridge_enabled = memcmp(&config[15], "on", 2) == 0;
    _callbacks->setBridgeState(_prefs->bridge_enabled);
    savePrefs();
    strcpy(reply, "OK");
  } else if (memcmp(config, "bridge.delay ", 13) == 0) {
    int delay = _atoi(&config[13]);
    if (delay >= 0 && delay <= 10000) {
      _prefs->bridge_delay = (uint16_t)delay;
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error: delay must be between 0-10000 ms");
    }
  } else if (memcmp(config, "bridge.source ", 14) == 0) {
    _prefs->bridge_pkt_src = memcmp(&config[14], "rx", 2) == 0;
#ifdef WITH_MQTT_BRIDGE
    if (_prefs->bridge_pkt_src == 1) {
      _mqtt_prefs.mqtt_rx_enabled = 1;
      _mqtt_prefs.mqtt_tx_enabled = 0;
    } else {
      _mqtt_prefs.mqtt_rx_enabled = 0;
      _mqtt_prefs.mqtt_tx_enabled = 1;
    }
#endif
    savePrefs();
    strcpy(reply, "OK");
#endif
#ifdef WITH_RS232_BRIDGE
  } else if (memcmp(config, "bridge.baud ", 12) == 0) {
    uint32_t baud = atoi(&config[12]);
    if (baud >= 9600 && baud <= BRIDGE_MAX_BAUD) {
      _prefs->bridge_baud = (uint32_t)baud;
      _callbacks->restartBridge();
      savePrefs();
      strcpy(reply, "OK");
    } else {
      sprintf(reply, "Error: baud rate must be between 9600-%d",BRIDGE_MAX_BAUD);
    }
#endif
#ifdef WITH_ESPNOW_BRIDGE
  } else if (memcmp(config, "bridge.channel ", 15) == 0) {
    int ch = atoi(&config[15]);
    if (ch > 0 && ch < 15) {
      _prefs->bridge_channel = (uint8_t)ch;
      _callbacks->restartBridge();
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error: channel must be between 1-14");
    }
  } else if (memcmp(config, "bridge.secret ", 14) == 0) {
    const char* secret = &config[14];
    if (secret[0] == 0 || strlen(secret) >= sizeof(_prefs->bridge_secret)) {
      sprintf(reply, "Error: secret must be 1-%u characters", (unsigned)(sizeof(_prefs->bridge_secret) - 1));
    } else {
      StrHelper::strncpy(_prefs->bridge_secret, secret, sizeof(_prefs->bridge_secret));
      _callbacks->restartBridge();
      savePrefs();
      strcpy(reply, "OK");
    }
#endif
  } else if (memcmp(config, "adc.multiplier ", 15) == 0) {
    _prefs->adc_multiplier = atof(&config[15]);
    if (_board->setAdcMultiplier(_prefs->adc_multiplier)) {
      savePrefs();
      if (_prefs->adc_multiplier == 0.0f) {
        strcpy(reply, "OK - using default board multiplier");
      } else {
        sprintf(reply, "OK - multiplier set to %.3f", _prefs->adc_multiplier);
      }
    } else {
      _prefs->adc_multiplier = 0.0f;
      strcpy(reply, "Error: unsupported");
    };
  } else if (memcmp(config, "reboot.interval ", 16) == 0) {
    int hours = _atoi(&config[16]);
    if (hours == 0) {
      _prefs->reboot_interval = 0;
      savePrefs();
      strcpy(reply, "reboot.interval disabled");
    } else if (hours < 1 || 255 < hours) {
      strcpy(reply, "Error: interval range is 1-255 hours");
    } else {
      _prefs->reboot_interval = hours;
      savePrefs();
      sprintf(reply, "OK - reboot.interval set to %d", _prefs->reboot_interval);
    }
  } else {
    sprintf(reply, "unknown config: %s", config);
  }
}

void CommonCLI::handleGetCmd(uint32_t sender_timestamp, char* command, char* reply) {
  const char* config = &command[4];
#if defined(ESP_PLATFORM) && defined(ADMIN_PASSWORD) && !defined(WEBCONFIG_DISABLED)
  if (strcmp(config, "webui") == 0) {
    if (!_callbacks->getWebUIStatus(reply)) {
      strcpy(reply, "Error: webui not supported on this build");
    }
    return;
  }
#endif
  // Observer/MQTT/WiFi/timezone/alert/SNMP commands live in CommonCLI_Observer.cpp.
  if (handleObserverGetCmd(sender_timestamp, config, reply)) return;
  if (isAdvancedRetryConfig(config) && !_callbacks->supportsAdvancedRetryConfig()) {
    strcpy(reply, "Error, unsupported on this role");
    return;
  }
  if (isBasicRetryConfig(config) && !_callbacks->supportsBasicRetryConfig()) {
    strcpy(reply, "Error, retry configuration unsupported on this role");
    return;
  }
  int recent_page = 1;
  if (memcmp(config, "dutycycle", 9) == 0) {
    float dc = 100.0f / (_prefs->airtime_factor + 1.0f);
    int dc_int = (int)dc;
    int dc_frac = (int)((dc - dc_int) * 10.0f + 0.5f);
    sprintf(reply, "> %d.%d%%", dc_int, dc_frac);
  } else if (strcmp(config, "system.watchdog") == 0) {
#if defined(NRF52_PLATFORM)
    sprintf(reply, "> %s", _prefs->system_watchdog_enabled ? "on" : "off");
#else
    strcpy(reply, "Error: unsupported on this platform");
#endif
  } else if (memcmp(config, "af", 2) == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(_prefs->airtime_factor));
  } else if (memcmp(config, "int.thresh", 10) == 0) {
    sprintf(reply, "> %d", (uint32_t) _prefs->interference_threshold);
  } else if (memcmp(config, "cad", 3) == 0) {
    sprintf(reply, "> %s. # channel busy: %u", _prefs->cad_enabled ? "on" : "off", _board->n_cad_busy);
  } else if (memcmp(config, "agc.reset.interval", 18) == 0) {
    sprintf(reply, "> %d", ((uint32_t) _prefs->agc_reset_interval) * 4);
  } else if (memcmp(config, "multi.acks", 10) == 0) {
    sprintf(reply, "> %d", (uint32_t) _prefs->multi_acks);
  } else if (memcmp(config, "allow.read.only", 15) == 0) {
    sprintf(reply, "> %s", _prefs->allow_read_only ? "on" : "off");
  } else if (memcmp(config, "telemetry.access", 16) == 0) {
    sprintf(reply, "> %s", _prefs->telemetry_access == TELEMETRY_ACCESS_ACL ? "acl" : "all");
  } else if (memcmp(config, "flood.advert.interval", 21) == 0) {
    sprintf(reply, "> %d", ((uint32_t) _prefs->flood_advert_interval));
  } else if (memcmp(config, "advert.interval", 15) == 0) {
    sprintf(reply, "> %d", ((uint32_t) _prefs->advert_interval) * 2);
  } else if (memcmp(config, "guest.password", 14) == 0) {
    sprintf(reply, "> %s", _prefs->guest_password);
  } else if (sender_timestamp == 0 && memcmp(config, "prv.key", 7) == 0) {  // from serial command line only
    uint8_t prv_key[PRV_KEY_SIZE];
    int len = _callbacks->getSelfId().writeTo(prv_key, PRV_KEY_SIZE);
    mesh::Utils::toHex(tmp, prv_key, len);
    sprintf(reply, "> %s", tmp);
  } else if (memcmp(config, "name", 4) == 0) {
    sprintf(reply, "> %s", _prefs->node_name);
  } else if (memcmp(config, "repeat", 6) == 0) {
    sprintf(reply, "> %s", _prefs->disable_fwd ? "off" : "on");
  } else if (memcmp(config, "lat", 3) == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(_prefs->node_lat));
  } else if (memcmp(config, "lon", 3) == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(_prefs->node_lon));
  } else if (memcmp(config, "radio.rxgain", 12) == 0) {
    sprintf(reply, "> %s", _prefs->rx_boosted_gain ? "on" : "off");
  } else if (memcmp(config, "radio.fem.rxgain", 16) == 0) {
    if (!_board->canControlLoRaFemLna()) {
      strcpy(reply, "Error: unsupported");
    } else {
      sprintf(reply, "> %s", _board->isLoRaFemLnaEnabled() ? "on" : "off");
    }
  } else if (memcmp(config, "tempradioat", 11) == 0 && (config[11] == 0 || config[11] == ' ')) {
    _callbacks->formatScheduledRadioParams(true, skipSpacesConst(&config[11]), reply);
  } else if (memcmp(config, "radioat", 7) == 0 && (config[7] == 0 || config[7] == ' ')) {
    _callbacks->formatScheduledRadioParams(false, skipSpacesConst(&config[7]), reply);
  } else if (memcmp(config, "radio.rxps", 10) == 0) {
    ensureRxPowerSavingDefaults(_prefs);
    sprintf(reply, "> %s,%lu,%lu", _prefs->rx_powersaving_enabled ? "on" : "off",
            (unsigned long)_prefs->rx_ps_rx_us, (unsigned long)_prefs->rx_ps_sleep_us);
  } else if (memcmp(config, "rxps.wd", 7) == 0) {
    uint32_t wd_soft, wd_hard;
    _callbacks->getRxPsWatchdogCounts(&wd_soft, &wd_hard);
    sprintf(reply, "> soft=%lu,hard=%lu", (unsigned long)wd_soft, (unsigned long)wd_hard);
  } else if (memcmp(config, "radio", 5) == 0) {
    char freq[16], bw[16];
    strcpy(freq, StrHelper::ftoa(_prefs->freq));
    strcpy(bw, StrHelper::ftoa3(_prefs->bw));
    sprintf(reply, "> %s,%s,%d,%d", freq, bw, (uint32_t)_prefs->sf, (uint32_t)_prefs->cr);
  } else if (memcmp(config, "rxdelay", 7) == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(_prefs->rx_delay_base));
  } else if (memcmp(config, "txdelay", 7) == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(_prefs->tx_delay_factor));
  } else if (strcmp(config, "flood.channel.data.hops") == 0) {
    char hops[8];
    formatFloodChannelBlockHops(hops, _prefs->flood_channel_data_max_hops);
    sprintf(reply, "> %s", hops);
  } else if (strcmp(config, "flood.channel.data") == 0) {
    char hops[8];
    formatFloodChannelBlockHops(hops, _prefs->flood_channel_data_max_hops);
    sprintf(reply, "> %s %s", _prefs->flood_channel_data_enabled ? "on" : "off", hops);
  } else if (memcmp(config, "flood.channel.block.hops", 24) == 0) {
    char hops[8];
    formatFloodChannelBlockHops(hops, _prefs->flood_channel_block_max_hops);
    sprintf(reply, "> %s", hops);
  } else if (memcmp(config, "flood.channel.block", 19) == 0
      && (config[19] == 0 || config[19] == ' ' || config[19] == '.')) {
    const char* cursor = &config[19];
    int index = 0;
    if (!parseFloodChannelBlockDotIndex(cursor, index)) {
      sprintf(reply, "Error, index 1-%d", FLOOD_CHANNEL_BLOCK_SLOTS);
      return;
    }
    cursor = skipSpacesConst(cursor);
    char selector[8];
    if (index > 0 && *cursor != 0) {
      strcpy(reply, "Error, use index or selector");
      return;
    }
    if (index > 0) {
      snprintf(selector, sizeof(selector), "%d", index);
      _callbacks->formatFloodChannelBlocks(selector, reply);
    } else {
      _callbacks->formatFloodChannelBlocks(cursor, reply);
    }
  } else if (memcmp(config, "flood.max.advert", 16) == 0) {
    sprintf(reply, "> %d", (uint32_t)_prefs->flood_max_advert);
  } else if (memcmp(config, "flood.max.unscoped", 18) == 0) {
    sprintf(reply, "> %d", (uint32_t)_prefs->flood_max_unscoped);
  } else if (memcmp(config, "flood.max", 9) == 0) {
    sprintf(reply, "> %d", (uint32_t)_prefs->flood_max);
  } else if (memcmp(config, "direct.txdelay", 14) == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(_prefs->direct_tx_delay_factor));
  } else if (memcmp(config, "retry.preset", 12) == 0) {
    sprintf(reply, "> %s", retryPresetName(_prefs->retry_preset));
  } else if (memcmp(config, "direct.retry", 12) == 0 && (config[12] == 0 || config[12] == ' ')) {
    sprintf(reply, "> %s", _prefs->direct_retry_enabled ? "on" : "off");
  } else if (memcmp(config, "direct.retry.heard", 18) == 0) {
    sprintf(reply, "> %s", _prefs->direct_retry_recent_enabled ? "on" : "off");
  } else if (memcmp(config, "direct.retry.margin", 19) == 0) {
    char margin[12];
    formatSnrDbX4(margin, sizeof(margin), _prefs->direct_retry_snr_margin_x4);
    sprintf(reply, "> %s", margin);
  } else if (memcmp(config, "direct.retry.count", 18) == 0) {
    sprintf(reply, "> %d", (uint32_t)_prefs->direct_retry_attempts);
  } else if (memcmp(config, "direct.retry.base", 17) == 0) {
    sprintf(reply, "> %d", (uint32_t)_prefs->direct_retry_base_ms);
  } else if (memcmp(config, "direct.retry.step", 17) == 0) {
    sprintf(reply, "> %d", (uint32_t)_prefs->direct_retry_step_ms);
  } else if (memcmp(config, "flood.retry.count", 17) == 0) {
    sprintf(reply, "> %d", (uint32_t)_prefs->flood_retry_attempts);
  } else if (memcmp(config, "flood.retry.path", 16) == 0) {
    char path_gate[8];
    formatFloodRetryPathGate(path_gate, _prefs->flood_retry_max_path);
    sprintf(reply, "> %s", path_gate);
  } else if (memcmp(config, "flood.retry.group.path", 22) == 0) {
    char path_gate[8];
    formatFloodRetryPathGate(path_gate, _prefs->flood_retry_group_max_path);
    sprintf(reply, "> %s", path_gate);
  } else if (memcmp(config, "flood.retry.prefixes", 20) == 0) {
    formatFloodRetryPrefixList(tmp, _prefs->flood_retry_prefixes, FLOOD_RETRY_PREFIX_SLOTS);
    sprintf(reply, "> %s", tmp[0] ? tmp : "none");
  } else if (memcmp(config, "flood.retry.ignore", 18) == 0) {
    formatFloodRetryPrefixList(tmp, _prefs->flood_retry_ignore_prefixes, FLOOD_RETRY_IGNORE_PREFIXES);
    sprintf(reply, "> %s", tmp[0] ? tmp : "none");
  } else if (memcmp(config, "flood.retry.advert", 18) == 0) {
    sprintf(reply, "> %s", _prefs->flood_retry_advert_enabled ? "on" : "off");
  } else if (memcmp(config, "flood.retry.bridge", 18) == 0) {
    sprintf(reply, "> %s", _prefs->flood_retry_bridge_enabled ? "on" : "off");
  } else if (memcmp(config, "flood.retry.bucket.", 19) == 0) {
    uint8_t bucket = atoi(&config[19]);
    if (bucket >= 1 && bucket <= FLOOD_RETRY_BRIDGE_BUCKETS) {
      formatFloodRetryPrefixList(tmp, _prefs->flood_retry_bridge_buckets[bucket - 1], FLOOD_RETRY_BUCKET_PREFIXES);
      sprintf(reply, "> %s", tmp[0] ? tmp : "none");
    } else {
      sprintf(reply, "Error, bucket 1-%d", FLOOD_RETRY_BRIDGE_BUCKETS);
    }
  } else if (memcmp(config, "direct.retry.cr", 15) == 0) {
    if (!_prefs->direct_retry_cr_enabled) {
      strcpy(reply, "> off");
    } else if (!_callbacks->supportsAdvancedRetryConfig()) {
      strcpy(reply, "> on");
    } else {
      char cr4[12], cr5[12], cr7[12], cr8[12];
      formatSnrDbX4(cr4, sizeof(cr4), _prefs->direct_retry_cr4_snr_x4);
      formatSnrDbX4(cr5, sizeof(cr5), _prefs->direct_retry_cr5_snr_x4);
      formatSnrDbX4(cr7, sizeof(cr7), _prefs->direct_retry_cr7_snr_x4);
      formatSnrDbX4(cr8, sizeof(cr8), _prefs->direct_retry_cr8_snr_x4);
      sprintf(reply, "> %s,%s,%s,%s", cr4, cr5, cr7, cr8);
    }
  } else if (parseRecentRepeaterGet(config, recent_page)) {
    _callbacks->formatRecentRepeatersReply(reply, recent_page);
  } else if (memcmp(config, "owner.info", 10) == 0) {
    *reply++ = '>';
    *reply++ = ' ';
    const char* sp = _prefs->owner_info;
    while (*sp) {
      *reply++ = (*sp == '\n') ? '|' : *sp;    // translate newline back to orig '|'
      sp++;
    }
    *reply = 0;  // set null terminator
  } else if (memcmp(config, "path.hash.mode", 14) == 0) {
    sprintf(reply, "> %d", (uint32_t)_prefs->path_hash_mode);
  } else if (memcmp(config, "loop.detect", 11) == 0) {
    if (_prefs->loop_detect == LOOP_DETECT_OFF) {
      strcpy(reply, "> off");
    } else if (_prefs->loop_detect == LOOP_DETECT_MINIMAL) {
      strcpy(reply, "> minimal");
    } else if (_prefs->loop_detect == LOOP_DETECT_MODERATE) {
      strcpy(reply, "> moderate");
    } else {
      strcpy(reply, "> strict");
    }
  } else if (memcmp(config, "tx", 2) == 0 && (config[2] == 0 || config[2] == ' ')) {
    sprintf(reply, "> %d", (int32_t) _prefs->tx_power_dbm);
  } else if (memcmp(config, "freq", 4) == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(_prefs->freq));
  } else if (memcmp(config, "public.key", 10) == 0) {
    strcpy(reply, "> ");
    mesh::Utils::toHex(&reply[2], _callbacks->getSelfId().pub_key, PUB_KEY_SIZE);
  } else if (memcmp(config, "role", 4) == 0) {
    sprintf(reply, "> %s", _callbacks->getRole());
  } else if (memcmp(config, "bridge.type", 11) == 0) {
    sprintf(reply, "> %s",
#ifdef WITH_RS232_BRIDGE
            "rs232"
#elif WITH_ESPNOW_BRIDGE
            "espnow"
#else
            "none"
#endif
    );
#ifdef WITH_BRIDGE
  } else if (memcmp(config, "bridge.enabled", 14) == 0) {
    sprintf(reply, "> %s", _prefs->bridge_enabled ? "on" : "off");
  } else if (memcmp(config, "bridge.delay", 12) == 0) {
    sprintf(reply, "> %d", (uint32_t)_prefs->bridge_delay);
  } else if (memcmp(config, "bridge.source", 13) == 0) {
    sprintf(reply, "> %s", _prefs->bridge_pkt_src ? "logRx" : "logTx");
#endif
#ifdef WITH_RS232_BRIDGE
  } else if (memcmp(config, "bridge.baud", 11) == 0) {
    sprintf(reply, "> %d", (uint32_t)_prefs->bridge_baud);
#endif
#ifdef WITH_ESPNOW_BRIDGE
  } else if (memcmp(config, "bridge.channel", 14) == 0) {
    sprintf(reply, "> %d", (uint32_t)_prefs->bridge_channel);
  } else if (memcmp(config, "bridge.secret", 13) == 0) {
    sprintf(reply, "> %s", _prefs->bridge_secret);
#endif
  } else if (memcmp(config, "bootloader.ver", 14) == 0) {
  #ifdef NRF52_PLATFORM
      char ver[32];
      if (_board->getBootloaderVersion(ver, sizeof(ver))) {
          sprintf(reply, "> %s", ver);
      } else {
          strcpy(reply, "> unknown");
      }
  #else
      strcpy(reply, "Error: unsupported");
  #endif
  } else if (memcmp(config, "adc.multiplier", 14) == 0) {
    float adc_mult = _board->getAdcMultiplier();
    if (adc_mult == 0.0f) {
      strcpy(reply, "Error: unsupported");
    } else {
      sprintf(reply, "> %.3f", adc_mult);
    }
  // Power management commands
  } else if (memcmp(config, "pwrmgt.support", 14) == 0) {
#ifdef NRF52_POWER_MANAGEMENT
    strcpy(reply, "> supported");
#else
    strcpy(reply, "> unsupported");
#endif
  } else if (memcmp(config, "pwrmgt.source", 13) == 0) {
#ifdef NRF52_POWER_MANAGEMENT
    strcpy(reply, _board->isExternalPowered() ? "> external" : "> battery");
#else
    strcpy(reply, "ERROR: Power management not supported");
#endif
  } else if (memcmp(config, "pwrmgt.bootreason", 17) == 0) {
    sprintf(reply, "> Reset: %s; Shutdown: %s",
      _board->getResetReasonString(_board->getResetReason()),
      _board->getShutdownReasonString(_board->getShutdownReason()));
  } else if (memcmp(config, "pwrmgt.bootmv", 13) == 0) {
#ifdef NRF52_POWER_MANAGEMENT
    sprintf(reply, "> %u mV", _board->getBootVoltage());
#else
    strcpy(reply, "ERROR: Power management not supported");
#endif
  } else if (memcmp(config, "reboot.interval", 15) == 0) {
    if (_prefs->reboot_interval == 0) {
      strcpy(reply, "disabled");
    } else {
      sprintf(reply, "> %d", (uint8_t)_prefs->reboot_interval);
    }
  } else {
    sprintf(reply, "??: %s", config);
  }
}

void CommonCLI::handleDelCmd(char* command, char* reply) {
  const char* config = &command[4];
  if (memcmp(config, "tempradioat", 11) == 0 && (config[11] == 0 || config[11] == ' ')) {
    _callbacks->deleteScheduledRadioParams(true, skipSpacesConst(&config[11]), reply);
  } else if (memcmp(config, "radioat", 7) == 0 && (config[7] == 0 || config[7] == ' ')) {
    _callbacks->deleteScheduledRadioParams(false, skipSpacesConst(&config[7]), reply);
  } else if (memcmp(config, "flood.channel.block", 19) == 0
      && (config[19] == ' ' || config[19] == '.')) {
    const char* cursor = &config[19];
    int index = 0;
    if (!parseFloodChannelBlockDotIndex(cursor, index)) {
      sprintf(reply, "Error, index 1-%d", FLOOD_CHANNEL_BLOCK_SLOTS);
      return;
    }
    cursor = skipSpacesConst(cursor);
    char selector[8];
    if (index > 0 && *cursor != 0) {
      strcpy(reply, "Error, use index or selector");
      return;
    }
    if (index > 0) {
      snprintf(selector, sizeof(selector), "%d", index);
      _callbacks->deleteFloodChannelBlock(selector, reply);
    } else if (*cursor != 0) {
      _callbacks->deleteFloodChannelBlock(cursor, reply);
    } else {
      strcpy(reply, "Error, use: del flood.channel.block <index|name|prefix>");
    }
  } else {
    strcpy(reply, "unknown del: ");
    StrHelper::strncpy(&reply[13], config, 160 - 14);
  }
}

static char* skipSpaces(char* s) {
  while (*s == ' ') s++;
  return s;
}

static void rtrimSpaces(char* s) {
  char* e = s + strlen(s);
  while (e > s && e[-1] == ' ') *--e = '\0';
}

static char* takeToken(char** cursor) {
  char* p = skipSpaces(*cursor);
  if (*p == '\0') { *cursor = p; return nullptr; }
  char* tok = p;
  while (*p && *p != ' ') p++;
  if (*p) *p++ = '\0';
  *cursor = p;
  return tok;
}

static char* splitNameJump(char* tok) {
  for (char* q = tok; *q; q++) {
    if (*q == '|' || *q == ',') {
      *q = '\0';
      char* jump = skipSpaces(q + 1);
      rtrimSpaces(jump);
      return jump;
    }
  }
  return nullptr;
}

static bool processRegionDefSegment(RegionMap* map, char* tok, RegionEntry** cursor, char* reply) {
  char* jump = splitNameJump(tok);
  char* name = skipSpaces(tok);
  if (*name == '\0') { snprintf(reply, 160, "Err - empty name"); return false; }
  if (jump && *jump == '\0') { snprintf(reply, 160, "Err - empty jump"); return false; }

  RegionEntry* r = map->putRegion(name, (*cursor)->id);
  if (r == NULL) { snprintf(reply, 160, "Err - put failed: %s", name); return false; }
  r->flags = 0;

  if (jump) {
    RegionEntry* j = map->findByNamePrefix(jump);
    if (j == NULL) { snprintf(reply, 160, "Err - unknown or ambiguous jump: %s", jump); return false; }
    *cursor = j;
  } else {
    *cursor = r;
  }
  return true;
}

void CommonCLI::handleRegionCmd(char* command, char* reply) {
  reply[0] = 0;

  // `region def`: must run before parseTextParts mutates the buffer
  char* cmd = skipSpaces(command);
  if (strncmp(cmd, "region def", 10) == 0 && (cmd[10] == ' ' || cmd[10] == '\0')) {
    char* payload = skipSpaces(cmd + 10);
    rtrimSpaces(payload);
    if (*payload == '\0') { snprintf(reply, 160, "Err - empty def"); return; }

    // Build the complete definition on a staged copy. A bad name, jump, table
    // overflow, or cycle leaves the live hierarchy completely unchanged.
    RegionMap staged(*_region_map);
    RegionEntry* cursor = &staged.getWildcard();
    for (char* tok; (tok = takeToken(&payload)) != nullptr; ) {
      if (!processRegionDefSegment(&staged, tok, &cursor, reply)) return;
    }
    *_region_map = staged;
    _region_map->exportTo(reply, 160);
    return;
  }

  const char* parts[4];
  int n = mesh::Utils::parseTextParts(command, parts, 4, ' ');
  if (n == 1) {
    _region_map->exportTo(reply, 160);
  } else if (n >= 2 && strcmp(parts[1], "load") == 0) {
    _callbacks->startRegionsLoad();
  } else if (n >= 2 && strcmp(parts[1], "save") == 0) {
    _prefs->discovery_mod_timestamp = getRTCClock()->getCurrentTime();   // this node is now 'modified' (for discovery info)
    savePrefs();
    bool success = _callbacks->saveRegions();
    strcpy(reply, success ? "OK" : "Err - save failed");
  } else if (n >= 3 && strcmp(parts[1], "allowf") == 0) {
    auto region = _region_map->findByNamePrefix(parts[2]);
    if (region) {
      region->flags &= ~REGION_DENY_FLOOD;
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Err - unknown region");
    }
  } else if (n >= 3 && strcmp(parts[1], "denyf") == 0) {
    auto region = _region_map->findByNamePrefix(parts[2]);
    if (region) {
      region->flags |= REGION_DENY_FLOOD;
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Err - unknown region");
    }
  } else if (n >= 3 && strcmp(parts[1], "get") == 0) {
    auto region = _region_map->findByNamePrefix(parts[2]);
    if (region) {
      auto parent = _region_map->findById(region->parent);
      if (parent && parent->id != 0) {
        sprintf(reply, " %s (%s) %s", region->name, parent->name, (region->flags & REGION_DENY_FLOOD) ? "" : "F");
      } else {
        sprintf(reply, " %s %s", region->name, (region->flags & REGION_DENY_FLOOD) ? "" : "F");
      }
    } else {
      strcpy(reply, "Err - unknown region");
    }
  } else if (n >= 3 && strcmp(parts[1], "home") == 0) {
    auto home = _region_map->findByNamePrefix(parts[2]);
    if (home) {
      _region_map->setHomeRegion(home);
      sprintf(reply, " home is now %s", home->name);
    } else {
      strcpy(reply, "Err - unknown region");
    }
  } else if (n == 2 && strcmp(parts[1], "home") == 0) {
    auto home = _region_map->getHomeRegion();
    sprintf(reply, " home is %s", home ? home->name : "*");
  } else if (n >= 3 && strcmp(parts[1], "default") == 0) {
    if (strcmp(parts[2], "<null>") == 0) {
      _region_map->setDefaultRegion(NULL);
      _callbacks->onDefaultRegionChanged(NULL);
      _callbacks->saveRegions();  // persist in one atomic step
      sprintf(reply, " default scope is now <null>");
    } else {
      auto def = _region_map->findByNamePrefix(parts[2]);
      if (def == NULL) {
        def = _region_map->putRegion(parts[2], 0);  // auto-create the default region
      }
      if (def) {
        def->flags = 0;   // make sure allow flood enabled
        _region_map->setDefaultRegion(def);
        _callbacks->onDefaultRegionChanged(def);
        _callbacks->saveRegions();  // persist in one atomic step
        sprintf(reply, " default scope is now %s", def->name);
      } else {
        strcpy(reply, "Err - region table full");
      }
    }
  } else if (n == 2 && strcmp(parts[1], "default") == 0) {
    auto def = _region_map->getDefaultRegion();
    sprintf(reply, " default scope is %s", def ? def->name : "<null>");
  } else if (n >= 3 && strcmp(parts[1], "put") == 0) {
    auto parent = n >= 4 ? _region_map->findByNamePrefix(parts[3]) : &(_region_map->getWildcard());
    if (parent == NULL) {
      strcpy(reply, "Err - unknown parent");
    } else {
      auto region = _region_map->putRegion(parts[2], parent->id);
      if (region == NULL) {
        strcpy(reply, "Err - unable to put");
      } else {
        region->flags = 0;   // New default: enable flood
        strcpy(reply, "OK - (flood allowed)");
      }
    }
  } else if (n >= 3 && strcmp(parts[1], "remove") == 0) {
    auto region = _region_map->findByName(parts[2]);
    if (region) {
      RegionEntry* current_default = _region_map->getDefaultRegion();
      bool removed_default = current_default != NULL && current_default->id == region->id;
      if (_region_map->removeRegion(*region)) {
        if (removed_default) _callbacks->onDefaultRegionChanged(NULL);
        strcpy(reply, "OK");
      } else {
        strcpy(reply, "Err - not empty");
      }
    } else {
      strcpy(reply, "Err - not found");
    }
  } else if (n >= 3 && strcmp(parts[1], "list") == 0) {
    uint8_t mask = 0;
    bool invert = false;
    
    if (strcmp(parts[2], "allowed") == 0) {
      mask = REGION_DENY_FLOOD;
      invert = false;  // list regions that DON'T have DENY flag
    } else if (strcmp(parts[2], "denied") == 0) {
      mask = REGION_DENY_FLOOD;
      invert = true;   // list regions that DO have DENY flag
    } else {
      strcpy(reply, "Err - use 'allowed' or 'denied'");
      return;
    }
    
    int len = _region_map->exportNamesTo(reply, 160, mask, invert);
    if (len == 0) {
      strcpy(reply, "-none-");
    }
  } else {
    strcpy(reply, "Err - ??");
  }
}
