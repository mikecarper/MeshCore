"""Compile production Tower context policy and reboot dispatch on the host."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def method(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


CONTEXT_SETUP = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#define NRF52_PLATFORM 1
#define OTA_SD_STORE 1
#define OTA_FLASH_STORE 1
#define OTA_SD_BOOTLOADER_UPDATE 1
#define OTA_TOWER_AUTO_STORE 1
#define MOTA_MIGRATION_TARGET_ID 0x77
#define MESHCORE_OTA_DEVICE_DEFLATE 1
#include "helpers/ota/OtaSelfServePolicy.h"
using namespace mesh::ota;
using OtaSend = void (*)();
struct MotaManifest { bool signature_valid = false; };
struct SignerPolicy { bool allowed = true; mutable unsigned checks = 0; };
[[maybe_unused]] static bool ota_manifest_trusted(const MotaManifest& manifest,
                                                const SignerPolicy& allow) {
  ++allow.checks;
  return allow.allowed && manifest.signature_valid;
}
using OtaManifestAdmit = bool (*)(void*, const MotaManifest&);
struct Context;
using OtaContext = Context;
using CodecFunction = void (*)();
static constexpr uint8_t CODEC_DETOOLS_INPLACE = 2;
static constexpr size_t MOTA_MFL = 197;
static void ota_transport_inflate() {}
static void ota_transport_deflate() {}
struct SelfFwInfo {
  bool valid = false;
  uint32_t target_id = 0, fw_version = 0;
  char hw_id[33] = {};
};
static SelfFwInfo current;
static bool ota_self_firmware(SelfFwInfo& out) { out = current; return current.valid; }
struct OtaBlCaps { bool valid = true; };
static bool ota_bootloader_self_update_caps_valid(const OtaBlCaps& caps) { return caps.valid; }
struct OtaStoreSdNrf52 {};
struct TowerStore {
  enum Mode { Unselected, Sd, Internal, Unsafe };
  Mode mode = Unselected, fitted = Sd;
  bool usable = true;
  unsigned selection_calls = 0, probes = 0;
  OtaStoreSdNrf52 sd;
  bool selectStorage() {
    ++selection_calls;
    if (mode == Unselected) { ++probes; mode = fitted; }
    return usable && mode != Unsafe;
  }
  bool usesExternal() const { return mode == Sd; }
  bool usesInternal() const { return mode == Internal; }
  OtaStoreSdNrf52& sdStore() { return sd; }
  void resetSelection() { mode = Unselected; }
};
struct Cache {
  OtaStoreSdNrf52* owner = nullptr;
  unsigned attachments = 0;
  void attach(OtaStoreSdNrf52& store) { ++attachments; owner = &store; }
};
struct Manager {
  bool full = true, bootloader = true, archive = false, enforce_version = false;
  uint8_t codec = 0xFF;
  uint32_t target = 0, migration = 0, floor = 0;
  CodecFunction encoder = ota_transport_deflate, decoder = nullptr;
  TowerStore* destination = nullptr;
  OtaManifestAdmit admission = nullptr;
  void* admission_context = nullptr;
  void set_manifest_admission(OtaManifestAdmit callback, void* context) {
    admission = callback; admission_context = context;
  }
  const uint8_t* primary = nullptr;
  unsigned clear_calls = 0, folder_sources = 3;
  void begin(uint32_t id, OtaSend, void*) { target = id; }
  void set_auto_migration_target(uint32_t id) { migration = id; }
  void set_transport_deflate_decoder(CodecFunction fn) { decoder = fn; }
  void set_transport_deflate_encoder(CodecFunction fn) { encoder = fn; }
  void set_auto_version_floor(uint32_t version, bool enforce) { floor = version; enforce_version = enforce; }
  void set_accept_full(bool value) { full = value; }
  void set_accept_bootloader(bool value) { bootloader = value; }
  void set_apply_codec(uint8_t value) { codec = value; }
  void set_fetch_store(TowerStore* value) { destination = value; }
  void set_archive_interest(bool value) { archive = value; }
  bool servingPrimaryManifest(const uint8_t* manifest) const { return primary == manifest; }
  void clear_primary() { ++clear_calls; primary = nullptr; }
};
struct Context {
  SignerPolicy allow;
  TowerStore fetch_store;
  Cache sd_cache;
  Manager manager;
  bool fetch_to_folder = true, self_serve_supported = true, serving = false;
  char hw_id[33] = {};
  uint8_t serve_self_manifest[MOTA_MFL] = {};
  OtaBlCaps caps;
  unsigned caps_reads = 0;
  const OtaBlCaps& bootloaderUpdateCaps() { ++caps_reads; return caps; }
'''

CONTEXT_CASES = r'''
};
static void send() {}
int main() {
  // Startup adopts authenticated self identity without selecting/mounting SD
  // or enabling a receiver capability that depends on the absent probe.
  current.valid = true; current.target_id = 0x1234; current.fw_version = 0x01170109;
  strcpy(current.hw_id, "Heltec_tower_v2");
  Context sd;
  sd.begin(0x55, send, nullptr, "fallback");
  assert(sd.manager.admission && sd.manager.admission_context == &sd);
  const MotaManifest signed_manifest{true}, forged_manifest{false};
  assert(sd.manager.admission(sd.manager.admission_context, signed_manifest));
  assert(!sd.manager.admission(sd.manager.admission_context, forged_manifest));
  sd.allow.allowed = false;
  assert(!sd.manager.admission(sd.manager.admission_context, signed_manifest));
  sd.allow.allowed = true;
  assert(sd.allow.checks == 3);
  assert(sd.fetch_store.probes == 0 && sd.fetch_store.selection_calls == 0);
  assert(sd.caps_reads == 0 && !sd.manager.full && !sd.manager.bootloader);
  assert(!sd.self_serve_supported && sd.manager.encoder == nullptr);
  assert(sd.manager.decoder == ota_transport_inflate && sd.manager.codec == 2);
  assert(sd.manager.target == 0x1234 && sd.manager.floor == 0x01170109);
  assert(sd.manager.enforce_version && sd.manager.migration == 0x77);
  assert(!sd.fetch_to_folder && strcmp(sd.hw_id, "Heltec_tower_v2") == 0);
  assert(sd.manager.destination == &sd.fetch_store);
  assert(sd.sd_cache.owner == &sd.fetch_store.sd && sd.sd_cache.attachments == 1);

  assert(sd.prepareTowerStorage());
  assert(sd.fetch_store.probes == 1 && sd.manager.full && sd.manager.bootloader);
  assert(sd.self_serve_supported && sd.manager.encoder == ota_transport_deflate);
  assert(sd.manager.archive && sd.manager.clear_calls == 0);
  sd.manager.primary = sd.serve_self_manifest; sd.serving = true;
  // Mid-session disappearance does not change the selected backend or clear
  // a successfully offered image: the actual store handles subsequent I/O.
  sd.fetch_store.fitted = TowerStore::Internal;
  for (unsigned i = 0; i < 5; ++i) assert(sd.prepareTowerStorage());
  assert(sd.fetch_store.mode == TowerStore::Sd && sd.fetch_store.probes == 1);
  assert(sd.serving && sd.manager.primary == sd.serve_self_manifest);

  // A legacy app-capable SD loader does not receive privileged packages.
  sd.caps.valid = false;
  assert(sd.prepareTowerStorage());
  assert(sd.manager.full && sd.self_serve_supported && !sd.manager.bootloader);

  // A deliberate selection reset removes only our automatic primary image.
  // Attached folder sources are independent and must remain available.
  sd.fetch_store.resetSelection();
  assert(sd.prepareTowerStorage());
  assert(sd.fetch_store.mode == TowerStore::Internal && sd.fetch_store.probes == 2);
  assert(!sd.manager.full && !sd.manager.bootloader && !sd.manager.archive);
  assert(!sd.self_serve_supported && sd.manager.encoder == nullptr);
  assert(!sd.serving && sd.manager.primary == nullptr && sd.manager.clear_calls == 1);
  assert(sd.manager.folder_sources == 3);
  for (unsigned i = 0; i < 5; ++i) assert(sd.prepareTowerStorage());
  assert(sd.manager.clear_calls == 1 && sd.fetch_store.probes == 2);

  // `ota dev serve self` deliberately exports an internal-fallback base for
  // delta capture. Repeated maintenance/status preparation must preserve that
  // explicit diagnostic offer while automatic self-serving stays disabled.
  sd.manager.primary = sd.serve_self_manifest; sd.serving = true;
  for (unsigned i = 0; i < 5; ++i) assert(sd.prepareTowerStorage());
  assert(sd.serving && sd.manager.primary == sd.serve_self_manifest);
  assert(!sd.self_serve_supported && sd.manager.encoder == nullptr);
  assert(!sd.manager.full && !sd.manager.bootloader && !sd.manager.archive);
  assert(sd.manager.clear_calls == 1 && sd.fetch_store.probes == 2);

  uint8_t manual_manifest[MOTA_MFL] = {};
  sd.manager.primary = manual_manifest; sd.serving = true;
  assert(sd.prepareTowerStorage());
  assert(sd.manager.primary == manual_manifest && sd.serving);
  assert(sd.manager.clear_calls == 1 && sd.manager.folder_sources == 3);

  // Unknown storage and a failed compatibility check disable every optional
  // capability even if the store still reports a physically present SD card.
  Context unsafe;
  unsafe.fetch_store.fitted = TowerStore::Unsafe;
  unsafe.begin(0x55, send, nullptr);
  assert(unsafe.manager.admission && unsafe.manager.admission_context == &unsafe);
  unsafe.allow.allowed = false;
  assert(!unsafe.manager.admission(unsafe.manager.admission_context, signed_manifest));
  assert(sd.manager.admission(sd.manager.admission_context, signed_manifest));
  assert(unsafe.allow.checks == 1 && sd.allow.checks == 4);
  assert(!unsafe.prepareTowerStorage());
  assert(!unsafe.manager.full && !unsafe.manager.bootloader && !unsafe.manager.archive);
  assert(!unsafe.self_serve_supported && unsafe.manager.encoder == nullptr);
  Context incompatible;
  incompatible.fetch_store.fitted = TowerStore::Sd;
  incompatible.fetch_store.usable = false;
  incompatible.begin(0x55, send, nullptr);
  assert(!incompatible.prepareTowerStorage());
  assert(!incompatible.manager.full && !incompatible.manager.bootloader);
  assert(!incompatible.self_serve_supported && incompatible.manager.encoder == nullptr);

  current = SelfFwInfo();
  Context legacy;
  legacy.begin(0x55, send, nullptr, "fallback");
  assert(legacy.manager.target == 0x55 && legacy.manager.floor == 0);
  assert(strcmp(legacy.hw_id, "fallback") == 0 && legacy.fetch_store.probes == 0);
}
'''

HANDOFF_SETUP = r'''
#include <cassert>
#include <cstdint>
#define OTA_TOWER_AUTO_STORE 1
#define OTA_SD_STORE 1
#define OTA_FLASH_STORE 1
#define OTA_SD_BOOTLOADER_UPDATE 1
static constexpr uint8_t GPREGRET2_OTA_STAGE_LEGACY = 0xD4;
static constexpr uint8_t GPREGRET2_OTA_STAGE_EXPANDED = 0xED;
static constexpr uint8_t GPREGRET2_OTA_STAGE_SD = 0x53;
static constexpr uint8_t GPREGRET2_OTA_STAGE_HYBRID = 0xA6;
static constexpr uint8_t GPREGRET2_OTA_STAGE_QSPI = 0x51;
static constexpr uint8_t GPREGRET_OTA_APPLY = 0x6A;
static constexpr uint8_t GPREGRET_OTA_BOOTLOADER_UPDATE = 0x6B;
struct Store {
  bool hybrid = false, authorized = true;
  unsigned published = 0;
  bool is_hybrid() const { return hybrid; }
  bool publish_hybrid_handoff() { ++published; return authorized; }
};
static Store* g_nrf52_apply_store = nullptr;
static uint32_t ceiling = 0xED000;
static bool reset_cleared = true;
static uint8_t request = 0, source = 0;
static unsigned resets = 0;
[[maybe_unused]] static uint32_t ota_nrf52_effective_stage_ceiling() { return ceiling; }
[[maybe_unused]] static uint8_t mota_nrf52_flash_stage_handoff(uint32_t n) { return n == 0xED000 ? 0xED : 0xD4; }
[[maybe_unused]] static bool ota_nrf52_clear_reset_reasons() { return reset_cleared; }
[[maybe_unused]] static void ota_nrf52_set_reset_handoff(uint8_t r, uint8_t s) { request = r; source = s; }
[[maybe_unused]] static void NVIC_SystemReset() { ++resets; }
'''

HANDOFF_CASES = r'''
int main() {
  ota_reboot_to_apply();
  assert(request == 0x6A && source == 0x53 && resets == 1);
  Store internal;
  g_nrf52_apply_store = &internal;
  ota_reboot_to_apply();
  assert(request == 0x6A && source == 0xED && resets == 2);
  ceiling = 0xD4000;
  ota_reboot_to_apply();
  assert(request == 0x6A && source == 0xD4 && resets == 3);
  g_nrf52_apply_store = nullptr;
  ota_reboot_to_apply();
  assert(request == 0x6A && source == 0x53 && resets == 4);
  // Shared legacy handling remains fail closed if a retained source cannot be
  // authorized. Tower's qualified application never enables this RAM store.
  internal.hybrid = true; internal.authorized = false;
  g_nrf52_apply_store = &internal;
  ota_reboot_to_apply();
  assert(request == 0 && source == 0xBD && resets == 5);
  internal.authorized = true; reset_cleared = false;
  ota_reboot_to_apply();
  assert(request == 0 && source == 0xBD && resets == 6 && internal.published == 1);
}
'''


class TowerAutoIntegrationTest(unittest.TestCase):
    def compile_run(self, source, expected=True):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            cpp = path / "integration.cpp"
            cpp.write_text(source, encoding="ascii")
            executable = path / "integration"
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "src"), str(cpp), "-o", str(executable)],
                           check=True, timeout=30)
            result = subprocess.run([str(executable)], cwd=path, capture_output=True,
                                    text=True, timeout=10)
            self.assertEqual(result.returncode == 0, expected, result.stderr)
            if not expected:
                self.assertIn("Assertion", result.stderr)

    def test_production_context_startup_and_deferred_storage_policy(self):
        context = (ROOT / "src/helpers/ota/OtaContext.h").read_text()
        methods = "\n".join(method(context, signature) for signature in (
            "OtaStoreSdNrf52& sdStagingStore()",
            "bool prepareTowerStorage()",
            "void begin(uint32_t target_id, OtaSend send, void* ctx",
        ))
        source = CONTEXT_SETUP + methods + CONTEXT_CASES
        self.compile_run(source)
        admission = """    manager.set_manifest_admission([](void* context, const MotaManifest& manifest) {
      return ota_manifest_trusted(manifest, static_cast<OtaContext*>(context)->allow);
    }, this);"""
        missing_admission = methods.replace(admission, "")
        self.assertNotEqual(missing_admission, methods)
        self.compile_run(CONTEXT_SETUP + missing_admission + CONTEXT_CASES, expected=False)
        wrong_context = methods.replace("}, this);", "}, nullptr);")
        self.assertNotEqual(wrong_context, methods)
        self.compile_run(CONTEXT_SETUP + wrong_context + CONTEXT_CASES, expected=False)
        # Negative controls prove the fixture catches the original startup
        # probe hazard and policy that accidentally advertises incompatible SD.
        eager = methods.replace("manager.begin(target_id, send, ctx);",
                                "fetch_store.selectStorage(); manager.begin(target_id, send, ctx);")
        self.compile_run(CONTEXT_SETUP + eager + CONTEXT_CASES, expected=False)
        permissive = methods.replace("usable && fetch_store.usesExternal()",
                                     "fetch_store.usesExternal()")
        self.compile_run(CONTEXT_SETUP + permissive + CONTEXT_CASES, expected=False)
        # The old unconditional cleanup erased explicit internal diagnostic
        # offers on the very next maintenance loop, blocking base capture.
        unconditional_clear = methods.replace(
            "    const bool was_self_serve_supported = self_serve_supported;\n", "").replace(
            "was_self_serve_supported && !self_serve_supported &&",
            "!self_serve_supported &&")
        self.assertNotEqual(unconditional_clear, methods)
        self.compile_run(CONTEXT_SETUP + unconditional_clear + CONTEXT_CASES,
                         expected=False)

    def test_production_reset_handoff_follows_approved_application_store(self):
        apply = (ROOT / "src/helpers/ota/OtaApply.cpp").read_text()
        production = method(apply, "void ota_reboot_to_apply() {                   // public:")
        self.compile_run(HANDOFF_SETUP + production + HANDOFF_CASES)
        old_dispatch = production.replace(
            "#if defined(OTA_RAK_AUTO_STORE) || defined(OTA_TOWER_AUTO_STORE)",
            "#if defined(OTA_RAK_AUTO_STORE)", 1)
        # Removing Tower from adaptive dispatch must break the runtime fixture;
        # it would always request SD even after internal-flash approval.
        self.compile_run(HANDOFF_SETUP + old_dispatch + HANDOFF_CASES, expected=False)

    def test_selection_precedes_live_resume_and_cli_codec_checks(self):
        mesh = (ROOT / "src/Mesh.cpp").read_text()
        maintenance = method(mesh, "void __attribute__((noinline)) Mesh::serviceLoopMaintenance()")
        self.assertLess(maintenance.index("prepareTowerStorage()"),
                        maintenance.index("resumeStaged(nullptr)"))
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        self.assertLess(cli.index("c.prepareTowerStorage()"), cli.index("c.manager.codecOk(selcodec)"))
        context = (ROOT / "src/helpers/ota/OtaContext.h").read_text()
        boot_apply = method(context, "bool apply_fetched_bootloader(")
        self.assertIn("if (!fetch_store.usesExternal())", boot_apply)
        self.assertIn("fetch_store.sdStore(), allow", boot_apply)
        apply = (ROOT / "src/helpers/ota/OtaApply.cpp").read_text()
        sd_apply = method(apply, "bool ota_apply_mota_nrf52(OtaStoreSdNrf52& store")
        self.assertLess(sd_apply.index("g_nrf52_apply_store = nullptr"),
                        sd_apply.index("ota_apply_mota_nrf52_external"))


if __name__ == "__main__":
    unittest.main()
