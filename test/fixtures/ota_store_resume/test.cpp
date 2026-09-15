#include <helpers/ota/OtaManager.h>
#include <helpers/ota/OtaStoreFlashEsp32.h>
#include <helpers/ota/OtaByteIO.h>
#include "../../test_ota/mota_vectors.h"
#include <cassert>
#include <cstring>
#include <vector>

using namespace mesh::ota;
static std::vector<uint8_t> flash(65536, 0xFF);
static size_t fail_read_offset = SIZE_MAX;
static const esp_partition_t partition{65536};
const esp_partition_t* esp_ota_get_next_update_partition(const esp_partition_t*) {
  return &partition;
}
esp_err_t esp_partition_read(const esp_partition_t*, size_t off, void* data, size_t n) {
  if (off == fail_read_offset) return -1;
  if (off > flash.size() || n > flash.size() - off) return -1;
  memcpy(data, flash.data() + off, n);
  return ESP_OK;
}
esp_err_t esp_partition_write(const esp_partition_t*, size_t off, const void* data, size_t n) {
  if (off > flash.size() || n > flash.size() - off) return -1;
  const auto* bytes = static_cast<const uint8_t*>(data);
  for (size_t i = 0; i < n; ++i) {
    assert((flash[off + i] & bytes[i]) == bytes[i]);
    flash[off + i] &= bytes[i];
  }
  return ESP_OK;
}
esp_err_t esp_partition_erase_range(const esp_partition_t*, size_t off, size_t n) {
  if (off > flash.size() || n > flash.size() - off || off % 4096 || n % 4096) return -1;
  memset(flash.data() + off, 0xFF, n);
  return ESP_OK;
}
static bool send(void*, const uint8_t*, uint16_t, bool) { return true; }

// Folder/SD store size comes from the backing file, independently of its header.
struct StatSizeStore : OtaStoreRam<4096> {
  bool fail_trailer_read = false;
  bool reopen() override { return staged_size() != 0; }
  bool read(uint32_t off, uint8_t* bytes, uint32_t n) const override {
    return !(fail_trailer_read && off == staged_size() - 5)
        && OtaStoreRam<4096>::read(off, bytes, n);
  }
};

static void resume_checks_file_envelope(int corruption) {
  StatSizeStore store;
  std::vector<uint8_t> bytes(SIM_MOTA_1K, SIM_MOTA_1K + SIM_MOTA_1K_LEN);
  MotaManifest manifest;
  assert(mota_parse(bytes.data(), bytes.size(), manifest));
  uint8_t mid[4]; memcpy(mid, manifest.merkle_root, sizeof(mid));
  if (corruption == 1) wr_u32le(bytes.data() + 4, bytes.size() + 1);
  if (corruption == 2) bytes.back() ^= 1;
  if (corruption == 3) store.fail_trailer_read = true;
  if (corruption == 4) memset(bytes.data() + bytes.size() - 5, 0xFF, 5);
  assert(store.begin(bytes.size()));
  assert(store.write(0, bytes.data(), bytes.size()));
  OtaManager receiver;
  receiver.begin(SIM_TARGET_ID, send, nullptr);
  receiver.set_fetch_store(&store);
  const bool adopted = receiver.resumeStagedExplicit(mid, SIM_TARGET_ID);
  assert(adopted == (corruption == 0));
  if (adopted) {
    for (unsigned guard = 0; guard < 100 && receiver.fetchState() == OtaManager::VERIFYING_STAGED; ++guard)
      receiver.loop();
    assert(receiver.fetchState() == OtaManager::COMPLETE);
  }
}

static std::vector<uint8_t> delta_container(uint32_t total) {
  uint32_t blocks = (total - 210u + 1023u) / 1024u;
  const uint32_t payload_size = total - 210u - blocks * 4u;
  assert((payload_size + 1023u) / 1024u == blocks);
  std::vector<uint8_t> bytes(total);
  memcpy(bytes.data(), MOTA_VEC, 8u + MOTA_MFL);
  wr_u32le(bytes.data() + 4, total);
  bytes[8 + 1] = 0;
  bytes[8 + 56] = CODEC_DETOOLS_SEQUENTIAL;
  wr_u32le(bytes.data() + 8 + 15, payload_size);
  uint8_t* leaves = bytes.data() + 8u + MOTA_MFL;
  uint8_t* payload = leaves + blocks * 4u;
  for (uint32_t i = 0; i < payload_size; ++i) payload[i] = static_cast<uint8_t>(i * 31u);
  for (uint32_t i = 0; i < blocks; ++i) {
    const uint32_t n = std::min(1024u, payload_size - i * 1024u);
    merkle_leaf(leaves + i * 4u, payload + i * 1024u, n);
  }
  merkle_root(bytes.data() + 8 + 20, leaves, blocks);
  memcpy(bytes.data() + total - 5, MOTA_TRAILER, 5);
  return bytes;
}

