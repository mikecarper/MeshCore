#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

struct RoundTripTracePath {
  uint8_t hash_size;
  uint8_t hop_count;
  size_t byte_len;
};

enum class RawTracePathParseResult : uint8_t {
  Valid,
  MissingHashSize,
  InvalidHashSize,
  MissingPrefixes,
  InvalidPrefix,
  TooManyHops,
  RouteTooLong,
};

struct RawTracePath {
  uint8_t hash_size;
  uint8_t hop_count;
  size_t byte_len;
};

inline bool isRawTracePathSeparator(char c) {
  return c == ' ' || c == '\t' || c == ',';
}

inline int rawTraceHexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

// Parse: <hash-size> <prefix>[,<prefix> ...]
// Prefix separators may be commas, spaces, tabs, or any mixture of them.
// TRACE uses power-of-two hash sizes, so only 1, 2, and 4 are accepted.
inline RawTracePathParseResult parseRawTracePath(const char* input,
                                                 uint8_t* output,
                                                 size_t output_capacity,
                                                 uint8_t max_hops,
                                                 RawTracePath& result) {
  result.hash_size = 0;
  result.hop_count = 0;
  result.byte_len = 0;

  if (input == nullptr) return RawTracePathParseResult::MissingHashSize;
  while (*input == ' ' || *input == '\t') input++;
  if (*input == 0) return RawTracePathParseResult::MissingHashSize;

  const char width_char = *input++;
  if (width_char != '1' && width_char != '2' && width_char != '4') {
    return RawTracePathParseResult::InvalidHashSize;
  }
  if (*input != 0 && !isRawTracePathSeparator(*input)) {
    return RawTracePathParseResult::InvalidHashSize;
  }
  const uint8_t hash_size = static_cast<uint8_t>(width_char - '0');
  result.hash_size = hash_size;

  while (isRawTracePathSeparator(*input)) input++;
  if (*input == 0) return RawTracePathParseResult::MissingPrefixes;
  if (output == nullptr || output_capacity < hash_size) {
    return RawTracePathParseResult::RouteTooLong;
  }

  const size_t expected_chars = static_cast<size_t>(hash_size) * 2;
  size_t offset = 0;
  uint8_t hop_count = 0;
  while (*input != 0) {
    const char* token = input;
    size_t token_len = 0;
    while (input[token_len] != 0
           && !isRawTracePathSeparator(input[token_len])) {
      token_len++;
    }
    if (token_len != expected_chars) {
      return RawTracePathParseResult::InvalidPrefix;
    }
    if (hop_count >= max_hops) {
      return RawTracePathParseResult::TooManyHops;
    }
    if (offset + hash_size > output_capacity) {
      return RawTracePathParseResult::RouteTooLong;
    }

    for (uint8_t i = 0; i < hash_size; i++) {
      const int high = rawTraceHexNibble(token[i * 2]);
      const int low = rawTraceHexNibble(token[i * 2 + 1]);
      if (high < 0 || low < 0) {
        return RawTracePathParseResult::InvalidPrefix;
      }
      output[offset++] = static_cast<uint8_t>((high << 4) | low);
    }
    hop_count++;
    input += token_len;
    while (isRawTracePathSeparator(*input)) input++;
  }

  result.hash_size = hash_size;
  result.hop_count = hop_count;
  result.byte_len = offset;
  return RawTracePathParseResult::Valid;
}

// Build the route used by Companion clients for a trace that returns to its
// origin: saved path, optional repeater/room endpoint, then the saved path in
// reverse. Stored three-byte paths are traced with their first two bytes per
// hop because the TRACE flag format has no unambiguous three-byte mode.
inline bool buildRoundTripTracePath(const uint8_t* saved_path,
                                    uint8_t encoded_path_len,
                                    const uint8_t* endpoint_hash,
                                    bool include_endpoint,
                                    uint8_t* output,
                                    size_t output_capacity,
                                    RoundTripTracePath& result) {
  result.hash_size = 0;
  result.hop_count = 0;
  result.byte_len = 0;

  const size_t saved_hash_size = (encoded_path_len >> 6) + 1;
  const size_t saved_hop_count = encoded_path_len & 63;
  if (saved_hash_size == 0 || saved_hash_size > 3
      || (saved_hop_count > 0 && saved_path == nullptr)
      || (include_endpoint && endpoint_hash == nullptr)) {
    return false;
  }

  const size_t trace_hash_size = saved_hash_size == 3 ? 2 : saved_hash_size;
  const size_t route_hop_count = saved_hop_count * 2
      + (include_endpoint ? 1 : 0);
  if (route_hop_count == 0 || route_hop_count > 255
      || trace_hash_size > output_capacity
      || route_hop_count > output_capacity / trace_hash_size
      || output == nullptr) {
    return false;
  }

  size_t offset = 0;
  for (size_t i = 0; i < saved_hop_count; i++) {
    memcpy(&output[offset], &saved_path[i * saved_hash_size], trace_hash_size);
    offset += trace_hash_size;
  }
  if (include_endpoint) {
    memcpy(&output[offset], endpoint_hash, trace_hash_size);
    offset += trace_hash_size;
  }
  for (size_t i = saved_hop_count; i > 0; i--) {
    memcpy(&output[offset], &saved_path[(i - 1) * saved_hash_size],
           trace_hash_size);
    offset += trace_hash_size;
  }

  result.hash_size = static_cast<uint8_t>(trace_hash_size);
  result.hop_count = static_cast<uint8_t>(route_hop_count);
  result.byte_len = offset;
  return true;
}

inline uint8_t traceFlagsForHashSize(uint8_t hash_size) {
  return hash_size == 1 ? 0
      : hash_size == 2 ? 1
      : hash_size == 4 ? 2
      : 0xFF;
}

}  // namespace mesh
