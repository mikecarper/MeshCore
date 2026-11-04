#pragma once
#include <stdint.h>
namespace mesh {
// Local packet metadata. This structure is never part of the LoRa payload.
struct W12PramRxSnapshot {
  uint32_t logical_freq_hz=0, actual_freq_hz=0, bw_hz=0, generation=0;
  uint16_t preamble=0;
  uint8_t profile=0, sf=0, cr=0;
  bool valid=false;
};
}