static void esp32_resume_preserves_container_trailer(bool finalized, bool full, uint32_t small_size = 0) {
  std::fill(flash.begin(), flash.end(), 0xFF);
  std::vector<uint8_t> bytes = small_size ? delta_container(small_size)
      : std::vector<uint8_t>(MOTA_VEC, MOTA_VEC + MOTA_VEC_LEN);
  if (!full) {
    bytes[8 + 1] = 0;
    bytes[8 + 56] = CODEC_DETOOLS_SEQUENTIAL;
  }
  MotaManifest manifest;
  assert(mota_parse(bytes.data(), bytes.size(), manifest));
  uint8_t mid[4]; memcpy(mid, manifest.merkle_root, sizeof(mid));
  {
    OtaStoreFlashEsp32 store;
    assert(store.plan_layout(full, manifest.image_size,
           manifest.payload - bytes.data(), manifest.payload_size, false));
    assert(store.begin(bytes.size()));
    // The manager writes the trailer separately from the metadata/payload.
    assert(store.write(0, bytes.data(), bytes.size() - 5));
    assert(store.write(bytes.size() - 5, bytes.data() + bytes.size() - 5, 5));
    if (finalized) assert(store.finalize());
    else store.checkpoint();
  }
  OtaStoreFlashEsp32 resumed;
  OtaManager receiver;
  receiver.begin(EXP_TARGET_ID, send, nullptr);
  receiver.set_fetch_store(&resumed);
  assert(receiver.resumeStagedExplicit(mid, EXP_TARGET_ID));
  for (unsigned guard = 0; guard < 100 && receiver.fetchState() == OtaManager::VERIFYING_STAGED; ++guard)
    receiver.loop();
  assert(receiver.fetchState() == OtaManager::COMPLETE);
  std::vector<uint8_t> output(bytes.size());
  assert(resumed.read(0, output.data(), output.size()));
  assert(output == bytes);
  MotaManifest parsed;
  assert(mota_parse(output.data(), output.size(), parsed));
  assert(mota_check_payload(parsed));
}

static void dirty_partial_checkpoint_can_finish(bool reboot) {
  std::fill(flash.begin(), flash.end(), 0xA5);
  auto bytes = delta_container(5547);
  MotaManifest manifest;
  assert(mota_parse(bytes.data(), bytes.size(), manifest));
  const uint32_t payload_off = manifest.payload - bytes.data();
  const uint32_t leaves_off = manifest.leaves - bytes.data();
  uint8_t mid[4]; memcpy(mid, manifest.merkle_root, sizeof(mid));
  OtaStoreFlashEsp32 original, reopened;
  assert(original.plan_layout(false, manifest.image_size, payload_off, manifest.payload_size, false));
  assert(original.begin(bytes.size()));
  assert(original.write(0, bytes.data(), leaves_off));
  assert(original.write(payload_off, manifest.payload, 1024));
  assert(original.write(leaves_off, manifest.leaves, 4));
  assert(original.write(bytes.size() - 5, MOTA_TRAILER, 5));
  original.checkpoint();
  OtaStoreFlashEsp32& active = reboot ? reopened : original;
  OtaManager receiver;
  receiver.begin(EXP_TARGET_ID, send, nullptr);
  receiver.set_fetch_store(&active);
  if (reboot) {
    assert(receiver.resumeStagedExplicit(mid, EXP_TARGET_ID));
    receiver.loop();
    assert(receiver.fetchState() == OtaManager::FETCHING);
    assert(receiver.blocksHave() == 1);
  }
  for (uint32_t i = 1; i < manifest.block_count; ++i) {
    const uint32_t n = std::min(1024u, manifest.payload_size - i * 1024u);
    assert(active.write(payload_off + i * 1024u, manifest.payload + i * 1024u, n));
    assert(active.write(leaves_off + i * 4u, manifest.leaves + i * 4u, 4));
  }
  assert(active.finalize());
  receiver.reset_session();
  assert(receiver.resumeStagedExplicit(mid, EXP_TARGET_ID));
  receiver.loop();
  assert(receiver.fetchState() == OtaManager::COMPLETE);
  std::vector<uint8_t> output(bytes.size());
  assert(active.read(0, output.data(), output.size()));
  assert(output == bytes);
}

