#pragma once

#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)

#include "OtaSource.h"
#include "OtaStore.h"
#include "OtaSdTypesNrf52.h"

namespace mesh {
namespace ota {

class OtaStoreSdNrf52;

// The wire format has an 8-bit advertised count. Reserve one slot for this node's running firmware and
// use every remaining slot for persistent SD-backed mOTAs.
#ifndef OTA_SD_CACHE_MAX
#define OTA_SD_CACHE_MAX 254
#endif
#ifndef OTA_SD_CACHE_RESERVE_BYTES
#define OTA_SD_CACHE_RESERVE_BYTES (8UL * 1024UL * 1024UL)  // preserve room for manual install staging
#endif

// Persistent capture destination and MotaSource for the SD OTA archive. Completed containers live as
// /mota/<mid>.mota; an in-progress capture uses /mota/<mid>.part and can resume after interruption.
// This object shares the staging store's mounted volume so FAT metadata never has two independent
// caches for the same card.
class OtaCacheSdNrf52 : public OtaStore, public MotaSource {
public:
  OtaCacheSdNrf52();
  ~OtaCacheSdNrf52() override;

  void attach(OtaStoreSdNrf52& owner);
  bool initialize();
  void resetMedia();
  bool rescan();

  bool autoCaptureEnabled() const { return _enabled; }
  bool setAutoCaptureEnabled(bool enabled);
  bool contains(const uint8_t mid[4]);
  uint8_t capturedCount() const { return _count; }
  bool initialized() const { return _initialized; }
  const char* last_error() const { return _error; }

  void set_mid(const uint8_t mid[4]);

  // OtaStore: one resumable .part download at a time.
  bool begin(uint32_t total_size) override;
  bool write(uint32_t off, const uint8_t* data, uint32_t len) override;
  bool read(uint32_t off, uint8_t* buf, uint32_t len) const override;
  uint32_t capacity() const override;
  uint32_t staged_size() const override { return _total; }
  void clear() override;
  bool set_meta_size(uint32_t meta_bytes) override;
  bool finalize() override;
  void checkpoint() override;
  bool reopen() override;
  bool plan_layout(bool, uint32_t, uint32_t, uint32_t, bool) override { return true; }

  // MotaSource: completed files are advertised and read directly from SD on demand.
  uint8_t count() override { return _initialized ? _count : 0; }
  bool describe(uint8_t idx, MotaDesc& out) override;
  bool read(uint8_t idx, uint32_t off, uint8_t* buf, uint32_t len) override;

private:
  static const char* const DIR;
  static const char* const DISABLED_PATH;

  bool ready();
  bool ensureDir();
  bool describeFile(OtaSdFile& file, MotaDesc& out) const;
  bool quarantineFinal(const uint8_t mid[4]);
  void pathFor(const uint8_t mid[4], const char* ext, char* out, size_t cap) const;
  void fail(const char* message);

  OtaStoreSdNrf52* _owner = nullptr;
  OtaSdFile* _file = nullptr;
#if defined(OTA_RAK15002_SD_STORE)
  // Valid archive files all have the fixed manifest followed immediately by
  // leaves and payload. Keep every descriptor field without caching three
  // redundant offsets/sizes per entry (192 bytes saved at 16 entries).
  struct CachedDesc {
    uint8_t mid[4];
    uint32_t target_id, fw_version, block_count, payload_size;
    uint8_t codec_id, flags, block_size_log2, source_caps;
    CachedDesc& operator=(const MotaDesc& d) {
      for (unsigned i = 0; i < 4; ++i) mid[i] = d.mid[i];
      target_id = d.target_id; fw_version = d.fw_version;
      block_count = d.block_count; payload_size = d.payload_size;
      codec_id = d.codec_id; flags = d.flags;
      block_size_log2 = d.block_size_log2; source_caps = d.source_caps;
      return *this;
    }
    uint32_t totalSize() const { return 8u + MOTA_MFL + block_count * 4u + payload_size + 5u; }
    operator MotaDesc() const {
      MotaDesc d;
      for (unsigned i = 0; i < 4; ++i) d.mid[i] = mid[i];
      d.target_id = target_id; d.fw_version = fw_version;
      d.block_count = block_count; d.payload_size = payload_size;
      d.codec_id = codec_id; d.flags = flags;
      d.block_size_log2 = block_size_log2; d.source_caps = source_caps;
      d.leaves_off = 8u + MOTA_MFL;
      d.payload_off = d.leaves_off + block_count * 4u;
      d.total_size = totalSize();
      return d;
    }
  };
  static_assert(sizeof(CachedDesc) == 24u, "bounded RAK SD index changed");
  CachedDesc _entries[OTA_SD_CACHE_MAX];
#else
  MotaDesc _entries[OTA_SD_CACHE_MAX];
#endif
  uint8_t _count = 0;
  uint8_t _mid[4] = {0};
  uint32_t _total = 0;
  bool _initialized = false;
  bool _enabled = true;
  char _error[80] = {0};
};

} // namespace ota
} // namespace mesh

#endif
