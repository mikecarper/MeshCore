#include <helpers/ota/OtaManager.h>
#include <helpers/ota/Multihash.h>
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
using namespace mesh::ota;

// Only production malloc/free calls are wrapped. C++ fixture containers use
// operator new, so the ledger measures the manager's actual cold allocations.
static std::array<void*, 32> live{};
static std::array<size_t, 32> sizes{};
static size_t allocated = 0, peak = 0;
static bool fail_next = false;
extern "C" void* __real_malloc(size_t);
extern "C" void __real_free(void*);
extern "C" void* __wrap_malloc(size_t bytes) {
  if (fail_next) { fail_next = false; return nullptr; }
  void* result = __real_malloc(bytes);
  if (result) {
    auto empty = std::find(live.begin(), live.end(), nullptr);
    assert(empty != live.end());
    const size_t slot = empty - live.begin();
    live[slot] = result; sizes[slot] = bytes;
    allocated += bytes; peak = std::max(peak, allocated);
  }
  return result;
}
extern "C" void __wrap_free(void* pointer) {
  if (pointer) {
    auto found = std::find(live.begin(), live.end(), pointer);
    if (found != live.end()) {
      const size_t slot = found - live.begin();
      allocated -= sizes[slot]; live[slot] = nullptr;
    }
  }
  __real_free(pointer);
}

static std::vector<uint8_t> reference_proof(std::vector<uint8_t> level, uint32_t index) {
  std::vector<uint8_t> proof;
  while (level.size() > 4) {
    const uint32_t count = level.size() / 4;
    if (!((count & 1u) && index == count - 1)) {
      const uint32_t sibling = index ^ 1u;
      proof.insert(proof.end(), level.begin() + sibling * 4, level.begin() + sibling * 4 + 4);
    }
    std::vector<uint8_t> next(((count + 1) / 2) * 4);
    for (uint32_t i = 0; i < count; i += 2) {
      if (i + 1 < count) merkle_combine(next.data() + (i / 2) * 4,
                                      level.data() + i * 4, level.data() + (i + 1) * 4);
      else memcpy(next.data() + (i / 2) * 4, level.data() + i * 4, 4);
    }
    level = std::move(next); index >>= 1;
  }
  return proof;
}
static void proofs() {
  std::vector<uint32_t> counts;
  for (uint32_t n = 1; n <= 65; ++n) counts.push_back(n);
  for (uint32_t n : {127,128,129,255,256,257,511,512,513,1023,1024,1025,2047,2048,4095,4096})
    counts.push_back(n);
  for (uint32_t count : counts) {
    std::vector<uint8_t> leaves(count * 4);
    for (uint32_t i = 0; i < count; ++i) {
      uint8_t value[4]; wr_u32le(value, i * 8191u + count);
      merkle_leaf(leaves.data() + i * 4, value, 4);
    }
    const auto original = leaves;
    uint8_t root[4]; merkle_root(root, leaves.data(), count);
    const uint32_t step = count <= 65 ? 1 : std::max(1u, count / 7);
    for (uint32_t i = 0; i < count; i += step) {
      uint8_t output[128], tiny_scratch = 0x5A;
      const uint8_t length = merkle_gen_proof(leaves.data(), count, i, nullptr, output);
      const auto expected = reference_proof(leaves, i);
      assert(expected.size() == length * 4u);
      assert(expected.empty() || !memcmp(output, expected.data(), expected.size()));
      assert(merkle_verify_from_leaf(leaves.data() + i * 4, i, output, length, root, count));
      assert(merkle_gen_proof(leaves.data(), count, i, &tiny_scratch, output) == length);
      assert(tiny_scratch == 0x5A && leaves == original);
    }
  }
  uint8_t output[128]{};
  assert(!merkle_gen_proof(nullptr, 1, 0, nullptr, output));
  assert(!merkle_gen_proof(output, 1, 1, nullptr, output));
  assert(!merkle_gen_proof(output, 1, 0, nullptr, nullptr));
}

