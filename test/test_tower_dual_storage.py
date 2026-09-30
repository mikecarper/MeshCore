"""Run production Tower settings/backend code against fault-injectable host stores."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def run_cpp(source):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / "test.cpp").write_text(source)
        subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-I", str(ROOT / "src"), str(path / "test.cpp"),
                        "-o", str(path / "test")], check=True)
        subprocess.run([str(path / "test")], check=True)


class TowerStorageTest(unittest.TestCase):
    def test_boot_pull_uses_privileged_source_gate_not_app_storage_bits(self):
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        start = cli.index('const uint8_t storage = c.fetch_store.usesExternal()')
        end = cli.index('#elif defined(OTA_SD_STORE)', start)
        self.assertIn('if (!selboot && !(bl.storage_flags & storage))', cli[start:end])
        self.assertIn('if (!c.bootloaderUpdateSupported())', cli[:start])

    def test_storage_alias_precedes_qspi_and_disabled_archive_explains_why(self):
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        self.assertLess(cli.index('is_cmd(a, "sd|storage"'),
                        cli.index('is_cmd(a, "qspi|storage"'))
        self.assertIn('SD OTA archive disabled; use set sdcard on and reboot', cli)

    def test_saved_setting_defaults_and_failures(self):
        production = (ROOT / "src/helpers/ota/OtaTowerStorageConfig.cpp").read_text()
        production = production[production.index("namespace mesh"):production.rindex("#endif")]
        run_cpp(r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <helpers/PersistentStoreFormat.h>
constexpr int LFS_ERR_IO=-5, LFS_ERR_NOENT=-2, FILE_O_READ=1, FILE_O_WRITE=2;
struct lfs_info {};
struct FILESYSTEM {
  std::map<std::string,std::vector<uint8_t>> files;
  bool io_fail=false, rename_fail=false, short_write=false;
  bool exists(const char* p) { return files.count(p); }
  bool remove(const char* p) { return files.erase(p); }
  void _lockFS() {} void _unlockFS() {}
  FILESYSTEM* _getFS() { return this; }
};
int lfs_stat(FILESYSTEM* fs, const char* p, lfs_info*) {
  return fs->io_fail ? LFS_ERR_IO : fs->exists(p) ? 0 : LFS_ERR_NOENT;
}
int lfs_rename(FILESYSTEM* fs, const char* a, const char* b) {
  if (fs->rename_fail) return LFS_ERR_IO;
  fs->files[b]=fs->files[a]; fs->files.erase(a); return 0;
}
struct File {
  FILESYSTEM& fs; std::string path;
  File(FILESYSTEM& f):fs(f) {}
  bool open(const char* p, int mode) {
    path=p; if (fs.io_fail) return false;
    if (mode==FILE_O_READ) return fs.exists(p);
    fs.files[path].clear(); return true;
  }
  size_t size() { return fs.files[path].size(); }
  size_t read(uint8_t* out, size_t n) { memcpy(out,fs.files[path].data(),n); return n; }
  size_t write(const uint8_t* in, size_t n) {
    if (fs.short_write) --n;
    fs.files[path].assign(in,in+n); return n;
  }
  void close() {} void flush() {}
};
''' + production + r'''
int main() {
  using namespace mesh::ota;
  FILESYSTEM fs;
  beginTowerStorageConfig(&fs); assert(towerSdEnabledAtBoot());
  assert(setTowerSdEnabled(false));
  assert(towerSdEnabledAtBoot() && !towerSdConfigured()); // pinned until reboot
  char reply[160]; assert(handleTowerSdCommand("",reply,sizeof(reply)));
  assert(strstr(reply,"reboot required"));
  beginTowerStorageConfig(&fs); assert(!towerSdEnabledAtBoot());
  fs.rename_fail=true; assert(!setTowerSdEnabled(true));
  beginTowerStorageConfig(&fs); assert(!towerSdEnabledAtBoot());
  fs.rename_fail=false; fs.short_write=true; assert(!setTowerSdEnabled(true));
  beginTowerStorageConfig(&fs); assert(!towerSdEnabledAtBoot());
  fs.short_write=false; assert(handleTowerSdCommand("on",reply,sizeof(reply)));
  assert(!towerSdEnabledAtBoot() && towerSdConfigured());
  beginTowerStorageConfig(&fs); assert(towerSdEnabledAtBoot());
  fs.files["/ota_sd"][7]^=1; beginTowerStorageConfig(&fs); assert(!towerSdEnabledAtBoot());
  fs.files["/ota_sd"].resize(3); beginTowerStorageConfig(&fs); assert(!towerSdEnabledAtBoot());
  fs.files.clear(); fs.io_fail=true;
  beginTowerStorageConfig(&fs); assert(!towerSdEnabledAtBoot());
  beginTowerStorageConfig(nullptr); assert(!towerSdEnabledAtBoot());
  assert(!setTowerSdEnabled(true));
  assert(!handleTowerSdCommand("format",reply,sizeof(reply)));
}
''')

    def test_exact_caps_preserve_sd_lineage(self):
        run_cpp(r'''
#include <cassert>
#include <cstring>
#define OTA_SD_DUAL_STORE 1
#include <helpers/ota/OtaBlInfo.h>
using namespace mesh::ota;
int main() {
  alignas(4) uint8_t bytes[160]={};
  const uint8_t sd[16]={'M','O','T','A','B','L','D','R',3,0,5,0,9,0,0,0};
  const uint8_t ram[32]={'M','O','T','A','R','A','M','A',1,0,72,0,0,0,1,0,
    'M','O','T','A','S','T','O','R',1,0,16,0,2,0,0,0};
  memcpy(bytes,sd,16);
  assert(ota_bootloader_update_storage_flags()==9);
  assert(ota_bootloader_self_update_caps_valid(ota_bl_update_caps_scan_aligned(bytes,160,9)));
  OtaBlCaps update = ota_bl_update_caps_scan_aligned(bytes,160,9);
  assert(ota_bootloader_self_update_source_valid(update,false));
  assert(!ota_bootloader_self_update_source_valid(update,true));
  update.optional_app_storage=2;
  assert(ota_bootloader_self_update_source_valid(update,true));
  update.optional_app_storage=0x14;
  assert(!ota_bootloader_self_update_source_valid(update,true));
  assert(ota_bl_app_caps_scan(bytes,160).storage_flags==9); // old SD: no internal path
  memcpy(bytes+32,ram,32);
  assert(ota_bl_app_caps_scan(bytes,160).storage_flags==11);
  assert(ota_bl_update_caps_scan_aligned(bytes,160,9).storage_flags==9);
  assert(!ota_bl_update_caps_scan_aligned(bytes,160,10).present); // migration required
  bytes[60]=0x14; assert(ota_bl_app_caps_scan(bytes,160).storage_flags==9); // RAK != Tower
  bytes[60]=2; bytes[61]=1; assert(!ota_bl_optional_app_storage(bytes,160));
  bytes[61]=0; memcpy(bytes+80,ram,32); assert(!ota_bl_optional_app_storage(bytes,160));
  memset(bytes+80,0,32); bytes[44]=1; assert(!ota_bl_optional_app_storage(bytes,160));
}
''')

    def test_backend_is_pinned_and_no_sd_access_when_off(self):
        header = (ROOT / "src/helpers/ota/OtaStoreTowerNrf52.h").read_text()
        body = header[header.index("class OtaStoreTowerNrf52"):header.index("} }")]
        run_cpp(r'''
#include <cassert>
#include <new>
#include <helpers/ota/OtaStore.h>
#include <helpers/ota/OtaBlInfo.h>
namespace mesh { namespace ota {
struct MainBoard {};
bool enabled=true, sd_ok=true;
unsigned sd_calls=0, internal_calls=0, sd_constructed=0, internal_constructed=0;
uint8_t flags=11;
bool towerSdEnabledAtBoot() { return enabled; }
OtaBlCaps simulatedCaps() { OtaBlCaps c; c.present=true; c.storage_flags=flags; return c; }
struct TestStore : OtaStore {
  bool begin(uint32_t) override { return true; }
  bool write(uint32_t,const uint8_t*,uint32_t) override { return true; }
  bool read(uint32_t,uint8_t*,uint32_t) const override { return true; }
  uint32_t capacity() const override { return 65536; }
  uint32_t staged_size() const override { return 0; }
  void clear() override {}
};
struct OtaStoreFlashNrf52 : TestStore {
  OtaStoreFlashNrf52() { ++internal_constructed; }
  bool begin(uint32_t) override { ++internal_calls; return true; }
};
struct OtaStoreSdNrf52 : TestStore {
  OtaStoreSdNrf52() { ++sd_constructed; }
  bool begin(uint32_t) override { ++sd_calls; return sd_ok; }
  const char* last_error() const { return "SD failed"; }
  bool formatCard(MainBoard&) { ++sd_calls; return sd_ok; }
  bool eraseCard(MainBoard&) { ++sd_calls; return sd_ok; }
  bool getSpace(MainBoard&,uint64_t&,uint64_t&) { ++sd_calls; return sd_ok; }
  bool listFiles(MainBoard&,uint16_t,char*,size_t) { ++sd_calls; return sd_ok; }
};
''' + body.replace("ota_bootloader_app_caps()", "simulatedCaps()") + r'''
} }
int main() {
  using namespace mesh::ota;
  { OtaStoreTowerNrf52 s; assert(s.usesExternal()); enabled=false;
    assert(s.usesExternal()); sd_ok=false; assert(!s.begin(100)); assert(!internal_calls); }
  const unsigned before=sd_calls, constructed=sd_constructed;
  { OtaStoreTowerNrf52 s; assert(s.usesInternal()); assert(s.begin(100));
    MainBoard board; uint64_t a=0,b=0; char reply[10];
    assert(!s.formatCard(board)); assert(!s.eraseCard(board));
    assert(!s.getSpace(board,a,b)); assert(!s.listFiles(board,0,reply,sizeof(reply)));
    assert(sd_calls==before && sd_constructed==constructed);
    flags=9; assert(!s.begin(100)); assert(s.capacity()==0); // old SD loader blocks internal
  }
  assert(internal_constructed==1);
}
''')

    def test_sd_target_keeps_identity_ram_and_capacities(self):
        import configparser
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "variants/heltec_tower_v2/platformio.ini")
        env = config["env:Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors"]
        self.assertEqual(env["extends"], "Heltec_tower_v2_sdcard")
        self.assertNotIn("board_build.ldscript", env)
        self.assertIn("-D MAX_NEIGHBOURS=254", env["build_flags"])
        self.assertIn("-D OTA_SD_DUAL_STORE=1", env["build_flags"])
        self.assertNotIn("-D OTA_HYBRID_RAM_STORE", env["build_flags"])


if __name__ == "__main__":
    unittest.main()
