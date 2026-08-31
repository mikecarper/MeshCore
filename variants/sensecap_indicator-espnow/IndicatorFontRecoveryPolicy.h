#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh {
namespace indicator_font {

// This URL is immutable: it names the Git blob itself rather than a branch,
// tag, or "latest" release. The raw media type is selected by the HTTPS
// request in IndicatorFontClient.cpp. A future font update must update the
// URL, published epoch, and all three integrity constants together.
static constexpr char kAssetUrl[] =
    "https://api.github.com/repos/mikecarper/MeshCore/git/blobs/"
    "45dfe8acac20974f53648ef71a31efefa1333fea";
static constexpr size_t kAssetSize = 1302608;
static constexpr uint32_t kAssetCrc32 = 0x19f80d64UL;
static constexpr char kAssetSha256[] =
    "61bce9662db314054e7bcfaa26147a28ad7b500b51baac4cae1caacce90b7421";
// The immutable asset commit was published at this epoch. A clock older than
// the object it is fetching cannot validate the origin's rotating TLS leaf.
static constexpr uint32_t kAssetPublishedEpoch = 1787708237UL;

// SNTP is asynchronous. A font repair must observe a fresh NTP response in the
// current attempt before opening TLS, even if the retained RTC already looks
// plausible. This is a maximum wait; a normal response returns immediately.
static constexpr uint32_t kNtpSyncWaitMillis = 15000UL;

enum class RecoveryNeed : uint8_t {
  None,
  Missing,
  Corrupt,
  VersionMismatch,
};

static constexpr uint8_t kMaximumAttemptsPerBoot = 4;
static constexpr uint8_t kMaximumServiceProbeAttemptsPerBoot = 4;
static constexpr uint8_t kMaximumPostCommitProbeAttemptsPerBoot = 4;

constexpr bool shouldFetch(RecoveryNeed need) {
  return need == RecoveryNeed::Missing || need == RecoveryNeed::Corrupt
      || need == RecoveryNeed::VersionMismatch;
}

// A missing/corrupt asset is using the built-in fallback, so the recovered
// asset can be loaded live.  A valid older/custom font is kept in RAM and the
// replacement becomes active on the next boot, avoiding two 1.3 MiB PSRAM
// allocations at once.
constexpr bool shouldActivateLive(RecoveryNeed need) {
  return need == RecoveryNeed::Missing || need == RecoveryNeed::Corrupt;
}

// Delay after the numbered failed attempt.  The first attempt is immediate
// once station Wi-Fi is connected; a fourth failure stops retries until boot.
constexpr uint32_t retryDelayAfter(uint8_t completed_attempts) {
  return completed_attempts <= 1 ? 30000UL
      : completed_attempts == 2 ? 120000UL
      : 600000UL;
}

// The RP2040 and ESP32 can leave reset at different times. If the startup INFO
// request cannot classify the font service, retry only after station Wi-Fi is
// usable: immediately, then after 2, 5, and 15 seconds. Four unanswered probes
// stop until the next boot instead of creating an unbounded UART worker loop.
constexpr uint32_t serviceProbeRetryDelayAfter(uint8_t completed_attempts) {
  return completed_attempts <= 1 ? 2000UL
      : completed_attempts == 2 ? 5000UL
      : 15000UL;
}

// Once COMMIT has been acknowledged, another HTTPS transfer cannot improve a
// transient INFO/GET failure: the verified asset is already local. Re-probe
// that committed pair on a separate finite budget, then stop until reboot.
// The first post-commit probe is immediate; these are the delays after its
// first, second, and third failures.
constexpr uint32_t postCommitProbeRetryDelayAfter(
    uint8_t completed_attempts) {
  return completed_attempts <= 1 ? 2000UL
      : completed_attempts == 2 ? 5000UL
      : 15000UL;
}

constexpr bool deadlineReached(uint32_t now, uint32_t deadline) {
  return deadline == 0 || static_cast<int32_t>(now - deadline) >= 0;
}

}  // namespace indicator_font
}  // namespace mesh
