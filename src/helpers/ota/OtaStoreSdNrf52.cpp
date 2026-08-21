#include "OtaStoreSdNrf52.h"

#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)

#include <Arduino.h>
#include <SdFat.h>
#include <SPI.h>
#include <new>
#include <stdlib.h>
#include <string.h>
#include "MeshCore.h"
#include "OtaBootloaderUpdate.h"
#include "OtaByteIO.h"
#include "OtaFlashLayout_nrf52.h"
#include "OtaSdBootToken.h"
#include "OtaSdHandoff.h"
#include "OtaSelf.h"
#include "flash/flash_nrf5x.h"

#ifndef OTA_SD_CS_PIN
#define OTA_SD_CS_PIN PIN_SPI1_NSS
#endif
#ifndef OTA_SD_SCK_MHZ
#define OTA_SD_SCK_MHZ 8
#endif

namespace mesh {
namespace ota {

const char* const OtaStoreSdNrf52::PATH = "/meshcore-ota.mota";

OtaStoreSdNrf52::OtaStoreSdNrf52()
    : _sd(new (std::nothrow) SdFs()), _file(new (std::nothrow) FsFile()) {}

OtaStoreSdNrf52::~OtaStoreSdNrf52() {
  if (_file) { _file->close(); delete _file; }
  if (_sd) { _sd->end(); delete _sd; }
}

void OtaStoreSdNrf52::fail(const char* message) {
  strncpy(_error, message ? message : "SD error", sizeof(_error) - 1);
  _error[sizeof(_error) - 1] = 0;
}

void OtaStoreSdNrf52::resetStoreState() {
  _total = 0;
  _first_sector = 0;
  _allocated_sectors = 0;
  _partition_start = 0;
  _partition_end = 0;
  _planned_bootloader = false;
  if (_file && *_file) _file->close();
  if (_sd) _sd->end();
  _mounted = false;
}

bool OtaStoreSdNrf52::beginCardOnly() {
  _error[0] = 0;
  resetStoreState();
  if (!_sd || !_file ||
      !_sd->cardBegin(SdSpiConfig(OTA_SD_CS_PIN, DEDICATED_SPI,
                                  SD_SCK_MHZ(OTA_SD_SCK_MHZ), &SPI1))) {
    fail("SD card init failed");
    return false;
  }
  return true;
}

bool OtaStoreSdNrf52::mount() {
  if (_mounted) return true;
  _error[0] = 0;
  if (!_sd || !_file || !_sd->begin(SdSpiConfig(OTA_SD_CS_PIN, DEDICATED_SPI,
                                                SD_SCK_MHZ(OTA_SD_SCK_MHZ), &SPI1))) {
    fail("SD mount failed");
    return false;
  }
  _mounted = true;
  if (!inspect_mbr()) {
    _sd->end();
    _mounted = false;
    return false;
  }
  return true;
}

bool OtaStoreSdNrf52::formatCard(MainBoard& board) {
  if (!beginCardOnly()) return false;

  board.serviceWatchdog();
  if (!_sd->format()) {
    fail("SD format failed");
    _sd->end();
    return false;
  }
  board.serviceWatchdog();
  if (!_sd->volumeBegin()) {
    fail("SD remount after format failed");
    _sd->end();
    return false;
  }

  _mounted = true;
  if (!inspect_mbr()) {
    _sd->end();
    _mounted = false;
    return false;
  }
  if (!invalidate_handoff()) {
    fail("SD handoff clear failed");
    _sd->end();
    _mounted = false;
    return false;
  }
  board.serviceWatchdog();
  return true;
}

bool OtaStoreSdNrf52::eraseCard(MainBoard& board) {
  if (!beginCardOnly()) return false;

  SdCard* card = _sd->card();
  const uint32_t sector_count = card ? card->sectorCount() : 0;
  if (!card || sector_count == 0) {
    fail("SD sector count failed");
    _sd->end();
    return false;
  }

  // Match SdFat's formatter example: bounded erase ranges avoid cards that
  // reject or time out on a single whole-device erase command. Servicing the
  // board watchdog between ranges keeps the intentionally long operation from
  // looking like a wedged main loop.
  const uint32_t erase_sectors = 262144UL;
  uint32_t first_sector = 0;
  while (first_sector < sector_count) {
    const uint32_t remaining = sector_count - first_sector;
    const uint32_t count = remaining < erase_sectors ? remaining : erase_sectors;
    const uint32_t last_sector = first_sector + count - 1;
    board.serviceWatchdog();
    if (!card->erase(first_sector, last_sector)) {
      fail("SD erase failed");
      _sd->end();
      return false;
    }
    first_sector += count;
  }

  board.serviceWatchdog();
  if (!card->syncDevice()) {
    fail("SD erase sync failed");
    _sd->end();
    return false;
  }
  _sd->end();
  return true;
}

bool OtaStoreSdNrf52::getSpace(MainBoard& board, uint64_t& used_bytes,
                               uint64_t& free_bytes) {
  used_bytes = 0;
  free_bytes = 0;
  _error[0] = 0;
  if (!mount()) return false;

  board.serviceWatchdog();
  const uint32_t cluster_count = _sd->clusterCount();
  const uint32_t free_clusters = _sd->freeClusterCount();
  const uint32_t bytes_per_cluster = _sd->bytesPerCluster();
  board.serviceWatchdog();
  if (cluster_count == 0 || bytes_per_cluster == 0 ||
      free_clusters > cluster_count) {
    fail("SD space query failed");
    return false;
  }

  free_bytes = (uint64_t)free_clusters * bytes_per_cluster;
  used_bytes = (uint64_t)(cluster_count - free_clusters) * bytes_per_cluster;
  return true;
}

namespace {

static const uint16_t SD_LIST_PAGE_SIZE = 2;
static const size_t SD_LIST_NAME_CAP = 256;       // FAT/exFAT long-name maximum plus NUL
static const size_t SD_LIST_SHOWN_PATH = 50;      // bounded CLI line; long paths get an explicit leading "..."

struct SdListState {
  SdListState(MainBoard& b, uint32_t f) : board(b), first(f) {}
  MainBoard& board;
  uint32_t first;
  uint32_t total = 0;
  char body[132] = {0};
  size_t used = 0;
  char* path = nullptr;
  size_t path_len = 0;
  size_t path_cap = 0;
  bool failed = false;
};

static void compact_file_size(char* out, size_t cap, uint64_t bytes) {
  static const char units[] = {'B', 'K', 'M', 'G', 'T'};
  uint8_t unit = 0;
  uint64_t whole = bytes;
  while (whole >= 1024 && unit < sizeof(units) - 1) { whole /= 1024; unit++; }
  snprintf(out, cap, "%lu%c", (unsigned long)whole, units[unit]);
}

static bool ensure_list_path(SdListState& state, size_t need) {
  if (need <= state.path_cap) return true;
  size_t cap = state.path_cap ? state.path_cap : 256;
  while (cap < need) {
    if (cap > SIZE_MAX / 2) { state.failed = true; return false; }
    cap *= 2;
  }
  char* grown = static_cast<char*>(realloc(state.path, cap));
  if (!grown) { state.failed = true; return false; }
  state.path = grown;
  state.path_cap = cap;
  return true;
}

static bool append_sd_list_entry(SdListState& state, const char* path, uint64_t bytes) {
  char size[12];
  compact_file_size(size, sizeof(size), bytes);
  const size_t path_len = strlen(path);
  const char* shown = path;
  char shortened[SD_LIST_SHOWN_PATH + 1];
  if (path_len > SD_LIST_SHOWN_PATH) {
    memcpy(shortened, "...", 3);
    memcpy(shortened + 3, path + path_len - (SD_LIST_SHOWN_PATH - 3), SD_LIST_SHOWN_PATH - 3);
    shortened[SD_LIST_SHOWN_PATH] = 0;
    shown = shortened;
  }
  size_t remain = sizeof(state.body) - state.used;
  int n = snprintf(state.body + state.used, remain, "\n %s %s", shown, size);
  if (n <= 0 || (size_t)n >= remain) { state.failed = true; return false; }
  state.used += (size_t)n;
  return true;
}

static bool scan_sd_files(FsFile& dir, SdListState& state) {
  FsFile file;
  while (file.openNext(&dir, O_RDONLY)) {
    state.board.serviceWatchdog();
    const size_t parent_len = state.path_len;
    if (parent_len > SIZE_MAX - SD_LIST_NAME_CAP - 2 ||
        !ensure_list_path(state, parent_len + SD_LIST_NAME_CAP + 2)) {
      file.close();
      return false;
    }
    size_t name_at = parent_len;
    if (name_at == 0 || state.path[name_at - 1] != '/') state.path[name_at++] = '/';
    state.path[name_at] = 0;
    file.getName(state.path + name_at, SD_LIST_NAME_CAP);
    size_t name_len = strlen(state.path + name_at);
    if (name_len == 0) { state.failed = true; file.close(); return false; }
    state.path_len = name_at + name_len;

    if (file.isDir()) {
      if (!scan_sd_files(file, state)) {
        state.path_len = parent_len;
        state.path[parent_len] = 0;
        file.close();
        return false;
      }
    } else {
      uint32_t index = state.total++;
      if (index >= state.first && index < state.first + SD_LIST_PAGE_SIZE) {
        if (!append_sd_list_entry(state, state.path, file.fileSize())) {
          state.path_len = parent_len;
          state.path[parent_len] = 0;
          file.close();
          return false;
        }
      }
    }
    state.path_len = parent_len;
    state.path[parent_len] = 0;
    file.close();
  }
  return true;
}

} // namespace

bool OtaStoreSdNrf52::listFiles(MainBoard& board, uint16_t page,
                                char* reply, size_t cap) {
  if (!reply || cap == 0) return false;
  if (page == 0) page = 1;
  _error[0] = 0;
  if (!mount()) return false;

  FsFile root = _sd->open("/", O_RDONLY);
  if (!root || !root.isDir()) { fail("SD root directory open failed"); return false; }
  SdListState state(board, (uint32_t)(page - 1) * SD_LIST_PAGE_SIZE);
  bool scanned = ensure_list_path(state, 2) && scan_sd_files(root, state);
  root.close();
  free(state.path);
  if (!scanned || state.failed) { fail("SD file listing failed"); return false; }

  uint32_t pages = (state.total + SD_LIST_PAGE_SIZE - 1) / SD_LIST_PAGE_SIZE;
  if (pages == 0) {
    snprintf(reply, cap, "> SD files: empty");
  } else if (page > pages) {
    snprintf(reply, cap, "Error: SD file page %u out of range (1-%lu)",
             (unsigned)page, (unsigned long)pages);
  } else {
    snprintf(reply, cap, "> SD files %u/%lu (%lu):%s",
             (unsigned)page, (unsigned long)pages,
             (unsigned long)state.total, state.body);
  }
  return true;
}

bool OtaStoreSdNrf52::inspect_mbr() {
  uint8_t sector[MOTA_SD_SECTOR_SIZE];
  if (!_sd->card() || !_sd->card()->readSector(0, sector)) {
    fail("SD MBR read failed");
    return false;
  }
  if (sector[510] != 0x55 || sector[511] != 0xAA) {
    fail("SD must use an MBR partition table");
    return false;
  }

  // SdFs mounts the first usable MBR partition. Require its start to leave
  // sector 1 unused; a protective GPT entry (type 0xEE) is not safe here.
  for (uint8_t i = 0; i < 4; i++) {
    const uint8_t* p = sector + 446 + (uint32_t)i * 16;
    uint8_t type = p[4];
    uint32_t start = mota_sd_rd32(p + 8);
    uint32_t count = mota_sd_rd32(p + 12);
    if (type == 0 || count == 0) continue;
    if (type == 0xEE || start <= MOTA_SD_HANDOFF_SECTOR ||
        start >= _sd->card()->sectorCount() ||
        count > _sd->card()->sectorCount() - start) {
      fail("SD needs an MBR partition starting after sector 1");
      return false;
    }
    _partition_start = start;
    _partition_end = start + count;
    return true;
  }
  fail("SD has no usable MBR partition");
  return false;
}

bool OtaStoreSdNrf52::invalidate_handoff() {
  if (!_mounted || !_sd->card()) return false;
  uint8_t sector[MOTA_SD_SECTOR_SIZE];
  if (!_sd->card()->readSector(MOTA_SD_HANDOFF_SECTOR, sector)) return false;
  memset(sector, 0xFF, MOTA_SD_HANDOFF_LEN);
  return _sd->card()->writeSector(MOTA_SD_HANDOFF_SECTOR, sector) &&
         _sd->card()->syncDevice();
}

bool OtaStoreSdNrf52::locate_file() {
  Sector_t first = 0, last = 0;
  if (!_file || !_file->contiguousRange(&first, &last) || last < first) {
    fail("OTA file is not contiguous");
    return false;
  }
  uint64_t need = ((uint64_t)_total + MOTA_SD_SECTOR_SIZE - 1) / MOTA_SD_SECTOR_SIZE;
  uint64_t available = (uint64_t)last - first + 1;
  if (need == 0 || need > available || first < _partition_start ||
      (uint64_t)first + need > _partition_end) {
    fail("OTA file sector range is invalid");
    return false;
  }
  _first_sector = (uint32_t)first;
  _allocated_sectors = (uint32_t)need;
  return true;
}

bool OtaStoreSdNrf52::plan_layout(bool, uint32_t image_size,
                                  uint32_t, uint32_t payload_size,
                                  bool is_bootloader) {
  _planned_bootloader = false;
  if (is_bootloader) {
#if defined(OTA_SD_BOOTLOADER_UPDATE)
    if (ota_bootloader_image_geometry_valid(image_size, payload_size)) {
      _planned_bootloader = true;
      return true;
    }
    fail("bootloader package geometry mismatch");
#else
    fail("bootloader update is not enabled for this SD target");
#endif
    return false;
  }
  const uint32_t app_base = mota_nrf52_app_base();
  if (image_size == 0 || payload_size == 0 || app_base >= MOTA_NRF52_APP_END ||
      image_size > MOTA_NRF52_APP_END - app_base) {
    fail("image exceeds nRF52 application region");
    return false;
  }
  return true;
}

bool OtaStoreSdNrf52::begin(uint32_t total_size) {
  _total = 0;
  _first_sector = 0;
  _allocated_sectors = 0;
  if (total_size < 13 || !mount()) return false;
  if (!invalidate_handoff()) {
    fail("SD handoff clear failed");
    return false;
  }
  if (*_file) _file->close();
  _sd->remove(PATH);
  if (!_file->open(PATH, O_RDWR | O_CREAT | O_EXCL)) {
    fail("SD OTA file create failed");
    return false;
  }
  if (!_file->preAllocate(total_size)) {
    fail("SD lacks contiguous space for update");
    _file->close();
    _sd->remove(PATH);
    return false;
  }
  _total = total_size;
  if (!locate_file()) {
    _total = 0;
    _file->close();
    _sd->remove(PATH);
    return false;
  }
  return true;
}

bool OtaStoreSdNrf52::set_meta_size(uint32_t meta_bytes) {
  if (!_total || meta_bytes > _total - 5) return false;
  uint8_t erased[512];
  memset(erased, 0xFF, sizeof(erased));
  for (uint32_t off = 0; off < meta_bytes; ) {
    uint32_t n = meta_bytes - off;
    if (n > sizeof(erased)) n = sizeof(erased);
    if (!write(off, erased, n)) return false;
    off += n;
  }
  return true;
}

bool OtaStoreSdNrf52::write(uint32_t offset, const uint8_t* data, uint32_t len) {
  if (!_file || !*_file || (uint64_t)offset + len > _total || !_file->seekSet(offset) ||
      _file->write(data, len) != len) {
    fail("SD OTA write failed");
    return false;
  }
  return true;
}

bool OtaStoreSdNrf52::read(uint32_t offset, uint8_t* buf, uint32_t len) const {
  if (!_file || !*_file || (uint64_t)offset + len > _total || !_file->seekSet(offset) ||
      _file->read(buf, len) != (int)len) {
    return false;
  }
  return true;
}

uint32_t OtaStoreSdNrf52::capacity() const {
  if (!_mounted || !_sd || !_sd->card()) return 0;
  uint64_t bytes = (uint64_t)_sd->card()->sectorCount() * MOTA_SD_SECTOR_SIZE;
  return bytes > UINT32_MAX ? UINT32_MAX : (uint32_t)bytes;
}

bool OtaStoreSdNrf52::finalize() {
  if (!_file || !*_file || !_file->sync() || !_sd->card()->syncDevice()) {
    fail("SD OTA sync failed");
    return false;
  }
  return locate_file();
}

void OtaStoreSdNrf52::checkpoint() {
  if (_file && *_file) {
    _file->sync();
    if (_sd->card()) _sd->card()->syncDevice();
  }
}

bool OtaStoreSdNrf52::reopen() {
  _total = 0;
  _planned_bootloader = false;
  if (!mount()) return false;
  if (*_file) _file->close();
  if (!_file->open(PATH, O_RDWR)) return false;
  uint8_t hdr[8];
  if (_file->fileSize() < 13 || !_file->seekSet(0) ||
      _file->read(hdr, sizeof(hdr)) != (int)sizeof(hdr) ||
      memcmp(hdr, MOTA_MAGIC, 4) != 0) {
    _file->close();
    return false;
  }
  uint32_t total = rd_u32le(hdr + 4);
  if (total < 13 || total != _file->fileSize()) {
    _file->close();
    return false;
  }
  _total = total;
  uint8_t manifest[MOTA_MFL];
  MotaManifest parsed;
  if (total < 8u + sizeof(manifest) + 5u || !_file->seekSet(8u) ||
      _file->read(manifest, sizeof(manifest)) != (int)sizeof(manifest) ||
      !mota_parse_manifest(manifest, sizeof(manifest), parsed)) {
    _total = 0;
    _file->close();
    return false;
  }
  if (parsed.is_bootloader()) {
#if defined(OTA_SD_BOOTLOADER_UPDATE)
    const uint64_t payload_off = 8u + sizeof(manifest) +
        (uint64_t)parsed.block_count * 4u;
    if (!ota_bootloader_image_geometry_valid(parsed.image_size, parsed.payload_size) ||
        payload_off + parsed.payload_size + 5u != total) {
      _total = 0;
      _file->close();
      return false;
    }
    _planned_bootloader = true;
#else
    _total = 0;
    _file->close();
    return false;
#endif
  }
  if (!locate_file()) {
    _total = 0;
    _file->close();
    return false;
  }
  return true;
}

void OtaStoreSdNrf52::clear() {
  _total = 0;
  _planned_bootloader = false;
  _first_sector = 0;
  _allocated_sectors = 0;
  if (!mount()) return;
  invalidate_handoff();
  if (_file && *_file) _file->close();
  _sd->remove(PATH);
}

bool OtaStoreSdNrf52::approve_for_bootloader(
    const uint8_t expected_boot_image_hash[32]) {
  if (!_total || !finalize()) return false;
#if defined(OTA_SD_BOOTLOADER_UPDATE)
  if (_planned_bootloader) {
    SelfFwInfo fi;
    if (!expected_boot_image_hash ||
        _total != MOTA_NRF52_BOOT_CONTAINER_SIZE ||
        !ota_self_firmware(fi) ||
        !ota_bootloader_scratch_headroom_valid(
            fi.valid, mota_nrf52_app_base(), fi.image_len,
            OTA_BOOT_SCRATCH_START) ||
        !ota_bootloader_live_bank_preserves_scratch(
            mota_nrf52_app_base(), fi.image_len, OTA_BOOT_SCRATCH_START)) {
      fail("running firmware/settings do not preserve boot scratch");
      return false;
    }
  }
#endif
  if (!write(8 + MOTA_OFF_APPROVAL, APPROVAL_YES, sizeof(APPROVAL_YES)) ||
      !_file->sync() || !_sd->card()->syncDevice()) {
    fail("SD approval write failed");
    return false;
  }

#if defined(OTA_SD_BOOTLOADER_UPDATE)
  if (_planned_bootloader) {
    uint8_t token[MOTA_SD_BOOT_TOKEN_LEN];
    mota_sd_boot_token_encode(token, _total, expected_boot_image_hash);

    // This page is outside the hash-valid live app and below InternalFS. The
    // token is temporary: OTAFIX validates it against the current SD bytes,
    // then consumes the page as the first bootloader scratch page.
    flash_nrf5x_flush();
    if (!flash_nrf5x_erase(OTA_BOOT_SCRATCH_START) ||
        flash_nrf5x_write(OTA_BOOT_SCRATCH_START, token, sizeof(token)) < 0) {
      fail("SD bootloader token flash write failed");
      return false;
    }
    flash_nrf5x_flush();
    if (memcmp((const void*)(uintptr_t)OTA_BOOT_SCRATCH_START,
               token, sizeof(token)) != 0) {
      fail("SD bootloader token flash verify failed");
      return false;
    }
  }
#endif

  uint8_t sector[MOTA_SD_SECTOR_SIZE];
  if (!_sd->card()->readSector(MOTA_SD_HANDOFF_SECTOR, sector)) {
    fail("SD handoff sector read failed");
    return false;
  }
  mota_sd_encode_handoff(sector, _first_sector, _allocated_sectors,
                         _total, _sd->card()->sectorCount());
  if (!_sd->card()->writeSector(MOTA_SD_HANDOFF_SECTOR, sector) ||
      !_sd->card()->syncDevice()) {
    fail("SD bootloader handoff write failed");
    return false;
  }
  return true;
}

} // namespace ota
} // namespace mesh

#endif
