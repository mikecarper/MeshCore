#include "RadioDirectionSettings.h"

#if MESH_ENABLE_RADIO_DIRECTION
#include "CheckedSettingsFile.h"
#include "ContactFileTransaction.h"
#include "FilePresence.h"
#include <stdio.h>
#include <string.h>

namespace mesh { namespace radioDirection {
namespace {
constexpr char Path[] = "/radio_mode";
constexpr size_t ImageSize = 10;
FILESYSTEM* settings_fs = nullptr;
// Companions do not load this infrastructure setting. Preserve their existing
// behavior until an infrastructure role explicitly initializes the record.
Mode selected = Mode::Both;
bool valid = true;
bool writable = false;

uint32_t checksum(const uint8_t* data, size_t size) {
  uint32_t crc = 0xffffffffU;
  while (size--) {
    crc ^= *data++;
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
  }
  return ~crc;
}

void encode(uint8_t image[ImageSize], Mode value) {
  memcpy(image, "RDM\1", 4);
  image[4] = static_cast<uint8_t>(value);
  image[5] = image[4] ^ 0xff;
  const uint32_t crc = checksum(image, 6);
  for (uint8_t i = 0; i < 4; ++i) image[6 + i] = crc >> (i * 8);
}

bool decode(const uint8_t image[ImageSize], Mode& value) {
  if (memcmp(image, "RDM\1", 4) != 0 || image[4] > 3 ||
      image[5] != (image[4] ^ 0xff)) return false;
  const uint32_t saved_crc = uint32_t(image[6]) | (uint32_t(image[7]) << 8) |
      (uint32_t(image[8]) << 16) | (uint32_t(image[9]) << 24);
  if (saved_crc != checksum(image, 6)) return false;
  value = static_cast<Mode>(image[4]);
  return true;
}

void quarantine() {
  selected = Mode::Off;
  valid = false;
  writable = false;
}

bool saveSetting(Mode value) {
  if (!settings_fs || !writable) return false;
  uint8_t image[ImageSize];
  encode(image, value);
  ContactFileTransaction file(settings_fs, Path, filePresence<FILESYSTEM>);
  using Outcome = ContactFileTransaction::Outcome;
  if (!file || file.write(image, sizeof(image)) != sizeof(image)) {
    if (file.outcome() == Outcome::Indeterminate) quarantine();
    return false;
  }
  file.commit();
  if (file.outcome() == Outcome::NotCommitted) return false;
  if (file.outcome() != Outcome::Committed) {
    quarantine();
    return false;
  }
  uint8_t verified[ImageSize];
  if (!storage::readFixedSettingsFile(settings_fs, Path, verified, sizeof(verified)) ||
      memcmp(verified, image, sizeof(image)) != 0) {
    quarantine();
    return false;
  }
  selected = value;
  valid = true;
  return true;
}

const char* modeName(Mode value) {
  switch (value) {
    case Mode::Both: return "both";
    case Mode::Rx: return "rx";
    case Mode::Tx: return "tx";
    case Mode::Off: return "off";
  }
  return "off";
}
} // namespace

Mode mode() { return selected; }

void loadDirectionSettings(FILESYSTEM* fs) {
  settings_fs = fs;
  quarantine();
  if (!fs || !ContactFileTransaction::recover(fs, Path, filePresence<FILESYSTEM>)) return;
  bool present = false;
  if (!filePresence(fs, Path, present)) return;
  if (!present) {
    selected = Mode::Both;
    valid = writable = true;
    return;
  }
  uint8_t image[ImageSize];
  const auto read = storage::readFixedSettingsFileResult(fs, Path, image, sizeof(image));
  if (read == storage::FixedSettingsRead::Unreadable) return;
  // A known malformed record permits explicit repair, while uncertainty must
  // be reclassified before another write can replace the saved authority.
  writable = true;
  if (read == storage::FixedSettingsRead::Complete) valid = decode(image, selected);
}

bool handleDirectionCommand(const char* command, char* reply, size_t capacity) {
  if (!command || !reply || !capacity) return false;
  const bool get = strncmp(command, "get radio.mode", 14) == 0 &&
      (command[14] == 0 || command[14] == ' ' || command[14] == '\t');
  const bool set = strncmp(command, "set radio.mode", 14) == 0 &&
      (command[14] == 0 || command[14] == ' ' || command[14] == '\t');
  if (!get && !set) return false;
  if (get) {
    if (command[14] != 0)
      snprintf(reply, capacity, "Error: use get radio.mode");
    else if (!valid)
      snprintf(reply, capacity, "Error: radio off; mode settings invalid or unreadable");
    else
      snprintf(reply, capacity, "> %s", modeName(selected));
    return true;
  }
  const char* value = command + 14;
  while (*value == ' ' || *value == '\t') ++value;
  Mode candidate;
  if (strcmp(value, "both") == 0) candidate = Mode::Both;
  else if (strcmp(value, "rx") == 0) candidate = Mode::Rx;
  else if (strcmp(value, "tx") == 0) candidate = Mode::Tx;
  else if (strcmp(value, "off") == 0) candidate = Mode::Off;
  else {
    snprintf(reply, capacity, "Error: use set radio.mode both|rx|tx|off");
    return true;
  }
  if (!writable) {
    snprintf(reply, capacity, "Error: radio mode storage unavailable; reboot first");
  } else if (!saveSetting(candidate)) {
    snprintf(reply, capacity, "Error: radio mode not saved%s",
             writable ? "" : "; storage uncertain, radio off until reload");
  } else {
    snprintf(reply, capacity, "OK - radio.mode %s", value);
  }
  return true;
}

}} // namespace mesh::radioDirection
#endif
