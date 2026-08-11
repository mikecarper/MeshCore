#include "Utils.h"
#include <AES.h>
#include <SHA256.h>

#ifdef USE_CC310_HW_CRYPTO
#include "helpers/NRF52Crypto.h"
#include "nrf_cc310/include/crys_hash.h"
#include "nrf_cc310/include/crys_hmac.h"
#include "nrf_cc310/include/ssi_aes.h"
#endif

#ifdef ARDUINO
  #include <Arduino.h>
#endif

namespace {

void sha256Software(uint8_t* hash, size_t hash_len, const uint8_t* msg, int msg_len) {
  SHA256 sha;
  sha.update(msg, msg_len);
  sha.finalize(hash, hash_len);
}

void sha256Software(uint8_t* hash, size_t hash_len,
                    const uint8_t* frag1, int frag1_len,
                    const uint8_t* frag2, int frag2_len) {
  SHA256 sha;
  sha.update(frag1, frag1_len);
  sha.update(frag2, frag2_len);
  sha.finalize(hash, hash_len);
}

int decryptSoftware(const uint8_t* shared_secret, uint8_t* dest,
                    const uint8_t* src, int src_len) {
  AES128 aes;
  uint8_t* dp = dest;
  const uint8_t* sp = src;

  aes.setKey(shared_secret, CIPHER_KEY_SIZE);
  while (sp - src < src_len) {
    aes.decryptBlock(dp, sp);
    dp += CIPHER_BLOCK_SIZE;
    sp += CIPHER_BLOCK_SIZE;
  }
  return static_cast<int>(sp - src);
}

int encryptSoftware(const uint8_t* shared_secret, uint8_t* dest,
                    const uint8_t* src, int src_len) {
  AES128 aes;
  uint8_t* dp = dest;

  aes.setKey(shared_secret, CIPHER_KEY_SIZE);
  while (src_len >= CIPHER_BLOCK_SIZE) {
    aes.encryptBlock(dp, src);
    dp += CIPHER_BLOCK_SIZE;
    src += CIPHER_BLOCK_SIZE;
    src_len -= CIPHER_BLOCK_SIZE;
  }
  if (src_len > 0) {
    uint8_t tmp[CIPHER_BLOCK_SIZE] = {};
    memcpy(tmp, src, src_len);
    aes.encryptBlock(dp, tmp);
    dp += CIPHER_BLOCK_SIZE;
  }
  return static_cast<int>(dp - dest);
}

void hmacSoftware(const uint8_t* shared_secret, uint8_t* dest,
                  const uint8_t* src, int src_len) {
  SHA256 sha;
  sha.resetHMAC(shared_secret, PUB_KEY_SIZE);
  sha.update(src, src_len);
  sha.finalizeHMAC(shared_secret, PUB_KEY_SIZE, dest, CIPHER_MAC_SIZE);
}

#ifdef USE_CC310_HW_CRYPTO

bool rangesOverlap(const uint8_t* first, size_t first_len,
                   const uint8_t* second, size_t second_len) {
  const uintptr_t first_addr = reinterpret_cast<uintptr_t>(first);
  const uintptr_t second_addr = reinterpret_cast<uintptr_t>(second);
  if (first_addr <= second_addr) return second_addr - first_addr < first_len;
  return first_addr - second_addr < second_len;
}

// Returns -1 when the hardware path is unavailable or any CC310 call fails.
// Callers retain the original input and can recompute the complete result in
// software.
int aesHardware(bool encrypting, const uint8_t* shared_secret, uint8_t* dest,
                const uint8_t* src, int src_len) {
  mesh::CC310CryptoSession session;
  if (!session) return -1;

  static SaSiAesUserContext_t ctx;
  SaSiAesUserKeyData_t key_data = {
    const_cast<uint8_t*>(shared_secret), CIPHER_KEY_SIZE
  };
  SaSiError_t rc = SaSi_AesInit(
      &ctx, encrypting ? SASI_AES_ENCRYPT : SASI_AES_DECRYPT,
      SASI_AES_MODE_ECB, SASI_AES_PADDING_NONE);
  const bool initialized = rc == SASI_OK;
  if (rc == SASI_OK) {
    rc = SaSi_AesSetKey(&ctx, SASI_AES_USER_KEY, &key_data, sizeof(key_data));
  }

  uint8_t* dp = dest;
  const uint8_t* sp = src;
  int remaining = src_len;
  while (rc == SASI_OK && remaining >= CIPHER_BLOCK_SIZE) {
    rc = SaSi_AesBlock(&ctx, const_cast<uint8_t*>(sp), CIPHER_BLOCK_SIZE, dp);
    if (rc == SASI_OK) {
      dp += CIPHER_BLOCK_SIZE;
      sp += CIPHER_BLOCK_SIZE;
      remaining -= CIPHER_BLOCK_SIZE;
    }
  }
  if (rc == SASI_OK && encrypting && remaining > 0) {
    uint8_t padded[CIPHER_BLOCK_SIZE] = {};
    memcpy(padded, sp, remaining);
    rc = SaSi_AesBlock(&ctx, padded, CIPHER_BLOCK_SIZE, dp);
    if (rc == SASI_OK) dp += CIPHER_BLOCK_SIZE;
  }

  size_t final_size = 0;
  if (rc == SASI_OK) {
    rc = SaSi_AesFinish(&ctx, 0, NULL, 0, NULL, &final_size);
  }
  const SaSiError_t free_rc = initialized ? SaSi_AesFree(&ctx) : SASI_OK;
  if (rc != SASI_OK || free_rc != SASI_OK) return -1;
  return static_cast<int>(dp - dest);
}

bool hmacHardware(const uint8_t* shared_secret, uint8_t* dest,
                  const uint8_t* src, int src_len) {
  mesh::CC310CryptoSession session;
  if (!session) return false;

  static CRYS_HASH_Result_t result;
  const CRYSError_t rc = CRYS_HMAC(
      CRYS_HASH_SHA256_mode, const_cast<uint8_t*>(shared_secret), PUB_KEY_SIZE,
      const_cast<uint8_t*>(src), static_cast<size_t>(src_len), result);
  if (rc != CRYS_OK) return false;
  memcpy(dest, result, CIPHER_MAC_SIZE);
  return true;
}

#endif

} // namespace

