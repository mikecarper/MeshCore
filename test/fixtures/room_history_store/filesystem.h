
#include <algorithm>
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

struct PowerCut {};
class MemoryFS;
static MemoryFS* metadata_filesystem = nullptr;
class File {
  MemoryFS* fs = nullptr;
  std::shared_ptr<std::vector<uint8_t>> bytes;
  std::string path;
  size_t cursor = 0, initial_size = 0;
  bool writable = false, directory = false, valid = false;
public:
  File() = default;
  File(MemoryFS*, std::shared_ptr<std::vector<uint8_t>>, const char*, bool, bool);
  operator bool() const { return valid; }
  bool isDirectory() const { return directory; }
  size_t size();
  bool seek(uint32_t position);
  int read(uint8_t*, size_t);
  size_t write(const uint8_t*, size_t);
  void flush();
  void close();
};
class MemoryFS {
public:
  void _lockFS() {}
  void _unlockFS() {}
  MemoryFS* _getFS() { return this; }
  std::map<std::string, std::shared_ptr<std::vector<uint8_t>>> files;
  std::set<std::string> faults, directories;
  std::vector<std::string> trace;
  size_t boundary = 0, cut_at = 0;
  bool short_write = false, short_read = false;
  std::vector<uint8_t> replacement_on_close;
  unsigned write_opens = 0;
  size_t capacity = std::numeric_limits<size_t>::max();
  unsigned read_calls = 0, fail_read_at = 0;
  std::string replacement_path;
  void point(const std::string& event) {
    trace.push_back(event);
    if (++boundary == cut_at) throw PowerCut{};
  }
  bool begin(const std::string& op) { point("before:" + op); return !faults.count(op); }
  void end(const std::string& op) { point("after:" + op); }
  bool exists(const char* path) {
    const std::string op = std::string("stat:") + path;
    const bool okay = begin(op);
    const bool present = files.count(path) || directories.count(path);
    end(op);
    return okay && present;
  }
  bool remove(const char* path) {
    const std::string op = std::string("remove:") + path;
    if (!begin(op)) return false;
    const bool removed = files.erase(path) + directories.erase(path) != 0;
    end(op); return removed;
  }
  bool rename(const char* from, const char* to) {
    const std::string op = std::string("rename:") + from + ":" + to;
    if (!begin(op) || !files.count(from) || files.count(to) || directories.count(to)) return false;
    files[to] = files[from]; files.erase(from); end(op); return true;
  }
  File open(const char* path, const char* mode = "r", bool create = false) {
    (void)create;
    const bool append = mode[0] == 'a';
    const bool write = mode[0] == 'w' || append;
    const std::string op = std::string("open:") + (append ? "a:" : write ? "w:" : "r:") + path;
    if (!begin(op)) return {};
    if (write) {
      ++write_opens;
      if (!append || !files.count(path)) files[path] = std::make_shared<std::vector<uint8_t>>();
      if (faults.count(std::string("directory:") + path)) directories.insert(path);
    }
    if (!files.count(path) && !directories.count(path)) { end(op); return {}; }
    if (!files.count(path)) files[path] = std::make_shared<std::vector<uint8_t>>();
    File file(this, files[path], path, write, directories.count(path));
    end(op); return file;
  }
  File open(const char* path, int mode) { assert(mode == 1); return open(path, "a"); }
  void put(const char* path, const std::vector<uint8_t>& bytes) {
    files[path] = std::make_shared<std::vector<uint8_t>>(bytes);
  }
  std::vector<uint8_t> get(const char* path) const { return *files.at(path); }
  void reset() { trace.clear(); boundary = cut_at = 0; faults.clear(); short_write = short_read = false; write_opens = 0; read_calls = fail_read_at = 0; replacement_on_close.clear(); replacement_path.clear(); capacity = std::numeric_limits<size_t>::max(); }
};
File::File(MemoryFS* owner, std::shared_ptr<std::vector<uint8_t>> data,
           const char* name, bool write, bool dir)
    : fs(owner), bytes(data), path(name), writable(write), directory(dir), valid(true) {
  initial_size = bytes->size(); cursor = write ? initial_size : 0;
}
size_t File::size() {
  const std::string op = "size:" + path;
  const bool okay = fs->begin(op); const size_t size = okay ? bytes->size() : 0;
  fs->end(op); return size;
}
int File::read(uint8_t* output, size_t length) {
  const std::string op = "read:" + path;
  if (!valid || directory || !fs->begin(op)) return -1;
  if (++fs->read_calls == fs->fail_read_at) return -1;
  length = std::min(length, bytes->size() - cursor);
  if (fs->short_read && length) --length;
  if (length) memcpy(output, bytes->data() + cursor, length);
  cursor += length; fs->end(op); return static_cast<int>(length);
}
size_t File::write(const uint8_t* data, size_t length) {
  const std::string op = "write:" + path;
  if (!valid || directory || !fs->begin(op)) return 0;
  if (fs->short_write && length) --length;
  size_t occupied = 0;
  for (const auto& entry : fs->files) occupied += entry.second->size();
  const size_t free = occupied < fs->capacity ? fs->capacity - occupied : 0;
  length = std::min(length, free);
  bytes->insert(bytes->end(), data, data + length); fs->end(op); return length;
}
void File::flush() {
  const std::string op = "flush:" + path;
  if (!fs->begin(op) && bytes->size() > initial_size) bytes->pop_back();
  fs->end(op);
}
void File::close() {
  const std::string op = std::string("close:") + (writable ? "w:" : "r:") + path;
  if (!fs->begin(op) && writable && bytes->size() > initial_size) (*bytes)[initial_size] ^= 1;
  if (writable && !fs->replacement_on_close.empty() && fs->replacement_path == path) *bytes = fs->replacement_on_close;
  valid = false; fs->end(op);
}

bool File::seek(uint32_t position) {
  const std::string op = "seek:" + path;
  if (!valid || directory || !fs->begin(op) || position > bytes->size()) return false;
  cursor = position; fs->end(op); return true;
}
#define FILE_O_WRITE 1
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
struct lfs_info {};
static constexpr int LFS_ERR_NOENT = -2;
static int lfs_stat(MemoryFS* fs, const char* path, lfs_info*) {
  const std::string op = std::string("stat:") + path;
  const bool okay = fs->begin(op);
  const bool present = fs->files.count(path) || fs->directories.count(path);
  fs->end(op);
  return !okay ? -5 : present ? 0 : LFS_ERR_NOENT;
}
#endif
