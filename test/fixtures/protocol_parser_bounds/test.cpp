#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include <helpers/AdvertDataHelpers.h>
#include <helpers/ClientPathObservation.h>
#include <helpers/DatagramPayloadLimits.h>
#include <helpers/RoutingPolicy.h>
#include "examples/simple_repeater/RateLimiter.h"
#include "production_constants.inc"
#include "production_packet.inc"

static void require(bool condition, const char* message) {
  if (!condition) {
    std::fprintf(stderr, "protocol parser bounds: %s\n", message);
    std::exit(1);
  }
}

struct ClientInfo {
  uint8_t out_path[MAX_PATH_SIZE] = {};
  uint8_t out_path_len = OUT_PATH_UNKNOWN;
  uint8_t observed_path[MAX_PATH_SIZE] = {};
  uint8_t observed_path_len = OUT_PATH_UNKNOWN;
  bool observed_path_pending = false;
  uint32_t observed_path_expiry = 0;
};

struct MyMesh {
  struct {
    char node_name[32] = "Bellevue";
    char owner_info[80] = "owner";
    bool disable_fwd = false;
  } _prefs;
  struct Clock {
    uint32_t now = 123456;
    uint32_t getCurrentTime() { return now; }
  } rtc_clock;
  Clock* getRTCClock() { return &rtc_clock; }
  struct RegionMap {
    unsigned exports = 0;
    size_t exportNamesTo(char* output, size_t capacity, uint8_t) {
      ++exports;
      const char names[] = "sea,pdx";
      require(capacity >= sizeof(names), "region reply capacity changed");
      memcpy(output, names, sizeof(names));
      return sizeof(names) - 1;
    }
  } region_map;
  RateLimiter anon_limiter{10000, 1};
  uint8_t reply_path[MAX_PATH_SIZE];
  uint8_t reply_path_len = OUT_PATH_UNKNOWN;
  uint8_t reply_data[MAX_PACKET_PAYLOAD] = {};
  mesh::Packet response;
  unsigned allocations = 0, direct_sends = 0, flood_sends = 0, logins = 0;
  uint32_t sent_delay = 0, login_timestamp = 0;
  bool login_flood = false, login_empty = false;
  struct ACL {
    ClientInfo client;
    bool present = false;
    ClientInfo* getClient(const uint8_t*, size_t) { return present ? &client : nullptr; }
  } acl;
  MyMesh() { memset(reply_path, 0xa5, sizeof(reply_path)); }
  uint8_t handleAnonRegionsReq(const mesh::Identity&, uint32_t, const uint8_t*, size_t);
  uint8_t handleAnonOwnerReq(const mesh::Identity&, uint32_t, const uint8_t*, size_t);
  uint8_t handleAnonClockReq(const mesh::Identity&, uint32_t, const uint8_t*, size_t);
  void onAnonDataRecv(mesh::Packet*, const uint8_t*, const mesh::Identity&, uint8_t*, size_t);
  uint8_t handleLoginReq(const mesh::Identity&, const uint8_t*, uint32_t timestamp,
                         const uint8_t* password, bool flood) {
    ++logins;
    login_timestamp = timestamp;
    login_flood = flood;
    login_empty = password[0] == 0;
    return 13;
  }
  bool isRs232BridgeRunning() const { return true; }
  bool isEspNowBridgeRunning() const { return true; }
  bool isBridgeRunning() const { return true; }
  unsigned long futureMillis(unsigned long delay) const { return 100 + delay; }
  mesh::Packet* createDatagram(uint8_t, const mesh::Identity&, const uint8_t*,
                              const uint8_t* data, size_t length) {
    ++allocations;
    require(length <= sizeof(response.payload), "reply exceeded datagram buffer");
    memcpy(response.payload, data, length);
    response.payload_len = length;
    return &response;
  }
  mesh::Packet* createPathReturn(const mesh::Identity& identity, const uint8_t* secret,
                                const uint8_t*, uint8_t, uint8_t,
                                const uint8_t* data, size_t length) {
    return createDatagram(PAYLOAD_TYPE_RESPONSE, identity, secret, data, length);
  }
  bool sendDirect(mesh::Packet*, const uint8_t* path, uint8_t encoded, uint32_t delay) {
    ++direct_sends;
    sent_delay = delay;
    require(mesh::Packet::isValidPathLen(encoded), "invalid path was transmitted");
    const size_t bytes = (encoded & 63U) * ((encoded >> 6) + 1U);
    response.path_len = encoded;
    memcpy(response.path, path, bytes);
    return true;
  }
  bool sendFloodReply(mesh::Packet*, uint32_t delay, uint8_t) {
    ++flood_sends;
    sent_delay = delay;
    return true;
  }
};

