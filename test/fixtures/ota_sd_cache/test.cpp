#include <SdFat.h>
#include <helpers/ota/OtaCacheSdNrf52.h>
#include <helpers/ota/OtaStoreSdNrf52.h>
#include <helpers/ota/MotaContainer.h>
#include <helpers/ota/OtaManager.h>
#include <helpers/ota/MerkleTree.h>
#include <helpers/ota/Multihash.h>
#include <helpers/ota/OtaByteIO.h>
#include "../../test_ota/mota_vectors.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <deque>

using namespace mesh::ota;

// The existing SD store owns the shared mounted volume. Replace only that
// hardware boundary: no boot authorization, flash writes, or apply call exists
// in this executable. The real archive implementation is compiled separately.
namespace mesh { namespace ota {
OtaStoreSdNrf52::OtaStoreSdNrf52() : _sd(new SdFs), _file(new FsFile) {}
OtaStoreSdNrf52::~OtaStoreSdNrf52() { delete _file; delete _sd; }
bool OtaStoreSdNrf52::mount() {
  if (_mounted) return true;
  _mounted = _sd && _sd->begin();
  if (!_mounted) fail("SD mount failed");
  return _mounted;
}
void OtaStoreSdNrf52::fail(const char* text) { snprintf(_error, sizeof(_error), "%s", text); }
bool OtaStoreSdNrf52::begin(uint32_t) { assert(false); return false; }
bool OtaStoreSdNrf52::write(uint32_t, const uint8_t*, uint32_t) { assert(false); return false; }
bool OtaStoreSdNrf52::read(uint32_t, uint8_t*, uint32_t) const { assert(false); return false; }
uint32_t OtaStoreSdNrf52::capacity() const { return 0; }
void OtaStoreSdNrf52::clear() { assert(false); }
bool OtaStoreSdNrf52::discard() { assert(false); return false; }
bool OtaStoreSdNrf52::set_meta_size(uint32_t) { assert(false); return false; }
bool OtaStoreSdNrf52::finalize() { assert(false); return false; }
void OtaStoreSdNrf52::checkpoint() { assert(false); }
bool OtaStoreSdNrf52::reopen() { assert(false); return false; }
bool OtaStoreSdNrf52::plan_layout(bool, uint32_t, uint32_t, uint32_t, bool) {
  assert(false); return false;
}
} }

static const uint8_t* const bytes = SIM_MOTA_2K;
static constexpr uint32_t length = SIM_MOTA_2K_LEN;
static const uint8_t* const mid = SIM_MOTA_2K + 8 + 20;
static std::string path(const char* ext) {
  char out[40];
  snprintf(out, sizeof(out), "/mota/%02x%02x%02x%02x.%s", mid[0], mid[1], mid[2], mid[3], ext);
  return out;
}
static void attach(OtaCacheSdNrf52& cache, OtaStoreSdNrf52& owner) {
  cache.attach(owner); cache.set_mid(mid);
}
static void capture(OtaCacheSdNrf52& cache) {
  assert(cache.begin(length));
  assert(cache.set_meta_size(8 + MOTA_MFL + SIM_MOTA_2K_BLOCKS * 4));
  assert(cache.write(0, bytes, length));
}
static void require_valid_fixture() {
  MotaManifest parsed;
  assert(mota_parse(bytes, length, parsed) && parsed.is_signed());
  assert(mota_check_root(parsed) && mota_check_payload(parsed) && mota_check_image_hash_full(parsed));
}
static void published(OtaCacheSdNrf52& cache) {
  assert(cache.count() == 1 && cache.contains(mid));
  MotaDesc d;
  assert(cache.describe(0, d) && !cache.describe(1, d));
  assert(d.target_id == SIM_TARGET_ID && d.total_size == length && d.block_count == 2);
  assert(d.block_size_log2 == 11 && d.flags == (MFLAG_FULL | MFLAG_SIGNED));
  std::vector<uint8_t> loaded(length);
  assert(cache.read(0, 0, loaded.data(), length));
  assert(!memcmp(loaded.data(), bytes, length));
  assert(!cache.read(0, length - 1, loaded.data(), 2));
  assert(!cache.read(0, UINT32_MAX, loaded.data(), 2));
  assert(!cache.read(1, 0, loaded.data(), 1));
  assert(sd_test::medium.nodes.count(path("mota")) == 1);
  assert(sd_test::medium.nodes.count(path("part")) == 0);
}

struct Link {
  OtaManager* peer = nullptr;
  std::deque<std::vector<uint8_t>> packets;
};
static bool send(void* context, const uint8_t* data, uint16_t count, bool) {
  static_cast<Link*>(context)->packets.emplace_back(data, data + count);
  return true;
}
static void deliver(Link& link) {
  while (!link.packets.empty()) {
    auto data = std::move(link.packets.front()); link.packets.pop_front();
    link.peer->on_message(data.data(), static_cast<uint16_t>(data.size()));
  }
}

