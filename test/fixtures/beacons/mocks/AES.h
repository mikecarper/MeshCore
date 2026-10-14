#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>

// BeaconManager uses only the production preset parser/modem calculator.
// Encryption has its independent real-AES oracle in test_meshtext_chat.py.
class BeaconUnusedAES {
 public:
  bool setKey(const uint8_t*, size_t) { return false; }
  void encryptBlock(uint8_t* output, const uint8_t* input) { std::memcpy(output, input, 16); }
  void clear() {}
};
using AES128 = BeaconUnusedAES;
using AES256 = BeaconUnusedAES;
