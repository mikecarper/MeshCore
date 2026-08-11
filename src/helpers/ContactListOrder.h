#pragma once

#include <stdint.h>

namespace mesh {

static constexpr uint8_t CONTACT_FLAG_FAVORITE = 0x01;

inline bool isFavoriteContact(uint8_t flags) {
  return (flags & CONTACT_FLAG_FAVORITE) != 0;
}

// Negative means A sorts before B. Favorites come first, with the newest
// advertisement first inside both the favorite and non-favorite groups.
inline int compareContactListOrder(uint8_t a_flags, uint32_t a_timestamp,
                                   uint8_t b_flags, uint32_t b_timestamp) {
  const bool a_favorite = isFavoriteContact(a_flags);
  const bool b_favorite = isFavoriteContact(b_flags);
  if (a_favorite != b_favorite) return a_favorite ? -1 : 1;
  if (a_timestamp > b_timestamp) return -1;
  if (a_timestamp < b_timestamp) return 1;
  return 0;
}

}  // namespace mesh