static std::vector<uint8_t> large_container(bool full) {
  auto bytes = delta_container(20000);
  if (full) {
    bytes[8 + 1] = MFLAG_FULL;
    bytes[8 + 56] = CODEC_FULL;
    wr_u32le(bytes.data() + 8 + 11, rd_u32le(bytes.data() + 8 + 15));
  }
  return bytes;
}

static void begin_container(OtaStoreFlashEsp32& store,
                            const std::vector<uint8_t>& bytes,
                            const MotaManifest& manifest) {
  assert(store.plan_layout(manifest.is_full(), manifest.image_size,
      manifest.payload - bytes.data(), manifest.payload_size, false));
  assert(store.begin(bytes.size()));
  assert(store.write(0, bytes.data(), manifest.leaves - bytes.data()));
  assert(store.write(bytes.size() - 5, MOTA_TRAILER, 5));
}

static void expect_bytes(OtaStoreFlashEsp32& store, uint32_t offset,
                         const uint8_t* expected, uint32_t len) {
  std::vector<uint8_t> actual(len);
  assert(store.read(offset, actual.data(), actual.size()));
  assert(memcmp(actual.data(), expected, len) == 0);
}

static void esp32_revisit_preserves_acknowledged_blocks(bool full, int persistence) {
  // Dirty previous firmware distinguishes fresh sectors from resumed ones.
  std::fill(flash.begin(), flash.end(), 0xA5);
  auto bytes = large_container(full);
  MotaManifest manifest;
  assert(mota_parse(bytes.data(), bytes.size(), manifest));
  const uint32_t payload_off = manifest.payload - bytes.data();
  const uint32_t leaves_off = manifest.leaves - bytes.data();
  // The full image streams at slot offset zero. A delta pins its first
  // container sector, so choose bytes in its second streaming sector instead.
  const uint32_t higher = full ? payload_off + 4096u : 8192u;
  const uint32_t lower = higher - 4096u;
  OtaStoreFlashEsp32 original, reopened;
  begin_container(original, bytes, manifest);
  assert(original.write(higher, bytes.data() + higher, 1024));
  assert(original.write(leaves_off, manifest.leaves, 4));
  if (persistence) original.checkpoint();
  OtaStoreFlashEsp32& active = persistence == 2 ? reopened : original;
  if (persistence == 2) assert(active.reopen());
  assert(active.write(lower, bytes.data() + lower, 1024));
  assert(active.write(higher + 1024, bytes.data() + higher + 1024, 1024));
  expect_bytes(active, higher, bytes.data() + higher, 2048);
  expect_bytes(active, lower, bytes.data() + lower, 1024);
  expect_bytes(active, leaves_off, manifest.leaves, 4);
  assert(active.finalize());
  assert(reopened.reopen());
  expect_bytes(reopened, higher, bytes.data() + higher, 2048);
  expect_bytes(reopened, lower, bytes.data() + lower, 1024);
}

static void deliver_block(OtaManager& receiver, const MotaManifest& manifest, uint32_t block) {
  uint8_t wire[256];
  const uint32_t len = std::min(1024u, manifest.payload_size - block * 1024u);
  for (uint32_t off = 0; off < len; off += OTA_FRAG_DATA) {
    DataMsg data{};
    memcpy(data.manifest_id, manifest.merkle_root, 4);
    data.block_idx = block;
    data.frag_off = off;
    data.data = manifest.payload + block * 1024u + off;
    data.data_len = std::min((uint32_t)OTA_FRAG_DATA, len - off);
    assert(receiver.on_message(wire, encode_data(wire, sizeof(wire), data)));
  }
  std::vector<uint8_t> scratch(manifest.block_count * 4);
  uint8_t siblings[128];
  ProofMsg proof{};
  memcpy(proof.manifest_id, manifest.merkle_root, 4);
  proof.block_idx = block;
  proof.n_proof = merkle_gen_proof(manifest.leaves, manifest.block_count, block,
                                  scratch.data(), siblings);
  proof.proof = siblings;
  assert(receiver.on_message(wire, encode_proof(wire, sizeof(wire), proof)));
}

