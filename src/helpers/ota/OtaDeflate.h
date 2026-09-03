#pragma once

#include <stdint.h>

namespace mesh {
namespace ota {

// Full raw RFC1951 transport decoder (stored, fixed-Huffman, and dynamic-Huffman blocks).
// `dst_cap` is the exact expected logical block length, not merely spare capacity. Success
// requires exact output and exact whole-byte input consumption; malformed/truncated streams,
// output overflow, and trailing bytes fail closed.
bool ota_transport_inflate(void* context, const uint8_t* src, uint16_t src_len,
                           uint8_t* dst, uint16_t dst_cap, uint16_t* dst_len);

} // namespace ota
} // namespace mesh