class Source : public MotaSource {
public:
  enum class HostEncoding { Off, Good, Refused, PartialFailure, Empty, NonShrinking, Oversized };
  std::vector<uint8_t> mota;
  MotaManifest manifest;
  bool fail_read = false;
  bool fail_payload = false;
  HostEncoding host_encoding = HostEncoding::Off;
  uint16_t host_encoded_size = 8;
  unsigned host_calls = 0, raw_reads = 0;
  Source(uint32_t blocks, uint8_t value, uint16_t final_block_size = OTA_MAX_BLOCK) {
    assert(blocks && final_block_size && final_block_size <= OTA_MAX_BLOCK);
    const uint32_t payload_size = (blocks - 1) * OTA_MAX_BLOCK + final_block_size;
    const uint32_t total = 8 + MOTA_MFL + blocks * 4 + payload_size + 5;
    mota.resize(total);
    memcpy(mota.data(), MOTA_MAGIC, 4); wr_u32le(mota.data() + 4, total);
    uint8_t* mf = mota.data() + 8;
    mf[0] = MOTA_FORMAT_VER; mf[1] = MFLAG_FULL; mf[2] = HASH_ALGO_SHA256;
    wr_u32le(mf + 3, 123); wr_u32le(mf + 7, 1);
    wr_u32le(mf + 11, payload_size); wr_u32le(mf + 15, payload_size);
    mf[19] = 11; mf[56] = CODEC_FULL;
    uint8_t* leaves = mf + MOTA_MFL;
    uint8_t* payload = leaves + blocks * 4;
    for (uint32_t i = 0; i < blocks; ++i) {
      const uint16_t length = i == blocks - 1 ? final_block_size : OTA_MAX_BLOCK;
      memset(payload + i * OTA_MAX_BLOCK, uint8_t(value + i), length);
      merkle_leaf(leaves + i * 4, payload + i * OTA_MAX_BLOCK, length);
    }
    merkle_root(mf + 20, leaves, blocks);
    memcpy(mota.data() + total - 5, MOTA_TRAILER, 5);
    assert(mota_parse(mota.data(), mota.size(), manifest));
  }
  uint8_t count() override { return 1; }
  bool describe(uint8_t index, MotaDesc& d) override {
    if (index) return false;
    memcpy(d.mid, manifest.merkle_root, 4); d.target_id = 123; d.fw_version = 1;
    d.codec_id = CODEC_FULL; d.flags = MFLAG_FULL; d.block_size_log2 = 11;
    d.total_size = mota.size(); d.leaves_off = 8 + MOTA_MFL;
    d.block_count = manifest.block_count;
    d.payload_off = manifest.payload - mota.data(); d.payload_size = manifest.payload_size;
    d.source_caps = host_encoding == HostEncoding::Off ? 0 : MOTA_SOURCE_CAP_DEFLATE_BLOCK;
    return true;
  }
  bool read(uint8_t index, uint32_t off, uint8_t* out, uint32_t len) override {
    if (index || (uint64_t)off + len > mota.size()) return false;
    const uint32_t payload_off = manifest.payload - mota.data();
    if (off >= payload_off && off < payload_off + manifest.payload_size) {
      ++raw_reads;
      if (fail_payload) { if (len) out[0] = 0xEE; return false; }
    }
    if (fail_read) { if (len) out[0] = 0xEE; return false; }
    memcpy(out, mota.data() + off, len); return true;
  }
  bool read_deflated_block(uint8_t index, uint16_t block, uint8_t* out,
                           uint16_t cap, uint16_t* len) override {
    ++host_calls;
    assert(index == 0 && block < manifest.block_count && len);
    assert(cap == std::min<uint32_t>(OTA_MAX_BLOCK, manifest.payload_size - block * OTA_MAX_BLOCK));
    // Deliberately opaque fixture representations: manager framing, hashes,
    // proofs and cache ownership are real; this is not a DEFLATE codec test.
    if (host_encoding == HostEncoding::Good) {
      assert(host_encoded_size > 0 && host_encoded_size < cap);
      memset(out, manifest.payload[block * OTA_MAX_BLOCK], host_encoded_size);
      *len = host_encoded_size;
      return true;
    }
    if (host_encoding == HostEncoding::PartialFailure) {
      memset(out, 0xEE, 17); *len = 17; return false;
    }
    if (host_encoding == HostEncoding::Empty) { out[0] = 0xEE; *len = 0; return true; }
    if (host_encoding == HostEncoding::NonShrinking) {
      memset(out, 0xEE, cap); *len = cap; return true;
    }
    if (host_encoding == HostEncoding::Oversized) {
      // Report a bad length without violating the callback's output capacity.
      out[0] = 0xEE; *len = cap + 1; return true;
    }
    *len = 0;
    return false;
  }
};
struct Sent {
  bool accept_data = true, accept_proof = true, accept_manifest = true;
  std::vector<std::vector<uint8_t>> packets;
};
static bool send(void* opaque, const uint8_t* data, uint16_t len, bool) {
  auto& sent = *static_cast<Sent*>(opaque);
  if ((data[0] == OTA_DATA && !sent.accept_data) ||
      (data[0] == OTA_PROOF && !sent.accept_proof) ||
      (data[0] == OTA_MANIFEST && !sent.accept_manifest)) return false;
  sent.packets.emplace_back(data, data + len); return true;
}
static bool encode(void*, const uint8_t* src, uint16_t, uint8_t* dst,
                   uint16_t cap, uint16_t* len) {
  assert(cap == OTA_MAX_BLOCK && src != dst); memset(dst, src[0], 8); *len = 8; return true;
}
static bool encode_long(void*, const uint8_t* src, uint16_t, uint8_t* dst,
                        uint16_t cap, uint16_t* len) {
  assert(cap == OTA_MAX_BLOCK); memset(dst, src[0], 400); *len = 400; return true;
}
static bool decode(void*, const uint8_t* src, uint16_t len, uint8_t* dst,
                   uint16_t cap, uint16_t* produced) {
  if (len != 8) return false;
  memset(dst, src[0], cap); *produced = cap; return true;
}
static bool request(OtaManager& manager, const Source& source, uint16_t block = 0, bool compressed = true) {
  ReqMsg req{}; memcpy(req.manifest_id, source.manifest.merkle_root, 4);
  req.block_idx = block; req.want_mask = compressed ? ota_req_make_v2(0xFFF, true, true) : 0xFFFF;
  uint8_t wire[MAX_PACKET_PAYLOAD];
  return manager.on_message(wire, encode_req(wire, sizeof(wire), req));
}
static bool request_v2(OtaManager& manager, const Source& source, uint16_t block, bool allow_deflate) {
  ReqMsg req{}; memcpy(req.manifest_id, source.manifest.merkle_root, 4);
  req.block_idx = block; req.want_mask = ota_req_make_v2(0xFFF, allow_deflate, true);
  uint8_t wire[MAX_PACKET_PAYLOAD];
  return manager.on_message(wire, encode_req(wire, sizeof(wire), req));
}
static bool manifest_request(OtaManager& manager, const Source& source) {
  GetManifestMsg req{}; memcpy(req.manifest_id, source.manifest.merkle_root, 4); req.want_mask = 0xFFFF;
  uint8_t wire[MAX_PACKET_PAYLOAD];
  return manager.on_message(wire, encode_get_manifest(wire, sizeof(wire), req));
}
static void drain(OtaManager& manager, uint32_t start = 100000) {
  for (unsigned turn = 0; turn < 100 && (manager.pendingServeJobs() || manager.pendingManifestJobs()); ++turn) {
    manager.set_clock(start + turn * 10000u); manager.serviceEgress();
  }
  assert(!manager.pendingServeJobs() && !manager.pendingManifestJobs());
}
static void verify(const Sent& sent, const Source& source, bool compressed) {
  std::vector<uint8_t> bytes;
  bool proof_seen = false;
  for (const auto& wire : sent.packets) {
    DataMsg data{}; ProofMsg proof{};
    if (decode_data(wire.data(), wire.size(), data) && !memcmp(data.manifest_id, source.manifest.merkle_root, 4)) {
      if (compressed) {
        uint8_t fragment; uint16_t encoded; bool deflated;
        assert(ota_data_v2_unpack_extended(data.frag_off, OTA_MAX_BLOCK, fragment, encoded, deflated));
        assert(deflated && fragment == 0 && encoded == 8 && data.data_len == 12);
        bytes.assign(OTA_MAX_BLOCK, data.data[4]);
      } else bytes.insert(bytes.end(), data.data, data.data + data.data_len);
    } else if (decode_proof(wire.data(), wire.size(), proof) &&
               !memcmp(proof.manifest_id, source.manifest.merkle_root, 4)) {
      assert(merkle_verify(source.manifest.payload, OTA_MAX_BLOCK, 0,
                           proof.proof, proof.n_proof, source.manifest.merkle_root, source.manifest.block_count));
      proof_seen = true;
    }
  }
  assert(proof_seen && bytes.size() == OTA_MAX_BLOCK);
  assert(!memcmp(bytes.data(), source.manifest.payload, OTA_MAX_BLOCK));
}

