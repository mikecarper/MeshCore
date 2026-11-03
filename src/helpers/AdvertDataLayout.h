#pragma once

#include <stddef.h>
#include <stdint.h>

#define ADV_TYPE_NONE         0
#define ADV_TYPE_CHAT         1
#define ADV_TYPE_REPEATER     2
#define ADV_TYPE_ROOM         3
#define ADV_TYPE_SENSOR       4
//FUTURE: 5..15

#define ADV_LATLON_MASK       0x10
#define ADV_FEAT1_MASK        0x20   // FUTURE
#define ADV_FEAT2_MASK        0x40   // FUTURE
#define ADV_NAME_MASK         0x80

namespace AdvertDataLayout {

// Metadata is optional on the wire. When present, every fixed-width field
// selected by its flags must fit before a parser can read any of those fields.
// The caller owns its overall size bound; types, values and name bytes remain
// opaque here so existing and future metadata retain their wire semantics.
static inline bool hasCompletePrefix(const uint8_t* app_data, size_t app_data_len) {
  if (app_data_len == 0) return true;
  if (app_data == nullptr) return false;
  const uint8_t flags = app_data[0];
  const size_t prefix_len = 1
      + ((flags & ADV_LATLON_MASK) ? 2 * sizeof(int32_t) : 0)
      + ((flags & ADV_FEAT1_MASK) ? sizeof(uint16_t) : 0)
      + ((flags & ADV_FEAT2_MASK) ? sizeof(uint16_t) : 0);
  return app_data_len >= prefix_len;
}

}  // namespace AdvertDataLayout
