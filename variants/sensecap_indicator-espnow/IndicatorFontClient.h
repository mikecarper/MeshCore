#pragma once

#include <stddef.h>
#include <stdint.h>

class IndicatorFontClient {
public:
  static uint8_t* load(size_t& size);
#ifdef INDICATOR_WIFI_FONT_RECOVERY
  // Poll from the Arduino loop. Network repair starts only after station Wi-Fi
  // is connected; post-commit local UART probes can run offline. Bounded clock
  // sync plus the TLS/download/SD transaction run in a worker task.
  // A returned PSRAM buffer is the newly recovered font and transfers ownership
  // to LGFXDisplay::installRuntimeFont().
  static uint8_t* serviceRecovery(size_t& size);
  static void noteRuntimeFontInstalled();
  // Use for a font loaded during ordinary startup validation.  This begins a
  // fresh per-boot recovery budget.
  static void noteRuntimeFontInvalid();
  // Use only for a freshly recovered font returned by serviceRecovery().  A
  // rejected live activation consumes the current attempt instead of
  // resetting the per-boot retry budget.
  static void noteRecoveredFontInvalid();
#endif
};
