#pragma once

#include <stdint.h>

namespace mesh {
namespace wifi {

static constexpr uint32_t kCompanionNtpRefreshMillis =
    24UL * 60UL * 60UL * 1000UL;
static constexpr uint32_t kCompanionNtpFailureRetryMillis =
    5UL * 60UL * 1000UL;
static constexpr uint32_t kCompanionNtpBusyRetryMillis = 5000UL;

// Schedules an immediate boot attempt, a 24-hour refresh after success, and
// bounded retries after transient failures. All intervals are shorter than
// half of the uint32_t range, so signed deadline comparisons remain safe over
// millis() rollover.
class CompanionWiFiNtpPolicy {
public:
  CompanionWiFiNtpPolicy() : _scheduled(false), _deadline(0) {}

  bool attemptDue(uint32_t now) const {
    return !_scheduled || static_cast<int32_t>(now - _deadline) >= 0;
  }

  void requestNow() {
    _scheduled = false;
    _deadline = 0;
  }

  void noteBusy(uint32_t now) {
    scheduleAfter(now, kCompanionNtpBusyRetryMillis);
  }

  void noteFailure(uint32_t now) {
    scheduleAfter(now, kCompanionNtpFailureRetryMillis);
  }

  void noteSuccess(uint32_t now) {
    scheduleAfter(now, kCompanionNtpRefreshMillis);
  }

  uint32_t deadline() const { return _deadline; }
  bool scheduled() const { return _scheduled; }

private:
  bool _scheduled;
  uint32_t _deadline;

  void scheduleAfter(uint32_t now, uint32_t interval) {
    _deadline = now + interval;
    _scheduled = true;
  }
};

}  // namespace wifi
}  // namespace mesh
