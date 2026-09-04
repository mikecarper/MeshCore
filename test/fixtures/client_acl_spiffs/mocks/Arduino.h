#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

#define MESH_DEBUG_PRINTLN(...) ((void)0)

class FakeFilesystem;
struct FakeFileHandle {
  FakeFilesystem* fs;
  std::string path;
  bool directory;
  bool writable;
  bool open = true;
  size_t position = 0;
};

class File {
  std::shared_ptr<FakeFileHandle> handle;
public:
  File() = default;
  File(FakeFilesystem* fs, const std::string& path, bool directory, bool writable)
      : handle(std::make_shared<FakeFileHandle>(FakeFileHandle{fs, path, directory, writable})) {}
  explicit operator bool() const { return handle && handle->open; }
  bool isDirectory() const { return *this && handle->directory; }
  size_t size() const;
  int read(uint8_t* output, size_t length);
  size_t write(const uint8_t* input, size_t length);
  void close();
};

class FakeFilesystem {
public:
  std::map<std::string, std::vector<uint8_t>> files;
  std::set<std::string> unreadable;
  std::set<std::string> directories_on_read;
  std::set<std::string> directories_on_write;
  std::map<std::string, size_t> read_open_count;
  std::string truncate_path;
  size_t truncate_on_read_open = 0;
  std::string fail_rename_from;
  std::set<std::string> fail_rename_from_paths;
  std::string fail_remove;
  std::string fail_open_write;
  std::string corrupt_on_close;
  size_t write_budget = std::numeric_limits<size_t>::max();
  size_t bytes_written = 0;
  size_t directory_closes = 0;
  size_t missing_read_opens = 0;

  bool exists(const char* path) const { return files.count(path) != 0; }
  File open(const char* path, const char* mode = "r", bool = false) {
    const std::string name(path);
    if (mode[0] == 'r') {
      ++read_open_count[name];
      if (unreadable.count(name)) return File();
      if (!exists(path)) {
        ++missing_read_opens;
        // The real Arduino-ESP32 SPIFFS opendir fallback for missing paths.
        return File(this, name, true, false);
      }
      if (truncate_path == name && read_open_count[name] == truncate_on_read_open)
        files[name].resize(1);
      return File(this, name, directories_on_read.count(name) != 0, false);
    }
    if (fail_open_write == name) return File();
    if (directories_on_write.count(name)) return File(this, name, true, true);
    files[name].clear();
    return File(this, name, false, true);
  }
  bool remove(const char* path) {
    return fail_remove != path && files.erase(path) != 0;
  }
  bool rename(const char* from, const char* to) {
    if (fail_rename_from == from || fail_rename_from_paths.count(from)
        || !exists(from) || exists(to)) return false;
    files[to] = files[from];
    files.erase(from);
    return true;
  }
};

inline size_t File::size() const {
  if (!*this || handle->directory) return 0;
  auto found = handle->fs->files.find(handle->path);
  return found == handle->fs->files.end() ? 0 : found->second.size();
}
inline int File::read(uint8_t* output, size_t length) {
  if (!*this || handle->directory) return 0;
  auto found = handle->fs->files.find(handle->path);
  if (found == handle->fs->files.end() || handle->position >= found->second.size()) return 0;
  length = std::min(length, found->second.size() - handle->position);
  std::memcpy(output, found->second.data() + handle->position, length);
  handle->position += length;
  return static_cast<int>(length);
}
inline size_t File::write(const uint8_t* input, size_t length) {
  if (!*this || handle->directory || !handle->writable) return 0;
  auto& fs = *handle->fs;
  length = std::min(length, fs.write_budget);
  if (length == 0) return 0;
  fs.write_budget -= length;
  fs.bytes_written += length;
  auto& data = fs.files[handle->path];
  data.resize(handle->position + length);
  std::memcpy(data.data() + handle->position, input, length);
  handle->position += length;
  return length;
}
inline void File::close() {
  if (!*this) return;
  if (handle->directory) ++handle->fs->directory_closes;
  if (handle->writable && handle->fs->corrupt_on_close == handle->path) {
    auto& data = handle->fs->files[handle->path];
    if (!data.empty()) data.back() ^= 0x80;
  }
  handle->open = false;
}
