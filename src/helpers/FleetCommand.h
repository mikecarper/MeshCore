#pragma once

#include <stddef.h>
#include <stdint.h>
#include "../Identity.h"
#include "FleetControlConfig.h"

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
  static constexpr size_t HeaderSize = 29;
  static constexpr size_t MinHeaderSize = 14;
  static constexpr size_t MaxPayloadLength = 165;
  static constexpr size_t FragmentHeaderSize = 11;
  static constexpr size_t FragmentDataLength = MaxPayloadLength - FragmentHeaderSize;
  static constexpr size_t MaxEnvelopeLength = 2 * FragmentDataLength;
  static constexpr size_t SinglePacketMaxCommandLength = MaxPayloadLength - MinHeaderSize - SignatureSize;
  static constexpr size_t MaxCommandLength = MaxEnvelopeLength - MinHeaderSize - SignatureSize;
  static constexpr size_t FixedTargetMaxCommandLength = MaxEnvelopeLength - HeaderSize - SignatureSize;
  static constexpr size_t MaxTargetBytes = 86;
  static constexpr size_t MaxTargetCount = 17;
  static constexpr uint8_t RegionRecordType = 0x20;
  static constexpr uint8_t HomeRecordType = 0x21;
  static constexpr uint8_t GeoRecordType = 0x22;
  static constexpr size_t GeoRecordLength = 13;
  static constexpr size_t MaxRegionNameLength = 30;
  static constexpr uint32_t MinRadiusMeters = 1;
  static constexpr uint32_t MaxRadiusMeters = 20050000;
  static constexpr uint32_t MinEpoch = 1735689600UL; // 2025-01-01 UTC
  static constexpr uint32_t MaxLifetime = 600;
  static constexpr uint32_t MaxClockLead = 60;

  struct Decoded {
    uint32_t sequence;
    uint32_t expires;
    bool broadcast;
    char command[MaxCommandLength + 1];
  };

  struct Targets {
    uint8_t length;
    uint8_t count;
    // Each record is a length/type byte (4, 6, or 16) followed by that many
    // bytes. Prefixes are raw public-key bytes; complete keys are SHA-256/128.
    // Region/home records are type 0x20/0x21, name length, then canonical name.
    // GPS records are type 0x22, signed latitude/longitude E6, radius metres LE32.
    uint8_t data[MaxTargetBytes];
  };

  enum class RegionTarget { Configured, Home };
  // Names are length-delimited and case-sensitive, with one leading '#' alias
  // removed by the encoder. The role matches against its existing RegionMap.
  // This bounded, read-only callback must not execute or mutate any state.
  using RegionMatcher = bool (*)(void* context, RegionTarget target,
                                const char* name, size_t length);
  using GeoMatcher = bool (*)(void* context, int32_t latitude_e6,
                             int32_t longitude_e6, uint32_t radius_meters);
  // Optional runtime work gate. Called only after all cheap envelope,
  // targeting and time checks, immediately before Ed25519 verification.
  using SignatureGate = bool (*)(void* context);

  struct Fragment {
    uint32_t sequence;
    uint16_t total_length;
    uint8_t index;
    const uint8_t* data;
    size_t length;
  };

  static bool privateKeyAllowed(const uint8_t* key);
  // "all" becomes the reserved zero target; otherwise require one complete
  // public key and derive the first 16 bytes of SHA-256, not a short node ID.
  static bool parseTarget(const char* text, uint8_t* target);
  // Parse one bounded token containing semicolon-separated public keys and named
  // regions. Bare key-length tokens never fall back to region names; explicit
  // region:/home: selectors permit ambiguous names. GPS circles use
  // gps:latitude,longitude:radius-km. "all" is accepted alone.
  static bool parseTargets(const char* text, size_t length, Targets& targets);
  static bool commandAllowed(const char* command);
  static bool withinRadius(int32_t center_latitude_e6, int32_t center_longitude_e6,
                           uint32_t radius_meters, int32_t local_latitude_e6,
                           int32_t local_longitude_e6);

  // Return the exact envelope size, or zero on invalid input. The signature
  // binds the domain, private channel key, complete header, and command bytes.
  static size_t encode(const LocalIdentity& publisher, const uint8_t* key,
                       uint32_t sequence, uint32_t expires,
                       const uint8_t* target, const char* command,
                       uint8_t* output, size_t capacity);

  // Broadcast and prefix/list requests use FMC2. Zero records imply all;
  // a single complete key retains FMC1 encoding. Targets and command share
  // the two-packet envelope budget, so lists reduce the command allowance.
  static size_t encode(const LocalIdentity& publisher, const uint8_t* key,
                       uint32_t sequence, uint32_t expires,
                       const Targets& targets, const char* command,
                       uint8_t* output, size_t capacity);

  // Accept an exact envelope only: group framing owns padding validation.
  // On success the caller must durably reject an old/repeated sequence before
  // any mutation, and defer execution until the receive stack has unwound.
  static bool decode(const Identity& publisher, const uint8_t* key,
                     const uint8_t* payload, size_t length, uint32_t now,
                     const uint8_t* self_public_key, Decoded& output,
                     RegionMatcher matcher = nullptr, void* context = nullptr,
                     GeoMatcher geo_matcher = nullptr, void* geo_context = nullptr,
                     SignatureGate signature_gate = nullptr, void* signature_context = nullptr);

  // Envelopes <= MaxPayloadLength travel unchanged. Longer envelopes use
  // exactly two FMP1 frames, each bounded by the group payload limit.
  static size_t fragment(const uint8_t* envelope, size_t length, uint8_t index,
                         uint8_t* output, size_t capacity);
  // Fragment metadata is untrusted. The receiver must assemble both parts,
  // verify the entire envelope once, require its signed sequence to match,
  // and durably reserve replay state before executing any command.
  static bool parseFragment(const uint8_t* payload, size_t length, Fragment& output);
};

} // namespace mesh
