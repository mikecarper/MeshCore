/*
 * Compile the vendored tinf core as C. Building the same source as C++ costs
 * several extra kilobytes with the embedded toolchains used by MeshCore.
 */
#include "../CompanionReaderConfig.h"
#include "../ui/Nrf52FontConfig.h"
#include "OtaDeflateConfig.h"
#if defined(ENABLE_OTA) || defined(OTA_TRANSPORT_DEFLATE_TEST) || COMPANION_FEATURE_READER \
    || MESHCORE_FONT_DEFLATE
#if defined(__GNUC__) && !defined(__clang__)
/* nRF52's Arduino recipe defaults to -Ofast, which more than doubles this
 * decoder. Keep the size policy local to tinf instead of changing MeshCore. */
#pragma GCC push_options
#if defined(NRF52_PLATFORM) && __GNUC__ >= 12
#pragma GCC optimize ("Oz")
#else
#pragma GCC optimize ("Os")
#endif
#endif
#if (defined(NRF52_PLATFORM) && COMPANION_FEATURE_READER) || defined(TINF_READER_STREAM_TEST)
#define MESHCORE_TINF_RECORD 1
#endif
#define MESHCORE_TINF_IMPLEMENTATION 1
#include "tinf/tinflate.c"
#if MESHCORE_OTA_DEVICE_DEFLATE
// One true-C translation unit for the existing strict decoder and the small
// bounded encoder. Do not bring uzlib's second decoder or wrapper sources in.
#include "tinydeflate/deflate.c"
#endif
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC pop_options
#endif
#endif
