#pragma once

#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace mesh {

// One loop-owned operation and eight bounded browser sequence floors. Caller
// serializes submit/read/begin/finish with its mutex; processing runs outside
// that mutex. Running input and output cannot be recycled by an HTTP handler.
// Sequence floors expire after twenty minutes and on reboot. Clients never
// retry a write after that window or across a changed boot identifier.
class RoomWebMailbox {
public:
  static constexpr size_t INPUT_CAPACITY = 4097;
  static constexpr size_t OUTPUT_CAPACITY = 2048;
  static constexpr uint32_t OWNER_TTL_MS = 1200000;
  static constexpr uint32_t RESULT_TTL_MS = 30000;
  enum class Result { Accepted, Busy, Invalid, Completed, Mismatch };
  enum class State { Idle, Pending, Running, Done };
private:
  struct Owner { uint8_t token[32]; uint32_t sequence = 0, seen = 0; } owners_[8] = {};
  uint8_t token_[32] = {}, digest_[32] = {};
  uint32_t sequence_ = 0, finished_ = 0;
  State state_ = State::Idle;
  bool delivered_ = false;
  bool owner_was_new_ = false;
  char input_[INPUT_CAPACITY] = {}, output_[OUTPUT_CAPACITY] = {};
public:
  static bool decodeToken(const char* text, uint8_t (&out)[32]) {
    if (!text || strlen(text) != 64) return false;
    uint8_t parsed[32], nonzero = 0;
    for (size_t i = 0; i < 32; ++i) {
      unsigned byte = 0;
      for (size_t n = 0; n < 2; ++n) {
        char c = text[i * 2 + n];
        unsigned value;
        if (c >= '0' && c <= '9') value = c - '0';
        else if (c >= 'a' && c <= 'f') value = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') value = c - 'A' + 10;
        else return false;
        byte = (byte << 4) | value;
      }
      parsed[i] = uint8_t(byte); nonzero |= parsed[i];
    }
    if (!nonzero) return false;
    memcpy(out, parsed, sizeof(parsed)); return true;
  }
  State state() const { return state_; }
  uint32_t sequence() const { return sequence_; }
  Result submit(const uint8_t (&token)[32], uint32_t sequence,
                const uint8_t (&digest)[32], const char* body, size_t length, uint32_t now) {
    if (!body || !sequence || !length || length >= INPUT_CAPACITY
        || memchr(body, 0, length)) return Result::Invalid;
    if (state_ != State::Idle && sequence == sequence_ && memcmp(token_, token, 32) == 0) {
      return memcmp(digest_, digest, 32) == 0 ? Result::Accepted : Result::Mismatch;
    }
    Owner* owner = nullptr;
    Owner* empty = nullptr;
    for (auto& entry : owners_) {
      if (entry.sequence && uint32_t(now - entry.seen) < OWNER_TTL_MS) {
        if (memcmp(entry.token, token, 32) == 0) owner = &entry;
      } else if (!empty) empty = &entry;
    }
    if (owner && sequence <= owner->sequence) return Result::Completed;
    if (state_ == State::Pending || state_ == State::Running
        || (state_ == State::Done && !delivered_
            && uint32_t(now - finished_) < RESULT_TTL_MS)) return Result::Busy;
    owner_was_new_ = !owner;
    if (!owner) owner = empty;
    if (!owner) return Result::Busy;
    memcpy(owner->token, token, 32); owner->sequence = sequence; owner->seen = now;
    memcpy(token_, token, 32); memcpy(digest_, digest, 32);
    memcpy(input_, body, length); input_[length] = 0; output_[0] = 0;
    sequence_ = sequence; delivered_ = false; state_ = State::Pending;
    return Result::Accepted;
  }
  bool begin() {
    if (state_ != State::Pending) return false;
    state_ = State::Running; return true;
  }
  char* input() { return input_; }
  char* output() { return output_; }
  const uint8_t* token() const { return token_; }
  void finish(uint32_t now, bool authenticated = true) {
    if (state_ != State::Running) return;
    memset(input_, 0, sizeof(input_)); // passwords and article data have served their purpose
    output_[OUTPUT_CAPACITY - 1] = 0;
    finished_ = now; state_ = State::Done;
    // Unauthenticated traffic must not occupy all eight sequence-floor slots
    // for twenty minutes. Existing authenticated floors are never erased.
    if (!authenticated && owner_was_new_) {
      for (auto& owner : owners_) if (owner.sequence == sequence_ && memcmp(owner.token, token_, 32) == 0) owner = Owner{};
    }
  }
  // Output must be copied while caller holds the mutex. Wrong owners never
  // learn whether another user's operation is pending or completed.
  const char* read(const uint8_t (&token)[32], uint32_t sequence, bool& pending) {
    pending = false;
    if (state_ == State::Idle || sequence_ != sequence || memcmp(token_, token, 32) != 0) return nullptr;
    if (state_ != State::Done) { pending = true; return nullptr; }
    return output_;
  }
  void delivered(const uint8_t (&token)[32], uint32_t sequence) {
    if (state_ == State::Done && sequence_ == sequence && memcmp(token_, token, 32) == 0) delivered_ = true;
  }
};
static_assert(sizeof(RoomWebMailbox) <= 6656, "Update the room WebConfig heap budget");

} // namespace mesh