static void reordered_manager_transfer_is_byte_exact(bool full) {
  std::fill(flash.begin(), flash.end(), 0xA5);
  auto bytes = large_container(full);
  MotaManifest manifest;
  assert(mota_parse(bytes.data(), bytes.size(), manifest));
  OtaStoreFlashEsp32 store;
  OtaManager receiver;
  receiver.begin(manifest.target_id, send, nullptr);
  receiver.set_fetch_store(&store);
  receiver.set_wire_v2_enabled(false);
  assert(receiver.pull(manifest.merkle_root, manifest.target_id) == OtaManager::PULL_STARTED);
  for (uint32_t off = 0; off < MOTA_MFL; off += OTA_MF_FRAG) {
    ManifestMsg message{};
    memcpy(message.manifest_id, manifest.merkle_root, 4);
    message.frag_idx = off / OTA_MF_FRAG;
    message.frag_total = (MOTA_MFL + OTA_MF_FRAG - 1) / OTA_MF_FRAG;
    message.bytes = manifest.manifest_start + off;
    message.len = std::min((uint32_t)OTA_MF_FRAG, MOTA_MFL - off);
    uint8_t wire[256];
    // MANIFEST returns false to permit forwarding even when locally accepted.
    receiver.on_message(wire, encode_manifest(wire, sizeof(wire), message));
  }
  assert(receiver.fetchState() == OtaManager::FETCHING);
  // Legitimate requests may complete in any order as their adaptive window
  // grows. At width three this includes full blocks 4,3,5; a later flight
  // revisits a delta sector after completing a block across its boundary.
  for (uint32_t first = 0; first < manifest.block_count; ) {
    const uint32_t count = std::min((uint32_t)receiver.fetchPipelineWidth(),
                                    manifest.block_count - first);
    if (count > 1) deliver_block(receiver, manifest, first + 1);
    deliver_block(receiver, manifest, first);
    for (uint32_t index = 2; index < count; ++index)
      deliver_block(receiver, manifest, first + index);
    first += count;
  }
  assert(receiver.fetchState() == OtaManager::COMPLETE);
  assert(receiver.blocksHave() == manifest.block_count);
  std::vector<uint8_t> output(bytes.size());
  assert(store.read(0, output.data(), output.size()));
  assert(output == bytes);
  MotaManifest stored;
  assert(mota_parse(output.data(), output.size(), stored));
  assert(mota_check_payload(stored));
  OtaStoreFlashEsp32 resumed;
  assert(resumed.reopen());
  expect_bytes(resumed, 0, bytes.data(), bytes.size());
}

static void fresh_sector_zero_after_reset(int reset) {
  std::fill(flash.begin(), flash.end(), 0xA5);
  auto bytes = large_container(true);
  MotaManifest manifest;
  assert(mota_parse(bytes.data(), bytes.size(), manifest));
  const uint32_t payload_off = manifest.payload - bytes.data();
  OtaStoreFlashEsp32 store;
  begin_container(store, bytes, manifest);
  if (reset != 0) {
    assert(store.write(payload_off, manifest.payload, 1024));
    assert(store.write(payload_off + 4096, manifest.payload + 4096, 1024));
    store.checkpoint();
    if (reset == 1) store.clear();
    else {
      assert(store.discard());
      OtaStoreFlashEsp32 abandoned;
      assert(!abandoned.reopen());
    }
    begin_container(store, bytes, manifest);
  }
  // A zero-length write must not make even the highest sector "seen" or
  // populate the first buffer with bytes left by the previous firmware/fetch.
  assert(store.write(payload_off + 8192, nullptr, 0));
  assert(store.write(payload_off, nullptr, 0));
  assert(store.write(payload_off + 32, manifest.payload + 32, 16));
  uint8_t erased[32]; memset(erased, 0xFF, sizeof(erased));
  expect_bytes(store, payload_off, erased, sizeof(erased));
  expect_bytes(store, payload_off + 32, manifest.payload + 32, 16);
  assert(store.finalize());
  assert(memcmp(flash.data(), erased, sizeof(erased)) == 0);
  assert(memcmp(flash.data() + 32, manifest.payload + 32, 16) == 0);
}

