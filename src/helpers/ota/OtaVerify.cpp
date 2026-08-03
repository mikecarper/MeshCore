#include "OtaVerify.h"
#include "MerkleTree.h"
#include "Multihash.h"
#include "Identity.h"
#include "OtaByteIO.h"
#include <SHA256.h>
#include <stdlib.h>
#include <string.h>

namespace mesh {
namespace ota {

VerifyResult ota_verify(const uint8_t* buf, uint32_t len, const SignerAllowlist& allow) {
  VerifyResult r;
  MotaManifest m;
  if (!mota_parse(buf, len, m)) return r;
  r.parsed = true;
  r.root_ok = mota_check_root(m);
  r.payload_ok = mota_check_payload(m);
  r.image_ok = m.is_full() ? mota_check_image_hash_full(m)
                           : true;   // delta image_hash needs the base; verified at apply time
  r.is_signed = m.is_signed();
  if (r.is_signed) {
    mesh::Identity signer(m.signer_pubkey);
    r.sig_ok = signer.verify(m.signature, m.manifest_start, (int)m.signed_len);
    r.trusted = r.sig_ok && allow.contains(m.signer_pubkey);
  }
  return r;
}

VerifyResult ota_verify(const OtaStore& store, const SignerAllowlist& allow) {
  VerifyResult r;
  const uint32_t total = store.staged_size();
  uint8_t hdr[8], manifest[MOTA_MFL], trailer[5];
  if (total < 8 + MOTA_MFL + 5 ||
      !store.read(0, hdr, sizeof(hdr)) ||
      memcmp(hdr, MOTA_MAGIC, 4) != 0 || rd_u32le(hdr + 4) != total ||
      !store.read(8, manifest, sizeof(manifest)) ||
      !store.read(total - 5, trailer, sizeof(trailer)) ||
      memcmp(trailer, MOTA_TRAILER, sizeof(trailer)) != 0) return r;

  MotaManifest m;
  if (!mota_parse_manifest(manifest, sizeof(manifest), m)) return r;
  const uint32_t leaves_off = 8 + MOTA_MFL;
  const uint64_t payload_off64 = (uint64_t)leaves_off + (uint64_t)m.block_count * 4;
  if (m.block_size() > OTA_DEFAULT_BLOCK_SIZE ||
      payload_off64 > UINT32_MAX ||
      payload_off64 + m.payload_size + 5 != total) return r;
  const uint32_t payload_off = (uint32_t)payload_off64;

  const size_t leaves_len = (size_t)m.block_count * 4;
  uint8_t* leaves = static_cast<uint8_t*>(malloc(leaves_len));
  uint8_t* block = static_cast<uint8_t*>(malloc(m.block_size()));
  if (!leaves || !block || !store.read(leaves_off, leaves, leaves_len)) {
    free(leaves); free(block); return r;
  }

  r.parsed = true;
  uint8_t root[4];
  merkle_root(root, leaves, m.block_count);
  r.root_ok = memcmp(root, m.merkle_root, sizeof(root)) == 0;

  SHA256 image_sha;
  r.payload_ok = true;
  uint32_t off = 0;
  for (uint32_t i = 0; i < m.block_count; i++) {
    uint32_t n = m.payload_size - off;
    if (n > m.block_size()) n = m.block_size();
    if (!store.read(payload_off + off, block, n)) {
      r.payload_ok = false;
      break;
    }
    uint8_t leaf[4];
    merkle_leaf(leaf, block, n);
    if (memcmp(leaf, leaves + (size_t)i * 4, 4) != 0) {
      r.payload_ok = false;
      break;
    }
    if (m.is_full()) image_sha.update(block, n);
    off += n;
  }
  if (off != m.payload_size) r.payload_ok = false;
  if (m.is_full() && r.payload_ok) {
    uint8_t image_hash[32];
    image_sha.finalize(image_hash, sizeof(image_hash));
    r.image_ok = memcmp(image_hash, m.image_hash, sizeof(image_hash)) == 0;
  } else {
    r.image_ok = !m.is_full();
  }

  r.is_signed = m.is_signed();
  if (r.is_signed) {
    mesh::Identity signer(m.signer_pubkey);
    r.sig_ok = signer.verify(m.signature, m.manifest_start, (int)m.signed_len);
    r.trusted = r.sig_ok && allow.contains(m.signer_pubkey);
  }
  free(leaves);
  free(block);
  return r;
}

} // namespace ota
} // namespace mesh
