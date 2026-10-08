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
  std::vector<uint8_t> mota;
  MotaManifest manifest;
  bool fail_read = false;
  Source(uint32_t blocks, uint8_t value) {
    const uint32_t total = 8 + MOTA_MFL + blocks * 4 + blocks * OTA_MAX_BLOCK + 5;
    mota.resize(total);
    memcpy(mota.data(), MOTA_MAGIC, 4); wr_u32le(mota.data() + 4, total);
    uint8_t* mf = mota.data() + 8;
    mf[0] = MOTA_FORMAT_VER; mf[1] = MFLAG_FULL; mf[2] = HASH_ALGO_SHA256;
    wr_u32le(mf + 3, 123); wr_u32le(mf + 7, 1);
    wr_u32le(mf + 11, blocks * OTA_MAX_BLOCK); wr_u32le(mf + 15, blocks * OTA_MAX_BLOCK);
    mf[19] = 11; mf[56] = CODEC_FULL;
    uint8_t* leaves = mf + MOTA_MFL;
    uint8_t* payload = leaves + blocks * 4;
    for (uint32_t i = 0; i < blocks; ++i) {
      memset(payload + i * OTA_MAX_BLOCK, uint8_t(value + i), OTA_MAX_BLOCK);
      merkle_leaf(leaves + i * 4, payload + i * OTA_MAX_BLOCK, OTA_MAX_BLOCK);
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
    return true;
  }
  bool read(uint8_t index, uint32_t off, uint8_t* out, uint32_t len) override {
    if (index || (uint64_t)off + len > mota.size()) return false;
    if (fail_read) { if (len) out[0] = 0xEE; return false; }
    memcpy(out, mota.data() + off, len); return true;
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
  assert(cap == OTA_MAX_BLOCK); memset(dst, src[0], 8); *len = 8; return true;
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
static void concurrent_receive() {
  Source outbound(5, 0x40), incoming(3, 0x70);
  Sent sent; OtaManager manager; OtaStoreRam<16384> store;
  manager.begin(123, send, &sent); manager.set_fetch_store(&store);
  manager.set_transport_deflate_encoder(encode); manager.set_transport_deflate_decoder(decode);
  assert(manager.add_source(&outbound) && request(manager, outbound));
  sent.accept_data = false; manager.serviceEgress();
  assert(allocated == 20 + OTA_MAX_BLOCK);
  assert(manager.pull(incoming.manifest.merkle_root, 123) == OtaManager::PULL_STARTED);
  for (unsigned f = 0; f < 2; ++f) {
    ManifestMsg mf{}; memcpy(mf.manifest_id, incoming.manifest.merkle_root, 4);
    mf.frag_idx = f; mf.frag_total = 2; mf.bytes = incoming.manifest.manifest_start + f * OTA_MF_FRAG;
    mf.len = f == 0 ? OTA_MF_FRAG : MOTA_MFL - OTA_MF_FRAG;
    uint8_t wire[MAX_PACKET_PAYLOAD]; manager.on_message(wire, encode_manifest(wire, sizeof(wire), mf));
  }
  assert(manager.fetchState() == OtaManager::FETCHING);
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
    assert(allocated == 20 + OTA_MAX_BLOCK && manager.pendingServeJobs());
  }
  assert(manager.fetchState() == OtaManager::COMPLETE);
  MotaManifest staged; assert(mota_parse(store.data(), store.staged_size(), staged));
  assert(!memcmp(staged.payload, incoming.manifest.payload, incoming.manifest.payload_size));
  sent.accept_data = true; drain(manager, 200000); verify(sent, outbound, true);
  manager.clear_sources(); assert(allocated == 0);
}
static void interleaved_manifest() {
  Source data_source(9, 0x35), manifest_source(17, 0x75);
  Sent sent; OtaManager manager;
  manager.begin(123, send, &sent); manager.set_transport_deflate_encoder(encode_long);
  assert(manager.add_source(&data_source) && manager.add_source(&manifest_source));
  assert(request(manager, data_source));
  manager.serviceEgress(); // first compressed fragment is admitted; later DATA stays pinned
  assert(manager.pendingServeJobs() && allocated == 36 + OTA_MAX_BLOCK);
  peak = allocated;
  assert(manifest_request(manager, manifest_source));
  assert(allocated == 68 + OTA_MAX_BLOCK && peak == allocated); // old leaves were freed before growth
  sent.accept_data = false;
  manager.set_clock(100000); manager.serviceEgress();
  manager.set_clock(200000); manager.serviceEgress();
  assert(!manager.pendingManifestJobs() && manager.pendingServeJobs());
  manager.loop(); assert(allocated == 68 + OTA_MAX_BLOCK);
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
  assert(allocated == OTA_MAX_BLOCK); // failed growth released old leaves, but pinned DATA survives
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
}
int main() {
  proofs(); assert(allocated == 0);
  lifetimes(); assert(allocated == 0);
  faults_and_capacity(); assert(allocated == 0);
  concurrent_receive(); assert(allocated == 0);
  interleaved_manifest(); assert(allocated == 0);
  puts("PASS: streaming proofs and OTA cold-buffer lifetimes");
}
