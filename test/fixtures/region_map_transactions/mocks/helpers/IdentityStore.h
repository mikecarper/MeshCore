#pragma once

// Native adapter for the public File/FILESYSTEM operations used by RegionMap
// and the real AtomicFileWriter/ContactFileTransaction implementations.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <map>
#include <set>
#include <string>
#include <vector>

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
  int read() { uint8_t value; return read(&value, 1) == 1 ? value : -1; }
  size_t write(const uint8_t* data, size_t size);
  bool setBufferSize(size_t size) { return size != 0; }
  void flush() {}
  void close() { fs_ = nullptr; }
};

class MemoryFS {
public:
  std::map<std::string, std::vector<uint8_t>> files;
  std::set<std::string> failed_rename_sources;
  std::set<std::string> metadata_errors;
  bool fail_write_open = false;
  bool fail_read_open = false;
  int fail_write_after = -1;
  size_t capacity = std::numeric_limits<size_t>::max();
  unsigned write_calls = 0, open_writes = 0, remove_calls = 0, rename_calls = 0;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM) || defined(RP2040_PLATFORM)
  bool rename_replaces = true;
#else
  bool rename_replaces = false;
#endif

  size_t used() const {
    size_t total = 0;
    for (const auto& item : files) total += item.second.size();
    return total;
  }
  bool exists(const char* path) const { return files.count(path) != 0; }
  bool remove(const char* path) {
    ++remove_calls;
    return files.erase(path) != 0;
  }
  bool rename(const char* from, const char* to) {
    ++rename_calls;
    if (failed_rename_sources.count(from) || !exists(from)
        || (!rename_replaces && exists(to))) return false;
    files[to] = files.at(from);
    files.erase(from);
    return true;
  }
  File open(const char* path, const char* mode = "r", bool = false) {
    if (*mode == 'w') {
      ++open_writes;
      if (fail_write_open) return {};
      files[path].clear();
    } else if (fail_read_open) {
      return {};
    }
    if (!exists(path)) return {};
    return File(this, path);
  }
  File open(const char* path, uint8_t mode) {
    return open(path, mode == 1 ? "w" : "r");
  }
};

inline MemoryFS* metadata_filesystem = nullptr;
enum { FILE_O_READ = 0, FILE_O_WRITE = 1 };

inline bool File::open(const char* path, uint8_t mode) {
  if (!fs_) return false;
  *this = fs_->open(path, mode);
  return fs_ != nullptr;
}
inline size_t File::size() const {
  return fs_ ? fs_->files.at(path_).size() : 0;
}
inline int File::read(uint8_t* data, size_t size) {
  if (!fs_) return -1;
  const auto& bytes = fs_->files.at(path_);
  size = std::min(size, bytes.size() - cursor_);
  if (size != 0) std::memcpy(data, bytes.data() + cursor_, size);
  cursor_ += size;
  return static_cast<int>(size);
}
inline size_t File::write(const uint8_t* data, size_t size) {
  if (!fs_) return 0;
  ++fs_->write_calls;
  auto& bytes = fs_->files.at(path_);
  if (fs_->fail_write_after >= 0) {
    const size_t limit = static_cast<size_t>(fs_->fail_write_after);
    size = bytes.size() >= limit ? 0 : std::min(size, limit - bytes.size());
  }
  const size_t used = fs_->used();
  size = used >= fs_->capacity ? 0 : std::min(size, fs_->capacity - used);
  if (size != 0) bytes.insert(bytes.end(), data, data + size);
  return size;
}

#define FILESYSTEM MemoryFS
