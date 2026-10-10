#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "helpers/ota/OtaTargets.h"

namespace mesh {
struct MainBoard {};
struct Utils {
  static void toHex(char* out, const uint8_t* bytes, size_t length) {
    static const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < length; ++i) {
      out[2 * i] = hex[bytes[i] >> 4]; out[2 * i + 1] = hex[bytes[i] & 15];
    }
    out[2 * length] = 0;
  }
};
namespace ota {
constexpr unsigned MFLAG_BOOTLOADER = 8, CODEC_FULL = 0, MOTA_FORMAT_VER = 2;
constexpr float OTA_SPEED_DEFAULT = 1.0f;
struct FwVersion {
  unsigned major, minor, patch, prerelease;
  static FwVersion unpack(uint32_t value) {
    return {value >> 24, (value >> 16) & 255, (value >> 8) & 255, value & 255};
  }
};
struct OtaBlCaps {
  bool present = true;
  uint8_t apply_abi = MOTA_FORMAT_VER;
  uint16_t codec_mask = 1;
};
struct SelfFwInfo {
  bool valid = true;
  uint8_t body_hash[8] = {0};
  uint32_t image_len = 1024;
};
struct OtaManager {
  enum FetchState { IDLE, WANT_MANIFEST, WANT_LEAVES, VERIFYING_STAGED, FETCHING, COMPLETE, PAUSED, FAILED };
  enum FetchError { FETCH_ERROR_MANIFEST, FETCH_ERROR_HASH_ALGO, FETCH_ERROR_VERSION,
    FETCH_ERROR_CODEC, FETCH_ERROR_GEOMETRY, FETCH_ERROR_TOO_LARGE, FETCH_ERROR_STORAGE,
    FETCH_ERROR_INTEGRITY, FETCH_ERROR_MANIFEST_TIMEOUT, FETCH_ERROR_LEAVES_TIMEOUT };
  struct CatRow {
    uint8_t mid[4] = {1, 2, 3, 4};
    uint32_t target_id = 0, fw_version = 0x01020300, last_ms = 9000;
    uint8_t flags = 0, codec = CODEC_FULL, n_seeders = 2;
  };
  CatRow rows[4];
  unsigned count = 0, queries = 0, clock_calls = 0, speed_calls = 0;
  uint32_t target_id = 0;
  FetchState state = IDLE;
  bool codec_supported = true;
  uint8_t served = 3;
  float adaptivePacketSpeed() const { return 2.5f; }
  void set_clock(uint32_t value) { assert(value == 10000); ++clock_calls; }
  void set_speed(float value) { assert(value == 1.25f); ++speed_calls; }
  void queryAll() { ++queries; }
  uint16_t catalogCount() const { return count; }
  unsigned sourceCount() const { return 2; }
  FetchState fetchState() const { return state; }
  const uint8_t* fetchManifestId() const { return rows[0].mid; }
  uint32_t target() const { return target_id; }
  const CatRow* catalogRow(uint16_t index) const { return index < count ? &rows[index] : nullptr; }
  bool codecOk(uint8_t) const { return codec_supported; }
  unsigned servedCount() const { return served; }
  void servedDigest(uint8_t* digest) const { memcpy(digest, rows[0].mid, 4); }
  unsigned blocksHave() const { return 1; }
  unsigned blocksTotal() const { return 2; }
  FetchError fetchError() const { return FETCH_ERROR_STORAGE; }
  bool fetched_is_bootloader() const { return false; }
};
struct OtaContext {
  OtaManager manager;
  OtaBlCaps caps;
  bool folder_active = false, serving = true;
  const char* folder_dest_info = "test-folder";
  uint32_t session_started_ms = 1000;
  char hw_id[2] = "r";
  struct { unsigned count() const { return 1; } } allow;
  const OtaBlCaps& bootloaderAppCaps() const { return caps; }
};
static OtaContext context;
static bool available = true, speed_consumed = false;
static unsigned acquisitions = 0, speed_commands = 0, control_commands = 0;
static float last_adaptive;
static const OtaContext* ota_context_if_active() { return available ? &context : nullptr; }
static bool ota_acquire_context(char* reply, size_t capacity) {
  ++acquisitions;
  if (!available) snprintf(reply, capacity, "ERR context unavailable");
  return available;
}
static OtaContext& ota_ctx() { return context; }
static bool handleSpeedCommand(const char*, char*, size_t, float pace) {
  ++speed_commands; last_adaptive = pace; return speed_consumed;
}
static float speedFactor() { return 1.25f; }
static unsigned millis() { return 10000; }
static unsigned ota_max_block_capability() { return 128; }
static bool ota_self_firmware_for_display(SelfFwInfo& info) { info = SelfFwInfo{}; return true; }
static uint8_t ota_bootloader_last_rc() { return 0; }
static bool ota_nrf52_boot_update_result(uint8_t) { return false; }

// @PRODUCTION_FUNCTIONS@

static __attribute__((noinline)) bool handle_control_command(
    const char* command, char* reply, mesh::MainBoard&, OtaContext&) {
  ++control_commands;
  snprintf(reply, 160, "control:%s", command);
  return true;
}

struct Reply {
  unsigned char before = 0xa5;
  char text[160];
  unsigned char after = 0x5a;
  Reply() { memset(text, 0xcc, sizeof text); }
  void checked() const {
    assert(before == 0xa5 && after == 0x5a);
    assert(memchr(text, 0, sizeof text));
  }
};
static mesh::MainBoard board;
static void invoke(const char* command, Reply& reply) {
  assert(handle_ota_command(command, reply.text, board));
  reply.checked();
}
static void reset() {
  context = OtaContext{}; available = true; speed_consumed = false;
  acquisitions = speed_commands = control_commands = 0;
}
static void dispatch_tests() {
  reset(); Reply reply;
  speed_consumed = true;
  assert(handle_ota_command("ota speed", reply.text, board));
  assert(acquisitions == 0 && last_adaptive == 2.5f);
  speed_consumed = false;
  assert(!handle_ota_command("otas", reply.text, board));
  assert(acquisitions == 0);
  available = false;
  invoke("ota ls", reply);
  assert(strcmp(reply.text, "ERR context unavailable") == 0);
  assert(last_adaptive == OTA_SPEED_DEFAULT && context.manager.queries == 0);
  reset();
  const char* statuses[] = {"ota", "ota status", "ota st", "ota   status"};
  for (const char* command : statuses) {
    invoke(command, reply);
#if defined(OTA_SEEDER_ONLY)
    assert(strstr(reply.text, "OTA seeder | install:disabled") == reply.text);
#else
    assert(strstr(reply.text, "OTA | this fw") == reply.text);
    assert(strstr(reply.text, "target:00000000 | maxblk:128"));
#endif
  }
  const char* aliases[] = {"ota neighbors", "ota nbrs", "ota updates", "ota ls", "ota n"};
  for (const char* command : aliases) {
    invoke(command, reply);
    assert(strstr(reply.text, "No updates seen yet"));
  }
  assert(context.manager.queries == 5);
  assert(context.manager.clock_calls == 9 && context.manager.speed_calls == 9);
  const char* controls[] = {"ota help", "ota stats", "ota dev verify", "ota pull 1 folder", "ota unknown"};
  for (const char* command : controls) {
    invoke(command, reply);
    assert(strstr(reply.text, "control:") == reply.text);
  }
  assert(control_commands == 5 && acquisitions == 14);
}
static void listing_tests() {
  reset(); Reply reply;
  const char* invalid[] = {"ota ls 0", "ota ls 256", "ota ls -1", "ota ls 1x", "ota ls 1 2", "ota ls 999999999999999999999"};
  for (const char* command : invalid) {
    invoke(command, reply);
    assert(strcmp(reply.text, "ERR usage: ota ls [page]") == 0);
  }
  assert(context.manager.queries == 0);
  invoke("ota ls 2", reply);
  assert(strcmp(reply.text, "ERR update page 2 out of range (1-1)") == 0);
  context.manager.count = 3;
  for (unsigned i = 0; i < 3; ++i) context.manager.rows[i].mid[0] = i + 1;
  invoke("ota ls 1", reply);
  assert(strstr(reply.text, "Updates 1/2") && strstr(reply.text, "1) 01020304") && strstr(reply.text, "2) 02020304"));
  assert(!strstr(reply.text, "3) 03020304"));
  invoke("ota ls 2", reply);
  assert(strstr(reply.text, "Updates 2/2") && strstr(reply.text, "3) 03020304"));
  context.manager.count = 1;
  const OtaManager::FetchState states[] = {OtaManager::COMPLETE, OtaManager::PAUSED, OtaManager::FAILED, OtaManager::FETCHING};
  for (OtaManager::FetchState state : states) {
    context.manager.state = state; invoke("ota ls", reply);
    const char* tag = state == OtaManager::COMPLETE ? "[ready]" : state == OtaManager::PAUSED ? "[paused]" : state == OtaManager::FAILED ? "[failed]" : "[downloading]";
    assert(strstr(reply.text, tag));
  }
  context.manager.state = OtaManager::IDLE;
  context.manager.rows[0].target_id = 0xffffffff;
  invoke("ota ls", reply); assert(strstr(reply.text, "[hw FFFFFFFF]"));
  context.manager.target_id = 0xffffffff;
  invoke("ota ls", reply); assert(strstr(reply.text, "[same target]"));
  context.manager.codec_supported = false;
  invoke("ota ls", reply); assert(strstr(reply.text, "[unsupported]"));
  context.manager.rows[0].flags = MFLAG_BOOTLOADER;
  invoke("ota ls", reply); assert(strstr(reply.text, "[bootloader unsupported]"));
}
static void name_tests(unsigned id, const char* name) {
  reset(); Reply reply; context.manager.count = 1;
  context.manager.rows[0].target_id = id;
  invoke("ota ls", reply);
  char expected[160];
  snprintf(expected, sizeof expected, "Updates 1/1 (2 src; refreshing):\n 1) 01020304 v1.2.3 full [%s] 2n 1s", name);
  assert(strcmp(reply.text, expected) == 0);
}
} // ota
} // mesh
int main() {
  mesh::ota::dispatch_tests();
  mesh::ota::listing_tests();
  unsigned id; char name[68];
  while (scanf("%x %67s", &id, name) == 2) mesh::ota::name_tests(id, name);
}
