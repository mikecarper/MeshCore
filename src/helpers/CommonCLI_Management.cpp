#include "CommonCLI.h"
#include "ManagementReporter.h"
#include "FileRead.h"
#include "PersistentStoreFormat.h"
#include <new>
#include <stdlib.h>
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include "AtomicFileWriter.h"
#else
#include "ContactFileTransaction.h"
#endif

namespace mesh {
static constexpr char DATA_ROUTE_FILE[] = "/data_tx";
static constexpr size_t DATA_ROUTE_SIZE = 108;
enum DataRegionMode : uint8_t {
  DATA_REGION_AUTO = 0,
  DATA_REGION_DEFAULT = 1,
  DATA_REGION_NAMED = 2,
  DATA_REGION_NONE = 3,
};

struct DataRouteState {
  FILESYSTEM* fs = nullptr;
  RegionMap* regions = nullptr;
  uint8_t path[MAX_PATH_SIZE] = {};
  uint8_t path_len = 0;  // safe fresh-install default: zero-hop direct
  uint8_t region_mode = DATA_REGION_AUTO;
  char region[31] = {};
  bool persisted = false;
  bool healthy = true;
};

static uint32_t readRoute32(const uint8_t* p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8)
      | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

static void writeRoute32(uint8_t* p, uint32_t value) {
  p[0] = uint8_t(value); p[1] = uint8_t(value >> 8);
  p[2] = uint8_t(value >> 16); p[3] = uint8_t(value >> 24);
}

static uint8_t nibble(char c) {
  if (c >= '0' && c <= '9') return uint8_t(c - '0');
  if (c >= 'a' && c <= 'f') return uint8_t(c - 'a' + 10);
  if (c >= 'A' && c <= 'F') return uint8_t(c - 'A' + 10);
  return 0xff;
}

static char* trimDataRoute(char* text) {
  while (*text == ' ') ++text;
  char* end = text + strlen(text);
  while (end > text && end[-1] == ' ') --end;
  *end = 0;
  return text;
}

static bool parseDataPath(char* raw, uint8_t path[MAX_PATH_SIZE],
                          uint8_t& path_len, const char*& error) {
  char* spec = trimDataRoute(raw);
  memset(path, 0, MAX_PATH_SIZE);
  if (!strcmp(spec, "direct")) { path_len = 0; return true; }
  if (!strcmp(spec, "none") || !strcmp(spec, "clear") || !strcmp(spec, "-")) {
    path_len = OUT_PATH_UNKNOWN; return true;
  }

  // Accept the compact management form (2:12abcd34) as well as the normal
  // telemetry/outpath comma form (12ab,cd34).
  if (spec[0] >= '1' && spec[0] <= '3' && spec[1] == ':') {
    const uint8_t width = uint8_t(spec[0] - '0');
    const char* hex = spec + 2;
    const size_t chars = strlen(hex);
    if (!chars || chars % (width * 2) || chars / 2 > MAX_PATH_SIZE) {
      error = "Err - path must be direct, none, or 1|2|3:hex"; return false;
    }
    const size_t bytes = chars / 2, hops = bytes / width;
    if (!hops || hops > 63) { error = "Err - path too long"; return false; }
    for (size_t i = 0; i < bytes; ++i) {
      const uint8_t hi = nibble(hex[i * 2]), lo = nibble(hex[i * 2 + 1]);
      if (hi > 15 || lo > 15) { error = "Err - invalid path hex"; return false; }
      path[i] = uint8_t((hi << 4) | lo);
    }
    path_len = uint8_t(((width - 1) << 6) | hops);
    return Packet::isValidPathLen(path_len);
  }

  uint8_t width = 0, hops = 0;
  char* token = spec;
  while (token && *token) {
    char* comma = strchr(token, ',');
    if (comma) *comma = 0;
    token = trimDataRoute(token);
    const size_t chars = strlen(token);
    const uint8_t token_width = uint8_t(chars / 2);
    if ((chars != 2 && chars != 4 && chars != 6)
        || (width && width != token_width) || hops >= 63
        || size_t(hops + 1) * token_width > MAX_PATH_SIZE) {
      error = "Err - path hashes must have one consistent 1-3 byte width";
      return false;
    }
    if (!width) width = token_width;
    for (uint8_t i = 0; i < width; ++i) {
      const uint8_t hi = nibble(token[i * 2]), lo = nibble(token[i * 2 + 1]);
      if (hi > 15 || lo > 15) { error = "Err - invalid path hex"; return false; }
      path[hops * width + i] = uint8_t((hi << 4) | lo);
    }
    ++hops; token = comma ? comma + 1 : nullptr;
  }
  if (!width || !hops) { error = "Err - missing path"; return false; }
  path_len = uint8_t(((width - 1) << 6) | hops);
  return Packet::isValidPathLen(path_len);
}

static void formatDataPath(const DataRouteState& route, char* out, size_t size) {
  if (route.path_len == OUT_PATH_UNKNOWN) { snprintf(out, size, "none"); return; }
  if (!Packet::isValidPathLen(route.path_len)) { snprintf(out, size, "invalid"); return; }
  const uint8_t hops = route.path_len & 63;
  if (!hops) { snprintf(out, size, "direct"); return; }
  const uint8_t width = (route.path_len >> 6) + 1;
  size_t used = snprintf(out, size, "%u:", unsigned(width));
  for (unsigned i = 0; i < unsigned(hops) * width && used + 2 < size; ++i) {
    used += snprintf(out + used, size - used, "%02x", route.path[i]);
  }
}

static bool dataRouteSave(DataRouteState& route) {
  if (!route.fs) return false;
  uint8_t data[DATA_ROUTE_SIZE] = {};
  memcpy(data, "DTX1", 4); data[4] = route.path_len; data[5] = route.region_mode;
  memcpy(data + 8, route.path, MAX_PATH_SIZE);
  memcpy(data + 72, route.region, sizeof(route.region));
  writeRoute32(data + 104, storage::updateCRC32(0xffffffff, data, 104));
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  AtomicFileWriter writer(route.fs, DATA_ROUTE_FILE);
#else
  ContactFileTransaction writer(route.fs, DATA_ROUTE_FILE);
#endif
  const bool ok = writer && writer.write(data, sizeof(data)) == sizeof(data)
      && writer.commit();
  if (ok) route.persisted = true;
  return ok;
}

static bool dataRouteLoad(DataRouteState& route) {
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!ContactFileTransaction::recover(route.fs, DATA_ROUTE_FILE)) return false;
#endif
  if (!route.fs->exists(DATA_ROUTE_FILE)) return true;
  uint8_t data[DATA_ROUTE_SIZE] = {};
  for (unsigned retry = 0; retry < 3; ++retry) {
    auto file = openFileRead(route.fs, DATA_ROUTE_FILE);
    const bool read = file && file.size() == sizeof(data)
        && file.read(data, sizeof(data)) == sizeof(data);
    if (file) file.close();
    const bool named = data[5] == DATA_REGION_NAMED;
    if (!read || memcmp(data, "DTX1", 4)
        || (data[4] != OUT_PATH_UNKNOWN && !Packet::isValidPathLen(data[4]))
        || data[5] > DATA_REGION_NONE || (named && !data[72])
        || data[102] != 0
        || readRoute32(data + 104)
            != storage::updateCRC32(0xffffffff, data, 104)) continue;
    route.path_len = data[4]; route.region_mode = data[5];
    memcpy(route.path, data + 8, MAX_PATH_SIZE);
    memcpy(route.region, data + 72, sizeof(route.region));
    route.region[sizeof(route.region) - 1] = 0;
    route.persisted = true;
    return true;
  }
  return false;
}