#include "production_handlers.inc"

static bool expectedCompletePath(unsigned encoded, size_t length) {
  const unsigned hash_size = (encoded >> 6) + 1;
  const unsigned path_bytes = (encoded & 63) * hash_size;
  return hash_size <= 3 && path_bytes <= MAX_PATH_SIZE && length >= path_bytes + 1;
}

static uint8_t invokeHandler(MyMesh& mesh, unsigned request_type,
                             const mesh::Identity& sender, const uint8_t* data, size_t length) {
  switch (request_type) {
    case ANON_REQ_TYPE_REGIONS: return mesh.handleAnonRegionsReq(sender, 42, data, length);
    case ANON_REQ_TYPE_OWNER: return mesh.handleAnonOwnerReq(sender, 42, data, length);
    default: return mesh.handleAnonClockReq(sender, 42, data, length);
  }
}

static void checkAnonymousHandlers() {
  const mesh::Identity sender;
  for (unsigned type : {ANON_REQ_TYPE_REGIONS, ANON_REQ_TYPE_OWNER, ANON_REQ_TYPE_BASIC}) {
    MyMesh empty;
    require(invokeHandler(empty, type, sender, nullptr, 0) == 0, "null path accepted");
    require(invokeHandler(empty, type, sender, nullptr, 100) == 0, "null nonempty path accepted");
    for (unsigned encoded = 0; encoded <= UINT8_MAX; ++encoded) {
      for (size_t length = 0; length <= MAX_PATH_SIZE + 1; ++length) {
        std::vector<uint8_t> bytes(length, 0x36);
        if (length) bytes[0] = encoded;
        MyMesh mesh;
        const bool expected = length > 0 && expectedCompletePath(encoded, length);
        const uint8_t reply_len = invokeHandler(mesh, type, sender, bytes.data(), length);
        require((reply_len != 0) == expected, "reply path encoding/length accepted incorrectly");
        if (!expected) {
          require(mesh.reply_path_len == OUT_PATH_UNKNOWN, "bad path changed reply route");
          require(std::all_of(std::begin(mesh.reply_path), std::end(mesh.reply_path),
                              [](uint8_t byte) { return byte == 0xa5; }),
                  "bad path copied bytes before rejection");
          require(mesh.region_map.exports == 0, "bad path generated region reply");
        } else {
          const size_t path_bytes = (encoded & 63U) * ((encoded >> 6) + 1U);
          require(mesh.reply_path_len == encoded, "valid path mode changed");
          require(memcmp(mesh.reply_path, bytes.data() + 1, path_bytes) == 0,
                  "valid path copied incorrectly");
          uint32_t timestamp = 0;
          memcpy(&timestamp, mesh.reply_data, sizeof(timestamp));
          require(timestamp == 42, "reply request tag changed");
          memcpy(&timestamp, mesh.reply_data + 4, sizeof(timestamp));
          require(timestamp == mesh.rtc_clock.now, "reply clock changed");
        }
      }
    }
  }
}

