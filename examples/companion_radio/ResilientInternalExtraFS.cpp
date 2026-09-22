#include "ResilientInternalExtraFS.h"

#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)

#include <helpers/IdentityStore.h>
#include <helpers/AtomicFileWriter.h>
#include <helpers/nrf52/InternalFlashStatus.h>
#include <helpers/nrf52/SoftDeviceState.h>
#include <nrf_soc.h>
#include <string.h>

namespace {
constexpr char PAGE_MAP_PATH[] = "/extrafs.badpages";
constexpr char PAGE_MAP_BACKUP_PATH[] = "/extrafs.badpages.bak";
constexpr uint32_t PAGE_MAP_V1_MAGIC = 0x31475042UL; // "BPG1" on nRF52
constexpr uint32_t PAGE_MAP_V2_MAGIC = 0x32475042UL; // "BPG2" on nRF52
constexpr uint8_t PAGE_MAP_READ_ATTEMPTS = 3;
#if defined(NRF52840_XXAA)
constexpr uint32_t PRIMARY_START = 0xED000UL;
#else
constexpr uint32_t PRIMARY_START = 0x6D000UL;
#endif
constexpr uint32_t PRIMARY_SIZE = 7UL * FLASH_NRF52_PAGE_SIZE;

StaticSemaphore_t shared_flash_lock_storage;
SemaphoreHandle_t shared_flash_lock = nullptr;

void feedInheritedWatchdog() {
  if (NRF_WDT->RUNSTATUS == 0) return;
  const uint32_t enabled = NRF_WDT->RREN & 0xFFUL;
  for (uint8_t channel = 0; channel < 8; ++channel) {
    if ((enabled & (1UL << channel)) != 0) {
      NRF_WDT->RR[channel] = WDT_RR_RR_Reload;
    }
  }
}

class FlashLock {
public:
  FlashLock() {
    if (shared_flash_lock == nullptr) {
      shared_flash_lock = xSemaphoreCreateMutexStatic(&shared_flash_lock_storage);
    }
    xSemaphoreTake(shared_flash_lock, portMAX_DELAY);
  }
  ~FlashLock() { xSemaphoreGive(shared_flash_lock); }
};

int primaryRead(const struct lfs_config*, lfs_block_t block, lfs_off_t off,
                void* buffer, lfs_size_t size) {
  FlashLock lock;
  return flash_nrf5x_read(buffer, PRIMARY_START + block * 128 + off, size)
          == static_cast<int>(size) ? 0 : LFS_ERR_IO;
}

int primaryProg(const struct lfs_config*, lfs_block_t block, lfs_off_t off,
                const void* buffer, lfs_size_t size) {
  FlashLock lock;
  return flash_nrf5x_write(PRIMARY_START + block * 128 + off, buffer, size)
          == static_cast<int>(size) ? 0 : LFS_ERR_IO;
}

int primaryErase(const struct lfs_config*, lfs_block_t block) {
  FlashLock lock;
  const uint32_t address = PRIMARY_START + block * 128;
  for (uint32_t offset = 0; offset < 128; offset++) {
    if (flash_nrf5x_write8(address + offset, 0xFF) != 1) return LFS_ERR_IO;
  }
  return 0;
}

int primarySync(const struct lfs_config*) {
  FlashLock lock;
  return mesh_flash_nrf5x_flush_checked() ? 0 : LFS_ERR_IO;
}

struct LegacyPageMapRecord {
  uint32_t magic;
  uint32_t bad_pages;
  uint32_t pending_pages;
  uint32_t check;
};

struct PageMapRecord {
  uint32_t magic;
  uint32_t generation;
  uint32_t bad_pages;
  uint32_t pending_pages;
  uint32_t check;
};

enum class PageMapReadState : uint8_t { Missing, Valid, Invalid };

struct PageMapCandidate {
  PageMapReadState state = PageMapReadState::Invalid;
  uint32_t generation = 0;
  uint32_t bad_pages = 0;
  uint32_t pending_pages = 0;
  bool legacy = false;
};

bool validPageMapValues(uint32_t bad_pages, uint32_t pending_pages) {
  return (bad_pages & ~mesh::storage::INTERNAL_EXTRAFS_PAGE_MASK) == 0
      && (pending_pages
          & ~(mesh::storage::INTERNAL_EXTRAFS_PAGE_MASK | (1UL << 31))) == 0
      && (bad_pages & pending_pages
          & mesh::storage::INTERNAL_EXTRAFS_PAGE_MASK) == 0
      && mesh::storage::countInternalExtraFsBadPages(bad_pages) <= 4;
}

PageMapCandidate readPageMapCandidate(Adafruit_LittleFS& primary,
                                      const char* path) {
  PageMapCandidate candidate;
  for (uint8_t attempt = 0; attempt < PAGE_MAP_READ_ATTEMPTS; ++attempt) {
    struct lfs_info info = {};
    primary._lockFS();
    const int stat_result = lfs_stat(primary._getFS(), path, &info);
    primary._unlockFS();
    if (stat_result == LFS_ERR_NOENT) {
      candidate.state = PageMapReadState::Missing;
      return candidate;
    }
    if (stat_result != LFS_ERR_OK || info.type != LFS_TYPE_REG
        || (info.size != sizeof(PageMapRecord)
            && info.size != sizeof(LegacyPageMapRecord))) {
      continue;
    }

    File file = primary.open(path, FILE_O_READ);
    if (!file) continue;
    if (info.size == sizeof(PageMapRecord)) {
      PageMapRecord record = {};
      const int count = file.read(reinterpret_cast<uint8_t*>(&record),
                                  sizeof(record));
      file.close();
      if (count == sizeof(record) && record.magic == PAGE_MAP_V2_MAGIC
          && record.check
              == ~(record.magic ^ record.generation ^ record.bad_pages
                   ^ record.pending_pages)
          && validPageMapValues(record.bad_pages, record.pending_pages)) {
        candidate.state = PageMapReadState::Valid;
        candidate.generation = record.generation;
        candidate.bad_pages = record.bad_pages;
        candidate.pending_pages = record.pending_pages;
        return candidate;
      }
    } else {
      LegacyPageMapRecord record = {};
      const int count = file.read(reinterpret_cast<uint8_t*>(&record),
                                  sizeof(record));
      file.close();
      if (count == sizeof(record) && record.magic == PAGE_MAP_V1_MAGIC
          && record.check
              == ~(record.magic ^ record.bad_pages ^ record.pending_pages)
          && validPageMapValues(record.bad_pages, record.pending_pages)) {
        candidate.state = PageMapReadState::Valid;
        candidate.bad_pages = record.bad_pages;
        candidate.pending_pages = record.pending_pages;
        candidate.legacy = true;
        return candidate;
      }
    }
  }
  return candidate;
}

bool newerGeneration(uint32_t candidate, uint32_t reference) {
  return static_cast<int32_t>(candidate - reference) > 0;
}

} // namespace

