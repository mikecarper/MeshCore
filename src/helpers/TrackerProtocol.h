#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh {
namespace tracker {

// Encrypted PAYLOAD_TYPE_REQ/RESPONSE packets carry the usual four-byte tag.
// Only the configured full peer identity may answer a client's pending tag.
static constexpr uint8_t RequestType = 0x0c;
static constexpr uint8_t Version = 1;
static constexpr size_t TagLength = 4;
static constexpr size_t RequestLength = 13;
static constexpr size_t ResponseLength = 9;
static constexpr size_t CipherBlockLength = 16;

enum Status : uint8_t { Unknown = 0, Safe = 1, Lost = 2 };
enum RequestFlags : uint8_t { FreshGps = 1, GpsAttemptFailed = 2 };

struct StatusRequest {
  uint8_t flags = 0;
  int32_t latitude = 0;   // degrees * 1e7
  int32_t longitude = 0;  // degrees * 1e7
  uint16_t battery_mv = 0;
};

inline uint32_t get32(const uint8_t* bytes) {
  uint32_t value = 0;
  for (size_t i = 0; i < 4; ++i) value |= uint32_t(bytes[i]) << (8 * i);
  return value;
}

inline void put32(uint8_t* bytes, uint32_t value) {
  for (size_t i = 0; i < 4; ++i) bytes[i] = uint8_t(value >> (8 * i));
}

inline int32_t getSigned32(const uint8_t* bytes) {
  const uint32_t value = get32(bytes);
  // Avoid implementation-defined conversion when the sign bit is set.
  return value <= UINT32_C(0x7fffffff) ? int32_t(value)
      : -1 - int32_t(UINT32_MAX - value);
}

inline bool validStatusRequest(const StatusRequest& request) {
  if (request.flags > GpsAttemptFailed) return false;
  if (!(request.flags & FreshGps)) {
    return request.latitude == 0 && request.longitude == 0;
  }
  return request.latitude >= -900000000 && request.latitude <= 900000000
      && request.longitude >= -1800000000 && request.longitude <= 1800000000;
}

inline bool zeroPadded(const uint8_t* bytes, size_t length, size_t logical,
                       size_t padded) {
  if (bytes == NULL || (length != logical && length != padded)) return false;
  for (size_t i = logical; i < length; ++i) {
    if (bytes[i] != 0) return false;
  }
  return true;
}

// The callback has removed the tag. The seventeen-byte plaintext occupies two
// cipher blocks, leaving a twenty-eight-byte body including zero padding.
inline bool parseStatusRequest(const uint8_t* bytes, size_t length,
                                StatusRequest& request) {
  if (!zeroPadded(bytes, length, RequestLength,
                  2 * CipherBlockLength - TagLength)
      || bytes[0] != RequestType || bytes[1] != Version) return false;
  StatusRequest value;
  value.flags = bytes[2];
  value.latitude = getSigned32(bytes + 3);
  value.longitude = getSigned32(bytes + 7);
  value.battery_mv = uint16_t(bytes[11]) | (uint16_t(bytes[12]) << 8);
  if (!validStatusRequest(value)) return false;
  request = value;
  return true;
}

inline size_t makeStatusRequest(const StatusRequest& request, uint8_t* bytes,
                                size_t capacity) {
  if (bytes == NULL || capacity < RequestLength || !validStatusRequest(request))
    return 0;
  bytes[0] = RequestType;
  bytes[1] = Version;
  bytes[2] = request.flags;
  put32(bytes + 3, uint32_t(request.latitude));
  put32(bytes + 7, uint32_t(request.longitude));
  bytes[11] = uint8_t(request.battery_mv);
  bytes[12] = uint8_t(request.battery_mv >> 8);
  return RequestLength;
}

inline size_t makeStatusResponse(uint32_t tag, uint8_t status,
                                 uint16_t stayawake_seconds, uint8_t* bytes,
                                 size_t capacity) {
  if (bytes == NULL || capacity < ResponseLength || status > Lost) return 0;
  put32(bytes, tag);
  bytes[4] = RequestType;
  bytes[5] = Version;
  bytes[6] = status;
  bytes[7] = uint8_t(stayawake_seconds);
  bytes[8] = uint8_t(stayawake_seconds >> 8);
  return ResponseLength;
}

inline bool parseStatusResponse(const uint8_t* bytes, size_t length,
                                uint32_t expected_tag, uint8_t& status,
                                uint16_t& stayawake_seconds) {
  if (!zeroPadded(bytes, length, ResponseLength, CipherBlockLength)
      || bytes[4] != RequestType || bytes[5] != Version || bytes[6] > Lost)
    return false;
  if (get32(bytes) != expected_tag) return false;
  status = bytes[6];
  stayawake_seconds = uint16_t(bytes[7]) | (uint16_t(bytes[8]) << 8);
  return true;
}

} // namespace tracker
} // namespace mesh
