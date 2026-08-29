#pragma once

#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)

#include "OtaStore.h"

class SdFs;
class FsFile;

namespace mesh {

class MainBoard;

namespace ota {

class OtaCacheSdNrf52;

// Persistent nRF52840 OTA store backed by the MeshTower V2 microSD socket.
// The .mota remains a normal file, but is preallocated contiguously so the
// bootloader can read it by raw sectors without embedding a FAT implementation.
// The exact self-update build uses this same one-file store for either an
// application package or an explicitly confirmed signed bootloader package.
class OtaStoreSdNrf52 : public OtaStore {
public:
  OtaStoreSdNrf52();
  ~OtaStoreSdNrf52() override;
  bool begin(uint32_t total_size) override;
  bool write(uint32_t offset, const uint8_t* data, uint32_t len) override;
  bool read(uint32_t offset, uint8_t* buf, uint32_t len) const override;
  uint32_t capacity() const override;
  uint32_t staged_size() const override { return _total; }
  void clear() override;
  bool discard() override;
  bool set_meta_size(uint32_t meta_bytes) override;
  bool finalize() override;
  void checkpoint() override;
  bool reopen() override;
  bool plan_layout(bool is_full, uint32_t image_size,
                   uint32_t payload_off, uint32_t payload_size,
                   bool is_bootloader) override;

  // Called only after the app has verified package kind, payload, applicable
  // base/identity, signature, trust, and storage-specific safety geometry.
  // Every package writes APRV, then publishes a reset-retained authorization
  // record containing geometry plus the normalized SHA-256 produced by the
  // same verification pass. Bootloader packages additionally bind the exact
  // authenticated signed image_hash to the E0000 scratch token.
  bool approve_for_bootloader(
      const uint8_t expected_boot_image_hash[32],
      const uint8_t authorized_container_hash[32]);
  bool formatCard(MainBoard& board);
  bool eraseCard(MainBoard& board);
  bool getSpace(MainBoard& board, uint64_t& used_bytes, uint64_t& free_bytes);
  bool listFiles(MainBoard& board, uint16_t page, char* reply, size_t cap);
  const char* last_error() const { return _error; }

private:
  friend class OtaCacheSdNrf52;
  static const char* const PATH;

  bool mount();
  bool beginCardOnly();
  bool locate_file();
  void resetStoreState();
  void fail(const char* message);

  SdFs* _sd = nullptr;
  mutable FsFile* _file = nullptr;
  bool _mounted = false;
  bool _planned_bootloader = false;
  uint32_t _total = 0;
  uint32_t _first_sector = 0;
  uint32_t _allocated_sectors = 0;
  char _error[80] = {0};
};

} // namespace ota
} // namespace mesh

#endif
