#include "DisplayPowerSettings.h"
#include "DisplayBuildFlags.h"
#include <stdio.h>

namespace mesh { namespace ui {
namespace {
FILESYSTEM* settings_fs = nullptr;
bool pairing_available = false;
const char* const path = "/display_prefs";
const char* const backup = "/display_prefs.bak";
const char* const temporary = "/display_prefs.tmp";

// Explicit bytes avoid compiler padding and preserve a small fixed format.
void encode(const DisplayPowerPrefs& prefs, uint8_t (&data)[12]) {
  data[0] = 'D'; data[1] = 'P'; data[2] = 1;
  data[3] = uint8_t(prefs.battery.mode);
  data[4] = prefs.battery.seconds & 255; data[5] = prefs.battery.seconds >> 8;
  data[6] = uint8_t(prefs.usb.mode);
  data[7] = prefs.usb.seconds & 255; data[8] = prefs.usb.seconds >> 8;
  data[9] = 0; data[10] = 0; data[11] = 0;
  for (unsigned i = 0; i < 11; ++i) data[11] ^= data[i];
}
bool readPrefs(const char* name, DisplayPowerPrefs& prefs) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  File file(*settings_fs);
  if (!file.open(name, FILE_O_READ)) return false;
#else
  File file = settings_fs->open(name, "r");
  if (!file) return false;
#endif
  uint8_t data[12], expected[12];
  const bool complete = file.size() == sizeof(data)
      && file.read(data, sizeof(data)) == sizeof(data);
  file.close();
  if (!complete) return false;
  prefs.battery.mode = DisplayMode(data[3]);
  prefs.battery.seconds = uint16_t(data[4]) | (uint16_t(data[5]) << 8);
  prefs.usb.mode = DisplayMode(data[6]);
  prefs.usb.seconds = uint16_t(data[7]) | (uint16_t(data[8]) << 8);
  encode(prefs, expected);
  return memcmp(data, expected, sizeof(data)) == 0
      && validDisplayProfile(prefs.battery) && validDisplayProfile(prefs.usb);
}
bool savePrefs(const DisplayPowerPrefs& prefs) {
  if (!settings_fs) return false;
  // Preserve the previous complete file until the replacement is verified.
  settings_fs->remove(temporary);
  if (settings_fs->exists(temporary)) return false;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  File file(*settings_fs);
  if (!file.open(temporary, FILE_O_WRITE)) return false;
#elif defined(RP2040_PLATFORM)
  File file = settings_fs->open(temporary, "w");
  if (!file) return false;
#else
  File file = settings_fs->open(temporary, "w", true);
  if (!file) return false;
#endif
  uint8_t data[12];
  encode(prefs, data);
  const bool written = file.write(data, sizeof(data)) == sizeof(data);
  file.close();
  DisplayPowerPrefs checked;
  if (!written || !readPrefs(temporary, checked)) return false;
  if (settings_fs->exists(backup) && !settings_fs->remove(backup)) return false;
  if (settings_fs->exists(path) && !settings_fs->rename(path, backup)) return false;
  if (!settings_fs->rename(temporary, path)) {
    if (settings_fs->exists(backup)) settings_fs->rename(backup, path);
    return false;
  }
  settings_fs->remove(backup);
  return true;
}
}

bool loadDisplayPowerSettings(FILESYSTEM* fs, bool pairing_supported) {
  settings_fs = fs;
  pairing_available = pairing_supported;
  DisplayPowerPrefs prefs;
  if (pairing_supported) prefs.battery.mode = prefs.usb.mode = DisplayMode::ButtonPairing;
  displayPowerPrefs() = prefs;
  if (!fs) return false;
  bool loaded = readPrefs(path, prefs);
  if (!loaded && readPrefs(backup, prefs)) {
    loaded = true;
    fs->remove(path);
    fs->rename(backup, path);
  }
  if (!loaded) return false;
  if (!pairing_supported) {
    if (displayModeSupportsPairing(prefs.battery.mode)) prefs.battery.mode = DisplayMode::Button;
    if (displayModeSupportsPairing(prefs.usb.mode)) prefs.usb.mode = DisplayMode::Button;
  }
  displayPowerPrefs() = prefs;
  return true;
}
bool displayPairingSupported() { return pairing_available; }

void migrateLegacyDisplayTimeout(uint16_t seconds) {
  if (seconds > 3600) return;
  auto prefs = displayPowerPrefs();
  prefs.battery.mode = prefs.usb.mode = seconds == 0 ? DisplayMode::On : DisplayMode::Automatic;
  if (seconds) prefs.battery.seconds = prefs.usb.seconds = seconds;
  // Keep the old behavior in memory even if flash is temporarily unavailable.
  displayPowerPrefs() = prefs;
  savePrefs(prefs);
}

bool handleDisplayPowerCommand(const char* command, char* reply, size_t reply_size) {
  const bool get = strncmp(command, "get ", 4) == 0;
  const bool set = strncmp(command, "set ", 4) == 0;
  if (!get && !set) return false;
  const char* key = command + 4;
  const char* keys[] = {"display.mode", "display.timeout", "display.usb.mode", "display.usb.timeout"};
  int index = -1;
  for (int i = 0; i < 4; ++i) {
    const size_t len = strlen(keys[i]);
    if (strncmp(key, keys[i], len) == 0 && (key[len] == 0 || key[len] == ' ')) {
      index = i; break;
    }
  }
  if (index < 0) return false;
#ifndef MESHCORE_HAS_REAL_DISPLAY
  snprintf(reply, reply_size, "Error: display unsupported");
  return true;
#else
  auto candidate = displayPowerPrefs();
  auto& profile = index < 2 ? candidate.battery : candidate.usb;
  const char* value = key + strlen(keys[index]);
  if (get) {
    if (*value) snprintf(reply, reply_size, "Error: unexpected argument");
    else if (index & 1) snprintf(reply, reply_size, "> %u", unsigned(profile.seconds));
    else snprintf(reply, reply_size, "> %s", displayModeName(profile.mode));
    return true;
  }
  while (*value == ' ') ++value;
  if (index & 1) {
    uint32_t seconds = 0;
    bool valid = *value != 0;
    for (const char* p = value; *p && valid; ++p) {
      valid = *p >= '0' && *p <= '9';
      if (valid) { seconds = seconds * 10 + (*p - '0'); valid = seconds <= 3600; }
    }
    if (!valid || seconds == 0) {
      snprintf(reply, reply_size, "Error: timeout must be 1-3600 seconds"); return true;
    }
    profile.seconds = uint16_t(seconds);
  } else if (!parseDisplayMode(value, profile.mode)) {
    snprintf(reply, reply_size, "Error: use off|on|button|pairing|button-pairing|automatic"); return true;
  } else if (displayModeSupportsPairing(profile.mode) && !pairing_available) {
    snprintf(reply, reply_size, "Error: BLE pairing display unsupported"); return true;
  }
  if (!savePrefs(candidate)) snprintf(reply, reply_size, "Error: display settings save failed");
  else { displayPowerPrefs() = candidate; snprintf(reply, reply_size, "OK"); }
  return true;
#endif
}

} }
