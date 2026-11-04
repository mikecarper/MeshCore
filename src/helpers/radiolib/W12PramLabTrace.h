#pragma once
#include <stdint.h>

namespace mesh { namespace w12pram {
// Lab-only SPI observation: never changes a transaction or touches the radio.
struct SpiTrace {
  bool active=false;
  uint32_t count=0;
  uint16_t first[8]={};
  void reset() { active=false; count=0; for (auto& op:first) op=0; }
  void add(uint16_t op) { if (!active) return; if (count<8) first[count]=op; ++count; }
};
inline SpiTrace& spiTrace() { static SpiTrace trace; return trace; }
} }