static uint8_t regionDepth(const RegionMap& regions, const RegionEntry* region) {
  if (!region || region->isWildcard()) return 0;
  uint8_t depth = 1;
  uint16_t parent = region->parent;
  for (int walked = 0; parent; ++walked) {
    if (walked >= regions.getCount()) return 0;
    const RegionEntry* entry = const_cast<RegionMap&>(regions).findById(parent);
    if (!entry || entry->isWildcard()) return 0;
    ++depth; parent = entry->parent;
  }
  return depth;
}

static bool usableScope(DataRouteState& route, const RegionEntry* region,
                        TransportKey& scope) {
  return region && !region->isWildcard() && !(region->flags & REGION_DENY_FLOOD)
      && route.regions->getTransportKeysFor(*region, &scope, 1) > 0
      && !scope.isNull();
}

static bool resolveDataScope(DataRouteState& route, TransportKey& scope,
                             char* resolved, size_t resolved_size,
                             bool* ambiguity) {
  if (resolved && resolved_size) resolved[0] = 0;
  if (ambiguity) *ambiguity = false;
  const RegionEntry* selected = nullptr;
  if (route.region_mode == DATA_REGION_NONE) return false;
  if (route.region_mode == DATA_REGION_DEFAULT) {
    selected = route.regions->getDefaultRegion();
  } else if (route.region_mode == DATA_REGION_NAMED) {
    selected = route.regions->findByName(route.region);
  } else {
    // Auto follows an explicit radio default when it is usable.  Radios
    // without one fall back to the single narrowest usable region in the
    // definition, which keeps fresh installs deterministic without silently
    // choosing between equally specific scopes.
    selected = route.regions->getDefaultRegion();
    TransportKey default_scope;
    if (usableScope(route, selected, default_scope)) {
      scope = default_scope;
      if (resolved && resolved_size) {
        strncpy(resolved, selected->name, resolved_size - 1);
        resolved[resolved_size - 1] = 0;
      }
      return true;
    }
    selected = nullptr;
    uint8_t best_depth = 0;
    bool tied = false;
    for (int i = 0; i < route.regions->getCount(); ++i) {
      const RegionEntry* candidate = route.regions->getByIdx(i);
      TransportKey candidate_scope;
      if (!usableScope(route, candidate, candidate_scope)) continue;
      const uint8_t depth = regionDepth(*route.regions, candidate);
      if (depth > best_depth) { selected = candidate; best_depth = depth; tied = false; }
      else if (depth && depth == best_depth) tied = true;
    }
    if (tied) { if (ambiguity) *ambiguity = true; return false; }
  }
  if (!usableScope(route, selected, scope)) return false;
  if (resolved && resolved_size) {
    strncpy(resolved, selected->name, resolved_size - 1);
    resolved[resolved_size - 1] = 0;
  }
  return true;
}
} // namespace mesh

