#include <helpers/ota/OtaManager.h>
#include <helpers/ota/OtaManifestPolicy.h>
#include <helpers/ota/OtaByteIO.h>
#include <helpers/ota/Multihash.h>
#include "test_ota/mota_vectors.h"
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <openssl/evp.h>
namespace mesh { extern unsigned signature_checks; }
using namespace mesh::ota;

static std::array<void*, 64> allocations{};
static std::array<size_t, 64> allocation_sizes{};
static size_t allocated = 0, peak = 0;
extern "C" void* __real_malloc(size_t);
extern "C" void __real_free(void*);
extern "C" void* __wrap_malloc(size_t size) {
  void* result = __real_malloc(size);
  if (result) {
    const auto free_slot = std::find(allocations.begin(), allocations.end(), nullptr);
    assert(free_slot != allocations.end());
    const size_t index = free_slot - allocations.begin();
    allocations[index] = result; allocation_sizes[index] = size;
    allocated += size; peak = std::max(peak, allocated);
  }
  return result;
}
extern "C" void __wrap_free(void* result) {
  const auto found = std::find(allocations.begin(), allocations.end(), result);
  if (result && found != allocations.end()) {
    const size_t index = found - allocations.begin();
    allocated -= allocation_sizes[index]; allocations[index] = nullptr;
  }
  __real_free(result);
}

