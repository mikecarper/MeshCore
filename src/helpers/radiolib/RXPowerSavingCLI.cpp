#include "RXPowerSavingCLI.h"
#include "../CLICommandUtils.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

static const char* rxPowerSavingLevelTag(uint8_t level) {
  if (isRxPowerSavingRiskyWorkingMaxLevel(level)) return "(riskyWorkingMax)";
  if (isRxPowerSavingOverdriveLevel(level)) return "(overdrive)";
  return "";
}

bool RXPowerSavingCLI::set(const char* value, uint8_t sf, float bw,
    RxPowerSavingConfig* config, RxPowerSavingControl* control,
    char* reply, size_t reply_size) {
  if (!config || !reply || !reply_size) return false;
  mesh::cli::RxPowerSavingArguments args = {};
  if (!mesh::cli::parseRxPowerSavingArgumentsStrict(value, args)) {
    snprintf(reply, reply_size, "ERROR: use off|on|conservative|balanced|max|overdrive|riskyWorkingMax|level <1-10>|<rx_us> <sleep_us>");
    return false;
  }
  RxPowerSavingConfig proposed = *config;
  using Mode = mesh::cli::RxPowerSavingArgumentMode;
  if (args.mode == Mode::Off) {
    proposed.enabled = 0;
  } else if (args.mode == Mode::Manual) {
    proposed.enabled = 1;
    proposed.level = 0;
    proposed.preamble = 0;
    proposed.rx_us = args.rx_us;
    proposed.sleep_us = args.sleep_us;
  } else {
    proposed.enabled = 1;
    proposed.level = args.mode == Mode::Conservative ? RX_POWERSAVING_CONSERVATIVE_LEVEL
        : args.mode == Mode::Balanced ? RX_POWERSAVING_BALANCED_LEVEL : args.level;
    const uint32_t preamble = args.mode == Mode::Level ? args.preamble : RX_POWERSAVING_PROFILE_PREAMBLE;
    if (args.level > RX_POWERSAVING_RISKY_WORKING_MAX_LEVEL ||
        proposed.level < 1 || proposed.level > RX_POWERSAVING_RISKY_WORKING_MAX_LEVEL ||
        (preamble != 0 && (preamble > 128 || !isRxPowerSavingPreamble(preamble)))) {
      snprintf(reply, reply_size, "ERROR: level range is 1-10; preamble is 16,32,64,128");
      return false;
    }
    proposed.preamble = static_cast<uint8_t>(preamble);
  }
  if (proposed.enabled && !normalizeRxPowerSavingConfig(&proposed, sf, bw,
      rxPowerSavingCaptureCost(control), rxPowerSavingTransition(control))) {
    snprintf(reply, reply_size, "ERROR: invalid RXPS timing or SF/BW");
    return false;
  }
  if (control ? !control->setRxPowerSaving(proposed.enabled != 0, proposed.rx_us, proposed.sleep_us)
              : proposed.enabled != 0) {
    snprintf(reply, reply_size, "ERROR: RX powersaving unsupported or radio busy");
    return false;
  }
  if (control) control->setRxPowerSavingConfigError(false);
  *config = proposed;
  snprintf(reply, reply_size, "OK - %s,level=%lu%s,preamble=%lu,rx=%lu,sleep=%lu",
      config->enabled ? "on" : "off", (unsigned long)config->level,
      rxPowerSavingLevelTag(config->level), (unsigned long)config->preamble,
      (unsigned long)config->rx_us, (unsigned long)config->sleep_us);
  return true;
}

void RXPowerSavingCLI::get(const RxPowerSavingConfig* config,
                           const RxPowerSavingControl* control,
                           uint8_t sf, float bw, char* reply, size_t reply_size,
                           uint16_t wire_preamble) {
  if (config == nullptr) return;
  get(*config, control ? control->getRxPowerSavingStatus() : RxPowerSavingStatus{},
      sf, bw, reply, reply_size, wire_preamble);
}

