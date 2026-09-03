/*
 * Compile the vendored tinf core as C. Building the same source as C++ costs
 * several extra kilobytes with the embedded toolchains used by MeshCore.
 */
#if (defined(ENABLE_OTA) && defined(OTA_TRANSPORT_DEFLATE_RX)) || defined(OTA_TRANSPORT_DEFLATE_TEST)
#if defined(__GNUC__) && !defined(__clang__)
/* nRF52's Arduino recipe defaults to -Ofast, which more than doubles this
 * decoder. Keep the size policy local to tinf instead of changing MeshCore. */
#pragma GCC push_options
#pragma GCC optimize ("Os")
#endif
#define MESHCORE_TINF_IMPLEMENTATION 1
#include "tinf/tinflate.c"
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC pop_options
#endif
#endif
