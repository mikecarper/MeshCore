#include "ResilientInternalExtraFS.h"

#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)

#include <helpers/IdentityStore.h>
#include <helpers/AtomicFileWriter.h>
#include <helpers/nrf52/SoftDeviceState.h>
#include <nrf_soc.h>
#include <string.h>

namespace {
constexpr char PAGE_MAP_PATH[] = "/extrafs.badpages";
constexpr uint32_t PAGE_MAP_MAGIC = 0x31475042UL; // "BPG1" on nRF52
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
  flash_nrf5x_flush();
  return 0;
}

struct PageMapRecord {
  uint32_t magic;
  uint32_t bad_pages;
  uint32_t pending_pages;
  uint32_t check;
};

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
  if (retained == BOOT_SCAN_ALL) {
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
  struct lfs_info info;
  const int stat_result = lfs_stat(primary._getFS(), PAGE_MAP_PATH, &info);
  if (stat_result == LFS_ERR_NOENT) {
    _configure_lfs();
    _map_ready = true;
    return true;
  }
  if (stat_result != LFS_ERR_OK || info.type != LFS_TYPE_REG
      || info.size != sizeof(PageMapRecord)) return false;

  File file = primary.open(PAGE_MAP_PATH, FILE_O_READ);
  if (!file) return false;
  PageMapRecord record = {};
  const int read_count = file.read(reinterpret_cast<uint8_t*>(&record),
                                   sizeof(record));
  file.close();
  if (read_count != sizeof(record) || record.magic != PAGE_MAP_MAGIC
      || record.check != ~(record.magic ^ record.bad_pages
                           ^ record.pending_pages)
      || (record.bad_pages & ~PAGE_MASK) != 0
      || (record.pending_pages & ~(PAGE_MASK | SCAN_REQUEST)) != 0
      || (record.bad_pages & record.pending_pages & PAGE_MASK) != 0
      || countInternalExtraFsBadPages(
             record.bad_pages | (record.pending_pages & PAGE_MASK))
          > MAX_BAD_PAGES) {
    return false;
  }
  _bad_pages = record.bad_pages;
  _pending_pages = record.pending_pages;
  _recorded_bad_pages = record.bad_pages;
  _recorded_pending_pages = record.pending_pages;
  _configure_lfs();
  _map_ready = true;
  return true;
}

bool ResilientInternalExtraFS::savePageMap(Adafruit_LittleFS& primary) {
  if (!_map_ready) return false;
  const PageMapRecord record = {
      PAGE_MAP_MAGIC, _bad_pages, _pending_pages,
      ~(PAGE_MAP_MAGIC ^ _bad_pages ^ _pending_pages)};
  mesh::AtomicFileWriter writer(&primary, PAGE_MAP_PATH);
  const bool saved = writer
      && writer.write(reinterpret_cast<const uint8_t*>(&record),
                      sizeof(record)) == sizeof(record)
      && writer.commit();
  if (saved) {
    _recorded_bad_pages = _bad_pages;
    _recorded_pending_pages = _pending_pages;
  }
  _stage = saved ? Stage::MapSaved : Stage::MapSaveFailed;
  return saved;
}

bool ResilientInternalExtraFS::pageMapNeedsSave() const {
  return _bad_pages != _recorded_bad_pages
      || _pending_pages != _recorded_pending_pages;
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

bool ResilientInternalExtraFS::requestBootScan() {
  if (!_map_ready) return false;
  // Do not touch the primary filesystem while radio/BLE tasks are running.
  // The suspect 4 KiB page (if known) fits in one retained byte. A fault hint
  // first gets a non-destructive mount and traversal at boot; only an explicit
  // user scan or unrecoverable filesystem triggers the destructive test.
  uint8_t marker = BOOT_SCAN_ALL;
  if ((_pending_pages & PAGE_MASK) != 0) {
    for (uint8_t page = 0; page < PAGE_COUNT; ++page) {
      if ((_pending_pages & (1UL << page)) != 0) {
        marker = BOOT_SCAN_BAD_PAGE_BASE + page;
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
    clearBootScanRequest();
    _pending_pages &= SCAN_REQUEST;
    _retained_bad_page = 0xFF;
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
  flash_nrf5x_flush();
  // Read actual flash, not flash_nrf5x_read(): that API may return its RAM
  // cache even when the physical erase/program silently failed.
  if (memcmp(reinterpret_cast<const void*>(address), _page_buffer,
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
