#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

inline bool isCompanionPushFrame(const uint8_t* frame, size_t len) {
  return frame != NULL && len > 0 && (frame[0] & 0x80) != 0;
}

enum CompanionFrameClass : uint8_t {
  COMPANION_RESPONSE = 0,
  COMPANION_REQUIRED_PUSH = 1,
  COMPANION_BEST_EFFORT_PUSH = 2,
};

/**
 * Companion push codes share one numeric range, but they do not share one
 * delivery contract. Command completions, login results, MSG_WAITING, and
 * status/telemetry replies are required for the app to make progress. Only
 * unsolicited discovery/path updates and packet logs are safe to shed.
 *
 * Unknown future push codes default to required so an older transport does
 * not silently discard a new protocol result it does not yet recognize.
 */
inline CompanionFrameClass companionFrameClass(const uint8_t* frame,
                                                size_t len) {
  if (!isCompanionPushFrame(frame, len)) return COMPANION_RESPONSE;
  switch (frame[0]) {
    case 0x80:  // ADVERT
    case 0x81:  // PATH_UPDATED
    case 0x84:  // RAW_DATA
    case 0x88:  // LOG_RX_DATA
    case 0x8A:  // NEW_ADVERT
      return COMPANION_BEST_EFFORT_PUSH;
    default:
      return COMPANION_REQUIRED_PUSH;
  }
}

inline bool companionFrameRequiresDelivery(const uint8_t* frame, size_t len) {
  return companionFrameClass(frame, len) != COMPANION_BEST_EFFORT_PUSH;
}

// Queue priority and transport ownership are separate. Some required pushes
// (MSG_WAITING, control data, and contact-capacity notifications) are
// unsolicited state changes which every connected app should observe. Only
// direct command responses and known asynchronous command completions inherit
// the requester's route. New push codes default to broadcast, which is safer
// than exposing one client's completion to whichever client spoke last.
inline bool companionFrameUsesRequesterRoute(const uint8_t* frame,
                                              size_t len) {
  if (frame == NULL || len == 0) return false;
  if (!isCompanionPushFrame(frame, len)) return true;
  switch (frame[0]) {
    case 0x82:  // SEND_CONFIRMED
    case 0x85:  // LOGIN_SUCCESS
    case 0x86:  // LOGIN_FAIL
    case 0x87:  // STATUS_RESPONSE
    case 0x89:  // TRACE_DATA
    case 0x8B:  // TELEMETRY_RESPONSE
    case 0x8C:  // BINARY_RESPONSE
    case 0x8D:  // PATH_DISCOVERY_RESPONSE
      return true;
    default:
      return false;
  }
}

/**
 * Add a frame to a companion transport's contiguous outbound queue.
 *
 * Protocol responses use codes below 0x80. Push frames use codes at or above
 * 0x80, but some pushes also complete an app operation. Keep one slot away
 * from best-effort traffic, let required frames displace best-effort traffic,
 * and order responses before required pushes before best-effort pushes.
 */
template <typename Frame, typename QueueLength>
bool enqueueCompanionFrame(Frame queue[], QueueLength& queue_len, size_t capacity,
                           const uint8_t* src, size_t len) {
  if (queue == NULL || src == NULL || len == 0 || capacity == 0) return false;

  size_t count = static_cast<size_t>(queue_len);
  if (count > capacity) return false;

  const CompanionFrameClass incoming_class = companionFrameClass(src, len);

  // MSG_WAITING is a level-triggered tickle: one queued copy is enough to tell
  // the app to fetch all pending messages. Coalescing it preserves space for
  // command completions without losing information.
  if (src[0] == 0x83) {
    for (size_t i = 0; i < count; ++i) {
      if (queue[i].len > 0 && queue[i].buf[0] == 0x83) return true;
    }
  }

  if (incoming_class == COMPANION_BEST_EFFORT_PUSH
      && count >= capacity - 1) {
    return false;  // preserve one slot for delivery-required traffic
  }

  if (count == capacity) {
    // A delivery-required frame may displace best-effort asynchronous traffic,
    // but never another delivery-required frame.
    if (incoming_class == COMPANION_BEST_EFFORT_PUSH) return false;
    size_t evict = count;
    while (evict > 0) {
      --evict;
      if (companionFrameClass(queue[evict].buf, queue[evict].len)
          == COMPANION_BEST_EFFORT_PUSH) {
        break;
      }
    }
    if (companionFrameClass(queue[evict].buf, queue[evict].len)
        != COMPANION_BEST_EFFORT_PUSH) {
      return false;
    }

    for (size_t i = evict; i + 1 < count; ++i) {
      queue[i] = queue[i + 1];
    }
    --count;
  }

  // Stable insertion sort also repairs a queue populated by older admission
  // behavior before the next frame is added.
  for (size_t i = 1; i < count; ++i) {
    Frame current = queue[i];
    CompanionFrameClass current_class =
        companionFrameClass(current.buf, current.len);
    size_t j = i;
    while (j > 0
           && companionFrameClass(queue[j - 1].buf, queue[j - 1].len)
                  > current_class) {
      queue[j] = queue[j - 1];
      --j;
    }
    queue[j] = current;
  }

  size_t insert_at = count;
  for (size_t i = 0; i < count; ++i) {
    if (companionFrameClass(queue[i].buf, queue[i].len) > incoming_class) {
      insert_at = i;
      break;
    }
  }
  for (size_t i = count; i > insert_at; --i) {
    queue[i] = queue[i - 1];
  }

  queue[insert_at].len = len;
  memcpy(queue[insert_at].buf, src, len);
  queue_len = static_cast<QueueLength>(count + 1);
  return true;
}

}  // namespace mesh