static void verify_v2(const Sent& sent, const Source& source, uint16_t block,
                      bool compressed, uint16_t encoded_size = 8) {
  std::vector<uint8_t> bytes;
  uint8_t identity[4]{};
  bool proof_seen = false;
  const uint16_t raw_size = std::min<uint32_t>(OTA_MAX_BLOCK, source.manifest.payload_size - block * OTA_MAX_BLOCK);
  const uint16_t expected_size = compressed ? encoded_size : raw_size;
  for (const auto& packet : sent.packets) {
    DataMsg data{}; ProofMsg proof{};
    if (decode_data(packet.data(), packet.size(), data)
        && !memcmp(data.manifest_id, source.manifest.merkle_root, 4)) {
      assert(data.block_idx == block);
      uint8_t fragment; uint16_t length; bool deflated;
      assert(ota_data_v2_unpack_extended(data.frag_off, raw_size, fragment, length, deflated));
      assert(deflated == compressed && length == expected_size);
      assert(fragment * OTA_FRAG_DATA_V2 == bytes.size());
      if (bytes.empty()) memcpy(identity, data.data, 4);
      else assert(!memcmp(identity, data.data, 4));
      bytes.insert(bytes.end(), data.data + 4, data.data + data.data_len);
    } else if (decode_proof(packet.data(), packet.size(), proof)
               && !memcmp(proof.manifest_id, source.manifest.merkle_root, 4)) {
      assert(proof.block_idx == block);
      assert(merkle_verify(source.manifest.payload + block * OTA_MAX_BLOCK, raw_size, block,
                           proof.proof, proof.n_proof, source.manifest.merkle_root,
                           source.manifest.block_count));
      proof_seen = true;
    }
  }
  assert(proof_seen && bytes.size() == expected_size);
  if (compressed) assert(bytes == std::vector<uint8_t>(expected_size, source.manifest.payload[block * OTA_MAX_BLOCK]));
  else assert(!memcmp(bytes.data(), source.manifest.payload + block * OTA_MAX_BLOCK, raw_size));
  uint8_t expected[4]; mh4(expected, bytes.data(), bytes.size());
  assert(!memcmp(identity, expected, 4));
}

