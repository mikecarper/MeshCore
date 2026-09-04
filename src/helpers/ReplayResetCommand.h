#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

enum class ReplayResetKind : uint8_t {
  NotReplay,
  Invalid,
  ExactKey,
  ExactKeyConfirm,
  AllConfirm,
};

struct ReplayResetRequest {
  ReplayResetKind kind;
  uint8_t key[32];
  uint8_t token[16];
};

namespace replay_reset_detail {

inline bool space(char value) {
  return value == ' ' || value == '\t' || value == '\r' || value == '\n';
}

inline const char* skipSpace(const char* text) {
  while (space(*text)) ++text;
  return text;
}

inline char lower(char value) {
  return value >= 'A' && value <= 'Z' ? (char)(value + ('a' - 'A')) : value;
}

inline bool word(const char* text, size_t length, const char* expected) {
  size_t i = 0;
  while (i < length && expected[i] != 0) {
    if (lower(text[i]) != expected[i]) return false;
    ++i;
  }
  return i == length && expected[i] == 0;
}

inline size_t wordLength(const char* text) {
  size_t length = 0;
  while (text[length] != 0 && !space(text[length])) ++length;
  return length;
}

inline int hexDigit(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  value = lower(value);
  return value >= 'a' && value <= 'f' ? value - 'a' + 10 : -1;
}

inline bool hex(const char* text, size_t length, uint8_t* output,
                size_t output_length) {
  if (length != output_length * 2U) return false;
  for (size_t i = 0; i < output_length; ++i) {
    const int high = hexDigit(text[2U * i]);
    const int low = hexDigit(text[2U * i + 1U]);
    if (high < 0 || low < 0) return false;
    output[i] = (uint8_t)((high << 4) | low);
  }
  return true;
}

}  // namespace replay_reset_detail

// Use this same parser before the receiver's timestamp mutation and in the
// command handler. Even malformed replay commands must not advance a sender's
// live floor: an old consumed confirmation could otherwise poison it again.
// This parser supplies no authentication or transport permission by itself.
inline ReplayResetKind parseReplayResetCommand(const char* command,
                                               ReplayResetRequest& request) {
  memset(&request, 0, sizeof(request));
  request.kind = ReplayResetKind::NotReplay;
  if (command == nullptr) return request.kind;
  using namespace replay_reset_detail;
  const char* text = skipSpace(command);
  // Match the optional two-character companion CLI response prefix.
  if (text[0] != 0 && text[1] != 0 && text[2] == '|') {
    text = skipSpace(text + 3);
  }
  const size_t verb_length = wordLength(text);
  if (!word(text, verb_length, "replay")) {
    // Reserve dotted replay subcommands as malformed members of this family.
    if (verb_length > 6U && word(text, 6U, "replay") && text[6] == '.') {
      request.kind = ReplayResetKind::Invalid;
    }
    return request.kind;
  }
  request.kind = ReplayResetKind::Invalid;
  text = skipSpace(text + verb_length);
  const size_t action_length = wordLength(text);
  if (!word(text, action_length, "reset")) return request.kind;
  text = skipSpace(text + action_length);
  const size_t key_length = wordLength(text);
  if (word(text, key_length, "all")) {
    text = skipSpace(text + key_length);
    const size_t confirmation_length = wordLength(text);
    if (word(text, confirmation_length, "confirm")
        && *skipSpace(text + confirmation_length) == 0) {
      request.kind = ReplayResetKind::AllConfirm;
    }
    return request.kind;
  }
  if (!hex(text, key_length, request.key, sizeof(request.key))) return request.kind;
  text = skipSpace(text + key_length);
  if (*text == 0) {
    request.kind = ReplayResetKind::ExactKey;
    return request.kind;
  }
  const size_t token_length = wordLength(text);
  if (!hex(text, token_length, request.token, sizeof(request.token))
      || *skipSpace(text + token_length) != 0) return request.kind;
  request.kind = ReplayResetKind::ExactKeyConfirm;
  return request.kind;
}

