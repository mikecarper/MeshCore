#pragma once

#if defined(NRF52_PLATFORM) && defined(OTA_RAK_AUTO_STORE)

#include "OtaBlInfo.h"
#include "OtaApply.h"
#include "OtaBootloaderUpdate.h"
#include "OtaStoreFlashNrf52.h"
#include "OtaStoreQspiNrf52.h"
#include "OtaRakStoragePolicy.h"
#include <new>

namespace mesh {
namespace ota {

// One RAK application image supports the board's internal OTA store and its
// explicitly matched external NOR. Selection is fixed for the lifetime of an
// app boot; no staged container is ever moved between backends.
class OtaStoreAdaptiveNrf52 : public OtaStore {
  enum Mode : uint8_t { UNSELECTED, INTERNAL_MODE, QSPI_MODE, UNSAFE };
  mutable Mode _mode = UNSELECTED;
  mutable const char* _reason = "not probed";
  // Only one backend is usable for a given board/bootloader pairing. Sharing
  // their two flash-page buffers saves 8 KiB of scarce nRF52840 runtime RAM.
  union StoreStorage {
    OtaStoreFlashNrf52 internal;
    OtaStoreQspiNrf52 external;
    StoreStorage() {}
    ~StoreStorage() {}
  };
  mutable StoreStorage _storage;

  void activateInternal() const {
    new (&_storage.internal) OtaStoreFlashNrf52();
    _mode = INTERNAL_MODE;
  }
  void activateExternal() const {
    new (&_storage.external) OtaStoreQspiNrf52();
    _mode = QSPI_MODE;
  }

  void select() const {
    if (_mode != UNSELECTED) return;
    const uint8_t detected = OtaStoreQspiNrf52::autoDetect();
    const OtaBlCaps caps = ota_bootloader_app_caps();
    if (detected == 3u) {
      _mode = UNSAFE;
      _reason = "both external NOR types detected";
      return;
    }
    const bool qspi_bootloader = caps.present &&
        (caps.storage_flags & OTA_BL_STORAGE_QSPI) != 0;
    OtaBootloaderIdentity identity;
    const bool identity_valid = qspi_bootloader &&
        ota_installed_bootloader_identity(identity) && identity.crc_ok;
#if defined(RAK_3401)
    const bool rak3401 = true;
#else
    const bool rak3401 = false;
#endif
    const RakStorageChoice choice = rak_storage_choice(
        detected, qspi_bootloader, identity_valid,
        identity_valid ? identity.device_name : nullptr, rak3401);
    if (choice == RakStorageChoice::Internal) {
      activateInternal();
      _reason = detected == 0u ? "no external NOR" :
                "external NOR fitted; internal bootloader";
      return;
    }
    if (choice == RakStorageChoice::Qspi) {
      activateExternal();
      _reason = detected == 1u ? "RAK15001 C" : "W25Q16";
      return;
    }
    _mode = UNSAFE;
    if (detected == 0u) {
      _reason = "QSPI bootloader but no matched external NOR";
    } else if (!identity_valid) {
      _reason = "QSPI bootloader identity unavailable";
    } else {
      _reason = "external NOR and OTAFIX bootloader do not match";
    }
  }

  OtaStore* active() {
    select();
    return _mode == QSPI_MODE ? static_cast<OtaStore*>(&_storage.external) :
           _mode == INTERNAL_MODE ? static_cast<OtaStore*>(&_storage.internal) : nullptr;
  }
  const OtaStore* active() const {
    select();
    return _mode == QSPI_MODE ? static_cast<const OtaStore*>(&_storage.external) :
           _mode == INTERNAL_MODE ? static_cast<const OtaStore*>(&_storage.internal) : nullptr;
  }

public:
  ~OtaStoreAdaptiveNrf52() override {
    if (_mode == QSPI_MODE) _storage.external.~OtaStoreQspiNrf52();
    else if (_mode == INTERNAL_MODE) _storage.internal.~OtaStoreFlashNrf52();
  }
  bool usesExternal() const { select(); return _mode == QSPI_MODE; }
  bool usesInternal() const { select(); return _mode == INTERNAL_MODE; }
  const char* selectionReason() const { select(); return _reason; }
  OtaStoreFlashNrf52& internalStore() { select(); return _storage.internal; }
  OtaStoreQspiNrf52& externalStore() { select(); return _storage.external; }
  uint32_t qspiCapacity() const { return usesExternal() ? _storage.external.capacity() : 0u; }
  uint32_t jedec_id() const { return usesExternal() ? _storage.external.jedec_id() : 0u; }
  uint8_t status1() const { return usesExternal() ? _storage.external.status1() : 0xFFu; }
  const char* last_stage() const { return usesExternal() ? _storage.external.last_stage() : "inactive"; }
  const char* last_error() const {
    return usesExternal() ? _storage.external.last_error() : selectionReason();
  }

  bool begin(uint32_t n) override { OtaStore* s = active(); return s && s->begin(n); }
  bool write(uint32_t p, const uint8_t* d, uint32_t n) override {
    OtaStore* s = active(); return s && s->write(p, d, n);
  }
  bool read(uint32_t p, uint8_t* d, uint32_t n) const override {
    const OtaStore* s = active(); return s && s->read(p, d, n);
  }
  uint32_t capacity() const override {
    const OtaStore* s = active(); return s ? s->capacity() : 0u;
  }
  uint32_t staged_size() const override {
    const OtaStore* s = active(); return s ? s->staged_size() : 0u;
  }
  void clear() override { OtaStore* s = active(); if (s) s->clear(); }
  bool discard() override { OtaStore* s = active(); return s && s->discard(); }
  bool set_meta_size(uint32_t n) override {
    OtaStore* s = active(); return s && s->set_meta_size(n);
  }
  bool finalize() override { OtaStore* s = active(); return s && s->finalize(); }
  void checkpoint() override { OtaStore* s = active(); if (s) s->checkpoint(); }
  bool reopen() override { OtaStore* s = active(); return s && s->reopen(); }
  bool reopenFor(const uint8_t* mid, uint32_t target) override {
    OtaStore* s = active(); return s && s->reopenFor(mid, target);
  }
  bool plan_layout(bool full, uint32_t image, uint32_t payload_off,
                   uint32_t payload_size, bool bootloader) override {
    OtaStore* s = active();
    return s && s->plan_layout(full, image, payload_off, payload_size, bootloader);
  }
};

} // namespace ota
} // namespace mesh

#endif
