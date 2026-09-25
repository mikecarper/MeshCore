#pragma once

#include <stdint.h>
#include <string.h>

namespace mesh {
namespace ota {

enum class RakStorageChoice : uint8_t { Internal, Qspi, Unsafe };

// detected: 0 = absent, 1 = RAK15001 C, 2 = W25Q16, 3 = both.
// A QSPI bootloader can only apply from the chip and wiring it was built for.
// Never stage into a different NOR just because it answered a JEDEC query.
inline RakStorageChoice rak_storage_choice(uint8_t detected,
                                           bool qspi_bootloader,
                                           bool identity_valid,
                                           const char* device_name,
                                           bool rak3401) {
  if (detected > 3u || detected == 3u) return RakStorageChoice::Unsafe;
  const char* auto_name = rak3401 ? "3401_AUTO_DFU" : "4631_AUTO_DFU";
  const bool merged_bootloader = identity_valid && device_name &&
                                 strcmp(device_name, auto_name) == 0;
  if (detected == 0u) return !qspi_bootloader || merged_bootloader
                             ? RakStorageChoice::Internal : RakStorageChoice::Unsafe;
  if (!qspi_bootloader) return RakStorageChoice::Internal;
  if (!identity_valid || !device_name) return RakStorageChoice::Unsafe;
  if (merged_bootloader) {
    return rak3401 && detected == 1u ? RakStorageChoice::Unsafe
                                     : RakStorageChoice::Qspi;
  }
  const char* expected = rak3401
      ? (detected == 2u ? "3401_W25Q16_DFU" : "")
      : (detected == 1u ? "4631_15001C_DFU" : "4631_W25Q16_DFU");
  return expected[0] && strcmp(device_name, expected) == 0
      ? RakStorageChoice::Qspi : RakStorageChoice::Unsafe;
}

} // namespace ota
} // namespace mesh