using mesh::storage::countInternalExtraFsBadPages;

struct lfs_config* ResilientInternalExtraFS::primaryConfig() {
  static struct lfs_config config = {};
  config.read = primaryRead;
  config.prog = primaryProg;
  config.erase = primaryErase;
  config.sync = primarySync;
  config.read_size = 128;
  config.prog_size = 128;
  config.block_size = 128;
  config.block_count = PRIMARY_SIZE / 128;
  config.lookahead = 128;
  return &config;
}

ResilientInternalExtraFS::ResilientInternalExtraFS(
    uint32_t flash_addr, uint32_t flash_size, uint32_t block_size)
    : CustomLFS(flash_addr, flash_size, block_size) {
  // Capture this before board power management can consume GPREGRET2. The
  // bootloader leaves this register alone for an ordinary app reset.
  const uint8_t retained = static_cast<uint8_t>(NRF_POWER->GPREGRET2);
  _boot_marker_at_init = retained;
  if (retained == BOOT_SCAN_ALL || retained == BOOT_SCAN_REPEAT) {
    _boot_scan_requested = true;
    _boot_scan_forced = true;
  } else if (retained >= BOOT_SCAN_BAD_PAGE_BASE
             && retained < BOOT_SCAN_BAD_PAGE_BASE + PAGE_COUNT) {
    _retained_bad_page = retained - BOOT_SCAN_BAD_PAGE_BASE;
    _boot_scan_requested = true;
  }
  _configure_lfs();
}

