#pragma once

#include <Arduino.h>
#include <atomic>
#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// A Stream facade for transports which provide a genuinely nonblocking write
// primitive separate from Stream::write().  Adafruit_USBD_CDC::write() loops
// and yields until it has queued the complete request; TinyUSB's lower-level
// tud_cdc_n_write() instead returns after one FIFO attempt.  Keeping the
// single-attempt policy here makes the primary Companion CDC and the dedicated
// logging CDC share the same reentrancy guard without changing other targets.
class SingleAttemptNonBlockingStream : public Stream {
 public:
  using TryWrite = size_t (*)(void* context, const uint8_t* data,
                              size_t size);
  using CanAccess = bool (*)(void* context);
  using ExclusiveOperation = void (*)(void* context);

  SingleAttemptNonBlockingStream(Stream& delegate, TryWrite try_write,
                                 void* context = nullptr,
                                 CanAccess can_access = nullptr)
      : _delegate(delegate), _try_write(try_write), _context(context),
        _can_access(can_access) {}

  int available() override {
    if (!canAccess()) return 0;
    const int count = _delegate.available();
    return canAccess() ? count : 0;
  }
  int read() override {
    if (!canAccess()) return -1;
    const int value = _delegate.read();
    // If the host changed while read() was in flight, consume/drop that byte
    // instead of handing it to the next session's protocol parser.
    return canAccess() ? value : -1;
  }
  int peek() override {
    if (!canAccess()) return -1;
    const int value = _delegate.peek();
    return canAccess() ? value : -1;
  }
  // The delegate's flush may wait forever while a CDC host keeps DTR asserted
  // but stops reading. A single-attempt facade must keep every operation
  // single-attempt, including flush.
  void flush() override {}
  int availableForWrite() override {
    return canAccess() ? _delegate.availableForWrite() : 0;
  }

  size_t write(uint8_t value) override { return write(&value, 1); }

  size_t write(const uint8_t* data, size_t size) override {
    if (data == nullptr || size == 0 || _try_write == nullptr) return 0;
    if (_writer_busy.test_and_set(std::memory_order_acquire)) return 0;

    if (!canAccess()) {
      _writer_busy.clear(std::memory_order_release);
      return 0;
    }

    size_t written = _try_write(_context, data, size);
    if (written > size) written = size;

    _writer_busy.clear(std::memory_order_release);
    return written;
  }

  // Check capacity and perform the sole write under the same producer gate.
  // A separate outer preflight is insufficient: another writer over this
  // shared CDC can otherwise consume capacity between check and write.
  size_t writeWholeRecord(const uint8_t* data, size_t size) {
    if (data == nullptr || size == 0 || _try_write == nullptr) return 0;
    if (_writer_busy.test_and_set(std::memory_order_acquire)) return 0;

    if (!canAccess()) {
      _writer_busy.clear(std::memory_order_release);
      return 0;
    }

    size_t written = 0;
    const int available = _delegate.availableForWrite();
    if (available >= 0 && static_cast<size_t>(available) >= size) {
      written = _try_write(_context, data, size);
      if (written > size) written = size;
    }

    _writer_busy.clear(std::memory_order_release);
    return written;
  }

  // Run a short transport/session operation only when no application writer
  // is in flight. The caller retries later on false; this never waits.
  bool tryRunExclusive(ExclusiveOperation operation, void* context = nullptr) {
    if (operation == nullptr
        || _writer_busy.test_and_set(std::memory_order_acquire)) {
      return false;
    }
    operation(context);
    _writer_busy.clear(std::memory_order_release);
    return true;
  }

 private:
  bool canAccess() const {
    return _can_access == nullptr || _can_access(_context);
  }

  Stream& _delegate;
  TryWrite _try_write;
  void* _context;
  CanAccess _can_access;
  std::atomic_flag _writer_busy = ATOMIC_FLAG_INIT;
};

// A whole-record view over a shared SingleAttemptNonBlockingStream. Unlike a
// generic wrapper, its capacity preflight uses the delegate's own writer gate,
// so Binary, terminal, logging, and mOTA producers cannot interleave between
// the capacity sample and the one low-level write.
template <size_t MAX_WRITE_SIZE = 256>
class AtomicWholeRecordNonBlockingStream : public Stream {
 public:
  explicit AtomicWholeRecordNonBlockingStream(
      SingleAttemptNonBlockingStream& delegate) : _delegate(delegate) {}

