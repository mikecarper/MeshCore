#!/usr/bin/env python3
"""Exercise production OTA identity refresh and deferred persistent policy load."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/ota/OtaContext.h>
#include <helpers/ota/OtaProtocol.h>
#include <cassert>
#include <new>
#include <vector>
#include "vectors.h"
using namespace mesh::ota;
static bool allocation_fails = false;
static unsigned allocation_attempts = 0;
void* operator new(std::size_t size, const std::nothrow_t&) noexcept {
  ++allocation_attempts;
  return allocation_fails ? nullptr : ::operator new(size);
}
namespace mesh { namespace ota {
bool ota_self_firmware(SelfFwInfo& info) { info = SelfFwInfo(); return false; }
} }
static std::vector<uint8_t> wire;
static bool send(void*, const uint8_t* data, uint16_t length, bool) {
  wire.assign(data, data + length); return true;
}
static AdvMsg beacon(OtaManager& manager) {
  manager.announce();
  AdvMsg adv{};
  assert(decode_adv(wire.data(), wire.size(), adv));
  return adv;
}
#if defined(OTA_SHARED_COMPANION_QUEUE)
static OtaContext* borrowed_context = nullptr;
static OtaContext* acquire(void*) {
  borrowed_context = new (std::nothrow) OtaContext;
  return borrowed_context;
}
static void release(void*) { delete borrowed_context; borrowed_context = nullptr; }
#endif
struct NodePrefs {
  uint8_t ota_autofetch=2, ota_max_hops=7, ota_autoinstall=1, ota_signer_count=1;
  uint16_t ota_checkpoint_blocks=99, ota_advert_interval=180;
  uint8_t ota_signers[MAX_OTA_SIGNERS][32]={{42}};
};
struct CommonCLI {
  NodePrefs prefs;
  NodePrefs* _prefs = &prefs;
  void syncOtaConfigFromPrefs();
};
@SYNC@
struct TestIdentity { uint8_t pub_key[32] = {}; };
namespace mesh {
static bool generation_ok = true;
static unsigned generation_calls = 0;
static bool hasReservedIdentityPrefix(const TestIdentity& id) { return !id.pub_key[0]; }
static bool generateUsableLocalIdentity(TestIdentity& id, int) {
  ++generation_calls;
  id.pub_key[0] = 0x91; return generation_ok;
}
static void discardESP32TrueRandom() {}
}
struct Store {
  bool load_ok = true, can_create = true, save_ok = true;
  unsigned saves = 0;
  bool loadMainIdentity(TestIdentity& id) { id.pub_key[0] = load_ok ? 0x12 : 0; return load_ok; }
  bool canCreateMainIdentity() const { return can_create; }
  bool saveMainIdentity(const TestIdentity&) { ++saves; return save_ok; }
};
struct Companion {
  Store store;
  Store* _store = &store;
  TestIdentity self_id;
  struct Board { bool rebooted = false; void reboot() { rebooted = true; } } board;
  int radio_new_identity = 0;
  bool accepted = false;
  void loadIdentity() {
    @STARTUP@
    accepted = true;
  }
  bool ok_reply = false;
  void writeOKFrame() { ok_reply = true; }
  void importIdentity(const TestIdentity& identity) {
    @IMPORT@
  }
};
static void checkPolicy(const OtaContext& c, const NodePrefs& prefs) {
  assert(c.manager.max_hops() == prefs.ota_max_hops);
  assert(c.manager.checkpoint_blocks() == prefs.ota_checkpoint_blocks);
  assert(c.manager.advert_mins() == prefs.ota_advert_interval);
  assert(c.allow.count() == prefs.ota_signer_count);
  assert(c.allow.contains(prefs.ota_signers[0]));
#if defined(OTA_SEEDER_ONLY)
  assert(c.manager.autofetch() == 0 && c.autoinstall == 0);
#else
  assert(c.manager.autofetch() == prefs.ota_autofetch);
  assert(c.autoinstall == prefs.ota_autoinstall);
#endif
}
int main() {
#if defined(OTA_SHARED_COMPANION_QUEUE)
  ota_set_context_storage(nullptr, acquire, release);
#endif
  uint8_t initial_id[32] = {};
  ota_begin_context(SIM_TARGET_ID, send, nullptr, "test", initial_id);

  // Loaded, generated, and failed startup identities run the real Companion block.
  Companion loaded;
  loaded.loadIdentity();
  assert(loaded.accepted && !loaded.board.rebooted && loaded.store.saves == 0);
#if OTA_DYNAMIC_CONTEXT
  assert(!ota_context_if_active()); // an identity refresh must not claim storage
#endif
  assert(ota_acquire_context(nullptr, 0));
  assert(beacon(ota_ctx().manager).seeder_id[0] == 0x12);
  Companion generated;
  generated.store.load_ok = false;
  generated.loadIdentity();
  assert(generated.accepted && generated.store.saves == 1);
  assert(beacon(ota_ctx().manager).seeder_id[0] == 0x91);
  Companion blocked;
  blocked.store.load_ok = false; blocked.store.can_create = false;
  blocked.loadIdentity();
  assert(!blocked.accepted && blocked.board.rebooted && blocked.store.saves == 0);
  assert(beacon(ota_ctx().manager).seeder_id[0] == 0x91);
  Companion failed_save;
  failed_save.store.load_ok = false; failed_save.store.save_ok = false;
  failed_save.loadIdentity();
  assert(!failed_save.accepted && failed_save.board.rebooted);
  assert(beacon(ota_ctx().manager).seeder_id[0] == 0x91);

  // Changing identity must preserve current fetch progress and the served set.
  auto& manager = ota_ctx().manager;
  assert(manager.serve(SIM_MOTA_1K, SIM_MOTA_1K_LEN));
  MotaManifest manifest;
  assert(mota_parse(SIM_MOTA_1K, SIM_MOTA_1K_LEN, manifest));
  assert(manager.pull_archive(manifest.merkle_root, SIM_TARGET_ID) == OtaManager::PULL_STARTED);
  const auto prior_state = manager.fetchState();
  assert(prior_state != OtaManager::IDLE);
  TestIdentity imported; imported.pub_key[0] = 0x34;
  loaded.importIdentity(imported);
  assert(loaded.ok_reply && loaded.self_id.pub_key[0] == 0x34);
  assert(manager.fetchState() == prior_state && manager.servedCount() == 1);
  assert(beacon(manager).seeder_id[0] == 0x34);
  loaded.store.save_ok = false; loaded.ok_reply = false;
  imported.pub_key[0] = 0x56;
  loaded.importIdentity(imported);
  assert(!loaded.ok_reply && loaded.self_id.pub_key[0] == 0x34);
  assert(beacon(manager).seeder_id[0] == 0x34);
  ota_refresh_seeder_identity(nullptr);
  assert(beacon(manager).seeder_id[0] == 0x34);

  // Two actual identities retain separate discovery records.
  OtaManager receiver;
  receiver.begin(SIM_TARGET_ID, send, nullptr);
  AdvMsg adv = beacon(manager);
  adv.n_motas = 1;
  uint8_t packet[256];
  receiver.on_message(packet, encode_adv(packet, sizeof(packet), adv));
  generated.loadIdentity();
  adv = beacon(manager); adv.n_motas = 1;
  receiver.on_message(packet, encode_adv(packet, sizeof(packet), adv));
  assert(receiver.sourceCount() == 2);
  manager.reset_session();
#if OTA_DYNAMIC_CONTEXT
  ota_release_context_if_idle(false);
  assert(!ota_context_if_active());
  assert(ota_acquire_context(nullptr, 0));
  assert(beacon(ota_ctx().manager).seeder_id[0] == 0x91);
  ota_release_context_if_idle(false);
#endif

  // Persisted policy registration works while allocation is impossible, and
  // policy/keys remain available on every later successful workspace claim.
  CommonCLI cli;
  allocation_fails = true;
  const auto attempts = allocation_attempts;
  cli.syncOtaConfigFromPrefs();
  assert(allocation_attempts == attempts);
  assert(ota_hop_limit() == cli.prefs.ota_max_hops);
#if OTA_DYNAMIC_CONTEXT
  assert(!ota_context_if_active());
  assert(!ota_acquire_context(nullptr, 0));
  assert(!ota_context_if_active());
#endif
  allocation_fails = false;
  for (unsigned cycle = 0; cycle < 3; ++cycle) {
    assert(ota_acquire_context(nullptr, 0));
    checkPolicy(ota_ctx(), cli.prefs);
#if OTA_DYNAMIC_CONTEXT
    ota_release_context_if_idle(false);
    assert(!ota_context_if_active());
    ++cli.prefs.ota_advert_interval; // a later saved value, no stale startup copy
#endif
  }
  // Re-registering against an active session refreshes policy without reset.
  assert(ota_acquire_context(nullptr, 0));
  assert(ota_ctx().manager.pull_archive(manifest.merkle_root, SIM_TARGET_ID)
         == OtaManager::PULL_STARTED);
  auto state = ota_ctx().manager.fetchState();
  cli.prefs.ota_max_hops = 2;
  cli.syncOtaConfigFromPrefs();
  assert(ota_ctx().manager.fetchState() == state);
  checkPolicy(ota_ctx(), cli.prefs);
  ota_ctx().manager.reset_session();
#if OTA_DYNAMIC_CONTEXT
  ota_release_context_if_idle(false);
#endif
  ota_set_context_config_loader(nullptr);
}
'''


class OtaIdentityPolicyTest(unittest.TestCase):
    def test_static_heap_and_borrowed_contexts_preserve_identity_and_policy(self):
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        begin = extract_braced(companion, "void MyMesh::begin(")
        startup = begin[begin.index("const bool identity_loaded"):
                        begin.index("// if name is provided")]
        self.assertLess(begin.index("BaseChatMesh::begin()"),
                        begin.index("ota_refresh_seeder_identity(self_id.pub_key)"))
        imported = companion[companion.index("cmd_frame[0] == CMD_IMPORT_PRIVATE_KEY"):]
        # Execute the real publication prefix. Contact-cache refresh after its
        # acknowledgement is unrelated to the OTA identity being checked here.
        imported = extract_braced(imported, "if (_store->saveMainIdentity(identity))")
        imported = imported[:imported.index("// re-load contacts")] + "}\n"
        sync = extract_braced((ROOT / "src/helpers/CommonCLI.cpp").read_text(),
                              "void CommonCLI::syncOtaConfigFromPrefs()")
        source_text = HARNESS.replace("@SYNC@", sync).replace(
            "@STARTUP@", startup).replace("@IMPORT@", imported)
        with tempfile.TemporaryDirectory(prefix="ota-identity-policy-") as directory:
            path = Path(directory)
            source = path / "test.cpp"
            source.write_text(source_text)
            (path / "vectors.h").write_text('#include "' +
                (ROOT / "test/test_ota/mota_vectors.h").as_posix() + '"\n')
            sanitizers = [] if os.name == "nt" else ["-fsanitize=address,undefined"]
            tinf = path / "tinf.o"
            subprocess.run([shutil.which("cc") or "gcc", "-DENABLE_OTA=1",
                            *sanitizers, "-c", str(ROOT / "src/helpers/ota/OtaTinf.c"),
                            "-o", str(tinf)], check=True)
            for mode in ("static", "heap", "shared"):
                for seeder in (False, True):
                    if mode == "shared" and not seeder:
                        continue  # borrowing the queue is source-only by design
                    with self.subTest(mode=mode, seeder=seeder):
                        # Production installers use a flash-backed fetch store.
                        # A small RAM stand-in avoids platform flash APIs while
                        # keeping the real heap-context budget assertion valid.
                        flags = ["-DENABLE_OTA=1", "-DESP32_PLATFORM=1",
                                 "-DOTA_FETCH_BUF_SIZE=4096"]
                        if mode == "heap":
                            flags += ["-DOTA_HEAP_CONTEXT=1"]
                        if mode == "shared":
                            flags += ["-DOTA_SHARED_COMPANION_QUEUE=1",
                                      "-DCOMPANION_RADIO_FULL=1"]
                        if seeder:
                            flags += ["-DOTA_SEEDER_ONLY=1"]
                        binary = path / "identity-policy.exe"
                        sources = ["OtaContext.cpp", "OtaManager.cpp", "OtaProtocol.cpp",
                                   "MotaContainer.cpp", "MerkleTree.cpp", "OtaDeflate.cpp"]
                        result = subprocess.run([
                            "c++", "-std=c++17", *sanitizers, *flags,
                            "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                            str(source),
                            *[str(ROOT / "src/helpers/ota" / name) for name in sources],
                            str(ROOT / "src/Utils.cpp"), str(tinf), "-o", str(binary),
                        ], capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
