#pragma once

#include "W12PramScannerCodec.h"

namespace mesh { namespace w12pram {

// Lab-only slot map. Each packet keeps its physical SF and logical radio;
// never interpret a five-slot index as a two-profile index.
static constexpr uint8_t SequentialMaxSlots = 5;
struct SequentialSlot { Profile physical; Packed packed; uint8_t logical_profile; };
struct SequentialPlan {
  SequentialSlot slots[SequentialMaxSlots] = {};
  uint32_t generation[2] = {};
  uint8_t count = 0;
};

inline bool packSequentialLab(const Profile& p, Packed& out) {
  if (p.freq_hz < 150000000u || p.freq_hz > 1090000000u || p.sf < 5 || p.sf > 8
      || p.cr < 5 || p.cr > 8 || !p.preamble) return false;
  uint8_t bw;
  switch (p.bw_hz) { case 62500: bw=3; break; case 125000: bw=4; break; case 250000: bw=5; break; default: return false; }
  // 5/6 are RadioLib's two-symbol reference values, not validated for the
  // lab's one-symbol CAD. RF tests must determine usable thresholds.
  const uint8_t peaks[] = {56, 56, 60, 64};
  const uint8_t ldro = ((uint64_t(1) << p.sf) * 1000000u >= 16000ULL * p.bw_hz);
  out.frequency=p.freq_hz;
  out.modem=(p.sf << 4 | bw) | (uint32_t((p.cr - 4) << 4 | ldro) << 8)
      | (uint32_t(peaks[p.sf - 5]) << 16) | (uint32_t(p.preamble) << 24);
  return true;
}

inline bool sequentialPlan(const Profile (&profiles)[2], uint8_t selected,
                           uint8_t first_sf, uint8_t last_sf, SequentialPlan& out) {
  out.count = 0;
  if (selected > 1 || first_sf < 5 || last_sf > 8 || first_sf > last_sf) return false;
  out.generation[0]=profiles[0].generation; out.generation[1]=profiles[1].generation;
  const int64_t diff = int64_t(profiles[0].freq_hz) - int64_t(profiles[1].freq_hz);
  if (diff <= -20000000LL || diff >= 20000000LL) return false;
  for (uint8_t sf=first_sf; sf<=last_sf; ++sf) {
    auto& slot=out.slots[out.count]; slot.physical=profiles[selected]; slot.physical.sf=sf;
    slot.logical_profile=selected;
    if (!packSequentialLab(slot.physical,slot.packed)) { out.count=0; return false; }
    ++out.count;
  }
  auto& fixed=out.slots[out.count]; fixed.logical_profile=selected ^ 1; fixed.physical=profiles[selected ^ 1];
  if (!pack(fixed.physical,fixed.packed)) { out.count=0; return false; }
  ++out.count;
  return true;
}

// Explicit-header LoRa airtime. The preamble belongs to the sender and is
// not transmitted in the header; p.preamble is only our configured estimate.
// CR and CRC are read from the actual received header before the FIFO resumes.
inline bool sequentialReceiveAirtime(const Profile& p, uint8_t cr, bool crc,
                                     int len, uint32_t& ms) {
  ms=0;
  if (p.sf<5 || p.sf>12 || !p.bw_hz || !p.preamble || cr<5 || cr>8
      || len<0 || len>255) return false;
  const uint64_t symbol_num=(uint64_t(1) << p.sf)*1000000ULL;
  const bool ldro=symbol_num>=16000ULL*p.bw_hz;
  const int sf_coeff=p.sf<=6 ? 0 : 8;
  int bits=8*len+(crc ? 16 : 0)-4*p.sf+sf_coeff+20;
  if (bits<0) bits=0;
  const int div=4*(p.sf-(ldro ? 2 : 0));
  const uint32_t coded=(bits+div-1)/div;
  const uint32_t symbols_x4=(p.preamble+8u)*4u+(p.sf<=6 ? 25u : 17u)+coded*cr*4u;
  const uint64_t denom=uint64_t(p.bw_hz)*4000ULL;
  ms=uint32_t((symbol_num*symbols_x4+denom-1)/denom);
  return true;
}

inline bool sequentialHit(const SequentialPlan& plan, uint8_t slot,
                          uint32_t gen0, uint32_t gen1, SequentialSlot& out) {
  if (!plan.count || plan.count > SequentialMaxSlots || slot >= plan.count
      || plan.generation[0] != gen0 || plan.generation[1] != gen1) return false;
  const auto& hit=plan.slots[slot];
  if (hit.logical_profile > 1 || hit.physical.generation != (hit.logical_profile ? gen1 : gen0)) return false;
  out=hit;
  return true;
}

} } // namespace mesh::w12pram
