#pragma once
#include <stddef.h>
#include <stdint.h>

namespace mesh { namespace w12pram {
// Lab-only SPI observation: never changes a transaction or touches the radio.
struct SpiTrace {
  bool active=false;
  uint32_t count=0;
  uint16_t first[8]={};
  // Observations, not a proof of current state: an SPI response reports the
  // mode at that transaction, including the arm command and status checks.
  uint16_t modes[6]={};
  uint16_t invalid_status=0;
  void reset() {
    active=false; count=0; invalid_status=0;
    for (auto& op:first) op=0;
    for (auto& mode:modes) mode=0;
  }
  void add(uint16_t op, const uint8_t* response, size_t len) {
    if (!active) return;
    if (count<8) first[count]=op;
    ++count;
    // The LR2021 clocks its big-endian 16-bit Stat at the start of each
    // frame. Reject reserved command/status and chip-mode values; this is a
    // plausibility filter, not a test that the radio is present or healthy.
    const bool plausible = response && len >= 2 && !(response[0] & 0xF8)
        && !(response[1] & 0x08) && (response[1] & 0x07) <= 5;
    uint16_t& value = plausible ? modes[response[1] & 0x07] : invalid_status;
    if (value != UINT16_MAX) ++value;
  }
};
inline SpiTrace& spiTrace() { static SpiTrace trace; return trace; }
} }
