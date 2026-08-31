#pragma once

#include <atomic>
#include <stdint.h>

namespace mesh {
namespace sntp_coord {

using CleanupHook = void (*)();

// ESP-IDF exposes one process-wide SNTP client and one notification callback.
// Keep callback installation/configTime() sequences mutually exclusive across
// otherwise independent firmware features. Acquisition is deliberately
// non-blocking: a caller can retry through its own bounded policy instead of
// deadlocking behind a failed network operation.
class OperationCoordinator {
public:
  OperationCoordinator() : owner_(0), next_generation_(1) {}

  bool owns(uint32_t generation) const {
    return generation != 0
        && owner_.load(std::memory_order_acquire) == generation;
  }

private:
  friend class OperationLease;

  bool tryAcquire(uint32_t& generation) {
    if (generation != 0) return false;

    uint32_t candidate = 0;
    do {
      candidate = next_generation_.fetch_add(1, std::memory_order_relaxed);
    } while (candidate == 0);

    uint32_t expected = 0;
    if (!owner_.compare_exchange_strong(
            expected, candidate, std::memory_order_acq_rel,
            std::memory_order_acquire)) {
      return false;
    }
    generation = candidate;
    return true;
  }

  bool release(uint32_t generation, CleanupHook cleanup) {
    // Run the callback cleanup before publishing the coordinator as free. This
    // prevents a new owner from installing its callback and then having a stale
    // owner's teardown clear it. Only OperationLease can release a generation.
    if (!owns(generation)) return false;
    if (cleanup != nullptr) cleanup();
    uint32_t expected = generation;
    return owner_.compare_exchange_strong(
        expected, 0, std::memory_order_acq_rel, std::memory_order_acquire);
  }

  std::atomic<uint32_t> owner_;
  std::atomic<uint32_t> next_generation_;
};

class OperationLease {
public:
  explicit OperationLease(OperationCoordinator& coordinator,
                          CleanupHook cleanup = nullptr)
      : coordinator_(coordinator), cleanup_(cleanup), generation_(0) {}

  ~OperationLease() { release(); }

  bool tryAcquire() { return coordinator_.tryAcquire(generation_); }

  bool owns() const { return coordinator_.owns(generation_); }

  uint32_t generation() const { return generation_; }

  bool release() {
    if (generation_ == 0) return false;
    const uint32_t releasing = generation_;
    // Make repeated release attempts inert even if cleanup code indirectly
    // observes this lease.
    generation_ = 0;
    return coordinator_.release(releasing, cleanup_);
  }

  OperationLease(const OperationLease&) = delete;
  OperationLease& operator=(const OperationLease&) = delete;

private:
  OperationCoordinator& coordinator_;
  CleanupHook cleanup_;
  uint32_t generation_;
};

// A function-local static in an external-linkage inline function denotes one
// object program-wide under the C++ ODR, including the Arduino-ESP32 2.x C++11
// toolchain. Both the font repair and pull-OTA paths use this exact instance.
inline OperationCoordinator& processWideCoordinator() {
  static OperationCoordinator coordinator;
  return coordinator;
}

}  // namespace sntp_coord
}  // namespace mesh