static void host_allocation_and_fallbacks() {
  for (bool fail_raw : {false, true}) {
    Source source(9, 0x42); source.host_encoding = Source::HostEncoding::Good;
    source.host_encoded_size = 400; source.fail_payload = fail_raw;
    Sent sent; OtaManager manager;
    manager.begin(123, send, &sent); manager.set_transport_deflate_encoder(encode);
    assert(manager.add_source(&source) && request(manager, source));
    assert(allocated == 36);
    fail_next = true;
    manager.serviceEgress();
    assert(fail_next && allocated == 36 && source.host_calls == 1 && source.raw_reads == 0);
    drain(manager); verify_v2(sent, source, 0, true, 400);
    assert(fail_next && allocated == 36 && source.host_calls == 1);
    fail_next = false; manager.clear_sources(); assert(allocated == 0);
  }
  for (auto mode : {Source::HostEncoding::Refused, Source::HostEncoding::PartialFailure,
                    Source::HostEncoding::Empty, Source::HostEncoding::NonShrinking,
                    Source::HostEncoding::Oversized}) {
    Source source(3, 0x51); source.host_encoding = mode;
    Sent sent; OtaManager manager;
    manager.begin(123, send, &sent); manager.set_transport_deflate_encoder(encode);
    assert(manager.add_source(&source) && request(manager, source));
    fail_next = true; drain(manager);
    assert(fail_next && allocated == 12 && source.host_calls == 1 && source.raw_reads == 1);
    verify_v2(sent, source, 0, false);
    fail_next = false; manager.clear_sources(); assert(allocated == 0);
  }
  Source source(3, 0x61); source.host_encoding = Source::HostEncoding::PartialFailure;
  source.fail_payload = true;
  Sent sent; OtaManager manager; manager.begin(123, send, &sent);
  assert(manager.add_source(&source) && request(manager, source));
  fail_next = true; manager.serviceEgress();
  assert(fail_next && sent.packets.empty() && !manager.pendingServeJobs());
  assert(source.host_calls == 1 && source.raw_reads == 1 && allocated == 12);
  fail_next = false; source.fail_payload = false; source.host_encoding = Source::HostEncoding::Good;
  assert(request(manager, source)); drain(manager); verify_v2(sent, source, 0, true);
  manager.clear_sources(); assert(allocated == 0);
  puts("PASS: host compression needs leaves only; allocation denial and invalid-output fallback");
}

static void host_tail_and_maximum_capacity() {
  for (bool maximum : {false, true}) {
    const uint32_t blocks = maximum ? OTA_PROOFGEN_SCRATCH / 4 : 3;
    Source source(blocks, 0x65, maximum ? OTA_MAX_BLOCK : 37);
    source.host_encoding = Source::HostEncoding::Good;
    source.fail_payload = true;
    const uint16_t block = blocks - 1;
    Sent sent; OtaManager manager; manager.begin(123, send, &sent);
    assert(manager.add_source(&source) && request(manager, source, block));
    assert(allocated == blocks * 4);
    if (maximum) assert(allocated == OTA_PROOFGEN_SCRATCH);
    fail_next = true; drain(manager); verify_v2(sent, source, block, true);
    assert(fail_next && allocated == blocks * 4 && source.host_calls == 1 && source.raw_reads == 0);
    fail_next = false; manager.clear_sources(); assert(allocated == 0);
  }
  puts("PASS: host short tail and maximum leaf capacity retain original source bounds");
}

