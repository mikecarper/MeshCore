#pragma once

#include <stdint.h>
#include <time.h>

namespace mesh {
namespace tls_clock {

// A certificate-validating TLS handshake must not treat an uninitialised or
// wrapped wall clock as usable. The fresh-proof bit is deliberately supplied by
// caller: a plausible retained RTC alone is not evidence that NTP answered in
// the operation which is about to download trusted content.
static constexpr time_t kMinimumValidEpoch = (time_t)1767225600;  // 2026-01-01 UTC

constexpr bool timeIsValid(time_t now) {
  return now >= kMinimumValidEpoch;
}

constexpr bool proofIsValid(bool fresh_proof, bool wifi_connected,
                            time_t now) {
  return fresh_proof && wifi_connected && timeIsValid(now);
}

// `now - proven_at` is the standard modulo-uint32_t elapsed-time calculation,
// so a proof remains valid across the one millis() rollover that a bounded
// operation can cross. Callers must reset the fresh-proof/generation state
// before every operation and must not retain a proof for a complete 2^32-ms
// cycle, where a 32-bit elapsed clock necessarily becomes ambiguous.
constexpr bool proofAgeIsValid(bool fresh_proof, uint32_t now,
                               uint32_t proven_at,
                               uint32_t maximum_age_millis) {
  return fresh_proof && now - proven_at <= maximum_age_millis;
}

// A callback can already be in flight when its owner unregisters it. Binding
// the proof to the lease generation makes such a late callback fail closed if
// a later operation of the same feature has already started.
constexpr bool proofGenerationIsValid(bool fresh_proof,
                                      uint32_t proof_generation,
                                      uint32_t expected_generation) {
  return fresh_proof && expected_generation != 0
      && proof_generation == expected_generation;
}

}  // namespace tls_clock
}  // namespace mesh
