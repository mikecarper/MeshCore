#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh { namespace tracker {

// Wire protocol v1 (25 bytes): version 01, 4-byte beacon ID, uint32 time slot
// in big endian, then 16 bytes of HMAC-SHA256(key, domain || version || ID ||
// slot). IDs are provisioned opaque bytes; each beacon has its own key.
// The 30-second slot is floor(Unix seconds / 30). The domain is exactly the
// ASCII bytes "MeshCore/HomeBeacon/v1" with no trailing NUL.
//
// A trusted clock is essential on BOTH gateway and tracker; the gateway may
// obtain time via Wi-Fi/NTP, and a tracker without a trusted RTC reports
// Unknown. No secret is broadcast. Captured packets may be replayed only
// until their fixed expiry; real-time relay and stolen-key attacks remain
// possible. A compromised or incorrectly set clock defeats freshness.
//
// This class keeps a clock high-water mark in RAM and locks on rollback.
// Resets lose that mark: callers must establish trusted time again after
// reboot, never use an unset/persisted-unverified clock as proof of time, and
// persist/validate trusted time externally if rollback across reboot matters.
// The verifier and supplied key/hmac callback are main-loop-owned.
class HomeBeacon {
 public:
  static constexpr size_t kKeyMinBytes = 16;
  static constexpr size_t kKeyMaxBytes = 32;
  static constexpr size_t kSize = 25;
  static constexpr uint8_t kVersion = 1;
  static constexpr uint32_t kSlotSeconds = 30;
  static constexpr uint64_t kEarliestUnix = 1577836800ULL; // 2020-01-01 UTC
  using Hmac = bool (*)(const uint8_t* key, size_t key_len,
                        const uint8_t* data, size_t data_len,
                        uint8_t out[32]);

  // Key storage must outlive this object. Accept only random 128/256-bit
  // provisioned keys, not passwords. Key quality is the caller's obligation.
  HomeBeacon(const uint8_t id[4], const uint8_t* key, size_t key_len, Hmac hmac)
      : id_(id), key_(key), key_len_(key_len), hmac_(hmac) {}

  // Return false unless caller confirms a trusted clock and usable key.
  bool encode(uint64_t unix_seconds, bool trusted_clock, uint8_t out[kSize]) const {
    uint32_t slot;
    if (!out || !usableKey() || !validTime(unix_seconds, trusted_clock, slot)) return false;
    out[0] = kVersion;
    for (size_t i = 0; i < 4; ++i) out[1 + i] = id_[i];
    putSlot(out + 5, slot);
    uint8_t tag[32];
    if (!mac(out, tag)) return false;
    for (size_t i = 0; i < 16; ++i) out[9 + i] = tag[i];
    return true;
  }

  // Returns true only while the packet is fresh and authenticated. On success
  // expiry_unix is an EXCLUSIVE bound fixed by the packet's slot; never reset
  // it based on reception time. False means Unknown, never "away" or lost.
  // Revalidate trusted time and the expiry before each use of cached presence.
  bool verify(const uint8_t* packet, size_t len, uint64_t unix_seconds,
              bool trusted_clock, uint64_t& expiry_unix) {
    expiry_unix = 0;
    uint32_t now_slot;
    if (!validTime(unix_seconds, trusted_clock, now_slot) || rollback_) return false;
    if (have_time_ && unix_seconds < last_unix_) {
      rollback_ = true;
      return false;
    }
    have_time_ = true;
    last_unix_ = unix_seconds;
    if (!usableKey() || !packet || len != kSize || packet[0] != kVersion) return false;
    for (size_t i = 0; i < 4; ++i) if (packet[1 + i] != id_[i]) return false;
    const uint32_t slot = getSlot(packet + 5);
    if (slot > now_slot || now_slot - slot > 1) return false;
    uint8_t tag[32];
    if (!mac(packet, tag)) return false;
    uint8_t diff = 0;
    for (size_t i = 0; i < 16; ++i) diff |= tag[i] ^ packet[9 + i];
    if (diff) return false;
    expiry_unix = (uint64_t(slot) + 2) * kSlotSeconds;
    return true;
  }

  // Only call after explicitly re-establishing trustworthy wall-clock time.
  void resetClockTrust() { have_time_ = false; rollback_ = false; last_unix_ = 0; }
  bool clockRollback() const { return rollback_; }

 private:
  bool usableKey() const {
    if (!id_ || !key_ || (key_len_ != 16 && key_len_ != 32) || !hmac_) return false;
    uint8_t any = 0;
    for (size_t i = 0; i < key_len_; ++i) any |= key_[i];
    return any != 0;
  }
  static bool validTime(uint64_t t, bool trusted, uint32_t& slot) {
    if (!trusted || t < kEarliestUnix || t / kSlotSeconds > UINT32_MAX) return false;
    slot = uint32_t(t / kSlotSeconds);
    return true;
  }
  static void putSlot(uint8_t* out, uint32_t slot) {
    for (int i = 3; i >= 0; --i) { out[i] = uint8_t(slot); slot >>= 8; }
  }
  static uint32_t getSlot(const uint8_t* in) {
    return (uint32_t(in[0]) << 24) | (uint32_t(in[1]) << 16) |
           (uint32_t(in[2]) << 8) | uint32_t(in[3]);
  }
  bool mac(const uint8_t* header, uint8_t out[32]) const {
    static const uint8_t domain[] = "MeshCore/HomeBeacon/v1";
    uint8_t input[sizeof(domain) - 1 + 9];
    for (size_t i = 0; i < sizeof(domain) - 1; ++i) input[i] = domain[i];
    for (size_t i = 0; i < 9; ++i) input[sizeof(domain) - 1 + i] = header[i];
    return hmac_(key_, key_len_, input, sizeof(input), out);
  }
  const uint8_t* id_;
  const uint8_t* key_;
  const size_t key_len_;
  const Hmac hmac_;
  bool have_time_ = false;
  bool rollback_ = false;
  uint64_t last_unix_ = 0;
};

}} // namespace mesh::tracker
