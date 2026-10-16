#pragma once

// Filesystem API boundary only. Production transaction, format validation,
// migration, command, and scheduling code is compiled unchanged by the runner.
#include <algorithm>
#include <cerrno>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <stdint.h>

class MemoryFS;
class File {
  MemoryFS* fs_ = nullptr;
  std::string path_;
  size_t cursor_ = 0;
public:
  File() = default;
  explicit File(MemoryFS& fs) : fs_(&fs) {}
  File(MemoryFS* fs, const char* path) : fs_(fs), path_(path) {}
  bool open(const char* path, uint8_t mode);
  operator bool() const { return fs_ != nullptr; }
  size_t size() const;
  bool isDirectory() const { return false; }
  int read(uint8_t* data, size_t size);
  size_t write(const uint8_t* data, size_t size);
  bool setBufferSize(size_t size) { return fs_ != nullptr && size != 0; }
  void flush() {}
  void close() { fs_ = nullptr; }
};

class MemoryFS {
public:
  std::map<std::string, std::vector<uint8_t>> files;
  std::set<std::string> fail_read_open, fail_write_open, fail_remove;
  std::set<std::pair<std::string, std::string>> fail_rename;
  std::map<std::string, size_t> read_limit, write_limit;
  bool fail_metadata = false, fail_final_read = false;
  int final_read_limit = -1;
  bool corrupt_publish = false, fail_metadata_after_publish = false;
  std::string publication_path = "/telemetry_tx";
  bool no_op_publish = false, late_publish_error = false;
  unsigned writes = 0, renames = 0;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  bool rename_replaces = true;
#else
  bool rename_replaces = false;
#endif
  bool exists(const char* path) const { return files.count(path); }
  bool remove(const char* path) {
    return !fail_remove.count(path) && files.erase(path);
  }
  bool rename(const char* from, const char* to) {
    ++renames;
    if (fail_rename.count({from, to}) || !exists(from)
        || (!rename_replaces && exists(to))) return false;
    if (from == publication_path + ".tmp" && no_op_publish) return true;
    files[to] = files[from]; files.erase(from);
    if (from == publication_path + ".tmp") {
      if (corrupt_publish) files[to][0] ^= 1;
      if (fail_final_read) fail_read_open.insert(to);
      if (final_read_limit >= 0) read_limit[to] = size_t(final_read_limit);
      if (fail_metadata_after_publish) fail_metadata = true;
      if (late_publish_error) return false;
    }
    return true;
  }
  File open(const char* path, const char* mode = "r", bool = false) {
    if (!path) return {};
    if (*mode == 'r' && fail_read_open.count(path)) return {};
    if (*mode == 'w') {
      if (fail_write_open.count(path)) return {};
      files[path].clear();
    }
    if (!exists(path)) return {};
    return File(this, path);
  }
  void _lockFS() {}
  void _unlockFS() {}
  MemoryFS* _getFS() { return this; }
  void clearFaults() {
    fail_read_open.clear(); fail_write_open.clear(); fail_remove.clear();
    fail_rename.clear(); read_limit.clear(); write_limit.clear();
    fail_metadata = fail_final_read = corrupt_publish = false;
    final_read_limit = -1;
    fail_metadata_after_publish = false;
    no_op_publish = late_publish_error = false;
  }
};

enum { FILE_O_READ = 0, FILE_O_WRITE = 1 };
inline bool File::open(const char* path, uint8_t mode) {
  if (!fs_) return false;
  *this = fs_->open(path, mode == FILE_O_WRITE ? "w" : "r");
  return fs_ != nullptr;
}
inline size_t File::size() const { return fs_ ? fs_->files[path_].size() : 0; }
inline int File::read(uint8_t* data, size_t size) {
  if (!fs_) return -1;
  const auto limit = fs_->read_limit.find(path_);
  if (limit != fs_->read_limit.end()) {
    if (cursor_ >= limit->second) return -1;
    size = std::min(size, limit->second - cursor_);
  }
  const auto& bytes = fs_->files.at(path_);
  size = std::min(size, bytes.size() - cursor_);
  if (size) memcpy(data, bytes.data() + cursor_, size);
  cursor_ += size;
  return static_cast<int>(size);
}
inline size_t File::write(const uint8_t* data, size_t size) {
  if (!fs_) return 0;
  ++fs_->writes;
  auto& bytes = fs_->files[path_];
  const auto limit = fs_->write_limit.find(path_);
  if (limit != fs_->write_limit.end()) {
    if (bytes.size() >= limit->second) return 0;
    size = std::min(size, limit->second - bytes.size());
  }
  bytes.insert(bytes.end(), data, data + size);
  return size;
}

#define FILESYSTEM MemoryFS
inline MemoryFS InternalFS;
struct lfs_info {};
static constexpr int LFS_ERR_NOENT = -2;
inline int lfs_stat(MemoryFS* fs, const char* path, lfs_info*) {
  return fs->fail_metadata ? -5 : (fs->exists(path) ? 0 : LFS_ERR_NOENT);
}
