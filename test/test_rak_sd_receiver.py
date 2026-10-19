"""Native regression for the exact RAK15002 receiver capability and selector."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def method(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def run_cpp(source, defines=()):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        cpp = path / "test.cpp"
        cpp.write_text(source, encoding="utf-8")
        exe = path / "test"
        subprocess.run([
            "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
            *("-D" + value for value in defines), "-I", str(ROOT),
            str(cpp), "-o", str(exe),
        ], check=True, timeout=30)
        subprocess.run([str(exe)], check=True, timeout=10)


class RakSdReceiverTest(unittest.TestCase):
    def test_actual_apply_requires_exact_sd_loader_and_selects_runtime_handoff(self):
        apply = (ROOT / "src/helpers/ota/OtaApply.cpp").read_text()
        functions = "\n".join(method(apply, signature) for signature in (
            "bool ota_apply_mota_nrf52(OtaStoreSdNrf52& store",
            "bool ota_apply_mota_nrf52(OtaStoreQspiNrf52& store",
            "void ota_reboot_to_apply() {                   // public:",
        ))
        run_cpp(r'''
extern "C" void meshRegulatorCleanReset() __attribute__((weak));
#include <cassert>
#include <cstring>
#include "src/helpers/ota/OtaBlInfo.h"
#include "src/helpers/ota/OtaFlashLayout_nrf52.h"
using namespace mesh::ota;
namespace mesh { namespace ota {
static bool identity_ok = true, verified = true, clear_ok = true;
static const char* identity_name = "4631_DFU";
static unsigned verification_calls = 0, resets = 0;
static uint8_t request = 0, source = 0;
static OtaBlCaps caps;
struct OtaBootloaderIdentity { bool crc_ok = true; const char* device_name; };
bool ota_installed_bootloader_identity(OtaBootloaderIdentity& out) {
  out.device_name = identity_name; return identity_ok;
}
OtaBlCaps appCaps() { return caps; }
#define ota_bootloader_app_caps appCaps
struct OtaStoreFlashNrf52 {
  bool hybrid = false;
  bool is_hybrid() const { return hybrid; }
  bool publish_hybrid_handoff() { return true; }
};
static OtaStoreFlashNrf52* g_nrf52_apply_store = nullptr;
static uint8_t g_nrf52_external_handoff = GPREGRET2_OTA_STAGE_QSPI;
struct OtaStoreSdNrf52 {};
struct OtaStoreQspiNrf52 {
  static bool headerW25Detected() { return false; }
  uint32_t jedec_id() const { return 0xef4015; }
};
struct SignerAllowlist {};
struct ApplyState {};
template<class Store> bool ota_apply_mota_nrf52_external(
    Store&, const SignerAllowlist&, uint8_t flag, const char*, bool trusted,
    ApplyState&, char*) {
  ++verification_calls;
  assert(trusted == (flag == OTA_BL_STORAGE_SD));
  return verified;
}
void ota_nrf52_set_reset_handoff(uint8_t req, uint8_t src) { request = req; source = src; }
bool ota_nrf52_clear_reset_reasons() { return clear_ok; }
void NVIC_SystemReset() { ++resets; }
uint32_t simulatedCeiling() { return MOTA_NRF52_STAGE_CEILING_EXPANDED; }
#define ota_nrf52_effective_stage_ceiling simulatedCeiling
''' + functions + r'''
} }
int main() {
  OtaStoreSdNrf52 sd; OtaStoreQspiNrf52 nor; OtaStoreFlashNrf52 internal;
  SignerAllowlist allow; ApplyState state; char msg[160];
  caps.optional_sd_app_storage = 0;
  g_nrf52_apply_store = &internal;
  assert(!ota_apply_mota_nrf52(sd, allow, state, msg));
  assert(!g_nrf52_apply_store && verification_calls == 0);
  caps.optional_sd_app_storage = OTA_BL_STORAGE_SD; identity_ok = false;
  assert(!ota_apply_mota_nrf52(sd, allow, state, msg) && verification_calls == 0);
  identity_ok = true; identity_name = "3401_DFU";
  assert(!ota_apply_mota_nrf52(sd, allow, state, msg) && verification_calls == 0);
  identity_name = "4631_DFU"; verified = false;
  assert(!ota_apply_mota_nrf52(sd, allow, state, msg));
  assert(verification_calls == 1 && g_nrf52_external_handoff == GPREGRET2_OTA_STAGE_QSPI);
  verified = true;
  assert(ota_apply_mota_nrf52(sd, allow, state, msg));
  ota_reboot_to_apply();
  assert(request == GPREGRET_OTA_APPLY && source == GPREGRET2_OTA_STAGE_SD && resets == 1);
  assert(ota_apply_mota_nrf52(nor, allow, state, msg));
  ota_reboot_to_apply();
  assert(request == GPREGRET_OTA_APPLY && source == GPREGRET2_OTA_STAGE_QSPI && resets == 2);
  g_nrf52_apply_store = &internal;
  ota_reboot_to_apply();
  assert(source == GPREGRET2_OTA_STAGE_EXPANDED && resets == 3);
  internal.hybrid = true;
  ota_reboot_to_apply();
  assert(source == GPREGRET2_OTA_STAGE_HYBRID && resets == 4);
  clear_ok = false;
  ota_reboot_to_apply();
  assert(request == 0 && source == 0xbd && resets == 5);
}
''', ("OTA_RAK15002_SD_STORE", "OTA_RAK_AUTO_STORE", "OTA_SD_STORE", "OTA_QSPI_STORE"))

    def test_actual_pull_gate_distinguishes_sd_nor_and_internal_boot_packages(self):
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        error = cli.index("ERR bootloader cannot apply from detected QSPI storage")
        start = cli.rfind("#elif defined(OTA_RAK_AUTO_STORE)", 0, error)
        end = cli.index("#elif defined(OTA_QSPI_STORE)", error)
        gate = cli[cli.index("\n", start) + 1:end]
        run_cpp(r'''
extern "C" void meshRegulatorCleanReset() __attribute__((weak));
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
constexpr uint8_t OTA_BL_STORAGE_QSPI = 4, OTA_BL_STORAGE_SD = 1, MFLAG_SIGNED = 2;
struct Store {
  int mode;
  bool usesExternal() const { return mode == 1 || mode == 2; }
  bool usesQspi() const { return mode == 1; }
  bool usesSd() const { return mode == 2; }
  bool usesInternal() const { return mode == 0; }
  const char* selectionReason() const { return "not prepared"; }
};
bool rejects(bool selboot, int medium, uint8_t flags, uint8_t sd, uint8_t selflags = 2) {
  struct { Store fetch_store; } c = {{medium}};
  struct { uint8_t storage_flags, optional_sd_app_storage; } bl = {flags, sd};
  char reply[160];
''' + gate + r'''
  return false;
}
int main() {
  assert(!rejects(false, 2, 0x1f, 1));
  assert(rejects(false, 2, 0x1e, 0));
  assert(rejects(false, 2, 0x1f, 1, 0));
  assert(!rejects(false, 1, 0x1e, 0));
  assert(rejects(false, 1, 0x0a, 0));
  assert(!rejects(false, 0, 0x0a, 0));
  assert(rejects(false, 3, 0x1f, 1));
  for (int medium = 0; medium < 4; ++medium)
    assert(!rejects(true, medium, 0x0a, 0, 0)); // privileged path is internal, never SD
}
''', ("OTA_RAK15002_SD_STORE",))

    def test_layout_and_status_dispatch_do_not_prepare_optional_storage(self):
        layout = (ROOT / "src/helpers/StorageLayout.cpp").read_text()
        start = layout.index("#if defined(ENABLE_OTA) && defined(OTA_RAK15002_SD_STORE)")
        end = layout.index("#elif defined(ENABLE_OTA) && defined(OTA_RAK_AUTO_STORE)", start)
        body = layout[layout.index("\n", start) + 1:end]
        self.assertNotIn("getSpace(", body)
        self.assertNotIn("ota_acquire_context", body)
        self.assertNotIn("ota_ctx()", body)
        run_cpp(r'''
extern "C" void meshRegulatorCleanReset() __attribute__((weak));
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstring>
namespace mesh { namespace ota {
struct Store {
  int medium = -1;
  const char* selectionReason() const { return medium < 0 ? "not probed" : "selected"; }
  bool usesSd() const { return medium == 2; }
  bool usesQspi() const { return medium == 1; }
  bool usesInternal() const { return medium == 0; }
  unsigned staged_size() const { return 0; }
};
struct Context { Store fetch_store; };
static Context* active = nullptr;
const Context* ota_context_if_active() { return active; }
} }
void appendStorageLayout(char* reply, size_t reply_size, const char* format, ...) {
  va_list args; va_start(args, format);
  vsnprintf(reply + strlen(reply), reply_size - strlen(reply), format, args);
  va_end(args);
}
void status(char* reply) {
  int board = 0; size_t reply_size = 160;
  reply[0] = 0;
''' + body + r'''
}
int main() {
  char reply[160];
  status(reply); assert(strstr(reply, "not-prepared"));
  mesh::ota::Context context; mesh::ota::active = &context;
  status(reply);
#if defined(OTA_SEEDER_ONLY)
  assert(strstr(reply, "source-only"));
#else
  assert(strstr(reply, "not-prepared"));
  for (int medium = 0; medium < 3; ++medium) {
    context.fetch_store.medium = medium;
    status(reply);
    assert(strstr(reply, medium == 0 ? "medium=internal" : medium == 1 ? "medium=qspi" : "medium=sd"));
  }
#endif
}
''')
        # Full Companion uses the separate stable SD archive, not an adaptive
        # receiver. Compile the same production status block in both roles.
        run_cpp(r'''
extern "C" void meshRegulatorCleanReset() __attribute__((weak));
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstring>
namespace mesh { namespace ota {
struct Context {};
const Context* ota_context_if_active() { static Context c; return &c; }
} }
void appendStorageLayout(char* reply, size_t reply_size, const char* format, ...) {
  va_list args; va_start(args, format);
  vsnprintf(reply + strlen(reply), reply_size - strlen(reply), format, args);
  va_end(args);
}
int main() {
  int board = 0; size_t reply_size = 160; char reply[160] = {};
''' + body + r'''
  assert(strstr(reply, "source-only"));
}
''', ("OTA_SEEDER_ONLY",))
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        dispatcher = method(cli, "bool handle_ota_command(")
        self.assertLess(dispatcher.index("return handle_status(reply, c)"),
                        dispatcher.index("c.prepareRakStorage()"))
        self.assertIn('"qspi|storage|self|id|stats|help|?|h"', dispatcher)

    def test_existing_local_combined_loader_advertises_the_exact_new_capability(self):
        candidate = (ROOT.parent / "Adafruit_nRF52_Bootloader_OTAFIX" /
                     "_build-rak4631-all-in-one-test-20261015/cmake/bootloader.bin")
        if not candidate.is_file():
            self.skipTest("Optional previously-built OTAFIX qualification candidate is absent")
        # Read-only admission of an existing ARM binary, not a bootloader build
        # or firmware upload. CI need not have the separate loader repository.
        image = candidate.read_bytes()
        self.assertEqual(len(image), 0xA000)
        source = r'''
#include <cassert>
#include "src/helpers/ota/OtaBlInfo.h"
using namespace mesh::ota;
static const uint8_t image[] = { @IMAGE@ };
int main() {
  const auto app = ota_bl_app_caps_scan(image, sizeof(image));
  const auto boot = ota_bl_update_caps_scan_aligned(image, sizeof(image), 0x0a);
  assert(app.present && app.storage_flags == 0x1f && app.optional_app_storage == 0x14);
  assert(app.optional_sd_app_storage == 1);
  assert(boot.present && boot.storage_flags == 0x0a && boot.optional_sd_app_storage == 1);
}
'''.replace("@IMAGE@", ",".join(str(value) for value in image))
        run_cpp(source)

    def test_memory_mapped_capabilities_are_scoped_to_rak4631(self):
        source = r'''
#include <cassert>
#include <cstring>
#include <sys/mman.h>
#include "src/helpers/ota/OtaBlInfo.h"
using namespace mesh::ota;
int main() {
  auto* flash = static_cast<uint8_t*>(mmap(reinterpret_cast<void*>(uintptr_t(0xf4000)),
      0xa000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0));
  assert(flash == reinterpret_cast<uint8_t*>(uintptr_t(0xf4000)));
  memset(flash, 0, 0xa000);
  const uint8_t records[] = {
    'M','O','T','A','B','L','D','R',3,0,5,0,0x0a,0,0,0,
    'M','O','T','A','R','A','M','A',1,0,72,0,0,0,1,0,
    'M','O','T','A','S','T','O','R',1,0,16,0,0x14,0,0,0,
    'M','O','T','A','S','D','R','A',1,0,72,0,1,0x53,0,0,
  };
  memcpy(flash, records, sizeof(records));
  auto app = ota_bootloader_app_caps();
  auto boot = ota_bootloader_update_caps();
#if defined(RAK_4631) || defined(OTA_RAK15002_SD_STORE)
  assert(app.storage_flags == 0x1f && app.optional_sd_app_storage == 1);
  assert(boot.storage_flags == 0x0a && boot.optional_sd_app_storage == 1);
#else
  assert(app.storage_flags == 0x1e && app.optional_sd_app_storage == 0);
  assert(boot.storage_flags == 0x0a && boot.optional_sd_app_storage == 0);
#endif
  assert(ota_bootloader_self_update_caps_valid(boot));
  assert(munmap(flash, 0xa000) == 0);
}
'''
        for scope in ((), ("RAK_4631",), ("OTA_RAK15002_SD_STORE",)):
            with self.subTest(scope=scope):
                run_cpp(source, ("NRF52_PLATFORM", "OTA_RAK_AUTO_STORE",
                                 "OTA_INTERNAL_BOOTLOADER_UPDATE", *scope))

    def test_exact_supplement_preserves_legacy_application_and_update_views(self):
        run_cpp(r'''
extern "C" void meshRegulatorCleanReset() __attribute__((weak));
#include <cassert>
#include <cstring>
#include <initializer_list>
#include "src/helpers/ota/OtaBlInfo.h"
using namespace mesh::ota;
static const uint8_t boot[16] = {
  'M','O','T','A','B','L','D','R',3,0,5,0,0x0a,0,0,0,
};
static const uint8_t bundle[32] = {
  'M','O','T','A','R','A','M','A',1,0,72,0,0,0,1,0,
  'M','O','T','A','S','T','O','R',1,0,16,0,0x14,0,0,0,
};
static const uint8_t sd[16] = {
  'M','O','T','A','S','D','R','A',1,0,72,0,1,0x53,0,0,
};
static void make(uint8_t* image) {
  memset(image, 0, 256);
  memcpy(image, boot, sizeof(boot));
  memcpy(image + 32, bundle, sizeof(bundle));
  memcpy(image + 80, sd, sizeof(sd));
}
static void check(const uint8_t* image, uint8_t expected) {
  auto app = ota_bl_app_caps_scan(image, 256);
  auto update = ota_bl_update_caps_scan_aligned(image, 256, 0x0a);
  assert(app.present && app.storage_flags == (0x1e | expected));
  assert(app.optional_app_storage == 0x14 && app.optional_sd_app_storage == expected);
  assert(update.present && update.storage_flags == 0x0a);
  assert(update.optional_app_storage == 0x14 && update.optional_sd_app_storage == expected);
}
int main() {
  uint8_t image[256];
  make(image); check(image, 1);
  // Old loaders retain exactly their old internal/NOR contract.
  memset(image + 80, 0, sizeof(sd)); check(image, 0);
  for (unsigned i = 0; i < sizeof(sd); ++i) {
    make(image); image[80 + i] ^= 1; check(image, 0);
  }
  make(image); memcpy(image + 112, sd, sizeof(sd)); check(image, 0);
  make(image); memcpy(image + 112, sd, sizeof(sd)); image[125] ^= 1; check(image, 0);
  make(image); memcpy(image + 248, sd, 8); check(image, 0); // truncated duplicate
  make(image); memmove(image + 81, image + 80, sizeof(sd)); image[80] = 0; check(image, 0);
  make(image);
  assert(ota_bl_rak_sd_app_storage(image, 95, 0x0a, 0x14) == 0);
  assert(ota_bl_rak_sd_app_storage(nullptr, 256, 0x0a, 0x14) == 0);
  for (uint8_t primary : {uint8_t(9), uint8_t(0x0e), uint8_t(0x1e)}) {
    make(image); image[12] = primary;
    auto app = ota_bl_app_caps_scan(image, sizeof(image));
    assert(app.optional_sd_app_storage == 0 && app.storage_flags == primary);
  }
  // Missing/corrupt retained-RAM/NOR metadata cannot borrow the SD marker.
  for (unsigned i = 0; i < sizeof(bundle); ++i) {
    make(image); image[32 + i] ^= 1;
    assert(ota_bl_app_caps_scan(image, sizeof(image)).optional_sd_app_storage == 0);
  }
  make(image); memcpy(image + 128, boot, sizeof(boot));
  assert(!ota_bl_update_caps_scan_aligned(image, sizeof(image), 0x0a).present);
}
''')

    def test_actual_adaptive_store_defers_io_and_preserves_each_owner(self):
        header = (ROOT / "src/helpers/ota/OtaStoreAdaptiveNrf52.h").read_text()
        body = header[header.index("class OtaStoreAdaptiveNrf52"):
                      header.index("} // namespace ota")]
        source = r'''
#include <cassert>
#include <cstring>
#include <new>
#include "src/helpers/ota/OtaStore.h"
#include "src/helpers/ota/OtaBlInfo.h"
#include "src/helpers/ota/OtaRakStoragePolicy.h"
#include "src/helpers/nrf52/Rak4631SharedSpi.h"
using namespace mesh::ota;
namespace mesh { namespace ota {
static unsigned probes = 0, card_checks = 0, sd_probes = 0;
static unsigned internal_live = 0, nor_live = 0;
static bool card = true, media = true, sd_cap = true, valid_identity = true;
static bool release_ok = true;
static uint8_t detected = 2;
static const char* device_name = "4631_DFU";
struct OtaBootloaderIdentity { bool crc_ok = true; const char* device_name; };
bool ota_installed_bootloader_identity(OtaBootloaderIdentity& out) {
  out.device_name = device_name; return valid_identity;
}
struct MotaManifest { bool is_bootloader() const { return false; } };
bool mota_parse_manifest(const uint8_t*, size_t, MotaManifest&) { return false; }
OtaBlCaps appCaps() {
  OtaBlCaps c; c.present = true; c.apply_abi = 3; c.codec_mask = 5;
  c.storage_flags = sd_cap ? 0x1f : 0x1e; c.optional_app_storage = 0x14;
  c.optional_sd_app_storage = sd_cap ? 1 : 0; return c;
}
OtaBlCaps bootCaps() { auto c = appCaps(); c.storage_flags = 0x0a; return c; }
bool ota_rak15002_sd_card_present() {
  assert(!rak4631_ethernet_owns_spi()); ++card_checks; return card;
}
class Store : public OtaStore {
public:
  uint32_t size = 0;
  bool begin(uint32_t n) override { size = n; return true; }
  bool write(uint32_t, const uint8_t*, uint32_t) override { return true; }
  bool read(uint32_t, uint8_t*, uint32_t) const override { return true; }
  uint32_t capacity() const override { return 65536; }
  uint32_t staged_size() const override { return size; }
  void clear() override { size = 0; }
};
class OtaStoreFlashNrf52 : public Store {
public:
  OtaStoreFlashNrf52() { ++internal_live; assert(nor_live == 0); }
  ~OtaStoreFlashNrf52() { --internal_live; }
  bool plan_layout(bool full, uint32_t, uint32_t, uint32_t, bool bootloader) override {
    return !full || bootloader;
  }
};
class OtaStoreQspiNrf52 : public Store {
public:
  OtaStoreQspiNrf52() { ++nor_live; assert(internal_live == 0); }
  ~OtaStoreQspiNrf52() { --nor_live; }
  static uint8_t autoDetect() { assert(!card && !rak4631_ethernet_owns_spi()); ++probes; return detected; }
  static bool headerW25Detected() { return false; }
  uint32_t jedec_id() const { return 0xef4015; }
  uint8_t status1() const { return 0; }
  const char* last_stage() const { return "idle"; }
  const char* last_error() const { return ""; }
};
class OtaStoreSdNrf52 : public Store {
public:
  bool probeMedia() { assert(card && !rak4631_ethernet_owns_spi()); ++sd_probes; return media; }
  bool releaseBus() { return release_ok && size == 0; }
  const char* last_error() const { return "SD test"; }
};
#define ota_bootloader_app_caps appCaps
#define ota_bootloader_update_caps bootCaps
''' + body + r'''
} }
int main() {
  {
    OtaStoreAdaptiveNrf52 store;
    auto* stable_sd = &store.sdStore();
    assert(!store.usesSd() && !store.usesQspi() && !store.usesInternal());
    assert(!store.usesExternal() && store.capacity() == 0 && store.staged_size() == 0);
    assert(card_checks == 0 && sd_probes == 0 && probes == 0);
    assert(store.selectStorage() && store.usesSd() && store.usesExternal());
    assert(sd_probes == 1 && probes == 0);
    assert(store.plan_layout(true, 100000, 365, 100000, false));
    assert(store.begin(100370));
    assert(!store.resetSelection());
    // A malformed caller cannot grant a previously selected external store
    // access after Ethernet took ownership, with or without the old combined
    // recipe flag. Its staged data must remain untouched.
    rak4631_set_ethernet_spi_owner(true);
    uint8_t byte = 0;
    assert(!store.begin(200) && !store.write(0, &byte, 1) && !store.read(0, &byte, 1));
    assert(store.capacity() == 0);
    store.clear();
    assert(stable_sd->staged_size() == 100370);
    rak4631_set_ethernet_spi_owner(false);
    assert(!store.plan_layout(true, 40960, 365, 40960, true)); // never abandon SD data
    assert(store.usesSd() && stable_sd->staged_size() == 100370);
    store.clear();
    assert(store.plan_layout(true, 40960, 365, 40960, true));
    assert(store.usesInternal() && &store.sdStore() == stable_sd);
    assert(store.begin(41330) && !store.resetSelection());
    store.clear();
    assert(store.plan_layout(true, 100000, 365, 100000, false) && store.usesSd());
    release_ok = false;
    assert(!store.resetSelection() && store.usesSd());
    release_ok = true;
    assert(store.resetSelection() && !store.usesSd());
    assert(&store.sdStore() == stable_sd);
  }
  assert(internal_live == 0 && nor_live == 0);
  probes = sd_probes = card_checks = 0; card = false;
  {
    OtaStoreAdaptiveNrf52 store;
    assert(store.selectStorage() && store.usesQspi() && !store.usesSd());
    assert(probes == 1 && sd_probes == 0);
    assert(store.begin(100));
    assert(!store.resetSelection());
    store.clear(); release_ok = false;
    assert(!store.resetSelection() && store.usesQspi() && nor_live == 1);
    release_ok = true; assert(store.resetSelection() && nor_live == 0);
  }
  card = true; sd_cap = false; probes = sd_probes = 0;
  {
    OtaStoreAdaptiveNrf52 store;
    assert(store.selectStorage() && store.usesInternal());
    assert(strstr(store.selectionReason(), "newer OTAFIX"));
    assert(probes == 0 && sd_probes == 0); // don't clock SD or same-CS NOR
    assert(!store.plan_layout(true, 100000, 365, 100000, false));
  }
  sd_cap = true; media = false;
  {
    OtaStoreAdaptiveNrf52 store;
    assert(store.selectStorage() && store.usesInternal());
    assert(probes == 0 && sd_probes == 1);
  }
  media = true; valid_identity = false; sd_probes = 0;
  {
    OtaStoreAdaptiveNrf52 store;
    assert(!store.selectStorage());
    assert(!store.usesExternal() && !store.usesInternal());
    assert(sd_probes == 0 && probes == 0);
  }
  valid_identity = true; device_name = "3401_DFU";
  {
    OtaStoreAdaptiveNrf52 store;
    assert(!store.selectStorage() && sd_probes == 0 && probes == 0);
  }
  device_name = "4631_DFU"; card_checks = 0;
  rak4631_set_ethernet_spi_owner(true);
  {
    OtaStoreAdaptiveNrf52 store;
    assert(store.selectStorage() && store.usesInternal());
    assert(card_checks == 0 && sd_probes == 0 && probes == 0);
  }
  rak4631_set_ethernet_spi_owner(false);
  assert(internal_live == 0 && nor_live == 0);
}
'''
        for legacy_combined in (False, True):
            with self.subTest(legacy_combined=legacy_combined):
                defines = ["OTA_RAK15002_SD_STORE", "OTA_INTERNAL_BOOTLOADER_UPDATE",
                           "OTA_RAK_AUTO_STORE"]
                if legacy_combined:
                    defines.append("RAK4631_COMBINED_ETHERNET")
                run_cpp(source, defines)


if __name__ == "__main__":
    unittest.main()
