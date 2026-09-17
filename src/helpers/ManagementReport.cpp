#include "ManagementReport.h"
#include <AES.h>
#include <SHA256.h>

namespace mesh { namespace management {
void passwordKey(const char* password, uint8_t key[32]) {
  SHA256 hash; hash.reset(); hash.update("#", 1);
  hash.update(password, strlen(password)); hash.finalize(key, 32);
}
void deriveKey(const uint8_t key[32], const char* domain, const uint8_t radio[16], uint8_t out[32]) {
  SHA256 hash; hash.resetHMAC(key, 32); hash.update(domain, strlen(domain));
  hash.update(radio, 16); hash.finalizeHMAC(key, 32, out, 32);
}
void fingerprint(const uint8_t key[32], const uint8_t radio[16], const uint8_t admin[32], uint8_t out[12]) {
  uint8_t derived[32]; deriveKey(key, "MeshCore-MGR1-ACL", radio, derived);
  SHA256 hash; hash.resetHMAC(derived, 32); hash.update(radio, 16); hash.update(admin, 32);
  hash.finalizeHMAC(derived, 32, out, 12); erase(derived, sizeof(derived));
}
bool equal(const uint8_t* a, const uint8_t* b, size_t size) {
  uint8_t d = 0; while (size--) d |= *a++ ^ *b++; return d == 0;
}
static void dbl(uint8_t b[16]) {
  const uint8_t carry = b[0] >> 7;
  for (unsigned i = 0; i < 15; ++i) b[i] = (b[i] << 1) | (b[i + 1] >> 7);
  b[15] = (b[15] << 1) ^ (carry ? 0x87 : 0);
}
// NIST SP 800-38B CMAC, using the existing rweather AES implementation.
static void cmac(AES128& aes, const uint8_t* data, size_t len, uint8_t out[16]) {
  uint8_t subkey[16] = {}, block[16] = {};
  aes.encryptBlock(subkey, subkey); dbl(subkey);
  while (len > 16) {
    for (unsigned i = 0; i < 16; ++i) block[i] ^= data[i];
    aes.encryptBlock(block, block); data += 16; len -= 16;
  }
  for (size_t i = 0; i < len; ++i) block[i] ^= data[i];
  if (len < 16) { dbl(subkey); block[len] ^= 0x80; }
  for (unsigned i = 0; i < 16; ++i) block[i] ^= subkey[i];
  aes.encryptBlock(out, block); erase(subkey, 16); erase(block, 16);
}
static void s2v(const uint8_t key[32], const uint8_t* aad, size_t aad_len,
                const uint8_t* data, size_t len, uint8_t tag[16]) {
  AES128 aes; aes.setKey(key, 16);
  uint8_t d[16] = {}, t[16], buf[MAX_PAYLOAD] = {};
  cmac(aes, d, 16, d); dbl(d); cmac(aes, aad, aad_len, t);
  for (unsigned i = 0; i < 16; ++i) d[i] ^= t[i];
  if (len >= 16) {
    memcpy(buf, data, len);
    for (unsigned i = 0; i < 16; ++i) buf[len - 16 + i] ^= d[i];
    cmac(aes, buf, len, tag);
  } else {
    dbl(d); memcpy(buf, data, len); buf[len] = 0x80;
    for (unsigned i = 0; i < 16; ++i) buf[i] ^= d[i];
    cmac(aes, buf, 16, tag);
  }
  erase(buf, sizeof(buf)); erase(d, 16); erase(t, 16);
}
static void ctr(const uint8_t key[32], uint8_t* data, size_t len, const uint8_t tag[16]) {
  AES128 aes; aes.setKey(key + 16, 16);
  uint8_t counter[16], stream[16]; memcpy(counter, tag, 16);
  counter[8] &= 0x7f; counter[12] &= 0x7f;
  while (len) {
    aes.encryptBlock(stream, counter); const size_t n = len < 16 ? len : 16;
    for (size_t i = 0; i < n; ++i) data[i] ^= stream[i];
    data += n; len -= n;
    for (int i = 15; i >= 0 && ++counter[i] == 0; --i) {}
  }
  erase(counter, 16); erase(stream, 16);
}
bool seal(const uint8_t key[32], const uint8_t* aad, size_t aad_len,
          uint8_t* data, size_t len, uint8_t tag[16]) {
  if (len > MAX_PAYLOAD || aad_len > MAX_PAYLOAD) return false;
  s2v(key, aad, aad_len, data, len, tag); ctr(key, data, len, tag); return true;
}
bool open(const uint8_t key[32], const uint8_t* aad, size_t aad_len,
          uint8_t* data, size_t len, const uint8_t tag[16]) {
  if (len > MAX_PAYLOAD || aad_len > MAX_PAYLOAD) return false;
  uint8_t expected[16]; ctr(key, data, len, tag); s2v(key, aad, aad_len, data, len, expected);
  const bool ok = equal(expected, tag, 16); erase(expected, 16);
  if (!ok) erase(data, len);
  return ok;
}
} }