void ResilientInternalExtraFS::_configure_lfs() {
  CustomLFS::_configure_lfs();
  _lfs_config.read = readCallback;
  _lfs_config.prog = progCallback;
  _lfs_config.erase = eraseCallback;
  _lfs_config.sync = syncCallback;
  _lfs_config.block_count =
      (PAGE_COUNT - countInternalExtraFsBadPages(_bad_pages)) * BLOCKS_PER_PAGE;
}

uint32_t ResilientInternalExtraFS::usableBytes() const {
  return (PAGE_COUNT - countInternalExtraFsBadPages(_bad_pages)) * PAGE_SIZE;
}

bool ResilientInternalExtraFS::loadPageMap(Adafruit_LittleFS& primary) {
  _map_ready = false;
  _bad_pages = 0;
  _pending_pages = 0;
  _map_generation = 0;
  _map_repair_needed = false;

  const PageMapCandidate main = readPageMapCandidate(primary, PAGE_MAP_PATH);
  const PageMapCandidate backup =
      readPageMapCandidate(primary, PAGE_MAP_BACKUP_PATH);
  if (main.state == PageMapReadState::Missing
      && backup.state == PageMapReadState::Missing) {
    _configure_lfs();
    _map_ready = true;
    return true;
  }

  const PageMapCandidate* chosen = nullptr;
  if (main.state == PageMapReadState::Valid
      && backup.state == PageMapReadState::Valid) {
    chosen = newerGeneration(backup.generation, main.generation)
        ? &backup : &main;
    _map_repair_needed = main.legacy || backup.legacy
        || main.generation != backup.generation
        || main.bad_pages != backup.bad_pages
        || main.pending_pages != backup.pending_pages;
  } else if (main.state == PageMapReadState::Valid) {
    chosen = &main;
    _map_repair_needed = true;
  } else if (backup.state == PageMapReadState::Valid) {
    chosen = &backup;
    _map_repair_needed = true;
  } else {
    return false;
  }

  _bad_pages = chosen->bad_pages;
  _pending_pages = chosen->pending_pages;
  _map_generation = chosen->generation;
  _recorded_bad_pages = _bad_pages;
  _recorded_pending_pages = _pending_pages;
  _configure_lfs();
  _map_ready = true;
  return true;
}

bool ResilientInternalExtraFS::savePageMap(Adafruit_LittleFS& primary) {
  if (!_map_ready) return false;
  const uint32_t generation = _map_generation + 1;
  const PageMapRecord record = {
      PAGE_MAP_V2_MAGIC, generation, _bad_pages, _pending_pages,
      ~(PAGE_MAP_V2_MAGIC ^ generation ^ _bad_pages ^ _pending_pages)};
  const auto write_record = [&primary, &record](const char* path) -> bool {
    mesh::AtomicFileWriter writer(&primary, path);
    return writer
        && writer.write(reinterpret_cast<const uint8_t*>(&record),
                        sizeof(record)) == sizeof(record)
        && writer.commit();
  };
  const bool main_saved = write_record(PAGE_MAP_PATH);
  const bool backup_saved = write_record(PAGE_MAP_BACKUP_PATH);
  const bool saved = main_saved && backup_saved;
  if (saved) {
    _map_generation = generation;
    _recorded_bad_pages = _bad_pages;
    _recorded_pending_pages = _pending_pages;
    _map_repair_needed = false;
  }
  _stage = saved ? Stage::MapSaved : Stage::MapSaveFailed;
  return saved;
}

