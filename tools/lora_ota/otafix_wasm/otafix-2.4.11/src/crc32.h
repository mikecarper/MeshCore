#ifndef OTAFIX_CRC32_H_
#define OTAFIX_CRC32_H_

#include <stddef.h>
#include <stdint.h>

// Reflected IEEE CRC-32, with the previous finalized CRC as the seed (zero
// starts a new stream). Keep one loop instead of specializing it for every
// fixed-size handoff record. LTO can fold identical copies across C files;
// keeping the helper here also preserves standalone use of the wire headers.
__attribute__((noinline, noclone, unused))
static uint32_t otafix_crc32_update(uint32_t crc, const uint8_t *data, size_t len) {
  crc = ~crc;
  while (len--) {
    crc ^= *data++;
    for (unsigned bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
  }
  return ~crc;
}

#endif // OTAFIX_CRC32_H_
