#if defined(ESP32_PLATFORM)

#include <stdint.h>

#ifndef MESHCORE_BUILD_ENV
#define MESHCORE_BUILD_ENV "unknown"
#endif

#ifndef OTA_VARIANT
#define OTA_VARIANT MESHCORE_BUILD_ENV
#endif

#ifndef FIRMWARE_VERSION
#define FIRMWARE_VERSION MESHCORE_DEFAULT_FIRMWARE_VERSION
#endif

namespace {

constexpr uint16_t MESHCORE_IMAGE_IDENTITY_SCHEMA = 1;
constexpr uint32_t MESHCORE_IMAGE_IDENTITY_END_MAGIC = 0x3144494dUL;  // "MID1"

struct MeshCoreImageIdentity {
  char magic[8];
  uint16_t schema;
  uint16_t size;
  char environment[96];
  char firmware_version[96];
  uint32_t end_magic;
};

static_assert(sizeof(MeshCoreImageIdentity) == 208,
              "MeshCore image identity layout changed");

}  // namespace

// ESP-IDF places .rodata_custom_desc immediately after esp_app_desc_t. The
// record therefore starts at byte 0x120 relative to every ESP32 app image,
// independent of the app partition's absolute flash address.
extern "C" __attribute__((section(".rodata_custom_desc"), used))
const MeshCoreImageIdentity meshcore_image_identity = {
    {'M', 'C', 'F', 'W', 'I', 'D', '0', '1'},
    MESHCORE_IMAGE_IDENTITY_SCHEMA,
    sizeof(MeshCoreImageIdentity),
    OTA_VARIANT,
    FIRMWARE_VERSION,
    MESHCORE_IMAGE_IDENTITY_END_MAGIC,
};

#endif