void CommonCLI::beginManagement(mesh::Mesh& mesh, FILESYSTEM* fs) {
  _management_mesh = &mesh; _management_fs = fs;
  _management_last_ms = millis(); _management_uptime_ms = _management_last_ms;
  if (!_data_route) {
    _data_route = new (std::nothrow) mesh::DataRouteState;
    if (_data_route) {
      _data_route->fs = fs; _data_route->regions = _region_map;
      _data_route->healthy = mesh::dataRouteLoad(*_data_route);
    }
  }
  // No reporter/history allocation for the default unconfigured installation.
  if (fs->exists("/management") || fs->exists("/management.bak")) {
    _management = new (std::nothrow) mesh::ManagementReporter(
      mesh, *_board, *_sensors, *_acl, *_prefs, *_callbacks, *this, fs);
  }
}

void CommonCLI::loopManagement() {
  const uint32_t now = millis();
  _management_uptime_ms += uint32_t(now - _management_last_ms); _management_last_ms = now;
  if (_management) _management->loop(_management_uptime_ms / 1000);
}

bool CommonCLI::getDataTxPath(const uint8_t*& path, uint8_t& path_len) const {
  static const uint8_t direct[MAX_PATH_SIZE] = {};
  if (!_data_route || !_data_route->healthy) { path = direct; path_len = OUT_PATH_UNKNOWN; return false; }
  path = _data_route->path; path_len = _data_route->path_len;
  return path_len != OUT_PATH_UNKNOWN && mesh::Packet::isValidPathLen(path_len);
}

