#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {
namespace indicator_font {

// STAGEV2 is deliberately a separate command word from the legacy STAGE
// grammar. Old RP2040 services therefore reject it before consuming binary
// data, which makes the explicit ERROR COMMAND response safe to fall back on.
static constexpr size_t kStageV2ChunkBytes = 512;
static constexpr char kStageV2ReadyReply[] = "READY 2 512";
static constexpr char kStageV2LegacyUnsupportedReply[] = "ERROR COMMAND";

enum class StageV2BeginAction : uint8_t {
  UseAcknowledged,
  UseLegacy,
  Fail,
};

inline StageV2BeginAction classifyStageV2BeginReply(bool replied,
                                                     const char* reply) {
  if (!replied || reply == nullptr) return StageV2BeginAction::Fail;
  if (strcmp(reply, kStageV2ReadyReply) == 0) {
    return StageV2BeginAction::UseAcknowledged;
  }
  if (strcmp(reply, kStageV2LegacyUnsupportedReply) == 0) {
    return StageV2BeginAction::UseLegacy;
  }
  return StageV2BeginAction::Fail;
}

inline size_t stageV2ChunkSize(size_t total, size_t acknowledged) {
  if (acknowledged >= total) return 0;
  const size_t remaining = total - acknowledged;
  return remaining < kStageV2ChunkBytes ? remaining : kStageV2ChunkBytes;
}

// Advance only after the receiver has accepted the complete negotiated block.
// This keeps cumulative ACK offsets unambiguous and rejects premature/partial
// progress observations.
inline bool advanceStageV2Offset(size_t total, size_t acknowledged,
                                 size_t stored, size_t& next) {
  const size_t expected = stageV2ChunkSize(total, acknowledged);
  if (expected == 0 || stored != expected) return false;
  next = acknowledged + expected;  // bounded by total, so this cannot overflow
  return true;
}

inline bool parseStageV2Ack(const char* reply, size_t expectedOffset) {
  static constexpr char prefix[] = "ACK ";
  if (reply == nullptr || expectedOffset == 0
      || strncmp(reply, prefix, sizeof(prefix) - 1) != 0) {
    return false;
  }

  const char* cursor = reply + sizeof(prefix) - 1;
  if (*cursor < '0' || *cursor > '9') return false;
  // The RP2040 emits canonical decimal offsets. Reject alternate spellings so
  // stale or damaged lines cannot be interpreted leniently.
  if (*cursor == '0' && cursor[1] != 0) return false;

  size_t parsed = 0;
  const size_t maximum = (size_t)-1;
  do {
    const size_t digit = (size_t)(*cursor - '0');
    if (parsed > (maximum - digit) / 10) return false;
    parsed = parsed * 10 + digit;
    ++cursor;
  } while (*cursor >= '0' && *cursor <= '9');

  return *cursor == 0 && parsed == expectedOffset;
}

}  // namespace indicator_font
}  // namespace mesh
