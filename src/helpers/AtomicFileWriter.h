#pragma once

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

/**
 * Power-loss-safe file replacement for the Adafruit LittleFS backends.
 *
 * Data is written to a sibling .tmp file. commit() flushes and closes that
 * file, reads it back while checking its size and CRC32C, and only then uses
 * LittleFS rename() to atomically replace the live file. Failures before that
 * publication leave the previous live file intact. Once rename is attempted,
 * a failure may leave the old or new image live; inspect outcome() before
 * assuming that the previous image survived.
 */
class AtomicFileWriter {
  static const size_t TEMP_PATH_CAPACITY = 96;
  static const uint8_t VALIDATION_ATTEMPTS = 3;

  FILESYSTEM* _fs;
  const char* _target_path;
  char _temp_path[TEMP_PATH_CAPACITY];
  File _file;
  size_t _bytes_written;
  uint32_t _crc;
  bool _opened;
  bool _write_ok;
public:
  enum class Outcome : uint8_t { Pending, NotCommitted, Committed, Indeterminate };

private:
  Outcome _outcome;

  static uint32_t updateCRC32C(uint32_t crc, const uint8_t* data, size_t len);
  void removeTempFile();
  bool validateFile(const char* path);

public:
  AtomicFileWriter(FILESYSTEM* fs, const char* target_path);
  ~AtomicFileWriter();
  AtomicFileWriter(const AtomicFileWriter&) = delete;
  AtomicFileWriter& operator=(const AtomicFileWriter&) = delete;

  operator bool() const { return _opened && _write_ok; }

  size_t write(uint8_t value);
  size_t write(const uint8_t* data, size_t len);

  /**
   * Commit the staged file. content_valid lets callers include format-specific
   * checks in the same decision; content_valid=false prevents publication.
   * Returns true only after verifying the public image. A false return can be
   * Indeterminate, not proof that publication did not happen.
   */
  bool commit(bool content_valid = true);
  Outcome outcome() const { return _outcome; }

  size_t bytesWritten() const { return _bytes_written; }
  const char* tempPath() const { return _temp_path; }
};

} // namespace mesh

#if defined(ATOMIC_FILE_WRITER_IMPLEMENTATION)

namespace mesh {

// Share this loop between write and readback instead of duplicating it under LTO.
__attribute__((noinline)) uint32_t AtomicFileWriter::updateCRC32C(
    uint32_t crc, const uint8_t* data, size_t len) {
  // Persistent images can end in their own IEEE CRC32. Reusing that polynomial
  // here would give all valid same-sized images a fixed whole-image residue.
  // This independent, nonpersistent fingerprint does not alter file formats.
  while (len-- > 0) {
    crc ^= *data++;
    for (unsigned bit = 0; bit < 8; bit++) {
      crc = (crc >> 1) ^ (0x82F63B78U & (0U - (crc & 1U)));
    }
  }
  return crc;
}

void AtomicFileWriter::removeTempFile() {
  if (_temp_path[0] != 0 && _fs->exists(_temp_path)) {
    _fs->remove(_temp_path);
  }
}

bool AtomicFileWriter::validateFile(const char* path) {
  for (uint8_t attempt = 0; attempt < VALIDATION_ATTEMPTS; ++attempt) {
    File verify(*_fs);
    if (!verify.open(path, FILE_O_READ)) continue;

    bool valid = !verify.isDirectory() && verify.size() == _bytes_written;
    uint32_t read_crc = 0xFFFFFFFFUL;
    size_t remaining = _bytes_written;
    uint8_t buf[64];

    while (valid && remaining > 0) {
      size_t chunk = remaining < sizeof(buf) ? remaining : sizeof(buf);
      int count = verify.read(buf, (uint16_t)chunk);
      if (count != (int)chunk) {
        valid = false;
        break;
      }
      read_crc = updateCRC32C(read_crc, buf, chunk);
      remaining -= chunk;
    }

    verify.close();
    if (valid && remaining == 0 && read_crc == _crc) return true;
  }
  return false;
}

AtomicFileWriter::AtomicFileWriter(FILESYSTEM* fs, const char* target_path)
  : _fs(fs), _target_path(target_path), _file(*fs), _bytes_written(0),
    _crc(0xFFFFFFFFUL), _opened(false), _write_ok(true), _outcome(Outcome::Pending) {
  _temp_path[0] = 0;
  if (target_path == NULL) {
    _write_ok = false;
    return;
  }

  size_t target_len = strlen(target_path);
  if (target_len + sizeof(".tmp") > sizeof(_temp_path)) {
    _write_ok = false;
    return;
  }

  memcpy(_temp_path, target_path, target_len);
  memcpy(_temp_path + target_len, ".tmp", sizeof(".tmp"));

  // A reset before a previous commit may leave a harmless stale temp file.
  // Never append to it: remove it before opening the new transaction.
  removeTempFile();
  _opened = _file.open(_temp_path, FILE_O_WRITE);
  _write_ok = _opened;
}

AtomicFileWriter::~AtomicFileWriter() {
  if (_outcome != Outcome::Pending) return;
  if (_opened) {
    _file.close();
    _opened = false;
  }
  removeTempFile();
}

size_t AtomicFileWriter::write(uint8_t value) {
  return write(&value, 1);
}

size_t AtomicFileWriter::write(const uint8_t* data, size_t len) {
  if (!_opened || !_write_ok || (data == NULL && len != 0)) {
    _write_ok = false;
    return 0;
  }

  size_t written = _file.write(data, len);
  if (written != len) {
    _write_ok = false;
    return written;
  }

  _bytes_written += written;
  _crc = updateCRC32C(_crc, data, written);
  return written;
}

bool AtomicFileWriter::commit(bool content_valid) {
  if (_outcome != Outcome::Pending) return false;

  bool success = _opened && _write_ok && content_valid;
  if (_opened) {
    _file.flush();
    if (_file.size() != _bytes_written) success = false;
    _file.close();
    _opened = false;
  }

  if (success) success = validateFile(_temp_path);
  if (success) {
    // Rename can report an error after publishing, or acknowledge a change
    // without replacing the public image. Its return value cannot establish
    // either outcome: always verify the public bytes against the staged CRC.
    _fs->rename(_temp_path, _target_path);
    success = validateFile(_target_path);
    _outcome = success ? Outcome::Committed : Outcome::Indeterminate;
  } else {
    _outcome = Outcome::NotCommitted;
  }
  // A failed rename may leave scratch even when the public image already
  // matches. It is never an authority or a rollback image.
  removeTempFile();
  return success;
}

} // namespace mesh

#endif // ATOMIC_FILE_WRITER_IMPLEMENTATION

#endif // NRF52_PLATFORM || STM32_PLATFORM
