#pragma once

#include <Arduino.h>
#include <stddef.h>
#include <stdint.h>
#include "BrowserOtaSeederMailbox.h"

// Unwired correlated transport primitive for SerialMotaSource. Browser endpoints MUST
// independently enforce the BrowserOtaLease for every operation, and detach
// the source from OtaContext before closing/replacing the stream. Never hold
// the lock across a network operation or a SerialMotaSource transaction.
namespace mesh { namespace ota {

class BrowserOtaLock {
public:
  virtual ~BrowserOtaLock() {}
  virtual void lock() = 0;
  virtual void unlock() = 0;
};

class BrowserOtaGuard {
public:
  explicit BrowserOtaGuard(BrowserOtaLock& lock) : _lock(lock) { _lock.lock(); }
  ~BrowserOtaGuard() { _lock.unlock(); }
  BrowserOtaGuard(const BrowserOtaGuard&) = delete;
  BrowserOtaGuard& operator=(const BrowserOtaGuard&) = delete;
private:
  BrowserOtaLock& _lock;
};

class BrowserOtaStream : public Stream {
public:
  explicit BrowserOtaStream(BrowserOtaLock& lock) : _lock(lock) {}

  uint32_t open() {
    BrowserOtaGuard guard(_lock);
    if (_active) return 0;
    _mailbox.reset();
    if (++_epoch == 0) ++_epoch;
    _active = true;
    return _epoch;
  }
  void close(uint32_t epoch) {
    BrowserOtaGuard guard(_lock);
    if (_active && epoch == _epoch) {
      _mailbox.reset();
      _active = false;
    }
  }
  bool browserRequest(uint32_t epoch, uint32_t& sequence, uint8_t* out,
                      size_t capacity, size_t& length) {
    BrowserOtaGuard guard(_lock);
    if (!_active || epoch != _epoch) { length = 0; return false; }
    return _mailbox.request(sequence, out, capacity, length);
  }
  bool browserResponse(uint32_t epoch, uint32_t sequence,
                       const uint8_t* data, size_t length) {
    BrowserOtaGuard guard(_lock);
    return _active && epoch == _epoch && _mailbox.response(sequence, data, length);
  }

  int available() override {
    BrowserOtaGuard guard(_lock);
    return _active ? static_cast<int>(_mailbox.available()) : 0;
  }
  int read() override {
    BrowserOtaGuard guard(_lock);
    return _active ? _mailbox.read() : -1;
  }
  int peek() override {
    BrowserOtaGuard guard(_lock);
    return _active ? _mailbox.peek() : -1;
  }
  void flush() override {} // Bytes are consumed by the browser, not a UART.
  // SerialMotaSource writes one complete, bounded frame at a time. A partial
  // frame must fail rather than producing an uncorrelated browser request.
  size_t write(uint8_t) override { return 0; }
  size_t write(const uint8_t* data, size_t length) override {
    BrowserOtaGuard guard(_lock);
    return _active && _mailbox.submit(data, length) ? length : 0;
  }

private:
  BrowserOtaLock& _lock;
  BrowserOtaSeederMailbox _mailbox;
  uint32_t _epoch = 0;
  bool _active = false;
};

}} // namespace mesh::ota
