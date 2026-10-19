#pragma once

#include <Arduino.h>
#include "MotaStreamWritePolicy.h"
#include "OtaSource.h"

// A MotaSource backed by a host "mota-seeder" daemon over a dedicated framed Stream, such as a spare
// UART, USB CDC, or TCP client. The device pulls catalog + bytes on demand (MotaSeederProto.h); the folder
// image is never held on the device - it streams through. Do not share the Stream with a text CLI because
// command/log text would collide with the binary framing. Each request's entire
// response (sync, header, payload, checksum) has one `timeout_ms` deadline after
// request transmission. Larger reads use multiple bounded requests. Keep the
// host responsive so a round-trip does not stall the primary transfer.

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
#if OTA_BOUNDED_SOURCE_PROOFS
  uint16_t read_quantum() const override { return responsePayloadCapacity(); }
  bool read_metadata(uint8_t idx, uint32_t off, uint8_t* buf, uint32_t len) override;
#endif
  bool    read_deflated_block(uint8_t idx, uint16_t block, uint8_t* buf,
                              uint16_t cap, uint16_t* len) override;

  // Only shared USB text/control ownership opts into retained boundaries.
  // Dedicated UART/BLE/TCP keep their legacy retry and stale-drain behavior.
  void enableSharedTextControl() { _shared_text_control = true; }
  // A shared USB owner consumes only out-of-response control bytes. One call
  // reads at most one underlying byte; RESPONSE_BYTE means binary was consumed.
  static constexpr int RESPONSE_BYTE = -2;
  int availableControlBytes();
  int readControlByte();
  bool hasPendingResponse() const { return _response_pending; }
  void resetSessionState();

protected:
  // Full framed response capacity, including magic/op/status/checksum. The
  // default preserves USB/UART/TCP geometry. A BLE source can override this
  // using its current ATT MTU without storing another buffer or callback.
  virtual uint16_t responseFrameCapacity() const { return 197; }

private:
  uint16_t responsePayloadCapacity() const;
  // Send a request (op+args) and read its response header; on OK, `payload` (if non-null) receives
  // `payload_len` bytes. Returns true iff a well-formed OK response for `op` arrived in time.
  bool txn(uint8_t op, const uint8_t* args, uint8_t arglen, uint8_t* payload, uint32_t payload_len);
  bool readByteT(uint8_t& b, uint32_t started); // within the shared response deadline
  bool readExact(uint8_t* b, uint32_t n, uint32_t started);
  int readTrackedByte();
  void queueControlByte(uint8_t b);
  int takeControlByte();

  enum ResponseStage : uint8_t {
    SYNC_FIRST, SYNC_SECOND, RESPONSE_OP, RESPONSE_STATUS,
    RESPONSE_PAYLOAD, RESPONSE_CHECKSUM,
  };
  bool _shared_text_control = false;
  bool _response_pending = false;
  ResponseStage _response_stage = SYNC_FIRST;
  uint32_t _response_payload_len = 0;
  uint32_t _response_remaining = 0;
  uint8_t _control_queue[64] = {};
  uint8_t _control_offset = 0;
  uint8_t _control_len = 0;
  bool _control_discard_line = false;

  Stream&  _io;
  uint32_t _to;
  MotaStreamWritePolicy _write_policy;
};

} // namespace ota
} // namespace mesh
