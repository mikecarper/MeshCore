#pragma once

#include <Identity.h>
#include <stddef.h>

namespace mesh {

// Keep identity generation bounded so platforms which cache startup entropy
// can provision an exact amount. Eleven attempts makes exhaustion vanishingly
// unlikely while still allowing callers to fail closed.
constexpr size_t MAX_LOCAL_IDENTITY_GENERATION_ATTEMPTS = 11;

// Startup-only cooperative progress hook. Radio entropy collection calls this
// between bytes, so displays can animate without threads or changing entropy.
struct IdentityGenerationProgress {
  void (*callback)(void*) = nullptr;
  void* context = nullptr;
};

inline IdentityGenerationProgress& identityGenerationProgress() {
  static IdentityGenerationProgress progress;
  return progress;
}

inline void serviceIdentityGenerationProgress() {
  const auto progress = identityGenerationProgress();
  if (progress.callback != nullptr) progress.callback(progress.context);
}

class ScopedIdentityGenerationProgress {
  IdentityGenerationProgress _previous;
public:
  ScopedIdentityGenerationProgress(void (*callback)(void*), void* context)
      : _previous(identityGenerationProgress()) {
    identityGenerationProgress().callback = callback;
    identityGenerationProgress().context = context;
  }
  ~ScopedIdentityGenerationProgress() {
    identityGenerationProgress() = _previous;
  }
  ScopedIdentityGenerationProgress(const ScopedIdentityGenerationProgress&) = delete;
  ScopedIdentityGenerationProgress& operator=(const ScopedIdentityGenerationProgress&) = delete;
};

inline bool hasReservedIdentityPrefix(const Identity& identity) {
  return identity.pub_key[0] == 0x00 || identity.pub_key[0] == 0xFF;
}

template <typename Generator>
bool generateUsableLocalIdentity(LocalIdentity& identity, Generator generator) {
  for (size_t attempt = 0;
       attempt < MAX_LOCAL_IDENTITY_GENERATION_ATTEMPTS;
       ++attempt) {
    serviceIdentityGenerationProgress();
    identity = generator();
    serviceIdentityGenerationProgress();
    if (!hasReservedIdentityPrefix(identity)) return true;
  }
  return false;
}

} // namespace mesh
