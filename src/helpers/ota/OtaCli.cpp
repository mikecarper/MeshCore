#include "OtaCli.h"
#include "OtaContext.h"
#include "FolderMotaStore.h"   // `ota pull <id> folder` destination (set_mid on the connected folder store)
#include "OtaVerify.h"
#include "OtaSelf.h"
#include "OtaTargets.h"   // ota_target_env_name(): human-readable name for a target_id (no string on the wire)
#if defined(NRF52_PLATFORM)
  #include "OtaBlInfo.h"  // installed bootloader application/update capability views
#endif
#include "Utils.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <Arduino.h>   // millis() for session-age display (device-only command surface)

namespace mesh {
namespace ota {

static uint32_t parse_u32(const char* s) {
  uint32_t n = 0;
  while (*s == ' ') s++;
  while (*s >= '0' && *s <= '9') n = n * 10 + (uint32_t)(*s++ - '0');
  return n;
}

static bool parse_page(const char* s, uint16_t& page) {
  while (*s == ' ') s++;
  if (*s == 0) { page = 1; return true; }
  uint32_t n = 0;
  const char* p = s;
  while (*p >= '0' && *p <= '9') {
    n = n * 10 + (uint32_t)(*p++ - '0');
    if (n > 255) return false;
  }
  while (*p == ' ') p++;
  if (*p != 0 || n == 0) return false;
  page = (uint16_t)n;
  return true;
}

static char fstate_char(OtaManager::FetchState s) {
  switch (s) {
    case OtaManager::IDLE: return 'I';
    case OtaManager::WANT_MANIFEST: return 'W';
    case OtaManager::WANT_LEAVES: return 'L';
    case OtaManager::VERIFYING_STAGED: return 'V';
    case OtaManager::FETCHING: return 'F';
    case OtaManager::COMPLETE: return 'C';
    case OtaManager::PAUSED: return 'P';
    default: return 'X';
  }
}

// For users, the only distinction that matters is application full/delta vs the separately gated
// bootloader package. Which application delta codec is an implementation detail.
static const char* codec_kind(uint8_t flags, uint8_t c) {
  return (flags & MFLAG_BOOTLOADER) ? "bootloader" : c == CODEC_FULL ? "full" : "delta";
}

static bool parse_hex_exact(const char* text, uint8_t* out, size_t bytes) {
  if (!text || strlen(text) != bytes * 2u) return false;
  for (size_t i = 0; i < bytes * 2u; i++) {
    const char c = text[i];
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')))
      return false;
  }
  return mesh::Utils::fromHex(out, bytes, text);
}

static bool staged_manifest(OtaContext& c, MotaManifest& m) {
  uint8_t hdr[8], manifest[MOTA_MFL];
  const uint32_t total = c.fetch_store.staged_size();
  return total >= 8u + MOTA_MFL + 5u && c.fetch_store.read(0, hdr, sizeof(hdr)) &&
         memcmp(hdr, MOTA_MAGIC, sizeof(MOTA_MAGIC)) == 0 && rd_u32le(hdr + 4) == total &&
         c.fetch_store.read(8, manifest, sizeof(manifest)) &&
         mota_parse_manifest(manifest, sizeof(manifest), m);
}

// A plain-language word for the fetch state (shown in `ota status`).
static const char* state_word(OtaManager::FetchState s) {
  switch (s) {
    case OtaManager::IDLE:          return "idle";
    case OtaManager::WANT_MANIFEST: return "starting";
    case OtaManager::WANT_LEAVES:   return "validating seed";
    case OtaManager::VERIFYING_STAGED: return "verifying staged blocks";
    case OtaManager::FETCHING:      return "downloading";
    case OtaManager::COMPLETE:      return "ready to install";
    case OtaManager::FAILED:        return "failed";
    case OtaManager::PAUSED:        return "paused (folder link lost - reconnect to resume)";
    default:                        return "?";
  }
}

// A compact one-word fetch state for the dense `ota stats` line (state_word's phrases are too long).
static const char* state_short(OtaManager::FetchState s) {
  switch (s) {
    case OtaManager::WANT_MANIFEST: return "manifest";
    case OtaManager::WANT_LEAVES:   return "leaves";
    case OtaManager::VERIFYING_STAGED: return "verify";
    case OtaManager::FETCHING:      return "dl";
    case OtaManager::COMPLETE:      return "done";
    case OtaManager::FAILED:        return "failed";
    case OtaManager::PAUSED:        return "paused";
    default:                        return "idle";
  }
}

static const char* fetch_error_word(OtaManager::FetchError error) {
  switch (error) {
    case OtaManager::FETCH_ERROR_MANIFEST:         return "invalid manifest";
    case OtaManager::FETCH_ERROR_HASH_ALGO:        return "unsupported hash";
    case OtaManager::FETCH_ERROR_VERSION:          return "not a newer version";
    case OtaManager::FETCH_ERROR_CODEC:            return "unsupported codec";
    case OtaManager::FETCH_ERROR_GEOMETRY:         return "invalid geometry";
    case OtaManager::FETCH_ERROR_TOO_LARGE:        return "image too large";
    case OtaManager::FETCH_ERROR_STORAGE:          return "storage error";
    case OtaManager::FETCH_ERROR_INTEGRITY:        return "integrity check";
    case OtaManager::FETCH_ERROR_MANIFEST_TIMEOUT: return "manifest timeout";
    case OtaManager::FETCH_ERROR_LEAVES_TIMEOUT:   return "leaves timeout";
    default:                                       return "none";
  }
}

// Render the packed fw_version as "v1.2.3" (or "v1.2.3.4" when a prerelease byte is set).
static void ver_str(char* out, size_t cap, uint32_t v) {
  FwVersion fw = FwVersion::unpack(v);
  if (fw.prerelease) snprintf(out, cap, "v%u.%u.%u.%u", fw.major, fw.minor, fw.patch, fw.prerelease);
  else               snprintf(out, cap, "v%u.%u.%u", fw.major, fw.minor, fw.patch);
}

// Match the first word of `a` against any of the '|'-separated names (so commands have intuitive aliases
// and short forms); on a match, point `*rest` at the argument text. Keeps the dispatch table readable.
static bool is_cmd(const char* a, const char* names, const char** rest) {
  size_t tlen = 0; while (a[tlen] && a[tlen] != ' ') tlen++;
  for (const char* s = names; *s; ) {
    const char* d = s; while (*d && *d != '|') d++;
    if ((size_t)(d - s) == tlen && tlen && strncmp(a, s, tlen) == 0) {
      const char* r = a + tlen; while (*r == ' ') r++;
      if (rest) *rest = r;
      return true;
    }
    s = (*d == '|') ? d + 1 : d;
  }
  return false;
}

// The everyday OTA surface is BitTorrent-shaped: `ota` shows what you're holding (your running firmware
// as a full mOTA + your one fetch session), `ota neighbors` shows the mOTAs heard around you, `ota pull`
// starts fetching one, `ota drop` frees the session. The raw primitives (manual content load, low-level
// apply steps) live under `ota dev ...` so they don't clutter the everyday surface. Every reply fits one
// packet so it works as remote-admin over LoRa.
static bool handle_dev(const char* d, char* reply, OtaContext& c);

bool handle_ota_command(const char* command, char* reply, mesh::MainBoard& board) {
  const char* a = command + 3;
  if (*a != 0 && *a != ' ') return false;
  while (*a == ' ') a++;
  OtaContext& c = ota_ctx();
  const char* rest = a;

  // ---- raw / internal primitives, tucked under `ota dev ...` ----
  if (is_cmd(a, "dev", &rest)) {
    return handle_dev(rest, reply, c);
  }

  // ---- help: list the commands in plain words (aliases in parentheses) ----
  if (is_cmd(a, "help|?|h", &rest)) {
#if defined(OTA_SEEDER_ONLY)
    snprintf(reply, 160,
      "OTA seeder: status | stats | ls=find images | get <id> folder=capture | cancel | "
      "announce | folder | config. LoRa install is disabled.");
#elif defined(NRF52_PLATFORM) && defined(OTA_FLASH_STORE) && !defined(OTA_SD_STORE) && \
      !defined(OTA_QSPI_STORE)
#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
    strcpy(reply,
      "OTA: status | stats | ls | get <id> flash | install | "
      "bootloader | cancel | announce | self | folder | config | key");
#else
    strcpy(reply,
      "OTA: status | stats | ls | get <id> flash [rescue] | install | rescue install <hash16> | "
      "cancel | announce | self | folder | config | key");
#endif
#elif defined(NRF52_PLATFORM) && defined(OTA_QSPI_STORE)
#if defined(OTA_QSPI_BOOTLOADER_UPDATE)
    strcpy(reply,
      "OTA: status | stats | ls | get <id> flash | install | bootloader | cancel | announce | self | "
      "qspi | folder | config | key");
#else
    strcpy(reply,
      "OTA: status | stats | ls | get <id> flash | install | cancel | announce | self | qspi | "
      "folder | config | key");
#endif
#elif defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
#if defined(OTA_SD_BOOTLOADER_UPDATE)
    strcpy(reply,
      "OTA: status | stats | ls | get <id> flash | install | bootloader | cancel | announce | self | "
      "folder | cache | config | key");
#else
    strcpy(reply,
      "OTA: status | stats | ls | get <id> flash | install | cancel | announce | self | folder | "
      "cache | config | key");
#endif
#else
    snprintf(reply, 160,
      "OTA: status | stats | ls | get <id> flash | install | cancel | announce | self | folder | "
      "cache | config | key. `ota ls [page]`; folder [validate].");
#endif

  // ---- inventory dashboard: running fw (self), the one fetch session, serving state ----
  } else if (*a == 0 || is_cmd(a, "status|st", &rest)) {
#if defined(OTA_SEEDER_ONLY)
    uint8_t dig[4]; c.manager.servedDigest(dig);
    char dighx[9]; mesh::Utils::toHex(dighx, dig, 4);
    snprintf(reply, 160,
             "OTA seeder | install:disabled | folder:%s | serving:%u dg=%s | target:00000000 (source only)",
             c.folder_active ? c.folder_dest_info : "not connected",
             (unsigned)c.manager.servedCount(), dighx);
#else
    SelfFwInfo fi; bool s = ota_self_firmware(fi);
    char selfhx[9]; if (s && fi.valid) mesh::Utils::toHex(selfhx, fi.body_hash, 4); else strcpy(selfhx, "?");
    OtaManager::FetchState fs = c.manager.fetchState();
    char dl[80];
    if (fs == OtaManager::IDLE) {
      strcpy(dl, "no download");
    } else {
      char midhx[9]; mesh::Utils::toHex(midhx, c.manager.fetchManifestId(), 4);
      unsigned have = (unsigned)c.manager.blocksHave(), tot = (unsigned)c.manager.blocksTotal();
      unsigned pct = tot ? (unsigned)((uint64_t)have * 100 / tot) : 0;
      unsigned age = c.session_started_ms ? (unsigned)((millis() - c.session_started_ms) / 1000) : 0;
      if (fs == OtaManager::FAILED)
        snprintf(dl, sizeof dl, "download: failed (%s) %u/%u id=%s",
                 fetch_error_word(c.manager.fetchError()), have, tot, midhx);
      else
        snprintf(dl, sizeof dl, "%sdownload: %s %u/%u (%u%%) id=%s %us",
                 c.manager.fetched_is_bootloader() ? "bootloader " : "",
                 state_word(fs), have, tot, pct, midhx, age);
    }
    const char* hw = (c.hw_id[0]) ? c.hw_id : "?";
    const char* tenv = ota_target_env_name(c.manager.target());   // env name, or "?" if not in the table
#if defined(NRF52_PLATFORM)
    // nRF52 applies via the bootloader - show (cached) whether it can, so `ota get`/`install` won't surprise.
    // blrc = the bootloader's last apply code (diagnostic; 0xB8=success, see ota_delta.c).
    const OtaBlCaps& bl = c.bootloaderAppCaps();
#if defined(OTA_QSPI_STORE)
    const char* bl_state = !bl.present ? "NONE" :
                           (bl.storage_flags & OTA_BL_STORAGE_QSPI) ? "QSPI" : "NO-QSPI";
#elif defined(OTA_SD_STORE)
    const char* bl_state = !bl.present ? "NONE" :
                           (bl.storage_flags & OTA_BL_STORAGE_SD) ? "SD" : "NO-SD";
#else
    const char* bl_state = bl.present ? "apply" : "NONE";
#endif
    const uint8_t last_rc = ota_bootloader_last_rc();
    const char* rc_name = ota_nrf52_boot_update_result(last_rc) ? "blup" : "blrc";
    // Keep the fixed-width parser/diagnostic fields ahead of the variable download text and optional
    // human target name. hw is bounded to 32 bytes, so target + the complete blup/blrc token always fit
    // in the 160-byte reply; snprintf may truncate only the descriptive tail.
    snprintf(reply, 160,
             "OTA | this fw %s (%uK) hw=%s | target:%08X | bl:%s %s:%02X | %s | "
             "serving:%s (%u) | keys:%u | env:%s",
             selfhx, (unsigned)((s ? fi.image_len : 0) / 1024), hw,
             (unsigned)c.manager.target(), bl_state, rc_name, last_rc, dl,
             c.serving ? "on" : "off", (unsigned)c.manager.servedCount(),
             (unsigned)c.allow.count(), tenv ? tenv : "?");
#else
    snprintf(reply, 160,
             "OTA | this fw %s (%uK) hw=%s | %s | serving:%s (%u) | keys:%u | target:%08X (%s)",
             selfhx, (unsigned)((s ? fi.image_len : 0) / 1024), hw, dl,
             c.serving ? "on" : "off", (unsigned)c.manager.servedCount(),
             (unsigned)c.allow.count(), (unsigned)c.manager.target(), tenv ? tenv : "?");
#endif
#endif

  // ---- admin OTA stats: crypto identities (our fw's content-id + body_hash), serving set, live fetch,
  //      policy - one dense line. The remote-admin CLI path is admin-gated, so this is admin-only over the
  //      mesh (send it from the app's repeater command screen, or the WiFi/serial OTA console).
  } else if (is_cmd(a, "stats", &rest)) {
    // our running fw's merkle content-id (mid) comes from the self serve entry; the EndF body_hash (fw
    // identity, matched against a delta base) is separate - surface BOTH (only body_hash showed before).
    const OtaManager::ServeEntry* self = nullptr;
    for (uint8_t i = 0; i < c.manager.servedCount(); i++) {
      const OtaManager::ServeEntry* e = c.manager.servedEntry(i);
      if (e && e->is_self) { self = e; break; }
    }
    SelfFwInfo fi; bool sok = ota_self_firmware(fi);
    char midhx[9], bodyhx[9], verbuf[20], dighx[9];
    if (self)            mesh::Utils::toHex(midhx, self->mid, 4);        else strcpy(midhx, "?");
    if (sok && fi.valid) mesh::Utils::toHex(bodyhx, fi.body_hash, 4);    else strcpy(bodyhx, "?");
    if (self)            ver_str(verbuf, sizeof verbuf, self->fw_version); else strcpy(verbuf, "v?");
    uint8_t dig[4]; c.manager.servedDigest(dig); mesh::Utils::toHex(dighx, dig, 4);
    OtaManager::FetchState fs = c.manager.fetchState();
    char fbuf[72];
    if (fs == OtaManager::IDLE) {
      strcpy(fbuf, "fetch idle");
    } else {
      char fmid[9]; mesh::Utils::toHex(fmid, c.manager.fetchManifestId(), 4);
      unsigned have = (unsigned)c.manager.blocksHave(), tot = (unsigned)c.manager.blocksTotal();
      unsigned pct = tot ? (unsigned)((uint64_t)have * 100 / tot) : 0;
      unsigned age = c.session_started_ms ? (unsigned)((millis() - c.session_started_ms) / 1000) : 0;
      if (fs == OtaManager::FAILED)
        snprintf(fbuf, sizeof fbuf, "fetch failed:%s %u/%u id=%s",
                 fetch_error_word(c.manager.fetchError()), have, tot, fmid);
      else
        snprintf(fbuf, sizeof fbuf, "fetch %s %u/%u %u%% id=%s %us",
                 state_short(fs), have, tot, pct, fmid, age);
    }
    uint8_t af = c.manager.autofetch();
    snprintf(reply, 160, "OTA | fw %s id=%s body=%s %ub %uK | serv %u dg=%s | %s | af=%s hops=%u",
             verbuf, midhx, bodyhx, (unsigned)(self ? self->have_count : 0),
             (unsigned)((sok ? fi.image_len : 0) / 1024), (unsigned)c.manager.servedCount(), dighx, fbuf,
             af == OtaManager::AUTOFETCH_ANY ? "any" : af == OtaManager::AUTOFETCH_SIGNED ? "signed" : "off",
             (unsigned)c.manager.max_hops());

  // ---- what's available around me (catalogued from beacons + OTA_HAVE), best/most-recent first ----
  } else if (is_cmd(a, "neighbors|nbrs|updates|ls|n", &rest)) {
    // Kick a fresh round of catalog queries (async - rows arrive over the next seconds); render what we
    // have now in plain words. The reply buffer is 160 B (serial / one LoRa packet for remote-admin), so
    // the retained protocol catalog is rendered two rows at a time.
    uint16_t page;
    if (!parse_page(rest, page)) { strcpy(reply, "ERR usage: ota ls [page]"); return true; }
    c.manager.queryAll();
    const int CAP = 160;
    static const uint8_t PAGE_SIZE = 2;
    uint16_t count = c.manager.catalogCount();
    uint16_t pages = count ? (uint16_t)((count + PAGE_SIZE - 1) / PAGE_SIZE) : 1;
    if (page > pages) {
      snprintf(reply, CAP, "ERR update page %u out of range (1-%u)", (unsigned)page, (unsigned)pages);
      return true;
    }
    int n = snprintf(reply, CAP, "Updates %u/%u (%u src; refreshing):",
                     (unsigned)page, (unsigned)pages, (unsigned)c.manager.sourceCount());
    OtaManager::FetchState fs = c.manager.fetchState();
    const uint8_t* cur = (fs != OtaManager::IDLE) ? c.manager.fetchManifestId() : nullptr;
    uint32_t myt = c.manager.target();   // effective target (EndF identity if present, else build flag)
#if defined(NRF52_PLATFORM)
    const OtaBlCaps& list_bl = c.bootloaderAppCaps();
#if defined(OTA_QSPI_BOOTLOADER_UPDATE) || defined(OTA_INTERNAL_BOOTLOADER_UPDATE) || \
    defined(OTA_SD_BOOTLOADER_UPDATE)
    const OtaBlCaps& list_bl_update = c.bootloaderUpdateCaps();
#endif
#endif
#if defined(NRF52_PLATFORM) && defined(OTA_FLASH_STORE) && !defined(OTA_SD_STORE) && \
    !defined(OTA_QSPI_STORE)
    SelfFwInfo list_self;
    bool list_has_endf = ota_self_firmware(list_self) && list_self.valid;
#endif
#if defined(NRF52_PLATFORM) && defined(OTA_SD_BOOTLOADER_UPDATE)
    SelfFwInfo list_sd_self;
    const bool list_sd_headroom = ota_self_firmware(list_sd_self) &&
        ota_bootloader_scratch_headroom_valid(
            list_sd_self.valid, mota_nrf52_app_base(), list_sd_self.image_len,
            OTA_BOOT_SCRATCH_START) &&
        ota_bootloader_live_bank_preserves_scratch(
            mota_nrf52_app_base(), list_sd_self.image_len,
            OTA_BOOT_SCRATCH_START);
#endif
    uint32_t now = millis(); int shown = 0;
    uint16_t first = (uint16_t)(page - 1) * PAGE_SIZE;
    uint16_t last = first + PAGE_SIZE; if (last > count) last = count;
    for (uint16_t i = first; i < last; i++) {
      const OtaManager::CatRow* h = c.manager.catalogRow(i);
      if (!h || CAP - n < 24) break;
      bool on = cur && memcmp(cur, h->mid, 4) == 0;
      // Tag the active session by real fetch state (not always "downloading" - COMPLETE is ready).
      const char* tag = "";
      if (on) {
        if (fs == OtaManager::COMPLETE)      tag = " [ready]";
        else if (fs == OtaManager::PAUSED)   tag = " [paused]";
        else if (fs == OtaManager::FAILED)   tag = " [failed]";
        else                                 tag = " [downloading]";  // WANT_*/FETCHING
      }
      uint32_t age = (now - h->last_ms) / 1000; if (age > 99999) age = 99999;
      char ver[20]; ver_str(ver, sizeof ver, h->fw_version);
      // Target equality alone is not an install-safety claim. Surface local codec/bootloader limitations,
      // and flag the explicit legacy rescue path when an app-only internal-flash nRF52 has no valid EndF.
      char hwbuf[16];
      const char* fit;
      const char* env = ota_target_env_name(h->target_id);
      const bool boot_package = (h->flags & MFLAG_BOOTLOADER) != 0;
      if (boot_package) {
        bool installable = false;
#if defined(NRF52_PLATFORM) && \
    (defined(OTA_QSPI_BOOTLOADER_UPDATE) || defined(OTA_INTERNAL_BOOTLOADER_UPDATE) || \
     defined(OTA_SD_BOOTLOADER_UPDATE))
        const OtaBootloaderIdentity& bid = c.bootloaderIdentity();
        installable = h->flags == (MFLAG_FULL | MFLAG_SIGNED | MFLAG_BOOTLOADER) &&
                      h->codec == CODEC_FULL && bid.present && bid.crc_ok &&
                      h->target_id == ota_bootloader_target_id(bid) &&
                      ota_bootloader_self_update_caps_valid(list_bl_update);
#if defined(OTA_SD_BOOTLOADER_UPDATE)
        installable = installable && list_sd_headroom;
#endif
#endif
        fit = installable ? "yours" : "bootloader unsupported";
      } else if (myt && h->target_id == myt) {
        bool installable = c.manager.codecOk(h->codec);
#if defined(NRF52_PLATFORM)
        installable = installable && list_bl.present && list_bl.apply_abi >= MOTA_FORMAT_VER
                   && h->codec < 16 && (list_bl.codec_mask & (1u << h->codec));
#if defined(OTA_SD_STORE)
        installable = installable && (list_bl.storage_flags & OTA_BL_STORAGE_SD);
#elif defined(OTA_QSPI_STORE)
        installable = installable && (list_bl.storage_flags & OTA_BL_STORAGE_QSPI);
#endif
#endif
        if (!installable) fit = "unsupported";
#if defined(NRF52_PLATFORM) && defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
        else if (!list_has_endf) fit = "no EndF; local recovery";
#elif defined(NRF52_PLATFORM) && defined(OTA_FLASH_STORE) && !defined(OTA_SD_STORE) && \
    !defined(OTA_QSPI_STORE)
        else if (!list_has_endf) fit = "rescue";
#endif
        else fit = "same target";
      } else if (env)               fit = env;
      else if (h->target_id == 0)   fit = "?";
      else { snprintf(hwbuf, sizeof hwbuf, "hw %08X", (unsigned)h->target_id); fit = hwbuf; }
      char midhx[9]; mesh::Utils::toHex(midhx, h->mid, 4);
      n += snprintf(reply + n, CAP - n, "\n %u) %s %s %s [%s] %un %us%s",
                    (unsigned)(i + 1), midhx, ver, codec_kind(h->flags, h->codec), fit,
                    (unsigned)h->n_seeders, (unsigned)age, tag);
      shown++;
    }
    if (shown == 0) strcpy(reply, "No updates seen yet - re-run `ota ls` in a few seconds (just asked around).");

  // ---- start fetching a specific catalogued mOTA (by list index or manifest_id) ----
  } else if (is_cmd(a, "pull|get|download", &rest)) {
#if defined(NRF52_PLATFORM) && defined(OTA_FLASH_STORE) && !defined(OTA_SD_STORE) && \
    !defined(OTA_QSPI_STORE) && !defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
    const char* pull_usage = "usage: ota pull <id> flash [rescue] | folder [validate]   (see `ota ls`)";
#else
    const char* pull_usage = "usage: ota pull <id> flash | folder [validate]   (see `ota ls`)";
#endif
    const char* p = rest; while (*p == ' ') p++;
    // split into "<selector> [destination]": selector = #N / N (catalogue index) or mid hex; destination =
    // flash | folder (MANDATORY - `folder` captures the .mota onto the connected motatool folder as <mid>.mota).
    char selstr[24]; int i = 0;
    while (p[i] && p[i] != ' ' && i < (int)sizeof(selstr) - 1) { selstr[i] = p[i]; i++; }
    selstr[i] = 0;
    const char* dst = p + i; while (*dst == ' ') dst++;
    if (selstr[0] == 0) { strcpy(reply, pull_usage); return true; }
    // resolve the catalogue row (index or explicit manifest_id)
    const OtaManager::CatRow* sel = nullptr; uint8_t mid[4];
    // An eight-digit all-numeric manifest ID is still an ID, not a huge list index. Bare short decimal
    // values retain the legacy index form; `#N` is the unambiguous explicit index spelling.
    size_t selector_len = strlen(selstr);
    bool explicit_index = selstr[0] == '#';
    const char* index_text = explicit_index ? selstr + 1 : selstr;
    bool decimal = *index_text != 0;
    uint16_t index = 0;
    for (const char* x = index_text; *x && decimal; x++) {
      if (*x < '0' || *x > '9') decimal = false;
      else if (index > 255 / 10 || (index == 255 / 10 && (uint8_t)(*x - '0') > 255 % 10)) decimal = false;
      else index = (uint16_t)(index * 10 + (uint8_t)(*x - '0'));
    }
    bool use_index = explicit_index || (selector_len != 8 && decimal);
    if (use_index && decimal) {
      if (index >= 1 && index <= c.manager.catalogCount()) sel = c.manager.catalogRow((uint8_t)(index - 1));
    } else if (!explicit_index && selector_len == 8 && mesh::Utils::fromHex(mid, 4, selstr)) {
      for (uint8_t k = 0; k < c.manager.catalogCount(); k++)
        if (memcmp(c.manager.catalogRow(k)->mid, mid, 4) == 0) { sel = c.manager.catalogRow(k); break; }
    }
    if (!sel) { strcpy(reply, "ERR no such update (copy its eight-digit ID from `ota ls`)"); return true; }
    // destination is MANDATORY: with none given, show the choices (flash always; folder iff a link is up).
    if (*dst == 0) {
#if defined(OTA_SEEDER_ONLY)
      if (c.folder_dest)
        snprintf(reply, 160, "choose a destination: `ota pull %s folder`  (folder: %s)",
                 selstr, c.folder_dest_info);
      else
        strcpy(reply, "ERR no folder connected (run motatool serve --tcp)");
#else
      if (c.folder_dest)
        snprintf(reply, 160, "choose a destination: `ota pull %s flash` | `ota pull %s folder`  (folder: %s)",
                 selstr, selstr, c.folder_dest_info);
      else
        snprintf(reply, 160, "choose a destination: `ota pull %s flash`  (folder: none connected - motatool serve)",
                 selstr);
#endif
      return true;
    }
    if (c.apply_pending) { strcpy(reply, "ERR busy applying"); return true; }
    // Parse exact destination/options. Prefix matches used to accept typos such as "flashgarbage" and an
    // ignored third token, which is especially unsafe for the explicit no-EndF rescue acknowledgement.
    char destination[8] = {0}, option[10] = {0}, extra[2] = {0};
    int parts = sscanf(dst, "%7s %9s %1s", destination, option, extra);
    if (parts < 1 || parts > 2) {
      snprintf(reply, 160, "ERR %s", pull_usage);
      return true;
    }
    bool to_flash = strcmp(destination, "flash") == 0;
    bool to_folder = strcmp(destination, "folder") == 0;
    bool validate = to_folder && parts == 2 && strcmp(option, "validate") == 0;
    bool rescue = to_flash && parts == 2 && strcmp(option, "rescue") == 0;
    if ((!to_flash && !to_folder) || (parts == 2 && !validate && !rescue)) {
      snprintf(reply, 160, "ERR %s", pull_usage);
      return true;
    }

    uint8_t selmid[4]; uint32_t seltgt = sel->target_id; uint8_t selcodec = sel->codec;
    const uint8_t selflags = sel->flags;
    const bool selboot = (selflags & MFLAG_BOOTLOADER) != 0;
    memcpy(selmid, sel->mid, 4);                         // sel may move when catalog traffic arrives

#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
    c.stopSdCacheFetch();                                // explicit operator work owns the receive slot
#endif
    OtaManager::FetchState current = c.manager.fetchState();
    if (current != OtaManager::IDLE && current != OtaManager::FAILED) {
      snprintf(reply, 160, "ERR OTA slot is %s; use `ota cancel` before replacing it", state_word(current));
      return true;
    }

    OtaStore* store = nullptr; const char* dname = nullptr;
    if (to_flash) {
#if defined(OTA_SEEDER_ONLY)
      strcpy(reply, "ERR seeder-only build cannot stage or install firmware; use `folder`");
      return true;
#else
      if (selboot) {
#if defined(NRF52_PLATFORM) && \
    (defined(OTA_QSPI_BOOTLOADER_UPDATE) || defined(OTA_INTERNAL_BOOTLOADER_UPDATE) || \
     defined(OTA_SD_BOOTLOADER_UPDATE))
        const OtaBootloaderIdentity& bid = c.bootloaderIdentity();
        const OtaBlCaps& bl = c.bootloaderUpdateCaps();
        if (selflags != (MFLAG_FULL | MFLAG_SIGNED | MFLAG_BOOTLOADER) ||
            selcodec != CODEC_FULL) {
          strcpy(reply, "ERR malformed bootloader catalog row; capture it to folder for inspection");
          return true;
        }
        if (!bid.present || !bid.crc_ok || seltgt != ota_bootloader_target_id(bid)) {
          strcpy(reply, "ERR bootloader package does not match this installed bootloader identity");
          return true;
        }
        if (!ota_bootloader_self_update_caps_valid(bl)) {
          strcpy(reply, "ERR installed bootloader lacks safe LoRa bootloader-update support");
          return true;
        }
#if defined(OTA_SD_BOOTLOADER_UPDATE)
        SelfFwInfo sd_self;
        if (!ota_self_firmware(sd_self) ||
            !ota_bootloader_scratch_headroom_valid(
                sd_self.valid, mota_nrf52_app_base(), sd_self.image_len,
                OTA_BOOT_SCRATCH_START) ||
            !ota_bootloader_live_bank_preserves_scratch(
                mota_nrf52_app_base(), sd_self.image_len,
                OTA_BOOT_SCRATCH_START)) {
          strcpy(reply, "ERR running firmware/settings do not preserve E0000 scratch; use local DFU/SWD");
          return true;
        }
#endif
#else
        strcpy(reply, "ERR this build cannot install bootloader packages; use folder capture or USB DFU");
        return true;
#endif
      } else if (!c.manager.codecOk(selcodec)) {
        snprintf(reply, 160,
                 "ERR %s codec %u cannot be installed by this build; use `ota pull %s folder` to capture it",
                 codec_kind(selflags, selcodec), (unsigned)selcodec, selstr);
        return true;
      }
#if defined(NRF52_PLATFORM)
      const OtaBlCaps& bl = selboot ? c.bootloaderUpdateCaps()
                                    : c.bootloaderAppCaps();
      if (!bl.present) {
        strcpy(reply, "ERR bootloader has no mOTA apply support; update it over USB first");
        return true;
      }
      const uint8_t need_abi = selboot ? MOTA_BOOT_FORMAT_VER : MOTA_APP_FORMAT_VER;
      if (bl.apply_abi < need_abi || selcodec >= 16 || !(bl.codec_mask & (1u << selcodec))) {
        snprintf(reply, 160, "ERR bootloader cannot apply mOTA ABI %u codec %u (has abi=%u codecs=0x%x)",
                 need_abi, (unsigned)selcodec, bl.apply_abi, bl.codec_mask);
        return true;
      }
#if defined(OTA_SD_STORE)
      if (!(bl.storage_flags & OTA_BL_STORAGE_SD)) {
        strcpy(reply, "ERR bootloader cannot apply an update staged on SD; update it over USB first");
        return true;
      }
#elif defined(OTA_QSPI_STORE)
      if (!(bl.storage_flags & OTA_BL_STORAGE_QSPI)) {
        strcpy(reply, "ERR bootloader cannot apply an update staged on QSPI; update it over USB first");
        return true;
      }
#endif
#endif
#if defined(NRF52_PLATFORM) && defined(OTA_FLASH_STORE) && !defined(OTA_SD_STORE) && \
    !defined(OTA_QSPI_STORE)
      SelfFwInfo self;
      bool has_endf = ota_self_firmware(self) && self.valid;
#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
      if (!has_endf) {
        strcpy(reply, "ERR no EndF; shared-slot builds refuse internal pulls; recover via USB/BLE DFU or SWD");
        return true;
      }
      if (rescue) {
        strcpy(reply, "ERR rescue is disabled on shared-slot bootloader-update builds; use `flash`");
        return true;
      }
#else
      if (!has_endf && !rescue) {
        snprintf(reply, 160,
                 "ERR no EndF; retry `ota pull %s flash rescue`, then use `ota rescue install <hash16>`",
                 selstr);
        return true;
      }
      if (!has_endf && seltgt != c.manager.target()) {
        strcpy(reply, "ERR rescue requires an update for this exact target");
        return true;
      }
      if (has_endf && rescue) {
        strcpy(reply, "ERR rescue is only for a running firmware with no valid EndF; use `flash`");
        return true;
      }
#endif
#else
      if (rescue) {
        strcpy(reply, "ERR rescue is available only on internal-flash nRF52 builds");
        return true;
      }
#endif
      store = &c.fetch_store; dname = rescue ? "flash+rescue" : "flash";
#endif
    } else if (to_folder) {
      if (!c.folder_dest) { strcpy(reply, "ERR no folder connected (run motatool serve --tcp/--serial)"); return true; }
      c.folder_dest->set_mid(selmid); store = c.folder_dest; dname = validate ? "folder+validate" : "folder";
    }
    c.manager.reset_session();
    c.fetch_to_folder = to_folder;
    c.manager.set_fetch_store(store);                        // stage this pull to the chosen destination
    OtaManager::PullResult result = to_folder
        ? c.manager.pull_archive(selmid, seltgt, validate)
        : c.manager.pull(selmid, seltgt, false);
    char midhx[9]; mesh::Utils::toHex(midhx, selmid, 4);
    if (result != OtaManager::PULL_STARTED && result != OtaManager::PULL_RESUMED) {
      c.manager.reset_session();
      c.fetch_to_folder = false;
      c.manager.set_fetch_store(&c.fetch_store);
      const char* why = result == OtaManager::PULL_NO_STORE ? "no destination store"
                      : result == OtaManager::PULL_BUSY ? "receive slot busy" : "invalid manifest id";
      snprintf(reply, 160, "ERR pull did not start: %s", why);
      return true;
    }
    snprintf(reply, 160, "OK %s mid=%s -> %s (primary traffic)",
             result == OtaManager::PULL_RESUMED ? "resuming" : "pulling", midhx, dname);

  // ---- discard the current session (e.g. a stalled old fetch) to free the slot ----
  } else if (is_cmd(a, "drop|cancel|stop", &rest)) {
    if (c.apply_pending) { strcpy(reply, "ERR update is armed; reboot is pending"); return true; }
    OtaManager::FetchState fs = c.manager.fetchState();
    const bool was_folder = c.fetch_to_folder;
    bool was_sd_archive = false;
    char midhx[9]; strcpy(midhx, "-");
    if (fs != OtaManager::IDLE) mesh::Utils::toHex(midhx, c.manager.fetchManifestId(), 4);
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
    was_sd_archive = c.sdCacheFetching();
    c.stopSdCacheFetch();
#endif
    c.manager.reset_session(); c.manager.want(0); c.manager.want_mid(nullptr);
    if (was_folder && c.folder_dest) c.folder_dest->clear();
    c.fetch_to_folder = false;
    c.manager.set_fetch_store(&c.fetch_store);   // revert to the default flash store (a folder pull switched it)
#if defined(NRF52_PLATFORM) && !defined(OTA_SD_STORE) && !defined(OTA_QSPI_STORE)
    c.manager.set_accept_full(false);
#endif
    const bool discarded = !was_folder && !was_sd_archive &&
                           c.fetch_store.discard();
    // Fetch cancellation and serving are independent. In particular, a
    // manual ESP32 serve view can point into serve_buf, so leave the manager
    // view and its caller-owned buffer intact until `ota dev clear` (which
    // detaches the view before releasing the buffer).
    c.session_started_ms = 0;
    if (was_folder) {
      snprintf(reply, 160,
               "OK dropped folder session (was %c mid=%s); host file left untouched",
               fstate_char(fs), midhx);
    } else if (was_sd_archive) {
      snprintf(reply, 160,
               "OK dropped SD archive session (was %c mid=%s); partial retained for resume",
               fstate_char(fs), midhx);
    } else if (!discarded) {
      snprintf(reply, 160,
               "ERR dropped live session (was %c mid=%s), but persistent OTA slot invalidation failed",
               fstate_char(fs), midhx);
    } else {
      snprintf(reply, 160,
               "OK dropped session (was %c mid=%s); OTA receive slot confirmed clear",
               fstate_char(fs), midhx);
    }

  // ---- broadcast our tiny beacon so peers discover us. If not already serving, set up flash-backed
  //      self-serve first (so we're a real, fetchable source of our own running firmware). ----
  } else if (is_cmd(a, "announce|adv", &rest)) {
#if defined(OTA_SEEDER_ONLY)
    c.manager.announce();
    snprintf(reply, 160, "OK beacon sent (serving=%u host mOTA)",
             (unsigned)c.manager.servedCount());
#else
    if (!c.serving) c.serving = ota_serve_self(c, 0);
    c.manager.announce();
    sprintf(reply, "OK beacon sent (serving=%s)", c.serving ? "self fw" : "nothing");
#endif

  // ---- raw-QSPI staging diagnostics (read-only probe; preserves a latched fetch failure) ----
  } else if (is_cmd(a, "qspi|storage", &rest)) {
#if defined(NRF52_PLATFORM) && defined(OTA_QSPI_STORE)
    // This probe only reads JEDEC/SR1. capacity() deliberately preserves a
    // latched fetch failure so asking for diagnostics cannot erase its cause.
    uint32_t qspi_capacity = c.fetch_store.capacity();
    const char *error = c.fetch_store.last_error();
    snprintf(reply, 160, "QSPI jedec=%06lX size=%luK sr1=%02X stage=%s%s%s",
             (unsigned long)c.fetch_store.jedec_id(),
             (unsigned long)(qspi_capacity / 1024), c.fetch_store.status1(),
             c.fetch_store.last_stage(), error[0] ? " error=" : "", error);
#else
    strcpy(reply, "ERR this build does not use a QSPI OTA store");
#endif

  // ---- running firmware identity (compare against a delta's base_hash) ----
  } else if (is_cmd(a, "self|id", &rest)) {
    SelfFwInfo fi;
    if (!ota_self_firmware(fi) || !fi.valid) { strcpy(reply, "ERR no EndF (firmware lacks the trailer?)"); return true; }
    char hx[17]; mesh::Utils::toHex(hx, fi.body_hash, 8);
    int n = snprintf(reply, 160, "self body=%u image=%u base_hash=%s", (unsigned)fi.body_len, (unsigned)fi.image_len, hx);
#if defined(NRF52_PLATFORM)
    // nRF52 applies via the bootloader, so surface whether THIS device's bootloader can install this store.
    const OtaBlCaps& bl = c.bootloaderAppCaps();   // cached (flash scanned once)
#if defined(OTA_QSPI_STORE)
    uint32_t qspi_capacity = c.fetch_store.capacity();
    n += snprintf(reply + n, 160 - n, " | QSPI store:%s%uK",
                  qspi_capacity ? "" : "ERR ", (unsigned)(qspi_capacity / 1024));
    if (bl.present && (bl.storage_flags & OTA_BL_STORAGE_QSPI))
      snprintf(reply + n, 160 - n, " | bootloader: QSPI apply OK (abi=%u codecs=0x%x)",
               bl.apply_abi, bl.codec_mask);
    else
      snprintf(reply + n, 160 - n,
               " | bootloader: NO QSPI mota-apply support (install will refuse)");
#elif defined(OTA_SD_STORE)
    if (bl.present && (bl.storage_flags & OTA_BL_STORAGE_SD))
      snprintf(reply + n, 160 - n, " | bootloader: SD apply OK (abi=%u codecs=0x%x)", bl.apply_abi, bl.codec_mask);
    else
      snprintf(reply + n, 160 - n, " | bootloader: NO SD mota-apply support (install will refuse)");
#else
    if (bl.present) {
      const uint32_t stage_ceiling = ota_nrf52_effective_stage_ceiling(bl);
      snprintf(reply + n, 160 - n,
               " | bootloader: apply OK (abi=%u codecs=0x%x stage=%05X)",
               bl.apply_abi, bl.codec_mask, (unsigned)stage_ceiling);
    }
    else            snprintf(reply + n, 160 - n, " | bootloader: NO mota-apply support (delta install will refuse)");
#endif
#endif

  } else if (is_cmd(a, "rescue", &rest)) {
    // Recovery-only nRF52 handoff for firmware whose normal EndF validation is broken. This command is
    // deliberately not an alias or automatic fallback: the operator must name `install` and provide
    // the exact 8-byte base hash carried by the already-fetched package. The bootloader independently
    // hashes the running app and refuses a mismatch before writing any application flash.
#if defined(NRF52_PLATFORM) && defined(OTA_FLASH_STORE) && !defined(OTA_SD_STORE) && \
    !defined(OTA_QSPI_STORE) && !defined(OTA_SEEDER_ONLY) && \
    !defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
    if (c.fetch_to_folder) {
      strcpy(reply, "ERR the complete update was captured to a folder, not staged for install; use `ota cancel`");
      return true;
    }
    const char* hash_text = nullptr;
    uint8_t operator_base_hash[8];
    if (!is_cmd(rest, "install", &hash_text) ||
        !mesh::Utils::fromHex(operator_base_hash, sizeof(operator_base_hash), hash_text)) {
      strcpy(reply, "ERR usage: ota rescue install <hash16>");
      return true;
    }
    if (c.manager.fetchState() != OtaManager::COMPLETE || c.fetch_store.staged_size() == 0) {
      sprintf(reply, "ERR no complete update fetched (fetch=%c %u/%u)",
              fstate_char(c.manager.fetchState()), (unsigned)c.manager.blocksHave(),
              (unsigned)c.manager.blocksTotal());
      return true;
    }
    char m2[100];
    bool ok = c.apply_fetched_rescue(operator_base_hash, m2);
    sprintf(reply, "%s | %s", ok ? "OK" : "ERR", m2);
#elif defined(NRF52_PLATFORM) && defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
    strcpy(reply, "ERR rescue is disabled on shared-slot bootloader-update builds; use local DFU/SWD if EndF is invalid");
#else
    strcpy(reply, "ERR rescue requires an internal-flash nRF52 LoRa OTA build");
#endif

  } else if (is_cmd(a, "bootloader|blupdate", &rest)) {
#if defined(NRF52_PLATFORM) && !defined(OTA_SEEDER_ONLY) && \
    (defined(OTA_QSPI_BOOTLOADER_UPDATE) || defined(OTA_INTERNAL_BOOTLOADER_UPDATE) || \
     defined(OTA_SD_BOOTLOADER_UPDATE))
    const OtaBootloaderIdentity& bid = c.bootloaderIdentity();
    const OtaBlCaps& bl = c.bootloaderUpdateCaps();
    if (*rest == 0 || strcmp(rest, "status") == 0) {
      if (!bid.present || !bid.crc_ok) {
        strcpy(reply, "Bootloader update unavailable: installed embedded manifest/CRC is invalid");
        return true;
      }
      if (!ota_bootloader_self_update_caps_valid(bl)) {
        strcpy(reply, "Bootloader update unavailable: installed bootloader supports application OTA only");
        return true;
      }
      MotaManifest staged;
      char midhx[9] = "-", hashhx[17] = "-";
      const bool ready = c.manager.fetchState() == OtaManager::COMPLETE &&
                         staged_manifest(c, staged) && staged.is_bootloader();
      if (ready) {
        mesh::Utils::toHex(midhx, c.manager.fetchManifestId(), 4);
        mesh::Utils::toHex(hashhx, staged.image_hash, 8);
      }
      snprintf(reply, 160, "BL board=%08X target=%08X name=%s crc=%08X abi=%u caps=%02X | staged:%s mid=%s hash=%s",
               (unsigned)bid.board_id, (unsigned)ota_bootloader_target_id(bid),
               bid.device_name, (unsigned)bid.crc32,
               bl.apply_abi, bl.storage_flags, ready ? "ready" : "none", midhx, hashhx);
      return true;
    }
    const char* confirm = nullptr;
    if (!is_cmd(rest, "install", &confirm)) {
      strcpy(reply, "ERR usage: ota bootloader install <MID8> <HASH16>"); return true;
    }
    char midtxt[9] = {0}, hashtxt[17] = {0}, extra[2] = {0};
    if (sscanf(confirm, "%8s %16s %1s", midtxt, hashtxt, extra) != 2) {
      strcpy(reply, "ERR usage: ota bootloader install <MID8> <HASH16>"); return true;
    }
    uint8_t operator_mid[4], operator_hash8[8];
    if (!parse_hex_exact(midtxt, operator_mid, sizeof(operator_mid)) ||
        !parse_hex_exact(hashtxt, operator_hash8, sizeof(operator_hash8))) {
      strcpy(reply, "ERR MID must be 8 hex and HASH must be 16 hex characters"); return true;
    }
    if (c.apply_pending) { strcpy(reply, "ERR another update is already armed"); return true; }
    if (c.fetch_to_folder || c.manager.fetchState() != OtaManager::COMPLETE ||
        c.fetch_store.staged_size() == 0 || !c.manager.fetched_is_bootloader()) {
      strcpy(reply, "ERR no complete bootloader package in local install storage"); return true;
    }
    char m2[100];
    const bool ok = c.apply_fetched_bootloader(operator_mid, operator_hash8, m2);
    snprintf(reply, 160, "%s | %s", ok ? "OK" : "ERR", m2);
#else
    strcpy(reply, "ERR this build cannot update its bootloader over LoRa");
#endif

  } else if (is_cmd(a, "install|apply|applydelta", &rest)) {
    // Apply the fetched update. Destructive (reflashes + reboots) and GATED, not interactive (no "type
    // yes" round-trip - unreliable over LoRa): refuse unless the fetch is COMPLETE, then the apply path
    // validates in order (payload hash -> built-for-this-firmware -> signature/trust) and returns the
    // FIRST failing gate, so the operator knows exactly why it refused; it proceeds only if all pass.
#if defined(OTA_SEEDER_ONLY)
    strcpy(reply, "ERR seeder-only build cannot install firmware via LoRa");
    return true;
#elif defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
    if (c.sdCacheFetching()) {
      strcpy(reply, "ERR SD archive capture is active; use `ota cancel` before installing");
      return true;
    }
#endif
    if (c.fetch_to_folder) {
      strcpy(reply, "ERR the complete update was captured to a folder, not staged for install; use `ota cancel`");
      return true;
    }
    if (c.manager.fetchState() != OtaManager::COMPLETE || c.fetch_store.staged_size() == 0) {
      sprintf(reply, "ERR no complete update fetched (fetch=%c %u/%u)",
              fstate_char(c.manager.fetchState()), (unsigned)c.manager.blocksHave(),
              (unsigned)c.manager.blocksTotal());
      return true;
    }
    // On success the slot is armed but NOT yet rebooted - defer so this reply reaches the operator first;
    // the mesh loop reboots once it has been transmitted (same path used by auto-install).
    char m2[100];
    bool ok = c.apply_fetched(m2);
    sprintf(reply, "%s | %s", ok ? "OK" : "ERR", m2);

  // ---- external folder relay: advertise + serve `.mota` from a host daemon over the seeder UART, so the
  //      node hosts MANY images (any architecture) it doesn't hold in flash. Trustless (fetchers verify). --
  } else if (is_cmd(a, "folder|fold", &rest)) {
    const char* p = rest;
    if (strcmp(p, "on") == 0) {
#if defined(OTA_FOLDER_SERIAL)
#if !defined(OTA_SEEDER_ONLY)
      if (!c.serving) c.serving = ota_serve_self(c, 0);   // keep serving our own fw alongside the folder
#endif
      char m2[120]; c.attach_folder(m2, sizeof(m2)); c.manager.announce();
      strncpy(reply, m2, 159); reply[159] = 0;
#else
      strcpy(reply, "ERR not built with OTA_FOLDER_SERIAL (set the seeder UART in platformio.ini)");
#endif
    } else if (strcmp(p, "off") == 0) {
      c.detach_folder(); c.manager.announce();
#if defined(OTA_SEEDER_ONLY)
      strcpy(reply, "OK folder detached (serving nothing)");
#else
      strcpy(reply, "OK folder detached (still serving own fw)");
#endif
    } else if (*p == 0) {                                 // status + list served entries (* = our own fw)
      uint16_t offered = 0, advertised = 0;
      bool have_stats = c.folderSourceStats(offered, advertised);
      int n = have_stats
          ? snprintf(reply, 159, "folder=%s host=%u/%u serving=%u:", c.folder_active ? "on" : "off",
                     (unsigned)advertised, (unsigned)offered, (unsigned)c.manager.servedCount())
          : snprintf(reply, 159, "folder=%s serving=%u:", c.folder_active ? "on" : "off",
                     (unsigned)c.manager.servedCount());
      for (uint8_t i = 0; i < c.manager.servedCount() && n < 148; i++) {
        const OtaManager::ServeEntry* e = c.manager.servedEntry(i);
        if (!e) break;
        char midhx[9]; mesh::Utils::toHex(midhx, e->mid, 4);
        n += snprintf(reply + n, 159 - n, " %s%s/%08X", e->is_self ? "*" : "", midhx, (unsigned)e->target_id);
      }
    } else {
      strcpy(reply, "ERR usage: ota folder [on|off]");
    }

  // ---- SD OTA archive: default-on capture of every advertised mOTA, retained and served after reboot. ----
  } else if (is_cmd(a, "cache|archive|seed", &rest)) {
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
    const char* p = rest;
    if (strcmp(p, "on") == 0 || strcmp(p, "off") == 0) {
      bool enabled = p[1] == 'n';
      if (!c.setSdCacheEnabled(enabled)) {
        snprintf(reply, 160, "ERR SD OTA archive setting failed: %s", c.sd_cache.last_error());
      } else {
        snprintf(reply, 160, "OK SD OTA archive capture %s (saved on card; cached files still seed)",
                 enabled ? "on" : "off");
      }
    } else if (*p != 0) {
      strcpy(reply, "ERR usage: ota cache [on|off]");
    } else if (!c.ensureSdCache()) {
      snprintf(reply, 160, "SD OTA archive unavailable: %s", c.sd_cache.last_error());
    } else {
      OtaManager::FetchState fs = c.manager.fetchState();
      if (c.sdCacheFetching()) {
        char midhx[9]; mesh::Utils::toHex(midhx, c.manager.fetchManifestId(), 4);
        unsigned have = (unsigned)c.manager.blocksHave(), total = (unsigned)c.manager.blocksTotal();
        snprintf(reply, 160, "SD OTA archive: capture=%s cached=%u, saving %s %u/%u",
                 c.sd_cache.autoCaptureEnabled() ? "on" : "off",
                 (unsigned)c.sd_cache.capturedCount(), midhx, have, total);
      } else {
        snprintf(reply, 160, "SD OTA archive: capture=%s cached=%u, %s; `ota cache off` stops new saves",
                 c.sd_cache.autoCaptureEnabled() ? "on" : "off",
                 (unsigned)c.sd_cache.capturedCount(), state_short(fs));
      }
    }
#else
    strcpy(reply, "ERR this target has no SD-backed OTA archive");
#endif

  // ---- policy config (persisted via NodePrefs). conservative defaults: autofetch/autoinstall off ----
  } else if (is_cmd(a, "config|cfg|set", &rest)) {
    const char* p = rest;
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
    if (strncmp(p, "cache ", 6) == 0 || strncmp(p, "sdseed ", 7) == 0) {
      const char* v = p + (p[0] == 'c' ? 6 : 7);
      bool enabled;
      if (strcmp(v, "on") == 0) enabled = true;
      else if (strcmp(v, "off") == 0) enabled = false;
      else { strcpy(reply, "ERR usage: ota config cache <on|off>"); return true; }
      if (!c.setSdCacheEnabled(enabled))
        snprintf(reply, 160, "ERR SD OTA archive setting failed: %s", c.sd_cache.last_error());
      else
        snprintf(reply, 160, "OK SD OTA archive capture %s (saved on card)", enabled ? "on" : "off");
    } else
#endif
#if defined(OTA_SEEDER_ONLY)
    if (strncmp(p, "autofetch ", 10) == 0 || strncmp(p, "autoinstall ", 12) == 0) {
      strcpy(reply, "ERR seeder-only build keeps autofetch and autoinstall off");
    } else
#endif
    if (strncmp(p, "autofetch ", 10) == 0) {
      const char* v = p + 10;
      uint8_t pol = strncmp(v, "any", 3) == 0    ? OtaManager::AUTOFETCH_ANY
                  : strncmp(v, "signed", 6) == 0 ? OtaManager::AUTOFETCH_SIGNED
                  : strncmp(v, "off", 3) == 0    ? OtaManager::AUTOFETCH_OFF : 0xFF;
      if (pol == 0xFF) { strcpy(reply, "ERR usage: ota config autofetch <off|any|signed>"); return true; }
      c.manager.set_autofetch(pol); c.config_dirty = true; strcpy(reply, "OK autofetch updated (saved)");
    } else if (strncmp(p, "autoinstall ", 12) == 0) {
      const char* v = p + 12;
      uint8_t pol = strncmp(v, "trusted", 7) == 0 ? OtaContext::AUTOINSTALL_TRUSTED
                  : strncmp(v, "off", 3) == 0     ? OtaContext::AUTOINSTALL_OFF : 0xFF;
      if (pol == 0xFF) { strcpy(reply, "ERR usage: ota config autoinstall <off|trusted>"); return true; }
      c.autoinstall = pol; c.config_dirty = true; strcpy(reply, "OK autoinstall updated (saved)");
    } else if (strncmp(p, "checkpoint ", 11) == 0) {    // resume checkpoint cadence (blocks; 0=never)
      long n = atol(p + 11);
      if (n < 0 || n > 4096) { strcpy(reply, "ERR usage: ota config checkpoint <0..4096>  (blocks; 0=never)"); return true; }
      c.manager.set_checkpoint_blocks((uint16_t)n); c.config_dirty = true;
      sprintf(reply, "OK checkpoint every %ld blocks (saved)%s", n, n == 0 ? " - periodic resume disabled" : "");
    } else if (strncmp(p, "advert ", 7) == 0) {         // beacon re-advertise cadence (minutes; 0=disable)
      long m = atol(p + 7);
      if (m < 0 || m > 10080) { strcpy(reply, "ERR usage: ota config advert <0..10080>  (minutes; 0=disable)"); return true; }
      c.manager.set_advert_mins((uint16_t)m); c.config_dirty = true;
      sprintf(reply, "OK re-advertise every %ld min (saved)%s", m, m == 0 ? " - periodic advert disabled" : "");
    } else if (strncmp(p, "hops ", 5) == 0) {           // OTA flood reach in hops (0 = direct only)
      long h = atol(p + 5);
      if (h < 0 || h > 8) { strcpy(reply, "ERR usage: ota config hops <0..8>  (hops; 0 = direct only)"); return true; }
      c.manager.set_max_hops((uint8_t)h); c.config_dirty = true;
      sprintf(reply, "OK OTA reach = %ld hop%s (saved)%s", h, h == 1 ? "" : "s", h == 0 ? " - direct only" : "");
    } else {                                            // show current policy
      uint8_t af = c.manager.autofetch();
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
      bool cache_ready = c.ensureSdCache();
      sprintf(reply, "ota config: cache=%s/%u autofetch=%s autoinstall=%s checkpoint=%u advert=%umin hops=%u keys=%u",
              cache_ready ? (c.sd_cache.autoCaptureEnabled() ? "on" : "off") : "unavailable",
              cache_ready ? (unsigned)c.sd_cache.capturedCount() : 0,
              af == OtaManager::AUTOFETCH_ANY ? "any" : af == OtaManager::AUTOFETCH_SIGNED ? "signed" : "off",
              c.autoinstall == OtaContext::AUTOINSTALL_TRUSTED ? "trusted" : "off",
              (unsigned)c.manager.checkpoint_blocks(), (unsigned)c.manager.advert_mins(),
              (unsigned)c.manager.max_hops(), (unsigned)c.allow.count());
#else
#if defined(OTA_SEEDER_ONLY)
      sprintf(reply, "ota config: mode=seeder-only autofetch=off autoinstall=off checkpoint=%u advert=%umin hops=%u",
              (unsigned)c.manager.checkpoint_blocks(), (unsigned)c.manager.advert_mins(),
              (unsigned)c.manager.max_hops());
#else
      sprintf(reply, "ota config: autofetch=%s autoinstall=%s checkpoint=%u advert=%umin hops=%u keys=%u  (persisted)",
              af == OtaManager::AUTOFETCH_ANY ? "any" : af == OtaManager::AUTOFETCH_SIGNED ? "signed" : "off",
              c.autoinstall == OtaContext::AUTOINSTALL_TRUSTED ? "trusted" : "off",
              (unsigned)c.manager.checkpoint_blocks(), (unsigned)c.manager.advert_mins(),
              (unsigned)c.manager.max_hops(), (unsigned)c.allow.count());
#endif
#endif
    }

  // ---- trusted signer allowlist (security config; persisted): `ota key add|rm <hex>` / `ota key` lists ----
  } else if (is_cmd(a, "key|keys", &rest)) {
    const char* p = rest;
    if (strncmp(p, "add ", 4) == 0) {
      uint8_t pub[32];
      if (mesh::Utils::fromHex(pub, 32, p + 4) && c.allow.add(pub)) { c.config_dirty = true; strcpy(reply, "OK key added (saved)"); }
      else strcpy(reply, "ERR key");
    } else if (strncmp(p, "rm ", 3) == 0 || strncmp(p, "remove ", 7) == 0) {
      uint8_t pub[32]; const char* h = p + (p[0] == 'r' && p[1] == 'm' ? 3 : 7);
      if (mesh::Utils::fromHex(pub, 32, h) && c.allow.remove(pub)) { c.config_dirty = true; strcpy(reply, "OK removed (saved)"); }
      else strcpy(reply, "ERR");
    } else {                                              // bare `ota key` (or `key list`) -> show them
      int n = snprintf(reply, 160, "trusted signer keys (%u):", (unsigned)c.allow.count());
      for (uint8_t i = 0; i < c.allow.count() && n < 140; i++) {
        char hx[17]; mesh::Utils::toHex(hx, c.allow.get(i), 8);
        n += snprintf(reply + n, 160 - n, " %s", hx);
      }
      if (c.allow.count() == 0) strcpy(reply, "no trusted signer keys yet (add one with `ota key add <hex>`)");
    }

  } else {
    strcpy(reply, "Unknown OTA command. Type `ota help`.");
  }
  return true;
}

// Raw / internal primitives (manual content load + low-level apply steps), under `ota dev ...`.
static bool handle_dev(const char* d, char* reply, OtaContext& c) {
#if defined(OTA_SEEDER_ONLY)
  (void)d;
  (void)c;
  strcpy(reply, "ERR ota dev staging/apply is disabled on this seeder-only build");
#else
  if (strncmp(d, "stage ", 6) == 0) {
    uint32_t sz = parse_u32(d + 6);
    if (sz == 0 || sz > OTA_SERVE_BUF_SIZE) { sprintf(reply, "ERR size 1..%u", OTA_SERVE_BUF_SIZE); }
    else if (!c.ensureServeBuffer()) { strcpy(reply, "ERR stage OOM"); }
    else { c.manager.clear_primary(); c.serving = false;
           memset(c.serve_buf, 0xFF, sz); c.serve_expected = sz;
           sprintf(reply, "OK stage %u bytes", (unsigned)sz); }

  } else if (strncmp(d, "recv ", 5) == 0) {
    const char* p = d + 5; uint32_t off = parse_u32(p);
    const char* hex = strchr(p, ' ');
    if (!hex) { strcpy(reply, "ERR usage: ota dev recv <off> <hex>"); return true; }
    hex++;
    int blen = (int)strlen(hex) / 2;
    uint8_t tmp[80];
    if (blen <= 0 || blen > (int)sizeof(tmp) || !mesh::Utils::fromHex(tmp, blen, hex)) strcpy(reply, "ERR hex");
    else if (!c.serve_buf || off + blen > c.serve_expected) strcpy(reply, "ERR off>size (stage first)");
    else { memcpy(c.serve_buf + off, tmp, blen); sprintf(reply, "OK %d@%u", blen, (unsigned)off); }

  } else if (strncmp(d, "serve self", 10) == 0) {     // host our own running firmware, served from flash
    if (ota_serve_self(c, 0)) {
      c.serving = true;
      char midhx[9]; mesh::Utils::toHex(midhx, c.serve_self_manifest + 20, 4);
      uint32_t img = (uint32_t)c.serve_self_manifest[11] | ((uint32_t)c.serve_self_manifest[12] << 8)
                   | ((uint32_t)c.serve_self_manifest[13] << 16) | ((uint32_t)c.serve_self_manifest[14] << 24);
      sprintf(reply, "OK serving self fw mid=%s (%u B, flash-backed) - peers can pull it", midhx, (unsigned)img);
    } else strcpy(reply, "ERR serve self (no EndF / image too big / OOM)");
  } else if (strncmp(d, "serve", 5) == 0) {
    if (!c.serve_buf || c.serve_expected == 0) {
      strcpy(reply, "ERR nothing staged");
      return true;
    }
    c.serving = c.manager.serve(c.serve_buf, c.serve_expected);
    if (!c.serving) { strcpy(reply, "ERR serve (bad .mota)"); return true; }
    VerifyResult r = ota_verify(c.serve_buf, c.serve_expected, c.allow);
    sprintf(reply, "OK serving | root=%d payload=%d img=%d sig=%d trust=%d",
            r.root_ok, r.payload_ok, r.image_ok, r.sig_ok, r.trusted);

  } else if (is_cmd(d, "resume", &d)) {     // explicit re-adopt of a known/active staged MID (test/debug)
    uint8_t mid[4];
    if (*d) {
      if (!parse_hex_exact(d, mid, sizeof(mid))) {
        strcpy(reply, "ERR usage: ota dev resume [mid8]");
        return true;
      }
    } else {
      // With no argument, reuse only an active/requested session MID. After a reboot the operator must
      // name the persisted MID explicitly; nullptr is reserved for policy-governed automatic adoption.
      if (c.manager.fetchState() == OtaManager::IDLE) {
        strcpy(reply, "ERR usage: ota dev resume <mid8>");
        return true;
      }
      memcpy(mid, c.manager.fetchManifestId(), sizeof(mid));
    }
    bool ok = c.manager.resumeStagedExplicit(mid, 0);
    sprintf(reply, "%s resume: sess=%c %u/%u", ok ? "OK" : "ERR", fstate_char(c.manager.fetchState()),
            (unsigned)c.manager.blocksHave(), (unsigned)c.manager.blocksTotal());

  } else if (strncmp(d, "announce", 8) == 0) {
    if (!c.serving) { strcpy(reply, "ERR not serving (ota dev serve first)"); return true; }
    c.manager.announce();
    strcpy(reply, "OK announced");

  } else if (strncmp(d, "verify", 6) == 0) {
    if (c.fetch_to_folder && c.manager.fetchState() == OtaManager::COMPLETE) {
      strcpy(reply, "ERR completed fetch is in the host folder, not local verification storage");
      return true;
    }
#if defined(NRF52_PLATFORM) && (defined(OTA_SD_STORE) || defined(OTA_QSPI_STORE))
    if (c.manager.fetchState() == OtaManager::COMPLETE) {
      VerifyResult r = ota_verify(static_cast<const OtaStore&>(c.fetch_store), c.allow);
      sprintf(reply, "verify parsed=%d root=%d payload=%d img=%d signed=%d sig=%d trust=%d | ok=%d auto=%d",
              r.parsed, r.root_ok, r.payload_ok, r.image_ok, r.is_signed, r.sig_ok, r.trusted,
              r.integrity_ok(), r.auto_appliable());
      return true;
    }
#endif
    const uint8_t* buf; uint32_t len;
#if defined(NRF52_PLATFORM) && (defined(OTA_SD_STORE) || defined(OTA_QSPI_STORE))
    buf = c.serve_buf; len = c.serve_buf ? c.serve_expected : 0;
#else
    if (c.manager.fetchState() == OtaManager::COMPLETE) { buf = c.fetch_store.data(); len = c.fetch_store.staged_size(); }
    else { buf = c.serve_buf; len = c.serve_buf ? c.serve_expected : 0; }
#endif
    if (len == 0 || !buf) { strcpy(reply, "ERR nothing to verify (flash-staged: applydelta verifies)"); return true; }
    VerifyResult r = ota_verify(buf, len, c.allow);
    sprintf(reply, "verify parsed=%d root=%d payload=%d img=%d signed=%d sig=%d trust=%d | ok=%d auto=%d",
            r.parsed, r.root_ok, r.payload_ok, r.image_ok, r.is_signed, r.sig_ok, r.trusted,
            r.integrity_ok(), r.auto_appliable());

  } else if (strncmp(d, "want ", 5) == 0) {
    const char* p = d + 5; while (*p == ' ') p++;
    if (strncmp(p, "auto", 4) == 0) { c.manager.want(0); c.manager.want_mid(nullptr); strcpy(reply, "OK auto (own target only)"); }
    else { uint32_t t = (uint32_t)strtoul(p, nullptr, 16); c.manager.want(t); c.manager.want_mid(nullptr);
           sprintf(reply, "OK cross-target: will fetch %08X (you ensure HW compatible)", (unsigned)t); }

  } else if (strncmp(d, "apply", 5) == 0) {
    const char* sub = d + 5; while (*sub == ' ') sub++;
    if (strncmp(sub, "slot", 4) == 0) {
      uint32_t addr = 0, size = 0;
      if (ota_apply_slot_info(&addr, &size)) sprintf(reply, "inactive slot addr=0x%X size=%u", (unsigned)addr, (unsigned)size);
      else strcpy(reply, "ERR no A/B slot (apply unsupported on this build)");
    } else if (strncmp(sub, "manifest", 8) == 0) {
      if (c.serve_buf && ota_apply_set_manifest(c.serve_buf, c.serve_expected, c.allow, c.apply_st))
        sprintf(reply, "manifest ok img=%u sig=%d trust=%d", (unsigned)c.apply_st.image_size, c.apply_st.sig_ok, c.apply_st.trusted);
      else strcpy(reply, "ERR manifest parse / not full-image / unsupported");
    } else if (strncmp(sub, "verify", 6) == 0) {
      bool ok = ota_apply_verify_slot(c.apply_st);
      sprintf(reply, "slot image_hash %s (size=%u)", ok ? "MATCH" : "MISMATCH", (unsigned)c.apply_st.image_size);
    } else if (strncmp(sub, "commit", 6) == 0) {
      if (!c.apply_st.slot_ok) { strcpy(reply, "ERR run 'ota dev apply verify' first (slot must match)"); return true; }
      ota_apply_commit();                 // set boot partition + reboot; no return
      strcpy(reply, "ERR commit failed (no A/B slot?)");
    } else {
      strcpy(reply, "ERR ota dev apply (slot|manifest|verify|commit)");
    }

  } else if (strncmp(d, "clear", 5) == 0) {
    const bool was_folder = c.fetch_to_folder;
    bool was_sd_archive = false;
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
    was_sd_archive = c.sdCacheFetching();
    c.stopSdCacheFetch();
#endif
    c.manager.clear_primary();
    c.serve_expected = 0; c.serving = false; c.releaseServeBuffer();
    c.manager.reset_session();
    if (was_folder && c.folder_dest) c.folder_dest->clear();
    c.fetch_to_folder = false;
    c.manager.set_fetch_store(&c.fetch_store);
    if (was_folder) {
      strcpy(reply, "OK cleared folder session; host file left untouched");
    } else if (was_sd_archive) {
      strcpy(reply, "OK cleared SD archive session; partial retained for resume");
    } else {
      const bool discarded = c.fetch_store.discard();
      strcpy(reply, discarded ? "OK OTA receive slot confirmed clear"
                              : "ERR RAM state cleared; persistent OTA slot invalidation failed");
    }

  } else {
    strcpy(reply, "ota dev: stage|recv|serve|announce|resume [mid8]|verify|want|apply|clear");
  }
#endif
  return true;
}

} // namespace ota
} // namespace mesh
