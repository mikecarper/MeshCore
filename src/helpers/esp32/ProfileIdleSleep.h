#pragma once

#include <stdint.h>
#include <esp_timer.h>
#include "PowerTestControls.h"

namespace mesh {
namespace esp32 {

// Preserve both the role's original wake deadline and the scanner's late
// admission. The board checks this absolute deadline again after configuring
// wake sources, so preemption cannot turn a short visit into a longer sleep.
// True means a board sleep was attempted, not proof that the SDK slept.
template <typename Mesh, typename Board>
bool tryProfileIdleSleep(Mesh& mesh, Board& board,
                         uint32_t budget_started_us, uint32_t budget_ms) {
  if (!manualCpuSleepEnabled() || budget_ms == 0 || budget_ms > 0x7fffffffUL / 1000UL) return false;

  const uint32_t observed_us = static_cast<uint32_t>(esp_timer_get_time());
  uint32_t profile_ms = 0;
  const uint32_t minimum_us = profileSleepMinimumMicros();
  uint32_t maximum_profile_ms = profileSleepSliceMillis();
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
  if (mesh.hasAutonomousDualRadioIdle()) maximum_profile_ms = autonomousProfileSleepSliceMillis();
#endif
  if (!mesh.getDualRadioIdleDelay(profile_ms) || profile_ms * 1000UL < minimum_us
      || profile_ms > maximum_profile_ms)
    return false;

  const uint32_t role_deadline = budget_started_us + budget_ms * 1000UL;
  const int32_t role_remaining_us = static_cast<int32_t>(role_deadline - observed_us);
  if (role_remaining_us < static_cast<int32_t>(minimum_us)) return false;

  const uint32_t profile_us = profile_ms * 1000UL;
  const uint32_t remaining_us = static_cast<uint32_t>(role_remaining_us) < profile_us
      ? static_cast<uint32_t>(role_remaining_us) : profile_us;
  board.sleepUntilMicroseconds(observed_us + remaining_us);
  return true;
}

}  // namespace esp32
}  // namespace mesh
