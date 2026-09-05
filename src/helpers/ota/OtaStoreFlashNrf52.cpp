#include "OtaStoreFlashNrf52.h"

#if defined(NRF52_PLATFORM) && defined(OTA_FLASH_STORE)

#include "OtaSelf.h"
#include "OtaBlInfo.h"
#include "MotaContainer.h"
#include "OtaDebug.h"
#include "OtaByteIO.h"           // align_down / rd_u32le (flash-page geometry + header read)
#include "OtaHybridHandoff.h"
#include <string.h>
#include "nrf.h"
#include "flash/flash_nrf5x.h"   // Adafruit core internal-flash driver (SoftDevice-safe; LittleFS path)

namespace mesh {
namespace ota {

namespace {

static void clear_hybrid_auth_record() {
#if defined(OTA_HYBRID_RAM_STORE)
  volatile uint32_t* const retained =
      reinterpret_cast<volatile uint32_t*>(MOTA_HYBRID_AUTH_ADDR);
  for (uint32_t i = 0; i < MOTA_HYBRID_AUTH_LEN / 4u; ++i)
    retained[i] = 0u;
  __DMB();
  __DSB();
#endif
}

static bool publish_hybrid_auth_record(
    const uint8_t record[MOTA_HYBRID_AUTH_LEN]) {
#if defined(OTA_HYBRID_RAM_STORE)
  if (!mota_hybrid_auth_valid(record)) return false;
  volatile uint32_t* const retained =
      reinterpret_cast<volatile uint32_t*>(MOTA_HYBRID_AUTH_ADDR);
  // Invalidate first, publish the body, then make the first magic word live.
  // Any reset during this sequence leaves a record OTAFIX rejects.
  retained[0] = 0u;
  __DMB();
  for (uint32_t i = 1u; i < MOTA_HYBRID_AUTH_LEN / 4u; ++i)
    retained[i] = mota_hybrid_rd32(record + i * 4u);
  __DMB();
  retained[0] = mota_hybrid_rd32(record);
  __DMB();
  __DSB();
  for (uint32_t i = 0; i < MOTA_HYBRID_AUTH_LEN / 4u; ++i)
    if (retained[i] != mota_hybrid_rd32(record + i * 4u)) return false;
  return true;
#else
  (void)record;
  return false;
#endif
}

}  // namespace

OtaStoreFlashNrf52::OtaStoreFlashNrf52() {
  // A descriptor is valid for one immediate software-reset handoff only.
  // OTAFIX consumes it before an application starts; clear any stale bytes if
  // a reset reached this constructor without entering the hybrid path.
  clear_hybrid_auth_record();
}

void OtaStoreFlashNrf52::reset_session() {
  _write_start = 0;
  _stage_ceiling = MOTA_NRF52_STAGE_CEILING_LEGACY;
  _flash_len = 0;
  _ram_len = 0;
  _total = 0;
  _hybrid = false;
  _pay_idx = 0;
  _flushed = false;
  _io_ok = true;
  _planned_bootloader = false;
  _planned_hybrid = false;
  _planned_total = 0;
  _planned_start = 0;
  _planned_flash_len = 0;
  _planned_ram_len = 0;
  memset(_hybrid_handoff, 0, sizeof(_hybrid_handoff));
  _hybrid_handoff_ready = false;
}

// A valid EndF gives the exact live-image extent. Legacy app-only builds keep
// the conservative rescue fallback. Shared internal bootloader-update builds
// reject every package kind without EndF because their normal linker may place
// live application bytes anywhere through the 0xED000 stage ceiling.
static bool protected_app_end(uint32_t app_base, uint32_t stage_ceiling, uint32_t& app_end) {
  SelfFwInfo fi;
  const bool valid = ota_self_firmware(fi) && fi.valid;
#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
  const bool refuse_without_endf = true;
#else
  const bool refuse_without_endf = false;
#endif
  return mota_nrf52_protected_app_end(
      app_base, stage_ceiling, valid, valid ? fi.image_len : 0u,
      refuse_without_endf, app_end);
}

bool OtaStoreFlashNrf52::plan_layout(bool is_full, uint32_t image_size,
                                      uint32_t payload_off, uint32_t payload_size,
                                      bool is_bootloader) {
  _planned_bootloader = false;
  _planned_hybrid = false;
  _planned_total = 0;
  _planned_start = 0;
  _planned_flash_len = 0;
  _planned_ram_len = 0;
  const uint64_t total64 = (uint64_t)payload_off + payload_size + 5u;
  if (total64 > UINT32_MAX) return false;

  if (!is_bootloader) {
#if defined(OTA_HYBRID_RAM_STORE)
    const uint32_t total = (uint32_t)total64;
    const uint32_t app_base = mota_nrf52_app_base();
    const uint32_t stage_ceiling = ota_nrf52_effective_stage_ceiling();
    uint32_t app_end = 0;
    uint32_t start = 0;
    if (!protected_app_end(app_base, stage_ceiling, app_end)) return false;
    uint32_t flash_len = 0, ram_len = 0;
    const OtaRamCaps ram_caps = ota_bootloader_ram_caps();
    // The layout record tells the offline packager to size detools' workspace
    // against this exact split. Use it deterministically for every application
    // delta larger than the mandatory flash page; choosing a smaller pure-
    // flash start merely because the bytes fit could invalidate that geometry.
    if (!is_full && ota_bootloader_supports_hybrid(ram_caps) &&
        stage_ceiling == MOTA_NRF52_STAGE_CEILING_EXPANDED &&
        mota_nrf52_hybrid_stage_plan(
            total, app_base, app_end, stage_ceiling,
            start, flash_len, ram_len)) {
      _planned_hybrid = true;
      _planned_total = total;
      _planned_start = start;
      _planned_flash_len = flash_len;
      _planned_ram_len = ram_len;
      return true;
    }
    // A container no larger than one page cannot have a non-empty SRAM suffix
    // under the frozen split formula. Preserve the established flash path for
    // that edge case; all larger hybrid-profile application packages fail
    // closed if the installed bootloader lacks MOTARAMA.
    if (total <= MOTA_NRF52_FLASH_PAGE)
      return mota_nrf52_stage_plan(
          total, app_base, app_end, stage_ceiling, start);
    return false;
#else
    (void)is_full;
    return true;
#endif
  }
#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
  if (!is_full || image_size != 40u * 1024u || payload_size != 40u * 1024u ||
      payload_off != 365u || total64 != MOTA_NRF52_BOOT_CONTAINER_SIZE ||
      !ota_bootloader_self_update_caps_valid(ota_bootloader_update_caps()))
    return false;

  // This is the release-blocking no-EndF gate: package kind is known before
  // begin(), and no page may be erased until the exact live app extent proves
  // the shared E2000..ED000 slot is free.
  SelfFwInfo fi;
  uint32_t start;
  const uint32_t app_base = mota_nrf52_app_base();
  if (!ota_self_firmware(fi) || !fi.valid || fi.image_len > UINT32_MAX - app_base ||
      !mota_nrf52_shared_boot_stage_plan(
          (uint32_t)total64, app_base, true, app_base + fi.image_len, start))
    return false;
  _planned_bootloader = true;
  _planned_total = (uint32_t)total64;
  _planned_start = start;
  return true;
#else
  (void)is_full; (void)image_size; (void)payload_off; (void)payload_size;
  return false;
#endif
}

// Write one whole 4 KB page from `buf` to flash (erase + program, ~85 ms). `buf` is PG bytes, 0xFF-padded
// past the container, so the program is clean. The selected ceiling and _write_start are page-aligned,
// and the container ends below the ceiling, so a full-page write never reaches protected storage.
static bool flash_matches(uint32_t addr, const uint8_t* expected, uint32_t n) {
  const volatile uint8_t* actual = (const volatile uint8_t*)(uintptr_t)addr;
  for (uint32_t i = 0; i < n; i++) if (actual[i] != expected[i]) return false;
  return true;
}

bool OtaStoreFlashNrf52::read_staged_header(
    void*, uint32_t address, uint32_t& total_size) {
  const uint8_t* header = (const uint8_t*)(uintptr_t)address;
  if (memcmp(header, MOTA_MAGIC, sizeof(MOTA_MAGIC)) != 0) return false;
  total_size = rd_u32le(header + sizeof(MOTA_MAGIC));
  return true;
}

bool OtaStoreFlashNrf52::invalidate_staged_header(
    void* context, uint32_t address) {
  OtaStoreFlashNrf52* store = static_cast<OtaStoreFlashNrf52*>(context);
  const uint8_t* page = (const uint8_t*)(uintptr_t)address;
  memcpy(store->_meta_page, page, PG);

  // Magic prevents application reopen; approval prevents an explicitly
  // triggered legacy bootloader from accepting a half-cleared container.
  // Rewrite the page once so both durable markers are consumed together.
  memset(store->_meta_page, 0, sizeof(MOTA_MAGIC));
  memset(store->_meta_page + 8u + MOTA_OFF_APPROVAL, 0,
         sizeof(APPROVAL_YES));
  if (flash_nrf5x_write(address, store->_meta_page, PG) < 0) {
    store->_io_ok = false;
    return false;
  }
  flash_nrf5x_flush();
  if (!flash_matches(address, store->_meta_page, PG)) {
    store->_io_ok = false;
    return false;
  }
  return true;
}

bool OtaStoreFlashNrf52::discard() {
  clear_hybrid_auth_record();
  const uint32_t app_base = mota_nrf52_app_base();
  const uint32_t stage_ceiling = ota_nrf52_effective_stage_ceiling();
  uint32_t app_end = 0;
  if (!protected_app_end(app_base, stage_ceiling, app_end)) {
    reset_session();
    _io_ok = false;
    return false;
  }

  uint32_t invalidated = 0;
  const bool ok = mota_nrf52_discard_staged_headers(
      app_base, app_end, stage_ceiling, this, read_staged_header,
      invalidate_staged_header, &invalidated);
  OTA_DBG("OTA flash: discard invalidated=%u ok=%d\n",
          (unsigned)invalidated, (int)ok);
  reset_session();
  _io_ok = ok;
  return ok;
}

bool OtaStoreFlashNrf52::flush_page(uint32_t page_idx, const uint8_t* buf) {
  if (!_io_ok || _flash_len < PG || page_idx >= _flash_len / PG ||
      _write_start > _stage_ceiling - PG ||
      page_idx > (_stage_ceiling - PG - _write_start) / PG) {
    _io_ok = false;
    return false;
  }
  uint32_t addr = _write_start + page_idx * PG;
  OTA_DBG("OTA flash: write page %u @ %08x\n", (unsigned)page_idx, (unsigned)addr);
  if (flash_nrf5x_write(addr, buf, PG) < 0) { _io_ok = false; return false; }
  flash_nrf5x_flush();
  if (!flash_matches(addr, buf, PG)) { _io_ok = false; return false; }
  return true;
}

uint32_t OtaStoreFlashNrf52::capacity() const {
  const uint32_t app_base = mota_nrf52_app_base();
  const uint32_t stage_ceiling = ota_nrf52_effective_stage_ceiling();
  uint32_t app_end;
  if (!protected_app_end(app_base, stage_ceiling, app_end)) return 0;
  const uint32_t flash_capacity =
      mota_nrf52_stage_capacity(app_base, app_end, stage_ceiling);
#if defined(OTA_HYBRID_RAM_STORE)
  const OtaRamCaps ram_caps = ota_bootloader_ram_caps();
  if (stage_ceiling == MOTA_NRF52_STAGE_CEILING_EXPANDED &&
      flash_capacity >= PG && ota_bootloader_supports_hybrid(ram_caps) &&
      flash_capacity <= UINT32_MAX - MOTA_NRF52_HYBRID_RAM_SIZE) {
    return flash_capacity + MOTA_NRF52_HYBRID_RAM_SIZE;
  }
#endif
  return flash_capacity;
}

bool OtaStoreFlashNrf52::flush_pay() {
  return _pay_idx == 0 || flush_page(_pay_idx, _pay_page);   // _pay_idx 0 == no payload page open
}

uint32_t OtaStoreFlashNrf52::run(uint32_t pos, uint32_t remain) const {
  uint32_t trailer = _total - 5;                        // container is always >= 13 bytes (begin checks)
  if (pos >= trailer) return remain;                    // tail: caller bounds remain to <= 5 already
  uint32_t end = pos + remain;
  if (!_hybrid || pos < _flash_len) {
    uint32_t page_end = (pos / PG + 1) * PG;
    if (end > page_end) end = page_end;                 // a run stays within one flash page,
    if (_hybrid && end > _flash_len) end = _flash_len;  // and one backing region,
  }
  if (end > trailer)  end = trailer;                    // and never crosses into the trailer tail
  return end - pos;
}

const uint8_t* OtaStoreFlashNrf52::read_slot(uint32_t pos) const {
  if (pos >= _total - 5)     return _trailer + (pos - (_total - 5));   // trailer tail (RAM until finalize)
  if (_hybrid && pos >= _flash_len)
    return (const uint8_t*)(uintptr_t)(MOTA_NRF52_HYBRID_RAM_START +
                                       pos - _flash_len);
  uint32_t page = pos / PG;
  if (page == 0)             return _meta_page + pos;                  // pinned page 0 (incl. leaves)
  if (page == _pay_idx)      return _pay_page + (pos - page * PG);     // current sliding payload page
  return (const uint8_t*)(uintptr_t)(_write_start + pos);              // already flushed -> memory-mapped
}

uint8_t* OtaStoreFlashNrf52::write_slot(uint32_t pos) {
  if (pos >= _total - 5)     return _trailer + (pos - (_total - 5));
  if (_hybrid && pos >= _flash_len)
    return (uint8_t*)(uintptr_t)(MOTA_NRF52_HYBRID_RAM_START +
                                 pos - _flash_len);
  uint32_t page = pos / PG;
  if (page == 0)             return _meta_page + pos;
  if (page > _pay_idx) {
    if (!flush_pay()) return nullptr;
    _pay_idx = page; memset(_pay_page, 0xFF, PG);             // advance, fresh page
  }
  if (page == _pay_idx)      return _pay_page + (pos - page * PG);
  return nullptr;                                                      // page < _pay_idx: already flushed
}

bool OtaStoreFlashNrf52::begin(uint32_t total_size) {
  const bool planned_bootloader = _planned_bootloader;
  const bool planned_hybrid = _planned_hybrid;
  const uint32_t planned_total = _planned_total;
  const uint32_t planned_start = _planned_start;
  const uint32_t planned_flash_len = _planned_flash_len;
  const uint32_t planned_ram_len = _planned_ram_len;
  clear_hybrid_auth_record();
  reset_session();

  // never collide with the running application image (its extent comes from its EndF trailer)
  const uint32_t app_base = mota_nrf52_app_base();
  const uint32_t stage_ceiling = ota_nrf52_effective_stage_ceiling();
  uint32_t app_end;
  if (planned_bootloader) {
#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
    SelfFwInfo fi;
    uint32_t checked_start;
    if (stage_ceiling != MOTA_NRF52_APP_END || total_size != planned_total ||
        !ota_self_firmware(fi) || !fi.valid || fi.image_len > UINT32_MAX - app_base ||
        !mota_nrf52_shared_boot_stage_plan(
            total_size, app_base, true, app_base + fi.image_len, checked_start) ||
        checked_start != planned_start)
      return false;
    app_end = app_base + fi.image_len;
#else
    return false;
#endif
  } else if (!protected_app_end(app_base, stage_ceiling, app_end)) {
    return false;
  }

  // Bottom-align below the selected ceiling and reject unless it sits above the running image. The
  // approval path later verifies the patch's detools workspace ends at/below this exact start.
  uint32_t start = 0;
  uint32_t flash_len = 0;
  uint32_t ram_len = 0;
  if (planned_hybrid) {
#if defined(OTA_HYBRID_RAM_STORE)
    if (planned_bootloader || total_size != planned_total ||
        stage_ceiling != MOTA_NRF52_STAGE_CEILING_EXPANDED ||
        !ota_bootloader_supports_hybrid(ota_bootloader_ram_caps()) ||
        !mota_nrf52_hybrid_stage_plan(
            total_size, app_base, app_end, stage_ceiling,
            start, flash_len, ram_len) ||
        start != planned_start || flash_len != planned_flash_len ||
        ram_len != planned_ram_len) {
      return false;
    }
#else
    return false;
#endif
  } else {
    if (!mota_nrf52_stage_plan(
            total_size, app_base, app_end, stage_ceiling, start)) {
      return false;
    }
    if (planned_bootloader && start != planned_start) return false;
    flash_len = stage_ceiling - start;
  }

  _write_start = start;
  _stage_ceiling = stage_ceiling;
  _flash_len = flash_len;
  _ram_len = ram_len;
  _total = total_size;
  _hybrid = planned_hybrid;
  memset(_meta_page, 0xFF, PG);     // assemble page 0 in RAM; 0xFF = erased sentinel (unfilled leaf slots)
  memset(_trailer, 0xFF, sizeof(_trailer));
#if defined(OTA_HYBRID_RAM_STORE)
  if (_hybrid)
    memset((void*)(uintptr_t)MOTA_NRF52_HYBRID_RAM_START, 0xFF, _ram_len);
#endif
  _pay_idx = 0;
  _flushed = false;
  _io_ok = true;
  _planned_bootloader = planned_bootloader;
  _planned_hybrid = planned_hybrid;
  _planned_total = planned_total;
  _planned_start = planned_start;
  _planned_flash_len = planned_flash_len;
  _planned_ram_len = planned_ram_len;
  OTA_DBG("OTA flash: begin total=%u start=%08x flash=%u ram=%u app_end=%08x ceiling=%08x\n",
          (unsigned)total_size, (unsigned)start, (unsigned)flash_len,
          (unsigned)ram_len, (unsigned)app_end, (unsigned)stage_ceiling);
  return true;                      // no pre-erase: each page is erased by its own (single) flush
}

bool OtaStoreFlashNrf52::write(uint32_t offset, const uint8_t* d, uint32_t len) {
  if ((uint64_t)offset + len > _total || !_io_ok) return false;
  for (uint32_t pos = offset, end = offset + len; pos < end; ) {
    uint32_t n = run(pos, end - pos);
    uint8_t* dst = write_slot(pos);
    if (!_io_ok) return false;
    if (dst) {
      memcpy(dst, d, n);
    } else {
      // out-of-order write to an already-flushed page: read-modify-write straight to flash. Safe -- the
      // driver erases the page before programming, so re-touching it never breaks writes-per-word.
      OTA_DBG("OTA flash: RMW page %u (out-of-order) @ off %u\n", (unsigned)(pos / PG), (unsigned)pos);
      uint32_t addr = _write_start + pos;
      if (flash_nrf5x_write(addr, d, n) < 0) { _io_ok = false; return false; }
      flash_nrf5x_flush();
      if (!flash_matches(addr, d, n)) { _io_ok = false; return false; }
    }
    pos += n; d += n;
  }
  return true;
}

bool OtaStoreFlashNrf52::read(uint32_t offset, uint8_t* buf, uint32_t len) const {
  if ((uint64_t)offset + len > _total || !_io_ok) return false;
  for (uint32_t pos = offset, end = offset + len; pos < end; ) {
    uint32_t n = run(pos, end - pos);
    memcpy(buf, read_slot(pos), n);
    pos += n; buf += n;
  }
  return true;
}

bool OtaStoreFlashNrf52::finalize() {
  if (_flushed) return _io_ok;
  if (_total == 0 || !_io_ok) return false;
  OTA_DBG("OTA flash: finalize total=%u\n", (unsigned)_total);
  if (!flush_pay()) return false;                 // the last (highest) payload page, if one is open
  if (!flush_page(0, _meta_page)) return false;   // header + manifest + leaves + first payload bytes
  const uint32_t trailer_off = _total - sizeof(_trailer);
  uint32_t flash_bytes = sizeof(_trailer);
  if (_hybrid) {
    flash_bytes = trailer_off < _flash_len ? _flash_len - trailer_off : 0u;
    if (flash_bytes > sizeof(_trailer)) flash_bytes = sizeof(_trailer);
  }
  if (flash_bytes != 0u) {
    const uint32_t trailer_addr = _write_start + trailer_off;
    if (flash_nrf5x_write(trailer_addr, _trailer, flash_bytes) < 0) {
      _io_ok = false;
      return false;
    }
    flash_nrf5x_flush();
    if (!flash_matches(trailer_addr, _trailer, flash_bytes)) {
      _io_ok = false;
      return false;
    }
  }
  if (_hybrid && flash_bytes < sizeof(_trailer)) {
    const uint32_t logical = trailer_off + flash_bytes;
    uint8_t* const dst = (uint8_t*)(uintptr_t)(
        MOTA_NRF52_HYBRID_RAM_START + logical - _flash_len);
    memcpy(dst, _trailer + flash_bytes, sizeof(_trailer) - flash_bytes);
    __DMB();
    __DSB();
    if (memcmp(dst, _trailer + flash_bytes,
               sizeof(_trailer) - flash_bytes) != 0) {
      _io_ok = false;
      return false;
    }
  }
  _flushed = true;
  return true;
}

bool OtaStoreFlashNrf52::approve_for_application(
    const uint8_t normalized_container_hash[32]) {
  clear_hybrid_auth_record();
  _hybrid_handoff_ready = false;
  if (!finalize() || _total < 8u + MOTA_MFL + 5u) return false;
  const uint32_t offset = 8u + MOTA_OFF_APPROVAL;
  if (offset + sizeof(APPROVAL_YES) > _flash_len) return false;
  const uint32_t address = _write_start + offset;
  if (flash_nrf5x_write(address, APPROVAL_YES, sizeof(APPROVAL_YES)) < 0) {
    _io_ok = false;
    return false;
  }
  flash_nrf5x_flush();
  if (!flash_matches(address, APPROVAL_YES, sizeof(APPROVAL_YES))) {
    _io_ok = false;
    return false;
  }
  memcpy(_meta_page + offset, APPROVAL_YES, sizeof(APPROVAL_YES));
  if (!_hybrid) return true;
#if defined(OTA_HYBRID_RAM_STORE)
  if (!normalized_container_hash ||
      !mota_hybrid_auth_encode(
          _hybrid_handoff, _total, _write_start, _flash_len, _ram_len,
          normalized_container_hash)) {
    _io_ok = false;
    return false;
  }
  _hybrid_handoff_ready = true;
  return true;
#else
  return false;
#endif
}

bool OtaStoreFlashNrf52::publish_hybrid_handoff() {
#if defined(OTA_HYBRID_RAM_STORE)
  if (!_hybrid || !_hybrid_handoff_ready || !_flushed || !_io_ok ||
      !mota_hybrid_auth_valid(_hybrid_handoff)) {
    return false;
  }
  return publish_hybrid_auth_record(_hybrid_handoff);
#else
  return false;
#endif
}

#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
bool OtaStoreFlashNrf52::approve_for_bootloader() {
  if (_hybrid || !finalize() || _total < 8u + MOTA_MFL + 5u) return false;
  const uint32_t offset = 8u + MOTA_OFF_APPROVAL;
  if (offset + sizeof(APPROVAL_YES) > _total) return false;
  const uint32_t address = _write_start + offset;
  if (flash_nrf5x_write(address, APPROVAL_YES, sizeof(APPROVAL_YES)) < 0) {
    _io_ok = false;
    return false;
  }
  flash_nrf5x_flush();
  if (!flash_matches(address, APPROVAL_YES, sizeof(APPROVAL_YES))) {
    _io_ok = false;
    return false;
  }
  memcpy(_meta_page + offset, APPROVAL_YES, sizeof(APPROVAL_YES));
  return true;
}
#endif

// Persist mid-transfer progress so a reboot can resume. Order matters for consistency: flush the open
// payload page FIRST, then page 0 (the leaf-progress markers) -- so every block whose leaf is now in flash
// also has its payload in flash. Infrequent (every OTA_CHECKPOINT_BLOCKS blocks), so the 2 extra page
// erases don't matter; at LoRa block rates it's roughly once per many minutes.
void OtaStoreFlashNrf52::checkpoint() {
  if (_total == 0 || _flushed || !_io_ok) return;
  if (!flush_pay()) return;                 // keep _pay_idx open; it may still receive writes
  flush_page(0, _meta_page);                // header + manifest + leaves accumulated so far
}

// Re-attach to a container already staged in flash (after a reboot), without erasing. The container is
// bottom-aligned (begin: start = (ceiling - total) & ~(PG-1)) and flash is memory-mapped, so scan page
// starts from just below that ceiling down to the app end for MOTA_MAGIC with a self-consistent total; adopt
// the first match (highest address = most recent for the common single-container case). The manager then
// parses the loaded manifest and validates geometry/root, so a stale leftover is rejected there.
bool OtaStoreFlashNrf52::reopen() {
  // A retained-SRAM suffix cannot survive a power cycle or app restart as a
  // resumable store. Reset all in-memory split state and adopt only a complete
  // flash span whose advertised total fits before the selected ceiling.
  reset_session();
  const uint32_t app_base = mota_nrf52_app_base();
  const uint32_t stage_ceiling = ota_nrf52_effective_stage_ceiling();
  uint32_t app_end;
  if (!protected_app_end(app_base, stage_ceiling, app_end)) return false;
  for (uint32_t start = align_down(stage_ceiling - PG, PG); start >= app_end; start -= PG) {
    const uint8_t* p = (const uint8_t*)(uintptr_t)start;
    if (memcmp(p, MOTA_MAGIC, 4) != 0) continue;
    uint32_t total = rd_u32le(p + 4);
    if (!mota_nrf52_container_span_valid(
            start, stage_ceiling, total, 8u + MOTA_MFL + 5u))
      continue;
    uint32_t want;   // must be valid + placed exactly where begin() would have staged it (same bounds fn)
    MotaManifest manifest;
    const bool manifest_ok = mota_parse_manifest(p + 8u, MOTA_MFL, manifest);
    const bool bootloader = manifest_ok && manifest.is_bootloader();
    if (bootloader) {
#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
      SelfFwInfo fi;
      if (!ota_bootloader_self_update_caps_valid(ota_bootloader_update_caps()) ||
          !ota_self_firmware(fi) || !fi.valid || fi.image_len > UINT32_MAX - app_base ||
          !mota_nrf52_shared_boot_stage_plan(
              total, app_base, true, app_base + fi.image_len, want) || want != start)
        continue;
#else
      continue;
#endif
    } else if (!mota_nrf52_stage_plan(
                   total, app_base, app_end, stage_ceiling, want) || want != start) {
      continue;
    }
    _write_start = start;
    _stage_ceiling = stage_ceiling;
    _flash_len = stage_ceiling - start;
    _ram_len = 0;
    _total = total;
    _hybrid = false;
    memcpy(_meta_page, p, PG);                  // load page 0 (header+manifest+leaves) into RAM to continue
    memcpy(_trailer, p + (total - 5), 5);        // recover the trailer tail (flushed at last finalize, if any)
    _pay_idx = 0;
    _flushed = false;
    _io_ok = true;
    _planned_bootloader = bootloader;
    _planned_total = bootloader ? total : 0u;
    _planned_start = bootloader ? start : 0u;
    OTA_DBG("OTA flash: reopen total=%u start=%08x ceiling=%08x\n",
            (unsigned)total, (unsigned)start, (unsigned)stage_ceiling);
    return true;
  }
  return false;
}

} // namespace ota
} // namespace mesh

#endif
