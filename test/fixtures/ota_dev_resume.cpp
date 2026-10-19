extern "C" void meshRegulatorCleanReset() __attribute__((weak));
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <vector>
#if defined(_WIN32)
#include <windows.h>
#else
#include <sys/mman.h>
#endif
#include <helpers/ota/OtaManager.h>
#include <Utils.h>

static bool g_fail_next_flash_write = false;
static uint32_t g_flash_writes = 0;
extern "C" int flash_nrf5x_write(uint32_t address, const void* data, uint32_t length) {
  ++g_flash_writes;
  if (g_fail_next_flash_write) { g_fail_next_flash_write = false; return -1; }
  memcpy(reinterpret_cast<void*>(uintptr_t(address)), data, length);
  return int(length);
}
extern "C" void flash_nrf5x_flush(void) {}

#include "@STORE_CPP@"

#define CHECK(condition) do { if (!(condition)) { \
  fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #condition); exit(1); \
} } while (0)

using namespace mesh::ota;
@MAPPINGS@

namespace mesh { namespace ota {
bool ota_self_firmware(SelfFwInfo& out) {
  out = SelfFwInfo(); out.valid = true; out.image_len = 0x80000u; return true;
}

struct CountingStore : OtaStoreFlashNrf52 {
  mutable unsigned reads = 0;
  unsigned reopens = 0;
  bool read(uint32_t offset, uint8_t* data, uint32_t length) const override {
    ++reads; return OtaStoreFlashNrf52::read(offset, data, length);
  }
  bool reopenFor(const uint8_t* mid, uint32_t target) override {
    ++reopens; return OtaStoreFlashNrf52::reopenFor(mid, target);
  }
};

struct Context {
  CountingStore fetch_store;
  OtaManager manager;
  OtaManager sender;
  bool apply_pending = false, bootloader_apply_pending = false;
  uint32_t apply_at = 0x12345678u, apply_hard = 0x23456789u;
};
@HELPERS@
static bool resume_cli(const char* d, char* reply, Context& c) {
  @RESUME@
  return true;
}

// The successful signature/allowlist approval boundary is seeded through the
// real store API. Only physical reset/reason/register operations are adapted.
static OtaStoreFlashNrf52* g_nrf52_apply_store = nullptr;
#if defined(OTA_RAK_AUTO_STORE)
static uint8_t g_nrf52_external_handoff = GPREGRET2_OTA_STAGE_QSPI;
#endif
static uint8_t request_marker = 0, source_marker = 0;
static unsigned reset_count = 0;
static bool ota_nrf52_clear_reset_reasons() { return true; }
static void ota_nrf52_set_reset_handoff(uint8_t request, uint8_t source) {
  request_marker = request; source_marker = source;
}
static void NVIC_SystemReset() { ++reset_count; }
@REBOOT@
} }

struct Packet { OtaManager* destination; std::vector<uint8_t> bytes; };
static std::deque<Packet> packets;
static bool send_packet(void* context, const uint8_t* bytes, uint16_t len, bool) {
  packets.push_back({static_cast<OtaManager*>(context), {bytes, bytes + len}});
  return true;
}

static std::vector<uint8_t> container(uint32_t total) {
  const uint32_t blocks = (total - 210u + 1027u) / 1028u;
  const uint32_t payload_size = total - 210u - blocks * 4u;
  CHECK((payload_size + 1023u) / 1024u == blocks);
  std::vector<uint8_t> bytes(total, 0);
  memcpy(bytes.data(), MOTA_MAGIC, sizeof(MOTA_MAGIC));
  wr_u32le(bytes.data() + 4, total);
  bytes[8] = MOTA_APP_FORMAT_VER;
  bytes[8 + 2] = HASH_ALGO_SHA256;
  wr_u32le(bytes.data() + 8 + 3, 0x12345678u);
  wr_u32le(bytes.data() + 8 + 7, 0x01170109u);
  wr_u32le(bytes.data() + 8 + 11, 0x81000u);
  wr_u32le(bytes.data() + 8 + 15, payload_size);
  bytes[8 + 19] = 10;
  bytes[8 + 56] = CODEC_DETOOLS_INPLACE;
  uint8_t* const leaves = bytes.data() + 8u + MOTA_MFL;
  uint8_t* const payload = leaves + blocks * 4u;
  for (uint32_t i = 0; i < payload_size; ++i) payload[i] = uint8_t(i * 37u + 11u);
  for (uint32_t i = 0; i < blocks; ++i)
    merkle_leaf(leaves + i * 4u, payload + i * 1024u,
                std::min(1024u, payload_size - i * 1024u));
  merkle_root(bytes.data() + 8 + 20, leaves, blocks);
  memcpy(bytes.data() + total - 5, MOTA_TRAILER, 5);
  MotaManifest manifest;
  CHECK(mota_parse(bytes.data(), bytes.size(), manifest));
  CHECK(mota_check_root(manifest) && mota_check_payload(manifest));
  return bytes;
}

