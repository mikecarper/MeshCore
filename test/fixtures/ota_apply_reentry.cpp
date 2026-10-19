extern "C" void meshRegulatorCleanReset() __attribute__((weak));

#include <helpers/ota/MotaContainer.h>
#include <helpers/ota/OtaByteIO.h>
#include <helpers/ota/FirmwareInfo.h>
#include <helpers/ota/OtaFlashLayout_nrf52.h>
#include <helpers/ota/OtaApply.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "test_ota/mota_vectors.h"
using namespace mesh::ota;
namespace mesh { namespace ota {
// Only the physical store is adapted. Parsing, context/CLI admission and
// reboot dispatch below come from production. The internal applier includes
// its real entry through the first read-rejection boundary; later flash and
// cryptographic work is an explicit stub, not a simulated second applier.
static unsigned store_calls = 0, metadata_calls = 0, apply_calls = 0;
static bool apply_ok = true;
struct StoreBytes {
  std::vector<uint8_t> bytes{MOTA_VEC, MOTA_VEC + MOTA_VEC_LEN};
  mutable unsigned reads = 0;
  unsigned fail_on_read = 0;
  bool hybrid = false;
  uint32_t staged_size() const { ++store_calls; return bytes.size(); }
  bool read(uint32_t off, uint8_t* dest, uint32_t n) const {
    ++store_calls;
    if (++reads == fail_on_read) return false;
    assert(off <= bytes.size() && n <= bytes.size() - off);
    memcpy(dest, bytes.data() + off, n); return true;
  }
  const uint8_t* data() const { ++store_calls; return bytes.data(); }
  bool is_hybrid() const { ++store_calls; return hybrid; }
  bool publish_hybrid_handoff() { return true; }
};
struct OtaStoreFlashNrf52 : StoreBytes {};
struct OtaStoreQspiNrf52 : StoreBytes {};
struct OtaStoreSdNrf52 : StoreBytes {};
static OtaStoreFlashNrf52* g_nrf52_apply_store = nullptr;
static uint8_t g_nrf52_external_handoff = GPREGRET2_OTA_STAGE_QSPI;
static unsigned resets = 0;
static uint8_t emitted_request = 0, emitted_source = 0;
static bool softdevice_enabled = false;
struct PowerRegisters { uint32_t GPREGRET = 0, GPREGRET2 = 0; } power_registers;
#define NRF_POWER (&power_registers)
namespace mesh_nrf52 {
bool softdeviceIsEnabled(uint8_t& enabled) { enabled = softdevice_enabled; return true; }
}
void sd_power_gpregret_clr(uint32_t index, uint32_t bits) {
  (index ? power_registers.GPREGRET2 : power_registers.GPREGRET) &= ~bits;
}
void sd_power_gpregret_set(uint32_t index, uint32_t bits) {
  (index ? power_registers.GPREGRET2 : power_registers.GPREGRET) |= bits;
}
bool ota_nrf52_clear_reset_reasons() { return true; }
uint32_t probe_ceiling() { return MOTA_NRF52_STAGE_CEILING_EXPANDED; }
void ota_nrf52_set_reset_handoff(uint8_t request, uint8_t source) {
  power_registers.GPREGRET = request; power_registers.GPREGRET2 = source;
}
void NVIC_SystemReset() {
  emitted_request = power_registers.GPREGRET;
  emitted_source = power_registers.GPREGRET2;
  ++resets;
}
static bool boundaryApply(ApplyState& st, char* msg);
@ENTRY@
static bool boundaryApply(ApplyState& st, char* msg) {
  ++apply_calls;
  if (!apply_ok) { strcpy(msg, "injected verification failure"); return false; }
  st.manifest_ok = st.slot_ok = true;
  strcpy(msg, "verified");
  return true;
}
bool ota_apply_mota_nrf52(OtaStoreQspiNrf52&, const SignerAllowlist&, ApplyState& st, char* msg) {
  g_nrf52_apply_store = nullptr;
  if (!boundaryApply(st, msg)) return false;
  g_nrf52_external_handoff = GPREGRET2_OTA_STAGE_QSPI;
  return true;
}
bool ota_apply_mota_nrf52(OtaStoreSdNrf52&, const SignerAllowlist&, ApplyState& st, char* msg) {
  g_nrf52_apply_store = nullptr;
  if (!boundaryApply(st, msg)) return false;
  g_nrf52_external_handoff = GPREGRET2_OTA_STAGE_SD;
  return true;
}
bool ota_rescue_mota_nrf52(const uint8_t*, uint32_t, const SignerAllowlist&,
                          const uint8_t*, uint32_t, ApplyState& st, char* msg) {
  return boundaryApply(st, msg);
}
bool ota_prepare_bootloader_update_nrf52(OtaStoreFlashNrf52&, const SignerAllowlist&,
    const OtaBootloaderIdentity&, const uint8_t*, const uint8_t*, const uint8_t*,
    ApplyState& st, char* msg) {
  return boundaryApply(st, msg);
}
#define ota_nrf52_effective_stage_ceiling probe_ceiling
@REBOOT@
#undef ota_nrf52_effective_stage_ceiling
@BOOT_REBOOT@
bool ota_self_firmware(SelfFwInfo& info) {
  ++metadata_calls; info.valid = true; info.fw_version = 1; return true;
}
struct OtaManager {
  enum FetchState { IDLE, COMPLETE };
  FetchState fetchState() const { ++metadata_calls; return COMPLETE; }
  unsigned blocksHave() const { return 1; }
  unsigned blocksTotal() const { return 1; }
  const uint8_t* fetchManifestId() const { static uint8_t mid[4] = {1}; ++metadata_calls; return mid; }
  uint32_t target() const { return 1; }
};
struct AdaptiveStore {
  OtaStoreFlashNrf52 internal;
  OtaStoreQspiNrf52 external;
  OtaStoreSdNrf52 sd;
  uint8_t mode = 0; // 0 internal, 1 hybrid, 2 QSPI, 3 SD
  bool usesExternal() const { ++store_calls; return mode >= 2; }
  bool usesQspi() const { ++store_calls; return mode == 2; }
  bool usesSd() const { ++store_calls; return mode == 3; }
  bool usesInternal() const { ++store_calls; return mode <= 1; }
  const char* selectionReason() const { return "internal"; }
  StoreBytes& active() { return mode <= 1 ? static_cast<StoreBytes&>(internal)
      : mode == 2 ? static_cast<StoreBytes&>(external) : static_cast<StoreBytes&>(sd); }
  const StoreBytes& active() const { return const_cast<AdaptiveStore*>(this)->active(); }
  uint32_t staged_size() const { return active().staged_size(); }
  bool read(uint32_t off, uint8_t* dest, uint32_t n) const { return active().read(off, dest, n); }
  OtaStoreFlashNrf52& internalStore() { ++store_calls; return internal; }
  OtaStoreQspiNrf52& externalStore() { ++store_calls; return external; }
  OtaStoreSdNrf52& sdStore() { ++store_calls; return sd; }
};
struct Context {
#if defined(OTA_RAK_AUTO_STORE)
  AdaptiveStore fetch_store;
#else
  OtaStoreFlashNrf52 fetch_store;
#endif
  OtaManager manager;
  SignerAllowlist allow;
  ApplyState apply_st;
  bool fetch_to_folder = false, apply_pending = false, bootloader_apply_pending = false;
  uint32_t apply_at = 0, apply_hard = 0;
  char hw_id[33] = "TESTHW";
  bool sdCacheFetching() const { return false; }
  const OtaBootloaderIdentity& bootloaderIdentity() const {
    static OtaBootloaderIdentity identity; ++metadata_calls; return identity;
  }
  @METHODS@
};
@IS_CMD@
char fstate_char(OtaManager::FetchState) { return 'C'; }
bool install(const char* a, char* reply, Context& c) {
  const char* rest = nullptr;
  @INSTALL@
  return true;
}
} }

