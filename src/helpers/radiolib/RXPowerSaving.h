#pragma once
#include <Arduino.h>

#define RX_POWERSAVING_DEFAULT_RX_US      65625UL
#define RX_POWERSAVING_DEFAULT_SLEEP_US   60000UL
#define RX_POWERSAVING_MIN_PERIOD_US      1000UL
#define RX_POWERSAVING_MAX_PERIOD_US      30000000UL

// A level-derived timing pair equal to this value means the requested LoRa
// tuple is too fast for a safe duty cycle. Keep the operator's RXPS preference
// enabled, but receive continuously until a later SF/BW change produces usable
// timings. Using an in-range pair lets the existing radio-parameter API carry
// the fallback without persisting invalid sub-millisecond periods.
#define RX_POWERSAVING_CONTINUOUS_FALLBACK_US RX_POWERSAVING_MIN_PERIOD_US

// The named profiles are level presets pinned to a 16-symbol preamble: most
// deployed senders still transmit 16-symbol preambles regardless of the newer
// SF-based rule (32 for SF <= 8). Revisit once the field has largely migrated.
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

// MeshCore preamble convention used for the RX powersaving timing calculation.
// Must stay in sync with RadioLibWrapper::preambleLengthForSF() (the value the
// radio actually transmits); kept local here because CommonCLI is radio-agnostic.
inline uint8_t rxPowerSavingPreambleForSF(uint8_t sf) {
  return sf <= 8 ? 32 : 16;
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
      || bw <= 0.0f || (preamble != 16 && preamble != 32)) {
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

  // At high-rate settings (notably SF5/BW500), the entire preamble can be
  // shorter than the radio's sleep/wake transition. Scaling either period up
  // would break the preamble-overlap guarantee and silently lose packets.
  // Canonicalize that case to continuous RX instead; the wrapper will resume
  // a real duty cycle automatically when slower parameters are restored.
  if (calculated_rx_us < RX_POWERSAVING_MIN_PERIOD_US
      || calculated_sleep_us < RX_POWERSAVING_MIN_PERIOD_US) {
    calculated_rx_us = RX_POWERSAVING_CONTINUOUS_FALLBACK_US;
    calculated_sleep_us = RX_POWERSAVING_CONTINUOUS_FALLBACK_US;
  }

  *rx_us = calculated_rx_us;
  *sleep_us = calculated_sleep_us;
  return true;
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
      || (preamble != 16 && preamble != 32)) {
    return false;
  }

  // Keep the configured/legacy preamble assumption if any level can satisfy
  // it. For SF5-SF8 the radio already transmits 32 symbols, so a second pass
  // may use that real preamble length when 16 symbols cannot cover the TCXO
  // transition at a faster tuple.
  const bool can_try_32 = preamble == 16
      && rxPowerSavingPreambleForSF(sf) == 32;
  const uint8_t pass_count = can_try_32 ? 2 : 1;
  for (uint8_t pass = 0; pass < pass_count; pass++) {
    const uint8_t candidate_preamble = pass == 0 ? preamble : 32;
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
    preamble = rxPowerSavingPreambleForSF(sf);
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
