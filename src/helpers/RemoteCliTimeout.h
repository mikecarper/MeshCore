#pragma once

#include <stdint.h>

namespace mesh {

static constexpr uint32_t REMOTE_CLI_TIMEOUT_TOTAL_PERCENT = 300UL;
static constexpr uint32_t REMOTE_CLI_MAX_TIMEOUT_MILLIS = 0x7FFFFFFFUL;

// Remote CLI_DATA waits for an application-level reply rather than a packet
// ACK. Use three times the normal route estimate and keep the result within
// the signed delta range accepted by futureMillis().
inline bool calculateRemoteCliTimeoutMillis(uint32_t base_timeout_millis,
                                            uint32_t& timeout_millis) {
  timeout_millis = 0;
  if (base_timeout_millis == 0) return false;

  const uint64_t total =
      static_cast<uint64_t>(base_timeout_millis)
      * REMOTE_CLI_TIMEOUT_TOTAL_PERCENT / 100UL;
  if (total > REMOTE_CLI_MAX_TIMEOUT_MILLIS) return false;

  timeout_millis = static_cast<uint32_t>(total);
  return true;
}

}  // namespace mesh
