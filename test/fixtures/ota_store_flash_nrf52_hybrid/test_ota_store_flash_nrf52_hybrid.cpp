#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#if defined(_WIN32)
#include <windows.h>
#else
#include <sys/mman.h>
#endif

// Exercise the production nRF52 store, including its fixed-address flash and
// reset-retained SRAM bridge, with process-local mappings at the target
// addresses. Compile-time test overrides model the application linker bounds
// without relying on platform-specific absolute linker symbols.

static bool g_fail_next_flash_write = false;
static uint32_t g_flash_writes = 0;

extern "C" int flash_nrf5x_write(uint32_t address, const void* data,
                                  uint32_t length) {
  ++g_flash_writes;
  if (g_fail_next_flash_write) {
    g_fail_next_flash_write = false;
    return -1;
  }
  std::memcpy(reinterpret_cast<void*>(static_cast<uintptr_t>(address)), data,
              length);
  return static_cast<int>(length);
}

extern "C" void flash_nrf5x_flush(void) {}

#include "../../../src/helpers/ota/OtaStoreFlashNrf52.cpp"

namespace mesh {
namespace ota {

bool ota_self_firmware(SelfFwInfo& out) {
  out = SelfFwInfo();
  out.valid = true;
  out.image_len = 0x00080000u;
  return true;
}

bool mota_parse_manifest(const uint8_t*, uint32_t, MotaManifest&) {
  return false;
}

}  // namespace ota
}  // namespace mesh

#define CHECK(condition) do { if (!(condition)) { \
  std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #condition); \
  std::exit(1); \
} } while (0)

static constexpr uintptr_t FLASH_MAP_START = 0x00020000u;
static constexpr size_t FLASH_MAP_SIZE = 0x000E0000u;
static constexpr uintptr_t RAM_MAP_START = 0x20000000u;
static constexpr size_t RAM_MAP_SIZE = 0x00040000u;

