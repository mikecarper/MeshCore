#pragma once

#include <stddef.h>
#include <stdint.h>

// The bridge supports standard Arduino targets whose NVS and OTA metadata
// remain at their normal addresses.  It selects a verified target table from
// the actual flash capacity at runtime; source application/SPIFFS sizing is
// intentionally not hard-coded.
namespace mesh {
namespace esp32_partition_migration {

constexpr uint32_t kRequiredFlashBytes = 16U * 1024U * 1024U;
constexpr uint32_t kXiaoRequiredFlashBytes = 8U * 1024U * 1024U;
constexpr uint32_t kPartitionTableAddress = 0x8000;
constexpr uint32_t kPartitionTableBytes = 0x1000;
constexpr uint32_t kSectorBytes = 0x1000;

struct PartitionGeometry {
  uint32_t nvs_address;
  uint32_t nvs_size;
  uint32_t otadata_address;
  uint32_t otadata_size;
  uint32_t app0_address;
  uint32_t app0_size;
  uint32_t app1_address;
  uint32_t app1_size;
  uint32_t spiffs_address;
  uint32_t spiffs_size;
};

constexpr PartitionGeometry kLegacyLayout = {
    0x9000, 0x5000,
    0xE000, 0x2000,
    0x10000, 0x140000,
    0x150000, 0x140000,
    0x290000, 0x160000,
};

// This is Arduino-ESP32's default_16MB.csv.  Its NVS and OTA-data locations
// deliberately remain the same as the legacy table; SPIFFS moves after both
// 6.25 MiB OTA slots.  The bridge stages the identity separately because a
// raw SPIFFS image cannot safely be expanded in place.
constexpr PartitionGeometry kExpandedLayout = {
    0x9000, 0x5000,
    0xE000, 0x2000,
    0x10000, 0x640000,
    0x650000, 0x640000,
    0xC90000, 0x360000,
};

// Arduino-ESP32's default_8MB.csv, used by the Seeed XIAO ESP32-S3 and other
// 8 MiB boards.  Its metadata locations intentionally match the 16 MiB map.
constexpr PartitionGeometry kExpanded8MBLayout = {
    0x9000, 0x5000,
    0xE000, 0x2000,
    0x10000, 0x330000,
    0x340000, 0x330000,
    0x670000, 0x180000,
};

constexpr bool sameGeometry(const PartitionGeometry& left,
                            const PartitionGeometry& right) {
  return left.nvs_address == right.nvs_address
      && left.nvs_size == right.nvs_size
      && left.otadata_address == right.otadata_address
      && left.otadata_size == right.otadata_size
      && left.app0_address == right.app0_address
      && left.app0_size == right.app0_size
      && left.app1_address == right.app1_address
      && left.app1_size == right.app1_size
      && left.spiffs_address == right.spiffs_address
      && left.spiffs_size == right.spiffs_size;
}

constexpr bool isLegacyLayout(const PartitionGeometry& geometry) {
  return sameGeometry(geometry, kLegacyLayout);
}

constexpr bool isExpandedLayout(const PartitionGeometry& geometry) {
  return sameGeometry(geometry, kExpandedLayout);
}

constexpr bool canMigrate(uint32_t flash_bytes,
                          const PartitionGeometry& geometry) {
  return flash_bytes >= kRequiredFlashBytes && isLegacyLayout(geometry);
}

// Binary prefix generated from Arduino-ESP32's default_16MB.csv by
// gen_esp32part.py.  The caller erases the complete 4 KiB partition-table
// sector first, then writes this prefix; the already-erased suffix is the
// required 0xFF padding.  Keeping the generated MD5 record makes the
// bootloader reject an accidental partial or altered entry list.
constexpr uint8_t kExpandedPartitionTablePrefix[] = {
    0xAA, 0x50, 0x01, 0x02, 0x00, 0x90, 0x00, 0x00,
    0x00, 0x50, 0x00, 0x00, 0x6E, 0x76, 0x73, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAA, 0x50, 0x01, 0x00, 0x00, 0xE0, 0x00, 0x00,
    0x00, 0x20, 0x00, 0x00, 0x6F, 0x74, 0x61, 0x64,
    0x61, 0x74, 0x61, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAA, 0x50, 0x00, 0x10, 0x00, 0x00, 0x01, 0x00,
    0x00, 0x00, 0x64, 0x00, 0x61, 0x70, 0x70, 0x30,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAA, 0x50, 0x00, 0x11, 0x00, 0x00, 0x65, 0x00,
    0x00, 0x00, 0x64, 0x00, 0x61, 0x70, 0x70, 0x31,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAA, 0x50, 0x01, 0x82, 0x00, 0x00, 0xC9, 0x00,
    0x00, 0x00, 0x36, 0x00, 0x73, 0x70, 0x69, 0x66,
    0x66, 0x73, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAA, 0x50, 0x01, 0x03, 0x00, 0x00, 0xFF, 0x00,
    0x00, 0x00, 0x01, 0x00, 0x63, 0x6F, 0x72, 0x65,
    0x64, 0x75, 0x6D, 0x70, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xEB, 0xEB, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xB3, 0x2D, 0xCD, 0xB8, 0x3B, 0x16, 0x74, 0xAF,
    0x27, 0x76, 0x8C, 0x92, 0xB1, 0xF8, 0x8A, 0x62,
};

constexpr size_t kExpandedPartitionTablePrefixBytes =
    sizeof(kExpandedPartitionTablePrefix);

// Binary prefix generated from Arduino-ESP32's default_8MB.csv.
constexpr uint8_t kExpanded8MBPartitionTablePrefix[] = {
    0xAA, 0x50, 0x01, 0x02, 0x00, 0x90, 0x00, 0x00,
    0x00, 0x50, 0x00, 0x00, 0x6E, 0x76, 0x73, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAA, 0x50, 0x01, 0x00, 0x00, 0xE0, 0x00, 0x00,
    0x00, 0x20, 0x00, 0x00, 0x6F, 0x74, 0x61, 0x64,
    0x61, 0x74, 0x61, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAA, 0x50, 0x00, 0x10, 0x00, 0x00, 0x01, 0x00,
    0x00, 0x00, 0x33, 0x00, 0x61, 0x70, 0x70, 0x30,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAA, 0x50, 0x00, 0x11, 0x00, 0x00, 0x34, 0x00,
    0x00, 0x00, 0x33, 0x00, 0x61, 0x70, 0x70, 0x31,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAA, 0x50, 0x01, 0x82, 0x00, 0x00, 0x67, 0x00,
    0x00, 0x00, 0x18, 0x00, 0x73, 0x70, 0x69, 0x66,
    0x66, 0x73, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAA, 0x50, 0x01, 0x03, 0x00, 0x00, 0x7F, 0x00,
    0x00, 0x00, 0x01, 0x00, 0x63, 0x6F, 0x72, 0x65,
    0x64, 0x75, 0x6D, 0x70, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xEB, 0xEB, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0x46, 0x7E, 0xB8, 0x96, 0xE2, 0x9D, 0x1A, 0xA9,
    0x38, 0xC5, 0x57, 0xF4, 0xCD, 0xFC, 0x4C, 0x5B,
};

constexpr size_t kExpanded8MBPartitionTablePrefixBytes =
    sizeof(kExpanded8MBPartitionTablePrefix);

struct TargetPlan {
  uint32_t flash_bytes;
  const char* name;
  PartitionGeometry layout;
  const uint8_t* partition_table_prefix;
  size_t partition_table_prefix_bytes;
};

constexpr TargetPlan kTarget8MB = {
    kXiaoRequiredFlashBytes, "Arduino default_8MB",
    kExpanded8MBLayout, kExpanded8MBPartitionTablePrefix,
    kExpanded8MBPartitionTablePrefixBytes,
};

constexpr TargetPlan kTarget16MB = {
    kRequiredFlashBytes, "Arduino default_16MB",
    kExpandedLayout, kExpandedPartitionTablePrefix,
    kExpandedPartitionTablePrefixBytes,
};

constexpr const TargetPlan* targetForFlash(uint32_t flash_bytes) {
  return flash_bytes == kXiaoRequiredFlashBytes ? &kTarget8MB
      : flash_bytes == kRequiredFlashBytes ? &kTarget16MB
      : nullptr;
}

constexpr bool hasStableMetadata(const PartitionGeometry& geometry) {
  return geometry.nvs_address == kLegacyLayout.nvs_address
      && geometry.nvs_size == kLegacyLayout.nvs_size
      && geometry.otadata_address == kLegacyLayout.otadata_address
      && geometry.otadata_size == kLegacyLayout.otadata_size;
}

inline bool isTargetLayout(uint32_t flash_bytes,
                           const PartitionGeometry& geometry) {
  const TargetPlan* const plan = targetForFlash(flash_bytes);
  return plan && sameGeometry(geometry, plan->layout);
}

inline bool partitionFitsFlash(uint32_t flash_bytes, uint32_t address,
                               uint32_t size) {
  return size != 0
      && static_cast<uint64_t>(address) + static_cast<uint64_t>(size)
          <= static_cast<uint64_t>(flash_bytes);
}

// Source layouts may vary in their two OTA-slot sizes and SPIFFS placement.
// The updater itself ensures the bridge fits its inactive slot.  We only
// accept layouts which retain NVS/otadata at the exact addresses the target
// keeps, have two distinct non-empty OTA slots, and expose a SPIFFS partition
// from which the identity can be staged.
inline bool canMigrateGeneric(uint32_t flash_bytes,
                              const PartitionGeometry& geometry) {
  const TargetPlan* const plan = targetForFlash(flash_bytes);
  return plan && hasStableMetadata(geometry)
      && partitionFitsFlash(flash_bytes, geometry.app0_address, geometry.app0_size)
      && partitionFitsFlash(flash_bytes, geometry.app1_address, geometry.app1_size)
      && partitionFitsFlash(flash_bytes, geometry.spiffs_address, geometry.spiffs_size)
      && geometry.app0_address != geometry.app1_address
      && !sameGeometry(geometry, plan->layout);
}

static_assert(kExpandedLayout.app0_address + kExpandedLayout.app0_size
                  == kExpandedLayout.app1_address,
              "expanded OTA slots must be adjacent");
static_assert(kExpandedLayout.app1_address + kExpandedLayout.app1_size
                  == kExpandedLayout.spiffs_address,
              "expanded SPIFFS must follow both OTA slots");
static_assert(kExpandedLayout.spiffs_size >= kLegacyLayout.spiffs_size,
              "the full legacy SPIFFS image must fit in the target partition");
static_assert(kExpanded8MBLayout.app0_address + kExpanded8MBLayout.app0_size
                  == kExpanded8MBLayout.app1_address,
              "8 MiB expanded OTA slots must be adjacent");
static_assert(kExpanded8MBLayout.app1_address + kExpanded8MBLayout.app1_size
                  == kExpanded8MBLayout.spiffs_address,
              "8 MiB expanded SPIFFS must follow both OTA slots");
static_assert(kExpandedPartitionTablePrefixBytes
                  == kExpanded8MBPartitionTablePrefixBytes,
              "target table buffers share one fixed DRAM size");

}  // namespace esp32_partition_migration
}  // namespace mesh
