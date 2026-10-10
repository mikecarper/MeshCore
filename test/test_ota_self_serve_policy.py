#!/usr/bin/env python3
"""Exercise automatic self-serving eligibility without hardware or PlatformIO."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import test_ota_heap_context as heap_test
from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]


class OtaSelfServePolicyTest(unittest.TestCase):
    def compile_and_run(self, path, source, defines=(), scenarios=((),)):
        binary = path / "self-serve-policy.exe"
        sanitizers = [] if os.name == "nt" else ["-fsanitize=address,undefined"]
        result = subprocess.run([
            "c++", "-std=c++17", *sanitizers,
            *["-D" + define for define in defines],
            "-I", str(ROOT / "src"), str(source), "-o", str(binary),
        ], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for scenario in scenarios:
            result = subprocess.run([str(binary), *scenario], text=True,
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_platform_and_storage_policy_matrix(self):
        # The adaptive RAK flag takes precedence over a generic QSPI/SD flag:
        # fitted storage is not usable until its application backend is matched.
        matrix = [
            ((), False, False),
            (("ESP32_PLATFORM=1",), True, True),
            (("ESP32_PLATFORM=1", "OTA_FLASH_STORE=1"), True, True),
            (("NRF52_PLATFORM=1",), False, False),
            (("NRF52_PLATFORM=1", "OTA_FLASH_STORE=1"), False, False),
            (("NRF52_PLATFORM=1", "OTA_QSPI_STORE=1"), True, True),
            (("NRF52_PLATFORM=1", "OTA_SD_STORE=1"), True, True),
            (("NRF52_PLATFORM=1", "OTA_RAK_AUTO_STORE=1"), False, True),
            (("NRF52_PLATFORM=1", "OTA_TOWER_AUTO_STORE=1", "OTA_SD_STORE=1",
              "OTA_FLASH_STORE=1"), False, True),
            (("NRF52_PLATFORM=1", "OTA_RAK_AUTO_STORE=1",
              "OTA_QSPI_STORE=1"), False, True),
            (("NRF52_PLATFORM=1", "OTA_RAK_AUTO_STORE=1",
              "OTA_SD_STORE=1"), False, True),
            (("STM32_PLATFORM=1", "OTA_QSPI_STORE=1"), False, False),
            (("RP2040_PLATFORM=1", "OTA_SD_STORE=1"), False, False),
        ]
        # Source-only Companions must advertise only their host-provided files,
        # even when their architecture and fitted storage would otherwise qualify.
        matrix += [(defines + ("OTA_SEEDER_ONLY=1", "COMPANION_RADIO_FULL=1"),
                    False, False) for defines, _, _ in matrix if defines]
        with tempfile.TemporaryDirectory(prefix="ota-self-policy-") as directory:
            path = Path(directory)
            source = path / "test.cpp"
            source.write_text(r'''
#include <helpers/ota/OtaSelfServePolicy.h>
#include <cassert>
int main() {
  using mesh::ota::ota_self_serve_supported;
  assert(ota_self_serve_supported() == bool(EXPECT_INTERNAL));
  assert(ota_self_serve_supported(false) == bool(EXPECT_INTERNAL));
  assert(ota_self_serve_supported(true) == bool(EXPECT_EXTERNAL));
}
''')
            for defines, internal, external in matrix:
                with self.subTest(defines=defines):
                    self.compile_and_run(path, source, defines + (
                        "EXPECT_INTERNAL=" + str(int(internal)),
                        "EXPECT_EXTERNAL=" + str(int(external)),
                    ))

    def test_begin_latches_application_capability_before_backend_changes(self):
        begin = method((ROOT / "src/helpers/ota/OtaContext.h").read_text(),
                       "void begin(uint32_t target_id, OtaSend send,")
        with tempfile.TemporaryDirectory(prefix="ota-self-begin-") as directory:
            path = Path(directory)
            (path / "begin.h").write_text(begin)
            source = path / "test.cpp"
            source.write_text(r'''
#include <helpers/ota/OtaSelfServePolicy.h>
#include <cstdint>
#include <cassert>
#include <cstring>
using namespace mesh::ota;
typedef bool (*OtaSend)(void*, const uint8_t*, uint16_t, bool);
struct SelfFwInfo { bool valid = false; uint32_t target_id = 0, fw_version = 0; char hw_id[33] = {}; };
static bool ota_self_firmware(SelfFwInfo& info) { info = SelfFwInfo(); return false; }
static int ota_transport_inflate;
struct MotaManifest {};
static bool ota_manifest_trusted(const MotaManifest&, int) { return false; }
struct Context;
using OtaContext = Context;
enum { CODEC_DETOOLS_INPLACE = 1, CODEC_DETOOLS_SEQUENTIAL = 2 };
struct Store {
  bool external = false;
  unsigned probes = 0;
  bool usesExternal() { ++probes; return external; }
};
struct Manager {
  static const uint8_t AUTOFETCH_OFF = 0;
  bool accept_full = false;
  void begin(uint32_t, OtaSend, void*) {}
  void set_transport_deflate_decoder(int) {}
  template<class Callback> void set_manifest_admission(Callback, void*) {}
  void set_auto_version_floor(uint32_t, bool) {}
  void set_accept_full(bool value) { accept_full = value; }
  void set_autofetch(uint8_t) {}
  void set_apply_codec(int) {}
  void set_apply_codec2(int) {}
  void set_accept_bootloader(bool) {}
  void set_fetch_store(Store*) {}
  void set_archive_interest(bool) {}
};
using OtaManager = Manager;
struct Context {
  Manager manager;
  int allow = 0;
  Store fetch_store;
  bool self_serve_supported = false, fetch_to_folder = false;
  static const uint8_t AUTOINSTALL_OFF = 0;
  uint8_t autoinstall = AUTOINSTALL_OFF;
  char hw_id[33] = {};
  struct Cache { void attach(Store&) {} } sd_cache;
  Store& sdStagingStore() { return fetch_store; }
#include "begin.h"
};
static bool send(void*, const uint8_t*, uint16_t, bool) { return true; }
int main() {
  Context c;
  assert(!c.self_serve_supported);
  c.begin(123, send, nullptr, "test");
  assert(c.self_serve_supported == bool(EXPECT_INTERNAL));
  // The adaptive store switches to internal flash for bootloader packages.
  // Eligibility describes the application storage chosen at begin, not that
  // transient fetch destination; there must be no live backend re-probe here.
  c.fetch_store.external = true;
  assert(c.self_serve_supported == bool(EXPECT_INTERNAL));
  c.begin(123, send, nullptr, "test");
  assert(c.self_serve_supported == bool(EXPECT_EXTERNAL));
  unsigned probes = c.fetch_store.probes;
  c.fetch_store.external = false;
  assert(c.self_serve_supported == bool(EXPECT_EXTERNAL));
  assert(c.fetch_store.probes == probes);
}
''')
            for defines, internal, external in [
                (("ESP32_PLATFORM=1",), True, True),
                (("NRF52_PLATFORM=1", "OTA_FLASH_STORE=1"), False, False),
                (("NRF52_PLATFORM=1", "OTA_QSPI_STORE=1"), True, True),
                (("NRF52_PLATFORM=1", "OTA_SD_STORE=1"), True, True),
                (("NRF52_PLATFORM=1", "OTA_RAK_AUTO_STORE=1"), False, True),
                (("ESP32_PLATFORM=1", "OTA_SEEDER_ONLY=1"), False, False),
            ]:
                with self.subTest(defines=defines):
                    self.compile_and_run(path, source, defines + (
                        "EXPECT_INTERNAL=" + str(int(internal)),
                        "EXPECT_EXTERNAL=" + str(int(external)),
                    ))

    def test_registration_and_temp_radio_service_keep_lazy_workspace(self):
        context = (ROOT / "src/helpers/ota/OtaContext.h").read_text()
        begin = method(context, "void begin(uint32_t target_id, OtaSend send,")
        self.assertRegex(context, r"bool\s+self_serve_supported\s*=\s*false")
        # Eligibility is cheap registration only. Scanning/hashing the image
        # and claiming its leaf/proof buffers stay at the announcement boundary.
        self.assertNotIn("ota_serve_self(", begin)
        self.assertNotIn("malloc(", begin)
        self.assertNotIn("ensureServeBuffer(", begin)
        service = method(context, "inline void ota_service_temp_radio_context(")
        with tempfile.TemporaryDirectory(prefix="ota-self-lazy-") as directory:
            path = Path(directory)
            (path / "service.h").write_text(service)
            source = path / "test.cpp"
            source.write_text(r'''
#include <cassert>
#include <cstddef>
static unsigned acquisitions = 0, releases = 0;
static bool ota_acquire_context(char*, size_t) { ++acquisitions; return false; }
static void ota_release_context_if_idle(bool temporary_radio_active) {
  assert(!temporary_radio_active); ++releases;
}
#include "service.h"
int main() {
  ota_service_temp_radio_context(false);
  assert(acquisitions == 0 && releases == 1);
  ota_service_temp_radio_context(true);
  assert(acquisitions == 1 && releases == 1); // failed acquisition is soft
  ota_service_temp_radio_context(false);
  assert(acquisitions == 1 && releases == 2);
}
''')
            self.compile_and_run(path, source)
        for name in ("simple_repeater/MyMesh.cpp", "simple_room_server/MyMesh.cpp",
                     "simple_sensor/SensorMesh.cpp"):
            with self.subTest(role=name):
                role = (ROOT / "examples" / name).read_text()
                self.assertIn("#if defined(ENABLE_OTA) && OTA_DYNAMIC_CONTEXT\n"
                              "  mesh::ota::ota_service_temp_radio_context(isAnyTempRadioActive());",
                              role)

    def test_automatic_self_init_is_gated_after_temp_radio_admission(self):
        mesh = (ROOT / "src/Mesh.cpp").read_text()
        auto_serve = "if (oc.self_serve_supported && !oc.serving)"
        self.assertIn(auto_serve, mesh)
        active = mesh.index("const bool ota_active = isAnyTempRadioActive();")
        inactive = method(mesh[active:], "if (!ota_active)")
        self.assertIn("return;", inactive)
        self.assertLess(active + len(inactive), mesh.index(auto_serve))
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        for signature in ('else if (is_cmd(a, "announce|adv", &rest))',
                          'else if (is_cmd(a, "folder|fold", &rest))'):
            with self.subTest(command=signature):
                branch = method(cli, signature)
                self.assertIn("c.self_serve_supported && !c.serving", branch)
                self.assertIn("ota_serve_self(c, 0)", branch)
        # Internal-only devices retain the explicit delta-base export diagnostic;
        # Companion staging/export remains covered by its existing seeder guard.
        dev = method(cli, 'else if (strncmp(d, "serve self", 10) == 0)')
        self.assertIn("ota_serve_self(c, 0)", dev)
        self.assertNotIn("self_serve_supported", dev)

    def test_public_serve_command_checks_running_manifest_and_reports_failures(self):
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        match = method(cli, "static bool is_cmd(")
        serve = method(cli, 'else if (is_cmd(a, "serve", &rest))').removeprefix("else ")
        announce = method(cli, 'else if (is_cmd(a, "announce|adv", &rest))').removeprefix("else ")
        with tempfile.TemporaryDirectory(prefix="ota-self-command-") as directory:
            path = Path(directory)
            (path / "command.h").write_text(
                match + '\nstatic bool command(const char* a, char* reply, Context& c) {\n'
                'const char* rest = a;\n' + serve + '\n' + announce +
                '\nreturn true;\n}\n')
            source = path / "test.cpp"
            source.write_text(r'''
#include <helpers/ota/OtaByteIO.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
using namespace mesh::ota;
namespace mesh { struct Utils {
  static void toHex(char* out, const uint8_t* bytes, size_t count) {
    static const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < count; ++i) {
      out[i * 2] = hex[bytes[i] >> 4]; out[i * 2 + 1] = hex[bytes[i] & 15];
    }
    out[count * 2] = 0;
  }
}; }
struct OtaManager {
  struct ServeEntry { bool is_self = false; } entries[3];
  uint8_t count = 0;
  unsigned announcements = 0;
  const uint8_t* primary_manifest = nullptr;
  uint8_t servedCount() const { return count; }
  const ServeEntry* servedEntry(uint8_t index) const {
    return index < count ? entries + index : nullptr;
  }
  bool servingPrimaryManifest(const uint8_t* manifest) const {
    return primary_manifest && primary_manifest == manifest;
  }
  void announce() { ++announcements; }
};
struct Context {
  OtaManager manager;
  bool self_serve_supported = false, serving = false;
  uint8_t serve_self_manifest[197] = {};
  // Source publication must not modify automatic installation or trust policy.
  uint8_t autoinstall = 0, autofetch = 0, signer_count = 1;
};
static unsigned self_calls = 0;
static bool self_ok = true;
static bool ota_serve_self(Context& c, uint32_t) {
  ++self_calls;
  if (!self_ok) return false;
  c.serving = true;
  c.manager.count = 1; c.manager.entries[0].is_self = true;
  c.manager.primary_manifest = c.serve_self_manifest;
  wr_u32le(c.serve_self_manifest + 11, 3000);
  wr_u32le(c.serve_self_manifest + 20, 0x78563412);
  return true;
}
#include "command.h"
int main() {
  Context c;
  char reply[160] = {}, plain[160] = {};
  auto call = [&](const char* text) {
    assert(command(text, reply, c));
    assert(c.autoinstall == 0 && c.autofetch == 0 && c.signer_count == 1);
  };
  call("serve");
  assert(strstr(reply, "default:off serving:off"));
  assert(self_calls == 0 && c.manager.announcements == 0);
  strcpy(plain, reply); call("serve status"); assert(!strcmp(plain, reply));
  call("serve self");
  assert(!strncmp(reply, "ERR", 3) && self_calls == 0);
  c.self_serve_supported = true;
  c.serving = true; c.manager.count = 2; c.manager.entries[0].is_self = true;
  uint8_t manually_staged_manifest[197] = {};
  c.manager.primary_manifest = manually_staged_manifest;
  call("serve status");
  assert(strstr(reply, "default:on serving:off")); // RAM primary is not own fw
  c.serving = false; c.manager.primary_manifest = c.serve_self_manifest;
  call("serve status");
  assert(strstr(reply, "default:on serving:on")); // exact own manifest pointer
  c.manager.count = 0; c.manager.primary_manifest = nullptr;
  for (const char* bad : {"serve self extra", "serve selfx", "serve on", "serve status x"}) {
    call(bad);
    assert(!strncmp(reply, "ERR usage:", 10));
    assert(self_calls == 0 && c.manager.announcements == 0);
  }
  self_ok = false;
  call("serve self");
  assert(!strncmp(reply, "ERR", 3) && self_calls == 1);
  assert(c.manager.announcements == 0);
  self_ok = true;
  call("serve self");
  assert(!strncmp(reply, "OK", 2) && self_calls == 2 && c.serving);
  assert(strstr(reply, "mid=12345678") && strstr(reply, "3000 B"));
  assert(strstr(reply, "flash-backed") && strstr(reply, "unsigned"));
  assert(strstr(reply, "TempRadio") && c.manager.announcements == 1);
  unsigned calls = self_calls;
  call("serve status");
  assert(strstr(reply, "serving:on") && self_calls == calls);

  // An explicit announcement is still allowed on ineligible devices, but it
  // must not implicitly initialize a full-image source there.
  c.self_serve_supported = false; c.serving = false; c.manager.count = 0;
  call("announce");
  assert(self_calls == calls && c.manager.announcements == 2);
  c.self_serve_supported = true;
  call("adv");
#if defined(OTA_SEEDER_ONLY)
  assert(self_calls == calls && !c.serving);
#else
  assert(self_calls == calls + 1 && c.serving);
#endif
  assert(c.manager.announcements == 3);
}
''')
            for defines in ((), ("OTA_SEEDER_ONLY=1",)):
                with self.subTest(defines=defines):
                    self.compile_and_run(path, source, defines)

    def test_real_manager_distinguishes_ram_primary_from_running_image(self):
        with tempfile.TemporaryDirectory(prefix="ota-self-primary-") as directory:
            path = Path(directory)
            source = path / "test.cpp"
            (path / "vectors.h").write_text('#include "' +
                (ROOT / "test/test_ota/mota_vectors.h").as_posix() + '"\n')
            source.write_text(r'''
#include <helpers/ota/OtaContext.h>
#include <cassert>
#include <cstring>
#include "vectors.h"
using namespace mesh::ota;
namespace mesh { namespace ota {
bool ota_self_firmware(SelfFwInfo& info) { info = SelfFwInfo(); return false; }
} }
static bool send(void*, const uint8_t*, uint16_t, bool) { return true; }
static bool read(void*, uint32_t, uint8_t*, uint32_t) { return true; }
int main() {
  OtaManager manager;
  manager.begin(EXP_TARGET_ID, send, nullptr);
  MotaManifest parsed;
  assert(mota_parse(MOTA_VEC, MOTA_VEC_LEN, parsed));
  uint8_t own_manifest[MOTA_MFL], proof[4096];
  memcpy(own_manifest, parsed.manifest_start, sizeof(own_manifest));
  assert(!manager.servingPrimaryManifest(own_manifest));
  assert(manager.serve(MOTA_VEC, MOTA_VEC_LEN));
  assert(manager.servedCount() == 1 && manager.servedEntry(0)->is_self);
  // Both primaries have identical manifest bytes and is_self, but only one
  // points into the context's own running-image manifest buffer.
  assert(manager.servingPrimaryManifest(parsed.manifest_start));
  assert(!manager.servingPrimaryManifest(own_manifest));
  assert(manager.serve_self(own_manifest, sizeof(own_manifest), parsed.leaves,
                            parsed.block_count, proof, sizeof(proof), read, nullptr));
  assert(manager.servedCount() == 1 && manager.servedEntry(0)->is_self);
  assert(manager.servingPrimaryManifest(own_manifest));
  assert(!manager.servingPrimaryManifest(parsed.manifest_start));
  manager.clear_primary();
  assert(!manager.servingPrimaryManifest(own_manifest));
  assert(!manager.servingPrimaryManifest(parsed.manifest_start));
}
''')
            # This helper compiles the real manager/container/context into a
            # native fixture, never invokes PlatformIO or hardware interfaces.
            heap_test.OtaHeapTest().compile_and_run(path, source)


if __name__ == "__main__":
    unittest.main()
