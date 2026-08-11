#pragma once

#if defined(USE_CC310_HW_CRYPTO)

#include <stddef.h>
#include <stdint.h>

namespace mesh {

// Serializes access to the CC310 and performs its process-lifetime
// initialization. A session is unavailable in interrupt context or when the
// one-time hardware initialization failed; callers must then use software.
class CC310CryptoSession {
public:
  CC310CryptoSession();
  ~CC310CryptoSession();

  explicit operator bool() const { return _available; }

  CC310CryptoSession(const CC310CryptoSession&) = delete;
  CC310CryptoSession& operator=(const CC310CryptoSession&) = delete;

private:
  bool _locked;
  bool _available;
};

// Initialize the CC310 once during normal system startup. Sessions also call
// this lazily so early crypto users remain safe.
bool initializeCC310Crypto();

// XOR CC310 random output into bytes already populated by another entropy
// source. A hardware failure leaves those existing bytes usable.
void mixCC310Random(uint8_t* dest, size_t size);

} // namespace mesh

#endif
