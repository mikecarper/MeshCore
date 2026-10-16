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
public:
  using PresenceProbe = bool (*)(FILESYSTEM*, const char*, bool&);
private:
  FILESYSTEM* _fs;
  const char* _target;
  char _temp[48];
  char _backup[48];
  File _file;
  File _verify;
  size_t _size = 0;
  uint32_t _crc = 0xffffffff;
  bool _ok = false;
  bool _finished = false;
  enum class CommitStage : uint8_t { Flush, Open, Verify, Publish };
  CommitStage _commit_stage = CommitStage::Flush;
  uint32_t _verify_crc = 0xffffffff;
  size_t _verify_remaining = 0;
  PresenceProbe _presence;
#if defined(ESP32_PLATFORM)
  // Pinned ESP SPIFFS uses 256-byte pages with a 5-byte data-page header.
  // This bounded stdio buffer coalesces records without the SDK's 4 KiB burst;
  // one logical record can flush at most one full buffer per write pass.
  static constexpr size_t ESP_WRITE_BUFFER_SIZE = 251;
  static constexpr size_t ESP_BACKGROUND_WRITE_BUFFER_SIZE = 502;
  static constexpr size_t ESP_VERIFY_BUFFER_SIZE = 128;
  static_assert(storage::CONTACT_RECORD_SIZE <= ESP_WRITE_BUFFER_SIZE,
                "Contact records must fit one bounded writer buffer");
  enum class BeginStage : uint8_t {
    Target, Backup, Recover, RetireBackup, Temp, Remove, Open, Configure, Ready, Failed
  };
  BeginStage _begin_stage = BeginStage::Target;
  bool _begin_target_exists = false;
  bool _begin_backup_exists = false;
  bool _begin_temp_exists = false;
  bool _owns_temp = false;
#endif
  __attribute__((noinline)) static uint32_t updateCRC32C(
      uint32_t crc, const uint8_t* data, size_t len) {
    // Persistent images may end in their own IEEE CRC32. Reusing that
    // polynomial here gives valid images a fixed whole-file residue. This
    // independent fingerprint is private to the transaction, never persisted.
    while (len-- > 0) {
      crc ^= *data++;
      for (unsigned bit = 0; bit < 8; bit++) {
        crc = (crc >> 1) ^ (0x82F63B78U & (0U - (crc & 1U)));
      }
    }
    return crc;
  }
  static bool probe(FILESYSTEM* fs, const char* path, bool& present,
                    PresenceProbe presence) {
    if (presence) return presence(fs, path, present);
    present = fs->exists(path);
    return true;
  }
  static bool removeIfPresent(FILESYSTEM* fs, const char* path,
                              PresenceProbe presence) {
    bool present = false;
    return probe(fs, path, present, presence) && (!present || fs->remove(path));
  }
public:
  enum class CommitProgress : uint8_t { Pending, Succeeded, Failed };
  static bool recover(FILESYSTEM* fs, const char* target,
                      PresenceProbe presence = nullptr) {
    char backup[48];
    snprintf(backup, sizeof(backup), "%s.bak", target);
    bool target_exists = false, backup_exists = false;
    if (!probe(fs, target, target_exists, presence)
        || !probe(fs, backup, backup_exists, presence)) return false;
    if (target_exists) return true;
    return !backup_exists || fs->rename(backup, target);
  }
  ContactFileTransaction(FILESYSTEM* fs, const char* target,
                         PresenceProbe presence = nullptr
#if defined(ESP32_PLATFORM)
                         , bool defer_begin = false
#endif
                         )
      : _fs(fs), _target(target), _presence(presence) {
    snprintf(_temp, sizeof(_temp), "%s.tmp", target);
    snprintf(_backup, sizeof(_backup), "%s.bak", target);
#if defined(ESP32_PLATFORM)
    // Explicit durability callers retain synchronous construction. The lazy
    // contact writer requests one setup operation per transport-serviced pass.
    if (!defer_begin) {
      while (serviceBegin() == BeginProgress::Pending) {}
    }
#else
    if (!recover(fs, target, presence)) return;
    if (!removeIfPresent(fs, _temp, presence)) return;
#if defined(RP2040_PLATFORM)
    _file = fs->open(_temp, "w");
#else
    _file = fs->open(_temp, "w", true);
#endif
    _ok = static_cast<bool>(_file);
#endif
  }
  ~ContactFileTransaction() {
    if (_file) _file.close();
    if (_verify) _verify.close();
    if (!_finished
#if defined(ESP32_PLATFORM)
        && _owns_temp
#endif
        ) removeIfPresent(_fs, _temp, _presence);
  }
  operator bool() const { return _ok; }
