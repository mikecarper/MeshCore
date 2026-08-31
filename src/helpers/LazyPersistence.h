#pragma once

#include <stdint.h>

namespace mesh {

static const uint32_t LAZY_PERSISTENCE_MAX_RETRY_DELAY_MILLIS = 300000UL;

inline unsigned long nonzeroLazyPersistenceDeadline(unsigned long deadline) {
  // Zero is the callers' "not pending" sentinel. Preserve a pending write at
  // the single millis() value where deadline arithmetic wraps exactly to zero.
  return deadline == 0 ? 1UL : deadline;
}

inline bool armFirstLazyPersistence(unsigned long& pending_deadline,
                                    unsigned long new_deadline,
                                    bool persistence_needed) {
  if (!persistence_needed || pending_deadline != 0) return false;
  pending_deadline = nonzeroLazyPersistenceDeadline(new_deadline);
  return true;
}

inline bool scheduleLazyPersistenceMutation(
    unsigned long& pending_deadline,
    uint8_t& consecutive_failures,
    unsigned long new_deadline) {
  // Once persistence has failed, ordinary mutations must not turn a permanent
  // filesystem fault back into a five-second wake/write loop. Only a
  // successful save resets the failure count and its bounded retry deadline.
  if (consecutive_failures != 0) {
    if (pending_deadline != 0) return false;
    pending_deadline = nonzeroLazyPersistenceDeadline(new_deadline);
    return true;
  }
  if (pending_deadline != 0) return false;
  pending_deadline = nonzeroLazyPersistenceDeadline(new_deadline);
  return true;
}

inline uint32_t recordLazyPersistenceSaveFailure(
    uint8_t& consecutive_failures,
    uint32_t base_delay,
    uint32_t maximum_delay) {
  uint32_t delay = base_delay > maximum_delay ? maximum_delay : base_delay;
  uint8_t remaining_doublings = consecutive_failures;
  while (remaining_doublings-- != 0 && delay < maximum_delay) {
    delay = delay > maximum_delay / 2U ? maximum_delay : delay * 2U;
  }
  if (consecutive_failures != UINT8_MAX) consecutive_failures++;
  return delay;
}

inline void resetLazyPersistenceAfterSuccess(
    unsigned long& pending_deadline,
    uint8_t& consecutive_failures) {
  pending_deadline = 0;
  consecutive_failures = 0;
}

inline void completeLazyPersistenceSave(unsigned long& pending_deadline,
                                        bool save_succeeded,
                                        unsigned long retry_deadline) {
  pending_deadline = save_succeeded
      ? 0UL
      : nonzeroLazyPersistenceDeadline(retry_deadline);
}

} // namespace mesh
