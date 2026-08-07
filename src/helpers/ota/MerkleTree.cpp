#include "MerkleTree.h"
#include "Multihash.h"
#include <string.h>

namespace mesh {
namespace ota {

void merkle_leaf(uint8_t out[4], const uint8_t* block, uint32_t block_len) {
  mh4(out, block, block_len);
}

void merkle_combine(uint8_t out[4], const uint8_t* left, const uint8_t* right) {
  sha256_trunc2(out, 4, left, 4, right, 4);
}

void MerkleAccumulator::reset() {
  memset(_peaks, 0, sizeof(_peaks));
  _valid_mask = 0;
  _count = 0;
}

bool MerkleAccumulator::add(const uint8_t leaf[4]) {
  if (leaf == nullptr || _count == UINT32_MAX) return false;
  uint8_t current[4];
  memcpy(current, leaf, 4);
  uint8_t level = 0;
  while (level < 32 && (_valid_mask & ((uint32_t)1U << level)) != 0) {
    merkle_combine(current, _peaks[level], current);
    _valid_mask &= ~((uint32_t)1U << level);
    level++;
  }
  if (level >= 32) return false;
  memcpy(_peaks[level], current, 4);
  _valid_mask |= (uint32_t)1U << level;
  _count++;
  return true;
}

bool MerkleAccumulator::finish(uint8_t out[4]) const {
  if (out == nullptr || _count == 0 || _valid_mask == 0) return false;
  uint8_t level = 0;
  while (level < 32
         && (_valid_mask & ((uint32_t)1U << level)) == 0) {
    level++;
  }
  if (level >= 32) return false;
  uint8_t accumulated[4];
  memcpy(accumulated, _peaks[level], 4);
  for (uint8_t next = level + 1; next < 32; ++next) {
    if ((_valid_mask & ((uint32_t)1U << next)) != 0) {
      merkle_combine(accumulated, _peaks[next], accumulated);
    }
  }
  memcpy(out, accumulated, 4);
  return true;
}

// Root via binary-counter / Merkle-Mountain-Range with right-to-left bagging.
// Equivalent to the level-by-level "pair adjacent, promote lone last (left||right)" reduction
// (verified against the reference implementation across many counts in the native tests).
void merkle_root(uint8_t out[4], const uint8_t* leaves, uint32_t count) {
  if (count == 0 || leaves == nullptr) { memset(out, 0, 4); return; }
  MerkleAccumulator accumulator;
  for (uint32_t i = 0; i < count; ++i) {
    if (!accumulator.add(leaves + (size_t)i * 4)) {
      memset(out, 0, 4);
      return;
    }
  }
  if (!accumulator.finish(out)) memset(out, 0, 4);
}

bool merkle_verify(const uint8_t* block, uint32_t block_len, uint32_t index,
                   const uint8_t* siblings, uint8_t n_siblings,
                   const uint8_t root[4], uint32_t count) {
  uint8_t leaf[4];
  merkle_leaf(leaf, block, block_len);
  return merkle_verify_from_leaf(leaf, index, siblings, n_siblings, root, count);
}

bool merkle_verify_from_leaf(const uint8_t leaf[4], uint32_t index,
                             const uint8_t* siblings, uint8_t n_siblings,
                             const uint8_t root[4], uint32_t count) {
  if (count == 0 || index >= count) return false;
  uint8_t h[4];
  memcpy(h, leaf, 4);

  uint32_t idx = index;
  uint32_t n = count;
  uint8_t p = 0;
  while (n > 1) {
    bool is_last_odd = (n & 1u) && (idx == n - 1);
    if (!is_last_odd) {
      if (p >= n_siblings) return false;
      const uint8_t* sib = siblings + (size_t)p * 4;
      p++;
      if (idx & 1u) merkle_combine(h, sib, h);   // odd index -> sibling on the left
      else          merkle_combine(h, h, sib);   // even index -> sibling on the right
    }
    idx >>= 1;
    n = (n + 1) >> 1;
  }
  return (p == n_siblings) && (memcmp(h, root, 4) == 0);
}

uint8_t merkle_gen_proof(const uint8_t* leaves, uint32_t count, uint32_t index,
                         uint8_t* scratch, uint8_t* out_siblings) {
  if (count == 0 || index >= count) return 0;
  memcpy(scratch, leaves, (size_t)count * 4);
  uint32_t n = count, idx = index;
  uint8_t p = 0;
  while (n > 1) {
    bool is_last_odd = (n & 1u) && (idx == n - 1);
    if (!is_last_odd) {
      uint32_t s = (idx & 1u) ? idx - 1 : idx + 1;
      memcpy(out_siblings + (size_t)p * 4, scratch + (size_t)s * 4, 4);
      p++;
    }
    // reduce one level in place (parent m written from children 2m,2m+1; m <= i so it's safe)
    uint32_t m = 0;
    for (uint32_t i = 0; i < n; i += 2) {
      if (i + 1 < n) merkle_combine(scratch + (size_t)m * 4, scratch + (size_t)i * 4, scratch + (size_t)(i + 1) * 4);
      else           memmove(scratch + (size_t)m * 4, scratch + (size_t)i * 4, 4);
      m++;
    }
    idx >>= 1;
    n = (n + 1) >> 1;
  }
  return p;
}

} // namespace ota
} // namespace mesh
