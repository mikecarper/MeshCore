extern "C" void meshRegulatorCleanReset() __attribute__((weak));

#include <helpers/ota/MotaContainer.h>
#include <helpers/ota/OtaByteIO.h>
#include <helpers/ota/FirmwareInfo.h>
#include <helpers/ota/OtaFlashLayout_nrf52.h>
#include <helpers/ota/OtaRawBootStage.h>
#include <helpers/ota/OtaApply.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "test_ota/mota_vectors.h"
#if defined(ENABLE_OTA) && !defined(MESHCORE_RAW_BOOT_STAGE_ENABLED)
#error "Qualified infrastructure fixture must include default raw staging"
#endif
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
#if defined(MESHCORE_RAW_BOOT_STAGE_ENABLED)
static uint8_t g_nrf52_raw_boot_source = 0;
#endif
static unsigned resets = 0;
static uint8_t emitted_request = 0, emitted_source = 0;
static bool softdevice_enabled = false;
static unsigned power_calls = 0, fail_power_call = 0;
static constexpr uint32_t NRF_SUCCESS = 0;
struct PowerRegisters { uint32_t GPREGRET = 0, GPREGRET2 = 0; } power_registers;
#define NRF_POWER (&power_registers)
namespace mesh_nrf52 {
uint32_t softdeviceIsEnabled(uint8_t& enabled) { enabled = softdevice_enabled; return NRF_SUCCESS; }
}
uint32_t sd_power_gpregret_clr(uint32_t index, uint32_t bits) {
  if (++power_calls == fail_power_call) return 1;
  (index ? power_registers.GPREGRET2 : power_registers.GPREGRET) &= ~bits;
  return NRF_SUCCESS;
}
uint32_t sd_power_gpregret_set(uint32_t index, uint32_t bits) {
  if (++power_calls == fail_power_call) return 1;
  (index ? power_registers.GPREGRET2 : power_registers.GPREGRET) |= bits;
  return NRF_SUCCESS;
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

#if defined(MESHCORE_RAW_BOOT_STAGE_ENABLED)
void test_raw_boot_stage(OtaRawBootLayout layout) {
  const bool external = layout == OtaRawBootLayout::ExternalScratch;
  const uint8_t abi = external ? 2u : 1u;
  const uint8_t record_size = external ? 44u : 40u;
  const uint8_t source = external ? 0xE0u : 0xE2u;
  const uint8_t capability[] = {'M','O','T','A','R','A','W', uint8_t('0' + abi),
                               abi,0, record_size,0, source,0,0,0};
  const uint32_t base = MOTA_NRF52_APP_BASE_S140_V6;
  const uint32_t start = ota_raw_boot_start(layout);
  assert(start == (external ? 0xE0000u : 0xE2000u));
  assert(ota_raw_boot_layout_fits(layout, base, start - base, MOTA_NRF52_APP_END));
  assert(!ota_raw_boot_layout_fits(layout, base, start - base + 1u, MOTA_NRF52_APP_END));
  assert(!ota_raw_boot_layout_fits(layout, base, 0, MOTA_NRF52_APP_END));
  assert(!ota_raw_boot_layout_fits(layout, base + 1u, 4096u, MOTA_NRF52_APP_END));
  assert(!ota_raw_boot_layout_fits(layout, base, 4096u, MOTA_NRF52_EXTRAFS_START));

  std::vector<uint8_t> installed(RAW_BOOT_IMAGE_SIZE, 0xFF);
  const auto supported = [&]() {
    return ota_raw_boot_stage_supported(installed.data(), installed.size(), layout);
  };
  assert(!supported());
  memcpy(installed.data() + 1, capability, sizeof(capability));
  assert(!supported());
  memcpy(installed.data() + 64, capability, sizeof(capability));
  assert(supported());
  assert(!ota_raw_boot_stage_supported(installed.data(), installed.size() - 1u, layout));
  assert(!ota_raw_boot_stage_supported(installed.data(), installed.size(), external
      ? OtaRawBootLayout::SharedInternal : OtaRawBootLayout::ExternalScratch));
  installed[64 + 8] = 3;
  assert(!supported());
  memcpy(installed.data() + 64, capability, sizeof(capability));
  installed[64 + 15] = 1;
  assert(!supported());
  memcpy(installed.data() + 64, capability, sizeof(capability));
  memcpy(installed.data() + 128, capability, sizeof(capability));
  assert(!supported());

  constexpr uint32_t pg = MOTA_NRF52_FLASH_PAGE;
  constexpr unsigned pages = RAW_BOOT_IMAGE_SIZE / pg;
  std::vector<uint8_t> image(RAW_BOOT_IMAGE_SIZE);
  for (size_t i = 0; i < image.size(); ++i) image[i] = uint8_t(17u * i + 11u * (i / pg));
  uint8_t hash[32];
  memset(hash, 0xA5, sizeof(hash));
  // The internal source overlaps the destination; the external source must
  // stay unchanged. Neither may arm a handoff after an I/O or readback failure.
  for (unsigned scenario = 0; scenario <= pages + 4u; ++scenario) {
    std::vector<uint8_t> slot((pages + 1u) * pg, 0xFF);
    std::vector<uint8_t> external_package(external ? MOTA_NRF52_BOOT_CONTAINER_SIZE : 0u, 0xFF);
    uint8_t* container = external ? external_package.data() : slot.data();
    memcpy(container + RAW_BOOT_PAYLOAD_OFFSET, image.data(), image.size());
    memcpy(container + 32u, hash, sizeof(hash)); // aliases flash erased by internal page 0
    const auto original_package = external_package;
    uint8_t page[pg];
    unsigned writes = 0;
    bool verified = false;
    const auto read_payload = [&](uint32_t off, uint8_t* bytes, uint32_t size) {
      assert(size == pg && off == RAW_BOOT_PAYLOAD_OFFSET + writes * pg && writes < pages);
      if (scenario == pages + 4u && writes == pages - 1u) return false;
      memcpy(bytes, container + off, size);
      return true;
    };
    const auto write_page = [&](uint32_t idx, const uint8_t* bytes) {
      assert(idx == writes++ && idx <= pages);
      if (idx == pages) assert(verified);
      memset(slot.data() + idx * pg, 0xFF, pg);
      if (scenario == idx + 1u) return false;
      memcpy(slot.data() + idx * pg, bytes, pg);
      if (scenario == pages + 2u && idx == pages - 1u) slot[idx * pg] ^= 1u;
      return true;
    };
    const auto verify = [&](const uint8_t* expected) {
      assert(writes == pages);
      assert(memcmp(expected, hash, sizeof(hash)) == 0);
      verified = memcmp(slot.data(), image.data(), image.size()) == 0;
      return verified;
    };
    const bool ok = ota_stage_raw_bootloader(
        layout, MOTA_NRF52_BOOT_CONTAINER_SIZE,
        RAW_BOOT_PAYLOAD_OFFSET + (scenario == pages + 3u), 0x12345678u,
        0x28860045u, container + 32u, page, read_payload, write_page, verify);
    const uint8_t* record = slot.data() + RAW_BOOT_IMAGE_SIZE;
    assert(external_package == original_package);
    assert(ok == (scenario == 0));
    if (ok) {
      assert(writes == pages + 1u);
      assert(memcmp(record, external ? "RAW2\x78\x56\x34\x12" : "RAW1\x78\x56\x34\x12", 8u) == 0);
      assert(memcmp(record + 8u, hash, sizeof(hash)) == 0);
      if (external) assert(rd_u32le(record + 40u) == 0x28860045u);
      for (unsigned i = record_size; i < pg; ++i) assert(record[i] == 0xFFu);
    } else {
      assert(rd_u32le(record) != (external ? RAW_BOOT_EXTERNAL_RECORD_MAGIC : RAW_BOOT_RECORD_MAGIC));
      if (scenario == pages + 2u) assert(writes == pages);
      if (scenario == pages + 3u) assert(writes == 0);
      if (scenario == pages + 4u) assert(writes == pages - 1u);
    }
  }
}

void test_raw_boot_handoff() {
  for (auto source : {0xE2u, 0xE0u}) for (bool sd : {false, true}) {
    softdevice_enabled = sd;
    g_nrf52_raw_boot_source = source;
    ota_reboot_to_bootloader_update();
    assert(emitted_request == GPREGRET_OTA_BOOTLOADER_UPDATE);
    assert(emitted_source == source);
    assert(!g_nrf52_raw_boot_source);
    ota_reboot_to_bootloader_update();
    assert(emitted_source == GPREGRET2_OTA_STAGE_EXPANDED);
  }
  for (unsigned call = 1; call <= 4; ++call) {
    power_registers = {};
    power_calls = 0;
    fail_power_call = call;
    g_nrf52_raw_boot_source = 0xE0u;
    ota_reboot_to_bootloader_update();
    assert(emitted_request == 0 && !g_nrf52_raw_boot_source);
  }
  softdevice_enabled = false;
  fail_power_call = 0;
  power_registers = {};
  resets = 0;
}
#endif

int main() {
#if defined(MESHCORE_RAW_BOOT_STAGE_ENABLED)
  test_raw_boot_stage(OtaRawBootLayout::SharedInternal);
  test_raw_boot_stage(OtaRawBootLayout::ExternalScratch);
  test_raw_boot_handoff();
#endif
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
