#pragma once
#ifndef MESH_ENABLE_FLEET_CONTROL
#define MESH_ENABLE_FLEET_CONTROL 1
#endif

#include <cassert>
#include <cstddef>
#include <cstdint>

// These narrower dispatch fixtures retain the complete production fleet branch
// but have no channel peripheral. The separate companion fleet suite compiles
// the real codec with real Ed25519 and exercises packet admission end to end.
#ifndef MAX_GROUP_CHANNELS
#define MAX_GROUP_CHANNELS 4
#endif
#ifndef PUB_KEY_SIZE
#define PUB_KEY_SIZE 32
#endif
#ifndef OUT_PATH_UNKNOWN
#define OUT_PATH_UNKNOWN 255
#endif

namespace mesh {
struct FleetCommand {
  static constexpr uint16_t DataType = 0xFF01;
  static constexpr size_t KeySize = 16, TargetSize = 16, MaxPayloadLength = 165;
  static constexpr uint32_t MinEpoch = 1735689600UL, MaxLifetime = 600;
  static bool parseTarget(const char*, uint8_t*) { return false; }
  static bool commandAllowed(const char*) { return false; }
  static bool privateKeyAllowed(const uint8_t*) { return false; }
  template<class Signer>
  static size_t encode(const Signer&, const uint8_t*, uint32_t, uint32_t,
                       const uint8_t*, const char*, uint8_t*, size_t) {
    assert(false && "fleet encoding belongs in the dedicated real-codec suite");
    return 0;
  }
};
}

struct FleetFixtureChannel { uint8_t secret[32] = {}; };
struct ChannelDetails { FleetFixtureChannel channel; char name[32] = {}; };
struct FleetFixtureClock { uint32_t getCurrentTime() const { return 0; } };
struct CompanionFleetFixture {
  bool getChannel(int, ChannelDetails&) { return false; }
  FleetFixtureClock* getRTCClock() {
    static FleetFixtureClock clock;
    return &clock;
  }
  bool sendGroupData(FleetFixtureChannel&, uint8_t*, uint8_t, uint16_t,
                     const uint8_t*, int) {
    assert(false && "fleet packet admission belongs in its dedicated suite");
    return false;
  }
};
