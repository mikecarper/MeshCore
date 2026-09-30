#pragma once
#if defined(NRF52_PLATFORM) && defined(OTA_SD_DUAL_STORE)
#include "OtaBlInfo.h"
#include "OtaStoreFlashNrf52.h"
#include "OtaStoreSdNrf52.h"
#include "OtaTowerStorageConfig.h"
#include <new>

namespace mesh { namespace ota {
// Only construct the configured backend. Disabled SD must not mount, allocate
// SdFat objects, seed archives, or touch SPI. Never fall back after an error.
class OtaStoreTowerNrf52 : public OtaStore {
  enum Mode : uint8_t { Unselected, Internal, Sd };
  mutable Mode _mode = Unselected;
  union Storage {
    OtaStoreFlashNrf52 internal;
    OtaStoreSdNrf52 sd;
    Storage() {}
    ~Storage() {}
  };
  mutable Storage _storage;
  void select() const {
    if (_mode != Unselected) return;
    if (towerSdEnabledAtBoot()) {
      new (&_storage.sd) OtaStoreSdNrf52();
      _mode = Sd;
    } else {
      new (&_storage.internal) OtaStoreFlashNrf52();
      _mode = Internal;
    }
  }
  bool compatible() const {
    const OtaBlCaps caps = ota_bootloader_app_caps();
    return caps.present && (caps.storage_flags &
        (usesExternal() ? OTA_BL_STORAGE_SD : OTA_BL_STORAGE_STAGE_CEILING));
  }
  OtaStore* activeStore() {
    select();
    return _mode == Sd ? static_cast<OtaStore*>(&_storage.sd) : &_storage.internal;
  }
  const OtaStore* activeStore() const {
    select();
    return _mode == Sd ? static_cast<const OtaStore*>(&_storage.sd) : &_storage.internal;
  }
public:
  ~OtaStoreTowerNrf52() override {
    if (_mode == Sd) _storage.sd.~OtaStoreSdNrf52();
    else if (_mode == Internal) _storage.internal.~OtaStoreFlashNrf52();
  }
  bool usesExternal() const { select(); return _mode == Sd; }
  bool usesInternal() const { return !usesExternal(); }
  OtaStoreFlashNrf52& internalStore() { select(); return _storage.internal; }
  OtaStoreSdNrf52& externalStore() { select(); return _storage.sd; }
  const char* selectionReason() const { return usesExternal() ? "SD enabled" : "SD disabled"; }
  const char* last_error() const {
    return usesExternal() ? _storage.sd.last_error() : "SD disabled; use set sdcard on and reboot";
  }
  bool begin(uint32_t n) override { return compatible() && activeStore()->begin(n); }
  bool write(uint32_t p, const uint8_t* d, uint32_t n) override { return activeStore()->write(p, d, n); }
  bool read(uint32_t p, uint8_t* d, uint32_t n) const override { return activeStore()->read(p, d, n); }
  uint32_t capacity() const override { return compatible() ? activeStore()->capacity() : 0; }
  uint32_t staged_size() const override { return activeStore()->staged_size(); }
  void clear() override { activeStore()->clear(); }
  bool discard() override { return activeStore()->discard(); }
  bool set_meta_size(uint32_t n) override { return activeStore()->set_meta_size(n); }
  bool finalize() override { return activeStore()->finalize(); }
  void checkpoint() override { activeStore()->checkpoint(); }
  bool reopen() override { return compatible() && activeStore()->reopen(); }
  bool reopenFor(const uint8_t* mid, uint32_t target) override {
    return compatible() && activeStore()->reopenFor(mid, target);
  }
  bool plan_layout(bool full, uint32_t image, uint32_t offset, uint32_t payload, bool boot) override {
    return compatible() && activeStore()->plan_layout(full, image, offset, payload, boot);
  }
  bool formatCard(MainBoard& board) { return usesExternal() && _storage.sd.formatCard(board); }
  bool eraseCard(MainBoard& board) { return usesExternal() && _storage.sd.eraseCard(board); }
  bool getSpace(MainBoard& board, uint64_t& used, uint64_t& free) {
    return usesExternal() && _storage.sd.getSpace(board, used, free);
  }
  bool listFiles(MainBoard& board, uint16_t page, char* reply, size_t capacity) {
    return usesExternal() && _storage.sd.listFiles(board, page, reply, capacity);
  }
};
} }
#endif
