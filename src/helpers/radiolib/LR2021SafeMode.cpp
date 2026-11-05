#include "LR2021SafeMode.h"

#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
#include <helpers/CheckedSettingsFile.h>
#include <helpers/FilePresence.h>
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
#include <helpers/ContactFileTransaction.h>
#endif
#include <stdio.h>
#include <string.h>

namespace mesh { namespace lr2021 {
namespace {
constexpr char Path[] = "/radio_safe";
constexpr size_t Size = 10;
FILESYSTEM* fs = nullptr;
bool safe = true, valid = false, writable = false;

uint32_t checksum(const uint8_t* data, size_t size) {
  uint32_t crc=0xffffffffU;
  while (size--) {
    crc ^= *data++;
    for (uint8_t bit=0;bit<8;++bit) crc=(crc>>1)^((crc&1)?0xedb88320U:0);
  }
  return ~crc;
}

bool decode(const uint8_t* b, bool& value) {
  if (memcmp(b, "RSM1", 4) || b[4] > 1 || b[5] != uint8_t(b[4] ^ 0xff)
      || (uint32_t(b[6]) | uint32_t(b[7])<<8 | uint32_t(b[8])<<16 | uint32_t(b[9])<<24) != checksum(b, 6)) return false;
  value = b[4] != 0;
  return true;
}

bool save(bool value) {
  if (!fs || !writable) return false;
  uint8_t bytes[Size] = {'R', 'S', 'M', '1', uint8_t(value), uint8_t(uint8_t(value) ^ 0xff)};
  const uint32_t crc=checksum(bytes, 6);
  for (size_t i=0; i<4; ++i) bytes[6+i]=uint8_t(crc>>(i*8));
#if defined(NRF52_PLATFORM)
  const auto outcome = storage::writeCheckedSettingsFile(fs, Path, bytes, Size);
  if (outcome == AtomicFileWriter::Outcome::NotCommitted) return false;
  if (outcome != AtomicFileWriter::Outcome::Committed) {
    safe=true; valid=writable=false; return false;
  }
#elif defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  ContactFileTransaction file(fs, Path, filePresence<FILESYSTEM>);
  using Outcome = ContactFileTransaction::Outcome;
  if (!file || file.write(bytes, Size) != Size) {
    if (file.outcome() == Outcome::Indeterminate) { safe=true; valid=writable=false; }
    return false;
  }
  file.commit();
  if (file.outcome() == Outcome::NotCommitted) return false;
  if (file.outcome() != Outcome::Committed) { safe=true; valid=writable=false; return false; }
  uint8_t verified[Size];
  if (!storage::readFixedSettingsFile(fs, Path, verified, Size) || memcmp(bytes, verified, Size)) {
    safe=true; valid=writable=false; return false;
  }
#else
  return false;
#endif
  safe=value; valid=true;
  return true;
}
}

bool safeMode() { return safe; }

void loadSafeModeSettings(FILESYSTEM* store) {
  fs=store; safe=true; valid=writable=false;
  if (!fs) return;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!ContactFileTransaction::recover(fs, Path, filePresence<FILESYSTEM>)) return;
#endif
  bool present=false;
  if (!filePresence(fs, Path, present)) return;
  if (!present) { safe=false; valid=writable=true; return; }
  uint8_t bytes[Size];
  const auto result=storage::readFixedSettingsFileResult(fs, Path, bytes, Size);
  if (result == storage::FixedSettingsRead::Unreadable) return;
  writable=true;
  bool value=true;
  if (result == storage::FixedSettingsRead::Complete && decode(bytes, value)) {
    safe=value; valid=true;
  }
}

bool handleSafeModeCommand(const char* command, char* reply, size_t capacity) {
  if (!command || !reply || !capacity) return false;
  if (!strcmp(command, "get radio.safe")) {
    snprintf(reply, capacity, valid ? "> %s" : "> on (settings unavailable)", safe ? "on" : "off");
    return true;
  }
  if (strncmp(command, "set radio.safe", 14) || (command[14] && command[14] != ' ' && command[14] != '\t')) return false;
  const char* value=command+14;
  while (*value==' ' || *value=='\t') ++value;
  if (strcmp(value,"on") && strcmp(value,"off")) {
    snprintf(reply,capacity,"Error: use set radio.safe on|off"); return true;
  }
  const bool requested=!strcmp(value,"on");
  if (!save(requested)) snprintf(reply,capacity,"Error: radio.safe not saved%s", safe ? "; safe mode on" : "");
  else snprintf(reply,capacity,"OK - radio.safe %s",value);
  return true;
}
} }
#endif
