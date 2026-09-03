#pragma once

#include <stdint.h>

// Wire contract for the "mota-seeder" link: a device (CLIENT) pulls `.mota` bytes on demand from a host
// daemon (SERVER) that owns a folder of `.mota` files. This is the FIRST concrete MotaSource transport
// (docs/ota_protocol.md Section 9) - the device speaks it over a dedicated Stream (a spare UART / USB-UART), so
// it never contends with the line-based text CLI on the main console.
//
// The device always initiates; every request gets exactly one response. Framing is resync-safe: the
// reader scans for the 2-byte magic, so line noise / a half-read frame just times out and is retried.
// Active transfers are primary mesh traffic; discovery remains background. All multi-byte fields are little-endian.
//
//   request  (device -> host):  'M' 'S'  op(1)  args...                     xsum(1 = XOR of op+args)
//   response (host -> device):  'm' 's'  op(1)  status(1)  payload...       xsum(1 = XOR of all prior)
//
//   OP_COUNT     0x01  args: -            resp payload: count(1)
//   OP_DESCRIBE  0x02  args: idx(1)       resp payload: MotaDesc wire (38 B, see below) [status OK]
//   OP_READ      0x03  args: idx(1) off(4) len(2)   resp payload: len bytes [status OK]
//   OP_DEFLATE_BLOCK 0x09 args: idx(1) block(2) off(2) len(2)
//                           resp payload: total_encoded_len(2) + len encoded bytes [status OK]
//                     `len=0` queries the size. Chunks are <=190 bytes so the full response payload remains
//                     <=192. The host returns ERR unless independent raw-RFC1951 compression is smaller than
//                     the original logical block; callers then use ordinary OP_READ/raw OTA_DATA v2.
//
//   MotaDesc wire (38 B): mid[4] target_id(4) fw_version(4) codec(1) flags(1) total_size(4)
//                         leaves_off(4) block_count(4) payload_off(4) payload_size(4)
//                         block_size_log2(1) source_caps(1) reserved(2)
//   source_caps bit 0 = OP_DEFLATE_BLOCK is implemented. Deployed hosts send zero, so a new node never
//                       waits on an unsupported operation during a rolling host upgrade.
//
// --- STORAGE ops (device -> host WRITE): "pull to folder". The device is fetching a `.mota` off the mesh
// (e.g. a neighbour's self-served firmware it has no local copy of) and streams it into the host folder as
// `<mid>.mota`, so the exact image can be captured for later delta-building. The device still initiates.
// Resume needs NO host bookkeeping: OP_BEGIN 0xFF-fills the file and, on reconnect, the device OP_SREADs the
// merkle leaves and re-requests only the missing blocks (identical to flash-resume). All keyed by mid[4].
//   OP_STAT      0x04  args: mid(4)                        resp payload: present(1) total_size(4)
//   OP_BEGIN     0x05  args: mid(4) total_size(4)          resp: status OK  (create/truncate, 0xFF-filled)
//   OP_WRITE     0x06  args: mid(4) off(4) len(2) data(len)  resp: status OK
//   OP_SREAD     0x07  args: mid(4) off(4) len(2)          resp payload: len bytes (0xFF = never written)
//   OP_FIN       0x08  args: mid(4)                        resp: status OK  (validate + make servable)
//
// status: 0 = OK, non-zero = error (idx out of range, read past EOF, no such stored file, ...). On error the
// response carries no payload (just magic+op+status+xsum).

namespace mesh {
namespace ota {

static const uint8_t  MOTA_SEEDER_REQ_MAGIC0 = 'M';
static const uint8_t  MOTA_SEEDER_REQ_MAGIC1 = 'S';
static const uint8_t  MOTA_SEEDER_RSP_MAGIC0 = 'm';
static const uint8_t  MOTA_SEEDER_RSP_MAGIC1 = 's';

static const uint8_t  MS_OP_COUNT    = 0x01;
static const uint8_t  MS_OP_DESCRIBE = 0x02;
static const uint8_t  MS_OP_READ     = 0x03;
static const uint8_t  MS_OP_STAT     = 0x04;   // storage: does <mid>.mota exist on the host? + its size
static const uint8_t  MS_OP_BEGIN    = 0x05;   // storage: create/truncate <mid>.mota (0xFF-filled)
static const uint8_t  MS_OP_WRITE    = 0x06;   // storage: write bytes at offset
static const uint8_t  MS_OP_SREAD    = 0x07;   // storage: read bytes back (resume: recompute missing blocks)
static const uint8_t  MS_OP_FIN      = 0x08;   // storage: transfer complete - validate + make servable
static const uint8_t  MS_OP_DEFLATE_BLOCK = 0x09; // source: independently compressed payload block/chunk

static const uint8_t  MS_STATUS_OK   = 0x00;
static const uint8_t  MS_STATUS_ERR  = 0x01;

static const uint16_t MOTA_DESC_WIRE = 38;   // existing reserved bytes carry geometry/caps; wire size is unchanged
// Keep host-to-device replies below common 256-byte USB CDC/UART receive rings. Manifest leaves can exceed
// that ring, and payload reads are normally 1 KB; splitting them here prevents silent overrun/checksum loss.
static const uint16_t MOTA_SEEDER_READ_MAX = 192;
static const uint16_t MOTA_SEEDER_DEFLATE_CHUNK_MAX = 190; // plus uint16 total length = 192-byte response payload
static const uint16_t MOTA_SEEDER_WRITE_MAX = 512;  // max data bytes per OP_WRITE/OP_SREAD (bounds frames)

} // namespace ota
} // namespace mesh
