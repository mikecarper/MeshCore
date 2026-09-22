#pragma once

#if defined(NRF52_PLATFORM)

#include <Adafruit_LittleFS.h>
#include <new>
#include <stdint.h>
#include <string.h>

namespace mesh {
namespace storage {

static constexpr char BAD_FILESYSTEM_NODE_NAME[] = "bad file system";

// Emergency-only volatile storage. It is allocated only after physical
// primary flash has failed every retry and a destructive reinitialization.
// Eight KiB is enough for identity/preferences and basic node operation while
// keeping the normal boot's RAM footprint unchanged.
class RamFallbackFileSystem {
  static constexpr uint32_t BLOCK_SIZE = 128;
  static constexpr uint32_t STORAGE_SIZE = 8UL * 1024UL;

  alignas(4) uint8_t _storage[STORAGE_SIZE];
  struct lfs_config _config;
  Adafruit_LittleFS _filesystem;

  static RamFallbackFileSystem* owner(const struct lfs_config* config) {
    return static_cast<RamFallbackFileSystem*>(config->context);
  }

  static bool validRange(lfs_block_t block, lfs_off_t off, lfs_size_t size) {
    return block < STORAGE_SIZE / BLOCK_SIZE && off <= BLOCK_SIZE
        && size <= BLOCK_SIZE - off;
  }

  static int read(const struct lfs_config* config, lfs_block_t block,
                  lfs_off_t off, void* buffer, lfs_size_t size) {
    if (!validRange(block, off, size)) return LFS_ERR_IO;
    memcpy(buffer, owner(config)->_storage + block * BLOCK_SIZE + off, size);
    return 0;
  }

  static int program(const struct lfs_config* config, lfs_block_t block,
                     lfs_off_t off, const void* buffer, lfs_size_t size) {
    if (!validRange(block, off, size)) return LFS_ERR_IO;
    uint8_t* destination = owner(config)->_storage + block * BLOCK_SIZE + off;
    const uint8_t* source = static_cast<const uint8_t*>(buffer);
    for (lfs_size_t i = 0; i < size; ++i) destination[i] &= source[i];
    return 0;
  }

  static int erase(const struct lfs_config* config, lfs_block_t block) {
    if (!validRange(block, 0, BLOCK_SIZE)) return LFS_ERR_IO;
    memset(owner(config)->_storage + block * BLOCK_SIZE, 0xFF, BLOCK_SIZE);
    return 0;
  }

  static int sync(const struct lfs_config*) { return 0; }

public:
  RamFallbackFileSystem() : _config{}, _filesystem(&_config) {
    memset(_storage, 0xFF, sizeof(_storage));
    _config.context = this;
    _config.read = read;
    _config.prog = program;
    _config.erase = erase;
    _config.sync = sync;
    _config.read_size = 16;
    _config.prog_size = 16;
    _config.block_size = BLOCK_SIZE;
    _config.block_count = STORAGE_SIZE / BLOCK_SIZE;
    _config.lookahead = 64;
  }

  bool begin() {
    return _filesystem.format() && _filesystem.begin();
  }

  Adafruit_LittleFS& filesystem() { return _filesystem; }
};

inline RamFallbackFileSystem* createRamFallbackFileSystem() {
  RamFallbackFileSystem* fallback = new (std::nothrow) RamFallbackFileSystem();
  if (fallback != nullptr && fallback->begin()) return fallback;
  delete fallback;
  return nullptr;
}

} // namespace storage
} // namespace mesh

#endif // NRF52_PLATFORM
