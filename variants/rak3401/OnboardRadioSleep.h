#pragma once

#include <string.h>

// Adapted from agessaman/MeshCore feat/rak13302-auto-onboard-sleep
// (d58042d8d25fbc9177f1b393ba0e393e3e5b276f).
namespace rak3401 {

enum class OnboardRadioState { Absent, Asleep, BusyTimeout, SleepFailed };

// Startup only, before radio, storage or other SPI users have begun. The
// RAK3401 image also supports a RAK4631 core using the external RAK13302.
OnboardRadioState sleepOnboardRadio();

// IO owns the temporary SPI setup and must release it on every outcome.
template <typename IO>
OnboardRadioState sleepOnboardRadio(IO& io, const char* expectedVersion) {
  io.begin();
  io.reset();
  const auto state = [&]() {
    // A missing chip also times out: the probe pull keeps floating BUSY high.
    if (!io.waitBusy(false, 20)) return OnboardRadioState::BusyTimeout;
    char version[6];
    io.readVersion(version);
    if (memcmp(version, expectedVersion, sizeof(version)) != 0) {
      return OnboardRadioState::Absent;
    }
    io.releaseProbePulls();
    if (!io.waitBusy(false, 20)) return OnboardRadioState::BusyTimeout;
    io.sleep();
    // Never read SPI to confirm sleep: an NSS falling edge wakes the chip.
    return io.waitBusy(true, 20) ? OnboardRadioState::Asleep : OnboardRadioState::SleepFailed;
  }();
  io.end();
  return state;
}

}  // namespace rak3401