static void host_backpressure_and_raw_switch() {
  Source source(5, 0x43); source.host_encoding = Source::HostEncoding::Good;
  source.host_encoded_size = 400;
  Sent sent; OtaManager manager; manager.begin(123, send, &sent);
  assert(manager.add_source(&source) && request(manager, source));
  sent.accept_data = false; fail_next = true;
  manager.serviceEgress(); manager.set_clock(100000); manager.serviceEgress();
  assert(fail_next && allocated == 20 && source.host_calls == 1 && sent.packets.empty());
  sent.accept_data = true; manager.serviceEgress(); // Admit exactly one of three fragments.
  assert(sent.packets.size() == 1 && manager.pendingServeJobs());
  source.host_encoding = Source::HostEncoding::PartialFailure; // A pinned representation must not be re-read.
  sent.accept_data = false; manager.set_clock(200000); manager.loop();
  assert(source.host_calls == 1 && allocated == 20);
  sent.accept_data = true; sent.accept_proof = false;
  for (unsigned turn = 0; turn < 10; ++turn) {
    manager.set_clock(300000 + turn * 10000); manager.serviceEgress();
  }
  assert(manager.pendingServeJobs() && fail_next && source.host_calls == 1 && allocated == 20);
  sent.accept_proof = true; drain(manager, 500000); verify_v2(sent, source, 0, true, 400);
  assert(fail_next); fail_next = false;
  sent.packets.clear();
  assert(request_v2(manager, source, 1, false)); drain(manager, 600000);
  assert(source.host_calls == 1 && source.raw_reads == 1 && allocated == 20);
  verify_v2(sent, source, 1, false);
  source.host_encoding = Source::HostEncoding::Good; source.host_encoded_size = 8;
  sent.packets.clear();
  assert(request(manager, source, 2)); drain(manager, 800000);
  assert(source.host_calls == 2 && source.raw_reads == 1 && allocated == 20);
  verify_v2(sent, source, 2, true);
  manager.clear_sources(); assert(allocated == 0);
  puts("PASS: host bytes stay pinned through DATA/PROOF backpressure and raw/compressed switches");
}

