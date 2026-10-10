#pragma once

#include <stdio.h>
#include "RoomAccessPolicyStore.h"

namespace mesh {

inline bool roomAclShouldPersist(uint8_t permissions, bool explicitly_assigned,
                                 uint8_t role_mask, uint8_t admin_role) {
  return explicitly_assigned || (permissions & role_mask) == (admin_role & role_mask);
}

// The two counts live beside their actual ClientInfo identity, not in a second
// table keyed by a short hash. The role owns resetting all counts when the
// policy's one shared monotonic minute expires. Re-login must not reset them.
class RoomAccessPolicy {
  RoomAccessSettings settings_;
  uint32_t quota_window_started_ = 0;
  bool quota_window_active_ = false;
  bool healthy_ = false;

  int findBan(const uint8_t* key) const {
    if (!key) return -1;
    for (uint8_t i = 0; i < settings_.ban_count; ++i) {
      if (memcmp(settings_.bans[i], key, ROOM_ACCESS_KEY_SIZE) == 0) return i;
    }
    return -1;
  }
  template <typename Filesystem>
  bool commit(Filesystem* fs, const RoomAccessSettings& next) {
    if (!healthy_) return false;
    const auto result = room_access_detail::save(fs, next);
    if (result == room_access_detail::SaveResult::Unavailable) healthy_ = false;
    if (result != room_access_detail::SaveResult::Saved) return false;
    if (settings_.posts_per_minute != next.posts_per_minute
        || settings_.polls_per_minute != next.polls_per_minute) quota_window_active_ = false;
    settings_ = next;
    return true;
  }
  bool consume(uint16_t& used, uint16_t limit) const {
    if (!healthy_) return false;
    if (limit == 0) return true;
    if (used >= limit) return false;
    ++used;
    return true;
  }
  static bool prefix(const char* command, const char* expected, const char*& args) {
    const size_t length = strlen(expected);
    if (strncmp(command, expected, length) != 0
        || (command[length] != 0 && command[length] != ' ')) return false;
    args = command + length;
    while (*args == ' ') ++args;
    return true;
  }
  static bool parseUnsigned(const char* value, uint16_t& result) {
    if (!value || *value < '0' || *value > '9') return false;
    uint32_t number = 0;
    do {
      number = number * 10 + unsigned(*value++ - '0');
      if (number > UINT16_MAX) return false;
    } while (*value >= '0' && *value <= '9');
    if (*value != 0) return false;
    result = static_cast<uint16_t>(number);
    return true;
  }

public:
  enum class BanResult : uint8_t { Saved, Unchanged, InvalidKey, Full, StorageFailure };

  bool healthy() const { return healthy_; }
  uint16_t postRate() const { return settings_.posts_per_minute; }
  uint16_t pollRate() const { return settings_.polls_per_minute; }
  uint8_t banCount() const { return settings_.ban_count; }
  const uint8_t* banAt(size_t index) const {
    return index < settings_.ban_count ? settings_.bans[index] : nullptr;
  }
  bool isBanned(const uint8_t* key) const { return findBan(key) >= 0; }
  // Apply before password checks and to existing sessions too. Unknown policy
  // storage cannot silently turn a banned identity into an admitted identity.
  bool allowsIdentity(const uint8_t* key) const {
    return healthy_ && room_access_detail::nonzeroKey(key) && !isBanned(key);
  }
  template <typename Filesystem>
  bool load(Filesystem* fs) {
    RoomAccessSettings staged;
    healthy_ = room_access_detail::recover(fs, staged, false);
    if (healthy_) settings_ = staged;
    quota_window_active_ = false;
    return healthy_;
  }
  template <typename Filesystem>
  BanResult addBan(Filesystem* fs, const uint8_t* key) {
    if (!room_access_detail::nonzeroKey(key)) return BanResult::InvalidKey;
    if (!healthy_) return BanResult::StorageFailure;
    if (isBanned(key)) return BanResult::Unchanged;
    if (settings_.ban_count == ROOM_ACCESS_MAX_BANS) return BanResult::Full;
    RoomAccessSettings next = settings_;
    memcpy(next.bans[next.ban_count++], key, ROOM_ACCESS_KEY_SIZE);
    return commit(fs, next) ? BanResult::Saved : BanResult::StorageFailure;
  }
  template <typename Filesystem>
  BanResult removeBan(Filesystem* fs, const uint8_t* key) {
    if (!room_access_detail::nonzeroKey(key)) return BanResult::InvalidKey;
    if (!healthy_) return BanResult::StorageFailure;
    const int at = findBan(key);
    if (at < 0) return BanResult::Unchanged;
    RoomAccessSettings next = settings_;
    --next.ban_count;
    if (at < next.ban_count) {
      memmove(next.bans[at], next.bans[at + 1], (next.ban_count - at) * ROOM_ACCESS_KEY_SIZE);
    }
    memset(next.bans[next.ban_count], 0, ROOM_ACCESS_KEY_SIZE);
    return commit(fs, next) ? BanResult::Saved : BanResult::StorageFailure;
  }
  template <typename Filesystem>
  bool setRates(Filesystem* fs, uint16_t post_rate, uint16_t poll_rate) {
    if (!healthy_) return false;
    if (post_rate == postRate() && poll_rate == pollRate()) return true;
    RoomAccessSettings next = settings_;
    next.posts_per_minute = post_rate;
    next.polls_per_minute = poll_rate;
    return commit(fs, next);
  }
  template <typename ResetCounts>
  void serviceQuotaWindow(uint32_t now, ResetCounts reset_counts) {
    if (!quota_window_active_ || uint32_t(now - quota_window_started_) >= 60000UL) {
      reset_counts();
      quota_window_started_ = now;
      quota_window_active_ = true;
    }
  }
  bool consumePost(uint16_t& used) const { return consume(used, postRate()); }
  bool consumeKeepAlive(uint16_t& used) const { return consume(used, pollRate()); }

