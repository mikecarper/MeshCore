#pragma once

#include <stdint.h>
#include <stddef.h>
#include <math.h>

namespace mesh {

enum class RadioProfileMode : uint8_t { Off = 0, Rx = 1, RxTx = 2 };
enum class RadioCrossMode : uint8_t { Auto = 0, On = 1, Off = 2 };

// These are modulation profiles of ONE transceiver, never additional radios.
struct RadioProfileParams {
  float freq = 0;
  float bw = 0;
  uint16_t preamble = 0;  // 0 = automatic, otherwise symbols
  uint8_t sf = 0;
  uint8_t cr = 0;

  bool operator==(const RadioProfileParams& other) const {
    return freq == other.freq && bw == other.bw && sf == other.sf
        && cr == other.cr && preamble == other.preamble;
  }
  bool operator!=(const RadioProfileParams& other) const { return !(*this == other); }
};

struct RadioProfileConfig {
  RadioProfileParams params;
  RadioProfileMode mode = RadioProfileMode::Off;
};

// Portable timing/routing model, also used by the native tests. Hardware access
// remains in RadioLibWrapper; timers and persisted settings remain in the CLI.
class RadioProfiles {
 public:
  // Normal RX uses a microsecond window, so fractional symbols are supported.
  static constexpr double SlowListenSymbols = 4.8;
  static constexpr uint8_t MinFastListenSymbols = 4;
  static constexpr uint8_t AcquisitionSymbols = 8;
  static constexpr uint16_t MaxPreamble = 65528;
  // The integrated fast RX/buffered-SPI path measures ~0.549 ms/hop on XIAO
  // and ~8.263 ms on Indicator (expander GPIO), excluding application scheduling.
  // Keep existing defaults unchanged during board/application qualification.
  // Thus 6 ms is not an upper bound: board-specific budgeting needs follow-up.
  // Packet receptions can extend a visit; these budgets describe an idle scan.
  static constexpr uint32_t SwitchBudgetUs = 6000;
  static constexpr uint32_t LoopBudgetUs = 4000;

  RadioProfileParams primary;
  RadioProfileConfig secondary;
  bool primary_temporary = false;
  bool secondary_temporary = false;
  RadioCrossMode cross = RadioCrossMode::Auto;
  uint32_t generation[2] = {1, 1};
  uint32_t switches = 0;
  uint32_t rx_packets[2] = {};
  uint32_t tx_packets[2] = {};
  uint32_t switch_failures = 0;
  uint32_t longest_switch_us = 0;

