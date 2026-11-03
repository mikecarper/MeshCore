#pragma once

#include <stdint.h>
#include <helpers/tracker/HomeBeacon.h>

namespace mesh { namespace home_beacon {

// Transport-independent gateway policy. The platform must stop advertising
// before updating a payload and call stopped()/started() only after the BLE
// operation succeeds. It must invoke evaluate frequently enough to enforce
// Wi-Fi disconnect and clock-sync expiry; decisions do not operate hardware.
class Controller {
 public:
  static constexpr uint32_t kSyncMaxAgeMs = 2UL * 60 * 60 * 1000;
  enum class Action { None, Stop, Update };
  struct Decision { Action action; uint32_t slot; };

  // Call only after successful synchronization from the configured time
  // source, not merely because the system clock contains a plausible date.
  // Clear trust when configuration changes. NTP itself is not authenticated.
  bool synchronized(uint32_t monotonic_ms, uint64_t utc_seconds) {
    if (!validUtc(utc_seconds)) { invalidateClock(); return false; }
    if (rollback_ || (have_time_ && utc_seconds < last_utc_)) {
      rollback_ = true; trusted_ = false; return false;
    }
    sync_ms_ = monotonic_ms;
    last_utc_ = utc_seconds;
    have_time_ = true;
    trusted_ = true;
    return true;
  }
  void invalidateClock() { trusted_ = false; }
  // Only after an authenticated external time recovery, never on Wi-Fi
  // reconnect or ordinary NTP callback. High-water mark is volatile RAM.
  void resetClockTrust() { trusted_ = false; have_time_ = false; rollback_ = false; last_utc_ = 0; }
  bool clockRollback() const { return rollback_; }

  Decision evaluate(uint32_t monotonic_ms, uint64_t utc_seconds,
                    bool connected_to_configured_wifi) {
    if (!connected_to_configured_wifi) trusted_ = false;
    if (!connected_to_configured_wifi || !trusted_ ||
        uint32_t(monotonic_ms - sync_ms_) >= kSyncMaxAgeMs ||
        !validUtc(utc_seconds) || utc_seconds < last_utc_) {
      if (trusted_ && validUtc(utc_seconds) && utc_seconds < last_utc_) {
        trusted_ = false; rollback_ = true;
      }
      return {advertising_ ? Action::Stop : Action::None, 0};
    }
    last_utc_ = utc_seconds;
    const uint32_t slot = uint32_t(utc_seconds / tracker::HomeBeacon::kSlotSeconds);
    return {!advertising_ || slot != slot_ ? Action::Update : Action::None, slot};
  }

  void started(uint32_t slot) { advertising_ = true; slot_ = slot; }
  void stopped() { advertising_ = false; }

 private:
  static bool validUtc(uint64_t utc) {
    return utc >= tracker::HomeBeacon::kEarliestUnix &&
           utc / tracker::HomeBeacon::kSlotSeconds <= UINT32_MAX;
  }
  bool trusted_ = false;
  bool have_time_ = false;
  bool rollback_ = false;
  bool advertising_ = false;
  uint32_t sync_ms_ = 0;
  uint32_t slot_ = 0;
  uint64_t last_utc_ = 0;
};

}} // namespace mesh::home_beacon
