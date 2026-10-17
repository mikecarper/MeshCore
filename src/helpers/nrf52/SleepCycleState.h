#pragma once

#include <atomic>
#include <stdint.h>

namespace mesh_nrf52 {

struct SleepCycleSnapshot {
  uint32_t generation = 0;
  uint32_t usb_status = 0;
  bool softdevice_enabled = false;
};

// The application task publishes once at loop entry, before checking work.
// A SoftDevice startup attempt can invalidate from another task without
// racing the publication. No SDK state is inferred from NVIC/vector state.
class SleepCycleState {
  std::atomic<uint32_t> generation_{0};
  std::atomic<uint32_t> prepared_generation_{0};
  std::atomic<uint32_t> usb_status_{0};
  std::atomic<uint32_t> error_{0};
  std::atomic<bool> softdevice_enabled_{false};
  std::atomic<bool> prepared_{false};

public:
  uint32_t begin() {
    const uint32_t token = generation_.fetch_add(1, std::memory_order_acq_rel) + 1;
    prepared_.store(false, std::memory_order_release);
    return token;
  }

  bool publish(uint32_t token, bool softdevice_enabled, uint32_t usb_status,
               uint32_t error = 0) {
    if (!current(token)) return false;
    usb_status_.store(usb_status, std::memory_order_relaxed);
    softdevice_enabled_.store(softdevice_enabled, std::memory_order_relaxed);
    error_.store(error, std::memory_order_relaxed);
    prepared_generation_.store(token, std::memory_order_relaxed);
    prepared_.store(error == 0, std::memory_order_release);
    return current(token);
  }

  void invalidate() {
    generation_.fetch_add(1, std::memory_order_acq_rel);
  }

  bool current(uint32_t token) const {
    return generation_.load(std::memory_order_acquire) == token;
  }

  bool snapshot(SleepCycleSnapshot& out) const {
    const uint32_t token = generation_.load(std::memory_order_acquire);
    if (!prepared_.load(std::memory_order_acquire)) return false;
    SleepCycleSnapshot candidate;
    candidate.generation = prepared_generation_.load(std::memory_order_relaxed);
    candidate.usb_status = usb_status_.load(std::memory_order_relaxed);
    candidate.softdevice_enabled = softdevice_enabled_.load(std::memory_order_relaxed);
    if (candidate.generation != token || !current(token)
        || !prepared_.load(std::memory_order_acquire)) return false;
    out = candidate;
    return true;
  }

  bool take(SleepCycleSnapshot& out) {
    if (!snapshot(out)) return false;
    prepared_.store(false, std::memory_order_release);
    return current(out.generation);
  }

  uint32_t lastError() const {
    const uint32_t token = generation_.load(std::memory_order_acquire);
    const uint32_t prepared_token = prepared_generation_.load(std::memory_order_relaxed);
    const uint32_t error = error_.load(std::memory_order_relaxed);
    return prepared_token == token && current(token) ? error : 0;
  }
};

static_assert(sizeof(SleepCycleState) <= 24, "Sleep-cycle cache exceeded its RAM budget");

inline SleepCycleState& sleepCycleState() {
  static SleepCycleState value;
  return value;
}

} // namespace mesh_nrf52
