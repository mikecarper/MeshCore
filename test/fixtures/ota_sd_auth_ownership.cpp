#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include "helpers/BorrowableFrameBuffer.h"
#include "helpers/ota/OtaSdAuthRecord.h"

#define STRINGIFY_INNER(value) #value
#define STRINGIFY(value) STRINGIFY_INNER(value)
#ifdef TEST_AUTH_START
asm(".global __mota_sd_auth_start__\n.set __mota_sd_auth_start__, " STRINGIFY(TEST_AUTH_START));
asm(".global __mota_sd_auth_end__\n.set __mota_sd_auth_end__, " STRINGIFY(TEST_AUTH_END));
#endif
// Simulate an ordinary linker with a large .svc_data/.fs_data prefix. Its
// initialized .data starts after the auth slot, but the slot is still owned by
// the application. A __data_start__ check alone would incorrectly allow writes.
asm(".global __data_start__\n.set __data_start__, 0x20006100");

static unsigned barriers;
[[maybe_unused]] static void __DMB() { ++barriers; }
[[maybe_unused]] static void __DSB() { ++barriers; }

namespace mesh {
namespace ota {
class OtaStoreSdNrf52 {
public:
  OtaStoreSdNrf52();
};
#include "production_auth.inc"
} // namespace ota
} // namespace mesh

struct Frame { uint8_t bytes[96]; };
struct Workspace {
  mesh::ota::OtaStoreSdNrf52 store;
  uint32_t payload = 0x12345678;
};

int main() {
  void* const mapped = mmap(reinterpret_cast<void*>(0x20006000), 4096,
      PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
  assert(mapped == reinterpret_cast<void*>(0x20006000));
  uint8_t* const page = static_cast<uint8_t*>(mapped);
  uint8_t before[4096];
  memset(page, 0xA7, 4096);
  memcpy(before, page, sizeof(before));
  mesh::BorrowableFrameBuffer<Frame, 256, 128, Workspace> queue;
  int head = 39;
  for (size_t index = 0; index < queue.capacity(); ++index)
    memset(queue.at(index).bytes, static_cast<int>(index), sizeof(Frame));
  for (unsigned cycle = 0; cycle != 64; ++cycle) {
    Workspace* const workspace = queue.acquire(97, head);
    assert(workspace && workspace->payload == 0x12345678 && head == 0);
    assert(queue.acquire(97, head) == workspace);
    assert(queue.capacity() == 128);
    for (size_t index = 0; index < 97; ++index) {
      const uint8_t expected = static_cast<uint8_t>(index + 39);
      for (uint8_t byte : queue.at(index).bytes) assert(byte == expected);
    }
    queue.release(head);
    queue.release(head);
    assert(queue.capacity() == 256);
  }
  if (EXPECT_OWNS) {
    for (unsigned index = 8; index < 80; ++index) assert(page[index] == 0);
    assert(barriers == 128);
  } else {
    assert(memcmp(page, before, sizeof(before)) == 0);
    assert(barriers == 0);
  }
#if !defined(OTA_SEEDER_ONLY)
  uint8_t record[mesh::ota::MOTA_SD_AUTH_LEN];
  for (size_t index = 0; index < sizeof(record); ++index)
    record[index] = static_cast<uint8_t>(index + 1);
  memcpy(before, page, sizeof(before));
  assert(mesh::ota::publish_sd_auth_record(record) == static_cast<bool>(EXPECT_OWNS));
  if (EXPECT_OWNS) {
    assert(memcmp(page + 8, record, sizeof(record)) == 0);
    mesh::ota::clear_sd_auth_record();
    for (unsigned index = 8; index < 80; ++index) assert(page[index] == 0);
  } else {
    assert(memcmp(page, before, sizeof(before)) == 0);
  }
#endif
  // Neither successful receivers nor failed/absent ownership touch either side.
  for (unsigned index = 0; index < 8; ++index) assert(page[index] == 0xA7);
  for (unsigned index = 80; index < 4096; ++index) assert(page[index] == 0xA7);
  assert(munmap(mapped, 4096) == 0);
  puts("SD auth ownership and 64 queue lifetimes passed");
}
