"""Run production OTA policy parsing, Companion commit/rollback, and filesystem storage."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
import test_ota_heap_context as heap_test

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cerrno>
#include <sys/stat.h>
#include <helpers/CLICommandUtils.h>
#include <helpers/ota/CompanionOtaConfig.h>
static MemoryFS* mounted_fs=nullptr;
#if defined(ESP32_PLATFORM)
extern "C" int stat(const char* path, struct stat*) noexcept {
  assert(mounted_fs && !strncmp(path, "/spiffs", 7));
  if (mounted_fs->stat_error) { errno=EIO; return -1; }
  if (!mounted_fs->files.count(path+7)) { errno=ENOENT; return -1; }
  return 0;
}
#endif
namespace mesh { namespace ota {
struct Manager {
  uint8_t af=0, hops=3; uint16_t checkpoint=4, advert=1440;
  uint8_t autofetch() const { return af; }
  uint8_t max_hops() const { return hops; }
  uint16_t checkpoint_blocks() const { return checkpoint; }
  uint16_t advert_mins() const { return advert; }
  void set_autofetch(uint8_t v) { af=v; }
  void set_max_hops(uint8_t v) { hops=v; }
  void set_checkpoint_blocks(uint16_t v) { checkpoint=v; }
  void set_advert_mins(uint16_t v) { advert=v; }
};
struct OtaContext {
  static constexpr uint8_t AUTOINSTALL_OFF=0, AUTOINSTALL_TRUSTED=1;
  Manager manager; SignerAllowlist allow;
  uint8_t autoinstall=0; bool config_dirty=false;
};
static OtaContext context;
static bool acquire_ok=true;
static bool ota_acquire_context(char* reply, size_t cap) {
  if (!acquire_ok) snprintf(reply, cap, "ERR unavailable");
  return acquire_ok;
}
static OtaContext& ota_ctx() { return context; }
static OtaContext* ota_context_if_active() { return &context; }
static void formatSpeed(char* text, size_t) { strcpy(text, "1"); }
@IS_CMD@
static bool config(const char* rest, char* reply, OtaContext& c) {
  @CONFIG@
  return true;
}
static bool handle_ota_command(const char* command, char* reply, int) {
  if (!strcmp(command, "ota key add test-key")) {
    uint8_t key[32]={93};
    context.allow.add(key); context.config_dirty=true;
    strcpy(reply, "OK key added (saved)");
    return true;
  }
  const char* rest;
  if (!is_cmd(command + 4, "config|cfg|set", &rest)) return false;
  return config(rest, reply, context);
}
} }
static bool companion(const char* command, char* reply, size_t reply_size) {
  int board=0;
  @WRAPPER@
  return false;
}
using namespace mesh::ota;
struct Common {
  struct Prefs {
    uint8_t ota_autofetch=0, ota_autoinstall=0, ota_max_hops=3, ota_signer_count=0;
    uint16_t ota_checkpoint_blocks=4, ota_advert_interval=1440;
    uint8_t ota_signers[MAX_OTA_SIGNERS][32]={};
  } prefs;
  Prefs* _prefs=&prefs;
  struct Callbacks { bool isTempRadioActive() const { return true; } } callbacks;
  Callbacks* _callbacks=&callbacks;
  struct Profiles { bool secondaryTemporary() const { return false; } } _radio_profiles;
  int board=0; int* _board=&board;
  bool save_ok=true; int saves=0;
  bool otaCommandNeedsTempRadio(const char*) const { return false; }
  bool saveCommonPrefs() { ++saves; return save_ok; }
  void run(const char* command, char* reply) { @COMMON@ }
};
static void reboot(MemoryFS& fs) {
  context=OtaContext();
  mounted_fs=&fs;
  beginCompanionOtaConfig(&fs);
  OtaConfigState loaded;
  if (loadCompanionOtaConfig(loaded)) loaded.apply(context);
}
int main() {
  MemoryFS fs;
  char reply[160];
  auto command = [&](const std::string& text, const char* expected) {
    memset(reply, 0, sizeof reply);
    assert(companion(text.c_str(), reply, sizeof reply));
    if (strncmp(reply, expected, strlen(expected)))
      fprintf(stderr, "%s: expected %s, got %s\n", text.c_str(), expected, reply);
    assert(!strncmp(reply, expected, strlen(expected)));
    assert(!context.config_dirty);
  };
  reboot(fs);
  for (const auto* setting : {"hops", "checkpoint", "advert"}) {
    const auto before=OtaConfigState::capture(context);
    for (const auto* bad : {"", " ", "x", "-1", "1x", "1 2", "1.0", "4294967296", "9999999999999999999999"}) {
      command(std::string("ota config ")+setting+" "+bad, "ERR");
      assert(context.manager.hops==before.hops && context.manager.checkpoint==before.checkpoint
          && context.manager.advert==before.advert && fs.files.empty());
    }
    command(std::string("ota config ")+setting, "ERR");
  }
  command("ota config hops 9", "ERR");
  command("ota config advert 10081", "ERR");
  command("ota config checkpoint 4097", "ERR");
  command("ota config unknown 7", "ERR");
  for (const auto* bad : {"", "anymore", "signedx", "off extra"})
    command(std::string("ota config autofetch ")+bad, "ERR");
  for (const auto* bad : {"", "trustedx", "off extra"})
    command(std::string("ota config autoinstall ")+bad, "ERR");
  assert(fs.files.empty());
#if defined(OTA_SEEDER_ONLY)
  command("ota config autofetch any", "ERR");
  command("ota config autoinstall trusted", "ERR");
#else
  command("ota config autofetch signed", "OK");
  command("ota config autoinstall trusted", "OK");
#endif
  command("ota config hops 8", "OK");
  command("ota cfg advert 10080", "OK");
  command("ota set checkpoint 4096", "OK");
  uint8_t key[32]={42};
  context.allow.add(key);
  assert(saveCompanionOtaConfig(OtaConfigState::capture(context)));
  reboot(fs);
  assert(context.manager.hops==8 && context.manager.advert==10080
      && context.manager.checkpoint==4096 && context.allow.contains(key));
#if defined(OTA_SEEDER_ONLY)
  assert(context.manager.af==0 && context.autoinstall==0);
#else
  assert(context.manager.af==2 && context.autoinstall==1);
#endif
  const auto good=fs.files["/ota_config"];
  fs.fail_write=true;
  command("ota config hops 2", "ERR OTA settings save failed; settings unchanged");
  assert(context.manager.hops==8 && fs.files["/ota_config"]==good);
  fs.fail_write=false;
  fs.fail_write_after=10;
  command("ota config hops 2", "ERR");
  fs.fail_write_after=-1;
  assert(context.manager.hops==8 && fs.files["/ota_config"]==good);
  fs.fail_rename=2;
  command("ota config hops 2", "ERR");
  assert(context.manager.hops==8 && fs.files["/ota_config"]==good);
  reboot(fs);
  assert(context.manager.hops==8 && context.allow.contains(key));
  fs.fail_rename_from={"/ota_config.tmp", "/ota_config.bak"};
  command("ota config hops 2", "ERR");
  assert(context.manager.hops==8 && fs.files["/ota_config.bak"]==good);
  command("ota config hops 2", "ERR"); // failed restoration is held
  fs.fail_rename_from.clear();
  reboot(fs);
  assert(context.manager.hops==8 && fs.files["/ota_config"]==good);
  fs.files["/ota_config.bak"]=good;
  fs.files.erase("/ota_config");
  reboot(fs); // reset between publication renames
  assert(context.manager.hops==8 && fs.files["/ota_config"]==good);
  fs.files["/ota_config.bak"]=good;
  fs.files.erase("/ota_config");
  fs.fail_rename=1;
  reboot(fs); // backup remains readable if repair is temporarily unavailable
  assert(context.manager.hops==8 && fs.files["/ota_config.bak"]==good);
  command("ota config hops 2", "ERR");
  reboot(fs);
  assert(context.manager.hops==8 && fs.files["/ota_config"]==good);
  fs.fail_read_open=true;
  assert(!fs.exists("/ota_config")); // model ESP32's open-based exists()
  command("ota config hops 2", "ERR");
  fs.fail_read_open=false;
  command("ota config hops 2", "ERR"); // held until a clean reload
  assert(context.manager.hops==8 && fs.files["/ota_config"]==good);
  reboot(fs);
  fs.stat_error=true;
  command("ota config hops 2", "ERR");
  fs.stat_error=false;
  command("ota config hops 2", "ERR");
  assert(fs.files["/ota_config"]==good && context.manager.hops==8);
  reboot(fs);
  command("ota config hops 0", "OK");
  command("ota config advert 0", "OK");
  command("ota config checkpoint 0", "OK");
  reboot(fs);
  assert(context.manager.hops==0 && context.manager.advert==0 && context.manager.checkpoint==0);
  fs.files["/ota_config"][4]^=1;
  const auto damaged=fs.files["/ota_config"];
  reboot(fs);
  command("ota config hops 7", "ERR");
  assert(fs.files["/ota_config"]==damaged);
  fs.files.clear(); reboot(fs);
  acquire_ok=false;
  command("ota config hops 7", "ERR unavailable");
  assert(fs.files.empty());

  acquire_ok=true; context=OtaContext();
  Common common;
  context.allow.add(key);
  common.run("ota config hops 7", reply);
  assert(!strncmp(reply, "OK", 2) && common.saves==1);
  assert(common.prefs.ota_max_hops==7 && common.prefs.ota_signer_count==1);
  assert(context.manager.hops==7 && !context.config_dirty);
  common.save_ok=false;
  common.run("ota config hops 2", reply);
  assert(!strncmp(reply, "ERR", 3) && common.saves==2);
  assert(common.prefs.ota_max_hops==7 && context.manager.hops==7 && !context.config_dirty);
  common.run("ota key add test-key", reply);
  assert(!strncmp(reply, "ERR", 3) && common.saves==3);
  assert(context.allow.count()==1 && common.prefs.ota_signer_count==1);
  assert(!memcmp(common.prefs.ota_signers[0], key, 32));
  const uint8_t zero[32]={};
  assert(!memcmp(common.prefs.ota_signers[1], zero, 32));
  common.save_ok=true;
  common.run("ota key add test-key", reply);
  assert(!strncmp(reply, "OK", 2) && common.saves==4);
  assert(context.allow.count()==2 && common.prefs.ota_signer_count==2);
  common.run("ota config hops bad", reply);
  assert(!strncmp(reply, "ERR", 3) && common.saves==4);
}
'''


class CompanionOtaConfigTest(unittest.TestCase):
    def test_production_commands_reboot_and_transaction_failures(self):
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        config = extract_braced(cli, 'if (is_cmd(a, "config|cfg|set", &rest))')
        config = config[config.index("{") + 1:-1]
        wrapper = extract_braced(companion, 'if (strncmp(command, "ota", 3) == 0')
        common_wrapper = extract_braced(common, 'if (memcmp(command, "ota", 3) == 0')
        # The closing brace is shared with the ENABLE_OTA fallback branch.
        common_wrapper = common_wrapper.replace("#else", "")
        source = (HARNESS.replace("@IS_CMD@", extract_braced(cli, "static bool is_cmd("))
                  .replace("@CONFIG@", config).replace("@WRAPPER@", wrapper)
                  .replace("@COMMON@", common_wrapper))
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as directory:
            # Exercise production metadata probes independently from the
            # deliberately open-based exists() API in this faulting backend.
            mock = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text()
            mock = mock.replace("bool fail_write = false;", "bool stat_error=false;\n  bool fail_write = false;")
            mock = mock.replace("bool exists(const char* path) const { return files.count(path); }",
                                "bool exists(const char* path) const { return !fail_read_open && files.count(path); }")
            mock = mock.replace("bool mkdir(const char*)", "void _lockFS() {}\n  void _unlockFS() {}\n"
                                "  MemoryFS* _getFS() { return this; }\n"
                                "  File open(const char* path, uint8_t mode) { return open(path, mode ? \"w\" : \"r\"); }\n"
                                "  bool mkdir(const char*)")
            mock += ("\nstruct lfs_info {};\nconstexpr int LFS_ERR_NOENT=-2;\n"
                     "inline int lfs_stat(MemoryFS* fs, const char* path, lfs_info*) {\n"
                     "  if (fs->stat_error) return -5;\n"
                     "  return fs->files.count(path) ? 0 : LFS_ERR_NOENT;\n}\n")
            mock_dir = Path(directory) / "helpers"
            mock_dir.mkdir()
            (mock_dir / "IdentityStore.h").write_text(mock)
            cpp = Path(directory) / "config.cpp"
            cpp.write_text(source)
            variants = [(platform, seeder, "-DCOMPANION_RADIO_FULL=1")
                        for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM", "STM32_PLATFORM")
                        for seeder in ([], ["-DOTA_SEEDER_ONLY=1"])]
            variants.append(("ESP32_PLATFORM", [], "-DCOMPANION_FEATURE_OTA_CLI=1"))
            for platform, seeder, capability in variants:
                    with self.subTest(platform=platform, seeder=seeder, capability=capability):
                        binary = Path(directory) / "config"
                        compiled = subprocess.run([
                            compiler, "-std=c++17", "-Wall", "-Wextra", "-D"+platform+"=1", *seeder,
                            "-DENABLE_OTA=1", capability, "-I", directory,
                            "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                            "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                            str(ROOT / "src/helpers/ota/CompanionOtaConfig.cpp"),
                            str(cpp), "-o", str(binary),
                        ], text=True, capture_output=True)
                        self.assertEqual(compiled.returncode, 0, compiled.stderr)
                        ran = subprocess.run([str(binary)], text=True, capture_output=True)
                        self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_static_and_dynamic_load_wiring_does_not_allocate_on_registration(self):
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        context = (ROOT / "src/helpers/ota/OtaContext.cpp").read_text()
        self.assertIn("ota_set_context_config_loader(mesh::ota::loadCompanionOtaConfig)", companion)
        loader = extract_braced(context, "void ota_set_context_config_loader(")
        self.assertIn("ota_context_if_active()", loader)
        self.assertNotIn("ota_acquire_context", loader)
        acquire = extract_braced(context, "bool ota_acquire_context(")
        self.assertIn("context_config_loader(restored)", acquire)
        self.assertIn("restored.apply(c)", acquire)

    def test_executable_dynamic_loader_and_seeder_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = path / "loader.cpp"
            source.write_text(r'''
#include <helpers/ota/OtaContext.h>
#include <cassert>
using namespace mesh::ota;
namespace mesh { namespace ota {
bool ota_self_firmware(SelfFwInfo& info) { info=SelfFwInfo(); return false; }
} }
static int calls=0;
static bool readable=true;
static OtaConfigState disk;
static bool load(OtaConfigState& restored) {
  ++calls;
  if (!readable) return false;
  restored=disk;
  return true;
}
static bool send(void*, const uint8_t*, uint16_t, bool) { return true; }
int main() {
  disk.hops=7; disk.advert=17; disk.checkpoint=32;
  disk.autofetch=2; disk.autoinstall=1;
  uint8_t key[32]={71}; disk.allow.add(key);
  ota_set_context_config_loader(load);
  assert(calls==0 && !ota_context_if_active()); // no eager heap or queue claim
  ota_begin_context(123, send, nullptr, "test", nullptr);
  for (int i=0; i<3; ++i) {
    assert(ota_acquire_context(nullptr, 0));
    assert(calls==i+1 && ota_ctx().manager.max_hops()==7);
    assert(ota_ctx().manager.advert_mins()==17 && ota_ctx().manager.checkpoint_blocks()==32);
    assert(ota_ctx().allow.contains(key));
    assert(ota_ctx().manager.autofetch()==0 && ota_ctx().autoinstall==0);
    ota_release_context_if_idle(false);
    assert(!ota_context_if_active());
  }
  assert(ota_acquire_context(nullptr, 0));
  disk.hops=2;
  ota_set_context_config_loader(load); // existing/static-context registration path
  assert(ota_ctx().manager.max_hops()==2);
  readable=false;
  ota_set_context_config_loader(load);
  assert(ota_ctx().manager.max_hops()==2); // failed read does not partly apply defaults
  ota_release_context_if_idle(false);
  ota_set_context_config_loader(nullptr);
}
''')
            heap_test.OtaHeapTest().compile_and_run(path, source)


if __name__ == "__main__":
    unittest.main()
