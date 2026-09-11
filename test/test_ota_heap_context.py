#!/usr/bin/env python3
"""Run the real heap-backed OTA context and its lifetime boundaries on host."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]


class OtaHeapTest(unittest.TestCase):
    def test_build_policy_only_selects_classic_esp32_and_respects_storage_owner(self):
        class Env(dict):
            def BoardConfig(self):
                return {"build.mcu": self["mcu"]}

            def Append(self, **values):
                for key, value in values.items():
                    self.setdefault(key, []).extend(value)

        script = (ROOT / "scripts/esp32_ota_heap_context.py").read_text()
        for mcu in ("esp32", "esp32s3", "esp32s2", "esp32c3", "nrf52840"):
            env = Env(mcu=mcu)
            exec(script, {"env": env, "Import": lambda _: None})
            self.assertEqual(env.get("CPPDEFINES", []),
                             [("OTA_HEAP_CONTEXT", 1)] if mcu == "esp32" else [])
        for flags in ("-DOTA_SHARED_COMPANION_QUEUE=1", ["-D", "OTA_SHARED_COMPANION_QUEUE=1"],
                      ["-DOTA_HEAP_CONTEXT=1"]):
            env = Env(mcu="esp32", BUILD_FLAGS=flags)
            exec(script, {"env": env, "Import": lambda _: None})
            self.assertNotIn("CPPDEFINES", env)
        env = Env(mcu="esp32", CPPDEFINES=[("OTA_SHARED_COMPANION_QUEUE", 1)])
        exec(script, {"env": env, "Import": lambda _: None})
        self.assertEqual(env["CPPDEFINES"], [("OTA_SHARED_COMPANION_QUEUE", 1)])

    def test_context_releases_self_serve_but_preserves_live_operations(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            source = path / "test.cpp"
            absent_context = method((ROOT / "src/Mesh.cpp").read_text(),
                                    "if (!ota::ota_context_if_active())")
            # Exercise the production absence guard with install support enabled,
            # independently of the source-only storage used by this host fixture.
            absent_context = absent_context.replace("#if !defined(OTA_SEEDER_ONLY)", "#if 1")
            (path / "mesh_absence.h").write_text(
                "namespace ota = mesh::ota;\nstruct MeshMaintenance {\n"
                "bool _ota_temp_was_active = true, _ota_resumed = true, _ota_autoinstall_tried = true;\n"
                "void service() {\n" + absent_context + "\n}\n};\n")
            source.write_text(r'''
#include <helpers/ota/OtaContext.h>
#include <cassert>
#include <new>
#include "mesh_absence.h"
using namespace mesh::ota;
static bool fail_allocation = false;
void* operator new(std::size_t size, const std::nothrow_t&) noexcept {
  return fail_allocation ? nullptr : ::operator new(size);
}
namespace mesh { namespace ota {
bool ota_self_firmware(SelfFwInfo& info) { info = SelfFwInfo(); return false; }
} }
static bool send(void*, const uint8_t*, uint16_t, bool) { return true; }
int main() {
  MeshMaintenance maintenance;
  maintenance.service();
  assert(!maintenance._ota_temp_was_active && !maintenance._ota_resumed
         && !maintenance._ota_autoinstall_tried);
  char reply[160] = {};
  assert(!ota_acquire_context(reply, sizeof(reply)));
  ota_begin_context(123, send, nullptr, "test", nullptr);
  fail_allocation = true;
  assert(!ota_acquire_context(reply, sizeof(reply)));
  assert(strstr(reply, "out of memory") && !ota_context_if_active());
  fail_allocation = false;
  for (int cycle = 0; cycle < 16; ++cycle) {
    ota_service_temp_radio_context(true);
    assert(ota_context_if_active());
    auto& c = ota_ctx();
    c.manager.set_max_hops(7);
    c.autoinstall = OtaContext::AUTOINSTALL_TRUSTED;
    c.serving = true;
    c.serve_self_leaves = static_cast<uint8_t*>(malloc(64));
    c.serve_self_proof = static_cast<uint8_t*>(malloc(64));
    assert(c.ensureServeBuffer());
    ota_service_temp_radio_context(true);
    assert(ota_context_if_active() == &c);
    ota_service_temp_radio_context(false);
    assert(!ota_context_if_active());
    assert(ota_hop_limit() == 7);
  }
  assert(ota_acquire_context(reply, sizeof(reply)));
  assert(ota_ctx().autoinstall == OtaContext::AUTOINSTALL_TRUSTED);
  assert(ota_ctx().manager.max_hops() == 7);
  ota_ctx().apply_pending = true;
  ota_service_temp_radio_context(false);
  assert(ota_context_if_active());
  ota_ctx().apply_pending = false;
  ota_ctx().folder_active = true;
  ota_service_temp_radio_context(false);
  assert(ota_context_if_active());
  ota_ctx().folder_active = false;
  ota_ctx().folder_dest = reinterpret_cast<FolderMotaStore*>(1);
  ota_service_temp_radio_context(false);
  assert(ota_context_if_active());
  ota_ctx().folder_dest = nullptr;
  // Manual staging is a series of CLI commands, even outside TempRadio.
  ota_ctx().serve_expected = 100;
  assert(ota_ctx().ensureServeBuffer());
  ota_ctx().serve_buf[0] = 0x42;
  ota_service_temp_radio_context(false);
  assert(ota_context_if_active() && ota_ctx().serve_buf[0] == 0x42);
  ota_ctx().serve_expected = 0;
  ota_service_temp_radio_context(false);
  assert(!ota_context_if_active());
}
''')
            flags = [] if os.name == "nt" else ["-fsanitize=address,undefined"]
            tinf = path / "tinf.o"
            subprocess.run([shutil.which("cc") or "gcc", "-DENABLE_OTA=1", *flags, "-c",
                            str(ROOT / "src/helpers/ota/OtaTinf.c"), "-o", str(tinf)], check=True)
            sources = ["OtaContext.cpp", "OtaManager.cpp", "OtaProtocol.cpp",
                       "MotaContainer.cpp", "MerkleTree.cpp", "OtaDeflate.cpp"]
            binary = path / "heap.exe"
            result = subprocess.run([
                "c++", "-std=c++17", *flags, "-DENABLE_OTA=1",
                "-DOTA_HEAP_CONTEXT=1", "-DESP32_PLATFORM=1", "-DOTA_SEEDER_ONLY=1",
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                str(source), *[str(ROOT / "src/helpers/ota" / name) for name in sources],
                str(ROOT / "src/Utils.cpp"), str(tinf), "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