static void map_target_region(uintptr_t address, size_t length) {
#if defined(_WIN32)
  void* const mapped = VirtualAlloc(reinterpret_cast<void*>(address), length,
                                    MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
#else
  void* const mapped = mmap(reinterpret_cast<void*>(address), length,
                            PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
  if (mapped == MAP_FAILED) {
    std::perror("mmap");
    std::exit(1);
  }
#endif
  CHECK(mapped == reinterpret_cast<void*>(address));
}

static void install_bootloader_capabilities() {
  using namespace mesh::ota;
  auto* const bootloader = reinterpret_cast<uint8_t*>(
      static_cast<uintptr_t>(MOTA_NRF52_BL_START));
  std::memset(bootloader, 0xFF,
              MOTA_NRF52_BL_END - MOTA_NRF52_BL_START);

  std::memcpy(bootloader, OTA_BL_MAGIC, sizeof(OTA_BL_MAGIC));
  std::memset(bootloader + 8, 0, 8);
  bootloader[8] = 3;
  bootloader[10] = 1u << 2;
  bootloader[12] = OTA_BL_STORAGE_STAGE_CEILING;

  uint8_t* const ram_cap = bootloader + 32;
  std::memcpy(ram_cap, MOTA_RAM_CAP_MAGIC, sizeof(MOTA_RAM_CAP_MAGIC));
  mota_hybrid_wr16(ram_cap + 8, MOTA_RAM_CAP_ABI);
  mota_hybrid_wr16(ram_cap + 10, MOTA_HYBRID_AUTH_LEN);
  mota_hybrid_wr32(ram_cap + 12, MOTA_NRF52_HYBRID_RAM_SIZE);
}

static std::vector<uint8_t> patterned_container(uint32_t total) {
  using namespace mesh::ota;
  std::vector<uint8_t> bytes(total);
  for (uint32_t i = 0; i < total; ++i)
    bytes[i] = static_cast<uint8_t>((i * 37u + 11u) & 0xFFu);
  std::memcpy(bytes.data(), MOTA_MAGIC, sizeof(MOTA_MAGIC));
  bytes[4] = static_cast<uint8_t>(total);
  bytes[5] = static_cast<uint8_t>(total >> 8);
  bytes[6] = static_cast<uint8_t>(total >> 16);
  bytes[7] = static_cast<uint8_t>(total >> 24);
  return bytes;
}

static void reset_target_memory() {
  std::memset(reinterpret_cast<void*>(FLASH_MAP_START), 0xFF, FLASH_MAP_SIZE);
  std::memset(reinterpret_cast<void*>(RAM_MAP_START), 0, RAM_MAP_SIZE);
  install_bootloader_capabilities();
  g_fail_next_flash_write = false;
  g_flash_writes = 0;
}

static void full_hybrid_lifecycle_crosses_the_backing_boundary() {
  using namespace mesh::ota;
  reset_target_memory();
  const uint32_t total = MOTA_NRF52_HYBRID_RAM_SIZE +
                         MOTA_NRF52_FLASH_PAGE + 123u;
  const uint32_t payload_offset = 205u;
  const uint32_t payload_size = total - payload_offset - 5u;
  const auto bytes = patterned_container(total);
  uint8_t hash[32];
  for (uint8_t i = 0; i < sizeof(hash); ++i) hash[i] = i;

  OtaStoreFlashNrf52 store;
  CHECK(store.plan_layout(false, 0x81000u, payload_offset, payload_size, false));
  CHECK(store.begin(total));
  CHECK(store.is_hybrid());
  CHECK(store.write_start() == 0x000EB000u);
  CHECK(store.flash_len() == 2u * MOTA_NRF52_FLASH_PAGE);
  CHECK(store.ram_len() == total - store.flash_len());

  CHECK(store.write(0, bytes.data(), total));
  std::vector<uint8_t> readback(total);
  CHECK(store.read(0, readback.data(), total));
  CHECK(readback == bytes);
  CHECK(store.finalize());
  CHECK(store.data() == nullptr);
  CHECK(store.read(0, readback.data(), total));
  CHECK(readback == bytes);

  auto* const auth = reinterpret_cast<uint8_t*>(MOTA_HYBRID_AUTH_ADDR);
  for (uint32_t i = 0; i < MOTA_HYBRID_AUTH_LEN; ++i) CHECK(auth[i] == 0);
  CHECK(store.approve_for_application(hash));
  CHECK(std::memcmp(reinterpret_cast<void*>(store.write_start() + 8u +
                         MOTA_OFF_APPROVAL), APPROVAL_YES,
                    sizeof(APPROVAL_YES)) == 0);
  for (uint32_t i = 0; i < MOTA_HYBRID_AUTH_LEN; ++i) CHECK(auth[i] == 0);
  CHECK(store.publish_hybrid_handoff());
  CHECK(mota_hybrid_auth_valid(auth));
  CHECK(mota_hybrid_rd32(auth + 16u) == total);
  CHECK(mota_hybrid_rd32(auth + 20u) == store.write_start());
  CHECK(mota_hybrid_rd32(auth + 24u) == store.flash_len());
  CHECK(mota_hybrid_rd32(auth + 28u) == store.ram_len());
  CHECK(std::memcmp(auth + 32u, hash, sizeof(hash)) == 0);
}

static void split_trailer_is_committed_to_flash_and_ram() {
  using namespace mesh::ota;
  reset_target_memory();
  const uint32_t total = MOTA_NRF52_FLASH_PAGE + 1u;
  const uint32_t payload_offset = 205u;
  const auto bytes = patterned_container(total);
  uint8_t hash[32] = {0xA5};

  OtaStoreFlashNrf52 store;
  CHECK(store.plan_layout(false, 0x80000u, payload_offset,
                          total - payload_offset - 5u, false));
  CHECK(store.begin(total));
  CHECK(store.flash_len() == MOTA_NRF52_FLASH_PAGE);
  CHECK(store.ram_len() == 1u);
  CHECK(store.write(0, bytes.data(), total));
  CHECK(store.finalize());

  const uint32_t trailer = total - 5u;
  CHECK(std::memcmp(reinterpret_cast<void*>(store.write_start() + trailer),
                    bytes.data() + trailer, 4u) == 0);
  CHECK(*reinterpret_cast<uint8_t*>(MOTA_NRF52_HYBRID_RAM_START) ==
        bytes.back());
  CHECK(store.approve_for_application(hash));
  CHECK(store.publish_hybrid_handoff());
}

static void failed_persistence_never_publishes_a_handoff() {
  using namespace mesh::ota;
  reset_target_memory();
  const uint32_t total = MOTA_NRF52_FLASH_PAGE + 1u;
  const uint32_t payload_offset = 205u;
  const auto bytes = patterned_container(total);
  uint8_t hash[32] = {0x33};

  OtaStoreFlashNrf52 store;
  CHECK(store.plan_layout(false, 0x80000u, payload_offset,
                          total - payload_offset - 5u, false));
  CHECK(store.begin(total));
  CHECK(store.write(0, bytes.data(), total));
  g_fail_next_flash_write = true;
  CHECK(!store.finalize());
  CHECK(!store.approve_for_application(hash));
  CHECK(!store.publish_hybrid_handoff());
  const auto* const auth = reinterpret_cast<const uint8_t*>(
      MOTA_HYBRID_AUTH_ADDR);
  for (uint32_t i = 0; i < MOTA_HYBRID_AUTH_LEN; ++i) CHECK(auth[i] == 0);
}

static void hybrid_state_is_one_shot_and_not_reopened_after_restart() {
  using namespace mesh::ota;
  reset_target_memory();
  const uint32_t total = MOTA_NRF52_FLASH_PAGE + 1u;
  const uint32_t payload_offset = 205u;
  const auto bytes = patterned_container(total);
  uint8_t hash[32] = {0x77};

  OtaStoreFlashNrf52 store;
  CHECK(store.plan_layout(false, 0x80000u, payload_offset,
                          total - payload_offset - 5u, false));
  CHECK(store.begin(total));
  CHECK(store.write(0, bytes.data(), total));
  CHECK(store.approve_for_application(hash));
  CHECK(store.publish_hybrid_handoff());
  CHECK(mota_hybrid_auth_valid(reinterpret_cast<const uint8_t*>(
      MOTA_HYBRID_AUTH_ADDR)));

  OtaStoreFlashNrf52 after_restart;
  CHECK(!mota_hybrid_auth_valid(reinterpret_cast<const uint8_t*>(
      MOTA_HYBRID_AUTH_ADDR)));
  CHECK(!after_restart.reopen());
  CHECK(after_restart.staged_size() == 0u);
}

int main() {
  map_target_region(FLASH_MAP_START, FLASH_MAP_SIZE);
  map_target_region(RAM_MAP_START, RAM_MAP_SIZE);
  full_hybrid_lifecycle_crosses_the_backing_boundary();
  std::puts("PASS: hybrid boundary lifecycle");
  split_trailer_is_committed_to_flash_and_ram();
  std::puts("PASS: split trailer");
  failed_persistence_never_publishes_a_handoff();
  std::puts("PASS: persistence failure stays closed");
  hybrid_state_is_one_shot_and_not_reopened_after_restart();
  std::puts("PASS: restart cannot reopen volatile suffix");
  std::puts("4 OtaStoreFlashNrf52 hybrid lifecycle checks passed");
  return 0;
}