bool CommonCLI::resolveDataTxScope(TransportKey& scope, char* name,
                                   size_t name_size, bool* ambiguous) const {
  return _data_route && _data_route->healthy
      && mesh::resolveDataScope(*_data_route, scope, name, name_size, ambiguous);
}

bool CommonCLI::adoptLegacyDataTxPath(const uint8_t* path, uint8_t path_len) {
  if (!_data_route || !_data_route->healthy || _data_route->persisted
      || path_len == OUT_PATH_UNKNOWN || !mesh::Packet::isValidPathLen(path_len)
      || ((path_len & 63) && !path)) return false;
  const uint8_t previous_len = _data_route->path_len;
  uint8_t previous[MAX_PATH_SIZE]; memcpy(previous, _data_route->path, sizeof(previous));
  _data_route->path_len = path_len; memset(_data_route->path, 0, sizeof(_data_route->path));
  if (path_len & 63) mesh::Packet::copyPath(_data_route->path, path, path_len);
  if (mesh::dataRouteSave(*_data_route)) return true;
  _data_route->path_len = previous_len; memcpy(_data_route->path, previous, sizeof(previous));
  return false;
}

bool CommonCLI::setDataTxPath(const char* spec, char* reply, size_t reply_size) {
  if (!spec || strlen(spec) > 140) {
    snprintf(reply, reply_size, "Err - invalid data.tx path"); return false;
  }
  char command[160];
  snprintf(command, sizeof(command), "set data.tx path %s", spec);
  char result[160] = {};
  const bool handled = handleDataTxCommand(command, result);
  snprintf(reply, reply_size, "%s", result);
  return handled && !strncmp(result, "OK", 2);
}

