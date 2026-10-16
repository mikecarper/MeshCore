#pragma once

#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)

// Keep SdFat implementation headers out of application headers: its global
// File typedef conflicts with Adafruit LittleFS's File. Only the two SD
// implementation translation units need complete volume/file definitions.
#if defined(OTA_RAK15002_SD_STORE)
class SdFat32;
class FatFile;
#else
class SdFs;
class FsFile;
#endif

namespace mesh {
namespace ota {

#if defined(OTA_RAK15002_SD_STORE)
using OtaSdFs = SdFat32;
using OtaSdFile = FatFile;
#else
using OtaSdFs = SdFs;
using OtaSdFile = FsFile;
#endif

} // namespace ota
} // namespace mesh

#endif
