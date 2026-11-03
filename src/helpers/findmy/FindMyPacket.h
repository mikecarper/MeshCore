// SPDX-License-Identifier: MIT
// Find My advertisement layout adapted from OpenHaystack Zephyr firmware.
// Copyright (c) 2021 Koen Vervloesem.
// https://github.com/koenvervloesem/openhaystack-zephyr
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to
// deal in the Software without restriction, including without limitation the
// rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
// sell copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.

#pragma once

#include <stdint.h>
#include <string.h>

namespace mesh {
namespace findmy {

// Build a legacy non-connectable advertisement's manufacturer data and random
// static address from a 28-byte OpenHaystack public key. addr is in display
// order; adapters that use least-significant-byte-first must reverse it.
// The caller prepends the AD length and manufacturer-data type when needed.
inline void buildPacket(const uint8_t key[28], uint8_t addr[6],
                        uint8_t payload[29], uint8_t status = 0) {
  addr[0] = static_cast<uint8_t>(key[0] | 0xc0);
  memcpy(addr + 1, key + 1, 5);

  payload[0] = 0x4c;  // Apple company identifier, little-endian
  payload[1] = 0x00;
  payload[2] = 0x12;  // Offline Finding
  payload[3] = 0x19;
  payload[4] = status;
  memcpy(payload + 5, key + 6, 22);
  payload[27] = static_cast<uint8_t>(key[0] >> 6);
  payload[28] = 0x00;
}

}  // namespace findmy
}  // namespace mesh
