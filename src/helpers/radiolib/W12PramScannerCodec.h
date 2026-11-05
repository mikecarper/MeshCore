#pragma once
#include <stdint.h>
#include <stddef.h>
#include <math.h>
namespace mesh { namespace w12pram {
struct Profile { uint32_t freq_hz, bw_hz; uint8_t sf, cr, preamble; uint32_t generation; };
struct Packed { uint32_t frequency, modem; };
struct Status { uint8_t state, last_hit, current, hooked; uint32_t cads, falses, lost, packets, rounds, events, lost_irq, irq, irq_gate; bool stopped, timeout_known, timeout_armed, timer_repaired; };
// 0 = unavailable (including an older blob), 1 = off, 2 = armed.
inline uint8_t timeoutObservation(const Status& s) {
  return !s.timeout_known ? 0 : (s.timeout_armed ? 2 : 1);
}
inline const char* timeoutObservationName(uint8_t observation) {
  return observation == 2 ? "armed" : observation == 1 ? "off" : "unknown";
}
enum class RecoveryOrigin : uint8_t { None, Other, NoCad, StatusPoll, RxStatus, RxResume, Handoff };
inline const char* recoveryOriginName(RecoveryOrigin origin) {
  switch (origin) {
    case RecoveryOrigin::None: return "none";
    case RecoveryOrigin::NoCad: return "no-cad";
    case RecoveryOrigin::StatusPoll: return "status-poll";
    case RecoveryOrigin::RxStatus: return "rx-status";
    case RecoveryOrigin::RxResume: return "rx-resume";
    case RecoveryOrigin::Handoff: return "handoff";
    default: return "other";
  }
}
inline bool pack(const Profile& p, Packed& out) {
  // Initial HIL: same-band sub-GHz, explicitly limited preamble/timing range.
  if (p.freq_hz < 150000000u || p.freq_hz > 1090000000u || p.sf < 7 || p.sf > 8 || p.cr < 5 || p.cr > 8 || p.preamble == 0) return false;
  uint8_t bw;
  switch (p.bw_hz) { case 62500: bw=3; break; case 125000: bw=4; break; case 250000: bw=5; break; default: return false; }
  const uint8_t peak = p.sf == 7 ? 60 : 64; // one-symbol CAD
  const uint32_t symbols=1u<<p.sf;
  const uint8_t ldro=(symbols * 1000000ULL >= 16000ULL * p.bw_hz) ? 1 : 0;
  const uint8_t m0=(p.sf<<4)|bw, m1=((p.cr-4)<<4)|ldro;
  out.frequency=p.freq_hz; out.modem=m0 | (uint32_t(m1)<<8) | (uint32_t(peak)<<16) | (uint32_t(p.preamble)<<24);
  return true;
}
inline uint32_t word(const uint8_t *p) { return uint32_t(p[0]) | (uint32_t(p[1])<<8) | (uint32_t(p[2])<<16) | (uint32_t(p[3])<<24); }
enum class PauseResult { Stopped, Busy, Invalid };
inline PauseResult pauseResult(const Status& s) {
  if (s.stopped && !s.hooked && s.state==0) return PauseResult::Stopped;
  if (!s.stopped && s.hooked && s.state>=1 && s.state<=4) return PauseResult::Busy;
  return PauseResult::Invalid;
}
inline bool mayResume(bool requested,bool sleeping,bool rx_allowed,bool tx_active,bool carrier,bool repairing,bool due) {
  return requested && !sleeping && rx_allowed && !tx_active && !carrier && !repairing && due;
}
// A watchdog reset discards the RX FIFO. Defer it whenever the scanner or
// host can see a completed packet, including an IRQ not yet processed by the
// chip-side state handler. A later loop can retry if the event was stale.
inline bool mayRecoverStalledScan(const Status& s, bool pending_irq, bool physical_irq) {
  return !(s.irq & (1u << 18)) && !pending_irq && !physical_irq;
}
inline bool mayRepairHeaderTimer(const Status& s, bool pending_irq, bool physical_irq, bool chip_busy) {
  constexpr uint32_t terminal = (1u << 6) | (1u << 9) | (1u << 18)
      | (1u << 21) | (1u << 22) | (1u << 23);
  return s.hooked && s.state == 3 && s.timeout_known && !s.timeout_armed
      && !(s.irq & terminal) && !pending_irq && !physical_irq && !chip_busy;
}
// A running chip-owned scan has no host-managed visit deadline. Bound the
// MCU sleep by the next health poll; the radio IRQ wakes it for a packet.
inline uint32_t idleBudget(bool active, bool hooked, bool blocked, bool generations_match,
                           uint32_t now, uint32_t last_poll, uint32_t slice_ms) {
  if (!active || !hooked || blocked || !generations_match || !slice_ms) return 0;
  const uint32_t elapsed = now - last_poll;
  if (elapsed >= 1000) return 0;
  const uint32_t until_poll = 1000 - elapsed;
  return until_poll < slice_ms ? until_poll : slice_ms;
}
inline void benchRequest(uint8_t mode,const Packed& p,uint8_t (&out)[9]) {
  const uint32_t f=p.frequency,m=p.modem;
  const uint8_t tmp[9]={6,mode,uint8_t(f>>24),uint8_t(f>>16),uint8_t(f>>8),uint8_t(f),uint8_t(m),uint8_t(m>>8),uint8_t(m>>24)};
  for (unsigned i=0;i<9;i++) out[i]=tmp[i];
}
enum class RxEvent { Ignore, Deliver, Invalid };
inline RxEvent classify(const Status& s) {
  if (!s.hooked) return RxEvent::Invalid;
  if (s.state==4 && s.last_hit<2 && (s.irq & (1u<<18))) return RxEvent::Deliver;
  // A delayed edge from a prior packet is harmless after the scanner resumed.
  if (s.state>=1 && s.state<=3 && !(s.irq & (1u<<18))) return RxEvent::Ignore;
  return RxEvent::Invalid;
}
inline Status decode(const uint8_t* p) {
  uint32_t v=word(p); Status s={uint8_t(v),uint8_t(v>>8),uint8_t(v>>16),uint8_t((v>>24)&1u),word(p+4),word(p+8),word(p+12),word(p+16),word(p+20),word(p+24),word(p+28),word(p+32),word(p+36),bool(p[3]&0x80u),bool(p[3]&0x04u),bool(p[3]&0x02u),bool(p[3]&0x08u)}; return s;
}
} }
