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

// The level scale is a catch dial, in symbols of the sender's preamble.
// Its empirical constants and measurements below come from PowerSaving-v17;
// those bench results do not establish reception guarantees on every board.
//
// Level N means: "at least this many symbols of the sender's preamble land
// inside an open RX window." That is the quantity the radio actually needs -
// a preamble is latched once enough of it has been heard - so the level states
// the requirement directly instead of stating a distance from an edge.
//
// The arithmetic follows from that in one step. Worst case, a preamble starts
// the instant an RX window closes, so what survives into the next window is
// `preamble - sleep`. Requiring that to be at least the level's catch gives
//
//     sleep = preamble - catch
//
// and the chip's capture cost never enters the geometry. It becomes a
// validation instead: a level is usable when its catch is at least the cost.
// The floor of the scale is 8 symbols precisely because that is what the
// slower of the two families needs (LR11x0 8, SX126x 6), so **every level
// means the same geometry on both radios** - which the previous margin-based
// scale could not do, because the cost sat inside its formula.
//
// The two profiles get their own ladders. On a 16-symbol preamble there are
// only 8 symbols to spend between the floor and the whole preamble, so the
// steps are single symbols; on a 32-symbol preamble there are 24, so the low
// levels can take much larger strides while retaining a larger capture margin.
static constexpr uint8_t RX_POWERSAVING_GUARDED_LEVELS = 8;
static constexpr float RX_POWERSAVING_LEVEL_CATCH_P16[RX_POWERSAVING_GUARDED_LEVELS] = {
  15.0f, 14.0f, 13.0f, 12.0f, 11.0f, 10.0f, 9.0f, 8.0f
};
static constexpr float RX_POWERSAVING_LEVEL_CATCH_P32[RX_POWERSAVING_GUARDED_LEVELS] = {
  24.0f, 20.0f, 16.0f, 14.0f, 12.0f, 10.0f, 9.0f, 8.0f
};

// Smallest catch any guarded level asks for, and the reason it is 8: the
// LR11x0 needs 8 symbols to latch where the SX126x needs 6.
static constexpr float RX_POWERSAVING_MIN_CATCH_SYMBOLS = 8.0f;

// On top of the ladder, every guarded level sleeps this much less than the
// arithmetic demands - so it catches 0.2 symbols more than its level promises.
// The reason is not capture safety: it is that specific register pairs the
// calculation lands on are defective (preamble latched, header never
// validated) in the same way isolated rxPeriod ticks 320 and 640 are. Nudging
// every generated geometry off those points cost 0.05 mA and took the worst
// measured cell from 3.33% packet loss to 0.11% across the whole ladder.
// Levels 9 and 10 are exempt - their geometries are pinned to bench
// measurements and shifting them would invalidate that data.
static constexpr float RX_POWERSAVING_MARGIN_PAD_SYMBOLS = 0.2f;

// Past the guarded scale sit two settings that break the datasheet's
// SetRxDutyCycle timer condition on purpose. Upstream bench measurements
// reported lower duty cycle for overdrive and packet loss at riskyWorkingMax.
// Neither measurement establishes a guarantee for other boards or RF links.
static constexpr uint8_t RX_POWERSAVING_OVERDRIVE_LEVEL = 9;
static constexpr uint8_t RX_POWERSAVING_RISKY_WORKING_MAX_LEVEL = 10;
static constexpr float RX_POWERSAVING_RISKY_WORKING_MAX_VIRTUAL_P32 = 11.0f;
static constexpr float RX_POWERSAVING_RISKY_WORKING_MAX_VIRTUAL_P16 = 10.25f;

// Top of the guarded scale: catches the ladder floor of 8 symbols (8.2 with
// the pad), the cheapest setting that still meets the datasheet condition and
// still works on both radio families. The CLI calls it `max`.
static constexpr uint8_t RX_POWERSAVING_MAX_LEVEL = RX_POWERSAVING_GUARDED_LEVELS;

#define RX_POWERSAVING_CONSERVATIVE_LEVEL 3 // catches 13 (P16) / 16 (P32)
#define RX_POWERSAVING_BALANCED_LEVEL 6 // catches 10 (P16) / 10 (P32)
#define RX_POWERSAVING_PROFILE_PREAMBLE 16
static constexpr uint8_t RX_POWERSAVING_MAX_CONSEC_ARM_FAILURES = 3;

