#pragma once
#include <Arduino.h>

#define RX_POWERSAVING_DEFAULT_RX_US      65625UL
#define RX_POWERSAVING_DEFAULT_SLEEP_US   60000UL
#define RX_POWERSAVING_MIN_PERIOD_US      32UL
#define RX_POWERSAVING_MIN_MANUAL_PERIOD_US 1000UL
#define RX_POWERSAVING_MAX_PERIOD_US      30000000UL

// Every sender must make the same wire-preamble choice for a given SF/BW,
// regardless of whether that sender enables RXPS locally. Qualify the choice
// against the worst supported SX1262+TCXO transition so a receiver never bases
// its sleep window on a longer preamble than another current sender transmits.
#define RX_POWERSAVING_WIRE_TRANSITION_US 6000UL

// A level-derived timing pair equal to this value means the requested LoRa
// tuple is too fast for a safe duty cycle. Keep the operator's RXPS preference
// enabled, but receive continuously until a later SF/BW change produces usable
// timings. Using an in-range pair lets the existing radio-parameter API carry
// the fallback without persisting invalid sub-millisecond periods.
#define RX_POWERSAVING_CONTINUOUS_FALLBACK_US 1000UL

// Named profiles keep a conservative 16-symbol timing assumption. The adaptive
// calculation may use the longer physical preamble only when the configured
// assumption cannot safely cover the current tuple.
#define RX_POWERSAVING_CONSERVATIVE_LEVEL 1
#define RX_POWERSAVING_BALANCED_LEVEL     5
#define RX_POWERSAVING_PROFILE_PREAMBLE   16

// Optional initial settings for infrastructure roles. They are intentionally
// disabled unless a build profile supplies them, so upstream/default builds
// retain continuous receive. Companion has its own historical defaults below.
#ifndef DEFAULT_RXPS_ENABLED
#define DEFAULT_RXPS_ENABLED              0
#endif
#ifndef DEFAULT_RXPS_LEVEL
#if DEFAULT_RXPS_ENABLED
#define DEFAULT_RXPS_LEVEL                RX_POWERSAVING_BALANCED_LEVEL
#else
#define DEFAULT_RXPS_LEVEL                0
#endif
#endif
#ifndef DEFAULT_RXPS_PREAMBLE
#if DEFAULT_RXPS_ENABLED
#define DEFAULT_RXPS_PREAMBLE             RX_POWERSAVING_PROFILE_PREAMBLE
#else
#define DEFAULT_RXPS_PREAMBLE             0
#endif
#endif

#if DEFAULT_RXPS_ENABLED
#if DEFAULT_RXPS_LEVEL < 1 || DEFAULT_RXPS_LEVEL > 10
#error "DEFAULT_RXPS_LEVEL must be between 1 and 10"
#endif
#if DEFAULT_RXPS_PREAMBLE != 16 && DEFAULT_RXPS_PREAMBLE != 32
#error "DEFAULT_RXPS_PREAMBLE must be 16 or 32"
#endif
#endif

// Initial settings for companions. Build flags can override the defaults;
// roles with runtime RXPS controls persist the operator's selection afterward.
#ifndef RXPS_FIXED_ENABLED
#define RXPS_FIXED_ENABLED                1
#endif
#ifndef RXPS_FIXED_LEVEL
#define RXPS_FIXED_LEVEL                  RX_POWERSAVING_BALANCED_LEVEL
#endif
#ifndef RXPS_FIXED_PREAMBLE
#define RXPS_FIXED_PREAMBLE               RX_POWERSAVING_PROFILE_PREAMBLE
#endif

inline bool isValidRxPowerSavingPeriod(uint32_t us) {
  return us >= RX_POWERSAVING_MIN_PERIOD_US && us <= RX_POWERSAVING_MAX_PERIOD_US;
}

inline bool rxPowerSavingUsesContinuousFallback(uint32_t rx_us, uint32_t sleep_us) {
  return rx_us == RX_POWERSAVING_CONTINUOUS_FALLBACK_US
      && sleep_us == RX_POWERSAVING_CONTINUOUS_FALLBACK_US;
}

inline uint32_t rxPowerSavingDefaultTransitionUs() {
#if defined(SX126X_DIO3_TCXO_VOLTAGE)
  // RadioLib uses a 5 ms DIO3 TCXO delay plus 1 ms for sleep/wake.
  return 6000UL;
#else
  // Crystal and non-SX126x implementations still need transition headroom.
  return 1000UL;
#endif
}

