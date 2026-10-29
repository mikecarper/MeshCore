#include "BridgeIngressSettings.h"

#if MESH_ENABLE_BRIDGE_INGRESS
#include "CheckedSettingsFile.h"
#include "FilePresence.h"
#include "BridgeSourceRoutes.h"
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include "AtomicFileWriter.h"
#else
#include "ContactFileTransaction.h"
#endif
#include <stdio.h>
#include <string.h>

namespace mesh { namespace bridge {
namespace {
constexpr char Path[] = "/bridge_ingress";
constexpr size_t ImageSize = 10;
FILESYSTEM* settings_fs = nullptr;
IngressProfile selected = IngressProfile::Blocked;
bool writable = false;
constexpr char RoutesPath[] = "/bridge_routes";
IngressRule routes[MaxIngressRoutes];
bool routes_healthy = false;
bool routes_writable = false;

uint32_t checksum(const uint8_t* data, size_t size) {
  uint32_t crc = 0xffffffffU;
  while (size--) {
    crc ^= *data++;
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
  }
  return ~crc;
}

void encode(uint8_t image[ImageSize], IngressProfile profile) {
  memcpy(image, profile == IngressProfile::Off ? "BIN\2" : "BIN\1", 4);
  image[4] = static_cast<uint8_t>(profile);
  image[5] = image[4] ^ 0xff;
  const uint32_t crc = checksum(image, 6);
  for (uint8_t i = 0; i < 4; ++i) image[6 + i] = crc >> (i * 8);
}

bool decode(const uint8_t image[ImageSize], IngressProfile& profile) {
  const bool v1 = memcmp(image, "BIN\1", 4) == 0;
  const bool v2 = memcmp(image, "BIN\2", 4) == 0;
  if ((!v1 && !v2) || (image[4] > 1 && !(v2 && image[4] == 3)) ||
      image[5] != (image[4] ^ 0xff)) return false;
  const uint32_t saved_crc = uint32_t(image[6]) | (uint32_t(image[7]) << 8) |
      (uint32_t(image[8]) << 16) | (uint32_t(image[9]) << 24);
  if (saved_crc != checksum(image, 6)) return false;
  profile = static_cast<IngressProfile>(image[4]);
  return true;
}

void quarantine() {
  selected = IngressProfile::Blocked;
  writable = false;
}

bool saveSetting(IngressProfile profile) {
  if (!settings_fs || !writable) return false;
  uint8_t image[ImageSize];
  encode(image, profile);
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  AtomicFileWriter file(settings_fs, Path);
#else
  ContactFileTransaction file(settings_fs, Path, filePresence<FILESYSTEM>);
#endif
  using Outcome = decltype(file.outcome());
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
  selected = profile;
  return true;
}

void loadRoutes(FILESYSTEM* fs) {
  for (auto& rule : routes) rule = IngressRule();
  routes_healthy = routes_writable = false;
  if (!fs) return;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!ContactFileTransaction::recover(fs, RoutesPath, filePresence<FILESYSTEM>)) return;
#endif
  bool present = false;
  if (!filePresence(fs, RoutesPath, present)) return;
  if (!present) { routes_healthy = routes_writable = true; return; }
  uint8_t image[RoutesImageSize];
  const auto read = storage::readFixedSettingsFileResult(fs, RoutesPath, image, sizeof(image));
  if (read == storage::FixedSettingsRead::Unreadable) return;
  routes_writable = true;
  routes_healthy = read == storage::FixedSettingsRead::Complete && decodeIngressRules(image, routes);
}

bool saveRoutes(const IngressRule* candidate) {
  if (!settings_fs || !routes_writable || !validIngressRules(candidate)) return false;
  uint8_t image[RoutesImageSize];
  encodeIngressRules(image, candidate);
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  AtomicFileWriter file(settings_fs, RoutesPath);
#else
  ContactFileTransaction file(settings_fs, RoutesPath, filePresence<FILESYSTEM>);
#endif
  using Outcome = decltype(file.outcome());
  if (!file || file.write(image, sizeof(image)) != sizeof(image)) {
    if (file.outcome() == Outcome::Indeterminate) routes_healthy = routes_writable = false;
    return false;
  }
  file.commit();
  if (file.outcome() == Outcome::NotCommitted) return false;
  uint8_t verified[RoutesImageSize];
  if (file.outcome() != Outcome::Committed ||
      !storage::readFixedSettingsFile(settings_fs, RoutesPath, verified, sizeof(verified)) ||
      memcmp(verified, image, sizeof(image)) != 0) {
    routes_healthy = routes_writable = false;
    return false;
  }
  memcpy(routes, candidate, sizeof(routes));
  routes_healthy = true;
  return true;
}

bool handleRouteCommand(const char* command, char* reply, size_t capacity) {
  const bool get = !strncmp(command, "get bridge.route", 16);
  const bool set = !strncmp(command, "set bridge.route", 16);
  const bool del = !strncmp(command, "del bridge.route", 16);
  const bool clear = !strcmp(command, "clear bridge.routes");
  if (!clear && ((!get && !set && !del) ||
      (command[16] && command[16] != '.' && command[16] != ' ' && command[16] != '\t'))) return false;
  if (!routes_writable) {
    snprintf(reply, capacity, "Error: bridge route storage unavailable; reboot first");
    return true;
  }
  IngressRule candidate[MaxIngressRoutes];
  memcpy(candidate, routes, sizeof(candidate));
  if (clear) {
    for (auto& rule : candidate) rule = IngressRule();
  } else {
    if (!routes_healthy) {
      snprintf(reply, capacity, "Error: bridge routes invalid; use clear bridge.routes");
      return true;
    }
    if (get && !command[16]) {
      unsigned count = 0;
      for (const auto& rule : routes) if (rule.source.kind != IngressSource::Kind::None) ++count;
      snprintf(reply, capacity, "> %u/8 routes; get bridge.route.1..8; default=%s", count,
               ingressProfileName(selected));
      return true;
    }
    const char* cursor = command + 16;
    if (*cursor != '.' || cursor[1] < '1' || cursor[1] > '8' ||
        (cursor[2] && cursor[2] != ' ' && cursor[2] != '\t')) {
      snprintf(reply, capacity, "Error: use get/set/del bridge.route.1..8");
      return true;
    }
    const unsigned index = cursor[1] - '1';
    cursor += 2;
    if (get && !*cursor) { formatIngressRule(index + 1, routes[index], reply, capacity); return true; }
    if (del && !*cursor) candidate[index] = IngressRule();
    else if (set && (*cursor == ' ' || *cursor == '\t')) {
      while (*cursor == ' ' || *cursor == '\t') ++cursor;
      char source_text[40];
      size_t length = 0;
      while (*cursor && *cursor != ' ' && *cursor != '\t' && length + 1 < sizeof(source_text))
        source_text[length++] = *cursor++;
      source_text[length] = 0;
      while (*cursor == ' ' || *cursor == '\t') ++cursor;
      IngressRule rule;
      if (!parseIngressSource(source_text, rule.source) || !parseIngressProfile(cursor, rule.profile)) {
        snprintf(reply, capacity, "Error: use set bridge.route.N mac:AA:BB:CC:DD:EE:FF|udp:IPv4:port radio|radio2|off");
        return true;
      }
      candidate[index] = rule;
      if (!validIngressRules(candidate)) {
        snprintf(reply, capacity, "Error: duplicate bridge route source");
        return true;
      }
    } else {
      snprintf(reply, capacity, "Error: use get/set/del bridge.route.1..8");
      return true;
    }
  }
  if (!saveRoutes(candidate))
    snprintf(reply, capacity, "Error: bridge routes not saved%s", routes_writable ? "" : "; ingress blocked until reboot");
  else snprintf(reply, capacity, clear ? "OK - bridge routes cleared" : "OK - bridge route saved");
  return true;
}
} // namespace

IngressProfile ingressProfile() { return routes_healthy ? selected : IngressProfile::Blocked; }

IngressRoute ingressRoute(const IngressSource* source) {
  if (!routes_healthy || selected == IngressProfile::Blocked) return {IngressProfile::Blocked, false};
  if (source && source->kind != IngressSource::Kind::None)
    for (const auto& rule : routes)
      if (sameSource(*source, rule.source)) return {rule.profile, true};
  return {selected, false};
}

void loadIngressSettings(FILESYSTEM* fs) {
  settings_fs = fs;
  quarantine();
  loadRoutes(fs);
  if (!fs) return;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!ContactFileTransaction::recover(fs, Path, filePresence<FILESYSTEM>)) return;
#endif
  bool present = false;
  if (!filePresence(fs, Path, present)) return;
  if (!present) {
    selected = IngressProfile::Radio;
    writable = true;
    return;
  }
  uint8_t image[ImageSize];
  const auto read = storage::readFixedSettingsFileResult(fs, Path, image, sizeof(image));
  if (read == storage::FixedSettingsRead::Unreadable) return;
  // A known malformed record can be explicitly repaired. Until then it must
  // not silently turn a saved radio2-only ingress into primary-radio traffic.
  writable = true;
  if (read == storage::FixedSettingsRead::Complete) decode(image, selected);
}