  bool enabled() const { return secondary.mode != RadioProfileMode::Off; }
  bool canTransmit(uint8_t profile) const {
    return profile == 0 || (profile == 1 && secondary.mode == RadioProfileMode::RxTx);
  }
  bool canCross() const {
    return cross == RadioCrossMode::On || (cross == RadioCrossMode::Auto
        && primary_temporary == secondary_temporary);
  }
  uint8_t transmitMask(uint8_t origin) const {
    if (origin > 1) origin = 0;
    uint8_t result = canTransmit(origin) ? (1U << origin) : 0;
    if (enabled() && canCross() && canTransmit(origin ^ 1)) result |= 1U << (origin ^ 1);
    return result;
  }
  const RadioProfileParams& params(uint8_t profile) const {
    return profile == 1 ? secondary.params : primary;
  }
  static bool valid(const RadioProfileParams& p) {
    if (!isfinite(p.freq) || !isfinite(p.bw) || p.freq < 150 || p.freq > 2500
        || p.sf < 5 || p.sf > 12 || p.cr < 5 || p.cr > 8
        || (p.preamble != 0 && (p.preamble < 8 || p.preamble > MaxPreamble))) return false;
    const float bandwidths[] = {7.8f, 10.4f, 15.6f, 20.8f, 31.25f, 41.7f,
                               62.5f, 125, 250, 500, 1000, 812.5f, 1625};
    for (float bw : bandwidths) if (fabsf(p.bw - bw) < 0.01f) return true;
    return false;
  }
  static double symbolUs(const RadioProfileParams& p) {
    return p.bw > 0 && p.sf <= 12 ? (double)(1U << p.sf) * 1000.0 / p.bw : 0;
  }
  static bool safePreamble(const RadioProfileParams& p, uint16_t symbols) {
    const double symbol = symbolUs(p);
    if (symbol <= 0) return false;
    const int de = symbol >= 16000 ? 1 : 0;
    const double payload = 8 + ceil((8.0 * 255 - 4 * p.sf
        + (p.sf <= 6 ? 20 : 28) + 16) / (4 * (p.sf - 2 * de))) * 8;
    // RadioLib's microsecond airtime calculation multiplies by four before
    // dividing. Reject settings that overflow that intermediate for a full
    // packet, including a retry using coding rate 4/8.
    return (symbols + (p.sf <= 6 ? 6.25 : 4.25) + payload) * symbol * 4 <= UINT32_MAX;
  }
  uint8_t slowerProfile() const {
    return symbolUs(primary) >= symbolUs(secondary.params) ? 0 : 1;
  }
  static double roundPreamble(double symbols) {
    return ceil((symbols < 32 ? 32 : symbols) / 8.0) * 8.0;
  }
  double automaticPreamble(uint8_t profile) const {
    const double symbol = symbolUs(params(profile));
    if (symbol <= 0) return 32;
    // Allow two slow-channel visits per slow preamble: one short detection
    // opportunity was insufficient in the production V4 test. The fast
    // preamble covers the blind interval during the slow visit and two sets
    // of acquisition symbols (the extra eight come from the hardware test).
    const double overhead = 2.0 * SwitchBudgetUs + LoopBudgetUs;
    if (profile == slowerProfile()) {
      const double fast_visit = MinFastListenSymbols * symbolUs(params(profile ^ 1));
      return roundPreamble(2.0 * (SlowListenSymbols * symbol + fast_visit + overhead) / symbol);
    }
    const double slow_visit = SlowListenSymbols * symbolUs(params(profile ^ 1));
    double symbols = roundPreamble((slow_visit + overhead) / symbol + 2 * AcquisitionSymbols);
    // Production V4 + XIAO testing lost SF8/500 packets at 72 and 80;
    // 88 passed with the paired SF7/62.5 profile. Keep that measured floor
    // even when the theoretical blind interval permits a shorter preamble.
    const auto& other = params(profile ^ 1);
    if (params(profile).sf == 8 && params(profile).bw == 500
        && other.sf == 7 && other.bw == 62.5f && symbols < 88) symbols = 88;
    return symbols;
  }
  uint32_t listenUs(uint8_t profile, uint16_t slow_preamble = 0) const {
    const uint8_t slow = slowerProfile();
    const double minimum = symbolUs(params(profile))
        * (profile == slow ? SlowListenSymbols : MinFastListenSymbols);
    if (!enabled() || profile == slow) return (uint32_t)ceil(minimum);
    if (!slow_preamble) slow_preamble = preamble(slow, 32);
    const double available = (double(slow_preamble) / 2.0 - SlowListenSymbols)
        * symbolUs(params(slow)) - 2.0 * SwitchBudgetUs - LoopBudgetUs;
    return available > minimum ? (uint32_t)floor(available) : (uint32_t)ceil(minimum);
  }
  uint16_t preamble(uint8_t profile, uint16_t single_profile_default) const {
    const auto& p = params(profile);
    if (p.preamble) return p.preamble;
    if (!enabled()) return single_profile_default;
    double symbols = automaticPreamble(profile);
    if (symbols < single_profile_default) symbols = single_profile_default;
    if (symbols > MaxPreamble) symbols = MaxPreamble;
    return (uint16_t)symbols;
  }
  bool automaticPreambleFits() const {
    for (uint8_t i = 0; i < (enabled() ? 2 : 1); ++i) {
      if (!valid(params(i)) || !safePreamble(params(i), preamble(i, 32))) return false;
      if (!enabled()) continue;
      if (!params(i).preamble && automaticPreamble(i) > MaxPreamble) return false;
    }
    if (enabled()) {
      const uint8_t slow = slowerProfile();
      const double available = (double(preamble(slow, 32)) / 2.0 - SlowListenSymbols)
          * symbolUs(params(slow)) - 2.0 * SwitchBudgetUs - LoopBudgetUs;
      if (available < MinFastListenSymbols * symbolUs(params(slow ^ 1))) return false;
    }
    return true;
  }
  void setPrimary(const RadioProfileParams& p, bool temporary) {
    if (primary != p || primary_temporary != temporary) ++generation[0];
    primary = p;
    primary_temporary = temporary;
  }
  void setSecondary(const RadioProfileConfig& p, bool temporary) {
    if (secondary.params != p.params || secondary.mode != p.mode
        || secondary_temporary != temporary) ++generation[1];
    secondary = p;
    secondary_temporary = temporary;
  }
};

}  // namespace mesh
