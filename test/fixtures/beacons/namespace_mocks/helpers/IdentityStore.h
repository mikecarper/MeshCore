#pragma once

// Extend the existing in-memory file adapter only for beacon namespace faults.
// File remains bound to the same base object and uses the real read/write API.
#define MemoryFS BeaconBaseFS
#include "../../../radio_profiles/mocks/helpers/IdentityStore.h"
#undef MemoryFS
#include <array>
#include <cassert>

enum { LFS_ERR_NOENT = -2, LFS_ERR_IO = -5 };
struct lfs_info {};
class MemoryFS : public BeaconBaseFS {
 public:
  enum Fault { None, Reject, Noop, LateError, Metadata, BothNames, NeitherName };
  std::array<Fault, 8> faults{};
  unsigned renames = 0, metadata_probes = 0, fail_metadata_probe = 0;
  unsigned error_probe = 1;
  bool metadata_error = false;
  void _lockFS() {}
  void _unlockFS() {}
  MemoryFS* _getFS() { return this; }
  bool rename(const char* from, const char* to) {
    assert(++renames < faults.size());
    const Fault fault = faults[renames];
    if (fault == Reject) return false;
    if (fault == Noop) return true;
    if (fault == BothNames || fault == NeitherName) {
      assert(exists(from) && !exists(to));
      if (fault == BothNames) files[to] = files.at(from);
      else files.erase(from);
      return true;
    }
    const bool moved = BeaconBaseFS::rename(from, to);
    if (fault == Metadata) {
      assert(moved);
      fail_metadata_probe = metadata_probes + error_probe;
    }
    return fault == LateError ? false : moved;
  }
  void clearFaults() {
    faults.fill(None); renames = 0; fail_rename = 0;
    fail_metadata_probe = 0; metadata_error = false;
  }
};
inline int lfs_stat(MemoryFS* fs, const char* path, lfs_info*) {
  ++fs->metadata_probes;
  if (fs->metadata_error || fs->metadata_probes == fs->fail_metadata_probe)
    return LFS_ERR_IO;
  return fs->exists(path) ? 0 : LFS_ERR_NOENT;
}