  int available() override { return _delegate.available(); }
  int read() override { return _delegate.read(); }
  int peek() override { return _delegate.peek(); }
  void flush() override {}
  int availableForWrite() override { return _delegate.availableForWrite(); }

  size_t write(uint8_t value) override { return write(&value, 1); }

  size_t write(const uint8_t* data, size_t size) override {
    if (data == nullptr || size == 0 || size > MAX_WRITE_SIZE) return 0;
    constexpr size_t PRINTF_SCRATCH_SIZE = 256;
    if (size == PRINTF_SCRATCH_SIZE && data[size - 1] == '\0') return 0;
    return _delegate.writeWholeRecord(data, size);
  }

 private:
  SingleAttemptNonBlockingStream& _delegate;
};

// A bounded byte queue for functional text written through a single-attempt
// transport. Producer calls are admitted whole and never wait; service() keeps
// a byte offset across short delegate writes so an ordinary draining host sees
// the exact ordered stream. Once the queue fills, later records are dropped as
// a whole rather than blocking the application loop.
//
// Adafruit nRF52 Print::printf() formats into a 256-byte scratch buffer but can
// pass vsnprintf()'s larger required length to write(). The NUL at byte 255 is
// the only safe signal that such a record was truncated before this facade.
template <size_t CAPACITY = 4096, size_t PRINTF_SCRATCH_SIZE = 256>
class BufferedNonBlockingWriteStream : public Stream {
 public:
  static_assert(CAPACITY > 1, "byte queue needs at least two bytes");
  static_assert(PRINTF_SCRATCH_SIZE > 1,
                "printf scratch size needs at least two bytes");

  explicit BufferedNonBlockingWriteStream(Stream& delegate)
      : _delegate(delegate) {}

  int available() override { return _delegate.available(); }
  int read() override { return _delegate.read(); }
  int peek() override { return _delegate.peek(); }

  // Drain only currently available capacity. Never forward a potentially
  // blocking delegate flush from the application loop.
  void flush() override { service(); }

  int availableForWrite() override {
    if (_busy.test_and_set(std::memory_order_acquire)) return 0;
    const size_t free = CAPACITY - _count;
    _busy.clear(std::memory_order_release);
    return free > static_cast<size_t>(INT_MAX)
        ? INT_MAX
        : static_cast<int>(free);
  }

  size_t write(uint8_t value) override { return write(&value, 1); }

  size_t write(const uint8_t* data, size_t size) override {
    if (data == nullptr || size == 0 || size > CAPACITY) return 0;
    if (size >= PRINTF_SCRATCH_SIZE
        && data[PRINTF_SCRATCH_SIZE - 1] == '\0') {
      return 0;
    }
    if (_busy.test_and_set(std::memory_order_acquire)) return 0;

    drainUnlocked();
    if (size > CAPACITY - _count) {
      _busy.clear(std::memory_order_release);
      return 0;
    }

    const size_t first = size < CAPACITY - _head
        ? size
        : CAPACITY - _head;
    memcpy(&_buffer[_head], data, first);
    if (first < size) memcpy(_buffer, data + first, size - first);
    _head = (_head + size) % CAPACITY;
    _count += size;

    drainUnlocked();
    _busy.clear(std::memory_order_release);
    return size;
  }

  // Called from the application loop. Retains every unwritten suffix for the
  // next call and stops immediately when the delegate reports no progress.
  size_t service() {
    if (_busy.test_and_set(std::memory_order_acquire)) return 0;
    const size_t written = drainUnlocked();
    _busy.clear(std::memory_order_release);
    return written;
  }

  void discardPending() {
    if (_busy.test_and_set(std::memory_order_acquire)) return;
    _head = 0;
    _tail = 0;
    _count = 0;
    _busy.clear(std::memory_order_release);
  }

  size_t queuedByteCount() {
    if (_busy.test_and_set(std::memory_order_acquire)) return CAPACITY;
    const size_t count = _count;
    _busy.clear(std::memory_order_release);
    return count;
  }

 private:
  size_t drainUnlocked() {
    size_t total = 0;
    while (_count > 0) {
      const int available = _delegate.availableForWrite();
      if (available <= 0) break;

      size_t attempt = _count < CAPACITY - _tail
          ? _count
          : CAPACITY - _tail;
      if (attempt > static_cast<size_t>(available)) {
        attempt = static_cast<size_t>(available);
      }
      size_t written = _delegate.write(&_buffer[_tail], attempt);
      if (written > attempt) written = attempt;
      if (written == 0) break;

      _tail = (_tail + written) % CAPACITY;
      _count -= written;
      total += written;
      if (written < attempt) break;
    }
    return total;
  }

