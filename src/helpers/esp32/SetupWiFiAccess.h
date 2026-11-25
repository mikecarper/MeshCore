#pragma once

#include <stdint.h>
#include <string.h>

// Explicit hotspot security applies to one setup session.
enum class SetupWiFiAccess : uint8_t { Configured, Protected, Open };

inline bool parseWebConfigStart(const char* args, bool& force_ap, SetupWiFiAccess& access) {
  access = SetupWiFiAccess::Configured;
  force_ap = args[0] != 0;
  if (!force_ap || strcmp(args, " ap") == 0) return true;
  if (strcmp(args, " ap open") == 0) access = SetupWiFiAccess::Open;
  else if (strcmp(args, " ap wpa2") == 0) access = SetupWiFiAccess::Protected;
  else return false;
  return true;
}