static void continued_writes_after_finalize_are_persisted(bool checkpoint) {
  std::fill(flash.begin(), flash.end(), 0xA5);
  auto bytes = large_container(true);
  MotaManifest manifest;
  assert(mota_parse(bytes.data(), bytes.size(), manifest));
  const uint32_t payload_off = manifest.payload - bytes.data();
  OtaStoreFlashEsp32 store;
  begin_container(store, bytes, manifest);
  assert(store.write(payload_off, manifest.payload, 1024));
  assert(store.finalize());
  // Finalization closes even sector zero. Reopening that highest sector must
  // preserve the old block, and a new write must make persistence dirty again.
  assert(store.write(payload_off + 1024, manifest.payload + 1024, 1024));
  if (checkpoint) store.checkpoint();
  else assert(store.finalize());
  OtaStoreFlashEsp32 resumed;
  assert(resumed.reopen());
  expect_bytes(resumed, payload_off, manifest.payload, 2048);
}

struct TransientLeafReadStore : OtaStoreRam<32768> {
  bool reconnectable = false;
  bool fail_after_commit = false;
  mutable bool fail_next_leaf = false;
  uint32_t failed_leaf = 0;
  uint32_t fail_write_at = UINT32_MAX;
  bool canReconnect() const override { return reconnectable; }
  bool write(uint32_t off, const uint8_t* data, uint32_t len) override {
    if (off == fail_write_at) {
      fail_write_at = UINT32_MAX;
      return false;
    }
    const bool ok = OtaStoreRam<32768>::write(off, data, len);
    if (ok && fail_after_commit && off == 8 + MOTA_MFL && len == 4) {
      fail_after_commit = false;
      fail_next_leaf = true;
    }
    return ok;
  }
  bool read(uint32_t off, uint8_t* data, uint32_t len) const override {
    if (fail_next_leaf && off == 8 + MOTA_MFL + failed_leaf * 4 && len == 4) {
      fail_next_leaf = false;
      return false;
    }
    return OtaStoreRam<32768>::read(off, data, len);
  }
};

struct RequestedFlight {
  std::vector<uint32_t> blocks;
  uint32_t requests = 0;
  static bool send(void* ctx, const uint8_t* bytes, uint16_t len, bool) {
    auto& flight = *static_cast<RequestedFlight*>(ctx);
    ReqWindowMsg request{};
    if (decode_req_window(bytes, len, request)) {
      ++flight.requests;
      flight.blocks.clear();
      for (uint8_t i = 0; i < request.n_items; ++i)
        flight.blocks.push_back(request.items[i].block_idx);
    }
    return true;
  }
};