static void fetch_complete(Context& c, const std::vector<uint8_t>& bytes) {
  OtaManager& sender = c.sender;
  packets.clear();
  sender.begin(0, send_packet, &c.manager);
  c.manager.begin(0x12345678u, send_packet, &sender);
  c.manager.set_apply_codec(CODEC_DETOOLS_INPLACE);
  c.manager.set_fetch_store(&c.fetch_store);
  CHECK(sender.serve(bytes.data(), bytes.size()));
  CHECK(c.manager.pull(bytes.data() + 8 + 20, 0x12345678u) == OtaManager::PULL_STARTED);
  for (uint32_t tick = 0; tick < 200000 && c.manager.fetchState() != OtaManager::COMPLETE; ++tick) {
    const uint32_t now = tick * 100u;
    c.manager.set_clock(now); sender.set_clock(now);
    c.manager.loop(); sender.loop();
    c.manager.serviceEgress(); sender.serviceEgress();
    if (!packets.empty()) {
      auto packet = std::move(packets.front()); packets.pop_front();
      packet.destination->on_message(packet.bytes.data(), packet.bytes.size());
    }
  }
  packets.clear();
  CHECK(c.manager.fetchState() == OtaManager::COMPLETE);
  CHECK(c.manager.blocksHave() == c.manager.blocksTotal());
  CHECK(c.fetch_store.staged_size() == bytes.size());
  // The sender remains owned by Context, so callback context stays live across
  // the CLI resume and subsequent pure-flash verification controls.
}

static void approve(Context& c) {
  uint8_t hash[32];
  for (unsigned i = 0; i < sizeof(hash); ++i) hash[i] = uint8_t(i + 1u);
  CHECK(c.fetch_store.approve_for_application(hash));
  g_nrf52_apply_store = &c.fetch_store;
}

static void arm(Context& c, unsigned flags) {
  c.apply_pending = (flags & 1u) != 0;
  c.bootloader_apply_pending = (flags & 2u) != 0;
}

static void baseline_loss() {
  reset_target_memory();
  Context c;
  const auto bytes = container(MOTA_NRF52_HYBRID_RAM_SIZE + MOTA_NRF52_FLASH_PAGE + 123u);
  fetch_complete(c, bytes);
  CHECK(c.fetch_store.is_hybrid());
  approve(c); arm(c, 1);
  ota_reboot_to_apply();
  CHECK(request_marker == GPREGRET_OTA_APPLY && source_marker == GPREGRET2_OTA_STAGE_HYBRID);
  CHECK(mota_hybrid_auth_valid(reinterpret_cast<const uint8_t*>(MOTA_HYBRID_AUTH_ADDR)));
  char command[32], reply[160];
  char hex[9]; mesh::Utils::toHex(hex, bytes.data() + 8 + 20, 4);
  snprintf(command, sizeof(command), "resume %s", hex);
  const unsigned reopens = c.fetch_store.reopens;
  CHECK(resume_cli(command, reply, c));
  CHECK(strncmp(reply, "ERR resume:", 11) == 0);
  CHECK(c.fetch_store.reopens == reopens + 1);
  CHECK(c.apply_pending && !c.bootloader_apply_pending);
  CHECK(g_nrf52_apply_store == &c.fetch_store);
  CHECK(!c.fetch_store.is_hybrid() && c.fetch_store.staged_size() == 0);
  CHECK(!c.fetch_store.publish_hybrid_handoff());
  ota_reboot_to_apply();
  CHECK(request_marker == GPREGRET_OTA_APPLY && source_marker == GPREGRET2_OTA_STAGE_EXPANDED);
  printf("BASELINE CONFIRMED: real COMPLETE hybrid -> failed resume -> hybrid/descriptor lost; armed pointer retained; reboot source A6 -> ED\n");
}

