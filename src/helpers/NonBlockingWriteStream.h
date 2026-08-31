#pragma once

#include <Arduino.h>
#include <atomic>
#include <stddef.h>
#include <stdint.h>

namespace mesh {

// A Stream facade for a transport whose underlying write() may wait for FIFO
// space. It never retries or forwards a write larger than one nRF52 TinyUSB TX
// FIFO. Print::printf() can report a length larger than its 256-byte scratch
// buffer; rejecting such a call here also prevents that stale length from
// making the delegate read beyond the scratch buffer.
template <size_t MAX_WRITE_SIZE = 256>
class WholeRecordNonBlockingStream : public Stream {
 public:
  explicit WholeRecordNonBlockingStream(Stream& delegate)
      : _delegate(delegate) {}

  int available() override { return _delegate.available(); }
  int read() override { return _delegate.read(); }
  int peek() override { return _delegate.peek(); }
  void flush() override { _delegate.flush(); }
  int availableForWrite() override { return _delegate.availableForWrite(); }

  size_t write(uint8_t value) override {
    return write(&value, 1);
  }

  size_t write(const uint8_t* data, size_t size) override {
    if (data == nullptr || size == 0 || size > MAX_WRITE_SIZE) return 0;

    // Never wait for another application writer. All diagnostic access to the
    // delegate is routed through this facade; the USB task can only drain the
    // TX FIFO, so free capacity cannot shrink while this gate is held.
    if (_writer_busy.test_and_set(std::memory_order_acquire)) return 0;

    size_t written = 0;
    const int available = _delegate.availableForWrite();
    if (available >= 0 && static_cast<size_t>(available) >= size) {
      written = _delegate.write(data, size);
    }

    _writer_busy.clear(std::memory_order_release);
    return written;
  }

 private:
  Stream& _delegate;
  std::atomic_flag _writer_busy = ATOMIC_FLAG_INIT;
};

}  // namespace mesh