static void transient_leaf_read_is_storage_failure(int phase, bool reconnectable) {
  auto bytes = large_container(true);
  MotaManifest manifest;
  assert(mota_parse(bytes.data(), bytes.size(), manifest));
  TransientLeafReadStore store;
  store.reconnectable = reconnectable;
  store.fail_after_commit = phase == 0 || phase == 4;
  store.failed_leaf = phase == 4 ? 2 : 0; // fail after one slot was already assigned
  RequestedFlight flight;
  OtaManager receiver;
  receiver.begin(manifest.target_id, RequestedFlight::send, &flight);
  receiver.set_fetch_store(&store);
  receiver.set_wire_v2_enabled(false);
  assert(receiver.pull(manifest.merkle_root, manifest.target_id) == OtaManager::PULL_STARTED);
  for (uint32_t off = 0; off < MOTA_MFL; off += OTA_MF_FRAG) {
    ManifestMsg message{};
    memcpy(message.manifest_id, manifest.merkle_root, 4);
    message.frag_idx = off / OTA_MF_FRAG;
    message.frag_total = (MOTA_MFL + OTA_MF_FRAG - 1) / OTA_MF_FRAG;
    message.bytes = manifest.manifest_start + off;
    message.len = std::min((uint32_t)OTA_MF_FRAG, MOTA_MFL - off);
    uint8_t wire[256];
    receiver.on_message(wire, encode_manifest(wire, sizeof(wire), message));
  }
  deliver_block(receiver, manifest, 0);
  if (phase != 0 && phase != 4) {
    store.fail_next_leaf = true;
    store.failed_leaf = phase == 3 ? 1 : 0;
    uint8_t wire[256];
    if (phase == 2) {
      ProofMsg proof{};
      memcpy(proof.manifest_id, manifest.merkle_root, 4);
      assert(receiver.on_message(wire, encode_proof(wire, sizeof(wire), proof)));
    } else {
      DataMsg data{};
      memcpy(data.manifest_id, manifest.merkle_root, 4);
      data.block_idx = store.failed_leaf;
      data.data = manifest.payload + data.block_idx * 1024;
      data.data_len = OTA_FRAG_DATA;
      assert(receiver.on_message(wire, encode_data(wire, sizeof(wire), data)));
    }
  }
  assert(!store.fail_next_leaf);
  assert(receiver.fetchState() == (reconnectable ? OtaManager::PAUSED : OtaManager::FAILED));
  assert(receiver.fetchError() == OtaManager::FETCH_ERROR_STORAGE);
  assert(receiver.blocksHave() == 1);
  if (phase == 0 || phase == 4) assert(flight.requests == 1); // never send a partially scanned flight
  const auto requests = flight.requests;
  receiver.set_clock(1000000);
  receiver.loop();
  assert(flight.requests == requests);
  uint8_t leaf[4];
  assert(store.read(8 + MOTA_MFL, leaf, sizeof(leaf)));
  assert(memcmp(leaf, manifest.leaves, sizeof(leaf)) == 0);
  if (reconnectable) assert(receiver.resumeFetchAfterReconnect());
  else assert(receiver.resumeStagedExplicit(manifest.merkle_root, manifest.target_id));
  while (receiver.fetchState() == OtaManager::VERIFYING_STAGED) receiver.loop();
  assert(receiver.blocksHave() == 1);
  for (uint32_t guard = 0; guard < manifest.block_count && receiver.fetchState() == OtaManager::FETCHING; ++guard) {
    const auto requested = flight.blocks;
    assert(!requested.empty());
    for (uint32_t block : requested) {
      assert(block != 0);
      deliver_block(receiver, manifest, block);
    }
  }
  assert(receiver.fetchState() == OtaManager::COMPLETE);
  assert(receiver.blocksHave() == manifest.block_count);
  assert(memcmp(store.data(), bytes.data(), bytes.size()) == 0);
}

static void stale_flash_header_does_not_mask_selected_checkpoint(int selection) {
  std::fill(flash.begin(), flash.end(), 0xFF);
  auto older = delta_container(5547);
  auto newer = delta_container(20000);
  MotaManifest old_manifest, new_manifest;
  assert(mota_parse(newer.data(), newer.size(), new_manifest));
  if (selection == 3 || selection == 4)
    memcpy(older.data() + 8 + 20, new_manifest.merkle_root, 4);
  if (selection == 3) wr_u32le(older.data() + 8 + 3, new_manifest.target_id + 1);
  assert(mota_parse(older.data(), older.size(), old_manifest));
  uint32_t old_start, new_start;
  {
    OtaStoreFlashEsp32 store;
    begin_container(store, older, old_manifest);
    assert(store.write(0, older.data(), older.size()));
    assert(store.finalize());
    old_start = store.write_start();
  }
  {
    OtaStoreFlashEsp32 store;
    begin_container(store, newer, new_manifest);
    assert(store.write(new_manifest.payload - newer.data(), new_manifest.payload, 1024));
    assert(store.write(new_manifest.leaves - newer.data(), new_manifest.leaves, 4));
    store.checkpoint();
    new_start = store.write_start();
  }
  assert(new_start < old_start);
  if (selection == 5) flash[old_start + 8] = 0; // invalid manifest cannot mask a good candidate
  if (selection == 6) fail_read_offset = 0;    // unknown candidate after finding a match fails closed
  if (selection == 7) memset(flash.data() + old_start, 0, 4);
  const auto snapshot = flash;
  OtaStoreFlashEsp32 store;
  OtaManager receiver;
  receiver.begin(new_manifest.target_id, send, nullptr);
  receiver.set_fetch_store(&store);
  receiver.set_autofetch(OtaManager::AUTOFETCH_ANY);
  uint8_t unknown_mid[4]; memcpy(unknown_mid, new_manifest.merkle_root, 4); unknown_mid[0] ^= 0xFF;
  const bool automatic = selection == 1 || selection == 5 || selection == 7;
  const bool resumed = automatic ? receiver.resumeStaged(nullptr) :
      receiver.resumeStagedExplicit(selection == 2 ? unknown_mid : new_manifest.merkle_root,
                                   new_manifest.target_id);
  const bool expected = selection == 0 || selection == 3 || selection == 5 || selection == 7;
  assert(resumed == expected);
  assert(flash == snapshot); // selecting/rejecting never rewrites either checkpoint
  fail_read_offset = SIZE_MAX;
  if (resumed) {
    assert(store.write_start() == new_start);
    while (receiver.fetchState() == OtaManager::VERIFYING_STAGED) receiver.loop();
    assert(receiver.fetchState() == OtaManager::FETCHING);
    assert(receiver.blocksHave() == 1);
  } else {
    assert(store.staged_size() == 0);
  }
  if (selection == 3) {
    OtaStoreFlashEsp32 wildcard;
    assert(!wildcard.reopenFor(new_manifest.merkle_root, 0)); // same MID, distinct targets is ambiguous
    assert(!wildcard.reopenFor(nullptr, new_manifest.target_id)); // target alone proves no recency
  }
}