#if defined(ESP32_PLATFORM)
  enum class BeginProgress : uint8_t { Pending, Ready, Failed };
  BeginProgress serviceBegin(bool background_contact_write = false) {
    bool ok = true;
    switch (_begin_stage) {
      case BeginStage::Target:
        ok = probe(_fs, _target, _begin_target_exists, _presence);
        if (ok) _begin_stage = BeginStage::Backup;
        break;
      case BeginStage::Backup:
        ok = probe(_fs, _backup, _begin_backup_exists, _presence);
        if (ok) _begin_stage = BeginStage::Recover;
        break;
      case BeginStage::Recover:
        if (!_begin_target_exists && _begin_backup_exists) {
          ok = _fs->rename(_backup, _target);
          if (ok) {
            _begin_target_exists = true;
            _begin_backup_exists = false;
          }
        }
        if (ok) _begin_stage = background_contact_write
            ? BeginStage::RetireBackup : BeginStage::Temp;
        break;
      case BeginStage::RetireBackup:
        // A successful prior publication may retain its old backup. Retire it
        // in a separate setup pass only when the original target was present.
        // Recover already consumed the backup when the target was missing.
        if (_begin_target_exists && _begin_backup_exists)
          ok = _fs->remove(_backup);
        if (ok) _begin_stage = BeginStage::Temp;
        break;
      case BeginStage::Temp:
        ok = probe(_fs, _temp, _begin_temp_exists, _presence);
        if (ok) _begin_stage = BeginStage::Remove;
        break;
      case BeginStage::Remove:
        if (_begin_temp_exists) ok = _fs->remove(_temp);
        if (ok) _begin_stage = BeginStage::Open;
        break;
      case BeginStage::Open:
        // The previous stages proved this name absent. Even an unsuccessful
        // create/open can leave a partial inode belonging to this attempt.
        // Before this point cancellation must leave an unowned stale temp.
        _owns_temp = true;
        _file = _fs->open(_temp, "w", true);
        ok = static_cast<bool>(_file);
        if (ok) _begin_stage = BeginStage::Configure;
        break;
      case BeginStage::Configure:
        // Select once at Configure, before the stream's first I/O. Default
        // and synchronous callers retain one 251-byte SPIFFS payload page;
        // background contacts coalesce two pages without a larger record.
        ok = _file.setBufferSize(background_contact_write
            ? ESP_BACKGROUND_WRITE_BUFFER_SIZE : ESP_WRITE_BUFFER_SIZE);
        if (ok) {
          _ok = true;
          _begin_stage = BeginStage::Ready;
          return BeginProgress::Ready;
        }
        break;
      case BeginStage::Ready:
        return BeginProgress::Ready;
      case BeginStage::Failed:
        return BeginProgress::Failed;
    }
    if (!ok) {
      _begin_stage = BeginStage::Failed;
      return BeginProgress::Failed;
    }
    return BeginProgress::Pending;
  }
