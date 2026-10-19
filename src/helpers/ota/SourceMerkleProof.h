#pragma once

#include "MerkleTree.h"
#include "OtaSource.h"
#include <string.h>

namespace mesh { namespace ota {

// A power-of-two group preserves the original tree after five reductions,
// including promotion of the final partial group. Retain group roots instead
// of the complete leaf table; a block's wire proof is its local proof followed
// by the group-root proof. No source/transport pointer owns storage here.
template <uint32_t MaximumLeaves, uint16_t FragmentCapacity>
class SourceMerkleProof {
public:
  enum class Step : uint8_t { Pending, Ready, Failed };
  static constexpr uint32_t GroupLeaves = 32;
  static constexpr uint32_t GroupBytes = GroupLeaves * 4;
  static constexpr uint32_t MaximumGroups = (MaximumLeaves + GroupLeaves - 1) / GroupLeaves;
  static_assert(MaximumLeaves > 0 && MaximumLeaves <= UINT32_MAX / 4,
                "invalid source proof capacity");

  void reset() {
    _source = nullptr;
    _roots_ready = false;
    _fragment_source = nullptr;
    _fragment_used = 0;
  }

  bool bind(MotaSource* source, uint8_t index, uint32_t offset,
            uint32_t count, const uint8_t root[4]) {
    if (!source || !root || !count || count > MaximumLeaves ||
        uint64_t(offset) + uint64_t(count) * 4 > UINT32_MAX) return false;
    if (_source == source && _index == index && _offset == offset &&
        _count == count && !memcmp(_root, root, 4)) return true;
    _source = source; _index = index; _offset = offset; _count = count;
    memcpy(_root, root, 4);
    _groups = (count + GroupLeaves - 1) / GroupLeaves;
    _warm_group = 0; _group_used = 0; _selected_group = UINT32_MAX;
    _roots_ready = false;
    return true;
  }

  // At most one source read, at most 32 leaves reduced after that read. The
  // final root check is bounded by MaximumGroups (128 on ESP32).
  Step prepare() {
    if (!_source) return Step::Failed;
    if (_roots_ready) return Step::Ready;
    const uint32_t bytes = groupBytes(_warm_group);
    if (!readPart(_source, _index, _offset + _warm_group * GroupBytes,
                  _group, bytes, _group_used)) return failProof();
    if (_group_used != bytes) return Step::Pending;
    if (!groupRoot(bytes, _roots + _warm_group * 4)) return failProof();
    ++_warm_group;
    _group_used = 0;
    if (_warm_group == _groups) {
      _accumulator.reset();
      for (uint32_t i = 0; i < _groups; ++i)
        if (!_accumulator.add(_roots + i * 4)) return failProof();
      uint8_t root[4];
      if (!_accumulator.finish(root) || memcmp(root, _root, 4)) return failProof();
      _roots_ready = true;
    }
    // Even the final warm read yields before reading the requested group.
    return Step::Pending;
  }

  Step proof(uint32_t block, uint8_t output[128], uint8_t& length) {
    length = 0;
    if (!output || !_source || block >= _count) return Step::Failed;
    const Step prepared = prepare();
    if (prepared != Step::Ready) return prepared;
    const uint32_t group = block / GroupLeaves;
    if (_selected_group != group) {
      _selected_group = group;
      _group_used = 0;
    }
    const uint32_t bytes = groupBytes(group);
    if (_group_used != bytes) {
      if (!readPart(_source, _index, _offset + group * GroupBytes,
                    _group, bytes, _group_used)) return failProof();
      if (_group_used != bytes) return Step::Pending;
      uint8_t root[4];
      if (!groupRoot(bytes, root) || memcmp(root, _roots + group * 4, 4))
        return failProof();
    }
    length = merkle_gen_proof(_group, bytes / 4, block % GroupLeaves, nullptr, output);
    length += merkle_gen_proof(_roots, _groups, group, nullptr, output + length * 4);
    return Step::Ready;
  }

  // Getter fragments have an independent binding and buffer. A request for
  // another MID cannot reset a partially warmed proof cache, and a rejected
  // radio emit retains the exact bytes without another source transaction.
  Step fragment(MotaSource* source, uint8_t index, uint32_t offset,
                uint16_t bytes, const uint8_t*& output) {
    output = nullptr;
    if (!source || !bytes || bytes > FragmentCapacity ||
        uint64_t(offset) + bytes > UINT32_MAX) return Step::Failed;
    if (_fragment_source != source || _fragment_index != index ||
        _fragment_offset != offset || _fragment_bytes != bytes) {
      _fragment_source = source; _fragment_index = index;
      _fragment_offset = offset; _fragment_bytes = bytes; _fragment_used = 0;
    }
    if (_fragment_used != bytes &&
        !readPart(source, index, offset, _fragment, bytes, _fragment_used)) {
      _fragment_source = nullptr;
      return Step::Failed;
    }
    if (_fragment_used != bytes) return Step::Pending;
    output = _fragment;
    return Step::Ready;
  }
  void releaseFragment() { _fragment_source = nullptr; _fragment_used = 0; }

private:
  static bool readPart(MotaSource* source, uint8_t index, uint32_t offset,
                       uint8_t* output, uint32_t bytes, uint32_t& used) {
    uint32_t chunk = source->read_quantum();
    if (!chunk) return false;
    if (chunk > GroupBytes) chunk = GroupBytes;
    if (chunk > bytes - used) chunk = bytes - used;
    if (!source->read_metadata(index, offset + used, output + used, chunk)) return false;
    used += chunk;
    return true;
  }
  uint32_t groupBytes(uint32_t group) const {
    const uint32_t remaining = _count - group * GroupLeaves;
    return (remaining < GroupLeaves ? remaining : GroupLeaves) * 4;
  }
  bool groupRoot(uint32_t bytes, uint8_t root[4]) {
    _accumulator.reset();
    for (uint32_t i = 0; i < bytes; i += 4)
      if (!_accumulator.add(_group + i)) return false;
    return _accumulator.finish(root);
  }
  Step failProof() { _source = nullptr; _roots_ready = false; return Step::Failed; }

  uint8_t _roots[MaximumGroups * 4];
  uint8_t _group[GroupBytes];
  uint8_t _fragment[FragmentCapacity];
  MerkleAccumulator _accumulator;
  MotaSource* _source = nullptr;
  MotaSource* _fragment_source = nullptr;
  uint32_t _offset = 0, _count = 0, _groups = 0;
  uint32_t _warm_group = 0, _group_used = 0, _selected_group = UINT32_MAX;
  uint32_t _fragment_offset = 0, _fragment_used = 0;
  uint16_t _fragment_bytes = 0;
  uint8_t _root[4] = {};
  uint8_t _index = 0, _fragment_index = 0;
  bool _roots_ready = false;
};

}} // namespace mesh::ota
