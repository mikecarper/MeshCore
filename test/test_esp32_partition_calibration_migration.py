"""Exercise production calibration staging and interrupted recovery on the host."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/esp32_partition_migrator/main.cpp"
EXPANDER = ROOT / "examples/partition_expander/main.cpp"

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
constexpr int MALLOC_CAP_INTERNAL=1, MALLOC_CAP_8BIT=2;
void* heap_caps_malloc(size_t n,int){return malloc(n);}
void heap_caps_free(void* p){free(p);}
void delay(int){}
char status_text[192] = {};

struct Filesystem;
struct File {
  Filesystem* fs = nullptr;
  std::string path;
  size_t pos = 0;
  bool write_mode = false;
  explicit operator bool() const {return fs != nullptr;}
  size_t size() const;
  size_t read(uint8_t* out,size_t size);
  size_t write(const uint8_t* data,size_t size);
  void flush(){}
  void close(){fs=nullptr;}
};
struct Filesystem {
  std::map<std::string,std::vector<uint8_t>> files;
  std::string fail_write;
  bool begin(bool format){assert(!format);return true;}
  void end(){}
  bool exists(const char* p){return files.count(p)!=0;}
  File open(const char* p,const char* mode){
    if (!strcmp(mode,"r") && !exists(p)) return {};
    if (!strcmp(mode,"w")) files[p].clear();
    return {this,p,0,!strcmp(mode,"w")};
  }
} SPIFFS;
size_t File::size() const{return fs->files.at(path).size();}
size_t File::read(uint8_t* out,size_t n){
  auto& data=fs->files.at(path);if(pos+n>data.size())return 0;
  memcpy(out,data.data()+pos,n);pos+=n;return n;
}
size_t File::write(const uint8_t* data,size_t n){
  if(!write_mode || path==fs->fail_write)return 0;
  fs->files[path].assign(data,data+n);pos=n;return n;
}

struct Preferences {
  static std::map<std::string,std::vector<uint8_t>> values;
  static std::string fail_key;
  static std::string fail_remove_key;
  bool begin(const char*,bool){return true;}
  void end(){}
  size_t getBytesLength(const char* key){auto it=values.find(key);return it==values.end()?0:it->second.size();}
  size_t getBytes(const char* key,void* p,size_t n){
    auto it=values.find(key);if(it==values.end()||it->second.size()!=n)return 0;
    memcpy(p,it->second.data(),n);return n;
  }
  size_t putBytes(const char* key,const void* p,size_t n){
    if(fail_key==key)return 0;
    auto data=static_cast<const uint8_t*>(p);
    values[key]=std::vector<uint8_t>(data,data+n);return n;
  }
  bool getBool(const char* key,bool fallback){auto it=values.find(key);return it==values.end()?fallback:it->second.at(0)!=0;}
  size_t putBool(const char* key,bool value){uint8_t b=value?1:0;return putBytes(key,&b,1);}
  bool remove(const char* key){return fail_remove_key!=key && values.erase(key)!=0;}
};
std::map<std::string,std::vector<uint8_t>> Preferences::values;
std::string Preferences::fail_key;
std::string Preferences::fail_remove_key;
@CONFIG@
@METHODS@

int main(int argc,char** argv){
  assert(argc==2);const int test=atoi(argv[1]);
  assert(kConfigFileCount==40);assert(sizeof(LegacyConfigStage)==264);
  const std::vector<uint8_t> node={1,2,3,4};
  const std::vector<std::string> paths={
    "/radio_xtrim","/radio_xtrim.bak","/radio_xtrim.tmp",
    "/radio_calibration","/radio_calibration.bak","/radio_calibration.tmp",
    "/radio_calibration.journal","/radio_calibration.journal.tmp"};
  if(test==1){
    // An old bridge may have staged V1 before a new recovery image boots.
    LegacyConfigStage old={};old.magic=kConfigStageMagic;old.present=1;
    old.sizes[0]=node.size();old.crcs[0]=~crc32(node.data(),node.size());
    Preferences n;n.begin("",false);n.putBytes("cfg00",node.data(),node.size());
    n.putBytes(kMigrationConfigKey,&old,sizeof(old));n.putBool(kMigrationConfigPendingKey,true);
    assert(validateStagedConfig());assert(restoreStagedConfig());
    assert(SPIFFS.files.at("/com_prefs")==node);
    for(const auto& p:paths)assert(!SPIFFS.files.count(p));
    assert(!n.getBool(kMigrationConfigPendingKey,false));return 0;
  }
  SPIFFS.files["/com_prefs"]=node;
  for(size_t i=0;i<paths.size();++i)SPIFFS.files[paths[i]]={uint8_t(10+i),uint8_t(40+i)};
  auto original=SPIFFS.files;
  if(test==2){
    // A failed last calibration write cannot publish a pending manifest.
    Preferences::fail_key="cfg39";assert(!stageLegacyConfig());
    assert(!Preferences::values.count(kMigrationConfigPendingKey));
    assert(!Preferences::values.count(kMigrationConfigKey));
    for(const auto& f:kConfigFiles)assert(!Preferences::values.count(f.nvs_key));
    assert(SPIFFS.files==original);return 0;
  }
  if(test==6){
    // A failure publishing the final pending marker removes all staged data.
    Preferences::fail_key=kMigrationConfigPendingKey;assert(!stageLegacyConfig());
    assert(!Preferences::values.count(kMigrationConfigPendingKey));
    assert(!Preferences::values.count(kMigrationConfigKey));
    for(const auto& f:kConfigFiles)assert(!Preferences::values.count(f.nvs_key));
    assert(SPIFFS.files==original);return 0;
  }
  assert(stageLegacyConfig());
  assert(Preferences::values.count(kMigrationConfigPendingKey));
  SPIFFS.files.clear();
  if(test==3){
    // Corruption in a later calibration blob blocks every restore write.
    Preferences::values["cfg38"][0]^=0x80;
    assert(!validateStagedConfig());assert(!restoreStagedConfig());
    assert(SPIFFS.files.empty());assert(Preferences::values.count(kMigrationConfigPendingKey));return 0;
  }
  if(test==4){
    // A partial destination write keeps the journal for a complete retry.
    SPIFFS.fail_write="/radio_calibration";
    assert(!restoreStagedConfig());assert(Preferences::values.count(kMigrationConfigPendingKey));
    SPIFFS.fail_write.clear();assert(restoreStagedConfig());assert(SPIFFS.files==original);return 0;
  }
  if(test==5){
    // Unexpected upper bits or malformed serialized sizes never restore files.
    auto& blob=Preferences::values[kMigrationConfigKey];
    assert(blob.size()==sizeof(ConfigStage));
    ConfigStage stage={};memcpy(&stage,blob.data(),sizeof(stage));stage.present_high|=0x80000000U;
    memcpy(blob.data(),&stage,sizeof(stage));assert(!restoreStagedConfig());
    assert(SPIFFS.files.empty());assert(Preferences::values.count(kMigrationConfigPendingKey));return 0;
  }
  if(test==7 || test==8){
    // A failed final commit or pending-marker removal remains retryable.
    if(test==7)Preferences::fail_key=kMigrationConfigRestoredKey;
    else Preferences::fail_remove_key=kMigrationConfigPendingKey;
    assert(!restoreStagedConfig());assert(Preferences::values.count(kMigrationConfigPendingKey));
    Preferences::fail_key.clear();Preferences::fail_remove_key.clear();
    assert(restoreStagedConfig());assert(SPIFFS.files==original);
    assert(!Preferences::values.count(kMigrationConfigPendingKey));return 0;
  }
  assert(test==0);assert(validateStagedConfig());assert(restoreStagedConfig());
  assert(SPIFFS.files==original);assert(!Preferences::values.count(kMigrationConfigPendingKey));
  for(const auto& f:kConfigFiles)assert(!Preferences::values.count(f.nvs_key));
}
'''


class PartitionCalibrationMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="partition-calibration-")
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index('constexpr char kMigrationNvsNamespace[]')
        end = source.index('#if defined(MESHCORE_MIGRATION_RESUME_OTA)', start)
        config = source[start:end]
        names = ("uint32_t crc32(", "bool configStagePresent(",
                 "void markConfigStagePresent(", "bool readConfigStage(",
                 "bool verifyStagedConfigBlob(", "void clearConfigStageBlobs(",
                 "bool validateStagedConfig(", "bool stageLegacyConfig(",
                 "bool restoreStagedConfig(")
        methods = "\n".join(extract_braced(source, name) for name in names)
        path = Path(cls.temp.name) / "test.cpp"
        path.write_text(HARNESS.replace("@CONFIG@", config).replace("@METHODS@", methods))
        cls.binary = Path(cls.temp.name) / "test"
        result = subprocess.run([
            "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
            "-I", str(ROOT / "src"), str(path), "-o", str(cls.binary),
        ], capture_output=True, text=True)
        if result.returncode:
            cls.temp.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_complete_calibration_and_journal_restore(self):
        subprocess.run([str(self.binary), "0"], check=True)

    def test_legacy_staging_manifest_restores_unchanged(self):
        subprocess.run([str(self.binary), "1"], check=True)

    def test_failed_staging_never_commits_partial_data(self):
        subprocess.run([str(self.binary), "2"], check=True)

    def test_corrupt_calibration_blocks_all_restore_writes(self):
        subprocess.run([str(self.binary), "3"], check=True)

    def test_partial_restore_keeps_pending_for_retry(self):
        subprocess.run([str(self.binary), "4"], check=True)

    def test_unrecognized_presence_bits_fail_closed(self):
        subprocess.run([str(self.binary), "5"], check=True)

    def test_pending_marker_failure_cleans_staging(self):
        subprocess.run([str(self.binary), "6"], check=True)

    def test_restored_and_pending_marker_failures_remain_retryable(self):
        for case in ("7", "8"):
            subprocess.run([str(self.binary), case], check=True)

    def test_w12_loads_calibration_before_radio_init_and_blocks_unreadable_records(self):
        source = EXPANDER.read_text(encoding="utf-8")
        body = extract_braced(source, "void startLoRaReceiver()")
        self.assertLess(body.index("SPIFFS.begin(false)"), body.index("loadLR2021CalibrationSettings"))
        self.assertLess(body.index("loadLR2021CalibrationSettings"), body.index("if (!radio_init())"))
        self.assertIn("&& has_calibration", body)
        self.assertIn("LR2021CalibrationSettingsReady()", body)
        self.assertIn('SPIFFS.exists("/radio_calibration.journal")', body)
        self.assertIn('SPIFFS.exists("/radio_xtrim.bak")', body)


if __name__ == "__main__":
    unittest.main()