// Symbols of a sender's preamble consumed before a packet can be latched:
// ~2 to latch the preamble plus ~2 lost to RX-window startup, measured on
// SX1262 at SF8/BW62.5. Capture needs sleep <= sender_preamble - this.
// Note this is well below the 8 symbols RadioLib quotes (SX126x.h) - that
// figure is the datasheet's margin for *reliable* latching, not the floor.
//
// Chip dependent, so it belongs with the driver rather than with each board:
// the build selects the family capture cost. On the upstream bench, LR1110
// held 300/300 at a margin of 7.78 symbols and started dropping packets at
// 6.89, for both profiles, where SX1262 is clean down to 6.
static constexpr float RX_POWERSAVING_CAPTURE_COST_SYMBOLS = 6.0f;
static constexpr float RX_POWERSAVING_CAPTURE_COST_SYMBOLS_LR11X0 = 8.0f;

// TCXO startup delay handed to SetDIO3AsTCXOCtrl (SX126x DS 13.3.6) /
// SetTcxoMode (LR11x0 UM 6.3.2). RadioLib defaults it to 5000 us and nothing
// overrode it, so every board paid 5 ms on every duty-cycle wake whatever
// crystal was fitted. Measured lowest working value on five modules: T096 400,
// Tracker V2 300, Waveshare 150, ThinkNode M3 200, T1000-E 200 us - so 1600
// keeps 4x margin over the worst of them.
//
// One knob for the whole tree, in `arduino_base`, because it is a property of
// the parts MeshCore is built with rather than of any one board. A variant that
// needs its own value defines it in its own build_flags and the later -D wins.
#ifndef MC_TCXO_DELAY_US
  #define MC_TCXO_DELAY_US 1600
#endif

// Sleep -> RX transition: RadioLib subtracts tcxoDelay + 1000 us from the
// requested sleep before writing the register, and the hardware spends that
// long saving context, restarting the XTAL and locking the PLL. Everything
// below derives from it. Callers can supply the resolved transition delay so
// both the sleep floor and the timer guard follow it.
static constexpr uint32_t RX_POWERSAVING_TRANSITION_US = MC_TCXO_DELAY_US + 1000;

// Slack the timer guard leaves on top of the datasheet condition, in symbols.
//
// Closing the condition exactly is not enough. The calculator lands on register
// pairs that satisfy it by a hair and still lose packets: rx=2476/sleep=2455
// ticks clears it by 22 us at 0.12 symbol and drops 2-6% on every SX1262 tried,
// while moving any one component clears the fault. It is the same family as the
// isolated bad ticks 320 and 640 - the condition does not predict which pairs
// misbehave, so the answer is to stop generating geometries that sit on its
// edge. One whole symbol of slack is what the bench evidence supports.
#ifndef MC_RXPS_TIMER_SLACK_SYMBOLS
  #define MC_RXPS_TIMER_SLACK_SYMBOLS 1.0f
#endif
static constexpr float RX_POWERSAVING_TIMER_SLACK_SYMBOLS = MC_RXPS_TIMER_SLACK_SYMBOLS;

// SX126x programs its duty cycle in these units and truncates the
// microsecond value on the way in. Everything the timer guard compares has to
// be expressed in whole ticks or it comes out short by a fraction of one.
static constexpr float RX_POWERSAVING_TICK_US = 15.625f;

// Margin above the transition before a duty cycle actually works; see below.
static constexpr uint32_t RX_POWERSAVING_MIN_SLEEP_MARGIN_US = 250;

// Symbols between the end of the sender's preamble and the end of the header:
// the LoRa sync word plus the 8-symbol explicit header. SF5/SF6 use a longer
// sync word than SF7 and above. Used by the SetRxDutyCycle timer guard.
static constexpr float RX_POWERSAVING_SYNC_SYMBOLS_LOW_SF = 6.25f;
static constexpr float RX_POWERSAVING_SYNC_SYMBOLS = 4.25f;
static constexpr float RX_POWERSAVING_HEADER_SYMBOLS = 8.0f;