static void lifetimes() {
  Source source(9, 0x42);
  Sent sent; OtaManager manager;
  manager.begin(123, send, &sent); manager.set_transport_deflate_encoder(encode);
  assert(manager.add_source(&source) && allocated == 0);
  assert(request(manager, source) && allocated == 36);
  sent.accept_data = false;
  manager.serviceEgress(); assert(allocated == 36 + OTA_MAX_BLOCK);
  manager.set_clock(OTA_SOURCE_BUFFER_IDLE_MS + 1000); manager.loop();
  assert(allocated == 36 + OTA_MAX_BLOCK && manager.pendingServeJobs());
  sent.accept_data = true; sent.accept_proof = false;
  manager.serviceEgress(); manager.set_clock(100000); manager.serviceEgress();
  manager.loop(); assert(allocated == 36 + OTA_MAX_BLOCK && manager.pendingServeJobs());
  sent.accept_proof = true; drain(manager); verify(sent, source, true);
  manager.set_clock(1000000); manager.loop();
  assert(allocated == 0 && manager.servedCount() == 1); // descriptors survive; reload on demand
  sent.packets.clear(); assert(request(manager, source)); drain(manager); verify(sent, source, true);
  assert(manager.remove_source(&source) && allocated == 0 && manager.servedCount() == 0);
  assert(manager.add_source(&source) && request(manager, source));
  manager.clearPendingEgress(); assert(allocated == 0 && manager.servedCount() == 1);
  assert(manifest_request(manager, source)); sent.accept_manifest = false;
  manager.set_clock(2000000); manager.loop(); assert(allocated == 36);
  manager.clear_sources(); assert(allocated == 0 && !manager.pendingManifestJobs());
}
static void faults_and_capacity() {
  Source small(3, 0x31), large(OTA_PROOFGEN_SCRATCH / 4, 0x52);
  Sent sent; OtaManager manager;
  manager.begin(123, send, &sent); manager.set_transport_deflate_encoder(encode);
  assert(manager.add_source(&small));
  fail_next = true; assert(!request(manager, small) && allocated == 0 && !manager.pendingServeJobs());
  assert(request(manager, small) && allocated == 12);
  fail_next = true; manager.serviceEgress();
  assert(allocated == 12); // encoder-output OOM selects raw v2 rather than dropping the transfer
  drain(manager);
  bool raw_seen = false;
  for (const auto& wire : sent.packets) {
    DataMsg data{};
    if (decode_data(wire.data(), wire.size(), data)) {
      uint8_t f; uint16_t n; bool deflated;
      assert(ota_data_v2_unpack_extended(data.frag_off, OTA_MAX_BLOCK, f, n, deflated));
      assert(!deflated); raw_seen = true;
    }
  }
  assert(raw_seen); manager.clearPendingEgress(); assert(allocated == 0);
  assert(manager.add_source(&large));
  peak = 0; assert(manifest_request(manager, large));
  assert(allocated == OTA_PROOFGEN_SCRATCH && peak == OTA_PROOFGEN_SCRATCH);
  manager.clearPendingEgress(); assert(allocated == 0);
  ReqProofMsg req{}; memcpy(req.manifest_id, large.manifest.merkle_root, 4);
  req.block_idx = large.manifest.block_count - 1;
  uint8_t wire[MAX_PACKET_PAYLOAD];
  sent.packets.clear(); fail_next = false;
  assert(manager.on_message(wire, encode_req_proof(wire, sizeof(wire), req)));
  fail_next = true; drain(manager); assert(fail_next); fail_next = false;
  assert(allocated == OTA_PROOFGEN_SCRATCH); // maximum-capacity proof needs no output allocation
  ProofMsg maximum{}; assert(sent.packets.size() == 1);
  assert(decode_proof(sent.packets[0].data(), sent.packets[0].size(), maximum));
  assert(merkle_verify_from_leaf(large.manifest.leaves + req.block_idx * 4, req.block_idx,
                                maximum.proof, maximum.n_proof, large.manifest.merkle_root,
                                large.manifest.block_count));
  manager.clearPendingEgress(); assert(allocated == 0);
  small.fail_read = true; assert(!manifest_request(manager, small));
  manager.set_clock(3000000); manager.loop(); assert(allocated == 0);
  small.fail_read = false; assert(request(manager, small)); drain(manager);
  assert(manager.serve(small.mota.data(), small.mota.size()) && allocated == 0);
  fail_next = true; assert(request(manager, small, 0, false)); drain(manager);
  assert(fail_next && allocated == 0); fail_next = false; // raw proof serving allocates nothing
}
static void begin_receive(OtaManager& manager, const Source& incoming) {
  assert(manager.pull(incoming.manifest.merkle_root, 123) == OtaManager::PULL_STARTED);
  for (unsigned f = 0; f < 2; ++f) {
    ManifestMsg mf{}; memcpy(mf.manifest_id, incoming.manifest.merkle_root, 4);
    mf.frag_idx = f; mf.frag_total = 2; mf.bytes = incoming.manifest.manifest_start + f * OTA_MF_FRAG;
    mf.len = f == 0 ? OTA_MF_FRAG : MOTA_MFL - OTA_MF_FRAG;
    uint8_t wire[MAX_PACKET_PAYLOAD]; manager.on_message(wire, encode_manifest(wire, sizeof(wire), mf));
  }
  assert(manager.fetchState() == OtaManager::FETCHING);
}
static void concurrent_receive(bool host = false) {
  Source outbound(5, 0x40), incoming(3, 0x70);
  if (host) {
    outbound.host_encoding = Source::HostEncoding::Good;
    outbound.host_encoded_size = 400;
  }
  const size_t expected_allocation = 20 + (host ? 0 : OTA_MAX_BLOCK);
  Sent sent; OtaManager manager; OtaStoreRam<16384> store;
  manager.begin(123, send, &sent); manager.set_fetch_store(&store);
  manager.set_transport_deflate_encoder(encode); manager.set_transport_deflate_decoder(decode);
  assert(manager.add_source(&outbound) && request(manager, outbound));
  sent.accept_data = host; manager.serviceEgress();
  if (host) assert(sent.packets.size() == 1 && manager.pendingServeJobs());
  sent.accept_data = false;
  assert(allocated == expected_allocation);
  begin_receive(manager, incoming);
  for (uint16_t block = 0; block < 3; ++block) {
    uint8_t representation[12]; memset(representation + 4, uint8_t(0x70 + block), 8);
    mh4(representation, representation + 4, 8);
    DataMsg data{}; memcpy(data.manifest_id, incoming.manifest.merkle_root, 4); data.block_idx = block;
    assert(ota_data_v2_pack_extended(0, 8, data.frag_off)); data.data = representation; data.data_len = 12;
    uint8_t wire[MAX_PACKET_PAYLOAD]; assert(manager.on_message(wire, encode_data(wire, sizeof(wire), data)));
    uint8_t siblings[128]; ProofMsg proof{}; memcpy(proof.manifest_id, incoming.manifest.merkle_root, 4);
    proof.block_idx = block; proof.n_proof = merkle_gen_proof(incoming.manifest.leaves, 3, block, nullptr, siblings);
    proof.proof = siblings; assert(manager.on_message(wire, encode_proof(wire, sizeof(wire), proof)));
    manager.set_clock(100000 + block * 10000); manager.loop();
    assert(allocated == expected_allocation && manager.pendingServeJobs());
  }
  assert(manager.fetchState() == OtaManager::COMPLETE);
  MotaManifest staged; assert(mota_parse(store.data(), store.staged_size(), staged));
  assert(!memcmp(staged.payload, incoming.manifest.payload, incoming.manifest.payload_size));
  sent.accept_data = true; drain(manager, 200000);
  if (host) verify_v2(sent, outbound, 0, true, 400);
  else verify(sent, outbound, true);
  if (host) assert(outbound.host_calls > 1 && outbound.raw_reads == 0);
  manager.clear_sources(); assert(allocated == 0);
  if (host) puts("PASS: host DATA reloads after successful interleaved inbound decoding");
}
static bool partial_decode_failure(void*, const uint8_t*, uint16_t, uint8_t* out,
                                    uint16_t cap, uint16_t* produced) {
  assert(cap == OTA_MAX_BLOCK); memset(out, 0xEE, cap / 2); *produced = cap / 2;
  return false;
}
static void host_receive_failure() {
  Source outbound(5, 0x44), incoming(3, 0x71);
  outbound.host_encoding = Source::HostEncoding::Good; outbound.host_encoded_size = 400;
  Sent sent; OtaManager manager; OtaStoreRam<16384> store;
  manager.begin(123, send, &sent); manager.set_fetch_store(&store);
  manager.set_transport_deflate_decoder(partial_decode_failure);
  assert(manager.add_source(&outbound) && request(manager, outbound));
  manager.serviceEgress(); // Already-admitted bytes must keep the same representation after reload.
  assert(sent.packets.size() == 1 && manager.pendingServeJobs());
  sent.accept_data = false;
  assert(outbound.host_calls == 1 && allocated == 20);
  begin_receive(manager, incoming);
  uint8_t representation[12]; memset(representation + 4, 0x71, 8);
  mh4(representation, representation + 4, 8);
  DataMsg data{}; memcpy(data.manifest_id, incoming.manifest.merkle_root, 4);
  assert(ota_data_v2_pack_extended(0, 8, data.frag_off));
  data.data = representation; data.data_len = sizeof(representation);
  uint8_t wire[MAX_PACKET_PAYLOAD];
  assert(manager.on_message(wire, encode_data(wire, sizeof(wire), data)));
  assert(manager.fetchState() == OtaManager::FETCHING);
  sent.accept_data = true; drain(manager, 100000); verify_v2(sent, outbound, 0, true, 400);
  assert(outbound.host_calls == 2 && outbound.raw_reads == 0 && allocated == 20);
  manager.clear_sources(); assert(allocated == 0);
  puts("PASS: partial failed inbound decoder cannot leak into pinned host DATA");
}
static void interleaved_manifest(bool host = false) {
  Source data_source(9, 0x35), manifest_source(17, 0x75);
  if (host) {
    data_source.host_encoding = Source::HostEncoding::Good;
    data_source.host_encoded_size = 400;
  }
  const size_t output_allocation = host ? 0 : OTA_MAX_BLOCK;
  Sent sent; OtaManager manager;
  manager.begin(123, send, &sent); manager.set_transport_deflate_encoder(encode_long);
  assert(manager.add_source(&data_source) && manager.add_source(&manifest_source));
  assert(request(manager, data_source));
  manager.serviceEgress(); // first compressed fragment is admitted; later DATA stays pinned
  assert(manager.pendingServeJobs() && allocated == 36 + output_allocation);
  peak = allocated;
  assert(manifest_request(manager, manifest_source));
  assert(allocated == 68 + output_allocation && peak == allocated); // old leaves were freed before growth
  sent.accept_data = false;
  manager.set_clock(100000); manager.serviceEgress();
  manager.set_clock(200000); manager.serviceEgress();
  assert(!manager.pendingManifestJobs() && manager.pendingServeJobs());
  manager.loop(); assert(allocated == 68 + output_allocation);
  sent.accept_data = true; drain(manager, 300000);
  std::vector<uint8_t> encoded;
  uint8_t identity[4]{}; bool proof_seen = false;
  for (const auto& wire : sent.packets) {
    DataMsg data{}; ProofMsg proof{};
    if (decode_data(wire.data(), wire.size(), data)) {
      assert(!memcmp(data.manifest_id, data_source.manifest.merkle_root, 4));
      uint8_t fragment; uint16_t length; bool deflated;
      assert(ota_data_v2_unpack_extended(data.frag_off, OTA_MAX_BLOCK, fragment, length, deflated));
      assert(deflated && length == 400 && fragment * OTA_FRAG_DATA_V2 == encoded.size());
      if (encoded.empty()) memcpy(identity, data.data, 4);
      else assert(!memcmp(identity, data.data, 4));
      encoded.insert(encoded.end(), data.data + 4, data.data + data.data_len);
    } else if (decode_proof(wire.data(), wire.size(), proof)) {
      assert(merkle_verify(data_source.manifest.payload, OTA_MAX_BLOCK, 0,
                           proof.proof, proof.n_proof, data_source.manifest.merkle_root,
                           data_source.manifest.block_count));
      proof_seen = true;
    }
  }
  assert(proof_seen && encoded == std::vector<uint8_t>(400, 0x35));
  uint8_t expected[4]; mh4(expected, encoded.data(), encoded.size()); assert(!memcmp(identity, expected, 4));
  manager.clearPendingEgress(); assert(allocated == 0 && manager.servedCount() == 2);
  sent.packets.clear(); assert(request(manager, data_source)); manager.serviceEgress();
  fail_next = true; assert(!manifest_request(manager, manifest_source));
  assert(allocated == output_allocation); // failed growth released old leaves, but pinned DATA survives
  drain(manager, 900000);
  encoded.clear();
  for (const auto& packet : sent.packets) {
    DataMsg data{};
    if (decode_data(packet.data(), packet.size(), data))
      encoded.insert(encoded.end(), data.data + 4, data.data + data.data_len);
  }
  assert(encoded == std::vector<uint8_t>(400, 0x35));
  manager.clearPendingEgress(); assert(allocated == 0);
  manager.set_clock(0xFFFFFF00u); assert(request(manager, data_source)); drain(manager, 0xFFFFFF00u);
  manager.set_clock(0xFFFFFF00u + 100000u); manager.loop(); assert(allocated == 0);
  if (host) puts("PASS: host DATA survives interleaved source growth, failure and rollover");
}

