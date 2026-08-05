#include "OtaCacheSdNrf52.h"

#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)

#include <SdFat.h>
#include <new>
#include <stdio.h>
#include <string.h>
#include "MotaContainer.h"
#include "OtaByteIO.h"
#include "OtaFormat.h"
#include "OtaManager.h"
#include "OtaStoreSdNrf52.h"

namespace mesh {
namespace ota {

const char* const OtaCacheSdNrf52::DIR = "/mota";
const char* const OtaCacheSdNrf52::DISABLED_PATH = "/mota/cache.off";

OtaCacheSdNrf52::OtaCacheSdNrf52() : _file(new (std::nothrow) FsFile()) {}

OtaCacheSdNrf52::~OtaCacheSdNrf52() {
  if (_file) { _file->close(); delete _file; }
}

void OtaCacheSdNrf52::fail(const char* message) {
  strncpy(_error, message ? message : "SD archive error", sizeof(_error) - 1);
  _error[sizeof(_error) - 1] = 0;
}

void OtaCacheSdNrf52::attach(OtaStoreSdNrf52& owner) {
  if (_owner == &owner) return;
  resetMedia();
  _owner = &owner;
}

void OtaCacheSdNrf52::resetMedia() {
  if (_file && *_file) _file->close();
  _count = 0;
  _total = 0;
  _initialized = false;
  _enabled = true;
  _error[0] = 0;
}

bool OtaCacheSdNrf52::ready() {
  if (!_owner || !_file) { fail("SD archive is not attached"); return false; }
  if (!_owner->mount()) { fail(_owner->last_error()); return false; }
  return true;
}

bool OtaCacheSdNrf52::ensureDir() {
  if (_owner->_sd->exists(DIR)) return true;
  if (_owner->_sd->mkdir(DIR)) return true;
  fail("SD archive directory create failed");
  return false;
}

bool OtaCacheSdNrf52::initialize() {
  if (_initialized) return true;
  _error[0] = 0;
  if (!ready() || !ensureDir()) return false;
  _enabled = !_owner->_sd->exists(DISABLED_PATH);
  return rescan();
}

static int cache_hex_nibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

bool OtaCacheSdNrf52::describeFile(FsFile& file, MotaDesc& out) const {
  uint64_t size64 = file.fileSize();
  if (size64 < 8 + MOTA_MFL + 5 || size64 > UINT32_MAX) return false;
  const uint32_t total = (uint32_t)size64;
  uint8_t hdr[8], manifest[MOTA_MFL], trailer[5];
  if (!file.seekSet(0) || file.read(hdr, sizeof(hdr)) != (int)sizeof(hdr) ||
      memcmp(hdr, MOTA_MAGIC, 4) != 0 || rd_u32le(hdr + 4) != total ||
      !file.seekSet(8) || file.read(manifest, sizeof(manifest)) != (int)sizeof(manifest) ||
      !file.seekSet(total - sizeof(trailer)) ||
      file.read(trailer, sizeof(trailer)) != (int)sizeof(trailer) ||
      memcmp(trailer, MOTA_TRAILER, sizeof(trailer)) != 0) return false;

  MotaManifest m;
  if (!mota_parse_manifest(manifest, sizeof(manifest), m)) return false;
  if (m.hash_algo != HASH_ALGO_SHA256 || m.block_size() == 0 || m.block_size() > OTA_MAX_BLOCK ||
      (uint64_t)m.block_count * 4 > OTA_PROOFGEN_SCRATCH) return false;
  uint64_t leaves_off = 8 + MOTA_MFL;
  uint64_t payload_off = leaves_off + (uint64_t)m.block_count * 4;
  if (payload_off > UINT32_MAX || payload_off + m.payload_size + 5 != total) return false;

  memcpy(out.mid, m.merkle_root, 4);
  out.target_id = m.target_id;
  out.fw_version = m.fw_version;
  out.codec_id = m.codec_id;
  out.flags = m.flags;
  out.block_size_log2 = m.block_size_log2;
  out.total_size = total;
  out.leaves_off = (uint32_t)leaves_off;
  out.block_count = m.block_count;
  out.payload_off = (uint32_t)payload_off;
  out.payload_size = m.payload_size;
  return true;
}

bool OtaCacheSdNrf52::rescan() {
  _initialized = false;
  _count = 0;
  if (!ready() || !ensureDir()) return false;

  FsFile dir = _owner->_sd->open(DIR, O_RDONLY);
  if (!dir || !dir.isDir()) { fail("SD archive directory open failed"); return false; }
  FsFile file;
  while (_count < OTA_SD_CACHE_MAX && file.openNext(&dir, O_RDONLY)) {
    if (!file.isDir()) {
      char name[24] = {0};
      file.getName(name, sizeof(name));
      size_t len = strlen(name);
      if (len == 13 && strcmp(name + 8, ".mota") == 0) {
        uint8_t name_mid[4];
        bool name_ok = true;
        for (uint8_t i = 0; i < 4; i++) {
          int hi = cache_hex_nibble(name[i * 2]);
          int lo = cache_hex_nibble(name[i * 2 + 1]);
          if (hi < 0 || lo < 0) { name_ok = false; break; }
          name_mid[i] = (uint8_t)((hi << 4) | lo);
        }
        MotaDesc d;
        if (name_ok && describeFile(file, d) && memcmp(name_mid, d.mid, 4) == 0)
          _entries[_count++] = d;
      }
    }
    file.close();
  }
  dir.close();
  _initialized = true;
  _error[0] = 0;
  return true;
}

void OtaCacheSdNrf52::pathFor(const uint8_t mid[4], const char* ext,
                              char* out, size_t cap) const {
  snprintf(out, cap, "%s/%02x%02x%02x%02x.%s", DIR,
           mid[0], mid[1], mid[2], mid[3], ext);
}

bool OtaCacheSdNrf52::contains(const uint8_t mid[4]) {
  if (!mid || !initialize()) return false;
  for (uint8_t i = 0; i < _count; i++)
    if (memcmp(_entries[i].mid, mid, 4) == 0) return true;

  // Recover a valid file that was published just before a card-sync/rescan failure. Existence alone is
  // never enough: malformed files must remain eligible for quarantine and a clean re-download.
  char path[32];
  pathFor(mid, "mota", path, sizeof(path));
  FsFile file = _owner->_sd->open(path, O_RDONLY);
  MotaDesc d;
  bool valid = file && describeFile(file, d) && memcmp(d.mid, mid, 4) == 0;
  file.close();
  if (!valid) return false;
  if (_count < OTA_SD_CACHE_MAX) _entries[_count++] = d;
  return true;
}

bool OtaCacheSdNrf52::quarantineFinal(const uint8_t mid[4]) {
  char final_path[32];
  pathFor(mid, "mota", final_path, sizeof(final_path));
  if (!_owner->_sd->exists(final_path)) return true;
  for (uint8_t i = 0; i < 10; i++) {
    char ext[8], bad_path[32];
    if (i == 0) strcpy(ext, "bad");
    else snprintf(ext, sizeof(ext), "bad%u", (unsigned)i);
    pathFor(mid, ext, bad_path, sizeof(bad_path));
    if (!_owner->_sd->exists(bad_path) && _owner->_sd->rename(final_path, bad_path)) return true;
  }
  fail("SD archive invalid file quarantine failed");
  return false;
}

bool OtaCacheSdNrf52::setAutoCaptureEnabled(bool enabled) {
  if (!initialize()) return false;
  if (enabled) {
    if (_owner->_sd->exists(DISABLED_PATH) && !_owner->_sd->remove(DISABLED_PATH)) {
      fail("SD archive enable marker remove failed");
      return false;
    }
  } else {
    FsFile marker = _owner->_sd->open(DISABLED_PATH, O_WRONLY | O_CREAT | O_TRUNC);
    static const uint8_t off[] = {'o', 'f', 'f', '\n'};
    if (!marker || marker.write(off, sizeof(off)) != sizeof(off) || !marker.sync()) {
      marker.close();
      fail("SD archive disable marker write failed");
      return false;
    }
    marker.close();
  }
  _enabled = enabled;                     // runtime mirrors the marker even if the final device sync reports late
  if (_owner->_sd->card() && !_owner->_sd->card()->syncDevice()) {
    fail("SD archive setting sync failed");
    return false;
  }
  _error[0] = 0;
  return true;
}

void OtaCacheSdNrf52::set_mid(const uint8_t mid[4]) {
  if (mid) memcpy(_mid, mid, 4);
  else memset(_mid, 0, sizeof(_mid));
}

bool OtaCacheSdNrf52::begin(uint32_t total_size) {
  _total = 0;
  if (total_size < 8 + MOTA_MFL + 5 || !initialize()) return false;
  if ((_mid[0] | _mid[1] | _mid[2] | _mid[3]) == 0) { fail("SD archive mid is unset"); return false; }
  char final_path[32], part_path[32];
  pathFor(_mid, "mota", final_path, sizeof(final_path));
  pathFor(_mid, "part", part_path, sizeof(part_path));
  if (contains(_mid)) { fail("SD archive already contains this mOTA"); return false; }
  if (_owner->_sd->exists(final_path) && !quarantineFinal(_mid)) return false;
  // begin() means resumeStaged() already rejected any old partial. Drop it before measuring space so a
  // corrupt/obsolete .part is not double-counted against the new allocation and the install reserve.
  if (*_file) _file->close();
  if (_owner->_sd->exists(part_path) && !_owner->_sd->remove(part_path)) {
    fail("SD archive obsolete partial remove failed");
    return false;
  }
  const uint32_t cluster_count = _owner->_sd->clusterCount();
  const uint32_t free_clusters = _owner->_sd->freeClusterCount();
  const uint32_t bytes_per_cluster = _owner->_sd->bytesPerCluster();
  const uint64_t free_bytes = (uint64_t)free_clusters * bytes_per_cluster;
  if (cluster_count == 0 || bytes_per_cluster == 0 || free_clusters > cluster_count) {
    fail("SD archive free-space query failed");
    return false;
  }
  if (free_bytes < (uint64_t)total_size + OTA_SD_CACHE_RESERVE_BYTES) {
    fail("SD archive free-space reserve reached");
    return false;
  }
  if (!_file->open(part_path, O_RDWR | O_CREAT | O_EXCL)) {
    fail("SD archive partial create failed");
    return false;
  }
  if (!_file->preAllocate(total_size)) {
    // preAllocate() requires one contiguous cluster run. Retry with normal sequential writes so a
    // fragmented card with enough total free space remains usable for archive files (unlike boot staging,
    // archive files are read through FAT and do not need contiguous sectors).
    _file->close();
    _owner->_sd->remove(part_path);
    if (!_file->open(part_path, O_RDWR | O_CREAT | O_EXCL)) {
      fail("SD archive fragmented allocation create failed");
      return false;
    }
    uint8_t erased[512];
    memset(erased, 0xFF, sizeof(erased));
    uint32_t left = total_size;
    while (left) {
      uint32_t n = left > sizeof(erased) ? sizeof(erased) : left;
      if (_file->write(erased, n) != n) {
        fail("SD archive lacks allocatable space");
        _file->close();
        _owner->_sd->remove(part_path);
        return false;
      }
      left -= n;
    }
    if (!_file->sync()) {
      fail("SD archive fragmented allocation sync failed");
      _file->close();
      _owner->_sd->remove(part_path);
      return false;
    }
  }
  _total = total_size;
  return true;
}

bool OtaCacheSdNrf52::set_meta_size(uint32_t meta_bytes) {
  if (!_total || meta_bytes > _total - 5) return false;
  uint8_t erased[512];
  memset(erased, 0xFF, sizeof(erased));
  for (uint32_t off = 0; off < meta_bytes; ) {
    uint32_t n = meta_bytes - off;
    if (n > sizeof(erased)) n = sizeof(erased);
    if (!write(off, erased, n)) return false;
    off += n;
  }
  return true;
}

bool OtaCacheSdNrf52::write(uint32_t off, const uint8_t* data, uint32_t len) {
  if (!_file || !*_file || (uint64_t)off + len > _total || !_file->seekSet(off) ||
      _file->write(data, len) != len) {
    fail("SD archive write failed");
    return false;
  }
  return true;
}

bool OtaCacheSdNrf52::read(uint32_t off, uint8_t* buf, uint32_t len) const {
  return _file && *_file && (uint64_t)off + len <= _total && _file->seekSet(off) &&
         _file->read(buf, len) == (int)len;
}

uint32_t OtaCacheSdNrf52::capacity() const {
  if (!_owner || !_owner->_mounted || !_owner->_sd || !_owner->_sd->card()) return 0;
  uint64_t bytes = (uint64_t)_owner->_sd->card()->sectorCount() * 512;
  return bytes > UINT32_MAX ? UINT32_MAX : (uint32_t)bytes;
}

void OtaCacheSdNrf52::checkpoint() {
  if (_file && *_file) {
    _file->sync();
    if (_owner && _owner->_sd->card()) _owner->_sd->card()->syncDevice();
  }
}

bool OtaCacheSdNrf52::finalize() {
  if (!_file || !*_file || !_total || !_file->sync()) { fail("SD archive sync failed"); return false; }
  if (_owner->_sd->card() && !_owner->_sd->card()->syncDevice()) {
    fail("SD archive card sync failed");
    return false;
  }
  char final_path[32], part_path[32];
  pathFor(_mid, "mota", final_path, sizeof(final_path));
  pathFor(_mid, "part", part_path, sizeof(part_path));
  _file->close();

  FsFile check = _owner->_sd->open(part_path, O_RDONLY);
  MotaDesc d;
  bool valid = check && describeFile(check, d) && memcmp(d.mid, _mid, 4) == 0;
  check.close();
  if (!valid) { fail("SD archive final container invalid"); return false; }

  if (_owner->_sd->exists(final_path)) {
    if (!_owner->_sd->remove(part_path)) {
      fail("SD archive duplicate cleanup failed");
      return false;
    }
  } else if (!_owner->_sd->rename(part_path, final_path)) {
    fail("SD archive publish rename failed");
    return false;
  }
  bool device_synced = !_owner->_sd->card() || _owner->_sd->card()->syncDevice();
  bool scanned = rescan();                  // register a valid renamed file even if device sync reported late
  if (!device_synced) { fail("SD archive publish sync failed"); return false; }
  return scanned;
}

bool OtaCacheSdNrf52::reopen() {
  _total = 0;
  if (!initialize()) return false;
  char part_path[32];
  pathFor(_mid, "part", part_path, sizeof(part_path));
  if (*_file) _file->close();
  if (!_file->open(part_path, O_RDWR)) return false;
  uint8_t hdr[8], manifest[MOTA_MFL];
  if (_file->fileSize() < 8 + MOTA_MFL + 5 || !_file->seekSet(0) ||
      _file->read(hdr, sizeof(hdr)) != (int)sizeof(hdr) ||
      memcmp(hdr, MOTA_MAGIC, 4) != 0 || !_file->seekSet(8) ||
      _file->read(manifest, sizeof(manifest)) != (int)sizeof(manifest)) {
    _file->close();
    return false;
  }
  uint32_t total = rd_u32le(hdr + 4);
  MotaManifest m;
  if (total != _file->fileSize() || !mota_parse_manifest(manifest, sizeof(manifest), m) ||
      memcmp(m.merkle_root, _mid, 4) != 0) {
    _file->close();
    return false;
  }
  _total = total;
  return true;
}

void OtaCacheSdNrf52::clear() {
  if (_file && *_file) _file->close();
  if (_owner && ready()) {
    char part_path[32];
    pathFor(_mid, "part", part_path, sizeof(part_path));
    _owner->_sd->remove(part_path);
  }
  _total = 0;
}

bool OtaCacheSdNrf52::describe(uint8_t idx, MotaDesc& out) {
  if (!_initialized || idx >= _count) return false;
  out = _entries[idx];
  return true;
}

bool OtaCacheSdNrf52::read(uint8_t idx, uint32_t off, uint8_t* buf, uint32_t len) {
  if (!_initialized || idx >= _count || (uint64_t)off + len > _entries[idx].total_size || !ready())
    return false;
  char path[32];
  pathFor(_entries[idx].mid, "mota", path, sizeof(path));
  FsFile file = _owner->_sd->open(path, O_RDONLY);
  bool ok = file && file.seekSet(off) && file.read(buf, len) == (int)len;
  file.close();
  return ok;
}

} // namespace ota
} // namespace mesh

#endif