static void checkAnonymousDispatch() {
  const mesh::Identity sender;
  const uint8_t secret[PUB_KEY_SIZE] = {};
  mesh::Packet request;
  request.header = (PAYLOAD_TYPE_ANON_REQ << PH_TYPE_SHIFT) | ROUTE_TYPE_DIRECT;
  for (unsigned type : {ANON_REQ_TYPE_REGIONS, ANON_REQ_TYPE_OWNER, ANON_REQ_TYPE_BASIC}) {
    for (size_t length = 0; length < 6; ++length) {
      if (length == 4) continue; // The valid timestamp-only login is tested below.
      MyMesh mesh;
      std::vector<uint8_t> bytes(length + 1, 0xa5);
      if (length >= 5) bytes[4] = type;
      mesh.onAnonDataRecv(&request, secret, sender, bytes.data(), length);
      require(mesh.allocations == 0 && mesh.logins == 0, "incomplete request produced a response");
      if (length < 4) require(bytes[length] == 0xa5, "short request wrote a terminator");
    }
    for (unsigned encoded = 0; encoded <= UINT8_MAX; ++encoded) {
      for (size_t path_length = 0; path_length <= MAX_PATH_SIZE; ++path_length) {
        const size_t length = 6 + path_length;
        std::vector<uint8_t> bytes(length + 1, 0x59);
        bytes[4] = type;
        bytes[5] = encoded;
        MyMesh mesh;
        const bool expected = expectedCompletePath(encoded, 1 + path_length);
        mesh.onAnonDataRecv(&request, secret, sender, bytes.data(), length);
        require(mesh.allocations == (expected ? 1U : 0U), "dispatch did not bound remaining path bytes");
        require(mesh.direct_sends == (expected ? 1U : 0U) && mesh.flood_sends == 0,
                "anonymous reply route changed");
        if (expected) {
          require(mesh.response.path_len == encoded, "transmitted encoded path changed");
          require(mesh.sent_delay == SERVER_RESPONSE_DELAY, "response delay changed");
          if (type == ANON_REQ_TYPE_BASIC) {
            uint8_t expected_features = 0;
#ifdef WITH_RS232_BRIDGE
            expected_features |= 1;
#ifdef WITH_ESPNOW_BRIDGE
            expected_features |= 3;
#endif
#elif WITH_ESPNOW_BRIDGE
            expected_features |= 3;
#endif
            require(mesh.response.payload[8] == expected_features, "bridge discovery features changed");
          }
        }
      }
    }
    // Match the real decrypt buffer shape: padding/stale capacity must not
    // allow a 16-byte plaintext to invent a 64-byte return path.
    uint8_t padded[MAX_PACKET_PAYLOAD];
    memset(padded, 0xa5, sizeof(padded));
    padded[4] = type;
    padded[5] = (1U << 6) | 32U;
    MyMesh mesh;
    mesh.onAnonDataRecv(&request, secret, sender, padded, CIPHER_BLOCK_SIZE);
    require(mesh.allocations == 0, "short decrypted payload exposed scratch-buffer bytes");
  }

  MyMesh invalid;
  uint8_t one_byte = 0x5a;
  invalid.onAnonDataRecv(nullptr, secret, sender, &one_byte, 1);
  invalid.onAnonDataRecv(&request, secret, sender, nullptr, 16);
  invalid.onAnonDataRecv(&request, secret, sender, &one_byte, MAX_PACKET_PAYLOAD);
  invalid.onAnonDataRecv(&request, secret, sender, &one_byte, SIZE_MAX);
  require(one_byte == 0x5a && invalid.allocations == 0 && invalid.logins == 0,
          "oversized or null input was read/written");

  // BaseChatMesh::sendLogin encodes only the timestamp for an empty password.
  // Use exactly one spare terminator byte, as allowed by the decrypt buffer,
  // and make that byte nonzero to prove dispatch supplies the empty password.
  for (unsigned route : {ROUTE_TYPE_DIRECT, ROUTE_TYPE_FLOOD}) {
    uint8_t empty_login[5] = {0x78, 0x56, 0x34, 0x12, 0xa5};
    request.header = (PAYLOAD_TYPE_ANON_REQ << PH_TYPE_SHIFT) | route;
    MyMesh mesh;
    mesh.onAnonDataRecv(&request, secret, sender, empty_login, 4);
    require(mesh.logins == 1 && mesh.allocations == 1 && mesh.flood_sends == 1,
            "exact timestamp-only login was rejected");
    require(mesh.login_timestamp == 0x12345678 && mesh.login_empty && empty_login[4] == 0,
            "timestamp-only login did not receive its empty password/clock");
    require(mesh.login_flood == (route == ROUTE_TYPE_FLOOD),
            "timestamp-only login lost its receive route");
  }

  request.header = (PAYLOAD_TYPE_ANON_REQ << PH_TYPE_SHIFT) | ROUTE_TYPE_DIRECT;
  uint8_t login[17] = {};
  MyMesh direct_login;
  direct_login.onAnonDataRecv(&request, secret, sender, login, 16);
  require(direct_login.logins == 1 && direct_login.flood_sends == 1,
          "valid blank login dispatch changed");
  request.header = (PAYLOAD_TYPE_ANON_REQ << PH_TYPE_SHIFT) | ROUTE_TYPE_FLOOD;
  MyMesh flood_login;
  flood_login.onAnonDataRecv(&request, secret, sender, login, 16);
  require(flood_login.logins == 1 && flood_login.flood_sends == 1,
          "valid flood login dispatch changed");
  login[4] = ANON_REQ_TYPE_BASIC;
  MyMesh flood_query;
  flood_query.onAnonDataRecv(&request, secret, sender, login, 16);
  require(flood_query.allocations == 0, "direct-only anonymous query accepted as flood");
}

