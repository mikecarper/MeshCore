#pragma once
#include <stddef.h>
#include <stdint.h>
#include <map>
#include <string>
#include <vector>
#include <cstring>
#include <algorithm>
class MemoryFS;
class File {
  MemoryFS* fs_ = nullptr;
  std::string path_;
  size_t cursor_ = 0;
 public:
  File() = default;
  File(MemoryFS* fs, const char* path) : fs_(fs), path_(path) {}
  operator bool() const { return fs_ != nullptr; }
  size_t size() const;
  size_t available() const { return size() - cursor_; }
  bool isDirectory() const { return false; }
  int read(uint8_t* data, size_t size);
  size_t write(const uint8_t* data, size_t size);
  void flush() {}
  void close() { fs_ = nullptr; }
};
class MemoryFS {
 public:
  std::map<std::string, std::vector<uint8_t>> files;
  bool fail_write = false;
  bool fail_remove = false;
  int fail_rename = 0;
  std::vector<std::string> fail_rename_from;
  bool mkdir(const char*) { return true; }
  bool exists(const char* path) const { return files.count(path); }
  bool remove(const char* path) { return !fail_remove && files.erase(path); }
  bool rename(const char* from, const char* to) {
    if (fail_rename > 0 && --fail_rename == 0) return false;
    if (std::find(fail_rename_from.begin(), fail_rename_from.end(), from) != fail_rename_from.end()) return false;
    if (!exists(from) || exists(to)) return false;
    files[to] = files[from]; files.erase(from); return true;
  }
  File open(const char* path, const char* mode = "r", bool = false) {
    if (*mode == 'w') files[path].clear();
    if (!exists(path)) return {};
    return File(this, path);
  }
};
inline size_t File::size() const { return fs_ ? fs_->files[path_].size() : 0; }
inline int File::read(uint8_t* data, size_t size) {
  if (!fs_) return -1;
  auto& bytes = fs_->files[path_];
  size = std::min(size, bytes.size() - cursor_);
  memcpy(data, bytes.data() + cursor_, size); cursor_ += size; return size;
}
inline size_t File::write(const uint8_t* data, size_t size) {
  if (!fs_ || fs_->fail_write) return 0;
  auto& bytes = fs_->files[path_];
  bytes.insert(bytes.end(), data, data + size); return size;
}
#define FILESYSTEM MemoryFS
