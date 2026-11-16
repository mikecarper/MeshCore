#pragma once
#include <cstddef>
#include <cstring>
inline int mbedtls_base64_decode(unsigned char* out, size_t, size_t* n,
                                 const unsigned char* input, size_t len) {
  const char valid[] = "dGhlIHNhbXBsZSBub25jZQ==";
  if (len != sizeof(valid) - 1 || memcmp(input, valid, len)) return -1;
  memcpy(out, "the sample nonce", 16); *n = 16; return 0;
}
inline int mbedtls_base64_encode(unsigned char* out, size_t, size_t* n,
                                 const unsigned char*, size_t) {
  const char value[] = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=";
  memcpy(out, value, sizeof(value)); *n = sizeof(value) - 1; return 0;
}