// Mirrors the SX126x/RF duty-cycle timer conversion: both encoded periods must
// contain at least one 15.625 us tick after the radio/TCXO transition time has
// been removed from the sleep period.
inline bool canStartRxPowerSavingDutyCycle(uint32_t rx_us, uint32_t sleep_us,
                                            uint32_t transition_us) {
  if (sleep_us <= transition_us) return false;
  return ((rx_us * 8UL) / 125UL) != 0
      && (((sleep_us - transition_us) * 8UL) / 125UL) != 0;
}

inline bool isRxPowerSavingPreamble(uint8_t preamble) {
  return preamble == 16 || preamble == 32 || preamble == 64 || preamble == 128;
}

inline bool isNumeric(const char *sp) {
  if (!sp || !*sp) return false;
  while (*sp) {
    if (*sp < '0' || *sp > '9') return false;
    sp++;
  }
  return true;
}

inline uint32_t ceilPositiveFloat(float value) {
  uint32_t rounded = (uint32_t)value;
  return value > (float)rounded ? rounded + 1 : rounded;
}

inline bool calcRxPowerSavingLevel(uint8_t level, uint8_t sf, float bw, uint8_t preamble, uint32_t *rx_us,
                                   uint32_t *sleep_us) {
  if (!rx_us || !sleep_us || level < 1 || level > 10 || sf < 5 || sf > 12
      || bw <= 0.0f || !isRxPowerSavingPreamble(preamble)) {
    return false;
  }

  const float symbol_us = (1000.0f * (float)(1UL << sf)) / bw;
  const float amount = (float)(level - 1) / 9.0f;
  const float rx_start_symbols = preamble == 16 ? 12.0f : 16.0f;
  const float sleep_start_symbols = preamble == 16 ? 2.0f : 15.0f;
  const float rx_edge_symbols = 8.0f;
  const float sleep_edge_symbols = (float)preamble + 4.25f - 8.0f;

  const float rx_symbols = rx_start_symbols + amount * (rx_edge_symbols - rx_start_symbols);
  const float sleep_symbols = sleep_start_symbols + amount * (sleep_edge_symbols - sleep_start_symbols);

  uint32_t calculated_rx_us = ceilPositiveFloat(rx_symbols * symbol_us);
  uint32_t calculated_sleep_us = (uint32_t)(sleep_symbols * symbol_us);

  // Do not scale an undersized period up: that would break the preamble-overlap
  // guarantee. The 32 us floor is the first whole-microsecond value that
  // produces a non-zero timer tick on every supported duty-cycle radio.
  if (calculated_rx_us < RX_POWERSAVING_MIN_PERIOD_US
      || calculated_sleep_us < RX_POWERSAVING_MIN_PERIOD_US) {
    calculated_rx_us = RX_POWERSAVING_CONTINUOUS_FALLBACK_US;
    calculated_sleep_us = RX_POWERSAVING_CONTINUOUS_FALLBACK_US;
  }

  *rx_us = calculated_rx_us;
  *sleep_us = calculated_sleep_us;
  return true;
}

// MeshCore wire-preamble convention. SF5-SF8 normally use 32 symbols. Try 64
// and then 128 only when every shorter choice fails to support RXPS at any
// level. If none works, retain 32 and fall back to continuous receive. This
// must stay in sync with RadioLibWrapper::preambleLengthForParams().
inline uint8_t rxPowerSavingPreambleForParams(uint8_t sf, float bw) {
  if (sf > 8) return 16;
  if (sf < 5 || bw <= 0.0f) return 32;

  // The validated LoRa bandwidth grid tops out at 500 kHz and contains 125,
  // 250, and 500 kHz at the fast end. Checking the resulting safe boundaries
  // directly is equivalent to searching all ten levels, but keeps this common
  // sender-side decision small enough for the 256 KiB STM32WL targets.
  if (sf >= 7 || bw <= 125.0f || (sf == 6 && bw <= 250.0f)) return 32;
  if (sf == 6 || bw <= 250.0f) return 64;
  return 128;
}

