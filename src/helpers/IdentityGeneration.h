#pragma once

#include <Identity.h>
#include <stddef.h>

namespace mesh {

// Keep identity generation bounded so platforms which cache startup entropy
// can provision an exact amount. Eleven attempts makes exhaustion vanishingly
// unlikely while still allowing callers to fail closed.
constexpr size_t MAX_LOCAL_IDENTITY_GENERATION_ATTEMPTS = 11;

inline bool hasReservedIdentityPrefix(const Identity& identity) {
  return identity.pub_key[0] == 0x00 || identity.pub_key[0] == 0xFF;
}

template <typename Generator>
bool generateUsableLocalIdentity(LocalIdentity& identity, Generator generator) {
  for (size_t attempt = 0;
       attempt < MAX_LOCAL_IDENTITY_GENERATION_ATTEMPTS;
       ++attempt) {
    identity = generator();
    if (!hasReservedIdentityPrefix(identity)) return true;
  }
  return false;
}

} // namespace mesh
