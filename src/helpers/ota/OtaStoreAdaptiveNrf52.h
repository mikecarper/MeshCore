#pragma once

#if defined(NRF52_PLATFORM) && defined(OTA_RAK_AUTO_STORE)

#include "OtaBlInfo.h"
#include "OtaApply.h"
#include "OtaBootloaderUpdate.h"
#include "OtaStoreFlashNrf52.h"
#include "OtaStoreQspiNrf52.h"
#include "OtaRakStoragePolicy.h"
#if defined(RAK4631_COMBINED_ETHERNET)
  #include "../nrf52/Rak4631SharedSpi.h"
#endif
#include <new>

namespace mesh {
namespace ota {

// One RAK application image supports the board's internal OTA store and its
// explicitly matched external NOR. Application placement follows the detected
// hardware. Bootloader packages always use internal flash; select the backend
// before begin(), never move a partially fetched container between stores.
class OtaStoreAdaptiveNrf52 : public OtaStore {
  enum Mode : uint8_t { UNSELECTED, INTERNAL_MODE, QSPI_MODE, UNSAFE };
  mutable Mode _mode = UNSELECTED;
  mutable const char* _reason = "not probed";
  bool _bootloader_store = false;
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

  bool selectBootloader() {
    if (!ota_bootloader_self_update_caps_valid(ota_bootloader_update_caps())) return false;
    if (_mode != INTERNAL_MODE) {
      if (_mode == QSPI_MODE) _storage.external.~OtaStoreQspiNrf52();
      activateInternal();
    }
    _bootloader_store = true;
    _reason = "bootloader package in internal flash";
    return true;
  }

  void selectApplication() {
    if (_bootloader_store) {
      _storage.internal.~OtaStoreFlashNrf52();
      _mode = UNSELECTED;
      _bootloader_store = false;
    }
    select();
  }

  void select() const {
    if (_mode != UNSELECTED) return;
#if defined(RAK4631_COMBINED_ETHERNET)
    const bool ethernet = rak4631_ethernet_owns_spi();
    const uint8_t detected = ethernet ? 0u : OtaStoreQspiNrf52::autoDetect();
#else
    const uint8_t detected = OtaStoreQspiNrf52::autoDetect();
#endif
    const OtaBlCaps caps = ota_bootloader_app_caps();
    if (detected == 3u) {
      _mode = UNSAFE;
      _reason = "both external NOR types detected";
      return;
    }
    const bool qspi_bootloader = caps.present &&
        (caps.storage_flags & OTA_BL_STORAGE_QSPI) != 0;
    if (detected == 2u && OtaStoreQspiNrf52::headerW25Detected() &&
        (caps.storage_flags & OTA_BL_STORAGE_HEADER_W25) == 0) {
      activateInternal();
      _reason = "header W25 needs newer OTAFIX bootloader";
      return;
    }
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
        identity_valid ? identity.device_name : nullptr, rak3401,
        caps.optional_app_storage == (OTA_BL_STORAGE_QSPI | OTA_BL_STORAGE_HEADER_W25));
    if (choice == RakStorageChoice::Internal) {
      activateInternal();
#if defined(RAK4631_COMBINED_ETHERNET)
      if (ethernet) {
        _reason = "Ethernet active; internal application deltas only";
        return;
      }
#endif
      _reason = detected == 0u ? "no external NOR" :
                "external NOR fitted; internal bootloader";
      return;
    }
    if (choice == RakStorageChoice::Qspi) {
      activateExternal();
      _reason = detected == 1u ? "RAK15001 C" :
                OtaStoreQspiNrf52::headerW25Detected() ? "W25Q16 header" : "W25Q16 SPI";
      return;
    }
    _mode = UNSAFE;
#if defined(RAK4631_COMBINED_ETHERNET)
    if (ethernet) {
      _reason = "Ethernet active; bootloader lacks internal OTA";
      return;
    }
#endif
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
#if defined(RAK4631_COMBINED_ETHERNET)
    if (_mode == QSPI_MODE && rak4631_ethernet_owns_spi()) return nullptr;
#endif
    return _mode == QSPI_MODE ? static_cast<OtaStore*>(&_storage.external) :
           _mode == INTERNAL_MODE ? static_cast<OtaStore*>(&_storage.internal) : nullptr;
  }
  const OtaStore* active() const {
    select();
#if defined(RAK4631_COMBINED_ETHERNET)
    if (_mode == QSPI_MODE && rak4631_ethernet_owns_spi()) return nullptr;
#endif
    return _mode == QSPI_MODE ? static_cast<const OtaStore*>(&_storage.external) :
           _mode == INTERNAL_MODE ? static_cast<const OtaStore*>(&_storage.internal) : nullptr;
  }

public:
#if defined(RAK4631_COMBINED_ETHERNET)
  // Only the main-loop handoff may reset the selection, after OtaContext has
  // proved that no receive/apply session is active. Never discard staged data
  // as a side effect of changing a network setting.
  bool resetSelection() {
    if (_mode == QSPI_MODE) {
      if (_storage.external.staged_size() != 0u) return false;
      _storage.external.~OtaStoreQspiNrf52();
    } else if (_mode == INTERNAL_MODE) {
      if (_storage.internal.staged_size() != 0u) return false;
      _storage.internal.~OtaStoreFlashNrf52();
    }
    _mode = UNSELECTED;
    _reason = "not probed";
    _bootloader_store = false;
    return true;
  }
#endif
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
  bool reopen() override { return reopenFor(nullptr, 0u); }
  bool reopenFor(const uint8_t* mid, uint32_t target) override {
    selectApplication();
    OtaStore* s = active();
    if (s && s->reopenFor(mid, target)) return true;
    // Only an explicit MID pull may resume privileged data. Automatic resume
    // stays on the application backend. Validate kind before adopting this
    // alternative store; the manager repeats all manifest and block checks.
    if (!mid || _mode == INTERNAL_MODE || !selectBootloader()) return false;
    uint8_t raw[MOTA_MFL];
    MotaManifest manifest;
    if (_storage.internal.reopenFor(mid, target) &&
        _storage.internal.read(8u, raw, sizeof(raw)) &&
        mota_parse_manifest(raw, sizeof(raw), manifest) && manifest.is_bootloader())
      return true;
    selectApplication();
    return false;
  }
  bool plan_layout(bool full, uint32_t image, uint32_t payload_off,
                   uint32_t payload_size, bool bootloader) override {
    if (bootloader) {
      if (!selectBootloader()) return false;
    } else {
      selectApplication();
    }
    OtaStore* s = active();
    return s && s->plan_layout(full, image, payload_off, payload_size, bootloader);
  }
};

} // namespace ota
} // namespace mesh

#endif
