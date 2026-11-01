#pragma once
#include <stdint.h>

extern const uint8_t migration_candidate[40960];
extern const uint8_t migration_candidate_sha256[32];
extern const uint8_t migration_legacy_sha256[32];
extern const uint8_t migration_stock_sha256[1][32];
extern const uint32_t migration_stock_count;
extern const bool migration_has_legacy;
extern const uint32_t migration_app_base, migration_boot_version, migration_board_id;
extern const uint16_t migration_softdevice_fwid;
extern const char migration_device_name[16];
