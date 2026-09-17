#include <helpers/ManagementReport.h>
#include <cassert>
#include <cstdio>
#include <vector>
#include <string>
using namespace mesh::management;
static std::vector<uint8_t> hex(const char* text) {
  std::vector<uint8_t> b;
  while (*text) { unsigned n; assert(sscanf(text, "%2x", &n) == 1); b.push_back(n); text += 2; }
  return b;
}
static void print(const uint8_t* p, size_t n) { while (n--) printf("%02x", *p++); puts(""); }
int main() {
  // RFC 5297 Appendix A.1 (real AES, never the repository's no-op AES mock).
  auto k = hex("fffefdfcfbfaf9f8f7f6f5f4f3f2f1f0f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff");
  auto ad = hex("101112131415161718191a1b1c1d1e1f2021222324252627");
  auto plain = hex("112233445566778899aabbccddee"); auto p = plain;
  uint8_t tag[16]; assert(seal(k.data(), ad.data(), ad.size(), p.data(), p.size(), tag));
  assert(equal(tag, hex("85632d07c6e8f37f950acd320a2ecc93").data(), 16));
  assert(p == hex("40c02b9690c4dc04daef7f6afe5c"));
  assert(open(k.data(), ad.data(), ad.size(), p.data(), p.size(), tag)); assert(p == plain);
  for (size_t n : {size_t(0), size_t(1), size_t(15), size_t(16), size_t(17), size_t(78), size_t(177)}) {
    std::vector<uint8_t> b(n, 0x39), original = b;
    assert(seal(k.data(), ad.data(), ad.size(), b.data(), n, tag));
    assert(open(k.data(), ad.data(), ad.size(), b.data(), n, tag)); assert(b == original);
    assert(seal(k.data(), ad.data(), ad.size(), b.data(), n, tag)); tag[4] ^= 1;
    assert(!open(k.data(), ad.data(), ad.size(), b.data(), n, tag));
    for (uint8_t c : b) assert(c == 0);
  }
  assert(!seal(k.data(), ad.data(), ad.size(), p.data(), 178, tag));
  History history;
  history.sample(0, 3900, -4); history.sample(59, 3100, 98);
  assert(history.week().voltage == 3100 && history.week().high == temperature(98));
  history.sample(3600, 3800, 28);
  history.advance(169 * 3600);
  assert(history.week().voltage == 3800 && history.period.voltage == 3100);
  history.advance(400 * 3600); assert(history.week().voltage == 0);
  assert(temperature(NAN) == 0 && temperature(-51) == 252 && temperature(201) == 253);
  Schedule schedule;
  assert(Schedule::validDirect(5) && Schedule::validDirect(90));
  assert(!Schedule::validDirect(4) && !Schedule::validDirect(91));
  assert(Schedule::validFlood(21) && Schedule::validFlood(90));
  assert(!Schedule::validFlood(20) && !Schedule::validFlood(91));
  schedule.advance(5 * DAY); assert(schedule.flood == 16 * DAY);
  schedule.reserve(false, 5, 21, 0); assert(schedule.flood == 16 * DAY);
  schedule.advance(16 * DAY); assert(!schedule.direct && !schedule.flood);
  schedule.reserve(true, 5, 21, 0); assert(schedule.flood == 21 * DAY);
  assert(schedule.direct == 5 * DAY);
  schedule.direct = 4 * DAY; schedule.flood = 0;
  schedule.reserve(true, 5, 21, 0); assert(schedule.direct == 4 * DAY);
  schedule.reserve(true, 5, 90, 0); assert(schedule.flood == 90 * DAY);
  Schedule restored = schedule; restored.advance(90 * DAY); assert(!restored.flood);
  uint8_t root[32], radio[16] = {}, admin[32] = {}, token[12], other[12];
  passwordKey("management test password", root);
  fingerprint(root, radio, admin, token); radio[0] = 1;
  fingerprint(root, radio, admin, other); assert(!equal(token, other, 12));
  AclList acl; assert(acl.add(token, ADMIN)); assert(acl.add(token, OTA_SIGNER));
  assert(acl.count == 1 && acl.entries[0][12] == 3);
  for (uint8_t i = 1; i < MAX_KEYS; ++i) { token[0] = i; assert(acl.add(token, ADMIN)); }
  token[0] = 240; assert(!acl.add(token, ADMIN)); assert(acl.pages() == MAX_PAGES);
  // Emit cross-language packets with all header fields and maximum ACL pages.
  for (uint8_t page = 0; page < acl.pages(); ++page) {
    uint8_t raw[MAX_PAYLOAD] = {}, enc[32]; memcpy(raw, "MGR1", 4); memcpy(raw + 4, radio, 16);
    write32(raw + 20, 42); write32(raw + 28, 0x01110105); write16(raw + 76, FIRMWARE);
    raw[71] = 5; raw[78] = page; raw[79] = acl.pages(); raw[80] = acl.count;
    raw[81] = page * PER_PAGE; raw[82] = PER_PAGE;
    memcpy(raw + HEADER, acl.entries[page * PER_PAGE], PER_PAGE * ENTRY);
    deriveKey(root, "MeshCore-MGR1-SIV", radio, enc);
    assert(seal(enc, raw, HEADER, raw + HEADER, PER_PAGE * ENTRY, raw + MAX_PAYLOAD - TAG));
    assert(validPage(raw, sizeof(raw))); print(raw, sizeof(raw));
    raw[81]++; assert(!validPage(raw, sizeof(raw))); raw[81]--;
    assert(!validPage(raw, sizeof(raw) - 1)); raw[80] = 37; assert(!validPage(raw, sizeof(raw)));
  }
  for (size_t n = 0; n < HEADER + TAG; ++n) assert(!validPage(plain.data(), n));
}
