#include <helpers/ota/OtaVerify.h>
#include <SHA256.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>
#include "test_ota/mota_vectors.h"

using namespace mesh::ota;
int main() {
  std::vector<uint8_t> bytes(MOTA_VEC, MOTA_VEC + MOTA_VEC_LEN);
  OtaStoreRam<8192> store;
  assert(store.begin(bytes.size()) && store.write(0, bytes.data(), bytes.size()));
  SignerAllowlist allow;
  VerifyResult verified=ota_verify(store,allow);
  assert(verified.integrity_ok() && verified.container_hash_ok);
  memset(bytes.data()+201,0,4);
  SHA256 sha;sha.update(bytes.data(),bytes.size());uint8_t expected[32];sha.finalize(expected,sizeof(expected));
  assert(!memcmp(expected,verified.container_hash,32));
  const uint8_t approved[4]={'A','P','R','V'};
  assert(store.write(201,approved,4));
  VerifyResult after=ota_verify(store,allow);
  assert(after.integrity_ok() && after.container_hash_ok && !memcmp(expected,after.container_hash,32));
  uint8_t bad=0;assert(store.read(300,&bad,1));bad^=1;assert(store.write(300,&bad,1));
  VerifyResult corrupt=ota_verify(store,allow);
  assert(!corrupt.integrity_ok() && !corrupt.container_hash_ok);
  puts("PASS real store verification hashes normalized full package");
}