static StoreBytes& selectMode(Context& c, uint8_t mode) {
#if defined(OTA_RAK_AUTO_STORE)
  c.fetch_store.mode = mode;
  c.fetch_store.internal.hybrid = mode == 1;
  return c.fetch_store.active();
#else
  assert(mode < 2);
  c.fetch_store.hybrid = mode == 1;
  return c.fetch_store;
#endif
}
static OtaStoreFlashNrf52* internalPointer(Context& c) {
#if defined(OTA_RAK_AUTO_STORE)
  return &c.fetch_store.internal;
#else
  return &c.fetch_store;
#endif
}
static uint8_t sourceFor(uint8_t mode) {
  return mode == 0 ? GPREGRET2_OTA_STAGE_EXPANDED
      : mode == 1 ? GPREGRET2_OTA_STAGE_HYBRID
      : mode == 2 ? GPREGRET2_OTA_STAGE_QSPI : GPREGRET2_OTA_STAGE_SD;
}
static void assertApplyState(const ApplyState& actual, const ApplyState& expected) {
  assert(actual.manifest_ok == expected.manifest_ok && actual.sig_ok == expected.sig_ok);
  assert(actual.trusted == expected.trusted && actual.slot_ok == expected.slot_ok);
  assert(actual.slot_addr == expected.slot_addr && actual.slot_size == expected.slot_size);
  assert(actual.image_size == expected.image_size);
  assert(memcmp(actual.image_hash, expected.image_hash, sizeof(actual.image_hash)) == 0);
}
static void invoke(Context& c, unsigned entry, char* reply) {
  uint8_t hash[8] = {1}, mid[4] = {1};
  bool result = false;
  switch (entry) {
    case 0: result = c.apply_fetched(reply); break;
    case 1: result = c.apply_fetched_trusted_auto(reply); break;
    case 2: result = c.apply_fetched_rescue(hash, reply); break;
    case 3: result = c.apply_fetched_impl(nullptr, false, reply); break;
    case 4: result = c.apply_fetched_bootloader(mid, hash, reply); break;
    default:
      assert(install(entry == 5 ? "install" : entry == 6 ? "apply" : "applydelta", reply, c));
      assert(strncmp(reply, "ERR", 3) == 0);
  }
  assert(!result);
}

