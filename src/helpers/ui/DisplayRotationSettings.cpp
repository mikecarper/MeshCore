#include "DisplayRotationSettings.h"

#include <stdio.h>
#include <string.h>

namespace mesh { namespace ui {
namespace {
FILESYSTEM* settings_fs = nullptr;
uint16_t degrees = 0;
const char* const path = "/display_rotation";
const char* const backup = "/display_rotation.bak";
const char* const temporary = "/display_rotation.tmp";

void encode(uint16_t value, uint8_t (&data)[6]) {
  data[0] = 'D'; data[1] = 'R'; data[2] = 1;
  data[3] = value & 255; data[4] = value >> 8;
  data[5] = 0;
  for (unsigned i = 0; i < 5; ++i) data[5] ^= data[i];
}

bool valid(uint16_t value) { return value <= 270 && value % 90 == 0; }

bool readPrefs(const char* name, uint16_t& value) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  File file(*settings_fs);
  if (!file.open(name, FILE_O_READ)) return false;
#else
  File file = settings_fs->open(name, "r");
  if (!file) return false;
#endif
  uint8_t data[6], expected[6];
  const bool complete = file.size() == sizeof(data)
      && file.read(data, sizeof(data)) == sizeof(data);
  file.close();
  if (!complete) return false;
  value = uint16_t(data[3]) | (uint16_t(data[4]) << 8);
  encode(value, expected);
  return valid(value) && memcmp(data, expected, sizeof(data)) == 0;
}

bool savePrefs(uint16_t value) {
  if (!settings_fs) return false;
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
  uint8_t data[6];
  encode(value, data);
  const bool written = file.write(data, sizeof(data)) == sizeof(data);
  file.close();
  uint16_t checked = 0;
  if (!written || !readPrefs(temporary, checked) || checked != value) return false;
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

bool loadDisplayRotationSettings(FILESYSTEM* fs) {
  settings_fs = fs;
  degrees = 0;
  if (!fs) return false;
  uint16_t loaded = 0;
  bool found = readPrefs(path, loaded);
  if (!found && readPrefs(backup, loaded)) {
    found = true;
    fs->remove(path);
    fs->rename(backup, path);
  }
  if (found) degrees = loaded;
  return found;
}

uint16_t displayRotationDegrees() { return degrees; }

bool handleDisplayRotationCommand(const char* command, char* reply,
                                  size_t reply_size, bool supported) {
  const bool get = strncmp(command, "get ", 4) == 0;
  const bool set = strncmp(command, "set ", 4) == 0;
  if (!get && !set) return false;
  const char* key = command + 4;
  constexpr size_t key_length = sizeof("display.rotation") - 1;
  if (strncmp(key, "display.rotation", key_length) != 0
      || (key[key_length] != 0 && key[key_length] != ' ')) return false;
  if (!supported) {
    snprintf(reply, reply_size, "Error: display rotation unsupported");
    return true;
  }
  const char* value = key + key_length;
  if (get) {
    if (*value) snprintf(reply, reply_size, "Error: unexpected argument");
    else if (degrees == 0) snprintf(reply, reply_size, "display.rotation default");
    else snprintf(reply, reply_size, "display.rotation %u", unsigned(degrees));
    return true;
  }
  while (*value == ' ') ++value;
  unsigned candidate = 0;
  bool parsed = *value != 0;
  for (const char* p = value; *p && parsed; ++p) {
    parsed = *p >= '0' && *p <= '9';
    if (parsed) {
      candidate = candidate * 10 + unsigned(*p - '0');
      parsed = candidate <= 270;
    }
  }
  if (!parsed || !valid(candidate)) {
    snprintf(reply, reply_size, "Error: use set display.rotation <0|90|180|270>");
  } else if (!savePrefs(uint16_t(candidate))) {
    snprintf(reply, reply_size, "Error: display rotation save failed");
  } else {
    degrees = uint16_t(candidate);
    if (degrees == 0) snprintf(reply, reply_size, "OK - display rotation reset to board default");
    else snprintf(reply, reply_size, "OK - display rotation %u", unsigned(degrees));
  }
  return true;
}

}}  // namespace mesh::ui
