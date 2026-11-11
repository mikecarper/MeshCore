// Read the installed Nordic SoftDevice FWID from its information structure.
//
// nrf_sdm.h defines SD_FWID_OFFSET relative to the SoftDevice base and
// explicitly requires MBR_SIZE as SD_FWID_GET()'s argument for the normal
// nRF52 layout. Passing zero reads 0x200C instead of the actual FWID at
// 0x300C. Keep this tiny wrapper shared with the host regression so the base
// cannot silently regress again.
#ifndef OTA_SOFTDEVICE_FWID_H_
#define OTA_SOFTDEVICE_FWID_H_

#include <stdint.h>

#ifndef MBR_SIZE
  #error "MBR_SIZE is required to read the installed SoftDevice FWID"
#endif
#ifndef SD_FWID_GET
  #error "SD_FWID_GET is required to read the installed SoftDevice FWID"
#endif

static inline uint16_t mota_runtime_softdevice_fwid_get(void) {
  return SD_FWID_GET(MBR_SIZE);
}

#endif // OTA_SOFTDEVICE_FWID_H_