int main() {
#if defined(OTA_RAK15002_SD_STORE)
  const unsigned modes = 4;
#elif defined(OTA_RAK_AUTO_STORE)
  const unsigned modes = 3;
#else
  const unsigned modes = 2;
#endif
  unsigned pending_cases = 0, healthy_cases = 0;
  for (uint8_t mode = 0; mode < modes; ++mode) {
    for (unsigned pending : {1U, 2U, 3U}) for (bool folder : {false, true})
    for (unsigned entry = 0; entry < 8; ++entry) {
      Context c;
      StoreBytes& store = selectMode(c, mode);
      store.fail_on_read = 3; // would reach/reset the applier handoff if admitted
      c.apply_pending = (pending & 1) != 0;
      c.bootloader_apply_pending = (pending & 2) != 0;
      c.fetch_to_folder = folder;
      c.apply_at = 0x12345678U; c.apply_hard = 0x22334455U;
      c.apply_st.manifest_ok = c.apply_st.sig_ok = c.apply_st.trusted = c.apply_st.slot_ok = true;
      c.apply_st.slot_addr = 0x26000; c.apply_st.slot_size = 0xB8000;
      c.apply_st.image_size = 12345;
      memset(c.apply_st.image_hash, 0xA5, sizeof(c.apply_st.image_hash));
      const ApplyState before = c.apply_st;
      // Seed the state left by an already-approved handoff. Initial signature
      // verification is deliberately not claimed by this host admission test.
      g_nrf52_apply_store = mode < 2 ? internalPointer(c) : nullptr;
      g_nrf52_external_handoff = sourceFor(mode);
      OtaStoreFlashNrf52* pointer = g_nrf52_apply_store;
      const uint8_t external = g_nrf52_external_handoff;
      store_calls = metadata_calls = apply_calls = 0;
      char reply[160] = {};
      invoke(c, entry, reply);
#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
      if (entry == 2) assert(strstr(reply, "rescue is disabled") != nullptr);
      else
#endif
      assert(strstr(reply, "already armed") != nullptr);
      assert(store_calls == 0 && metadata_calls == 0 && apply_calls == 0);
      assert(store.reads == 0);
      assert(c.apply_pending == ((pending & 1) != 0));
      assert(c.bootloader_apply_pending == ((pending & 2) != 0));
      assert(c.fetch_to_folder == folder && c.apply_at == 0x12345678U && c.apply_hard == 0x22334455U);
      assertApplyState(c.apply_st, before);
      assert(g_nrf52_apply_store == pointer && g_nrf52_external_handoff == external);
      // Execute the appropriate production reboot function with register and
      // SoftDevice adapters; no reset or peripheral access reaches hardware.
      for (bool enabled : {false, true}) {
        softdevice_enabled = enabled;
        const unsigned previous_resets = resets;
        if (c.bootloader_apply_pending) {
          ota_reboot_to_bootloader_update();
          assert(emitted_request == GPREGRET_OTA_BOOTLOADER_UPDATE);
#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
          assert(emitted_source == GPREGRET2_OTA_STAGE_EXPANDED);
#else
          assert(emitted_source == GPREGRET2_OTA_STAGE_QSPI);
#endif
        } else {
          ota_reboot_to_apply();
          assert(emitted_request == GPREGRET_OTA_APPLY && emitted_source == sourceFor(mode));
        }
        assert(resets == previous_resets + 1);
      }
      ++pending_cases;
    }

    // A failed first application is not a latched armed state. Preserve normal
    // preflight rejection and let an explicit retry reach the apply boundary.
    for (unsigned fault : {0U, 1U, 2U, 3U, 4U}) {
      Context c;
      StoreBytes& store = selectMode(c, mode);
      g_nrf52_apply_store = nullptr;
      store_calls = metadata_calls = apply_calls = 0;
      apply_ok = fault != 3;
      char reply[160] = {};
      if (fault == 0) strcpy(c.hw_id, "WRONG");
      if (fault == 1) store.fail_on_read = 1;
      if (fault == 2) c.fetch_to_folder = true;
      if (fault == 4) assert(!c.apply_fetched_trusted_auto(reply)); // unsigned auto refusal
      else assert(!c.apply_fetched(reply));
      assert(!c.apply_pending && !c.bootloader_apply_pending);
      assert(apply_calls == (fault == 3 ? 1U : 0U));
      strcpy(c.hw_id, "TESTHW"); store.fail_on_read = 0;
      c.fetch_to_folder = false; apply_ok = true;
      assert(c.apply_fetched(reply));
      assert(c.apply_pending && !c.bootloader_apply_pending);
      assert(c.apply_st.manifest_ok && c.apply_st.slot_ok);
      ++healthy_cases;
    }
    {
      Context c;
      StoreBytes& store = selectMode(c, mode);
      store.bytes[9] |= MFLAG_SIGNED;
      char reply[160] = {};
      // Actual auto preflight requires signed/newer; downstream cryptographic
      // acceptance remains the explicit boundary stub, not a signature test.
      assert(c.apply_fetched_trusted_auto(reply));
      assert(c.apply_pending && !c.bootloader_apply_pending);
      ++healthy_cases;
    }
  }
#if !defined(OTA_INTERNAL_BOOTLOADER_UPDATE) && !defined(OTA_SD_STORE)
  {
    Context c; selectMode(c, 0);
    uint8_t hash[8] = {1}; char reply[160] = {};
    apply_ok = false;
    assert(!c.apply_fetched_rescue(hash, reply));
    assert(!c.apply_pending);
    apply_ok = true;
    assert(c.apply_fetched_rescue(hash, reply));
    assert(c.apply_pending && !c.bootloader_apply_pending);
    ++healthy_cases;
  }
#endif
#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
  // Privileged first-apply admission remains usable; failed verification does
  // not arm either flag, and retry may arm bootloader-specific deferred reboot.
  {
    Context c; selectMode(c, 0);
    uint8_t mid[4] = {1}, hash[8] = {1}; char reply[160] = {};
    apply_ok = false;
    assert(!c.apply_fetched_bootloader(mid, hash, reply));
    assert(!c.apply_pending && !c.bootloader_apply_pending);
    apply_ok = true;
    assert(c.apply_fetched_bootloader(mid, hash, reply));
    assert(c.apply_pending && c.bootloader_apply_pending);
    ++healthy_cases;
  }
#endif
  printf("PASS: %u armed admission cases; %u first-apply/retry controls\n", pending_cases, healthy_cases);
}