  Stream& _delegate;
  uint8_t _buffer[CAPACITY]{};
  size_t _head = 0;
  size_t _tail = 0;
  size_t _count = 0;
  std::atomic_flag _busy = ATOMIC_FLAG_INIT;
};

// A bounded, lossy record queue for transports whose low-level write must run
// in one owner task. Producers only copy complete records into this queue; they
// never touch the delegate. The owner calls drainOne() from its own task. This
// is particularly important for nRF52 TinyUSB: starting a CDC transfer from an
// application task can race control transfers handled by TinyUSB's high-
// priority task, while an unread host can leave that race armed indefinitely.
template <size_t SLOT_COUNT = 8, size_t MAX_WRITE_SIZE = 256>
class TaskOwnedWriteStream : public Stream {
 public:
  static_assert(SLOT_COUNT >= 2, "record queue needs at least two slots");

  explicit TaskOwnedWriteStream(Stream& delegate) : _delegate(delegate) {}

  int available() override { return _delegate.available(); }
  int read() override { return _delegate.read(); }
  int peek() override { return _delegate.peek(); }

  // Flushing is owned by the transport task. A producer must never re-enter
  // the delegate through this facade.
  void flush() override {}

  int availableForWrite() override {
    const size_t head = _head.load(std::memory_order_relaxed);
    const size_t next = increment(head);
    return next == _tail.load(std::memory_order_acquire)
        ? 0
        : static_cast<int>(MAX_WRITE_SIZE);
  }

  size_t write(uint8_t value) override { return write(&value, 1); }

  size_t write(const uint8_t* data, size_t size) override {
    if (data == nullptr || size == 0 || size > MAX_WRITE_SIZE) return 0;
    if (_producer_busy.test_and_set(std::memory_order_acquire)) return 0;

    const size_t head = _head.load(std::memory_order_relaxed);
    const size_t next = increment(head);
    if (next == _tail.load(std::memory_order_acquire)) {
      _producer_busy.clear(std::memory_order_release);
      return 0;
    }

    Record& record = _records[head];
    memcpy(record.data, data, size);
    record.size = size;
    _head.store(next, std::memory_order_release);
    _producer_busy.clear(std::memory_order_release);
    return size;
  }

  // Called only by the delegate's owner task. A short delegate write is
  // discarded rather than retried, because replaying a prefix would duplicate
  // bytes and diagnostics are intentionally best effort.
  size_t drainOne() {
    const size_t tail = _tail.load(std::memory_order_relaxed);
    if (tail == _head.load(std::memory_order_acquire)) return 0;

    const Record& record = _records[tail];
    const int available = _delegate.availableForWrite();
    if (available < 0 || static_cast<size_t>(available) < record.size) return 0;

    size_t written = _delegate.write(record.data, record.size);
    if (written > record.size) written = record.size;
    if (written > 0) {
      _tail.store(increment(tail), std::memory_order_release);
    }
    return written;
  }

  // Called by the owner task, or after its transport callback has been disabled
  // so no consumer can race this cursor. Producers may publish concurrently;
  // loading head before storing tail preserves that newly published record.
  void discardPending() {
    _tail.store(_head.load(std::memory_order_acquire),
                std::memory_order_release);
  }

  size_t queuedRecordCount() const {
    const size_t head = _head.load(std::memory_order_acquire);
    const size_t tail = _tail.load(std::memory_order_acquire);
    return head >= tail ? head - tail : SLOT_COUNT - tail + head;
  }

 private:
  struct Record {
    size_t size = 0;
    uint8_t data[MAX_WRITE_SIZE]{};
  };

  static constexpr size_t increment(size_t value) {
    return value + 1 == SLOT_COUNT ? 0 : value + 1;
  }

  Stream& _delegate;
  Record _records[SLOT_COUNT];
  std::atomic<size_t> _head{0};
  std::atomic<size_t> _tail{0};
  std::atomic_flag _producer_busy = ATOMIC_FLAG_INIT;
};

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

    // Adafruit nRF52 Print::printf() formats into char[256], then forwards
    // vsnprintf()'s required length without clamping it to that buffer. An
    // exactly 256-character result therefore arrives here as 255 characters
    // plus the formatter's trailing NUL. Drop that truncated record as a
    // whole, just as we do for reported lengths greater than the buffer.
    constexpr size_t PRINTF_SCRATCH_SIZE = 256;
    if (size == PRINTF_SCRATCH_SIZE && data[size - 1] == '\0') return 0;

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
