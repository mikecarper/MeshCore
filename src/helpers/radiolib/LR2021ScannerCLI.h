#pragma once

#if defined(USE_LR2021) && defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
#include <helpers/radiolib/LR2021SafeMode.h>
#include <stdio.h>
#include <string.h>

namespace mesh { namespace lr2021 {
// Companion's control parser uses this same handler for USB and framed CLI.
template <typename Radio>
bool handleScannerCommand(const char* command, char* reply, size_t capacity, Radio& radio) {
  if (!command || !reply || !capacity) return false;
  const bool pram = strncmp(command, "get pramscan", 12) == 0 || strncmp(command, "set pramscan", 12) == 0;
  const bool multi = strncmp(command, "get radio.multisf", 17) == 0 || strncmp(command, "set radio.multisf", 17) == 0;
  const bool safe = strncmp(command, "get radio.safe", 14) == 0 || strncmp(command, "set radio.safe", 14) == 0;
  if (radio.labScannerUncertain() && (pram || multi || safe)
      && strcmp(command, "get radio.safe") && strcmp(command, "set radio.safe on")
      && strcmp(command, "get pramscan") && strcmp(command, "get pramscan.last")
      && strcmp(command, "get pramscan.diag") && strcmp(command, "get pramscan.timing")
      && strcmp(command, "get pramscan.recovery") && strcmp(command, "set pramscan recover")) {
    snprintf(reply, capacity, "ERR - ambiguous RX FIFO; use set pramscan recover");
    return true;
  }
  if (handleSafeModeCommand(command, reply, capacity)) return true;
#if defined(MESH_W12_MULTISF_EXPERIMENT) && MESH_W12_MULTISF_EXPERIMENT
  if (handleMultiSfSettingsCommand(command, reply, capacity)) {
    if (!strcmp(command, "get radio.multisf")) {
      char status[80]; radio.formatW12MultiSf(status, sizeof(status));
      const size_t used = strlen(reply);
      if (used < capacity) snprintf(reply + used, capacity - used, "; %s", status);
    }
    return true;
  }
#endif
  if (!strcmp(command, "get pramscan")) { radio.labScannerStatus(reply, capacity); return true; }
  if (!strcmp(command, "get pramscan.last")) { radio.labScannerLast(reply, capacity); return true; }
  if (!strcmp(command, "get pramscan.diag")) { radio.labScannerDiag(reply, capacity); return true; }
  if (!strcmp(command, "get pramscan.timing")) { radio.labScannerTiming(reply, capacity); return true; }
  if (!strcmp(command, "get pramscan.recovery")) { radio.labScannerRecoveryDiag(reply, capacity); return true; }
  if (!strcmp(command, "set pramscan on")) {
    snprintf(reply, capacity, safeMode() ? "ERR - radio.safe on; set radio.safe off first"
        : radio.labScannerSetAutomaticManual(true) ? "OK - PRAM auto on; check get pramscan"
        : "ERR - ambiguous FIFO; recover first");
    return true;
  }
  if (!strcmp(command, "set pramscan off")) {
    snprintf(reply, capacity, radio.labScannerSetAutomaticManual(false)
        ? "OK - PRAM auto off until reboot; radio restored"
        : "ERR - scanner busy or restore failed; retry");
    return true;
  }
  if (!strcmp(command, "set pramscan recover")) {
    snprintf(reply, capacity, radio.labScannerRecover()
        ? "OK - recovered; ambiguous FIFO discarded" : "ERR - no recoverable ambiguous FIFO or reset failed");
    return true;
  }
  return false;
}
} }
#endif
