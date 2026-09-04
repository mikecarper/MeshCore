#pragma once
#include "Arduino.h"
#define PUB_KEY_SIZE 32
#define MAX_PATH_SIZE 64
namespace mesh {
struct Identity {
  uint8_t pub_key[PUB_KEY_SIZE];
  Identity() { std::memset(pub_key, 0, sizeof(pub_key)); }
  explicit Identity(const uint8_t* key) { std::memcpy(pub_key, key, sizeof(pub_key)); }
  bool matches(const Identity& other) const { return std::memcmp(pub_key, other.pub_key, sizeof(pub_key)) == 0; }
};
struct LocalIdentity : Identity {
  void calcSharedSecret(uint8_t* output, const uint8_t* key) const {
    std::memcpy(output, key, PUB_KEY_SIZE);
  }
};
}
