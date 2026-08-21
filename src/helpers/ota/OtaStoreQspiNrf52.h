#pragma once

#if defined(OTA_QSPI_STORE) && defined(QSPIFLASH)
#error "OTA_QSPI_STORE raw staging cannot share a QSPI chip with QSPIFLASH"
#endif

#if defined(OTA_QSPI_SHARED_WISBLOCK_SPI) && defined(ETHERNET_ENABLED)
#error "WisBlock SPI OTA staging cannot share the bus/chip-select with Ethernet"
#endif

#if defined(OTA_QSPI_SHARED_WISBLOCK_SPI) && defined(OTA_SD_STORE)
#error "WisBlock SPI OTA staging cannot share the bus/chip-select with SD staging"
#endif

#if defined(OTA_QSPI_SHARED_WISBLOCK_SPI) && defined(RAK_3401)
#error "RAK3401's RAK13302 radio already owns the WisBlock SPI bus/chip-select"
#endif

#if defined(NRF52_PLATFORM) && defined(OTA_QSPI_STORE)

#include "OtaStore.h"

namespace mesh {
namespace ota {

// Persistent raw-QSPI staging for nRF52840 repeater builds. The complete .mota
// lives at external-flash offset zero, leaving the whole internal application
// region available to the bootloader as an in-place workspace. This store must
// never be enabled on a build that mounts the same chip as a filesystem.
class OtaStoreQspiNrf52 : public OtaStore {
  static const uint32_t PAGE = 4096;
  static const uint32_t PROGRAM = 256;
  static const uint32_t MAX_FLASH = 16UL * 1024 * 1024;
  static const uint32_t MAX_PAGES = MAX_FLASH / PAGE;
  static const uint32_t INVALID_PAGE = 0xFFFFFFFFUL;

  uint32_t _total = 0;
  uint32_t _flash_size = 0;
  bool _qspi_active = false;
  bool _qspi_awake = false;
  bool _qspi_ready = false;
  bool _io_ok = true;
  bool _meta_dirty = false;
  bool _data_dirty = false;
  uint32_t _data_page_index = INVALID_PAGE;
  char _error[80] = { 0 };

  alignas(4) uint8_t _meta_page[PAGE];
  alignas(4) uint8_t _data_page[PAGE];
  alignas(4) uint8_t _bounce[PROGRAM];
  uint8_t _known_pages[MAX_PAGES / 8];

  void fail(const char *message);
  bool ensureFlash();
  void releaseFlash();
  bool waitReady(uint32_t timeout_ms);
  bool customInstruction(uint8_t opcode, uint8_t length, uint8_t *rx = nullptr);
  bool dmaReadAligned(uint32_t address, uint32_t length);
  bool dmaWriteAligned(uint32_t address, uint32_t length);
  bool rawRead(uint32_t address, void *data, uint32_t length);
  bool rawWrite(uint32_t address, const void *data, uint32_t length);
  bool rawErasePage(uint32_t address);
  bool flushPage(uint32_t page, const uint8_t *data);
  bool flushMeta();
  bool flushData();
  bool useDataPage(uint32_t page);
  void resetSession();
  bool pageKnown(uint32_t page) const;
  void setPageKnown(uint32_t page);

public:
  OtaStoreQspiNrf52();
  ~OtaStoreQspiNrf52() override;

  bool begin(uint32_t total_size) override;
  bool write(uint32_t offset, const uint8_t *data, uint32_t len) override;
  bool read(uint32_t offset, uint8_t *buf, uint32_t len) const override;
  uint32_t capacity() const override;
  uint32_t staged_size() const override { return _total; }
  void clear() override;
  bool set_meta_size(uint32_t meta_bytes) override;
  bool finalize() override;
  void checkpoint() override;
  bool reopen() override;
  bool plan_layout(bool is_full, uint32_t image_size, uint32_t payload_off, uint32_t payload_size) override;

  // Sets APRV only after all app-side verification gates have passed.
  bool approve_for_bootloader();
  const char *last_error() const { return _error; }
};

} // namespace ota
} // namespace mesh

#endif
