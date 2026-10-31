#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// The radio2 (MediaMesh) profile a repeater appends to its DISCOVER_RESP when
// the request set bit 1 (0x02) of its flags byte (docs/radio_profiles.md,
// "Discover the local radio2 profile"):
//   [version=1][flags][freq u32 LE, MHz x1000][bw u32 LE, kHz x1000][sf][cr]
//   flags: bit 0 configured, bits 1-2 mode (0 off, 1 rx, 2 rxtx), bit 3 temporary
//   radio2 off: just [1][0]
// A responder that sent no trailer is unknown (older firmware), not off.
// Mesh-independent so native tests can exercise it.
// Eight bytes: repeaters keep one per cached neighbour and per discovery
// result, and the large-neighbour ESP32 builds have little DRAM to spare.
struct Radio2DiscoverTrailer {
  enum State : uint8_t { Unknown = 0, Off = 1, On = 2 };
  uint32_t freq_khz;     // MHz x1000
  uint16_t bw_dhz;       // bandwidth in 100 Hz steps (kHz x10), all the status format shows
  uint8_t sf : 4;
  uint8_t cr : 4;
  uint8_t state : 2;
  uint8_t mode : 2;      // 1 rx, 2 rxtx (On only)
  uint8_t temporary : 1;

  static Radio2DiscoverTrailer unknown() {
    Radio2DiscoverTrailer t;
    memset(&t, 0, sizeof(t));
    return t;
  }

  uint32_t bwHz() const { return (uint32_t)bw_dhz * 100; }

  // The trailer in `b` (what follows the public key), or Unknown when there is
  // none or it is incomplete or of an unknown version. A later version may
  // append; the first twelve bytes keep their meaning.
  static Radio2DiscoverTrailer parse(const uint8_t* b, size_t n) {
    Radio2DiscoverTrailer t = unknown();
    if (!b || n < 2 || b[0] < 1) return t;
    const uint8_t flags = b[1];
    if (!(flags & 0x01)) {
      t.state = Off;
      return t;
    }
    if (n < 12) return t;
    const uint8_t mode = (flags >> 1) & 0x03;
    if (mode != 1 && mode != 2) {
      t.state = Off;
      return t;
    }
    t.state = On;
    t.mode = mode;
    t.temporary = (flags & 0x08) != 0 ? 1 : 0;
    t.freq_khz = (uint32_t)b[2] | ((uint32_t)b[3] << 8) | ((uint32_t)b[4] << 16) | ((uint32_t)b[5] << 24);
    const uint32_t bw = (uint32_t)b[6] | ((uint32_t)b[7] << 8) | ((uint32_t)b[8] << 16) | ((uint32_t)b[9] << 24);
    t.bw_dhz = (uint16_t)((bw / 100) > 0xFFFF ? 0xFFFF : bw / 100);
    t.sf = b[10] & 0x0F;
    t.cr = b[11] & 0x0F;
    return t;
  }

  const char* modeName() const {
    if (state != On) return "off";
    return mode == 2 ? "rxtx" : "rx";
  }

  // "MHz,kHz,SF,CR" in the MQTT status `radio` format (frequency to 6
  // decimals, bandwidth to 1), from the trailer's exact integers.
  void formatRadio(char* dest, size_t dest_size) const {
    if (!dest || dest_size == 0) return;
    snprintf(dest, dest_size, "%lu.%03lu000,%lu.%lu,%u,%u",
             (unsigned long)(freq_khz / 1000), (unsigned long)(freq_khz % 1000),
             (unsigned long)(bw_dhz / 10), (unsigned long)(bw_dhz % 10),
             (unsigned)sf, (unsigned)cr);
  }
};