static std::vector<uint8_t> distinct_image(uint8_t value) {
  std::vector<uint8_t> image(bytes, bytes + length);
  image[9] = MFLAG_FULL; // distinct unsigned fixtures, never a forged signature
  memset(image.data() + 8 + MOTA_OFF_SIGNER, 0, 32 + 64);
  MotaManifest manifest;
  assert(mota_parse(image.data(), image.size(), manifest));
  auto* payload = const_cast<uint8_t*>(manifest.payload);
  payload[0] = value;
  for (uint32_t block = 0; block < manifest.block_count; ++block) {
    const uint32_t offset = block * manifest.block_size();
    const uint32_t count = std::min(manifest.block_size(), manifest.payload_size - offset);
    merkle_leaf(const_cast<uint8_t*>(manifest.leaves) + block * 4, payload + offset, count);
  }
  merkle_root(const_cast<uint8_t*>(manifest.merkle_root), manifest.leaves, manifest.block_count);
  mh32(const_cast<uint8_t*>(manifest.image_hash), payload, manifest.payload_size);
  assert(mota_check_root(manifest) && mota_check_payload(manifest) && mota_check_image_hash_full(manifest));
  return image;
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const int scenario = atoi(argv[1]);
  sd_test::medium.reset(); require_valid_fixture();
  if (scenario == 0) {
    {
      OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner);
      capture(cache); assert(cache.count() == 0); assert(cache.finalize()); published(cache);
      assert(!cache.begin(length)); // duplicate must not truncate the good archive
    }
    OtaStoreSdNrf52 owner; OtaCacheSdNrf52 rebooted; attach(rebooted, owner);
    assert(rebooted.initialize()); published(rebooted);
  } else if (scenario == 1) {
    {
      OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner);
      assert(cache.begin(length)); assert(cache.write(0, bytes, 8 + MOTA_MFL));
      cache.checkpoint(); assert(cache.count() == 0);
    }
    OtaStoreSdNrf52 owner; OtaCacheSdNrf52 resumed; attach(resumed, owner);
    assert(resumed.initialize() && resumed.count() == 0 && resumed.reopen());
    assert(resumed.staged_size() == length);
    uint8_t header[8]; assert(resumed.read(0, header, sizeof(header)));
    assert(!memcmp(header, bytes, sizeof(header)));
    assert(resumed.write(0, bytes, length) && resumed.finalize()); published(resumed);
  } else if (scenario == 2) {
    OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner); capture(cache);
    sd_test::medium.rename_ok = false;
    assert(!cache.finalize() && cache.count() == 0);
    assert(sd_test::medium.nodes.count(path("part")) == 1);
    sd_test::medium.rename_ok = true;
    assert(cache.reopen() && cache.finalize()); published(cache);
  } else if (scenario == 3) {
    for (int kind = 0; kind < 3; ++kind) {
      sd_test::medium.reset();
      OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner); capture(cache);
      if (kind == 0) sd_test::medium.file_sync_ok = false;
      else sd_test::medium.fail_card_sync_at = kind;
      assert(!cache.finalize());
      if (kind == 2) {
        // The rename occurred, but its subsequent device sync failed. Valid
        // published data remains discoverable; do not pretend the call succeeded.
        assert(cache.count() == 1); published(cache);
      } else {
        assert(cache.count() == 0 && sd_test::medium.nodes.count(path("part")) == 1);
        sd_test::medium.file_sync_ok = true; sd_test::medium.fail_card_sync_at = 0;
        assert(cache.finalize()); published(cache);
      }
    }
  } else if (scenario == 4) {
    for (int kind = 0; kind < 5; ++kind) {
      sd_test::medium.reset();
      auto& m = sd_test::medium;
      if (kind == 0) m.connected = false;
      if (kind == 1) m.mount_ok = false;
      if (kind == 2) m.free_clusters = 1;
      if (kind == 3) m.free_clusters = m.clusters + 1;
      if (kind == 4) m.cluster_bytes = 0;
      OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner);
      assert(!cache.begin(length) && cache.staged_size() == 0 && cache.count() == 0);
      assert(!m.nodes.count(path("part")) && cache.last_error()[0]);
    }
  } else if (scenario == 5) {
    for (bool writes : {true, false}) {
      sd_test::medium.reset(); sd_test::medium.preallocate_ok = false;
      sd_test::medium.write_ok = writes;
      OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner);
      if (writes) { capture(cache); assert(cache.finalize()); published(cache); }
      else {
        assert(!cache.begin(length) && cache.staged_size() == 0);
        assert(!sd_test::medium.nodes.count(path("part")));
      }
    }
  } else if (scenario == 6) {
    for (int kind = 0; kind < 5; ++kind) {
      sd_test::medium.reset();
      OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner); capture(cache);
      auto& data = sd_test::medium.nodes[path("part")].bytes;
      if (kind == 0) data[0] ^= 1;             // bad magic
      if (kind == 1) data.back() ^= 1;         // bad trailer
      if (kind == 2) data[8 + 20] ^= 1;        // MID not selected
      if (kind == 3) data[4] ^= 1;             // declared length mismatch
      if (kind == 4) data[8 + 2] = 0;          // unknown hash algorithm
      assert(!cache.finalize() && cache.count() == 0);
      assert(sd_test::medium.nodes.count(path("part")) == 1);
      // Malformed complete files must not be listed even after reboot/rescan.
      auto saved = std::move(sd_test::medium.nodes[path("part")]);
      sd_test::medium.nodes.erase(path("part"));
      sd_test::medium.nodes[path("mota")] = std::move(saved);
      assert(cache.rescan() && cache.count() == 0 && !cache.contains(mid));
    }
  } else if (scenario == 7) {
    {
      OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner);
      capture(cache); assert(cache.finalize()); assert(cache.setAutoCaptureEnabled(false));
      assert(!cache.autoCaptureEnabled()); published(cache);
    }
    OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner);
    assert(cache.initialize() && !cache.autoCaptureEnabled()); published(cache);
    assert(cache.setAutoCaptureEnabled(true) && cache.autoCaptureEnabled());
    assert(!sd_test::medium.nodes.count("/mota/cache.off"));
  } else if (scenario == 8) {
    OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner);
    assert(cache.initialize());
    // A malformed existing final cannot suppress clean replacement. Keep the
    // bad bytes under quarantine instead of silently erasing operator evidence.
    sd_test::medium.nodes[path("mota")] = {false, {1, 2, 3}};
    capture(cache);
    assert(sd_test::medium.nodes.count(path("bad")) == 1);
    assert(cache.finalize()); published(cache);
  } else if (scenario == 9) {
    OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner);
    capture(cache); assert(cache.finalize());
    OtaManager seeder, receiver;
    OtaStoreRam<8192> destination;
    Link outbound{&receiver, {}}, inbound{&seeder, {}};
    seeder.begin(0, send, &outbound);
    receiver.begin(SIM_TARGET_ID, send, &inbound);
    receiver.set_fetch_store(&destination);
    assert(seeder.add_source(&cache) && seeder.servedCount() == 1);
    assert(receiver.pull(mid, SIM_TARGET_ID) == OtaManager::PULL_STARTED);
    for (uint32_t now = 100; now <= 300000 && receiver.fetchState() != OtaManager::COMPLETE;
         now += 100) {
      seeder.set_clock(now); receiver.set_clock(now);
      deliver(inbound); deliver(outbound);
      seeder.serviceEgress(); receiver.serviceEgress();
      seeder.loop(); receiver.loop();
      assert(receiver.fetchState() != OtaManager::FAILED);
    }
    assert(receiver.fetchState() == OtaManager::COMPLETE);
    assert(destination.staged_size() == length && !memcmp(destination.data(), bytes, length));
    MotaManifest fetched;
    assert(mota_parse(destination.data(), destination.staged_size(), fetched));
    assert(mota_check_root(fetched) && mota_check_payload(fetched));
  } else if (scenario == 10) {
    static_assert(OTA_SD_CACHE_MAX == 16, "fixture exercises the exact optional profile cap");
    sd_test::medium.nodes["/mota"] = {true, {}};
    std::string first;
    for (unsigned i = 0; i < OTA_SD_CACHE_MAX; ++i) {
      auto image = distinct_image(static_cast<uint8_t>(i));
      const uint8_t* image_mid = image.data() + 8 + 20;
      char name[40]; snprintf(name, sizeof(name), "/mota/%02x%02x%02x%02x.mota",
                            image_mid[0], image_mid[1], image_mid[2], image_mid[3]);
      if (i == 0) first = name;
      sd_test::medium.nodes[name] = {false, std::move(image)};
    }
    // A previously interrupted seventeenth capture must remain untouched when
    // no advertised slot is available, rather than being deleted by begin().
    sd_test::medium.nodes[path("part")] = {false, std::vector<uint8_t>(bytes, bytes + length)};
    OtaStoreSdNrf52 owner; OtaCacheSdNrf52 cache; attach(cache, owner);
    assert(cache.initialize() && cache.count() == OTA_SD_CACHE_MAX);
    const auto original = sd_test::medium.nodes[path("part")].bytes;
    const auto count = sd_test::medium.nodes.size();
    assert(!cache.begin(length));
    assert(strstr(cache.last_error(), "entry limit"));
    assert(sd_test::medium.nodes.size() == count);
    assert(sd_test::medium.nodes[path("part")].bytes == original);
    // Once an operator makes room, the existing partial can be resumed and
    // published without re-creating or truncating it.
    sd_test::medium.nodes.erase(first);
    assert(cache.rescan() && cache.count() == OTA_SD_CACHE_MAX - 1);
    assert(cache.reopen() && cache.finalize() && cache.count() == OTA_SD_CACHE_MAX);
    assert(cache.contains(mid) && !sd_test::medium.nodes.count(path("part")));
  } else return 2;
  puts("PASS: production SD archive boundary");
}
