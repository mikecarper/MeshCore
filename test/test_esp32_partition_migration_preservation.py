"""Execute strict 4 MiB source/retention guards without hardware or PlatformIO."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/esp32_partition_migrator/main.cpp"

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <helpers/ESP32PartitionMigrationPolicy.h>
namespace migration = mesh::esp32_partition_migration;
constexpr size_t kIdentityFileBytes=96;
char status_text[192]={};
struct Entry {std::string path;bool dir;size_t bytes;std::vector<std::string> children;};
struct File {
  Entry* item=nullptr;size_t index=0;
  explicit operator bool() const{return item!=nullptr;}
  bool isDirectory()const{return item&&item->dir;}
  const char* path()const{return item?item->path.c_str():nullptr;}
  size_t size()const{return item?item->bytes:0;}
  File openNextFile();
  void close(){item=nullptr;}
};
struct Filesystem {
  std::map<std::string,Entry> entries;
  bool mounted=true;unsigned begins=0,ends=0;
  bool begin(bool format){assert(!format);++begins;return mounted;}
  void end(){++ends;}
  bool exists(const char* path){return entries.count(path)!=0;}
  File open(const char* path,const char* mode){
    assert(!strcmp(mode,"r"));
    auto it=entries.find(path);return {it==entries.end()?nullptr:&it->second,0};
  }
  void add(const char* path,size_t bytes,const char* parent="/") {
    entries[path]={path,false,bytes,{}};entries[parent].children.push_back(path);
  }
  void base() {
    *this=Filesystem();entries["/"]={"/",true,0,{}};
    add("/identity/_main.id",96);
  }
} SPIFFS;
File File::openNextFile(){
  if(!item||index>=item->children.size())return {};
  return SPIFFS.open(item->children[index++].c_str(),"r");
}
@CONFIG@
@METHODS@
int main(int argc,char**argv) {
  assert(argc==2);int scenario=atoi(argv[1]);SPIFFS.base();
  if(scenario==0) {
    SPIFFS.add("/com_prefs",512);SPIFFS.add("/s_contacts",2048);
    SPIFFS.add("/s_login_replay",256);SPIFFS.add("/radio_profiles",1024);
    SPIFFS.add("/mqtt_prefs",2888);SPIFFS.add("/management",512);
    assert(preflightStrictPreservation(migration::kTarget4MB));
    assert(SPIFFS.begins==1&&SPIFFS.ends==1);
  } else if(scenario==1) {
    for(const char* path:{"/custom.ini","/mqtt_prefs.tmp","/identity/other.id","/logs.txt"}) {
      SPIFFS.base();SPIFFS.add(path,10);
      assert(!preflightStrictPreservation(migration::kTarget4MB));
      assert(strstr(status_text,"unpreserved"));assert(SPIFFS.ends==1);
    }
  } else if(scenario==2) {
    for(size_t bytes:{size_t(0),size_t(16385),size_t(UINT32_MAX)+1}) {
      SPIFFS.base();SPIFFS.add("/radio_profiles",bytes);
      assert(!preflightStrictPreservation(migration::kTarget4MB));
    }
    SPIFFS.base();SPIFFS.add("/s_contacts",12000);
    SPIFFS.add("/radio_profiles",12000);SPIFFS.add("/mqtt_prefs",12000);
    assert(!preflightStrictPreservation(migration::kTarget4MB));
    assert(strstr(status_text,"budget"));
  } else if(scenario==3) {
    for(const auto& layout:{migration::kLegacyLayout,migration::kMinSpiffs4MBLayout,
                            migration::kExpanded4MBLayout}) {
      assert(migration::isReviewed4MBSource(4U*1024U*1024U,layout));
      assert(!migration::isReviewed4MBSource(8U*1024U*1024U,layout));
      auto changed=layout;changed.spiffs_address+=4096;
      assert(!migration::isReviewed4MBSource(4U*1024U*1024U,changed));
      changed=layout;changed.nvs_address+=4096;
      assert(!migration::isReviewed4MBSource(4U*1024U*1024U,changed));
      changed=layout;changed.app1_address=changed.app0_address;
      assert(!migration::isReviewed4MBSource(4U*1024U*1024U,changed));
    }
    auto target=migration::kTarget4MB;target.layout.spiffs_size=0x30000;
    assert(!preflightStrictPreservation(target));assert(SPIFFS.begins==0);
    assert(migration::kMinSpiffs4MBLayout.spiffs_address==0x3D0000);
    assert(migration::kMinSpiffs4MBLayout.spiffs_size==0x20000);
    // min_spiffs has a separate 64 KiB coredump after its 128 KiB filesystem;
    // silently treating the coredump as filesystem capacity is not reviewed.
    auto includes_coredump=migration::kMinSpiffs4MBLayout;
    includes_coredump.spiffs_size=0x30000;
    assert(!migration::isReviewed4MBSource(4U*1024U*1024U,includes_coredump));
    assert(migration::kMinSpiffs4MBLayout.app0_address+migration::kMinSpiffs4MBLayout.app0_size
        ==migration::kMinSpiffs4MBLayout.app1_address);
    // An old-B bridge can copy to A without erasing its currently running bytes.
    assert(migration::kExpanded4MBLayout.app0_address+migration::kMinSpiffs4MBLayout.app1_size
        <=migration::kMinSpiffs4MBLayout.app1_address);
  } else if(scenario==4) {
    SPIFFS.entries["/"].children.clear();
    SPIFFS.entries["/"].children.push_back("/identity");
    SPIFFS.entries["/identity"]={"/identity",true,0,{"/identity/_main.id"}};
    assert(preflightStrictPreservation(migration::kTarget4MB));
    SPIFFS.base();SPIFFS.entries["/"].children.push_back("/unknown");
    SPIFFS.entries["/unknown"]={"/unknown",true,0,{}};
    assert(!preflightStrictPreservation(migration::kTarget4MB));
    SPIFFS.base();SPIFFS.entries["/"].children.clear();
    assert(!preflightStrictPreservation(migration::kTarget4MB));
    SPIFFS.base();SPIFFS.entries["/radio_profiles"]={"/radio_profiles",false,10,{}};
    assert(!preflightStrictPreservation(migration::kTarget4MB));
    assert(strstr(status_text,"incomplete"));
    SPIFFS.base();SPIFFS.mounted=false;
    assert(!preflightStrictPreservation(migration::kTarget4MB));
  } else if(scenario==5) {
    SPIFFS.entries["/identity/_main.id"].bytes=128;
    assert(!preflightStrictPreservation(migration::kTarget4MB));
    assert(strstr(status_text,"identity"));
    SPIFFS.base();SPIFFS.entries["/"].children.push_back("/identity/_main.id");
    assert(!preflightStrictPreservation(migration::kTarget4MB));
  } else if(scenario==6) {
    migration::PreservationBudget budget(65536);
    for(unsigned i=0;i<33;++i)assert(budget.addFile(96));
    assert(!budget.addFile(96));
    migration::PreservationBudget large(65536);
    assert(large.addFile(96)&&large.addFile(16384));
    assert(large.requiredBytes()<=65536);
    assert(!large.addFile(16384));
    migration::PreservationBudget invalid(65536);
    assert(!invalid.addFile(0)&&!invalid.addFile(UINT32_MAX));
  } else return 2;
}
'''


class StrictPartitionPreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="strict-partition-preservation-")
        source = SOURCE.read_text()
        start = source.index("struct ConfigFile {")
        end = source.index(";", source.index("constexpr size_t kConfigFileCount", start)) + 1
        config = source[start:end]
        methods = "\n".join(extract_braced(source, name) for name in (
            "bool inventoryStrictDirectory(", "bool preflightStrictPreservation("))
        path = Path(cls.directory.name) / "test.cpp"
        cls.binary = Path(cls.directory.name) / "test"
        path.write_text(HARNESS.replace("@CONFIG@", config).replace("@METHODS@", methods))
        result = subprocess.run([
            "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
            "-I", str(ROOT / "src"), str(path), "-o", str(cls.binary),
        ], text=True, capture_output=True)
        if result.returncode:
            cls.directory.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_saved_node_acl_radios_mqtt_fit(self):
        subprocess.run([str(self.binary), "0"], check=True)

    def test_unknown_files_are_not_silently_discarded(self):
        subprocess.run([str(self.binary), "1"], check=True)

    def test_empty_oversized_and_over_capacity_config_is_refused(self):
        subprocess.run([str(self.binary), "2"], check=True)

    def test_only_reviewed_source_maps_and_target_capacity_are_accepted(self):
        subprocess.run([str(self.binary), "3"], check=True)

    def test_nested_flat_missing_and_incomplete_inventory(self):
        subprocess.run([str(self.binary), "4"], check=True)

    def test_unpreserved_identity_metadata_and_duplicate_paths_are_refused(self):
        subprocess.run([str(self.binary), "5"], check=True)

    def test_budget_reserves_gc_and_an_atomic_largest_file_rewrite(self):
        subprocess.run([str(self.binary), "6"], check=True)

    def test_preflight_precedes_every_destructive_migration_step(self):
        source = SOURCE.read_text()
        body = extract_braced(source, "void runMigration()")
        guard = body.index("preflightStrictPreservation(*plan)")
        for operation in ("stageLegacyIdentity()", "stageLegacyConfig()", "copyAndVerify(*running",
                          "esp_ota_set_boot_partition(", "publishExpandedPartitionTable(*plan)"):
            self.assertLess(guard, body.index(operation))
        self.assertIn("MESHCORE_MIGRATION_STRICT_4MB", source)
        # Existing boards keep their existing migration behavior unless a new,
        # reviewed package explicitly opts into this narrower contract.
        self.assertIn("#if defined(MESHCORE_MIGRATION_STRICT_4MB) && MESHCORE_MIGRATION_STRICT_4MB", body)


if __name__ == "__main__":
    unittest.main()
