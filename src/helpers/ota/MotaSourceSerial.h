#pragma once

#include <Arduino.h>
#include "OtaSource.h"

// A MotaSource backed by a host "mota-seeder" daemon over a dedicated Stream (a spare UART / USB-UART).
// The device pulls catalog + bytes on demand (MotaSeederProto.h); the folder image is never held on the
// device - it streams through. Use a stream that is NOT the text-CLI console so the binary framing never
// collides with command/log text. Reads block on the Stream up to `timeout_ms`; keep the daemon on a fast
// link so a serial round-trip does not stall the primary transfer.

namespace mesh {
namespace ota {

class SerialMotaSource : public MotaSource {
public:
  // Hardware serial needs flush() to finish transmitting before waiting for a
  // reply. NetworkClient/WiFiClient gives flush() receive-side semantics and
  // discards bytes, so TCP users must pass flush_after_write=false.
  explicit SerialMotaSource(Stream& io, uint32_t timeout_ms = 400,
                            bool flush_after_write = true)
      : _io(io), _to(timeout_ms), _flush_after_write(flush_after_write) {}

  uint8_t count() override;
  bool    describe(uint8_t idx, MotaDesc& out) override;
  bool    read(uint8_t idx, uint32_t off, uint8_t* buf, uint32_t len) override;

private:
  // Send a request (op+args) and read its response header; on OK, `payload` (if non-null) receives
  // `payload_len` bytes. Returns true iff a well-formed OK response for `op` arrived in time.
  bool txn(uint8_t op, const uint8_t* args, uint8_t arglen, uint8_t* payload, uint32_t payload_len);
  bool readByteT(uint8_t& b);          // one byte within the timeout
  bool readExact(uint8_t* b, uint32_t n);

  Stream&  _io;
  uint32_t _to;
  bool     _flush_after_write;
};

} // namespace ota
} // namespace mesh