void RXPowerSavingCLI::get(const RxPowerSavingConfig& config,
                           const RxPowerSavingStatus& status,
                           uint8_t sf, float bw, char* reply, size_t reply_size,
                           uint16_t wire_preamble) {
  if (reply == nullptr || reply_size == 0) return;
  // Preamble symbols the node actually catches. Not simply the number the level
  // asked for: the sleep floor can shorten the sleep further, and at SF6 with
  // the 16-symbol profile that collapses the first few levels onto the same
  // point. Reporting the requested value there would be a lie.
  char margin[24];
  margin[0] = 0;
  if (status.supported && status.armed && status.last_error == 0 && isValidRxPowerSavingPeriod(config.sleep_us) &&
      config.level >= 1 && config.level <= RX_POWERSAVING_GUARDED_LEVELS &&
      sf >= 5 && sf <= 12 && bw > 0.0f && bw <= 500.0f) {
    const uint16_t preamble = wire_preamble ? wire_preamble : rxPowerSavingPreambleForParams(sf, bw);
    const float symbol_us = (1000.0f * (float)(1UL << sf)) / bw;
    const float effective = (float)preamble - (float)config.sleep_us / symbol_us;
    // No %f on this platform's printf, and rounding the whole and fractional
    // parts separately overflows: 4.999 came out as "4.10".
    const float scaled = effective * 10.0f + (effective < 0.0f ? -0.5f : 0.5f);
    // Validated 30-second periods, SF5+ and these radio-family bandwidths
    // bound the result well inside int32, including a 65535-symbol override.
    const int tenths = (int)scaled;
    snprintf(margin, sizeof(margin), ",catch=%d.%d", tenths / 10,
             tenths < 0 ? -(tenths % 10) : tenths % 10);
  }

  // Overdrive has to be visible here, otherwise it reads as an ordinary level
  // and nobody remembers it runs outside the datasheet rule.
  int len = snprintf(reply, reply_size,
           "> desired=%s,effective=%s,supported=%s,err=%d,fail=%lu,level=%lu%s%s,preamble=%lu,rx=%lu,sleep=%lu",
           config.enabled ? "on" : "off", status.armed ? "armed"
               : status.receiving ? "continuous" : "inactive",
           status.supported ? "yes" : "no", (int)status.last_error,
           (unsigned long)status.arm_failures, (unsigned long)config.level,
           isRxPowerSavingUnguardedLevel(config.level) ? "(!)" : "", margin,
           (unsigned long)config.preamble, (unsigned long)config.rx_us,
           (unsigned long)config.sleep_us);

  // Runtime holds are distinct from an arm failure. Keep configuration keys
  // stable, and put the bounded recovery countdown in the same status reply.
  if (len > 0 && (size_t)len < reply_size && config.enabled && !status.armed) {
    const char* hold = status.suspended ? "radio2" : status.calibrating ? "calib"
        : status.timing_fallback ? "timing" : nullptr;
    if (hold) {
      snprintf(reply + len, reply_size - (size_t)len, ",hold=%s", hold);
    } else if (status.retry_pending) {
      snprintf(reply + len, reply_size - (size_t)len, ",retry=%lus,streak=%u",
          (unsigned long)((uint64_t(status.retry_delay_ms) + 999UL) / 1000UL),
          (unsigned)status.consecutive_failures);
    }
  }

  // Only report the periods the hardware really runs with when a driver had to
  // clamp them - printing them always would not fit the 160 byte reply buffer.
  if (len > 0 && (size_t)len < reply_size && status.armed &&
      (status.effective_rx_us != config.rx_us ||
       status.effective_sleep_us != config.sleep_us)) {
    snprintf(reply + len, reply_size - (size_t)len, ",erx=%lu,eslp=%lu",
             (unsigned long)status.effective_rx_us,
             (unsigned long)status.effective_sleep_us);
  }
}
