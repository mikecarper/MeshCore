"""Exercise production MQTT commit/recovery after rejected saves and power loss."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_common_prefs_commit import HARNESS
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class MqttPrefsCommitTest(unittest.TestCase):
    def test_rejected_changes_never_publish_during_recovery(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        filesystem = HARNESS[:HARNESS.index('@STORE@')]
        # ESP32 exists() opens the file, but native rename/stat do not. Model
        # those separately so a failed read cannot masquerade as absence.
        filesystem = filesystem.replace(
            'bool exists(const char* path) const { return files.count(path) != 0; }',
            'bool exists(const char* path) const { return files.count(path) != 0 '
            '&& !faults.count(std::string("open:r:") + path); }')
        filesystem = filesystem.replace(
            '|| !exists(from) || exists(to)',
            '|| !files.count(from) || files.count(to)')
        filesystem = filesystem.replace(
            'if (!exists(path)) return {};', 'if (!files.count(path)) return {};')
        filesystem = filesystem.replace(
            'bool short_write = false;', 'MemoryFS();\n  bool short_write = false;')
        filesystem = filesystem.replace(
            'size_t write(const uint8_t*, size_t);',
            'size_t write(const uint8_t*, size_t);\n'
            '  size_t read(uint8_t* out, size_t count) {\n'
            '    if (!valid) return 0;\n'
            '    count = count < bytes->size() ? count : bytes->size();\n'
            '    for (size_t i=0;i<count;++i) out[i]=(*bytes)[i];\n'
            '    return count;\n'
            '  }')
        program = filesystem + r'''
#include <helpers/MQTTPrefsRecovery.h>
#include <helpers/FilePresence.h>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
static MemoryFS* stat_fs = nullptr;
MemoryFS::MemoryFS() { stat_fs=this; }
extern "C" int stat(const char* absolute, struct stat*) noexcept {
  const std::string path=std::string(absolute).substr(7); // /spiffs
  if (stat_fs->faults.count("stat:" + path)) { errno=EIO;return -1; }
  if (!stat_fs->files.count(path)) { errno=ENOENT;return -1; }
  return 0;
}
// Codec layout correctness has separate native tests. Exercise the actual
// production presence/read classifier with a small opaque-layout marker.
struct MQTTPrefsHeader { uint8_t bytes[4]; };
namespace MQTTPrefsCodec {
struct Plan { bool preserve_file; };
Plan classify(const uint8_t* prefix,size_t read,size_t size) {
  return {size < sizeof(MQTTPrefsHeader) || read != sizeof(MQTTPrefsHeader)
          || prefix[0] == 0xff};
}
}
'''
        program += extract_braced(source, 'static File openMqttPrefsRead(') + '\n'
        program += extract_braced(source, 'static MQTTPrefsRecovery::FileState mqttPrefsFileState(') + '\n'
        program += extract_braced(source, 'static bool recoverMqttPrefsFiles(') + '\n'
        program += extract_braced(source, 'class MQTTPrefsFileStore') + ';\n'
        program += r'''
const std::vector<uint8_t> previous = {1, 2, 3, 4};
const std::vector<uint8_t> candidate = {9, 8, 7, 6, 5};
bool save(MemoryFS& fs) {
  MQTTPrefsFileStore store(&fs);
  return MQTTPrefsAtomicStore::imageCommitted(MQTTPrefsAtomicStore::writeImage(
      store, [&](MQTTPrefsFileStore& target) {
        return target.write(candidate.data(), candidate.size()) == candidate.size();
      }));
}
int main() {
  // Individual failures retain the published previous configuration.
  for (const char* fault : {"open:w:/mqtt_prefs.tmp", "open:r:/mqtt_prefs.tmp",
                           "rename:/mqtt_prefs:/mqtt_prefs.bak",
                           "rename:/mqtt_prefs.tmp:/mqtt_prefs"}) {
    MemoryFS fs; fs.put("/mqtt_prefs", previous); fs.faults.insert(fault);
    assert(!save(fs));
    assert(fs.get("/mqtt_prefs") == previous);
    fs.faults.clear();
    assert(!recoverMqttPrefsFiles(&fs));
    assert(fs.get("/mqtt_prefs") == previous);
    assert(save(fs)); // no reboot needed after a transient failure
    assert(fs.get("/mqtt_prefs") == candidate);
  }
  {
    MemoryFS fs; fs.put("/mqtt_prefs", previous); fs.short_write=true;
    assert(!save(fs)); assert(fs.get("/mqtt_prefs") == previous);
    fs.short_write=false;
    assert(save(fs));
  }
  // Compound failure: publish, rollback, and scratch removal all fail.
  for (bool reboot : {false, true}) {
    MemoryFS fs; fs.put("/mqtt_prefs", previous);
    fs.faults = {"rename:/mqtt_prefs.tmp:/mqtt_prefs",
                 "rename:/mqtt_prefs.bak:/mqtt_prefs", "remove:/mqtt_prefs.tmp"};
    assert(!save(fs));
    assert(!fs.exists("/mqtt_prefs") && fs.get("/mqtt_prefs.bak") == previous);
    assert(fs.get("/mqtt_prefs.tmp") == candidate);
    fs.faults.clear();
    if (reboot) {
      assert(!recoverMqttPrefsFiles(&fs));
      assert(fs.get("/mqtt_prefs") == previous);
    } else {
      // A subsequent attempted save can finish rollback itself. Refuse the new
      // write, then verify that begin() recovered only the old published image.
      fs.faults.insert("open:w:/mqtt_prefs.tmp");
      assert(!save(fs)); assert(fs.get("/mqtt_prefs") == previous);
    }
  }
  // A rejected first save has no backup; it must never turn into a saved image.
  {
    MemoryFS fs;
    fs.faults = {"rename:/mqtt_prefs.tmp:/mqtt_prefs", "remove:/mqtt_prefs.tmp"};
    assert(!save(fs)); assert(fs.exists("/mqtt_prefs.tmp"));
    fs.faults.clear(); assert(!recoverMqttPrefsFiles(&fs));
    assert(!fs.exists("/mqtt_prefs") && !fs.exists("/mqtt_prefs.tmp"));
    assert(save(fs)); assert(fs.get("/mqtt_prefs") == candidate);
  }
  // Power loss at each publication boundary: only a named primary is committed.
  for (unsigned boundary=0; boundary<4; ++boundary) {
    MemoryFS fs; fs.put("/mqtt_prefs", previous); fs.put("/mqtt_prefs.tmp", candidate);
    if (boundary >= 1) assert(fs.rename("/mqtt_prefs", "/mqtt_prefs.bak"));
    if (boundary >= 2) assert(fs.rename("/mqtt_prefs.tmp", "/mqtt_prefs"));
    if (boundary >= 3) assert(fs.remove("/mqtt_prefs.bak"));
    assert(!recoverMqttPrefsFiles(&fs));
    assert(fs.get("/mqtt_prefs") == (boundary < 2 ? previous : candidate));
  }
  // Failure to clean a backup after a successful publication is not rejection.
  {
    MemoryFS fs; fs.put("/mqtt_prefs", previous);
    fs.faults.insert("remove:/mqtt_prefs.bak");
    assert(save(fs)); assert(fs.get("/mqtt_prefs") == candidate);
    fs.faults.clear(); assert(!recoverMqttPrefsFiles(&fs));
    assert(fs.get("/mqtt_prefs") == candidate);
    assert(!fs.exists("/mqtt_prefs.bak"));
  }
  // Preserve opaque committed primary/backup images, not an unpublished temp.
  for (bool backup : {false, true}) {
    MemoryFS fs; const char* path = backup ? "/mqtt_prefs.bak" : "/mqtt_prefs";
    const std::vector<uint8_t> opaque = {0xff, 2, 3, 4};
    fs.put(path, opaque); fs.put("/mqtt_prefs.tmp", candidate);
    assert(recoverMqttPrefsFiles(&fs));
    assert(fs.get("/mqtt_prefs") == opaque);
    assert(!save(fs)); assert(fs.get("/mqtt_prefs") == opaque);
  }
  // A failed open is not a missing committed file, even when exists() says so.
  for (bool backup : {false, true}) {
    MemoryFS fs; const char* path = backup ? "/mqtt_prefs.bak" : "/mqtt_prefs";
    fs.put(path, previous); fs.put("/mqtt_prefs.tmp", candidate);
    fs.faults.insert(std::string("open:r:") + path);
    assert(!fs.exists(path));
    assert(mqttPrefsFileState(&fs,path) == MQTTPrefsRecovery::FileState::Preserve);
    assert(recoverMqttPrefsFiles(&fs));
    assert(fs.get("/mqtt_prefs") == previous);
    fs.faults.insert("open:r:/mqtt_prefs");
    assert(!save(fs)); assert(fs.get("/mqtt_prefs") == previous);
    assert(fs.get("/mqtt_prefs.tmp") == candidate);
    fs.faults.clear(); assert(!recoverMqttPrefsFiles(&fs));
    assert(fs.get("/mqtt_prefs") == previous);
    assert(save(fs)); assert(fs.get("/mqtt_prefs") == candidate);
  }
  // Metadata I/O failures also hold the existing image; retry can recover.
  {
    MemoryFS fs; fs.put("/mqtt_prefs",previous);
    fs.faults.insert("stat:/mqtt_prefs");
    assert(recoverMqttPrefsFiles(&fs)); assert(!save(fs));
    assert(fs.get("/mqtt_prefs") == previous && !fs.exists("/mqtt_prefs.tmp"));
    fs.faults.clear(); assert(save(fs));
  }
  // Failed recovery holds artifacts and refuses a new write.
  {
    MemoryFS fs; fs.put("/mqtt_prefs.bak", previous); fs.put("/mqtt_prefs.tmp", candidate);
    fs.faults.insert("rename:/mqtt_prefs.bak:/mqtt_prefs");
    assert(recoverMqttPrefsFiles(&fs)); assert(!save(fs));
    assert(fs.get("/mqtt_prefs.bak") == previous && fs.get("/mqtt_prefs.tmp") == candidate);
  }
  puts("MQTT rejected-save and power-loss recovery scenarios passed");
}
'''
        with tempfile.TemporaryDirectory(prefix='mesh-mqtt-commit-') as directory:
            cpp, exe = Path(directory) / 'test.cpp', Path(directory) / 'test'
            cpp.write_text(program)
            subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-DESP32_PLATFORM',
                            '-fsanitize=address,undefined', '-fno-pie', '-no-pie',
                            '-I', str(ROOT / 'src'), str(cpp), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
