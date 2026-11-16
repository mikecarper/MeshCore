#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "CompanionFrameQueue.h"

namespace mesh { namespace ble_ota {

static constexpr uint8_t MAGIC[] = {0x7f, 0x4f, 0x54, 0x41, 1};
static constexpr uint8_t FRAGMENT = 0x7e;
static constexpr size_t ATT_MIN_PAYLOAD = 20;
static constexpr size_t HEADER = 3;
static constexpr size_t MAX_FRAME = 188;

// Keep a partly delivered frame in place while ordinary queue priority and
// displacement rules continue to apply to the waiting tail.
template <typename Frame, typename Count>
bool enqueue(Frame* queue, Count& count, size_t capacity, bool pinned,
             const uint8_t* data, size_t len) {
  const size_t prefix = pinned && count > 0 ? 1 : 0;
  if (static_cast<size_t>(count) > capacity || capacity <= prefix) return false;
  Count tail = static_cast<Count>(count - prefix);
  if (!mesh::enqueueCompanionFrame(queue + prefix, tail, capacity - prefix, data, len)) return false;
  count = static_cast<Count>(tail + prefix);
  return true;
}

inline bool isHello(const uint8_t* data, size_t len) {
  return data && len == sizeof(MAGIC) && memcmp(data, MAGIC, sizeof(MAGIC)) == 0;
}

class Receiver {
  uint8_t bytes_[MAX_FRAME] = {};
  uint16_t length_ = 0;
  uint16_t received_ = 0;
public:
  enum class Result { Invalid, Partial, Complete };
  void reset() { length_ = received_ = 0; }
  Result accept(const uint8_t* fragment, size_t len) {
    if (!fragment || len <= HEADER || len > ATT_MIN_PAYLOAD || fragment[0] != FRAGMENT) { reset(); return Result::Invalid; }
    const size_t length = fragment[1], offset = fragment[2], count = len - HEADER;
    if (!length || length > MAX_FRAME || offset != received_ || count > length - offset ||
        (received_ && length != length_) || (!received_ && offset)) { reset(); return Result::Invalid; }
    if (!received_) length_ = length;
    memcpy(bytes_ + offset, fragment + HEADER, count);
    received_ += count;
    return received_ == length_ ? Result::Complete : Result::Partial;
  }
  uint8_t received() const { return static_cast<uint8_t>(received_); }
  size_t copyTo(uint8_t* out, size_t capacity) {
    if (!out || received_ != length_ || !length_ || capacity < length_) return 0;
    const size_t length = length_;
    memcpy(out, bytes_, length);
    reset();
    return length;
  }
};

inline size_t fragment(const uint8_t* frame, size_t len, size_t offset,
                       uint8_t* out, size_t capacity) {
  if (!frame || !out || !len || len > MAX_FRAME || offset >= len || capacity <= HEADER) return 0;
  const size_t count = len - offset < capacity - HEADER ? len - offset : capacity - HEADER;
  out[0] = FRAGMENT; out[1] = static_cast<uint8_t>(len); out[2] = static_cast<uint8_t>(offset);
  memcpy(out + HEADER, frame + offset, count);
  return HEADER + count;
}

} } // namespace mesh::ble_ota
