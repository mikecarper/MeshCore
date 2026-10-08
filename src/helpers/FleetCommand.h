#pragma once

#include <stddef.h>
#include <stdint.h>
#include "../Identity.h"

#ifndef MESH_ENABLE_FLEET_CONTROL
  // Fixed 240 KiB STM32 images have no room for fleet control.
  #if defined(STM32_PLATFORM)
    #define MESH_ENABLE_FLEET_CONTROL 0
  #else
    #define MESH_ENABLE_FLEET_CONTROL 1
  #endif
#endif

namespace mesh {

// Signed, private-channel fleet commands. The channel key supplies privacy;
// only the publisher's Ed25519 signature authorizes a command. Receivers store
// the public identity and never need the publisher's private identity.
class FleetCommand {
public:
  static constexpr uint16_t DataType = 0xFF01;
  static constexpr size_t KeySize = 16;
  static constexpr size_t TargetSize = 16;
  static constexpr size_t SignatureSize = 64;
  static constexpr size_t MaxCommandLength = 72;
  static constexpr size_t HeaderSize = 29;
  static constexpr size_t MaxPayloadLength = 165;
  static constexpr uint32_t MinEpoch = 1735689600UL; // 2025-01-01 UTC
  static constexpr uint32_t MaxLifetime = 600;
  static constexpr uint32_t MaxClockLead = 60;

  struct Decoded {
    uint32_t sequence;
    uint32_t expires;
    char command[MaxCommandLength + 1];
  };

  static bool privateKeyAllowed(const uint8_t* key);
  // "all" becomes the reserved zero target; otherwise require one complete
  // public key and derive the first 16 bytes of SHA-256, not a short node ID.
  static bool parseTarget(const char* text, uint8_t* target);
  static bool commandAllowed(const char* command);

  // Return the exact envelope size, or zero on invalid input. The signature
  // binds the domain, private channel key, complete header, and command bytes.
  static size_t encode(const LocalIdentity& publisher, const uint8_t* key,
                       uint32_t sequence, uint32_t expires,
                       const uint8_t* target, const char* command,
                       uint8_t* output, size_t capacity);

  // Accept an exact envelope only: group framing owns padding validation.
  // On success the caller must durably reject an old/repeated sequence before
  // any mutation, and defer execution until the receive stack has unwound.
  static bool decode(const Identity& publisher, const uint8_t* key,
                     const uint8_t* payload, size_t length, uint32_t now,
                     const uint8_t* self_public_key, Decoded& output);
};

} // namespace mesh