static void storage_write_failure_is_retryable(bool leaf_write, bool reconnectable) {
  auto bytes = large_container(true);
  MotaManifest manifest;
  assert(mota_parse(bytes.data(), bytes.size(), manifest));
  TransientLeafReadStore store;
  store.reconnectable = reconnectable;
  assert(store.begin(bytes.size()));
  assert(store.write(0, bytes.data(), bytes.size()));
  std::vector<uint8_t> missing((manifest.block_count - 1) * 4, 0xFF);
  assert(store.write(8 + MOTA_MFL + 4, missing.data(), missing.size()));
  RequestedFlight flight;
  OtaManager receiver;
  receiver.begin(manifest.target_id, RequestedFlight::send, &flight);
  receiver.set_fetch_store(&store);
  receiver.set_wire_v2_enabled(false);
  assert(receiver.resumeStagedExplicit(manifest.merkle_root, manifest.target_id));
  while (receiver.fetchState() == OtaManager::VERIFYING_STAGED) receiver.loop();
  assert(receiver.blocksHave() == 1);
  store.fail_write_at = leaf_write ? 8 + MOTA_MFL + 4
      : uint32_t(manifest.payload - bytes.data()) + 1024;
  deliver_block(receiver, manifest, 1);
  assert(store.fail_write_at == UINT32_MAX);
  assert(receiver.fetchState() == (reconnectable ? OtaManager::PAUSED : OtaManager::FAILED));
  assert(receiver.fetchError() == OtaManager::FETCH_ERROR_STORAGE);
  assert(receiver.blocksHave() == 1);
  if (reconnectable) assert(receiver.resumeFetchAfterReconnect());
  else assert(receiver.resumeStagedExplicit(manifest.merkle_root, manifest.target_id));
  while (receiver.fetchState() == OtaManager::VERIFYING_STAGED) receiver.loop();
  for (uint32_t guard = 0; guard < manifest.block_count && receiver.fetchState() == OtaManager::FETCHING; ++guard) {
    const auto requested = flight.blocks;
    assert(!requested.empty());
    for (uint32_t block : requested) deliver_block(receiver, manifest, block);
  }
  assert(receiver.fetchState() == OtaManager::COMPLETE);
  assert(memcmp(store.data(), bytes.data(), bytes.size()) == 0);
}

int main(int argc, char** argv) {
  const int scenario = argc > 1 ? atoi(argv[1]) : 0;
  if (scenario < 5) resume_checks_file_envelope(scenario);
  else if (scenario < 9) esp32_resume_preserves_container_trailer((scenario & 1) == 0, scenario >= 7);
  else if (scenario < 13) esp32_resume_preserves_container_trailer((scenario & 1) == 0, false,
                                                scenario < 11 ? 2278 : 4098);
  else if (scenario < 15) dirty_partial_checkpoint_can_finish(scenario == 13);
  else if (scenario < 21) esp32_revisit_preserves_acknowledged_blocks(
      scenario < 18, (scenario - 15) % 3);
  else if (scenario < 23) reordered_manager_transfer_is_byte_exact(scenario == 21);
  else if (scenario < 26) fresh_sector_zero_after_reset(scenario - 23);
  else if (scenario < 28) continued_writes_after_finalize_are_persisted(scenario == 27);
  else if (scenario < 38) transient_leaf_read_is_storage_failure((scenario - 28) % 5, scenario >= 33);
  else if (scenario < 46) stale_flash_header_does_not_mask_selected_checkpoint(scenario - 38);
  else if (scenario < 50) storage_write_failure_is_retryable((scenario & 1) != 0, scenario >= 48);
  else assert(false);
}
