"""Exercise the actual common preference store and recovery after failed commits."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>
#include <helpers/CommonPrefsRecovery.h>
#include <helpers/MQTTPrefsAtomicStore.h>
class MemoryFS;
class File {
  MemoryFS* fs = nullptr;
  std::shared_ptr<std::vector<uint8_t>> bytes;
  bool valid = false;
public:
  File() = default;
  File(MemoryFS* owner, std::shared_ptr<std::vector<uint8_t>> contents)
      : fs(owner), bytes(contents), valid(true) {}
  operator bool() const { return valid; }
  size_t write(const uint8_t*, size_t);
  size_t size() const { return valid ? bytes->size() : 0; }
  void close() { valid = false; }
};
class MemoryFS {
public:
  std::map<std::string, std::shared_ptr<std::vector<uint8_t>>> files;
  std::set<std::string> faults;
  bool short_write = false;
  bool exists(const char* path) const { return files.count(path) != 0; }
  bool remove(const char* path) {
    if (faults.count(std::string("remove:") + path)) return false;
    return files.erase(path) != 0;
  }
  bool rename(const char* from, const char* to) {
    if (faults.count(std::string("rename:") + from + ":" + to)
        || !exists(from) || exists(to)) return false;
    files[to] = files[from]; files.erase(from); return true;
  }
  File open(const char* path, const char* mode = "r", bool = false) {
    if (faults.count(std::string("open:") + mode + ":" + path)) return {};
    if (mode[0] == 'w') files[path] = std::make_shared<std::vector<uint8_t>>();
    if (!exists(path)) return {};
    return File(this, files[path]);
  }
  void put(const char* path, const std::vector<uint8_t>& image) {
    files[path] = std::make_shared<std::vector<uint8_t>>(image);
  }
  const std::vector<uint8_t>& get(const char* path) const { return *files.at(path); }
};
size_t File::write(const uint8_t* data, size_t count) {
  if (!valid) return 0;
  if (fs->short_write && count) --count;
  bytes->insert(bytes->end(), data, data + count);
  return count;
}
using FILESYSTEM = MemoryFS;
@STORE@;
class CommonCLI {
public:
  bool recoverCommonPrefsFiles(FILESYSTEM*);
};
@RECOVERY@
const std::vector<uint8_t> previous_image = {1, 2, 3, 4};
const std::vector<uint8_t> candidate_image = {9, 8, 7, 6, 5};
bool save(MemoryFS& fs) {
  CommonPrefsFileStore store(&fs);
  return MQTTPrefsAtomicStore::imageCommitted(MQTTPrefsAtomicStore::writeImage(
      store, [](CommonPrefsFileStore& out) {
        return out.write(candidate_image.data(), candidate_image.size()) == candidate_image.size();
      }));
}
void assertRecoveredPrevious(MemoryFS& fs) {
  CommonCLI cli;
  fs.faults.clear(); fs.short_write = false;
  assert(cli.recoverCommonPrefsFiles(&fs));
  assert(fs.get("/com_prefs") == previous_image);
  assert(!fs.exists("/com_prefs.tmp") && !fs.exists("/com_prefs.bak"));
  assert(save(fs));
  assert(cli.recoverCommonPrefsFiles(&fs));
  assert(fs.get("/com_prefs") == candidate_image);
}
int main() {
  unsigned scenarios = 0;
  for (const char* fault : {"open:w:/com_prefs.tmp", "open:r:/com_prefs.tmp",
      "rename:/com_prefs:/com_prefs.bak", "rename:/com_prefs.tmp:/com_prefs"}) {
    MemoryFS fs; fs.put("/com_prefs", previous_image); fs.faults.insert(fault);
    assert(!save(fs)); assertRecoveredPrevious(fs); ++scenarios;
  }
  { MemoryFS fs; fs.put("/com_prefs", previous_image); fs.short_write = true;
    assert(!save(fs)); assertRecoveredPrevious(fs); ++scenarios; }
  for (bool restore_fails : {false, true}) {
    for (bool discard_fails : {false, true}) {
      MemoryFS fs; fs.put("/com_prefs", previous_image);
      fs.faults.insert("rename:/com_prefs.tmp:/com_prefs");
      if (restore_fails) fs.faults.insert("rename:/com_prefs.bak:/com_prefs");
      if (discard_fails) fs.faults.insert("remove:/com_prefs.tmp");
      assert(!save(fs));
      if (restore_fails) {
        CommonCLI cli;
        assert(!cli.recoverCommonPrefsFiles(&fs));
        assert(!fs.exists("/com_prefs"));
        assert(fs.get("/com_prefs.bak") == previous_image);
      }
      assertRecoveredPrevious(fs); ++scenarios;
    }
  }
  { // Before a power cut publishes the candidate, backup remains authoritative.
    MemoryFS fs; fs.put("/com_prefs.bak", previous_image);
    fs.put("/com_prefs.tmp", candidate_image);
    assertRecoveredPrevious(fs); ++scenarios;
  }
  { // After publication, cleanup failure cannot roll back the accepted image.
    MemoryFS fs; fs.put("/com_prefs", previous_image);
    fs.faults.insert("remove:/com_prefs.bak");
    assert(save(fs)); assert(fs.get("/com_prefs") == candidate_image);
    fs.faults.clear(); CommonCLI cli;
    assert(cli.recoverCommonPrefsFiles(&fs));
    assert(fs.get("/com_prefs") == candidate_image);
    assert(!fs.exists("/com_prefs.bak")); ++scenarios;
  }
  { // A failed first save must not become a successful save on reboot.
    MemoryFS fs; fs.faults.insert("rename:/com_prefs.tmp:/com_prefs");
    fs.faults.insert("remove:/com_prefs.tmp");
    assert(!save(fs)); fs.faults.clear(); CommonCLI cli;
    assert(cli.recoverCommonPrefsFiles(&fs));
    assert(!fs.exists("/com_prefs") && !fs.exists("/com_prefs.tmp")); ++scenarios;
  }
  { // Recovery cleanup can fail without losing the previous image.
    MemoryFS fs; fs.put("/com_prefs.bak", previous_image);
    fs.put("/com_prefs.tmp", candidate_image);
    fs.faults.insert("remove:/com_prefs.tmp"); CommonCLI cli;
    assert(!cli.recoverCommonPrefsFiles(&fs));
    assert(fs.get("/com_prefs") == previous_image);
    assertRecoveredPrevious(fs); ++scenarios;
  }
  printf("%u actual common preference commit/recovery scenarios passed\n", scenarios);
}
'''


class CommonPrefsCommitTest(unittest.TestCase):
    def test_actual_store_commit_and_recovery(self):
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="utf-8")
        harness = HARNESS.replace("@STORE@", extract_braced(source, "class CommonPrefsFileStore"))
        harness = harness.replace("@RECOVERY@", extract_braced(
            source, "bool CommonCLI::recoverCommonPrefsFiles("))
        with tempfile.TemporaryDirectory(prefix="meshcore-common-commit-") as temp:
            cpp = Path(temp) / "test.cpp"
            exe = Path(temp) / ("test.exe" if os.name == "nt" else "test")
            cpp.write_text(harness, encoding="utf-8")
            subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra",
                            "-Werror", "-I", str(ROOT / "src"), str(cpp), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    unittest.main()
