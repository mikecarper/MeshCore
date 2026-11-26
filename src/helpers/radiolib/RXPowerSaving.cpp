#include "RXPowerSaving.h"
#include <math.h>

bool normalizeRxPowerSavingConfig(RxPowerSavingConfig* config, uint8_t sf, float bw,
                                  float capture_cost_symbols, uint32_t transition_us) {
  if (config == nullptr || sf < 5 || sf > 12 || !isfinite(bw) || bw <= 0
      || bw > 1000000.0f || !(capture_cost_symbols > 0.0f)
      || !isfinite(capture_cost_symbols) || transition_us >= RX_POWERSAVING_MAX_PERIOD_US) return false;
  RxPowerSavingConfig proposed = *config;
  proposed.enabled = proposed.enabled ? 1 : 0;
  if (proposed.level > RX_POWERSAVING_MAX_CONFIG_LEVEL) {
    proposed.level = RX_POWERSAVING_BALANCED_LEVEL;
  }
  if (proposed.preamble != 0 && !isRxPowerSavingPreamble(proposed.preamble)) {
    proposed.preamble = RX_POWERSAVING_PROFILE_PREAMBLE;
  }
  if (proposed.level == 0) {
    if (proposed.rx_us < RX_POWERSAVING_MIN_MANUAL_PERIOD_US ||
        proposed.sleep_us < RX_POWERSAVING_MIN_MANUAL_PERIOD_US ||
        !isValidRxPowerSavingPeriod(proposed.rx_us) ||
        !isValidRxPowerSavingPeriod(proposed.sleep_us)) return false;
  } else if (!recalcRxPowerSavingFromLevel(proposed.level, sf, bw, proposed.preamble,
                                         &proposed.rx_us, &proposed.sleep_us,
                                         nullptr, nullptr, transition_us, capture_cost_symbols)) {
    return false;
  }
  *config = proposed;
  return true;
}

bool applyRxPowerSavingConfig(RxPowerSavingConfig* config, uint8_t sf, float bw,
                              RxPowerSavingControl* control) {
  if (config == nullptr) return false;
  const bool valid = !config->enabled || normalizeRxPowerSavingConfig(
      config, sf, bw, rxPowerSavingCaptureCost(control), rxPowerSavingTransition(control));
  if (control == nullptr) return !config->enabled;
  const bool accepted = control->setRxPowerSaving(
      config->enabled && valid, config->rx_us, config->sleep_us);
  control->setRxPowerSavingConfigError(!valid);
  return valid && accepted;
}
