#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh_nrf52 {

// Guard serializes task access on the single-core nRF52. Keep every guarded
// operation bounded to metadata or one frame copy; UART FIFO operations must
// run outside the guard because Bluefruit takes a FreeRTOS mutex there.
template <typename Frame, size_t Capacity, typename Guard>
class BleRxFrameQueue {
  static_assert(Capacity > 0 && Capacity <= 255, "invalid BLE RX capacity");
  static constexpr uint16_t INVALID_HANDLE = 0xffff;

  Frame _frames[Capacity];
  uint32_t _generation = 0;
  uint16_t _handle = INVALID_HANDLE;
  uint16_t _resets_in_progress = 0;
  uint8_t _head = 0;
  uint8_t _count = 0;
  bool _enabled = false;
  bool _secured = false;

  void invalidate() {
    ++_generation;
    _head = _count = 0;
  }

  bool accepts(uint16_t handle, uint32_t generation) const {
    return _enabled && _secured && !_resets_in_progress
        && handle == _handle && generation == _generation;
  }

public:
  enum class PushResult { Accepted, Full, Stale };

  void setEnabled(bool enabled) {
    Guard guard;
    _enabled = enabled;
    _handle = INVALID_HANDLE;
    _secured = false;
    invalidate();
  }

  void connect(uint16_t handle) {
    Guard guard;
    _handle = _enabled ? handle : INVALID_HANDLE;
    _secured = false;
    invalidate();
  }

  void secure(uint16_t handle) {
    Guard guard;
    if (_enabled && _handle == handle && handle != INVALID_HANDLE) {
      _secured = true;
    }
  }

  // Physical CONNECT is observed by the BLE worker before deferred lifecycle
  // callbacks. A disable/re-enable invalidates that observation even if the
  // SoftDevice still has the same numeric handle.
#if defined(RAK_4631) && defined(__GNUC__)
  // Share the guarded query across callbacks in the constrained RAK image.
  __attribute__((noinline))
#endif
  bool hasConnection(uint16_t handle) const {
    Guard guard;
    return _enabled && handle != INVALID_HANDLE && _handle == handle;
  }

  void disconnect(uint16_t handle) {
    Guard guard;
    if (_handle != handle) return;
    _handle = INVALID_HANDLE;
    _secured = false;
    invalidate();
  }

  template <typename Flush>
  void reset(Flush flush) {
    {
      Guard guard;
      ++_resets_in_progress;
      invalidate();
    }
    flush();
    {
      Guard guard;
      --_resets_in_progress;
    }
  }

  bool snapshot(uint16_t handle, uint32_t& generation) const {
    Guard guard;
    generation = _generation;
    return accepts(handle, generation);
  }

  bool current(uint16_t handle, uint32_t generation) const {
    Guard guard;
    return accepts(handle, generation);
  }

  PushResult push(uint16_t handle, uint32_t generation, const Frame& frame) {
    Guard guard;
    if (!accepts(handle, generation)) return PushResult::Stale;
    if (_count == Capacity) return PushResult::Full;
    _frames[(_head + _count) % Capacity] = frame;
    ++_count;
    return PushResult::Accepted;
  }

  size_t pop(uint8_t* destination) {
    Guard guard;
    if (!_count) return 0;
    const Frame& frame = _frames[_head];
    const size_t length = frame.len;
    memcpy(destination, frame.buf, length);
    _head = (_head + 1) % Capacity;
    --_count;
    return length;
  }

  uint8_t size() const {
    Guard guard;
    return _count;
  }

  uint32_t generation() const {
    Guard guard;
    return _generation;
  }
};

}  // namespace mesh_nrf52
