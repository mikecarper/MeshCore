#pragma once

// In-memory filesystem boundary only. Cache/container/manifest code remains
// production code; this fixture never opens a host file or a real SD card.
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <map>
#include <string>
#include <vector>

constexpr int O_RDONLY = 1, O_WRONLY = 2, O_RDWR = 4;
constexpr int O_CREAT = 8, O_TRUNC = 16, O_EXCL = 32;
namespace sd_test {
struct Node {
  bool directory = false;
  std::vector<uint8_t> bytes;
};
struct Medium {
  std::map<std::string, Node> nodes;
  bool connected = true, mount_ok = true, rename_ok = true;
  bool file_sync_ok = true, remove_ok = true, preallocate_ok = true;
  bool write_ok = true;
  unsigned card_sync_calls = 0, fail_card_sync_at = 0;
  uint32_t clusters = 32768, free_clusters = 16384, cluster_bytes = 4096;
  void reset() { *this = Medium(); }
};
inline Medium medium;
} // namespace sd_test

class SdCard {
public:
  bool syncDevice() {
    auto& m = sd_test::medium;
    return m.connected && ++m.card_sync_calls != m.fail_card_sync_at;
  }
  uint32_t sectorCount() const { return sd_test::medium.connected ? 1048576 : 0; }
};

class FsFile {
  std::string _path;
  size_t _position = 0, _next = 0;
  std::vector<std::string> _children;
  sd_test::Node* node() const {
    auto& m = sd_test::medium;
    auto it = m.nodes.find(_path);
    return m.connected && !_path.empty() && it != m.nodes.end() ? &it->second : nullptr;
  }
public:
  explicit operator bool() const { return node() != nullptr; }
  bool open(const char* path, int flags) {
    close();
    auto& m = sd_test::medium;
    if (!m.connected || !path || !*path) return false;
    auto it = m.nodes.find(path);
    if ((flags & O_EXCL) && it != m.nodes.end()) return false;
    if (it == m.nodes.end()) {
      if (!(flags & O_CREAT)) return false;
      it = m.nodes.emplace(path, sd_test::Node{}).first;
    }
    if (flags & O_TRUNC) it->second.bytes.clear();
    _path = path;
    if (it->second.directory) {
      const std::string prefix = _path + "/";
      for (const auto& entry : m.nodes) {
        if (entry.first.compare(0, prefix.size(), prefix) == 0 &&
            entry.first.find('/', prefix.size()) == std::string::npos)
          _children.push_back(entry.first);
      }
    }
    return true;
  }
  bool openNext(FsFile* directory, int flags) {
    close();
    return directory && directory->isDir() && directory->_next < directory->_children.size()
        && open(directory->_children[directory->_next++].c_str(), flags);
  }
  void close() { _path.clear(); _position = 0; _next = 0; _children.clear(); }
  bool isDir() const { auto* n = node(); return n && n->directory; }
  uint64_t fileSize() const { auto* n = node(); return n ? n->bytes.size() : 0; }
  bool getName(char* out, size_t cap) const {
    if (!node() || !out || !cap) return false;
    const auto slash = _path.rfind('/');
    const std::string name = _path.substr(slash == std::string::npos ? 0 : slash + 1);
    const size_t count = std::min(name.size(), cap - 1);
    memcpy(out, name.data(), count); out[count] = 0; return name.size() < cap;
  }
  bool seekSet(uint64_t position) {
    if (!node() || position > fileSize()) return false;
    _position = static_cast<size_t>(position); return true;
  }
  int read(void* out, size_t count) {
    auto* n = node();
    if (!n || !out) return -1;
    count = std::min(count, n->bytes.size() - _position);
    memcpy(out, n->bytes.data() + _position, count); _position += count;
    return static_cast<int>(count);
  }
  size_t write(const void* data, size_t count) {
    auto* n = node();
    if (!n || !sd_test::medium.write_ok || !data || _position > SIZE_MAX - count) return 0;
    n->bytes.resize(std::max(n->bytes.size(), _position + count), 0xFF);
    memcpy(n->bytes.data() + _position, data, count); _position += count; return count;
  }
  bool preAllocate(uint32_t count) {
    auto* n = node();
    if (!n || !sd_test::medium.preallocate_ok) return false;
    n->bytes.resize(count, 0xFF); return true;
  }
  bool sync() const { return node() && sd_test::medium.file_sync_ok; }
};

class SdFs {
  SdCard _card;
public:
  bool begin() { return sd_test::medium.connected && sd_test::medium.mount_ok; }
  void end() {}
  bool exists(const char* path) const {
    return sd_test::medium.connected && sd_test::medium.nodes.count(path);
  }
  bool mkdir(const char* path) {
    return sd_test::medium.connected &&
        sd_test::medium.nodes.emplace(path, sd_test::Node{true, {}}).second;
  }
  FsFile open(const char* path, int flags) { FsFile f; f.open(path, flags); return f; }
  bool remove(const char* path) {
    return sd_test::medium.connected && sd_test::medium.remove_ok &&
        sd_test::medium.nodes.erase(path) != 0;
  }
  bool rename(const char* from, const char* to) {
    auto& m = sd_test::medium;
    auto it = m.nodes.find(from);
    if (!m.connected || !m.rename_ok || it == m.nodes.end() || m.nodes.count(to)) return false;
    m.nodes.emplace(to, std::move(it->second)); m.nodes.erase(it); return true;
  }
  uint32_t clusterCount() const { return sd_test::medium.clusters; }
  uint32_t freeClusterCount() const { return sd_test::medium.free_clusters; }
  uint32_t bytesPerCluster() const { return sd_test::medium.cluster_bytes; }
  SdCard* card() { return sd_test::medium.connected ? &_card : nullptr; }
};