struct Wire {
  bool admit = true;
  unsigned attempts = 0;
  std::vector<std::vector<uint8_t>> messages;
  static bool send(void* raw, const uint8_t* message, uint16_t length, bool) {
    auto& self = *static_cast<Wire*>(raw);
    ++self.attempts;
    if (!self.admit) return false;
    self.messages.emplace_back(message, message + length);
    return true;
  }
};
struct Store : OtaStoreRam<4096> {
  unsigned plans = 0, begins = 0, writes = 0;
  bool plan_layout(bool, uint32_t, uint32_t, uint32_t, bool) override { ++plans; return true; }
  bool begin(uint32_t size) override { ++begins; return OtaStoreRam<4096>::begin(size); }
  bool write(uint32_t offset, const uint8_t* bytes, uint32_t size) override {
    ++writes; return OtaStoreRam<4096>::write(offset, bytes, size);
  }
};
struct TrustedPolicy {
  SignerAllowlist allow;
  unsigned calls = 0;
  static bool admit(void* raw, const MotaManifest& manifest) {
    auto& policy = *static_cast<TrustedPolicy*>(raw);
    ++policy.calls;
    return ota_manifest_trusted(manifest, policy.allow);
  }
};
static std::vector<uint8_t> signed_manifest(TrustedPolicy& policy) {
  std::vector<uint8_t> result(SIM_MOTA + 8, SIM_MOTA + 8 + MOTA_MFL);
  uint8_t secret[32];
  for (unsigned i = 0; i < sizeof(secret); ++i) secret[i] = uint8_t(i + 1);
  EVP_PKEY* key = EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519, nullptr, secret, sizeof(secret));
  assert(key);
  size_t public_size = 32;
  assert(EVP_PKEY_get_raw_public_key(key, result.data() + 97, &public_size) == 1);
  assert(public_size == 32 && policy.allow.add(result.data() + 97));
  EVP_MD_CTX* context = EVP_MD_CTX_new();
  size_t signature_size = 64;
  assert(context && EVP_DigestSignInit(context, nullptr, nullptr, nullptr, key) == 1);
  assert(EVP_DigestSign(context, result.data() + 129, &signature_size,
                        result.data(), MOTA_SIGNED_LEN) == 1);
  assert(signature_size == 64);
  EVP_MD_CTX_free(context); EVP_PKEY_free(key);
  return result;
}
static void deliver_manifest(OtaManager& manager, const uint8_t mid[4], const uint8_t* bytes) {
  for (unsigned fragment = 0; fragment < 2; ++fragment) {
    ManifestMsg message{};
    memcpy(message.manifest_id, mid, 4);
    message.frag_idx = fragment; message.frag_total = 2;
    message.bytes = bytes + fragment * OTA_MF_FRAG;
    message.len = fragment ? MOTA_MFL - OTA_MF_FRAG : OTA_MF_FRAG;
    uint8_t wire[256];
    manager.on_message(wire, encode_manifest(wire, sizeof(wire), message));
  }
}
static void have(OtaManager& manager, const MotaManifest& manifest, uint32_t source = 1) {
  uint8_t row[OTA_HAVE_ROW_BYTES]{};
  memcpy(row, manifest.merkle_root, 4);
  wr_u32le(row + 4, manifest.target_id); wr_u32le(row + 8, manifest.fw_version);
  row[12] = manifest.codec_id; row[13] = manifest.flags; wr_u16le(row + 14, 1);
  HaveMsg message{};
  wr_u32le(message.seeder_id, source); message.frag_total = 1;
  message.n_rows = 1; message.rows = row;
  uint8_t wire[256];
  manager.on_message(wire, encode_have(wire, sizeof(wire), message));
}
static MotaManifest parse(const std::vector<uint8_t>& bytes) {
  MotaManifest manifest{};
  assert(mota_parse_manifest(bytes.data(), bytes.size(), manifest));
  return manifest;
}
static void retain_existing(Store& store) {
  assert(store.begin(SIM_MOTA_LEN) && store.write(0, SIM_MOTA, SIM_MOTA_LEN));
  store.plans = store.begins = store.writes = 0;
}
static void intact(const Store& store) {
  assert(store.plans == 0 && store.begins == 0 && store.writes == 0);
  assert(store.staged_size() == SIM_MOTA_LEN);
  assert(!memcmp(store.data(), SIM_MOTA, SIM_MOTA_LEN));
}
static void signature_admission() {
  TrustedPolicy policy;
  const auto good = signed_manifest(policy);
  const auto valid = parse(good);
  assert(ota_manifest_trusted(valid, policy.allow));
  for (unsigned scenario = 0; scenario < 5; ++scenario) {
    auto bytes = good;
    TrustedPolicy configured = policy;
    if (scenario == 1) configured.allow.clear();
    if (scenario == 2) bytes[129] ^= 1; // signature corruption
    if (scenario == 3) bytes[57] ^= 1;  // signed hardware identity corruption
    if (scenario == 4) bytes[1] &= uint8_t(~MFLAG_SIGNED);
    const auto manifest = parse(bytes);
    Store store; retain_existing(store);
    Wire wire; OtaManager manager;
    manager.begin(valid.target_id, Wire::send, &wire);
    manager.set_fetch_store(&store);
    manager.set_autofetch(OtaManager::AUTOFETCH_SIGNED);
    if (scenario != 0) manager.set_manifest_admission(TrustedPolicy::admit, &configured);
    have(manager, valid);
    assert(manager.fetchState() == OtaManager::WANT_MANIFEST);
    const unsigned checks = mesh::signature_checks;
    deliver_manifest(manager, valid.merkle_root, bytes.data());
    assert(manager.fetchState() == OtaManager::FAILED);
    assert(manager.fetchError() == OtaManager::FETCH_ERROR_MANIFEST);
    intact(store);
    if (scenario == 0 || scenario == 1 || scenario == 4) assert(mesh::signature_checks == checks);
    else assert(mesh::signature_checks == checks + 1);
    if (scenario != 4) assert(!ota_manifest_trusted(manifest, configured.allow) || scenario == 0);
  }
  // Approval is unsigned local authorization and must not survive peer ingress.
  auto approved = good;
  memset(approved.data() + 193, 0xA5, 4);
  Store store; retain_existing(store); Wire wire; OtaManager manager;
  manager.begin(valid.target_id, Wire::send, &wire);
  manager.set_fetch_store(&store);
  manager.set_autofetch(OtaManager::AUTOFETCH_SIGNED);
  manager.set_manifest_admission(TrustedPolicy::admit, &policy);
  have(manager, valid);
  deliver_manifest(manager, valid.merkle_root, approved.data());
  assert(manager.fetchState() == OtaManager::FETCHING);
  assert(store.plans == 1 && store.begins == 1);
  MotaManifest staged;
  assert(mota_parse_manifest(store.data() + 8, MOTA_MFL, staged));
  assert(ota_manifest_trusted(staged, policy.allow));
    assert(!staged.is_approved());
  // NOR flash can only clear bits. The unapproved marker must stay erased so
  // the separate authorized local installer can program APPROVAL_YES later.
  for (unsigned i = 0; i < 4; ++i) {
    assert(staged.approval[i] == 0xFF);
    assert((staged.approval[i] & APPROVAL_YES[i]) == APPROVAL_YES[i]);
  }
  // Automatic reboot resume has the same signature gate; manual capture is
  // permitted independently and never grants automatic install approval.
  const uint8_t corrupt_signature = store.data()[8 + 129] ^ 1u;
  assert(store.write(8 + 129, &corrupt_signature, 1));
  manager.reset_session();
  assert(!manager.resumeStaged(nullptr));
  assert(manager.pull(valid.merkle_root, valid.target_id) == OtaManager::PULL_RESUMED);
  assert(manager.fetchState() == OtaManager::VERIFYING_STAGED);
}
static void failure_cooldown(bool rollover) {
  TrustedPolicy policy;
  auto bytes = signed_manifest(policy);
  bytes[129] ^= 1;
  const auto manifest = parse(bytes);
  Store store; retain_existing(store); Wire wire; OtaManager manager;
  manager.begin(manifest.target_id, Wire::send, &wire);
  manager.set_fetch_store(&store);
  manager.set_autofetch(OtaManager::AUTOFETCH_SIGNED);
  manager.set_manifest_admission(TrustedPolicy::admit, &policy);
  uint32_t now = rollover ? UINT32_MAX - 30000u : 100u;
  uint32_t delay = OTA_AUTOFETCH_FAILURE_BACKOFF_MS;
  for (unsigned attempt = 0; attempt < 8; ++attempt) {
    manager.set_clock(now);
    have(manager, manifest);
    assert(manager.fetchState() == OtaManager::WANT_MANIFEST);
    deliver_manifest(manager, manifest.merkle_root, bytes.data());
    assert(manager.fetchState() == OtaManager::FAILED);
    intact(store);
    const unsigned admitted = policy.calls;
    // Neither a repeated source nor rotating a forged MID bypasses cooldown.
    auto rotated = bytes; rotated[20] ^= 0x42;
    const auto other = parse(rotated);
    for (unsigned packet = 0; packet < 100; ++packet) {
      have(manager, packet % 2 ? other : manifest, packet + 2);
      assert(manager.fetchState() == OtaManager::FAILED);
    }
    assert(policy.calls == admitted);
    manager.set_clock(now + delay - 1u);
    have(manager, manifest);
    assert(manager.fetchState() == OtaManager::FAILED);
    now += delay;
    if (delay > OTA_AUTOFETCH_FAILURE_MAX_BACKOFF_MS / 2u) delay = OTA_AUTOFETCH_FAILURE_MAX_BACKOFF_MS;
    else delay *= 2u;
  }
  const unsigned admitted = policy.calls;
  // The authorized manual pull works during cooldown, without silently
  // altering the signed-only automatic policy or installation trust gate.
  auto manual_bytes = bytes; manual_bytes[20] ^= 0x42;
  const auto manual_manifest = parse(manual_bytes);
  assert(manager.pull(manual_manifest.merkle_root, manual_manifest.target_id) == OtaManager::PULL_STARTED);
  deliver_manifest(manager, manual_manifest.merkle_root, manual_bytes.data());
  assert(manager.fetchState() == OtaManager::FETCHING && policy.calls == admitted);
  MotaManifest staged;
  assert(mota_parse_manifest(store.data() + 8, MOTA_MFL, staged));
  assert(!ota_manifest_trusted(staged, policy.allow));
}