bool ResilientInternalExtraFS::pageMapNeedsSave() const {
  return _map_repair_needed
      || _bad_pages != _recorded_bad_pages
      || _pending_pages != _recorded_pending_pages;
}

void ResilientInternalExtraFS::resetPageMapForDestructiveRecovery() {
  _bad_pages = 0;
  _pending_pages = 0;
  _recorded_bad_pages = 0;
  _recorded_pending_pages = 0;
  _map_generation = 0;
  _map_repair_needed = true;
  _map_ready = true;
  _configure_lfs();
}

bool ResilientInternalExtraFS::setBootScanRequest(uint8_t value) {
  uint8_t sd_enabled = 0;
  if (mesh_nrf52::softdeviceIsEnabled(sd_enabled) != NRF_SUCCESS) return false;
  if (sd_enabled) {
    volatile uint32_t readback = 0;
    return sd_power_gpregret_clr(1, 0xFF) == NRF_SUCCESS
        && sd_power_gpregret_set(1, value) == NRF_SUCCESS
        && sd_power_gpregret_get(1, (uint32_t*)&readback) == NRF_SUCCESS
        && static_cast<uint8_t>(readback) == value;
  }
  NRF_POWER->GPREGRET2 = value;
  return static_cast<uint8_t>(NRF_POWER->GPREGRET2) == value;
}

bool ResilientInternalExtraFS::clearBootScanRequest() {
  if (!_boot_scan_requested) return true;
  uint8_t sd_enabled = 0;
  if (mesh_nrf52::softdeviceIsEnabled(sd_enabled) != NRF_SUCCESS) return false;
  if (sd_enabled) {
    if (sd_power_gpregret_clr(1, 0xFF) != NRF_SUCCESS) return false;
  } else {
    NRF_POWER->GPREGRET2 = 0;
  }
  _boot_scan_requested = false;
  return true;
}

bool ResilientInternalExtraFS::requestBootScan(bool force_full_scan) {
  if (!_map_ready && !force_full_scan) return false;
  // Do not touch the primary filesystem while radio/BLE tasks are running.
  // The suspect 4 KiB page (if known) fits in one retained byte. A fault hint
  // first gets a non-destructive mount and traversal at boot; only an explicit
  // user scan or unrecoverable filesystem triggers the destructive test.
  uint8_t marker = BOOT_SCAN_ALL;
  if (!force_full_scan && (_pending_pages & PAGE_MASK) != 0) {
    for (uint8_t page = 0; page < PAGE_COUNT; ++page) {
      if ((_pending_pages & (1UL << page)) != 0) {
        marker = (_retained_bad_page == page)
            ? BOOT_SCAN_REPEAT
            : BOOT_SCAN_BAD_PAGE_BASE + page;
        break;
      }
    }
  }
  return setBootScanRequest(marker);
}

void ResilientInternalExtraFS::acknowledgeRecoveredBootHint() {
  // A healthy mount and successful file traversal are enough to preserve
  // the existing filesystem after a transient runtime flash error. The
  // hint remains a diagnostic only; it is not a bad-page verdict.
  if (!_boot_scan_forced) {
    _pending_pages &= SCAN_REQUEST;
    if (_retained_bad_page < PAGE_COUNT) {
      // Power management consumes GPREGRET2 during startup. Re-arm this first
      // strike only after the filesystem has mounted and traversed cleanly. A
      // later fault on the same page becomes the forced second-strike marker.
      _boot_scan_requested =
          setBootScanRequest(BOOT_SCAN_BAD_PAGE_BASE + _retained_bad_page);
    } else {
      clearBootScanRequest();
    }
  }
}

