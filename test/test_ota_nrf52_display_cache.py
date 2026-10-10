#!/usr/bin/env python3
"""Exercise real nRF52 self verification and status/stats without device headers."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_ota_self_metadata_cache import method


ROOT = Path(__file__).resolve().parents[1]


HARNESS = r'''
#include <helpers/ota/FirmwareInfo.h>
#include <helpers/ota/OtaByteIO.h>
#include <SHA256.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

namespace mesh {
struct Utils {
  static void toHex(char* out, const uint8_t* data, size_t len) {
    const char* digits = "0123456789abcdef";
    for (size_t i = 0; i < len; ++i) {
      out[i * 2] = digits[data[i] >> 4]; out[i * 2 + 1] = digits[data[i] & 15];
    }
    out[len * 2] = 0;
  }
};
namespace ota {
static const uint32_t MOTA_NRF52_STAGE_CEILING_LEGACY = 0xD4000;
static const uint32_t MOTA_NRF52_STAGE_CEILING_EXPANDED = 0xED000;
static const uint32_t MOTA_NRF52_APP_END = 0xED000;
static const uint32_t MOTA_NRF52_FLASH_PAGE = 4096;
static uint32_t app_base = 0x26000, stage_ceiling = 0xD4000;
static std::vector<uint8_t> flash;
static unsigned scans = 0, hashes = 0;
static uint64_t hashed_bytes = 0;
static uint32_t clock_ms = 1000;
uint32_t mota_nrf52_app_base() { return app_base; }
uint32_t mota_nrf52_layout_stage_ceiling() { return stage_ceiling; }
@GEOMETRY@

static uint32_t effective_ceiling() {
#if defined(OTA_SD_STORE)
  return MOTA_NRF52_APP_END;
#else
  return stage_ceiling;
#endif
}
void mh8(uint8_t out[8], const uint8_t* data, size_t len) {
  ++hashes; hashed_bytes += len;
  SHA256 sha; sha.update(data, len); sha.finalize(out, 8);
}
@SCANNER@
// Translate the real accessor's memory-mapped address at the scanner boundary, without changing
// either production function. This keeps the harness portable and compatible with sanitizers.
bool find_self_firmware(const uint8_t* region, uint32_t len, SelfFwInfo& out, bool verify_body) {
  ++scans;
  assert(uintptr_t(region) == app_base && len == effective_ceiling() - app_base);
  assert(len <= flash.size());
  return scan_flash(flash.data(), len, out, verify_body);
}
@FRESH@
@DISPLAY@

struct OtaManager {
  enum FetchState { IDLE, WANT_MANIFEST, WANT_LEAVES, VERIFYING_STAGED, FETCHING,
                    COMPLETE, FAILED, PAUSED };
  enum FetchError { FETCH_ERROR_NONE, FETCH_ERROR_MANIFEST, FETCH_ERROR_HASH_ALGO,
    FETCH_ERROR_VERSION, FETCH_ERROR_CODEC, FETCH_ERROR_GEOMETRY, FETCH_ERROR_TOO_LARGE,
    FETCH_ERROR_STORAGE, FETCH_ERROR_INTEGRITY, FETCH_ERROR_MANIFEST_TIMEOUT,
    FETCH_ERROR_LEAVES_TIMEOUT };
  enum { AUTOFETCH_ANY = 2, AUTOFETCH_SIGNED = 1 };
  struct ServeEntry { bool is_self; uint8_t mid[4]; uint32_t fw_version, have_count; };
  FetchState state = IDLE;
  FetchError error = FETCH_ERROR_NONE;
  unsigned have = 0, total = 26;
  uint8_t mid[4] = {1, 2, 3, 4};
  FetchState fetchState() const { return state; }
  FetchError fetchError() const { return error; }
  unsigned blocksHave() const { return have; }
  unsigned blocksTotal() const { return total; }
  const uint8_t* fetchManifestId() const { return mid; }
  bool fetched_is_bootloader() const { return false; }
  uint32_t target() const { return 0x12345678; }
  unsigned servedCount() const { return 0; }
  const ServeEntry* servedEntry(unsigned) const { return nullptr; }
  void servedDigest(uint8_t* out) const { memset(out, 0, 4); }
  uint8_t autofetch() const { return 0; }
  unsigned max_hops() const { return 2; }
};
struct OtaBlCaps { bool present = true; uint8_t storage_flags = 1; };
static const uint8_t OTA_BL_STORAGE_SD = 1;
struct Context {
  OtaManager manager;
  uint32_t session_started_ms = 1000;
  char hw_id[33] = "test";
  bool serving = false;
  struct Allow { unsigned count() const { return 0; } } allow;
  OtaBlCaps caps;
  const OtaBlCaps& bootloaderAppCaps() const { return caps; }
};
uint32_t millis() { return clock_ms; }
unsigned ota_max_block_capability() { return 2048; }
const char* ota_target_env_name(uint32_t) { return "test"; }
uint8_t ota_bootloader_last_rc() { return 0xB8; }
bool ota_nrf52_boot_update_result(uint8_t) { return false; }
@CLI_HELPERS@
using OtaContext = Context;
@STATUS@
void status(Context& c, char* reply) { assert(handle_status(reply, c)); }
void stats(Context& c, char* reply) { @STATS@ }

static SelfFwInfo image(uint32_t body, uint8_t tag) {
  assert(body + ENDF_LEN <= effective_ceiling() - app_base);
  flash.assign(effective_ceiling() - app_base, uint8_t(0xA0 + tag));
  uint8_t* trailer = flash.data() + body;
  memcpy(trailer, ENDF_MAGIC, 4);
  wr_u32le(trailer + 4, body);
  SHA256 sha; sha.update(flash.data(), body); sha.finalize(trailer + 8, 8);
  wr_u32le(trailer + 16, 0x01110100u + tag);
  wr_u32le(trailer + 20, 0xA0000000u + tag);
  memset(trailer + 24, 'A' + tag, 32);
  SelfFwInfo expected;
  assert(scan_flash(flash.data(), flash.size(), expected, false));
  return expected;
}
static void expect_info(const SelfFwInfo& actual, const SelfFwInfo& expected) {
  assert(actual.valid && actual.body_len == expected.body_len);
  assert(actual.image_len == expected.image_len && actual.endf_offset == expected.endf_offset);
  assert(actual.fw_version == expected.fw_version && actual.target_id == expected.target_id);
  assert(memcmp(actual.body_hash, expected.body_hash, 8) == 0);
  assert(memcmp(actual.hw_id, expected.hw_id, 33) == 0);
}
static void expect_empty(const SelfFwInfo& info) {
  assert(!info.valid && !info.body_len && !info.image_len && !info.endf_offset);
  assert(!info.fw_version && !info.target_id);
  for (uint8_t value : info.body_hash) assert(value == 0);
  for (char value : info.hw_id) assert(value == 0);
}
} }
using namespace mesh::ota;

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string which = argv[1];
  SelfFwInfo out;
  if (which == "production") {
    const SelfFwInfo expected = image(489u * 1024u, 1);
    assert(ota_self_firmware_for_display(out));
    expect_info(out, expected);
    assert(scans == 1 && hashes == 1 && hashed_bytes == expected.body_len);
    for (unsigned i = 0; i < 50; ++i) {
      out = SelfFwInfo();
      assert(ota_self_firmware_for_display(out));
      expect_info(out, expected);
    }
    assert(scans == 1 && hashes == 1 && hashed_bytes == expected.body_len);
    // Every caller receives a copy, not a mutable view of the cache.
    out.valid = false; out.body_hash[0] ^= 1; out.hw_id[0] = '?';
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    std::cout << "489-KiB body verified once; zero scans or hashes for 50 display polls\n";
  } else if (which == "base") {
    image(4096, 1);
    assert(ota_self_firmware_for_display(out));
    app_base = 0x27000;
    SelfFwInfo expected = image(8192, 2);
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    assert(scans == 2 && hashes == 2);
    app_base = 0x26000;
    expected = image(1024, 3);
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    assert(scans == 3 && hashes == 3);
  } else if (which == "ceiling") {
    SelfFwInfo expected = image(4096, 1);
    assert(ota_self_firmware_for_display(out));
    stage_ceiling = MOTA_NRF52_STAGE_CEILING_EXPANDED;
#if defined(OTA_SD_STORE)
    // SD always scans through APP_END, irrespective of internal staging geometry.
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    stage_ceiling = 123;
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    assert(scans == 1 && hashes == 1);
#else
    expected = image(8192, 2);
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    assert(scans == 2 && hashes == 2);
    stage_ceiling = MOTA_NRF52_STAGE_CEILING_LEGACY;
    expected = image(1024, 3);
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    assert(scans == 3 && hashes == 3);
    stage_ceiling = 123;
    assert(!ota_self_firmware_for_display(out)); expect_empty(out);
    assert(scans == 3 && hashes == 3);
    stage_ceiling = MOTA_NRF52_STAGE_CEILING_LEGACY;
    expected = image(512, 4);
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    assert(scans == 4 && hashes == 4);
#endif
  } else if (which == "invalid") {
    image(4096, 1);
    assert(ota_self_firmware_for_display(out));
    ++app_base; // Alignment failure must clear the previously successful snapshot.
    assert(!ota_self_firmware_for_display(out)); expect_empty(out);
    assert(scans == 1 && hashes == 1);
    --app_base;
    SelfFwInfo expected = image(1024, 2);
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    assert(scans == 2 && hashes == 2);
    app_base = MOTA_NRF52_APP_END; // Empty/inverted application regions are invalid too.
    assert(!ota_self_firmware_for_display(out)); expect_empty(out);
    assert(scans == 2 && hashes == 2);
  } else if (which == "retry") {
    image(4096, 1);
    flash[0] ^= 1;
    assert(!ota_self_firmware_for_display(out)); expect_empty(out);
    assert(!ota_self_firmware_for_display(out)); expect_empty(out);
    assert(scans == 2 && hashes == 2);
    SelfFwInfo expected = image(4096, 2);
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    assert(scans == 3 && hashes == 3);
    app_base = 0x27000; // A failed new layout must never resurrect the old success.
    image(4096, 3);
    wr_u32le(flash.data() + 4096 + 4, 4095);
    assert(!ota_self_firmware_for_display(out)); expect_empty(out);
    assert(!ota_self_firmware_for_display(out)); expect_empty(out);
    assert(scans == 5 && hashes == 3);
    expected = image(1024, 4);
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    assert(scans == 6 && hashes == 4);
  } else if (which == "integrity") {
    const SelfFwInfo expected = image(4096, 1);
    assert(ota_self_firmware_for_display(out));
    flash[0] ^= 1; // A cached display must never weaken the fresh safety accessor.
    assert(!ota_self_firmware(out)); expect_empty(out);
    assert(scans == 2 && hashes == 2);
    assert(ota_self_firmware_for_display(out)); expect_info(out, expected);
    assert(scans == 2 && hashes == 2);
    assert(!ota_self_firmware(out)); expect_empty(out);
    assert(scans == 3 && hashes == 3);
  } else if (which == "cli") {
    image(489u * 1024u, 1);
    Context c;
    char reply[160];
    status(c, reply); assert(strstr(reply, "no download"));
    stats(c, reply); assert(strstr(reply, "fetch idle"));
    assert(scans == 1 && hashes == 1);
    for (unsigned i = 1; i <= 25; ++i) {
      c.manager.state = OtaManager::FETCHING; c.manager.have = i;
      clock_ms = 1000 + i * 1000;
      char progress[20], age[20];
      snprintf(progress, sizeof progress, "%u/26", i);
      snprintf(age, sizeof age, "%us", i);
      status(c, reply);
      assert(strstr(reply, progress) && strstr(reply, age) && strstr(reply, "id=01020304"));
      stats(c, reply);
      assert(strstr(reply, progress) && strstr(reply, age) && strstr(reply, "id=01020304"));
    }
    c.manager.mid[0] = 10;
    c.manager.state = OtaManager::FAILED; c.manager.error = OtaManager::FETCH_ERROR_INTEGRITY;
    status(c, reply); assert(strstr(reply, "failed (integrity check)") && strstr(reply, "id=0a020304"));
    stats(c, reply); assert(strstr(reply, "failed:integrity check") && strstr(reply, "id=0a020304"));
    c.manager.state = OtaManager::COMPLETE; c.manager.have = 26;
    status(c, reply); assert(strstr(reply, "ready to install 26/26"));
    stats(c, reply); assert(strstr(reply, "fetch done 26/26"));
    assert(scans == 1 && hashes == 1);
    std::cout << "Live status/stats progress, state, MID and errors update without rescanning\n";
  } else {
    assert(false && "unknown test case");
  }
}
'''


class OtaNrf52DisplayCacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-nrf52-display-")
        cls.addClassCleanup(cls.temp.cleanup)
        self_source = (ROOT / "src/helpers/ota/OtaSelf.cpp").read_text(encoding="utf-8")
        nrf_source = self_source[self_source.index("#elif defined(NRF52_PLATFORM)\n// nRF52 internal flash"):]
        scanner = method((ROOT / "src/helpers/ota/FirmwareInfo.cpp").read_text(encoding="utf-8"),
                         "bool find_self_firmware(").replace("find_self_firmware(", "scan_flash(", 1)
        layout = (ROOT / "src/helpers/ota/OtaFlashLayout_nrf52.h").read_text(encoding="utf-8")
        geometry = method(layout, "inline bool mota_nrf52_stage_ceiling_valid(") + "\n" + method(
            layout, "inline bool mota_nrf52_layout_valid(uint32_t app_base, uint32_t stage_ceiling)")
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text(encoding="utf-8")
        status = method(cli, "static __attribute__((noinline)) bool handle_status(")
        stats = method(cli, '} else if (is_cmd(a, "stats", &rest))')
        helpers = "\n".join(method(cli, signature) for signature in (
            "static const char* state_word(", "static const char* state_short(",
            "static const char* fetch_error_word(", "static void ver_str("))
        source = HARNESS
        for token, value in {
            "GEOMETRY": geometry, "SCANNER": scanner,
            "FRESH": method(nrf_source, "bool ota_self_firmware(SelfFwInfo& out)"),
            "DISPLAY": method(self_source, "bool ota_self_firmware_for_display(SelfFwInfo& out)"),
            "CLI_HELPERS": helpers,
            "STATUS": status,
            "STATS": stats[stats.index("{") + 1:-1],
        }.items():
            source = source.replace("@" + token + "@", value)
        cls.binaries = {}
        for name, defines in (("internal", ()), ("sd", ("OTA_SD_STORE=1",))):
            binary = Path(cls.temp.name) / (name + (".exe" if os.name == "nt" else ""))
            sanitizers = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
            built = subprocess.run([
                # The real CLI deliberately truncates descriptive tails to its 160-byte reply cap.
                "c++", "-std=c++11", "-Wall", "-Wextra", *sanitizers,
                "-DNRF52_PLATFORM=1", *["-D" + define for define in defines],
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                "-x", "c++", "-", "-o", str(binary),
            ], input=source, text=True, capture_output=True)
            if built.returncode:
                raise AssertionError(built.stderr)
            cls.binaries[name] = binary

    def run_case(self, case):
        for storage, binary in self.binaries.items():
            with self.subTest(storage=storage):
                result = subprocess.run([str(binary), case], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_body_is_verified_once_for_fifty_polls(self):
        self.run_case("production")

    def test_application_base_changes_force_fresh_verification(self):
        self.run_case("base")

    def test_exact_storage_ceiling_is_used_for_cache_invalidation(self):
        self.run_case("ceiling")

    def test_invalid_geometry_clears_previous_success(self):
        self.run_case("invalid")

    def test_body_or_trailer_failures_remain_retryable(self):
        self.run_case("retry")

    def test_fresh_verification_still_detects_body_mutation_after_display_hit(self):
        self.run_case("integrity")

    def test_real_status_and_stats_poll_live_progress_without_hashing_again(self):
        self.run_case("cli")

    def test_unsupported_platform_display_delegates_empty_result(self):
        self_source = (ROOT / "src/helpers/ota/OtaSelf.cpp").read_text(encoding="utf-8")
        unsupported = self_source[self_source.index("#else\nbool ota_self_firmware(SelfFwInfo& out)"):]
        source = r'''
#include <helpers/ota/OtaSelf.h>
#include <cassert>
namespace mesh { namespace ota {
@FRESH@
@DISPLAY@
} }
int main() {
  mesh::ota::SelfFwInfo out;
  out.valid = true; out.body_len = 42; out.hw_id[0] = 'X';
  assert(!mesh::ota::ota_self_firmware_for_display(out));
  assert(!out.valid && !out.body_len && !out.hw_id[0]);
}
'''
        source = source.replace("@FRESH@", method(unsupported, "bool ota_self_firmware(SelfFwInfo& out)"))
        source = source.replace("@DISPLAY@", method(self_source, "bool ota_self_firmware_for_display(SelfFwInfo& out)"))
        sanitizers = [] if os.name == "nt" else ["-fsanitize=address,undefined"]
        for platform in (None, "STM32_PLATFORM=1", "RP2040_PLATFORM=1"):
            with self.subTest(platform=platform):
                binary = Path(self.temp.name) / "unsupported.exe"
                built = subprocess.run([
                    "c++", "-std=c++11", "-Wall", "-Wextra", "-Werror", *sanitizers,
                    *(["-D" + platform] if platform else []), "-I", str(ROOT / "src"),
                    "-x", "c++", "-", "-o", str(binary),
                ], input=source, text=True, capture_output=True)
                self.assertEqual(built.returncode, 0, built.stderr)
                result = subprocess.run([str(binary)], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
