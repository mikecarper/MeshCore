#pragma once

#include <stdarg.h>
#include <stdio.h>
#include "FloodFilterPolicy.h"

namespace FloodRuleCLI {

inline bool isChannelReference(const char* text) {
  return FloodFilterPolicy::channelHashPrefixEqual(text, "key:");
}

// Keep the existing non-secret key:XXXXXXXX display usable when editing or
// copying rules on this node. Never resolve a fingerprint collision to an
// arbitrary key, and never widen it into an unauthenticated channel hash.
template<typename Entry>
bool copyChannelReference(const char* text, const Entry* entries, unsigned count,
                          uint8_t& key_len, uint8_t& channel_hash,
                          uint8_t* secret, char* name, size_t name_len) {
  if (!isChannelReference(text) || !entries) return false;
  const Entry* found = nullptr;
  for (unsigned i = 0; i < count; i++) {
    const auto& entry = entries[i];
    if (!entry.active || !isChannelReference(entry.channel_name)
        || !FloodFilterPolicy::channelRequiresAuthentication(entry.channel_key_len)
        || strlen(text) != strlen(entry.channel_name)
        || !FloodFilterPolicy::channelHashPrefixEqual(text, entry.channel_name)) continue;
    if (found && !FloodFilterPolicy::sameChannelKey(found->channel_key_len,
          found->channel_secret, entry.channel_key_len, entry.channel_secret)) return false;
    found = &entry;
  }
  if (!found) return false;
  key_len = found->channel_key_len;
  channel_hash = found->channel_hash;
  memcpy(secret, found->channel_secret, sizeof(found->channel_secret));
  snprintf(name, name_len, "%s", found->channel_name);
  return true;
}

// No temporary expansion buffer: compact and descriptive commands feed the
// same parser. This writer emits a complete, pasteable command or an error,
// never a truncated command whose missing suffix could change its meaning.
class CommandWriter {
  char* _out;
  size_t _capacity;
  size_t _used = 0;
  bool _complete = true;
public:
  CommandWriter(char* out, size_t capacity) : _out(out), _capacity(capacity) {}
  void append(const char* format, ...) {
    if (!_complete || !_out || _used >= _capacity) { _complete = false; return; }
    va_list args;
    va_start(args, format);
    int length = vsnprintf(_out + _used, _capacity - _used, format, args);
    va_end(args);
    if (length < 0 || (size_t)length >= _capacity - _used) _complete = false;
    else _used += (size_t)length;
  }
  bool finish() {
    if (!_complete && _out && _capacity) {
      snprintf(_out, _capacity, "Err - compact command exceeds reply size");
    }
    return _complete;
  }
};

// Generalized repeater and room-server rows have the same named fields.
// Defaults are omitted only when the setter reconstructs exactly that value.
template<typename Entry>
bool formatCompact(char* out, size_t capacity, unsigned slot, const Entry& entry) {
  using namespace FloodFilterPolicy;
  CommandWriter text(out, capacity);
  text.append("set fr.%u ", slot);
  if (entry.payload_type == 0xFF) text.append("any");
  else text.append("%u", (unsigned)entry.payload_type);
  if (entry.transport_modes != RULE_MODE_RADIO)
    text.append(" m=%s", compactRuleModeName(entry.transport_modes));
  if (entry.min_hops != 0 || entry.max_hops != 63) {
    if (entry.min_hops == entry.max_hops) text.append(" %u", (unsigned)entry.min_hops);
    else if (entry.max_hops == 63) text.append(" %u+", (unsigned)entry.min_hops);
    else text.append(" %u-%u", (unsigned)entry.min_hops, (unsigned)entry.max_hops);
  }
  if (entry.channel_key_len != 0) text.append(" c=%s", entry.channel_name);
  if (entry.match_blacklisted_path) text.append(" p=bl");
  else if (entry.path_hops) {
    text.append(" p=");
    for (unsigned hop = 0; hop < entry.path_hops; hop++) {
      if (hop) text.append(",");
      for (unsigned byte = 0; byte < entry.path_hash_size; byte++)
        text.append("%02X", entry.path[hop * entry.path_hash_size + byte]);
    }
  }
  switch (entry.incoming_scope_kind) {
    case RULE_IN_NONE: text.append(" i=n"); break;
    case RULE_IN_SCOPED: text.append(" i=s"); break;
    case RULE_IN_ALLOWED: text.append(" i=a"); break;
    case RULE_IN_UNKNOWN: text.append(" i=u"); break;
    case RULE_IN_SCOPE: text.append(" i=s:%s", entry.incoming_scope_name); break;
    case RULE_IN_REGION: text.append(" i=r:%s", entry.incoming_scope_name); break;
    default: break;
  }
  if (entry.drop_on_match) text.append(" d");
  else if (entry.scope_name[0]) text.append(" s=%s", entry.scope_name);
  else if (entry.target_region_name[0]) text.append(" r=%s", entry.target_region_name);
  if (entry.rate_limit_enabled) text.append(" q=%u", (unsigned)entry.rate_per_minute);
  if (entry.priority) text.append(" pri=%u", (unsigned)entry.priority);
  if (entry.stop_on_match) text.append(" s");
  if (entry.scope_uses_slow_timing || entry.suspend_on_temp_radio) {
    text.append(" f=%s%s%s", entry.scope_uses_slow_timing ? "s" : "",
        entry.suspend_on_temp_radio ? "t" : "", entry.retry_on_match ? "r" : "");
  } else if (entry.retry_on_match) text.append(" r");
  return text.finish();
}

}  // namespace FloodRuleCLI