// Shortest sleep that actually produces a working duty cycle.
//
// RadioLib subtracts tcxoDelay + 1000 us from the requested sleep before
// writing the register. At or below that the register underflows and arming
// fails with RADIOLIB_ERR_INVALID_SLEEP_PERIOD (-708), after which the wrapper
// falls back to continuous RX while the config still says power saving is on.
// Measured on SX1262: the P16 profile fails to arm on levels 1-5 at SF6 and on
// levels 1-2 at SF7, exactly where that bound predicts.
//
// But arming successfully is NOT the boundary. Just above it the SX1262 accepts
// the command, returns ERR_NONE, and then detects **zero** preambles - a
// silently deaf receiver, which is worse than the honest -708. Measured twice,
// on two different SF and RX windows: sleep 6050-6100 us is dead, 6200 us and
// everything above it is lossless (40/40 at every value up to 21617 us). The
// chip needs roughly 200 us of *programmed* sleep, i.e. ~12 register ticks, so
// this leaves 250 us of margin on top of the transition time.
//
// The default follows MC_TCXO_DELAY_US; a caller can supply another transition.
static constexpr uint32_t RX_POWERSAVING_MIN_SLEEP_US =
    RX_POWERSAVING_TRANSITION_US + RX_POWERSAVING_MIN_SLEEP_MARGIN_US;


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
  return SX126X_DIO3_TCXO_VOLTAGE > 0.0f ? MC_TCXO_DELAY_US + 1000UL : 1000UL;
#elif defined(LR11X0_DIO3_TCXO_VOLTAGE)
  return LR11X0_DIO3_TCXO_VOLTAGE > 0.0f ? MC_TCXO_DELAY_US + 1000UL : 1000UL;
#else
  return MC_TCXO_DELAY_US + 1000UL;
#endif
}

