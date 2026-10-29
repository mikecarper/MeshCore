#include "CLIReplyText.h"
#include "LedSettings.h"
#include "CheckedSettingsFile.h"
#include <MeshCore.h>
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include <helpers/AtomicFileWriter.h>
#else
#include <helpers/ContactFileTransaction.h>
#endif
#include <stdio.h>
#include <string.h>

namespace mesh { namespace leds {
namespace {
MainBoard* main_board = nullptr;
FILESYSTEM* settings_fs = nullptr;
const char* const path = "/leds_v1";
const char* const backup = "/leds_v1.bak";

const uint8_t images[2][8] = {
    {'L', 'E', 'D', 1, 0, 0xff, 0, 0},
    {'L', 'E', 'D', 1, 1, 0xfe, 0, 0}};

// Return the saved value, or -1 for an absent or invalid image. Comparing the
// two canonical images shares the write format without a second encoder.
int readSetting(const char* name) {
  uint8_t data[8];
  const bool complete = storage::readFixedSettingsFile(settings_fs, name, data, sizeof(data));
  if (!complete || data[4] > 1
      || memcmp(data, images[data[4]], sizeof(data)) != 0) return -1;
  return data[4];
}

bool saveSetting(bool on) {
  if (!settings_fs) return false;
  const uint8_t* data = images[on ? 1 : 0];
#if defined(NRF52_PLATFORM)
  return storage::writeCheckedSettingsFile(settings_fs, path, data, sizeof(images[0]))
      == AtomicFileWriter::Outcome::Committed;
#else
#if defined(STM32_PLATFORM)
  AtomicFileWriter file(settings_fs, path);
#else
  ContactFileTransaction file(settings_fs, path);
#endif
  if (!file || file.write(data, sizeof(images[0])) != sizeof(images[0]) || !file.commit())
    return false;
  return readSetting(path) == int(on);
#endif
}
} // namespace

void beginSettings(MainBoard& board, FILESYSTEM* fs) {
  main_board = &board;
  settings_fs = fs;
  int setting = readSetting(path);
  if (setting < 0 && (setting = readSetting(backup)) >= 0) {
    // Recover the rename gap without discarding the last complete preference.
    if ((!fs->exists(path) || fs->remove(path)) && fs->rename(backup, path)) {
      if (readSetting(path) != setting) settings_fs = nullptr;
    } else {
      settings_fs = nullptr;
    }
  }
  setEnabled(setting != 0);
  board.applyLedPolicy();
  applyStatusPolicy();
}

// Keep the uncommon filesystem transaction out of the large STM32 CLI
// dispatcher, while retaining ordinary inlining choices elsewhere.
#if defined(STM32_PLATFORM)
__attribute__((noinline))
#endif
bool handleCommand(const char* command, char* reply, size_t capacity) {
  if (!command || !reply || capacity == 0) return false;
  const bool get = command[0] == 'g';
  if ((!get && command[0] != 's')
      || strncmp(command + 1, "et leds", 7) != 0) return false;
  const char* value = command + 8;
  if (*value && *value != ' ' && *value != '\t') return false;
  const char* message;
  if (get) {
    message = *value ? MESH_CLI_REPLY_TEXT("Error: usage get leds", "Error: get leds") : enabled() ? "> on" : "> off";
  } else {
    while (*value == ' ' || *value == '\t') ++value;
    const bool on = strcmp(value, "on") == 0;
    if (!on && strcmp(value, "off") != 0) {
      message = MESH_CLI_REPLY_TEXT("Error: usage set leds on|off", "Error: set leds on|off");
    } else if (!main_board || !saveSetting(on)) {
      message = "Error: LED setting save unconfirmed; reboot may differ";
    } else {
      setEnabled(on);
      main_board->applyLedPolicy();
      applyStatusPolicy();
      message = "OK";
    }
  }
  snprintf(reply, capacity, "%s", message);
  return true;
}
} } // namespace mesh::leds
