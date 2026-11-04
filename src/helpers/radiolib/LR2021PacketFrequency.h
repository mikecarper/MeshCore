#pragma once

#include <stdint.h>

namespace mesh { namespace lr2021 {

// Local metadata for one successfully received physical packet. Never put
// this structure on the wire or infer transmitter identity from it.
struct PacketFrequencyObservation {
  int32_t offset_hz = 0;
  uint32_t frequency_hz = 0;
  uint32_t captured_at_ms = 0;
  bool valid = false;
};

}} // namespace mesh::lr2021
