#pragma once

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <Packet.h>
#include "FloodRuleTargets.h"
#include "TxtDataHelpers.h"
#include "FloodTextPattern.h"
#include "FloodGroupPlainText.h"

namespace mesh { class Utils; }

namespace FloodRuleExtensions {

enum Route : uint8_t { LegacyRoute, FloodRoute, DirectRoute, AnyRoute };
enum PathPosition : uint8_t { Prefix, Anywhere, Suffix, Exact };

// Zero initialization retains every FPF7 default. Keep the old path's first
// nine bytes in its established field; only a fourth 3-byte ID needs a tail.
struct Fields {
  // RAM needs only one byte per bound. FPF7 still stores two-byte values;
  // decode and validate those before narrowing so corrupt high bytes fail.
  uint8_t min_payload;
  uint8_t max_payload;
  uint8_t route;
  uint8_t probability;
  uint8_t path_position;
  uint8_t disabled;
  uint8_t payload_set;
  uint8_t path_tail[3];
  char sender[64];
  char text[64];
};
static_assert(MAX_PACKET_PAYLOAD <= UINT8_MAX, "Extended rule payload bounds need one byte");
static_assert(sizeof(Fields) == 138, "Update the extended filter RAM budget");

inline bool hasFields(const Fields& fields) {
  return fields.min_payload || fields.max_payload || fields.route
      || fields.probability || fields.path_position || fields.disabled || fields.payload_set
      || fields.path_tail[0] || fields.path_tail[1] || fields.path_tail[2]
      || fields.sender[0] || fields.text[0];
}

inline bool sameFields(const Fields& left, const Fields& right) {
  return left.min_payload == right.min_payload && left.max_payload == right.max_payload
      && left.route == right.route && left.probability == right.probability
      && left.path_position == right.path_position && left.disabled == right.disabled
      && left.payload_set == right.payload_set
      && !memcmp(left.path_tail, right.path_tail, sizeof(left.path_tail))
      && !strcmp(left.sender, right.sender) && !strcmp(left.text, right.text);
}

template<typename Entry>
inline bool tableUsesExtensions(const Entry* entries, unsigned count) {
  for (unsigned i = 0; i < count; ++i)
    if (entries[i].active && (hasFields(entries[i].extra) || entries[i].path_hops > 3)) return true;
  return false;
}

inline bool contentRequested(const Fields& fields) {
  return fields.sender[0] || fields.text[0];
}

inline bool validFields(const Fields& fields) {
  return fields.route <= AnyRoute && fields.probability <= 100
      && fields.path_position <= Exact && fields.disabled <= 1 && fields.payload_set <= 1
      && fields.min_payload <= MAX_PACKET_PAYLOAD
      && fields.max_payload <= MAX_PACKET_PAYLOAD
      && (!fields.payload_set || fields.min_payload <= fields.max_payload)
      && memchr(fields.sender, 0, sizeof(fields.sender))
      && memchr(fields.text, 0, sizeof(fields.text))
      && mesh::flood::textPatternValid(fields.sender, strlen(fields.sender))
      && mesh::flood::textPatternValid(fields.text, strlen(fields.text));
}

template<typename Entry>
inline bool validRuleFields(const Entry& entry) {
  if (!validFields(entry.extra)) return false;
  const bool path_valid = entry.path_hops == 0
      ? entry.path_hash_size <= 3 || entry.path_hash_size == 0x82
      : entry.path_hash_size >= 1 && entry.path_hash_size <= 3
          && entry.path_hops <= 4 && !entry.match_blacklisted_path;
  const bool content_valid = !contentRequested(entry.extra)
      || ((entry.channel_key_len == 16 || entry.channel_key_len == 32)
          && (entry.payload_type == 0xFF || entry.payload_type == PAYLOAD_TYPE_GRP_TXT));
  const bool flood_actions = FloodRuleTargets::scopeName(entry)[0] || FloodRuleTargets::regionName(entry)[0]
      || entry.retry_on_match || entry.scope_uses_slow_timing;
  return path_valid && content_valid && (!flood_actions
      || entry.extra.route == LegacyRoute || entry.extra.route == FloodRoute);
}

// Strip grouping quotes, preserving regex escapes and all quoted whitespace.
// Token pointers borrow this buffer; malformed input never reaches a setter.
inline int tokenize(char* buffer, char* tokens[], unsigned capacity) {
  char* read = buffer;
  char* write = buffer;
  unsigned count = 0;
  while (*read) {
    while (*read == ' ' || *read == '\t') ++read;
    if (!*read) break;
    if (count == capacity) return -1;
    tokens[count++] = write;
    char quote = 0;
    while (*read) {
      char c = *read++;
      if (c == '\\' && *read) {
        if (quote && (*read == quote || *read == '\\')) *write++ = *read++;
        else { *write++ = c; *write++ = *read++; }
      } else if (quote && c == quote) {
        quote = 0;
      } else if (!quote && (c == '"' || c == '\'')) {
        quote = c;
      } else if (!quote && (c == ' ' || c == '\t')) {
        break;
      } else {
        *write++ = c;
      }
    }
    if (quote) return -1;
    *write++ = 0;
  }
  return int(count);
}

inline bool unsignedValue(const char* text, uint16_t maximum, uint16_t& value) {
  if (!text || !*text) return false;
  uint32_t parsed = 0;
  do {
    if (*text < '0' || *text > '9') return false;
    parsed = parsed * 10U + uint8_t(*text++ - '0');
    if (parsed > maximum) return false;
  } while (*text);
  value = uint16_t(parsed);
  return true;
}

inline bool enabledValue(const char* value, uint8_t& disabled) {
  if (!strcmp(value, "on") || !strcmp(value, "1")) disabled = 0;
  else if (!strcmp(value, "off") || !strcmp(value, "0")) disabled = 1;
  else return false;
  return true;
}

// Return 0 for an existing-parser token, 1 when handled, -1 on invalid input.
inline int parseToken(const char* token, Fields& fields, uint8_t& seen, char* reply) {
  const char* value = strchr(token, '=');
  if (!value) return 0;
  const size_t length = size_t(value++ - token);
  unsigned bit;
  if ((length == 6 && !strncmp(token, "sender", 6)) || (length == 1 && *token == 'n')) bit = 0;
  else if ((length == 4 && !strncmp(token, "text", 4)) || (length == 1 && *token == 'x')) bit = 1;
  else if ((length == 3 && !strncmp(token, "len", 3)) || (length == 1 && *token == 'l')) bit = 2;
  else if ((length == 5 && !strncmp(token, "route", 5)) || (length == 2 && !strncmp(token, "rt", 2))) bit = 3;
  else if ((length == 4 && !strncmp(token, "prob", 4)) || (length == 2 && !strncmp(token, "pr", 2))) bit = 4;
  else if ((length == 7 && !strncmp(token, "enabled", 7)) || (length == 1 && *token == 'e')) bit = 5;
  else return 0;
  if (seen & (1U << bit)) {
    strcpy(reply, "Err - duplicate rule parameter");
    return -1;
  }
  seen |= uint8_t(1U << bit);
  bool valid = true;
  if (bit <= 1) {
    valid = *value && strlen(value) < sizeof(fields.sender)
        && mesh::flood::textPatternValid(value, strlen(value));
    if (valid) strcpy(bit == 0 ? fields.sender : fields.text, value);
    else strcpy(reply, "Err - invalid pattern (maximum 63 bytes)");
  } else if (bit == 2) {
    char range[20];
    valid = strlen(value) < sizeof(range);
    if (valid) {
      strcpy(range, value);
      char* separator = strchr(range, '-');
      char* plus = strchr(range, '+');
      if (separator) *separator++ = 0;
      if (plus) *plus = 0;
      uint16_t minimum = 0, maximum = 0;
      valid = unsignedValue(range, MAX_PACKET_PAYLOAD, minimum);
      if (valid && separator) valid = !plus
          && unsignedValue(separator, MAX_PACKET_PAYLOAD, maximum)
          && minimum <= maximum;
      else if (valid) {
        valid = !plus || plus[1] == 0;
        maximum = plus ? MAX_PACKET_PAYLOAD : minimum;
      }
      if (valid) {
        fields.min_payload = uint8_t(minimum);
        fields.max_payload = uint8_t(maximum);
      }
    }
    if (valid) fields.payload_set = 1;
    if (!valid) strcpy(reply, "Err - len is N, N+, or N-M payload bytes");
  } else if (bit == 3) {
    if (!strcmp(value, "flood") || !strcmp(value, "f")) fields.route = FloodRoute;
    else if (!strcmp(value, "direct") || !strcmp(value, "d")) fields.route = DirectRoute;
    else if (!strcmp(value, "any") || !strcmp(value, "a")) fields.route = AnyRoute;
    else { valid = false; strcpy(reply, "Err - route is flood, direct, or any"); }
  } else if (bit == 4) {
    uint16_t probability;
    valid = unsignedValue(value, 100, probability) && probability != 0;
    if (valid) fields.probability = probability == 100 ? 0 : uint8_t(probability);
    else strcpy(reply, "Err - prob must be 1-100");
  } else {
    valid = enabledValue(value, fields.disabled);
    if (!valid) strcpy(reply, "Err - enabled is on or off");
  }
  return valid ? 1 : -1;
}

inline bool routeMatches(const Fields& fields, const mesh::Packet* packet, uint8_t context) {
  if (!packet) return false;
  if (fields.route == LegacyRoute) return context != 0 || packet->isRouteFlood();
  return fields.route == AnyRoute || (fields.route == FloodRoute ? packet->isRouteFlood() : packet->isRouteDirect());
}

inline bool matchesEnvelope(const Fields& fields, const mesh::Packet* packet, uint8_t context) {
  return packet && !fields.disabled && routeMatches(fields, packet, context)
      && (!fields.payload_set || (packet->payload_len >= fields.min_payload
          && packet->payload_len <= fields.max_payload));
}

inline bool matchesProbability(const Fields& fields, uint32_t roll) {
  return fields.probability == 0 || fields.probability == 100
      || roll % 100U < fields.probability;
}

// A received packet can be evaluated during both timing and forwarding.
// Use one boot-randomized decision per payload/rule/admission phase, stable
// across path growth and scope rewrites. Repeated evaluation cannot reroll it.
template <typename PacketType>
inline uint32_t probabilityRoll(const PacketType* packet, uint32_t seed,
                                unsigned slot, uint8_t context) {
  uint32_t hash = 2166136261UL;
  const auto add = [&hash](uint8_t byte) { hash = (hash ^ byte) * 16777619UL; };
  for (unsigned i = 0; i < 4; ++i) add(uint8_t(seed >> (8 * i)));
  add(uint8_t(slot)); add(context);
  if (packet) {
    add(packet->getPayloadType());
    add(uint8_t(packet->payload_len)); add(uint8_t(packet->payload_len >> 8));
    const unsigned length = packet->payload_len <= MAX_PACKET_PAYLOAD
        ? packet->payload_len : MAX_PACKET_PAYLOAD;
    for (unsigned i = 0; i < length; ++i) add(packet->payload[i]);
  }
  hash ^= hash >> 16;
  hash *= 0x85ebca6bUL;
  hash ^= hash >> 13;
  hash *= 0xc2b2ae35UL;
  return hash ^ (hash >> 16);
}

inline int hexValue(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

inline bool parsePath(const char* value, uint8_t& width, uint8_t& hops,
                      uint8_t first[9], Fields& fields, bool prefix_legacy) {
  memset(first, 0, 9);
  memset(fields.path_tail, 0, sizeof(fields.path_tail));
  width = hops = 0;
  fields.path_position = prefix_legacy ? Prefix : Anywhere;
  if (!strcmp(value, "*")) { fields.path_position = Prefix; return true; }
  bool start = *value == '^';
  if (start) ++value;
  const size_t size = strlen(value);
  bool end = size && value[size - 1] == '$';
  const char* finish = value + size - (end ? 1 : 0);
  if (start || end) fields.path_position = start ? (end ? Exact : Prefix) : Suffix;
  uint8_t bytes[12] = {};
  while (value < finish) {
    const char* next = value;
    while (next < finish && *next != '>' && *next != ',') ++next;
    const size_t digits = size_t(next - value);
    if ((digits != 2 && digits != 4 && digits != 6) || hops >= 4
        || (width && digits != size_t(width) * 2)) return false;
    width = uint8_t(digits / 2);
    for (unsigned i = 0; i < width; ++i) {
      const int high = hexValue(value[i * 2]), low = hexValue(value[i * 2 + 1]);
      if (high < 0 || low < 0) return false;
      bytes[hops * width + i] = uint8_t((high << 4) | low);
    }
    ++hops;
    if (next == finish) break;
    value = next + 1;
    if (value == finish) return false;
  }
  if (!hops) return false;
  memcpy(first, bytes, 9);
  memcpy(fields.path_tail, bytes + 9, 3);
  return true;
}

inline bool pathMatches(const Fields& fields, const mesh::Packet* packet,
                        uint8_t width, uint8_t hops, const uint8_t first[9]) {
  if (!hops) return true; // Existing hash-width-only condition is checked separately.
  if (!packet || !packet->isRouteFlood() || packet->getPayloadType() == 9
      || width < 1 || width > 3 || width != packet->getPathHashSize()
      || hops > 4 || packet->getPathHashCount() < hops
      || unsigned(packet->getPathHashCount()) * width > MAX_PATH_SIZE) return false;
  const unsigned count = packet->getPathHashCount();
  if (fields.path_position == Exact && count != hops) return false;
  uint8_t wanted[12];
  memcpy(wanted, first, 9);
  memcpy(wanted + 9, fields.path_tail, 3);
  unsigned begin = fields.path_position == Suffix ? count - hops : 0;
  unsigned last = fields.path_position == Anywhere ? count - hops : begin;
  for (unsigned at = begin; at <= last; ++at)
    if (!memcmp(packet->path + at * width, wanted, hops * width)) return true;
  return false;
}

template <typename PacketType, typename Crypto = mesh::Utils>
inline bool matchesContent(const Fields& fields, const PacketType* packet,
                           const uint8_t* secret, uint8_t key_len,
                           uint32_t* work = nullptr, bool conservative = false,
                           bool* budget_exceeded = nullptr) {
  if (budget_exceeded) *budget_exceeded = false;
  if (!contentRequested(fields)) return true;
  if (!packet || !secret || (key_len != 16 && key_len != 32)
      || packet->getPayloadType() != PAYLOAD_TYPE_GRP_TXT
      || packet->payload_len <= PATH_HASH_SIZE + CIPHER_MAC_SIZE) return false;
  uint8_t plain[MAX_PACKET_PAYLOAD];
  int size = Crypto::MACThenDecrypt(secret, plain,
      packet->payload + PATH_HASH_SIZE, packet->payload_len - PATH_HASH_SIZE);
  mesh::FloodGroupPlainText::View view;
  if (size <= 0 || !mesh::FloodGroupPlainText::parse(
          plain, size_t(size), TXT_TYPE_PLAIN, view)) return false;
  using mesh::flood::PatternMatchResult;
  if (fields.sender[0]) {
    if (!view.has_sender) return false;
    const auto result = mesh::flood::matchTextPattern(fields.sender, strlen(fields.sender),
        view.sender.data, view.sender.size, false, work);
    if (result == PatternMatchResult::BudgetExceeded) {
      if (budget_exceeded) *budget_exceeded = true;
      return conservative;
    }
    if (result != PatternMatchResult::Match) return false;
  }
  if (!fields.text[0]) return true;
  const auto result = mesh::flood::matchTextPattern(fields.text, strlen(fields.text),
      view.text.data, view.text.size, false, work);
  if (result == PatternMatchResult::BudgetExceeded && budget_exceeded)
    *budget_exceeded = true;
  return result == PatternMatchResult::Match
      || (result == PatternMatchResult::BudgetExceeded && conservative);
}

template <typename File>
bool readFields(File& file, Fields& fields) {
  memset(&fields, 0, sizeof(fields));
  auto read = [&file](void* out, size_t size) {
    return file.read(static_cast<uint8_t*>(out), size) == size;
  };
  uint16_t minimum = 0, maximum = 0;
  if (!read(&minimum, 2) || !read(&maximum, 2)
      || minimum > MAX_PACKET_PAYLOAD || maximum > MAX_PACKET_PAYLOAD) return false;
  fields.min_payload = uint8_t(minimum);
  fields.max_payload = uint8_t(maximum);
  uint8_t sender_size = 0, text_size = 0;
  return read(&fields.route, 1) && read(&fields.probability, 1)
      && read(&fields.path_position, 1) && read(&fields.disabled, 1)
      && read(&fields.payload_set, 1)
      && read(fields.path_tail, 3)
      && read(&sender_size, 1) && sender_size < sizeof(fields.sender)
      && read(fields.sender, sender_size)
      && read(&text_size, 1) && text_size < sizeof(fields.text)
      && read(fields.text, text_size) && validFields(fields);
}

template <typename Writer>
bool writeFields(Writer& write, const Fields& fields) {
  const uint16_t minimum = fields.min_payload, maximum = fields.max_payload;
  uint8_t sender_size = uint8_t(strlen(fields.sender));
  uint8_t text_size = uint8_t(strlen(fields.text));
  return write(&minimum, 2) && write(&maximum, 2)
      && write(&fields.route, 1) && write(&fields.probability, 1)
      && write(&fields.path_position, 1) && write(&fields.disabled, 1)
      && write(&fields.payload_set, 1)
      && write(fields.path_tail, 3)
      && write(&sender_size, 1) && write(fields.sender, sender_size)
      && write(&text_size, 1) && write(fields.text, text_size);
}

template <typename Writer>
void appendQuoted(Writer& writer, const char* key, const char* value) {
  writer.append(" %s=\"", key);
  for (const char* p = value; *p; ++p) {
    if (*p == '"' || *p == '\\') writer.append("\\");
    writer.append("%c", *p);
  }
  writer.append("\"");
}

template <typename Writer>
void appendFields(Writer& writer, const Fields& fields, bool compact) {
  if (fields.sender[0]) appendQuoted(writer, compact ? "n" : "sender", fields.sender);
  if (fields.text[0]) appendQuoted(writer, compact ? "x" : "text", fields.text);
  if (fields.payload_set) {
    const char* key = compact ? "l" : "len";
    if (fields.max_payload == fields.min_payload) writer.append(" %s=%u", key, unsigned(fields.min_payload));
    else if (fields.max_payload == MAX_PACKET_PAYLOAD) writer.append(" %s=%u+", key, unsigned(fields.min_payload));
    else writer.append(" %s=%u-%u", key, unsigned(fields.min_payload), unsigned(fields.max_payload));
  }
  if (fields.route) writer.append(" %s=%s", compact ? "rt" : "route",
      fields.route == FloodRoute ? (compact ? "f" : "flood")
      : fields.route == DirectRoute ? (compact ? "d" : "direct") : (compact ? "a" : "any"));
  if (fields.probability)
    writer.append(" %s=%u", compact ? "pr" : "prob", unsigned(fields.probability));
  if (fields.disabled) writer.append(" %s=off", compact ? "e" : "enabled");
}

template <typename Writer>
void appendPath(Writer& writer, const Fields& fields, uint8_t width,
                uint8_t hops, const uint8_t first[9], bool compact) {
  if (!hops) return;
  const bool legacy = fields.path_position == Prefix && hops <= 3;
  writer.append(" %s=", legacy ? (compact ? "p" : "prefix") : (compact ? "pa" : "path"));
  if (!legacy && (fields.path_position == Prefix || fields.path_position == Exact)) writer.append("^");
  uint8_t path[12];
  memcpy(path, first, 9); memcpy(path + 9, fields.path_tail, 3);
  for (unsigned i = 0; i < hops; ++i) {
    if (i) writer.append(legacy ? "," : ">");
    for (unsigned j = 0; j < width; ++j) writer.append("%02X", unsigned(path[i * width + j]));
  }
  if (fields.path_position == Suffix || fields.path_position == Exact) writer.append("$");
}

} // namespace FloodRuleExtensions
