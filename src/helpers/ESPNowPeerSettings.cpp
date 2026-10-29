#include "ESPNowPeerSettings.h"

#if defined(WITH_ESPNOW_BRIDGE)
#include "CheckedSettingsFile.h"
#include "ContactFileTransaction.h"
#include "FilePresence.h"
#include <stdio.h>
#include <string.h>

namespace mesh { namespace bridge {
namespace {
constexpr char Path[] = "/espnow_peer";
constexpr size_t ImageSize = 14;
FILESYSTEM* settings_fs = nullptr;
uint8_t selected[6] = {255, 255, 255, 255, 255, 255};
bool healthy = false, writable = false;

bool broadcast(const uint8_t* address) {
  for (unsigned i = 0; i < 6; ++i) if (address[i] != 255) return false;
  return true;
}

bool valid(const uint8_t* address) {
  if (!address) return false;
  if (broadcast(address)) return true;
  uint8_t any = 0;
  for (unsigned i = 0; i < 6; ++i) any |= address[i];
  return any && !(address[0] & 1);
}

uint32_t checksum(const uint8_t* bytes, size_t size) {
  uint32_t crc = 0xffffffffU;
  while (size--) {
    crc ^= *bytes++;
    for (unsigned bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
  }
  return ~crc;
}

void encode(uint8_t image[ImageSize], const uint8_t* address) {
  memcpy(image, "ENP\1", 4);
  memcpy(image + 4, address, 6);
  const uint32_t crc = checksum(image, 10);
  for (unsigned i = 0; i < 4; ++i) image[10 + i] = crc >> (8 * i);
}

bool decode(const uint8_t image[ImageSize], uint8_t* address) {
  if (memcmp(image, "ENP\1", 4) || !valid(image + 4)) return false;
  uint32_t saved = 0;
  for (unsigned i = 0; i < 4; ++i) saved |= uint32_t(image[10 + i]) << (8 * i);
  if (saved != checksum(image, 10)) return false;
  memcpy(address, image + 4, 6);
  return true;
}

int hex(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}
} // namespace

void loadEspNowPeerSettings(FILESYSTEM* fs) {
  settings_fs = fs;
  healthy = writable = false;
  if (!fs || !ContactFileTransaction::recover(fs, Path, filePresence<FILESYSTEM>)) return;
  bool present = false;
  if (!filePresence(fs, Path, present)) return;
  if (!present) {
    memset(selected, 255, sizeof(selected));
    healthy = writable = true;
    return;
  }
  uint8_t image[ImageSize];
  const auto read = storage::readFixedSettingsFileResult(fs, Path, image, sizeof(image));
  if (read == storage::FixedSettingsRead::Unreadable) return;
  writable = true; // A classified malformed record permits an explicit repair.
  healthy = read == storage::FixedSettingsRead::Complete && decode(image, selected);
}

bool espNowPeerSettingsHealthy() { return healthy; }

bool copyEspNowPeer(uint8_t destination[6]) {
  if (!destination || !healthy) return false;
  memcpy(destination, selected, 6);
  return true;
}

bool parseEspNowPeer(const char* text, uint8_t destination[6]) {
  if (!text || !destination) return false;
  uint8_t candidate[6];
  if (!strcmp(text, "broadcast")) memset(candidate, 255, sizeof(candidate));
  else {
    if (strlen(text) != 17) return false;
    for (unsigned i = 0; i < 6; ++i) {
      const int hi = hex(text[3 * i]), lo = hex(text[3 * i + 1]);
      if (hi < 0 || lo < 0 || (i < 5 && text[3 * i + 2] != ':')) return false;
      candidate[i] = (hi << 4) | lo;
    }
    if (!valid(candidate) || broadcast(candidate)) return false;
  }
  memcpy(destination, candidate, sizeof(candidate));
  return true;
}

bool saveEspNowPeer(const uint8_t destination[6]) {
  if (!settings_fs || !writable || !valid(destination)) return false;
  if (healthy && !memcmp(selected, destination, 6)) return true;
  uint8_t image[ImageSize];
  encode(image, destination);
  ContactFileTransaction file(settings_fs, Path, filePresence<FILESYSTEM>);
  using Outcome = ContactFileTransaction::Outcome;
  if (!file || file.write(image, sizeof(image)) != sizeof(image)) {
    if (file.outcome() == Outcome::Indeterminate) healthy = writable = false;
    return false;
  }
  file.commit();
  if (file.outcome() == Outcome::NotCommitted) return false;
  uint8_t verified[ImageSize];
  if (file.outcome() != Outcome::Committed ||
      !storage::readFixedSettingsFile(settings_fs, Path, verified, sizeof(verified)) ||
      memcmp(verified, image, sizeof(image))) {
    healthy = writable = false;
    return false;
  }
  memcpy(selected, destination, sizeof(selected));
  healthy = true;
  return true;
}

void formatEspNowPeer(char* reply, size_t capacity) {
  if (!reply || !capacity) return;
  if (!healthy) snprintf(reply, capacity, "Error: ESP-NOW peer settings unavailable; bridge blocked");
  else if (broadcast(selected)) snprintf(reply, capacity, "> broadcast");
  else snprintf(reply, capacity, "> %02X:%02X:%02X:%02X:%02X:%02X", selected[0], selected[1],
                selected[2], selected[3], selected[4], selected[5]);
}

}} // namespace mesh::bridge
#endif
