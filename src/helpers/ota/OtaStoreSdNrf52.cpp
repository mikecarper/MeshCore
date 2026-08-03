#include "OtaStoreSdNrf52.h"

#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)

#include <Arduino.h>
#include <SdFat.h>
#include <SPI.h>
#include <new>
#include <string.h>
#include "OtaByteIO.h"
#include "OtaFlashLayout_nrf52.h"
#include "OtaSdHandoff.h"

#ifndef OTA_SD_CS_PIN
#define OTA_SD_CS_PIN PIN_SPI1_NSS
#endif
#ifndef OTA_SD_SCK_MHZ
#define OTA_SD_SCK_MHZ 8
#endif

namespace mesh {
namespace ota {

const char* const OtaStoreSdNrf52::PATH = "/meshcore-ota.mota";

OtaStoreSdNrf52::OtaStoreSdNrf52()
    : _sd(new (std::nothrow) SdFs()), _file(new (std::nothrow) FsFile()) {}

OtaStoreSdNrf52::~OtaStoreSdNrf52() {
  if (_file) { _file->close(); delete _file; }
  if (_sd) { _sd->end(); delete _sd; }
}

void OtaStoreSdNrf52::fail(const char* message) {
  strncpy(_error, message ? message : "SD error", sizeof(_error) - 1);
  _error[sizeof(_error) - 1] = 0;
}

bool OtaStoreSdNrf52::mount() {
  if (_mounted) return true;
  _error[0] = 0;
  if (!_sd || !_file || !_sd->begin(SdSpiConfig(OTA_SD_CS_PIN, DEDICATED_SPI,
                                                SD_SCK_MHZ(OTA_SD_SCK_MHZ), &SPI1))) {
    fail("SD mount failed");
    return false;
  }
  _mounted = true;
  if (!inspect_mbr()) {
    _sd->end();
    _mounted = false;
    return false;
  }
  return true;
}

bool OtaStoreSdNrf52::inspect_mbr() {
  uint8_t sector[MOTA_SD_SECTOR_SIZE];
  if (!_sd->card() || !_sd->card()->readSector(0, sector)) {
    fail("SD MBR read failed");
    return false;
  }
  if (sector[510] != 0x55 || sector[511] != 0xAA) {
    fail("SD must use an MBR partition table");
    return false;
  }

  // SdFs mounts the first usable MBR partition. Require its start to leave
  // sector 1 unused; a protective GPT entry (type 0xEE) is not safe here.
  for (uint8_t i = 0; i < 4; i++) {
    const uint8_t* p = sector + 446 + (uint32_t)i * 16;
    uint8_t type = p[4];
    uint32_t start = mota_sd_rd32(p + 8);
    uint32_t count = mota_sd_rd32(p + 12);
    if (type == 0 || count == 0) continue;
    if (type == 0xEE || start <= MOTA_SD_HANDOFF_SECTOR ||
        start >= _sd->card()->sectorCount() ||
        count > _sd->card()->sectorCount() - start) {
      fail("SD needs an MBR partition starting after sector 1");
      return false;
    }
    _partition_start = start;
    _partition_end = start + count;
    return true;
  }
  fail("SD has no usable MBR partition");
  return false;
}

bool OtaStoreSdNrf52::invalidate_handoff() {
  if (!_mounted || !_sd->card()) return false;
  uint8_t sector[MOTA_SD_SECTOR_SIZE];
  if (!_sd->card()->readSector(MOTA_SD_HANDOFF_SECTOR, sector)) return false;
  memset(sector, 0xFF, MOTA_SD_HANDOFF_LEN);
  return _sd->card()->writeSector(MOTA_SD_HANDOFF_SECTOR, sector) &&
         _sd->card()->syncDevice();
}

bool OtaStoreSdNrf52::locate_file() {
  Sector_t first = 0, last = 0;
  if (!_file || !_file->contiguousRange(&first, &last) || last < first) {
    fail("OTA file is not contiguous");
    return false;
  }
  uint64_t need = ((uint64_t)_total + MOTA_SD_SECTOR_SIZE - 1) / MOTA_SD_SECTOR_SIZE;
  uint64_t available = (uint64_t)last - first + 1;
  if (need == 0 || need > available || first < _partition_start ||
      (uint64_t)first + need > _partition_end) {
    fail("OTA file sector range is invalid");
    return false;
  }
  _first_sector = (uint32_t)first;
  _allocated_sectors = (uint32_t)need;
  return true;
}

bool OtaStoreSdNrf52::plan_layout(bool, uint32_t image_size,
                                  uint32_t, uint32_t payload_size) {
  const uint32_t app_base = mota_nrf52_app_base();
  if (image_size == 0 || payload_size == 0 || app_base >= MOTA_NRF52_APP_END ||
      image_size > MOTA_NRF52_APP_END - app_base) {
    fail("image exceeds nRF52 application region");
    return false;
  }
  return true;
}

bool OtaStoreSdNrf52::begin(uint32_t total_size) {
  _total = 0;
  _first_sector = 0;
  _allocated_sectors = 0;
  if (total_size < 13 || !mount()) return false;
  if (!invalidate_handoff()) {
    fail("SD handoff clear failed");
    return false;
  }
  if (*_file) _file->close();
  _sd->remove(PATH);
  if (!_file->open(PATH, O_RDWR | O_CREAT | O_EXCL)) {
    fail("SD OTA file create failed");
    return false;
  }
  if (!_file->preAllocate(total_size)) {
    fail("SD lacks contiguous space for update");
    _file->close();
    _sd->remove(PATH);
    return false;
  }
  _total = total_size;
  if (!locate_file()) {
    _total = 0;
    _file->close();
    _sd->remove(PATH);
    return false;
  }
  return true;
}

bool OtaStoreSdNrf52::set_meta_size(uint32_t meta_bytes) {
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

bool OtaStoreSdNrf52::write(uint32_t offset, const uint8_t* data, uint32_t len) {
  if (!_file || !*_file || (uint64_t)offset + len > _total || !_file->seekSet(offset) ||
      _file->write(data, len) != len) {
    fail("SD OTA write failed");
    return false;
  }
  return true;
}

bool OtaStoreSdNrf52::read(uint32_t offset, uint8_t* buf, uint32_t len) const {
  if (!_file || !*_file || (uint64_t)offset + len > _total || !_file->seekSet(offset) ||
      _file->read(buf, len) != (int)len) {
    return false;
  }
  return true;
}

uint32_t OtaStoreSdNrf52::capacity() const {
  if (!_mounted || !_sd || !_sd->card()) return 0;
  uint64_t bytes = (uint64_t)_sd->card()->sectorCount() * MOTA_SD_SECTOR_SIZE;
  return bytes > UINT32_MAX ? UINT32_MAX : (uint32_t)bytes;
}

bool OtaStoreSdNrf52::finalize() {
  if (!_file || !*_file || !_file->sync() || !_sd->card()->syncDevice()) {
    fail("SD OTA sync failed");
    return false;
  }
  return locate_file();
}

void OtaStoreSdNrf52::checkpoint() {
  if (_file && *_file) {
    _file->sync();
    if (_sd->card()) _sd->card()->syncDevice();
  }
}

bool OtaStoreSdNrf52::reopen() {
  _total = 0;
  if (!mount()) return false;
  if (*_file) _file->close();
  if (!_file->open(PATH, O_RDWR)) return false;
  uint8_t hdr[8];
  if (_file->fileSize() < 13 || !_file->seekSet(0) ||
      _file->read(hdr, sizeof(hdr)) != (int)sizeof(hdr) ||
      memcmp(hdr, MOTA_MAGIC, 4) != 0) {
    _file->close();
    return false;
  }
  uint32_t total = rd_u32le(hdr + 4);
  if (total < 13 || total != _file->fileSize()) {
    _file->close();
    return false;
  }
  _total = total;
  if (!locate_file()) {
    _total = 0;
    _file->close();
    return false;
  }
  return true;
}

void OtaStoreSdNrf52::clear() {
  _total = 0;
  _first_sector = 0;
  _allocated_sectors = 0;
  if (!mount()) return;
  invalidate_handoff();
  if (_file && *_file) _file->close();
  _sd->remove(PATH);
}

bool OtaStoreSdNrf52::approve_for_bootloader() {
  if (!_total || !finalize()) return false;
  if (!write(8 + MOTA_OFF_APPROVAL, APPROVAL_YES, sizeof(APPROVAL_YES)) ||
      !_file->sync() || !_sd->card()->syncDevice()) {
    fail("SD approval write failed");
    return false;
  }

  uint8_t sector[MOTA_SD_SECTOR_SIZE];
  if (!_sd->card()->readSector(MOTA_SD_HANDOFF_SECTOR, sector)) {
    fail("SD handoff sector read failed");
    return false;
  }
  mota_sd_encode_handoff(sector, _first_sector, _allocated_sectors,
                         _total, _sd->card()->sectorCount());
  if (!_sd->card()->writeSector(MOTA_SD_HANDOFF_SECTOR, sector) ||
      !_sd->card()->syncDevice()) {
    fail("SD bootloader handoff write failed");
    return false;
  }
  return true;
}

} // namespace ota
} // namespace mesh

#endif
