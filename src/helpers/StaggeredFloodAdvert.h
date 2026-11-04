#pragma once

#include <stdint.h>
#include <RadioProfiles.h>
#include <helpers/RadioDirectionPolicy.h>

namespace mesh {

inline bool radio2FloodAdvertEligible(const RadioProfiles* profiles) {
  return radioDirection::radioTransmitAllowed() && profiles && profiles->canTransmit(1)
      && profiles->secondary.params.protocol == RadioProfileProtocol::MeshCore;
}

// A local advert originates on the chosen profile. This avoids treating a
// radio2-only transmission as a cross-profile forward subject to cross rules.
template <typename PacketT>
inline void targetRadio2FloodAdvert(PacketT& packet) {
  packet.radio_profile = 1;
  packet.tx_radio = RADIO_TX_SECONDARY;
  packet.radio_local = true;
}

// One optional radio2 flood at the midpoint of each primary flood interval.
// A missed midpoint is never replayed when radio2 becomes usable afterwards.
class StaggeredFloodAdvert {
 public:
  // Do not transmit old midpoints after a long sleep; small scheduling delays
  // are acceptable, but an overdue advert must not become a wake-up burst.
  static constexpr uint32_t MaxLatenessMs = 60000UL;

  void schedule(uint32_t now, uint32_t interval_ms, bool eligible) {
    if (interval_ms < 2 || interval_ms >= 0x80000000UL) { deadline_ = 0; return; }
    eligible_before_ = eligible;
    deadline_ = now + interval_ms / 2;
    // Existing sleep-timer APIs reserve zero as "disabled". Moving this
    // single rollover case forward by one millisecond preserves that contract.
    if (deadline_ == 0) deadline_ = 1;
  }

  bool service(uint32_t now, bool eligible) {
    if (!deadline_) return false;
    const uint32_t lateness = now - deadline_;
    if (static_cast<int32_t>(lateness) < 0) {
      eligible_before_ = eligible;
      return false;
    }
    const bool send = eligible && eligible_before_ && lateness <= MaxLatenessMs;
    deadline_ = 0;
    return send;
  }

  // A disabled setting must not later replay a midpoint missed while off.
  void pause() { eligible_before_ = false; }

  uint32_t sleepDeadline(bool eligible) const {
    return eligible ? deadline_ : 0;
  }

 private:
  uint32_t deadline_ = 0;
  bool eligible_before_ = false;
};

} // namespace mesh
