#pragma once

#include <Arduino.h>
#include "MotaStreamWritePolicy.h"
#include "OtaSource.h"

// A MotaSource backed by a host "mota-seeder" daemon over a dedicated framed Stream, such as a spare
// UART, USB CDC, or TCP client. The device pulls catalog + bytes on demand (MotaSeederProto.h); the folder
// image is never held on the device - it streams through. Do not share the Stream with a text CLI because
// command/log text would collide with the binary framing. Reads block up to `timeout_ms`; keep the host
// endpoint responsive so a round-trip does not stall the primary transfer.

namespace mesh {
namespace ota {

class SerialMotaSource : public MotaSource {
public:
  explicit SerialMotaSource(Stream& io, MotaStreamWritePolicy write_policy,
                            uint32_t timeout_ms = 400)
      : _io(io), _to(timeout_ms), _write_policy(write_policy) {}

  uint8_t count() override;
  bool    describe(uint8_t idx, MotaDesc& out) override;
  bool    read(uint8_t idx, uint32_t off, uint8_t* buf, uint32_t len) override;
  bool    read_deflated_block(uint8_t idx, uint16_t block, uint8_t* buf,
                              uint16_t cap, uint16_t* len) override;

private:
  // Send a request (op+args) and read its response header; on OK, `payload` (if non-null) receives
  // `payload_len` bytes. Returns true iff a well-formed OK response for `op` arrived in time.
  bool txn(uint8_t op, const uint8_t* args, uint8_t arglen, uint8_t* payload, uint32_t payload_len);
  bool readByteT(uint8_t& b);          // one byte within the timeout
  bool readExact(uint8_t* b, uint32_t n);

  Stream&  _io;
  uint32_t _to;
  MotaStreamWritePolicy _write_policy;
};

} // namespace ota
} // namespace mesh