static void checkAdverts() {
  const uint8_t one_byte = ADV_NAME_MASK;
  require(!AdvertDataParser(nullptr, 0).isValid(), "null advert accepted");
  require(!AdvertDataParser(nullptr, 1).isValid(), "null nonempty advert accepted");
  require(!AdvertDataParser(&one_byte, MAX_ADVERT_DATA_SIZE + 1).isValid(), "oversized advert accepted");
  require(!AdvertDataParser(&one_byte, UINT8_MAX).isValid(), "max byte length advert accepted");
  for (unsigned flags = 0; flags <= UINT8_MAX; ++flags) {
    const size_t required = 1 + ((flags & ADV_LATLON_MASK) ? 8 : 0)
        + ((flags & ADV_FEAT1_MASK) ? 2 : 0) + ((flags & ADV_FEAT2_MASK) ? 2 : 0);
    for (size_t length = 0; length <= MAX_ADVERT_DATA_SIZE; ++length) {
      std::vector<uint8_t> bytes(length, 'x');
      if (length) bytes[0] = flags;
      AdvertDataParser parser(bytes.data(), length);
      require(parser.isValid() == (length >= required), "advert field boundary rejected incorrectly");
      if (parser.isValid()) {
        require(parser.getType() == (flags & 15), "advert type changed");
        require(strlen(parser.getName()) == ((flags & ADV_NAME_MASK) ? length - required : 0),
                "advert name length/termination changed");
      } else {
        require(parser.getIntLat() == 0 && parser.getIntLon() == 0
                && parser.getFeat1() == 0 && parser.getFeat2() == 0
                && !parser.hasName(), "truncated advert changed optional-field defaults");
      }
    }
  }
  uint8_t bytes[MAX_ADVERT_DATA_SIZE];
  AdvertDataBuilder builder(ADV_TYPE_REPEATER, "SEA Bellevue", 47.0, -122.0);
  builder.setFeat1(0x1234);
  builder.setFeat2(0x5678);
  AdvertDataParser parsed(bytes, builder.encodeTo(bytes));
  require(parsed.isValid() && parsed.getType() == ADV_TYPE_REPEATER
          && parsed.getIntLat() == 47000000 && parsed.getIntLon() == -122000000
          && parsed.getFeat1() == 0x1234 && parsed.getFeat2() == 0x5678
          && strcmp(parsed.getName(), "SEA Bellevue") == 0, "valid advert round trip changed");
}

int main() {
  checkAdverts();
  checkAnonymousHandlers();
  checkAnonymousDispatch();
  std::puts("PASS: production protocol parser bounds");
}