inline float rxPowerSavingDefaultCaptureCost() {
#ifdef USE_LR1110
  return RX_POWERSAVING_CAPTURE_COST_SYMBOLS_LR11X0;
#else
  return RX_POWERSAVING_CAPTURE_COST_SYMBOLS;
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

inline uint32_t ceilRxPowerSavingValue(float value) {
  uint32_t rounded = (uint32_t)value;
  return value > (float)rounded ? rounded + 1 : rounded;
}

// SX126x programs its duty cycle in 15.625 us ticks and truncates the
// microsecond value on the way in, so a period of 10013 us is really 10000 us
// on air while the CLI happily reports 10013. Snapping to the nearest tick and
// returning the smallest microsecond value that still lands on it makes the
// reported periods the ones the hardware runs - which matters here, because the
// register value turned out to be what a duty-cycle defect keys on, not the
// microseconds we asked for.
inline uint32_t rxPowerSavingTickToUs(uint64_t tick) {
  if (tick == 0) tick = 1;
  return (uint32_t)((tick * 125 + 7) / 8);            // ceil(tick * 15.625)
}

inline uint32_t snapRxPowerSavingToTick(uint32_t us) {
  return rxPowerSavingTickToUs(((uint64_t)us * 8 + 62) / 125);   // nearest
}

// Rounding the listen window to the *nearest* tick can shave a few microseconds
// off, and the timer guard below has no room to give: it must hold with the
// register values the radio actually runs. So that one rounds up.
inline uint32_t snapRxPowerSavingUpToTick(uint32_t us) {
  return rxPowerSavingTickToUs(((uint64_t)us * 8 + 124) / 125);  // ceil
}

inline float rxPowerSavingLevelCatch(uint8_t level, uint8_t preamble) {
  if (level < 1 || level > RX_POWERSAVING_GUARDED_LEVELS) return 0.0f;
  return preamble >= 32 ? RX_POWERSAVING_LEVEL_CATCH_P32[level - 1]
                        : RX_POWERSAVING_LEVEL_CATCH_P16[level - 1];
}

inline bool isRxPowerSavingOverdriveLevel(uint8_t level) {
  return level == RX_POWERSAVING_OVERDRIVE_LEVEL;
}

inline bool isRxPowerSavingRiskyWorkingMaxLevel(uint8_t level) {
  return level == RX_POWERSAVING_RISKY_WORKING_MAX_LEVEL;
}

// Both levels past the guarded scale run outside the datasheet's timer
// condition. Worth surfacing wherever a level is reported.
inline bool isRxPowerSavingUnguardedLevel(uint8_t level) {
  return level >= RX_POWERSAVING_OVERDRIVE_LEVEL;
}

// rxPeriod register values measured to break SetRxDutyCycle on SX1262: the
// radio latches the preamble and then never validates a header, losing 35% of
// packets at SF6 and all of them at SF7. Both were reproduced on two boards
// with an LR1110 witnessing 100% of the same transmissions, and both bands are
// exactly one tick wide - 639 and 641 are lossless.
//
// This list is certainly incomplete: about thirty values were sampled out of
// the thousands a profile can generate. Levels 1-8 do not need it because the
// timer guard makes any value safe; it exists only for the unguarded maximum,
// where it is the one protection left.
static const uint32_t RX_POWERSAVING_BAD_RX_TICKS[] = {320, 640};

inline uint32_t avoidBadRxPowerSavingTick(uint32_t rx_us) {
  const uint32_t tick = (rx_us * 8) / 125;
  for (uint32_t bad : RX_POWERSAVING_BAD_RX_TICKS) {
    // One tick longer costs 15.625 us of listening and steps clear of the band.
    if (tick == bad) return (uint32_t)((((uint64_t)tick + 1) * 125 + 7) / 8);
  }
  return rx_us;
}

inline bool calcRxPowerSavingLevel(uint8_t level, uint8_t sf, float bw, uint8_t preamble,
                            uint32_t* rx_us, uint32_t* sleep_us,
                            float capture_cost_symbols = rxPowerSavingDefaultCaptureCost(),
                            uint32_t transition_us = rxPowerSavingDefaultTransitionUs()) {
  if (rx_us == nullptr || sleep_us == nullptr || level < 1 ||
      level > RX_POWERSAVING_RISKY_WORKING_MAX_LEVEL || sf < 5 || sf > 12 ||
      !(bw > 0.0f) || bw > 1000000.0f || !isRxPowerSavingPreamble(preamble)) {
    return false;
  }
  const bool unguarded = isRxPowerSavingUnguardedLevel(level);

  const float symbol_us = (1000.0f * (float)(1UL << sf)) / bw;
  // Worst case a preamble starts the instant an RX window closes, so the part
  // that survives into the next window is `preamble - sleep`. The absolute
  // limit is therefore reached when that equals the chip's capture cost; the
  // unguarded levels are pinned to geometries measured at or past that point,
  // which is why they still need it.
  const float sleep_edge_symbols = (float)preamble - capture_cost_symbols;

  float sleep_symbols;
  float rx_symbols = 0.0f;      // only used by the unguarded levels
  if (unguarded) {
    // Kept on the old interpolation because that is the geometry the bench
    // measured: overdrive is the top of the profile, and `riskyWorkingMax` is
    // the point past it where delivery started to fall.
    float amount = 1.0f;
    if (isRxPowerSavingRiskyWorkingMaxLevel(level)) {
      const float virtual_level = preamble == 16 ? RX_POWERSAVING_RISKY_WORKING_MAX_VIRTUAL_P16
                                                 : RX_POWERSAVING_RISKY_WORKING_MAX_VIRTUAL_P32;
      amount = (virtual_level - 1.0f) / 9.0f;
    }
    const float rx_start_symbols = preamble == 16 ? 12.0f : 16.0f;
    const float sleep_start_symbols = preamble == 16 ? 2.0f : 15.0f;
    rx_symbols = rx_start_symbols + amount * (8.0f - rx_start_symbols);
    sleep_symbols = sleep_start_symbols + amount * (sleep_edge_symbols - sleep_start_symbols);
  } else {
    // Guarded levels state their catch directly, so the sleep is just what is
    // left of the preamble - no capture cost involved. The pad makes the node
    // catch a fifth of a symbol more than promised, which is what keeps the
    // generated register pairs off the defective ones.
    const float catch_symbols = rxPowerSavingLevelCatch(level, preamble);
    // A level that undertakes to catch less than this radio needs to latch is
    // not usable: say so rather than return a geometry that cannot work.
    if (catch_symbols < capture_cost_symbols) return false;
    sleep_symbols = (float)preamble - catch_symbols - RX_POWERSAVING_MARGIN_PAD_SYMBOLS;
    if (sleep_symbols < 0.0f) sleep_symbols = 0.0f;
  }

  *sleep_us = (uint32_t)(sleep_symbols * symbol_us);

  // Below the hardware floor the duty cycle cannot be armed at all: the driver
  // returns -708 and the wrapper quietly runs continuous RX, so the node keeps
  // reporting power saving while spending full RX current. Just above that the
  // SX1262 arms with ERR_NONE and then detects no preambles at all, which is
  // worse. Measured floor: 6050-6100 us dead, 6200 us and up lossless.
  const uint32_t min_sleep_us = transition_us + RX_POWERSAVING_MIN_SLEEP_MARGIN_US;
  if (*sleep_us < min_sleep_us) {
    if (!unguarded || (float)min_sleep_us > sleep_edge_symbols * symbol_us) {
      return false;
    }
    *sleep_us = min_sleep_us;
  }
  *sleep_us = transition_us + snapRxPowerSavingToTick(*sleep_us - transition_us);

  // SetRxDutyCycle timer guard, straight out of the SX1261/2 datasheet: on
  // preamble detection the radio restarts its timer with 2*rxPeriod +
  // sleepPeriod (register values), and the packet dies if the header does not
  // arrive inside it. The datasheet states the requirement as
  //     Tpreamble + Theader <= 2 * rxPeriod + sleepPeriod
  // Measured: when it holds, no period value causes trouble. When it is broken
  // the SX1262 usually gets away with it - but not always, and the exceptions
  // are a single register tick wide. rxPeriod 640 (exactly 10.000 ms) loses
  // 60-65% of packets at SF6 and everything at SF7, on two different boards,
  // while 639 and 641 are lossless; rxPeriod 320 behaves the same way. Nothing
  // in the register value predicts which ticks misbehave, so satisfy the
  // condition instead of dodging the values. The listen window is therefore not
  // a free parameter on the guarded scale - it is whatever the condition needs.
  const float sync_symbols =
      sf <= 6 ? RX_POWERSAVING_SYNC_SYMBOLS_LOW_SF : RX_POWERSAVING_SYNC_SYMBOLS;
  const float need_us =
      ((float)preamble + sync_symbols + RX_POWERSAVING_HEADER_SYMBOLS +
       RX_POWERSAVING_TIMER_SLACK_SYMBOLS) * symbol_us;
  // Work in the radio's own units: the driver truncates (sleep - transition) to
  // 15.625 us ticks, so the microsecond difference overstates the programmed
  // sleep by up to one tick - enough to leave the condition short on every
  // level of a profile.
  const uint32_t sleep_ticks = ((*sleep_us - transition_us) * 8) / 125;
  const float programmed_sleep_us = (float)sleep_ticks * RX_POWERSAVING_TICK_US;

  if (unguarded) {
    *rx_us = ceilRxPowerSavingValue(rx_symbols * symbol_us);
  } else {
    const float min_rx_us = (need_us - programmed_sleep_us) / 2.0f;
    *rx_us = ceilRxPowerSavingValue(min_rx_us > 0.0f ? min_rx_us : symbol_us);
  }

  *rx_us = snapRxPowerSavingUpToTick(*rx_us);
  if (unguarded) *rx_us = avoidBadRxPowerSavingTick(*rx_us);
  return isValidRxPowerSavingPeriod(*rx_us) && isValidRxPowerSavingPeriod(*sleep_us);
}

// Preserve the deployed Cascade wire-preamble convention. The longer choices
// were established with the previous timing model and 6 ms transition budget.
// They remain a network compatibility contract, independent of the new local
// RXPS model, oscillator configuration, or whether RXPS is enabled.
inline uint8_t rxPowerSavingPreambleForParams(uint8_t sf, float bw) {
  if (sf > 8) return 16;
  if (sf < 5 || bw <= 0.0f) return 32;

  // Fixed boundaries keep every sender compatible and avoid a timing-model
  // search in the sender path on small STM32WL targets.
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
      || sf < 5 || sf > 12 || !(bw > 0.0f) || bw > 1000000.0f
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
    // Automatic adjustment never opts an operator into experimental levels.
    const uint8_t last_level = requested_level <= RX_POWERSAVING_MAX_LEVEL
        ? RX_POWERSAVING_MAX_LEVEL : requested_level;
    for (uint8_t level = requested_level; level <= last_level; level++) {
      uint32_t candidate_rx_us = 0, candidate_sleep_us = 0;
      if (!calcRxPowerSavingLevel(level, sf, bw, candidate_preamble,
              &candidate_rx_us, &candidate_sleep_us,
              rxPowerSavingDefaultCaptureCost(), transition_us)) continue;

      // The catch assumption can be conservative, but the receive timeout
      // must still cover the entire preamble our senders actually transmit.
      if (!isRxPowerSavingUnguardedLevel(level)) {
        const float symbol_us = (1000.0f * (float)(1UL << sf)) / bw;
        const float sync = sf <= 6 ? RX_POWERSAVING_SYNC_SYMBOLS_LOW_SF
                                  : RX_POWERSAVING_SYNC_SYMBOLS;
        const float need = (transmitted_preamble + sync
            + RX_POWERSAVING_HEADER_SYMBOLS + RX_POWERSAVING_TIMER_SLACK_SYMBOLS) * symbol_us;
        const uint32_t sleep_ticks = ((candidate_sleep_us - transition_us) * 8UL) / 125UL;
        const uint32_t guarded_rx = snapRxPowerSavingUpToTick(ceilRxPowerSavingValue(
            (need - sleep_ticks * RX_POWERSAVING_TICK_US) / 2.0f));
        if (guarded_rx > candidate_rx_us) candidate_rx_us = guarded_rx;
      }

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