namespace mesh {

uint32_t RNG::nextInt(uint32_t _min, uint32_t _max) {
  uint32_t num;
  random((uint8_t *) &num, sizeof(num));
  return (num % (_max - _min)) + _min;
}

void Utils::sha256(uint8_t *hash, size_t hash_len, const uint8_t* msg, int msg_len) {
#ifdef USE_CC310_HW_CRYPTO
  if (hash_len <= SHA256::HASH_SIZE && msg_len >= 0) {
    CC310CryptoSession session;
    if (session) {
      static CRYS_HASH_Result_t result;
      const CRYSError_t rc = CRYS_HASH(
          CRYS_HASH_SHA256_mode, const_cast<uint8_t*>(msg),
          static_cast<size_t>(msg_len), result);
      if (rc == CRYS_OK) {
        memcpy(hash, result, hash_len);
        return;
      }
    }
  }
#endif
  sha256Software(hash, hash_len, msg, msg_len);
}

void Utils::sha256(uint8_t *hash, size_t hash_len, const uint8_t* frag1, int frag1_len, const uint8_t* frag2, int frag2_len) {
#ifdef USE_CC310_HW_CRYPTO
  if (hash_len <= SHA256::HASH_SIZE && frag1_len > 0 && frag2_len > 0) {
    CC310CryptoSession session;
    if (session) {
      static CRYS_HASHUserContext_t ctx;
      static CRYS_HASH_Result_t result;
      CRYSError_t rc = CRYS_HASH_Init(&ctx, CRYS_HASH_SHA256_mode);
      const bool initialized = rc == CRYS_OK;
      if (rc == CRYS_OK) {
        rc = CRYS_HASH_Update(&ctx, const_cast<uint8_t*>(frag1),
                              static_cast<size_t>(frag1_len));
      }
      if (rc == CRYS_OK) {
        rc = CRYS_HASH_Update(&ctx, const_cast<uint8_t*>(frag2),
                              static_cast<size_t>(frag2_len));
      }
      if (rc == CRYS_OK) rc = CRYS_HASH_Finish(&ctx, result);
      if (rc == CRYS_OK) {
        memcpy(hash, result, hash_len);
        return;
      }
      if (initialized) {
        const bool context_freed = CRYS_HASH_Free(&ctx) == CRYS_OK;
        (void) context_freed;
      }
    }
  }
#endif
  sha256Software(hash, hash_len, frag1, frag1_len, frag2, frag2_len);
}

int Utils::decrypt(const uint8_t* shared_secret, uint8_t* dest, const uint8_t* src, int src_len) {
  if (shared_secret == NULL || dest == NULL || src == NULL || src_len <= 0
      || (src_len % CIPHER_BLOCK_SIZE) != 0) {
    return 0;
  }

#ifdef USE_CC310_HW_CRYPTO
  if (!rangesOverlap(dest, static_cast<size_t>(src_len),
                     src, static_cast<size_t>(src_len))) {
    const int hardware_len = aesHardware(false, shared_secret, dest, src, src_len);
    if (hardware_len >= 0) return hardware_len;
  }
#endif
  return decryptSoftware(shared_secret, dest, src, src_len);
}

int Utils::encrypt(const uint8_t* shared_secret, uint8_t* dest, const uint8_t* src, int src_len) {
  if (shared_secret == NULL || dest == NULL || src == NULL || src_len <= 0) return 0;

#ifdef USE_CC310_HW_CRYPTO
  const size_t output_len = static_cast<size_t>(
      (src_len + CIPHER_BLOCK_SIZE - 1) / CIPHER_BLOCK_SIZE * CIPHER_BLOCK_SIZE);
  if (!rangesOverlap(dest, output_len, src, static_cast<size_t>(src_len))) {
    const int hardware_len = aesHardware(true, shared_secret, dest, src, src_len);
    if (hardware_len >= 0) return hardware_len;
  }
#endif
  return encryptSoftware(shared_secret, dest, src, src_len);
}

int Utils::encryptThenMAC(const uint8_t* shared_secret, uint8_t* dest, const uint8_t* src, int src_len) {
  int enc_len = encrypt(shared_secret, dest + CIPHER_MAC_SIZE, src, src_len);

#ifdef USE_CC310_HW_CRYPTO
  if (!hmacHardware(shared_secret, dest, dest + CIPHER_MAC_SIZE, enc_len))
#endif
  {
    hmacSoftware(shared_secret, dest, dest + CIPHER_MAC_SIZE, enc_len);
  }

  return CIPHER_MAC_SIZE + enc_len;
}

int Utils::MACThenDecrypt(const uint8_t* shared_secret, uint8_t* dest, const uint8_t* src, int src_len) {
  if (shared_secret == NULL || dest == NULL || src == NULL || src_len <= CIPHER_MAC_SIZE) return 0;

  const int enc_len = src_len - CIPHER_MAC_SIZE;
  if ((enc_len % CIPHER_BLOCK_SIZE) != 0) return 0;  // reject partial AES blocks before hashing/decrypting

  uint8_t hmac[CIPHER_MAC_SIZE];
#ifdef USE_CC310_HW_CRYPTO
  if (!hmacHardware(shared_secret, hmac, src + CIPHER_MAC_SIZE, enc_len))
#endif
  {
    hmacSoftware(shared_secret, hmac, src + CIPHER_MAC_SIZE, enc_len);
  }
  if (memcmp(hmac, src, CIPHER_MAC_SIZE) == 0) {
    return decrypt(shared_secret, dest, src + CIPHER_MAC_SIZE, enc_len);
  }
  return 0; // invalid HMAC
}

static const char hex_chars[] = "0123456789ABCDEF";

void Utils::toHex(char* dest, const uint8_t* src, size_t len) {
  while (len > 0) {
    uint8_t b = *src++;
    *dest++ = hex_chars[b >> 4];
    *dest++ = hex_chars[b & 0x0F];
    len--;
  }
  *dest = 0;
}

void Utils::printHex(Stream& s, const uint8_t* src, size_t len) {
  while (len > 0) {
    uint8_t b = *src++;
    s.print(hex_chars[b >> 4]);
    s.print(hex_chars[b & 0x0F]);
    len--;
  }
}

static uint8_t hexVal(char c) {
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= '0' && c <= '9') return c - '0';
  return 0;
}

bool Utils::isHexChar(char c) {
  return c == '0' || hexVal(c) > 0;
}

bool Utils::fromHex(uint8_t* dest, int dest_size, const char *src_hex) {
  if (dest == NULL || src_hex == NULL || dest_size < 0) return false;
  int len = strlen(src_hex);
  if (len != dest_size*2) return false;  // incorrect length

  uint8_t* dp = dest;
  while (dp - dest < dest_size) {
    char ch = *src_hex++;
    char cl = *src_hex++;
    if (!isHexChar(ch) || !isHexChar(cl)) return false;
    *dp++ = (hexVal(ch) << 4) | hexVal(cl);
  }
  return true;
}

int Utils::parseTextParts(char* text, const char* parts[], int max_num, char separator) {
  int num = 0;
  char* sp = text;
  while (*sp && num < max_num) {
    parts[num++] = sp;
    while (*sp && *sp != separator) sp++;
    if (*sp) {
       *sp++ = 0;  // replace the seperator with a null, and skip past it
    }
  }
  // if we hit the maximum parts, make sure LAST entry does NOT have separator 
  while (*sp && *sp != separator) sp++;
  if (*sp) {
    *sp = 0;  // replace the separator with null
  }
  return num;
}

}
