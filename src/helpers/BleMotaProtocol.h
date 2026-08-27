#pragma once

#include <stdint.h>

namespace mesh {
namespace ota {

// MeshCore BLE mOTA seeder service. The device notifies one framed seeder
// request on REQUEST; the paired host writes the framed response to RESPONSE.
// Both characteristics require an encrypted MITM-authenticated BLE link.
static constexpr char BLE_MOTA_SERVICE_UUID[] =
    "14518fc2-7e7a-4d84-8cae-6664b0234cf2";
static constexpr char BLE_MOTA_REQUEST_UUID[] =
    "2bfaa1ee-7030-459a-b65a-e7cfd5b09735";
static constexpr char BLE_MOTA_RESPONSE_UUID[] =
    "acf38a51-dd58-4dce-917f-0b1135e41b1a";

static constexpr uint16_t BLE_MOTA_REQUEST_MAX = 11;
static constexpr uint16_t BLE_MOTA_RESPONSE_MAX = 197;

}  // namespace ota
}  // namespace mesh
