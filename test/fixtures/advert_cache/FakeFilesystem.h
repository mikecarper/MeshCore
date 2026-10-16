#pragma once
#include <algorithm>
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <memory>
#include <string>
#include <vector>
#include <sys/stat.h>

class FakeFilesystem;
struct Stream {
  FakeFilesystem* fs;
  std::string path;
  bool writer, closed = false;
  size_t position = 0, buffer_size = 4096;
  std::vector<uint8_t> buffer;
  Stream(FakeFilesystem*, const char*, bool);
  void close();
  ~Stream() { close(); }
};
class File {
  std::shared_ptr<Stream> stream;
public:
  File() = default;
  File(FakeFilesystem*, const char*, bool);
  explicit operator bool() const { return stream && !stream->closed; }
  bool setBufferSize(size_t);
  size_t write(const uint8_t*, size_t);
  size_t read(uint8_t*, size_t);
  size_t size();
  void close() { if (stream) stream->close(); stream.reset(); }
};
class FakeFilesystem {
public:
  std::map<std::string, std::vector<uint8_t>> files;
  unsigned operations = 0, handles = 0, backend_writes = 0;
  unsigned rename_calls = 0, remove_calls = 0, config_calls = 0;
  unsigned metadata_calls = 0, fail_metadata_at = 0;
  unsigned fail_rename = 0, fail_remove = 0, fail_config = 0;
  bool fail_metadata = false, fail_open = false, partial_open = false;
  bool fail_read = false, short_write = false, fail_close = false, fail_format = false;
  size_t read_limit = SIZE_MAX;
  size_t max_backend_write = 0;
  bool exists(const char* path) const { return files.count(path) != 0; }
  bool mkdir(const char*) { ++operations; return true; }
  bool format() { ++operations; if (fail_format) return false; files.clear(); return true; }
  File open(const char* path, const char* mode = "r", bool = false) {
    ++operations;
    bool writer = mode[0] == 'w';
    if (fail_open) {
      if (writer && partial_open) files[path].clear();
      return File();
    }
    if (!writer && !exists(path)) return File();
    if (writer) files[path].clear();
    return File(this, path, writer);
  }
  bool rename(const char* from, const char* to) {
    ++operations; ++rename_calls;
    if (rename_calls == fail_rename || !exists(from) || exists(to)) return false;
    files[to] = files.at(from); files.erase(from); return true;
  }
  bool remove(const char* path) {
    ++operations; ++remove_calls;
    assert(exists(path) && "No VFS log for normal absence");
    if (remove_calls == fail_remove) return false;
    files.erase(path); return true;
  }
  void backend(const std::string& path, const std::vector<uint8_t>& bytes) {
    if (bytes.empty()) return;
    ++backend_writes;
    max_backend_write = std::max(max_backend_write, bytes.size());
    files[path].insert(files[path].end(), bytes.begin(), bytes.end());
  }
};
inline Stream::Stream(FakeFilesystem* f, const char* p, bool w)
    : fs(f), path(p), writer(w) { ++fs->handles; }
inline void Stream::close() {
  if (closed) return;
  ++fs->operations;
  if (writer && !fs->fail_close) fs->backend(path, buffer);
  buffer.clear(); closed = true; --fs->handles;
}
inline File::File(FakeFilesystem* fs, const char* path, bool writer)
    : stream(std::make_shared<Stream>(fs, path, writer)) {}
inline bool File::setBufferSize(size_t length) {
  assert(stream && stream->position == 0);
  ++stream->fs->operations; ++stream->fs->config_calls;
  if (stream->fs->config_calls == stream->fs->fail_config) return false;
  stream->buffer_size = length; return true;
}
inline size_t File::write(const uint8_t* data, size_t length) {
  assert(stream && stream->writer); ++stream->fs->operations;
  if (stream->fs->short_write && length) --length;
  size_t consumed = 0;
  while (consumed < length) {
    if (stream->buffer.size() == stream->buffer_size) {
      stream->fs->backend(stream->path, stream->buffer); stream->buffer.clear();
    }
    size_t n = std::min(length-consumed, stream->buffer_size-stream->buffer.size());
    stream->buffer.insert(stream->buffer.end(), data+consumed, data+consumed+n);
    consumed += n;
    if (stream->buffer.size() == stream->buffer_size) {
      stream->fs->backend(stream->path, stream->buffer); stream->buffer.clear();
    }
  }
  stream->position += length; return length;
}
inline size_t File::read(uint8_t* data, size_t length) {
  assert(stream && !stream->writer); ++stream->fs->operations;
  if (stream->fs->fail_read) return 0;
  const auto& bytes = stream->fs->files.at(stream->path);
  size_t n = std::min(std::min(length, stream->fs->read_limit),
                      bytes.size()-std::min(stream->position, bytes.size()));
  if (n) memcpy(data, bytes.data()+stream->position, n);
  stream->position += n; return n;
}
inline size_t File::size() {
  assert(stream); ++stream->fs->operations; return stream->fs->files.at(stream->path).size();
}
inline int fixtureStat(FakeFilesystem* fs, const char* path, struct stat*) {
  ++fs->operations;
  ++fs->metadata_calls;
  if (fs->fail_metadata || fs->metadata_calls == fs->fail_metadata_at) { errno = EIO; return -1; }
  assert(strncmp(path, "/spiffs", 7) == 0);
  if (!fs->exists(path+7)) { errno = ENOENT; return -1; }
  return 0;
}
namespace fs { using SPIFFSFS = FakeFilesystem; }
using esp_err_t = int;
constexpr int ESP_OK = 0;
inline int nvs_flash_erase() { return ESP_OK; }
