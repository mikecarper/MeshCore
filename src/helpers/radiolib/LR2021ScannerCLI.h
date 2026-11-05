#pragma once

#if defined(USE_LR2021) && defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
#include <helpers/radiolib/LR2021SafeMode.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

namespace mesh { namespace lr2021 {
// Only explicitly named scanner controls are accepted by the framed CLI.
inline bool isScannerFrameCommandAllowed(const uint8_t* bytes, size_t length) {
  if (!bytes || !length || length > 48) return false;
  char command[49];
  for (size_t i = 0; i < length; ++i) {
    if (bytes[i] < 0x20 || bytes[i] > 0x7e) return false;
    command[i] = static_cast<char>(bytes[i]);
  }
  command[length] = 0;
  static const char* const exact[] = {
    "get radio.safe", "set radio.safe on", "set radio.safe off", "get radio.multisf",
    "set radio.multisf on", "set radio.multisf off", "set radio.multisf radio",
    "set radio.multisf radio1", "set radio.multisf radio2", "get pramscan",
    "get pramscan.last", "get pramscan.diag", "get pramscan.timing",
    "get pramscan.recovery", "set pramscan on", "set pramscan off", "set pramscan recover"
  };
  for (const char* allowed : exact) if (!strcmp(command, allowed)) return true;
  static const char range[] = "set radio.multisf ";
  if (strncmp(command, range, sizeof(range)-1)) return false;
  unsigned first=0, last=0; int used=0;
  const char* value = command + sizeof(range)-1;
  return sscanf(value, "%u-%u%n", &first, &last, &used) == 2 && !value[used]
      && first >= 5 && last <= 10 && first <= last && last-first <= 3;
}

// Companion's control parser uses this handler for local CLI requests.
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