// One short-lived challenge binds a reset to both the authenticated issuer and
// exact target. It is deliberately RAM-only: a reboot invalidates all captured
// confirmations. Disclose the same token on retries for the first two minutes;
// then retain it for confirmation only until the original five-minute deadline.
// Retries never replace the token or restart either window.
// Call consume() before attempting durable state publication;
// a failed write requires a new challenge rather than making an old one usable.
class ReplayResetNonce {
public:
  static constexpr size_t KEY_SIZE = 32;
  static constexpr size_t TOKEN_SIZE = 16;
  static constexpr uint32_t RESEND_WINDOW_MILLIS = 120000;
  static constexpr uint32_t LIFETIME_MILLIS = 300000;
  static constexpr int64_t CLOCK_TOLERANCE_SECONDS = 5;

  enum class IssueResult : uint8_t {
    Issued, Reused, AwaitingConfirmation, Busy, Invalid
  };

  ReplayResetNonce() { clear(); }

  IssueResult issue(const uint8_t* issuer, const uint8_t* target,
                    const uint8_t* random_token, uint32_t now_millis,
                    uint32_t now_epoch) {
    if (issuer == nullptr || target == nullptr || now_epoch == 0) {
      return IssueResult::Invalid;
    }
    if (isCurrent(now_millis, now_epoch)) {
      if (!sameIdentity(issuer, target)) return IssueResult::Busy;
      return now_millis - issued_millis_ < RESEND_WINDOW_MILLIS
          ? IssueResult::Reused : IssueResult::AwaitingConfirmation;
    }
    clear();
    if (random_token == nullptr) return IssueResult::Invalid;
    uint8_t nonzero = 0;
    for (size_t i = 0; i < TOKEN_SIZE; ++i) nonzero |= random_token[i];
    if (nonzero == 0) return IssueResult::Invalid;
    memcpy(issuer_, issuer, KEY_SIZE);
    memcpy(target_, target, KEY_SIZE);
    memcpy(token_, random_token, TOKEN_SIZE);
    issued_millis_ = now_millis;
    issued_epoch_ = now_epoch;
    active_ = true;
    return IssueResult::Issued;
  }

  bool matches(const uint8_t* issuer, const uint8_t* target,
               const uint8_t* supplied_token, uint32_t now_millis,
               uint32_t now_epoch) const {
    if (issuer == nullptr || target == nullptr || supplied_token == nullptr
        || !isCurrent(now_millis, now_epoch) || !sameIdentity(issuer, target)) {
      return false;
    }
    uint8_t difference = 0;
    for (size_t i = 0; i < TOKEN_SIZE; ++i) {
      difference |= token_[i] ^ supplied_token[i];
    }
    return difference == 0;
  }

  bool consume(const uint8_t* issuer, const uint8_t* target,
               const uint8_t* supplied_token, uint32_t now_millis,
               uint32_t now_epoch) {
    if (!matches(issuer, target, supplied_token, now_millis, now_epoch)) return false;
    clear();
    return true;
  }

  const uint8_t* token() const { return token_; }

  uint32_t remainingSeconds(uint32_t now_millis, uint32_t now_epoch) const {
    if (!isCurrent(now_millis, now_epoch)) return 0;
    return (LIFETIME_MILLIS - (now_millis - issued_millis_)) / 1000U;
  }

  void clear() {
    active_ = false;
    memset(issuer_, 0, sizeof(issuer_));
    memset(target_, 0, sizeof(target_));
    memset(token_, 0, sizeof(token_));
    issued_millis_ = 0;
    issued_epoch_ = 0;
  }

private:
  bool sameIdentity(const uint8_t* issuer, const uint8_t* target) const {
    return memcmp(issuer_, issuer, KEY_SIZE) == 0
        && memcmp(target_, target, KEY_SIZE) == 0;
  }

  bool isCurrent(uint32_t now_millis, uint32_t now_epoch) const {
    if (!active_ || now_epoch == 0) return false;
    const uint32_t elapsed = now_millis - issued_millis_;
    if (elapsed >= LIFETIME_MILLIS) return false;
    const uint64_t expected_epoch = (uint64_t)issued_epoch_ + elapsed / 1000U;
    if (expected_epoch > UINT32_MAX) return false;
    const int64_t clock_difference = (int64_t)now_epoch - (int64_t)expected_epoch;
    return clock_difference >= -CLOCK_TOLERANCE_SECONDS
        && clock_difference <= CLOCK_TOLERANCE_SECONDS;
  }

  uint8_t issuer_[KEY_SIZE];
  uint8_t target_[KEY_SIZE];
  uint8_t token_[TOKEN_SIZE];
  uint32_t issued_millis_;
  uint32_t issued_epoch_;
  bool active_;
};

}  // namespace mesh
