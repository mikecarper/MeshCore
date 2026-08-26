#include "FloodRuleEngine.h"

#if MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE

#include <stddef.h>
#include <string.h>

#include <helpers/FloodFilterPolicy.h>
#include <helpers/RegionNameUtils.h>

namespace {

static const char RULE_FILE[] = "/flood_filter";
static const char RULE_TEMP_FILE[] = "/flood_filter.tmp";
static const char RULE_BACKUP_FILE[] = "/flood_filter.bak";
static const char RULE_USAGE[] =
    "Err - use: set flood.rule[.n] type=<type> [hops=<range>] [...]";
static const char DUPLICATE_OPTION[] = "Err - duplicate filter option";

static_assert(CIPHER_KEY_SIZE == FloodFilterPolicy::CHANNEL_KEY_128_LEN,
              "flood rule 128-bit key encoding changed");
static_assert(PUB_KEY_SIZE == FloodFilterPolicy::CHANNEL_KEY_256_LEN,
              "flood rule 256-bit key encoding changed");

// Channel encryption uses a 128-bit key, while MACThenDecrypt takes a
// PUB_KEY_SIZE buffer. Keep the unused half zero-padded like GroupChannel.
static const uint8_t PUBLIC_CHANNEL_SECRET[PUB_KEY_SIZE] = {
  0x8b, 0x33, 0x87, 0xe9, 0xc5, 0xcd, 0xea, 0x6a,
  0xc9, 0xe5, 0xed, 0xba, 0xa1, 0x15, 0xcd, 0x72
};

static const char* skipSpaces(const char* text) {
  while (text != NULL && *text == ' ') text++;
  return text == NULL ? "" : text;
}

static bool asciiEqual(const char* left, const char* right) {
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

static bool asciiStartsWith(const char* text, const char* prefix) {
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

static void copyString(char* dest, const char* src, size_t dest_len) {
  if (dest == NULL || dest_len == 0) return;
  if (src == NULL) src = "";
  size_t len = strlen(src);
  if (len >= dest_len) len = dest_len - 1;
  memcpy(dest, src, len);
  dest[len] = 0;
}

static bool parseUnsigned(const char* text, uint32_t maximum,
                          uint32_t& value) {
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

static const char* payloadTypeName(uint8_t type) {
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
    case FloodRuleEngine::ANY_TYPE: return "any";
    default: return "invalid";
  }
}

static bool parsePayloadType(const char* text, uint8_t& type) {
  uint32_t numeric = 0;
  if (parseUnsigned(text, PH_TYPE_MASK, numeric)) {
    type = (uint8_t)numeric;
    return true;
  }
  if (text != NULL && text[0] == '0'
      && (text[1] == 'x' || text[1] == 'X') && text[2] != 0) {
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
  if (asciiStartsWith(text, "payload_type_")) text += strlen("payload_type_");
  if (asciiEqual(text, "any")) type = FloodRuleEngine::ANY_TYPE;
  else if (asciiEqual(text, "req")) type = PAYLOAD_TYPE_REQ;
  else if (asciiEqual(text, "response") || asciiEqual(text, "resp")) type = PAYLOAD_TYPE_RESPONSE;
  else if (asciiEqual(text, "txt_msg") || asciiEqual(text, "txt")) type = PAYLOAD_TYPE_TXT_MSG;
  else if (asciiEqual(text, "ack")) type = PAYLOAD_TYPE_ACK;
  else if (asciiEqual(text, "advert")) type = PAYLOAD_TYPE_ADVERT;
  else if (asciiEqual(text, "grp_txt") || asciiEqual(text, "group_text")) type = PAYLOAD_TYPE_GRP_TXT;
  else if (asciiEqual(text, "grp_data") || asciiEqual(text, "group_data")) type = PAYLOAD_TYPE_GRP_DATA;
  else if (asciiEqual(text, "anon_req")) type = PAYLOAD_TYPE_ANON_REQ;
  else if (asciiEqual(text, "path")) type = PAYLOAD_TYPE_PATH;
  else if (asciiEqual(text, "trace")) type = PAYLOAD_TYPE_TRACE;
  else if (asciiEqual(text, "multipart")) type = PAYLOAD_TYPE_MULTIPART;
  else if (asciiEqual(text, "control")) type = PAYLOAD_TYPE_CONTROL;
  else if (asciiEqual(text, "ota")) type = PAYLOAD_TYPE_OTA;
  else if (asciiEqual(text, "raw") || asciiEqual(text, "raw_custom")) type = PAYLOAD_TYPE_RAW_CUSTOM;
  else return false;
  return true;
}

static bool parseHopSpec(const char* text, uint8_t& min_hops,
                         uint8_t& max_hops) {
  if (text == NULL || *text == 0) return false;
  if (asciiEqual(text, "all")) {
    min_hops = 0;
    max_hops = FloodRuleEngine::MAX_HOPS;
    return true;
  }
  char spec[12];
  if (strlen(text) >= sizeof(spec)) return false;
  copyString(spec, text, sizeof(spec));
  size_t len = strlen(spec);
  uint32_t parsed_min = 0;
  uint32_t parsed_max = 0;
  if (len > 1 && spec[len - 1] == '+') {
    spec[len - 1] = 0;
    if (!parseUnsigned(spec, FloodRuleEngine::MAX_HOPS, parsed_min)) return false;
    min_hops = (uint8_t)parsed_min;
    max_hops = FloodRuleEngine::MAX_HOPS;
    return true;
  }
  char* dash = strchr(spec, '-');
  if (dash != NULL) {
    *dash++ = 0;
    if (!parseUnsigned(spec, FloodRuleEngine::MAX_HOPS, parsed_min)
        || !parseUnsigned(dash, FloodRuleEngine::MAX_HOPS, parsed_max)
        || parsed_min > parsed_max) {
      return false;
    }
    min_hops = (uint8_t)parsed_min;
    max_hops = (uint8_t)parsed_max;
    return true;
  }
  if (!parseUnsigned(spec, FloodRuleEngine::MAX_HOPS, parsed_min)) return false;
  min_hops = max_hops = (uint8_t)parsed_min;
  return true;
}

static void formatHopSpec(char* dest, size_t dest_len, uint8_t min_hops,
                          uint8_t max_hops) {
  if (min_hops == 0 && max_hops == FloodRuleEngine::MAX_HOPS) {
    snprintf(dest, dest_len, "all");
  } else if (max_hops == FloodRuleEngine::MAX_HOPS) {
    snprintf(dest, dest_len, "%u+", (unsigned int)min_hops);
  } else if (min_hops == max_hops) {
    snprintf(dest, dest_len, "%u", (unsigned int)min_hops);
  } else {
    snprintf(dest, dest_len, "%u-%u", (unsigned int)min_hops,
             (unsigned int)max_hops);
  }
}

static bool normalizeScopeName(const char* text, char* dest,
                               size_t dest_len) {
  if (text == NULL || dest == NULL || dest_len < 3) return false;
  if (*text == '#') text++;
  if (*text == 0 || *text == '$') return false;
  size_t len = 0;
  while (text[len]) {
    uint8_t c = (uint8_t)text[len];
    if (!RegionMap::is_name_char(c) || c == '#' || c == '$') return false;
    len++;
  }
  if (len + 2 > dest_len) return false;
  dest[0] = '#';
  memcpy(&dest[1], text, len + 1);
  return true;
}

static bool validStoredScopeName(const char* text) {
  if (text == NULL || text[0] != '#' || text[1] == 0) return false;
  for (size_t i = 1; i < FloodRuleEngine::NAME_LEN; i++) {
    uint8_t c = (uint8_t)text[i];
    if (c == 0) return true;
    if (!RegionMap::is_name_char(c) || c == '#' || c == '$') return false;
  }
  return false;
}

static bool validStoredRegionName(const char* text) {
  if (text == NULL || text[0] == 0 || strcmp(text, "*") == 0) return false;
  for (size_t i = 0; i < FloodRuleEngine::NAME_LEN; i++) {
    uint8_t c = (uint8_t)text[i];
    if (c == 0) return true;
    if (!RegionMap::is_name_char(c)) return false;
  }
  return false;
}

static void deriveScopeKey(const char* scope_name, TransportKey& scope) {
  mesh::Utils::sha256(scope.key, sizeof(scope.key),
                      (const uint8_t*)scope_name, strlen(scope_name));
}

static bool parseChannel(const char* text, uint8_t secret[PUB_KEY_SIZE],
                         uint8_t& key_len, uint8_t& channel_hash,
                         char* name, size_t name_len) {
  if (text == NULL || *text == 0) return false;
  memset(secret, 0, PUB_KEY_SIZE);
  if (FloodFilterPolicy::parseChannelHashMatcher(text, channel_hash)) {
    key_len = FloodFilterPolicy::CHANNEL_HASH_ONLY_LEN;
    secret[0] = channel_hash;
    int written = snprintf(name, name_len, "hash:%02X", channel_hash);
    return written >= 0 && (size_t)written < name_len;
  } else if (asciiEqual(text, "public")) {
    key_len = CIPHER_KEY_SIZE;
    memcpy(secret, PUBLIC_CHANNEL_SECRET, sizeof(PUBLIC_CHANNEL_SECRET));
    copyString(name, "public", name_len);
  } else if (text[0] == '#' && text[1] != 0) {
    if (strlen(text) >= name_len) return false;
    key_len = CIPHER_KEY_SIZE;
    mesh::Utils::sha256(secret, key_len, (const uint8_t*)text,
                        strlen(text));
    copyString(name, text, name_len);
  } else {
    size_t hex_len = strlen(text);
    if (hex_len != CIPHER_KEY_SIZE * 2 && hex_len != PUB_KEY_SIZE * 2) {
      return false;
    }
    for (size_t i = 0; i < hex_len; i++) {
      if (!mesh::Utils::isHexChar(text[i])) return false;
    }
    key_len = (uint8_t)(hex_len / 2);
    if (!mesh::Utils::fromHex(secret, key_len, text)) return false;
    uint8_t prefix[4];
    mesh::Utils::sha256(prefix, sizeof(prefix), secret, key_len);
    char prefix_hex[9];
    mesh::Utils::toHex(prefix_hex, prefix, sizeof(prefix));
    snprintf(name, name_len, "key:%s", prefix_hex);
  }
  mesh::Utils::sha256(&channel_hash, sizeof(channel_hash), secret, key_len);
  return true;
}

static bool parsePathPrefix(const char* text, uint8_t& hash_size,
                            uint8_t& path_hops,
                            uint8_t path[FloodRuleEngine::PATH_PREFIX_BYTES_MAX]) {
  memset(path, 0, FloodRuleEngine::PATH_PREFIX_BYTES_MAX);
  if (text == NULL || *text == 0 || strcmp(text, "*") == 0) {
    hash_size = 0;
    path_hops = 0;
    return text != NULL && *text != 0;
  }
  if (strlen(text) >= 32) return false;
  char input[32];
  copyString(input, text, sizeof(input));
  char* token = input;
  uint8_t parsed_size = 0;
  uint8_t count = 0;
  while (token != NULL) {
    char* comma = strchr(token, ',');
    if (comma != NULL) *comma = 0;
    size_t hex_len = strlen(token);
    if (hex_len != 2 && hex_len != 4 && hex_len != 6) return false;
    uint8_t token_size = (uint8_t)(hex_len / 2);
    if ((parsed_size != 0 && parsed_size != token_size)
        || count >= FloodRuleEngine::PATH_PREFIX_HOPS_MAX) {
      return false;
    }
    for (size_t i = 0; i < hex_len; i++) {
      if (!mesh::Utils::isHexChar(token[i])) return false;
    }
    parsed_size = token_size;
    if (!mesh::Utils::fromHex(&path[count * parsed_size], parsed_size,
                              token)) {
      return false;
    }
    count++;
    token = comma == NULL ? NULL : comma + 1;
  }
  if (count == 0) return false;
  hash_size = parsed_size;
  path_hops = count;
  return true;
}

static void formatPathPrefix(
    char* dest, size_t dest_len, uint8_t hash_size, uint8_t path_hops,
    const uint8_t path[FloodRuleEngine::PATH_PREFIX_BYTES_MAX]) {
  if (hash_size == 0 || path_hops == 0) {
    copyString(dest, "*", dest_len);
    return;
  }
  size_t used = 0;
  dest[0] = 0;
  for (uint8_t i = 0; i < path_hops; i++) {
    char hop[7];
    mesh::Utils::toHex(hop, &path[i * hash_size], hash_size);
    int written = snprintf(&dest[used], dest_len - used, "%s%s",
                           i == 0 ? "" : ",", hop);
    if (written < 0 || (size_t)written >= dest_len - used) {
      dest[dest_len - 1] = 0;
      return;
    }
    used += (size_t)written;
  }
}

static bool commandMatches(const char* command, const char* base) {
  size_t len = strlen(base);
  return strncmp(command, base, len) == 0
      && (command[len] == 0 || command[len] == '.' || command[len] == ' ');
}

static File openRead(FILESYSTEM* fs, const char* path) {
  return fs->open(path);
}

static File openWrite(FILESYSTEM* fs, const char* path) {
  return fs->open(path, "w", true);
}

static uint32_t updateFileHash(uint32_t hash,
                               const uint8_t* data, size_t len) {
  while (len-- > 0) {
    hash ^= *data++;
    hash *= 16777619UL;
  }
  return hash;
}

static bool verifyWrittenFile(FILESYSTEM* fs, const char* path,
                              size_t expected_size,
                              uint32_t expected_hash) {
  File file = openRead(fs, path);
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
    hash = updateFileHash(hash, buffer, amount);
    remaining -= amount;
  }
  file.close();
  return hash == expected_hash;
}

}  // namespace

FloodRuleEngine::FloodRuleEngine() : _fs(NULL), _regions(NULL) {
  memset(_entries, 0, sizeof(_entries));
}

void FloodRuleEngine::begin(FILESYSTEM* fs, RegionMap* regions) {
  _fs = fs;
  _regions = regions;
  load();
}

void FloodRuleEngine::seedDefaults() {
  memset(_entries, 0, sizeof(_entries));
  Entry& entry = _entries[0];
  entry.active = true;
  entry.payload_type = PAYLOAD_TYPE_OTA;
  entry.min_hops = 0;
  entry.max_hops = MAX_HOPS;
  entry.suspend_on_temp_radio = true;
  entry.drop_on_match = true;
}

void FloodRuleEngine::load() {
  if (_fs == NULL) {
    seedDefaults();
    return;
  }

  enum class FileState : uint8_t { Missing, Valid, Invalid, Unreadable };
  auto loadFile = [this](const char* path) -> FileState {
    memset(_entries, 0, sizeof(_entries));
    if (!_fs->exists(path)) return FileState::Missing;
    File file = openRead(_fs, path);
    if (!file) return FileState::Unreadable;

    Entry* loaded = _entries;
    auto readExact = [&file](void* dest, size_t len) {
      return file.read((uint8_t*)dest, len) == len;
    };

  uint8_t magic[4];
  uint8_t count = 0;
  bool success = readExact(magic, sizeof(magic));
  bool version_6 = success && memcmp(magic, "FPF6", 4) == 0;
  bool version_7 = success && memcmp(magic, "FPF7", 4) == 0;
  success = (version_6 || version_7)
      && readExact(&count, sizeof(count)) && count <= RULE_SLOTS;

  for (int i = 0; success && i < count; i++) {
    uint8_t active = 0;
    uint8_t suspend_on_temp_radio = 0;
    uint8_t match_blacklisted_path = 0;
    uint8_t scope_requires_region_match = 0;
    uint8_t scope_uses_slow_timing = 0;
    uint8_t drop_on_match = 0;
    uint8_t rate_limit_enabled = 0;
    uint8_t stop_on_match = 0;
    uint8_t stored_rule_channel = 0;

    success = readExact(&active, sizeof(active))
        && readExact(&loaded[i].payload_type,
                     sizeof(loaded[i].payload_type))
        && readExact(&loaded[i].min_hops, sizeof(loaded[i].min_hops))
        && readExact(&loaded[i].max_hops, sizeof(loaded[i].max_hops))
        && readExact(&suspend_on_temp_radio,
                     sizeof(suspend_on_temp_radio))
        && readExact(loaded[i].scope_name, sizeof(loaded[i].scope_name))
        && readExact(&match_blacklisted_path,
                     sizeof(match_blacklisted_path))
        && readExact(&scope_requires_region_match,
                     sizeof(scope_requires_region_match))
        && readExact(&scope_uses_slow_timing,
                     sizeof(scope_uses_slow_timing));
    if (success && version_7) {
      success = readExact(&loaded[i].incoming_scope_kind,
                          sizeof(loaded[i].incoming_scope_kind))
          && readExact(loaded[i].incoming_scope_name,
                       sizeof(loaded[i].incoming_scope_name))
          && readExact(&stored_rule_channel,
                       sizeof(stored_rule_channel))
          && readExact(loaded[i].channel_secret,
                       sizeof(loaded[i].channel_secret))
          && readExact(loaded[i].channel_name,
                       sizeof(loaded[i].channel_name))
          && readExact(&loaded[i].path_hash_size,
                       sizeof(loaded[i].path_hash_size))
          && readExact(&loaded[i].path_hops,
                       sizeof(loaded[i].path_hops))
          && readExact(loaded[i].path, sizeof(loaded[i].path))
          && readExact(&drop_on_match, sizeof(drop_on_match))
          && readExact(&rate_limit_enabled, sizeof(rate_limit_enabled))
          && readExact(&loaded[i].rate_per_minute,
                       sizeof(loaded[i].rate_per_minute))
          && readExact(loaded[i].target_region_name,
                       sizeof(loaded[i].target_region_name))
          && readExact(&loaded[i].priority, sizeof(loaded[i].priority))
          && readExact(&stop_on_match, sizeof(stop_on_match));
    } else {
      loaded[i].incoming_scope_kind = scope_requires_region_match
          ? FloodFilterPolicy::RULE_IN_ALLOWED
          : FloodFilterPolicy::RULE_IN_ANY;
      drop_on_match = loaded[i].scope_name[0] == 0 ? 1 : 0;
    }
    if (success && version_7) {
      success = FloodFilterPolicy::decodeStoredRuleChannel(
          stored_rule_channel, loaded[i].channel_key_len,
          loaded[i].retry_on_match);
    } else {
      loaded[i].retry_on_match = false;
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

    bool scope_name_terminated = memchr(
        loaded[i].scope_name, 0, sizeof(loaded[i].scope_name)) != NULL;
    bool input_name_terminated = memchr(
        loaded[i].incoming_scope_name, 0,
        sizeof(loaded[i].incoming_scope_name)) != NULL;
    bool channel_name_terminated = memchr(
        loaded[i].channel_name, 0,
        sizeof(loaded[i].channel_name)) != NULL;
    bool target_name_terminated = memchr(
        loaded[i].target_region_name, 0,
        sizeof(loaded[i].target_region_name)) != NULL;
    bool direct_target = scope_name_terminated
        && loaded[i].scope_name[0] != 0;
    bool region_target = target_name_terminated
        && loaded[i].target_region_name[0] != 0;

    bool incoming_valid = loaded[i].incoming_scope_kind
        <= FloodFilterPolicy::RULE_IN_REGION;
    if (incoming_valid
        && loaded[i].incoming_scope_kind == FloodFilterPolicy::RULE_IN_SCOPE) {
      incoming_valid = input_name_terminated
          && validStoredScopeName(loaded[i].incoming_scope_name);
    } else if (incoming_valid
        && loaded[i].incoming_scope_kind == FloodFilterPolicy::RULE_IN_REGION) {
      incoming_valid = input_name_terminated
          && validStoredRegionName(loaded[i].incoming_scope_name);
    } else if (incoming_valid) {
      incoming_valid = input_name_terminated
          && loaded[i].incoming_scope_name[0] == 0;
    }

    bool channel_valid = FloodFilterPolicy::channelKeyLengthSupported(
        loaded[i].channel_key_len);
    if (channel_valid && loaded[i].channel_key_len == 0) {
      channel_valid = channel_name_terminated
          && loaded[i].channel_name[0] == 0;
    } else if (channel_valid
        && FloodFilterPolicy::channelHashOnly(
            loaded[i].channel_key_len)) {
      channel_valid = channel_name_terminated;
      if (channel_valid) {
        loaded[i].channel_hash = loaded[i].channel_secret[0];
        memset(&loaded[i].channel_secret[1], 0,
               sizeof(loaded[i].channel_secret) - 1);
        snprintf(loaded[i].channel_name,
                 sizeof(loaded[i].channel_name), "hash:%02X",
                 loaded[i].channel_hash);
      }
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
      channel_valid = loaded[i].payload_type == ANY_TYPE
          || loaded[i].payload_type == PAYLOAD_TYPE_GRP_TXT
          || loaded[i].payload_type == PAYLOAD_TYPE_GRP_DATA;
    }

    bool path_valid = (loaded[i].path_hash_size == 0
            && loaded[i].path_hops == 0)
        || (!loaded[i].match_blacklisted_path
            && loaded[i].path_hash_size >= 1
            && loaded[i].path_hash_size <= 3
            && loaded[i].path_hops >= 1
            && loaded[i].path_hops <= PATH_PREFIX_HOPS_MAX);
    bool action_valid = loaded[i].drop_on_match || direct_target
        || region_target || loaded[i].rate_limit_enabled
        || loaded[i].stop_on_match || loaded[i].retry_on_match;
    if (!((loaded[i].payload_type <= PH_TYPE_MASK
              || loaded[i].payload_type == ANY_TYPE)
          && loaded[i].min_hops <= loaded[i].max_hops
          && loaded[i].max_hops <= MAX_HOPS
          && scope_name_terminated
          && (!direct_target
              || validStoredScopeName(loaded[i].scope_name))
          && target_name_terminated
          && (!region_target
              || validStoredRegionName(loaded[i].target_region_name))
          && !(direct_target && region_target)
          && !(loaded[i].drop_on_match
              && (direct_target || region_target))
          && !(loaded[i].drop_on_match
              && loaded[i].rate_limit_enabled)
          && !(loaded[i].drop_on_match
              && loaded[i].retry_on_match)
          && (!loaded[i].rate_limit_enabled
              || loaded[i].rate_per_minute < RATE_UNLIMITED)
          && (!loaded[i].scope_uses_slow_timing
              || direct_target || region_target)
          && incoming_valid && channel_valid && path_valid
          && action_valid)) {
      success = false;
    }
  }
  if (success && version_7 && file.available() > 0) {
    uint8_t section_magic[4];
    uint8_t channel_data_slot = 0xFF;
    uint8_t channel_data_max_hops = 0xFF;
    uint8_t scope_count = 0;
    uint8_t direct_count = 0;
    uint8_t blacklist_count = 0;
    success = readExact(section_magic, sizeof(section_magic))
        && memcmp(section_magic, "FPS1", sizeof(section_magic)) == 0
        && readExact(&channel_data_slot, sizeof(channel_data_slot))
        && readExact(&channel_data_max_hops,
                     sizeof(channel_data_max_hops))
        && readExact(&scope_count, sizeof(scope_count))
        && readExact(&direct_count, sizeof(direct_count))
        && readExact(&blacklist_count, sizeof(blacklist_count))
        && channel_data_slot == 0xFF
        && channel_data_max_hops == 0xFF
        && scope_count == 0 && direct_count == 0
        && blacklist_count == 0
        && file.available() == 0;
  }
    file.close();
    if (!success) memset(_entries, 0, sizeof(_entries));
    return success ? FileState::Valid : FileState::Invalid;
  };

  FileState primary = loadFile(RULE_FILE);
  if (primary == FileState::Valid) {
    if (_fs->exists(RULE_TEMP_FILE)) _fs->remove(RULE_TEMP_FILE);
    if (_fs->exists(RULE_BACKUP_FILE)) _fs->remove(RULE_BACKUP_FILE);
    return;
  }
  if (primary == FileState::Unreadable) {
    return;
  }

  FileState temp = loadFile(RULE_TEMP_FILE);
  if (temp == FileState::Valid) {
    if (primary != FileState::Unreadable) {
      if (primary == FileState::Invalid) _fs->remove(RULE_FILE);
      if (!_fs->exists(RULE_FILE)
          && _fs->rename(RULE_TEMP_FILE, RULE_FILE)) {
        if (_fs->exists(RULE_BACKUP_FILE)) _fs->remove(RULE_BACKUP_FILE);
      }
    }
    return;
  }

  FileState backup = loadFile(RULE_BACKUP_FILE);
  if (backup == FileState::Valid) {
    if (primary != FileState::Unreadable) {
      if (primary == FileState::Invalid) _fs->remove(RULE_FILE);
      if (!_fs->exists(RULE_FILE)
          && _fs->rename(RULE_BACKUP_FILE, RULE_FILE)
          && temp != FileState::Unreadable
          && _fs->exists(RULE_TEMP_FILE)) {
        _fs->remove(RULE_TEMP_FILE);
      }
    }
    return;
  }

  seedDefaults();
  if (primary == FileState::Invalid) _fs->remove(RULE_FILE);
  if (temp == FileState::Invalid) _fs->remove(RULE_TEMP_FILE);
  if (backup == FileState::Invalid) _fs->remove(RULE_BACKUP_FILE);
  if (primary != FileState::Unreadable && temp != FileState::Unreadable
      && backup != FileState::Unreadable) {
    save();
  }
}

bool FloodRuleEngine::save() {
  if (_fs == NULL) return false;
  if (_fs->exists(RULE_TEMP_FILE) || _fs->exists(RULE_BACKUP_FILE))
    return false;
  File file = openWrite(_fs, RULE_TEMP_FILE);
  if (!file) return false;
  size_t bytes_written = 0;
  uint32_t write_hash = 2166136261UL;
  auto writeExact = [&file, &bytes_written, &write_hash](
      const void* src, size_t len) {
    const uint8_t* data = (const uint8_t*)src;
    if (file.write(data, len) != len) return false;
    bytes_written += len;
    write_hash = updateFileHash(write_hash, data, len);
    return true;
  };

  const uint8_t magic[4] = {'F', 'P', 'F', '7'};
  uint8_t count = RULE_SLOTS;
  bool success = writeExact(magic, sizeof(magic))
      && writeExact(&count, sizeof(count));
  for (int i = 0; success && i < RULE_SLOTS; i++) {
    const Entry& entry = _entries[i];
    uint8_t active = entry.active ? 1 : 0;
    uint8_t suspend_on_temp_radio = entry.suspend_on_temp_radio ? 1 : 0;
    uint8_t match_blacklisted_path = entry.match_blacklisted_path ? 1 : 0;
    uint8_t scope_requires_region_match =
        entry.incoming_scope_kind == FloodFilterPolicy::RULE_IN_ALLOWED
            ? 1 : 0;
    uint8_t scope_uses_slow_timing = entry.scope_uses_slow_timing ? 1 : 0;
    uint8_t drop_on_match = entry.drop_on_match ? 1 : 0;
    uint8_t rate_limit_enabled = entry.rate_limit_enabled ? 1 : 0;
    uint8_t stop_on_match = entry.stop_on_match ? 1 : 0;
    uint8_t stored_rule_channel =
        FloodFilterPolicy::encodeStoredRuleChannel(
            entry.channel_key_len, entry.retry_on_match);
    success = writeExact(&active, sizeof(active))
        && writeExact(&entry.payload_type, sizeof(entry.payload_type))
        && writeExact(&entry.min_hops, sizeof(entry.min_hops))
        && writeExact(&entry.max_hops, sizeof(entry.max_hops))
        && writeExact(&suspend_on_temp_radio,
                      sizeof(suspend_on_temp_radio))
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
        && writeExact(&stored_rule_channel, sizeof(stored_rule_channel))
        && writeExact(entry.channel_secret, sizeof(entry.channel_secret))
        && writeExact(entry.channel_name, sizeof(entry.channel_name))
        && writeExact(&entry.path_hash_size, sizeof(entry.path_hash_size))
        && writeExact(&entry.path_hops, sizeof(entry.path_hops))
        && writeExact(entry.path, sizeof(entry.path))
        && writeExact(&drop_on_match, sizeof(drop_on_match))
        && writeExact(&rate_limit_enabled, sizeof(rate_limit_enabled))
        && writeExact(&entry.rate_per_minute,
                      sizeof(entry.rate_per_minute))
        && writeExact(entry.target_region_name,
                      sizeof(entry.target_region_name))
        && writeExact(&entry.priority, sizeof(entry.priority))
        && writeExact(&stop_on_match, sizeof(stop_on_match));
  }
  const uint8_t section_magic[4] = {'F', 'P', 'S', '1'};
  const uint8_t no_channel_data_slot = 0xFF;
  const uint8_t channel_data_hops_all = 0xFF;
  const uint8_t empty_count = 0;
  success = success
      && writeExact(section_magic, sizeof(section_magic))
      && writeExact(&no_channel_data_slot,
                    sizeof(no_channel_data_slot))
      && writeExact(&channel_data_hops_all,
                    sizeof(channel_data_hops_all))
      && writeExact(&empty_count, sizeof(empty_count))
      && writeExact(&empty_count, sizeof(empty_count))
      && writeExact(&empty_count, sizeof(empty_count));
  file.close();
  if (!success || !verifyWrittenFile(
          _fs, RULE_TEMP_FILE, bytes_written, write_hash)) {
    _fs->remove(RULE_TEMP_FILE);
    return false;
  }
  if (_fs->exists(RULE_FILE)
      && !_fs->rename(RULE_FILE, RULE_BACKUP_FILE)) {
    _fs->remove(RULE_TEMP_FILE);
    return false;
  }
  if (!_fs->rename(RULE_TEMP_FILE, RULE_FILE)) {
    return false;
  }
  if (_fs->exists(RULE_BACKUP_FILE)) _fs->remove(RULE_BACKUP_FILE);
  return true;
}

bool FloodRuleEngine::fieldsMatch(
    const Entry& entry, const mesh::Packet* packet,
    bool temp_radio_active, bool incoming_is_scoped,
    uint16_t incoming_transport_code, bool incoming_region_allowed,
    const RegionEntry* incoming_region) const {
  if (!entry.active || packet == NULL || !packet->isRouteFlood()) return false;
  if (!FloodFilterPolicy::channelKeyLengthSupported(
          entry.channel_key_len)) return false;
  if (entry.suspend_on_temp_radio && temp_radio_active) return false;
  // FULL room servers do not carry the repeater's separate passive blacklist.
  // Legacy blacklist-qualified rows stay inert instead of widening their match.
  if (entry.match_blacklisted_path) return false;
  if (!FloodFilterPolicy::pathStartsWith(
          packet, entry.path_hash_size, entry.path_hops, entry.path)) {
    return false;
  }

  uint8_t type = packet->getPayloadType();
  uint8_t hops = packet->getPathHashCount();
  if ((entry.payload_type != ANY_TYPE && entry.payload_type != type)
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
      deriveScopeKey(entry.incoming_scope_name, incoming_scope);
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

bool FloodRuleEngine::authenticateChannel(
    const Entry& entry, const mesh::Packet* packet) const {
  if (entry.channel_key_len == 0
      || FloodFilterPolicy::channelHashOnly(entry.channel_key_len)) {
    return true;
  }
  if (!FloodFilterPolicy::channelRequiresAuthentication(
          entry.channel_key_len)) return false;
  uint8_t data[MAX_PACKET_PAYLOAD];
  return mesh::Utils::MACThenDecrypt(
      entry.channel_secret, data, &packet->payload[PATH_HASH_SIZE],
      packet->payload_len - PATH_HASH_SIZE) > 0;
}

int FloodRuleEngine::nextMatch(uint32_t match_mask,
                               uint32_t visited_mask) const {
  uint8_t priorities[RULE_SLOTS];
  uint8_t specificities[RULE_SLOTS];
  for (int i = 0; i < RULE_SLOTS; i++) {
    priorities[i] = _entries[i].priority;
    specificities[i] = FloodFilterPolicy::channelMatcherSpecificity(
        _entries[i].channel_key_len);
  }
  return FloodFilterPolicy::nextOrderedRule(
      match_mask, visited_mask, priorities, specificities, RULE_SLOTS);
}

bool FloodRuleEngine::resolveTargetRegion(
    const char* name, TransportKey& scope,
    const char*& canonical_name) {
  if (_regions == NULL || name == NULL || name[0] == 0) return false;
  RegionEntry* region = _regions->findByName(name);
  if (region == NULL || region->isWildcard()
      || (region->flags & REGION_DENY_FLOOD) != 0
      || _regions->getTransportKeysFor(*region, &scope, 1) <= 0
      || scope.isNull()) {
    return false;
  }
  canonical_name = region->name;
  return true;
}

uint32_t FloodRuleEngine::applyStop(uint32_t match_mask) {
  uint8_t priorities[RULE_SLOTS];
  uint8_t specificities[RULE_SLOTS];
  uint8_t stop_flags[RULE_SLOTS];
  for (int i = 0; i < RULE_SLOTS; i++) {
    priorities[i] = _entries[i].priority;
    const Entry& entry = _entries[i];
    specificities[i] = FloodFilterPolicy::channelMatcherSpecificity(
        entry.channel_key_len);
    bool region_usable = true;
    if (entry.target_region_name[0] != 0) {
      TransportKey scope;
      const char* canonical_name = NULL;
      region_usable = resolveTargetRegion(
          entry.target_region_name, scope, canonical_name);
    }
    stop_flags[i] = FloodFilterPolicy::stopActionApplies(
        entry.stop_on_match, entry.target_region_name[0] != 0,
        region_usable) ? 1 : 0;
  }
  return FloodFilterPolicy::truncateRulesAtStop(
      match_mask, priorities, specificities, stop_flags, RULE_SLOTS);
}

uint32_t FloodRuleEngine::evaluate(
    const mesh::Packet* packet, bool temp_radio_active,
    bool incoming_region_allowed,
    const RegionEntry* incoming_region) {
  static_assert(RULE_SLOTS <= 32,
                "flood rule match mask supports at most 32 slots");
  if (packet == NULL || !packet->isRouteFlood()) return 0;
  bool incoming_is_scoped =
      packet->getRouteType() == ROUTE_TYPE_TRANSPORT_FLOOD;
  uint16_t incoming_transport_code = incoming_is_scoped
      ? packet->transport_codes[0] : 0;
  bool channel_auth_checked[RULE_SLOTS] = { false };
  bool channel_auth_valid[RULE_SLOTS] = { false };
  uint32_t result = 0;
  for (int i = 0; i < RULE_SLOTS; i++) {
    const Entry& entry = _entries[i];
    if (!fieldsMatch(entry, packet, temp_radio_active, incoming_is_scoped,
                     incoming_transport_code, incoming_region_allowed,
                     incoming_region)) {
      continue;
    }
    bool authenticated = true;
    if (FloodFilterPolicy::channelRequiresAuthentication(
            entry.channel_key_len)) {
      int cached = -1;
      for (int j = 0; j < i; j++) {
        if (channel_auth_checked[j]
            && FloodFilterPolicy::sameChannelKey(
                _entries[j].channel_key_len, _entries[j].channel_secret,
                entry.channel_key_len, entry.channel_secret)) {
          cached = j;
          break;
        }
      }
      authenticated = cached >= 0
          ? channel_auth_valid[cached]
          : authenticateChannel(entry, packet);
      channel_auth_checked[i] = true;
      channel_auth_valid[i] = authenticated;
    }
    if (authenticated) result |= (uint32_t)1U << i;
  }
  return applyStop(result);
}

bool FloodRuleEngine::hasRetryRules() const {
  for (int i = 0; i < RULE_SLOTS; i++) {
    if (_entries[i].active && _entries[i].retry_on_match) return true;
  }
  return false;
}

bool FloodRuleEngine::allowsRetry(uint32_t match_mask) const {
  for (int i = 0; i < RULE_SLOTS; i++) {
    if ((match_mask & ((uint32_t)1U << i)) != 0
        && _entries[i].retry_on_match) return true;
  }
  return false;
}

bool FloodRuleEngine::applyScope(mesh::Packet* packet, uint32_t match_mask,
                                 bool& scope_set, bool& fast_track,
                                 bool log_change) {
  scope_set = false;
  fast_track = false;
  if (packet == NULL || !packet->isRouteFlood()) return false;

  uint32_t visited = 0;
  while (true) {
    int i = nextMatch(match_mask, visited);
    if (i < 0) break;
    visited |= (uint32_t)1U << i;
    const Entry& entry = _entries[i];
    if (entry.scope_name[0] == 0
        && entry.target_region_name[0] == 0) {
      continue;
    }

    TransportKey scope;
    const char* target_name = entry.scope_name;
    if (entry.scope_name[0] != 0) {
      deriveScopeKey(entry.scope_name, scope);
    } else {
      if (!resolveTargetRegion(
              entry.target_region_name, scope, target_name)) {
        continue;
      }
    }

    bool changed = FloodFilterPolicy::setTransportScope(
        packet, scope.calcTransportCode(packet));
    scope_set = true;
    fast_track = FloodFilterPolicy::fastTrackScopeChange(
        changed, entry.scope_uses_slow_timing);
    if (changed && log_change) {
      MESH_DEBUG_PRINTLN(
          "flood.rule set scope slot=%d type=%d hops=%d scope=%s tx=%s",
          i + 1, packet->getPayloadType(), packet->getPathHashCount(),
          target_name, entry.scope_uses_slow_timing ? "slow" : "fast");
    }
    return changed;
  }
  return false;
}

bool FloodRuleEngine::shouldBlock(const mesh::Packet* packet,
                                  uint32_t match_mask,
                                  uint32_t now_millis) const {
  if (packet == NULL || !packet->isRouteFlood()) return false;
  uint8_t type = packet->getPayloadType();
  uint8_t hops = packet->getPathHashCount();

  uint32_t visited = 0;
  while (true) {
    int i = nextMatch(match_mask, visited);
    if (i < 0) break;
    visited |= (uint32_t)1U << i;
    const Entry& entry = _entries[i];
    bool blocked = entry.drop_on_match;
    if (entry.rate_limit_enabled
        && FloodFilterPolicy::rateLimitReached(
            entry.rate_window_active, now_millis,
            entry.rate_window_started, entry.rate_window_count,
            entry.rate_per_minute)) {
      blocked = true;
    }
    if (blocked) {
      MESH_DEBUG_PRINTLN(
          "allowPacketForward: flood.rule matched slot=%d type=%d hops=%d",
          i + 1, type, hops);
      return true;
    }
  }
  return false;
}

void FloodRuleEngine::commitRates(const mesh::Packet* packet,
                                  uint32_t match_mask,
                                  uint32_t now_millis) {
  if (packet == NULL || !packet->isRouteFlood()) return;
  for (int i = 0; i < RULE_SLOTS; i++) {
    Entry& entry = _entries[i];
    if ((match_mask & ((uint32_t)1U << i)) == 0
        || !entry.rate_limit_enabled) {
      continue;
    }
    if (!entry.rate_window_active
        || now_millis - entry.rate_window_started >= 60000UL) {
      entry.rate_window_active = true;
      entry.rate_window_started = now_millis;
      entry.rate_window_count = 0;
    }
    if (entry.rate_window_count < 0xFFFF) entry.rate_window_count++;
  }
}

void FloodRuleEngine::formatDetail(int index, char* reply,
                                   size_t reply_len) const {
  if (index < 0 || index >= RULE_SLOTS || !_entries[index].active) {
    snprintf(reply, reply_len, "Err - empty filter slot");
    return;
  }
  const Entry& entry = _entries[index];
  char hops[12];
  char prefix[32];
  char incoming[48];
  char action[72];
  char rate[24];
  char retry[10];
  formatHopSpec(hops, sizeof(hops), entry.min_hops, entry.max_hops);
  if (entry.match_blacklisted_path) copyString(prefix, "blacklist", sizeof(prefix));
  else formatPathPrefix(prefix, sizeof(prefix), entry.path_hash_size,
                        entry.path_hops, entry.path);

  switch (entry.incoming_scope_kind) {
    case FloodFilterPolicy::RULE_IN_NONE:
      copyString(incoming, "none", sizeof(incoming));
      break;
    case FloodFilterPolicy::RULE_IN_SCOPED:
      copyString(incoming, "scoped", sizeof(incoming));
      break;
    case FloodFilterPolicy::RULE_IN_ALLOWED:
      copyString(incoming, "allowed", sizeof(incoming));
      break;
    case FloodFilterPolicy::RULE_IN_UNKNOWN:
      copyString(incoming, "unknown", sizeof(incoming));
      break;
    case FloodFilterPolicy::RULE_IN_SCOPE:
      snprintf(incoming, sizeof(incoming), "scope:%s",
               entry.incoming_scope_name);
      break;
    case FloodFilterPolicy::RULE_IN_REGION:
      snprintf(incoming, sizeof(incoming), "region:%s%s",
               entry.incoming_scope_name,
               _regions != NULL
                       && _regions->findByName(entry.incoming_scope_name) != NULL
                   ? "" : "?");
      break;
    default:
      copyString(incoming, "any", sizeof(incoming));
      break;
  }

  action[0] = 0;
  if (entry.drop_on_match) {
    copyString(action, "drop", sizeof(action));
  } else if (entry.scope_name[0] != 0) {
    snprintf(action, sizeof(action), "scope=%s", entry.scope_name);
  } else if (entry.target_region_name[0] != 0) {
    RegionEntry* current = _regions == NULL ? NULL
        : _regions->findByName(entry.target_region_name);
    snprintf(action, sizeof(action), "region=%s%s",
             entry.target_region_name, current == NULL ? "?" : "");
  }
  rate[0] = 0;
  if (entry.rate_limit_enabled) {
    snprintf(rate, sizeof(rate), "%srate=%u/min",
             action[0] == 0 ? "" : " ",
             (unsigned int)entry.rate_per_minute);
  }
  retry[0] = 0;
  if (entry.retry_on_match) {
    snprintf(retry, sizeof(retry), "%sretry",
             action[0] == 0 && rate[0] == 0 ? "" : " ");
  }

  int written = snprintf(
      reply, reply_len,
      "> %d type=%s hops=%s channel=%s prefix=%s in=%s %s%s%s priority=%u%s%s%s",
      index + 1, payloadTypeName(entry.payload_type), hops,
      entry.channel_key_len == 0 ? "*" : entry.channel_name,
      prefix, incoming, action, rate, retry,
      (unsigned int)entry.priority,
      entry.stop_on_match ? " stop" : "",
      entry.scope_uses_slow_timing ? " tx=slow" : "",
      entry.suspend_on_temp_radio ? " suspend=tempradio" : "");
  if (written >= 0 && (size_t)written < reply_len) return;

  char compact_type[4];
  if (entry.payload_type == ANY_TYPE) copyString(compact_type, "any", sizeof(compact_type));
  else snprintf(compact_type, sizeof(compact_type), "%u",
                (unsigned int)entry.payload_type);
  char compact_incoming[44];
  switch (entry.incoming_scope_kind) {
    case FloodFilterPolicy::RULE_IN_NONE:
      copyString(compact_incoming, "n", sizeof(compact_incoming));
      break;
    case FloodFilterPolicy::RULE_IN_SCOPED:
      copyString(compact_incoming, "s", sizeof(compact_incoming));
      break;
    case FloodFilterPolicy::RULE_IN_ALLOWED:
      copyString(compact_incoming, "a", sizeof(compact_incoming));
      break;
    case FloodFilterPolicy::RULE_IN_UNKNOWN:
      copyString(compact_incoming, "u", sizeof(compact_incoming));
      break;
    case FloodFilterPolicy::RULE_IN_SCOPE:
      snprintf(compact_incoming, sizeof(compact_incoming), "s:%s",
               entry.incoming_scope_name);
      break;
    case FloodFilterPolicy::RULE_IN_REGION:
      snprintf(compact_incoming, sizeof(compact_incoming), "r:%s",
               entry.incoming_scope_name);
      break;
    default:
      copyString(compact_incoming, "*", sizeof(compact_incoming));
      break;
  }
  char compact_action[44];
  if (entry.drop_on_match) {
    copyString(compact_action, " drop", sizeof(compact_action));
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
  if (entry.scope_uses_slow_timing || entry.suspend_on_temp_radio
      || entry.retry_on_match) {
    snprintf(compact_flags, sizeof(compact_flags), " f=%s%s%s",
             entry.scope_uses_slow_timing ? "s" : "",
             entry.suspend_on_temp_radio ? "t" : "",
             entry.retry_on_match ? "r" : "");
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

void FloodRuleEngine::format(const char* args, char* reply) const {
  const char* selector = skipSpaces(args);
  if (*selector == '.') selector = skipSpaces(selector + 1);
  if (*selector != 0) {
    uint32_t slot = 0;
    if (!parseUnsigned(selector, RULE_SLOTS, slot) || slot == 0) {
      snprintf(reply, 160, "Err - filter slot must be 1-%u",
               (unsigned int)RULE_SLOTS);
      return;
    }
    formatDetail((int)slot - 1, reply, 160);
    return;
  }

  size_t used = (size_t)snprintf(reply, 160, ">");
  int active_count = 0;
  bool truncated = false;
  for (int i = 0; i < RULE_SLOTS; i++) {
    const Entry& entry = _entries[i];
    if (!entry.active) continue;
    active_count++;
    char hops[12];
    char item[120];
    char target[48];
    char priority[8];
    formatHopSpec(hops, sizeof(hops), entry.min_hops, entry.max_hops);
    if (entry.drop_on_match) {
      copyString(target, "!drop", sizeof(target));
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
             i + 1, payloadTypeName(entry.payload_type), hops,
             entry.match_blacklisted_path ? "?blacklist" : "", target,
             priority, entry.stop_on_match ? "~stop" : "",
             entry.rate_limit_enabled ? "~rate" : "",
             entry.retry_on_match ? "~retry" : "",
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
  if (active_count == 0) copyString(reply, "> off", 160);
  else if (truncated) copyString(&reply[used], " ...", 160 - used);
}

void FloodRuleEngine::set(const char* args, char* reply,
                          bool require_explicit_action) {
  const char* cursor = skipSpaces(args);
  int requested_slot = -1;
  if (*cursor == '.') {
    cursor++;
    const char* slot_start = cursor;
    while (*cursor >= '0' && *cursor <= '9') cursor++;
    size_t slot_len = (size_t)(cursor - slot_start);
    char slot_text[8];
    if (slot_len == 0 || slot_len >= sizeof(slot_text)) {
      snprintf(reply, 160, "Err - filter slot must be 1-%u",
               (unsigned int)RULE_SLOTS);
      return;
    }
    memcpy(slot_text, slot_start, slot_len);
    slot_text[slot_len] = 0;
    uint32_t slot = 0;
    if (!parseUnsigned(slot_text, RULE_SLOTS, slot) || slot == 0) {
      snprintf(reply, 160, "Err - filter slot must be 1-%u",
               (unsigned int)RULE_SLOTS);
      return;
    }
    requested_slot = (int)slot - 1;
    if (*cursor != ' ') {
      copyString(reply, RULE_USAGE, 160);
      return;
    }
  }
  cursor = skipSpaces(cursor);
  if (strlen(cursor) >= 192) {
    copyString(reply, "Err - rule parameters too long", 160);
    return;
  }

  char params[192];
  copyString(params, cursor, sizeof(params));
  char* tokens[18];
  int token_count = 0;
  char* token = params;
  while (*token != 0) {
    if (token_count >= 18) {
      copyString(reply, "Err - too many rule parameters", 160);
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
    copyString(reply, RULE_USAGE, 160);
    return;
  }

  uint8_t payload_type = 0;
  const char* type_text = asciiStartsWith(tokens[0], "type=")
      ? tokens[0] + strlen("type=") : tokens[0];
  if (!parsePayloadType(type_text, payload_type)) {
    copyString(reply,
               "Err - packet type must be name, any, 0-15, or 0x00-0x0F",
               160);
    return;
  }

  uint8_t min_hops = 0;
  uint8_t max_hops = MAX_HOPS;
  bool hops_set = false;
  bool suspend_on_temp_radio = false;
  bool path_set = false;
  uint8_t path_hash_size = 0;
  uint8_t path_hops = 0;
  uint8_t path[PATH_PREFIX_BYTES_MAX];
  memset(path, 0, sizeof(path));
  uint8_t incoming_scope_kind = FloodFilterPolicy::RULE_IN_ANY;
  char incoming_scope_name[NAME_LEN];
  memset(incoming_scope_name, 0, sizeof(incoming_scope_name));
  bool incoming_set = false;
  uint8_t channel_key_len = 0;
  uint8_t channel_hash = 0;
  uint8_t channel_secret[PUB_KEY_SIZE];
  memset(channel_secret, 0, sizeof(channel_secret));
  char channel_name[NAME_LEN];
  memset(channel_name, 0, sizeof(channel_name));
  bool channel_set = false;
  bool scope_timing_set = false;
  bool scope_uses_slow_timing = false;
  char scope_name[NAME_LEN];
  memset(scope_name, 0, sizeof(scope_name));
  char target_region_name[NAME_LEN];
  memset(target_region_name, 0, sizeof(target_region_name));
  bool target_set = false;
  bool drop_on_match = false;
  bool drop_set = false;
  bool rate_limit_enabled = false;
  uint16_t rate_per_minute = 0;
  uint8_t priority = 0;
  bool priority_set = false;
  bool stop_on_match = false;
  bool retry_on_match = false;

  for (int i = 1; i < token_count; i++) {
    if (asciiEqual(tokens[i], "suspend=tempradio")) {
      if (suspend_on_temp_radio) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      suspend_on_temp_radio = true;
    } else if (asciiStartsWith(tokens[i], "f=")) {
      const char* flags = tokens[i] + 2;
      if (*flags == 0) {
        copyString(reply, "Err - compact flags are s, t, and/or r", 160);
        return;
      }
      while (*flags != 0) {
        if (*flags == 's' || *flags == 'S') {
          if (scope_timing_set) {
            copyString(reply, DUPLICATE_OPTION, 160);
            return;
          }
          scope_timing_set = true;
          scope_uses_slow_timing = true;
        } else if (*flags == 't' || *flags == 'T') {
          if (suspend_on_temp_radio) {
            copyString(reply, DUPLICATE_OPTION, 160);
            return;
          }
          suspend_on_temp_radio = true;
        } else if (*flags == 'r' || *flags == 'R') {
          if (retry_on_match) {
            copyString(reply, DUPLICATE_OPTION, 160);
            return;
          }
          retry_on_match = true;
        } else {
          copyString(reply, "Err - compact flags are s, t, and/or r", 160);
          return;
        }
        flags++;
      }
    } else if (asciiEqual(tokens[i], "path=blacklist")) {
      copyString(reply,
                 "Err - path=blacklist is repeater-only; use prefix= on rooms",
                 160);
      return;
    } else if (asciiEqual(tokens[i], "require=region")) {
      if (incoming_set) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      incoming_set = true;
      incoming_scope_kind = FloodFilterPolicy::RULE_IN_ALLOWED;
    } else if (asciiStartsWith(tokens[i], "in=")
        || asciiStartsWith(tokens[i], "i=")) {
      if (incoming_set) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      incoming_set = true;
      bool compact = asciiStartsWith(tokens[i], "i=");
      const char* value = tokens[i] + (compact ? 2 : strlen("in="));
      if (asciiEqual(value, "any") || (compact && asciiEqual(value, "*"))) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_ANY;
      } else if (asciiEqual(value, "none") || asciiEqual(value, "unscoped")
          || (compact && asciiEqual(value, "n"))) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_NONE;
      } else if (asciiEqual(value, "scoped")
          || (compact && asciiEqual(value, "s"))) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_SCOPED;
      } else if (asciiEqual(value, "allowed") || asciiEqual(value, "known")
          || (compact && asciiEqual(value, "a"))) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_ALLOWED;
      } else if (asciiEqual(value, "unknown")
          || (compact && asciiEqual(value, "u"))) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_UNKNOWN;
      } else if (asciiStartsWith(value, "scope:")
          || (compact && asciiStartsWith(value, "s:"))) {
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_SCOPE;
        const char* name = value + (asciiStartsWith(value, "scope:")
            ? strlen("scope:") : 2);
        if (!normalizeScopeName(name, incoming_scope_name,
                                sizeof(incoming_scope_name))) {
          copyString(reply, "Err - bad incoming scope name", 160);
          return;
        }
      } else if (asciiStartsWith(value, "region:")
          || (compact && asciiStartsWith(value, "r:"))) {
        const char* name = value + (asciiStartsWith(value, "region:")
            ? strlen("region:") : 2);
        RegionEntry* region = _regions == NULL ? NULL
            : _regions->findByNamePrefix(name);
        if (region == NULL || region->isWildcard()) {
          copyString(reply, "Err - bad incoming region", 160);
          return;
        }
        incoming_scope_kind = FloodFilterPolicy::RULE_IN_REGION;
        copyString(incoming_scope_name, region->name,
                   sizeof(incoming_scope_name));
      } else {
        copyString(reply,
            "Err - in=any|none|scoped|allowed|unknown|scope:name|region:name",
            160);
        return;
      }
    } else if (asciiStartsWith(tokens[i], "priority=")
        || asciiStartsWith(tokens[i], "pri=")) {
      if (priority_set) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      const char* value = strchr(tokens[i], '=') + 1;
      uint32_t parsed = 0;
      if (!parseUnsigned(value, 255, parsed)) {
        copyString(reply, "Err - priority must be 0-255", 160);
        return;
      }
      priority = (uint8_t)parsed;
      priority_set = true;
    } else if (asciiEqual(tokens[i], "stop")
        || asciiEqual(tokens[i], "action=stop")) {
      if (stop_on_match) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      stop_on_match = true;
    } else if (asciiEqual(tokens[i], "retry")
        || asciiEqual(tokens[i], "retry=on")
        || asciiEqual(tokens[i], "retry=allow")
        || asciiEqual(tokens[i], "action=retry")) {
      if (retry_on_match) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      retry_on_match = true;
    } else if (asciiStartsWith(tokens[i], "tx=")) {
      if (scope_timing_set) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      if (asciiEqual(tokens[i], "tx=slow")) {
        scope_uses_slow_timing = true;
      } else if (!asciiEqual(tokens[i], "tx=fast")) {
        copyString(reply, "Err - tx timing must be fast or slow", 160);
        return;
      }
      scope_timing_set = true;
    } else if (asciiStartsWith(tokens[i], "scope=")) {
      if (target_set || drop_set) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      if (!normalizeScopeName(tokens[i] + strlen("scope="), scope_name,
                              sizeof(scope_name))) {
        copyString(reply,
                   "Err - scope must be a public name of at most 30 characters",
                   160);
        return;
      }
      target_set = true;
    } else if (asciiStartsWith(tokens[i], "region=")) {
      if (target_set || drop_set) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      RegionEntry* region = _regions == NULL ? NULL
          : _regions->findByNamePrefix(tokens[i] + strlen("region="));
      TransportKey target_scope;
      if (region == NULL || region->isWildcard()
          || (region->flags & REGION_DENY_FLOOD) != 0
          || _regions->getTransportKeysFor(*region, &target_scope, 1) <= 0
          || target_scope.isNull()) {
        copyString(reply, "Err - bad target region", 160);
        return;
      }
      copyString(target_region_name, region->name,
                 sizeof(target_region_name));
      target_set = true;
    } else if (asciiEqual(tokens[i], "drop")
        || asciiEqual(tokens[i], "action=drop")) {
      if (drop_set || target_set) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      drop_on_match = true;
      drop_set = true;
    } else if (asciiStartsWith(tokens[i], "rate=")
        || asciiStartsWith(tokens[i], "q=")) {
      if (rate_limit_enabled) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      bool compact = asciiStartsWith(tokens[i], "q=");
      char rate_text[24];
      copyString(rate_text,
                 tokens[i] + (compact ? 2 : strlen("rate=")),
                 sizeof(rate_text));
      char* slash = strchr(rate_text, '/');
      if ((!compact && slash == NULL)
          || (slash != NULL && !(strcmp(slash, "/min") == 0
              || strcmp(slash, "/m") == 0))) {
        copyString(reply, "Err - rate format is X/min", 160);
        return;
      }
      if (slash != NULL) *slash = 0;
      uint32_t parsed = 0;
      if (!parseUnsigned(rate_text, RATE_UNLIMITED - 1, parsed)) {
        copyString(reply, "Err - rate must be 0-65534/min", 160);
        return;
      }
      rate_per_minute = (uint16_t)parsed;
      rate_limit_enabled = true;
    } else if (asciiStartsWith(tokens[i], "channel=")
        || asciiStartsWith(tokens[i], "c=")) {
      if (channel_set) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      channel_set = true;
      const char* value = tokens[i] + (asciiStartsWith(tokens[i], "c=")
          ? 2 : strlen("channel="));
      if (!asciiEqual(value, "*")) {
        if (!parseChannel(value, channel_secret, channel_key_len,
                          channel_hash, channel_name,
                          sizeof(channel_name))) {
          copyString(reply,
                     "Err - channel must be *, public, #channel, hash:XX, or a key",
                     160);
          return;
        }
      }
    } else if (asciiStartsWith(tokens[i], "prefix=")
        || asciiStartsWith(tokens[i], "p=")
        || asciiStartsWith(tokens[i], "path=")) {
      if (path_set) {
        copyString(reply, DUPLICATE_OPTION, 160);
        return;
      }
      const char* value = strchr(tokens[i], '=') + 1;
      if (!parsePathPrefix(value, path_hash_size, path_hops, path)) {
        copyString(reply,
                   "Err - prefix is * or 1-3 comma-separated 1/2/3-byte IDs",
                   160);
        return;
      }
      path_set = true;
    } else if (asciiStartsWith(tokens[i], "hops=")) {
      if (hops_set || !parseHopSpec(tokens[i] + strlen("hops="),
                                    min_hops, max_hops)) {
        copyString(reply,
                   "Err - hops must be all, N, N+, or N-M (0-63)", 160);
        return;
      }
      hops_set = true;
    } else if (!hops_set
        && parseHopSpec(tokens[i], min_hops, max_hops)) {
      hops_set = true;
    } else {
      copyString(reply, RULE_USAGE, 160);
      return;
    }
  }

  if (scope_timing_set && !target_set) {
    copyString(reply, "Err - tx timing requires scope= or region=", 160);
    return;
  }
  if (drop_on_match && rate_limit_enabled) {
    copyString(reply, "Err - drop cannot be combined with rate", 160);
    return;
  }
  if (drop_on_match && retry_on_match) {
    copyString(reply, "Err - drop cannot be combined with retry", 160);
    return;
  }
  if (channel_key_len != 0 && payload_type != ANY_TYPE
      && payload_type != PAYLOAD_TYPE_GRP_TXT
      && payload_type != PAYLOAD_TYPE_GRP_DATA) {
    copyString(reply,
               "Err - channel matcher requires type=any|grp_txt|grp_data",
               160);
    return;
  }
  bool action_set = drop_set || target_set || rate_limit_enabled
      || stop_on_match || retry_on_match;
  if (require_explicit_action && !action_set) {
    copyString(reply,
        "Err - flood.rule requires drop, scope=, region=, rate=, retry, or stop",
        160);
    return;
  }
  if (!action_set) drop_on_match = true;

  Entry candidate;
  memset(&candidate, 0, sizeof(candidate));
  candidate.active = true;
  candidate.payload_type = payload_type;
  candidate.min_hops = min_hops;
  candidate.max_hops = max_hops;
  candidate.suspend_on_temp_radio = suspend_on_temp_radio;
  candidate.scope_uses_slow_timing = scope_uses_slow_timing;
  candidate.incoming_scope_kind = incoming_scope_kind;
  copyString(candidate.incoming_scope_name, incoming_scope_name,
             sizeof(candidate.incoming_scope_name));
  candidate.channel_key_len = channel_key_len;
  candidate.channel_hash = channel_hash;
  memcpy(candidate.channel_secret, channel_secret,
         sizeof(candidate.channel_secret));
  copyString(candidate.channel_name, channel_name,
             sizeof(candidate.channel_name));
  candidate.path_hash_size = path_hash_size;
  candidate.path_hops = path_hops;
  memcpy(candidate.path, path, sizeof(candidate.path));
  copyString(candidate.scope_name, scope_name, sizeof(candidate.scope_name));
  copyString(candidate.target_region_name, target_region_name,
             sizeof(candidate.target_region_name));
  candidate.drop_on_match = drop_on_match;
  candidate.rate_limit_enabled = rate_limit_enabled;
  candidate.rate_per_minute = rate_per_minute;
  candidate.priority = priority;
  candidate.stop_on_match = stop_on_match;
  candidate.retry_on_match = retry_on_match;

  int slot = requested_slot;
  if (slot < 0) {
    for (int i = 0; i < RULE_SLOTS; i++) {
      if (_entries[i].active
          && memcmp(&_entries[i], &candidate,
                    offsetof(Entry, rate_window_started)) == 0) {
        slot = i;
        break;
      }
    }
  }
  if (slot < 0) {
    for (int i = 0; i < RULE_SLOTS; i++) {
      if (!_entries[i].active) {
        slot = i;
        break;
      }
    }
  }
  if (slot < 0) {
    copyString(reply, "Err - filter table full", 160);
    return;
  }

  Entry previous = _entries[slot];
  _entries[slot] = candidate;
  if (!save()) {
    _entries[slot] = previous;
    copyString(reply, "Err - unable to save flood filter", 160);
    return;
  }
  char detail[160];
  formatDetail(slot, detail, sizeof(detail));
  const char* text = detail[0] == '>' ? skipSpaces(detail + 1) : detail;
  snprintf(reply, 160, "OK - %s", text);
}

void FloodRuleEngine::remove(const char* args, char* reply) {
  const char* selector = skipSpaces(args);
  if (*selector == '.') selector = skipSpaces(selector + 1);
  if (asciiEqual(selector, "all")) {
    Entry previous[RULE_SLOTS];
    memcpy(previous, _entries, sizeof(previous));
    memset(_entries, 0, sizeof(_entries));
    if (!save()) {
      memcpy(_entries, previous, sizeof(_entries));
      copyString(reply, "Err - unable to save flood filter", 160);
    } else {
      copyString(reply, "OK - all flood filters removed", 160);
    }
    return;
  }

  uint32_t slot = 0;
  if (!parseUnsigned(selector, RULE_SLOTS, slot) || slot == 0) {
    snprintf(reply, 160, "Err - use: del flood.rule.<1-%u>|all",
             (unsigned int)RULE_SLOTS);
    return;
  }
  int index = (int)slot - 1;
  if (!_entries[index].active) {
    copyString(reply, "Err - empty filter slot", 160);
    return;
  }
  Entry previous = _entries[index];
  memset(&_entries[index], 0, sizeof(_entries[index]));
  if (!save()) {
    _entries[index] = previous;
    copyString(reply, "Err - unable to save flood filter", 160);
  } else {
    copyString(reply, "OK", 160);
  }
}

bool FloodRuleEngine::handleCommand(const char* command, char* reply) {
  if (command == NULL || reply == NULL) return false;
  if (commandMatches(command, "get flood.filter.blacklist")
      || commandMatches(command, "set flood.filter.blacklist")
      || commandMatches(command, "del flood.filter.blacklist")) {
    copyString(reply,
               "Err - flood.filter.blacklist is repeater-only; use prefix=",
               160);
    return true;
  }
  if (commandMatches(command, "get flood.rule")) {
    format(command + strlen("get flood.rule"), reply);
    return true;
  }
  if (commandMatches(command, "set flood.rule")) {
    set(command + strlen("set flood.rule"), reply, true);
    return true;
  }
  if (commandMatches(command, "del flood.rule")) {
    remove(command + strlen("del flood.rule"), reply);
    return true;
  }
  if (commandMatches(command, "get flood.filter")) {
    format(command + strlen("get flood.filter"), reply);
    return true;
  }
  if (commandMatches(command, "set flood.filter")) {
    set(command + strlen("set flood.filter"), reply, false);
    return true;
  }
  if (commandMatches(command, "del flood.filter")) {
    remove(command + strlen("del flood.filter"), reply);
    return true;
  }
  return false;
}

#endif
