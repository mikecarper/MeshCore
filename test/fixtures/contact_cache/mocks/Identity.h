#pragma once
#include "Utils.h"
#include <ed_25519.h>
namespace mesh {
class Identity {
public:
  uint8_t pub_key[32] = {};
  Identity() = default;
  explicit Identity(const uint8_t* key) { memcpy(pub_key, key, 32); }
};
class LocalIdentity : public Identity {
public:
  uint8_t private_key[64] = {};
  mutable unsigned derivations = 0;
  template <typename Reader> bool readFrom(Reader& in) {
    return in.read(pub_key, sizeof(pub_key)) == sizeof(pub_key)
        && in.read(private_key, sizeof(private_key)) == sizeof(private_key);
  }
  void readFrom(const uint8_t* src, size_t len) {
    if (len == sizeof(private_key) + sizeof(pub_key)) {
      memcpy(private_key, src, sizeof(private_key));
      memcpy(pub_key, src + sizeof(private_key), sizeof(pub_key));
    }
  }
  size_t writeTo(uint8_t* out, size_t size) const {
    if (size < 96) return 0;
    memcpy(out, private_key, 64);
    memcpy(out + 64, pub_key, 32);
    return 96;
  }
  void calcSharedSecret(uint8_t* out, const uint8_t* peer) const {
    ++derivations;
    ed25519_key_exchange(out, peer, private_key);
  }
};
}
