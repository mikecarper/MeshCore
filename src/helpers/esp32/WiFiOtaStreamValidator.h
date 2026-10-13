#pragma once

#include "WiFiOtaSession.h"

namespace mesh {
namespace wifi {

// Hash supplies reset/update/finalize (the production instance is Crypto
// SHA256). Keep exactly one EndF trailer pending so the body is hashed and
// parsed while it streams, without buffering the firmware in RAM.
template<class Hash>
class OtaStreamValidator {
  enum class Phase { Header, SegmentHeader, SegmentData, Padding, Digest, Done };
  const OtaSessionIdentity& identity;
  Hash body_hash, esp_hash;
  size_t expected, received = 0, body_processed = 0, checksum_offset = 0;
  uint8_t tail[56] = {}, header[24] = {}, segment[8] = {}, image_digest[32] = {};
  size_t tail_used = 0, header_used = 0, segment_used = 0, digest_used = 0;
  uint32_t data_remaining = 0;
  uint8_t segments_remaining = 0, checksum = 0xef;
  Phase phase = Phase::Header;
  bool invalid = false, finished = false;

  static uint32_t read32(const uint8_t* bytes) {
    return static_cast<uint32_t>(bytes[0]) | static_cast<uint32_t>(bytes[1]) << 8 |
        static_cast<uint32_t>(bytes[2]) << 16 | static_cast<uint32_t>(bytes[3]) << 24;
  }
  bool reject() { invalid = true; return false; }
  void segmentComplete() {
    --segments_remaining;
    segment_used = 0;
    if (segments_remaining) phase = Phase::SegmentHeader;
    else {
      checksum_offset = ((body_processed / 16) + 1) * 16 - 1;
      phase = Phase::Padding;
    }
  }

  bool bodyByte(uint8_t value) {
    const size_t offset = body_processed++;
    if (body_processed > expected - sizeof(tail)) return reject();
    if (phase != Phase::Digest && phase != Phase::Done) esp_hash.update(&value, 1);
    switch (phase) {
      case Phase::Header:
        header[header_used++] = value;
        if (header_used == sizeof(header)) {
          const uint16_t chip = static_cast<uint16_t>(header[12]) |
              static_cast<uint16_t>(header[13]) << 8;
          if (header[0] != 0xe9 || header[1] < 1 || header[1] > 16 ||
              header[2] > 3 || header[23] > 1 || chip != identity.chip) return reject();
          segments_remaining = header[1];
          phase = Phase::SegmentHeader;
        }
        return true;
      case Phase::SegmentHeader:
        segment[segment_used++] = value;
        if (segment_used == sizeof(segment)) {
          data_remaining = read32(segment + 4);
          if (data_remaining > expected - sizeof(tail) - body_processed) return reject();
          phase = Phase::SegmentData;
          if (!data_remaining) segmentComplete();
        }
        return true;
      case Phase::SegmentData:
        checksum ^= value;
        if (--data_remaining == 0) segmentComplete();
        return true;
      case Phase::Padding:
        if (offset < checksum_offset) return value == 0 || reject();
        if (offset != checksum_offset || value != checksum) return reject();
        if (header[23] == 1) {
          esp_hash.finalize(image_digest, sizeof(image_digest));
          phase = Phase::Digest;
        } else phase = Phase::Done;
        return true;
      case Phase::Digest:
        if (value != image_digest[digest_used++]) return reject();
        if (digest_used == sizeof(image_digest)) phase = Phase::Done;
        return true;
      case Phase::Done:
        return reject(); // No second image, partition table or arbitrary suffix.
    }
    return reject();
  }

  bool bodyBytes(const uint8_t* bytes, size_t count) {
    body_hash.update(bytes, count);
    for (size_t i = 0; i < count; ++i) if (!bodyByte(bytes[i])) return false;
    return true;
  }

public:
  OtaStreamValidator(const OtaSessionIdentity& bound_identity, size_t total)
      : identity(bound_identity), expected(total) {
    body_hash.reset();
    esp_hash.reset();
    invalid = !identity.prepared() || total < 24 + 8 + 16 + sizeof(tail) ||
        total > identity.slot;
  }

  bool valid() const { return !invalid && !finished; }

  bool update(const uint8_t* bytes, size_t count) {
    if (!valid() || (!bytes && count) || count > expected - received) return reject();
    received += count;
    if (count + tail_used <= sizeof(tail)) {
      if (count) memcpy(tail + tail_used, bytes, count);
      tail_used += count;
      return true;
    }
    size_t flush = count + tail_used - sizeof(tail);
    const size_t old_flush = flush < tail_used ? flush : tail_used;
    if (!bodyBytes(tail, old_flush)) return false;
    tail_used -= old_flush;
    memmove(tail, tail + old_flush, tail_used);
    flush -= old_flush;
    if (!bodyBytes(bytes, flush)) return false;
    bytes += flush;
    count -= flush;
    memcpy(tail + tail_used, bytes, count);
    tail_used += count;
    return true;
  }

  bool finish() {
    if (!valid()) return false;
    finished = true;
    if (received != expected || tail_used != sizeof(tail) || phase != Phase::Done ||
        body_processed != expected - sizeof(tail) || memcmp(tail, "EndF", 4) != 0 ||
        read32(tail + 4) != body_processed) return reject();
    char hardware[32] = {};
    const size_t length = strlen(identity.hardware);
    if (!length || length > sizeof(hardware)) return reject();
    memcpy(hardware, identity.hardware, length);
    if (memcmp(tail + 24, hardware, sizeof(hardware)) != 0) return reject();
    uint8_t digest[32];
    body_hash.finalize(digest, sizeof(digest));
    if (memcmp(digest, tail + 8, 8) != 0) return reject();
    return true;
  }
};

} // namespace wifi
} // namespace mesh
