#pragma once

#if defined(NRF52_PLATFORM)

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

/**
 * Power-loss-safe file replacement for the nRF52 LittleFS backend.
 *
 * Data is written to a sibling .tmp file. commit() flushes and closes that
 * file, reads it back while checking its size and CRC32, and only then uses
 * LittleFS rename() to atomically replace the live file. A failed or abandoned
 * write removes the temporary file and leaves the previous live file intact.
 */
class AtomicFileWriter {
  static const size_t TEMP_PATH_CAPACITY = 96;

  FILESYSTEM* _fs;
  const char* _target_path;
  char _temp_path[TEMP_PATH_CAPACITY];
  File _file;
  size_t _bytes_written;
  uint32_t _crc;
  bool _opened;
  bool _write_ok;
  bool _finished;

  static uint32_t updateCRC32(uint32_t crc, const uint8_t* data, size_t len);
  void removeTempFile();
  bool validateTempFile();

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
   * checks in the same decision; false always preserves the previous file.
   */
  bool commit(bool content_valid = true);

  size_t bytesWritten() const { return _bytes_written; }
  const char* tempPath() const { return _temp_path; }
};

} // namespace mesh

#if defined(ATOMIC_FILE_WRITER_IMPLEMENTATION)

namespace mesh {

uint32_t AtomicFileWriter::updateCRC32(uint32_t crc, const uint8_t* data, size_t len) {
  while (len-- > 0) {
    crc ^= *data++;
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc >> 1) ^ ((crc & 1) ? 0xEDB88320UL : 0);
    }
  }
  return crc;
}

void AtomicFileWriter::removeTempFile() {
  if (_temp_path[0] != 0 && _fs->exists(_temp_path)) {
    _fs->remove(_temp_path);
  }
}

bool AtomicFileWriter::validateTempFile() {
  File verify(*_fs);
  if (!verify.open(_temp_path, FILE_O_READ)) return false;

  bool valid = verify.size() == _bytes_written;
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
    read_crc = updateCRC32(read_crc, buf, chunk);
    remaining -= chunk;
  }

  verify.close();
  return valid && remaining == 0 && read_crc == _crc;
}

AtomicFileWriter::AtomicFileWriter(FILESYSTEM* fs, const char* target_path)
  : _fs(fs), _target_path(target_path), _file(*fs), _bytes_written(0),
    _crc(0xFFFFFFFFUL), _opened(false), _write_ok(true), _finished(false) {
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
  if (_finished) return;
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
  _crc = updateCRC32(_crc, data, written);
  return written;
}

bool AtomicFileWriter::commit(bool content_valid) {
  if (_finished) return false;

  bool success = _opened && _write_ok && content_valid;
  if (_opened) {
    _file.flush();
    if (_file.size() != _bytes_written) success = false;
    _file.close();
    _opened = false;
  }

  if (success) success = validateTempFile();
  if (success) success = _fs->rename(_temp_path, _target_path);
  if (!success) removeTempFile();

  _finished = true;
  return success;
}

} // namespace mesh

#endif // ATOMIC_FILE_WRITER_IMPLEMENTATION

#endif // NRF52_PLATFORM
