#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

inline bool isCompanionPushFrame(const uint8_t* frame, size_t len) {
  return frame != NULL && len > 0 && (frame[0] & 0x80) != 0;
}

/**
 * Add a frame to a companion transport's contiguous outbound queue.
 *
 * Protocol responses use codes below 0x80 and complete an app command that is
 * waiting for them. Push frames use codes at or above 0x80 and are
 * asynchronous. Keep one slot available for a response, and if an older queue
 * is already full of mixed traffic, let a response replace the newest push.
 * Responses are inserted before pushes so packet-log traffic cannot delay a
 * command indefinitely.
 */
template <typename Frame, typename QueueLength>
bool enqueueCompanionFrame(Frame queue[], QueueLength& queue_len, size_t capacity,
                           const uint8_t* src, size_t len) {
  if (queue == NULL || src == NULL || len == 0 || capacity == 0) return false;

  size_t count = static_cast<size_t>(queue_len);
  if (count > capacity) return false;

  const bool push = isCompanionPushFrame(src, len);
  if (push && count >= capacity - 1) {
    return false;  // preserve one slot for the reply to an app command
  }

  if (count == capacity) {
    // A response may displace best-effort asynchronous traffic, but never an
    // earlier response that another command is already waiting for.
    size_t evict = count;
    while (evict > 0) {
      --evict;
      if (isCompanionPushFrame(queue[evict].buf, queue[evict].len)) break;
    }
    if (!isCompanionPushFrame(queue[evict].buf, queue[evict].len)) return false;

    for (size_t i = evict; i + 1 < count; ++i) {
      queue[i] = queue[i + 1];
    }
    --count;
  }

  size_t insert_at = count;
  if (!push) {
    // Keep responses FIFO with respect to one another, ahead of asynchronous
    // pushes such as raw-packet logs and message-waiting notifications.
    // The stable partition also repairs a queue populated by older admission
    // behavior before this policy gets a chance to add the next response.
    for (size_t i = 1; i < count; ++i) {
      if (isCompanionPushFrame(queue[i].buf, queue[i].len)) continue;

      Frame response = queue[i];
      size_t j = i;
      while (j > 0
             && isCompanionPushFrame(queue[j - 1].buf, queue[j - 1].len)) {
        queue[j] = queue[j - 1];
        --j;
      }
      queue[j] = response;
    }
    for (size_t i = 0; i < count; ++i) {
      if (isCompanionPushFrame(queue[i].buf, queue[i].len)) {
        insert_at = i;
        break;
      }
    }
    for (size_t i = count; i > insert_at; --i) {
      queue[i] = queue[i - 1];
    }
  }

  queue[insert_at].len = len;
  memcpy(queue[insert_at].buf, src, len);
  queue_len = static_cast<QueueLength>(count + 1);
  return true;
}

}  // namespace mesh