// Treat requested_level as the operator's minimum. Faster tuples shorten each
// LoRa symbol, so progressively more aggressive levels are tried until the
// sleep window is long enough to cover the radio/TCXO transition. The
// configured level itself is not changed; callers persist only the effective
// timings, allowing slower tuples to return to the requested level later.
// effective_level and effective_preamble are 0 when no combination can safely
// duty-cycle and continuous RX is required for this tuple.
inline bool calcRxPowerSavingLevelAtOrAbove(
    uint8_t requested_level, uint8_t sf, float bw, uint8_t preamble,
    uint32_t transition_us, uint32_t *rx_us, uint32_t *sleep_us,
    uint8_t *effective_level = nullptr,
    uint8_t *effective_preamble = nullptr) {
  if (!rx_us || !sleep_us || requested_level < 1 || requested_level > 10
      || sf < 5 || sf > 12 || bw <= 0.0f
      || !isRxPowerSavingPreamble(preamble)) {
    return false;
  }

  // Never derive a receive window from more symbols than are actually sent.
  // Keep the configured/conservative assumption when possible, then use the
  // tuple's real, longer wire preamble only when that is required.
  const uint8_t transmitted_preamble = rxPowerSavingPreambleForParams(sf, bw);
  const uint8_t configured_preamble = preamble < transmitted_preamble
      ? preamble : transmitted_preamble;
  const bool can_try_transmitted = transmitted_preamble > configured_preamble;
  const uint8_t pass_count = can_try_transmitted ? 2 : 1;
  for (uint8_t pass = 0; pass < pass_count; pass++) {
    const uint8_t candidate_preamble = pass == 0
        ? configured_preamble : transmitted_preamble;
    for (uint8_t level = requested_level; level <= 10; level++) {
      const float symbol_us = (1000.0f * (float)(1UL << sf)) / bw;
      const float amount = (float)(level - 1) / 9.0f;
      const float rx_start_symbols = candidate_preamble == 16 ? 12.0f : 16.0f;
      const float sleep_start_symbols = candidate_preamble == 16 ? 2.0f : 15.0f;
      const float rx_edge_symbols = 8.0f;
      const float sleep_edge_symbols = (float)candidate_preamble + 4.25f - 8.0f;
      const float rx_symbols = rx_start_symbols
          + amount * (rx_edge_symbols - rx_start_symbols);
      const float sleep_symbols = sleep_start_symbols
          + amount * (sleep_edge_symbols - sleep_start_symbols);
      const uint32_t candidate_rx_us = ceilPositiveFloat(rx_symbols * symbol_us);
      const uint32_t candidate_sleep_us = (uint32_t)(sleep_symbols * symbol_us);

      if (isValidRxPowerSavingPeriod(candidate_rx_us)
          && isValidRxPowerSavingPeriod(candidate_sleep_us)
          && canStartRxPowerSavingDutyCycle(
              candidate_rx_us, candidate_sleep_us, transition_us)) {
        *rx_us = candidate_rx_us;
        *sleep_us = candidate_sleep_us;
        if (effective_level) *effective_level = level;
        if (effective_preamble) *effective_preamble = candidate_preamble;
        return true;
      }
    }
  }

  *rx_us = RX_POWERSAVING_CONTINUOUS_FALLBACK_US;
  *sleep_us = RX_POWERSAVING_CONTINUOUS_FALLBACK_US;
  if (effective_level) *effective_level = 0;
  if (effective_preamble) *effective_preamble = 0;
  return true;
}

inline void ensureRxPowerSavingDefaults(uint32_t *rx_ps_rx_us, uint32_t *rx_ps_sleep_us) {
  if (!isValidRxPowerSavingPeriod(*rx_ps_rx_us)) {
    *rx_ps_rx_us = RX_POWERSAVING_DEFAULT_RX_US;
  }

  if (!isValidRxPowerSavingPeriod(*rx_ps_sleep_us)) {
    *rx_ps_sleep_us = RX_POWERSAVING_DEFAULT_SLEEP_US;
  }
}

// Recomputes rx_ps_rx_us/rx_ps_sleep_us from the stored level and the current
// radio SF/BW. No-op (returns false) for manual timings (rx_ps_level == 0).
// Lets level-based RX powersaving auto-retune when SF/BW change.
inline bool recalcRxPowerSavingFromLevel(uint8_t level, uint8_t sf, float bw, uint8_t preamble,
                                         uint32_t *rx_ps_rx_us, uint32_t *rx_ps_sleep_us,
                                         uint8_t *effective_level = nullptr,
                                         uint8_t *effective_preamble = nullptr,
                                         uint32_t transition_us = rxPowerSavingDefaultTransitionUs()) {
  if (level < 1 || level > 10) {
    return false; // manual: nothing to recompute
  }

  if (preamble == 0) {
    preamble = rxPowerSavingPreambleForParams(sf, bw);
  }

  uint32_t rx_us, sleep_us;
  if (!calcRxPowerSavingLevelAtOrAbove(
          level, sf, bw, preamble, transition_us, &rx_us, &sleep_us,
          effective_level, effective_preamble)) {
    return false;
  }

  if (!isValidRxPowerSavingPeriod(rx_us) || !isValidRxPowerSavingPeriod(sleep_us)) {
    return false;
  }

  *rx_ps_rx_us = rx_us;
  *rx_ps_sleep_us = sleep_us;

  return true;
}
