#include "MotaSourceSerial.h"
#include "MotaSeederProto.h"
#include "OtaByteIO.h"
#include <string.h>

namespace mesh {
namespace ota {

bool SerialMotaSource::readByteT(uint8_t& b) {
  uint32_t t0 = millis();
  while ((millis() - t0) < _to) {
    int c = _io.read();
    if (c >= 0) { b = (uint8_t)c; return true; }
    delay(1);  // let BLE/WiFi callbacks deliver the response
  }
  return false;
}

bool SerialMotaSource::readExact(uint8_t* b, uint32_t n) {
  for (uint32_t i = 0; i < n; i++) if (!readByteT(b[i])) return false;
  return true;
}

// One request/response transaction. Resync-safe: drains stale input, frames the request with an XOR
// checksum, then scans for the response magic and validates op+status+checksum before delivering payload.
bool SerialMotaSource::txn(uint8_t op, const uint8_t* args, uint8_t arglen,
                           uint8_t* payload, uint32_t payload_len) {
  while (_io.read() >= 0) {}                       // drop any stale/partial bytes before a fresh request
  if (arglen > 7) return false;                    // largest source request is READ(idx,off,len)
  uint8_t xs = op;
  for (uint8_t i = 0; i < arglen; i++) xs ^= args[i];
  uint8_t frame[11];
  uint8_t frame_len = 0;
  frame[frame_len++] = MOTA_SEEDER_REQ_MAGIC0;
  frame[frame_len++] = MOTA_SEEDER_REQ_MAGIC1;
  frame[frame_len++] = op;
  if (arglen) { memcpy(frame + frame_len, args, arglen); frame_len += arglen; }
  frame[frame_len++] = xs;
  if (_io.write(frame, frame_len) != frame_len) return false;
  if (_write_policy == MotaStreamWritePolicy::FlushTransmit) _io.flush();

  // scan for response magic 'm' 's' (tolerate leading noise)
  uint32_t t0 = millis(); bool got = false;
  uint8_t prev = 0;
  while ((millis() - t0) < _to) {
    int c = _io.read();
    if (c < 0) { delay(1); continue; }
    if (prev == MOTA_SEEDER_RSP_MAGIC0 && (uint8_t)c == MOTA_SEEDER_RSP_MAGIC1) { got = true; break; }
    prev = (uint8_t)c;
  }
  if (!got) return false;

  uint8_t hdr[2];
  if (!readExact(hdr, 2)) return false;            // op, status
  if (hdr[0] != op) return false;
  uint8_t rxs = (uint8_t)(MOTA_SEEDER_RSP_MAGIC0 ^ MOTA_SEEDER_RSP_MAGIC1) ^ hdr[0] ^ hdr[1];
  bool ok = (hdr[1] == MS_STATUS_OK);
  if (ok && payload_len) {
    if (!readExact(payload, payload_len)) return false;
    for (uint32_t i = 0; i < payload_len; i++) rxs ^= payload[i];
  }
  uint8_t xsum;
  if (!readByteT(xsum)) return false;
  if (xsum != rxs) return false;                   // corrupt frame -> caller retries
  return ok;
}

uint8_t SerialMotaSource::count() {
  uint8_t n = 0;
  if (!txn(MS_OP_COUNT, nullptr, 0, &n, 1)) return 0;
  return n;
}

bool SerialMotaSource::describe(uint8_t idx, MotaDesc& out) {
  uint8_t args[1] = { idx };
  uint8_t w[MOTA_DESC_WIRE];
  if (!txn(MS_OP_DESCRIBE, args, 1, w, MOTA_DESC_WIRE)) return false;
  memcpy(out.mid, w, 4);
  out.target_id    = rd_u32le(w + 4);
  out.fw_version   = rd_u32le(w + 8);
  out.codec_id     = w[12];
  out.flags        = w[13];
  out.total_size   = rd_u32le(w + 14);
  out.leaves_off   = rd_u32le(w + 18);
  out.block_count  = rd_u32le(w + 22);
  out.payload_off  = rd_u32le(w + 26);
  out.payload_size = rd_u32le(w + 30);
  out.block_size_log2 = w[34];
  out.source_caps = w[35];
  // bytes [36,38) remain reserved (zero) for forward compatibility.
  return true;
}

bool SerialMotaSource::read(uint8_t idx, uint32_t off, uint8_t* buf, uint32_t len) {
  if (!buf && len != 0) return false;
  if (len > UINT32_MAX - off) return false;
  uint32_t done = 0;
  while (done < len) {
    uint16_t chunk = (uint16_t)(len - done > MOTA_SEEDER_READ_MAX
        ? MOTA_SEEDER_READ_MAX : len - done);
    uint8_t args[7];
    args[0] = idx;
    wr_u32le(args + 1, off + done);
    args[5] = (uint8_t)(chunk & 0xFF); args[6] = (uint8_t)(chunk >> 8);
    if (!txn(MS_OP_READ, args, 7, buf + done, chunk)) return false;
    done += chunk;
  }
  return true;
}

bool SerialMotaSource::read_deflated_block(uint8_t idx, uint16_t block, uint8_t* buf,
                                           uint16_t cap, uint16_t* len) {
  if (!buf || !len || cap == 0) return false;
  *len = 0;

  uint8_t args[7] = { idx };
  args[1] = (uint8_t)(block & 0xFF); args[2] = (uint8_t)(block >> 8);
  // Query the independently compressed representation's exact size first. The old host returns an error for
  // the unknown operation, which is the intentional raw fallback during a rolling deployment.
  uint8_t total_wire[2];
  if (!txn(MS_OP_DEFLATE_BLOCK, args, sizeof(args), total_wire, sizeof(total_wire))) return false;
  const uint16_t total = rd_u16le(total_wire);
  if (total == 0 || total > cap) return false;

  uint16_t done = 0;
  while (done < total) {
    const uint16_t chunk = (uint16_t)((total - done > MOTA_SEEDER_DEFLATE_CHUNK_MAX)
        ? MOTA_SEEDER_DEFLATE_CHUNK_MAX : total - done);
    args[3] = (uint8_t)(done & 0xFF); args[4] = (uint8_t)(done >> 8);
    args[5] = (uint8_t)(chunk & 0xFF); args[6] = (uint8_t)(chunk >> 8);
    uint8_t response[2 + MOTA_SEEDER_DEFLATE_CHUNK_MAX];
    if (!txn(MS_OP_DEFLATE_BLOCK, args, sizeof(args), response, (uint16_t)(2 + chunk)) ||
        rd_u16le(response) != total) return false;
    memcpy(buf + done, response + 2, chunk);
    done = (uint16_t)(done + chunk);
  }
  *len = total;
  return true;
}

} // namespace ota
} // namespace mesh