struct SelfEncoder {
  uint8_t* expected_output;
  unsigned calls = 0;
};
static bool self_encode(void* context, const uint8_t* input, uint16_t length,
                         uint8_t* output, uint16_t capacity, uint16_t* produced) {
  auto& encoder = *static_cast<SelfEncoder*>(context);
  assert(output == encoder.expected_output && input != output);
  ++encoder.calls;
  return encode(nullptr, input, length, output, capacity, produced);
}
static bool self_read(void* context, uint32_t offset, uint8_t* output, uint32_t length) {
  auto& source = *static_cast<Source*>(context);
  return source.read(0, uint32_t(source.manifest.payload - source.mota.data()) + offset, output, length);
}
static void caller_owned_scratch() {
  Source source(3, 0x63);
  std::array<uint8_t, OTA_MAX_BLOCK> scratch{};
  SelfEncoder encoder{scratch.data()};
  Sent sent; OtaManager manager; manager.begin(123, send, &sent);
  manager.set_transport_deflate_encoder(self_encode, &encoder);
  assert(manager.serve_self(source.manifest.manifest_start, MOTA_MFL, source.manifest.leaves,
                            source.manifest.block_count, scratch.data(), scratch.size(), self_read, &source));
  fail_next = true; assert(request(manager, source)); drain(manager);
  assert(fail_next && allocated == 0 && encoder.calls == 1);
  verify_v2(sent, source, 0, true);
  fail_next = false; manager.clearPendingEgress();
  assert(scratch[0] == 0x63 && allocated == 0);
  puts("PASS: caller-owned self-source encoder output remains separate and unallocated");
}
int main() {
  proofs(); assert(allocated == 0);
  lifetimes(); assert(allocated == 0);
  faults_and_capacity(); assert(allocated == 0);
  concurrent_receive(); assert(allocated == 0);
  interleaved_manifest(); assert(allocated == 0);
  host_allocation_and_fallbacks(); assert(allocated == 0);
  host_tail_and_maximum_capacity(); assert(allocated == 0);
  host_backpressure_and_raw_switch(); assert(allocated == 0);
  concurrent_receive(true); assert(allocated == 0);
  host_receive_failure(); assert(allocated == 0);
  interleaved_manifest(true); assert(allocated == 0);
  caller_owned_scratch(); assert(allocated == 0);
  puts("PASS: streaming proofs and OTA cold-buffer lifetimes");
}
