#pragma once

#include <Dispatcher.h>
#include <helpers/CLICommandUtils.h>

namespace mesh {

// Volatile maintenance commands shared by every role using RadioProfileCLI.
inline bool handleCarrierWaveCommand(Radio* radio, const char* command,
                                     char* reply, size_t capacity) {
  const char* text = command;
  bool query = false;
  if (!strncmp(text, "get ", 4)) { text += 4; query = true; }
  else if (!strncmp(text, "set ", 4)) text += 4;
  const size_t length = strcspn(text, " \t");
  const bool secondary = length == 3 && !strncmp(text, "cw2", 3);
  if (!secondary && !(length == 2 && !strncmp(text, "cw", 2))) return false;
  const char* name = secondary ? "cw2" : "cw";
  const char* args = cli::skipRecentRepeaterSpaces(text + length);
  if (!radio || !radio->supportsCarrierWave()) {
    snprintf(reply, capacity, "Error: CW unsupported on this radio");
    return true;
  }
  if (!*args) {
    if (radio->isCarrierWaveActive()) {
      const uint32_t remaining = radio->carrierWaveRemainingMillis();
      snprintf(reply, capacity, "> %s on; %lu.%03lu seconds left",
          radio->carrierWaveProfile() ? "cw2" : "cw",
          (unsigned long)(remaining / 1000), (unsigned long)(remaining % 1000));
    } else snprintf(reply, capacity, "> off");
    return true;
  }
  uint32_t duration = 0;
  const size_t action_length = strcspn(args, " \t");
  const char* seconds = cli::skipRecentRepeaterSpaces(args + action_length);
  const bool on = action_length == 2 && !strncmp(args, "on", 2);
  const bool off = action_length == 3 && !strncmp(args, "off", 3);
  bool valid = !query && (on || (off && !*seconds));
  if (on) {
    duration = Radio::CarrierWaveDefaultMillis;
    if (*seconds) {
      float value;
      valid = valid && cli::parseDecimalStrict(seconds, value)
          && value >= 0.001f && value <= Radio::CarrierWaveMaxMillis / 1000.0f;
      if (valid) duration = static_cast<uint32_t>(value * 1000.0f + 0.5f);
    }
  }
  if (!valid) {
    snprintf(reply, capacity, "Error: use %s on [seconds 0.001-60] | %s off", name, name);
    return true;
  }
  if (on && secondary && (!radio->profiles() || !radio->profiles()->canTransmit(1))) {
    snprintf(reply, capacity, "Error: cw2 requires active radio2 rxtx");
    return true;
  }
  const auto result = radio->setCarrierWave(secondary ? 1 : 0, duration);
  if (result == RadioParamApplyResult::BUSY) {
    snprintf(reply, capacity, "Error: radio busy; stop active CW or retry when idle");
  } else if (result != RadioParamApplyResult::APPLIED) {
    snprintf(reply, capacity, "Error: CW transition failed; check radio recovery");
  } else if (on) {
    snprintf(reply, capacity, "OK - %s on for %lu.%03lu seconds; RX paused", name,
        (unsigned long)(duration / 1000), (unsigned long)(duration % 1000));
  } else snprintf(reply, capacity, "OK - carrier off%s",
      radio->isInRecvMode() ? ", receive resumed" : "");
  return true;
}

}  // namespace mesh
