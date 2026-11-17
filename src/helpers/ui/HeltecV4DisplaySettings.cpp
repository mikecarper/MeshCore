#if defined(HELTEC_V4_DISPLAY_UNIFIED) && HELTEC_V4_DISPLAY_UNIFIED

#include "HeltecV4DisplaySettings.h"
#include <helpers/ui/HeltecV4GpioPolicy.h>
#include <helpers/CheckedSettingsFile.h>
#include <helpers/FilePresence.h>
#include <helpers/RuntimePinClaims.h>
#include <stdio.h>
#include <string.h>

namespace mesh { namespace ui {
namespace {
FILESYSTEM* settings_fs = nullptr;
bool saved_mode = false, boot_fault = false, writes_frozen = false;
HeltecV4DisplayMode requested_mode = HeltecV4DisplayMode::Auto;
constexpr char path[] = "/display_type";
constexpr char backup[] = "/display_type.bak";
constexpr char temporary[] = "/display_type.tmp";

void encode(HeltecV4DisplayMode mode, uint8_t (&data)[6]) {
  data[0] = 'D'; data[1] = 'T'; data[2] = 1;
  data[3] = uint8_t(mode); data[4] = 0; data[5] = 0;
  for (unsigned i = 0; i < 5; ++i) data[5] ^= data[i];
}

bool read(const char* name, HeltecV4DisplayMode& mode) {
  uint8_t data[6], expected[6];
  if (!storage::readFixedSettingsFile(settings_fs, name, data, sizeof(data))
      || data[3] > uint8_t(HeltecV4DisplayMode::Off)) return false;
  const auto candidate = HeltecV4DisplayMode(data[3]);
  encode(candidate, expected);
  if (memcmp(data, expected, sizeof(data))) return false;
  mode = candidate; return true;
}

bool recover() {
  HeltecV4DisplayMode mode;
  if (read(path, mode) || !read(backup, mode)) return true;
  if (settings_fs->exists(path) && !settings_fs->remove(path)) return false;
  return settings_fs->rename(backup, path) && read(path, mode);
}

bool save(HeltecV4DisplayMode mode) {
  if (!settings_fs || writes_frozen || !recover()) return false;
  if (settings_fs->exists(temporary) && !settings_fs->remove(temporary)) return false;
  if (settings_fs->exists(temporary)) return false;
  File file = settings_fs->open(temporary, "w", true);
  if (!file) return false;
  uint8_t data[6]; encode(mode, data);
  const bool written = file.write(data, sizeof(data)) == sizeof(data);
  file.close();
  HeltecV4DisplayMode checked;
  if (!written || !read(temporary, checked) || checked != mode) return false;
  if (settings_fs->exists(backup) && !settings_fs->remove(backup)) return false;
  if (settings_fs->exists(path) && !settings_fs->rename(path, backup)) return false;
  if (!settings_fs->rename(temporary, path)) {
    if (settings_fs->exists(backup) && !settings_fs->exists(path)
        && !settings_fs->rename(backup, path)) writes_frozen = true;
    return false;
  }
  if (!read(path, checked) || checked != mode) { writes_frozen = true; return false; }
  if (settings_fs->exists(backup)) settings_fs->remove(backup);
  return true;
}

bool accessoryPinsClaimed() {
  const uint64_t accessories = pins::pinMask(33) | pins::pinMask(44)
      | pins::pinMask(47) | pins::pinMask(48);
  for (unsigned owner = 0; owner < 4; ++owner)
    if (pins::held(pins::Owner(owner)) & accessories) return true;
  return false;
}

const char* modeName(HeltecV4DisplayMode mode) { return heltecV4DisplayModeName(mode); }

bool pendingReboot() {
  if (boot_fault) return true;
#if MESH_CUSTOM_PERIPHERALS
  return heltecV4OledExpansionActive()
      ? requested_mode != HeltecV4DisplayMode::Oled
      : requested_mode == HeltecV4DisplayMode::Oled;
#else
  return false;
#endif
}

} // namespace

bool loadHeltecV4DisplaySettings(FILESYSTEM* fs, HeltecV4Display& display) {
  settings_fs = fs;
  saved_mode = writes_frozen = false;
  boot_fault = fs == nullptr;
  requested_mode = HeltecV4DisplayMode::Auto;
  if (fs) {
    bool present = false, backup_present = false;
    const bool known = filePresence(fs, path, present) && filePresence(fs, backup, backup_present);
    if (!known || ((present || backup_present)
        && (!recover() || !read(path, requested_mode)))) boot_fault = true;
    else saved_mode = present || backup_present;
  }
  // Only an explicit verified OLED preference releases accessory GPIO.
  // Missing settings retain Auto. Unreadable or damaged existing settings
  // stay headless so TFT probes cannot toggle previously wired accessories.
  setHeltecV4OledExpansionAtBoot(!boot_fault && saved_mode
      && requested_mode == HeltecV4DisplayMode::Oled);
  display.setConfigurationFault(boot_fault);
  display.setMode(boot_fault ? HeltecV4DisplayMode::Off : requested_mode);
  return saved_mode && !boot_fault;
}

bool handleHeltecV4DisplayCommand(const char* command, char* reply,
                                 size_t reply_size, HeltecV4Display& display) {
  if (!command || !reply || !reply_size) return false;
  const bool get = strncmp(command, "get ", 4) == 0;
  if (!get && strncmp(command, "set ", 4)) return false;
  constexpr char key[] = "display.type";
  constexpr size_t length = sizeof(key) - 1;
  if (strncmp(command + 4, key, length)
      || (command[4 + length] && command[4 + length] != ' ')) return false;
  const char* value = command + 4 + length;
  if (get) {
    if (*value) snprintf(reply, reply_size, "Error: unexpected argument");
    else snprintf(reply, reply_size, "display.type %s (active %s%s)%s",
        modeName(requested_mode), display.status(), pendingReboot() ? "; reboot required" : "",
        boot_fault ? "; saved config rejected" : "");
    return true;
  }
  while (*value == ' ') ++value;
  HeltecV4DisplayMode mode;
  if (!parseHeltecV4DisplayMode(value, mode)) {
    snprintf(reply, reply_size, "Error: use set display.type <auto|oled|tft|off>"); return true;
  }
  const bool oled_boot = heltecV4OledExpansionActive();
  if (oled_boot && mode != HeltecV4DisplayMode::Oled && accessoryPinsClaimed()) {
    snprintf(reply, reply_size, "Error: release GPIO33/44/47/48 accessories before leaving OLED mode"); return true;
  }
  if ((!saved_mode || requested_mode != mode) && !save(mode)) {
    snprintf(reply, reply_size, "Error: display type save failed or unconfirmed; active pins unchanged; reboot may differ"); return true;
  }
  saved_mode = true; requested_mode = mode;
  setHeltecV4OledExpansionAllocation(mode == HeltecV4DisplayMode::Oled);
  // A boot that released accessory pins may not reinitialize TFT/touch
  // through a live command. A repaired damaged preference is reboot-only too.
  if (!boot_fault && (!oled_boot || mode == HeltecV4DisplayMode::Oled)) display.setMode(mode);
  snprintf(reply, reply_size, "OK - display.type %s (active %s%s)",
      modeName(mode), display.status(), pendingReboot() ? "; reboot required" : "");
  return true;
}

}} // namespace mesh::ui
#endif // HELTEC_V4_DISPLAY_UNIFIED
