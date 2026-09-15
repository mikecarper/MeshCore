#include <helpers/ota/OtaManager.h>
#include <helpers/ota/OtaStoreFlashEsp32.h>
#include <helpers/ota/OtaByteIO.h>
#include "../../test_ota/mota_vectors.h"
#include <cassert>
#include <cstring>
#include <vector>

using namespace mesh::ota;
static std::vector<uint8_t> flash(65536, 0xFF);
static const esp_partition_t partition{65536};
const esp_partition_t* esp_ota_get_next_update_partition(const esp_partition_t*) {
  return &partition;
}
esp_err_t esp_partition_read(const esp_partition_t*, size_t off, void* data, size_t n) {
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

int main(int argc, char** argv) {
  const int scenario = argc > 1 ? atoi(argv[1]) : 0;
  if (scenario < 5) resume_checks_file_envelope(scenario);
  else if (scenario < 9) esp32_resume_preserves_container_trailer((scenario & 1) == 0, scenario >= 7);
  else if (scenario < 13) esp32_resume_preserves_container_trailer((scenario & 1) == 0, false,
                                                scenario < 11 ? 2278 : 4098);
  else dirty_partial_checkpoint_can_finish(scenario == 13);
}
