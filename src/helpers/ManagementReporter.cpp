#include "ManagementReporter.h"
#include "CommonCLI.h"
#include "FileRead.h"
#include "PersistentStoreFormat.h"
#include <new>
#include <stdlib.h>
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include "AtomicFileWriter.h"
#else
#include "ContactFileTransaction.h"
#endif
#if defined(ESP32_PLATFORM)
#include <WiFi.h>
#include <esp_ota_ops.h>
#endif
#if defined(ENABLE_OTA)
#include "ota/OtaContext.h"
#include "ota/OtaDeflate.h"
#endif

namespace mesh {
using namespace management;
static constexpr char STATE_FILE[] = "/management";
static constexpr size_t STATE_SIZE = 120;
struct ManagementReporter::Working {
  History history;
  Extrema during_report;
  AclList acl;
  uint8_t header[HEADER] = {};
  TransportKey scope;
  uint8_t route_path[MAX_PATH_SIZE] = {};
  uint8_t route_path_len = OUT_PATH_UNKNOWN;
  uint32_t sample_in = 0, page_in = 0, lifetime = 0, history_seconds = 0;
  uint8_t page = 0;
  bool sending = false, flood = false, partial_period = true;
};

ManagementReporter::ManagementReporter(Mesh& mesh, MainBoard& board, SensorManager& sensors,
    ClientACL& acl, NodePrefs& prefs, CommonCLICallbacks& callbacks,
    CommonCLI& common_cli, FILESYSTEM* fs)
    : mesh(mesh), board(board), sensors(sensors), acl(acl), prefs(prefs),
      callbacks(callbacks), common_cli(common_cli), fs(fs) {
  last_ms = millis(); healthy = load();
  if (healthy && enabled && !allocate()) healthy = false;
}
ManagementReporter::~ManagementReporter() { delete work; erase(key, sizeof(key)); }
bool ManagementReporter::allocate() {
  static_assert(sizeof(Working) <= 1536, "Keep opt-in management working RAM bounded");
  if (!work) work = new (std::nothrow) Working;
  return work != nullptr;
}
bool ManagementReporter::load() {
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!ContactFileTransaction::recover(fs, STATE_FILE)) return false;
#endif
  if (!fs->exists(STATE_FILE)) return true; // fresh install remains off
  uint8_t b[STATE_SIZE] = {};
  for (unsigned retry = 0; retry < 3; ++retry) {
    auto f = openFileRead(fs, STATE_FILE);
    const bool read = f && f.size() == sizeof(b) && f.read(b, sizeof(b)) == sizeof(b);
    if (f) f.close();
    const bool legacy = read && !memcmp(b, "MGC1", 4);
    const bool current = read && !memcmp(b, "MGC2", 4);
    const bool direct_valid = legacy ? Schedule::validDirect(b[5])
        : (b[5] == 0 || Schedule::validDirect(b[5]));
    const bool flood_valid = legacy
        ? (b[7] == OUT_PATH_UNKNOWN || Packet::isValidPathLen(b[7]))
        : (b[7] == 0 || Schedule::validFlood(b[7]));
    if ((!legacy && !current) || b[4] > 1 || b[6] > 1 ||
        !direct_valid || !flood_valid || (b[4] && (!b[6] || (!b[5] && !b[7]))) ||
        read32(b + 116) != storage::updateCRC32(0xffffffff, b, 116) ||
        read32(b + 104) > 90 * DAY + 3600 || read32(b + 108) > 90 * DAY + 3600) continue;
    enabled = b[4]; direct_days = b[5]; keyed = b[6];
    flood_days = legacy ? (direct_days < 21 ? 21 : direct_days) : b[7];
    if (legacy && b[7] != OUT_PATH_UNKNOWN) common_cli.adoptLegacyDataTxPath(b + 8, b[7]);
    memcpy(key, b + 72, 32);
    schedule.direct = read32(b + 104); schedule.flood = read32(b + 108);
    if (!direct_days) schedule.direct = 0;
    if (!flood_days) schedule.flood = 0;
    sequence = read32(b + 112); erase(b, sizeof(b)); return true;
  }
  erase(b, sizeof(b)); return false; // unreadable/corrupt state never transmits
}
bool ManagementReporter::save() {
  uint8_t b[STATE_SIZE] = {};
  memcpy(b, "MGC2", 4); b[4] = enabled; b[5] = direct_days; b[6] = keyed;
  b[7] = flood_days; memcpy(b + 72, key, 32);
  write32(b + 104, schedule.direct); write32(b + 108, schedule.flood); write32(b + 112, sequence);
  write32(b + 116, storage::updateCRC32(0xffffffff, b, 116));
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  AtomicFileWriter writer(fs, STATE_FILE);
#else
  ContactFileTransaction writer(fs, STATE_FILE);
#endif
  const bool ok = writer && writer.write(b, sizeof(b)) == sizeof(b) && writer.commit();
  erase(b, sizeof(b)); if (ok) checkpoint = 0; return ok;
}
void ManagementReporter::cancel() {
  if (work) work->sending = false;
}
uint32_t ManagementReporter::jitter() const {
  return (read32(mesh.self_id.pub_key) ^ (sequence * 2654435761UL)) % 3600;
}
void ManagementReporter::snapshot() {
  auto& w = *work; memset(w.header, 0, sizeof(w.header)); w.acl = AclList();
  auto* p = w.header; memcpy(p, "MGR1", 4); memcpy(p + 4, mesh.self_id.pub_key, 16);
  write32(p + 20, sequence); write32(p + 24, mesh.getRTCClock()->getCurrentTime());
  const uint64_t hours = uptime / 3600; write16(p + 60, hours > 65535 ? 65535 : hours);
  w.history.week().encode(p + 62); w.history.period.encode(p + 66);
  const uint32_t history_hours = w.history_seconds / 3600;
  p[70] = history_hours > 168 ? 168 : history_hours;
  p[71] = w.flood ? flood_days : direct_days;
  p[72] = !strcmp(callbacks.getRole(), "repeater") ? 1 : !strcmp(callbacks.getRole(), "room_server") ? 2 : 3;
  uint16_t valid = MCU_TEMPERATURE | (history_hours < 168 ? PARTIAL_WEEK : 0) | (w.partial_period ? PARTIAL_PERIOD : 0);
  p[75] = GPS | OTA; // known active-state bits; unknown is not confused with off
  if (sensors.isGPSDetected()) p[73] |= GPS;
  auto* gps = sensors.getLocationProvider();
  if (gps && gps->isEnabled()) p[74] |= GPS;
#if defined(ESP32_PLATFORM)
  p[73] |= WIFI; p[75] |= WIFI;
  if (WiFi.status() == WL_CONNECTED) p[74] |= WIFI;
#endif
#if defined(NRF52_PLATFORM) || (defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT)
  p[73] |= USB; p[75] |= USB;
  if (board.isUsbDataConnected()) p[74] |= USB;
#endif
#ifdef WITH_MQTT_BRIDGE
  p[73] |= NTP; p[75] |= NTP;
  if (callbacks.managementNtpSynced()) p[74] |= NTP;
#endif
#if defined(ENABLE_OTA)
  p[73] |= OTA;
  ota::SelfFwInfo info;
  if (ota::ota_self_firmware(info) && info.valid) {
    write32(p + 28, info.fw_version); write32(p + 36, info.target_id);
    memcpy(p + 40, info.body_hash, 8); write32(p + 48, info.image_len);
    valid |= BASE; if (info.fw_version) valid |= FIRMWARE;
  }
  uint32_t caps = 1; // compiled OTA protocol
  // bits 8..23 apply codecs; transfer capabilities are separate low bits.
  caps |= 2; // OTA transport decoder is installed in all ENABLE_OTA builds
  caps |= 4; // 2 KiB application transfer blocks
#if defined(ESP32_PLATFORM)
  const esp_partition_t* running = esp_ota_get_running_partition();
  const esp_partition_t* next = esp_ota_get_next_update_partition(nullptr);
  if (running && next && running->address != next->address) {
    write32(p + 52, next->size); valid |= STORE; p[74] |= OTA;
    caps |= 7UL << 8; // full + detools sequential + in-place
  }
#elif defined(NRF52_PLATFORM)
  const auto bl = ota::ota_bootloader_app_caps();
#if defined(OTA_SD_STORE) || defined(OTA_QSPI_STORE)
  const uint16_t codecs = bl.codec_mask & 5u; // full + in-place
#else
  const uint16_t codecs = bl.codec_mask & 4u; // internal app path accepts deltas only
#endif
  if (bl.present && bl.apply_abi >= 2) caps |= uint32_t(codecs) << 8;
  ota::OtaBootloaderIdentity identity;
  if (ota::ota_installed_bootloader_identity(identity) && identity.boot_version) {
    write32(p + 32, identity.boot_version); valid |= BOOTLOADER;
  }
  // Never allocate an OTA workspace or wake external media just for a report.
  if (auto* context = ota::ota_context_if_active()) {
    const uint32_t capacity = context->fetch_store.capacity();
    write32(p + 52, capacity); valid |= STORE;
    if (capacity && bl.present && bl.apply_abi >= 2 && codecs) p[74] |= OTA;
  } else {
    p[75] &= ~OTA; // compiled support, readiness not established
  }
#else
  p[75] &= ~OTA;
#endif
#if defined(OTA_SEEDER_ONLY)
  caps &= 0xff; p[74] &= ~OTA;
#endif
  write32(p + 56, caps);
#endif
  // If EndF is absent, only publish a version we can parse unambiguously.
  if (!(valid & FIRMWARE)) {
    unsigned major = 0, minor = 0, patch = 0, pre = 0;
    const char* version = callbacks.getFirmwareVer(); if (*version == 'v') ++version;
    const int n = sscanf(version, "%u.%u.%u.%u", &major, &minor, &patch, &pre);
    if (n >= 3 && major <= 255 && minor <= 255 && patch <= 255 && pre <= 255) {
      write32(p + 28, major << 24 | minor << 16 | patch << 8 | pre); valid |= FIRMWARE;
    }
  }
  write16(p + 76, valid);
  uint8_t token[12]; bool complete = true;
  for (int i = 0; i < acl.getNumClients(); ++i) {
    const auto* client = acl.getClientByIdx(i);
    if (!client->isAdmin()) continue;
    fingerprint(key, p + 4, client->id.pub_key, token); complete &= w.acl.add(token, ADMIN);
  }
#if defined(ENABLE_OTA)
  if (prefs.ota_signer_count > 4) complete = false;
  for (unsigned i = 0; i < prefs.ota_signer_count && i < 4; ++i) {
    fingerprint(key, p + 4, prefs.ota_signers[i], token); complete &= w.acl.add(token, OTA_SIGNER);
  }
#endif
  erase(token, sizeof(token));
  if (!complete) healthy = false; // never silently truncate an ACL
}
void ManagementReporter::start(bool flood) {
  if (sequence == UINT32_MAX) { healthy = false; return; }
  auto& w = *work;
  w.route_path_len = OUT_PATH_UNKNOWN;
  memset(w.route_path, 0, sizeof(w.route_path));
  if (flood) {
    if (!common_cli.resolveDataTxScope(w.scope)) return;
  } else {
    const uint8_t* configured = nullptr;
    if (!common_cli.getDataTxPath(configured, w.route_path_len)) return;
    if (w.route_path_len & 63) {
      Packet::copyPath(w.route_path, configured, w.route_path_len);
    }
  }
  ++sequence; schedule.reserve(flood, direct_days, flood_days, jitter());
  if (!save()) { healthy = false; return; } // reserve BEFORE any packet leaves
  w.flood = flood;
  snapshot(); if (!healthy) return;
  w.page = 0; w.page_in = 0;
  w.during_report = Extrema();
  w.sending = true; w.lifetime = 3600;
}
void ManagementReporter::sendPage() {
  auto& w = *work;
  uint8_t packet[MAX_PAYLOAD], enc[32]; memcpy(packet, w.header, HEADER);
  const uint8_t first = w.page * PER_PAGE;
  const uint8_t count = w.acl.count - first < PER_PAGE ? w.acl.count - first : PER_PAGE;
  packet[78] = w.page; packet[79] = w.acl.pages(); packet[80] = w.acl.count;
  packet[81] = first; packet[82] = count;
  const size_t private_len = count * ENTRY, size = HEADER + private_len + TAG;
  memcpy(packet + HEADER, w.acl.entries + first, private_len);
  deriveKey(key, "MeshCore-MGR1-SIV", packet + 4, enc);
  seal(enc, packet, HEADER, packet + HEADER, private_len, packet + HEADER + private_len);
  erase(enc, sizeof(enc));
  Packet* pkt = mesh.createRawData(packet, size);
  if (!pkt) { w.page_in = 60; return; }
  // Dedicated background admission avoids normal direct-message priority and
  // automatic retries. Dispatcher still enforces normal channel/duty limits.
  if (!mesh.sendManagementData(pkt, w.flood, w.route_path, w.route_path_len,
                               prefs.path_hash_mode + 1,
                               w.flood ? w.scope.key : nullptr)) {
    w.page_in = 60; return;
  }
  ++w.page; w.page_in = 60;
  if (w.page == w.acl.pages()) {
    w.sending = false;
    // Readings gathered while the frozen snapshot was being paged belong to
    // the next report. Never discard that interval on completion.
    w.history.period = w.during_report; w.partial_period = false;
  }
}
void ManagementReporter::loop(uint64_t node_uptime_seconds) {
  const uint32_t now = millis(), delta = now - last_ms; last_ms = now;
  const uint64_t accumulated = uint64_t(fraction_ms) + delta;
  const uint32_t elapsed = accumulated / 1000; fraction_ms = accumulated % 1000;
  uptime = node_uptime_seconds == UINT64_MAX ? uptime + elapsed : node_uptime_seconds;
  if (!healthy || !enabled || !keyed || !work) return;
  auto& w = *work; schedule.advance(elapsed); checkpoint += elapsed;
  w.history_seconds += elapsed;
  w.sample_in = Schedule::sub(w.sample_in, elapsed);
  w.page_in = Schedule::sub(w.page_in, elapsed);
  w.lifetime = Schedule::sub(w.lifetime, elapsed);
  if (w.sending && !w.lifetime) { w.sending = false; w.partial_period = true; }
  if (!w.sample_in) {
    const uint16_t voltage = board.getBattMilliVolts();
    const float celsius = board.getMCUTemperature();
    w.history.sample(w.history_seconds, voltage, celsius);
    if (w.sending) w.during_report.add(voltage, temperature(celsius));
    w.sample_in = 60;
  }
  if (checkpoint >= 3600 && !save()) { healthy = false; return; }
  if (mesh.isAnyTempRadioActive() || mesh.hasOutbound() || !mesh.getRemainingTxBudget()) return;
  if (!w.sending) {
    const uint8_t* route_path = nullptr; uint8_t route_path_len = OUT_PATH_UNKNOWN;
    // Flood has its own conservative schedule. MQTT upload has no deployed
    // return path, so a direct transmission never postpones it. When both are
    // due, the scoped flood replaces the redundant direct transmission.
    if (flood_days && !schedule.flood) start(true);
    if (!w.sending && direct_days
        && common_cli.getDataTxPath(route_path, route_path_len) && !schedule.direct) {
      start(false);
    }
  }
  if (healthy && w.sending && !w.page_in) sendPage();
}
bool ManagementReporter::command(char* command, char* reply, size_t size) {
  if (!strcmp(command, "get mgmt")) {
    const uint8_t* route_path = nullptr; uint8_t route_path_len = OUT_PATH_UNKNOWN;
    TransportKey scope;
    const bool routed = common_cli.getDataTxPath(route_path, route_path_len);
    const bool scoped = common_cli.resolveDataTxScope(scope);
    char direct[12], flood[12];
    if (direct_days) snprintf(direct, sizeof(direct), "%ud", direct_days);
    else strcpy(direct, "off");
    if (flood_days) snprintf(flood, sizeof(flood), "%ud", flood_days);
    else strcpy(flood, "off");
    snprintf(reply, size, "> %s key=%s direct=%s/%luh flood=%s/%luh path=%s region=%s%s",
      enabled ? "on" : "off", keyed ? "set" : "unset", direct,
      (unsigned long)(schedule.direct / 3600), flood,
      (unsigned long)(schedule.flood / 3600),
      routed ? "set" : "missing", scoped ? "set" : "missing",
      healthy ? "" : " FAULT(no TX)");
    return true;
  }
  if (strncmp(command, "set mgmt.", 9)) return false;
  char* value = strchr(command + 9, ' ');
  if (!value) {
    snprintf(reply, size, "ERR: mgmt.enabled/direct/flood/interval/password value");
    return true;
  }
  ++value;
  if (!healthy) { snprintf(reply, size, "ERR: management state fault; repair storage/reboot first"); return true; }
  // Failed persistence stops reporting; no uncommitted state is transmitted.
  cancel();
  if (!strncmp(command, "set mgmt.password ", 18)) {
    const size_t len = strlen(value);
    if (len < 12 || len > 96) { snprintf(reply, size, "ERR: password must be 12..96 bytes"); erase(value, len); return true; }
    passwordKey(value, key); erase(value, len); keyed = true;
  } else if (!strncmp(command, "set mgmt.enabled ", 17)) {
    if (strcmp(value, "on") && strcmp(value, "off")) { snprintf(reply, size, "ERR: use on/off"); return true; }
    const bool on = !strcmp(value, "on");
    const uint8_t* route_path = nullptr; uint8_t route_path_len = OUT_PATH_UNKNOWN;
    TransportKey scope;
    const bool direct_ready = !direct_days
        || common_cli.getDataTxPath(route_path, route_path_len);
    const bool flood_ready = !flood_days || common_cli.resolveDataTxScope(scope);
    if (on && (!keyed || (!direct_days && !flood_days) || !direct_ready
        || !flood_ready || !allocate())) {
      snprintf(reply, size, "ERR: password, enabled route(s), data.tx path/region and free RAM required");
      return true;
    }
    if (on && !enabled && sequence == 0) {
      if (direct_days && schedule.direct == direct_days * DAY) schedule.direct += jitter();
      if (flood_days && schedule.flood == flood_days * DAY) schedule.flood += jitter();
    }
    enabled = on;
  } else if (!strncmp(command, "set mgmt.direct ", 16)) {
    if (!strcmp(value, "off")) {
      if (enabled && !flood_days) {
        snprintf(reply, size, "ERR: disable management or leave flood enabled"); return true;
      }
      direct_days = 0; schedule.direct = 0;
    } else {
      char* end; const unsigned long n = strtoul(value, &end, 10);
      const uint8_t* route_path = nullptr; uint8_t route_path_len = OUT_PATH_UNKNOWN;
      if (!*value || *end || !Schedule::validDirect(n)
          || !common_cli.getDataTxPath(route_path, route_path_len)) {
        snprintf(reply, size, "ERR: direct needs data.tx path and 5..90 days"); return true;
      }
      direct_days = n; schedule.direct = direct_days * DAY;
    }
  } else if (!strncmp(command, "set mgmt.flood ", 15)) {
    if (!strcmp(value, "off")) {
      if (enabled && !direct_days) {
        snprintf(reply, size, "ERR: disable management or leave direct enabled"); return true;
      }
      flood_days = 0; schedule.flood = 0;
    } else {
      char* end; const unsigned long n = strtoul(value, &end, 10);
      TransportKey scope;
      if (!*value || *end || !Schedule::validFlood(n)
          || !common_cli.resolveDataTxScope(scope)) {
        snprintf(reply, size, "ERR: flood needs data.tx region and 21..90 days"); return true;
      }
      flood_days = n; schedule.flood = flood_days * DAY;
    }
  } else if (!strncmp(command, "set mgmt.interval ", 18)) {
    char* end; const unsigned long n = strtoul(value, &end, 10);
    const uint8_t* route_path = nullptr; uint8_t route_path_len = OUT_PATH_UNKNOWN;
    TransportKey scope;
    if (!*value || *end || !Schedule::validDirect(n)
        || !common_cli.getDataTxPath(route_path, route_path_len)
        || !common_cli.resolveDataTxScope(scope)) {
      snprintf(reply, size, "ERR: interval needs path, region and 5..90 days"); return true;
    }
    // Compatibility shorthand: configure both schedules together.
    direct_days = n; flood_days = n < 21 ? 21 : n;
    schedule.direct = direct_days * DAY; schedule.flood = flood_days * DAY;
  } else { snprintf(reply, size, "ERR: unknown management setting"); return true; }
  if (!save()) { healthy = false; snprintf(reply, size, "ERR: save failed; reporting stopped"); return true; }
  if (!enabled) { delete work; work = nullptr; }
  snprintf(reply, size, "OK"); return true;
}
}
