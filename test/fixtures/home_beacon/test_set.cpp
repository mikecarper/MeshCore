#include <cassert>
#include <cstdint>
#include <cstring>
#include <openssl/hmac.h>
#include <helpers/tracker/HomeBeaconSet.h>

using namespace mesh::tracker;

static bool mac(const uint8_t* key, size_t len, const uint8_t* msg, size_t n, uint8_t out[32]) {
  unsigned int produced = 0;
  return HMAC(EVP_sha256(), key, int(len), msg, n, out, &produced) && produced == 32;
}

int main() {
  const uint8_t id1[] = {1, 2, 3, 4}, id2[] = {5, 6, 7, 8}, id3[] = {9, 10, 11, 12};
  const uint8_t key1[16] = {1}, key2[16] = {2}, key3[16] = {3}, zero[16] = {};
  constexpr uint64_t now = 1700000000ULL;
  HomeBeaconSet<2> set(mac);
  assert(!set.add(id1, zero, sizeof(zero)));
  assert(set.add(id1, key1, sizeof(key1)));
  assert(!set.add(id1, key2, sizeof(key2)));
  assert(!set.add(id2, key1, sizeof(key1)));
  assert(set.add(id2, key2, sizeof(key2)));
  assert(!set.add(id3, key3, sizeof(key3)));

  uint8_t one[27] = {0xff, 0xff}, two[27] = {0xff, 0xff};
  HomeBeacon b1(id1, key1, sizeof(key1), mac), b2(id2, key2, sizeof(key2), mac);
  assert(b1.encode(now, true, one + 2));
  assert(b2.encode(now, true, two + 2));
  assert(set.presence(now, true) == HomePresence::Unknown);
  assert(!set.observe(one, sizeof(one), now, false));
  assert(set.presence(now, false) == HomePresence::Unknown);
  assert(set.observe(one, sizeof(one), now, true));
  assert(set.presence(now, true) == HomePresence::Present);
  // An untrusted/invalid report cannot extend or clear valid evidence.
  two[26] ^= 1;
  assert(!set.observe(two, sizeof(two), now + 1, true));
  assert(set.presence(now + 1, true) == HomePresence::Present);
  two[26] ^= 1;
  assert(set.observe(two, sizeof(two), now + 1, true));
  assert(set.remove(id1));
  assert(set.presence(now + 1, true) == HomePresence::Unknown);
  assert(!set.observe(one, sizeof(one), now + 1, true));
  assert(set.observe(two, sizeof(two), now + 1, true));

  const uint64_t end = (now / 30 + 2) * 30;
  assert(set.presence(end - 1, true) == HomePresence::Present);
  assert(!set.observe(two, sizeof(two), end, true));
  assert(set.presence(end, true) == HomePresence::Unknown);
  assert(!set.observe(two, sizeof(two), now, true));
  assert(set.clockRollback());
  assert(set.presence(end, true) == HomePresence::Unknown);
  set.resetClockTrust();
  assert(set.observe(two, sizeof(two), now, true));
  assert(set.presence(now, true) == HomePresence::Present);
  assert(set.presence(now, false) == HomePresence::Unknown);
  assert(set.presence(now, true) == HomePresence::Unknown);
  two[0] = 0;
  assert(!set.observe(two, sizeof(two), now, true));
  assert(!set.observe(nullptr, 0, now, true));
  assert(!set.observe(one, sizeof(one) - 1, now, true));
}