const char* ResilientInternalExtraFS::stageName(Stage stage) {
  switch (stage) {
    case Stage::Ready: return "ready";
    case Stage::Scanning: return "scanning";
    case Stage::TooManyBadPages: return "too-many-bad-pages";
    case Stage::Scanned: return "scanned";
    case Stage::MapSaveFailed: return "map-save-failed";
    case Stage::MapSaved: return "map-saved";
    case Stage::FormatFailed: return "format-failed";
    case Stage::MountFailed: return "mount-failed";
    case Stage::ValidationFailed: return "validation-failed";
    case Stage::Repaired: return "repaired";
  }
  return "unknown";
}

uint32_t ResilientInternalExtraFS::physicalPageForBlock(
    lfs_block_t block) const {
  if (block >= _lfs_config.block_count) return INVALID_PAGE;
  return mesh::storage::internalExtraFsPhysicalPage(
      _bad_pages, block / BLOCKS_PER_PAGE);
}

void ResilientInternalExtraFS::retirePending(uint32_t physical_page) {
  if (physical_page < PAGE_COUNT) {
    _pending_pages |= 1UL << physical_page;
  }
  _io_failed = true;
  _dirty = false;
  _cached_page = INVALID_PAGE;
}

int ResilientInternalExtraFS::commitPage() {
  if (_io_failed) return LFS_ERR_CORRUPT;
  if (!_dirty) return 0;
  const uint32_t address = _flash_addr + _cached_page * PAGE_SIZE;
  if (flash_nrf5x_write(address, _page_buffer, PAGE_SIZE) != PAGE_SIZE) {
    retirePending(_cached_page);
    return LFS_ERR_CORRUPT;
  }
  const bool flush_ok = mesh_flash_nrf5x_flush_checked();
  // Read actual flash, not flash_nrf5x_read(): that API may return its RAM
  // cache even when the physical erase/program silently failed.
  if (!flush_ok
      || memcmp(reinterpret_cast<const void*>(address), _page_buffer,
                PAGE_SIZE) != 0) {
    retirePending(_cached_page);
    return LFS_ERR_CORRUPT;
  }
  _dirty = false;
  return 0;
}

int ResilientInternalExtraFS::loadPage(uint32_t physical_page) {
  if (_io_failed || physical_page >= PAGE_COUNT) return LFS_ERR_CORRUPT;
  if (physical_page == _cached_page) return 0;
  const int result = commitPage();
  if (result != 0) return result;
  flash_nrf5x_flush();
  memcpy(_page_buffer,
         reinterpret_cast<const void*>(_flash_addr + physical_page * PAGE_SIZE),
         PAGE_SIZE);
  _cached_page = physical_page;
  return 0;
}

int ResilientInternalExtraFS::readCallback(
    const struct lfs_config* config, lfs_block_t block, lfs_off_t off,
    void* buffer, lfs_size_t size) {
  FlashLock lock;
  auto* fs = static_cast<ResilientInternalExtraFS*>(config->context);
  const uint32_t page = fs->physicalPageForBlock(block);
  if (page == INVALID_PAGE || off > fs->_block_size
      || size > fs->_block_size - off || fs->loadPage(page) != 0) {
    return LFS_ERR_CORRUPT;
  }
  memcpy(buffer, fs->_page_buffer + (block % BLOCKS_PER_PAGE) * fs->_block_size
                     + off, size);
  return 0;
}

int ResilientInternalExtraFS::progCallback(
    const struct lfs_config* config, lfs_block_t block, lfs_off_t off,
    const void* buffer, lfs_size_t size) {
  FlashLock lock;
  auto* fs = static_cast<ResilientInternalExtraFS*>(config->context);
  const uint32_t page = fs->physicalPageForBlock(block);
  if (page == INVALID_PAGE || off > fs->_block_size
      || size > fs->_block_size - off || fs->loadPage(page) != 0) {
    return LFS_ERR_CORRUPT;
  }
  memcpy(fs->_page_buffer + (block % BLOCKS_PER_PAGE) * fs->_block_size + off,
         buffer, size);
  fs->_dirty = true;
  return 0;
}

