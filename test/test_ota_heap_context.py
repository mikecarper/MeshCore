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
            self.compile_and_run(path, source)

    def test_disconnected_folder_capture_retains_selected_session(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            source = path / "test.cpp"
            (path / "vectors.h").write_text('#include "' +
                (ROOT / "test/test_ota/mota_vectors.h").as_posix() + '"\n')
            (path / "tcp_detach.h").write_text(method(
                (ROOT / "src/helpers/esp32/WiFiOtaSeeder.cpp").read_text(),
                "void detachTcpFolder("))
            source.write_text(r'''
#include <helpers/ota/OtaContext.h>
#include <cassert>
#include <vector>
#include <helpers/ota/FolderMotaStore.h>
#include "vectors.h"
using namespace mesh::ota;
namespace mesh { namespace ota {
bool ota_self_firmware(SelfFwInfo& info) { info = SelfFwInfo(); return false; }
} }
struct Message { OtaManager* dest; std::vector<uint8_t> bytes; };
static std::vector<Message> queue;
static bool send(void* ctx, const uint8_t* bytes, uint16_t len, bool) {
  queue.push_back({*static_cast<OtaManager**>(ctx), {bytes, bytes + len}});
  return true;
}
struct EmptySource : MotaSource {
  uint8_t count() override { return 0; }
  bool describe(uint8_t, MotaDesc&) override { return false; }
  bool read(uint8_t, uint32_t, uint8_t*, uint32_t) override { return false; }
};
static Stream stream;
struct MemoryFolder : FolderMotaStore {
  OtaStoreRam<4096> memory;
  bool fail_begin = false, fail_finalize = false;
  bool fail_root_read = false;
  bool corrupt_root_read = false;
  OtaManager* receiver = nullptr;
  uint32_t fail_write_at = UINT32_MAX;
  uint32_t fail_read_at = UINT32_MAX;
  MemoryFolder() : FolderMotaStore(stream, MotaStreamWritePolicy::NoFlush, 20) {}
  bool begin(uint32_t n) override { return !fail_begin && memory.begin(n); }
  bool write(uint32_t off, const uint8_t* bytes, uint32_t n) override {
    return off != fail_write_at && memory.write(off, bytes, n);
  }
  bool read(uint32_t off, uint8_t* bytes, uint32_t n) const override {
    if (off == fail_read_at || (fail_root_read && receiver && receiver->blocksTotal()
        && receiver->blocksHave() == receiver->blocksTotal())) return false;
    if (!memory.read(off, bytes, n)) return false;
    if (corrupt_root_read && receiver && receiver->blocksTotal()
        && receiver->blocksHave() == receiver->blocksTotal() && n) bytes[0] ^= 1;
    return true;
  }
  uint32_t staged_size() const override { return memory.staged_size(); }
  bool reopen() override { return memory.reopen(); }
  bool finalize() override { return !fail_finalize; }
};
static bool tcp_folder_attached = true;
#include "tcp_detach.h"
int main(int argc, char** argv) {
  const int scenario = argc > 1 ? atoi(argv[1]) : 0;
  MemoryFolder store;
  EmptySource source;
  OtaManager server;
  OtaManager* server_ptr = &server;
  ota_begin_context(SIM_TARGET_ID, send, &server_ptr, "test", nullptr);
  assert(ota_acquire_context(nullptr, 0));
  OtaContext* context = ota_context_if_active();
  OtaManager* client_ptr = &context->manager;
  store.receiver = client_ptr;
  server.begin(0, send, &client_ptr);
  assert(server.serve(SIM_MOTA_1K, SIM_MOTA_1K_LEN));
  MotaManifest manifest;
  assert(mota_parse(SIM_MOTA_1K, SIM_MOTA_1K_LEN, manifest));
  uint8_t mid[4]; memcpy(mid, manifest.merkle_root, sizeof(mid));
  char reply[160] = {};
  assert(context->attach_folder_source(&source, OtaContext::FOLDER_LINK_TCP,
                                       "tcp", reply, sizeof(reply)));
  context->manager.set_fetch_store(&store);
  context->fetch_to_folder = true;
  context->set_folder_dest(&store, "tcp");
  store.fail_begin = scenario == 2;
  store.fail_finalize = scenario == 5;
  store.fail_root_read = scenario == 10;
  store.corrupt_root_read = scenario == 14;
  if (scenario == 3) store.fail_write_at = 8;
  if (scenario == 4) store.fail_write_at = manifest.payload - SIM_MOTA_1K;
  if (scenario == 13) store.fail_read_at = manifest.payload - SIM_MOTA_1K;
  assert(context->manager.pull_archive(mid, SIM_TARGET_ID, scenario == 9 || scenario == 12 || scenario == 13)
         == OtaManager::PULL_STARTED);
  uint32_t now = 0;
  auto tick = [&]() {
    if (!queue.empty()) {
      Message msg = queue.front(); queue.erase(queue.begin());
      msg.dest->on_message(msg.bytes.data(), msg.bytes.size());
    } else {
      now += 1000;
      context->manager.set_clock(now); server.set_clock(now);
      context->manager.loop(); server.loop();
    }
    context->manager.serviceEgress(); server.serviceEgress();
  };
  if ((scenario >= 1 && scenario <= 6) || scenario >= 9) {
    unsigned guard = 100000;
    while (guard--) {
      tick();
      if (scenario == 14) {
        if (context->manager.fetchState() == OtaManager::FAILED) break;
      } else if ((scenario >= 2 && scenario <= 5) || scenario == 10 || scenario == 13) {
        if (context->manager.fetchState() == OtaManager::PAUSED) break;
        assert(context->manager.fetchState() != OtaManager::FAILED);
      } else if (scenario == 9 || scenario == 12) {
        if (context->manager.fetchState() == OtaManager::WANT_LEAVES) break;
      } else if (context->manager.blocksHave() >= 1) break;
    }
    assert(guard != UINT32_MAX);
  }
  if (scenario == 14) {
    assert(context->manager.fetchError() == OtaManager::FETCH_ERROR_INTEGRITY);
    detachTcpFolder(true);
    ota_release_context_if_idle(false);
    assert(!ota_context_if_active());
    return 0;
  }
  // Execute the real TCP loss cleanup, then the normal role lifetime service.
  detachTcpFolder(true);
  assert(context->manager.fetchState() == OtaManager::PAUSED);
  ota_release_context_if_idle(false);
  assert(ota_context_if_active() == context);
  ota_release_context_if_idle(true);
  assert(ota_context_if_active() == context);
  assert(memcmp(context->manager.fetchManifestId(), mid, sizeof(mid)) == 0);
  queue.clear();
  const auto sent = context->manager.packetsSent();
  for (unsigned i = 0; i < 100; ++i) context->manager.loop();
  assert(context->manager.packetsSent() == sent);
  assert(!context->attach_folder_source(&source, OtaContext::FOLDER_LINK_BLE,
                                        "ble", reply, sizeof(reply)));
  if (scenario == 7 || scenario == 8) {
    if (scenario == 7) context->manager.reset_session();
    else {
      context->detach_folder();
      // Static-context builds also need an idle slot after explicit detach.
      assert(context->manager.fetchState() == OtaManager::IDLE);
      assert(!context->fetch_to_folder && !context->folder_dest);
    }
    ota_release_context_if_idle(false);
    assert(!ota_context_if_active());
    return 0;
  }
  store.fail_begin = store.fail_finalize = false;
  store.fail_root_read = false;
  store.fail_write_at = UINT32_MAX;
  store.fail_read_at = UINT32_MAX;
  assert(context->attach_folder_source(&source, OtaContext::FOLDER_LINK_TCP,
                                       "tcp", reply, sizeof(reply)));
  if (scenario == 6) {
    // A different file at the host cannot change the interrupted selection.
    uint8_t wrong_mid[4] = {9, 8, 7, 6};
    assert(store.memory.write(8 + 20, wrong_mid, sizeof(wrong_mid)));
    context->set_folder_dest(&store, "tcp");
    assert(context->manager.fetchState() == OtaManager::PAUSED);
    assert(memcmp(context->manager.fetchManifestId(), mid, sizeof(mid)) == 0);
    assert(store.memory.write(8 + 20, mid, sizeof(mid)));
  }
  if (scenario == 12) {
    uint8_t wrong_mid[4] = {9, 8, 7, 6};
    assert(store.memory.write(8 + 20, wrong_mid, sizeof(wrong_mid)));
  }
  context->set_folder_dest(&store, "tcp");
  if (scenario == 11) {
    store.fail_read_at = manifest.payload - SIM_MOTA_1K;
    context->manager.loop();
    assert(context->manager.fetchState() == OtaManager::PAUSED);
    context->disconnect_folder();
    ota_release_context_if_idle(false);
    assert(ota_context_if_active() == context);
    store.fail_read_at = UINT32_MAX;
    assert(context->attach_folder_source(&source, OtaContext::FOLDER_LINK_TCP,
                                         "tcp", reply, sizeof(reply)));
    context->set_folder_dest(&store, "tcp");
  }
  unsigned guard = 100000;
  while (context->manager.fetchState() != OtaManager::COMPLETE && guard--) {
    tick();
    assert(context->manager.fetchState() != OtaManager::FAILED);
  }
  assert(context->manager.fetchState() == OtaManager::COMPLETE);
  assert(store.staged_size() == SIM_MOTA_1K_LEN);
  assert(memcmp(store.memory.data(), SIM_MOTA_1K, SIM_MOTA_1K_LEN) == 0);
  context->detach_folder(); context->clear_folder_dest();
  ota_release_context_if_idle(false);
  assert(!ota_context_if_active());
}
''')
            self.compile_and_run(path, source, [(str(i),) for i in range(15)])

    def test_self_refresh_revokes_view_before_freeing_buffers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            source = path / "test.cpp"
            (path / "self_serve.h").write_text(method(
                (ROOT / "src/helpers/ota/OtaSelf.cpp").read_text(),
                "bool ota_serve_self("))
            serve_command = method((ROOT / "src/helpers/ota/OtaCli.cpp").read_text(),
                                   'else if (strncmp(d, "serve", 5) == 0)')
            # Run the production admission/status path; the verification report
            # following it is unrelated to a rejected replacement.
            serve_command = serve_command.removeprefix("else ").split("VerifyResult r", 1)[0]
            (path / "serve_command.h").write_text(
                'static bool serve_command(OtaContext& c, char* reply) {\n'
                'const char* d = "serve";\n' + serve_command + '} return true; }\n')
            source.write_text(r'''
#include <helpers/ota/OtaContext.h>
#include <helpers/ota/OtaByteIO.h>
#include <SHA256.h>
#include <cassert>
#define OTA_SELF_LEAVES_MAX 65536u
using namespace mesh::ota;
static int allocation_countdown = -1;
static bool fail_read = false;
static void* self_malloc(size_t bytes) {
  if (allocation_countdown == 0) return nullptr;
  if (allocation_countdown > 0) --allocation_countdown;
  return malloc(bytes);
}
namespace mesh { namespace ota {
bool ota_self_firmware(SelfFwInfo& info) {
  info = SelfFwInfo(); info.valid = true; info.image_len = 3000;
  info.target_id = 123; info.fw_version = 1;
  return true;
}
bool ota_self_read(uint32_t off, uint8_t* buf, uint32_t len) {
  if (fail_read) return false;
  for (uint32_t i = 0; i < len; ++i) buf[i] = (uint8_t)(off + i);
  return true;
}
static bool self_read_cb(void*, uint32_t off, uint8_t* buf, uint32_t len) {
  return ota_self_read(off, buf, len);
}
#define malloc self_malloc
#include "self_serve.h"
#undef malloc
} }
static bool send(void*, const uint8_t*, uint16_t, bool) { return true; }
#include "serve_command.h"
int main(int argc, char** argv) {
  OtaContext context;
  context.begin(123, send, nullptr, "test");
  assert(ota_serve_self(context, 1));
  context.serving = true;
  assert(context.manager.servedCount() == 1);
  // A rejected staged replacement must preserve the CLI's serving flag too.
  assert(context.ensureServeBuffer());
  context.serve_expected = 1;
  char reply[160] = {};
  assert(serve_command(context, reply));
  assert(strncmp(reply, "ERR serve", 9) == 0);
  assert(context.serving && context.manager.servedCount() == 1);
  context.serve_expected = 0;
  GetManifestMsg manifest{};
  ReqMsg block{};
  memcpy(manifest.manifest_id, context.serve_self_manifest + 20, 4);
  memcpy(block.manifest_id, manifest.manifest_id, 4);
  manifest.want_mask = block.want_mask = 0xFFFF;
  uint8_t wire[MAX_PACKET_PAYLOAD];
  auto length = encode_get_manifest(wire, sizeof(wire), manifest);
  assert(context.manager.on_message(wire, length));
  length = encode_req(wire, sizeof(wire), block);
  assert(context.manager.on_message(wire, length));
  assert(context.manager.pendingManifestJobs() == 1);
  assert(context.manager.pendingServeJobs() == 1);

  const int failure = argc > 1 ? atoi(argv[1]) : 0;
  allocation_countdown = failure < 2 ? failure : -1;
  fail_read = failure == 2;
  assert(!ota_serve_self(context, 2));
  assert(context.manager.servedCount() == 0);
  assert(!context.serving);
  assert(!context.serve_self_leaves && !context.serve_self_proof);
  assert(context.manager.pendingManifestJobs() == 0);
  assert(context.manager.pendingServeJobs() == 0);
  context.manager.serviceEgress();
  assert(!context.manager.on_message(wire, length));

  // Reconnect/retry is still possible after either allocation or flash-read failure.
  allocation_countdown = -1; fail_read = false;
  assert(ota_serve_self(context, 3));
  assert(context.manager.servedCount() == 1);
  assert(context.serving);
}
''')
            self.compile_and_run(path, source, [("0",), ("1",), ("2",)])

    def compile_and_run(self, path, source, scenarios=((),)):
        flags = [] if os.name == "nt" else ["-fsanitize=address,undefined"]
        tinf = path / "tinf.o"
        subprocess.run([shutil.which("cc") or "gcc", "-DENABLE_OTA=1", *flags, "-c",
                        str(ROOT / "src/helpers/ota/OtaTinf.c"), "-o", str(tinf)], check=True)
        sources = ["OtaContext.cpp", "OtaManager.cpp", "OtaProtocol.cpp", "FolderMotaStore.cpp",
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
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                subprocess.run([str(binary), *scenario], check=True)


if __name__ == "__main__":
    unittest.main()