static std::vector<uint8_t> container(uint32_t blocks) {
  const uint32_t block_size = 128, payload_size = blocks * block_size;
  const uint32_t size = 8 + MOTA_MFL + blocks * 4 + payload_size + 5;
  std::vector<uint8_t> image(size, 0);
  memcpy(image.data(), MOTA_MAGIC, 4); wr_u32le(image.data() + 4, size);
  uint8_t* manifest = image.data() + 8;
  memcpy(manifest, SIM_MOTA + 8, MOTA_MFL);
  manifest[1] = MFLAG_FULL; manifest[2] = HASH_ALGO_SHA256;
  manifest[19] = 7;
  wr_u32le(manifest + 11, payload_size); wr_u32le(manifest + 15, payload_size);
  // codec is offset56 in the fixed manifest.
  manifest[56] = CODEC_FULL;
  uint8_t* leaves = manifest + MOTA_MFL;
  uint8_t* payload = leaves + blocks * 4;
  for (uint32_t i = 0; i < payload_size; ++i) payload[i] = uint8_t(i * 17u);
  for (uint32_t i = 0; i < blocks; ++i) merkle_leaf(leaves + i * 4, payload + i * block_size, block_size);
  merkle_root(manifest + 20, leaves, blocks);
  memcpy(image.data() + size - 5, MOTA_TRAILER, 5);
  MotaManifest parsed; assert(mota_parse(image.data(), size, parsed));
  assert(parsed.block_count == blocks);
  return image;
}
static bool leaves(OtaManager& manager, const uint8_t* mid, uint16_t mask, OtaReplyRoute route = {}) {
  GetLeavesMsg request{};
  memcpy(request.manifest_id, mid, 4); request.want_mask = mask;
  uint8_t wire[32];
  return manager.on_message(wire, encode_get_leaves(wire, sizeof(wire), request), route);
}
static bool route_valid(void* current, const OtaReplyRoute& route) {
  return route == *static_cast<OtaReplyRoute*>(current);
}
static void leaf_pacing(bool rollover) {
  const auto image = container(OTA_DIFF_MAX_BLOCKS);
  MotaManifest manifest; assert(mota_parse(image.data(), image.size(), manifest));
  Wire wire; OtaManager manager;
  manager.begin(1, Wire::send, &wire); assert(manager.serve(image.data(), image.size()));
  uint32_t now = rollover ? UINT32_MAX - 50u : 100u;
  manager.set_clock(now);
  OtaReplyRoute route; route.profile = 1; route.generation = 7;
  assert(leaves(manager, manifest.merkle_root, 0xFFFF, route));
  assert(manager.pendingManifestJobs() == 1 && wire.messages.empty());
  for (unsigned i = 0; i < 100; ++i) assert(leaves(manager, manifest.merkle_root, 0xFFFF, route));
  assert(manager.pendingManifestJobs() == 1 && wire.messages.empty());
  manager.serviceEgress(route_valid, &route);
  assert(wire.attempts == 0); // turnaround deadline has not expired
  now += 100;
  manager.set_clock(now); wire.admit = false;
  for (unsigned i = 0; i < 100; ++i) manager.serviceEgress(route_valid, &route);
  assert(wire.messages.empty() && manager.pendingManifestJobs() == 1);
  wire.admit = true;
  for (unsigned fragment = 0; fragment < 16; ++fragment) {
    const unsigned before = wire.messages.size();
    manager.serviceEgress(route_valid, &route);
    assert(wire.messages.size() == before + 1);
    LeavesMsg reply;
    assert(decode_leaves(wire.messages.back().data(), wire.messages.back().size(), reply));
    assert(reply.frag_idx == fragment && reply.frag_total == 16);
    assert(!memcmp(reply.bytes, manifest.leaves + fragment * OTA_LEAVES_FRAG, reply.len));
    if (fragment < 15) {
      assert(leaves(manager, manifest.merkle_root, 0xFFFF, route));
      manager.serviceEgress(route_valid, &route);
      assert(wire.messages.size() == before + 1); // duplicates never reset/send emitted bits
    }
    now += 100; manager.set_clock(now);
  }
  assert(manager.pendingManifestJobs() == 0 && wire.messages.size() == 16);
  // Retry exactly the requested holes, without synchronous transmission.
  assert(leaves(manager, manifest.merkle_root, 0x8001, route));
  for (unsigned i = 0; i < 2; ++i) {
    now += 100; manager.set_clock(now); manager.serviceEgress(route_valid, &route);
    LeavesMsg reply;
    assert(decode_leaves(wire.messages.back().data(), wire.messages.back().size(), reply));
    assert(reply.frag_idx == (i ? 15 : 0));
  }
  assert(manager.pendingManifestJobs() == 0);
  // Closed temporary radio generations and explicit teardown cannot retain
  // stale queued work or reply on another profile.
  assert(leaves(manager, manifest.merkle_root, 1, route));
  ++route.generation;
  const auto before = wire.messages.size();
  manager.serviceEgress(route_valid, &route);
  assert(manager.pendingManifestJobs() == 0 && wire.messages.size() == before);
  assert(leaves(manager, manifest.merkle_root, 1, route));
  manager.clearPendingEgress();
  assert(manager.pendingManifestJobs() == 0);
  assert(!leaves(manager, manifest.merkle_root, 0, route));
  const auto oversized = container(OTA_DIFF_MAX_BLOCKS + 1);
  assert(manager.serve(oversized.data(), oversized.size()));
  MotaManifest large; assert(mota_parse(oversized.data(), oversized.size(), large));
  assert(!leaves(manager, large.merkle_root, 0xFFFF, route));
  assert(manager.pendingManifestJobs() == 0);
}
static void catalog(OtaManager& manager, uint32_t first, uint32_t last) {
  for (uint32_t id = first; id < last; ++id) {
    uint8_t row[OTA_HAVE_ROW_BYTES]{};
    wr_u32le(row, id + 1); wr_u32le(row + 4, 123);
    row[12] = CODEC_FULL; row[13] = MFLAG_FULL;
    HaveMsg message{}; wr_u32le(message.seeder_id, 42);
    message.frag_total = 1; message.n_rows = 1; message.rows = row;
    uint8_t wire[256];
    manager.on_message(wire, encode_have(wire, sizeof(wire), message));
  }
}
static void catalog_budget() {
  assert(allocated == 0);
  {
    OtaManager manager; manager.begin(123, nullptr, nullptr);
    catalog(manager, 0, 255);
    assert(manager.catalogCount() == OTA_INLINE_CATALOG && allocated == 0);
    manager.queryAll(); catalog(manager, 0, 255);
    assert(manager.catalogCount() == OTA_MAX_CATALOG);
    assert(allocated == sizeof(OtaManager::CatRow) * OTA_MAX_CATALOG && allocated <= 16384);
    AdvMsg withdrawal{}; wr_u32le(withdrawal.seeder_id, 42);
    uint8_t wire[64]; manager.on_message(wire, encode_adv(wire, sizeof(wire), withdrawal));
    assert(manager.catalogCount() == 0 && allocated == 0);
    catalog(manager, 0, 255);
    assert(allocated != 0);
    manager.begin(123, nullptr, nullptr);
    assert(manager.catalogCount() == 0 && allocated == 0);
    catalog(manager, 0, 255);
    assert(manager.catalogCount() == OTA_INLINE_CATALOG && allocated == 0);
  }
  assert(allocated == 0);
}
static void proof_noise() {
  MotaManifest manifest; assert(mota_parse(SIM_MOTA_1K, SIM_MOTA_1K_LEN, manifest));
  Store store; Wire wire; OtaManager manager;
  manager.begin(manifest.target_id, Wire::send, &wire);
  manager.set_fetch_store(&store); manager.set_clock(100);
  manager.set_wire_v2_enabled(false);
  assert(manager.pull(manifest.merkle_root, manifest.target_id) == OtaManager::PULL_STARTED);
  deliver_manifest(manager, manifest.merkle_root, manifest.manifest_start);
  uint8_t packet[256];
  const uint32_t size = manifest.block_size();
  for (uint32_t offset = 0; offset < size; offset += OTA_FRAG_DATA) {
    DataMsg data{}; memcpy(data.manifest_id, manifest.merkle_root, 4);
    data.block_idx = 0; data.frag_off = offset;
    data.data = manifest.payload + offset; data.data_len = std::min(size - offset, uint32_t(OTA_FRAG_DATA));
    manager.on_message(packet, encode_data(packet, sizeof(packet), data));
  }
  std::array<uint8_t, 128> siblings{};
  const uint8_t count = merkle_gen_proof(manifest.leaves, manifest.block_count, 0, nullptr, siblings.data());
  assert(count > 0);
  ProofMsg proof{}; memcpy(proof.manifest_id, manifest.merkle_root, 4);
  proof.block_idx = 0; proof.n_proof = count; proof.proof = siblings.data();
  siblings[0] ^= 1;
  const unsigned before = wire.messages.size();
  for (unsigned i = 0; i < 100; ++i) manager.on_message(packet, encode_proof(packet, sizeof(packet), proof));
  assert(manager.blocksHave() == 0 && wire.messages.size() == before);
  siblings[0] ^= 1;
  manager.on_message(packet, encode_proof(packet, sizeof(packet), proof));
  assert(manager.blocksHave() == 1); // forged proof did not erase valid DATA
}
int main() {
  signature_admission();
  failure_cooldown(false); failure_cooldown(true);
  leaf_pacing(false); leaf_pacing(true);
  catalog_budget(); proof_noise();
  assert(allocated == 0);
  puts("PASS: authenticated automatic staging, bounded catalog, paced leaves and proof-noise recovery");
}