int ResilientInternalExtraFS::eraseCallback(
    const struct lfs_config* config, lfs_block_t block) {
  FlashLock lock;
  auto* fs = static_cast<ResilientInternalExtraFS*>(config->context);
  const uint32_t page = fs->physicalPageForBlock(block);
  if (page == INVALID_PAGE || fs->loadPage(page) != 0) {
    return LFS_ERR_CORRUPT;
  }
  memset(fs->_page_buffer + (block % BLOCKS_PER_PAGE) * fs->_block_size,
         0xFF, fs->_block_size);
  fs->_dirty = true;
  return 0;
}

int ResilientInternalExtraFS::syncCallback(const struct lfs_config* config) {
  FlashLock lock;
  auto* fs = static_cast<ResilientInternalExtraFS*>(config->context);
  return fs->commitPage();
}

bool ResilientInternalExtraFS::rawPageEquals(uint32_t address,
                                              uint8_t value) const {
  const volatile uint8_t* raw =
      reinterpret_cast<const volatile uint8_t*>(address);
  for (uint32_t offset = 0; offset < PAGE_SIZE; offset++) {
    if (raw[offset] != value) return false;
  }
  return true;
}

bool ResilientInternalExtraFS::testPhysicalPage(uint32_t physical_page) {
  const uint32_t address = _flash_addr + physical_page * PAGE_SIZE;
  for (uint8_t attempt = 0; attempt < 2; attempt++) {
    feedInheritedWatchdog();
    flash_nrf5x_flush();
    if (!flash_nrf5x_erase(address) || !rawPageEquals(address, 0xFF)) continue;
    feedInheritedWatchdog();
    memset(_page_buffer, 0, PAGE_SIZE);
    if (flash_nrf5x_write(address, _page_buffer, PAGE_SIZE) != PAGE_SIZE) {
      continue;
    }
    flash_nrf5x_flush();
    if (!rawPageEquals(address, 0x00)) continue;
    feedInheritedWatchdog();
    if (flash_nrf5x_erase(address) && rawPageEquals(address, 0xFF)) {
      return true;
    }
  }
  return false;
}

bool ResilientInternalExtraFS::scanAndRetireBadPages() {
  if (!_map_ready || _flash_addr != 0xD4000UL || _flash_total_size != 0x19000UL
      || _block_size != 128) return false;
  if (!clearBootScanRequest()) return false;
  end();
  _cached_page = INVALID_PAGE;
  _dirty = false;
  _io_failed = false;
  _stage = Stage::Scanning;
  _scanned_pages = 0;
  _detected_bad_pages = 0;

  // A runtime readback mismatch is a reason to scan, not proof of a dead
  // physical page: SoftDevice contention can produce transient failures.
  // Retire only a page that fails the isolated early-boot erase/write/read
  // test on both attempts.
  uint32_t bad = _bad_pages;
  if (countInternalExtraFsBadPages(bad) > MAX_BAD_PAGES) {
    _stage = Stage::TooManyBadPages;
    return false;
  }
  for (uint32_t page = 0; page < PAGE_COUNT; page++) {
    feedInheritedWatchdog();
    if ((bad & (1UL << page)) != 0) continue;
    if (!testPhysicalPage(page)) {
      bad |= 1UL << page;
      _detected_bad_pages |= 1UL << page;
      if (countInternalExtraFsBadPages(bad) > MAX_BAD_PAGES) {
        _stage = Stage::TooManyBadPages;
        return false;
      }
    }
    _scanned_pages++;
  }
  _bad_pages = bad;
  _pending_pages = 0;
  _configure_lfs();
  _stage = Stage::Scanned;
  return true;
}

#endif
