"""Exercise combined RAK4631 SPI ownership and the actual adaptive store."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def run_cpp(source, extra_sources=()):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        sources = []
        for index, text in enumerate((source, *extra_sources)):
            cpp = path / f"test{index}.cpp"
            cpp.write_text(text)
            sources.append(str(cpp))
        exe = path / "test"
        subprocess.run([
            "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-DRAK4631_COMBINED_ETHERNET", "-DOTA_RAK_AUTO_STORE",
            "-DOTA_INTERNAL_BOOTLOADER_UPDATE", "-DMESHCORE_OTA_DEVICE_DEFLATE=1",
            "-I", str(ROOT),
            *sources, "-o", str(exe),
        ], check=True)
        subprocess.run([str(exe)], check=True)


class CombinedRakOtaTest(unittest.TestCase):
    def test_self_serve_and_manual_buffer_cannot_allocate_during_ethernet(self):
        context = (ROOT / "src/helpers/ota/OtaContext.h").read_text()
        start = context.index("  bool ensureServeBuffer() {")
        end = context.index("  void releaseServeBuffer()", start)
        buffer_method = context[start:end]
        self_source = (ROOT / "src/helpers/ota/OtaSelf.cpp").read_text()
        start = self_source.index("bool ota_serve_self(OtaContext& c, uint32_t fw_version) {")
        end = self_source.index("  SelfFwInfo fi;", start)
        self_prologue = self_source[start:end]
        source = r'''
#include <cassert>
#include <cstdlib>
#include "src/helpers/nrf52/Rak4631SharedSpi.h"
using namespace mesh::ota;
namespace mesh { namespace ota {
static unsigned allocations = 0, self_body_calls = 0;
constexpr unsigned OTA_SERVE_BUF_SIZE = 4096;
void* tracked_malloc(size_t bytes) {
  assert(!rak4631_ethernet_owns_spi()); ++allocations;
  return std::malloc(bytes);
}
struct OtaContext {
  uint8_t* serve_buf = nullptr;
#define malloc tracked_malloc
''' + buffer_method + r'''
#undef malloc
};
''' + self_prologue + r'''
  (void)c; (void)fw_version;
  ++self_body_calls;
  return true;
}
} }
int main() {
  OtaContext context;
  rak4631_set_ethernet_spi_owner(true);
  assert(!context.ensureServeBuffer() && !ota_serve_self(context, 0));
  assert(!context.serve_buf && allocations == 0 && self_body_calls == 0);
  rak4631_set_ethernet_spi_owner(false);
  assert(context.ensureServeBuffer() && ota_serve_self(context, 0));
  assert(context.serve_buf && allocations == 1 && self_body_calls == 1);
  free(context.serve_buf);
}
'''
        run_cpp(source)

    def test_context_handoff_frees_idle_self_serve_and_preserves_busy_ota(self):
        header = (ROOT / "src/helpers/ota/OtaContext.h").read_text()
        start = header.index("  // Only the main thread may change ownership.")
        end = header.index("#endif\n\n#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)", start)
        body = header[start:end]
        source = r'''
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "src/helpers/ota/OtaBlInfo.h"
#include "src/helpers/ota/OtaRakStoragePolicy.h"
#include "src/helpers/nrf52/Rak4631SharedSpi.h"
using namespace mesh::ota;
namespace mesh { namespace ota {
static unsigned frees = 0, probes = 0;
static uint8_t detection = 2;
static uint32_t rak_id = 0;
static OtaBlCaps app_caps, boot_caps;
struct OtaBootloaderIdentity {
  bool present = true, crc_ok = true;
  const char* device_name = "4631_DFU";
};
static OtaBootloaderIdentity identity;
void ota_transport_deflate() {}
bool ota_self_serve_supported(bool external) { return external; }
constexpr unsigned CODEC_DETOOLS_INPLACE = 2;
class OtaStoreQspiNrf52 {
public:
  static uint8_t autoDetect() { assert(!rak4631_ethernet_owns_spi()); ++probes; return detection; }
  static void autoProbeIds(uint32_t& rak, uint32_t& w25) { rak = rak_id; w25 = 0xef4015; }
};
struct OtaManager {
  enum FetchState { IDLE, WANT_MANIFEST, WANT_LEAVES, VERIFYING_STAGED, FETCHING,
                    COMPLETE, FAILED, PAUSED };
  unsigned state = IDLE, serve_jobs = 0, manifest_jobs = 0, served = 0;
  bool own = false, accept_full = true, accept_boot = false, archive = true;
  void (*encoder)() = ota_transport_deflate;
  bool servingPrimaryManifest(const uint8_t*) const { return own; }
  unsigned fetchState() const { return state; }
  unsigned pendingServeJobs() const { return serve_jobs; }
  unsigned pendingManifestJobs() const { return manifest_jobs; }
  unsigned servedCount() const { return served; }
  void set_accept_full(bool value) { accept_full = value; }
  void set_accept_bootloader(bool value) { accept_boot = value; }
  void set_apply_codec(unsigned) {}
  void set_fetch_store(void*) {}
  void set_archive_interest(bool value) { archive = value; }
  void set_transport_deflate_encoder(void (*value)()) { encoder = value; }
  void clear_primary() { own = false; served = 0; }
};
struct Store {
  unsigned size = 0, resets = 0;
  bool allow_reset = true, allow_internal = true;
  unsigned staged_size() const { return size; }
  bool resetSelection() { if (size || !allow_reset) return false; ++resets; return true; }
  bool usesExternal() const { return !rak4631_ethernet_owns_spi() && detection == 2; }
  bool usesInternal() const { return allow_internal && rak4631_ethernet_owns_spi(); }
};
void tracked_free(void* pointer) {
  if (pointer) { assert(!rak4631_ethernet_owns_spi()); ++frees; }
  std::free(pointer);
}
struct Context {
  OtaManager manager;
  Store fetch_store;
  uint8_t serve_self_manifest[1] = {};
  bool apply_pending = false, bootloader_apply_pending = false, serving = false;
  bool folder_active = false, fetch_to_folder = false, self_serve_supported = true;
  unsigned serve_expected = 0;
  void* _folder_source = nullptr;
  void* folder_dest = nullptr;
  void* disconnected_folder_dest = nullptr;
  uint8_t *serve_self_leaves = nullptr, *serve_self_proof = nullptr, *serve_buf = nullptr;
  OtaBlCaps& bootloaderAppCaps() { return app_caps; }
  OtaBlCaps& bootloaderUpdateCaps() { return boot_caps; }
  OtaBootloaderIdentity& bootloaderIdentity() { return identity; }
  void releaseServeBuffer() { tracked_free(serve_buf); serve_buf = nullptr; }
#define free tracked_free
''' + body + r'''
#undef free
};
} }
int main() {
  app_caps.present = boot_caps.present = true;
  app_caps.apply_abi = boot_caps.apply_abi = 3;
  app_caps.codec_mask = boot_caps.codec_mask = 5;
  app_caps.storage_flags = 0x1e; app_caps.optional_app_storage = 0x14;
  boot_caps.storage_flags = 0x0a;
  Context c;
  char error[160];
  for (unsigned state = 1; state <= 7; ++state) {
    c.manager.state = state;
    assert(!c.prepareRakEthernet(error, sizeof(error)));
    assert(!c.canReleaseRakEthernet() && !rak4631_ethernet_owns_spi());
    assert(probes == 0 && c.fetch_store.resets == 0);
  }
  c.manager.state = OtaManager::IDLE;
  c.fetch_store.size = 100;
  assert(!c.prepareRakEthernet(error, sizeof(error)) && probes == 0);
  c.fetch_store.size = 0;
  for (bool* flag : {&c.apply_pending, &c.bootloader_apply_pending, &c.serving,
                     &c.folder_active, &c.fetch_to_folder}) {
    *flag = true;
    assert(!c.prepareRakEthernet(error, sizeof(error)) && probes == 0);
    *flag = false;
  }
  for (unsigned* count : {&c.manager.serve_jobs, &c.manager.manifest_jobs,
                          &c.manager.served, &c.serve_expected}) {
    *count = 1;
    assert(!c.prepareRakEthernet(error, sizeof(error)) && probes == 0);
    *count = 0;
  }
  for (void** pointer : {&c._folder_source, &c.folder_dest, &c.disconnected_folder_dest}) {
    *pointer = &c;
    assert(!c.prepareRakEthernet(error, sizeof(error)) && probes == 0);
    *pointer = nullptr;
  }
  detection = 1; rak_id = 0xc84015;
  assert(!c.prepareRakEthernet(error, sizeof(error)));
  assert(strstr(error, "RAK15001") && !rak4631_ethernet_owns_spi());
  detection = 3;
  assert(!c.prepareRakEthernet(error, sizeof(error)));
  detection = 2; rak_id = 0x123456;
  assert(!c.prepareRakEthernet(error, sizeof(error)));
  assert(strstr(error, "unknown SPI"));
  rak_id = 0;
  app_caps.optional_app_storage = 0;
  identity.device_name = "4631_W25Q16_DFU";
  assert(!c.prepareRakEthernet(error, sizeof(error)));
  assert(strstr(error, "bootloader cannot") && c.fetch_store.resets == 0);
  app_caps.optional_app_storage = 0x14; identity.device_name = "4631_DFU";
  c.manager.own = true; c.manager.served = 1; c.serving = true;
  c.serve_self_leaves = static_cast<uint8_t*>(malloc(64));
  c.serve_self_proof = static_cast<uint8_t*>(malloc(64));
  c.serve_buf = static_cast<uint8_t*>(malloc(64));
  assert(c.prepareRakEthernet(error, sizeof(error)));
  assert(rak4631_ethernet_owns_spi() && c.fetch_store.resets == 1);
  assert(frees == 3 && !c.serve_self_leaves && !c.serve_self_proof && !c.serve_buf);
  assert(!c.serving && c.manager.served == 0 && !c.manager.own);
  assert(!c.manager.accept_full && c.manager.accept_boot && !c.manager.archive);
  assert(!c.self_serve_supported && !c.manager.encoder);
  c.manager.state = OtaManager::FETCHING;
  assert(!c.releaseRakEthernet() && rak4631_ethernet_owns_spi());
  assert(c.fetch_store.resets == 1 && !c.self_serve_supported);
  c.manager.state = OtaManager::IDLE; c.fetch_store.size = 100;
  assert(!c.releaseRakEthernet() && rak4631_ethernet_owns_spi());
  c.fetch_store.size = 0;
  assert(c.releaseRakEthernet() && !rak4631_ethernet_owns_spi());
  assert(c.fetch_store.resets == 2 && c.manager.accept_full);
  assert(c.self_serve_supported && c.manager.encoder == ota_transport_deflate);
}
'''
        run_cpp("#include <initializer_list>\n" + source)

    def test_real_encoder_cannot_allocate_while_ethernet_owns_spi(self):
        source = r'''
#include <cassert>
#include <cstdlib>
#include <cstring>
#include "src/helpers/ota/OtaDeflate.h"
#include "src/helpers/nrf52/Rak4631SharedSpi.h"
using namespace mesh::ota;
static unsigned allocations = 0;
extern "C" void* __real_calloc(size_t, size_t);
extern "C" void* __wrap_calloc(size_t n, size_t size) {
  assert(!rak4631_ethernet_owns_spi()); ++allocations;
  return __real_calloc(n, size);
}
int main() {
  uint8_t input[256], encoded[256], output[256];
  memset(input, 'A', sizeof(input));
  uint16_t length = 123;
  rak4631_set_ethernet_spi_owner(true);
  assert(!ota_transport_deflate(nullptr, input, sizeof(input), encoded, sizeof(encoded), &length));
  assert(length == 0 && allocations == 0);
  rak4631_set_ethernet_spi_owner(false);
  assert(ota_transport_deflate(nullptr, input, sizeof(input), encoded, sizeof(encoded), &length));
  assert(allocations == 1 && length < sizeof(input));
  rak4631_set_ethernet_spi_owner(true);
  uint16_t decoded = 0;
  assert(ota_transport_inflate(nullptr, encoded, length, output, sizeof(output), &decoded));
  assert(decoded == sizeof(input) && memcmp(input, output, sizeof(input)) == 0);
  assert(allocations == 1); // ordinary OTA reception stays available
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            objects = []
            for relative in ("OtaTinf.c",):
                obj = path / (Path(relative).stem + ".o")
                subprocess.run([
                    "cc", "-std=c99", "-DENABLE_OTA", "-DMESHCORE_OTA_DEVICE_DEFLATE=1",
                    "-c", str(ROOT / "src/helpers/ota" / relative), "-o", str(obj),
                ], check=True)
                objects.append(str(obj))
            cpp = path / "test.cpp"
            cpp.write_text(source)
            exe = path / "test"
            subprocess.run([
                "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-DRAK4631_COMBINED_ETHERNET", "-DENABLE_OTA",
                "-DMESHCORE_OTA_DEVICE_DEFLATE=1", "-I", str(ROOT),
                str(cpp), str(ROOT / "src/helpers/ota/OtaDeflate.cpp"),
                *objects, "-Wl,--wrap=calloc", "-o", str(exe),
            ], check=True)
            subprocess.run([str(exe)], check=True)

    def test_only_explicit_combined_build_can_relax_static_bus_exclusion(self):
        base = ["NRF52_PLATFORM", "OTA_QSPI_STORE", "OTA_QSPI_SHARED_WISBLOCK_SPI"]
        cases = [
            (base, True),
            (base + ["ETHERNET_ENABLED"], False),
            (base + ["ETHERNET_ENABLED", "RAK4631_COMBINED_ETHERNET"], False),
            (base + ["ETHERNET_ENABLED", "RAK4631_COMBINED_ETHERNET",
                     "OTA_RAK_AUTO_STORE", "RAK_4631"], True),
        ]
        for defines, accepted in cases:
            with self.subTest(defines=defines):
                result = subprocess.run([
                    "c++", "-std=c++17", "-E", "-x", "c++", "-",
                    "-I", str(ROOT), *("-D" + value for value in defines),
                ], input='#include "src/helpers/ota/OtaStoreQspiNrf52.h"\n',
                   capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, accepted, result.stderr)

    def test_spi_owner_is_shared_between_translation_units(self):
        run_cpp(r'''
#include <cassert>
#include "src/helpers/nrf52/Rak4631SharedSpi.h"
void reserve_elsewhere(bool);
int main() {
  assert(!mesh::ota::rak4631_ethernet_owns_spi());
  reserve_elsewhere(true);
  assert(mesh::ota::rak4631_ethernet_owns_spi());
  reserve_elsewhere(false);
  assert(!mesh::ota::rak4631_ethernet_owns_spi());
}
''', (r'''
#include "src/helpers/nrf52/Rak4631SharedSpi.h"
void reserve_elsewhere(bool owner) {
  mesh::ota::rak4631_set_ethernet_spi_owner(owner);
}
''',))

    def test_actual_adaptive_store_respects_owner_and_bootloader(self):
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
static unsigned detection = 2, probes = 0, internal_live = 0, external_live = 0;
static uint8_t storage_flags = 0x1e, optional_storage = 0x14;
static bool valid_identity = true;
static const char* device_name = "4631_DFU";
struct OtaBootloaderIdentity { bool crc_ok = true; const char* device_name; };
bool ota_installed_bootloader_identity(OtaBootloaderIdentity& identity) {
  identity.device_name = device_name; return valid_identity;
}
struct MotaManifest { bool is_bootloader() const { return false; } };
bool mota_parse_manifest(const uint8_t*, size_t, MotaManifest&) { return false; }
OtaBlCaps simulatedAppCaps() {
  OtaBlCaps c; c.present = true; c.apply_abi = 3; c.codec_mask = 5;
  c.storage_flags = storage_flags; c.optional_app_storage = optional_storage;
  return c;
}
OtaBlCaps simulatedBootCaps() {
  OtaBlCaps c = simulatedAppCaps(); c.storage_flags = 0x0a; return c;
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
  OtaStoreFlashNrf52() { ++internal_live; assert(external_live == 0); }
  ~OtaStoreFlashNrf52() { --internal_live; }
};
class OtaStoreQspiNrf52 : public Store {
public:
  OtaStoreQspiNrf52() { ++external_live; assert(internal_live == 0); }
  ~OtaStoreQspiNrf52() { --external_live; }
  static uint8_t autoDetect() { ++probes; return detection; }
  static bool headerW25Detected() { return false; }
  uint32_t jedec_id() const { return 0xef4015; }
  uint8_t status1() const { return 0; }
  const char* last_stage() const { return "idle"; }
  const char* last_error() const { return ""; }
};
#define ota_bootloader_app_caps simulatedAppCaps
#define ota_bootloader_update_caps simulatedBootCaps
''' + body + r'''
} }
int main() {
  // Ethernet may not discover NOR, and selects the internal path only when
  // the installed bootloader supports that actual source.
  rak4631_set_ethernet_spi_owner(true);
  {
    OtaStoreAdaptiveNrf52 store;
    assert(store.usesInternal() && !store.usesExternal());
    assert(probes == 0);
    assert(strstr(store.selectionReason(), "Ethernet active"));
    assert(store.plan_layout(true, 40960, 365, 40960, true));
    assert(store.usesInternal());
    assert(store.begin(41330));
    assert(!store.resetSelection()); // privileged staging is pinned too
  }
  assert(internal_live == 0 && external_live == 0);
  storage_flags = 0x06; optional_storage = 0;
  device_name = "4631_W25Q16_DFU";
  {
    OtaStoreAdaptiveNrf52 store;
    assert(!store.usesInternal() && !store.usesExternal());
    assert(store.capacity() == 0 && !store.begin(100));
    assert(strstr(store.selectionReason(), "bootloader lacks internal OTA"));
    assert(probes == 0);
  }
  storage_flags = 0x1e; optional_storage = 0x14;
  device_name = "4631_DFU"; valid_identity = false;
  {
    OtaStoreAdaptiveNrf52 store;
    assert(!store.usesInternal()); // an unverified optional marker is insufficient
    assert(!store.begin(100));
    assert(probes == 0);
  }
  valid_identity = true;
  rak4631_set_ethernet_spi_owner(false);
  {
    OtaStoreAdaptiveNrf52 store;
    assert(store.usesExternal() && probes == 1);
    assert(store.begin(100));
    assert(!store.resetSelection()); // neither receive nor ready data is dropped
    rak4631_set_ethernet_spi_owner(true); // defensive gate against a bad caller
    uint8_t byte = 0;
    assert(!store.begin(200) && !store.write(0, &byte, 1));
    assert(!store.read(0, &byte, 1) && store.capacity() == 0);
    assert(!store.resetSelection()); // existing data remains pinned
    rak4631_set_ethernet_spi_owner(false);
    store.clear();
    assert(store.resetSelection());
    assert(internal_live == 0 && external_live == 0);
    rak4631_set_ethernet_spi_owner(true);
    assert(store.usesInternal() && probes == 1);
    assert(store.resetSelection());
    rak4631_set_ethernet_spi_owner(false);
    assert(store.usesExternal() && probes == 2);
  }
  assert(internal_live == 0 && external_live == 0);
}
'''
        run_cpp(source)

    def test_actual_discovery_does_no_gpio_io_while_ethernet_owns_spi(self):
        cpp = (ROOT / "src/helpers/ota/OtaStoreQspiNrf52.cpp").read_text()
        body = cpp[cpp.index("uint8_t OtaStoreQspiNrf52::autoDetect()"):
                   cpp.index("OtaStoreQspiNrf52::OtaStoreQspiNrf52()")]
        source = r'''
#include <cassert>
#include <cstdint>
#include "src/helpers/nrf52/Rak4631SharedSpi.h"
using namespace mesh::ota;
namespace mesh { namespace ota {
static uint8_t auto_cs_pin = 0xff, auto_detection = 0xff;
static uint32_t auto_jedec_id = 0, auto_rak_probe_id = 0, auto_w25_probe_id = 0;
static bool auto_header_w25 = false;
static constexpr uint8_t HEADER_SCK_PIN = 16, HEADER_IO0_PIN = 17, HEADER_IO1_PIN = 15;
static unsigned gpio_calls = 0, nor_calls = 0;
static uint32_t rak_id = 0, w25_id = 0xef4015, header_id = 0;
uint8_t arduino_to_physical(uint32_t pin) { return pin; }
void nrf_gpio_pin_set(uint32_t) { ++gpio_calls; }
void nrf_gpio_cfg_output(uint32_t) { ++gpio_calls; }
void nrf_gpio_cfg_default(uint32_t) { ++gpio_calls; }
uint32_t probe_nor(uint8_t cs) {
  ++nor_calls;
  return cs == 26 ? rak_id : auto_header_w25 ? header_id : w25_id;
}
class OtaStoreQspiNrf52 {
public:
  static uint8_t autoDetect();
  static bool headerW25Detected();
  static void autoProbeIds(uint32_t&, uint32_t&);
};
#if defined(OTA_RAK_AUTO_STORE)
''' + body + r'''
} }
int main() {
  uint32_t rak, w25;
  rak4631_set_ethernet_spi_owner(true);
  assert(OtaStoreQspiNrf52::autoDetect() == 0);
  OtaStoreQspiNrf52::autoProbeIds(rak, w25);
  assert(!OtaStoreQspiNrf52::headerW25Detected());
  assert(gpio_calls == 0 && nor_calls == 0 && auto_detection == 0xff);
  rak4631_set_ethernet_spi_owner(false);
  assert(OtaStoreQspiNrf52::autoDetect() == 2);
  assert(nor_calls == 2 && auto_cs_pin == 31 && auto_jedec_id == 0xef4015);
  const unsigned gpio_after_discovery = gpio_calls;
  rak4631_set_ethernet_spi_owner(true);
  assert(OtaStoreQspiNrf52::autoDetect() == 0);
  OtaStoreQspiNrf52::autoProbeIds(rak, w25);
  assert(rak == 0 && w25 == 0xef4015); // diagnostics retain physical discovery
  assert(gpio_calls == gpio_after_discovery && nor_calls == 2);
  rak4631_set_ethernet_spi_owner(false);
  assert(OtaStoreQspiNrf52::autoDetect() == 2 && nor_calls == 2);
  auto_detection = 0xff; w25_id = 0; header_id = 0xef4015;
  assert(OtaStoreQspiNrf52::autoDetect() == 2);
  assert(OtaStoreQspiNrf52::headerW25Detected());
  const unsigned after_header_probe = nor_calls;
  rak4631_set_ethernet_spi_owner(true);
  assert(OtaStoreQspiNrf52::headerW25Detected()); // GPS still reserves header UART1
  assert(nor_calls == after_header_probe);
}
'''
        run_cpp(source)


if __name__ == "__main__":
    unittest.main()