static unsigned guarded_cases() {
  unsigned cases = 0;
  for (uint32_t total : {3072u, 4097u, MOTA_NRF52_HYBRID_RAM_SIZE + MOTA_NRF52_FLASH_PAGE + 123u})
  for (unsigned flags : {1u, 2u, 3u})
  for (unsigned argument = 0; argument < 5; ++argument) {
    reset_target_memory();
    Context c;
    const auto bytes = container(total);
    fetch_complete(c, bytes); approve(c); arm(c, flags);
    const bool hybrid = c.fetch_store.is_hybrid();
    const uint32_t start = c.fetch_store.write_start(), flash_len = c.fetch_store.flash_len();
    const uint32_t ram_len = c.fetch_store.ram_len(), have = c.manager.blocksHave();
    const uint32_t blocks = c.manager.blocksTotal(), sent = c.manager.packetsSent();
    const unsigned reopens = c.fetch_store.reopens, reads = c.fetch_store.reads, writes = g_flash_writes;
    if (hybrid) CHECK(c.fetch_store.publish_hybrid_handoff());
    std::vector<uint8_t> auth(MOTA_HYBRID_AUTH_LEN);
    memcpy(auth.data(), reinterpret_cast<const void*>(MOTA_HYBRID_AUTH_ADDR), auth.size());
    std::vector<uint8_t> backing(total);
    CHECK(c.fetch_store.OtaStoreFlashNrf52::read(0, backing.data(), total));
    char command[48], reply[160], hex[9];
    mesh::Utils::toHex(hex, bytes.data() + 8 + 20, 4);
    if (argument == 0) snprintf(command, sizeof(command), "resume %s", hex);
    else strcpy(command, argument == 1 ? "resume" : argument == 2 ? "resume malformed" : argument == 3 ? "resume 00112233" : "resume 00112233 trailing");
    CHECK(resume_cli(command, reply, c));
    CHECK(strcmp(reply, "ERR another update is already armed") == 0);
    CHECK(c.fetch_store.reopens == reopens && c.fetch_store.reads == reads && g_flash_writes == writes);
    CHECK(c.apply_pending == bool(flags & 1u) && c.bootloader_apply_pending == bool(flags & 2u));
    CHECK(c.apply_at == 0x12345678u && c.apply_hard == 0x23456789u);
    CHECK(c.manager.fetchState() == OtaManager::COMPLETE && c.manager.blocksHave() == have);
    CHECK(c.manager.blocksTotal() == blocks && c.manager.packetsSent() == sent);
    CHECK(memcmp(c.manager.fetchManifestId(), bytes.data() + 8 + 20, 4) == 0);
    CHECK(c.fetch_store.is_hybrid() == hybrid && c.fetch_store.staged_size() == total);
    CHECK(c.fetch_store.write_start() == start && c.fetch_store.flash_len() == flash_len && c.fetch_store.ram_len() == ram_len);
    CHECK(g_nrf52_apply_store == &c.fetch_store);
    std::vector<uint8_t> after(total);
    CHECK(c.fetch_store.OtaStoreFlashNrf52::read(0, after.data(), total));
    CHECK(after == backing);
    CHECK(memcmp(auth.data(), reinterpret_cast<const void*>(MOTA_HYBRID_AUTH_ADDR), auth.size()) == 0);
    if (hybrid) CHECK(c.fetch_store.publish_hybrid_handoff());
    ota_reboot_to_apply();
    CHECK(request_marker == GPREGRET_OTA_APPLY);
    CHECK(source_marker == (hybrid ? GPREGRET2_OTA_STAGE_HYBRID : GPREGRET2_OTA_STAGE_EXPANDED));
    ++cases;
  }
  return cases;
}

static unsigned healthy_cases() {
  unsigned cases = 0;
  for (uint32_t total : {3072u, 4097u})
  for (bool no_argument : {false, true}) {
    reset_target_memory();
    Context c;
    const auto bytes = container(total);
    fetch_complete(c, bytes);
    char command[32], reply[160], hex[9];
    mesh::Utils::toHex(hex, bytes.data() + 8 + 20, 4);
    if (no_argument) strcpy(command, "resume");
    else snprintf(command, sizeof(command), "resume %s", hex);
    const unsigned reopens = c.fetch_store.reopens;
    CHECK(resume_cli(command, reply, c));
    CHECK(c.fetch_store.reopens == reopens + 1);
    if (total <= MOTA_NRF52_FLASH_PAGE) {
      CHECK(strncmp(reply, "OK resume:", 10) == 0);
      CHECK(c.manager.fetchState() == OtaManager::VERIFYING_STAGED);
      for (unsigned guard = 0; guard < 100 && c.manager.fetchState() == OtaManager::VERIFYING_STAGED; ++guard) c.manager.loop();
      CHECK(c.manager.fetchState() == OtaManager::COMPLETE);
      CHECK(c.manager.blocksHave() == c.manager.blocksTotal());
    } else {
      CHECK(strncmp(reply, "ERR resume:", 11) == 0);
      CHECK(!c.fetch_store.is_hybrid() && c.fetch_store.staged_size() == 0);
    }
    CHECK(!c.apply_pending && !c.bootloader_apply_pending);
    ++cases;
  }
  for (const char* command : {"resume", "resume malformed", "resume 00112233 trailing"}) {
    reset_target_memory(); Context c;
    c.manager.begin(0x12345678u, nullptr, nullptr); c.manager.set_fetch_store(&c.fetch_store);
    char reply[160]; CHECK(resume_cli(command, reply, c));
    CHECK(strncmp(reply, "ERR usage:", 10) == 0 && c.fetch_store.reopens == 0);
    ++cases;
  }
  return cases;
}

int main(int argc, char** argv) {
  map_target_region(FLASH_MAP_START, FLASH_MAP_SIZE);
  map_target_region(RAM_MAP_START, RAM_MAP_SIZE);
  if (argc > 1 && strcmp(argv[1], "baseline") == 0) baseline_loss();
  else printf("PASS: %u armed cases; %u healthy controls\n", guarded_cases(), healthy_cases());
}
