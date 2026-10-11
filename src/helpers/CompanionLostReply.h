#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace companion {

// This is an owner-selected answer, never an assessment of a GPS position.
// Values are also the append-only Companion preference representation.
enum LostReply : uint8_t { LostReplyOff = 0, LostReplyNo = 1, LostReplyYes = 2 };

inline bool lostReplyWhitespace(char c) {
  return c == ' ' || uint8_t(c - '\t') <= uint8_t('\r' - '\t');
}

inline bool isLostReplyQuestion(const char* text) {
  if (text == NULL) return false;
  while (lostReplyWhitespace(*text)) ++text;
  static const char question[] = "am i lost";
  for (size_t i = 0; i < sizeof(question) - 1; ++i) {
    char c = *text++;
    if (c >= 'A' && c <= 'Z') c = char(c + ('a' - 'A'));
    if (c != question[i]) return false;
  }
  if (*text == '?') ++text;
  while (lostReplyWhitespace(*text)) ++text;
  return *text == 0;
}

inline const char* lostReplyName(uint8_t value) {
  return value == LostReplyNo ? "no" : value == LostReplyYes ? "yes" : "off";
}

// Only directly attached clients call this handler. No radio CLI route may
// change the locally saved answer, even with a remote CLI permission flag.
template<typename Prefs, typename Save>
bool handleLostReplyCommand(Prefs& prefs, const char* command, char* reply,
                            size_t capacity, Save save) {
  if (strcmp(command, "get lost.reply") == 0) {
    snprintf(reply, capacity, "> %s", lostReplyName(prefs.lost_reply));
    return true;
  }
  const char prefix[] = "set lost.reply ";
  if (strncmp(command, prefix, sizeof(prefix) - 1) != 0) return false;
  const char* value = command + sizeof(prefix) - 1;
  uint8_t selected;
  if (strcmp(value, "off") == 0) selected = LostReplyOff;
  else if (strcmp(value, "no") == 0) selected = LostReplyNo;
  else if (strcmp(value, "yes") == 0) selected = LostReplyYes;
  else {
    snprintf(reply, capacity, "Error: use off, no or yes");
    return true;
  }
  const uint8_t previous = prefs.lost_reply;
  prefs.lost_reply = selected;
  if (!save()) {
    prefs.lost_reply = previous;
    snprintf(reply, capacity, "Error: could not save lost reply");
  } else {
    snprintf(reply, capacity, "> lost.reply %s", value);
  }
  return true;
}

// Four retained full peer identities bound both memory and response traffic.
// A peer slot cannot be evicted during its minute-long cooldown. Exact radio
// retries remain suppressed while that peer's last answered request is kept;
// this runtime cache is intentionally cleared by a reboot.
class LostReplyLimiter {
public:
  static constexpr size_t PeerCount = 4;
  static constexpr size_t KeySize = 32;
  static constexpr uint32_t PeerIntervalMillis = 60000;
  static constexpr uint32_t GlobalIntervalMillis = 10000;

  LostReplyLimiter() : last_reply_(0), replied_(false) {
    memset(peers_, 0, sizeof(peers_));
  }

  bool canReply(const uint8_t* key, uint32_t timestamp, uint32_t now) const {
    return replySlot(key, timestamp, now) >= 0;
  }

  void rememberReply(const uint8_t* key, uint32_t timestamp, uint32_t now) {
    const int chosen = replySlot(key, timestamp, now);
    if (chosen < 0) return;
    Peer& peer = peers_[chosen];
    memcpy(peer.key, key, KeySize);
    peer.timestamp = timestamp;
    peer.replied_at = now;
    peer.valid = true;
    last_reply_ = now;
    replied_ = true;
  }

private:
  struct Peer {
    uint8_t key[KeySize];
    uint32_t timestamp;
    uint32_t replied_at;
    bool valid;
  };
  Peer peers_[PeerCount];
  uint32_t last_reply_;
  bool replied_;

  // Share the full identity/cooldown scan between admission and commit.
  // Inlining both copies costs flash on constrained Companion targets.
#if defined(__GNUC__)
  __attribute__((noinline))
#endif
  int replySlot(const uint8_t* key, uint32_t timestamp, uint32_t now) const {
    if (key == NULL || (replied_ && uint32_t(now - last_reply_) < GlobalIntervalMillis))
      return -1;
    int available = -1;
    for (size_t i = 0; i < PeerCount; ++i) {
      const Peer& peer = peers_[i];
      if (peer.valid && memcmp(peer.key, key, KeySize) == 0) {
        return peer.timestamp != timestamp
            && uint32_t(now - peer.replied_at) >= PeerIntervalMillis ? int(i) : -1;
      }
      if (available < 0 && (!peer.valid
          || uint32_t(now - peer.replied_at) >= PeerIntervalMillis)) available = int(i);
    }
    return available;
  }
};

static_assert(sizeof(LostReplyLimiter) <= 192, "lost reply state stays bounded");

} // namespace companion
} // namespace mesh