bool handleIngressCommand(const char* command, char* reply, size_t capacity) {
  if (!command || !reply || !capacity) return false;
  if (handleRouteCommand(command, reply, capacity)) return true;
  const bool get = strncmp(command, "get bridge.ingress", 18) == 0 &&
      (command[18] == 0 || command[18] == ' ' || command[18] == '\t');
  const bool set = strncmp(command, "set bridge.ingress", 18) == 0 &&
      (command[18] == 0 || command[18] == ' ' || command[18] == '\t');
  if (!get && !set) return false;
  if (get) {
    if (command[18] != 0)
      snprintf(reply, capacity, "Error: use get bridge.ingress");
    else if (ingressProfile() == IngressProfile::Blocked)
      snprintf(reply, capacity, "Error: bridge ingress blocked; settings invalid or unreadable");
    else
      snprintf(reply, capacity, "> %s", ingressProfileName(selected));
    return true;
  }
  const char* value = command + 18;
  while (*value == ' ' || *value == '\t') ++value;
  IngressProfile profile;
  if (!parseIngressProfile(value, profile)) {
    snprintf(reply, capacity, "Error: use set bridge.ingress radio|radio2|off");
    return true;
  }
  if (!writable) {
    snprintf(reply, capacity, "Error: bridge ingress storage unavailable; reboot first");
  } else if (!saveSetting(profile)) {
    snprintf(reply, capacity, "Error: bridge ingress not saved%s",
             writable ? "" : "; storage uncertain, ingress blocked until reload");
  } else {
    snprintf(reply, capacity, "OK - bridge.ingress %s", value);
  }
  return true;
}

}} // namespace mesh::bridge
#endif
