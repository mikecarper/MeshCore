#pragma once

// SPIFFS cannot replace an existing name in one rename. Preserve the previous
// file as .bak until the verified replacement has its final name. Recovery of
// the rename gap happens before loading contacts on the next boot.
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
#include "IdentityStore.h"
#include "PersistentStoreFormat.h"
#include <stdio.h>

namespace mesh {
class ContactFileTransaction {
  FILESYSTEM* _fs;
  const char* _target;
  char _temp[48];
  char _backup[48];
  File _file;
  size_t _size = 0;
  uint32_t _crc = 0xffffffff;
  bool _ok = false;
  bool _finished = false;
public:
  static bool recover(FILESYSTEM* fs, const char* target) {
    char backup[48];
    snprintf(backup, sizeof(backup), "%s.bak", target);
    if (fs->exists(target)) return true;
    return !fs->exists(backup) || fs->rename(backup, target);
  }
  ContactFileTransaction(FILESYSTEM* fs, const char* target) : _fs(fs), _target(target) {
    snprintf(_temp, sizeof(_temp), "%s.tmp", target);
    snprintf(_backup, sizeof(_backup), "%s.bak", target);
    if (!recover(fs, target)) return;
    if (fs->exists(_temp) && !fs->remove(_temp)) return;
#if defined(RP2040_PLATFORM)
    _file = fs->open(_temp, "w");
#else
    _file = fs->open(_temp, "w", true);
#endif
    _ok = static_cast<bool>(_file);
  }
  ~ContactFileTransaction() {
    if (_file) _file.close();
    if (!_finished) _fs->remove(_temp);
  }
  operator bool() const { return _ok; }
  size_t write(const uint8_t* data, size_t len) {
    if (!_ok) return 0;
    const size_t wrote = _file.write(data, len);
    _ok = wrote == len;
    _size += wrote;
    _crc = storage::updateCRC32(_crc, data, wrote);
    return wrote;
  }
  bool commit(bool valid = true) {
    if (_finished) return false;
    if (_file) { _file.flush(); _file.close(); }
    bool ok = _ok && valid;
    File verify = _fs->open(_temp, "r");
    ok = ok && verify && verify.size() == _size;
    uint32_t crc = 0xffffffff;
    size_t remaining = _size;
    uint8_t buf[64];
    while (ok && remaining) {
      const size_t count = remaining < sizeof(buf) ? remaining : sizeof(buf);
      ok = verify.read(buf, count) == count;
      if (ok) { crc = storage::updateCRC32(crc, buf, count); remaining -= count; }
    }
    if (verify) verify.close();
    ok = ok && crc == _crc;
    if (ok && _fs->exists(_backup)) ok = _fs->remove(_backup);
    bool backed_up = false;
    if (ok && _fs->exists(_target)) {
      ok = _fs->rename(_target, _backup);
      backed_up = ok;
    }
    if (ok) ok = _fs->rename(_temp, _target);
    if (!ok && backed_up) _fs->rename(_backup, _target);
    if (ok) _fs->remove(_backup);
    else _fs->remove(_temp);
    _finished = true;
    return ok;
  }
};
} // namespace mesh
#endif
