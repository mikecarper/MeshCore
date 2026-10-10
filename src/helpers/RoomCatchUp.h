#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

enum class RoomCatchUpMode : uint8_t { Before, SkipNext, KeepNewest };

struct RoomCatchUpPlan {
  uint32_t candidate_cursor;
  uint16_t skipped;
  bool pending_post_cancelled;
  uint16_t backlog_remaining;
};

inline bool parseRoomCatchUpCount(const char* input, uint32_t& result) {
  if (input == nullptr || *input == 0) return false;
  uint32_t value = 0;
  for (const char* next = input; *next; ++next) {
    if (*next < '0' || *next > '9') return false;
    const uint32_t digit = static_cast<uint32_t>(*next - '0');
    if (value > (UINT32_MAX - digit) / 10) return false;
    value = value * 10 + digit;
  }
  result = value;
  return true;
}

inline bool parseRoomCatchUpDatePart(const char* input, size_t length,
                                    uint32_t& value) {
  value = 0;
  for (size_t index = 0; index < length; ++index) {
    if (input[index] < '0' || input[index] > '9') return false;
    value = value * 10 + static_cast<uint32_t>(input[index] - '0');
  }
  return true;
}

// UTC only. Date-only values denote midnight. A zero epoch is a valid no-op
// cutoff. No libc time conversion, timezone state, or allocation is needed.
inline bool parseRoomCatchUpBefore(const char* input, uint32_t& result) {
  if (input == nullptr) return false;
  const size_t length = strlen(input);
  if ((length != 10 && length != 20) || input[4] != '-') {
    return parseRoomCatchUpCount(input, result);
  }
  if (input[7] != '-') return false;
  uint32_t year, month, day, hour = 0, minute = 0, second = 0;
  if (!parseRoomCatchUpDatePart(input, 4, year)
      || !parseRoomCatchUpDatePart(input + 5, 2, month)
      || !parseRoomCatchUpDatePart(input + 8, 2, day)) return false;
  if (length == 20 && (input[10] != 'T' || input[13] != ':'
      || input[16] != ':' || input[19] != 'Z'
      || !parseRoomCatchUpDatePart(input + 11, 2, hour)
      || !parseRoomCatchUpDatePart(input + 14, 2, minute)
      || !parseRoomCatchUpDatePart(input + 17, 2, second))) return false;
  if (year < 1970 || year > 2106 || month == 0 || month > 12
      || hour > 23 || minute > 59 || second > 59) return false;
  const bool leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
  static const uint8_t month_days[] = {31,28,31,30,31,30,31,31,30,31,30,31};
  const uint32_t limit = month_days[month - 1] + (month == 2 && leap ? 1 : 0);
  if (day == 0 || day > limit) return false;
  const uint32_t previous_year = year - 1;
  uint32_t days = (year - 1970) * 365
      + (previous_year / 4 - previous_year / 100 + previous_year / 400)
      - (1969 / 4 - 1969 / 100 + 1969 / 400);
  for (uint32_t index = 0; index + 1 < month; ++index) {
    days += month_days[index] + (index == 1 && leap ? 1 : 0);
  }
  days += day - 1;
  const uint64_t timestamp = static_cast<uint64_t>(days) * 86400
      + hour * 3600 + minute * 60 + second;
  if (timestamp > UINT32_MAX) return false;
  result = static_cast<uint32_t>(timestamp);
  return true;
}

// Room retention is bounded to 32 posts. The cyclic array need not be in
// timestamp order. Own posts never count toward unread backlog or a skip count.
// Cursor selection uses retained timestamps only, never a requested future
// date or cursor+1 arithmetic near UINT32_MAX.
template <typename Client, typename Post>
RoomCatchUpPlan planRoomCatchUp(const Client& client, const Post* posts,
                               size_t count, RoomCatchUpMode mode,
                               uint32_t value) {
  const uint32_t floor = client.extra.room.sync_since;
  RoomCatchUpPlan plan = {floor, 0, false, 0};
  if (posts == nullptr || count > 32) return plan;
  uint16_t total = 0;
  for (size_t index = 0; index < count; ++index) {
    const auto& post = posts[index];
    if (post.post_timestamp > floor && !post.author.matches(client.id)) ++total;
  }
  if (mode == RoomCatchUpMode::Before) {
    for (size_t index = 0; index < count; ++index) {
      const auto& post = posts[index];
      if (post.post_timestamp > plan.candidate_cursor && post.post_timestamp < value
          && !post.author.matches(client.id)) plan.candidate_cursor = post.post_timestamp;
    }
  } else if (mode == RoomCatchUpMode::SkipNext || mode == RoomCatchUpMode::KeepNewest) {
    const uint32_t target = mode == RoomCatchUpMode::SkipNext
        ? (value < total ? value : total) : (value < total ? total - value : 0);
    while (plan.skipped < target) {
      uint32_t next_timestamp = 0;
      for (size_t index = 0; index < count; ++index) {
        const auto& post = posts[index];
        if (post.post_timestamp > plan.candidate_cursor && !post.author.matches(client.id)
            && (next_timestamp == 0 || post.post_timestamp < next_timestamp)) {
          next_timestamp = post.post_timestamp;
        }
      }
      if (next_timestamp == 0) break;
      plan.candidate_cursor = next_timestamp;
      plan.skipped = 0;
      for (size_t index = 0; index < count; ++index) {
        const auto& post = posts[index];
        if (post.post_timestamp > floor && post.post_timestamp <= plan.candidate_cursor
            && !post.author.matches(client.id)) ++plan.skipped;
      }
    }
  }
  plan.skipped = 0;
  for (size_t index = 0; index < count; ++index) {
    const auto& post = posts[index];
    if (post.post_timestamp > floor && post.post_timestamp <= plan.candidate_cursor
        && !post.author.matches(client.id)) ++plan.skipped;
  }
  plan.backlog_remaining = total - plan.skipped;
  plan.pending_post_cancelled = plan.candidate_cursor > floor
      && client.extra.room.pending_ack != 0 && client.extra.room.pending_topic_revision == 0
      && client.extra.room.push_post_timestamp != 0
      && client.extra.room.push_post_timestamp <= plan.candidate_cursor;
  return plan;
}

// The caller cancels the matching transport retry before/after applying this
// plan. A delayed post ACK cannot match once pending_ack is cleared. An
// outstanding topic or a newer post is rechecked and retained unchanged.
template <typename Client>
bool applyRoomCatchUpPlan(Client& client, const RoomCatchUpPlan& plan) {
  if (plan.candidate_cursor <= client.extra.room.sync_since) return false;
  client.extra.room.sync_since = plan.candidate_cursor;
  client.extra.room.push_failures = 0;
  if (plan.pending_post_cancelled && client.extra.room.pending_topic_revision == 0
      && client.extra.room.push_post_timestamp != 0
      && client.extra.room.push_post_timestamp <= plan.candidate_cursor) {
    client.extra.room.pending_ack = 0;
    client.extra.room.push_post_timestamp = 0;
    client.extra.room.ack_timeout = 0;
  }
  return true;
}

} // namespace mesh