  static bool parseFullKey(const char* text, uint8_t (&key)[ROOM_ACCESS_KEY_SIZE]) {
    if (!text || strlen(text) != ROOM_ACCESS_KEY_SIZE * 2) return false;
    uint8_t parsed[ROOM_ACCESS_KEY_SIZE];
    for (size_t i = 0; i < sizeof(parsed); ++i) {
      uint8_t byte = 0;
      for (unsigned nibble = 0; nibble < 2; ++nibble) {
        const char digit = text[i * 2 + nibble];
        unsigned value;
        if (digit >= '0' && digit <= '9') value = digit - '0';
        else if (digit >= 'a' && digit <= 'f') value = digit - 'a' + 10;
        else if (digit >= 'A' && digit <= 'F') value = digit - 'A' + 10;
        else return false;
        byte = static_cast<uint8_t>((byte << 4) | value);
      }
      parsed[i] = byte;
    }
    if (!room_access_detail::nonzeroKey(parsed)) return false;
    memcpy(key, parsed, sizeof(parsed));
    return true;
  }
  void formatBanPage(unsigned page, char* reply, size_t capacity) const {
    if (!reply || capacity == 0) return;
    if (!healthy_) { snprintf(reply, capacity, "Err - room access storage unavailable"); return; }
    const unsigned pages = settings_.ban_count == 0 ? 1 : (settings_.ban_count + 1) / 2;
    if (page == 0 || page > pages) {
      snprintf(reply, capacity, "Err - ban page range: 1-%u", pages); return;
    }
    if (settings_.ban_count == 0) { snprintf(reply, capacity, "Room bans: empty"); return; }
    const unsigned offset = (page - 1) * 2;
    const unsigned rows = settings_.ban_count - offset < 2 ? settings_.ban_count - offset : 2;
    const int header = snprintf(reply, capacity, "Room bans %u/%u", page, pages);
    if (header < 0 || size_t(header) >= capacity
        || rows > (capacity - size_t(header) - 1) / (ROOM_ACCESS_KEY_SIZE * 2 + 1)) {
      snprintf(reply, capacity, "Err - ban reply too small"); return;
    }
    static const char hex_digits[] = "0123456789abcdef";
    size_t used = static_cast<size_t>(header);
    for (unsigned row = 0; row < rows; ++row) {
      reply[used++] = '\n';
      const uint8_t* key = settings_.bans[offset + row];
      for (size_t i = 0; i < ROOM_ACCESS_KEY_SIZE; ++i) {
        reply[used++] = hex_digits[key[i] >> 4];
        reply[used++] = hex_digits[key[i] & 15];
      }
    }
    reply[used] = 0;
  }

  // The role must authorize Admin/local access before invoking this backend.
  template <typename Filesystem>
  bool handleConfig(Filesystem* fs, const char* command, char* reply, size_t capacity) {
    if (!command || !reply || capacity == 0) return false;
    const char* args;
    bool remove = false;
    if (prefix(command, "room.ban", args) || (remove = prefix(command, "room.unban", args))) {
      uint8_t key[ROOM_ACCESS_KEY_SIZE];
      if (!parseFullKey(args, key)) {
        snprintf(reply, capacity, "Err - use a complete 64-hex public key"); return true;
      }
      const BanResult result = remove ? removeBan(fs, key) : addBan(fs, key);
      if (result == BanResult::Full) snprintf(reply, capacity, "Err - room ban list full (32)");
      else if (result == BanResult::StorageFailure) snprintf(reply, capacity, "Err - room access save failed");
      else snprintf(reply, capacity, remove ? "OK - room ban removed" : "OK - room ban saved");
      return true;
    }
    if (prefix(command, "get room.bans", args)) {
      uint16_t page = 1;
      if (*args && !parseUnsigned(args, page)) snprintf(reply, capacity, "Err - usage: get room.bans [page]");
      else formatBanPage(page, reply, capacity);
      return true;
    }
    bool post = false;
    if ((post = prefix(command, "get room.post.rate", args)) || prefix(command, "get room.poll.rate", args)) {
      if (*args) return false;
      if (!healthy_) snprintf(reply, capacity, "Err - room access storage unavailable");
      else snprintf(reply, capacity, "> %u/min (0=off)", unsigned(post ? postRate() : pollRate()));
      return true;
    }
    if ((post = prefix(command, "set room.post.rate", args)) || prefix(command, "set room.poll.rate", args)) {
      uint16_t value;
      if (!parseUnsigned(args, value)) snprintf(reply, capacity, "Err - rate must be 0-65535/min");
      else if (!setRates(fs, post ? value : postRate(), post ? pollRate() : value))
        snprintf(reply, capacity, "Err - room access save failed");
      else snprintf(reply, capacity, "OK - room rate saved");
      return true;
    }
    return false;
  }
};
static_assert(sizeof(RoomAccessPolicy) <= 1040,
              "Room access policy must not allocate a duplicate identity table");
} // namespace mesh