bool CommonCLI::handleDataTxCommand(char* command, char* reply) {
  const bool get_all = !strcmp(command, "get data.tx");
  const bool get_path = !strcmp(command, "get data.tx path") || !strcmp(command, "get data.tx.path");
  const bool get_region = !strcmp(command, "get data.tx region") || !strcmp(command, "get data.tx.region");
  const char* path_prefix = !strncmp(command, "set data.tx path ", 17) ? "set data.tx path "
      : (!strncmp(command, "set data.tx.path ", 17) ? "set data.tx.path " : nullptr);
  const char* region_prefix = !strncmp(command, "set data.tx region ", 19) ? "set data.tx region "
      : (!strncmp(command, "set data.tx.region ", 19) ? "set data.tx.region " : nullptr);
  if (!get_all && !get_path && !get_region && !path_prefix && !region_prefix) return false;
  if (!_data_route) { strcpy(reply, "Err - data.tx unavailable"); return true; }
  if (!_data_route->healthy) { strcpy(reply, "Err - data.tx storage fault"); return true; }

  char path_text[132]; mesh::formatDataPath(*_data_route, path_text, sizeof(path_text));
  if (get_path) { snprintf(reply, 160, "> %s", path_text); return true; }
  if (get_region) {
    if (_data_route->region_mode == mesh::DATA_REGION_AUTO) {
      char resolved[31]; bool ambiguous = false; TransportKey scope;
      const bool ok = resolveDataTxScope(scope, resolved, sizeof(resolved), &ambiguous);
      snprintf(reply, 160, "> auto (%s)", ok ? resolved : ambiguous ? "ambiguous" : "unresolved");
    } else if (_data_route->region_mode == mesh::DATA_REGION_DEFAULT) strcpy(reply, "> default");
    else if (_data_route->region_mode == mesh::DATA_REGION_NONE) strcpy(reply, "> none");
    else snprintf(reply, 160, "> %s", _data_route->region);
    return true;
  }
  if (get_all) {
    char resolved[31]; bool ambiguous = false; TransportKey scope;
    const bool scoped = resolveDataTxScope(scope, resolved, sizeof(resolved), &ambiguous);
    const char* mode = _data_route->region_mode == mesh::DATA_REGION_AUTO ? "auto"
        : _data_route->region_mode == mesh::DATA_REGION_DEFAULT ? "default"
        : _data_route->region_mode == mesh::DATA_REGION_NONE ? "none" : _data_route->region;
    snprintf(reply, 160, "> path=%s region=%s resolved=%s", path_text, mode,
             scoped ? resolved : ambiguous ? "ambiguous" : "none");
    return true;
  }

  const mesh::DataRouteState previous = *_data_route;
  if (path_prefix) {
    char* value = command + strlen(path_prefix); const char* error = nullptr;
    uint8_t candidate[MAX_PATH_SIZE], encoded = OUT_PATH_UNKNOWN;
    if (!mesh::parseDataPath(value, candidate, encoded, error)) {
      snprintf(reply, 160, "%s", error ? error : "Err - invalid path"); return true;
    }
    _data_route->path_len = encoded; memcpy(_data_route->path, candidate, sizeof(candidate));
  } else {
    char* value = mesh::trimDataRoute(command + strlen(region_prefix));
    if (!strcmp(value, "auto")) { _data_route->region_mode = mesh::DATA_REGION_AUTO; _data_route->region[0] = 0; }
    else if (!strcmp(value, "default")) { _data_route->region_mode = mesh::DATA_REGION_DEFAULT; _data_route->region[0] = 0; }
    else if (!strcmp(value, "none") || !strcmp(value, "clear") || !strcmp(value, "-")) {
      _data_route->region_mode = mesh::DATA_REGION_NONE; _data_route->region[0] = 0;
    } else {
      RegionEntry* region = _region_map->findByNamePrefix(value);
      TransportKey scope;
      if (!region || !mesh::usableScope(*_data_route, region, scope)) {
        strcpy(reply, "Err - unknown, ambiguous, denied, or unusable region"); return true;
      }
      _data_route->region_mode = mesh::DATA_REGION_NAMED;
      strncpy(_data_route->region, region->name, sizeof(_data_route->region) - 1);
      _data_route->region[sizeof(_data_route->region) - 1] = 0;
    }
  }
  if (!mesh::dataRouteSave(*_data_route)) {
    *_data_route = previous; strcpy(reply, "Err - unable to save data.tx"); return true;
  }
  strcpy(reply, "OK"); return true;
}

bool CommonCLI::handleManagementCommand(char* command, char* reply) {
  if (handleDataTxCommand(command, reply)) return true;
  if (!strcmp(command, "get mgmt.path")) {
    char alias[] = "get data.tx path";
    return handleDataTxCommand(alias, reply);
  }
  if (!strncmp(command, "set mgmt.path ", 14)) {
    char alias[160];
    snprintf(alias, sizeof(alias), "set data.tx path %s", command + 14);
    return handleDataTxCommand(alias, reply);
  }
  if (strcmp(command, "get mgmt") && strncmp(command, "get mgmt.", 9)
      && strncmp(command, "set mgmt.", 9)) return false;
  if (!_management && _management_mesh && _management_fs) {
    _management = new (std::nothrow) mesh::ManagementReporter(
      *_management_mesh, *_board, *_sensors, *_acl, *_prefs, *_callbacks, *this,
      _management_fs);
  }
  if (!_management) { strcpy(reply, "ERR: management unavailable"); return true; }
  if (!_management->command(command, reply, 160)) strcpy(reply, "ERR: unknown management setting");
  return true;
}
