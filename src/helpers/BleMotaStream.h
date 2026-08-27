#pragma once

#include <Arduino.h>
#include <atomic>
#include <stddef.h>
#include <stdint.h>

namespace mesh {
namespace ota {

// A single-producer/single-consumer Stream adapter for the nRF52 BLE mOTA
// GATT service. The BLE callback appends host response fragments while the
// mesh loop synchronously consumes them through SerialMotaSource. Device
// requests travel in the other direction through the bounded send callback.
//
// One seeder response is at most 197 bytes. Keeping 256 bytes here holds a
// complete response while still failing closed on duplicate or injected data.
class BleMotaStream : public Stream {
public:
  static constexpr uint16_t RX_CAPACITY = 256;
  using SendCallback = size_t (*)(void* context, const uint8_t* data,
                                  size_t length);

  BleMotaStream() = default;

  void setSender(SendCallback callback, void* context) {
    _send_context = context;
    _send = callback;
  }

  void setActive(bool active) {
    _active.store(false, std::memory_order_release);
    if (active) {
      clear();
    } else {
      invalidateWriters();
    }
    _overflowed.store(false, std::memory_order_release);
    _active.store(active, std::memory_order_release);
  }

  bool isActive() const {
    return _active.load(std::memory_order_acquire);
  }

  bool overflowed() const {
    return _overflowed.load(std::memory_order_acquire);
  }

  // Called only by the BLE response-characteristic callback. A fragment is
  // accepted atomically or rejected in full; a partial frame is never queued.
  bool pushRx(const uint8_t* data, size_t length) {
    if (!isActive() || data == nullptr || length == 0
        || length >= RX_CAPACITY) {
      return false;
    }

    const uint32_t head_state = _head_state.load(std::memory_order_acquire);
    const uint16_t head = static_cast<uint16_t>(head_state);
    const uint16_t tail = _tail.load(std::memory_order_acquire);
    const uint16_t used = head >= tail ? head - tail
                                       : RX_CAPACITY - (tail - head);
    const uint16_t free_bytes = RX_CAPACITY - used - 1;
    if (length > free_bytes) {
      _overflowed.store(true, std::memory_order_release);
      return false;
    }

    uint16_t cursor = head;
    for (size_t i = 0; i < length; ++i) {
      _rx[cursor] = data[i];
      cursor = static_cast<uint16_t>((cursor + 1) % RX_CAPACITY);
    }
    const uint32_t next_state = (head_state & 0xFFFF0000UL) | cursor;
    uint32_t expected = head_state;
    // setActive() changes the generation, and a new session also resets the
    // head. The CAS ensures a callback which overlapped either transition
    // cannot publish stale bytes into the next source session, even when both
    // sessions happened to use the same ring index.
    return isActive()
        && _head_state.compare_exchange_strong(
            expected, next_state, std::memory_order_release,
            std::memory_order_relaxed);
  }

  int available() override {
    if (!isActive()) return 0;
    const uint16_t head = static_cast<uint16_t>(
        _head_state.load(std::memory_order_acquire));
    const uint16_t tail = _tail.load(std::memory_order_relaxed);
    return head >= tail ? head - tail : RX_CAPACITY - (tail - head);
  }

  int read() override {
    if (!isActive()) return -1;
    const uint16_t tail = _tail.load(std::memory_order_relaxed);
    const uint16_t head = static_cast<uint16_t>(
        _head_state.load(std::memory_order_acquire));
    if (tail == head) return -1;
    const uint8_t value = _rx[tail];
    _tail.store(static_cast<uint16_t>((tail + 1) % RX_CAPACITY),
                std::memory_order_release);
    return value;
  }

  int peek() override {
    if (!isActive()) return -1;
    const uint16_t tail = _tail.load(std::memory_order_relaxed);
    const uint16_t head = static_cast<uint16_t>(
        _head_state.load(std::memory_order_acquire));
    return tail == head ? -1 : _rx[tail];
  }

  void flush() override {}

  size_t write(uint8_t value) override {
    return write(&value, 1);
  }

  size_t write(const uint8_t* data, size_t length) override {
    if (!isActive() || data == nullptr || length == 0 || _send == nullptr) {
      return 0;
    }
    return _send(_send_context, data, length);
  }

  using Print::write;

private:
  static uint16_t nextGeneration(uint32_t state) {
    return static_cast<uint16_t>(state >> 16) + 1;
  }

  void invalidateWriters() {
    const uint32_t previous = _head_state.load(std::memory_order_relaxed);
    const uint32_t next =
        (static_cast<uint32_t>(nextGeneration(previous)) << 16)
        | static_cast<uint16_t>(previous);
    _head_state.store(next, std::memory_order_release);
  }

  void clear() {
    const uint32_t previous = _head_state.load(std::memory_order_relaxed);
    _head_state.store(
        static_cast<uint32_t>(nextGeneration(previous)) << 16,
        std::memory_order_release);
    _tail.store(0, std::memory_order_release);
  }

  uint8_t _rx[RX_CAPACITY] = {};
  // High 16 bits are a source-session generation; low 16 bits are the ring
  // head. Packing both into one lock-free nRF52840 atomic prevents an ABA
  // commit across clear()/restart.
  std::atomic<uint32_t> _head_state{0};
  std::atomic<uint16_t> _tail{0};
  std::atomic<bool> _active{false};
  std::atomic<bool> _overflowed{false};
  SendCallback _send = nullptr;
  void* _send_context = nullptr;
};

}  // namespace ota
}  // namespace mesh
