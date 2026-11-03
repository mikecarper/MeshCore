#include <cassert>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#include "helpers/findmy/FindMyConfig.h"

struct FakeFS;
struct FakeFile {
  FakeFS* fs = nullptr;
  std::string path;
  bool writable = false;
  FakeFile() = default;
  FakeFile(FakeFS* owner, const char* name, bool can_write)
      : fs(owner), path(name), writable(can_write) {}
  explicit operator bool() const { return fs != nullptr; }
  bool isDirectory() const { return false; }
  size_t size() const;
  size_t read(uint8_t* out, size_t n);
  size_t write(const uint8_t* data, size_t n);
  void close() {}
  void flush() {}
};
struct FakeFS {
  std::map<std::string, std::vector<uint8_t>> files;
  bool fail_write = false, fail_rename = false;
  bool exists(const char* path) const { return files.count(path) != 0; }
  FakeFile open(const char* path, const char* mode = "r") {
    if (mode[0] == 'w') { files[path].clear(); return {this, path, true}; }
    return exists(path) ? FakeFile{this, path, false} : FakeFile{};
  }
  bool remove(const char* path) { return files.erase(path) != 0; }
  bool rename(const char* from, const char* to) {
    if (fail_rename || !exists(from)) return false;
    files[to] = files[from]; files.erase(from); return true;
  }
};
size_t FakeFile::size() const { return fs->files[path].size(); }
size_t FakeFile::read(uint8_t* out, size_t n) {
  const auto& bytes = fs->files[path];
  n = n < bytes.size() ? n : bytes.size();
  memcpy(out, bytes.data(), n); return n;
}
size_t FakeFile::write(const uint8_t* data, size_t n) {
  if (!writable) return 0;
  if (fs->fail_write) n /= 2;
  fs->files[path] = std::vector<uint8_t>(data, data + n); return n;
}

int main() {
  using namespace mesh::findmy;
  // Bytes 1 through 28, as exported using padded Base64.
  const char* key = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHA==";
  char reply[160];
  Config c;
  assert(!c.enabled && !c.has_key);
  assert(parseCommand("get findmy", c, reply, sizeof(reply)) == CommandResult::Query);
  assert(strstr(reply, "off") && strstr(reply, "unset"));
  assert(parseCommand("set findmy on", c, reply, sizeof(reply)) == CommandResult::Error);
  assert(!c.enabled);
  const std::string set_key = std::string("set findmy.key ") + key;
  assert(parseCommand(set_key.c_str(), c, reply, sizeof(reply)) == CommandResult::Changed);
  assert(c.has_key && !c.enabled);
  for (int i = 0; i < 28; ++i) assert(c.key[i] == i + 1);
  uint8_t prior[28]; memcpy(prior, c.key, sizeof(prior));
  assert(parseCommand("set findmy.key AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB==", c, reply, sizeof(reply)) == CommandResult::Error);
  assert(memcmp(prior, c.key, 28) == 0); // Reject noncanonical trailing bits.
  assert(parseCommand("set findmy.key AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==", c, reply, sizeof(reply)) == CommandResult::Error);
  assert(parseCommand("set findmy.key AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHA!!", c, reply, sizeof(reply)) == CommandResult::Error);
  assert(parseCommand("set findmy on", c, reply, sizeof(reply)) == CommandResult::Changed);
  assert(c.enabled);
  assert(parseCommand("get findmy", c, reply, sizeof(reply)) == CommandResult::Query);
  assert(strstr(reply, "on") && strstr(reply, "set") && !strstr(reply, key));
  assert(parseCommand("get other", c, reply, sizeof(reply)) == CommandResult::Unhandled);

  FakeFS fs;
  Config load;
  assert(loadConfig(&fs, load) == StoreResult::NotFound && !load.enabled);
  assert(saveConfig(&fs, c) == StoreResult::Success && c.generation == 1);
  assert(loadConfig(&fs, load) == StoreResult::Success && load.enabled && load.generation == 1);
  assert(memcmp(load.key, c.key, 28) == 0);
  Config stale = c;
  c.enabled = false;
  fs.fail_write = true;
  assert(saveConfig(&fs, c) == StoreResult::Unavailable);
  assert(loadConfig(&fs, load) == StoreResult::Success && load.enabled && load.generation == 1);
  fs.fail_write = false;
  fs.fail_rename = true;
  assert(saveConfig(&fs, c) == StoreResult::Unavailable);
  assert(loadConfig(&fs, load) == StoreResult::Success && load.enabled && load.generation == 1);
  fs.fail_rename = false;
  assert(saveConfig(&fs, c) == StoreResult::Success && c.generation == 2);
  assert(saveConfig(&fs, stale) == StoreResult::Conflict);
  assert(loadConfig(&fs, load) == StoreResult::Success && !load.enabled && load.generation == 2);
  assert(parseCommand("set findmy.key clear", c, reply, sizeof(reply)) == CommandResult::Changed);
  assert(!c.enabled && !c.has_key && !keyHasBits(c.key));
  assert(saveConfig(&fs, c) == StoreResult::Success && c.generation == 3);
  assert(loadConfig(&fs, load) == StoreResult::Success && !load.has_key);
  fs.files["/findmy.1"][12] ^= 0x80;
  assert(loadConfig(&fs, load) == StoreResult::Unavailable && !load.enabled && !load.has_key);
  assert(saveConfig(&fs, c) == StoreResult::Unavailable);
}
