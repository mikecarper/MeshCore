#include <cassert>
#include <cstdint>
#include <cstring>
#ifdef TEST_RWEATHER
#include <helpers/tracker/HomeBeaconCrypto.h>
#else
#include <openssl/hmac.h>
#endif
#include <helpers/tracker/HomeBeacon.h>
#include <helpers/tracker/TrackerEscalation.h>

using mesh::tracker::HomeBeacon;
using mesh::tracker::HomePresence;
using mesh::tracker::TrackerEscalation;

static bool hmacSha256(const uint8_t* key, size_t key_len, const uint8_t* msg,
                       size_t msg_len, uint8_t out[32]) {
#ifdef TEST_RWEATHER
  return mesh::tracker::homeBeaconHmacSha256(key, key_len, msg, msg_len, out);
#else
  unsigned int n = 0;
  return HMAC(EVP_sha256(), key, int(key_len), msg, msg_len, out, &n) && n == 32;
#endif
}

int main() {
  const uint8_t id1[4] = {0x12, 0x34, 0x56, 0x78};
  const uint8_t id2[4] = {0x87, 0x65, 0x43, 0x21};
  const uint8_t key1[16] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
  const uint8_t key2[32] = {2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,
                            18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33};
  const uint8_t empty[16] = {};
  const uint8_t known[25] = {0x01,0x12,0x34,0x56,0x78,0x03,0x60,0xaa,0x2a,
                              0x96,0xd4,0x67,0x05,0x30,0x00,0xe0,0x46,0x24,
                              0xaa,0x52,0xfb,0xbb,0x32,0x0c,0xd2};
  constexpr uint64_t t = 1700000000ULL;
  HomeBeacon b1(id1, key1, sizeof(key1), hmacSha256);
  HomeBeacon b2(id2, key2, sizeof(key2), hmacSha256);
  HomeBeacon unknown(id2, key1, sizeof(key1), hmacSha256);
  HomeBeacon wrong_key(id1, key2, sizeof(key2), hmacSha256);
  HomeBeacon no_key(id1, empty, sizeof(empty), hmacSha256);
  HomeBeacon bad_len(id1, key1, 15, hmacSha256);
  uint8_t p1[25], p2[25], modified[25];
  uint64_t expiry = 99;

  // Independently generated HMAC-SHA256 vector.
  assert(b1.encode(t, true, p1));
  assert(std::memcmp(p1, known, sizeof(known)) == 0);
  assert(b1.verify(p1, sizeof(p1), t, true, expiry));
  assert(expiry == ((t / 30) + 2) * 30);
  assert(!no_key.encode(t, true, p2) && !bad_len.encode(t, true, p2));
  assert(!b1.encode(t, false, p2));
  assert(!b1.verify(p1, sizeof(p1), t, false, expiry) && expiry == 0);
  assert(!b1.verify(p1, sizeof(p1)-1, t, true, expiry));
  assert(!unknown.verify(p1, sizeof(p1), t, true, expiry));
  assert(!wrong_key.verify(p1, sizeof(p1), t, true, expiry));
  assert(b2.encode(t, true, p2));
  assert(b2.verify(p2, sizeof(p2), t, true, expiry));
  assert(!b1.verify(p2, sizeof(p2), t, true, expiry));
  // Identity, slot, version, and tag are all covered by authentication.
  for (size_t i = 0; i < sizeof(p1); ++i) {
    std::memcpy(modified, p1, sizeof(p1));
    modified[i] ^= 1;
    assert(!b1.verify(modified, sizeof(modified), t, true, expiry));
  }
  // Any fresh configured beacon can grant home. Manual lost still wins.
  bool at_home = b1.verify(p2, sizeof(p2), t, true, expiry) ||
                 b2.verify(p2, sizeof(p2), t, true, expiry);
  assert(at_home);
  TrackerEscalation causes = {true, true, true, at_home ? HomePresence::Present : HomePresence::Unknown};
  assert(causes.lost());

  // Replays never renew the authenticated slot's absolute expiry.
  const uint64_t end = ((t / 30) + 2) * 30;
  assert(b1.verify(p1, sizeof(p1), end - 1, true, expiry) && expiry == end);
  assert(!b1.verify(p1, sizeof(p1), end, true, expiry) && expiry == 0);
  assert(!b2.verify(p2, sizeof(p2), end, true, expiry));
  // Rollback latches fail-closed until the caller re-establishes trust.
  assert(!b1.verify(p1, sizeof(p1), t, true, expiry) && b1.clockRollback());
  assert(!b1.verify(p1, sizeof(p1), end, true, expiry));
  b1.resetClockTrust();
  assert(b1.verify(p1, sizeof(p1), t, true, expiry));
  b1.resetClockTrust();
  assert(!b1.verify(p1, sizeof(p1), 0, true, expiry));
  assert(!b1.verify(p1, sizeof(p1), HomeBeacon::kEarliestUnix - 1, true, expiry));

  // No uint32 Unix-seconds rollover in 2106; slot conversion is 64-bit.
  b1.resetClockTrust();
  assert(b1.encode(0x100000001ULL, true, p1));
  assert(b1.verify(p1, sizeof(p1), 0x100000001ULL, true, expiry));
  const uint64_t last_slot = uint64_t(UINT32_MAX) * 30;
  b1.resetClockTrust();
  assert(b1.encode(last_slot, true, p1));
  assert(b1.verify(p1, sizeof(p1), last_slot, true, expiry));
  assert(expiry == (uint64_t(UINT32_MAX) + 2) * 30);
  assert(!b1.encode(last_slot + 30, true, p2));
  assert(!b1.verify(p1, sizeof(p1), last_slot + 30, true, expiry));
}
