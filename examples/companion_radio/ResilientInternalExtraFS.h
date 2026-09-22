#pragma once

#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)

#include <CustomLFS.h>
#include <helpers/nrf52/InternalExtraFsPageMap.h>

// The T1000-E and other nRF52 internal-ExtraFS companions use 25 physical
// 4 KiB pages. LittleFS sees 128-byte blocks; a failed physical page must be
// removed as a whole, not treated as 32 unrelated bad blocks.
class ResilientInternalExtraFS : public CustomLFS {
public:
  enum class Stage : uint8_t {
    Ready, Scanning, TooManyBadPages, Scanned, MapSaveFailed,
    MapSaved, FormatFailed, MountFailed, ValidationFailed, Repaired
  };
  static constexpr uint32_t PAGE_COUNT =
      mesh::storage::INTERNAL_EXTRAFS_PAGE_COUNT;
  static constexpr uint32_t PAGE_SIZE = FLASH_NRF52_PAGE_SIZE;
  static constexpr uint32_t BLOCKS_PER_PAGE = PAGE_SIZE / 128;

  ResilientInternalExtraFS(uint32_t flash_addr, uint32_t flash_size,
                           uint32_t block_size);
  static struct lfs_config* primaryConfig();

  // Read the per-device page map from primary identity storage before the
  // first secondary mount. An unreadable map must never be guessed as empty.
  bool loadPageMap(Adafruit_LittleFS& primary);
  bool pageMapReady() const { return _map_ready; }
  bool recoveryPending() const {
    return _boot_scan_forced || (_pending_pages & SCAN_REQUEST) != 0;
  }
  bool ioFailed() const { return _io_failed; }
  uint32_t badPages() const { return _bad_pages; }
  uint32_t pendingPages() const { return _pending_pages; }
  uint32_t usableBytes() const;
  Stage stage() const { return _stage; }
  uint8_t scannedPages() const { return _scanned_pages; }
  uint32_t detectedBadPages() const { return _detected_bad_pages; }
  uint8_t bootMarkerAtInit() const { return _boot_marker_at_init; }
  static const char* stageName(Stage stage);
  void setStage(Stage stage) { _stage = stage; }

  // Only call after ExtraFS has been unmounted and its contents have been
  // declared unrecoverable. Tests every physical page by erase / program /
  // direct flash readback / erase, then maps LittleFS around bad pages.
  bool scanAndRetireBadPages();
  bool savePageMap(Adafruit_LittleFS& primary);
  bool pageMapNeedsSave() const;
  void resetPageMapForDestructiveRecovery();
  void requirePageMapRewrite() { _map_repair_needed = true; }
  bool requestBootScan(bool force_full_scan = false);
  void acknowledgeRecoveredBootHint();

protected:
  void _configure_lfs() override;

private:
  static constexpr uint32_t INVALID_PAGE = 0xFFFFFFFFUL;
  static constexpr uint32_t PAGE_MASK =
      mesh::storage::INTERNAL_EXTRAFS_PAGE_MASK;
  static constexpr uint32_t SCAN_REQUEST = 1UL << 31;
  static constexpr uint8_t MAX_BAD_PAGES = 4;
  // GPREGRET2 is retained across a software reset. Reserve 0x20..0x38 for
  // the 25 physical ExtraFS pages and 0x3E/0x3F for aggregate scans. This is
  // deliberately disjoint from OTA staging values, legacy apply results
  // 0x90..0x9F and 0xB1..0xBF, and bootloader-update results 0xC1..0xC9.
  static constexpr uint8_t BOOT_SCAN_ALL = 0x3F;
  static constexpr uint8_t BOOT_SCAN_REPEAT = 0x3E;
  static constexpr uint8_t BOOT_SCAN_BAD_PAGE_BASE = 0x20;

  uint32_t _bad_pages = 0;
  uint32_t _pending_pages = 0;
  bool _boot_scan_requested = false;
  bool _boot_scan_forced = false;
  uint8_t _boot_marker_at_init = 0;
  uint8_t _retained_bad_page = 0xFF;
  uint32_t _recorded_bad_pages = 0;
  uint32_t _recorded_pending_pages = 0;
  uint32_t _map_generation = 0;
  bool _map_repair_needed = false;
  uint32_t _cached_page = INVALID_PAGE;
  bool _dirty = false;
  bool _io_failed = false;
  bool _map_ready = false;
  Stage _stage = Stage::Ready;
  uint8_t _scanned_pages = 0;
  uint32_t _detected_bad_pages = 0;
  alignas(4) uint8_t _page_buffer[PAGE_SIZE];

  uint32_t physicalPageForBlock(lfs_block_t block) const;
  int loadPage(uint32_t physical_page);
  int commitPage();
  bool testPhysicalPage(uint32_t physical_page);
  bool rawPageEquals(uint32_t address, uint8_t value) const;
  void retirePending(uint32_t physical_page);
  bool setBootScanRequest(uint8_t value);
  bool clearBootScanRequest();

  static int readCallback(const struct lfs_config* config, lfs_block_t block,
                          lfs_off_t off, void* buffer, lfs_size_t size);
  static int progCallback(const struct lfs_config* config, lfs_block_t block,
                          lfs_off_t off, const void* buffer, lfs_size_t size);
  static int eraseCallback(const struct lfs_config* config, lfs_block_t block);
  static int syncCallback(const struct lfs_config* config);
};

#endif