#endif
  bool readyToPublish() const {
    return !_finished && _commit_stage == CommitStage::Publish;
  }
  size_t write(const uint8_t* data, size_t len) {
    if (!_ok || _finished || _commit_stage != CommitStage::Flush) return 0;
    const size_t wrote = _file.write(data, len);
    _ok = wrote == len;
    _size += wrote;
    _crc = updateCRC32C(_crc, data, wrote);
    return wrote;
  }
  // The default verification pass reads one 64-byte chunk. ESP background
  // callers may request up to eight chunks while reusing the same scratch
  // buffer. Transports run between passes without exposing the rename gap;
  // synchronous commit() and other platforms retain the one-chunk default.
  CommitProgress serviceCommit(bool valid = true, unsigned max_verify_chunks = 1,
                                bool defer_backup_cleanup = false) {
#if defined(ESP32_PLATFORM)
    // A premature commit must not publish, clean up, or implicitly aggregate
    // the deferred setup operations inside this call.
    if (_begin_stage != BeginStage::Ready) return CommitProgress::Failed;
#endif
    if (_finished) return CommitProgress::Failed;
    bool ok = _ok && valid;
    if (ok && _commit_stage == CommitStage::Flush) {
      if (_file) { _file.flush(); _file.close(); }
      _commit_stage = CommitStage::Open;
      return CommitProgress::Pending;
    }
    if (ok && _commit_stage == CommitStage::Open) {
      _verify = _fs->open(_temp, "r");
      ok = static_cast<bool>(_verify);
#if defined(ESP32_PLATFORM)
      // Select read-ahead at Open only. Default/synchronous callers retain
      // 128 bytes; an eight-chunk background pass may coalesce its 512-byte
      // budget in one backend refill. Later budget changes retain this choice,
      // with at most 512 backend bytes per pass and the same 64-byte scratch.
      // Configuration must succeed before verification or publication.
      ok = ok && _verify.setBufferSize(
          max_verify_chunks >= 8 ? 512 : ESP_VERIFY_BUFFER_SIZE);
#endif
      ok = ok && _verify.size() == _size;
      if (ok) {
        _verify_remaining = _size;
        _commit_stage = CommitStage::Verify;
        return CommitProgress::Pending;
      }
    }
    if (ok && _commit_stage == CommitStage::Verify) {
      uint8_t buf[64];
      const size_t count = _verify_remaining < sizeof(buf)
          ? _verify_remaining : sizeof(buf);
      if (count != 0) {
#if defined(ESP32_PLATFORM)
        if (max_verify_chunks == 0) max_verify_chunks = 1;
        if (max_verify_chunks > 8) max_verify_chunks = 8;
#else
        max_verify_chunks = 1;
#endif
        do {
          const size_t chunk = _verify_remaining < sizeof(buf)
              ? _verify_remaining : sizeof(buf);
          ok = _verify.read(buf, chunk) == chunk;
          if (!ok) break;
          _verify_crc = updateCRC32C(_verify_crc, buf, chunk);
          _verify_remaining -= chunk;
        } while (--max_verify_chunks != 0 && _verify_remaining != 0);
        if (ok) return CommitProgress::Pending;
      } else {
        _verify.close();
        ok = _verify_crc == _crc;
        if (ok) {
          _commit_stage = CommitStage::Publish;
          return CommitProgress::Pending;
        }
      }
    }
    // Keep both renames and any rollback in this pass. Cold-path readers must
    // not run between removing the target name and installing its replacement.
    if (_file) _file.close();
    if (_verify) _verify.close();
    bool backup_exists = false, target_exists = false;
    if (ok) ok = probe(_fs, _backup, backup_exists, _presence)
        && probe(_fs, _target, target_exists, _presence);
    if (ok && backup_exists) ok = _fs->remove(_backup);
    bool backed_up = false;
    if (ok && target_exists) {
      ok = _fs->rename(_target, _backup);
      backed_up = ok;
    }
    if (ok) ok = _fs->rename(_temp, _target);
    if (!ok && backed_up) _fs->rename(_backup, _target);
    // Cleanup remains best effort after the publish/rollback result, but an
    // absent file must not trigger the SDK's error log on the shared USB port.
    if (ok) {
#if defined(ESP32_PLATFORM)
      // Background contacts publish the target and its path handles now.
      // A later transaction retires this recoverable old backup separately.
      if (!defer_backup_cleanup)
#else
      (void)defer_backup_cleanup; // Other platforms retain immediate cleanup.
#endif
        removeIfPresent(_fs, _backup, _presence);
    } else removeIfPresent(_fs, _temp, _presence);
    _finished = true;
    return ok ? CommitProgress::Succeeded : CommitProgress::Failed;
  }
  bool commit(bool valid = true) {
    CommitProgress progress;
    do { progress = serviceCommit(valid); }
    while (progress == CommitProgress::Pending);
    return progress == CommitProgress::Succeeded;
  }
};
} // namespace mesh
#endif
